import json
import tempfile
import time
import unittest
from pathlib import Path

from fc27.errors import FC27Error
from fc27.runtime import RuntimeManager
from fc27.sbc import SbcService
from fc27.sbc_optimizer import chemistry_score


class FakeCatalog:
    def __init__(self, facts):
        self.facts = facts
        self.market_candidates = []

    def sbc_item_facts(self, card_ea_ids):
        return {card_id: self.facts[card_id] for card_id in card_ea_ids if card_id in self.facts}

    def sbc_catalog_candidates(self, constraints, max_overall=None):
        rows = list(self.market_candidates)
        if max_overall is not None:
            rows = [row for row in rows if row["overall"] <= max_overall]
        return rows


class FakePriceClient:
    def __init__(self):
        self.calls = []

    def current_prices(self, card_ea_ids):
        self.calls.append(list(card_ea_ids))
        observed_at = "2026-09-20T12:00:00Z"
        return {
            "observed_at": observed_at,
            "manifest_version": 1,
            "hashes": {"index": "i", "ps5": "p", "pc": "c"},
            "prices": [
                {
                    "card_ea_id": card_ea_id,
                    "platform": "pc",
                    "observed_at": observed_at,
                    "price": 100 + offset * 50,
                    "status": "market_or_normal",
                    "is_extinct": False,
                }
                for offset, card_ea_id in enumerate(sorted(card_ea_ids))
            ],
            "missing_card_ea_ids": [],
        }


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
                "slots": [
                    "GK", "LB", "CB", "CB", "RB", "CM",
                    "CM", "CM", "LW", "RW", "ST",
                ],
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
                "slots": [
                    "GK", "LB", "CB", "CB", "RB", "CDM",
                    "CM", "CM", "CAM", "ST", "ST",
                ],
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
            for item_id in range(1, 16):
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
                    "base_player_ea_id": card_id,
                    "overall": 60 + item_id % 4,
                    "quality": "bronze",
                    "club_id": item_id % 3,
                    "league_id": item_id % 4,
                    "nation_id": item_id % 5,
                    "is_special": False,
                    "is_evolution": False,
                }
            connection.commit()
        self.price_client = FakePriceClient()
        self.service = SbcService(
            self.runtime, FakeCatalog(facts), price_client=self.price_client
        )

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
        for item_id in range(1, 16):
            self.service.catalog.facts[1000 + item_id]["overall"] = 60
        result = self.service.solve(
            "4",
            "16",
            {"max_tradeable_value": 0},
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

    def test_solver_and_validator_reject_duplicate_base_player_instances(self):
        self.service.capture_challenges(challenge_payload())
        self.service.catalog.facts[1001]["base_player_ea_id"] = 500
        self.service.catalog.facts[1002]["base_player_ea_id"] = 500
        result = self.service.solve(
            "4",
            "16",
            {"candidate_item_ids": list(range(1, 13))},
            max_solutions=1,
        )
        solution = result["solutions"][0]
        selected_base_players = [
            self.service.catalog.facts[1000 + item_id]["base_player_ea_id"]
            for item_id in solution["item_ids"]
        ]
        self.assertEqual(len(selected_base_players), len(set(selected_base_players)))

        challenge = self.runtime.get_sbc_challenge("16")
        rows = self.runtime.items_by_ids(list(range(1, 12)))
        items = [
            {**row, **self.service.catalog.facts[row["card_ea_id"]]}
            for row in rows
        ]
        validation = self.service.validate(challenge, items)
        self.assertFalse(validation["valid"])
        self.assertIn(
            {"type": "duplicate_base_player_ids", "base_player_ea_ids": [500]},
            validation["failures"],
        )

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
                "max_tradeable_value": 0,
            },
            max_solutions=2,
        )
        self.assertEqual(result["solver"]["engine"], "or-tools-cp-sat")
        for solution in result["solutions"]:
            self.assertTrue({12, 13}.issubset(solution["item_ids"]))
            self.assertEqual(solution["objective"]["required_item_ids"], [12, 13])
            self.assertIn(
                solution["validation"]["solver"]["status"],
                {"OPTIMAL_PROVEN", "FEASIBLE_UNPROVEN"},
            )
            persisted = self.runtime.get_sbc_solution(solution["solution_id"])
            self.assertEqual(persisted["objective"]["required_item_ids"], [12, 13])

    def test_optimizer_prioritizes_rating_before_tradeability(self):
        self.service.capture_challenges(challenge_payload())
        with self.runtime.connect() as connection:
            for item_id in range(1, 16):
                connection.execute(
                    """UPDATE club_items
                       SET tradeable = ?, acquisition_cost = ?
                       WHERE item_id = ?""",
                    (0 if item_id == 15 else 1, item_id * 100, item_id),
                )
            connection.commit()
        self.service.catalog.facts[1015]["overall"] = 64
        for item_id in range(1, 15):
            self.service.catalog.facts[1000 + item_id]["overall"] = 60
        result = self.service.solve("4", "16", {}, max_solutions=1)
        solution = result["solutions"][0]
        self.assertNotIn(15, solution["item_ids"])
        self.assertEqual(solution["validation"]["metrics"]["max_overall"], 60)

    def test_optimizer_prefers_untradeable_after_equal_rating(self):
        self.service.capture_challenges(challenge_payload())
        with self.runtime.connect() as connection:
            connection.execute("UPDATE club_items SET tradeable = 1")
            connection.execute(
                "UPDATE club_items SET tradeable = 0 WHERE item_id = 15"
            )
            connection.commit()
        for item_id in range(1, 16):
            self.service.catalog.facts[1000 + item_id]["overall"] = 60
        result = self.service.solve(
            "4",
            "16",
            {"candidate_item_ids": list(range(1, 13)) + [15]},
            max_solutions=1,
        )
        self.assertIn(15, result["solutions"][0]["item_ids"])

    def test_optimizer_default_budget_is_longer_than_five_seconds(self):
        from fc27.sbc_optimizer import SbcOptimizer

        self.assertEqual(SbcOptimizer().time_limit_seconds, 180.0)

    def test_rating_vector_precedes_total_overall(self):
        payload = brick_challenge_payload(3)
        payload["challenges"][0]["slots"] = ["GK", "LB", "CB"]
        payload["challenges"][0]["requirements"] = [
            specific_requirement(6, [2], -1, scope=0)
        ]
        challenge = self.service.capture_challenges(payload)["challenges"][0]
        facts = {
            1: (80, 3),
            2: (79, 1),
            3: (66, 1),
            4: (78, 2),
            5: (78, 2),
        }
        for item_id, (overall, club_id) in facts.items():
            self.service.catalog.facts[1000 + item_id].update(
                {"overall": overall, "club_id": club_id}
            )
        result = self.service.solve(
            "1",
            challenge["challenge_id"],
            {
                "candidate_item_ids": list(facts),
                "required_item_ids": [1],
            },
            max_solutions=1,
        )
        solution = result["solutions"][0]
        self.assertEqual(set(solution["item_ids"]), {1, 4, 5})
        self.assertEqual(
            solution["validation"]["metrics"]["rating_vector"],
            [80, 78, 78],
        )

    def test_hall_model_rejects_false_out_of_position_assignment(self):
        payload = brick_challenge_payload(3)
        payload["challenges"][0]["slots"] = ["ST", "GK", "CB"]
        payload["challenges"][0]["requirements"] = [
            specific_requirement(35, [4], -1, scope=2)
        ]
        challenge = self.service.capture_challenges(payload)["challenges"][0]
        for item_id in (1, 2, 3):
            self.service.catalog.facts[1000 + item_id].update(
                {
                    "club_id": 10,
                    "league_id": 20,
                    "nation_id": 30,
                    "positions": ["ST", "GK"] if item_id < 3 else ["CB"],
                }
            )
        with self.assertRaises(FC27Error) as context:
            self.service.solve(
                "1",
                challenge["challenge_id"],
                {"candidate_item_ids": [1, 2, 3]},
                max_solutions=1,
            )
        self.assertEqual(context.exception.code, "SBC_NO_SOLUTION")

    def test_default_planner_returns_owned_only_without_loading_market_prices(self):
        payload = brick_challenge_payload(3)
        challenge = self.service.capture_challenges(payload)["challenges"][0]
        for item_id in range(1, 16):
            self.service.catalog.facts[1000 + item_id]["overall"] = 70
        self.service.catalog.market_candidates = [
            {
                "card_ea_id": 2000 + index,
                "base_player_ea_id": 3000 + index,
                "overall": 60,
                "quality": "bronze",
                "club_id": 100 + index,
                "league_id": 200 + index,
                "nation_id": 300 + index,
                "positions": ["ST"],
                "is_special": False,
                "is_evolution": False,
            }
            for index in range(1, 4)
        ]
        result = self.service.solve(
            "1", challenge["challenge_id"], {}, max_solutions=1
        )
        self.assertEqual(result["requested_purchase_budget"], 0)
        self.assertEqual([plan["purchase_count"] for plan in result["plans"]], [0])
        self.assertEqual(result["plans"][0]["plan_type"], "club_only")
        self.assertEqual(result["plans"][0]["metrics"]["rating_vector"], [70] * 3)
        self.assertTrue(result["plans"][0]["executable"])
        self.assertEqual(result["solution_count"], 1)
        self.assertEqual(result["candidate_pool"]["catalog_count"], 0)
        self.assertEqual(list(result["budget_analysis"]), ["0"])
        self.assertEqual(self.price_client.calls, [])
        self.assertEqual(result["actions_performed"], [])

    def test_owned_only_solver_uses_180_second_deadline(self):
        challenge = self.service.capture_challenges(challenge_payload())["challenges"][0]
        observed = {}

        def capture_deadline(
            challenge,
            items,
            objective,
            max_solutions,
            deadline,
            **kwargs,
        ):
            observed["remaining"] = deadline - time.monotonic()
            return self.service._empty_optimizer_result(
                "UNKNOWN_NO_SOLUTION_FOUND"
            ), []

        self.service._solve_domain = capture_deadline
        with self.assertRaisesRegex(FC27Error, "shared deadline"):
            self.service.solve(
                "4",
                challenge["challenge_id"],
                {},
                max_solutions=1,
                purchase_budget=0,
            )
        self.assertGreater(observed["remaining"], 179.0)
        self.assertLessEqual(observed["remaining"], 180.0)

    def test_planner_expands_exact_purchase_levels_incrementally(self):
        payload = brick_challenge_payload(3)
        challenge = self.service.capture_challenges(payload)["challenges"][0]
        for item_id in range(1, 16):
            self.service.catalog.facts[1000 + item_id]["overall"] = 70
        self.service.catalog.market_candidates = [
            {
                "card_ea_id": 2000 + index,
                "base_player_ea_id": 3000 + index,
                "overall": 60,
                "quality": "bronze",
                "club_id": 100 + index,
                "league_id": 200 + index,
                "nation_id": 300 + index,
                "positions": ["ST"],
                "is_special": False,
                "is_evolution": False,
            }
            for index in range(1, 4)
        ]
        solve_calls = []
        solve_domain = self.service._solve_domain

        def recording_solve_domain(*args, **kwargs):
            solve_calls.append(kwargs.get("exact_purchase_count"))
            return solve_domain(*args, **kwargs)

        self.service._solve_domain = recording_solve_domain
        result = self.service.solve(
            "1",
            challenge["challenge_id"],
            {},
            max_solutions=2,
            purchase_budget=2,
        )
        by_budget = {}
        for plan in result["plans"]:
            by_budget.setdefault(plan["purchase_budget"], []).append(plan)
            self.assertEqual(plan["purchase_count"], plan["purchase_budget"])
        self.assertEqual(set(by_budget), {0, 1, 2})
        self.assertEqual(by_budget[0][0]["metrics"]["rating_vector"], [70] * 3)
        self.assertEqual(by_budget[1][0]["metrics"]["rating_vector"], [70, 70, 60])
        self.assertEqual(by_budget[2][0]["metrics"]["rating_vector"], [70, 60, 60])
        self.assertTrue(by_budget[0][0]["executable"])
        self.assertFalse(by_budget[1][0]["executable"])
        self.assertTrue(by_budget[1][0]["market_verification_required"])
        self.assertEqual(by_budget[1][0]["solver_status"], "LOCAL_OPTIMUM")
        first_purchase_ids = {
            row["card_ea_id"] for row in by_budget[1][0]["purchase_targets"]
        }
        second_purchase_ids = {
            row["card_ea_id"] for row in by_budget[2][0]["purchase_targets"]
        }
        self.assertTrue(first_purchase_ids.issubset(second_purchase_ids))
        self.assertEqual(result["requested_purchase_budget"], 2)
        self.assertEqual(set(result["budget_analysis"]), {"0", "1", "2"})
        self.assertNotIn("market_benchmark", result)
        self.assertEqual(len(self.price_client.calls), 1)
        self.assertEqual(result["solution_count"], 2)
        self.assertEqual(solve_calls, [0])

    def test_purchase_budget_cannot_exceed_challenge_size(self):
        challenge = self.service.capture_challenges(brick_challenge_payload(3))[
            "challenges"
        ][0]
        with self.assertRaises(FC27Error) as context:
            self.service.solve(
                "1", challenge["challenge_id"], {}, purchase_budget=4
            )
        self.assertEqual(context.exception.code, "SBC_PURCHASE_BUDGET_INVALID")

    def test_planner_supports_purchase_budget_equal_to_challenge_size(self):
        challenge = self.service.capture_challenges(brick_challenge_payload(3))[
            "challenges"
        ][0]
        for item_id in range(1, 16):
            self.service.catalog.facts[1000 + item_id]["overall"] = 70
        self.service.catalog.market_candidates = [
            {
                "card_ea_id": 2100 + index,
                "base_player_ea_id": 3100 + index,
                "overall": 60,
                "quality": "bronze",
                "club_id": 400 + index,
                "league_id": 500 + index,
                "nation_id": 600 + index,
                "positions": ["ST"],
                "is_special": False,
                "is_evolution": False,
            }
            for index in range(3)
        ]
        result = self.service.solve(
            "1",
            challenge["challenge_id"],
            {},
            max_solutions=1,
            purchase_budget=3,
        )
        highest = [
            plan for plan in result["plans"] if plan["purchase_budget"] == 3
        ]
        self.assertEqual(len(highest), 1)
        self.assertEqual(highest[0]["purchase_count"], 3)
        self.assertEqual(highest[0]["owned_count"], 0)

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
        slots = challenge_payload()["challenges"][0]["slots"]
        for item_id in range(1, 12):
            card_id = 1000 + item_id
            self.service.catalog.facts[card_id].update(
                {
                    "overall": 75,
                    "quality": "gold",
                    "club_id": 73 if item_id == 1 else 219 if item_id == 2 else 300 + item_id,
                    "league_id": 308,
                    "nation_id": 42 if item_id <= 2 else 1,
                    "positions": [slots[item_id - 1]],
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

    def test_same_nation_minimum_requirement_uses_live_key_four_mapping(self):
        captured = self.service.capture_challenges(
            challenge_payload([specific_requirement(4, [4], 4, scope=0)])
        )
        challenge = captured["challenges"][0]
        self.assertEqual(challenge["constraints"][0]["type"], "same_nation_count")
        for item_id in range(1, 14):
            self.service.catalog.facts[1000 + item_id].update(
                {
                    "overall": 75,
                    "quality": "gold",
                    "nation_id": 4 if item_id <= 3 else item_id + 10,
                }
            )
        with self.assertRaises(FC27Error) as context:
            self.service.solve(
                "4",
                challenge["challenge_id"],
                {"candidate_item_ids": list(range(1, 12))},
                max_solutions=1,
            )
        self.assertEqual(context.exception.code, "SBC_NO_SOLUTION")
        self.service.catalog.facts[1004]["nation_id"] = 4
        result = self.service.solve(
            "4",
            challenge["challenge_id"],
            {"candidate_item_ids": list(range(1, 12))},
            max_solutions=1,
        )
        self.assertTrue(result["solutions"][0]["validation"]["valid"])

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
        payload = challenge_payload([bronze_requirement(), specific_requirement(35, [33], -1)])
        payload["challenges"][0]["formation"] = "f442"
        payload["challenges"][0]["slots"] = [
            "GK", "RB", "CB", "CB", "LB", "RM",
            "CM", "CM", "LM", "ST", "ST",
        ]
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
            self.service.catalog.facts[1000 + item_id].update(
                {
                    "positions": [position],
                    "club_id": 1,
                    "league_id": 1,
                    "nation_id": 1,
                }
            )
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

    def test_solver_does_not_require_positions_without_chemistry(self):
        payload = challenge_payload()
        payload["challenges"][0]["slots"] = [
            "GK", "RB", "CB", "CB", "LB", "RM",
            "CM", "CM", "LM", "ST", "ST",
        ]
        self.service.capture_challenges(payload)
        for item_id in range(1, 16):
            self.service.catalog.facts[1000 + item_id].update(
                {"positions": ["ST"], "overall": 60}
            )
        result = self.service.solve("4", "16", {}, max_solutions=1)
        self.assertEqual(result["solutions"][0]["item_ids"], list(range(1, 12)))

    def test_standard_card_chemistry_uses_only_in_position_contributors(self):
        items = [
            {"item_id": 1, "positions": ["ST"], "club_id": 10, "league_id": 20, "nation_id": 30},
            {"item_id": 2, "positions": ["CAM"], "club_id": 10, "league_id": 20, "nation_id": 30},
            {"item_id": 3, "positions": ["CM"], "club_id": 99, "league_id": 20, "nation_id": 40},
        ]
        correct = chemistry_score(items, ["ST", "CAM", "CM"])
        self.assertEqual(correct["per_player"], [3, 3, 1])
        self.assertEqual(correct["total"], 7)

        one_out_of_position = chemistry_score(items, ["GK", "CAM", "CM"])
        self.assertEqual(one_out_of_position["per_player"], [0, 0, 0])
        self.assertEqual(one_out_of_position["total"], 0)

    def test_team_chemistry_allows_low_value_out_of_position_fillers(self):
        payload = challenge_payload(
            [bronze_requirement(), specific_requirement(35, [9], -1)]
        )
        slots = payload["challenges"][0]["slots"]
        self.service.capture_challenges(payload)
        for item_id in range(1, 16):
            fact = self.service.catalog.facts[1000 + item_id]
            fact.update(
                {
                    "positions": ["GK"],
                    "club_id": 100 + item_id,
                    "league_id": 200 + item_id,
                    "nation_id": 300 + item_id,
                }
            )
        for item_id, position in zip((1, 2, 3), slots[:3]):
            self.service.catalog.facts[1000 + item_id].update(
                {
                    "positions": [position],
                    "club_id": 10,
                    "league_id": 20,
                    "nation_id": 30,
                }
            )
        result = self.service.solve(
            "4",
            "16",
            {"candidate_item_ids": list(range(1, 12))},
            max_solutions=1,
        )
        solution = result["solutions"][0]
        self.assertEqual(solution["validation"]["metrics"]["chemistry"], 9)
        positions_by_item = {
            item_id: set(self.service.catalog.facts[1000 + item_id]["positions"])
            for item_id in solution["item_ids"]
        }
        self.assertTrue(
            any(
                slot["position"] not in positions_by_item[slot["item_id"]]
                for slot in solution["slots"]
            )
        )

    def test_current_marquee_formations_use_ea_slot_order(self):
        expected = {
            "f451": ["GK", "RB", "CB", "CB", "LB", "RM", "CM", "LM", "CAM", "CAM", "ST"],
            "f532": ["GK", "RB", "CB", "CB", "CB", "LB", "CDM", "CM", "CM", "ST", "ST"],
            "f5212": ["GK", "RB", "CB", "CB", "CB", "LB", "CM", "CM", "CAM", "ST", "ST"],
        }
        for formation, slots in expected.items():
            payload = challenge_payload()
            payload["challenges"][0]["formation"] = formation
            payload["challenges"][0]["slots"] = slots
            captured = self.service.capture_challenges(payload)
            self.assertEqual(captured["challenges"][0]["slots"], slots)

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

    def test_requirement_key_registry_matches_current_web_app_enum(self):
        cases = [
            (4, [4], -1, 0, "same_nation_count"),
            (5, [4], -1, 0, "same_league_count"),
            (6, [3], -1, 1, "same_club_count"),
            (7, [2], -1, 0, "nation_count"),
            (8, [6], -1, 1, "league_count"),
            (9, [3], -1, 1, "club_count"),
            (10, [38], 2, 0, "specific_nation_count"),
            (11, [53], 2, 0, "specific_league_count"),
            (12, [240, 243], 2, 0, "specific_club_count"),
            (17, [3], 1, 0, "quality_count"),
            (19, [75], -1, 0, "team_rating"),
            (26, [75], 4, 0, "overall_count"),
            (27, [75], 4, 2, "overall_count"),
            (28, [75], 4, 0, "overall_count"),
            (35, [26], -1, 0, "chemistry"),
            (36, [2], -1, 0, "all_players_chemistry_points"),
        ]
        for key, values, count, scope, expected_type in cases:
            with self.subTest(key=key, scope=scope):
                constraints, unsupported = self.service._normalize_requirements(
                    [specific_requirement(key, values, count, scope)]
                )
                self.assertEqual(unsupported, [])
                self.assertEqual(constraints[0]["type"], expected_type)

    def test_known_unsupported_requirement_reports_web_app_enum_name(self):
        constraints, unsupported = self.service._normalize_requirements(
            [specific_requirement(18, [12], 1, 0)]
        )
        self.assertEqual(constraints, [])
        self.assertEqual(
            unsupported[0]["reason"],
            "known unsupported requirement PLAYER_RARITY (18)",
        )

    def test_explicit_slot_positions_are_authoritative_over_formation_name(self):
        payload = challenge_payload()
        challenge = payload["challenges"][0]
        challenge["formation"] = "f999"
        challenge["slots"] = [
            "GK", "RB", "CB", "CB", "LB", "RM",
            "CM", "LM", "CAM", "CAM", "ST",
        ]
        normalized = self.service.capture_challenges(payload)["challenges"][0]
        self.assertEqual(normalized["slots"], challenge["slots"])
        self.assertEqual(normalized["slot_positions_source"], "challenge.slots")

    def test_completed_refresh_retains_previous_exact_slot_contract(self):
        first = challenge_payload()
        first_challenge = first["challenges"][0]
        captured = self.service.capture_challenge(
            {"set": first["set"], "challenge": first_challenge}
        )["challenge"]
        self.assertEqual(captured["slot_indices"], list(range(11)))

        refreshed = challenge_payload()
        value = refreshed["challenges"][0]
        value.update(
            {
                "status": "COMPLETED",
                "completed": True,
                "player_count": None,
                "slot_indices": None,
                "slots": None,
                "slot_layout_error": {
                    "code": 466,
                    "status": 403,
                    "message": "completed challenge unavailable",
                },
            }
        )
        retained = self.service.capture_challenge(
            {"set": refreshed["set"], "challenge": value}
        )["challenge"]
        self.assertEqual(retained["slot_indices"], list(range(11)))
        self.assertEqual(retained["slots"], first_challenge["slots"])
        self.assertEqual(retained["slot_indices_source"], "persisted_ea_slot_contract")

    def test_save_readback_retains_previous_position_contract(self):
        first = challenge_payload()
        first_challenge = first["challenges"][0]
        self.service.capture_challenge(
            {"set": first["set"], "challenge": first_challenge}
        )

        readback = challenge_payload()
        value = readback["challenges"][0]
        value["slot_positions"] = []
        value["slot_positions_source"] = None
        value["slots"] = None
        retained = self.service.capture_challenge(
            {"set": readback["set"], "challenge": value}
        )["challenge"]
        self.assertEqual(retained["slot_indices"], list(range(11)))
        self.assertEqual(retained["slots"], first_challenge["slots"])
        self.assertEqual(
            retained["slot_positions_source"], "persisted_ea_slot_contract"
        )

    def test_solver_excludes_special_evolution_and_active_squad_items(self):
        self.service.capture_challenges(challenge_payload())
        self.service.catalog.facts[1001]["is_special"] = True
        self.service.catalog.facts[1002]["is_evolution"] = True
        result = self.service.solve(
            "4", "16", {}, max_solutions=1, reserved_item_ids=[3]
        )
        solution = result["solutions"][0]
        self.assertTrue({1, 2, 3}.isdisjoint(solution["item_ids"]))
        self.assertEqual(
            result["candidate_pool"]["excluded_counts"],
            {"active_squad": 1, "evolution": 1, "special": 1},
        )

    def test_required_item_reports_exact_automatic_protection_reason(self):
        self.service.capture_challenges(challenge_payload())
        cases = [
            (1, "special", []),
            (2, "evolution", []),
            (3, "active_squad", [3]),
        ]
        self.service.catalog.facts[1001]["is_special"] = True
        self.service.catalog.facts[1002]["is_evolution"] = True
        for item_id, reason, reserved in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(FC27Error) as context:
                    self.service.solve(
                        "4",
                        "16",
                        {"required_item_ids": [item_id]},
                        max_solutions=1,
                        reserved_item_ids=reserved,
                    )
                self.assertEqual(context.exception.code, "SBC_REQUIRED_ITEM_INELIGIBLE")
                self.assertEqual(context.exception.details["items"][0]["reason"], reason)

    def test_live_marquee_matchups_fixture_normalizes_and_solves_end_to_end(self):
        fixture = json.loads(
            (
                Path(__file__).parent
                / "fixtures"
                / "fc27_marquee_matchups_20260920.json"
            ).read_text(encoding="utf-8")
        )
        captured = self.service.capture_challenges(fixture)
        by_id = {
            challenge["challenge_id"]: challenge
            for challenge in captured["challenges"]
        }
        self.assertEqual(
            [constraint["type"] for constraint in by_id["39"]["constraints"]],
            [
                "specific_club_count",
                "specific_league_count",
                "same_nation_count",
                "same_club_count",
                "team_rating",
                "chemistry",
            ],
        )
        self.assertEqual(by_id["38"]["constraints"][2]["type"], "same_league_count")

        for challenge_id in ("37", "38", "39"):
            challenge = by_id[challenge_id]
            for item_id, position in enumerate(challenge["slots"], start=1):
                fact = self.service.catalog.facts[1000 + item_id]
                fact.update(
                    {
                        "overall": 75,
                        "quality": "gold",
                        "positions": [position],
                        "is_special": False,
                        "is_evolution": False,
                    }
                )
                if challenge_id == "37":
                    fact.update(
                        {
                            "club_id": 100 + item_id,
                            "league_id": 308,
                            "nation_id": 38,
                        }
                    )
                elif challenge_id == "38":
                    fact.update(
                        {
                            "club_id": 73 if item_id <= 5 else 219 if item_id <= 10 else 500,
                            "league_id": 1 if item_id <= 5 else 2 if item_id <= 10 else 3,
                            "nation_id": 18 if item_id <= 10 else 40,
                        }
                    )
                else:
                    fact.update(
                        {
                            "club_id": [240, 243, 300, 301, 302, 303, 304, 305, 400, 401, 402][item_id - 1],
                            "league_id": 53 if item_id <= 8 else 54,
                            "nation_id": 1 if item_id <= 8 else 2,
                        }
                    )
            result = self.service.solve(
                "16",
                challenge_id,
                {"candidate_item_ids": list(range(1, 12))},
                max_solutions=1,
            )
            solution = result["solutions"][0]
            self.assertTrue(solution["validation"]["valid"])
            chemistry_requirement = next(
                constraint["value"]
                for constraint in challenge["constraints"]
                if constraint["type"] == "chemistry"
            )
            self.assertGreaterEqual(
                solution["validation"]["metrics"]["chemistry"],
                chemistry_requirement,
            )


if __name__ == "__main__":
    unittest.main()
