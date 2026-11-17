"""2.2.3.2 Collect mDNS Evidence (#147): the services devices announce with mDNS/DNS-SD.

A Chromecast announces _googlecast._tcp, a printer _ipp._tcp, AirPlay speakers and TVs
_airplay._tcp and _raop._tcp, HomeKit accessories _hap._tcp. Each service a device offers
becomes one piece of evidence, and the device's .local name becomes its hostname.

    result = collect_mdns(["192.168.1.20", "192.168.1.31"], wait_s=3)
    result.evidence["192.168.1.20"]
        # [RawEvidence(MDNS, "service=_googlecast._tcp; name=Living Room TV; model=Chromecast Ultra")]
    result.hostnames["192.168.1.20"]      # "Living-Room-TV"

Uses the zeroconf library: plain UDP on port 5353, no admin rights (SEC-04, NFR-04).
On Windows the first run may show a Firewall prompt for Python (README, subtask 4).
Only parsed fields are kept, never whole records (data rule), and only for the hosts
Host Discovery found: answers from any other address are dropped.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from backend.records.enums import EvidenceType
from backend.services.scan.collect_evidence import CollectorResult
from backend.services.scan.evidence import RawEvidence, clean_field

logger = logging.getLogger(__name__)

METHOD = "mdns"
MAX_SERVICE_TYPES = 50         
MAX_INSTANCES = 500
MODEL_KEYS = (b"md", b"model", b"ty")       
LOCAL = ".local."

FIND_SHARE = 0.4
BROWSE_SHARE = 0.3
MAX_INFO_WAIT_S = 1.0 

@dataclass(frozen=True)
class MdnsService:
    """One service instance as zeroconf resolved it. Untrusted: the device chose every field."""
    service_type: str 
    instance_name: str
    server: str | None
    addresses: tuple[str, ...] = ()
    properties: dict[bytes, bytes | None] = field(default_factory=dict)

#the only part that touches the network
def browse_mdns(wait_s: float, *, clock: Callable[[], float] = time.monotonic,
                sleep: Callable[[float], None] = time.sleep) -> list[MdnsService]:
    """Every mDNS service on the network, finished by wait_s. Raises OSError when port 5353
    can't be joined and ImportError when zeroconf isn't installed: collect_mdns turns those
    into "unavailable". clock and sleep are for tests."""
    from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf, ZeroconfServiceTypes

    deadline = clock() + wait_s
    found: list[tuple[str, str]] = []       # (type, instance name), in the order they arrived
    seen: set[tuple[str, str]] = set()
    lock = threading.Lock()                 # zeroconf calls the browser's handler on its own thread

    def on_change(zeroconf: Any, service_type: str, name: str, state_change: Any) -> None:
        if state_change is not ServiceStateChange.Added:
            return
        with lock:
            if len(found) < MAX_INSTANCES and (service_type, name) not in seen:
                seen.add((service_type, name))
                found.append((service_type, name))

    zc = Zeroconf()
    browser = None
    try:
        # 1. Which service types are on the network. find()'s timeout is in SECONDS.
        types = ZeroconfServiceTypes.find(zc=zc, timeout=max(0.1, wait_s * FIND_SHARE))
        types = sorted(types)[:MAX_SERVICE_TYPES]
        if not types:
            return []

        # 2. The instances of each type, for as long as this step's share lasts
        browser = ServiceBrowser(zc, types, handlers=[on_change])
        sleep(max(0.0, min(wait_s * BROWSE_SHARE, deadline - clock())))
        with lock:
            instances = list(found)

        # 3. Each instance's address, host name and TXT record, until the deadline
        services = []
        for service_type, name in instances:
            left = deadline - clock()
            if left <= 0:
                break
            # get_service_info's timeout is in MILLISECONDS, unlike find()'s
            info = zc.get_service_info(service_type, name, timeout=int(min(left, MAX_INFO_WAIT_S) * 1000))
            if info is not None:
                services.append(MdnsService(service_type, name, info.server,
                                            tuple(info.parsed_addresses()), dict(info.properties or {})))
        return services
    finally:
        if browser is not None:
            browser.cancel()
        zc.close()


#pure: turning what was heard into evidence
def mdns_evidence(services: Iterable[MdnsService], hosts: Sequence[str]) -> CollectorResult:
    """One evidence item per service per host, and each host's .local name. Addresses that
    aren't in hosts are dropped. Pure: takes what browse_mdns returned."""
    wanted = set(hosts)
    evidence: dict[str, list[RawEvidence]] = {}
    hostnames: dict[str, str] = {}

    for service in services:
        line = _raw_value(service)
        hostname = _hostname(service.server)
        for address in service.addresses:
            if address not in wanted:
                continue
            if line:
                items = evidence.setdefault(address, [])
                item = RawEvidence(EvidenceType.MDNS, line)
                if item not in items:                  
                    items.append(item)
            if hostname and address not in hostnames:
                hostnames[address] = hostname
    return CollectorResult(METHOD, evidence, hostnames)


def collect_mdns(hosts: Sequence[str], wait_s: float,
                 browse: Callable[[float], list[MdnsService]] = browse_mdns) -> CollectorResult:
    """The Collector for mDNS. If it can't run at all, the result says so and why, and the
    scan carries on without it (NFR-06)."""
    if not hosts:
        return CollectorResult(METHOD, {}, {})          
    try:
        services = browse(wait_s)
    except ImportError:
        logger.warning("mDNS evidence skipped: the zeroconf library isn't installed", exc_info=True)
        return _unavailable("the zeroconf library isn't installed")
    except OSError as exc:
        logger.warning("mDNS evidence skipped: couldn't listen on UDP port 5353", exc_info=True)
        return _unavailable(f"couldn't listen on UDP port 5353 ({exc.strerror or type(exc).__name__})")
    except Exception:                                   
        logger.exception("mDNS evidence skipped: unexpected error")
        return _unavailable("unexpected error; see the server log")
    return mdns_evidence(services, hosts)

#helpers

def _raw_value(service: MdnsService) -> str:
    """'service=_googlecast._tcp; name=Living Room TV; model=Chromecast Ultra', leaving out
    any part that is empty after cleaning."""
    service_type = _without_suffix(service.service_type, LOCAL)
    name = _without_suffix(service.instance_name, "." + service.service_type)
    parts = [("service", clean_field(service_type)), ("name", clean_field(name)),
             ("model", clean_field(_model(service.properties)))]
    return clean_field("; ".join(f"{key}={value}" for key, value in parts if value)) or ""


def _model(properties: dict[Any, Any]) -> str | None:
    """The model from the TXT record: md, then model, then ty. Bytes that aren't UTF-8
    become the replacement character rather than an error."""
    for key in MODEL_KEYS:
        for candidate in (key, key.decode()):           
            value = properties.get(candidate)
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            if isinstance(value, str) and clean_field(value):
                return value
    return None


def _hostname(server: str | None) -> str:
    """'Living-Room-TV.local.' -> 'Living-Room-TV'"""
    if not server:
        return ""
    name = server.rstrip(".")
    if name.lower().endswith(".local"):
        name = name[: -len(".local")]
    return clean_field(name) or ""


def _without_suffix(text: str, suffix: str) -> str:
    return text[: -len(suffix)] if suffix and text.lower().endswith(suffix.lower()) else text.rstrip(".")


def _unavailable(why: str) -> CollectorResult:
    return CollectorResult(METHOD, {}, {}, available=False, reason=f"mDNS unavailable: {why}")