import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fc27.daemon import FC27Daemon
from fc27.errors import FC27Error
from fc27.mcp import TOOLS
from fc27.schema import CATALOG_SCHEMA


class IdentityBridge:
    def health(self):
        return {"connected": True, "queued_requests": 0, "pending_requests": 0}

    def call(self, method, params):
        payloads = {
            "getIdentity": {
                "persona_id": "persona-123",
                "platform": "ps5",
                "club_id": 10,
                "club_name": "Fixture Club",
            },
            "getCoinBalance": {"credits": 5000},
            "getClubPage": {"itemData": [], "retrievedAll": True},
            "getStoragePage": {"items": [], "end_of_list": True},
            "getUnassigned": {"itemData": []},
            "getTradepile": {"auctionInfo": []},
            "getSessionStatus": {
                "webAppConnected": True,
                "authenticated": True,
                "sidCaptured": True,
                "phishingTokenCaptured": True,
                "apiBaseUrl": "https://example.ea.com/ut/game/fc27",
                "apiHost": "example.ea.com",
                "gameVersion": "fc27",
                "capturedAt": "2026-09-18T13:00:00Z",
            },
        }
        if method not in payloads:
            raise AssertionError(method)
        return {"ok": True, "data": payloads[method]}


class ReconciliationBridge:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        value = (
            self.response[method]
            if isinstance(self.response, dict) and method in self.response
            else self.response
        )
        return {"ok": True, "data": value}


class MCPTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        catalog_path = root / "catalog.sqlite"
        connection = sqlite3.connect(catalog_path)
        connection.executescript(CATALOG_SCHEMA)
        connection.execute("INSERT INTO nations VALUES (1, 'Nation')")
        connection.execute("INSERT INTO leagues VALUES (1, 'League')")
        connection.execute("INSERT INTO positions VALUES (25, 'ST')")
        connection.execute("INSERT INTO rarities VALUES (718, 'GOLD', 0, 'Rare')")
        connection.execute("INSERT INTO players(base_player_ea_id, common_name, nation_id) VALUES (100, 'Player', 1)")
        connection.execute("INSERT INTO cards(card_ea_id, futgg_id, base_player_ea_id, overall, quality, rarity_id, league_id) VALUES (200, 300, 100, 90, 'GOLD', 718, 1)")
        connection.execute("INSERT INTO card_positions VALUES (200, 25, 1)")
        connection.execute("INSERT INTO catalog_meta VALUES ('snapshot_finished_at', '2026-09-18T10:04:38Z')")
        connection.commit()
        connection.close()
        web_root = root / "web"
        web_root.mkdir()
        (web_root / "index.html").write_text("bridge", encoding="utf-8")
        policy_path = root / "policy.json"
        policy_path.write_text(
            json.dumps(
                {
                    "execution_mode": "observe",
                    "minimum_coin_reserve": 0,
                    "maximum_single_purchase": 0,
                    "maximum_batch_spend": 0,
                    "maximum_daily_spend": 0,
                    "maximum_batch_actions": 1,
                    "maximum_same_card_owned": 1,
                    "maximum_tradepile_usage": 0,
                    "protected_item_ids": [],
                    "allowed_action_types": [],
                }
            ),
            encoding="utf-8",
        )
        self.daemon = FC27Daemon(catalog_path, web_root, policy_path=policy_path)

    def tearDown(self):
        self.directory.cleanup()

    def test_tools_list_contains_exact_catalog(self):
        response = self.daemon.mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(len(TOOLS), 10)
        self.assertEqual(names, [tool["name"] for tool in TOOLS])

    def test_catalog_query_returns_uniform_envelope(self):
        response = self.daemon.mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "catalog_query", "arguments": {"card_ea_ids": [200]}}})
        result = response["result"]["structuredContent"]
        self.assertTrue(result["ok"])
        self.assertEqual(result["meta"]["source"], "catalog")
        self.assertEqual(result["data"]["cards"][0]["card_ea_id"], 200)

    def test_prelogin_and_observe_errors_are_actionable(self):
        sync = self.daemon.call_tool("sync_club", {})
        execute = self.daemon.call_tool("execute_actions", {})
        self.assertEqual(sync["error"]["code"], "EA_SESSION_REQUIRED")
        self.assertIn("recovery", sync["error"])
        self.assertEqual(execute["error"]["code"], "EXECUTION_DISABLED")

    def test_sync_selects_persona_before_inventory_stage(self):
        self.daemon.bridge = IdentityBridge()
        result = self.daemon.call_tool("sync_club", {})
        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["complete"])
        account = self.daemon.accounts.status()
        self.assertEqual(account["persona_id"], "persona-123")
        self.assertEqual(account["club_name"], "Fixture Club")
        self.assertEqual(account["coin_balance"], 5000)

    def test_status_reports_public_session_and_observe_policy(self):
        self.daemon.bridge = IdentityBridge()
        result = self.daemon.call_tool("status", {})
        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["ea_session"]["authenticated"])
        self.assertTrue(result["data"]["ea_session"]["sidCaptured"])
        self.assertNotIn("sid", result["data"]["ea_session"])
        self.assertEqual(result["data"]["policy"]["execution_mode"], "observe")
        self.assertFalse(result["data"]["policy"]["account_writes_enabled"])

    def test_rpc_does_not_expose_raw_browser_methods(self):
        bridge = ReconciliationBridge({"success": True})
        self.daemon.bridge = bridge
        with self.assertRaisesRegex(FC27Error, "Unknown daemon RPC method"):
            self.daemon.rpc(
                {
                    "method": "browser_call",
                    "params": {"method": "submitSbc", "params": {}},
                }
            )
        self.assertEqual(bridge.calls, [])

    def test_sbc_reconciliation_reads_evidence_from_browser(self):
        self.daemon.accounts.activate(
            {"persona_id": "persona-123", "platform": "ps5", "club_name": "Fixture Club"}
        )
        runtime = self.daemon.accounts.active
        runtime.sbc_save_reconciliation_target = lambda action_id: {
            "action_id": action_id,
            "set_id": "4",
            "challenge_id": "16",
            "solution_id": "solution-1",
            "expected_sync_id": 1,
        }
        observed = {}

        def reconcile(action_id, response):
            observed["action_id"] = action_id
            observed["response"] = response
            return {"reconciled": True}

        runtime.reconcile_sbc_save_action = reconcile
        trusted = {"source": "ea_webapp_fresh"}
        bridge = ReconciliationBridge(trusted)
        self.daemon.bridge = bridge
        result = self.daemon.rpc(
            {
                "method": "reconcile_sbc_save",
                "params": {
                    "action_id": "save-1",
                    "response": {"source": "forged"},
                },
            }
        )
        self.assertEqual(result, {"reconciled": True})
        self.assertEqual(
            bridge.calls,
            [("readSavedSbcSquad", {"set_id": "4", "challenge_id": "16"})],
        )
        self.assertEqual(observed, {"action_id": "save-1", "response": trusted})

    def test_sbc_verification_reads_evidence_from_browser(self):
        self.daemon.accounts.activate(
            {"persona_id": "persona-123", "platform": "ps5", "club_name": "Fixture Club"}
        )
        runtime = self.daemon.accounts.active
        runtime.sbc_save_verification_target = lambda action_id: {
            "action_id": action_id,
            "set_id": "4",
            "challenge_id": "16",
            "solution_id": "solution-1",
            "expected_sync_id": 1,
        }
        observed = {}

        def verify(action_id, response):
            observed["action_id"] = action_id
            observed["response"] = response
            return {"verified": True}

        runtime.verify_sbc_saved_action = verify
        trusted = {"source": "ea_webapp_fresh"}
        bridge = ReconciliationBridge(trusted)
        self.daemon.bridge = bridge
        result = self.daemon.rpc(
            {
                "method": "verify_sbc_save",
                "params": {
                    "action_id": "save-1",
                    "response": {"source": "forged"},
                },
            }
        )
        self.assertEqual(result, {"verified": True})
        self.assertEqual(
            bridge.calls,
            [("readSavedSbcSquad", {"set_id": "4", "challenge_id": "16"})],
        )
        self.assertEqual(observed, {"action_id": "save-1", "response": trusted})

    def test_sbc_submit_reconciliation_sources_sync_and_evidence_internally(self):
        self.daemon.accounts.activate(
            {"persona_id": "persona-123", "platform": "ps5", "club_name": "Fixture Club"}
        )
        runtime = self.daemon.accounts.active
        runtime.sbc_submit_reconciliation_target = lambda action_id: {
            "action_id": action_id,
            "resolved": False,
            "set_id": "4",
            "challenge_id": "16",
            "solution_id": "solution-1",
            "item_ids": list(range(1, 12)),
            "expected_sync_id": 1,
        }
        runtime.items_by_ids = lambda item_ids: []
        observed = {}

        def reconcile(action_id, sync, post_submit, saved_squad):
            observed.update(
                {
                    "action_id": action_id,
                    "sync": sync,
                    "post_submit": post_submit,
                    "saved_squad": saved_squad,
                }
            )
            return {"reconciled": True}

        runtime.reconcile_sbc_submit_action = reconcile
        sync = {"sync_id": 2, "complete": True}
        self.daemon._sync_full = lambda kind: sync
        captured = []

        class CaptureService:
            def capture_challenge(self, value):
                captured.append(value)
                return value

        self.daemon._sbc_service = lambda: CaptureService()
        post_submit = {
            "source": "ea_webapp_fresh",
            "freshness": {"sets_requested": True, "challenges_requested": True},
            "set": {"id": 4},
            "challenge": {"id": 16},
        }
        bridge = ReconciliationBridge(
            {"readSbcSubmissionState": post_submit}
        )
        self.daemon.bridge = bridge
        result = self.daemon.rpc(
            {
                "method": "reconcile_sbc_submit",
                "params": {
                    "action_id": "submit-1",
                    "response": {"source": "forged"},
                    "item_ids": [999],
                },
            }
        )
        self.assertEqual(result, {"reconciled": True})
        self.assertEqual(
            bridge.calls,
            [("readSbcSubmissionState", {"set_id": "4", "challenge_id": "16"})],
        )
        self.assertEqual(captured, [post_submit])
        self.assertEqual(
            observed,
            {
                "action_id": "submit-1",
                "sync": sync,
                "post_submit": post_submit,
                "saved_squad": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
