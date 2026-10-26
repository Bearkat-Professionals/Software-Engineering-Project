"""Load the IEEE MA-L (OUI) list into vendor and oui_prefix (#37).

Download oui.csv from https://standards-oui.ieee.org/oui/oui.csv, then from the
project root:

    py -m database.scripts.load_oui_data path\\to\\oui.csv
    py -m database.scripts.load_oui_data path\\to\\oui.csv --database network_device_scanner

Defaults to the TEST database (DB_NAME + "_test", or TEST_DB_NAME). Safe to run
again: rows that already match are left alone, and vendor 1 ("unknown") is never changed.
"""
import argparse
import csv
import os
import string
from dataclasses import replace

from backend.config import DatabaseConfig
from backend.data_access.database_engine import DatabaseEngine
from backend.records.enums import RecordStatus

VENDOR_NAME_MAX = 255   # vendor.vendor_name VARCHAR(255) in database_schema.sql
BATCH_SIZE = 1000


def parse_oui_csv(path) -> tuple[list[tuple[str, str]], int]:
    """Return ([(prefix "28:6F:B9", vendor name), ...], rows skipped). No database."""
    by_prefix = {}      # a dict keeps the last name when a prefix appears twice
    skipped = 0
    with open(path, encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            hex_digits = (row.get("Assignment") or "").strip().upper()
            name = (row.get("Organization Name") or "").strip()
            if len(hex_digits) != 6 or not all(c in string.hexdigits for c in hex_digits) or not name:
                skipped += 1
                continue
            by_prefix[f"{hex_digits[0:2]}:{hex_digits[2:4]}:{hex_digits[4:6]}"] = name
    return list(by_prefix.items()), skipped


def load_oui_data(engine: DatabaseEngine, pairs) -> dict:
    """Write the parsed pairs in one transaction. Returns the counts printed by main()."""
    # Truncate rather than skip: the prefix still identifies the maker with a shortened name
    truncated = sum(1 for _, name in pairs if len(name) > VENDOR_NAME_MAX)
    pairs = [(prefix, name[:VENDOR_NAME_MAX].rstrip()) for prefix, name in pairs]

    with engine.unit_of_work() as cursor:
        cursor.execute("SELECT vendor_id, vendor_name FROM vendor")
        existing_vendors = {row["vendor_name"].casefold() for row in cursor.fetchall()}
        cursor.execute("SELECT oui_prefix, vendor_id FROM oui_prefix")
        existing_prefixes = {row["oui_prefix"].upper(): row["vendor_id"] for row in cursor.fetchall()}

        # vendor_name is UNIQUE under a case-insensitive collation, so "CISCO" and "Cisco" are one row
        new_names = {}
        for _, name in pairs:
            if name.casefold() not in existing_vendors:
                new_names.setdefault(name.casefold(), name)
        # The no-op update leaves existing rows (vendor 1 included) exactly as they are.
        # Not "vendor_id = vendor_id": MySQL rejects that in a multi-row insert (error 1869)
        _execute_in_batches(cursor,
            "INSERT INTO vendor (vendor_name, status) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE vendor_name = vendor_name",
            [(name, RecordStatus.ACTIVE.value) for name in new_names.values()])

        cursor.execute("SELECT vendor_id, vendor_name FROM vendor")
        vendor_rows = cursor.fetchall()
        vendor_ids = {row["vendor_name"].casefold(): row["vendor_id"] for row in vendor_rows}

        changes = []
        for prefix, name in pairs:
            vendor_id = vendor_ids.get(name.casefold())
            if vendor_id is None:
                # MySQL's collation also ignores accents, so it may have matched a row Python didn't
                cursor.execute("SELECT vendor_id FROM vendor WHERE vendor_name = %s", (name,))
                vendor_id = vendor_ids[name.casefold()] = cursor.fetchone()["vendor_id"]
            if existing_prefixes.get(prefix) != vendor_id:
                changes.append((prefix, vendor_id))
        _execute_in_batches(cursor,
            "INSERT INTO oui_prefix (oui_prefix, vendor_id) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE vendor_id = VALUES(vendor_id)",
            changes)

    prefixes_added = sum(1 for prefix, _ in changes if prefix not in existing_prefixes)
    return {
        "vendors_added": len(vendor_rows) - len(existing_vendors),
        "prefixes_added": prefixes_added,
        "prefixes_updated": len(changes) - prefixes_added,
        "names_truncated": truncated,
    }


def _execute_in_batches(cursor, sql, rows):
    for start in range(0, len(rows), BATCH_SIZE):
        cursor.executemany(sql, rows[start:start + BATCH_SIZE])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Load the IEEE OUI list into vendor and oui_prefix.")
    parser.add_argument("csv_path")
    parser.add_argument("--database", help="database name (default: the test database)")
    args = parser.parse_args(argv)

    config = DatabaseConfig.from_env()
    # Same rule as connect_to_test_database() in tests/data_access/test_repository.py
    name = args.database or os.getenv("TEST_DB_NAME", f"{config.database}_test")
    engine = DatabaseEngine(replace(config, database=name), pool_size=1)

    pairs, skipped = parse_oui_csv(args.csv_path)
    counts = load_oui_data(engine, pairs)
    print(f"Database:  {name}")
    print(f"Vendors:   {counts['vendors_added']} added (existing vendors are never changed)")
    print(f"Prefixes:  {counts['prefixes_added']} added, {counts['prefixes_updated']} updated")
    print(f"Skipped:   {skipped} rows (blank, bad hex, or no name)")
    print(f"Truncated: {counts['names_truncated']} names longer than {VENDOR_NAME_MAX} characters")


if __name__ == "__main__":
    main()
