"""BB-1: read the host's ARP table without admin rights.

    read_arp_cache()            -> every entry
    read_arp_cache("10.0.0.1")  -> just that IP's entry ([] if it isn't there)

Returns None when the table can't be read ("unavailable"); never raises for
a missing command, a timeout or a failed run. Only Windows is built so far;
macOS and Linux adapters come in M6 and plug in behind this same function.
"""
import ipaddress
import subprocess
import sys

from backend.black_boxes.read_arp_cache.arp_entry import ArpEntry
from backend.black_boxes.read_arp_cache.parser import parse_arp_output

TIMEOUT_SECONDS = 10

# Stops a console window flashing up on Windows. The constant only exists on
# Windows, so other systems get 0 (no flags) - same approach as BB-3.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def read_arp_cache(ip: str | None = None) -> list[ArpEntry] | None:
    if ip is not None:
        ipaddress.ip_address(ip)    # raises ValueError for anything that isn't a real IP
    if sys.platform == "win32":
        return _read_windows(ip)
    return None                     # other operating systems: not built yet (M6)


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