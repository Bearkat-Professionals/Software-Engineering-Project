"""SH-4.1 Evaluate Flag: should a device be flagged as possibly suspicious?

Applies spec 7.3, the flagging policy:

    Rule 1  Flag a device that is unidentified, partially identified, or has
            an unknown device type.
    Rule 2  The flag clears when a later scan raises the device to the
            identified level, or when a user marks it trusted.
    Rule 3  A trusted device is not re-flagged unless a new MAC address appears.

A pure function: it takes the level, the trust status and a new-device
indicator, and calls nothing (system design 6.1). SH-4 calls it on every save,
so scans and manual edits flag devices the same way.

"A new MAC address appears" means a new device: the MAC is a device's identity
(DI-01), so a device seen under a new MAC is a new record, and trust given to
the old record doesn't carry over.

An unknown device type never reaches the identified level (spec 7.2:
identified shows the name and type), so rule 1's "unknown device type" is
covered by the level.

    evaluate_flag(IdentificationLevel.PARTIAL, trusted=False, is_new_device=True)   # -> True
"""
from backend.records.enums import IdentificationLevel


def evaluate_flag(level: IdentificationLevel, trusted: bool, is_new_device: bool) -> bool:
    """True if the device should be flagged as possibly suspicious."""
    level = IdentificationLevel(level)          # also accepts "partial"; a typo raises ValueError

    if trusted and not is_new_device:           # rules 2 and 3: trusted, same MAC -> never flagged
        return False
    if level is IdentificationLevel.IDENTIFIED: # rule 2: identified -> not flagged
        return False
    return True                                 # rule 1: unidentified or partially identified
