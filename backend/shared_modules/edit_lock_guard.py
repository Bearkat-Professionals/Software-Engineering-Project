"""SH-2 Edit Lock Guard: who has which record open for editing (#138).

A form takes a hold when it opens, renews it while it stays open, and releases it
when it closes. A hold that isn't renewed expires after lifetime_s, so a closed
browser can't keep a record held. SH-2 only reports (design 8.4): callers ask
held_by_others() and decide. Deletes and retires are refused while another user
holds the record; edits are never blocked.

Holds versus the version guard: a hold is a courtesy that warns others before they
act. The version guard (loaded_at checked against the audit log, under a row lock)
is what actually stops a lost update, and it still works when nobody took a hold
or a restart has dropped them all.

    guard = EditLockGuard()
    hold = guard.take(("device", 7), user_id=3, session_id=sid)
    guard.renew(hold.hold_id, user_id=3)                   # every 30 s from the open form
    guard.held_by_others(("device", 7), user_id=5)         # -> [hold]: user 3 is editing
    guard.release(hold.hold_id, user_id=3)                 # on save or cancel
    guard.release_session(sid)                             # on sign-out

In memory only, like the sign-in sessions: a restart drops every hold. Hold ids and
session ids are secrets, so nothing here is logged, and a Hold never carries the
session id.
"""
from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

RecordKey = tuple[str, int]             # (audit_log.record_type, record id), e.g. ("device", 7)

DEFAULT_LIFETIME_S = 120.0              # 2 minutes; the form renews every 30 s
DEFAULT_MAX_PER_USER = 20


@dataclass(frozen=True)
class Hold:
    """One open edit form's claim on a record.

    since is wall-clock time, for messages ("since 14:05"). expires_at is on the
    guard's monotonic clock, so changing the computer's clock can't end or stretch
    a hold."""
    hold_id: str
    key: RecordKey
    user_id: int
    since: datetime
    expires_at: float

    def alive(self, now: float) -> bool:
        return now < self.expires_at


class EditLockGuard:
    """The hold registry. Flask answers requests on several threads, so every method
    holds one lock from start to finish."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], datetime] = datetime.now,
                 lifetime_s: float = DEFAULT_LIFETIME_S,
                 max_per_user: int = DEFAULT_MAX_PER_USER) -> None:
        if lifetime_s <= 0:
            raise ValueError("lifetime_s must be more than 0")
        if max_per_user < 1:
            raise ValueError("max_per_user must be 1 or more")
        self.lifetime_s = lifetime_s
        self._clock = clock
        self._wall_clock = wall_clock
        self._max_per_user = max_per_user
        self._holds: dict[str, Hold] = {}       # hold id -> hold, in the order taken (oldest first)
        self._sessions: dict[str, str] = {}     # hold id -> the session that took it (private)
        self._lock = threading.Lock()

    def take(self, key: RecordKey, user_id: int, session_id: str) -> Hold:
        """A new hold on the record. If the user already has max_per_user holds,
        their oldest is released first. Taking the same record twice (two tabs)
        gives two holds, so closing one tab doesn't free the other."""
        key = _checked_key(key)
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be the signed-in session's id")
        now = self._clock()
        with self._lock:
            self._drop_expired(now)
            mine = [hold.hold_id for hold in self._holds.values() if hold.user_id == user_id]
            for hold_id in mine[:max(0, len(mine) - self._max_per_user + 1)]:
                self._remove(hold_id)
            hold = Hold(hold_id=secrets.token_urlsafe(16), key=key, user_id=user_id,
                        since=self._wall_clock(), expires_at=now + self.lifetime_s)
            self._holds[hold.hold_id] = hold
            self._sessions[hold.hold_id] = session_id
            return hold

    def renew(self, hold_id: str, user_id: int) -> Hold | None:
        """The hold with its expiry pushed out; None if it's gone, expired, or
        belongs to someone else (the form should then reload)."""
        if not isinstance(hold_id, str):
            return None
        now = self._clock()
        with self._lock:
            self._drop_expired(now)
            hold = self._holds.get(hold_id)
            if hold is None or hold.user_id != user_id:
                return None
            renewed = replace(hold, expires_at=now + self.lifetime_s)
            self._holds[hold_id] = renewed          # same key: keeps its place in the order
            return renewed

    def release(self, hold_id: str, user_id: int) -> None:
        """Ends the user's own hold. A hold that's already gone, or someone else's,
        is left alone without an error: the form is closing either way."""
        if not isinstance(hold_id, str):
            return
        with self._lock:
            hold = self._holds.get(hold_id)
            if hold is not None and hold.user_id == user_id:
                self._remove(hold_id)

    def release_session(self, session_id: str) -> int:
        """Ends every hold taken in this session (sign-out). Returns how many."""
        with self._lock:
            ended = [hold_id for hold_id, sid in self._sessions.items() if sid == session_id]
            for hold_id in ended:
                self._remove(hold_id)
            return len(ended)

    def held_by_others(self, key: RecordKey, user_id: int) -> list[Hold]:
        """Live holds on the record by users other than user_id, oldest first, one
        per user (their oldest): two tabs are still one person editing."""
        key = _checked_key(key)
        now = self._clock()
        with self._lock:
            self._drop_expired(now)
            found: dict[int, Hold] = {}
            for hold in self._holds.values():
                if hold.key == key and hold.user_id != user_id:
                    found.setdefault(hold.user_id, hold)
            return list(found.values())

    def count(self) -> int:
        """How many holds are live, for tests and status checks."""
        now = self._clock()
        with self._lock:
            self._drop_expired(now)
            return len(self._holds)

    # ---- inside the lock only ------------------------------------------------------

    def _drop_expired(self, now: float) -> None:
        for hold_id in [hold.hold_id for hold in self._holds.values() if not hold.alive(now)]:
            self._remove(hold_id)

    def _remove(self, hold_id: str) -> None:
        self._holds.pop(hold_id, None)
        self._sessions.pop(hold_id, None)


def _checked_key(key: RecordKey) -> RecordKey:
    """("device", 7) as given; a malformed key raises ValueError, because a hold under
    ("device", "7") would never match and would silently block nothing."""
    if (not isinstance(key, tuple) or len(key) != 2 or not isinstance(key[0], str) or not key[0]
            or not isinstance(key[1], int) or isinstance(key[1], bool) or key[1] < 1):
        raise ValueError(f"a record key is (record_type, id), e.g. ('device', 7), not {key!r}")
    return key
