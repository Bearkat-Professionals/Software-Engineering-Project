# Starter device types and signatures for device identification (#152).
# Run after seed_reference_data.sql. Every statement is safe to run again.

INSERT IGNORE INTO device_type (category_name, status) VALUES
    ('printer', 'active'),
    ('streaming device', 'active'),
    ('smart speaker', 'active'),
    ('computer', 'active'),
    ('phone or tablet', 'active'),
    ('router', 'active'),
    ('smart home', 'active'),
    ('game console', 'active'),
    ('nas', 'active');

# A signature is a duplicate when its evidence type and pattern are already present.
# The Apple TV pattern is intentionally narrower than _airplay._tcp so Sonos devices
# advertising AirPlay do not tie between streaming-device and smart-speaker rules.
# host=echo matches an Amazon Echo's mDNS host name (host=echoshow, host=echo-dot-...):
# some Echos announce only a generic service such as _spotify-connect._tcp.
INSERT INTO device_signature (
    device_type_id,
    pattern_value,
    evidence_type,
    resulting_device_name,
    resulting_model,
    status
)
SELECT
    device_type.device_type_id,
    seed.pattern_value,
    seed.evidence_type,
    seed.resulting_device_name,
    NULL,
    'active'
FROM (
    SELECT 'printer' AS category_name, '_ipp._tcp' AS pattern_value,
           'mdns' AS evidence_type, 'Network printer' AS resulting_device_name
    UNION ALL SELECT 'printer', '_pdl-datastream._tcp', 'mdns', 'Network printer'
    UNION ALL SELECT 'streaming device', '_googlecast._tcp', 'mdns', 'Google Cast device'
    UNION ALL SELECT 'streaming device', 'model=AppleTV', 'mdns', 'Apple TV'
    UNION ALL SELECT 'smart speaker', '_sonos._tcp', 'mdns', 'Sonos speaker'
    UNION ALL SELECT 'smart home', '_hap._tcp', 'mdns', 'HomeKit accessory'
    UNION ALL SELECT 'smart speaker', 'host=echo', 'mdns', 'Amazon Echo'
    UNION ALL SELECT 'streaming device', 'manufacturer=Roku', 'ssdp', 'Roku'
    UNION ALL SELECT 'router', 'Router', 'ssdp', 'Router'
    UNION ALL SELECT 'smart speaker', 'manufacturer=Sonos', 'ssdp', 'Sonos speaker'
    UNION ALL SELECT 'smart home', 'Philips hue bridge', 'ssdp', 'Hue Bridge'
    UNION ALL SELECT 'game console', 'Xbox', 'ssdp', 'Xbox'
    UNION ALL SELECT 'computer', 'DESKTOP-', 'netbios_llmnr', 'Windows PC'
    UNION ALL SELECT 'computer', 'LAPTOP-', 'netbios_llmnr', 'Windows laptop'
    UNION ALL SELECT 'phone or tablet', 'iphone', 'reverse_dns', 'iPhone'
    UNION ALL SELECT 'phone or tablet', 'android', 'reverse_dns', 'Android device'
) AS seed
INNER JOIN device_type
    ON device_type.category_name = seed.category_name
   AND device_type.status = 'active'
WHERE NOT EXISTS (
    SELECT 1
    FROM device_signature AS existing
    WHERE existing.evidence_type = seed.evidence_type
      AND existing.pattern_value = seed.pattern_value
);
