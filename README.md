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
│   │   └── read_gateway_config/
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
