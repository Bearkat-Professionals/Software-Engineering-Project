"""Generic Repository: create, get, list, update and delete for any entity.

One Repository serves one entity class, e.g. Repository(engine, Vendor). It
builds SQL from the entity's TABLE, PK and column names, and passes every value
as a query parameter, never pasted into the SQL text.

Each method runs in its own transaction unless a unit of work is passed in.
Pass the same one to several calls and they commit or roll back together
(design 3.0, CM-04):

    with engine.unit_of_work() as unit:
        vendor = vendors.create(Vendor(vendor_name="Acme"), unit)
        prefixes.create(OuiPrefix(oui_prefix="AA:BB:CC", vendor_id=vendor.pk), unit)

Auditing, conflict checks and validation are not done here, the data access
modules built on this class (3.1 to 3.4) add them.
"""

from __future__ import annotations

from contextlib import contextmanager
from enum import Enum
from typing import Any, Generic, Generator, TypeVar

from backend.data_access.database_engine import DatabaseEngine
from backend.records.entity import Entity

T = TypeVar("T", bound=Entity)

class RecordNotFoundError(Exception):
    """The record to update or delete does not exist, perhaps
       another user deleted it (CM-12, CM-14).
    """

class Repository(Generic[T]):
    def __init__(self, engine: DatabaseEngine, entity_type: type[T]):
        self._engine = engine
        self._entity_type = entity_type
        self._table = entity_type.TABLE
        self._pk = entity_type.PK
        self._columns = entity_type.column_names()  # Child table fields are left out

    def create(self, entity: T, unit: Any = None) -> T:
        """INSERT the entity and return it with its new primary key filled in."""
        row = entity.to_row(include_id=entity.pk is not None)   # keep an id only if one was given
        columns = ", ".join(f"`{name}`" for name in row)
        placeholders = ", ".join(["%s"] * len(row))
        sql = f"INSERT INTO `{self._table}` ({columns}) VALUES ({placeholders})"
        with self._cursor(unit) as cursor:
            cursor.execute(sql, tuple(row.values()))
            if entity.pk is None:
                setattr(entity, self._pk, cursor.lastrowid)
        return entity

    def get(self, pk: Any, unit: Any = None) -> T | None:
        """Return the entity with this primary key, or none if ther is none."""
        sql = f"SELECT {self._select_list()} FROM `{self._table}` WHERE `{self._pk}` = %s"
        with self._cursor(unit) as cursor:
            cursor.execute(sql, (pk,))
            row = cursor.fetchone()
        return self._entity_type.from_row(row) if row else None

    def list(self, unit: Any = None, **filters: Any) -> list[T]:
        """Return every entity, or only those whose columns equal the filters.
           
           vendors.list(status=RecordStatus.ACTIVE) -> WHERE `status` = `active`
           devices.list(model=None) -> WHERE `model` is NULL
        """
        conditions, params = [], []
        for name, value in filters.items():
            if name not in self._columns:
                raise ValueError(f"{self._table} has no column '{name}'")
            if value is None:
                conditions.append(f"`{name}` IS NULL")
            else:
                conditions.append(f"`{name}` = %s")
                params.append(value.value if isinstance(value, Enum) else value)

        sql = f"SELECT {self._select_list()} FROM `{self._table}`"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += f" ORDER BY `{self._pk}`"

        with self._cursor(unit) as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
        return [self._entity_type.from_row(row) for row in rows]

    def update(self, entity: T, unit: Any = None) -> T:
        """Write every column of an existing entity back to its row"""
        if entity.pk is None:
            raise ValueError(f"Cannot update a {self._table} that was never created")
        row = entity.to_row()   # every column except the PK
        assignments = ", ".join(f"`{name}` = %s" for name in row)
        sql = f"UPDATE `{self._table}` SET {assignments} WHERE `{self._pk}` = %s"
        with self._cursor(unit) as cursor:
            cursor.execute(sql, (*row.values(), entity.pk))
            if cursor.rowcount == 0:
                raise RecordNotFoundError(f"{self._table} {self._pk}={entity.pk} does not exist.")
        return entity

    def delete(self, pk: Any, unit: Any = None) -> None:
        """DELETE the row with this primary key"""
        sql = f"DELETE FROM `{self._table}` WHERE `{self._pk}` = %s"
        with self._cursor(unit) as cursor:
            cursor.execute(sql, (pk,))
            if cursor.rowcount == 0:
                raise RecordNotFoundError(f"{self._table} {self._pk}={pk} does not exist.")    

    #------ helpers ---------------------------------------------------------------------------

    def _select_list(self) -> str:
        return ", ".join(f"`{name}`" for name in self._columns)

    @contextmanager
    def _cursor(self, unit: Any) -> Generator[Any, None, None]:
        """User the caller's unit of work if one was passed, otherwise
           open one for this call only.
        """
        if unit is not None:
            yield unit
        else:
            with self._engine.unit_of_work() as cursor:
                yield cursor