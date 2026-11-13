-- 002: add device.flag_set_by_user for #103 (a flag a user set sticks until a user trusts the device).
-- Run this once against an existing database created before PR #103.

ALTER TABLE device
    ADD COLUMN flag_set_by_user BOOLEAN NOT NULL DEFAULT FALSE AFTER flagged_status;
