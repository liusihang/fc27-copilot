import json
import tempfile
import unittest
from pathlib import Path

from fc27.errors import FC27Error
from fc27.execution import ExecutionService
from fc27.policy import PolicyStore
from fc27.runtime import RuntimeManager


def policy(mode="auto", **overrides):
    value = {
        "execution_mode": mode,
        "minimum_coin_reserve": 1000,
        "maximum_single_purchase": 2000,
        "maximum_batch_spend": 3000,
        "maximum_daily_spend": 5000,
        "maximum_batch_actions": 3,
        "maximum_same_card_owned": 3,
        "maximum_tradepile_usage": 100,
        "protected_item_ids": [],
        "allowed_action_types": ["buy_now", "move_item"],
    }
    value.update(overrides)
    return value


def buy_action(action_id="a1", key="buy-1", price=1000, card_id=200):
    return {
        "action_id": action_id,
        "idempotency_key": key,
        "type": "buy_now",
        "trade_id": 900,
        "expected_card_ea_id": card_id,
        "max_price": price,
    }


class ExecutionServiceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        manager = RuntimeManager(root / "accounts")
        manager.activate({"persona_id": "123", "platform": "pc", "club_name": "Club"})
        self.runtime = manager.active
        self.policy_path = root / "policy.json"
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
            connection.execute(
                """INSERT INTO club_items(
                     item_id, card_ea_id, location, tradeable, protected,
                     first_seen_at, last_seen_at, last_sync_id
                   ) VALUES (10, 200, 'club', 1, 1,
                             '2026-09-18T10:00:00Z', '2026-09-18T10:00:00Z', 1)"""
            )
            connection.commit()

    def tearDown(self):
        self.directory.cleanup()

    def service(self, policy_value, dispatcher=None):
        self.policy_path.write_text(json.dumps(policy_value), encoding="utf-8")
        return ExecutionService(
            self.runtime, PolicyStore(self.policy_path), dispatcher=dispatcher
        )

    def request(self, actions=None, **overrides):
        value = {
            "batch_id": "batch-1",
            "expected_sync_id": 1,
            "stop_on_error": True,
            "confirmed": True,
            "actions": actions or [buy_action()],
        }
        value.update(overrides)
        return value

    def test_observe_mode_rejects_before_audit_or_dispatch(self):
        calls = []
        service = self.service(policy("observe"), calls.append)
        with self.assertRaises(FC27Error) as context:
            service.execute(self.request())
        self.assertEqual(context.exception.code, "EXECUTION_DISABLED")
        self.assertEqual(calls, [])
        with self.runtime.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM action_batches").fetchone()[0], 0)

    def test_suggest_mode_requires_confirmation(self):
        service = self.service(policy("suggest"), lambda action: {"ok": True})
        with self.assertRaises(FC27Error) as context:
            service.execute(self.request(confirmed=False))
        self.assertEqual(context.exception.code, "CONFIRMATION_REQUIRED")

    def test_rejects_stale_state_spend_ownership_and_protected_items(self):
        service = self.service(policy(), lambda action: {"ok": True})
        with self.assertRaises(FC27Error) as stale:
            service.execute(self.request(expected_sync_id=0))
        self.assertEqual(stale.exception.code, "STALE_CLUB_STATE")

        with self.assertRaises(FC27Error) as spend:
            service.execute(self.request(actions=[buy_action(price=2500)]))
        self.assertEqual(spend.exception.code, "POLICY_DENIED")

        ownership_policy = policy(maximum_same_card_owned=1)
        service = self.service(ownership_policy, lambda action: {"ok": True})
        with self.assertRaises(FC27Error) as ownership:
            service.execute(self.request())
        self.assertEqual(ownership.exception.code, "POLICY_DENIED")

        move = {
            "action_id": "move-1",
            "idempotency_key": "move-1",
            "type": "move_item",
            "item_id": 10,
            "destination": "tradepile",
        }
        service = self.service(policy(), lambda action: {"ok": True})
        with self.assertRaises(FC27Error) as protected:
            service.execute(self.request(actions=[move]))
        self.assertEqual(protected.exception.code, "POLICY_DENIED")

    def test_records_actions_and_replays_without_dispatching_twice(self):
        calls = []

        def dispatch(action):
            calls.append(action["action_id"])
            return {"trade_id": action["trade_id"], "status": "simulated"}

        service = self.service(policy(), dispatch)
        request = self.request()
        first = service.execute(request)
        self.assertEqual(first["batch"]["status"], "complete")
        self.assertEqual(first["actions"][0]["status"], "complete")
        self.assertFalse(first["replayed"])

        second = service.execute(request)
        self.assertTrue(second["replayed"])
        self.assertEqual(calls, ["a1"])

        changed = self.request(actions=[buy_action(price=900)])
        with self.assertRaises(FC27Error) as conflict:
            service.execute(changed)
        self.assertEqual(conflict.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_rejects_duplicate_idempotency_keys_before_audit(self):
        actions = [buy_action("a1", "same", 500, 201), buy_action("a2", "same", 500, 202)]
        service = self.service(policy(), lambda action: {"ok": True})
        with self.assertRaises(FC27Error) as context:
            service.execute(self.request(actions=actions))
        self.assertEqual(context.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_rejects_an_action_id_already_used_by_another_batch(self):
        service = self.service(policy(), lambda action: {"ok": True})
        service.execute(self.request())
        second = self.request(
            batch_id="batch-2",
            actions=[buy_action(action_id="a1", key="buy-2", card_id=201)],
        )
        with self.assertRaises(FC27Error) as context:
            service.execute(second)
        self.assertEqual(context.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_list_item_requires_an_ordered_positive_price_band(self):
        action = {
            "action_id": "list-1",
            "idempotency_key": "list-1",
            "type": "list_item",
            "item_id": 10,
            "starting_bid": 800,
            "buy_now_price": 700,
        }
        service = self.service(
            policy(allowed_action_types=["list_item"]), lambda value: {"ok": True}
        )
        with self.assertRaises(FC27Error) as context:
            service.execute(self.request(actions=[action]))
        self.assertEqual(context.exception.code, "INVALID_ACTION")

    def test_item_actions_require_a_current_tradeable_item(self):
        missing = {
            "action_id": "move-missing",
            "idempotency_key": "move-missing",
            "type": "move_item",
            "item_id": 999,
            "destination": "tradepile",
        }
        service = self.service(policy(), lambda value: {"ok": True})
        with self.assertRaises(FC27Error) as context:
            service.execute(self.request(actions=[missing]))
        self.assertEqual(context.exception.code, "ITEM_NOT_FOUND")

    def test_sbc_action_normalization_accepts_variable_unique_item_count(self):
        action = {
            "action_id": "save-1",
            "idempotency_key": "save-1",
            "type": "save_sbc_squad",
            "set_id": "1",
            "challenge_id": "1",
            "solution_id": "solution-1",
            "item_ids": [101, 102, 103],
        }
        normalized = ExecutionService._normalize_action(action)
        self.assertEqual(normalized["item_ids"], [101, 102, 103])

        action["item_ids"] = []
        with self.assertRaises(FC27Error) as empty:
            ExecutionService._normalize_action(action)
        self.assertEqual(empty.exception.code, "INVALID_ACTION")

    def test_squad_action_normalization_uses_web_app_ranges(self):
        action = {
            "action_id": "tactic-1",
            "idempotency_key": "tactic-1",
            "type": "save_squad_tactics",
            "squad_id": 1,
            "expected_squad_hash": "a" * 64,
            "tactic_id": 6,
            "defensive_style": 3,
            "defensive_line_height": 100,
            "build_up_play_style": 2,
            "instructions": [
                {"slot_index": 9, "position_id": 25, "role_id": 24, "variation_id": 5}
            ],
        }
        normalized = ExecutionService._normalize_action(action)
        self.assertEqual(normalized["tactic_id"], 6)
        self.assertEqual(normalized["defensive_line_height"], 100)

        action["defensive_line_height"] = 101
        with self.assertRaises(FC27Error) as invalid:
            ExecutionService._normalize_action(action)
        self.assertEqual(invalid.exception.code, "INVALID_ACTION")

    def test_tactics_active_false_is_an_explicit_change(self):
        action = {
            "action_id": "tactic-off",
            "idempotency_key": "tactic-off",
            "type": "save_squad_tactics",
            "squad_id": 1,
            "expected_squad_hash": "c" * 64,
            "tactic_id": 6,
            "active": False,
        }
        normalized = ExecutionService._normalize_action(action)
        self.assertIs(normalized["active"], False)

    def test_squad_hash_only_actions_do_not_require_club_sync_id(self):
        action = {
            "action_id": "active-1",
            "idempotency_key": "active-1",
            "type": "set_active_squad",
            "squad_id": 1,
            "expected_squad_hash": "d" * 64,
        }
        request = self.request(actions=[action])
        request.pop("expected_sync_id")
        service = self.service(
            policy(allowed_action_types=["set_active_squad"]),
            lambda value: {"ok": True},
        )
        result = service.execute(request)
        self.assertEqual(result["batch"]["status"], "complete")
        self.assertIsNone(result["batch"]["expected_sync_id"])

    def test_inventory_dependent_actions_still_require_club_sync_id(self):
        request = self.request()
        request.pop("expected_sync_id")
        service = self.service(policy(), lambda value: {"ok": True})
        with self.assertRaises(FC27Error) as context:
            service.execute(request)
        self.assertEqual(context.exception.code, "INVALID_ACTION_BATCH")

    def test_squad_items_obey_owned_and_protected_checks(self):
        action = {
            "action_id": "squad-1",
            "idempotency_key": "squad-1",
            "type": "save_squad",
            "squad_id": 1,
            "expected_squad_hash": "b" * 64,
            "slot_updates": [{"slot_index": 0, "item_id": 10}],
        }
        service = self.service(
            policy(allowed_action_types=["save_squad"]), lambda value: {"ok": True}
        )
        with self.assertRaises(FC27Error) as protected:
            service.execute(self.request(actions=[action]))
        self.assertEqual(protected.exception.code, "POLICY_DENIED")


if __name__ == "__main__":
    unittest.main()
