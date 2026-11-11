-- 001: add sent-alert history for spec 7.5 and AT-12.
-- Run this once against an existing database created before PR #111.

CREATE TABLE IF NOT EXISTS alert_history(
    alert_history_id INT AUTO_INCREMENT PRIMARY KEY,
    event_id INT NOT NULL,
    recipient_email VARCHAR(320) NOT NULL,
    sent_at DATETIME(6) NOT NULL,
    device_id INT,
    scan_id INT,

    INDEX idx_alert_history_delivery
        (event_id, device_id, scan_id, recipient_email, sent_at),

    FOREIGN KEY (event_id) REFERENCES alert_event(event_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (device_id) REFERENCES device(device_id)
        ON DELETE SET NULL,
    FOREIGN KEY (scan_id) REFERENCES scan(scan_id)
        ON DELETE SET NULL
) ENGINE=InnoDB;
