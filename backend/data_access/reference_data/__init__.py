"""3.2 Reference Data Access: vendors and their OUI prefixes, device types and
device signatures.

    from backend.data_access.reference_data import ReferenceStore, OuiPrefixStore, find_reference_rules

    ReferenceStore(engine, Vendor)      3.2.2 store, 3.2.3 retire, 3.2.4 usage / in_use
    OuiPrefixStore(engine)              3.2.2 for OUI prefixes: add, reassign, find
    find_reference_rules(engine)        3.2.1 everything active, loaded once per scan
"""
from backend.data_access.reference_data.oui_prefixes import (InvalidPrefixError, OuiPrefixStore,
                                                             normalize_prefix)
from backend.data_access.reference_data.reference_rules import ReferenceRules, find_reference_rules
from backend.data_access.reference_data.reference_store import (DuplicateReferenceError,
                                                                ProtectedReferenceError,
                                                                ReferenceStore)

__all__ = [
    "DuplicateReferenceError", "InvalidPrefixError", "OuiPrefixStore", "ProtectedReferenceError",
    "ReferenceRules", "ReferenceStore", "find_reference_rules", "normalize_prefix",
]
