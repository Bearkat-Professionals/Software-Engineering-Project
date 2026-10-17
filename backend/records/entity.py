"""Base class for every entity.

An entity is a plain data object that mirrors one database table. It holds
data and a little behavior about itself, but never talks to the database
(that is the job of backend/data_access/).

Conventions every subclass follows:
  * attribute names == database column names, including the primary key
    (device_id, vendor_id, ...)
  * foreign keys are stored as ids (device_type_id), not nested objects
  * TABLE names the table and PK names the primary-key attribute
  * the *_FIELDS class variables tell the base class how to convert special
    columns when reading and writing rows
"""
import json
from dataclasses import dataclass, fields
from datetime import date, datetime
from enum import Enum
from typing import Any, ClassVar, TypeVar

T = TypeVar("T", bound="Entity")


def _plain(value: Any) -> Any:
    """Enums become their stored text."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _json_safe(value: Any) -> Any:
    """Also turns dates into ISO text so the result can be dumped as JSON."""
    value = _plain(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


@dataclass(kw_only=True)
class Entity:
    TABLE: ClassVar[str] = ""
    PK: ClassVar[str] = ""                               # primary-key attribute/column
    ENUM_FIELDS: ClassVar[dict[str, type[Enum]]] = {}    # column -> Enum class
    BOOL_FIELDS: ClassVar[tuple[str, ...]] = ()          # MySQL returns 0/1
    JSON_FIELDS: ClassVar[tuple[str, ...]] = ()          # stored as JSON text
    CHILD_FIELDS: ClassVar[tuple[str, ...]] = ()         # live in another table
    # Columns the database fills in itself (a DEFAULT) when no value is given.
    # If the attribute is None it is left out of the row, so the default applies
    # instead of the INSERT failing on NOT NULL.
    OMIT_IF_NONE: ClassVar[tuple[str, ...]] = ()

    @property
    def pk(self) -> Any:
        """The value of this entity's primary key (None until it is saved)."""
        return getattr(self, self.PK)

    @classmethod
    def column_names(cls) -> list[str]:
        """Database columns this entity maps to (child-table fields excluded)."""
        return [f.name for f in fields(cls) if f.name not in cls.CHILD_FIELDS]

    @classmethod
    def from_row(cls: type[T], row: dict[str, Any]) -> T:
        """Build an entity from one database row (a dict of column -> value)."""
        names = {f.name for f in fields(cls)}
        data = {k: v for k, v in row.items() if k in names}
        for name, enum_type in cls.ENUM_FIELDS.items():
            if data.get(name) is not None:
                data[name] = enum_type(data[name])
        for name in cls.BOOL_FIELDS:
            if data.get(name) is not None:
                data[name] = bool(data[name])
        for name in cls.JSON_FIELDS:
            if isinstance(data.get(name), (str, bytes)):
                data[name] = json.loads(data[name])
        return cls(**data)

    def to_row(self, include_id: bool = False) -> dict[str, Any]:
        """Column -> value dict ready for an INSERT/UPDATE."""
        row: dict[str, Any] = {}
        for f in fields(self):
            name = f.name
            if name in self.CHILD_FIELDS:
                continue
            if name == self.PK and not include_id:
                continue
            value = getattr(self, name)
            if value is None and name in self.OMIT_IF_NONE:
                continue
            if name in self.JSON_FIELDS and value is not None:
                value = json.dumps(value)
            else:
                value = _plain(value)
            row[name] = value
        return row

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict for API responses and exports."""
        return {f.name: _json_safe(getattr(self, f.name)) for f in fields(self)}