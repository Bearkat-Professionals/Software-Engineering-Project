"""Turn the text printed by Windows `arp -a` into ArpEntry records.

Lines are matched by their shape (IP, then MAC, then type), never by the
column headings, because the headings are translated on non-English Windows.
"""
import re

from backend.black_boxes.read_arp_cache.arp_entry import ArpEntry, ArpEntryType

IP = r"\d{1,3}(?:\.\d{1,3}){3}"                    # 10.0.0.1
MAC = r"[0-9A-Fa-f]{2}(?:[-:][0-9A-Fa-f]{2}){5}"   # 70-13-01-65-8e-f7

# "Interface: 10.0.0.2 --- 0x12"  (the word "Interface" may be translated)
INTERFACE_LINE = re.compile(rf"({IP})\s+---\s+0x[0-9A-Fa-f]+")

# "  10.0.0.1     70-13-01-65-8e-f7     dynamic"
ENTRY_LINE = re.compile(rf"^\s*({IP})\s+({MAC})\s+(\S+)\s*$")

# The type word is translated too (German "dynamisch", Spanish "dinámico", ...),
# so it is recognised by how it starts.
DYNAMIC_PREFIXES = ("dyn", "din")
STATIC_PREFIXES = ("stat", "est")


def normalize_mac(raw: str) -> str:
    """'70-13-01-65-8e-f7' or '70:13:1:65:8e:f7' -> '70:13:01:65:8E:F7'"""
    parts = raw.replace("-", ":").split(":")
    return ":".join(part.zfill(2) for part in parts).upper()


def parse_entry_type(word: str) -> ArpEntryType | None:
    word = word.lower()
    if word.startswith(DYNAMIC_PREFIXES):
        return ArpEntryType.DYNAMIC
    if word.startswith(STATIC_PREFIXES):
        return ArpEntryType.STATIC
    return None


def parse_arp_output(text: str) -> list[ArpEntry]:
    """Every entry in the output, each tagged with the interface table it was under."""
    entries: list[ArpEntry] = []
    interface_ip = None

    for line in text.splitlines():
        interface = INTERFACE_LINE.search(line)
        if interface:
            interface_ip = interface.group(1)    # every row below belongs to this adapter
            continue

        entry = ENTRY_LINE.match(line)
        if entry and interface_ip:
            ip, mac, type_word = entry.groups()
            entry_type = parse_entry_type(type_word)
            if entry_type is not None:
                entries.append(ArpEntry(
                    ip_address=ip,
                    mac_address=normalize_mac(mac),
                    interface_ip=interface_ip,
                    entry_type=entry_type,
                ))
    return entries

#Linux
LINUX_DYNAMIC_STATES = frozenset({"REACHABLE", "STALE", "DELAY", "PROBE"})
LINUX_STATIC_STATES = frozenset({"PERMANENT", "NOARP"})

ADDRESS_LINE = re.compile(rf"^\d+:\s+(\S+)\s+inet\s+({IP})/\d+")

PROC_FLAG_PERMANENT = 0x4
ZERO_MAC = "00:00:00:00:00:00"
_MAC_ONLY = re.compile(rf"^{MAC}$")
_IP_ONLY = re.compile(rf"^{IP}$")

def parse_linux_addresses(text: str) -> dict[str, str]:
    """Each device's IPv4 address, from `ip -4 -o addr show`: {"wlp2s0": "192.168.1.50"}.
    The first address of a device wins; "eth0@if5" (containers) is read as "eth0"."""
    addresses: dict[str, str] = {}
    for line in text.splitlines():
        found = ADDRESS_LINE.match(line.strip())
        if found:
            device, ip = found.groups()
            addresses.setdefault(device.split("@")[0], ip)
    return addresses

def parse_linux_neigh(text: str, addresses: dict[str, str]) -> list[ArpEntry]:
    """Entries from `ip -4 neigh show`, matched by shape:

        192.168.1.1 dev wlp2s0 lladdr 70:13:01:65:8e:f7 REACHABLE
        192.168.1.30 dev wlp2s0 lladdr 28:6f:b9:00:00:01 router DELAY
        192.168.1.9 dev wlp2s0  FAILED                        (skipped: no MAC)

        The state is whichever word is a known state, so words after it ("STALE proto zebra",
        from routing software) don't matter. interface_ip is the device's own address, or "".
    """
    entries: list[ArpEntry] = []
    for line in text.splitlines():
        words = line.split()
        if len(words) < 4 or not _IP_ONLY.match(words[0]) or words[1] != "dev":
            continue
        device = words[2]
        mac = words[words.index("lladdr") + 1] if "lladdr" in words[3:-1] else None
        if mac is None or not _MAC_ONLY.match(mac):
            continue
        states = [word for word in words[3:] if word in LINUX_DYNAMIC_STATES or word in LINUX_STATIC_STATES]
        if not states:
            continue                        # FAILED, INCOMPLETE, NONE: no usable entry
        entry_type = ArpEntryType.DYNAMIC if states[0] in LINUX_DYNAMIC_STATES else ArpEntryType.STATIC
        entries.append(ArpEntry(ip_address=words[0], mac_address=normalize_mac(mac),
                                interface_ip=addresses.get(device, ""), entry_type=entry_type))
    return entries


def parse_proc_net_arp(text: str, addresses: dict[str, str]) -> list[ArpEntry]:
    """Entries from /proc/net/arp (the header line is skipped by shape):

        IP address       HW type     Flags       HW address            Mask     Device
        192.168.1.1      0x1         0x2         70:13:01:65:8e:f7     *        wlp2s0

    Flags 0x0 or the all-zero MAC is an incomplete entry, skipped; flag 0x4 is permanent.
    """
    entries: list[ArpEntry] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 6 or not _IP_ONLY.match(fields[0]) or not _MAC_ONLY.match(fields[3]):
            continue
        try:
            flags = int(fields[2], 16)
        except ValueError:
            continue
        mac = normalize_mac(fields[3])
        if flags == 0 or mac == ZERO_MAC:
            continue
        entry_type = ArpEntryType.STATIC if flags & PROC_FLAG_PERMANENT else ArpEntryType.DYNAMIC
        entries.append(ArpEntry(ip_address=fields[0], mac_address=mac,
                                interface_ip=addresses.get(fields[5], ""), entry_type=entry_type))
    return entries

# macOS
MACOS_MAC = re.compile(r"^[0-9A-Fa-f]{1,2}(?::[0-9A-Fa-f]{1,2}){5}$")   # 70:13:1:65:8e:f7 (no leading zeros)
MACOS_LINE = re.compile(rf"\(({IP})\) at (\S+) on (\S+)(.*)$")


def parse_macos_arp(text: str, addresses: dict[str, str]) -> list[ArpEntry]:
    """Entries from macOS `arp -an`, matched by shape: (<IP>) at <MAC> on <interface>

        ? (192.168.1.1) at 70:13:1:65:8e:f7 on en0 ifscope [ethernet]
        ? (192.168.1.7) at (incomplete) on en0 ifscope [ethernet]       (skipped: no MAC yet)
        ? (224.0.0.251) at 1:0:5e:0:0:fb on en0 ifscope permanent [ethernet]

    "permanent" is static, everything else dynamic. Broadcast and multicast
    entries are kept as they are. interface_ip is the interface's own address, or "".
    """
    entries: list[ArpEntry] = []
    for line in text.splitlines():
        found = MACOS_LINE.search(line)
        if not found:
            continue                        # e.g. "-- no entry" lines
        ip, mac, interface, rest = found.groups()
        if not MACOS_MAC.match(mac):
            continue                        # "(incomplete)": no usable entry
        entry_type = ArpEntryType.STATIC if "permanent" in rest.split() else ArpEntryType.DYNAMIC
        entries.append(ArpEntry(ip_address=ip, mac_address=normalize_mac(mac),
                                interface_ip=addresses.get(interface, ""), entry_type=entry_type))
    return entries