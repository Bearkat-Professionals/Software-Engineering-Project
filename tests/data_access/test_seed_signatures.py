"""Database checks for the repeat-safe starter signature seed (#152)."""
import unittest
from pathlib import Path

from backend.data_access.reference_data import ReferenceStore
from backend.data_access.repository import Repository
from backend.records import DeviceSignature, User
from backend.records.enums import RecordStatus
from tests.data_access.test_repository import CLEANUP, connect_to_test_database


SEED_FILE = Path(__file__).resolve().parents[2] / "database" / "seed" / "seed_signatures.sql"
SEED_TYPES = {
    "printer",
    "streaming device",
    "smart speaker",
    "computer",
    "phone or tablet",
    "router",
    "smart home",
    "game console",
    "nas",
}
EXPECTED_SIGNATURES = {
    ("mdns", "_ipp._tcp", "printer", "Network printer"),
    ("mdns", "_pdl-datastream._tcp", "printer", "Network printer"),
    ("mdns", "_googlecast._tcp", "streaming device", "Google Cast device"),
    ("mdns", "model=AppleTV", "streaming device", "Apple TV"),
    ("mdns", "_sonos._tcp", "smart speaker", "Sonos speaker"),
    ("mdns", "_spotify-connect._tcp", "smart speaker", "Spotify Connect speaker"),
    ("mdns", "_hap._tcp", "smart home", "HomeKit accessory"),
    ("mdns", "_companion-link._tcp", "phone or tablet", "Apple device"),
    ("ssdp", "manufacturer=Roku", "streaming device", "Roku"),
    ("ssdp", "InternetGatewayDevice", "router", "Router"),
    ("ssdp", "manufacturer=Sonos", "smart speaker", "Sonos speaker"),
    ("ssdp", "Philips hue bridge", "smart home", "Hue Bridge"),
    ("ssdp", "Xbox", "game console", "Xbox"),
    ("netbios_llmnr", "DESKTOP-", "computer", "Windows PC"),
    ("netbios_llmnr", "LAPTOP-", "computer", "Windows laptop"),
    ("reverse_dns", "iphone", "phone or tablet", "iPhone"),
    ("reverse_dns", "android", "phone or tablet", "Android device"),
}
EXTRA_CLEANUP = (
    "DELETE FROM device_signature",
    "DELETE FROM device_type WHERE device_type_id <> 1",
)


def load_seed(engine) -> None:
    """Execute this seed file without relying on a command-line database client."""
    sql = "\n".join(
        line for line in SEED_FILE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    with engine.unit_of_work() as cursor:
        for statement in sql.split(";"):
            if statement.strip():
                cursor.execute(statement)


class SeedSignaturesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = connect_to_test_database()

    def setUp(self):
        self._clean_database()

    def tearDown(self):
        self._clean_database()

    def _clean_database(self):
        with self.engine.unit_of_work() as cursor:
            for statement in CLEANUP + EXTRA_CLEANUP:
                cursor.execute(statement)

    def _counts(self):
        with self.engine.unit_of_work() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM device_type")
            type_count = cursor.fetchone()["total"]
            cursor.execute("SELECT COUNT(*) AS total FROM device_signature")
            signature_count = cursor.fetchone()["total"]
        return type_count, signature_count

    def test_seed_can_run_twice_without_duplicates(self):
        load_seed(self.engine)
        counts_after_first_load = self._counts()
        load_seed(self.engine)

        self.assertEqual(self._counts(), counts_after_first_load)
        self.assertEqual(counts_after_first_load, (len(SEED_TYPES) + 1, len(EXPECTED_SIGNATURES)))

        with self.engine.unit_of_work() as cursor:
            cursor.execute("SELECT category_name, status FROM device_type")
            loaded_types = {(row["category_name"], row["status"]) for row in cursor.fetchall()}
            cursor.execute("""
                SELECT signature.evidence_type, signature.pattern_value,
                       device_type.category_name, signature.resulting_device_name
                FROM device_signature AS signature
                INNER JOIN device_type
                    ON device_type.device_type_id = signature.device_type_id
                WHERE signature.status = 'active'
                  AND device_type.status = 'active'
            """)
            loaded_signatures = {
                (row["evidence_type"], row["pattern_value"], row["category_name"],
                 row["resulting_device_name"])
                for row in cursor.fetchall()
            }
            cursor.execute("""
                SELECT evidence_type, pattern_value, COUNT(*)
                FROM device_signature
                GROUP BY evidence_type, pattern_value
                HAVING COUNT(*) > 1
            """)
            duplicates = cursor.fetchall()

        self.assertEqual(loaded_types, {(name, "active") for name in SEED_TYPES | {"unknown"}})
        self.assertEqual(loaded_signatures, EXPECTED_SIGNATURES)
        self.assertEqual(duplicates, [])

    def test_seeded_signature_is_visible_and_retireable(self):
        load_seed(self.engine)
        admin_id = Repository(self.engine, User).create(
            User(username="seed-admin", password_hash="not-a-real-hash")
        ).user_id
        signatures = ReferenceStore(self.engine, DeviceSignature)

        roku = signatures.list_page(text="manufacturer=Roku").records[0]
        self.assertEqual(roku.status, RecordStatus.ACTIVE)

        signatures.retire(roku.pk, admin_id)

        self.assertEqual(signatures.find(roku.pk).status, RecordStatus.RETIRED)
