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


## Contributers
* Blaine Pavlock
* Cullen Tolmsoff
* Skyler Geary
* Mason Murphy
* Jayden Harper
* Josemir Soto
* Jason Thomas
