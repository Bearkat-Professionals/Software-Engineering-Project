"""Turn each system's routing output into DefaultRoute records, and choose one.

    Windows  `route print -4 0.0.0.0`          parse_windows_routes
    Linux    `ip -4 route show default`        parse_linux_routes (with parse_linux_addresses)
             `/proc/net/route`                 parse_proc_net_route (when `ip` is missing)
    macOS    `route -n get default`            parse_macos_route (with parse_macos_ifconfig)

Pure functions over text, so every format is tested from recorded output on any system.
Lines are matched by their shape, never by headings that Windows translates.
"""
import ipaddress
import re

from backend.black_boxes.read_gateway_config.gateway import DefaultRoute, Gateway

IP = r"\d{1,3}(?:\.\d{1,3}){3}"
_IP_ONLY = re.compile(rf"^{IP}$")
_HEX8 = re.compile(r"^[0-9A-Fa-f]{8}$")
RTF_UP, RTF_GATEWAY = 0x1, 0x2                      

_LINUX_ADDRESS = re.compile(rf"^\d+:\s+(\S+)\s+inet\s+({IP})/\d+")
_MACOS_INTERFACE = re.compile(r"^([A-Za-z0-9]+):\s+flags=")
_MACOS_INET = re.compile(rf"^\s+inet\s+({IP})\s")

def _is_ip(text: str) -> bool:
    if not _IP_ONLY.match(text):
        return False
    try:
        ipaddress.IPv4Address(text)
    except ValueError:
        return False
    return True

#Windows
def parse_windows_routes(text: str) -> list[DefaultRoute]:
    """The default routes in the active table: rows of five columns,

        0.0.0.0          0.0.0.0      192.168.1.1    192.168.1.212     25

    The "Persistent Routes" rows have four columns, so they never match. A gateway
    of "On-link" (translated on other languages) isn't an IP, so it isn't a gateway."""
    routes: list[DefaultRoute] = []
    for line in text.splitlines():
        words = line.split()
        if (len(words) != 5 or words[0] != "0.0.0.0" or words[1] != "0.0.0.0"
                or not _is_ip(words[2]) or not _is_ip(words[3]) or not words[4].isdigit()):
            continue
        routes.append(DefaultRoute(gateway=words[2], interface=words[3], local_ip=words[3], metric=int(words[4])))
    return routes

#Linux
def parse_linux_addresses(text: str) -> dict[str, str]:
    """Device name -> its IPv4 address, from `ip -4 -o addr show` ("eth0@if8" is "eth0")."""
    addresses: dict[str, str] = {}
    for line in text.splitlines():
        found = _LINUX_ADDRESS.match(line.strip())
        if found:
            addresses.setdefault(found.group(1).split("@")[0], found.group(2))
    return addresses

def parse_linux_routes(text: str, addresses: dict[str, str]) -> list[DefaultRoute]:
    """From `ip -4 route show default`:

        default via 192.168.1.1 dev wlp2s0 proto dhcp src 192.168.1.50 metric 600
        default via 10.8.0.1 dev tun0 metric 50
        default dev wg0 scope link                      (no `via`: no gateway, skipped)

    local_ip is `src`, or the device's address. No `metric` means 0, as the kernel does."""
    routes: list[DefaultRoute] = []
    for line in text.splitlines():
        words = line.split()
        if not words or words[0] != "default":
            continue
        values = {key: words[i + 1] for i, key in enumerate(words[:-1]) if key in ("via", "dev", "src", "metric")}
        gateway, device = values.get("via"), values.get("dev")
        if gateway is None or device is None or not _is_ip(gateway):
            continue
        metric = values.get("metric", "0")
        routes.append(DefaultRoute(gateway=gateway, interface=device,
                                   local_ip=values.get("src") or addresses.get(device),
                                   metric=int(metric) if metric.isdigit() else None))
    return routes

def hex_to_ip(value: str) -> str:
    """/proc/net/route stores addresses as little-endian hex: "0101A8C0" -> "192.168.1.1"."""
    return ".".join(str(int(value[i:i + 2], 16)) for i in (6, 4, 2, 0))

def parse_proc_net_route(text: str) -> list[DefaultRoute]:
    """The default routes in /proc/net/route (used when `ip` isn't installed):

        Iface   Destination  Gateway   Flags  RefCnt  Use  Metric  Mask      ...
        wlp2s0  00000000     0101A8C0  0003   0       0    600     00000000  ...

    A default route has destination and mask 0, and the UP and GATEWAY flags."""
    routes: list[DefaultRoute] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 8 or not all(_HEX8.match(fields[i]) for i in (1, 2, 7)):
            continue                                        # the header, or a line cut short
        try:
            flags, metric = int(fields[3], 16), int(fields[6])
        except ValueError:
            continue
        if fields[1] != "00000000" or fields[7] != "00000000" or (flags & (RTF_UP | RTF_GATEWAY)) != RTF_UP | RTF_GATEWAY:
            continue
        routes.append(DefaultRoute(gateway=hex_to_ip(fields[2]), interface=fields[0], metric=metric))
    return routes

#macOS
def parse_macos_route(text: str) -> DefaultRoute | None:
    """From `route -n get default` (or `... -ifscope en0 default`):

           route to: default
        destination: default
            gateway: 192.168.1.1
          interface: en0

    None when there's no default route ("not in table") or the gateway isn't an IP."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, colon, value = line.partition(":")
        if colon:
            values.setdefault(key.strip(), value.strip())
    gateway, interface = values.get("gateway", ""), values.get("interface", "")
    if not _is_ip(gateway) or not interface:
        return None
    return DefaultRoute(gateway=gateway, interface=interface)

def parse_macos_ifconfig(text: str) -> dict[str, str]:
    """Adapter name -> its first IPv4 address, from `ifconfig`."""
    addresses: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        interface = _MACOS_INTERFACE.match(line)
        if interface:
            current = interface.group(1)
            continue
        inet = _MACOS_INET.match(line)
        if inet and current:
            addresses.setdefault(current, inet.group(1))
    return addresses

#choosing one
def explain_choice(routes: list[DefaultRoute], local_ip: str | None = None) -> tuple[Gateway | None, str]:
    """The gateway and why: the route on the adapter facing local_ip (the scan's network);
    otherwise the lowest metric; otherwise the first. (None, why) when there's no route."""
    if not routes:
        return None, "no default route"
    if local_ip:
        facing = [route for route in routes if local_ip in (route.local_ip, route.interface)]
        if facing:
            chosen = _lowest_metric(facing)
            return Gateway(chosen.gateway, chosen.interface), f"on the adapter with {local_ip}"
    with_metric = [route for route in routes if route.metric is not None]
    if with_metric:
        chosen = _lowest_metric(with_metric)
        why = "the only default route" if len(routes) == 1 else f"the lowest metric ({chosen.metric})"
    else:
        chosen = routes[0]
        why = "the only default route" if len(routes) == 1 else "the first default route"
    if local_ip:
        why += f"; no route is on the adapter with {local_ip}"
    return Gateway(chosen.gateway, chosen.interface), why

def choose_gateway(routes: list[DefaultRoute], local_ip: str | None = None) -> Gateway | None:
    return explain_choice(routes, local_ip)[0]

def _lowest_metric(routes: list[DefaultRoute]) -> DefaultRoute:
    """The lowest metric; a tie keeps the first listed. A route without a metric comes last."""
    return min(routes, key=lambda route: (route.metric is None, route.metric or 0))
