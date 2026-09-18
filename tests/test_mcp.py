import sqlite3
import tempfile
import unittest
from pathlib import Path

from fc27.daemon import FC27Daemon
from fc27.mcp import TOOLS
from fc27.schema import CATALOG_SCHEMA


class IdentityBridge:
    def health(self):
        return {"connected": True, "queued_requests": 0, "pending_requests": 0}

    def call(self, method, params):
        if method != "getIdentity":
            raise AssertionError(method)
        return {
            "ok": True,
            "data": {
                "persona_id": "persona-123",
                "platform": "ps5",
                "club_id": 10,
                "club_name": "Fixture Club",
            },
        }


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
        self.daemon = FC27Daemon(catalog_path, web_root)

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
        self.assertEqual(result["error"]["code"], "ACCOUNT_SYNC_NOT_READY")
        account = self.daemon.accounts.status()
        self.assertEqual(account["persona_id"], "persona-123")
        self.assertEqual(account["club_name"], "Fixture Club")


if __name__ == "__main__":
    unittest.main()
