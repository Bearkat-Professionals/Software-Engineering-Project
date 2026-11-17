"""2.2.3.3 Collect SSDP Evidence (#147, spec WF-04, IN-05, SEC-13).

Asks the local network "who supports UPnP?" with one SSDP M-SEARCH, then reads the
small XML description each answering device points to, and turns its manufacturer,
model and name into evidence:

    result = collect_ssdp(["192.168.1.1", "192.168.1.20"], wait_s=3.0)
    result.evidence["192.168.1.20"]
    # -> [RawEvidence(EvidenceType.SSDP, "manufacturer=Roku; model=Roku Ultra; model_number=4800X; name=Living Room")]

Everything a device sends is untrusted, and the LOCATION header names a URL the
device chose. So:
  - a description is fetched only from the address that answered, only over http,
    and only when that address is one of the scan's hosts (no fetching other servers);
  - each fetch has a timeout, a 64 KB cap, and never follows a redirect;
  - the XML is parsed with defusedxml with DTDs forbidden (no "billion laughs");
  - only the parsed fields are kept, never the payload (design: data rules).

Plain UDP and TCP sockets: no administrator rights (SEC-04, NFR-04). Multicast TTL 1,
so the search never leaves the local network.
"""
from __future__ import annotations

import http.client
import logging
import socket
import time
from collections.abc import Callable, Collection, Sequence
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Protocol
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree as SafeElementTree
from defusedxml.common import DefusedXmlException

from backend.records.enums import EvidenceType
from backend.services.scan.collect_evidence import CollectorResult
from backend.services.scan.evidence import RawEvidence, clean_field

METHOD = "ssdp"
SSDP_GROUP = ("239.255.255.250", 1900)
MX_S = 2                                    # devices answer within MX seconds
M_SEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    'MAN: "ssdp:discover"\r\n'
    f"MX: {MX_S}\r\n"
    "ST: ssdp:all\r\n"
    "\r\n"
).encode("ascii")
SEND_TIMES = 2                              # UDP can drop a packet; the UPnP spec suggests repeating
MAX_RESPONSE_BYTES = 8192
MAX_ANSWERS = 2000                          # a noisy network can't keep the search reading forever
MAX_LOCATIONS_PER_HOST = 3
MAX_URL_LENGTH = 2048
FETCH_TIMEOUT_S = 2.0
FETCH_BUDGET_S = 2.0                        # all fetches together, after the search
MAX_DESCRIPTION_BYTES = 65536
FETCH_WORKERS = 16
REQUEST_HEADERS = {"User-Agent": "NetworkDeviceScanner/1.0 UPnP/1.1", "Accept": "text/xml, application/xml"}

# The description fields kept, in the order they're written: (our name, UPnP element)
DESCRIPTION_FIELDS = (("manufacturer", "manufacturer"), ("model", "modelName"),
                      ("model_number", "modelNumber"), ("name", "friendlyName"))
UPNP_DEVICE_NS = "{urn:schemas-upnp-org:device-1-0}"

Location = tuple[str, int, str]             # (host, port, path), already checked by safe_location

logger = logging.getLogger(__name__)


class DatagramSocket(Protocol):
    """The parts of socket.socket that search() uses; tests pass a fake."""

    def sendto(self, data: bytes, address: tuple[str, int]) -> int: ...
    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]: ...
    def settimeout(self, value: float | None) -> None: ...
    def close(self) -> None: ...


# ---- the search (UDP) --------------------------------------------------------------

def local_address_for(hosts: Sequence[str]) -> str | None:
    """This computer's own address on the network the hosts are on, e.g. "192.168.1.212",
    or None if it can't be told. "Connecting" a UDP socket sends nothing: it only asks
    the operating system which adapter it would use to reach the first host."""
    if not hosts:
        return None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((hosts[0], 9))            # 9 = "discard"; no packet is sent
            address = probe.getsockname()[0]
    except (OSError, ValueError):
        return None
    return address if address != "0.0.0.0" else None


def open_search_socket(interface_ip: str | None = None) -> socket.socket:
    """A UDP socket on any free port, with multicast kept to the local network. With
    interface_ip, the search goes out of that adapter. Otherwise Windows picks one,
    and it can pick a VPN or virtual adapter where nobody answers."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        if interface_ip is not None:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_ip))
        sock.bind((interface_ip or "", 0))
    except OSError:
        sock.close()
        raise
    return sock


def parse_ssdp_response(data: bytes) -> dict[str, str]:
    """The headers of one SSDP answer, names in lower case, values trimmed. Only the
    first MAX_RESPONSE_BYTES are read. {} for anything that isn't an "HTTP/1.x 200" reply.

        parse_ssdp_response(b"HTTP/1.1 200 OK\\r\\nLOCATION: http://192.168.1.1:5000/d.xml\\r\\n\\r\\n")
        # -> {"location": "http://192.168.1.1:5000/d.xml"}
    """
    lines = data[:MAX_RESPONSE_BYTES].decode("latin-1").splitlines()   # latin-1 never fails
    status = lines[0].split() if lines else []
    if len(status) < 2 or not status[0].upper().startswith("HTTP/") or status[1] != "200":
        return {}
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line.strip():
            break                                   # end of the headers
        name, colon, value = line.partition(":")
        name = name.strip().lower()
        if colon and name and name not in headers:  # a repeated header keeps its first value
            headers[name] = value.strip()
    return headers


def search(wait_s: float, interface_ip: str | None = None,
           open_socket: Callable[[str | None], DatagramSocket] = open_search_socket,
           clock: Callable[[], float] = time.monotonic) -> list[tuple[str, dict[str, str]]]:
    """Send the M-SEARCH out of interface_ip's adapter and collect (source ip, headers)
    for every answer that arrives within wait_s. Raises OSError if the socket can't be
    opened or sent on; trouble while reading only ends the reading early."""
    deadline = clock() + wait_s
    answers: list[tuple[str, dict[str, str]]] = []
    sock = open_socket(interface_ip)
    try:
        for _ in range(SEND_TIMES):
            sock.sendto(M_SEARCH, SSDP_GROUP)
        while len(answers) < MAX_ANSWERS:
            left = deadline - clock()
            if left <= 0:
                break
            sock.settimeout(left)
            try:
                data, address = sock.recvfrom(MAX_RESPONSE_BYTES)
            except TimeoutError:                    # socket.timeout: wait_s is over
                break
            except ConnectionResetError:            # Windows reports an ICMP "port unreachable" this way
                continue
            except OSError:
                logger.warning("SSDP: stopped reading answers early", exc_info=True)
                break
            headers = parse_ssdp_response(data)
            if headers:
                answers.append((address[0], headers))
    finally:
        sock.close()
    return answers


# ---- the description (HTTP) ------------------------------------------------------------

def safe_location(url: str, source_ip: str, hosts: Collection[str]) -> Location | None:
    """(host, port, path) to fetch, only when ALL of these hold, otherwise None:
    the scheme is http, there's no user name or password in it, the host is exactly
    the address that answered, that address is one of the scan's hosts, and the
    port is 1 to 65535. So a device can only point us back at itself.

        safe_location("http://192.168.1.5:8060/", "192.168.1.5", hosts)      # -> ("192.168.1.5", 8060, "/")
        safe_location("http://192.168.1.5@evil.com/", "192.168.1.5", hosts)  # -> None
    """
    if source_ip not in hosts or not isinstance(url, str) or len(url) > MAX_URL_LENGTH:
        return None
    try:
        parts = urlsplit(url.strip())
        port = 80 if parts.port is None else parts.port     # .port raises ValueError when it isn't a port
    except ValueError:
        return None
    if parts.scheme != "http" or parts.username is not None or parts.password is not None:
        return None
    if parts.hostname != source_ip or not 1 <= port <= 65535:
        return None
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return source_ip, port, path


def fetch_description(host: str, port: int, path: str, timeout_s: float = FETCH_TIMEOUT_S,
                      max_bytes: int = MAX_DESCRIPTION_BYTES, *,
                      connect: Callable[..., http.client.HTTPConnection] = http.client.HTTPConnection,
                      clock: Callable[[], float] = time.monotonic) -> bytes | None:
    """The description's bytes, or None: not status 200, over max_bytes, slower than
    timeout_s in total, or any network or HTTP error. http.client never follows a
    redirect and ignores proxy settings, which is why it's used and not urllib."""
    deadline = clock() + timeout_s
    connection = connect(host, port, timeout=timeout_s)
    try:
        connection.request("GET", path, headers=REQUEST_HEADERS)
        response = connection.getresponse()
        if response.status != 200:
            return None
        body = bytearray()
        while len(body) <= max_bytes:
            if clock() > deadline:                  # a server trickling bytes still gives up on time
                return None
            chunk = response.read1(max_bytes + 1 - len(body))
            if not chunk:
                break
            body += chunk
        return bytes(body) if len(body) <= max_bytes else None
    except (OSError, http.client.HTTPException, ValueError):
        return None
    finally:
        connection.close()


def parse_description(xml: bytes) -> dict[str, str]:
    """The root device's manufacturer, model, model_number and name, each cleaned,
    empty ones left out. {} for XML that is broken, has a DTD, or has no device.
    Devices that forget the UPnP namespace are read too."""
    try:
        root = SafeElementTree.fromstring(xml, forbid_dtd=True)
    except (ParseError, DefusedXmlException, ValueError):
        return {}
    for namespace in (UPNP_DEVICE_NS, ""):
        device = root.find(f"{namespace}device")
        if device is not None:
            return _description_fields(device, namespace)
    return {}


def _description_fields(device: Element, namespace: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, tag in DESCRIPTION_FIELDS:
        value = clean_field(device.findtext(f"{namespace}{tag}"))
        if value:
            found[key] = value
    return found


# ---- the collector -------------------------------------------------------------------

def collect_ssdp(hosts: Sequence[str], wait_s: float, *,
                 search: Callable[[float, str | None], list[tuple[str, dict[str, str]]]] = search,
                 fetch: Callable[[str, int, str], bytes | None] = fetch_description,
                 local_address: Callable[[Sequence[str]], str | None] = local_address_for,
                 fetch_budget_s: float = FETCH_BUDGET_S, workers: int = FETCH_WORKERS,
                 clock: Callable[[], float] = time.monotonic) -> CollectorResult:
    """2.2.3.3 for the scan's hosts. Searches from this computer's adapter on the
    hosts' network and listens for wait_s, then fetches the descriptions for at most
    fetch_budget_s more. One evidence item per distinct description; a host whose
    descriptions couldn't be read gets its SERVER header instead."""
    in_scope = set(hosts)
    try:
        answers = search(wait_s, local_address(hosts))
    except OSError as exc:
        return CollectorResult(METHOD, {}, {}, available=False,
                               reason=f"the SSDP search couldn't be sent ({exc.strerror or type(exc).__name__})")
    deadline = clock() + fetch_budget_s

    locations: dict[str, list[Location]] = {}       # ip -> distinct safe locations, answer order
    servers: dict[str, str] = {}
    for ip, headers in answers:
        if ip not in in_scope:
            continue
        locations.setdefault(ip, [])
        if headers.get("server"):
            servers.setdefault(ip, headers["server"])
        target = safe_location(headers.get("location", ""), ip, in_scope)
        if target is not None and target not in locations[ip] and len(locations[ip]) < MAX_LOCATIONS_PER_HOST:
            locations[ip].append(target)

    bodies = _fetch_all([target for targets in locations.values() for target in targets],
                        fetch, deadline, workers, clock)
    evidence: dict[str, list[RawEvidence]] = {}
    for ip, targets in locations.items():
        values = [_evidence_value(parse_description(bodies[target])) for target in targets if bodies.get(target)]
        values = list(dict.fromkeys(value for value in values if value))   # distinct, in order
        if not values and clean_field(servers.get(ip)):
            values = [f"server={_value(clean_field(servers[ip]))}"]
        if values:
            evidence[ip] = [RawEvidence(EvidenceType.SSDP, value) for value in values]

    logger.info("SSDP: %d answers, %d hosts in scope answered, %d with evidence",
                len(answers), len(locations), len(evidence))
    return CollectorResult(METHOD, evidence, {})


def _fetch_all(targets: list[Location], fetch: Callable[[str, int, str], bytes | None],
               deadline: float, workers: int, clock: Callable[[], float]) -> dict[Location, bytes | None]:
    """Fetch every target at once, waiting no later than the deadline. A fetch still
    running then is abandoned: its thread finishes in the background, and its result
    is ignored, so a slow device can't delay the scan."""
    if not targets:
        return {}
    pool = ThreadPoolExecutor(max_workers=min(workers, len(targets)), thread_name_prefix="ssdp-fetch")
    try:
        futures = {pool.submit(fetch, *target): target for target in targets}
        done, _ = wait(futures, timeout=max(0.0, deadline - clock()))
        return {futures[future]: future.result() for future in done if future.exception() is None}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _evidence_value(fields: dict[str, str]) -> str | None:
    """"manufacturer=Roku; model=Roku Ultra; ..." in DESCRIPTION_FIELDS order, or None."""
    parts = [f"{key}={_value(fields[key])}" for key, _ in DESCRIPTION_FIELDS if fields.get(key)]
    return "; ".join(parts) or None


def _value(text: str | None) -> str:
    """A ";" inside a value would look like a new field, so it becomes ","."""
    return (text or "").replace(";", ",")
