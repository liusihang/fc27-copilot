import threading
import time
import unittest

from fc27.auto_sync import AutoSyncCoordinator


class AutoSyncCoordinatorTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.called = threading.Event()

        def synchronize(kind):
            self.calls.append(kind)
            self.coordinator.note_full_sync()
            self.called.set()

        self.coordinator = AutoSyncCoordinator(
            synchronize,
            threading.Lock(),
            debounce_seconds=0.02,
            login_delay_seconds=0.02,
            retry_seconds=0.02,
        )

    def tearDown(self):
        self.coordinator.stop()

    def test_authenticated_session_schedules_one_full_sync(self):
        result = self.coordinator.accept(
            {
                "event_id": "login-1",
                "type": "session_authenticated",
                "observed_at": "2026-09-19T00:00:00Z",
                "data": {
                    "authenticated": True,
                    "gameVersion": "fc27",
                    "apiHost": "utas.external.s2.fut.ea.com",
                    "capturedAt": "2026-09-19T00:00:00Z",
                },
            }
        )
        self.assertTrue(result["scheduled"])
        self.assertTrue(self.called.wait(1))
        self.assertEqual(self.calls, ["auto_login"])
        self.assertIsNotNone(self.coordinator.health()["last_success_at"])

    def test_account_change_events_are_coalesced(self):
        for number in (1, 2):
            self.coordinator.accept(
                {
                    "event_id": f"change-{number}",
                    "type": "account_changed",
                    "data": {"method": "PUT", "path": "/ut/game/fc27/item"},
                }
            )
        self.assertTrue(self.called.wait(1))
        self.assertEqual(self.calls, ["auto_account_change"])

    def test_explicit_sync_after_event_suppresses_redundant_auto_sync(self):
        self.coordinator.accept(
            {
                "event_id": "change-1",
                "type": "account_changed",
                "data": {"method": "PUT", "path": "/ut/game/fc27/item"},
            }
        )
        self.coordinator.note_full_sync()
        deadline = time.monotonic() + 1
        while self.coordinator.health()["pending"] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.coordinator.health()["pending"])

    def test_duplicate_event_id_is_idempotent(self):
        event = {
            "event_id": "same",
            "type": "account_changed",
            "data": {},
        }
        self.coordinator.accept(event)
        replay = self.coordinator.accept(event)
        self.assertTrue(replay["duplicate"])
        self.assertTrue(self.called.wait(1))
        self.assertEqual(len(self.calls), 1)

    def test_retryable_login_failure_retries_and_succeeds(self):
        attempts = []
        completed = threading.Event()

        class RetryableError(Exception):
            retryable = True
            code = "EA_PERSONA_NOT_FOUND"

        def synchronize(kind):
            attempts.append(kind)
            if len(attempts) == 1:
                raise RetryableError("Persona is not ready")
            coordinator.note_full_sync()
            completed.set()

        coordinator = AutoSyncCoordinator(
            synchronize,
            threading.Lock(),
            debounce_seconds=0.01,
            login_delay_seconds=0.01,
            retry_seconds=0.01,
            maximum_attempts=3,
        )
        try:
            coordinator.accept(
                {
                    "event_id": "retry-login",
                    "type": "session_authenticated",
                    "data": {
                        "authenticated": True,
                        "gameVersion": "fc27",
                        "apiHost": "example.ea.com",
                        "capturedAt": "2026-09-19T00:00:00Z",
                    },
                }
            )
            self.assertTrue(completed.wait(1))
            self.assertEqual(attempts, ["auto_login", "auto_login"])
            self.assertIsNone(coordinator.health()["last_error"])
        finally:
            coordinator.stop()


if __name__ == "__main__":
    unittest.main()
