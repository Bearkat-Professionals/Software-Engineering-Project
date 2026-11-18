"""Tests for 2.2.3.1 Collect Name Evidence (#148): reverse DNS with a fake lookup, NetBIOS
with a fake socket. No network.

    python -m unittest tests.services.scan.test_collect_name_evidence -v
"""
from __future__ import annotations

import socket
import struct
import threading
import time
import unittest

from backend.records.enums import EvidenceType
from backend.services.scan.collect_name_evidence import (NETBIOS_PORT, collect_netbios, collect_reverse_dns,
                                                         node_status_request, parse_node_status)
from backend.services.scan.evidence import FIELD_MAX, RawEvidence, clean_field

PC_IP, ROUTER_IP, PRINTER_IP, OUTSIDE_IP = "192.168.1.20", "192.168.1.1", "192.168.1.30", "10.9.9.9"
ENCODED_STAR = b"\x20" + b"CK" + b"AA" * 15 + b"\x00"         # "*" padded with NULs, first-level encoded


def name_entry(name: str, suffix: int, flags: int) -> bytes:
    return name.encode("ascii").ljust(15, b" ") + bytes([suffix]) + struct.pack(">H", flags)


def nbstat_reply(transaction_id: int, entries: list[bytes], *, pointer: bool = False) -> bytes:
    """A node status reply as Windows sends it: names, then 46 bytes of statistics (MAC first)."""
    statistics = bytes.fromhex("a0595c5cfa2b") + b"\x00" * 40
    rdata = bytes([len(entries)]) + b"".join(entries) + statistics
    header = struct.pack(">HHHHHH", transaction_id, 0x8400, 0, 1, 0, 0)    # response, authoritative, 1 answer
    name = b"\xc0\x0c" if pointer else ENCODED_STAR
    return header + name + struct.pack(">HHIH", 0x0021, 0x0001, 0, len(rdata)) + rdata


# What a Windows PC answers: its workgroup (a group name) comes first here, then its own name
PC_ENTRIES = [name_entry("WORKGROUP", 0x00, 0x8400),           # group: not the computer's name
              name_entry("DESKTOP-AB12CD", 0x00, 0x0400),      # unique, active: the computer's name
              name_entry("DESKTOP-AB12CD", 0x20, 0x0400)]      # its file server service
PC_REPLY = nbstat_reply(0x1234, PC_ENTRIES)


# ---- the NBSTAT packets ------------------------------------------------------------------

class NodeStatusPacketTests(unittest.TestCase):
    def test_request_bytes(self) -> None:
        request = node_status_request(0x1234)
        self.assertEqual(request, struct.pack(">HHHHHH", 0x1234, 0, 1, 0, 0, 0) + ENCODED_STAR + b"\x00\x21\x00\x01")
        self.assertEqual(len(request), 50)
        self.assertEqual(node_status_request(0x1_0005)[:2], b"\x00\x05")      # kept to 16 bits

    def test_a_real_reply_gives_the_unique_workstation_name(self) -> None:
        self.assertEqual(parse_node_status(PC_REPLY), "DESKTOP-AB12CD")

    def test_a_compressed_answer_name_is_read_too(self) -> None:
        self.assertEqual(parse_node_status(nbstat_reply(1, PC_ENTRIES, pointer=True)), "DESKTOP-AB12CD")

    def test_only_group_names_gives_none(self) -> None:
        reply = nbstat_reply(1, [name_entry("WORKGROUP", 0x00, 0x8400), name_entry("WORKGROUP", 0x1E, 0x8400)])
        self.assertIsNone(parse_node_status(reply))

    def test_cut_off_empty_and_garbage_give_none(self) -> None:
        names_end = len(PC_REPLY) - 46
        cases = {"empty": b"", "header only": PC_REPLY[:12], "cut inside the names": PC_REPLY[:names_end - 5],
                 "cut inside the name": PC_REPLY[:20], "all 0xFF": b"\xff" * 80, "no answers": PC_REPLY[:6] + b"\x00" * 6,
                 "not NBSTAT": PC_REPLY.replace(b"\x00\x21\x00\x01", b"\x00\x20\x00\x01", 1),
                 "zero names": nbstat_reply(1, [])}
        for name, data in cases.items():
            with self.subTest(name):
                self.assertIsNone(parse_node_status(data))

    def test_random_garbage_never_raises(self) -> None:
        import random
        generator = random.Random(148)
        for _ in range(2000):
            data = bytes(generator.randrange(256) for _ in range(generator.randrange(120)))
            parse_node_status(data)                                      # must not raise
        for cut in range(len(PC_REPLY)):
            parse_node_status(PC_REPLY[:cut])

    def test_control_characters_in_a_name_are_cleaned(self) -> None:
        reply = nbstat_reply(1, [b"EVIL\r\nNAME\x00\x00\x00\x00\x00" + b"\x00" + b"\x04\x00"])
        self.assertEqual(parse_node_status(reply), "EVIL NAME")


# ---- reverse DNS -------------------------------------------------------------------------

class ReverseDnsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.release = threading.Event()        # lets "hung" lookups end once a test is over

    def tearDown(self) -> None:
        self.release.set()

    def collect(self, lookup, hosts=(ROUTER_IP,), wait_s=2.0, **options):
        return collect_reverse_dns(list(hosts), wait_s, lookup=lookup, workers=4, **options)

    def test_a_name_becomes_evidence_and_the_hostname(self) -> None:
        result = self.collect(lambda ip: ("router.lan", [], [ip]))
        self.assertEqual(result.method, "reverse_dns")
        self.assertTrue(result.available)
        self.assertEqual(result.evidence, {ROUTER_IP: [RawEvidence(EvidenceType.REVERSE_DNS, "router.lan")]})
        self.assertEqual(result.hostnames, {ROUTER_IP: "router.lan"})

    def test_no_name_is_not_a_failure(self) -> None:
        for error in (socket.herror(1, "Unknown host"), socket.gaierror(11001, "getaddrinfo failed"),
                      TimeoutError("timed out")):
            with self.subTest(error=type(error).__name__):
                def lookup(ip, error=error):
                    raise error
                result = self.collect(lookup)
                self.assertEqual((result.available, result.evidence, result.hostnames), (True, {}, {}))

    def test_the_ip_again_or_an_empty_name_is_skipped(self) -> None:
        names = {ROUTER_IP: ROUTER_IP, PC_IP: "", PRINTER_IP: "printer.lan"}
        result = self.collect(lambda ip: (names[ip], [], [ip]), hosts=names)
        self.assertEqual(result.hostnames, {PRINTER_IP: "printer.lan"})

    def test_a_bare_netbios_name_from_windows_fallback_is_not_dns(self) -> None:     # DI-04
        for name in ("READYSHARE", "READYSHARE.", "  READYSHARE  "):
            with self.subTest(name=name):
                result = self.collect(lambda ip, name=name: (name, [], [ip]))
                self.assertEqual((result.available, result.evidence, result.hostnames), (True, {}, {}))

    def test_a_dotted_name_with_a_trailing_dot_is_kept(self) -> None:
        self.assertEqual(self.collect(lambda ip: ("router.lan.", [], [ip])).hostnames, {ROUTER_IP: "router.lan."})

    def test_names_are_cleaned(self) -> None:
        result = self.collect(lambda ip: ("evil\x1b[31m.lan\x00" + "x" * 400, [], [ip]))
        name = result.hostnames[ROUTER_IP]
        self.assertTrue(name.startswith("evil [31m.lan x"))
        self.assertEqual(len(name), FIELD_MAX)

    def test_a_hung_lookup_does_not_hold_the_others_or_the_deadline(self) -> None:
        def lookup(ip):
            if ip == PC_IP:
                self.release.wait(30)               # like gethostbyaddr with no answer
            return ("router.lan", [], [ip])
        start = time.monotonic()
        result = self.collect(lookup, hosts=(PC_IP, ROUTER_IP), wait_s=1.0)
        self.assertLess(time.monotonic() - start, 1.0 + 1.0)
        self.assertEqual(result.hostnames, {ROUTER_IP: "router.lan"})

    def test_one_lookup_is_given_up_on_after_its_own_timeout(self) -> None:
        def lookup(ip):
            if ip == PC_IP:
                self.release.wait(30)
            return ("router.lan", [], [ip])
        start = time.monotonic()
        self.collect(lookup, hosts=(PC_IP, ROUTER_IP), wait_s=10.0, lookup_timeout_s=0.2)
        self.assertLess(time.monotonic() - start, 1.5)          # well before wait_s

    def test_a_slow_answer_after_its_timeout_is_ignored(self) -> None:
        def lookup(ip):
            time.sleep(0.4)
            return ("late.lan", [], [ip])
        self.assertEqual(self.collect(lookup, wait_s=2.0, lookup_timeout_s=0.2).hostnames, {})

    def test_a_lookup_that_cannot_be_called_is_unavailable(self) -> None:
        result = self.collect(None)
        self.assertFalse(result.available)
        self.assertTrue(result.reason)

        def broken(ip):
            raise TypeError("gethostbyaddr() argument 1 must be str")
        result = self.collect(broken)
        self.assertFalse(result.available)
        self.assertIn("TypeError", result.reason)

    def test_no_hosts(self) -> None:
        self.assertEqual(self.collect(lambda ip: ("x.lan", [], []), hosts=()).hostnames, {})


# ---- NetBIOS -----------------------------------------------------------------------------

class FakeSocket:
    """A UDP socket: remembers what was sent; recvfrom hands out `replies`, then waits out its timeout."""
    def __init__(self, replies=(), fail_send_to=()) -> None:
        self.replies = list(replies)
        self.fail_send_to = set(fail_send_to)
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.timeout: float | None = None
        self.closed = False

    def sendto(self, data, address):
        if address[0] in self.fail_send_to:
            raise OSError(10065, "No route to host")
        self.sent.append((data, address))
        return len(data)

    def settimeout(self, value):
        self.timeout = value

    def recvfrom(self, size):
        if self.replies:
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        time.sleep(self.timeout or 0)
        raise TimeoutError("timed out")

    def close(self):
        self.closed = True


class NetbiosTests(unittest.TestCase):
    FIRST_ID = 0x1234                           # PC_IP is asked first, so it gets 0x1234

    def collect(self, sock, hosts=(PC_IP, ROUTER_IP), wait_s=0.5):
        return collect_netbios(list(hosts), wait_s, open_socket=lambda: sock, first_transaction_id=self.FIRST_ID)

    def test_one_request_per_host_to_port_137(self) -> None:
        sock = FakeSocket()
        self.collect(sock)
        self.assertEqual(sock.sent, [(node_status_request(0x1234), (PC_IP, NETBIOS_PORT)),
                                     (node_status_request(0x1235), (ROUTER_IP, NETBIOS_PORT))])
        self.assertTrue(sock.closed)

    def test_a_matching_reply_gives_evidence_and_the_hostname(self) -> None:
        sock = FakeSocket([(PC_REPLY, (PC_IP, NETBIOS_PORT))])
        result = self.collect(sock)
        self.assertEqual(result.method, "netbios")
        self.assertTrue(result.available)
        self.assertEqual(result.evidence, {PC_IP: [RawEvidence(EvidenceType.NETBIOS_LLMNR, "DESKTOP-AB12CD")]})
        self.assertEqual(result.hostnames, {PC_IP: "DESKTOP-AB12CD"})

    def test_a_reply_from_an_address_not_asked_is_ignored(self) -> None:
        sock = FakeSocket([(PC_REPLY, (OUTSIDE_IP, NETBIOS_PORT))])
        self.assertEqual(self.collect(sock).hostnames, {})

    def test_a_wrong_transaction_id_is_ignored(self) -> None:
        router_answers_with_pcs_id = (PC_REPLY, (ROUTER_IP, NETBIOS_PORT))     # 0x1234 belongs to PC_IP
        sock = FakeSocket([router_answers_with_pcs_id, (nbstat_reply(0x9999, PC_ENTRIES), (PC_IP, NETBIOS_PORT))])
        self.assertEqual(self.collect(sock).hostnames, {})

    def test_windows_port_unreachable_does_not_stop_the_reading(self) -> None:
        sock = FakeSocket([ConnectionResetError(10054, "reset"), (PC_REPLY, (PC_IP, NETBIOS_PORT))])
        self.assertEqual(self.collect(sock).hostnames, {PC_IP: "DESKTOP-AB12CD"})

    def test_returns_as_soon_as_every_host_has_answered(self) -> None:
        sock = FakeSocket([(PC_REPLY, (PC_IP, NETBIOS_PORT))])
        start = time.monotonic()
        self.collect(sock, hosts=(PC_IP,), wait_s=5.0)
        self.assertLess(time.monotonic() - start, 1.0)

    def test_a_socket_that_fails_to_open_is_unavailable(self) -> None:
        def fail():
            raise OSError(10013, "An attempt was made to access a socket in a way forbidden")
        result = collect_netbios([PC_IP], 0.5, open_socket=fail)
        self.assertFalse(result.available)
        self.assertIn("couldn't be opened", result.reason)

    def test_a_socket_that_cannot_send_is_unavailable_and_closed(self) -> None:
        sock = FakeSocket(fail_send_to={PC_IP, ROUTER_IP})
        result = self.collect(sock)
        self.assertFalse(result.available)
        self.assertIn("couldn't be sent", result.reason)
        self.assertTrue(sock.closed)

    def test_one_unroutable_host_does_not_stop_the_others(self) -> None:
        sock = FakeSocket([(PC_REPLY, (PC_IP, NETBIOS_PORT))], fail_send_to={ROUTER_IP})
        result = self.collect(sock)
        self.assertTrue(result.available)
        self.assertEqual(result.hostnames, {PC_IP: "DESKTOP-AB12CD"})


# ---- both: a host that never answers -----------------------------------------------------

class DeadlineTests(unittest.TestCase):
    WAIT_S = 0.8

    def test_reverse_dns_returns_on_time(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)

        def never(ip):
            release.wait(30)
            return ("late.lan", [], [])
        start = time.monotonic()
        result = collect_reverse_dns([PC_IP], self.WAIT_S, lookup=never, workers=2)
        self.assertLess(time.monotonic() - start, self.WAIT_S + 1)
        self.assertEqual((result.available, result.hostnames), (True, {}))

    def test_netbios_returns_on_time(self) -> None:
        sock = FakeSocket()
        start = time.monotonic()
        result = collect_netbios([PC_IP], self.WAIT_S, open_socket=lambda: sock)
        self.assertLess(time.monotonic() - start, self.WAIT_S + 1)
        self.assertEqual((result.available, result.hostnames), (True, {}))


class CleanFieldTests(unittest.TestCase):
    def test_strips_control_characters_and_trims_to_255(self) -> None:
        self.assertEqual(clean_field("  OFFICE\r\n-PC\x00\x7f "), "OFFICE -PC")
        self.assertEqual(len(clean_field("a" * 1000)), 255)
        self.assertIsNone(clean_field("\x00\t "))


if __name__ == "__main__":
    unittest.main()
