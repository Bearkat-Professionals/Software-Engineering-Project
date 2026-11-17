# tests/services/scan/test_collect_ssdp_evidence.py
"""Tests for 2.2.3.3 Collect SSDP Evidence (#147). No network: the search uses a fake
socket, and the fetch tests use a tiny HTTP server on 127.0.0.1 (this computer only).

    py -3.13 -m unittest tests.services.scan.test_collect_ssdp_evidence -v
"""
from __future__ import annotations

import socket
import threading
import time
import unittest
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend.records.enums import EvidenceType
from backend.services.scan import collect_ssdp_evidence as ssdp
from backend.services.scan.collect_ssdp_evidence import (M_SEARCH, SSDP_GROUP, collect_ssdp, fetch_description,
                                                         local_address_for, open_search_socket, parse_description,
                                                         parse_ssdp_response, safe_location, search)
from backend.services.scan.evidence import RawEvidence

ROKU_IP, ROUTER_IP, OUTSIDE_IP = "192.168.1.20", "192.168.1.1", "10.9.9.9"
HOSTS = {ROKU_IP, ROUTER_IP}

# A real answer from a Roku, as it arrives on the wire
ROKU_ANSWER = (b"HTTP/1.1 200 OK\r\n"
               b"Cache-Control: max-age=3600\r\n"
               b"ST: roku:ecp\r\n"
               b"USN: uuid:roku:ecp:YN00AB123456\r\n"
               b"Ext: \r\n"
               b"Server: Roku/9.4.0 UPnP/1.0 Roku/9.4.0\r\n"
               b"LOCATION: http://192.168.1.20:8060/\r\n"
               b"\r\n")

ROKU_DESCRIPTION = b"""<?xml version="1.0" encoding="UTF-8" ?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:roku-com:device:player:1-0</deviceType>
    <friendlyName>Living Room</friendlyName>
    <manufacturer>Roku</manufacturer>
    <modelName>Roku Ultra</modelName>
    <modelNumber>4800X</modelNumber>
    <serialNumber>YN00AB123456</serialNumber>
  </device>
</root>"""

ROUTER_DESCRIPTION = b"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device>
    <deviceType>urn:schemas-upnp-org:device:InternetGatewayDevice:1</deviceType>
    <friendlyName>NETGEAR R7000</friendlyName>
    <manufacturer>NETGEAR, Inc.</manufacturer>
    <modelName>R7000</modelName>
    <deviceList>
      <device><manufacturer>Embedded Co</manufacturer><modelName>WAN device</modelName></device>
    </deviceList>
  </device>
</root>"""

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>
<root xmlns="urn:schemas-upnp-org:device-1-0"><device><manufacturer>&lol2;</manufacturer></device></root>"""

ROKU_VALUE = "manufacturer=Roku; model=Roku Ultra; model_number=4800X; name=Living Room"


def answer(location: str, server: str | None = None) -> dict[str, str]:
    headers = {"location": location}
    if server is not None:
        headers["server"] = server
    return headers


# ---- parse_ssdp_response -------------------------------------------------------------

class ParseResponseTests(unittest.TestCase):

    def test_a_real_answer(self) -> None:
        headers = parse_ssdp_response(ROKU_ANSWER)
        self.assertEqual(headers["location"], "http://192.168.1.20:8060/")
        self.assertEqual(headers["server"], "Roku/9.4.0 UPnP/1.0 Roku/9.4.0")
        self.assertEqual(headers["ext"], "")

    def test_odd_spacing_case_and_line_endings(self) -> None:
        headers = parse_ssdp_response(b"http/1.1 200 OK\nLoCaTiOn :   http://192.168.1.1/d.xml  \n"
                                      b"SERVER:x\nserver: second\n\nLOCATION: after-the-blank-line\n")
        self.assertEqual(headers, {"location": "http://192.168.1.1/d.xml", "server": "x"})

    def test_anything_else_gives_nothing(self) -> None:
        for data in [b"", b"\x00\xff garbage", b"NOTIFY * HTTP/1.1\r\nLOCATION: x\r\n\r\n",
                     b"HTTP/1.1 404 Not Found\r\nLOCATION: x\r\n\r\n", b"HTTP/1.1\r\n", b"\r\n\r\n"]:
            with self.subTest(data=data):
                self.assertEqual(parse_ssdp_response(data), {})

    def test_only_the_first_8_kb_is_read(self) -> None:
        data = b"HTTP/1.1 200 OK\r\nX-Pad: " + b"a" * 9000 + b"\r\nLOCATION: http://late/\r\n\r\n"
        self.assertNotIn("location", parse_ssdp_response(data))


# ---- search (fake socket) ---------------------------------------------------------------

class FakeSocket:
    """Plays back answers, then times out. Records what was sent."""

    def __init__(self, answers: list[tuple[bytes, str] | Exception], fail_send: bool = False) -> None:
        self.answers = list(answers)
        self.fail_send = fail_send
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def sendto(self, data: bytes, address: tuple[str, int]) -> int:
        if self.fail_send:
            raise OSError(10051, "A socket operation was attempted to an unreachable network")
        self.sent.append((data, address))
        return len(data)

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        if not self.answers:
            raise socket.timeout("timed out")
        item = self.answers.pop(0)
        if isinstance(item, Exception):
            raise item
        data, ip = item
        return data[:size], (ip, 1900)

    def settimeout(self, value: float | None) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class SearchTests(unittest.TestCase):

    def test_sends_the_search_and_collects_answers(self) -> None:
        fake = FakeSocket([(ROKU_ANSWER, ROKU_IP), (b"garbage", ROUTER_IP)])
        found = search(1.0, open_socket=lambda interface_ip: fake)
        self.assertEqual([ip for ip, _ in found], [ROKU_IP])            # garbage dropped
        self.assertEqual(fake.sent, [(M_SEARCH, SSDP_GROUP)] * 2)
        self.assertTrue(fake.closed)

    def test_the_search_message(self) -> None:
        text = M_SEARCH.decode("ascii")
        self.assertTrue(text.startswith("M-SEARCH * HTTP/1.1\r\n"))
        for line in ["HOST: 239.255.255.250:1900", 'MAN: "ssdp:discover"', "MX: 2", "ST: ssdp:all"]:
            self.assertIn(line + "\r\n", text)
        self.assertTrue(text.endswith("\r\n\r\n"))

    def test_a_reset_is_skipped_and_reading_goes_on(self) -> None:
        fake = FakeSocket([ConnectionResetError(10054, "reset"), (ROKU_ANSWER, ROKU_IP)])
        self.assertEqual(len(search(1.0, open_socket=lambda interface_ip: fake)), 1)

    def test_a_failed_send_raises_and_closes_the_socket(self) -> None:
        fake = FakeSocket([], fail_send=True)
        with self.assertRaises(OSError):
            search(1.0, open_socket=lambda interface_ip: fake)
        self.assertTrue(fake.closed)

    def test_stops_at_the_deadline(self) -> None:
        times = iter([0.0, 0.5, 2.0])                                 # start, before 1st read, before 2nd
        fake = FakeSocket([(ROKU_ANSWER, ROKU_IP), (ROKU_ANSWER, ROUTER_IP)])
        found = search(1.0, open_socket=lambda interface_ip: fake, clock=lambda: next(times))
        self.assertEqual(len(found), 1)

    def test_the_socket_is_opened_on_the_given_adapter(self) -> None:
        opened: list[str | None] = []

        def open_socket(interface_ip: str | None) -> FakeSocket:
            opened.append(interface_ip)
            return FakeSocket([])

        search(1.0, "192.168.1.212", open_socket=open_socket)
        search(1.0, open_socket=open_socket)
        self.assertEqual(opened, ["192.168.1.212", None])


class AdapterTests(unittest.TestCase):
    """Found on a real PC: with a adapter installed, Windows sent the search out of
    the adapter and nobody answered. The search must leave from the hosts' network."""

    def test_the_local_address_for_the_hosts_network(self) -> None:
        self.assertEqual(local_address_for(["127.0.0.1"]), "127.0.0.1")    # this computer only

    def test_no_hosts_no_address(self) -> None:
        self.assertIsNone(local_address_for([]))

    def test_the_socket_is_bound_to_that_adapter(self) -> None:
        with open_search_socket("127.0.0.1") as sock:
            self.assertEqual(sock.getsockname()[0], "127.0.0.1")


# ---- safe_location ---------------------------------------------------------------------

class SafeLocationTests(unittest.TestCase):

    def test_allowed(self) -> None:
        self.assertEqual(safe_location("http://192.168.1.20:8060/", ROKU_IP, HOSTS), (ROKU_IP, 8060, "/"))
        self.assertEqual(safe_location("HTTP://192.168.1.20/desc.xml?x=1", ROKU_IP, HOSTS),
                         (ROKU_IP, 80, "/desc.xml?x=1"))
        self.assertEqual(safe_location("http://192.168.1.20:5000", ROKU_IP, HOSTS), (ROKU_IP, 5000, "/"))

    def test_refused(self) -> None:
        cases = {
            "https": ("https://192.168.1.20:8060/", ROKU_IP),
            "another host": ("http://192.168.1.1:5000/", ROKU_IP),
            "outside scope": (f"http://{OUTSIDE_IP}/", OUTSIDE_IP),
            "file": ("file:///etc/passwd", ROKU_IP),
            "a name": ("http://router.local/d.xml", ROKU_IP),
            "userinfo trick": ("http://192.168.1.20@evil.com/", ROKU_IP),
            "password": ("http://user:pw@192.168.1.20/", ROKU_IP),
            "other spelling": ("http://192.168.001.020/", ROKU_IP),
            "port 0": ("http://192.168.1.20:0/", ROKU_IP),
            "port too big": ("http://192.168.1.20:70000/", ROKU_IP),
            "port not a number": ("http://192.168.1.20:abc/", ROKU_IP),
            "empty": ("", ROKU_IP),
            "too long": ("http://192.168.1.20/" + "a" * 3000, ROKU_IP),
        }
        for label, (url, source_ip) in cases.items():
            with self.subTest(label):
                self.assertIsNone(safe_location(url, source_ip, HOSTS))


# ---- parse_description -----------------------------------------------------------------

class ParseDescriptionTests(unittest.TestCase):

    def test_a_real_roku(self) -> None:
        self.assertEqual(parse_description(ROKU_DESCRIPTION),
                         {"manufacturer": "Roku", "model": "Roku Ultra", "model_number": "4800X",
                          "name": "Living Room"})

    def test_the_root_device_not_the_embedded_one(self) -> None:
        self.assertEqual(parse_description(ROUTER_DESCRIPTION),
                         {"manufacturer": "NETGEAR, Inc.", "model": "R7000", "name": "NETGEAR R7000"})

    def test_a_missing_namespace_is_read_too(self) -> None:
        xml = b"<root><device><manufacturer>Acme</manufacturer><modelName>X1</modelName></device></root>"
        self.assertEqual(parse_description(xml), {"manufacturer": "Acme", "model": "X1"})

    def test_a_dtd_is_refused(self) -> None:
        self.assertEqual(parse_description(BILLION_LAUGHS), {})

    def test_an_external_entity_is_refused(self) -> None:
        xml = (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
               b"<root><device><manufacturer>&x;</manufacturer></device></root>")
        self.assertEqual(parse_description(xml), {})

    def test_broken_xml_gives_nothing(self) -> None:
        for xml in [ROKU_DESCRIPTION[:120], b"", b"not xml", b"<root><nodevice/></root>"]:
            with self.subTest(xml=xml[:30]):
                self.assertEqual(parse_description(xml), {})

    def test_fields_are_cleaned(self) -> None:
        xml = ("<root><device><manufacturer>  Acme\t\n Corp </manufacturer>"
               f"<modelName>{'M' * 400}</modelName><friendlyName>   </friendlyName></device></root>").encode()
        found = parse_description(xml)
        self.assertEqual(found["manufacturer"], "Acme Corp")
        self.assertEqual(len(found["model"]), 255)
        self.assertNotIn("name", found)


# ---- fetch_description (a real HTTP server on 127.0.0.1) -------------------------------

class Handler(BaseHTTPRequestHandler):
    """Answers by path: /ok, /exact, /big, /redirect, /slow, /trickle; anything else is 404."""

    def do_GET(self) -> None:
        if self.path == "/ok":
            self._send(200, ROKU_DESCRIPTION)
        elif self.path == "/big":
            self._send(200, b"x" * (ssdp.MAX_DESCRIPTION_BYTES + 1))
        elif self.path == "/exact":
            self._send(200, b"x" * ssdp.MAX_DESCRIPTION_BYTES)
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/slow":
            time.sleep(1.0)
            self._send(200, ROKU_DESCRIPTION)
        elif self.path == "/trickle":
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            for _ in range(100):                                     # 1 byte every 0.05 s: 5 s in all
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.05)
        else:
            self._send(404, b"")

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:      # keep the test output quiet
        pass


class QuietServer(ThreadingHTTPServer):
    """The slow tests hang up on purpose, so the server's "connection aborted" is expected."""

    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        pass


class FetchDescriptionTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = QuietServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def fetch(self, path: str, timeout_s: float = 2.0) -> bytes | None:
        return fetch_description("127.0.0.1", self.port, path, timeout_s=timeout_s)

    def test_a_description_is_fetched(self) -> None:
        self.assertEqual(self.fetch("/ok"), ROKU_DESCRIPTION)

    def test_exactly_64_kb_is_allowed_one_byte_more_is_not(self) -> None:
        self.assertEqual(len(self.fetch("/exact")), ssdp.MAX_DESCRIPTION_BYTES)
        self.assertIsNone(self.fetch("/big"))

    def test_a_redirect_is_not_followed(self) -> None:
        self.assertIsNone(self.fetch("/redirect"))

    def test_not_200_gives_none(self) -> None:
        self.assertIsNone(self.fetch("/missing"))

    def test_a_slow_server_is_given_up_on(self) -> None:
        started = time.monotonic()
        self.assertIsNone(self.fetch("/slow", timeout_s=0.2))
        self.assertLess(time.monotonic() - started, 0.8)

    def test_a_trickling_server_is_given_up_on(self) -> None:
        started = time.monotonic()
        self.assertIsNone(self.fetch("/trickle", timeout_s=0.3))
        self.assertLess(time.monotonic() - started, 1.0)

    def test_nobody_listening_gives_none(self) -> None:
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            port = unused.getsockname()[1]
        self.assertIsNone(fetch_description("127.0.0.1", port, "/", timeout_s=0.5))


# ---- collect_ssdp (fake search and fetch) ----------------------------------------------

class FakeFetch:
    """Answers fetches from a dict of (host, port, path) -> bytes or None; records each call."""

    def __init__(self, bodies: dict[tuple[str, int, str], bytes | None], delay_s: float = 0.0) -> None:
        self.bodies = bodies
        self.delay_s = delay_s
        self.calls: list[tuple[str, int, str]] = []
        self.lock = threading.Lock()

    def __call__(self, host: str, port: int, path: str) -> bytes | None:
        with self.lock:
            self.calls.append((host, port, path))
        time.sleep(self.delay_s)
        return self.bodies.get((host, port, path))


def fake_search(answers: list[tuple[str, dict[str, str]]]
                ) -> Callable[[float, str | None], list[tuple[str, dict[str, str]]]]:
    return lambda wait_s, interface_ip=None: answers


class CollectTests(unittest.TestCase):

    def test_a_device_becomes_one_evidence_item(self) -> None:
        answers = [(ROKU_IP, answer("http://192.168.1.20:8060/"))]
        fetch = FakeFetch({(ROKU_IP, 8060, "/"): ROKU_DESCRIPTION})
        result = collect_ssdp(sorted(HOSTS), 1.0, search=fake_search(answers), fetch=fetch)
        self.assertTrue(result.available)
        self.assertEqual(result.method, "ssdp")
        self.assertEqual(result.evidence, {ROKU_IP: [RawEvidence(EvidenceType.SSDP, ROKU_VALUE)]})
        self.assertEqual(result.hostnames, {})

    def test_at_most_3_locations_per_host_each_fetched_once(self) -> None:
        answers = [(ROUTER_IP, answer(f"http://192.168.1.1:5000/{n}.xml")) for n in (1, 2, 1, 3, 4)]
        fetch = FakeFetch({(ROUTER_IP, 5000, "/1.xml"): ROUTER_DESCRIPTION})
        result = collect_ssdp(sorted(HOSTS), 1.0, search=fake_search(answers), fetch=fetch)
        self.assertEqual(sorted(fetch.calls), [(ROUTER_IP, 5000, f"/{n}.xml") for n in (1, 2, 3)])
        self.assertEqual(len(result.evidence[ROUTER_IP]), 1)

    def test_the_same_description_twice_is_one_item(self) -> None:
        answers = [(ROKU_IP, answer("http://192.168.1.20:8060/a")), (ROKU_IP, answer("http://192.168.1.20:8060/b"))]
        fetch = FakeFetch({(ROKU_IP, 8060, "/a"): ROKU_DESCRIPTION, (ROKU_IP, 8060, "/b"): ROKU_DESCRIPTION})
        result = collect_ssdp(sorted(HOSTS), 1.0, search=fake_search(answers), fetch=fetch)
        self.assertEqual(len(result.evidence[ROKU_IP]), 1)

    def test_server_header_when_no_description_could_be_read(self) -> None:
        answers = [(ROKU_IP, answer("http://192.168.1.20:8060/", server="Roku/9.4.0 UPnP/1.0"))]
        result = collect_ssdp(sorted(HOSTS), 1.0, search=fake_search(answers), fetch=FakeFetch({}))
        self.assertEqual(result.evidence, {ROKU_IP: [RawEvidence(EvidenceType.SSDP, "server=Roku/9.4.0 UPnP/1.0")]})

    def test_server_header_when_the_location_is_unsafe(self) -> None:
        answers = [(ROKU_IP, answer("http://8.8.8.8/steal", server="Evil/1.0"))]
        fetch = FakeFetch({})
        result = collect_ssdp(sorted(HOSTS), 1.0, search=fake_search(answers), fetch=fetch)
        self.assertEqual(fetch.calls, [])                                   # never fetched
        self.assertEqual(result.evidence[ROKU_IP][0].raw_value, "server=Evil/1.0")

    def test_hosts_outside_the_scan_are_ignored(self) -> None:
        answers = [(OUTSIDE_IP, answer(f"http://{OUTSIDE_IP}/", server="Outside/1.0"))]
        fetch = FakeFetch({(OUTSIDE_IP, 80, "/"): ROKU_DESCRIPTION})
        result = collect_ssdp(sorted(HOSTS), 1.0, search=fake_search(answers), fetch=fetch)
        self.assertEqual((result.evidence, fetch.calls), ({}, []))

    def test_a_hostile_description_gives_only_the_server_header(self) -> None:
        answers = [(ROKU_IP, answer("http://192.168.1.20:8060/", server="Roku/9.4.0"))]
        fetch = FakeFetch({(ROKU_IP, 8060, "/"): BILLION_LAUGHS})
        result = collect_ssdp(sorted(HOSTS), 1.0, search=fake_search(answers), fetch=fetch)
        self.assertEqual(result.evidence[ROKU_IP][0].raw_value, "server=Roku/9.4.0")

    def test_a_semicolon_in_a_value_cannot_add_a_field(self) -> None:
        xml = b"<root><device><manufacturer>Acme; model=Xbox</manufacturer></device></root>"
        answers = [(ROKU_IP, answer("http://192.168.1.20/"))]
        result = collect_ssdp(sorted(HOSTS), 1.0, search=fake_search(answers),
                              fetch=FakeFetch({(ROKU_IP, 80, "/"): xml}))
        self.assertEqual(result.evidence[ROKU_IP][0].raw_value, "manufacturer=Acme, model=Xbox")

    def test_a_search_that_cannot_be_sent_is_unavailable(self) -> None:
        def broken(wait_s: float, interface_ip: str | None = None) -> list[tuple[str, dict[str, str]]]:
            raise OSError(10051, "A socket operation was attempted to an unreachable network")
        result = collect_ssdp(sorted(HOSTS), 1.0, search=broken)
        self.assertFalse(result.available)
        self.assertIn("unreachable network", result.reason)
        self.assertEqual(result.evidence, {})

    def test_the_search_leaves_from_the_hosts_network(self) -> None:
        asked: list[tuple[float, str | None]] = []

        def recording(wait_s: float, interface_ip: str | None = None) -> list[tuple[str, dict[str, str]]]:
            asked.append((wait_s, interface_ip))
            return []

        collect_ssdp(sorted(HOSTS), 3.0, search=recording, local_address=lambda hosts: "192.168.1.212")
        self.assertEqual(asked, [(3.0, "192.168.1.212")])

    def test_nobody_answering_is_not_a_failure(self) -> None:
        result = collect_ssdp(sorted(HOSTS), 1.0, search=fake_search([]))
        self.assertTrue(result.available)
        self.assertEqual(result.evidence, {})

    def test_slow_fetches_are_abandoned_at_the_deadline(self) -> None:
        answers = [(ROKU_IP, answer("http://192.168.1.20:8060/", server="Roku/9.4.0"))]
        fetch = FakeFetch({(ROKU_IP, 8060, "/"): ROKU_DESCRIPTION}, delay_s=2.0)
        started = time.monotonic()
        result = collect_ssdp(sorted(HOSTS), 1.0, search=fake_search(answers), fetch=fetch, fetch_budget_s=0.2)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(result.evidence[ROKU_IP][0].raw_value, "server=Roku/9.4.0")   # the fetch didn't count

    def test_a_fetch_that_crashes_is_treated_as_unread(self) -> None:
        def crashing(host: str, port: int, path: str) -> bytes | None:
            raise RuntimeError("bug")
        answers = [(ROKU_IP, answer("http://192.168.1.20:8060/", server="Roku/9.4.0"))]
        result = collect_ssdp(sorted(HOSTS), 1.0, search=fake_search(answers), fetch=crashing)
        self.assertEqual(result.evidence[ROKU_IP][0].raw_value, "server=Roku/9.4.0")


if __name__ == "__main__":
    unittest.main()
