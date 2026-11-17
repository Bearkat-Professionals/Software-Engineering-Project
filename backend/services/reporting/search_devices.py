"""2.4.1 Search Devices: "Validate criteria (SH-1) and return matching devices."

Part of the OOD's QueryReportService. The route passes the query string exactly as
it arrived (text from a URL); every value is checked, every problem is reported at
once (OUT-04), and the store is only asked when all of them pass.

    result = search_devices(DeviceStore(engine), request.args, page=1, per_page=50)
    result.page         # a DevicePage, or None when the criteria were refused
    result.reasons      # e.g. ("seen_to must be a date like 2026-11-07, not '7th'.",)

A parameter left out, or sent blank (an empty box or "any" in a form), means "any".
Unknown parameters are ignored, so page and per_page can travel in the same query.
Reasons repeat what was typed, so a screen that shows them must escape them (XSS).
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from backend.data_access.network_data import DevicePage, DeviceSearch
from backend.data_access.network_data.device_store import DEFAULT_PER_PAGE, SORT_COLUMNS
from backend.records.enums import NetworkStatus
from backend.shared_modules.validate_input import CONFIDENCE_MAX, CONFIDENCE_MIN, validate_confidence

TEXT_MAX = 100
LARGEST_ID = 2_147_483_647
ORDERS = {"asc": False, "desc": True}
STATUSES = tuple(status.value for status in NetworkStatus)
BOOLEANS = {"true": True, "false": False}
SHOWN_MAX = 40

_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_WHOLE = re.compile(r"[0-9]+")

@dataclass(frozen=True)
class SearchResult:
    page: DevicePage | None             
    reasons: tuple[str, ...] = ()       

    @property
    def ok(self) -> bool:
        return self.page is not None

class DeviceSearcher(Protocol):
    """DeviceStore is one; tests pass a fake."""

    def search(self, search: DeviceSearch = DeviceSearch(), page: int = 1,
               per_page: int = DEFAULT_PER_PAGE, unit: Any = None) -> DevicePage: ...

def search_devices(devices: DeviceSearcher, params: Mapping[str, str], page: int = 1,
                   per_page: int = DEFAULT_PER_PAGE) -> SearchResult:
    """Check the criteria, then search. Nothing is queried unless every value is valid."""
    search, reasons = parse_search(params)
    if reasons:
        return SearchResult(None, tuple(reasons))
    return SearchResult(devices.search(search, page, per_page))

def parse_search(params: Mapping[str, str]) -> tuple[DeviceSearch | None, list[str]]:
    """The query string as a DeviceSearch, or every reason it can't be one. Pure."""
    reasons: list[str] = []
    fields: dict[str, Any] = {}

    def check(name: str, field: str, parse) -> None:
        text = _given(params, name)
        if text is None:
            return
        value, reason = parse(name, text)
        if reason:
            reasons.append(reason)
        else:
            fields[field] = value

    check("type", "device_type_id", _id)
    check("vendor", "vendor_id", _id)
    check("status", "network_status", _status)
    check("flagged", "flagged_status", _boolean)
    check("trusted", "trusted_status", _boolean)
    check("confidence_min", "confidence_min", _confidence)
    check("confidence_max", "confidence_max", _confidence)
    check("seen_from", "seen_from", _date)
    check("seen_to", "seen_to", _date)
    check("q", "text", _text)
    check("sort", "sort", _sort)
    check("order", "descending", _order)

    low, high = fields.get("confidence_min"), fields.get("confidence_max")
    if low is not None and high is not None and low > high:
        reasons.append(f"confidence_min ({low}) must not be more than confidence_max ({high}).")
    start, end = fields.get("seen_from"), fields.get("seen_to")
    if start is not None and end is not None and start > end:
        reasons.append(f"seen_from ({start}) must not be after seen_to ({end}).")

    if reasons:
        return None, reasons
    return DeviceSearch(**fields), []

#one parameter each (name, text) -> (value, reason)
def _id(name: str, text: str) -> tuple[int | None, str | None]:
    if _WHOLE.fullmatch(text) and 1 <= int(text) <= LARGEST_ID:
        return int(text), None
    return None, f"{name} must be a whole number of 1 or more, not {_shown(text)}."

def _status(name: str, text: str) -> tuple[NetworkStatus | None, str | None]:
    if text.lower() in STATUSES:
        return NetworkStatus(text.lower()), None
    return None, f"{name} must be {_either(STATUSES)}, not {_shown(text)}."

def _boolean(name: str, text: str) -> tuple[bool | None, str | None]:
    if text.lower() in BOOLEANS:
        return BOOLEANS[text.lower()], None
    return None, f"{name} must be true or false, not {_shown(text)}."

def _confidence(name: str, text: str) -> tuple[int | None, str | None]:
    result = validate_confidence(text)                  # SH-1
    if result:
        return result.value, None
    return None, f"{name} must be a whole number from {CONFIDENCE_MIN} to {CONFIDENCE_MAX}, not {_shown(text)}."

def _date(name: str, text: str) -> tuple[date | None, str | None]:
    if _DATE.fullmatch(text):                           # fromisoformat alone also takes 20261107
        try:
            return date.fromisoformat(text), None
        except ValueError:                              # 2026-02-30 and the like
            pass
    return None, f"{name} must be a date like 2026-11-07, not {_shown(text)}."

def _text(name: str, text: str) -> tuple[str | None, str | None]:
    if len(text) > TEXT_MAX:                            # not repeated back: it could be anything
        return None, f"{name} must be at most {TEXT_MAX} characters, not {len(text)}."
    return text, None

def _sort(name: str, text: str) -> tuple[str | None, str | None]:
    if text.lower() in SORT_COLUMNS:
        return text.lower(), None
    return None, f"{name} must be {_either(tuple(SORT_COLUMNS))}, not {_shown(text)}."

def _order(name: str, text: str) -> tuple[bool | None, str | None]:
    if text.lower() in ORDERS:
        return ORDERS[text.lower()], None
    return None, f"{name} must be {_either(tuple(ORDERS))}, not {_shown(text)}."

#helpers 
def _given(params: Mapping[str, str], name: str) -> str | None:
    """The value trimmed, or None when it's missing or blank ("any")."""
    value = params.get(name)
    if not isinstance(value, str):
        return None
    return value.strip() or None

def _either(choices: tuple[str, ...]) -> str:
    """('a', 'b', 'c') -> "a, b or c" """
    return choices[0] if len(choices) == 1 else ", ".join(choices[:-1]) + " or " + choices[-1]

def _shown(text: str) -> str:
    """A bad value as a reason repeats it: quoted, and cut short if it's long."""
    return f"'{text[:SHOWN_MAX]}...'" if len(text) > SHOWN_MAX else f"'{text}'"