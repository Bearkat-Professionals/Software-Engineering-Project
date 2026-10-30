export const ENUMS = {

    vendor: { status: ["active", "retired"]},
    device_type: { status: ["active", "retired"]},
    device_signature: { status: ["active", "retired"], evidence_type: ["oui", "mdns", "ssdp", "netbios_llmnr", "reverse_dns"]},
    app_user: { role: ["admin", "editor", "viewer"], account_status: ["active", "disabled"]},
    device: { network_status: ["online", "offline"]},
    scan: { status: ["running", "completed"], outcome: ["success", "partial", "failed"]},
    scan_observation: { status: ["online", "offline"]},
    identification_evidence: { evidence_type: ["oui", "mdns", "ssdp", "netbios_llmnr", "reverse_dns"]},
    alert_event: { event_name: ["new_unidentified_device", "flagged_device", "scan_failed", "backup_failed"]},
    audit_log: { action: ["insert", "update", "delete"]}

};

export const BOOLEANS = {

    device: ["is_randomized_mac", "trusted_status", "flagged_status"],
    alert_setting: ["enabled"]

};

export const FOREIGN_KEYS = {

    oui_prefix: { vendor_id: { table: "vendor", label: "vendor_name"} },

    device: {

        device_type_id: { table: "device_type", label: "category_name"},
        vendor_id: {table: "vendor", label: "vendor_name"}

    },

    device_signature: { device_type_id: { table: "device_type", label: "category_name"} },
    scan: { user_id: { table: "app_user", label: "username"} },

    scan_observation: {

        scan_id: { table: "scan", label: "target_scope" },
        device_id: { table: "device", label: "mac_address" }

    },

    identification_evidence: {

        device_id: { table: "device", label: "mac_address"},
        signature_id: { table: "device_signature", label: "pattern_value" } 

    },

    alert_setting: { user_id: { table: "app_user", label: "username"} },
    audit_log: { user_id: { table: "app_user", label: "username"} }

};

export const OPTIONAL_FK = {

    device: ["device_type_id", "vendor_id"],
    identification_evidence: ["signature_id"]

};