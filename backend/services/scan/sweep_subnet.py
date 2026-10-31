"""Sweep Subnet (2.2.2, #28): ping every host address in the scope in parallel,
and return the ones that replied.

    hosts, status = sweep_subnet("192.168.1.0/24")                         # defaults: 32 at once, 1 s
    hosts, status = sweep_subnet(scope, settings=ScanSettings.from_env())  # tuned from .env (#81)
    hosts, status = sweep_subnet(scope, probe=network.probe)               # tests: a fake network

status is "unavailable" when no probe could run at all (no ping command, no
network), so the scan can say so instead of reporting an empty network.
"""

import ipaddress
from functools import partial

from backend.black_boxes.timed_network_request import Probe, ProbeStatus, ping, run_probes
from backend.services.scan.scan_settings import ScanSettings
from backend.shared_modules.validate_input import validate_scope


def list_host_addresses(scope: str) -> list[str]:
    """Every usable host address in the scope; raises ValueError for a bad scope."""
    result = validate_scope(scope)
    if not result:
        raise ValueError(result.reason)
    network = ipaddress.ip_network(result.value)
    return [str(address) for address in network.hosts()]


def sweep_subnet(scope: str, probe: Probe | None = None,
                 settings: ScanSettings | None = None) -> tuple[list[str], str]:
    """Probe every host address, settings.probe_workers at a time.

    With no probe, uses ping with settings.probe_timeout_s. A probe passed in
    keeps its own timeout. Returns (addresses that replied, "ok" or "unavailable").
    """
    settings = settings or ScanSettings()
    if probe is None:
        probe = partial(ping, timeout_s=settings.probe_timeout_s)

    addresses = list_host_addresses(scope)
    results = run_probes(addresses, probe, settings.probe_workers)

    if all(result.status == ProbeStatus.UNAVAILABLE for result in results):
        return [], "unavailable"

    responders = [result.target for result in results if result.responded]
    return responders, "ok"