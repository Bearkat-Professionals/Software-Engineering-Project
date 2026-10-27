"""3.2.2 Store Reference Record, for OUI prefixes.

An OUI prefix (the first half of a MAC address, e.g. "28:6F:B9") belongs to
one vendor; a vendor can own many. Prefixes have no status of their own: a
retired vendor's prefixes stop matching because the vendor is retired (3.2.1).

Prefixes are stored in the same form as the MACs on devices (uppercase, colons),
so a device's vendor is found from the first 8 characters of its MAC.

    prefixes = OuiPrefixStore(engine)
    prefixes.add("28-6f-b9", vendor_id=acme.vendor_id, changed_by=admin_id)   # stored as 28:6F:B9
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Generator

from backend.data_access.audit_trail.audit_trail import AuditTrail
from backend.data_access.database_engine import DatabaseEngine
from backend.data_access.reference_data.reference_store import (UNKNOWN_ID, DuplicateReferenceError,
                                                                ProtectedReferenceError)
from backend.data_access.repository import RecordNotFoundError, Repository
from backend.records import OuiPrefix, Vendor
from backend.records.enums import AuditAction

_SEPARATORS = re.compile(r"[:\-.\s]")
_SIX_HEX = re.compile(r"[0-9A-F]{6}")


class InvalidPrefixError(ValueError):
    """The text is not an OUI prefix (6 hex digits, optionally separated). Nothing was saved."""


def normalize_prefix(text: str) -> str:
    """'28-6f-b9', '286FB9' or '28:6F:B9' -> '28:6F:B9'. Raises InvalidPrefixError otherwise."""
    digits = _SEPARATORS.sub("", text or "").upper()
    if not _SIX_HEX.fullmatch(digits):
        raise InvalidPrefixError(f"'{text}' is not an OUI prefix (expected 6 hex digits, like 28:6F:B9)")
    return f"{digits[0:2]}:{digits[2:4]}:{digits[4:6]}"


class OuiPrefixStore:
    def __init__(self, engine: DatabaseEngine):
        self._engine = engine
        self._prefixes = Repository(engine, OuiPrefix)
        self._vendors = Repository(engine, Vendor)
        self._audit = AuditTrail(engine)

    # ---- finding ----------------------------------------------------------------

    def find(self, prefix: str, unit: Any = None) -> OuiPrefix | None:
        matches = self._prefixes.list(unit, oui_prefix=normalize_prefix(prefix))
        return matches[0] if matches else None

    def for_vendor(self, vendor_id: int, unit: Any = None) -> list[OuiPrefix]:
        """Every prefix one vendor owns."""
        return self._prefixes.list(unit, vendor_id=vendor_id)

    # ---- 3.2.2 Store Reference Record -------------------------------------------

    def add(self, prefix: str, vendor_id: int, changed_by: int, unit: Any = None) -> OuiPrefix:
        """Give a vendor a new prefix, plus its audit entry.

        Raises InvalidPrefixError, DuplicateReferenceError (the prefix already
        belongs to a vendor, DI-07), RecordNotFoundError (no such vendor) or
        ProtectedReferenceError (the "unknown" vendor). Nothing is saved then.
        """
        normalized = normalize_prefix(prefix)
        with _unit_of_work(self._engine, unit) as cursor:
            self._check_vendor(vendor_id, cursor)
            existing = self._prefixes.list(cursor, oui_prefix=normalized)
            if existing:
                raise DuplicateReferenceError(
                    f"OUI prefix {normalized} already belongs to vendor {existing[0].vendor_id}")
            created = self._prefixes.create(OuiPrefix(oui_prefix=normalized, vendor_id=vendor_id), cursor)
            self._audit.record(changed_by, AuditAction.INSERT, None, created, cursor)
        return created

    def reassign(self, prefix: str, vendor_id: int, changed_by: int, unit: Any = None) -> OuiPrefix:
        """Move an existing prefix to another vendor (a correction), plus its audit entry."""
        normalized = normalize_prefix(prefix)
        with _unit_of_work(self._engine, unit) as cursor:
            self._check_vendor(vendor_id, cursor)
            matches = self._prefixes.list(cursor, oui_prefix=normalized)
            if not matches:
                raise RecordNotFoundError(f"OUI prefix {normalized} does not exist")
            current = matches[0]
            if current.vendor_id == vendor_id:
                return current
            old = self._prefixes.get(current.pk, cursor)      # a separate copy for the audit's old value
            current.vendor_id = vendor_id
            self._prefixes.update(current, cursor)
            self._audit.record(changed_by, AuditAction.UPDATE, old, current, cursor)
        return current

    # ---- helpers ---------------------------------------------------------------

    def _check_vendor(self, vendor_id: int, cursor: Any) -> None:
        if vendor_id == UNKNOWN_ID:
            raise ProtectedReferenceError("the 'unknown' vendor can't own OUI prefixes")
        if self._vendors.get(vendor_id, cursor) is None:
            raise RecordNotFoundError(f"vendor {vendor_id} does not exist")


@contextmanager
def _unit_of_work(engine: DatabaseEngine, unit: Any) -> Generator[Any, None, None]:
    """Join the caller's unit of work if one was passed, otherwise open one."""
    if unit is not None:
        yield unit
    else:
        with engine.unit_of_work() as cursor:
            yield cursor
