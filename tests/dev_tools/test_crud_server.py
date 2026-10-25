"""Tests for the development CRUD server (#19), through Flask's test client.

The server gets in-memory repositories instead of MySQL, so these check the
routes, status codes and error messages without a database.

    python -m unittest discover tests
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.data_access.database_engine import IntegrityConstraintError
from backend.dev_tools.crud_server import create_app
from tests.fakes.memory_repository import InMemoryRepository


class CrudServerTests(unittest.TestCase):
    def setUp(self):
        self.repositories = {}          # table name -> the fake the server is using

        def make_repository(entity_type):
            repository = InMemoryRepository(entity_type)
            self.repositories[entity_type.TABLE] = repository
            return repository

        self.client = create_app(make_repository).test_client()

    # ---- the five routes -------------------------------------------------------

    def test_vendor_can_be_created_listed_changed_and_deleted(self):
        created = self.client.post("/api/vendor", json={"vendor_name": "Acme"})
        self.assertEqual(created.status_code, 201)
        vendor_id = created.get_json()["vendor_id"]

        listed = self.client.get("/api/vendor").get_json()
        self.assertEqual([row["vendor_name"] for row in listed], ["Acme"])

        changed = self.client.put(f"/api/vendor/{vendor_id}",
                                  json={"vendor_name": "Acme Corp", "status": "active"})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.get(f"/api/vendor/{vendor_id}").get_json()["vendor_name"], "Acme Corp")

        self.assertEqual(self.client.delete(f"/api/vendor/{vendor_id}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/vendor/{vendor_id}").status_code, 404)

    def test_list_filters_by_column(self):
        self.client.post("/api/vendor", json={"vendor_name": "Old Co", "status": "retired"})
        self.client.post("/api/vendor", json={"vendor_name": "New Co"})
        retired = self.client.get("/api/vendor?status=retired").get_json()
        self.assertEqual([row["vendor_name"] for row in retired], ["Old Co"])

    def test_tables_route_lists_every_table_with_its_key(self):
        tables = self.client.get("/api/tables").get_json()
        self.assertEqual(len(tables), 12)
        self.assertEqual(tables["device"]["primary_key"], "device_id")
        self.assertIn("mac_address", tables["device"]["columns"])

    # ---- guardrails --------------------------------------------------------------

    def test_password_hash_is_never_returned(self):
        created = self.client.post("/api/app_user", json={"username": "sam", "password_hash": "secret-hash"})
        self.assertNotIn("password_hash", created.get_json())
        self.assertNotIn("secret-hash", self.client.get("/api/app_user").get_data(as_text=True))

    # ---- errors are JSON with a message ----------------------------------------

    def test_error_responses(self):
        cases = [
            ("unknown table",          self.client.get("/api/nope"),                      404),
            ("missing row",            self.client.get("/api/vendor/99"),                 404),
            ("update a missing row",   self.client.put("/api/vendor/99", json={"vendor_name": "x"}), 404),
            ("delete a missing row",   self.client.delete("/api/vendor/99"),              404),
            ("unknown filter column",  self.client.get("/api/vendor?colour=red"),         400),
            ("body is not JSON",       self.client.post("/api/vendor", data="hello"),     400),
            ("required column missing", self.client.post("/api/vendor", json={}),         400),
            ("bad enum value",         self.client.post("/api/vendor", json={"vendor_name": "x", "status": "sleeping"}), 400),
        ]
        for name, response, expected_code in cases:
            with self.subTest(name):
                self.assertEqual(response.status_code, expected_code)
                self.assertTrue(response.get_json()["error"])          # a message the page can show

    def test_database_rule_broken_is_409_conflict(self):
        self.client.post("/api/vendor", json={"vendor_name": "In Use Co"})
        refusal = IntegrityConstraintError("Cannot delete or update a parent row")
        with mock.patch.object(self.repositories["vendor"], "delete", side_effect=refusal):
            response = self.client.delete("/api/vendor/1")
        self.assertEqual(response.status_code, 409)
        self.assertIn("parent row", response.get_json()["error"])

    # ---- the HTML page -----------------------------------------------------------

    def test_serves_the_test_page_or_says_it_is_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            page = Path(folder) / "index.html"
            page.write_text("<h1>CRUD test</h1>")
            with_page = create_app(InMemoryRepository, page=page).test_client()
            self.assertIn("CRUD test", with_page.get("/").get_data(as_text=True))

            without_page = create_app(InMemoryRepository, page=Path(folder) / "missing.html").test_client()
            self.assertEqual(without_page.get("/").status_code, 404)


if __name__ == "__main__":
    unittest.main()
