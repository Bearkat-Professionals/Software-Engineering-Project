import json
import logging
from datetime import datetime, timezone

from backend.data_access.account_settings import UserStore
from backend.data_access.network_data import DeviceSearch, DeviceStore, ScanStore
from backend.data_access.reference_data import ReferenceStore
from backend.records import DeviceSignature
from backend.records.enums import ScanStatus
from backend.services.records.reference_maintenance import MaintenanceStatus
from backend.services.reporting.device_detail import resolve_evidence
from backend.services.scan.assign_level import stored_level
from backend.services.scan.evidence import EvidenceItem



logger = logging.getLogger(__name__)

FORMAT = "network-device-export"
FORMAT_VERSION = 1
MAX_EXPORT_DEVICES = 10000



class ExportResult:
    def __init__(self, status, document, reason):
        self.status = status
        self.document = document
        self.reason = reason



def iso_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.astimezone()
    value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")




def get_text(value):
    if value is None:
        return None
    return value.value




def get_evidence_id(record):
    return record.evidence_id



def get_observation_id(observation):
    return observation.observation_id



def device_export(listing, evidence, signatures):
    device = listing.device
    result = resolve_evidence(evidence, signatures)
    resolution = result[0]


    evidence_rows = []
    for record in sorted(evidence, key=get_evidence_id):
        item = EvidenceItem.from_record(record)
        if item in resolution.kept:
            counted = True
        else:
            counted = False
        row = {
        "evidence_type": get_text(record.evidence_type),
        "raw_value": record.raw_value,
        "weight": record.weight,
        "signature_id": record.signature_id,
        "counted": counted,
        }
        evidence_rows.append(row)


    level = stored_level(device.device_type_id, device.vendor_id, device.device_name, device.confidence_score)


    observed = {
        "mac": device.mac_address,
        "is_randomized_mac": device.is_randomized_mac,
        "ip": device.current_ip,
        "hostname": device.hostname,
        "network_status": get_text(device.network_status),
        "first_seen": iso_utc(device.first_seen),
        "last_seen": iso_utc(device.last_seen),
    }


    identification = {
        "type": listing.device_type_name,
        "vendor": listing.vendor_name,
        "name": device.device_name,
        "model": device.model,
        "level": get_text(level),
        "confidence_score": device.confidence_score,
    }

    user_marks = {
    "trusted": device.trusted_status,
    "flagged": device.flagged_status,
    "flag_set_by_user": device.flag_set_by_user,
    }


    one_device = {
        "device_id": device.device_id,
        "observed": observed,
        "identification": identification,
        "user_marks": user_marks,
        "evidence": evidence_rows,
    }
    return one_device



def scan_export(scan, started_by_username, observation):
    observation_rows = []
    for observation in sorted(observation, key=get_observation_id):
        row = {
            "device_id": observation.device_id,
            "ip": observation.ip_address,
            "hostname": observation.hostname,
            "status": get_text(observation.status),
            "observed_at": iso_utc(observation.observed_at),
        }
        observation_rows.append(row)


    one_scan = {
        "scan_id": scan.scan_id,
        "scope": scan.target_scope,
        "started_by": started_by_username,
        "started_at": iso_utc(scan.started_at),
        "ended_at": iso_utc(scan.ended_at),
        "outcome": get_text(scan.outcome),
        "errors": scan.error_message,
        "observations": observation_rows,
    }
    return one_scan


def devices_scope(search):
    seen_from = None
    if search.seen_from is not None:
        seen_from = search.seen_from.isoformat()
    seen_to = None
    if search.seen_to is not None:
        seen_to = search.seen_to.isoformat()

    filters = {
        "device_type_id": search.device_type_id,
        "vendor_id": search.vendor_id,
        "network_status": get_text(search.network_status),
        "flagged_status": search.flagged_status,
        "trusted_status": search.trusted_status,
        "is_randomized_mac": search.is_randomized_mac,
        "confidence_min": search.confidence_min,
        "confidence_max": search.confidence_max,
        "seen_from": seen_from,
        "seen_to": seen_to,
        "text": search.text,
    }
    scope = {
        "kind": "devices",
        "filters": filters,
        "sort": search.sort,
        "descending": search.descending,
    }
    return scope


def scan_scope(scan_id):
    scope = {
        "kind": "scan",
        "scan_id": scan_id,
    }
    return scope





def export_text(document):
    text = json.dumps(document, ensure_ascii=False, indent= 2)
    return text + "\n"


class ExportService:
    def __init__(self, unit_of_work, devices, scans, signatures, users):
        self.unit_of_work = unit_of_work
        self.devices = devices
        self.scans = scans
        self.signatures = signatures
        self.users = users



    def export_devices(self, search, exported_by):
        with self.unit_of_work() as unit:
            listings = self.devices.search_all(search, MAX_EXPORT_DEVICES, unit)
            if len(listings) > MAX_EXPORT_DEVICES:
                fail_msg = "More than " + str(MAX_EXPORT_DEVICES) + " devices match. Narrow the filters."
                return ExportResult(MaintenanceStatus.REJECTED, None, fail_msg)
            devices = self.devices_export(listings, unit)


        if search == DeviceSearch():
            scope = "all"
        else:
            scope = "filtered"
        log_msg = exported_by.username + " exported devices (" + scope + "): " + str(len(devices)) + " devices"
        logger.info(log_msg)

        document = self.make_document(exported_by, devices_scope(search), None, devices)
        return ExportResult(MaintenanceStatus.DONE, document, None)


    def export_scan(self, scan_id, exported_by):
        with self.unit_of_work() as unit:
            scan = self.scans.find(scan_id, unit)
            if scan is None or scan.status != ScanStatus.COMPLETED:     
                fail_msg = "Scan " + str(scan_id) + " doesn't exist or hasn't finished."
                return ExportResult(MaintenanceStatus.NOT_FOUND, None, fail_msg)

            started_by = self.users.find(scan.user_id, unit)       
            observations = self.scans.observations_for(scan_id, unit)
            device_ids = []
            for observation in observations:       
                device_ids.append(observation.device_id)
            listings = self.devices.listings_for(device_ids, unit)
            devices = self.devices_export(listings, unit)



        if started_by is None:        #the user could have been deleted
            username = None
        else:
            username = started_by.username
        log_msg = exported_by.username + " exported scan " + str(scan_id) + ": " + str(len(devices)) + " devices"
        logger.info(log_msg)

        one_scan = scan_export(scan, username, observations)
        document = self.make_document(exported_by, scan_scope(scan_id), one_scan, devices)
        return ExportResult(MaintenanceStatus.DONE, document, None)

    def devices_export(self, listings, unit):
        device_ids = []
        for listing in listings:
            device_ids.append(listing.device.device_id)
        evidence = self.scans.evidence_for_devices(device_ids, unit)

        signatures = {}
        for device_id in evidence:
            for record in evidence[device_id]:
                if record.signature_id is not None and record.signature_id not in signatures:
                    signatures[record.signature_id] = self.signatures.find(record.signature_id, unit)

        devices = []
        for listing in listings:
            device_evidence = evidence.get(listing.device.device_id, [])
            one_device = device_export(listing, device_evidence, signatures)
            devices.append(one_device)
        return devices

    def make_document(self, exported_by, scope, scan, devices):
        now = datetime.now(timezone.utc)
        document = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "exported_at": iso_utc(now),
            "exported_by": exported_by.username,
            "scope": scope,
            "device_count": len(devices),
            "devices": devices,
            "scan": scan,
        }
        return document


def build_export_service(engine):
    devices = DeviceStore(engine)
    scans = ScanStore(engine)
    signatures = ReferenceStore(engine, DeviceSignature)
    users = UserStore(engine)
    service = ExportService(engine.unit_of_work, devices, scans, signatures, users)
    return service

    
             

            




