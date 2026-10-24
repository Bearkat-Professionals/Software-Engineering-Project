# Run once, right after database_schema.sql.
# DEVICE.device_type_id and DEVICE.vendor_id default to 1, so row 1 in each
# table must be "unknown" (DI-03). The ids are set explicitly for that reason.

INSERT INTO device_type (device_type_id, category_name, status) VALUES
    (1, 'unknown', 'active');

INSERT INTO vendor (vendor_id, vendor_name, status) VALUES
    (1, 'unknown', 'active');

# Events that can trigger an email alert (spec 7.5)
INSERT INTO alert_event (event_name) VALUES
    ('new_unidentified_device'),
    ('flagged_device'),
    ('scan_failed'),
    ('backup_failed');
