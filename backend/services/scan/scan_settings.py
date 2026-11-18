"""Scan settings (#81, #151): how many probes run at once, how long each one waits,
which discovery methods a scan uses, and which evidence methods ask each host what it is.

Defaults work without any setup. To tune them without changing code, add to .env:

    SCAN_PROBE_WORKERS=64                   # 1 to 128 (BB-3's MAX_WORKERS)
    SCAN_PROBE_TIMEOUT_S=0.5                # more than 0, at most 5 seconds
    SCAN_EVIDENCE_METHODS=mdns,ssdp         # any of mdns, ssdp, netbios, reverse_dns; empty = off
    SCAN_EVIDENCE_WAIT_S=5                  # how long mDNS and SSDP listen, 1 to 10 seconds

    settings = ScanSettings.from_env()
    hosts, status = sweep_subnet(scope, settings=settings)
    settings.to_configuration()     # JSON-ready values for scan.configuration_used

A sweep takes about (hosts / probe_workers) * probe_timeout_s: a /24 is 254 hosts,
so 32 workers and 1 second is 8 rounds, about 8 to 10 seconds.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

import backend.config  # noqa: F401  (loads .env into os.environ)
from backend.black_boxes.timed_network_request import DEFAULT_WORKERS, MAX_WORKERS
from backend.shared_modules.validate_input import validate_timeout

DEFAULT_PROBE_TIMEOUT_S = 1.0
MAX_PROBE_TIMEOUT_S = 5.0       # tighter than SH-1's 30 s: the sweep waits this long per round
DISCOVERY_METHODS = ("arp", "ping")     # Host Discovery methods, in the order they run; also the allowed values
EVIDENCE_METHODS = ("mdns", "ssdp", "netbios", "reverse_dns")    # 2.2.3 (#147); also the allowed values
DEFAULT_EVIDENCE_WAIT_S = 5.0          
MIN_EVIDENCE_WAIT_S = 1.0
MAX_EVIDENCE_WAIT_S = 10.0

WORKERS_KEY = "SCAN_PROBE_WORKERS"
TIMEOUT_KEY = "SCAN_PROBE_TIMEOUT_S"
EVIDENCE_METHODS_KEY = "SCAN_EVIDENCE_METHODS"
EVIDENCE_WAIT_KEY = "SCAN_EVIDENCE_WAIT_S"
# The first words of each field's error, so from_env can name the .env key
_KEY_FOR_ERROR = {"Probe workers": WORKERS_KEY, "Probe timeout": TIMEOUT_KEY,
                  "Evidence methods": EVIDENCE_METHODS_KEY, "Evidence wait": EVIDENCE_WAIT_KEY}


@dataclass(frozen=True)
class ScanSettings:
    probe_workers: int = DEFAULT_WORKERS
    probe_timeout_s: float = DEFAULT_PROBE_TIMEOUT_S
    discovery_methods: tuple[str, ...] = DISCOVERY_METHODS     # recorded with each scan (#45)
    evidence_methods: tuple[str, ...] = EVIDENCE_METHODS       # () turns the evidence phase off
    evidence_wait_s: float = DEFAULT_EVIDENCE_WAIT_S

    def __post_init__(self) -> None:
        workers = self.probe_workers
        if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= MAX_WORKERS:
            raise ValueError(f"Probe workers must be a whole number from 1 to {MAX_WORKERS}, not {workers!r}.")

        timeout = self.probe_timeout_s
        checked = validate_timeout(timeout) if isinstance(timeout, (int, float)) else None
        if not checked or checked.value > MAX_PROBE_TIMEOUT_S:
            raise ValueError(f"Probe timeout must be more than 0 and at most {MAX_PROBE_TIMEOUT_S:g} seconds, "
                             f"not {timeout!r}.")
        object.__setattr__(self, "probe_timeout_s", checked.value)     # 1 -> 1.0

        methods = self.discovery_methods
        if isinstance(methods, str) or not isinstance(methods, (tuple, list)):
            raise ValueError(f"Discovery methods must be a list of methods, not {methods!r}.")
        unknown = [method for method in methods if method not in DISCOVERY_METHODS]
        if not methods or unknown:
            raise ValueError(f"Discovery methods must be one or more of {', '.join(DISCOVERY_METHODS)}, "
                             f"not {list(methods)!r}.")
        object.__setattr__(self, "discovery_methods", tuple(dict.fromkeys(methods)))   # list -> tuple, no repeats

        evidence = self.evidence_methods
        if isinstance(evidence, str) or not isinstance(evidence, (tuple, list)):
            raise ValueError(f"Evidence methods must be a list of methods, not {evidence!r}.")
        unknown = [method for method in evidence if method not in EVIDENCE_METHODS]
        if unknown:                                     # empty is allowed: the phase is off
            raise ValueError(f"Evidence methods must be any of {', '.join(EVIDENCE_METHODS)}, "
                             f"not {list(evidence)!r}.")
        object.__setattr__(self, "evidence_methods", tuple(dict.fromkeys(evidence)))

        wait = self.evidence_wait_s
        if (isinstance(wait, bool) or not isinstance(wait, (int, float))
                or not MIN_EVIDENCE_WAIT_S <= wait <= MAX_EVIDENCE_WAIT_S):     # NaN fails both sides
            raise ValueError(f"Evidence wait must be from {MIN_EVIDENCE_WAIT_S:g} to {MAX_EVIDENCE_WAIT_S:g} "
                             f"seconds, not {wait!r}.")
        object.__setattr__(self, "evidence_wait_s", float(wait))

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ScanSettings:
        """Settings from .env (or the given mapping); a missing or blank key keeps its default.
        The one exception: SCAN_EVIDENCE_METHODS set to nothing turns the evidence phase off."""
        environ = os.environ if environ is None else environ
        workers_text = (environ.get(WORKERS_KEY) or "").strip()
        timeout_text = (environ.get(TIMEOUT_KEY) or "").strip()
        wait_text = (environ.get(EVIDENCE_WAIT_KEY) or "").strip()

        values: dict[str, Any] = {}
        if workers_text:
            if not workers_text.isdigit():
                raise ValueError(f"{WORKERS_KEY} in .env must be a whole number, not {workers_text!r}.")
            values["probe_workers"] = int(workers_text)
        if timeout_text:
            try:
                values["probe_timeout_s"] = float(timeout_text)
            except ValueError:
                raise ValueError(f"{TIMEOUT_KEY} in .env must be a number of seconds, "
                                 f"not {timeout_text!r}.") from None
        if environ.get(EVIDENCE_METHODS_KEY) is not None:          # present, even if empty
            values["evidence_methods"] = tuple(part.strip().lower()
                                               for part in environ[EVIDENCE_METHODS_KEY].split(",") if part.strip())
        if wait_text:
            try:
                values["evidence_wait_s"] = float(wait_text)
            except ValueError:
                raise ValueError(f"{EVIDENCE_WAIT_KEY} in .env must be a number of seconds, "
                                 f"not {wait_text!r}.") from None
        try:
            return cls(**values)
        except ValueError as exc:
            key = next(key for start, key in _KEY_FOR_ERROR.items() if str(exc).startswith(start))
            raise ValueError(f"{key} in .env: {exc}") from None

    def to_configuration(self) -> dict[str, Any]:
        """The settings as plain JSON-ready values, for scan.configuration_used."""
        return {"probe_workers": self.probe_workers, "probe_timeout_s": self.probe_timeout_s,
                "discovery_methods": list(self.discovery_methods),
                "evidence_methods": list(self.evidence_methods), "evidence_wait_s": self.evidence_wait_s}
