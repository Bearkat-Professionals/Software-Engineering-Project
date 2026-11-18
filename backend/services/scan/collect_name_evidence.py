"""2.2.3.1 Collect Name Evidence (#148, part of #147): the names devices go by.

Two collectors, each a Collector (collect_evidence.py):

    result = collect_reverse_dns(["192.168.1.1", "192.168.1.20"], wait_s=3.0)
    result.evidence["192.168.1.1"]      # -> [RawEvidence(EvidenceType.REVERSE_DNS, "router.lan")]
    result.hostnames["192.168.1.1"]     # -> "router.lan"

    result = collect_netbios(["192.168.1.20"], wait_s=2.0)
    result.evidence["192.168.1.20"]     # -> [RawEvidence(EvidenceType.NETBIOS_LLMNR, "OFFICE-PC")]

Reverse DNS asks the network's DNS server for each address's PTR name. NetBIOS sends
one node status request (NBSTAT, RFC 1002 4.2.17) to UDP port 137 of each host and keeps
the computer's own name from the reply. LLMNR is left out (decided on #147).

Every name a device or DNS server sends is untrusted: it goes through clean_field (no
control characters, at most 255 characters) before it is used. Plain UDP and the
system resolver: no administrator rights (SEC-04, NFR-04).
"""
from __future__ import annotations

import logging
import random
import socket
import struct
import time
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, Protocol

from backend.records.enums import EvidenceType
from backend.services.scan.collect_evidence import CollectorResult
from backend.services.scan.evidence import RawEvidence, clean_field
from backend.services.scan.scan_settings import ScanSettings

logger = logging.getLogger(__name__)

REVERSE_DNS_METHOD = "reverse_dns"
NETBIOS_METHOD = "netbios"

LOOKUP_TIMEOUT_S = 2.0                      # gethostbyaddr has no timeout of its own
POLL_S = 0.05                               # how often the lookup wait checks for given-up lookups

NETBIOS_PORT = 137
NBSTAT_TYPE, IN_CLASS = 0x0021, 0x0001
GROUP_FLAG = 0x8000                         # in a name entry's flags: a group name, not this computer's own
WORKSTATION_SUFFIX = 0x00
MAX_REPLY_BYTES = 2048

Lookup = Callable[[str], tuple[str, list[str], list[str]]]     # socket.gethostbyaddr's shape


class DatagramSocket(Protocol):
    """The parts of socket.socket that collect_netbios uses; tests pass a fake."""

    def sendto(self, data: bytes, address: tuple[str, int]) -> int: ...
    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]: ...
    def settimeout(self, value: float | None) -> None: ...
    def close(self) -> None: ...


# ---- reverse DNS -------------------------------------------------------------------------

def collect_reverse_dns(hosts: Sequence[str], wait_s: float, *, lookup: Lookup = socket.gethostbyaddr,
                        workers: int | None = None, lookup_timeout_s: float = LOOKUP_TIMEOUT_S,
                        clock: Callable[[], float] = time.monotonic) -> CollectorResult:
    """The PTR name of each host, looked up workers at a time (SCAN_PROBE_WORKERS by default).
    One lookup is given up on after lookup_timeout_s, and all of them by wait_s: a lookup
    still running then is left to finish in the background and its answer is ignored.
    An address with no name, or whose "name" is the address again, gets no evidence. So does
    a bare one-label name ("READYSHARE"): on Windows, gethostbyaddr falls back to NetBIOS and
    LLMNR when there's no PTR record, and that name is NetBIOS evidence, not DNS (DI-04)."""
    if not callable(lookup):
        return _unavailable(REVERSE_DNS_METHOD, "reverse DNS lookups can't be made on this computer")
    hosts = list(dict.fromkeys(hosts))
    answers, broken = _lookup_all(hosts, lookup, wait_s, workers or ScanSettings.from_env().probe_workers,
                                  lookup_timeout_s, clock)
    if broken is not None and not answers:
        return _unavailable(REVERSE_DNS_METHOD, f"reverse DNS lookups failed ({broken})")

    evidence: dict[str, list[RawEvidence]] = {}
    hostnames: dict[str, str] = {}
    for ip, answer in answers.items():
        name = clean_field(answer[0] if answer else None)
        if name is None or name == ip or "." not in name.rstrip("."):
            continue        # no PTR record; Windows' NetBIOS/LLMNR fallback answered instead
        evidence[ip] = [RawEvidence(EvidenceType.REVERSE_DNS, name)]
        hostnames[ip] = name
    logger.info("Reverse DNS: %d hosts asked, %d answered in time, %d with a name",
                len(hosts), len(answers), len(hostnames))
    return CollectorResult(REVERSE_DNS_METHOD, evidence, hostnames)


def _lookup_all(hosts: list[str], lookup: Lookup, wait_s: float, workers: int, lookup_timeout_s: float,
                clock: Callable[[], float]) -> tuple[dict[str, Any], str | None]:
    """ip -> lookup answer, for the lookups that finished within their own timeout and wait_s.
    The second value names an unexpected error (not a socket error), meaning the lookup
    function itself is broken; socket errors only mean that host has no name."""
    if not hosts:
        return {}, None
    deadline = clock() + wait_s
    started: dict[str, float] = {}              # ip -> when a worker began it (written by the workers)
    took: dict[str, float] = {}

    def timed(ip: str) -> Any:
        started[ip] = clock()
        try:
            return lookup(ip)
        finally:
            took[ip] = clock() - started[ip]

    pool = ThreadPoolExecutor(max_workers=min(workers, len(hosts)), thread_name_prefix="reverse-dns")
    try:
        futures: dict[Future, str] = {pool.submit(timed, ip): ip for ip in hosts}
        pending = set(futures)
        while pending:
            now = clock()
            if now >= deadline:
                break
            pending = {future for future in pending                  # stop waiting for a lookup past its timeout
                       if not (futures[future] in started and now - started[futures[future]] > lookup_timeout_s)}
            if pending:
                _, pending = wait(pending, timeout=min(POLL_S, deadline - now), return_when=FIRST_COMPLETED)

        answers: dict[str, Any] = {}
        broken: str | None = None
        for future, ip in futures.items():
            if not future.done() or future.cancelled() or took.get(ip, lookup_timeout_s + 1) > lookup_timeout_s:
                continue
            error = future.exception()
            if error is None:
                answers[ip] = future.result()
            elif not isinstance(error, OSError):        # herror, gaierror and timeouts are OSErrors
                broken = broken or type(error).__name__
        return answers, broken
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # never block on a stuck lookup thread


# ---- NetBIOS -----------------------------------------------------------------------------

def node_status_request(transaction_id: int) -> bytes:
    """An NBSTAT query for the name "*", which every NetBIOS host answers.

        node_status_request(0x1234)[:2]     # -> b"\\x12\\x34"
    """
    header = struct.pack(">HHHHHH", transaction_id & 0xFFFF, 0x0000, 1, 0, 0, 0)
    name = b"*".ljust(16, b"\x00")
    encoded = bytes(letter for byte in name for letter in (ord("A") + (byte >> 4), ord("A") + (byte & 0x0F)))
    return header + b"\x20" + encoded + b"\x00" + struct.pack(">HH", NBSTAT_TYPE, IN_CLASS)


def parse_node_status(data: bytes) -> str | None:
    """The computer's own name from a node status reply: the first unique (not group)
    name with suffix 0x00, trailing spaces stripped and cleaned. None for a reply that
    is short, cut off, empty, has no such name, or isn't a node status reply. Never raises."""
    try:
        return _node_status_name(data)
    except (IndexError, struct.error, ValueError, TypeError):
        return None


def _node_status_name(data: bytes) -> str | None:
    if len(data) < 12 or struct.unpack_from(">H", data, 6)[0] < 1:      # ANCOUNT: no answer
        return None
    offset = _skip_name(data, 12)
    record_type, record_class, _ttl, _length = struct.unpack_from(">HHIH", data, offset)
    if record_type != NBSTAT_TYPE or record_class != IN_CLASS:
        return None
    offset += 10
    count = data[offset]
    offset += 1
    if offset + 18 * count > len(data):                                 # cut off inside the names
        return None
    for start in range(offset, offset + 18 * count, 18):
        suffix = data[start + 15]
        flags = struct.unpack_from(">H", data, start + 16)[0]
        if suffix == WORKSTATION_SUFFIX and not flags & GROUP_FLAG:
            name = clean_field(data[start:start + 15].decode("latin-1").rstrip(" "))
            if name:
                return name
    return None


def _skip_name(data: bytes, offset: int) -> int:
    """The offset just after the DNS-style name at offset, which may end in a 0xC0 pointer."""
    while True:
        length = data[offset]
        if length & 0xC0 == 0xC0:                   # compression pointer: 2 bytes, and the name ends
            return offset + 2
        if length & 0xC0:
            raise ValueError("bad label")
        offset += 1 + length
        if length == 0:
            return offset


def open_netbios_socket() -> socket.socket:
    """A UDP socket on any free port: replies come back to it from port 137."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.bind(("", 0))
    except OSError:
        sock.close()
        raise
    return sock


def collect_netbios(hosts: Sequence[str], wait_s: float, *,
                    open_socket: Callable[[], DatagramSocket] = open_netbios_socket,
                    clock: Callable[[], float] = time.monotonic,
                    first_transaction_id: int | None = None) -> CollectorResult:
    """Send one node status request to each host's port 137 from one socket, then read
    replies until wait_s is up (or every host has answered). A reply counts only when it
    comes from an address that was asked AND carries that address's transaction id."""
    deadline = clock() + wait_s
    hosts = list(dict.fromkeys(hosts))
    first = random.randrange(0x10000) if first_transaction_id is None else first_transaction_id
    try:
        sock = open_socket()
    except OSError as exc:
        return _unavailable(NETBIOS_METHOD, f"the NetBIOS socket couldn't be opened ({_why(exc)})")

    asked: dict[str, int] = {}                  # ip -> its transaction id
    answered: set[str] = set()
    hostnames: dict[str, str] = {}
    try:
        send_error: OSError | None = None
        for number, ip in enumerate(hosts):
            transaction_id = (first + number) & 0xFFFF
            try:
                sock.sendto(node_status_request(transaction_id), (ip, NETBIOS_PORT))
            except OSError as exc:              # e.g. an address this computer can't route to
                send_error = exc
                continue
            asked[ip] = transaction_id
        if hosts and not asked:
            return _unavailable(NETBIOS_METHOD, f"NetBIOS requests couldn't be sent ({_why(send_error)})")

        while len(answered) < len(asked):
            left = deadline - clock()
            if left <= 0:
                break
            sock.settimeout(left)
            try:
                data, address = sock.recvfrom(MAX_REPLY_BYTES)
            except TimeoutError:                # socket.timeout: wait_s is over
                break
            except ConnectionResetError:        # Windows reports an ICMP "port unreachable" this way
                continue
            except OSError:
                logger.warning("NetBIOS: stopped reading replies early", exc_info=True)
                break
            ip = address[0]
            if ip not in asked or ip in answered or len(data) < 2 or struct.unpack_from(">H", data)[0] != asked[ip]:
                continue                        # not ours: another address, or an old or forged reply
            answered.add(ip)
            name = parse_node_status(data)
            if name:
                hostnames[ip] = name
    finally:
        sock.close()

    logger.info("NetBIOS: %d hosts asked, %d answered, %d with a name", len(asked), len(answered), len(hostnames))
    evidence = {ip: [RawEvidence(EvidenceType.NETBIOS_LLMNR, name)] for ip, name in hostnames.items()}
    return CollectorResult(NETBIOS_METHOD, evidence, hostnames)


# ---- helpers -----------------------------------------------------------------------------

def _unavailable(method: str, reason: str) -> CollectorResult:
    return CollectorResult(method, {}, {}, available=False, reason=reason)


def _why(exc: OSError | None) -> str:
    return (exc.strerror or type(exc).__name__) if exc is not None else "unknown error"
