import tempfile
import unittest
from pathlib import Path

from fc27.errors import FC27Error
from fc27.runtime import RuntimeManager
from fc27.sbc import SbcService


class FakeCatalog:
    def __init__(self, facts):
        self.facts = facts

    def sbc_item_facts(self, card_ea_ids):
        return {card_id: self.facts[card_id] for card_id in card_ea_ids if card_id in self.facts}


def bronze_requirement():
    return {
        "kvPairs": {"_collection": {"3": [1]}},
        "count": -1,
        "scope": 2,
    }


def challenge_payload(requirements=None):
    return {
        "set": {
            "id": 4,
            "name": "Bronze Upgrade",
            "status": None,
            "expires": 2071242001,
            "repeatable": True,
            "rewards": [{"type": "pack", "value": 509}],
            "raw": {"id": 4, "repeatabilityMode": "UNLIMITED"},
        },
        "challenges": [
            {
                "id": 16,
                "set_id": 4,
                "name": "Bronze Upgrade",
                "status": "NOT_STARTED",
                "repeatable": True,
                "completed": False,
                "formation": "f41212",
                "rewards": [],
                "requirements": requirements or [bronze_requirement()],
                "raw": {"id": 16, "formation": "f41212"},
            }
        ],
    }


class SbcServiceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        manager = RuntimeManager(Path(self.directory.name) / "accounts")
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
            for item_id in range(1, 14):
                card_id = 1000 + item_id
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
                    "overall": 60 + item_id % 4,
                    "quality": "bronze",
                    "club_id": item_id % 3,
                    "league_id": item_id % 4,
                    "nation_id": item_id % 5,
                }
            connection.commit()
        self.service = SbcService(self.runtime, FakeCatalog(facts))

    def tearDown(self):
        self.directory.cleanup()

    def test_capture_round_trip_preserves_normalized_and_raw_evidence(self):
        captured = self.service.capture_challenges(challenge_payload())
        self.assertEqual(captured["set"]["rewards"][0]["value"], 509)
        challenge = self.runtime.get_sbc_challenge("16")
        self.assertEqual(challenge["formation"], "f41212")
        self.assertEqual(len(challenge["slots"]), 11)
        self.assertEqual(challenge["constraints"][0]["type"], "squad_quality")
        self.assertEqual(challenge["constraints"][0]["quality"], "bronze")
        self.assertEqual(challenge["raw"]["id"], 16)

    def test_solver_persists_multiple_exact_validated_solutions(self):
        self.service.capture_challenges(challenge_payload())
        result = self.service.solve(
            "16",
            {"prefer_untradeable": True, "max_tradeable_value": 0},
            max_solutions=2,
        )
        self.assertEqual(result["solution_count"], 2)
        self.assertNotEqual(
            result["solutions"][0]["item_ids"], result["solutions"][1]["item_ids"]
        )
        for solution in result["solutions"]:
            self.assertTrue(solution["validation"]["valid"])
            persisted = self.runtime.get_sbc_solution(solution["solution_id"])
            self.assertEqual(persisted["item_ids"], solution["item_ids"])

    def test_unknown_requirement_blocks_solver(self):
        unknown = {
            "kvPairs": {"_collection": {"999": [1]}},
            "count": -1,
            "scope": 2,
        }
        self.service.capture_challenges(challenge_payload([unknown]))
        with self.assertRaises(FC27Error) as context:
            self.service.solve("16", {}, max_solutions=1)
        self.assertEqual(context.exception.code, "SBC_SCHEMA_UNSUPPORTED")
        self.assertEqual(
            context.exception.details["unsupported_constraints"][0]["reason"],
            "unknown requirement key 999",
        )


if __name__ == "__main__":
    unittest.main()
