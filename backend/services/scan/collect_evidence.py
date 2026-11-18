"""2.2.3 Evidence Collection Manager (#147, #151).

Runs the collectors that are turned on, all at the same time, and merges what they
found per host. The Scan Manager hands each host's evidence to Identification (2.2.4)
and its hostname to SH-4.

    collected = collect_evidence(["192.168.1.1", "192.168.1.20"],
                                 {"mdns": collect_mdns, "ssdp": collect_ssdp}, wait_s=3.0,
                                 on_done=lambda done, total: print(f"{done} of {total}"))
    collected.evidence["192.168.1.20"]   # -> (RawEvidence(MDNS, "service=..."), RawEvidence(SSDP, "..."))
    collected.hostnames["192.168.1.20"]  # -> "Living-Room-TV"
    collected.unavailable                # -> (("mdns", "couldn't listen on UDP port 5353 (...)"),)

A method that can't run is listed in `unavailable`, and the others' results are kept
(NFR-06). A host that doesn't answer isn't a failure. Collectors save nothing.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from backend.services.scan.evidence import RawEvidence, clean_field

logger = logging.getLogger(__name__)

HOSTNAME_ORDER = ("reverse_dns", "mdns", "netbios")     # whose hostname wins (#147)
GRACE_S = 5.0                                           # past wait_s, a collector is given up on
LATE_REASON = "didn't finish in time"


@dataclass(frozen=True)
class CollectorResult:
    method: str                                     # "mdns", "ssdp", "netbios", "reverse_dns"
    evidence: dict[str, list[RawEvidence]]          # ip -> what was found
    hostnames: dict[str, str]                       # ip -> name, if this method finds names
    available: bool = True                          # False: the method couldn't run at all
    reason: str | None = None                       # why, for the scan's error list


Collector = Callable[[Sequence[str], float], CollectorResult]    # (in-scope host IPs, seconds allowed)


@dataclass(frozen=True)
class CollectedEvidence:
    """Every collector's findings, merged per host."""
    evidence: dict[str, tuple[RawEvidence, ...]]    # ip -> each distinct value once
    hostnames: dict[str, str]                       # ip -> the name chosen by HOSTNAME_ORDER
    unavailable: tuple[tuple[str, str], ...]        # (method, reason), in the order given


NO_EVIDENCE = CollectedEvidence({}, {}, ())


def collect_evidence(hosts: Iterable[str], collectors: Mapping[str, Collector], wait_s: float,
                     on_done: Callable[[int, int], None] | None = None, *,
                     grace_s: float = GRACE_S, clock: Callable[[], float] = time.monotonic) -> CollectedEvidence:
    """Run every collector at once, each on its own thread with wait_s, and merge the results.

    A collector that raises, or that says it couldn't run, is unavailable. One still running
    at wait_s + grace_s is unavailable too ("didn't finish in time"); its thread is left to
    finish in the background and whatever it returns is ignored. on_done(done, total) is
    called after each collector, so the count goes 1, 2, ... total.
    """
    hosts = list(dict.fromkeys(hosts))
    if not hosts or not collectors:
        return NO_EVIDENCE
    methods = list(collectors)
    total = len(methods)
    lock = threading.Lock()
    results: dict[str, CollectorResult] = {}
    failed: dict[str, str] = {}
    counted: set[str] = set()                       # finished, failed or given up on: never counted twice

    def finished(method: str, result: CollectorResult | None, reason: str | None) -> None:
        with lock:
            if method in counted:                   # also a late result after the wait: dropped
                return
            counted.add(method)
            if reason is not None:
                failed[method] = reason
            else:
                results[method] = result
            _report(on_done, len(counted), total)   # inside the lock: the count never goes backwards

    def run(method: str) -> None:
        try:
            result = collectors[method](list(hosts), wait_s)
        except Exception as exc:
            logger.warning("evidence method %s failed", method, exc_info=True)
            finished(method, None, type(exc).__name__)
            return
        if not isinstance(result, CollectorResult):
            finished(method, None, "returned no result")
        elif not result.available:
            finished(method, None, result.reason or "unavailable")
        else:
            finished(method, result, None)

    threads = [threading.Thread(target=run, args=(method,), name=f"evidence-{method}", daemon=True)
               for method in methods]
    for thread in threads:
        thread.start()
    deadline = clock() + wait_s + grace_s
    for thread in threads:
        thread.join(max(0.0, deadline - clock()))

    with lock:
        for method in methods:
            if method not in counted:
                logger.warning("evidence method %s didn't finish within %.1f s", method, wait_s + grace_s)
                counted.add(method)
                failed[method] = LATE_REASON
                _report(on_done, len(counted), total)
        kept = dict(results)
        unavailable = tuple((method, failed[method]) for method in methods if method in failed)

    return _merge(hosts, [(method, kept[method]) for method in methods if method in kept], unavailable)


def _merge(hosts: list[str], results: list[tuple[str, CollectorResult]],
           unavailable: tuple[tuple[str, str], ...]) -> CollectedEvidence:
    """Per host: each distinct value once, in the order the collectors were given, and
    the hostname from the first method in HOSTNAME_ORDER that found one. Anything for an
    address that isn't one of the scan's hosts is dropped; every value is cleaned again."""
    wanted = set(hosts)
    found: dict[str, dict[RawEvidence, None]] = {}
    names: dict[str, dict[str, str]] = {}           # ip -> method -> name
    for method, result in results:              # the method's name as the scan knows it, not as it says
        for ip, items in (result.evidence or {}).items():
            if ip not in wanted:
                continue
            for item in items or ():
                value = clean_field(getattr(item, "raw_value", None))
                if value:
                    found.setdefault(ip, {})[RawEvidence(item.evidence_type, value)] = None
        for ip, name in (result.hostnames or {}).items():
            name = clean_field(name)
            if ip in wanted and name:
                names.setdefault(ip, {}).setdefault(method, name)

    order = list(HOSTNAME_ORDER) + [method for method, _ in results if method not in HOSTNAME_ORDER]
    evidence = {ip: tuple(found[ip]) for ip in hosts if ip in found}
    hostnames = {ip: next(names[ip][method] for method in order if method in names[ip])
                 for ip in hosts if ip in names and any(method in names[ip] for method in order)}
    return CollectedEvidence(evidence, hostnames, unavailable)


def _report(on_done: Callable[[int, int], None] | None, done: int, total: int) -> None:
    if on_done is None:
        return
    try:
        on_done(done, total)
    except Exception:                               # progress is nice to have; never lose results over it
        logger.exception("evidence progress report failed")
