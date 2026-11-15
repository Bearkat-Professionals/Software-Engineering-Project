"""Helpers the API route modules share (#51, #126, the device record routes): reading the
request the same way everywhere, and the conflict notice's wording. Each stops a bad
request with a 400 and a sentence a person can read. No database code here (tier rules).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from flask import abort, request

from backend.records import User

MAX_PAGE = 1_000_000            # keeps OFFSET far inside what MySQL accepts
NOT_AN_OBJECT = "Send the values as a JSON object."


class UserFinder(Protocol):
    """UserStore is one: to name who made a conflicting change."""

    def find(self, user_id: int) -> User | None: ...


def whole_number(name: str, *, default: int, largest: int) -> int:
    """A query value like ?page=2 as a number from 1 to largest; stops with 400 if it isn't."""
    text = request.args.get(name)
    if text is None:
        return default
    cleaned = text.strip()
    if not cleaned.isdecimal() or not 1 <= int(cleaned) <= largest:
        abort(400, description=f"{name} must be a whole number from 1 to {largest}, not '{text}'.")
    return int(cleaned)


def json_body() -> dict[str, Any]:
    """The JSON body as a dict. A body that isn't JSON at all is already 415 (auth.py)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400, description=NOT_AN_OBJECT)
    return body


def read_loaded_at(value: Any, reload: str) -> datetime:
    """loaded_at as a screen sends it back (from a GET): an ISO time with no time zone.
    Stops with 400 and `reload` if it's missing, unreadable, or has a time zone: the audit
    log's times are plain local times, and comparing the two would fail."""
    if not isinstance(value, str):
        abort(400, description=reload)
    try:
        when = datetime.fromisoformat(value.strip())
    except ValueError:
        abort(400, description=reload)
    if when.tzinfo is not None:
        abort(400, description=reload)
    return when


def conflict_json(users: UserFinder, label: str, changed_by: int | None, changed_at: datetime | None,
                  loaded_at: datetime | None) -> dict[str, Any]:
    """'This vendor was changed by sam at 14:05 after you opened it.' with who and when as
    fields too. The services know only the user's id; the date is added when the change
    wasn't on the day the record was opened."""
    user = users.find(changed_by) if changed_by is not None else None
    who = user.username if user is not None else "another user"
    if changed_at is None:
        when = ""
    elif loaded_at is not None and changed_at.date() == loaded_at.date():
        when = f" at {changed_at:%H:%M}"
    else:
        when = f" at {changed_at:%Y-%m-%d %H:%M}"
    return {"error": f"This {label} was changed by {who}{when} after you opened it. Reload to see their changes.",
            "changed_by": who,
            "changed_at": changed_at.isoformat() if changed_at is not None else None}
