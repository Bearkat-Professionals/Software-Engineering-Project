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
    """'70-13-01-65-8e-f7' -> '70:13:01:65:8E:F7'"""
    return raw.replace("-", ":").upper()


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