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

    def list(self, unit=None, **filters):
        """Equality filters, compared as text like the URL gives them (?status=active)."""
        def text(value):
            return value.value if isinstance(value, Enum) else str(value)
        return [row for row in self.rows.values()
                if all(text(getattr(row, name)) == text(value) for name, value in filters.items())]

    def update(self, entity, unit=None):
        if entity.pk not in self.rows:
            raise RecordNotFoundError(f"{self.entity_type.TABLE} {entity.pk} does not exist")
        self.rows[entity.pk] = entity
        return entity

    def delete(self, pk, unit=None):
        if pk not in self.rows:
            raise RecordNotFoundError(f"{self.entity_type.TABLE} {pk} does not exist")
        del self.rows[pk]
