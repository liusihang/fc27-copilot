import json
import tempfile
import unittest
from pathlib import Path

from fc27.actions import ActionDispatcher
from fc27.errors import FC27Error
from fc27.execution import ExecutionService
from fc27.policy import PolicyStore
from fc27.runtime import RuntimeManager
from fc27.sbc import SbcService


REQUIRED = ("coins", "club", "storage", "unassigned", "tradepile")


class FakeCatalog:
    def __init__(self, facts):
        self.facts = facts

    def sbc_item_facts(self, card_ea_ids):
        return {card_id: self.facts[card_id] for card_id in card_ea_ids}


class FakeBridge:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        value = self.response[method]
        if callable(value):
            value = value()
        if isinstance(value, Exception):
            raise value
        return {"ok": True, "data": value}


def browser_challenge(item_ids=None):
    value = {
        "set": {
            "id": 4,
            "name": "Bronze Upgrade",
            "repeatable": True,
            "rewards": [],
            "raw": {"id": 4},
        },
        "challenge": {
            "id": 16,
            "set_id": 4,
            "name": "Bronze Upgrade",
            "status": "NOT_STARTED",
            "repeatable": True,
            "completed": False,
            "formation": "f41212",
            "rewards": [],
            "requirements": [
                {
                    "kvPairs": {"_collection": {"3": [1]}},
                    "count": -1,
                    "scope": 2,
                }
            ],
            "raw": {"id": 16, "formation": "f41212"},
        },
    }
    if item_ids is not None:
        value["saved_item_ids"] = item_ids
        value["squad"] = {
            "eligible": True,
            "eligibility_evidence": {
                "source": "ea_challenge_requirements",
                "identity_match": True,
                "requirements": [{"index": 0, "met": True}],
                "all_requirements_met": True,
                "submit_available": True,
            },
        }
        value["source"] = "ea_webapp_fresh"
        value["freshness"] = {
            "sets_requested": True,
            "challenges_requested": True,
            "challenge_loaded": True,
        }
    return value


class SbcActionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        manager = RuntimeManager(root / "accounts")
        manager.activate({"persona_id": "123", "platform": "pc", "club_name": "Club"})
        self.runtime = manager.active
        facts = {}
        with self.runtime.connect() as connection:
            connection.execute(
                """INSERT INTO sync_runs(sync_id, kind, started_at, finished_at, status)
                   VALUES (1, 'fixture', '2026-09-18T10:00:00Z',
                           '2026-09-18T10:00:00Z', 'complete')"""
            )
            connection.execute(
                """UPDATE account_state SET coin_balance = 10000,
                     last_full_sync_id = 1, last_full_sync_at = '2026-09-18T10:00:00Z'
                   WHERE persona_id = '123'"""
            )
            for item_id in range(1, 12):
                card_id = 2000 + item_id
                connection.execute(
                    """INSERT INTO club_items(
                         item_id, card_ea_id, location, tradeable, protected,
                         loan_uses_remaining, acquisition_cost,
                         first_seen_at, last_seen_at, last_sync_id
                       ) VALUES (?, ?, 'club', 0, 0, -1, 200,
                                 '2026-09-18T10:00:00Z',
                                 '2026-09-18T10:00:00Z', 1)""",
                    (item_id, card_id),
                )
                facts[card_id] = {
                    "card_ea_id": card_id,
                    "overall": 62,
                    "quality": "bronze",
                    "club_id": item_id,
                    "league_id": item_id,
                    "nation_id": item_id,
                }
            connection.commit()
        self.catalog = FakeCatalog(facts)
        self.sbc = SbcService(self.runtime, self.catalog)
        payload = browser_challenge()
        self.sbc.capture_challenge(payload)
        self.solution = self.sbc.solve("16", {"max_tradeable_value": 0}, 1)["solutions"][0]
        self.policy_path = root / "policy.json"

    def tearDown(self):
        self.directory.cleanup()

    def policy(self, action_type):
        value = {
            "execution_mode": "suggest",
            "minimum_coin_reserve": 0,
            "maximum_single_purchase": 0,
            "maximum_batch_spend": 0,
            "maximum_daily_spend": 0,
            "maximum_batch_actions": 1,
            "maximum_same_card_owned": 4,
            "maximum_tradepile_usage": 100,
            "protected_item_ids": [],
            "allowed_action_types": [action_type],
        }
        self.policy_path.write_text(json.dumps(value), encoding="utf-8")
        return PolicyStore(self.policy_path)

    def action(self, action_type, action_id):
        return {
            "action_id": action_id,
            "idempotency_key": action_id,
            "type": action_type,
            "set_id": "4",
            "challenge_id": "16",
            "solution_id": self.solution["solution_id"],
            "item_ids": self.solution["item_ids"],
        }

    def execute(self, action_type, bridge, sync_full, batch_id):
        dispatcher = ActionDispatcher(
            bridge, self.runtime, sync_full, catalog=self.catalog
        )
        return ExecutionService(
            self.runtime, self.policy(action_type), dispatcher=dispatcher
        ).execute(
            {
                "batch_id": batch_id,
                "expected_sync_id": self.runtime.account_summary()["last_full_sync_id"],
                "confirmed": True,
                "actions": [self.action(action_type, f"{action_type}-1")],
            }
        )

    def insert_failed_save(
        self,
        action_id,
        batch_id,
        *,
        action_type="save_sbc_squad",
        action_status="failed",
        error_code="BRIDGE_TIMEOUT",
        batch_status="failed",
        expected_sync_id=1,
    ):
        action = self.action(action_type, action_id)
        with self.runtime.connect() as connection:
            connection.execute(
                """INSERT INTO action_batches(
                     batch_id, execution_mode, expected_sync_id, created_at, status
                   ) VALUES (?, 'suggest', ?, '2026-09-18T10:00:00Z', ?)""",
                (batch_id, expected_sync_id, batch_status),
            )
            connection.execute(
                """INSERT INTO actions(
                     action_id, batch_id, sequence_no, action_type, idempotency_key,
                     params_json, status, error_code
                   ) VALUES (?, ?, 0, ?, ?, ?, ?, ?)""",
                (
                    action_id,
                    batch_id,
                    action_type,
                    action["idempotency_key"],
                    json.dumps(action),
                    action_status,
                    error_code,
                ),
            )
            connection.commit()
        return action

    def test_save_then_submit_uses_exact_items_and_post_submit_sync(self):
        item_ids = self.solution["item_ids"]
        save_bridge = FakeBridge(
            {
                "saveSbcSquad": {
                    "set_id": 4,
                    "challenge_id": 16,
                    "requested_item_ids": item_ids,
                },
                "readSavedSbcSquad": browser_challenge(item_ids),
            }
        )
        saved = self.execute("save_sbc_squad", save_bridge, lambda kind: None, "save-batch")
        self.assertEqual(saved["batch"]["status"], "complete")
        self.assertEqual(self.runtime.get_sbc_solution(self.solution["solution_id"])["status"], "saved")

        def sync_full(kind):
            sync_id = self.runtime.begin_sync(kind)
            payload = {
                area: {
                    "area": area,
                    "page_count": 1,
                    "item_count": 0,
                    "complete": True,
                    "items": [],
                    "listings": [],
                    **({"coin_balance": 10000} if area == "coins" else {}),
                }
                for area in REQUIRED
            }
            for result in payload.values():
                self.runtime.record_sync_part(sync_id, result)
            return self.runtime.commit_full_sync(sync_id, payload, REQUIRED)

        submit_bridge = FakeBridge(
            {
                "submitSbc": {"submitted_item_ids": item_ids, "success": True},
                "getSbcChallenge": browser_challenge(),
            }
        )
        submitted = self.execute(
            "submit_sbc", submit_bridge, sync_full, "submit-batch"
        )
        self.assertEqual(submitted["batch"]["status"], "complete")
        self.assertEqual(
            self.runtime.get_sbc_solution(self.solution["solution_id"])["status"],
            "submitted",
        )
        self.assertEqual(self.runtime.items_by_ids(item_ids), [])

    def test_submit_requires_positive_ea_eligibility_readback(self):
        self.runtime.update_sbc_solution_status(
            self.solution["solution_id"],
            "saved",
            {"saved_at_sync_id": 1, "ea_eligible": False},
        )
        bridge = FakeBridge(
            {
                "submitSbc": {"success": True},
                "getSbcChallenge": browser_challenge(),
            }
        )
        dispatcher = ActionDispatcher(
            bridge, self.runtime, lambda kind: None, catalog=self.catalog
        )
        service = ExecutionService(
            self.runtime, self.policy("submit_sbc"), dispatcher=dispatcher
        )
        with self.assertRaisesRegex(FC27Error, "positive EA eligibility"):
            service.execute(
                {
                    "batch_id": "ineligible-submit",
                    "expected_sync_id": 1,
                    "confirmed": True,
                    "actions": [self.action("submit_sbc", "submit-ineligible")],
                }
            )
        self.assertEqual(bridge.calls, [])

    def test_submit_rejects_legacy_positive_evidence(self):
        self.runtime.update_sbc_solution_status(
            self.solution["solution_id"],
            "saved",
            {
                "saved_at_sync_id": 1,
                "ea_eligible": True,
                "source": "ea_webapp_cache",
            },
        )
        bridge = FakeBridge({})
        dispatcher = ActionDispatcher(
            bridge, self.runtime, lambda kind: None, catalog=self.catalog
        )
        service = ExecutionService(
            self.runtime, self.policy("submit_sbc"), dispatcher=dispatcher
        )
        with self.assertRaisesRegex(FC27Error, "fresh trusted EA save evidence"):
            service.execute(
                {
                    "batch_id": "legacy-submit",
                    "expected_sync_id": 1,
                    "confirmed": True,
                    "actions": [self.action("submit_sbc", "submit-legacy")],
                }
            )
        self.assertEqual(bridge.calls, [])

    def test_save_timeout_fails_without_automatic_readback(self):
        bridge = FakeBridge(
            {
                "saveSbcSquad": FC27Error("BRIDGE_TIMEOUT", "timed out"),
            }
        )
        saved = self.execute("save_sbc_squad", bridge, lambda kind: None, "save-timeout")
        self.assertEqual(saved["batch"]["status"], "failed")
        self.assertEqual(
            saved["actions"][0]["error_code"], "SBC_SAVE_OUTCOME_UNKNOWN"
        )
        self.assertEqual([method for method, _ in bridge.calls], ["saveSbcSquad"])
        solution = self.runtime.get_sbc_solution(self.solution["solution_id"])
        self.assertEqual(solution["status"], "validated")
        target = self.runtime.sbc_save_reconciliation_target("save_sbc_squad-1")
        self.assertEqual(target["solution_id"], self.solution["solution_id"])

    def _assert_save_timeout_blocks_retry(self, error_code):
        bridge = FakeBridge(
            {"saveSbcSquad": FC27Error(error_code, "timed out")}
        )
        first = self.execute(
            "save_sbc_squad", bridge, lambda kind: None, f"{error_code}-batch"
        )
        self.assertEqual(
            first["actions"][0]["error_code"],
            "SBC_SAVE_OUTCOME_UNKNOWN",
        )
        bridge.calls.clear()
        dispatcher = ActionDispatcher(
            bridge, self.runtime, lambda kind: None, catalog=self.catalog
        )
        service = ExecutionService(
            self.runtime,
            self.policy("save_sbc_squad"),
            dispatcher=dispatcher,
        )
        with self.assertRaisesRegex(
            FC27Error, "already has an active, completed, or ambiguous"
        ):
            service.execute(
                {
                    "batch_id": f"{error_code}-replacement",
                    "expected_sync_id": 1,
                    "confirmed": True,
                    "actions": [
                        self.action(
                            "save_sbc_squad", f"{error_code}-replacement-action"
                        )
                    ],
                }
            )
        self.assertEqual(bridge.calls, [])

    def test_page_bridge_save_timeout_blocks_a_second_save(self):
        self._assert_save_timeout_blocks_retry("PAGE_BRIDGE_TIMEOUT")

    def test_ea_service_save_timeout_blocks_a_second_save(self):
        self._assert_save_timeout_blocks_retry("EA_SERVICE_TIMEOUT")

    def test_new_save_rejects_prior_ambiguous_action_before_ea_contact(self):
        self.insert_failed_save("ambiguous-save", "ambiguous-save-batch")
        bridge = FakeBridge({})
        dispatcher = ActionDispatcher(
            bridge, self.runtime, lambda kind: None, catalog=self.catalog
        )
        service = ExecutionService(
            self.runtime, self.policy("save_sbc_squad"), dispatcher=dispatcher
        )
        with self.assertRaisesRegex(FC27Error, "already has an active, completed, or ambiguous"):
            service.execute(
                {
                    "batch_id": "replacement-save-batch",
                    "expected_sync_id": 1,
                    "confirmed": True,
                    "actions": [self.action("save_sbc_squad", "replacement-save")],
                }
            )
        self.assertEqual(bridge.calls, [])

    def test_new_save_rejects_saved_solution_before_ea_contact(self):
        item_ids = self.solution["item_ids"]
        bridge = FakeBridge(
            {
                "saveSbcSquad": {"status": 200},
                "readSavedSbcSquad": browser_challenge(item_ids),
            }
        )
        saved = self.execute("save_sbc_squad", bridge, lambda kind: None, "first-save")
        self.assertEqual(saved["batch"]["status"], "complete")
        bridge.calls.clear()
        dispatcher = ActionDispatcher(
            bridge, self.runtime, lambda kind: None, catalog=self.catalog
        )
        service = ExecutionService(
            self.runtime, self.policy("save_sbc_squad"), dispatcher=dispatcher
        )
        with self.assertRaisesRegex(FC27Error, "cannot start a new save"):
            service.execute(
                {
                    "batch_id": "second-save",
                    "expected_sync_id": 1,
                    "confirmed": True,
                    "actions": [self.action("save_sbc_squad", "second-save-action")],
                }
            )
        self.assertEqual(bridge.calls, [])

    def test_submit_requires_completed_save_action_and_batch(self):
        item_ids = self.solution["item_ids"]
        save_bridge = FakeBridge(
            {
                "saveSbcSquad": {"status": 200},
                "readSavedSbcSquad": browser_challenge(item_ids),
            }
        )
        saved = self.execute(
            "save_sbc_squad", save_bridge, lambda kind: None, "audit-save-batch"
        )
        action_id = saved["actions"][0]["action_id"]
        with self.runtime.connect() as connection:
            connection.execute(
                "UPDATE actions SET status = 'running' WHERE action_id = ?",
                (action_id,),
            )
            connection.execute(
                "UPDATE action_batches SET status = 'running' WHERE batch_id = 'audit-save-batch'"
            )
            connection.commit()
        bridge = FakeBridge({})
        dispatcher = ActionDispatcher(
            bridge, self.runtime, lambda kind: None, catalog=self.catalog
        )
        service = ExecutionService(
            self.runtime, self.policy("submit_sbc"), dispatcher=dispatcher
        )
        with self.assertRaisesRegex(FC27Error, "matching completed save action"):
            service.execute(
                {
                    "batch_id": "submit-with-incomplete-audit",
                    "expected_sync_id": 1,
                    "confirmed": True,
                    "actions": [self.action("submit_sbc", "submit-with-incomplete-audit")],
                }
            )
        self.assertEqual(bridge.calls, [])

    def test_submit_rechecks_batch_sync_before_ea_contact(self):
        item_ids = self.solution["item_ids"]
        save_bridge = FakeBridge(
            {
                "saveSbcSquad": {"status": 200},
                "readSavedSbcSquad": browser_challenge(item_ids),
            }
        )
        saved = self.execute(
            "save_sbc_squad", save_bridge, lambda kind: None, "sync-race-save"
        )
        self.assertEqual(saved["batch"]["status"], "complete")
        submit_bridge = FakeBridge({})
        real_dispatcher = ActionDispatcher(
            submit_bridge, self.runtime, lambda kind: None, catalog=self.catalog
        )

        def advance_sync_then_dispatch(action):
            with self.runtime.connect() as connection:
                connection.execute(
                    "UPDATE account_state SET last_full_sync_id = 2 WHERE persona_id = '123'"
                )
                connection.commit()
            return real_dispatcher(action)

        result = ExecutionService(
            self.runtime,
            self.policy("submit_sbc"),
            dispatcher=advance_sync_then_dispatch,
        ).execute(
            {
                "batch_id": "sync-race-submit",
                "expected_sync_id": 1,
                "confirmed": True,
                "actions": [self.action("submit_sbc", "sync-race-submit-action")],
            }
        )
        self.assertEqual(result["batch"]["status"], "failed")
        self.assertEqual(result["actions"][0]["error_code"], "STALE_CLUB_STATE")
        self.assertEqual(submit_bridge.calls, [])

    def test_save_acknowledged_readback_failure_is_reconcilable_without_resave(self):
        item_ids = self.solution["item_ids"]
        bridge = FakeBridge(
            {
                "saveSbcSquad": {
                    "set_id": 4,
                    "challenge_id": 16,
                    "requested_item_ids": item_ids,
                },
                "readSavedSbcSquad": FC27Error(
                    "EA_SERVICE_TIMEOUT", "fresh read timed out"
                ),
            }
        )
        saved = self.execute(
            "save_sbc_squad", bridge, lambda kind: None, "save-readback-pending"
        )
        self.assertEqual(saved["batch"]["status"], "failed")
        self.assertEqual(
            saved["actions"][0]["error_code"], "SBC_SAVE_READBACK_PENDING"
        )
        self.assertEqual(
            [method for method, _ in bridge.calls],
            ["saveSbcSquad", "readSavedSbcSquad"],
        )
        target = self.runtime.sbc_save_reconciliation_target("save_sbc_squad-1")
        self.assertEqual(target["expected_sync_id"], 1)
        self.assertEqual(
            self.runtime.get_sbc_solution(self.solution["solution_id"])["status"],
            "validated",
        )

    def test_save_finalization_rejects_sync_that_advanced_during_readback(self):
        item_ids = self.solution["item_ids"]

        def advance_sync():
            with self.runtime.connect() as connection:
                connection.execute(
                    "UPDATE account_state SET last_full_sync_id = 2 WHERE persona_id = '123'"
                )
                connection.commit()
            return browser_challenge(item_ids)

        bridge = FakeBridge(
            {
                "saveSbcSquad": {
                    "set_id": 4,
                    "challenge_id": 16,
                    "requested_item_ids": item_ids,
                },
                "readSavedSbcSquad": advance_sync,
            }
        )
        saved = self.execute(
            "save_sbc_squad", bridge, lambda kind: None, "save-sync-race"
        )
        self.assertEqual(saved["batch"]["status"], "failed")
        self.assertEqual(saved["actions"][0]["error_code"], "STALE_CLUB_STATE")
        self.assertEqual(
            self.runtime.get_sbc_solution(self.solution["solution_id"])["status"],
            "validated",
        )

    def test_reconcile_failed_sbc_save_requires_exact_trusted_readback(self):
        self.insert_failed_save("save-failed", "save-failed-batch")
        self.insert_failed_save("save-mismatched", "save-mismatched-batch")
        self.insert_failed_save("save-untrusted", "save-untrusted-batch")

        response = browser_challenge(self.solution["item_ids"])
        untrusted = {**response, "source": "ea_webapp_cache"}
        with self.assertRaisesRegex(FC27Error, "fresh trusted EA readback"):
            self.runtime.reconcile_sbc_save_action("save-untrusted", untrusted)

        mismatched = {**response, "saved_item_ids": list(reversed(self.solution["item_ids"]))}
        with self.assertRaisesRegex(FC27Error, "exact confirmed item order"):
            self.runtime.reconcile_sbc_save_action("save-mismatched", mismatched)

        result = self.runtime.reconcile_sbc_save_action("save-failed", response)
        self.assertTrue(result["reconciled"])
        with self.runtime.connect() as connection:
            action_row = connection.execute(
                "SELECT status, error_code FROM actions WHERE action_id = 'save-failed'"
            ).fetchone()
            batch_row = connection.execute(
                "SELECT status FROM action_batches WHERE batch_id = 'save-failed-batch'"
            ).fetchone()
        self.assertEqual(action_row["status"], "complete")
        self.assertIsNone(action_row["error_code"])
        self.assertEqual(batch_row["status"], "complete")
        solution = self.runtime.get_sbc_solution(self.solution["solution_id"])
        self.assertEqual(solution["status"], "saved")

    def test_reconcile_rejects_stale_sync_and_solution_state_regression(self):
        self.insert_failed_save("save-stale", "save-stale-batch")
        with self.runtime.connect() as connection:
            connection.execute(
                "UPDATE account_state SET last_full_sync_id = 2 WHERE persona_id = '123'"
            )
            connection.commit()
        with self.assertRaisesRegex(FC27Error, "Expected sync|expected sync"):
            self.runtime.sbc_save_reconciliation_target("save-stale")

        with self.runtime.connect() as connection:
            connection.execute(
                "UPDATE account_state SET last_full_sync_id = 1 WHERE persona_id = '123'"
            )
            connection.execute(
                "UPDATE sbc_solutions SET status = 'submitted' WHERE solution_id = ?",
                (self.solution["solution_id"],),
            )
            connection.commit()
        with self.assertRaisesRegex(FC27Error, "cannot transition to saved"):
            self.runtime.sbc_save_reconciliation_target("save-stale")

    def test_reconcile_rejects_invalid_action_and_batch_states(self):
        self.insert_failed_save(
            "save-wrong-type",
            "save-wrong-type-batch",
            action_type="move_item",
        )
        with self.assertRaisesRegex(FC27Error, "not an SBC save action"):
            self.runtime.sbc_save_reconciliation_target("save-wrong-type")

        self.insert_failed_save(
            "save-running-batch",
            "save-running-batch-id",
            batch_status="running",
        )
        with self.assertRaisesRegex(FC27Error, "batch is not"):
            self.runtime.sbc_save_reconciliation_target("save-running-batch")

        self.insert_failed_save(
            "save-wrong-error",
            "save-wrong-error-batch",
            error_code="SBC_SAVE_READBACK_FAILED",
        )
        with self.assertRaisesRegex(FC27Error, "ambiguous or pending"):
            self.runtime.sbc_save_reconciliation_target("save-wrong-error")

    def test_reconcile_rejects_solution_challenge_mismatch(self):
        self.insert_failed_save("save-wrong-challenge", "save-wrong-challenge-batch")
        with self.runtime.connect() as connection:
            connection.execute(
                """INSERT INTO sbc_challenges(
                     challenge_id, set_id, name, status, repeatable,
                     requirements_json, observed_at, raw_json
                   ) SELECT '17', set_id, name, status, repeatable,
                            requirements_json, observed_at, raw_json
                     FROM sbc_challenges WHERE challenge_id = '16'"""
            )
            connection.execute(
                "UPDATE sbc_solutions SET challenge_id = '17' WHERE solution_id = ?",
                (self.solution["solution_id"],),
            )
            connection.commit()
        with self.assertRaisesRegex(FC27Error, "does not match"):
            self.runtime.sbc_save_reconciliation_target("save-wrong-challenge")

    def test_fresh_verification_replaces_legacy_canonical_evidence(self):
        item_ids = self.solution["item_ids"]
        save_bridge = FakeBridge(
            {
                "saveSbcSquad": {
                    "set_id": 4,
                    "challenge_id": 16,
                    "requested_item_ids": item_ids,
                },
                "readSavedSbcSquad": browser_challenge(item_ids),
            }
        )
        saved = self.execute(
            "save_sbc_squad", save_bridge, lambda kind: None, "save-to-verify"
        )
        action_id = saved["actions"][0]["action_id"]
        with self.runtime.connect() as connection:
            solution = connection.execute(
                "SELECT validation_json FROM sbc_solutions WHERE solution_id = ?",
                (self.solution["solution_id"],),
            ).fetchone()
            validation = json.loads(solution["validation_json"])
            validation["execution"] = {
                "saved_at_sync_id": 1,
                "ea_eligible": True,
                "source": "ea_webapp_cache",
            }
            connection.execute(
                "UPDATE sbc_solutions SET validation_json = ? WHERE solution_id = ?",
                (json.dumps(validation), self.solution["solution_id"]),
            )
            connection.execute(
                "UPDATE actions SET result_json = ? WHERE action_id = ?",
                (json.dumps({"source": "ea_webapp_cache"}), action_id),
            )
            connection.commit()

        target = self.runtime.sbc_save_verification_target(action_id)
        self.assertEqual(target["expected_sync_id"], 1)
        result = self.runtime.verify_sbc_saved_action(
            action_id, browser_challenge(item_ids)
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["source"], "ea_webapp_fresh")
        solution = self.runtime.get_sbc_solution(self.solution["solution_id"])
        self.assertEqual(
            solution["validation"]["execution"]["source"], "ea_webapp_fresh"
        )


if __name__ == "__main__":
    unittest.main()
