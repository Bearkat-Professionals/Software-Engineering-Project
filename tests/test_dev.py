"""Tests for dev.py, the short-command runner. Nothing is actually run:
subprocess.run is replaced by a mock that records the command.

    py -3.13 dev.py test
"""
from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import dev


def run_main(args: list[str]) -> tuple[int, str]:
    """Call dev.main(args) and return its exit code and everything it printed."""
    output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        code = dev.main(args)
    return code, output.getvalue()


def finished(returncode: int) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(args=[], returncode=returncode)


class CommandTableTests(unittest.TestCase):
    def test_every_command_points_at_a_real_module(self) -> None:
        for name, command in dev.COMMANDS.items():
            with self.subTest(command=name):
                self.assertIsNotNone(importlib.util.find_spec(command.module),
                                     f"dev.py {name}: no module {command.module}")

    def test_every_command_has_a_summary(self) -> None:
        for name, command in dev.COMMANDS.items():
            with self.subTest(command=name):
                self.assertTrue(command.summary.strip())


class BuildCommandTests(unittest.TestCase):
    def test_short_name_becomes_the_long_command(self) -> None:
        self.assertEqual(dev.build_command("chd", ["192.168.1.0/24"]),
                         [sys.executable, "-m", "backend.services.scan.check_host_discovery", "192.168.1.0/24"])

    def test_tool_options_pass_through_untouched(self) -> None:
        cases = [
            ("ping",   ["192.168.1.1", "--sweep", "192.168.1.0/24"]),
            ("backup", ["full", "--database", "network_device_scanner"]),
            ("chd",    ["--help"]),
        ]
        for name, extra in cases:
            with self.subTest(command=name):
                self.assertEqual(dev.build_command(name, extra)[3:], extra)

    def test_fixed_arguments_come_before_the_users(self) -> None:
        self.assertEqual(dev.build_command("test", ["-v"]),
                         [sys.executable, "-m", "unittest", "discover", "tests", "-v"])

    def test_no_extra_arguments(self) -> None:
        self.assertEqual(dev.build_command("arp", []),
                         [sys.executable, "-m", "backend.black_boxes.read_arp_cache.check_arp_cache"])


class MainTests(unittest.TestCase):
    def test_no_command_lists_every_command(self) -> None:
        for args in ([], ["help"], ["-h"], ["--help"]):
            with self.subTest(args=args):
                code, printed = run_main(args)
                self.assertEqual(code, 0)
                for name in dev.COMMANDS:
                    self.assertIn(name, printed)

    def test_unknown_command_is_an_error_and_runs_nothing(self) -> None:
        with mock.patch("dev.subprocess.run") as run:
            code, printed = run_main(["chdd"])
        self.assertEqual(code, 2)
        self.assertIn("Unknown command 'chdd'", printed)
        run.assert_not_called()

    def test_runs_the_tool_from_the_project_root(self) -> None:
        with mock.patch("dev.subprocess.run", return_value=finished(0)) as run:
            run_main(["backup", "list"])
        self.assertEqual(run.call_args.args[0], dev.build_command("backup", ["list"]))
        self.assertEqual(run.call_args.kwargs["cwd"], dev.ROOT)

    def test_exit_code_comes_from_the_tool(self) -> None:
        for returncode in (0, 1, 3):
            with self.subTest(returncode=returncode):
                with mock.patch("dev.subprocess.run", return_value=finished(returncode)):
                    code, _ = run_main(["test"])
                self.assertEqual(code, returncode)

    def test_ctrl_c_exits_quietly_with_130(self) -> None:
        with mock.patch("dev.subprocess.run", side_effect=KeyboardInterrupt):
            code, printed = run_main(["crud"])
        self.assertEqual((code, printed), (130, ""))


class WindowsWrapperTests(unittest.TestCase):
    def test_dev_cmd_forwards_everything_to_dev_py(self) -> None:
        text = (dev.ROOT / "dev.cmd").read_text()
        self.assertIn('"%~dp0dev.py" %*', text)
        self.assertIn("exit /b %ERRORLEVEL%", text)


if __name__ == "__main__":
    unittest.main()