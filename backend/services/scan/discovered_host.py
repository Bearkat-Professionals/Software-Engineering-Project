# DiscoveredHost is the record every Host Discovery module passes along.
# mac_address is None until a MAC is known.
# is_randomized_mac stays False until 2.2.2.5 checks it.

from dataclasses import dataclass


@dataclass(frozen=True)
class DiscoveredHost:
    ip_address: str
    mac_address: str = None
    is_randomized_mac: bool = False