"""Unit tests for the scan settings (#81). No network, no database.

    python -m unittest discover tests
"""
from __future__ import annotations

import dataclasses
import json
import os
import unittest
from typing import Any
from unittest import mock

from backend.black_boxes.timed_network_request import DEFAULT_WORKERS, MAX_WORKERS
from backend.services.scan.scan_settings import (DEFAULT_PROBE_TIMEOUT_S, MAX_PROBE_TIMEOUT_S, TIMEOUT_KEY,
                                                  WORKERS_KEY, ScanSettings)


class ScanSettingsValueTests(unittest.TestCase):
    def test_defaults(self) -> None:
        settings = ScanSettings()
        self.assertEqual((settings.probe_workers, settings.probe_timeout_s),
                         (DEFAULT_WORKERS, DEFAULT_PROBE_TIMEOUT_S))

    def test_edges_are_allowed(self) -> None:
        for workers in (1, MAX_WORKERS):
            with self.subTest(workers=workers):
                self.assertEqual(ScanSettings(probe_workers=workers).probe_workers, workers)
        for timeout in (0.001, MAX_PROBE_TIMEOUT_S):
            with self.subTest(timeout=timeout):
                self.assertEqual(ScanSettings(probe_timeout_s=timeout).probe_timeout_s, timeout)

    def test_whole_number_timeout_becomes_a_float(self) -> None:
        settings = ScanSettings(probe_timeout_s=2)
        self.assertEqual(settings.probe_timeout_s, 2.0)
        self.assertIsInstance(settings.probe_timeout_s, float)

    def test_bad_worker_counts_are_refused(self) -> None:
        bad_counts: list[Any] = [0, -1, MAX_WORKERS + 1, True, 2.0, "32", None]
        for workers in bad_counts:
            with self.subTest(workers=workers):
                with self.assertRaisesRegex(ValueError, f"from 1 to {MAX_WORKERS}"):
                    ScanSettings(probe_workers=workers)

    def test_bad_timeouts_are_refused(self) -> None:
        bad_timeouts: list[Any] = [0, -1, MAX_PROBE_TIMEOUT_S + 0.01, float("nan"), float("inf"), True, "1", None]
        for timeout in bad_timeouts:
            with self.subTest(timeout=timeout):
                with self.assertRaisesRegex(ValueError, "at most"):
                    ScanSettings(probe_timeout_s=timeout)

    def test_settings_cannot_change_after_creation(self) -> None:
        settings = ScanSettings()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            settings.probe_workers = 64         # type: ignore[misc]

    def test_configuration_survives_json(self) -> None:
        configuration = ScanSettings(probe_workers=64, probe_timeout_s=0.5).to_configuration()
        self.assertEqual(configuration, {"probe_workers": 64, "probe_timeout_s": 0.5})
        self.assertEqual(json.loads(json.dumps(configuration)), configuration)


class ScanSettingsFromEnvTests(unittest.TestCase):
    def test_missing_or_blank_keys_keep_the_defaults(self) -> None:
        for environ in ({}, {WORKERS_KEY: "", TIMEOUT_KEY: "   "}):
            with self.subTest(environ=environ):
                self.assertEqual(ScanSettings.from_env(environ), ScanSettings())

    def test_values_are_read_and_trimmed(self) -> None:
        settings = ScanSettings.from_env({WORKERS_KEY: " 64 ", TIMEOUT_KEY: "0.5"})
        self.assertEqual(settings, ScanSettings(probe_workers=64, probe_timeout_s=0.5))

    def test_unreadable_values_name_their_key(self) -> None:
        cases = [({WORKERS_KEY: "abc"}, WORKERS_KEY),
                 ({WORKERS_KEY: "-1"}, WORKERS_KEY),
                 ({WORKERS_KEY: "3.5"}, WORKERS_KEY),
                 ({TIMEOUT_KEY: "fast"}, TIMEOUT_KEY)]
        for environ, key in cases:
            with self.subTest(environ=environ):
                with self.assertRaisesRegex(ValueError, key):
                    ScanSettings.from_env(environ)

    def test_out_of_range_values_name_their_key(self) -> None:
        cases = [({WORKERS_KEY: "0"}, WORKERS_KEY),
                 ({WORKERS_KEY: str(MAX_WORKERS + 1)}, WORKERS_KEY),
                 ({TIMEOUT_KEY: "10"}, TIMEOUT_KEY),
                 ({TIMEOUT_KEY: "nan"}, TIMEOUT_KEY)]
        for environ, key in cases:
            with self.subTest(environ=environ):
                with self.assertRaisesRegex(ValueError, f"^{key} in .env"):
                    ScanSettings.from_env(environ)

    def test_reads_the_real_environment_by_default(self) -> None:
        with mock.patch.dict(os.environ, {WORKERS_KEY: "16", TIMEOUT_KEY: "2"}):
            self.assertEqual(ScanSettings.from_env(), ScanSettings(probe_workers=16, probe_timeout_s=2.0))


if __name__ == "__main__":
    unittest.main()
