"""BB-3 ping probe: one ICMP echo through the OS's ping command

Raw ICMP sockets need administrator rights; the OS ping command does not
(design section 12, NFR-04). Everything OS-specific about ping lives here:
the flags each OS uses and how to tell a real reply from an error message.
"""

import ipaddress
import math
import platform
import subprocess
import time

from backend.black_boxes.timed_network_request.probe_result import ProbeStatus, ProbeResult

# ping needs a moment to start; a process still running after that is hung (SEC-15).
_PROCESS_GRACE_S = 1.0

# Stops a console window flashing up for every ping on Windows. The constant only
# exists on Windows, so other systems get 0 (no flags).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

def build_ping_command(ip: str, timeout_s: float, system: str) -> list[str]:
    """The command that sends one ping and waits at most timesout_s for the reply
    
        system is platform.system(): "Windows", "Darwin" (macOS), or "Linux".
    """

    if system == "Windows":
        return ["ping", "-n", "1", "-w", str(int(timeout_s * 1000)), ip]
    if system == "Darwin":
        return ["ping", "-c", "1", "-W", str(int(timeout_s * 1000)), ip]
    return ["ping", "-c", "1", "-W", str(max(1, math.ceil(timeout_s))), ip]

def read_ping_reply(returncode: int, output: str, system: str) -> bool:
    """True if the ping got a real reply from the target."""

    if system == "Windows":
        # Windows also exits 0 for "Destination host unreachable" and "TTL expired
        # in transit". Only a real reply line contains "TTL=".
        return returncode == 0 and "TTL=" in output
    return returncode == 0

def ping(ip: str, timeout_s: float = 1.0) -> ProbeResult:
    """Ping one IPv4 address once. Never raises: problems come back as a status."""
    start = time.perf_counter()

    def result(status: ProbeStatus, detail: str | None = None) -> ProbeResult:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return ProbeResult(ip, status, elapsed_ms, detail)

    try:
        ipaddress.IPv4Address(ip)       # also blocks values ping would read as a flag
    except ValueError:
        return result(ProbeStatus.UNAVAILABLE, f"{ip!r} is not an IPv4 address")

    system = platform.system()
    command = build_ping_command(ip, timeout_s, system)

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout_s + _PROCESS_GRACE_S,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return result(ProbeStatus.TIMEOUT, f"ping did not finish within {timeout_s + _PROCESS_GRACE_S:.1f} s")
    except OSError as exc:
        return result(ProbeStatus.UNAVAILABLE, f"ping could not run: {exc}")

    output = completed.stdout.decode(errors="replace")  # bytes -> text: odd characters can't crash it
    if read_ping_reply(completed.returncode, output, system):
        return result(ProbeStatus.OK)
    return result(ProbeStatus.NO_REPLY)