"""Integration test for load_oui_lookup (#38) on the TEST database.

Same setup as tests/data_access/test_repository.py; skipped if it isn't available.

    python -m unittest tests.services.scan.test_load_oui_lookup -v
"""
import unittest

from backend.data_access.repository import Repository
from backend.records import OuiPrefix, Vendor
from backend.records.enums import RecordStatus
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.match_oui import load_oui_lookup, match_oui
from tests.data_access.test_repository import CLEANUP, connect_to_test_database


class LoadOuiLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = connect_to_test_database()

    def setUp(self):
        with self.engine.unit_of_work() as cursor:
            for sql in CLEANUP:
                cursor.execute(sql)
        vendors, prefixes = Repository(self.engine, Vendor), Repository(self.engine, OuiPrefix)
        with self.engine.unit_of_work() as unit:
            self.vantiva = vendors.create(Vendor(vendor_name="Vantiva - Connected Home"), unit)
            gone = vendors.create(Vendor(vendor_name="Gone Co", status=RecordStatus.RETIRED), unit)
            prefixes.create(OuiPrefix(oui_prefix="70:13:01", vendor_id=self.vantiva.vendor_id), unit)
            prefixes.create(OuiPrefix(oui_prefix="00:11:22", vendor_id=gone.vendor_id), unit)

    def test_loads_active_vendors_prefixes_only(self):
        lookup = load_oui_lookup(self.engine)
        self.assertEqual(lookup, {"70:13:01": (self.vantiva.vendor_id, "Vantiva - Connected Home")})

    def test_router_mac_matches_its_manufacturer(self):
        match = match_oui(DiscoveredHost("10.0.0.1", "70:13:01:65:8E:F7"), load_oui_lookup(self.engine))
        self.assertEqual((match.vendor_id, match.vendor_name),
                         (self.vantiva.vendor_id, "Vantiva - Connected Home"))


if __name__ == "__main__":
    unittest.main()
