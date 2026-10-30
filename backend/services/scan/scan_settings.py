"""Scan settings (#81): how many probes run at once and how long each one waits.

Defaults work without any setup. To tune them without changing code, add to .env:

    SCAN_PROBE_WORKERS=64       # 1 to 128 (BB-3's MAX_WORKERS)
    SCAN_PROBE_TIMEOUT_S=0.5    # more than 0, at most 5 seconds

    settings = ScanSettings.from_env()
    hosts, status = sweep_subnet(scope, settings=settings)
    settings.to_configuration()     # {"probe_workers": 64, "probe_timeout_s": 0.5} for scan.configuration_used

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

WORKERS_KEY = "SCAN_PROBE_WORKERS"
TIMEOUT_KEY = "SCAN_PROBE_TIMEOUT_S"


@dataclass(frozen=True)
class ScanSettings:
    probe_workers: int = DEFAULT_WORKERS
    probe_timeout_s: float = DEFAULT_PROBE_TIMEOUT_S

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

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ScanSettings:
        """Settings from .env (or the given mapping); a missing or blank key keeps its default."""
        environ = os.environ if environ is None else environ
        workers_text = (environ.get(WORKERS_KEY) or "").strip()
        timeout_text = (environ.get(TIMEOUT_KEY) or "").strip()

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
        try:
            return cls(**values)
        except ValueError as exc:
            key = WORKERS_KEY if "workers" in str(exc) else TIMEOUT_KEY
            raise ValueError(f"{key} in .env: {exc}") from None

    def to_configuration(self) -> dict[str, Any]:
        """The settings as plain JSON-ready values, for scan.configuration_used."""
        return {"probe_workers": self.probe_workers, "probe_timeout_s": self.probe_timeout_s}
