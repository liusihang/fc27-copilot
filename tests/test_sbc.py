import json
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


def minimum_rating_requirement(value):
    return {
        "kvPairs": {"_collection": {"19": [value]}},
        "count": -1,
        "scope": 0,
    }


def specific_requirement(key, values, count, scope=0):
    return {
        "kvPairs": {"_collection": {str(key): list(values)}},
        "count": count,
        "scope": scope,
    }


def overall_requirement(key, overall, count):
    return {
        "kvPairs": {"_collection": {str(key): [overall]}},
        "count": count,
        "scope": 0,
    }


def brick_challenge_payload(
    player_count, *, minimum=None, maximum=None, slot_indices=None
):
    requirements = []
    if minimum is not None:
        requirements.append(overall_requirement(26, minimum, player_count))
    if maximum is not None:
        requirements.append(overall_requirement(28, maximum, player_count))
    if slot_indices is None:
        slot_indices = list(range(player_count))
    return {
        "set": {
            "id": 1,
            "name": "Intro to SBCs",
            "repeatable": False,
            "rewards": [],
            "raw": {"id": 1},
        },
        "challenges": [
            {
                "id": player_count,
                "set_id": 1,
                "name": f"Brick {player_count}",
                "status": "NOT_STARTED",
                "repeatable": False,
                "completed": False,
                "challenge_type": "BRICK_CHALLENGE",
                "slot_indices": slot_indices,
                "formation": "f433",
                "rewards": [],
                "requirements": requirements,
                "raw": {"id": player_count, "formation": "f433", "type": "BRICK_CHALLENGE"},
            }
        ],
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
                "slot_indices": list(range(11)),
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
            "4",
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

    def test_solver_requires_matching_set_and_challenge_identity(self):
        self.service.capture_challenges(challenge_payload())
        with self.assertRaises(FC27Error) as context:
            self.service.solve("999", "16", {}, max_solutions=1)
        self.assertEqual(context.exception.code, "SBC_CHALLENGE_NOT_FOUND")

    def test_solver_includes_every_agent_required_item(self):
        self.service.capture_challenges(challenge_payload())
        result = self.service.solve(
            "4",
            "16",
            {
                "required_item_ids": [12, 13],
                "prefer_untradeable": True,
                "max_tradeable_value": 0,
            },
            max_solutions=2,
        )
        self.assertEqual(result["solver"]["engine"], "or-tools-cp-sat")
        for solution in result["solutions"]:
            self.assertTrue({12, 13}.issubset(solution["item_ids"]))
            self.assertEqual(solution["objective"]["required_item_ids"], [12, 13])
            self.assertIn(solution["validation"]["solver"]["status"], {"optimal", "feasible"})
            persisted = self.runtime.get_sbc_solution(solution["solution_id"])
            self.assertEqual(persisted["objective"]["required_item_ids"], [12, 13])

    def test_solver_minimizes_tradeable_value(self):
        self.service.capture_challenges(challenge_payload())
        with self.runtime.connect() as connection:
            for item_id in range(1, 14):
                connection.execute(
                    """UPDATE club_items
                       SET tradeable = 1, acquisition_cost = ?
                       WHERE item_id = ?""",
                    (item_id * 100, item_id),
                )
            connection.commit()
        result = self.service.solve(
            "4", "16", {"prefer_untradeable": False}, max_solutions=1
        )
        self.assertEqual(result["solutions"][0]["item_ids"], list(range(1, 12)))
        self.assertEqual(result["solutions"][0]["tradeable_value"], 6600)

    def test_optimizer_rating_model_matches_independent_validator(self):
        self.service.capture_challenges(
            challenge_payload([bronze_requirement(), minimum_rating_requirement(62)])
        )
        result = self.service.solve("4", "16", {}, max_solutions=2)
        for solution in result["solutions"]:
            self.assertGreaterEqual(
                solution["validation"]["metrics"]["team_rating"], 62
            )

    def test_marquee_requirement_keys_and_chemistry_are_supported(self):
        for item_id in range(1, 12):
            card_id = 1000 + item_id
            self.service.catalog.facts[card_id].update(
                {
                    "overall": 75,
                    "quality": "gold",
                    "club_id": 73 if item_id == 1 else 219 if item_id == 2 else 300 + item_id,
                    "league_id": 308,
                    "nation_id": 42 if item_id <= 2 else 1,
                }
            )
        captured = self.service.capture_challenges(
            challenge_payload(
                [
                    specific_requirement(10, [42], 1),
                    specific_requirement(11, [308], 2),
                    specific_requirement(12, [73, 219], 1),
                    specific_requirement(3, [2], -1),
                    specific_requirement(35, [14], -1),
                    minimum_rating_requirement(75),
                ]
            )
        )
        challenge = captured["challenges"][0]
        self.assertEqual(
            [constraint["type"] for constraint in challenge["constraints"]],
            [
                "specific_nation_count",
                "specific_league_count",
                "specific_club_count",
                "squad_quality",
                "chemistry",
                "team_rating",
            ],
        )
        self.assertEqual(challenge["unsupported_constraints"], [])
        result = self.service.solve("4", "16", {}, max_solutions=1)
        solution = result["solutions"][0]
        self.assertTrue(solution["validation"]["valid"])
        self.assertGreaterEqual(solution["validation"]["metrics"]["chemistry"], 14)

    def test_minimum_squad_quality_accepts_higher_quality_items(self):
        captured = self.service.capture_challenges(
            challenge_payload([specific_requirement(3, [2], -1)])
        )
        challenge = captured["challenges"][0]
        for card_id in range(1001, 1012):
            self.service.catalog.facts[card_id]["quality"] = "gold"
        result = self.service.solve("4", challenge["challenge_id"], {}, max_solutions=1)
        self.assertTrue(result["solutions"][0]["validation"]["valid"])

    def test_solver_assigns_cards_to_formation_slots(self):
        payload = challenge_payload()
        payload["challenges"][0]["formation"] = "f442"
        captured = self.service.capture_challenges(payload)
        challenge = captured["challenges"][0]
        self.assertEqual(
            challenge["slots"],
            ["GK", "RB", "CB", "CB", "LB", "RM", "CM", "CM", "LM", "ST", "ST"],
        )
        positions = [
            "ST",
            "GK",
            "RB",
            "CB",
            "CB",
            "LB",
            "RM",
            "CM",
            "CM",
            "LM",
            "ST",
        ]
        for item_id, position in enumerate(positions, start=1):
            self.service.catalog.facts[1000 + item_id]["positions"] = [position]
        result = self.service.solve(
            "4",
            challenge["challenge_id"],
            {"candidate_item_ids": list(range(1, 12))},
            max_solutions=1,
        )
        solution = result["solutions"][0]
        self.assertEqual(set(solution["item_ids"]), set(range(1, 12)))
        self.assertEqual(
            [slot["position"] for slot in solution["slots"]],
            challenge["slots"],
        )
        positions_by_item = {
            item_id: position for item_id, position in enumerate(positions, start=1)
        }
        self.assertTrue(
            all(
                positions_by_item[slot["item_id"]] == slot["position"]
                for slot in solution["slots"]
            )
        )

    def test_brick_challenge_uses_requirement_count_instead_of_formation_size(self):
        captured = self.service.capture_challenges(
            brick_challenge_payload(1, maximum=64)
        )
        challenge = captured["challenges"][0]
        self.assertEqual(challenge["challenge_type"], "BRICK_CHALLENGE")
        self.assertEqual(challenge["player_count"], 1)
        self.assertIn("brick_requirement_count", challenge["player_count_source"])
        self.assertEqual(challenge["slot_indices"], [0])
        self.assertEqual(challenge["slots"], ["GK"])
        self.assertEqual(challenge["constraints"][0]["type"], "overall_count")
        result = self.service.solve(
            "1",
            challenge["challenge_id"],
            {"required_item_ids": [12]},
            max_solutions=1,
        )
        self.assertEqual(result["solutions"][0]["item_ids"], [12])

    def test_variable_player_counts_cover_fc26_early_midseason_samples(self):
        samples = json.loads(
            (Path(__file__).parent / "fixtures" / "fc26_sbc_samples.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(samples), 10)
        self.assertEqual(
            {sample["player_count"] for sample in samples}, {1, 2, 3, 4, 8, 11}
        )
        self.assertTrue(
            all("fut.gg/api/fut/sbc/26/" in sample["source"] for sample in samples)
        )
        self.assertTrue(
            all("fut.gg/api/squads/" in sample["solution_source"] for sample in samples)
        )
        self.assertTrue(
            all("2025-09-01" <= sample["published_at"] <= "2026-03-31" for sample in samples)
        )
        covered_requirements = {
            kind for sample in samples for kind in sample["requirement_kinds"]
        }
        self.assertTrue(
            {
                "individual_overall_min",
                "individual_overall_max",
                "team_rating_min",
                "quality_min",
                "rare_count_min",
                "special_card_count_min",
                "club_match_min",
                "league_count",
                "nation_count",
            }.issubset(covered_requirements)
        )
        for count in (1, 2, 3, 4, 8):
            captured = self.service.capture_challenges(
                brick_challenge_payload(count, maximum=84)
            )
            challenge = captured["challenges"][0]
            self.assertEqual(challenge["player_count"], count)
            self.assertEqual(len(challenge["slots"]), count)

    def test_full_open_challenge_uses_observed_field_slots(self):
        captured = self.service.capture_challenges(challenge_payload())
        challenge = captured["challenges"][0]
        self.assertEqual(challenge["player_count"], 11)
        self.assertEqual(challenge["slot_indices"], list(range(11)))
        self.assertEqual(challenge["player_count_source"], "challenge.slot_indices")

    def test_contradictory_player_count_evidence_stays_unsupported(self):
        payload = brick_challenge_payload(3, maximum=84)
        payload["challenges"][0]["player_count"] = 2
        captured = self.service.capture_challenges(payload)
        challenge = captured["challenges"][0]
        self.assertIsNone(challenge["player_count"])
        self.assertEqual(
            challenge["unsupported_constraints"][-1]["reason"],
            "player count evidence is contradictory",
        )

    def test_custom_brick_without_count_evidence_is_not_assumed_to_be_eleven(self):
        payload = brick_challenge_payload(3, maximum=83)
        challenge = payload["challenges"][0]
        challenge["challenge_type"] = "CUSTOM_BRICK_CHALLENGE"
        challenge["raw"]["type"] = "CUSTOM_BRICK_CHALLENGE"
        challenge["slot_indices"] = None
        challenge["requirements"] = [
            {
                "kvPairs": {"_collection": {"7": [2]}},
                "count": -1,
                "scope": 0,
            }
        ]
        captured = self.service.capture_challenges(payload)
        normalized = captured["challenges"][0]
        self.assertIsNone(normalized["player_count"])
        self.assertEqual(normalized["slots"], [])
        self.assertEqual(
            normalized["unsupported_constraints"][-1]["reason"],
            "variable-size challenge has no bounded player count evidence",
        )

    def test_custom_brick_accepts_explicit_squad_size(self):
        payload = brick_challenge_payload(4, maximum=84)
        challenge = payload["challenges"][0]
        challenge["challenge_type"] = "CUSTOM_BRICK_CHALLENGE"
        challenge["raw"]["type"] = "CUSTOM_BRICK_CHALLENGE"
        challenge["slot_indices"] = [2, 3, 7, 8]
        captured = self.service.capture_challenges(payload)
        normalized = captured["challenges"][0]
        self.assertEqual(normalized["player_count"], 4)
        self.assertEqual(normalized["slot_indices"], [2, 3, 7, 8])
        self.assertEqual(normalized["slots"], ["CB", "CB", "CM", "LW"])

    def test_solution_persists_real_noncontiguous_fillable_slot_indices(self):
        payload = brick_challenge_payload(
            3, maximum=64, slot_indices=[2, 5, 8]
        )
        captured = self.service.capture_challenges(payload)
        result = self.service.solve(
            "1", captured["challenges"][0]["challenge_id"], {}, max_solutions=1
        )
        solution = result["solutions"][0]
        self.assertEqual(
            [entry["slot_index"] for entry in solution["slots"]], [2, 5, 8]
        )
        persisted = self.runtime.get_sbc_solution(solution["solution_id"])
        self.assertEqual(
            [entry["slot_index"] for entry in persisted["slots"]], [2, 5, 8]
        )

    def test_required_and_excluded_item_conflict_is_actionable(self):
        self.service.capture_challenges(challenge_payload())
        with self.assertRaises(FC27Error) as context:
            self.service.solve(
                "4",
                "16",
                {"required_item_ids": [12], "exclude_item_ids": [12]},
                max_solutions=1,
            )
        self.assertEqual(context.exception.code, "SBC_OBJECTIVE_CONFLICT")
        self.assertEqual(context.exception.details["item_ids"], [12])

    def test_missing_required_item_is_rejected(self):
        self.service.capture_challenges(challenge_payload())
        with self.assertRaises(FC27Error) as context:
            self.service.solve(
                "4", "16", {"required_item_ids": [999999]}, max_solutions=1
            )
        self.assertEqual(context.exception.code, "SBC_REQUIRED_ITEM_MISSING")
        self.assertEqual(context.exception.details["item_ids"], [999999])

    def test_protected_required_item_is_rejected(self):
        self.service.capture_challenges(challenge_payload())
        with self.runtime.connect() as connection:
            connection.execute(
                "UPDATE club_items SET protected = 1 WHERE item_id = 12"
            )
            connection.commit()
        with self.assertRaises(FC27Error) as context:
            self.service.solve(
                "4", "16", {"required_item_ids": [12]}, max_solutions=1
            )
        self.assertEqual(context.exception.code, "SBC_REQUIRED_ITEM_INELIGIBLE")
        self.assertEqual(context.exception.details["items"][0]["reason"], "protected")

    def test_duplicate_required_item_is_rejected(self):
        self.service.capture_challenges(challenge_payload())
        with self.assertRaises(FC27Error) as context:
            self.service.solve(
                "4", "16", {"required_item_ids": [12, 12]}, max_solutions=1
            )
        self.assertEqual(context.exception.code, "SBC_OBJECTIVE_INVALID")

    def test_unknown_requirement_blocks_solver(self):
        unknown = {
            "kvPairs": {"_collection": {"999": [1]}},
            "count": -1,
            "scope": 2,
        }
        self.service.capture_challenges(challenge_payload([unknown]))
        with self.assertRaises(FC27Error) as context:
            self.service.solve("4", "16", {}, max_solutions=1)
        self.assertEqual(context.exception.code, "SBC_SCHEMA_UNSUPPORTED")
        self.assertEqual(
            context.exception.details["unsupported_constraints"][0]["reason"],
            "unknown requirement key 999",
        )


if __name__ == "__main__":
    unittest.main()
