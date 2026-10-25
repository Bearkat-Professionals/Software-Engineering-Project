"""Fake BB-3 for unit tests: answers like the real black box, with no network

    network = FakeNetwork(responding={"192.168.1.1", "192.168.1.20"})
    results = run_probes(addresses, network.probe)   # the real runner, fake pings
    assert "192.168.1.5" in network.probed           # it was asked about that address
"""

import threading

from backend.black_boxes.timed_network_request import ProbeResult, ProbeStatus

class FakeNetwork:
    """A fake network, addresses in `responding` answer
    
    every other address gets NO_REPLY. with unavailable=True, every probe
    reports the method can't run
    """

    def __init__(self, responding=(), unavailable: bool = False):
        self.responding = set(responding)
        self.unavailable = unavailable
        self.probed: list[str] = []
        self._lock = threading.Lock()

    def probe(self, target: str) -> ProbeResult:
        with self._lock:
            self.probed.append(target)
        if self.unavailable:
            return ProbeResult(target, ProbeStatus.UNAVAILABLE, 0, "fake: ping unavailable")
        if target in self.responding:
            return ProbeResult(target, ProbeStatus.OK, 1)
        return ProbeResult(target, ProbeStatus.NO_REPLY, 1000)        