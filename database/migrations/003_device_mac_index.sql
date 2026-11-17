-- 003: index the MAC lookup used by locking device saves for #140.
-- Run this once against an existing database created before PR #162.

CREATE INDEX idx_device_mac ON device (mac_address, is_randomized_mac);
