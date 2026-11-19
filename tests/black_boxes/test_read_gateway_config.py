"""Unit tests for BB-2 Read Gateway Config (#174).

No real routing table: the parsers get recorded output from tests/fixtures/gateway/,
and subprocess.run and open are replaced with stand-ins, so these pass offline on
Windows, Linux and macOS alike.

    py -m unittest tests.black_boxes.test_read_gateway_config -v
"""
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from backend.black_boxes.read_gateway_config import DefaultRoute, Gateway, read_default_routes, read_gateway
from backend.black_boxes.read_gateway_config.parser import (choose_gateway, explain_choice, hex_to_ip,
                                                           parse_linux_addresses, parse_linux_routes,
                                                           parse_macos_ifconfig, parse_macos_route,
                                                           parse_proc_net_route, parse_windows_routes)

RUN = "backend.black_boxes.read_gateway_config.read_gateway_config.subprocess.run"
OPEN = "backend.black_boxes.read_gateway_config.read_gateway_config.open"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gateway"

LAN_WINDOWS = DefaultRoute("192.168.1.1", "192.168.1.212", "192.168.1.212", 25)
VPN_WINDOWS = DefaultRoute("10.8.0.1", "10.8.0.6", "10.8.0.6", 5)
LAN_LINUX = DefaultRoute("192.168.1.1", "wlp2s0", "192.168.1.50", 600)
VPN_LINUX = DefaultRoute("10.8.0.1", "tun0", "10.8.0.6", 50)
LINUX_ADDRESSES = {"lo": "127.0.0.1", "wlp2s0": "192.168.1.50", "tun0": "10.8.0.6"}


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def finished(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["x"], returncode, stdout=stdout, stderr="")


def answers(**by_word: "str | BaseException"):
    """A stand-in for subprocess.run: the answer for the first key (in the order given) that's
    a word of the command, e.g. answers(ifscope=..., get=...). Records every command in .commands."""
    commands: list[list[str]] = []

    def run(command, **kwargs):
        commands.append(command)
        words = {word.lstrip("-") for word in command}
        key = next(key for key in by_word if key in words)
        answer = by_word[key]
        if isinstance(answer, BaseException):
            raise answer
        return finished(answer)
    run.commands = commands
    return run


# ---- the parsers --------------------------------------------------------------------

class WindowsParserTests(unittest.TestCase):
    def test_active_routes_with_a_vpn(self):
        self.assertEqual(parse_windows_routes(fixture("windows_route_print_vpn.txt")), [LAN_WINDOWS, VPN_WINDOWS])
        # skipped: "On-link" isn't a gateway; OpenVPN's 0.0.0.0/128.0.0.0 half-route isn't a default
        # route; the four-column Persistent Routes row isn't active

    def test_odd_rows_are_skipped_not_a_crash(self):
        for row in ("0.0.0.0  0.0.0.0  192.168.1.1  192.168.1.212  Default",     # five columns, no metric
                    "0.0.0.0  0.0.0.0  192.168.1.1  192.168.1.212",              # four columns
                    "0.0.0.0  0.0.0.0  192.168.1.1  192.168.1.212  25  extra"):  # six
            with self.subTest(row=row):
                self.assertEqual(parse_windows_routes(row), [])

    def test_translated_headings_dont_matter(self):
        self.assertEqual(parse_windows_routes(fixture("windows_route_print_german.txt")), [LAN_WINDOWS])

    def test_no_default_route(self):
        self.assertEqual(parse_windows_routes("Active Routes:\nNone\n"), [])
        self.assertEqual(parse_windows_routes(""), [])


class LinuxParserTests(unittest.TestCase):
    def test_device_addresses(self):
        self.assertEqual(parse_linux_addresses(fixture("linux_ip_addr.txt")), LINUX_ADDRESSES)

    def test_ip_route_with_a_vpn(self):
        routes = parse_linux_routes(fixture("linux_ip_route_vpn.txt"), LINUX_ADDRESSES)
        self.assertEqual(routes, [LAN_LINUX, VPN_LINUX])            # "default dev wg0" has no via: skipped

    def test_src_wins_over_the_device_address_and_no_metric_is_0(self):
        routes = parse_linux_routes("default via 192.168.1.1 dev wlp2s0 src 192.168.1.77\n", LINUX_ADDRESSES)
        self.assertEqual(routes, [DefaultRoute("192.168.1.1", "wlp2s0", "192.168.1.77", 0)])
        self.assertEqual(parse_linux_routes("default via 192.168.1.1 dev eth5\n", {})[0].local_ip, None)

    def test_hex_is_little_endian(self):
        self.assertEqual(hex_to_ip("0101A8C0"), "192.168.1.1")
        self.assertEqual(hex_to_ip("0100080A"), "10.8.0.1")
        self.assertEqual(hex_to_ip("00000000"), "0.0.0.0")

    def test_proc_net_route(self):
        self.assertEqual(parse_proc_net_route(fixture("linux_proc_net_route.txt")),
                         [DefaultRoute("192.168.1.1", "wlp2s0", None, 600), DefaultRoute("10.8.0.1", "tun0", None, 50)])
        # skipped: the header, a network route (mask 00FFFFFF), one without the GATEWAY flag,
        # and OpenVPN's 0.0.0.0/1 half-route (mask 00000080)

    def test_nothing_to_find(self):
        self.assertEqual(parse_linux_routes("", {}), [])
        self.assertEqual(parse_proc_net_route("Iface\tDestination\tGateway\n"), [])


class MacParserTests(unittest.TestCase):
    """Built from constructed output: there's no Mac to record from (#174)."""

    def test_route_get(self):
        self.assertEqual(parse_macos_route(fixture("macos_route_get_default_vpn.txt")),
                         DefaultRoute("10.8.0.1", "utun3"))
        self.assertEqual(parse_macos_route(fixture("macos_route_get_ifscope_en0.txt")),
                         DefaultRoute("192.168.1.1", "en0"))

    def test_no_default_route(self):
        self.assertIsNone(parse_macos_route(fixture("macos_route_get_none.txt")))
        self.assertIsNone(parse_macos_route("    gateway: link#14\n  interface: en0\n"))   # not an IP

    def test_ifconfig(self):
        self.assertEqual(parse_macos_ifconfig(fixture("macos_ifconfig.txt")),
                         {"lo0": "127.0.0.1", "en0": "192.168.1.60", "utun3": "10.8.0.6"})


# ---- choosing the gateway ---------------------------------------------------------------

class ChooseGatewayTests(unittest.TestCase):
    def test_local_ip_picks_the_lan_over_the_vpn(self):
        self.assertEqual(choose_gateway([LAN_WINDOWS, VPN_WINDOWS], "192.168.1.212"),
                         Gateway("192.168.1.1", "192.168.1.212"))
        self.assertEqual(choose_gateway([VPN_LINUX, LAN_LINUX], "192.168.1.50"), Gateway("192.168.1.1", "wlp2s0"))

    def test_without_local_ip_the_lowest_metric_wins(self):
        gateway, why = explain_choice([LAN_WINDOWS, VPN_WINDOWS])
        self.assertEqual((gateway, why), (Gateway("10.8.0.1", "10.8.0.6"), "the lowest metric (5)"))

    def test_a_local_ip_that_matches_nothing_falls_back_to_the_metric(self):
        gateway, why = explain_choice([LAN_LINUX, VPN_LINUX], "172.16.0.9")
        self.assertEqual(gateway, Gateway("10.8.0.1", "tun0"))
        self.assertIn("no route is on the adapter with 172.16.0.9", why)

    def test_without_metrics_the_first_wins(self):
        first, second = DefaultRoute("192.168.1.1", "en0"), DefaultRoute("10.8.0.1", "utun3")
        self.assertEqual(explain_choice([first, second]), (Gateway("192.168.1.1", "en0"), "the first default route"))
        self.assertEqual(explain_choice([first])[1], "the only default route")

    def test_a_tie_keeps_the_first_listed(self):
        tied = [DefaultRoute("192.168.1.1", "a", None, 10), DefaultRoute("10.0.0.1", "b", None, 10)]
        self.assertEqual(choose_gateway(tied).ip, "192.168.1.1")

    def test_no_routes_no_gateway(self):
        self.assertEqual(explain_choice([]), (None, "no default route"))
        self.assertIsNone(choose_gateway([], "192.168.1.212"))


# ---- read_gateway(): the runners, with subprocess.run (and open) faked ------------------

@mock.patch("sys.platform", "win32")
class WindowsReadTests(unittest.TestCase):
    def test_the_lan_router_with_a_vpn_connected(self):
        run = answers(route=fixture("windows_route_print_vpn.txt"))
        with mock.patch(RUN, side_effect=run) as patched:
            self.assertEqual(read_gateway("192.168.1.212"), Gateway("192.168.1.1", "192.168.1.212"))
        self.assertEqual(run.commands, [["route", "print", "-4", "0.0.0.0"]])
        self.assertNotIn("shell", patched.call_args.kwargs)
        self.assertEqual(patched.call_args.kwargs["timeout"], 10)

    def test_no_default_route_is_none(self):
        with mock.patch(RUN, side_effect=answers(route="Active Routes:\nNone\n")):
            self.assertIsNone(read_gateway())
            self.assertEqual(read_default_routes(), [])

    def test_missing_command_timeout_and_failure_are_none(self):
        for problem in (FileNotFoundError("route"), subprocess.TimeoutExpired("route", 10), OSError("blocked")):
            with self.subTest(problem=type(problem).__name__), mock.patch(RUN, side_effect=problem):
                self.assertIsNone(read_gateway())
                self.assertIsNone(read_default_routes())
        with mock.patch(RUN, return_value=finished("", returncode=1)):
            self.assertIsNone(read_gateway())

    @mock.patch(RUN)
    def test_a_bad_local_ip_is_refused_before_anything_runs(self, run):
        with self.assertRaises(ValueError):
            read_gateway("192.168.1.212; whoami")
        run.assert_not_called()


@mock.patch("sys.platform", "linux")
class LinuxReadTests(unittest.TestCase):
    def test_the_lan_router_with_a_vpn_connected(self):
        run = answers(route=fixture("linux_ip_route_vpn.txt"), addr=fixture("linux_ip_addr.txt"))
        with mock.patch(RUN, side_effect=run) as patched:
            self.assertEqual(read_gateway("192.168.1.50"), Gateway("192.168.1.1", "wlp2s0"))
            self.assertEqual(read_gateway(), Gateway("10.8.0.1", "tun0"))                  # lowest metric
        self.assertEqual(run.commands[:2], [["ip", "-4", "route", "show", "default"], ["ip", "-4", "-o", "addr", "show"]])
        self.assertEqual(patched.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_ip_missing_reads_proc_net_route(self):
        with mock.patch(RUN, side_effect=FileNotFoundError("ip")), \
             mock.patch(OPEN, mock.mock_open(read_data=fixture("linux_proc_net_route.txt"))) as opened:
            self.assertEqual(read_gateway(), Gateway("10.8.0.1", "tun0"))
        self.assertEqual(opened.call_args.args[0], "/proc/net/route")

    def test_both_missing_or_a_timeout_is_none(self):
        with mock.patch(RUN, side_effect=FileNotFoundError("ip")), mock.patch(OPEN, side_effect=OSError("no /proc")):
            self.assertIsNone(read_gateway())
        with mock.patch(RUN, side_effect=subprocess.TimeoutExpired("ip", 10)), mock.patch(OPEN) as opened:
            self.assertIsNone(read_gateway())
        opened.assert_not_called()

    def test_no_address_map_still_finds_the_routes(self):
        run = answers(route=fixture("linux_ip_route_vpn.txt"), addr=OSError("blocked"))
        with mock.patch(RUN, side_effect=run):
            routes = read_default_routes()
        self.assertEqual([route.local_ip for route in routes], ["192.168.1.50", None])     # src still known


@mock.patch("sys.platform", "darwin")
class MacReadTests(unittest.TestCase):
    def test_a_vpn_default_asks_again_for_the_scan_facing_adapter(self):
        run = answers(ifscope=fixture("macos_route_get_ifscope_en0.txt"), get=fixture("macos_route_get_default_vpn.txt"),
                      ifconfig=fixture("macos_ifconfig.txt"))
        with mock.patch(RUN, side_effect=run):
            self.assertEqual(read_gateway("192.168.1.60"), Gateway("192.168.1.1", "en0"))
        self.assertIn(["route", "-n", "get", "-ifscope", "en0", "default"], run.commands)

    def test_no_second_question_when_the_default_already_faces_the_scan(self):
        run = answers(get=fixture("macos_route_get_ifscope_en0.txt"), ifconfig=fixture("macos_ifconfig.txt"))
        with mock.patch(RUN, side_effect=run):
            self.assertEqual(read_gateway("192.168.1.60"), Gateway("192.168.1.1", "en0"))
            self.assertEqual(read_gateway(), Gateway("192.168.1.1", "en0"))
        self.assertFalse(any("-ifscope" in command for command in run.commands))

    def test_no_default_route_is_none(self):
        run = answers(get=fixture("macos_route_get_none.txt"), ifconfig=fixture("macos_ifconfig.txt"))
        with mock.patch(RUN, side_effect=run):
            self.assertIsNone(read_gateway())
        with mock.patch(RUN, return_value=finished("", returncode=1)):
            self.assertIsNone(read_gateway())


class OtherSystemTests(unittest.TestCase):
    @mock.patch(RUN)
    def test_other_systems_are_none(self, run):
        with mock.patch("sys.platform", "freebsd14"):
            self.assertIsNone(read_gateway())
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()