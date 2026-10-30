"""Fixed value sets used by the entity classes.

Each enum's value is the exact text stored in the database column, so the
database side must store these strings (ENUM columns or CHECK constraints).
The relational schema does not list the allowed values for these columns yet,
so every set below is a proposal that has to be confirmed with the database
teammate. A value the database returns that is not listed here raises a
ValueError when the row is loaded, which makes a mismatch loud, not silent.
"""
from enum import Enum


class NetworkStatus(str, Enum):         # DEVICE.network_status, SCAN_OBSERVATION.status
    ONLINE = "online"
    OFFLINE = "offline"


class RecordStatus(str, Enum):          # status on VENDOR, DEVICE_TYPE, DEVICE_SIGNATURE
    ACTIVE = "active"
    RETIRED = "retired"                 # DI-06: retire, never delete, if in use


class EvidenceType(str, Enum):          # spec section 7.1
    OUI = "oui"
    MDNS = "mdns"
    SSDP = "ssdp"
    NETBIOS_LLMNR = "netbios_llmnr"
    REVERSE_DNS = "reverse_dns"


class ScanStatus(str, Enum):            # SCAN.status: where the scan is in its life
    RUNNING = "running"
    COMPLETED = "completed"


class ScanOutcome(str, Enum):           # SCAN.outcome: how it ended (empty until it ends)
    SUCCESS = "success"
    PARTIAL = "partial"                 # finished, but some discovery methods were unavailable
    FAILED = "failed"


class UserRole(str, Enum):              # placeholder roles: confirm with the team
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class AccountStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class AlertEventName(str, Enum):        # ALERT_EVENT.event_name rows (spec section 7.5)
    NEW_UNIDENTIFIED_DEVICE = "new_unidentified_device"
    FLAGGED_DEVICE = "flagged_device"
    SCAN_FAILED = "scan_failed"
    BACKUP_FAILED = "backup_failed"


class AuditAction(str, Enum):
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"


class IdentificationLevel(str, Enum):   # spec 7.2; not stored: worked out from the evidence (2.2.4.4)
    IDENTIFIED = "identified"           # name, type, and model where known
    PARTIAL = "partial"                 # partially identified (generic): type and vendor only
    UNIDENTIFIED = "unidentified"       # shown as "Unknown"
