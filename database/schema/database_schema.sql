#   Create and select the database first:
#
#   CREATE DATABASE IF NOT EXISTS network_device_scanner
#		CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;   # utf8mb4 matches the Python connection
#   USE network_device_scanner;
#
# Then run this file, then database/seed/seed_reference_data.sql (the "unknown" rows the defaults point at).

CREATE TABLE app_user(
    user_id INT AUTO_INCREMENT PRIMARY KEY,           #auto_increment automatically gives each new record the next ID number
    username VARCHAR(100) NOT NULL UNIQUE,            #login looks users up by name, so it must be unique
    password_hash VARCHAR(255) NOT NULL,              #hash only, never the plain password (SEC-02)
    role VARCHAR(50) NOT NULL,                        #admin, editor, viewer
    account_status VARCHAR(30) NOT NULL               #active or disabled
) ENGINE=InnoDB;

CREATE TABLE device_type(
    device_type_id INT AUTO_INCREMENT PRIMARY KEY,
    category_name VARCHAR(100) NOT NULL UNIQUE,       #DI-07: no duplicate categories
    status VARCHAR(30) NOT NULL                       #active or retired
) ENGINE=InnoDB;

CREATE TABLE vendor(
    vendor_id INT AUTO_INCREMENT PRIMARY KEY,
    vendor_name VARCHAR(255) NOT NULL UNIQUE,         #DI-07: no duplicate vendors
    status VARCHAR(30) NOT NULL                       #active or retired
) ENGINE=InnoDB;

CREATE TABLE oui_prefix(
    oui_id INT AUTO_INCREMENT PRIMARY KEY,
    oui_prefix VARCHAR(17) NOT NULL UNIQUE,           #DI-07: the same OUI twice is rejected
    vendor_id INT NOT NULL,

    FOREIGN KEY (vendor_id)
        REFERENCES vendor(vendor_id)
        ON DELETE RESTRICT                            #rule that stops user from accidentally deleting important data
        ON UPDATE RESTRICT                            #rule that blocks changing an ID while other tables are using it
) ENGINE=InnoDB;

CREATE TABLE device (
    device_id INT AUTO_INCREMENT PRIMARY KEY,
    mac_address VARCHAR(17) NOT NULL,
    is_randomized_mac BOOLEAN NOT NULL DEFAULT FALSE, #DI-02: randomized MACs are non-unique and never merged
    current_ip VARCHAR(45),
    hostname VARCHAR(255),
    device_type_id INT NOT NULL DEFAULT 1,            #1 = the "unknown" device type from the seed file (DI-03)
    vendor_id INT NOT NULL DEFAULT 1,                 #1 = the "unknown" vendor from the seed file
    device_name VARCHAR(255),
    model VARCHAR(255),
    trusted_status BOOLEAN NOT NULL DEFAULT FALSE,
    flagged_status BOOLEAN NOT NULL DEFAULT FALSE,
    confidence_score INT NOT NULL DEFAULT 0,          #whole percent, 0 to 100
    first_seen DATETIME,                              #tracks the exact moment a device was initially detected on the network
    last_seen DATETIME,
    network_status VARCHAR(50),                       #online or offline

    mac_unique_key VARCHAR(17) AS (IF(is_randomized_mac, NULL, mac_address)) STORED,
    UNIQUE KEY unique_nonrandomized_mac (mac_unique_key),

    CHECK (confidence_score BETWEEN 0 AND 100),       #rule ensures the confidence score is always between 0 and 100

    INDEX idx_device_current_ip (current_ip),         #search by IP
    INDEX idx_device_last_seen (last_seen),           #filter by date range

    FOREIGN KEY (device_type_id)
        REFERENCES device_type(device_type_id)        #can only assign a type that is on the master list
        ON DELETE RESTRICT,

    FOREIGN KEY (vendor_id)
        REFERENCES vendor(vendor_id)                  #block accidental deletions
        ON DELETE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE scan (
    scan_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,                             #who started the scan (CM-11)
    target_scope VARCHAR(255),
    configuration_used TEXT,                          #JSON text: filters, timeouts
    started_at DATETIME,
    ended_at DATETIME,
    status VARCHAR(50),                               #running or completed
    outcome VARCHAR(100),                             #success, partial or failed (empty until the scan ends)
    error_message TEXT,

    INDEX idx_scan_started_at (started_at),           #recent scans first

    FOREIGN KEY (user_id)
        REFERENCES app_user(user_id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE scan_observation(
    observation_id INT AUTO_INCREMENT PRIMARY KEY,
    scan_id INT NOT NULL,
    device_id INT NOT NULL,
    ip_address VARCHAR(45),
    hostname VARCHAR(255),
    status VARCHAR(50),                               #online or offline
    observed_at DATETIME,                             #stores the date and time when a device was detected during the scan

    UNIQUE (scan_id, device_id),                      #a scan records each device at most once

    FOREIGN KEY (scan_id) REFERENCES scan(scan_id)
        ON DELETE RESTRICT,                           #scan history is kept
    FOREIGN KEY (device_id) REFERENCES device(device_id)
        ON DELETE CASCADE                             #deletes a device's observation records if the device is deleted (DI-05)
) ENGINE=InnoDB;

CREATE TABLE device_signature(
    signature_id INT AUTO_INCREMENT PRIMARY KEY,
    device_type_id INT NOT NULL,
    pattern_value TEXT NOT NULL,
    evidence_type VARCHAR(100) NOT NULL,              #oui, mdns, ssdp, netbios_llmnr, reverse_dns
    resulting_device_name VARCHAR(255),
    resulting_model VARCHAR(255),
    status VARCHAR(30) NOT NULL,                      #active or retired

    FOREIGN KEY (device_type_id)
        REFERENCES device_type(device_type_id)
        ON DELETE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE identification_evidence (
    evidence_id INT AUTO_INCREMENT PRIMARY KEY,
    device_id INT NOT NULL,
    signature_id INT,                                 #NULL when no signature applies (e.g. an OUI match)
    evidence_type VARCHAR(100) NOT NULL,
    raw_value TEXT,
    weight INT NOT NULL DEFAULT 0,                    #points this evidence adds to the confidence score

    FOREIGN KEY (device_id) REFERENCES device(device_id)
        ON DELETE CASCADE,                            #deletes a device's evidence if the device is deleted (DI-05)
    FOREIGN KEY (signature_id)
        REFERENCES device_signature(signature_id)
        ON DELETE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE alert_event(
    event_id INT AUTO_INCREMENT PRIMARY KEY,
    event_name VARCHAR(100) NOT NULL UNIQUE
) ENGINE=InnoDB;

CREATE TABLE alert_setting(
    alert_setting_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    recipient_email VARCHAR(320) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,

    FOREIGN KEY (user_id) REFERENCES app_user(user_id)
        ON DELETE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE alert_setting_event(
    alert_setting_id INT NOT NULL,
    event_id INT NOT NULL,
    PRIMARY KEY (alert_setting_id, event_id),

    FOREIGN KEY (alert_setting_id)
        REFERENCES alert_setting(alert_setting_id)
        ON DELETE CASCADE,                            #deleting an alert setting removes its event links

    FOREIGN KEY (event_id)
        REFERENCES alert_event(event_id)
        ON DELETE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE audit_log(
    audit_log_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    record_type VARCHAR(100) NOT NULL,                #which table changed
    record_id INT NOT NULL,                           #which row
    action VARCHAR(100) NOT NULL,                     #insert, update or delete
    old_value TEXT,                                   #JSON text
    new_value TEXT,                                   #JSON text
    changed_at DATETIME(6) NOT NULL,                  #microseconds, so two edits in the same second are told apart

    # Edit conflicts are found by reading the latest entry for one record
    # (OOD 3.2 checkConflict), so that lookup gets its own index.
    INDEX idx_audit_record (record_type, record_id, changed_at),

    FOREIGN KEY (user_id) REFERENCES app_user(user_id)
        ON DELETE RESTRICT
) ENGINE=InnoDB;
