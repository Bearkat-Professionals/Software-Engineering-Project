import unittest
from unittest import mock

from backend.black_boxes.timed_network_request import (DEFAULT_WORKERS, ProbeResult, ProbeStatus,
                                                       run_probes)
from backend.services.scan.scan_settings import DEFAULT_PROBE_TIMEOUT_S, ScanSettings
from backend.services.scan.sweep_subnet import list_host_addresses, sweep_subnet
from tests.fakes.fake_network import FakeNetwork

SWEEP = "backend.services.scan.sweep_subnet"      # patch names where sweep_subnet looks them up


def no_reply(ip: str, timeout_s: float) -> ProbeResult:
    """Stands in for ping: every address is silent."""
    return ProbeResult(ip, ProbeStatus.NO_REPLY, 0)


#Host Addresses
class TestListHostAddresses(unittest.TestCase):
    def test_slash_24_skips_network_and_broadcast(self):
        addresses = list_host_addresses("192.168.1.0/24")
        self.assertEqual(len(addresses), 254)
        self.assertEqual(addresses[0], "192.168.1.1")
        self.assertEqual(addresses[-1], "192.168.1.254")
        self.assertNotIn("192.168.1.0", addresses)
        self.assertNotIn("192.168.1.255", addresses)

    def test_slash_30(self):
        self.assertEqual(list_host_addresses("192.168.1.0/30"), ["192.168.1.1", "192.168.1.2"])

    def test_slash_32_is_one_address(self):
        self.assertEqual(list_host_addresses("192.168.1.50/32"), ["192.168.1.50"])

    def test_bad_scope_raises_with_reason(self):
        for scope in ["8.8.8.0/24", "10.0.0.0/8", "192.168.1.0", "", None]:
            with self.subTest(scope=scope):
                with self.assertRaises(ValueError):
                    list_host_addresses(scope)

    def test_error_message_is_the_validation_reason(self):
        with self.assertRaises(ValueError) as error:
            list_host_addresses("8.8.8.0/24")
        self.assertIn("Can't scan 8.8.8.0/24", str(error.exception))

#Sweep Subnet
class TestSweepSubnet(unittest.TestCase):
    def test_some_addresses_answer(self):
        network = FakeNetwork(responding={"192.168.1.1", "192.168.1.20"})
        hosts, status = sweep_subnet("192.168.1.0/24", probe=network.probe)
        self.assertEqual(status, "ok")
        self.assertEqual(hosts, ["192.168.1.1", "192.168.1.20"])

    def test_none_answer_is_still_ok(self):
        network = FakeNetwork()
        hosts, status = sweep_subnet("192.168.1.0/24", probe=network.probe)
        self.assertEqual(status, "ok")
        self.assertEqual(hosts, [])

    def test_ping_unavailable(self):
        network = FakeNetwork(unavailable=True)
        hosts, status = sweep_subnet("192.168.1.0/24", probe=network.probe)
        self.assertEqual(status, "unavailable")
        self.assertEqual(hosts, [])

    def test_results_keep_input_order(self):
        responding = {"192.168.1.200", "192.168.1.3", "192.168.1.77", "192.168.1.1"}
        network = FakeNetwork(responding=responding)
        hosts, status = sweep_subnet("192.168.1.0/24", probe=network.probe)
        self.assertEqual(hosts, ["192.168.1.1", "192.168.1.3", "192.168.1.77", "192.168.1.200"])

    def test_pings_every_host_address(self):
        network = FakeNetwork()
        sweep_subnet("192.168.1.0/24", probe=network.probe)
        self.assertEqual(sorted(network.probed), sorted(list_host_addresses("192.168.1.0/24")))

    def test_does_not_ping_network_or_broadcast(self):
        network = FakeNetwork()
        sweep_subnet("192.168.1.0/24", probe=network.probe)
        self.assertNotIn("192.168.1.0", network.probed)
        self.assertNotIn("192.168.1.255", network.probed)

    def test_one_failing_probe_does_not_stop_the_sweep(self):
        network = FakeNetwork(responding={"192.168.1.1"})

        def flaky_probe(target):
            if target == "192.168.1.2":
                raise OSError("fake crash")
            return network.probe(target)

        hosts, status = sweep_subnet("192.168.1.0/30", probe=flaky_probe)
        self.assertEqual(status, "ok")
        self.assertEqual(hosts, ["192.168.1.1"])

    def test_bad_scope_pings_nothing(self):
        for scope in ["8.8.8.0/24", "10.0.0.0/8", "not a range"]:
            with self.subTest(scope=scope):
                network = FakeNetwork(responding={"8.8.8.8"})
                with self.assertRaises(ValueError):
                    sweep_subnet(scope, probe=network.probe)
                self.assertEqual(network.probed, [])

    def test_one_worker_still_works(self) -> None:
        network = FakeNetwork(responding={"192.168.1.2"})
        hosts, status = sweep_subnet("192.168.1.0/30", probe=network.probe,
                                     settings=ScanSettings(probe_workers=1))
        self.assertEqual((hosts, status), (["192.168.1.2"], "ok"))

    def test_ping_gets_the_settings_timeout(self) -> None:
        with mock.patch(f"{SWEEP}.ping", side_effect=no_reply) as fake_ping:
            sweep_subnet("192.168.1.0/30", settings=ScanSettings(probe_timeout_s=0.5))
        self.assertEqual(sorted(call.args[0] for call in fake_ping.call_args_list),
                         ["192.168.1.1", "192.168.1.2"])
        for call in fake_ping.call_args_list:
            self.assertEqual(call.kwargs, {"timeout_s": 0.5})

    def test_worker_count_reaches_run_probes(self) -> None:
        network = FakeNetwork()
        with mock.patch(f"{SWEEP}.run_probes", wraps=run_probes) as watched:
            sweep_subnet("192.168.1.0/30", probe=network.probe, settings=ScanSettings(probe_workers=7))
        self.assertEqual(watched.call_args.args[2], 7)

    def test_no_settings_keeps_the_old_defaults(self) -> None:
        with mock.patch(f"{SWEEP}.ping", side_effect=no_reply) as fake_ping, \
             mock.patch(f"{SWEEP}.run_probes", wraps=run_probes) as watched:
            sweep_subnet("192.168.1.0/30")
        self.assertEqual(watched.call_args.args[2], DEFAULT_WORKERS)
        self.assertEqual(fake_ping.call_args.kwargs, {"timeout_s": DEFAULT_PROBE_TIMEOUT_S})


if __name__ == "__main__":
    unittest.main()