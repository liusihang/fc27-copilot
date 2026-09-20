import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fc27.daemon import FC27Daemon
from fc27.errors import FC27Error
from fc27.mcp import SUPPORTED_PROTOCOL_VERSIONS, TOOLS
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
            "getObjectives": {
                "status": 200,
                "campaign": {
                    "id": 88,
                    "title": "Season 1",
                    "current_xp": 1550,
                    "level_count": 30,
                },
                "season_levels": [
                    {
                        "level": 1,
                        "required_xp": 1000,
                        "remaining_xp": 0,
                        "standard": {"claimed": True, "claimable": False, "unlocked": True, "rewards": []},
                        "premium": {"claimed": True, "claimable": False, "unlocked": True, "rewards": []},
                    }
                ],
                "sections": [{"id": 1, "name": "Seasonal", "group_count": 1}],
                "categories": [
                    {
                        "id": 1,
                        "name": "Seasonal",
                        "groups": [
                            {
                                "id": 2,
                                "composite_id": "SEASONAL-2",
                                "title": "Starter",
                                "completed": False,
                                "tasks": [{"id": 3, "title": "Play one match"}],
                            }
                        ],
                    }
                ],
            },
            "getEvolutions": {
                "status": 200,
                "categories": [{"id": 0, "name": "Evolutions"}],
                "lifecycle": {"active": [10]},
                "evolutions": [
                    {
                        "id": 10,
                        "ea_id": 10,
                        "name": "Intro",
                        "display_group": "my_evolutions",
                        "availability": "account_started",
                        "active": True,
                        "started": True,
                        "completed": False,
                        "expired": False,
                        "levels": [],
                    }
                ],
            },
            "getSquads": {
                "status": 200,
                "active_squad_id": 7,
                "max_squads": 10,
                "list_full": False,
                "squads": [
                    {
                        "squad_id": 7,
                        "name": "Main",
                        "formation": {"id": 8, "name": "f433", "display_name": "4-3-3"},
                        "rating": 83,
                        "chemistry": 31,
                        "active_tactic_id": 1,
                        "slots": [{"slot_index": 0, "item": {"item_id": 100}}],
                        "tactics": [],
                    }
                ],
                "catalog": {
                    "formations": [
                        {
                            "id": 8,
                            "name": "f433",
                            "positions": [
                                {"slot_index": 0, "id": 0, "name": "GK", "general_position": 0},
                                {"slot_index": 1, "id": 7, "name": "LB", "general_position": 7},
                                {"slot_index": 2, "id": 4, "name": "RCB", "general_position": 5},
                                {"slot_index": 3, "id": 6, "name": "LCB", "general_position": 5},
                                {"slot_index": 4, "id": 3, "name": "RB", "general_position": 3},
                                {"slot_index": 5, "id": 13, "name": "RCM", "general_position": 14},
                                {"slot_index": 6, "id": 14, "name": "CM", "general_position": 14},
                                {"slot_index": 7, "id": 15, "name": "LCM", "general_position": 14},
                                {"slot_index": 8, "id": 27, "name": "LW", "general_position": 27},
                                {"slot_index": 9, "id": 25, "name": "ST", "general_position": 25},
                                {"slot_index": 10, "id": 23, "name": "RW", "general_position": 23}
                            ]
                        }
                    ]
                },
            },
            "getSbcSets": {
                "status": 200,
                "sets": [{"id": 4, "name": "Bronze Upgrade", "completed": False}],
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
        self.daemon.auto_sync.stop()
        self.directory.cleanup()

    def test_tools_list_contains_exact_catalog(self):
        response = self.daemon.mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(len(TOOLS), 12)
        self.assertEqual(names, [tool["name"] for tool in TOOLS])
        self.assertIn("sbc_refresh", names)
        self.assertNotIn("catalog_refresh", names)

    def test_content_query_schema_exposes_season_sections_and_states(self):
        tool = next(value for value in TOOLS if value["name"] == "content_query")
        branches = tool["inputSchema"]["oneOf"]
        by_type = {
            branch["properties"]["content_type"]["const"]: branch["properties"]
            for branch in branches
        }
        self.assertEqual(set(by_type), {"season", "objective", "evolution"})
        self.assertIn("fc_pro", by_type["objective"]["section"]["enum"])
        self.assertIn("my_evolutions", by_type["evolution"]["section"]["enum"])
        self.assertIn("claimable", by_type["season"]["state"]["enum"])
        self.assertNotIn("sbc", by_type)

    def test_squad_query_schema_and_live_result(self):
        tool = next(value for value in TOOLS if value["name"] == "squad_query")
        properties = tool["inputSchema"]["properties"]
        self.assertEqual(properties["selection"]["enum"], ["all", "active", "exact"])
        self.assertEqual(properties["detail"]["default"], "summary")
        self.assertFalse(properties["include_options"]["default"])
        self.daemon.bridge = IdentityBridge()
        result = self.daemon.call_tool(
            "squad_query", {"selection": "active", "detail": "detailed"}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["active_squad_id"], 7)
        self.assertEqual(result["data"]["squads"][0]["formation"]["id"], 8)
        self.assertEqual(len(result["data"]["squads"][0]["squad_hash"]), 64)
        self.assertNotIn("catalog", result["data"])

        with_options = self.daemon.call_tool(
            "squad_query",
            {
                "selection": "active",
                "detail": "detailed",
                "include_options": True,
            },
        )
        self.assertEqual(with_options["data"]["catalog"]["formations"][0]["id"], 8)

    def test_sbc_solve_schema_exposes_exact_required_item_ids(self):
        tool = next(value for value in TOOLS if value["name"] == "sbc_solve")
        purchase_budget = tool["inputSchema"]["properties"]["purchase_budget"]
        self.assertEqual(purchase_budget["minimum"], 0)
        self.assertEqual(purchase_budget["maximum"], 11)
        self.assertEqual(purchase_budget["default"], 0)
        self.assertIn("zero", purchase_budget["description"].lower())
        objective = tool["inputSchema"]["properties"]["objective"]
        required = objective["properties"]["required_item_ids"]
        self.assertEqual(required["items"]["type"], "integer")
        self.assertEqual(required["items"]["minimum"], 1)
        self.assertIn("description", required["items"])
        self.assertEqual(required["maxItems"], 11)
        self.assertTrue(required["uniqueItems"])
        self.assertNotIn("prefer_untradeable", objective["properties"])
        self.assertFalse(objective["additionalProperties"])
        self.assertIn("club_query", tool["description"])
        self.assertIn("rating vector", tool["description"])
        self.assertIn("set_id", tool["inputSchema"]["required"])
        actions = next(
            value for value in TOOLS if value["name"] == "execute_actions"
        )["inputSchema"]["properties"]["actions"]["items"]["oneOf"]
        save_sbc = next(
            value
            for value in actions
            if value["properties"]["type"]["const"] == "save_sbc_squad"
        )
        action_items = save_sbc["properties"]["item_ids"]
        self.assertEqual(action_items["minItems"], 1)
        self.assertEqual(action_items["maxItems"], 11)
        self.assertTrue(action_items["uniqueItems"])

    def test_action_schema_is_discriminated_and_closed(self):
        tool = next(value for value in TOOLS if value["name"] == "execute_actions")
        actions = tool["inputSchema"]["properties"]["actions"]["items"]["oneOf"]
        self.assertEqual(len(actions), 11)
        by_type = {
            value["properties"]["type"]["const"]: value for value in actions
        }
        self.assertEqual(
            set(by_type["buy_now"]["required"]),
            {
                "action_id",
                "idempotency_key",
                "type",
                "trade_id",
                "expected_card_ea_id",
                "max_price",
            },
        )
        self.assertTrue(all(value["additionalProperties"] is False for value in actions))
        self.assertNotIn("expected_sync_id", tool["inputSchema"]["required"])

    def test_tool_contracts_have_output_schemas_and_parameter_descriptions(self):
        def assert_described(schema):
            for value in schema.get("properties", {}).values():
                self.assertIn("description", value)
                assert_described(value)
            for branch in schema.get("oneOf", []):
                assert_described(branch)

        for tool in TOOLS:
            self.assertIn("outputSchema", tool)
            assert_described(tool["inputSchema"])

    def test_openclaw_accepts_top_level_tool_schema_shapes(self):
        for tool in TOOLS:
            self.assertEqual(
                tool["inputSchema"].get("type"),
                "object",
                f"{tool['name']} inputSchema must declare top-level object",
            )
            self.assertEqual(
                tool["outputSchema"].get("type"),
                "object",
                f"{tool['name']} outputSchema must declare top-level object",
            )

    def test_side_effect_annotations_match_local_persistence(self):
        by_name = {tool["name"]: tool for tool in TOOLS}
        self.assertFalse(by_name["sync_club"]["annotations"]["readOnlyHint"])
        self.assertFalse(by_name["market_search"]["annotations"]["readOnlyHint"])
        self.assertFalse(by_name["sbc_refresh"]["annotations"]["readOnlyHint"])
        self.assertFalse(by_name["sbc_solve"]["annotations"]["readOnlyHint"])
        self.assertTrue(by_name["sbc_query"]["annotations"]["readOnlyHint"])

    def test_initialize_negotiates_only_supported_protocol_versions(self):
        accepted = self.daemon.mcp.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        self.assertEqual(accepted["result"]["protocolVersion"], "2025-06-18")
        future = self.daemon.mcp.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {"protocolVersion": "2099-01-01"},
            }
        )
        self.assertEqual(
            future["result"]["protocolVersion"], SUPPORTED_PROTOCOL_VERSIONS[0]
        )
        self.assertIn("card_ea_id", future["result"]["instructions"])

    def test_sbc_refresh_persists_and_query_reads_cache_without_raw_by_default(self):
        self.daemon.bridge = IdentityBridge()
        self.daemon.call_tool("sync_club", {})
        refreshed = self.daemon.call_tool("sbc_refresh", {})
        self.assertTrue(refreshed["ok"])
        self.assertEqual(refreshed["data"]["sets"][0]["set_id"], "4")
        self.assertNotIn("raw", refreshed["data"]["sets"][0])

        cached = self.daemon.call_tool("sbc_query", {})
        self.assertTrue(cached["ok"])
        self.assertEqual(cached["meta"]["source"], "runtime")
        self.assertEqual(cached["data"]["sets"][0]["set_id"], "4")

    def test_sbc_solve_reads_active_squad_items_before_optimization(self):
        self.daemon.bridge = IdentityBridge()
        self.daemon.call_tool("sync_club", {})
        captured = {}

        class CaptureService:
            def solve(
                self,
                set_id,
                challenge_id,
                objective,
                max_solutions,
                *,
                purchase_budget,
                reserved_item_ids,
            ):
                captured["reserved_item_ids"] = reserved_item_ids
                captured["purchase_budget"] = purchase_budget
                return {"solution_count": 0, "solutions": []}

        self.daemon._sbc_service = lambda: CaptureService()
        response = self.daemon.call_tool(
            "sbc_solve",
            {"set_id": "4", "challenge_id": "16", "purchase_budget": 2},
        )
        self.assertTrue(response["ok"])
        self.assertEqual(captured["reserved_item_ids"], [100])
        self.assertEqual(captured["purchase_budget"], 2)

    def test_sbc_refresh_enriches_slots_from_live_formation_repository(self):
        self.daemon.bridge = IdentityBridge()
        payload = {
            "set": {"id": 16, "name": "Marquee Matchups", "raw": {"id": 16}},
            "challenge": {
                "id": 37,
                "set_id": 16,
                "name": "FC Porto v SL Benfica",
                "formation": "f433",
                "slot_indices": list(range(11)),
                "requirements": [],
                "raw": {"id": 37, "formation": "f433"},
            },
        }
        enriched = self.daemon._enrich_sbc_slot_positions(payload)
        self.assertEqual(
            enriched["challenge"]["slot_positions_source"],
            "ea_formation_repository",
        )
        self.assertEqual(
            enriched["challenge"]["slot_positions"][0]["name"], "GK"
        )

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

    def test_numeric_ea_error_code_is_normalized_for_mcp_output(self):
        class NumericErrorBridge:
            def call(self, method, params):
                return {
                    "ok": False,
                    "error": {
                        "code": 401,
                        "status": 401,
                        "message": "EA Web App service failed with status 401.",
                    },
                }

        self.daemon.accounts.activate(
            {
                "persona_id": "persona-123",
                "platform": "ps5",
                "club_name": "Fixture Club",
            }
        )
        self.daemon.bridge = NumericErrorBridge()
        response = self.daemon.mcp.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "sbc_refresh", "arguments": {}},
            }
        )
        result = response["result"]["structuredContent"]
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "401")
        self.assertIsInstance(result["error"]["code"], str)

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

    def test_content_query_reads_account_objectives(self):
        self.daemon.bridge = IdentityBridge()
        result = self.daemon.call_tool(
            "content_query",
            {
                "content_type": "objective",
                "source": "ea",
                "detail": "detailed",
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["source"], "ea")
        self.assertEqual(result["data"]["items"][0]["title"], "Starter")
        self.assertEqual(result["data"]["items"][0]["tasks"][0]["id"], 3)
        self.assertEqual(result["data"]["items"][0]["content_type"], "SEASONAL")
        self.assertEqual(result["data"]["source_meta"]["sections"][0]["name"], "Seasonal")

    def test_content_query_reads_compact_fc_season_levels(self):
        self.daemon.bridge = IdentityBridge()
        result = self.daemon.call_tool(
            "content_query",
            {
                "content_type": "season",
                "source": "ea",
                "state": "all",
                "detail": "detailed",
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["items"][0]["level"], 1)
        self.assertEqual(result["data"]["source_meta"]["campaign"]["current_xp"], 1550)

    def test_content_query_auto_merges_ea_and_futgg_evolutions(self):
        class FixtureContentClient:
            def evolutions(self, scope):
                self.scope = scope
                return {
                    "manifest_version": 1,
                    "manifest_key": "active-evolutions",
                    "manifest_hash": "hash",
                    "evolutions": [
                        {
                            "id": "futgg:100",
                            "futgg_id": 100,
                            "ea_id": 10,
                            "name": "Intro",
                            "expired": False,
                            "levels": [],
                        },
                        {
                            "id": "futgg:101",
                            "futgg_id": 101,
                            "ea_id": 999999,
                            "name": "Future EVO [SP 10]",
                            "display_group": "public",
                            "availability": "public",
                            "expired": False,
                            "levels": [],
                        },
                    ],
                }

        self.daemon.bridge = IdentityBridge()
        self.daemon.futgg_content = FixtureContentClient()
        result = self.daemon.call_tool(
            "content_query",
            {"content_type": "evolution", "source": "auto", "state": "current"},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["meta"]["source"], "ea_webapp+futgg")
        self.assertEqual(result["data"]["total_count"], 2)
        intro = next(item for item in result["data"]["items"] if item["ea_id"] == 10)
        self.assertEqual(intro["futgg_id"], 100)
        self.assertEqual(intro["source_evidence"]["match_method"], "ea_id")

    def test_content_query_uses_futgg_evolutions_without_account(self):
        class FixtureContentClient:
            def evolutions(self, scope):
                return {
                    "manifest_version": 1,
                    "manifest_key": "active-evolutions",
                    "manifest_hash": "hash",
                    "evolutions": [
                        {
                            "id": "futgg:10",
                            "futgg_id": 10,
                            "name": "Intro",
                            "display_group": "public",
                            "availability": "public",
                            "expired": False,
                            "levels": [],
                        }
                    ],
                }

        self.daemon.futgg_content = FixtureContentClient()
        result = self.daemon.call_tool(
            "content_query",
            {"content_type": "evolution", "source": "auto"},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["meta"]["source"], "futgg")
        self.assertEqual(result["data"]["items"][0]["name"], "Intro")

    def test_content_query_rejects_futgg_objectives(self):
        result = self.daemon.call_tool(
            "content_query",
            {"content_type": "objective", "source": "futgg"},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "CONTENT_SOURCE_UNAVAILABLE")

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
