"""Access Control Service (2.1, OOD AccessControlService): sign in, the password rule,
creating accounts and changing passwords (#89).

    service = AccessControlService(UserStore(engine))
    result = service.authenticate("sam", "the password")
    result.user            # the User when signed in, else None
    result.reason          # why not, in words a person can read

Rules (agreed on #86):
- One message for an unknown username and a wrong password, and both take the same
  time, so the login page can't be used to find out which usernames exist.
- 5 failed sign-ins for one username within 15 minutes lock that username for
  15 minutes. Counted in memory: a server restart clears it.
- Passwords: 12 to 128 characters, not the username. No "must contain a symbol" rules
  (NIST SP 800-63B: length matters more). Only Argon2id hashes are stored (BB-5).
- Passwords and hashes are never logged or returned (POL-03, SEC-08).

No Flask here: the Application Controller (#90) turns these results into responses.
"""
from __future__ import annotations

import math
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Protocol

from backend.black_boxes.password_hashing import hash_password, verify_password
from backend.data_access.account_settings import LastActiveAdminError
from backend.data_access.database_engine import IntegrityConstraintError
from backend.data_access.repository import RecordNotFoundError
from backend.records import User
from backend.records.enums import AccountStatus, UserRole

WRONG_LOGIN = "Username or password is incorrect."
DISABLED_LOGIN = "This account is disabled. Ask an admin to turn it back on."

MAX_TRACKED = 10_000                # most usernames whose failures are remembered (a few MB at most)
MAX_FAILURES = 5
FAILURE_WINDOW_S = 15 * 60          # 5 failures within this long ...
LOCKOUT_S = 15 * 60                 # ... lock the username for this long

PASSWORD_MIN = 12
PASSWORD_MAX = 128                  # stops huge passwords being sent to slow the server
USERNAME_MAX = 100                  # app_user.username is VARCHAR(100)
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._-]+")

ACCOUNT_NOT_FOUND = "The user account was not found."
SELF_ROLE = "You can't change your own admin role. Ask another admin to do it."
SELF_STATUS = "You can't disable your own account. Ask another admin to do it."
LAST_ADMIN = "The last active admin can't be disabled or demoted."


class UserFinderStorer(Protocol):
    """The parts of 3.3's UserStore used here; tests pass a fake."""
    def find(self, user_id: int) -> User | None: ...
    def find_by_username(self, username: str) -> User | None: ...
    def list(self) -> list[User]: ...
    def store(self, user: User, changed_by: int) -> User: ...
    def apply_account_change(self, user_id: int, changed_by: int, *,
                             role: UserRole | None = None,
                             account_status: AccountStatus | None = None,
                             password_hash: str | None = None) -> User: ...


@dataclass(frozen=True)
class LoginResult:
    user: User | None                   # None when refused
    reason: str | None = None           # why it was refused; None when signed in

    @property
    def signed_in(self) -> bool:
        return self.user is not None


@dataclass(frozen=True)
class AccountResult:
    user: User | None                   # the stored user; None when refused
    reasons: tuple[str, ...] = ()       # every reason it was refused (OUT-04)

    @property
    def ok(self) -> bool:
        return self.user is not None


@dataclass
class _Attempts:
    """Failed sign-ins for one username, kept in memory."""
    failures: list[float] = field(default_factory=list)     # clock times of recent failures
    locked_until: float = 0.0


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A real Argon2id hash nobody knows the password for. An unknown username is
    checked against it, so it costs the same time as a known one. Made once."""
    return hash_password("no account has this password, it only costs time")


class AccessControlService:
    def __init__(self, users: UserFinderStorer, clock: Callable[[], float] = time.monotonic) -> None:
        self.users = users
        self._clock = clock
        self._attempts: OrderedDict[str, _Attempts] = OrderedDict()
        self._lock = threading.Lock()           # Flask answers requests on several threads
        _dummy_hash()       # made now, at start-up, so the first unknown username isn't slower

    # ---- sign in -----------------------------------------------------------------

    def authenticate(self, username: str, password: str) -> LoginResult:
        """Sign in, or say why not. Never raises for bad input."""
        name = username.strip() if isinstance(username, str) else ""
        key = name.casefold()           # "Sam" and "sam" are one account (the column is case-insensitive)

        wait = self._seconds_locked(key)
        if wait:
            minutes = math.ceil(wait / 60)
            return LoginResult(None, f"Too many attempts. Try again in {minutes} minute{'s' * (minutes != 1)}.")

        user = self.users.find_by_username(name) if name else None
        stored_hash = user.password_hash if user is not None else _dummy_hash()
        password_ok = verify_password(stored_hash, password)     # runs either way: same time (SEC-08)

        if user is None or not password_ok:
            self._record_failure(key)
            return LoginResult(None, WRONG_LOGIN)
        if user.account_status != AccountStatus.ACTIVE:
            return LoginResult(None, DISABLED_LOGIN)
        self._clear_failures(key)
        return LoginResult(user)

    # ---- the rules ---------------------------------------------------------------

    def check_password_rule(self, username: str, password: str) -> str | None:
        """Why this password isn't allowed for this username, or None if it is."""
        if not isinstance(password, str):
            return "The password must be text."
        if len(password) < PASSWORD_MIN:
            return f"The password must be at least {PASSWORD_MIN} characters."
        if len(password) > PASSWORD_MAX:
            return f"The password can be at most {PASSWORD_MAX} characters."
        if not password.strip():
            return "The password can't be only spaces."
        if isinstance(username, str) and password.strip().casefold() == username.strip().casefold():
            return "The password can't be the same as the username."
        return None

    def check_username(self, username: str) -> str | None:
        """Why this username can't be used for a new account, or None if it can."""
        if not isinstance(username, str) or not username:
            return "The username is empty."
        if len(username) > USERNAME_MAX:
            return f"The username can be at most {USERNAME_MAX} characters."
        if not USERNAME_PATTERN.fullmatch(username):
            return "The username can only use letters, digits, dots, underscores and hyphens."
        return None

    # ---- accounts ----------------------------------------------------------------

    def create_user(self, username: str, password: str, role: UserRole | str,
                    created_by: int) -> AccountResult:
        """Create an account. Every problem is reported at once; nothing is stored unless all pass."""
        reasons = [reason for reason in (self.check_username(username),
                                         self.check_password_rule(username, password)) if reason]
        try:
            role = UserRole(role)
        except (TypeError, ValueError):
            reasons.append(f"'{role}' is not a role. Use admin, editor or viewer.")
        if not reasons and self.users.find_by_username(username) is not None:
            reasons.append(f"The username '{username}' is already taken.")
        if reasons:
            return AccountResult(None, tuple(reasons))

        user = User(username=username, password_hash=hash_password(password), role=role)
        try:
            return AccountResult(self.users.store(user, changed_by=created_by))   # audited (POL-02)
        except IntegrityConstraintError:            # someone took the name a moment ago
            return AccountResult(None, (f"The username '{username}' is already taken.",))

    def change_password(self, user: User, current_password: str, new_password: str) -> AccountResult:
        """Change a user's own password, after checking the current one."""
        if not isinstance(current_password, str) or not verify_password(user.password_hash, current_password):
            return AccountResult(None, ("The current password is incorrect.",))
        reason = self.check_password_rule(user.username, new_password)
        if reason is None and current_password == new_password:
            reason = "The new password must be different from the current one."
        if reason:
            return AccountResult(None, (reason,))
        changed = replace(user, password_hash=hash_password(new_password))
        return AccountResult(self.users.store(changed, changed_by=user.user_id))   # audit says password_changed

    def list_users(self) -> list[User]:
        """Accounts for the admin screen. User.to_dict() removes hashes at the route."""
        return self.users.list()

    def set_role(self, user_id: int, role: UserRole | str, changed_by: int) -> AccountResult:
        target = self.users.find(user_id)
        if target is None:
            return AccountResult(None, (ACCOUNT_NOT_FOUND,))
        try:
            role = UserRole(role)
        except (TypeError, ValueError):
            return AccountResult(None, (f"'{role}' is not a role. Use admin, editor or viewer.",))
        if target.user_id == changed_by and role != UserRole.ADMIN:
            return AccountResult(None, (SELF_ROLE,))
        if target.role == role:
            return AccountResult(target)
        return self._apply_admin_change(target.user_id, changed_by, role=role)

    def set_status(self, user_id: int, status: AccountStatus | str,
                   changed_by: int) -> AccountResult:
        target = self.users.find(user_id)
        if target is None:
            return AccountResult(None, (ACCOUNT_NOT_FOUND,))
        try:
            status = AccountStatus(status)
        except (TypeError, ValueError):
            return AccountResult(None, (f"'{status}' is not a status. Use active or disabled.",))
        if target.user_id == changed_by and status == AccountStatus.DISABLED:
            return AccountResult(None, (SELF_STATUS,))
        if target.account_status == status:
            return AccountResult(target)
        return self._apply_admin_change(target.user_id, changed_by, account_status=status)

    def reset_password(self, user_id: int, password: str, changed_by: int) -> AccountResult:
        target = self.users.find(user_id)
        if target is None:
            return AccountResult(None, (ACCOUNT_NOT_FOUND,))
        reason = self.check_password_rule(target.username, password)
        if reason is None and verify_password(target.password_hash, password):
            reason = "The new password must be different from the current one."
        if reason:
            return AccountResult(None, (reason,))
        return self._apply_admin_change(
            target.user_id, changed_by, password_hash=hash_password(password))

    def _apply_admin_change(self, user_id: int, changed_by: int, **changes: object) -> AccountResult:
        try:
            changed = self.users.apply_account_change(user_id, changed_by, **changes)
        except RecordNotFoundError:
            return AccountResult(None, (ACCOUNT_NOT_FOUND,))
        except LastActiveAdminError:
            return AccountResult(None, (LAST_ADMIN,))
        return AccountResult(changed)

    # ---- failed sign-ins (in memory) ---------------------------------------------

    def _seconds_locked(self, key: str) -> float:
        with self._lock:
            attempts = self._attempts.get(key)
            if attempts is None:
                return 0.0
            return max(0.0, attempts.locked_until - self._clock())

    def _record_failure(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            attempts = self._attempts.pop(key, None) or _Attempts()
            attempts.failures = [at for at in attempts.failures if now - at < FAILURE_WINDOW_S]
            attempts.failures.append(now)
            if len(attempts.failures) >= MAX_FAILURES:
                attempts.locked_until = now + LOCKOUT_S
                attempts.failures.clear()
            self._attempts[key] = attempts          # re-added at the end: the most recent
            self._forget_old(now)

    def _clear_failures(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)

    def _forget_old(self, now: float) -> None:
        """Keep at most MAX_TRACKED usernames, so made-up usernames can't fill memory.

        Over the limit, usernames with nothing recent go first; if that isn't enough,
        the ones that failed longest ago go, even if still recent or locked."""
        if len(self._attempts) <= MAX_TRACKED:
            return
        for key, attempts in list(self._attempts.items()):
            recent = any(now - at < FAILURE_WINDOW_S for at in attempts.failures)
            if not recent and attempts.locked_until <= now:
                del self._attempts[key]
        while len(self._attempts) > MAX_TRACKED:
            self._attempts.popitem(last=False)      # the least recently failed
