"""BB-1: read the host's ARP table without admin rights.

    read_arp_cache()            -> every entry
    read_arp_cache("10.0.0.1")  -> just that IP's entry ([] if it isn't there)

Returns None when the table can't be read ("unavailable"); never raises for
a missing command, a timeout or a failed run.

    Windows   arp -a
    Linux     ip -4 neigh show, or /proc/net/arp where `ip` is missing (#174)
    macOS     arp -an, or arp -n <ip>
    others    None for now
"""
import ipaddress
import os
import re
import subprocess
import sys

from backend.black_boxes.read_arp_cache.arp_entry import ArpEntry
from backend.black_boxes.read_arp_cache.parser import (parse_arp_output, parse_linux_addresses, parse_linux_neigh,
                                                      parse_macos_arp, parse_proc_net_arp)

TIMEOUT_SECONDS = 10
PROC_NET_ARP = "/proc/net/arp"
_MISSING = object()                 # _run_linux: the command isn't installed

# Stops a console window flashing up on Windows. The constant only exists on
# Windows, so other systems get 0 (no flags) - same approach as BB-3.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def read_arp_cache(ip: str | None = None) -> list[ArpEntry] | None:
    if ip is not None:
        ipaddress.ip_address(ip)    # raises ValueError for anything that isn't a real IP
    if sys.platform == "win32":
        return _read_windows(ip)
    if sys.platform == "linux":
        return _read_linux(ip)
    if sys.platform == "darwin":
        return _read_macos(ip)
    return None                     


def _read_windows(ip: str | None) -> list[ArpEntry] | None:
    command = ["arp", "-a"] + ([ip] if ip else [])
    try:
        result = subprocess.run(
            command,
            capture_output=True,          # collect the output instead of printing it
            encoding="oem",               # console code page, so translated text decodes
            errors="replace",             # never crash on an odd character
            timeout=TIMEOUT_SECONDS,
            creationflags=_NO_WINDOW,             # no console window pops up
        )
    except (OSError, subprocess.TimeoutExpired):
        return None                       # arp missing, blocked, or hung
    if result.returncode != 0:
        return None                       # arp ran but failed
    return parse_arp_output(result.stdout)


def _read_linux(ip: str | None) -> list[ArpEntry] | None:
    """`ip -4 neigh show` (and `ip -4 -o addr show` for each device's own address).
    Where `ip` isn't installed, /proc/net/arp instead. None only when neither can be read."""
    neigh = _run_linux(["ip", "-4", "neigh", "show"] + (["to", ip] if ip else []))
    if neigh is _MISSING:
        return _read_proc_net_arp(ip)
    if neigh is None:
        return None                       # ip is there but failed or hung
    addresses = _run_linux(["ip", "-4", "-o", "addr", "show"])
    devices = parse_linux_addresses(addresses) if isinstance(addresses, str) else {}   # no map: interface_ip ""
    return _only(parse_linux_neigh(neigh, devices), ip)


def _read_proc_net_arp(ip: str | None) -> list[ArpEntry] | None:
    try:
        with open(PROC_NET_ARP, encoding="ascii", errors="replace") as table:
            text = table.read()
    except OSError:
        return None                       # no /proc either (or not allowed to read it)
    return _only(parse_proc_net_arp(text, {}), ip)


def _run_linux(command: list[str]) -> str | object | None:
    """The command's output; _MISSING if it isn't installed; None if it failed or hung.
    LC_ALL=C keeps the output in the plain form the parser expects. No shell."""
    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace",
                                timeout=TIMEOUT_SECONDS, env={**os.environ, "LC_ALL": "C"})
    except FileNotFoundError:
        return _MISSING
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _only(entries: list[ArpEntry], ip: str | None) -> list[ArpEntry]:
    """Just the asked-for IP's entries ([] if it isn't there), or all of them."""
    return entries if ip is None else [entry for entry in entries if entry.ip_address == ip]


def _read_macos(ip: str | None) -> list[ArpEntry] | None:
    """`arp -an` (or `arp -n <ip>`), and `ipconfig getifaddr <if>` for each adapter's own address.
    "-- no entry" (exit code 1) means the table was read and is empty for that IP: [], not None."""
    result = _run_macos(["arp", "-n", ip] if ip else ["arp", "-an"])
    if result is None:
        return None                       # arp missing, blocked or hung
    if result.returncode == 1 and "no entry" in result.stdout:
        return []                         # "192.168.1.99 (192.168.1.99) -- no entry"
    if result.returncode != 0:
        return None                       # arp ran but failed
    addresses: dict[str, str] = {}
    for interface in sorted(set(re.findall(r"\) at \S+ on (\S+)", result.stdout))):
        own = _run_macos(["ipconfig", "getifaddr", interface])      # once per adapter seen
        addresses[interface] = own.stdout.strip() if own is not None and own.returncode == 0 else ""
    return _only(parse_macos_arp(result.stdout, addresses), ip)


def _run_macos(command: list[str]) -> subprocess.CompletedProcess | None:
    """The finished command, or None if it isn't installed, is blocked or hangs.
    LC_ALL=C keeps the output in the plain form the parser expects. No shell."""
    try:
        return subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace",
                              timeout=TIMEOUT_SECONDS, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return None