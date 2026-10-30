"""Short commands for the project's tests and manual tools. Run from the project root.

    py -3.13 dev.py                          list every command
    py -3.13 dev.py test                     run every unit test (add -v for names)
    py -3.13 dev.py chd 192.168.1.0/24       Host Discovery checkpoint
    py -3.13 dev.py backup list              anything after the command goes to the tool
    py -3.13 dev.py chd --help               the tool's own help

On Windows, dev.cmd drops the "py -3.13":   .\\dev chd 192.168.1.0/24
Each command runs the same `python -m ...` module the README lists, so the long
commands keep working.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Command:
    module: str                          # what `python -m` runs
    summary: str                         # shown in the command list
    fixed_args: tuple[str, ...] = ()     # always passed, before the user's own arguments


COMMANDS: dict[str, Command] = {
    "test":   Command("unittest", "run every unit test", ("discover", "tests")),
    "conn":   Command("backend.check_connection", "check the database connection"),
    "crud":   Command("backend.dev_tools.crud_server", "start the CRUD test server (test database)"),
    "ping":   Command("backend.black_boxes.timed_network_request.check_ping",
                      "ping addresses, or --sweep a range (BB-3)"),
    "arp":    Command("backend.black_boxes.read_arp_cache.check_arp_cache",
                      "print this machine's ARP table (BB-1)"),
    "chd":    Command("backend.services.scan.check_host_discovery",
                      "run Host Discovery on a range and list the devices (2.2.2)"),
    "save":   Command("backend.services.scan.check_save_devices",
                      "scan a range and save the devices to the test database (2.2.5.2)"),
    "backup": Command("backend.data_access.backup_recovery.check_backup",
                      "back up, list, verify, restore or compare databases (3.5)"),
    "oui":    Command("database.scripts.load_oui_data",
                      "load the IEEE OUI list (oui.csv) into vendor and oui_prefix (test DB by default)"),
}


def build_command(name: str, extra: list[str]) -> list[str]:
    """The full command line for `dev.py name extra...`. Runs nothing."""
    command = COMMANDS[name]
    return [sys.executable, "-m", command.module, *command.fixed_args, *extra]


def show_commands() -> None:
    print("Usage: py -3.13 dev.py COMMAND [arguments for that tool]\n")
    width = max(len(name) for name in COMMANDS)
    for name, command in COMMANDS.items():
        print(f"  {name:<{width}}  {command.summary}")
    print("\nAdd --help after a command for that tool's own options, e.g. dev.py chd --help")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help", "help"):
        show_commands()
        return 0

    name, extra = args[0], args[1:]
    if name not in COMMANDS:
        print(f"Unknown command {name!r}.\n", file=sys.stderr)
        show_commands()
        return 2

    try:
        return subprocess.run(build_command(name, extra), cwd=ROOT).returncode
    except KeyboardInterrupt:          # Ctrl+C, e.g. stopping the CRUD server
        return 130


if __name__ == "__main__":
    sys.exit(main())
