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
| `app` | `backend.main` | start the application (needs `SECRET_KEY` in `.env`, see Running the app) |
| `admin` | `backend.services.access_control.create_admin` | create the first admin account on an empty database; asks for the password without showing it (`--database NAME` for another database than the test one) |
| `crud` | `backend.dev_tools.crud_server` | start the CRUD API the React test page uses (test database) |
| `ping` | `backend.black_boxes.timed_network_request.check_ping` | ping addresses, or `--sweep` a range (BB-3) |
| `arp` | `backend.black_boxes.read_arp_cache.check_arp_cache` | print this machine's ARP table (BB-1) |
| `chd` | `backend.services.scan.check_host_discovery` | run Host Discovery on a range and list the devices (2.2.2); `--workers` / `--timeout` try other sweep settings |
| `scan` | `backend.services.scan.check_scan` | run a whole scan in the background as a user, watch its progress, then see the outcome and devices (#44) |
| `ev` | `backend.services.scan.check_evidence` | ask every address in a range (default: your subnet) what it is with mDNS, SSDP, ...; prints each host's evidence and hostname, saves nothing (2.2.3, #151) |
| `ident` | `backend.services.scan.check_identification` | after a scan, list every device's vendor, type, score, level and flag, checked against its stored evidence (#69, test database; `--database NAME` for another) |
| `speed` | `backend.services.scan.check_sweep_speed` | time a ping sweep at 1, 8, 32 and 64 workers and print a table for the PR (#81) |
| `backup` | `backend.data_access.backup_recovery.check_backup` | back up, list, verify, restore or compare databases (3.5) |
| `oui` | `database.scripts.load_oui_data` | load the IEEE OUI list into vendor and oui_prefix (`.\dev oui path\to\oui.csv`) |

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

## Scan speed settings (#81)
The ping sweep runs many pings at once. Two settings control it:

| `.env` key | Default | Allowed | What it does |
|---|---|---|---|
| `SCAN_PROBE_WORKERS` | 32 | 1 to 128 | how many pings run at the same time |
| `SCAN_PROBE_TIMEOUT_S` | 1.0 | more than 0, at most 5 | how long each ping waits for a reply |

A sweep takes at most about (addresses ÷ workers) × timeout. A /24 has 254 addresses, so 32 workers and 1 second is 8 rounds, about 8 to 10 seconds; 64 workers is about 4 to 5 seconds. More workers are faster until your machine or network can't keep up, so check that the number of devices found stays the same.

Try settings for one run without changing `.env`:
```
.\dev chd 192.168.1.0/24 --workers 64 --timeout 0.5
```
Compare several worker counts (the 1-worker run takes about 4 minutes on a /24):
```
.\dev speed 192.168.1.0/24                       # 1, 8, 32 and 64 workers
.\dev speed 192.168.1.0/24 --workers 32 64 128   # only these
```
In code:
```python
from backend.services.scan.scan_settings import ScanSettings

settings = ScanSettings.from_env()                      # .env, or the defaults
hosts, status = sweep_subnet(scope, settings=settings)
```
## Evidence collection settings (#151)
After Host Discovery, a scan asks each host what it is (2.2.3). mDNS and SSDP are built; NetBIOS and reverse DNS are accepted in the setting and skipped until #148 is merged.

| `.env` key | Default | Allowed | What it does |
|---|---|---|---|
| `SCAN_EVIDENCE_METHODS` | `mdns,ssdp,netbios,reverse_dns` | any of those, comma separated; set it to nothing (`SCAN_EVIDENCE_METHODS=`) to turn the phase off | which evidence methods run, all at the same time |
| `SCAN_EVIDENCE_WAIT_S` | 3 | 1 to 10 | how long mDNS and SSDP listen for answers |

A method that's turned on but can't run (e.g. another program holds UDP port 5353) is listed in the scan's errors as `mdns unavailable: ...`, the others still run, and the scan ends `partial`. A method turned off isn't an error. With every method on, the phase takes about `SCAN_EVIDENCE_WAIT_S` + 2 seconds, and never more than `SCAN_EVIDENCE_WAIT_S` + 5.

See what each host says, without saving anything:
```
.\dev ev                                       # your subnet
.\dev ev 192.168.1.0/24 --methods mdns --wait 5
.\dev ev 192.168.1.0/24 --ip 192.168.1.20      # one device
```
**Windows Firewall:** the first time Python listens for mDNS or SSDP answers, Windows may ask whether to allow it. Allow it on **private** networks, or those methods hear nothing.

A scan records its settings in `scan.configuration_used`. Saving results stays one host at a time on purpose: each save is a short database transaction, and threads would need a connection each for little gain.

## Running a scan (#44)
A scan runs in the background: it opens a session, discovers hosts, collects evidence from them, identifies and saves them, then closes with an outcome (`success`, `partial` or `failed`) and any errors. Only one scan runs at a time.

```
.\dev scan 192.168.1.0/24 --user blaine                  # watch it, then see the devices
.\dev scan 192.168.1.0/24 --user blaine --no-ping        # ping unavailable: ends partial
.\dev scan 192.168.1.0/24 --user blaine --force-error    # saving fails: ends failed, still closed
```

Before the first run (the database tests empty these tables, so redo it after `.\dev test`):
1. Create the user in the CRUD page (`app_user`), or use `scanner`, which the tests leave behind.
2. Load the OUI list for vendor names: `.\dev oui path\to\oui.csv`.

| Outcome | When |
|---|---|
| `success` | every discovery method worked and every host was saved |
| `partial` | the scan finished, but ping, the ARP cache or an evidence method was unavailable, or some hosts couldn't be saved |
| `failed` | no discovery method worked, or the scan stopped with an error (the first error line says why) |

**"Refused: A scan started by ... is still running."** Another scan really is running, or one was cut off by a crash or Ctrl+C before it could close. If you're sure nothing else is scanning, add `--recover` once: it closes scans left running as failed. Don't use it while a teammate's scan is running on the same database; it would close theirs too.

In code (for the Application Controller, #50):
```python
sessions = ScanSessionController(ScanStore(engine), UserStore(engine))   # ONE, shared by every request
sessions.recover_stuck_scans()                                           # once, at startup
manager = build_scan_manager(engine, sessions, ScanSettings.from_env(), on_finished=send_alerts)
manager.start(scope, user_id)        # returns at once; the scan screen polls sessions.status()
```
`on_finished` gets the `ScanResult` after the outcome is saved, with `alerts` listing `scan_failed` and each new flagged device. The manager itself sends nothing.

## Checking identification (#69)
Every device a scan saves is identified from its evidence (2.2.4): the OUI matched from its MAC, plus any signature evidence stored by earlier scans. That gives its vendor, type, a 0-100 confidence score, a level and the flag (spec 7.1-7.3). After a scan, list what each device was identified as:

```
.\dev scan 192.168.1.0/24 --user blaine      # scan first
.\dev ident                                  # every device in the test database
.\dev ident --database nds                   # another database
```

It changes nothing in the database. The level isn't stored (spec 7.2 works it out from the evidence), so `ident` works it out again from each device's stored evidence and today's reference rules, exactly as the next scan would.

| Column | Meaning |
|---|---|
| Score | 0-100 from the evidence; each evidence type counts once (OUI 20, mDNS 35, SSDP 35, NetBIOS/LLMNR 25, reverse DNS 20) |
| Level | `identified` (type and name from a signature, score 50 or more), `partial` (vendor or type known), or `unidentified` |
| Flagged | `yes` unless the device is identified, or trusted and seen before under the same MAC (spec 7.3) |
| Check | `ok` when the next scan would change nothing: the stored score and flag match the evidence, and the type is one a scan keeps. Otherwise what it would change: the reference data changed since the device was last scanned (for example, a signature was retired) |


Until the evidence collectors arrive (M6), scans find OUI evidence only: expect a score of 20 (`partial`) for a known vendor or 0 (`unidentified`), and every untrusted device flagged.

A scan fills in a device's type, name and model only when they're empty ("unknown" for the type), so values a user set are kept, and a scan never resets a type to unknown (#104). Retiring a signature lowers the score and level on the next scan, but the type it gave stays until a user changes it.


## Setting up your .env file
Make sure to create a .env file in your workspace. Git ignores this file so you are able to put your database credentials here securely.
Paste the below into the file and replace with your information.

Optional: scan speed settings (see [Scan speed settings](#scan-speed-settings-81)). Leave them out to use the defaults.
```
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER= <your_username>
DB_PASSWORD= <your_database_password>
DB_NAME=network_device_scanner
SCAN_PROBE_WORKERS=32       # pings at once, 1 to 128
SCAN_PROBE_TIMEOUT_S=1.0    # seconds each ping waits, more than 0 and at most 5
SCAN_EVIDENCE_METHODS=mdns,ssdp,netbios,reverse_dns   # empty turns evidence collection off
SCAN_EVIDENCE_WAIT_S=3      # seconds mDNS and SSDP listen, 1 to 10
```

## Creating the first admin (#89)
A fresh database has no users, so nobody can sign in. Create the first admin with:
```
.\dev admin                                     # the test database
.\dev admin --database network_device_scanner   # the real one
```
It asks for a username (letters, digits, `.` `_` `-`) and then the password twice, without showing it: 12 to 128 characters, not the same as the username. The password is never typed on the command line, because PowerShell saves commands in its history. Only an Argon2id hash is stored.

It only works on a database with **no users**. Once any account exists, it changes nothing and says so: further accounts are added by an admin in the app, so every account has someone accountable for creating it.

Sign in to the app with this account (see Signing in under API).

## Running the app
Add these to your .env:
```
SECRET_KEY=<64 random characters>   # required, at least 32; make one with: py -3.13 -c "import secrets; print(secrets.token_hex(32))"
APP_PORT=5000                       # optional, 5000 if left out
```
Then start MAMP and run this from the project root:
```
.\dev app
```
(or `py -3.13 -m backend.main`)
Check it at http://127.0.0.1:5000/api/health after signing in (signed out, it returns 401). It listens on 127.0.0.1 only, so other machines can't reach it.

The app refuses to start if `SECRET_KEY` is missing or shorter than 32 characters. Keep it secret: anyone with it could make a session cookie that signs them in. Changing it signs everyone out.

The CRUD test server also uses port 5000. To run both, set `APP_PORT=5001` and use http://127.0.0.1:5001.

The scan screen is the React app in `frontend/app`. While that page is running, keep `APP_PORT=5001`. Install, run, and build steps are in `frontend/app/README.md`.

## API (#51)
The JSON below is the contract with the screens: change it only together with them. Every error, whatever its status, is `{"error": "a sentence a person can read"}`, so a screen can show it as it is.

### Signing in (#90)
Every endpoint except `POST /api/login` needs a signed-in user: signed out gives **401**, a role without the permission gives **403**. Requests that change data (POST, PUT, PATCH, DELETE) must be JSON, or they get **415**.

| Request | Body | Result |
|---|---|---|
| `POST /api/login` | `{"username": "sam", "password": "..."}` | 200 `{"user": {...}, "permissions": [...]}` and a session cookie; 401 for a wrong username or password |
| `POST /api/logout` | `{}` (every POST must be JSON) | 204; the old cookie stops working |
| `GET /api/me` | none | 200, the same shape as login |

The cookie is HttpOnly and SameSite=Strict (Secure once HTTPS arrives). A session ends after 30 minutes without a request, or 8 hours after signing in. Sessions live in the server's memory, so restarting the app signs everyone out.

### POST /api/scans: start a scan
Needs the `editor` or `admin` role. Send `Content-Type: application/json`:
```json
{"scope": "192.168.1.0/24"}
```

| Result | Status | Body |
|---|---|---|
| Started | 202 Accepted | `{"scan_id": 12, "status_url": "/api/scans/status"}` |
| Bad, missing or too large scope | 400 Bad Request | `{"error": "10.0.0.0/8 is too big. Use a range of /22 or smaller, like /24."}` |
| Account not found | 401 Unauthorized | `{"error": "Your account was not found. Sign in again."}` |
| Role can't start scans | 403 Forbidden | `{"error": "Your role (viewer) can't start scans."}` |
| Another scan is running | 409 Conflict | `{"error": "A scan started by sam at 14:03 is still running."}` |

The scan runs in the background and records who started it. Follow it with the status endpoint.

### GET /api/scans/status: what the scanner is doing
Any role. While a scan runs:
```json
{"running": true, "scan_id": 12, "scope": "192.168.1.0/24",
 "started_by": "sam", "started_at": "2026-10-26T14:03:00", "elapsed_seconds": 6,
 "phase": "discovering_hosts", "done": 120, "total": 254,
 "errors": ["ping unavailable: ping command not found"]}
```
`phase` is `discovering_hosts`, `saving_results` or `closing`. `phase`, `done` and `total` are `null` for the first moment, before the scan reports any progress.

When nothing is running:
```json
{"running": false, "last_scan": {"scan_id": 12, "scope": "192.168.1.0/24",
 "started_by": "sam", "started_at": "2026-10-26T14:03:00", "ended_at": "2026-10-26T14:03:11",
 "outcome": "partial", "errors": ["ping unavailable: ping command not found"]}}
```
`outcome` is `success`, `partial` or `failed`. Before any scan has run: `{"running": false, "last_scan": null}`

Times are the server's local time, without a time zone.

### GET /api/devices?page=1&per_page=50: the device list
Any role.
```json
{"devices": [
   {"device_id": 7, "name": null, "type": "unknown", "vendor": "Apple, Inc.",
    "ip": "192.168.1.23", "mac": "F0:D1:A9:12:34:56", "is_randomized_mac": false,
    "network_status": "online", "confidence": 20, "flagged": true, "trusted": false,
    "last_seen": "2026-10-26T14:03:09"}],
 "page": 1, "per_page": 50, "total": 14}
```
- Vendor and type are names, not ids. Retired vendors and types still show their names.
- `name` is `null` when unknown; the screen shows "Unknown".
- Sorted by `last_seen`, newest first.
- `page` defaults to 1 and `per_page` to 50 (at most 200). A page past the end gives an empty `devices` list with the real `total`.
- A `page` or `per_page` that isn't a whole number in range gets a 400 naming the bad value.

### One device: read, add, edit, mark and delete (2.3.1)
Reading needs any role; changing needs `editor` or `admin` (a viewer gets 403, and the refusal is logged). Every change goes through the same save path as scans (SH-4) and is audited.

| Request | Body | Success |
|---|---|---|
| `GET /api/devices/<id>` | none | 200, the device and `loaded_at` |
| `POST /api/devices` | `mac`, and any of the fields below | 201, the device |
| `PUT /api/devices/<id>` | the fields to change, and `loaded_at` | 200, the device |
| `PUT /api/devices/<id>/mark` | `{"mark": "flagged", "loaded_at": "..."}` | 200, the device |
| `DELETE /api/devices/<id>?loaded_at=...` | `{}` | 200, what was removed |
| `GET /api/device-types` | none | 200, active types for the type picker |
| `GET /api/vendors?search=app&limit=20` | none | 200, active vendors for the vendor search |

**The device**, as GET and every change reply. It's the device list's fields plus what the edit form needs:
```json
{"device": {"device_id": 7, "name": "Office printer", "type": "printer", "vendor": "HP Inc.",
            "device_type_id": 4, "vendor_id": 12, "model": "LaserJet 4000",
            "ip": "192.168.1.23", "hostname": "NPI1A2B3C", "mac": "F0:D1:A9:12:34:56",
            "is_randomized_mac": false, "network_status": "online", "confidence": 20,
            "flagged": false, "trusted": true, "mark": "trusted",
            "first_seen": "2026-10-26T14:03:09", "last_seen": "2026-11-08T09:12:44"},
 "loaded_at": "2026-11-08T10:15:02"}
```
- `type` and `vendor` are names (retired ones still show); `device_type_id` and `vendor_id` are for the pickers.
- `mark` is the user's choice: `trusted` (never flagged), `flagged` (stays flagged whatever scans find), or `automatic` (the flag follows the identification level, so a device that isn't fully identified stays flagged until someone trusts it).
- IP, hostname, first and last seen, network status and confidence come from scans and can't be edited here.

**Adding.** Only `mac` is required:
```json
{"mac": "F0:D1:A9:12:34:56", "name": "Office printer", "device_type_id": 4,
 "vendor_id": 12, "model": "LaserJet 4000", "ip": "192.168.1.23", "trusted": false}
```
A device added by hand starts offline, with no first or last seen, until a scan sees it.

**Editing.** Send `loaded_at` and only the fields that change: `name`, `device_type_id`, `vendor_id`, `model`.
- A field left out stays as it is. `""` clears a name or model. `null` is refused ("must be text"): send `""` or leave the field out.
- The MAC can't be edited, because it's the device's identity (DI-01). For a wrong MAC, delete the device and add it again.
- A vendor you choose always wins over the one the OUI suggested.

**Marking.** `{"mark": "trusted" | "flagged" | "automatic", "loaded_at": "..."}`. Trusted and flagged are opposites: choosing one clears the other.

**Deleting.** `DELETE /api/devices/7?loaded_at=2026-11-08T10:15:02`, sent with the body `{}` and `Content-Type: application/json` (every change must be JSON). Its scan history and evidence go with it:
```json
{"deleted": 7, "observations_removed": 3, "evidence_removed": 2}
```
A scan may add the device again if it's still on the network.

**The pickers.** `GET /api/device-types` lists the active types by name:
```json
{"device_types": [{"device_type_id": 4, "name": "printer"}, {"device_type_id": 1, "name": "unknown"}]}
```
`GET /api/vendors?search=app&limit=20` finds active vendors whose name contains the text, ignoring case, with names that start with it first:
```json
{"vendors": [{"vendor_id": 3, "name": "Apple, Inc."}, {"vendor_id": 88, "name": "Appleton Systems"}]}
```
`search` is 2 to 100 characters; `limit` defaults to 20 (at most 50). There are tens of thousands of vendors, so the form searches instead of showing a dropdown.

**`loaded_at`** comes from `GET /api/devices/<id>` (or a change's reply) and is sent back unchanged with every edit, mark and delete. If someone else changed the device after that time, nothing is saved and you get a 409. Missing, unreadable, or with a time zone (like a trailing `Z`) gives 400 "loaded_at is missing or unreadable. Reload the device and try again."

| Refused because | Status | Body |
|---|---|---|
| Invalid values or fields (every problem listed) | 400 | `{"error": "The device can't be saved.", "reasons": ["F0:D1 is not a valid MAC address.", "'vendor_id' must be a whole number."]}` |
| Role can't edit devices | 403 | `{"error": "Your role (viewer) can't edit devices."}` |
| No such device | 404 | `{"error": "There is no device with id 7. It may have been deleted; reload the list."}` |
| Changed by someone else since you loaded it | 409 | `{"error": "This device was changed by sam at 14:05 after you opened it. Reload to see their changes.", "changed_by": "sam", "changed_at": "2026-11-08T14:05:31"}` |
| That MAC is already a device | 409 | `{"error": "A device with MAC F0:D1:A9:12:34:56 is already stored. Edit that one instead.", "device_id": 7}` |

`device_id` in the last refusal lets the screen offer a link to the existing device. Device names and models are stored exactly as typed, so show them as text, never as HTML.


### Reference data: vendors, device types, signatures and OUI prefixes (#126)
`<kind>` is `vendors`, `device-types` or `signatures`; anything else is 404. Reading needs any role; changing needs `editor` or `admin` (a viewer gets 403, and the refusal is logged). Nothing is ever deleted: records are retired and can be reactivated (DI-06).

| Request | Body | Success |
|---|---|---|
| `GET /api/reference/<kind>?q=apple&status=active&page=1&per_page=50` | none | 200, one page |
| `GET /api/reference/<kind>/<id>` | none | 200, one record |
| `POST /api/reference/<kind>` | the fields | 201, the record |
| `PUT /api/reference/<kind>/<id>` | the fields to change, and `loaded_at` | 200, the record |
| `POST /api/reference/<kind>/<id>/retire` | `{"loaded_at": "..."}` | 200, the record |
| `POST /api/reference/<kind>/<id>/reactivate` | `{"loaded_at": "..."}` | 200, the record |
| `POST /api/reference/vendors/<id>/prefixes` | `{"prefix": "f0-d1-a9"}` | 201, the prefix |
| `PUT /api/reference/prefixes/<prefix>` | `{"vendor_id": 12, "loaded_at": "..."}` | 200, the prefix |

**A list.** Retired records are included unless `status` says otherwise:
```json
{"records": [{"id": 3, "name": "Apple, Inc.", "status": "active"}],
 "page": 1, "per_page": 50, "total": 14}
```
- `q` finds names (or a signature's pattern) containing the text, ignoring case; at most 100 characters.
- `status` is `active`, `retired` or `both` (the default).
- `page` defaults to 1 and `per_page` to 50 (at most 200), as for devices.
- Sorted by name; signatures by evidence type, then pattern.

A signature record has more fields, including its device type's name:
```json
{"id": 9, "evidence_type": "mdns", "pattern": "echo", "device_type_id": 4,
 "device_type": "smart speaker", "resulting_device_name": "Amazon Echo",
 "resulting_model": null, "status": "active"}
```

**One record**, with what uses it and the version you're looking at (`prefixes` only for vendors):
```json
{"record": {"id": 3, "name": "Apple, Inc.", "status": "active"},
 "usage": {"device": 12},
 "usage_text": "12 devices use this vendor.",
 "prefixes": ["3C:07:54", "F0:D1:A9"],
 "loaded_at": "2026-11-11T10:15:02"}
```
A device type's `usage` is `{"device": 12, "device_signature": 3}`; a signature's is `{"identification_evidence": 5}`. Show `usage_text` in the retire confirmation.

**Adding and editing.** Vendors and device types: `{"name": "Acme"}`. Signatures:
```json
{"evidence_type": "mdns", "pattern": "echo", "device_type_id": 4,
 "resulting_device_name": "Amazon Echo", "resulting_model": null}
```
- An edit sends only the fields that change, plus `loaded_at`. `null` clears a signature's resulting name or model.
- A signature's evidence type is `mdns`, `ssdp`, `netbios_llmnr` or `reverse_dns` (not `oui`: prefixes belong to vendors). Its pattern is 3 to 255 characters and matches anywhere in the value, ignoring case. Its device type must be active.
- Any other field is refused with a 400 naming the ones allowed.
- **A record change replies with the same JSON as `GET /api/reference/<kind>/<id>`**, including a new `loaded_at` to use for the next change. Replace what the screen shows with the reply.
- Changes take effect on the next scan; a running scan keeps the rules it started with.

**OUI prefixes.** Add one to a vendor with `{"prefix": "f0-d1-a9"}`; any common spelling works, and it's stored as `F0:D1:A9`. Move one to another vendor with `PUT /api/reference/prefixes/F0:D1:A9` and `{"vendor_id": 12, "loaded_at": "..."}`, sending the vendor page's `loaded_at`. Prefixes can't be deleted; a wrong one is moved. Both reply:
```json
{"prefix": "F0:D1:A9", "vendor_id": 12, "loaded_at": "2026-11-11T10:16:40"}
```
Reload the vendor afterwards to show its new list of prefixes.

**`loaded_at`** comes from `GET .../<id>` and is sent back unchanged with every edit, retire, reactivate and move. If someone else changed the record after that time, the change is refused with 409 and nothing is saved. It's required: missing, unreadable, or with a time zone (like a trailing `Z`) gives 400 "loaded_at is missing or unreadable. Reload the record and try again."

| Refused because | Status | Body |
|---|---|---|
| Invalid values (every problem listed) | 400 | `{"error": "The signature can't be saved.", "reasons": ["The pattern is 2 characters long; it must be 3 to 255."]}` |
| The built-in "Unknown" vendor or type | 403 | `{"error": "The built-in 'Unknown' device type can't be changed or retired: new devices use it until they are identified."}` |
| Role can't edit reference data | 403 | `{"error": "Your role (viewer) can't edit reference data."}` |
| No such record or prefix | 404 | `{"error": "There is no vendor with id 999. It may have been removed; reload the list."}` |
| Name, pattern or prefix already exists | 409 | `{"error": "A vendor with this name already exists.", "existing_id": 88}` |
| Changed by someone else since you loaded it | 409 | `{"error": "This vendor was changed by sam at 14:05 after you opened it. Reload to see their changes.", "changed_by": "sam", "changed_at": "2026-11-11T14:05:31"}` |

A prefix refusal says "The OUI prefix can't be saved." A duplicate offers `existing_id` so the screen can link to the existing record (`null` when two people added it at the same moment). Names and patterns are stored exactly as typed, so show them as text, never as HTML.


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
| `GET /api/<table>` | List rows (filter with `?column=value`); without paging parameters it still returns every matching row |
| `GET /api/<table>?limit=50&offset=0` | Return one stable primary-key-ordered page; `limit` is 1–500 and `offset` is 0 or more |
| `GET /api/<table>?search=cisco&search_column=vendor_name&limit=20` | Search one column by literal contained text (`%` and `_` are not wildcards) |
| `GET /api/<table>/<id>` | Get one row |
| `POST /api/<table>` | Create a row from a JSON body |
| `PUT /api/<table>/<id>` | Replace a row (send every column, not only the changed ones) |
| `DELETE /api/<table>/<id>` | Delete a row |
| `GET /api/tables` | Every table with its primary key and columns |

List responses include the total number of matching rows in the `X-Total-Count` header, before `limit` and `offset` are applied.

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

Step 2.1: Apply database migrations to existing databases before running the app or tests. Run each new file in `database/migrations/` once, in number order. For PR #111, run `database/migrations/001_alert_history.sql`, and for PR #103, `database/migrations/002_flag_set_by_user.sql` (without it, saving any device fails with "Unknown column 'flag_set_by_user'"). Run each against both:

- `network_device_scanner` (the real development database)
- `network_device_scanner_test` (or the database named by `TEST_DB_NAME`)

In MAMP/phpMyAdmin, select the first database, choose **Import**, select the migration file, and run it. Repeat for the test database. A fresh database created from `database/schema/database_schema.sql` already contains these changes and does not need the migrations.

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
* Javier Gonzalez
