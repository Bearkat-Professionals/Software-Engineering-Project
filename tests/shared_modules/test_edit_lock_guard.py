"""Tests for SH-2 Edit Lock Guard (#139). No database, no network, no real clock.

    py -3.13 -m unittest tests.shared_modules.test_edit_lock_guard -v
"""
import threading
import unittest
from datetime import datetime, timedelta

from backend.shared_modules.edit_lock_guard import EditLockGuard, Hold, RecordKey

DEVICE_7: RecordKey = ("device", 7)
VENDOR_7: RecordKey = ("vendor", 7)          # same id, different record type
SAM, ALEX, KIM = 1, 2, 3


class FakeClock:
    """The guard's monotonic clock: only moves when a test says so."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeWallClock:
    """datetime.now stand-in: one minute later on every call, so "oldest" is visible in since."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 16, 14, 0)

    def __call__(self) -> datetime:
        self.now += timedelta(minutes=1)
        return self.now


def make_guard(lifetime_s: float = 120.0, max_per_user: int = 20) -> tuple[EditLockGuard, FakeClock]:
    clock = FakeClock()
    return EditLockGuard(clock=clock, wall_clock=FakeWallClock(), lifetime_s=lifetime_s,
                         max_per_user=max_per_user), clock


def holders(guard: EditLockGuard, key: RecordKey) -> list[int]:
    """Who holds the record, as seen by a user who holds nothing (user 99)."""
    return [hold.user_id for hold in guard.held_by_others(key, user_id=99)]


class TakeRenewReleaseTests(unittest.TestCase):

    def test_take_gives_a_live_hold(self) -> None:
        guard, clock = make_guard()
        hold = guard.take(DEVICE_7, SAM, "session-a")
        self.assertIsInstance(hold, Hold)
        self.assertEqual((hold.key, hold.user_id), (DEVICE_7, SAM))
        self.assertEqual(hold.expires_at, clock.now + 120)
        self.assertEqual(hold.since, datetime(2026, 11, 16, 14, 1))
        self.assertEqual(holders(guard, DEVICE_7), [SAM])

    def test_hold_ids_are_random_and_long(self) -> None:
        guard, _ = make_guard()
        ids = {guard.take(DEVICE_7, SAM, "session-a").hold_id for _ in range(20)}
        self.assertEqual(len(ids), 20)
        self.assertTrue(all(len(hold_id) >= 20 for hold_id in ids))     # 16 random bytes as text

    def test_a_hold_does_not_carry_the_session_id(self) -> None:
        guard, _ = make_guard()
        hold = guard.take(DEVICE_7, SAM, "secret-session")
        self.assertNotIn("secret-session", repr(hold))

    def test_renew_pushes_the_expiry_out(self) -> None:
        guard, clock = make_guard()
        hold = guard.take(DEVICE_7, SAM, "session-a")
        clock.advance(30)
        renewed = guard.renew(hold.hold_id, SAM)
        self.assertEqual(renewed.expires_at, clock.now + 120)
        self.assertEqual((renewed.hold_id, renewed.since), (hold.hold_id, hold.since))

    def test_release_ends_the_hold(self) -> None:
        guard, _ = make_guard()
        hold = guard.take(DEVICE_7, SAM, "session-a")
        guard.release(hold.hold_id, SAM)
        self.assertEqual(holders(guard, DEVICE_7), [])
        self.assertEqual(guard.count(), 0)

    def test_releasing_a_hold_that_is_gone_does_nothing(self) -> None:
        guard, _ = make_guard()
        hold = guard.take(DEVICE_7, SAM, "session-a")
        guard.release(hold.hold_id, SAM)
        guard.release(hold.hold_id, SAM)                    # twice: no error
        guard.release("never-existed", SAM)
        guard.release(None, SAM)                            # type: ignore[arg-type]  (bad JSON)
        self.assertEqual(guard.count(), 0)

    def test_renewing_an_unknown_hold_gives_none(self) -> None:
        guard, _ = make_guard()
        self.assertIsNone(guard.renew("never-existed", SAM))
        self.assertIsNone(guard.renew(["not", "text"], SAM))    # type: ignore[arg-type]  (bad JSON)

    def test_renewing_a_released_hold_gives_none(self) -> None:
        guard, _ = make_guard()
        hold = guard.take(DEVICE_7, SAM, "session-a")
        guard.release(hold.hold_id, SAM)
        self.assertIsNone(guard.renew(hold.hold_id, SAM))


class ExpiryTests(unittest.TestCase):

    def test_a_hold_lives_until_its_lifetime_ends(self) -> None:
        guard, clock = make_guard()
        guard.take(DEVICE_7, SAM, "session-a")
        clock.advance(119.9)
        self.assertEqual(holders(guard, DEVICE_7), [SAM])
        clock.advance(0.1)                                  # exactly 120 s: gone
        self.assertEqual(holders(guard, DEVICE_7), [])
        self.assertEqual(guard.count(), 0)

    def test_renewals_keep_a_hold_alive(self) -> None:
        guard, clock = make_guard()
        hold = guard.take(DEVICE_7, SAM, "session-a")
        for _ in range(10):                                 # 10 x 100 s, far past one lifetime
            clock.advance(100)
            self.assertIsNotNone(guard.renew(hold.hold_id, SAM))
        self.assertEqual(holders(guard, DEVICE_7), [SAM])

    def test_renewing_an_expired_hold_gives_none(self) -> None:
        guard, clock = make_guard()
        hold = guard.take(DEVICE_7, SAM, "session-a")
        clock.advance(120)
        self.assertIsNone(guard.renew(hold.hold_id, SAM))
        self.assertEqual(guard.count(), 0)                  # and stays gone

    def test_lifetime_can_be_changed(self) -> None:
        guard, clock = make_guard(lifetime_s=10)
        guard.take(DEVICE_7, SAM, "session-a")
        clock.advance(10)
        self.assertEqual(guard.count(), 0)


class HeldByOthersTests(unittest.TestCase):

    def test_your_own_hold_is_not_listed(self) -> None:
        guard, _ = make_guard()
        guard.take(DEVICE_7, SAM, "session-a")
        self.assertEqual(guard.held_by_others(DEVICE_7, SAM), [])

    def test_two_other_users_are_listed_oldest_first(self) -> None:
        guard, _ = make_guard()
        guard.take(DEVICE_7, ALEX, "session-b")
        guard.take(DEVICE_7, SAM, "session-a")
        guard.take(DEVICE_7, KIM, "session-c")
        others = guard.held_by_others(DEVICE_7, SAM)
        self.assertEqual([hold.user_id for hold in others], [ALEX, KIM])
        self.assertLess(others[0].since, others[1].since)

    def test_renewing_does_not_change_who_is_oldest(self) -> None:
        guard, clock = make_guard()
        alex = guard.take(DEVICE_7, ALEX, "session-b")
        guard.take(DEVICE_7, KIM, "session-c")
        clock.advance(60)
        guard.renew(alex.hold_id, ALEX)
        self.assertEqual(holders(guard, DEVICE_7), [ALEX, KIM])

    def test_one_user_in_two_tabs_is_listed_once(self) -> None:
        guard, _ = make_guard()
        first = guard.take(DEVICE_7, ALEX, "session-b")
        guard.take(DEVICE_7, ALEX, "session-b")
        others = guard.held_by_others(DEVICE_7, SAM)
        self.assertEqual([hold.hold_id for hold in others], [first.hold_id])

    def test_closing_one_tab_keeps_the_other_tabs_hold(self) -> None:
        guard, _ = make_guard()
        first = guard.take(DEVICE_7, ALEX, "session-b")
        guard.take(DEVICE_7, ALEX, "session-b")
        guard.release(first.hold_id, ALEX)
        self.assertEqual(holders(guard, DEVICE_7), [ALEX])

    def test_other_records_are_not_listed(self) -> None:
        guard, _ = make_guard()
        guard.take(VENDOR_7, ALEX, "session-b")             # same id, another record type
        guard.take(("device", 8), ALEX, "session-b")
        self.assertEqual(holders(guard, DEVICE_7), [])


class OwnershipTests(unittest.TestCase):

    def test_someone_elses_hold_cannot_be_renewed(self) -> None:
        guard, clock = make_guard()
        hold = guard.take(DEVICE_7, ALEX, "session-b")
        clock.advance(100)
        self.assertIsNone(guard.renew(hold.hold_id, SAM))
        clock.advance(20)                                   # SAM's renew didn't extend it
        self.assertEqual(holders(guard, DEVICE_7), [])

    def test_someone_elses_hold_cannot_be_released(self) -> None:
        guard, _ = make_guard()
        hold = guard.take(DEVICE_7, ALEX, "session-b")
        guard.release(hold.hold_id, SAM)
        self.assertEqual(holders(guard, DEVICE_7), [ALEX])


class LimitTests(unittest.TestCase):

    def test_the_21st_hold_releases_the_users_oldest(self) -> None:
        guard, _ = make_guard()
        for device_id in range(1, 22):                      # devices 1 to 21
            guard.take(("device", device_id), SAM, "session-a")
        self.assertEqual(guard.count(), 20)
        self.assertEqual(holders(guard, ("device", 1)), [])        # the oldest went
        self.assertEqual(holders(guard, ("device", 2)), [SAM])
        self.assertEqual(holders(guard, ("device", 21)), [SAM])

    def test_the_limit_is_per_user(self) -> None:
        guard, _ = make_guard(max_per_user=2)
        guard.take(DEVICE_7, ALEX, "session-b")
        for device_id in range(1, 4):
            guard.take(("device", device_id), SAM, "session-a")
        self.assertEqual(holders(guard, DEVICE_7), [ALEX])         # ALEX untouched by SAM's limit
        self.assertEqual(guard.count(), 3)                         # ALEX's 1 + SAM's 2

    def test_expired_holds_do_not_count_toward_the_limit(self) -> None:
        guard, clock = make_guard(max_per_user=2)
        guard.take(("device", 1), SAM, "session-a")
        guard.take(("device", 2), SAM, "session-a")
        clock.advance(120)
        guard.take(("device", 3), SAM, "session-a")
        guard.take(("device", 4), SAM, "session-a")
        self.assertEqual(holders(guard, ("device", 3)), [SAM])
        self.assertEqual(guard.count(), 2)


class SessionTests(unittest.TestCase):

    def test_release_all_ends_everyones_holds(self) -> None:
        guard, _ = make_guard()
        guard.take(DEVICE_7, SAM, "session-a")
        guard.take(VENDOR_7, ALEX, "session-b")
        self.assertEqual(guard.release_all(), 2)
        self.assertEqual(guard.count(), 0)
        self.assertEqual(guard.release_session("session-a"), 0)    # nothing left behind for the session
        self.assertEqual(guard.release_all(), 0)                    # again: fine

    def test_release_session_releases_only_that_sessions_holds(self) -> None:
        guard, _ = make_guard()
        guard.take(DEVICE_7, SAM, "session-a")
        guard.take(("device", 8), SAM, "session-a")
        guard.take(VENDOR_7, SAM, "session-a2")             # SAM signed in on a second browser
        guard.take(DEVICE_7, ALEX, "session-b")
        self.assertEqual(guard.release_session("session-a"), 2)
        self.assertEqual(holders(guard, DEVICE_7), [ALEX])
        self.assertEqual(holders(guard, ("device", 8)), [])
        self.assertEqual(holders(guard, VENDOR_7), [SAM])

    def test_an_unknown_session_releases_nothing(self) -> None:
        guard, _ = make_guard()
        guard.take(DEVICE_7, SAM, "session-a")
        self.assertEqual(guard.release_session("session-z"), 0)
        self.assertEqual(guard.count(), 1)


class CheckTests(unittest.TestCase):

    def test_malformed_keys_are_refused(self) -> None:
        guard, _ = make_guard()
        for key in [("device", "7"), ("device", 0), ("device", -1), ("", 7), ["device", 7],
                    ("device", True), ("device",), None]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    guard.take(key, SAM, "session-a")       # type: ignore[arg-type]
                with self.assertRaises(ValueError):
                    guard.held_by_others(key, SAM)          # type: ignore[arg-type]

    def test_a_missing_session_id_is_refused(self) -> None:
        guard, _ = make_guard()
        for session_id in ["", None]:
            with self.subTest(session_id=session_id):
                with self.assertRaises(ValueError):
                    guard.take(DEVICE_7, SAM, session_id)   # type: ignore[arg-type]

    def test_bad_settings_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            EditLockGuard(lifetime_s=0)
        with self.assertRaises(ValueError):
            EditLockGuard(max_per_user=0)


class ThreadTests(unittest.TestCase):
    """Flask answers each request on its own thread. 20 threads start at the same moment
    (a Barrier) and hammer the guard; afterwards the registry must still add up."""

    THREADS = 20

    def run_threads(self, work) -> None:
        start = threading.Barrier(self.THREADS)
        errors: list[BaseException] = []

        def run(user_id: int) -> None:
            try:
                start.wait()
                work(user_id)
            except BaseException as exc:                    # a failure in a thread must fail the test
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(user_id,)) for user_id in range(1, self.THREADS + 1)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])

    def test_taking_and_releasing_at_once_leaves_nothing_behind(self) -> None:
        guard, _ = make_guard()

        def work(user_id: int) -> None:
            for device_id in range(1, 51):
                hold = guard.take(("device", device_id), user_id, f"session-{user_id}")
                guard.held_by_others(("device", device_id), user_id)
                guard.release(hold.hold_id, user_id)

        self.run_threads(work)
        self.assertEqual(guard.count(), 0)

    def test_the_limit_holds_under_load(self) -> None:
        guard, _ = make_guard()

        def work(user_id: int) -> None:
            for device_id in range(1, 31):                  # 30 each, limit 20
                guard.take(("device", device_id), user_id, f"session-{user_id}")

        self.run_threads(work)
        self.assertEqual(guard.count(), self.THREADS * 20)
        self.assertEqual(len(guard.held_by_others(("device", 30), user_id=99)), self.THREADS)
        self.assertEqual(guard.held_by_others(("device", 10), user_id=99), [])   # everyone's oldest 10 went


if __name__ == "__main__":
    unittest.main()
