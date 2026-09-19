import threading
import time
from collections import deque
from datetime import datetime, timezone

from .errors import FC27Error


EVENT_TYPES = ("session_authenticated", "account_changed")


def utc_now():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class AutoSyncCoordinator:
    def __init__(
        self,
        sync_callback,
        execution_lock,
        debounce_seconds=1.5,
        login_delay_seconds=4.0,
        retry_seconds=3.0,
        maximum_attempts=3,
    ):
        self.sync_callback = sync_callback
        self.execution_lock = execution_lock
        self.debounce_seconds = float(debounce_seconds)
        self.login_delay_seconds = float(login_delay_seconds)
        self.retry_seconds = float(retry_seconds)
        self.maximum_attempts = int(maximum_attempts)
        self._condition = threading.Condition()
        self._thread = None
        self._stopped = False
        self._due_at = None
        self._pending_received_at = None
        self._pending_reasons = set()
        self._seen_event_ids = set()
        self._seen_event_order = deque()
        self._session_fingerprint = None
        self._attempt = 0
        self._last_full_sync_monotonic = 0.0
        self._state = {
            "pending": False,
            "running": False,
            "reasons": [],
            "last_event_at": None,
            "last_attempt_at": None,
            "last_success_at": None,
            "last_error": None,
            "last_event": None,
            "attempt": 0,
        }

    def accept(self, event):
        event_id = str(event.get("event_id") or "").strip()
        event_type = str(event.get("type") or "").strip()
        data = event.get("data") or {}
        if not event_id:
            raise FC27Error("INVALID_BROWSER_EVENT", "Browser event requires event_id.")
        if event_type not in EVENT_TYPES:
            raise FC27Error(
                "INVALID_BROWSER_EVENT",
                f"Unsupported browser event type: {event_type or '<empty>'}.",
                recovery=f"Use one of: {', '.join(EVENT_TYPES)}.",
            )
        if not isinstance(data, dict):
            raise FC27Error("INVALID_BROWSER_EVENT", "Browser event data must be an object.")

        with self._condition:
            if event_id in self._seen_event_ids:
                return {"accepted": True, "scheduled": False, "duplicate": True}
            self._remember_event(event_id)

            if event_type == "session_authenticated":
                if data.get("authenticated") is not True:
                    return {"accepted": True, "scheduled": False, "authenticated": False}
                fingerprint = (
                    data.get("gameVersion"),
                    data.get("apiHost"),
                    data.get("capturedAt"),
                )
                if fingerprint == self._session_fingerprint and self._state["last_success_at"]:
                    return {"accepted": True, "scheduled": False, "duplicate_session": True}
                self._session_fingerprint = fingerprint

            received_at = time.monotonic()
            self._attempt = 0
            self._pending_received_at = received_at
            self._pending_reasons.add(event_type)
            delay = self.login_delay_seconds if event_type == "session_authenticated" else self.debounce_seconds
            self._due_at = received_at + delay
            self._state.update(
                {
                    "pending": True,
                    "reasons": sorted(self._pending_reasons),
                    "last_event_at": event.get("observed_at") or utc_now(),
                    "last_event": {
                        "type": event_type,
                        "data": data,
                    },
                    "attempt": 0,
                }
            )
            self._ensure_thread_locked()
            self._condition.notify_all()
            return {
                "accepted": True,
                "scheduled": True,
                "reasons": self._state["reasons"],
            }

    def note_full_sync(self):
        with self._condition:
            self._last_full_sync_monotonic = time.monotonic()
            self._condition.notify_all()

    def health(self):
        with self._condition:
            return dict(self._state)

    def stop(self):
        with self._condition:
            self._stopped = True
            self._condition.notify_all()
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)

    def _remember_event(self, event_id):
        self._seen_event_ids.add(event_id)
        self._seen_event_order.append(event_id)
        while len(self._seen_event_order) > 256:
            expired = self._seen_event_order.popleft()
            self._seen_event_ids.discard(expired)

    def _ensure_thread_locked(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="fc27-auto-sync",
            daemon=True,
        )
        self._thread.start()

    def _run(self):
        while True:
            with self._condition:
                while not self._stopped and self._due_at is None:
                    self._condition.wait()
                if self._stopped:
                    return
                remaining = self._due_at - time.monotonic()
                if remaining > 0:
                    self._condition.wait(remaining)
                    continue
                event_received_at = self._pending_received_at
                reasons = sorted(self._pending_reasons)
                self._due_at = None
                self._pending_received_at = None
                self._pending_reasons.clear()
                self._state.update({"pending": False, "reasons": reasons})

            with self.execution_lock:
                with self._condition:
                    if self._last_full_sync_monotonic >= event_received_at:
                        self._attempt = 0
                        self._state.update({"running": False, "reasons": [], "attempt": 0})
                        continue
                    self._attempt += 1
                    self._state.update(
                        {
                            "running": True,
                            "last_attempt_at": utc_now(),
                            "last_error": None,
                            "attempt": self._attempt,
                        }
                    )
                try:
                    kind = "auto_account_change" if "account_changed" in reasons else "auto_login"
                    self.sync_callback(kind)
                except Exception as error:
                    with self._condition:
                        error_value = {
                            "at": utc_now(),
                            "code": getattr(error, "code", type(error).__name__),
                            "message": str(error),
                        }
                        retryable = bool(getattr(error, "retryable", False))
                        if retryable and self._attempt < self.maximum_attempts:
                            retry_at = time.monotonic()
                            self._pending_received_at = retry_at
                            self._pending_reasons.update(reasons)
                            self._due_at = retry_at + self.retry_seconds
                            self._state.update(
                                {
                                    "pending": True,
                                    "running": False,
                                    "reasons": sorted(self._pending_reasons),
                                    "last_error": error_value,
                                }
                            )
                            self._condition.notify_all()
                        else:
                            if "session_authenticated" in reasons:
                                self._session_fingerprint = None
                            self._state.update(
                                {
                                    "running": False,
                                    "reasons": [],
                                    "last_error": error_value,
                                }
                            )
                else:
                    with self._condition:
                        self._attempt = 0
                        self._state.update(
                            {
                                "running": False,
                                "reasons": [],
                                "last_success_at": utc_now(),
                                "last_error": None,
                                "attempt": 0,
                            }
                        )
