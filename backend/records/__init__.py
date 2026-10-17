"""Entity classes: one per table in the relational schema.

(ALERT_SETTING_EVENT is a pure link table and has no class; see AlertSetting.)
"""
from backend.records.alert_event import AlertEvent
from backend.records.alert_setting import AlertSetting
from backend.records.audit_log import AuditLog
from backend.records.device import Device
from backend.records.device_signature import DeviceSignature
from backend.records.device_type import DeviceType
from backend.records.entity import Entity
from backend.records.identification_evidence import IdentificationEvidence
from backend.records.oui_prefix import OuiPrefix
from backend.records.reference_entity import ReferenceEntity
from backend.records.scan import Scan
from backend.records.scan_observation import ScanObservation
from backend.records.user import User
from backend.records.vendor import Vendor

ALL_ENTITIES = (
    AlertEvent, AlertSetting, AuditLog, Device, DeviceSignature, DeviceType,
    IdentificationEvidence, OuiPrefix, Scan, ScanObservation, User, Vendor,
)

__all__ = [
    "ALL_ENTITIES", "AlertEvent", "AlertSetting", "AuditLog", "Device",
    "DeviceSignature", "DeviceType", "Entity", "IdentificationEvidence",
    "OuiPrefix", "ReferenceEntity", "Scan", "ScanObservation", "User", "Vendor",
]