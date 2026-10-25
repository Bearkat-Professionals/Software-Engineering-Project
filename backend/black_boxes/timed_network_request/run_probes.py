"""BB-3 parallel runner: many probes at once, with a cap on how many run together

Probes spend almost all their time waiting for a reply, so running them on
threads turns a /24 sweep from about 4 minutes into about 8 to 10 seconds
(design section 14.1). Parallelism is kept here, not spread around the code
(design section 10).
"""

import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Callable, Sequence

from backend.black_boxes.timed_network_request.probe_result import ProbeResult, ProbeStatus

Probe = Callable[[str], ProbeResult]

def run_probes(targets: Sequence[str], probe: Probe, max_workers: int = 32) -> list[ProbeResult]:
    """Run probe(target) for every target, at most max_workers at a time.
    
    Results come back in the same order as the targets. Never raise for a failing
    probe. That target gets UNAVALIABLE result and rest carry on (SEC-14)

    run_probes(addresses, ping)                            # 1 s timeout
    run_probes(addresses, partial(ping, timeout_s=0.5))    # another timeout
    """

    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")
    if not targets:
        return []

    workers = min(max_workers, len(targets))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bb3-probe") as pool:
        return list(pool.map(partial(_run_safely, probe), targets))

def _run_safely(probe: Probe, target: str) -> ProbeResult:
    """Call the probe, turning any unexpected exception into an UNAVAILABLE result."""
    start = time.perf_counter()
    try:
        return probe(target)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return ProbeResult(target, ProbeStatus.UNAVAILABLE, elapsed_ms, f"probe failed: {exc!r}")