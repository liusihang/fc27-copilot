import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from .errors import FC27Error


@dataclass
class PendingRequest:
    event: threading.Event = field(default_factory=threading.Event)
    response: object = None


class BrowserBridge:
    def __init__(self, connected_window_seconds=35):
        self.connected_window_seconds = connected_window_seconds
        self._condition = threading.Condition()
        self._queue = deque()
        self._pending = {}
        self._browser_last_seen = 0.0

    def health(self):
        with self._condition:
            return {
                "connected": self._is_connected_locked(),
                "last_seen_at_epoch": self._browser_last_seen or None,
                "queued_requests": len(self._queue),
                "pending_requests": len(self._pending),
            }

    def call(self, method, params=None, timeout_seconds=30):
        request_id = str(uuid.uuid4())
        pending = PendingRequest()
        with self._condition:
            if not self._is_connected_locked():
                raise FC27Error(
                    "BRIDGE_NOT_CONNECTED",
                    "The local browser bridge is not connected.",
                    retryable=True,
                    recovery="Open the fc27d bridge page in the Chrome profile containing the FC27 extension.",
                )
            self._pending[request_id] = pending
            self._queue.append(
                {
                    "type": "fc27-request",
                    "request_id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )
            self._condition.notify_all()

        if not pending.event.wait(timeout_seconds):
            with self._condition:
                self._pending.pop(request_id, None)
                self._queue = deque(
                    envelope
                    for envelope in self._queue
                    if envelope["request_id"] != request_id
                )
            raise FC27Error(
                "BRIDGE_TIMEOUT",
                f"The browser bridge did not answer within {timeout_seconds} seconds.",
                retryable=True,
                recovery="Confirm that the bridge page and FC27 Web App tab remain open, then retry.",
            )

        return pending.response

    def poll(self, timeout_seconds=25):
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            self._browser_last_seen = time.time()
            while not self._queue:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
                self._browser_last_seen = time.time()
            return self._queue.popleft()

    def respond(self, request_id, payload):
        with self._condition:
            self._browser_last_seen = time.time()
            pending = self._pending.pop(str(request_id), None)
            if pending is None:
                return False
            pending.response = payload
            pending.event.set()
            return True

    def mark_connected(self):
        with self._condition:
            self._browser_last_seen = time.time()

    def _is_connected_locked(self):
        return time.time() - self._browser_last_seen < self.connected_window_seconds
