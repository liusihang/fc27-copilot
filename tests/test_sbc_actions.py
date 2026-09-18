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
        return {"ok": True, "data": self.response[method]}


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
        value["squad"] = {"eligible": True}
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

    def test_save_then_submit_uses_exact_items_and_post_submit_sync(self):
        item_ids = self.solution["item_ids"]
        save_bridge = FakeBridge({"saveSbcSquad": browser_challenge(item_ids)})
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


if __name__ == "__main__":
    unittest.main()
