"""BB-3 Timed Network Request (black box).

Hides the OS ping command, subprocesses, timeouts and threads. Callers import
only from this package:

    from backend.black_boxes.timed_network_request import ping, run_probes, ProbeResult, ProbeStatus

build_ping_command() and read_ping_reply() are deliberately not exported: they
are internal details, imported directly only by this box's own unit tests.
"""
from backend.black_boxes.timed_network_request.ping import ping
from backend.black_boxes.timed_network_request.probe_result import ProbeResult, ProbeStatus
from backend.black_boxes.timed_network_request.run_probes import DEFAULT_WORKERS, MAX_WORKERS, Probe, run_probes

__all__ = ["DEFAULT_WORKERS", "MAX_WORKERS", "Probe", "ProbeResult", "ProbeStatus", "ping", "run_probes"]
