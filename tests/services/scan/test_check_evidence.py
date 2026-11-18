"""Tests for the .\\dev ev checkpoint's pure parts (#151). No network.

    py -m unittest tests.services.scan.test_check_evidence -v
"""
from __future__ import annotations

import unittest

from backend.records.enums import EvidenceType
from backend.services.scan.check_evidence import default_scope, report_lines
from backend.services.scan.collect_evidence import CollectedEvidence
from backend.services.scan.evidence import RawEvidence


class CheckEvidenceTests(unittest.TestCase):
    def test_the_default_scope_is_the_24_around_this_computer(self) -> None:
        self.assertEqual(default_scope("192.168.1.212"), "192.168.1.0/24")
        self.assertEqual(default_scope("10.0.5.7"), "10.0.5.0/24")

    def test_each_host_sorted_by_ip_with_its_hostname_and_evidence(self) -> None:
        collected = CollectedEvidence(
            {"192.168.1.10": (RawEvidence(EvidenceType.SSDP, "manufacturer=Roku"),),
             "192.168.1.9": (RawEvidence(EvidenceType.MDNS, "service=_ipp._tcp; name=Printer"),)},
            {"192.168.1.9": "office-printer", "192.168.1.30": "only-a-name"},
            (("mdns", "couldn't listen on UDP port 5353 (in use)"),))
        self.assertEqual(report_lines(collected), [
            "  192.168.1.9      hostname: office-printer",
            "      mdns         service=_ipp._tcp; name=Printer",
            "  192.168.1.10     hostname: (none)",
            "      ssdp         manufacturer=Roku",
            "  192.168.1.30     hostname: only-a-name",
            "  UNAVAILABLE  mdns: couldn't listen on UDP port 5353 (in use)",
        ])

    def test_nothing_found_prints_nothing(self) -> None:
        self.assertEqual(report_lines(CollectedEvidence({}, {}, ())), [])


if __name__ == "__main__":
    unittest.main()