import unittest

from backend.services.scan.sweep_subnet import list_host_addresses, sweep_subnet
from tests.fakes.fake_network import FakeNetwork


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

    def test_max_workers_one_still_works(self):
        network = FakeNetwork(responding={"192.168.1.2"})
        hosts, status = sweep_subnet("192.168.1.0/30", probe=network.probe, max_workers=1)
        self.assertEqual((hosts, status), (["192.168.1.2"], "ok"))


if __name__ == "__main__":
    unittest.main()