import ipaddress
import math
import re
from dataclasses import dataclass
from typing import Any, Optional
CONFIDENCE_MIN = 0
CONFIDENCE_MAX = 100
MAX_TIMEOUT_SECONDS = 30

#Result Record
@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    value: Any = None
    reason: Optional[str] = None

    @classmethod
    def ok(cls, value):
        return cls(valid = True, value=value)

    @classmethod
    def reject(cls, reason):
        return cls(valid = False, reason = reason)
    def __bool__(self):
        return self.valid

#Helpers
def clean_text(text):
    if not isinstance(text, str):
        return None
    text = text.strip()
    return text or None

def show(value):
    if isinstance(value, str):
        return value.strip() or "(blank)"
    if value is None:
        return "(blank)"
    return str(value)

#IP Address
def validate_ip(text):
    cleaned = clean_text(text)
    if cleaned is None:
        return ValidationResult.reject ("The IP address field is empty.")
    try:
        address = ipaddress.IPv4Address(cleaned)
    except ValueError:
        return ValidationResult.reject(f"Invalid IP address: {cleaned}")
    return ValidationResult.ok(str(address))

#MAC Address
_MAC_COLON = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")    
_MAC_HYPHEN = re.compile(r"^[0-9A-Fa-f]{2}(-[0-9A-Fa-f]{2}){5}$")   
_MAC_DOTTED = re.compile(r"^[0-9A-Fa-f]{4}(\.[0-9A-Fa-f]{4}){2}$")

def validate_mac(text):
    cleaned = clean_text(text)
    if cleaned is None:
        return ValidationResult.reject("The MAC address field is empty.")
    if not (_MAC_COLON.match(cleaned) or _MAC_HYPHEN.match(cleaned) or _MAC_DOTTED.match(cleaned)):
        return ValidationResult.reject(f"{cleaned} is not a valid MAC address.")
    hex_digits = re.sub(r"[:\-.]", "", cleaned).upper()
    pairs = [hex_digits[i:i + 2] for i in range(0, 12, 2)]
    return ValidationResult.ok(":".join(pairs))

#Limits
MAX_SCOPE_PREFIX = 22
PRIVATE_NETWORKS = ( ipaddress.IPv4Network("10.0.0.0/8"), ipaddress.IPv4Network("172.16.0.0/12"), ipaddress.IPv4Network("192.168.0.0/16"),)

#Scope
def validate_scope(text):
    cleaned = clean_text(text)
    if cleaned is None:
        return ValidationResult.reject("The scan range field is empty.")

    if "/" not in cleaned:
        return ValidationResult.reject(f"{cleaned} is not a scan range. " "Use CIDR form, for example 192.168.1.0/24.")

    address_part, _, prefix_part = cleaned.partition("/")
    if not validate_ip(address_part):
        return ValidationResult.reject(f"{cleaned} is not a valid scan range: {address_part} is not a valid IP address.")
    if not prefix_part.isdigit() or not 0 <= int(prefix_part) <= 32:
        return ValidationResult.reject(f"{cleaned} is not a valid scan range.")

    try:
        network = ipaddress.IPv4Network(cleaned, strict = True)
    except ValueError: 
        suggested = ipaddress.IPv4Network(cleaned, strict = False)
        return ValidationResult.reject(f"Range should start at {suggested}, not {cleaned}.")
    
    if not any(network.subnet_of(private) for private in PRIVATE_NETWORKS):
        return ValidationResult.reject(f"Can't scan {network}: scans must stay inside 10.x.x.x, 172.16–31.x.x or 192.168.x.x.")

    if network.prefixlen < MAX_SCOPE_PREFIX:
        return ValidationResult.reject(f"{network} is too big. Use a range of /{MAX_SCOPE_PREFIX} or smaller, like /24.")

    return ValidationResult.ok(str (network))

#Confidence Score 
def validate_confidence(value):
    reason_bad = (f"{show(value)} is out of range. Confidence scores go from {CONFIDENCE_MIN} to {CONFIDENCE_MAX}.")

    if isinstance(value, bool):
        return ValidationResult.reject(reason_bad)
    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return ValidationResult.reject(reason_bad)
        number = int(value)
    elif isinstance(value, str):
        cleaned = value.strip()
        if len(cleaned) > 10 or not re.fullmatch(r"[+-]?\d+", cleaned):
            return ValidationResult.reject(reason_bad)
        number = int(cleaned)
    else:
        return ValidationResult.reject(reason_bad)

    if not CONFIDENCE_MIN <= number <= CONFIDENCE_MAX:
        return ValidationResult.reject(reason_bad)
    return ValidationResult.ok(number)

#Timeout
def validate_timeout(seconds):
    reason_bad = (f"Timeout must be more than 0 and no more than {MAX_TIMEOUT_SECONDS} seconds, not {show(seconds)}.")

    if isinstance(seconds, bool):
        return ValidationResult.reject(reason_bad)

    if isinstance(seconds, (int, float)):
        number = float(seconds)
    elif isinstance(seconds, str):
        try:
            number = float(seconds.strip())
        except ValueError:
            return ValidationResult.reject(reason_bad)
    else:
        return ValidationResult.reject(reason_bad)
    if not math.isfinite(number) or not 0 < number <= MAX_TIMEOUT_SECONDS:
        return ValidationResult.reject(reason_bad)
    return ValidationResult.ok(number)

VALIDATORS = {
    "ip" : validate_ip,
    "mac" : validate_mac,
    "scope" : validate_scope,
    "confidence" : validate_confidence,
    "timeout" : validate_timeout,
}

def validate(value, kind):
    check = VALIDATORS.get(kind)
    if check is None:
        return ValidationResult.reject(f"Unknown kind of value: {kind}.")
    return check(value)