import json
import tempfile
import unittest
from pathlib import Path

from fc27.errors import FC27Error
from fc27.policy import PolicyStore


BASE_POLICY = {
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


class PolicyStoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "policy.json"

    def tearDown(self):
        self.directory.cleanup()

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def test_loads_and_normalizes_policy(self):
        value = {**BASE_POLICY, "protected_item_ids": [2, 1, 2]}
        self.write(value)
        policy = PolicyStore(self.path).load()
        self.assertEqual(policy["protected_item_ids"], [1, 2])

    def test_rejects_unknown_or_missing_fields(self):
        value = dict(BASE_POLICY)
        value.pop("maximum_daily_spend")
        value["extra"] = 1
        self.write(value)
        with self.assertRaises(FC27Error) as context:
            PolicyStore(self.path).load()
        self.assertEqual(context.exception.code, "POLICY_INVALID")
        self.assertEqual(context.exception.details["unknown"], ["extra"])
        self.assertEqual(context.exception.details["missing"], ["maximum_daily_spend"])


if __name__ == "__main__":
    unittest.main()
