import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from fc27.daemon import FC27Daemon, FC27HTTPServer
from fc27.schema import CATALOG_SCHEMA


class DaemonHTTPTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        catalog_path = root / "catalog.sqlite"
        connection = sqlite3.connect(catalog_path)
        connection.executescript(CATALOG_SCHEMA)
        connection.execute("INSERT INTO nations VALUES (1, 'France')")
        connection.execute("INSERT INTO leagues VALUES (10, 'League')")
        connection.execute("INSERT INTO clubs VALUES (20, 'Club')")
        connection.execute("INSERT INTO positions VALUES (25, 'ST')")
        connection.execute("INSERT INTO rarities VALUES (718, 'GOLD', 0, 'Rare')")
        connection.execute(
            "INSERT INTO players(base_player_ea_id, common_name, nation_id) VALUES (100, 'Player', 1)"
        )
        connection.execute(
            """INSERT INTO cards(
                 card_ea_id, futgg_id, base_player_ea_id, overall, quality,
                 rarity_id, club_id, league_id
               ) VALUES (200, 300, 100, 90, 'GOLD', 718, 20, 10)"""
        )
        connection.execute("INSERT INTO card_positions VALUES (200, 25, 1)")
        connection.execute("INSERT INTO catalog_meta VALUES ('schema_version', '3')")
        connection.commit()
        connection.close()
        web_root = root / "web"
        web_root.mkdir()
        (web_root / "index.html").write_text("bridge", encoding="utf-8")
        daemon = FC27Daemon(catalog_path, web_root)
        self.server = FC27HTTPServer(("127.0.0.1", 0), daemon)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.fc27.auto_sync.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.directory.cleanup()

    def test_health_and_catalog_rpc(self):
        with urlopen(f"{self.base_url}/health", timeout=2) as response:
            health = json.load(response)
        self.assertTrue(health["ok"])
        request = Request(
            f"{self.base_url}/rpc",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"method": "catalog_query", "params": {"card_ea_ids": [200]}}).encode(),
        )
        with urlopen(request, timeout=2) as response:
            result = json.load(response)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["cards"][0]["card_ea_id"], 200)

    def test_browser_event_endpoint_accepts_public_session_state(self):
        request = Request(
            f"{self.base_url}/browser/event",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(
                {
                    "event_id": "session-1",
                    "type": "session_authenticated",
                    "observed_at": "2026-09-19T00:00:00Z",
                    "data": {"authenticated": False},
                }
            ).encode(),
        )
        with urlopen(request, timeout=2) as response:
            result = json.load(response)
            self.assertEqual(response.status, 202)
        self.assertTrue(result["ok"])
        self.assertFalse(result["data"]["scheduled"])


if __name__ == "__main__":
    unittest.main()
