"""Fake 3.2 ReferenceStore for unit tests: find() only, in memory.

Works for any reference record type, like the real generic store. Starts with
record 1, the seeded "unknown" vendor or type (DI-03), unless told not to.

    vendors = FakeReferenceStore(Vendor, [Vendor(vendor_id=9, vendor_name="Apple")])
    vendors.find(9)          # a copy of the Apple vendor
    vendors.find(99)         # None
    vendors.lookups          # [9, 99]: every id asked for, in order
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Generic, Iterable, TypeVar

from backend.records import DeviceType, Vendor
from backend.records.reference_entity import ReferenceEntity

T = TypeVar("T", bound=ReferenceEntity)

UNKNOWN_ID = 1


def _unknown(record_type: type[ReferenceEntity]) -> ReferenceEntity:
    """The seeded "unknown" row with id 1, as the seed file creates it."""
    if record_type is Vendor:
        return Vendor(vendor_id=UNKNOWN_ID, vendor_name="Unknown")
    if record_type is DeviceType:
        return DeviceType(device_type_id=UNKNOWN_ID, category_name="Unknown")
    raise ValueError(f"no seeded unknown row for {record_type.__name__}")


class FakeReferenceStore(Generic[T]):
    def __init__(self, record_type: type[T], records: Iterable[T] = (),
                 with_unknown: bool = True) -> None:
        self.rows: dict[int, T] = {}
        self.lookups: list[int] = []
        if with_unknown:
            unknown = _unknown(record_type)
            self.rows[unknown.pk] = unknown
        for record in records:
            self.rows[record.pk] = deepcopy(record)

    def find(self, pk: int, unit: Any = None) -> T | None:
        self.lookups.append(pk)
        found = self.rows.get(pk)
        return deepcopy(found) if found is not None else None
