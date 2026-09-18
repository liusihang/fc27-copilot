import threading
import unittest

from fc27.bridge import BrowserBridge
from fc27.errors import FC27Error


class BrowserBridgeTest(unittest.TestCase):
    def test_round_trip_correlates_request_and_response(self):
        bridge = BrowserBridge()
        bridge.mark_connected()
        result = {}

        def caller():
            result["value"] = bridge.call("getSessionStatus", {"value": 1}, 2)

        thread = threading.Thread(target=caller)
        thread.start()
        envelope = bridge.poll(1)
        self.assertEqual(envelope["method"], "getSessionStatus")
        self.assertEqual(envelope["params"], {"value": 1})
        self.assertTrue(bridge.respond(envelope["request_id"], {"authenticated": False}))
        self.assertFalse(bridge.respond(envelope["request_id"], {"authenticated": True}))
        thread.join(2)
        self.assertEqual(result["value"], {"authenticated": False})

    def test_call_rejects_disconnected_bridge(self):
        bridge = BrowserBridge()
        with self.assertRaises(FC27Error) as context:
            bridge.call("getSessionStatus", timeout_seconds=1)
        self.assertEqual(context.exception.code, "BRIDGE_NOT_CONNECTED")

    def test_timeout_removes_pending_request(self):
        bridge = BrowserBridge()
        bridge.mark_connected()
        with self.assertRaises(FC27Error) as context:
            bridge.call("getSessionStatus", timeout_seconds=0.01)
        self.assertEqual(context.exception.code, "BRIDGE_TIMEOUT")
        self.assertEqual(bridge.health()["pending_requests"], 0)
        self.assertIsNone(bridge.poll(0.01))


if __name__ == "__main__":
    unittest.main()
