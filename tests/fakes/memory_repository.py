"""In-memory stand-in for backend.data_access.repository.Repository.

Same methods and the same "not found" behaviour, but rows live in a dict, so
tests of code that uses a repository need no database.

    vendors = InMemoryRepository(Vendor)
    vendors.create(Vendor(vendor_name="Acme"))     # gets vendor_id 1
"""
from enum import Enum

from backend.data_access.repository import RecordNotFoundError


class InMemoryRepository:
    def __init__(self, entity_type):
        self.entity_type = entity_type
        self.rows = {}                 # primary key -> entity
        self._next_id = 1

    def create(self, entity, unit=None):
        if entity.pk is None:
            setattr(entity, entity.PK, self._next_id)
        self._next_id = max(self._next_id, entity.pk) + 1
        self.rows[entity.pk] = entity
        return entity

    def get(self, pk, unit=None):
        return self.rows.get(pk)

    def list(self, unit=None, *, limit=None, offset=0, contains=None, **filters):
        """Apply Repository-compatible filters, stable ordering and paging."""
        self._validate_page(limit, offset)
        columns = set(self.entity_type.column_names())
        unknown = set(filters) - columns
        if unknown:
            raise ValueError(f"{self.entity_type.TABLE} has no column {sorted(unknown)[0]!r}")
        if contains is not None and contains[0] not in columns:
            raise ValueError(f"{self.entity_type.TABLE} has no column {contains[0]!r}")

        def text(value):
            return value.value if isinstance(value, Enum) else str(value)

        rows = sorted(self.rows.values(), key=lambda row: row.pk)
        rows = [row for row in rows
                if all(text(getattr(row, name)) == text(value)
                       for name, value in filters.items())]
        if contains is not None:
            name, value = contains
            needle = text(value).casefold()
            rows = [row for row in rows if needle in text(getattr(row, name)).casefold()]
        end = None if limit is None else offset + limit
        return rows[offset:end]

    def count(self, unit=None, *, contains=None, **filters):
        return len(self.list(contains=contains, **filters))

    @staticmethod
    def _validate_page(limit, offset):
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            raise ValueError("limit must be a positive whole number")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative whole number")

    def update(self, entity, unit=None):
        if entity.pk not in self.rows:
            raise RecordNotFoundError(f"{self.entity_type.TABLE} {entity.pk} does not exist")
        self.rows[entity.pk] = entity
        return entity

    def delete(self, pk, unit=None):
        if pk not in self.rows:
            raise RecordNotFoundError(f"{self.entity_type.TABLE} {pk} does not exist")
        del self.rows[pk]
