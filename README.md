# Network Device Scanner

## Project structure
```text
Software-Engineering-Project/
├── README
├── dev.py                               # short commands: py -3.13 dev.py
├── dev.cmd                              # Windows shortcut: .\dev
├── docs/                                # user spec, system design, diagrams
│
├── frontend/                            # Tier 1: 1.0 Web UI Manager
│   ├── crud-react/                      # dev-only CRUD test page (React + Vite), not the product UI
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
- Javascript
- MySQL
- React
- MAMP/LAMP
- Git

## Quick commands (dev.py)
Every test and developer tool has a short command. Run them from the project root.

On Windows (`dev.cmd` adds the `py -3.13` for you):
```
.\dev                          # list every command
.\dev test                     # run every unit test
.\dev chd 192.168.1.0/24       # anything after the command goes to the tool
```
On macOS/Linux, or anywhere without `dev.cmd`: `py -3.13 dev.py test` or `python3 dev.py test`.

| Command | Runs `python -m` ... | Use it to |
|---|---|---|
| `test` | `unittest discover tests` | run every unit test (`-v` shows names, `-k sweep` runs only matching tests) |
| `conn` | `backend.check_connection` | check the database settings in your `.env` |
| `crud` | `backend.dev_tools.crud_server` | start the CRUD API the React test page uses (test database) |
| `ping` | `backend.black_boxes.timed_network_request.check_ping` | ping addresses, or `--sweep` a range (BB-3) |
| `arp` | `backend.black_boxes.read_arp_cache.check_arp_cache` | print this machine's ARP table (BB-1) |
| `chd` | `backend.services.scan.check_host_discovery` | run Host Discovery on a range and list the devices (2.2.2) |
| `backup` | `backend.data_access.backup_recovery.check_backup` | back up, list, verify, restore or compare databases (3.5) |

Add `--help` after any command for that tool's own options, e.g. `.\dev chd --help`. The long `python -m ...` commands still work. To add a tool, add one line to `COMMANDS` in `dev.py`.

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
.\dev ping 192.168.1.1 192.168.1.250 --sweep 192.168.1.0/24
```

Run the unit tests (they use no network, so they pass offline):
```
.\dev test
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

## CRUD test page and server (development only)
A small tool for checking the database by hand: list, add, edit and delete rows in any table. **It is a developer tool, not part of the product.** It skips login, roles, validation and the audit log, so it only listens on your own machine and the real application never imports it.

It is two programs, so it uses two terminals:
- the **CRUD server** (Flask, http://127.0.0.1:5000/api): the API that reads and writes the database
- the **test page** (React, http://localhost:5173): the page you use; it sends its `/api` requests to the server

**First time only:** install the page's packages. You need Node.js 20.19 or newer (`node -v` to check).
```
cd frontend\crud-react
npm install
```

**Terminal 1**, from the project root: start the server
```
.\dev crud                                    # uses the test database
.\dev crud --database network_device_scanner  # uses the real database
```
(Long form: `py -3.13 -m backend.dev_tools.crud_server`)

**Terminal 2**: start the page, then open http://localhost:5173
```
cd frontend\crud-react
npm run dev
```
Stop each one with **Ctrl+C**. Opening http://127.0.0.1:5000/ only shows a note pointing to the page.

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
- Then start the test page in a second terminal: see "CRUD test page and server" above


If everything was setup and ran correctly you should see a message stating:

WARNING: This is a development server. Do not use it in a production deployment. Use a production WSGI server instead.

### MAMP on Windows/MacOS

Step 1: Start MAMP and make sure MySQL is green

Step 2: Open a terminal in the project root folder

Step 3: Run the server with the following command (test database ONLY):
    - `.\dev crud`   (long form: `py -3.13 -m backend.dev_tools.crud_server`)
    - keep it on port 5000: the React page sends its requests there. To use another port, also change the `/api` line in `frontend/crud-react/vite.config.js`

Step 4: Check that it started. It should print:
    `DEVELOPMENT ONLY: CRUD API on http://127.0.0.1:5000/api using database 'network_device_scanner_test'`

Step 5: In a second terminal, start the page (run `npm install` there once first):
    - `cd frontend\crud-react`
    - `npm run dev`

Step 6: Open http://localhost:5173 in your browser. That's the test page.

Step 7: Stop both with **Ctrl+C** in their terminals.

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
.\dev backup full                  # back up every table
.\dev backup partial               # back up the reference tables
.\dev backup list                  # stored backups, newest first
.\dev backup verify NAME           # is it complete and unchanged?
.\dev backup restore NAME          # asks before changing anything
.\dev backup compare DB_A DB_B     # do two databases hold the same rows?
```
(Long form: `py -3.13 -m backend.data_access.backup_recovery.check_backup ...`)

Add `--database network_device_scanner` to `full`, `partial` or `restore` to use the real database. Backing up only reads it, but restoring into any database whose name doesn't end in `_test` also needs `--allow-real`. Use `--help` after any command for its options.

Example: check that a backup of the real database rebuilds it exactly in the test database:
```
.\dev backup full --database network_device_scanner
.\dev backup restore NAME
.\dev backup compare network_device_scanner network_device_scanner_test
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
