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

## Contributers
* Blaine Pavlock
* Cullen Tolmsoff
* Skyler Geary
* Mason Murphy
* Jayden Harper
* Josemir Soto
* Jason Thomas
