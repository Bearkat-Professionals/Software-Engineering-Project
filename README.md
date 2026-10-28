# Network Device Scanner

## Project structure
```text
Software-Engineering-Project/
├── README
├── docs/                                # user spec, system design, diagrams
│
├── frontend/                            # Tier 1: 1.0 Web UI Manager
│   ├── components/
│   ├── views/
│   │   ├── login/
│   │   ├── devices/
│   │   ├── scans/
│   │   ├── reference_settings/
│   │   └── reports/
│   └── styles/
│
├── backend/                             # Tier 2 (plus data-access code of Tier 3)
│   ├── records/                         # DeviceRecord, EvidenceItem, ScanRecord, etc.
│   ├── controller/
│   ├── services/                        # Tier 2 modules
│   │   ├── access_control/
│   │   ├── scan/
│   │   │   ├── session/
│   │   │   ├── discovery/
│   │   │   ├── evidence/
│   │   │   ├── identification/
│   │   │   └── recorder/
│   │   ├── records/
│   │   │   ├── devices/
│   │   │   └── reference_data/
│   │   ├── reporting/
│   │   └── backup/
│   ├── shared_modules/
│   ├── black_boxes/
│   │   ├── read_arp_cache/
│   │   ├── read_gateway_config/
│   │   ├── backup_media/
│   │   └── timed_network_request/       # BB-3: ping and parallel probes
│   ├── dev_tools/
│   └── data_access/                     # 3.0 Data Storage Manager (code only)
│       ├── network_data/
│       ├── reference_data/
│       ├── account_settings/
│       ├── audit_trail/
│       └── backup_recovery/
│
├── tests/
│   ├── services/
│   ├── data_access/
│   ├── black_boxes/
│   ├── dev_tools/
│   └── fakes/                           # fake ARP data, fake mail, in-memory DB
│
└── database/                            # the database itself, no application code
    ├── schema/
    ├── migrations/                      # numbered changes, e.g. add version column
    ├── seed/
    └── scripts/
```

## Tech Stack
- Python
- MySQL
- MAMP/LAMP
- Git

## Ping (BB-3 Timed Network Request)
Sends pings through the operating system's `ping` command, so no admin rights are needed. A probe never raises: every call returns a `ProbeResult` with a status of `ok`, `no_reply`, `timeout` or `unavailable`.

```python
from backend.black_boxes.timed_network_request import ping, run_probes

ping("192.168.1.1")                 # one address, 1 second timeout
run_probes(addresses, ping)         # many addresses, 32 at a time, results in the same order
```

Import only from the package as shown above, not from the files inside it.

Check it against your own network (replace the addresses with your gateway, an unused address and your subnet):
```
python -m backend.black_boxes.timed_network_request.check_ping 192.168.1.1 192.168.1.250 --sweep 192.168.1.0/24
```

Run the unit tests (they use no network, so they pass offline):
```
python -m unittest discover tests
```

For other modules' tests, use `tests/fakes/fake_network.py` instead of real pings.

## Setting up your .env file
Make sure to create a .env file in your workspace. Git ignores this file so you are able to put your database credentials here securely.
Paste the below into the file and replace with your information.
```
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER= <your_username>
DB_PASSWORD= <your_database_password>
DB_NAME=network_device_scanner
```

## CRUD test server (development only)
A small Flask server for checking the database by hand: list, add, edit and delete rows in any table, through the HTML test page or the API. **It is a developer tool, not part of the product.** It skips login, roles, validation and the audit log, so it only listens on your own machine (`127.0.0.1`) and the real application never imports it.

Start it from the project root, then open http://127.0.0.1:5000/
```
python -m backend.dev_tools.crud_server                                    # uses the test database
python -m backend.dev_tools.crud_server --database network_device_scanner  # uses the real database
```
or
```
py -m backend.dev_tools.crud_server                                    # uses the test database
py -m backend.dev_tools.crud_server --database network_device_scanner  # uses the real database
```
By default it uses the test database (`DB_NAME` + `_test`, or `TEST_DB_NAME` from your `.env`). Create that database with the same schema and seed files as the real one.

| Request | Does |
|---|---|
| `GET /api/<table>` | List rows (filter with `?column=value`) |
| `GET /api/<table>/<id>` | Get one row |
| `POST /api/<table>` | Create a row from a JSON body |
| `PUT /api/<table>/<id>` | Replace a row (send every column, not only the changed ones) |
| `DELETE /api/<table>/<id>` | Delete a row |
| `GET /api/tables` | Every table with its primary key and columns |

Errors come back as `{"error": "message"}`. Add rows in dependency order: `device_type` and `vendor` before `device`, and an `app_user` before a `scan`.

## Starting and setting up the Server

### MariaDB on Linux
Step 1: Installing MariaDB & Start it (Run the following commands in your terminal)

- sudo pacman -S mariaDB (If on Arch Linux)
- sudo mariadb-install-db --user=mysql --basedir=/usr --datadir=/var/lib/mysql
- sudo systemctl enable --now mariadb

Step 2: Create the account and test database (Run the following commands in your terminal)

You're going to start MariaDB and run the following commands.

- CREATE USER '[your_user_here]'@'127.0.0.1' IDENTIFIED BY '[your_password_here]';
- CREATE DATABASE IF NOT EXISTS network_device_scanner_test
      CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
- GRANT ALL PRIVILEGES ON network_device_scanner_test.* TO '[your_user_here]'@'127.0.0.1';
- FLUSH PRIVILEGES;
EXIT;

Step 2.1: Creating your .env

- Create your .env file and then input the following
- DB_HOST=127.0.0.1
- DB_PORT=3306
- DB_USER=[your_user_here]
- DB_NAME=network_device_scanner

Step 3: Load the schema and the seed (I ran these commands in my IDE)

- mariadb -h 127.0.0.1 -P 3306 -u [your_user_here] -p network_device_scanner_test < database/schema/database_schema.sql
- mariadb -h 127.0.0.1 -P 3306 -u [your_user_here] -p network_device_scanner_test < database/seed/seed_reference_data.sql

Step 4: Install python requirements (Run the following commands inside your IDE terminal)

- python3 -m venv .venv
- source .venv/bin/activate
- python -m pip install -r requirements.txt

Step 5: Start the CRUD server with the virtual environment (Run the following commands inside your IDE termnial)

- source .venv/bin/activate
- python3 -m backend.dev_tools.crud_server

If everything was setup and ran correctly you should see a message stating:

WARNING: This is a development server. Do not use it in a production deployment. Use a production WSGI server instead.

### MAMP on Windows/MacOS

Step 1: Start MAMP and make sure MySQL is green

Step 2: Open a terminal in the project root folder

Step 3: Run the server with the following command (test database ONLY):
    - `py -3.13 -m backend.dev_tools.crud_server`
    - if port 5000 is busy, add `--port -5001`
Step 4: Checked that it started. It should print:
    `DEVELOPMENT ONLY: CRUD server on http://127.0.0.1:5000/ using database 'network_device_scanner_test'`

Step 5: Open http://127.0.0.1:5000/ in your browser. That's the test page.

Step 6: Stop it with **Ctrl+C** in the terminal.

## Backup and recovery (3.5)
Backups copy the database's rows into a compressed file in `backups/` (or the folder in `BACKUP_DIR` in your `.env`). The folder is in `.gitignore` because backups contain password hashes. The schema is not in the backup; it lives in `database/schema/`.

```python
from backend.black_boxes.backup_media import BackupMedia
from backend.data_access.backup_recovery.create_full_backup import create_full_backup
from backend.data_access.backup_recovery.verify_backup import verify_backup
from backend.data_access.backup_recovery.restore_from_backup import restore_from_backup

media = BackupMedia.from_env()
stored = create_full_backup(engine, media)        # every table
verify_backup(media, stored.name).ok              # True if complete and unchanged
restore_from_backup(engine, media, stored.name)   # all or nothing
```

- **Full backup** (3.5.1): every table. **Partial backup** (3.5.2): the reference tables (vendor, OUI prefixes, device types, signatures).
- **Verify** (3.5.3) checks every table's row count and checksum against the backup's manifest, and lists every problem it finds.
- **Restore** (3.5.4) verifies first, then replaces the rows inside one transaction. If anything is wrong (damaged file, schema changed, a row left pointing at a missing parent) nothing is changed.

### Trying it by hand
`check_backup` is a developer tool for running backups from the command line. It uses the **test database** unless you pass `--database`. Run it from the project root:
```
py -m backend.data_access.backup_recovery.check_backup full                  # back up every table
py -m backend.data_access.backup_recovery.check_backup partial               # back up the reference tables
py -m backend.data_access.backup_recovery.check_backup list                  # stored backups, newest first
py -m backend.data_access.backup_recovery.check_backup verify NAME           # is it complete and unchanged?
py -m backend.data_access.backup_recovery.check_backup restore NAME          # asks before changing anything
py -m backend.data_access.backup_recovery.check_backup compare DB_A DB_B     # do two databases hold the same rows?
```
Add `--database network_device_scanner` to `full`, `partial` or `restore` to use the real database. Backing up only reads it, but restoring into any database whose name doesn't end in `_test` also needs `--allow-real`. Use `--help` after any command for its options.

Example: check that a backup of the real database rebuilds it exactly in the test database:
```
py -m backend.data_access.backup_recovery.check_backup full --database network_device_scanner
py -m backend.data_access.backup_recovery.check_backup restore NAME
py -m backend.data_access.backup_recovery.check_backup compare network_device_scanner network_device_scanner_test
```
The last command should end with `All tables match.`

## Contributers
* Blaine Pavlock
* Cullen Tolmsoff
* Skyler Geary
* Mason Murphy
* Jayden Harper
* Josemir Soto
* Jason Thomas
