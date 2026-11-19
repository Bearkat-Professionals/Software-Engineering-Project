-- 004: store the default gateway BB-2 infers during each scan (#178).
-- Run this once against an existing database and its matching test database.

ALTER TABLE scan
    ADD COLUMN gateway_ip VARCHAR(45) NULL,
    ADD COLUMN gateway_mac VARCHAR(17) NULL;
