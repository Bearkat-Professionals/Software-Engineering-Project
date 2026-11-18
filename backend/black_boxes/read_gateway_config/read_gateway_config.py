"""BB-2: find this computer's default gateway without admin rights.

    read_gateway()                   -> Gateway(ip="192.168.1.1", interface="192.168.1.212"), or None
    read_gateway("192.168.1.212")    -> the default route on the adapter with that address
                                        (local_address_for(hosts) gives the scan's one)
    read_default_routes()            -> every default route found, for checks and logs

None means no gateway was found or the routes couldn't be read. Both are normal: it
never raises for a missing command, a timeout or a failed run (only for a local_ip
that isn't an IP). With a VPN connected there are several default routes, and the
VPN's often comes first, so local_ip picks the one facing the scan's network.

    Windows   route print -4 0.0.0.0
    Linux     ip -4 route show default (+ ip -4 -o addr show), or /proc/net/route
    macOS     route -n get default (+ ifconfig, and -ifscope <adapter> when it isn't the
              scan-facing one). Built from recorded output; not yet run on a real Mac.
"""
import ipaddress
import os
import subprocess
import sys

from backend.black_boxes.read_gateway_config.gateway import DefaultRoute, Gateway
from backend.black_boxes.read_gateway_config.parser import (choose_gateway, parse_linux_addresses, parse_linux_routes,
                                                           parse_macos_ifconfig, parse_macos_route,
                                                           parse_proc_net_route, parse_windows_routes)

TIMEOUT_SECONDS = 10
PROC_NET_ROUTE = "/proc/net/route"
_MISSING = object()                                     
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  

def read_gateway(local_ip: str | None = None) -> Gateway | None:
    routes = read_default_routes(local_ip)
    return choose_gateway(routes, local_ip) if routes else None

def read_default_routes(local_ip: str | None = None) -> list[DefaultRoute] | None:
    """Every default route this system lists; [] when there are none; None when the
    routing table couldn't be read."""
    if local_ip is not None:
        ipaddress.IPv4Address(local_ip)                  
    if sys.platform == "win32":
        return _read_windows()
    if sys.platform == "linux":
        return _read_linux()
    if sys.platform == "darwin":
        return _read_macos(local_ip)
    return None

def _read_windows() -> list[DefaultRoute] | None:
    output = _run(["route", "print", "-4", "0.0.0.0"], windows=True)
    return parse_windows_routes(output) if isinstance(output, str) else None

def _read_linux() -> list[DefaultRoute] | None:
    output = _run(["ip", "-4", "route", "show", "default"])
    if output is _MISSING:
        try:
            with open(PROC_NET_ROUTE, encoding="ascii", errors="replace") as table:
                return parse_proc_net_route(table.read())
        except OSError:
            return None
    if not isinstance(output, str):
        return None
    addresses = _run(["ip", "-4", "-o", "addr", "show"])
    return parse_linux_routes(output, parse_linux_addresses(addresses) if isinstance(addresses, str) else {})

def _read_macos(local_ip: str | None) -> list[DefaultRoute] | None:
    """The default route, plus the scan-facing adapter's own (-ifscope) when it's another one."""
    output = _run(["route", "-n", "get", "default"])
    if not isinstance(output, str):
        return None
    ifconfig = _run(["ifconfig"])
    addresses = parse_macos_ifconfig(ifconfig) if isinstance(ifconfig, str) else {}
    routes = [route for route in [parse_macos_route(output)] if route is not None]
    facing = next((name for name, ip in addresses.items() if ip == local_ip), None) if local_ip else None
    if facing and all(route.interface != facing for route in routes):
        scoped = _run(["route", "-n", "get", "-ifscope", facing, "default"])
        route = parse_macos_route(scoped) if isinstance(scoped, str) else None
        if route is not None:
            routes.append(route)
    return [DefaultRoute(route.gateway, route.interface, addresses.get(route.interface)) for route in routes]

def _run(command: list[str], windows: bool = False) -> str | object | None:
    """The command's output; _MISSING if it isn't installed; None if it failed or hung. No shell.
    Windows decodes with the console code page; elsewhere LC_ALL=C keeps the output untranslated."""
    options = ({"encoding": "oem", "creationflags": _NO_WINDOW} if windows
               else {"encoding": "utf-8", "env": {**os.environ, "LC_ALL": "C"}})
    try:
        result = subprocess.run(command, capture_output=True, errors="replace", timeout=TIMEOUT_SECONDS, **options)
    except FileNotFoundError:
        return _MISSING
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout
