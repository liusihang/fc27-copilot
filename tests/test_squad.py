import unittest

from fc27.errors import FC27Error
from fc27.squad import SquadService


class SquadServiceTest(unittest.TestCase):
    def fixture(self):
        return {
            "active_squad_id": 7,
            "max_squads": 10,
            "list_full": False,
            "status": 200,
            "squads": [
                {
                    "squad_id": 7,
                    "name": "Main",
                    "formation": {"id": 8, "name": "f433"},
                    "active_tactic_id": 1,
                    "slots": [
                        {"slot_index": 0, "item": {"item_id": 100}},
                        {"slot_index": 1, "item": {"item_id": 101}},
                    ],
                    "tactics": [
                        {
                            "id": 1,
                            "name": "Balanced",
                            "state": 1,
                            "formation": {"id": 8},
                            "defensive_style": 1,
                            "defensive_line_height": 50,
                            "build_up_play_style": 0,
                            "positions": [0, 1],
                            "instructions": [
                                {"slot_index": 0, "position": 1, "role_id": 2, "variation_id": 0}
                            ],
                        }
                    ],
                },
                {"squad_id": 9, "name": "Concept", "formation": {"id": 3}},
            ],
            "catalog": {"formations": [{"id": 8, "name": "f433"}]},
        }

    def test_active_squad_is_normalized_and_hashed(self):
        options = SquadService.validate_arguments(
            {"detail": "detailed", "include_options": True}
        )
        result = SquadService.normalize(self.fixture(), options)
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["squads"][0]["squad_id"], 7)
        self.assertTrue(result["squads"][0]["active"])
        self.assertEqual(len(result["squads"][0]["squad_hash"]), 64)
        self.assertEqual(result["catalog"]["formations"][0]["id"], 8)

    def test_hash_changes_with_slots_or_tactics(self):
        first = self.fixture()["squads"][0]
        original = SquadService.hash_squad(first)
        changed_slot = self.fixture()["squads"][0]
        changed_slot["slots"][0]["item"]["item_id"] = 999
        self.assertNotEqual(original, SquadService.hash_squad(changed_slot))
        changed_tactic = self.fixture()["squads"][0]
        changed_tactic["tactics"][0]["defensive_line_height"] = 70
        self.assertNotEqual(original, SquadService.hash_squad(changed_tactic))

    def test_exact_requires_squad_id(self):
        with self.assertRaises(FC27Error) as context:
            SquadService.validate_arguments({"selection": "exact"})
        self.assertEqual(context.exception.code, "INVALID_SQUAD_SELECTION")

    def test_summary_does_not_expose_incomplete_hash(self):
        options = SquadService.validate_arguments({"detail": "summary"})
        result = SquadService.normalize(self.fixture(), options)
        self.assertNotIn("squad_hash", result["squads"][0])


if __name__ == "__main__":
    unittest.main()
