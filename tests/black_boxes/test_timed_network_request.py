"""Unit tests for BB-3 Timed Network Request. No real network is used:
the pure functions get saved ping output, and ping() runs against a patched
subprocess.run, so these pass with the network unplugged.

    python -m unittest discover tests
"""
import subprocess
import time
import unittest
from unittest import mock

from backend.black_boxes.timed_network_request import ProbeResult, ProbeStatus, ping, run_probes
from backend.black_boxes.timed_network_request.ping import build_ping_command, read_ping_reply
from tests.fakes.fake_network import FakeNetwork

# Real Windows output, captured on a team machine
WINDOWS_REPLY = (
    "Pinging 127.0.0.1 with 32 bytes of data:\r\n"
    "Reply from 127.0.0.1: bytes=32 time<1ms TTL=128\r\n"
)
WINDOWS_TTL_EXPIRED = (                   # exit code 0, but the target never answered
    "Pinging 192.0.2.1 with 32 bytes of data:\r\n"
    "Reply from 173.219.208.29: TTL expired in transit.\r\n"
)
WINDOWS_UNREACHABLE = (                   # exit code 0, sent by our own machine
    "Pinging 192.168.1.250 with 32 bytes of data:\r\n"
    "Reply from 192.168.1.10: Destination host unreachable.\r\n"
)
WINDOWS_TIMED_OUT = (
    "Pinging 192.168.1.251 with 32 bytes of data:\r\n"
    "Request timed out.\r\n"
)


class BuildPingCommandTests(unittest.TestCase):
    def test_windows_waits_in_milliseconds(self):
        self.assertEqual(build_ping_command("10.0.0.1", 1.0, "Windows"),
                         ["ping", "-n", "1", "-w", "1000", "10.0.0.1"])

    def test_macos_waits_in_milliseconds(self):
        self.assertEqual(build_ping_command("10.0.0.1", 0.5, "Darwin"),
                         ["ping", "-c", "1", "-W", "500", "10.0.0.1"])

    def test_linux_waits_in_whole_seconds_rounded_up(self):
        self.assertEqual(build_ping_command("10.0.0.1", 1.0, "Linux"),
                         ["ping", "-c", "1", "-W", "1", "10.0.0.1"])
        self.assertEqual(build_ping_command("10.0.0.1", 0.3, "Linux")[4], "1")   # never 0


class ReadPingReplyTests(unittest.TestCase):
    def test_windows_real_reply(self):
        self.assertTrue(read_ping_reply(0, WINDOWS_REPLY, "Windows"))

    def test_windows_exit_code_0_without_a_reply_is_not_a_reply(self):
        self.assertFalse(read_ping_reply(0, WINDOWS_TTL_EXPIRED, "Windows"))
        self.assertFalse(read_ping_reply(0, WINDOWS_UNREACHABLE, "Windows"))

    def test_windows_timed_out(self):
        self.assertFalse(read_ping_reply(1, WINDOWS_TIMED_OUT, "Windows"))

    def test_linux_and_macos_use_the_exit_code(self):
        for system in ("Linux", "Darwin"):
            with self.subTest(system=system):
                self.assertTrue(read_ping_reply(0, "", system))
                self.assertFalse(read_ping_reply(1, "", system))


class PingTests(unittest.TestCase):
    """ping() itself, with subprocess.run replaced so nothing is really sent."""

    def setUp(self):
        # Pretend to be Windows, whatever machine runs the tests
        patcher = mock.patch("platform.system", return_value="Windows")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _ping_with(self, **run_behaviour):
        with mock.patch("subprocess.run", **run_behaviour) as fake_run:
            return ping("192.168.1.1"), fake_run

    def test_reply_is_ok(self):
        done = subprocess.CompletedProcess([], 0, stdout=WINDOWS_REPLY.encode())
        result, _ = self._ping_with(return_value=done)
        self.assertEqual(result.status, ProbeStatus.OK)
        self.assertTrue(result.responded)
        self.assertEqual(result.target, "192.168.1.1")

    def test_no_reply(self):
        done = subprocess.CompletedProcess([], 0, stdout=WINDOWS_UNREACHABLE.encode())
        result, _ = self._ping_with(return_value=done)
        self.assertEqual(result.status, ProbeStatus.NO_REPLY)
        self.assertFalse(result.responded)

    def test_hung_process_is_a_timeout(self):
        result, _ = self._ping_with(side_effect=subprocess.TimeoutExpired("ping", 2.0))
        self.assertEqual(result.status, ProbeStatus.TIMEOUT)
        self.assertIsNotNone(result.detail)

    def test_missing_ping_command_is_unavailable(self):
        result, _ = self._ping_with(side_effect=FileNotFoundError("ping"))
        self.assertEqual(result.status, ProbeStatus.UNAVAILABLE)
        self.assertIn("could not run", result.detail)

    def test_bad_target_is_rejected_before_anything_runs(self):
        for target in ("-t", "not-an-ip", "", "192.168.1.300", "::1"):
            with self.subTest(target=target):
                with mock.patch("subprocess.run") as fake_run:
                    result = ping(target)
                self.assertEqual(result.status, ProbeStatus.UNAVAILABLE)
                fake_run.assert_not_called()


class RunProbesTests(unittest.TestCase):
    def test_results_keep_the_order_of_the_targets(self):
        def slow_first(target):                      # earlier targets finish last
            time.sleep(0.05 * (5 - int(target)))
            return ProbeResult(target, ProbeStatus.OK, 0)
        targets = ["1", "2", "3", "4", "5"]
        results = run_probes(targets, slow_first)
        self.assertEqual([r.target for r in results], targets)

    def test_probes_run_in_parallel(self):
        def wait_a_bit(target):
            time.sleep(0.2)
            return ProbeResult(target, ProbeStatus.NO_REPLY, 200)
        start = time.perf_counter()
        run_probes([str(n) for n in range(20)], wait_a_bit, max_workers=20)
        self.assertLess(time.perf_counter() - start, 2.0)    # one at a time would take 4 s

    def test_a_crashing_probe_only_affects_its_own_target(self):
        def crash_on_two(target):
            if target == "2":
                raise RuntimeError("bug in a probe")
            return ProbeResult(target, ProbeStatus.OK, 0)
        results = run_probes(["1", "2", "3"], crash_on_two)
        self.assertEqual([r.status for r in results],
                         [ProbeStatus.OK, ProbeStatus.UNAVAILABLE, ProbeStatus.OK])
        self.assertIn("bug in a probe", results[1].detail)

    def test_works_with_the_fake_network(self):
        network = FakeNetwork(responding={"10.0.0.2"})
        results = run_probes(["10.0.0.1", "10.0.0.2"], network.probe)
        self.assertEqual([r.responded for r in results], [False, True])
        self.assertCountEqual(network.probed, ["10.0.0.1", "10.0.0.2"])

    def test_no_targets_and_bad_worker_count(self):
        self.assertEqual(run_probes([], ping), [])
        with self.assertRaises(ValueError):
            run_probes(["10.0.0.1"], ping, max_workers=0)


if __name__ == "__main__":
    unittest.main()