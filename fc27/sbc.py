import hashlib
import json
import math
import time
from collections import Counter, defaultdict

from .errors import FC27Error
from .market import FutggPriceClient
from .runtime import utc_now
from .sbc_optimizer import QUALITY_RANKS, SbcOptimizer, chemistry_score


SCOPE_OPERATORS = {0: "min", 1: "max", 2: "exact"}
QUALITY_VALUES = {1: "bronze", 2: "silver", 3: "gold"}
VARIABLE_SQUAD_CHALLENGE_TYPES = {"BRICK_CHALLENGE", "CUSTOM_BRICK_CHALLENGE"}
REQUIREMENT_KEY_SPECS = {
    0: {"name": "TEAM_STAR_RATING", "family": "unsupported"},
    2: {"name": "PLAYER_COUNT", "family": "scalar", "type": "player_count"},
    3: {"name": "PLAYER_QUALITY", "family": "squad_quality"},
    4: {"name": "SAME_NATION_COUNT", "family": "same", "type": "same_nation_count"},
    5: {"name": "SAME_LEAGUE_COUNT", "family": "same", "type": "same_league_count"},
    6: {"name": "SAME_CLUB_COUNT", "family": "same", "type": "same_club_count"},
    7: {"name": "NATION_COUNT", "family": "scalar", "type": "nation_count"},
    8: {"name": "LEAGUE_COUNT", "family": "scalar", "type": "league_count"},
    9: {"name": "CLUB_COUNT", "family": "scalar", "type": "club_count"},
    10: {"name": "NATION_ID", "family": "specific", "type": "specific_nation_count", "ids": "nation_ids"},
    11: {"name": "LEAGUE_ID", "family": "specific", "type": "specific_league_count", "ids": "league_ids"},
    12: {"name": "CLUB_ID", "family": "specific", "type": "specific_club_count", "ids": "club_ids"},
    13: {"name": "SCOPE", "family": "unsupported"},
    15: {"name": "LEGEND_COUNT", "family": "unsupported"},
    16: {"name": "NUM_TROPHY_REQUIRED", "family": "unsupported"},
    17: {"name": "PLAYER_LEVEL", "family": "quality_count"},
    18: {"name": "PLAYER_RARITY", "family": "unsupported"},
    19: {"name": "TEAM_RATING", "family": "scalar", "type": "team_rating"},
    21: {"name": "PLAYER_COUNT_COMBINED", "family": "unsupported"},
    25: {"name": "PLAYER_RARITY_GROUP", "family": "unsupported"},
    26: {"name": "PLAYER_MIN_OVR", "family": "overall_count", "overall_operator": "min"},
    27: {"name": "PLAYER_EXACT_OVR", "family": "overall_count", "overall_operator": "exact"},
    28: {"name": "PLAYER_MAX_OVR", "family": "overall_count", "overall_operator": "max"},
    30: {"name": "FIRST_OWNER_PLAYERS_COUNT", "family": "unsupported"},
    33: {"name": "PLAYER_TRADABILITY", "family": "unsupported"},
    35: {"name": "CHEMISTRY_POINTS", "family": "scalar", "type": "chemistry"},
    36: {"name": "ALL_PLAYERS_CHEMISTRY_POINTS", "family": "scalar", "type": "all_players_chemistry_points"},
}
OBJECTIVE_KEYS = {
    "candidate_item_ids",
    "required_item_ids",
    "exclude_item_ids",
    "max_tradeable_value",
    "max_item_overall",
}


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class SbcService:
    def __init__(self, runtime, catalog, price_client=None):
        self.runtime = runtime
        self.catalog = catalog
        self.price_client = price_client or FutggPriceClient()

    def capture_sets(self, payload, include_raw=True):
        observed_at = utc_now()
        sets = [self._normalize_set(value, observed_at) for value in payload.get("sets") or []]
        self.runtime.upsert_sbc_sets(sets)
        return self._present(
            {"count": len(sets), "sets": sets, "observed_at": observed_at},
            include_raw,
        )

    def capture_challenges(self, payload, include_raw=True):
        observed_at = utc_now()
        normalized_set = self._normalize_set(payload["set"], observed_at)
        challenges = [
            self._retain_cached_slot_contract(
                self._normalize_challenge(value, normalized_set["set_id"], observed_at)
            )
            for value in payload.get("challenges") or []
        ]
        self.runtime.upsert_sbc_sets([normalized_set])
        self.runtime.upsert_sbc_challenges(challenges)
        return self._present(
            {
                "set": normalized_set,
                "count": len(challenges),
                "challenges": challenges,
                "observed_at": observed_at,
            },
            include_raw,
        )

    def capture_challenge(self, payload, include_raw=True):
        observed_at = utc_now()
        normalized_set = self._normalize_set(payload["set"], observed_at)
        challenge = self._retain_cached_slot_contract(
            self._normalize_challenge(
                payload["challenge"], normalized_set["set_id"], observed_at
            )
        )
        self.runtime.upsert_sbc_sets([normalized_set])
        self.runtime.upsert_sbc_challenges([challenge])
        return self._present(
            {"set": normalized_set, "challenge": challenge}, include_raw
        )

    def query(self, *, set_id=None, challenge_id=None, include_raw=False):
        result = self.runtime.query_sbcs(set_id=set_id, challenge_id=challenge_id)
        if challenge_id is not None:
            challenge = result.get("challenge")
            if challenge is not None and str(challenge.get("set_id")) != str(set_id):
                challenge = None
            result = {"challenge": challenge}
        return self._present(result, include_raw)

    def solve(
        self,
        set_id,
        challenge_id,
        objective=None,
        max_solutions=5,
        *,
        purchase_budget=0,
        reserved_item_ids=None,
    ):
        started_at = time.monotonic()
        objective = self._normalize_objective(objective)
        challenge = self.runtime.get_sbc_challenge(challenge_id)
        if challenge is None or str(challenge.get("set_id")) != str(set_id):
            raise FC27Error(
                "SBC_CHALLENGE_NOT_FOUND",
                f"SBC challenge {challenge_id} in set {set_id} is not cached.",
                recovery="Call FC27:sbc_refresh with set_id and challenge_id, then retry.",
            )
        unsupported = challenge["unsupported_constraints"]
        if unsupported:
            raise FC27Error(
                "SBC_SCHEMA_UNSUPPORTED",
                "The challenge contains constraints that cannot be validated locally.",
                recovery="Inspect unsupported_constraints and implement those requirement types before solving.",
                details={"unsupported_constraints": unsupported},
            )
        player_count = self._player_count(challenge)
        purchase_budget = self._normalize_purchase_budget(
            purchase_budget, player_count
        )
        deadline = started_at + min(600.0, 120.0 + 45.0 * purchase_budget)
        items, automatic_exclusions = self._candidate_items(
            objective, reserved_item_ids or []
        )
        items = self._apply_quality_prefilter(items, challenge["constraints"])
        if any(
            constraint["type"] in ("chemistry", "all_players_chemistry_points")
            for constraint in challenge["constraints"]
        ) and any(str(slot).startswith("ITEM_") for slot in challenge["slots"]):
            raise FC27Error(
                "SBC_SCHEMA_UNSUPPORTED",
                "Chemistry solving requires exact EA position metadata for every fillable slot.",
                recovery="Refresh the active challenge after reloading the extension, then retry.",
            )
        self._validate_required_items(
            challenge, items, objective, automatic_exclusions["by_item_id"]
        )
        maximum_solutions = max(1, min(int(max_solutions), 10))
        club_started_at = time.monotonic()
        club_optimized, club_candidates = self._solve_domain(
            challenge,
            items,
            objective,
            maximum_solutions,
            deadline,
            exact_purchase_count=0,
        )
        club_elapsed = time.monotonic() - club_started_at
        solutions = []
        plans = []
        for candidate, selected, validation in club_candidates:
            validation["candidate_pool"] = {
                "eligible_count": len(items),
                "excluded_counts": automatic_exclusions["counts"],
            }
            solution = self._solution(challenge, selected, objective, validation)
            self.runtime.save_sbc_solution(solution)
            solutions.append(solution)
            plans.append(
                self._plan(
                    challenge,
                    selected,
                    validation,
                    candidate,
                    plan_type="club_only",
                    purchase_budget=0,
                    solution_id=solution["solution_id"],
                )
            )

        catalog_coverage = {
            "catalog_count": 0,
            "price_eligible_count": 0,
            "price_unavailable_count": 0,
            "modeled_market_count": 0,
            "generated_column_count": 0,
            "max_modeled_domain_count": len(items),
            "branch_count": 0,
            "price_source": None,
        }
        budget_analysis = {
            "0": self._budget_summary(
                [club_optimized],
                club_candidates,
                purchase_budget=0,
                branch_count=1,
                generated_column_count=0,
                modeled_domain_count=len(items),
                elapsed_seconds=club_elapsed,
            )
        }
        planner_results = [club_optimized]
        previous_frontier = club_candidates
        if purchase_budget > 0:
            club_rating_cap = None
            if club_candidates:
                club_rating_cap = club_candidates[0][2]["metrics"]["max_overall"]
            market_items, catalog_coverage = self._market_candidates(
                challenge, objective, player_count, club_rating_cap
            )
            generated_market_ids = set()
            maximum_modeled_domain = len(items)
            total_branch_count = 0
            for current_budget in range(1, purchase_budget + 1):
                level_started_at = time.monotonic()
                level_deadline = min(deadline, level_started_at + 20.0)
                branches = previous_frontier[:3] or [None]
                level_candidates = []
                level_results = []
                level_generated_ids = set()
                level_modeled_domain = len(items)
                level_branch_count = 0
                for branch_index, branch in enumerate(branches):
                    now = time.monotonic()
                    if now >= level_deadline:
                        break
                    anchors = branch[1] if branch is not None else items
                    fixed_market_items = [
                        row for row in anchors if row.get("source") == "market"
                    ]
                    fixed_item_ids = [
                        int(row["item_id"]) for row in fixed_market_items
                    ]
                    columns = self._residual_market_columns(
                        market_items,
                        challenge,
                        anchors,
                        current_budget,
                        excluded_card_ids={
                            int(row["card_ea_id"]) for row in fixed_market_items
                        },
                    )
                    level_generated_ids.update(
                        int(row["card_ea_id"]) for row in columns
                    )
                    generated_market_ids.update(level_generated_ids)
                    domain_by_item_id = {
                        int(row["item_id"]): row
                        for row in [*items, *fixed_market_items, *columns]
                    }
                    domain = list(domain_by_item_id.values())
                    maximum_modeled_domain = max(maximum_modeled_domain, len(domain))
                    level_modeled_domain = max(level_modeled_domain, len(domain))
                    local_candidates = (
                        self._local_replacement_candidates(
                            challenge,
                            anchors,
                            columns,
                            objective,
                            maximum_solutions,
                        )
                        if branch is not None
                        else []
                    )
                    if local_candidates:
                        optimized = {
                            "status": "LOCAL_OPTIMUM",
                            "complete": True,
                            "proof": {
                                "rating_optimality": "UNPROVEN",
                                "policy_optimality": "UNPROVEN",
                                "local_neighborhood": "single_market_replacement",
                                "frontier_complete": True,
                                "stages": [],
                            },
                            "solutions": [
                                candidate[0] for candidate in local_candidates
                            ],
                        }
                        candidates = local_candidates
                    else:
                        remaining_branches = len(branches) - branch_index
                        branch_deadline = min(
                            level_deadline,
                            now
                            + max(
                                5.0,
                                (level_deadline - now) / remaining_branches,
                            ),
                        )
                        hint = (
                            branch[0].get("slot_item_ids")
                            if branch is not None
                            else None
                        )
                        optimized, candidates = self._solve_domain(
                            challenge,
                            domain,
                            objective,
                            maximum_solutions,
                            branch_deadline,
                            exact_purchase_count=current_budget,
                            fixed_item_ids=fixed_item_ids,
                            hint_slot_item_ids=hint,
                        )
                    optimized, candidates = self._mark_realization_scope(
                        optimized,
                        candidates,
                        purchase_budget=current_budget,
                        branch_index=branch_index,
                        fixed_item_ids=fixed_item_ids,
                        generated_column_count=len(columns),
                        modeled_domain_count=len(domain),
                    )
                    level_results.append(optimized)
                    level_candidates.extend(candidates)
                    planner_results.append(optimized)
                    total_branch_count += 1
                    level_branch_count += 1

                previous_frontier = self._pareto_candidates(
                    level_candidates, maximum_solutions
                )
                level_elapsed = time.monotonic() - level_started_at
                budget_analysis[str(current_budget)] = self._budget_summary(
                    level_results,
                    previous_frontier,
                    purchase_budget=current_budget,
                    branch_count=level_branch_count,
                    generated_column_count=len(level_generated_ids),
                    modeled_domain_count=level_modeled_domain,
                    elapsed_seconds=level_elapsed,
                )
                for candidate, selected, validation in previous_frontier:
                    plans.append(
                        self._plan(
                            challenge,
                            selected,
                            validation,
                            candidate,
                            plan_type="hybrid_purchase",
                            purchase_budget=current_budget,
                        )
                    )
            catalog_coverage["generated_column_count"] = len(generated_market_ids)
            catalog_coverage["max_modeled_domain_count"] = maximum_modeled_domain
            catalog_coverage["branch_count"] = total_branch_count

        plans = self._deduplicate_plans(plans)
        if not plans:
            statuses = {value["status"] for value in planner_results}
            if "UNKNOWN_NO_SOLUTION_FOUND" in statuses:
                raise FC27Error(
                    "SBC_SOLVER_TIMEOUT",
                    "The SBC planner reached its shared deadline before finding a feasible squad.",
                    retryable=True,
                    recovery="Retry after reducing explicit exclusions or after refreshing the club mirror.",
                    details={"required_item_ids": objective["required_item_ids"]},
                )
            raise FC27Error(
                "SBC_NO_SOLUTION",
                "No owned or requested-purchase combination satisfies the normalized challenge and Agent objective.",
                recovery="Inspect required items and exclusions or acquire additional eligible cards.",
                details={
                    "solver_status": sorted(statuses),
                    "required_item_ids": objective["required_item_ids"],
                },
            )
        return {
            "challenge": challenge,
            "objective": objective,
            "requested_purchase_budget": purchase_budget,
            "candidate_pool": {
                "eligible_count": len(items),
                "excluded_counts": automatic_exclusions["counts"],
                **catalog_coverage,
            },
            "solver": {
                "engine": (
                    "or-tools-cp-sat"
                    if purchase_budget == 0
                    else "incremental-residual-planner"
                ),
                "fallback_engine": (
                    None if purchase_budget == 0 else "or-tools-cp-sat"
                ),
                "status": budget_analysis[str(purchase_budget)]["solver_status"],
                "scope": budget_analysis[str(purchase_budget)]["optimality_scope"],
            },
            "solution_count": len(solutions),
            "solutions": solutions,
            "plan_count": len(plans),
            "plans": plans,
            "budget_analysis": budget_analysis,
            "actions_performed": [],
        }

    def _solve_domain(
        self,
        challenge,
        items,
        objective,
        max_solutions,
        deadline,
        *,
        exact_purchase_count=None,
        fixed_item_ids=None,
        hint_slot_item_ids=None,
    ):
        player_count = self._player_count(challenge)
        if len(items) < player_count:
            return self._empty_optimizer_result("INFEASIBLE_PROVEN"), []
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return self._empty_optimizer_result("UNKNOWN_NO_SOLUTION_FOUND"), []
        optimized = SbcOptimizer(time_limit_seconds=remaining).solve(
            challenge,
            items,
            objective,
            max_solutions,
            exact_purchase_count=exact_purchase_count,
            fixed_item_ids=fixed_item_ids,
            hint_slot_item_ids=hint_slot_item_ids,
        )
        by_item_id = {int(row["item_id"]): row for row in items}
        candidates = []
        for candidate in optimized["solutions"]:
            ordered_item_ids = candidate.get("slot_item_ids") or candidate["item_ids"]
            selected = [by_item_id[item_id] for item_id in ordered_item_ids]
            validation = self.validate(challenge, selected, objective)
            if not validation["valid"]:
                raise FC27Error(
                    "SBC_SOLVER_VALIDATION_FAILED",
                    "The optimized SBC candidate failed independent local validation.",
                    recovery="Inspect the returned validation failure and correct the solver model before retrying.",
                    details={
                        "item_ids": candidate["item_ids"],
                        "validation": validation,
                    },
                )
            validation["solver"] = {
                "engine": "or-tools-cp-sat",
                "status": candidate["status"],
                "objective_value": candidate["objective_value"],
                "objective_components": candidate["objective_components"],
                "proof": candidate["proof"],
                "wall_time_seconds": candidate["wall_time_seconds"],
            }
            candidates.append((candidate, selected, validation))
        return optimized, candidates

    @staticmethod
    def _mark_realization_scope(
        optimized,
        candidates,
        *,
        purchase_budget,
        branch_index,
        fixed_item_ids,
        generated_column_count,
        modeled_domain_count,
    ):
        scope = {
            "type": "residual_realization_branch",
            "purchase_budget": purchase_budget,
            "branch_index": branch_index,
            "fixed_market_item_ids": list(fixed_item_ids),
            "generated_column_count": generated_column_count,
            "modeled_domain_count": modeled_domain_count,
        }
        marked = []
        for candidate, selected, validation in candidates:
            status = (
                "OPTIMAL_WITHIN_REALIZATION_SET"
                if candidate["status"] == "OPTIMAL_PROVEN"
                else "LOCAL_OPTIMUM"
                if candidate["status"] in ("LOCAL_FEASIBLE", "LOCAL_OPTIMUM")
                else "VALIDATED_FEASIBLE"
            )
            candidate = {
                **candidate,
                "status": status,
                "proof": {
                    **candidate["proof"],
                    "optimality_scope": scope,
                },
            }
            validation = {
                **validation,
                "solver": {
                    **validation["solver"],
                    "status": status,
                    "proof": candidate["proof"],
                },
            }
            marked.append((candidate, selected, validation))
        if marked:
            statuses = {candidate[0]["status"] for candidate in marked}
            if statuses == {"OPTIMAL_WITHIN_REALIZATION_SET"}:
                scoped_status = "OPTIMAL_WITHIN_REALIZATION_SET"
            elif statuses == {"LOCAL_OPTIMUM"}:
                scoped_status = "LOCAL_OPTIMUM"
            else:
                scoped_status = "VALIDATED_FEASIBLE"
        else:
            scoped_status = optimized["status"]
        return {
            **optimized,
            "status": scoped_status,
            "proof": {
                **optimized["proof"],
                "optimality_scope": scope,
            },
            "solutions": [value[0] for value in marked],
        }, marked

    def _local_replacement_candidates(
        self,
        challenge,
        anchor_items,
        market_columns,
        objective,
        limit,
    ):
        required_item_ids = {
            int(item_id) for item_id in objective.get("required_item_ids") or []
        }
        replaceable_indexes = [
            index
            for index, row in enumerate(anchor_items)
            if row.get("source") != "market"
            and int(row["item_id"]) not in required_item_ids
        ]
        candidates = []
        for market_item in market_columns:
            for item_index in replaceable_indexes:
                selected = list(anchor_items)
                selected[item_index] = market_item
                validation = self.validate(challenge, selected, objective)
                if not validation["valid"]:
                    continue
                components = self._objective_components(selected)
                candidate = {
                    "status": "LOCAL_FEASIBLE",
                    "item_ids": sorted(int(row["item_id"]) for row in selected),
                    "slot_item_ids": [int(row["item_id"]) for row in selected],
                    "objective_value": 0,
                    "objective_components": components,
                    "proof": {
                        "rating_optimality": "UNPROVEN",
                        "policy_optimality": "UNPROVEN",
                        "pareto_status": "NONDOMINATED_WITHIN_RETURNED",
                        "frontier_complete": False,
                        "local_neighborhood": "single_market_replacement",
                        "stages": [],
                    },
                    "wall_time_seconds": 0.0,
                }
                validation["solver"] = {
                    "engine": "local-neighborhood",
                    "status": "LOCAL_FEASIBLE",
                    "objective_value": 0,
                    "objective_components": components,
                    "proof": candidate["proof"],
                    "wall_time_seconds": 0.0,
                }
                candidates.append((candidate, selected, validation))
        return self._pareto_candidates(candidates, limit)

    @staticmethod
    def _objective_components(items):
        rating_vector = sorted(
            (int(row["overall"]) for row in items), reverse=True
        )
        return {
            "rating_vector": rating_vector,
            "max_overall": max(rating_vector, default=0),
            "purchase_count": sum(
                1 for row in items if row.get("source") == "market"
            ),
            "purchase_value": sum(
                int(row.get("purchase_price") or 0) for row in items
            ),
            "tradeable_item_count": sum(
                1
                for row in items
                if row.get("source") != "market" and row.get("tradeable")
            ),
            "tradeable_value": sum(
                int(row.get("tradeable_value") or 0) for row in items
            ),
            "total_overall": sum(rating_vector),
            "item_ids": [int(row["item_id"]) for row in items],
        }

    @classmethod
    def _pareto_candidates(cls, candidates, limit):
        by_items = {}
        for value in candidates:
            key = tuple(sorted(int(row["item_id"]) for row in value[1]))
            current = by_items.get(key)
            if current is None or cls._candidate_sort_key(value) < cls._candidate_sort_key(
                current
            ):
                by_items[key] = value
        unique = list(by_items.values())
        frontier = [
            candidate
            for candidate in unique
            if not any(
                other is not candidate and cls._dominates(other, candidate)
                for other in unique
            )
        ]
        return sorted(frontier, key=cls._candidate_sort_key)[:limit]

    @staticmethod
    def _dominates(left, right):
        left_components = left[0]["objective_components"]
        right_components = right[0]["objective_components"]
        left_values = (
            tuple(left[2]["metrics"]["rating_vector"]),
            int(left_components["purchase_value"]),
            int(left_components["tradeable_value"]),
            int(left_components["tradeable_item_count"]),
            -int(left[2]["metrics"]["chemistry"]),
        )
        right_values = (
            tuple(right[2]["metrics"]["rating_vector"]),
            int(right_components["purchase_value"]),
            int(right_components["tradeable_value"]),
            int(right_components["tradeable_item_count"]),
            -int(right[2]["metrics"]["chemistry"]),
        )
        return all(
            left_value <= right_value
            for left_value, right_value in zip(left_values, right_values)
        ) and any(
            left_value < right_value
            for left_value, right_value in zip(left_values, right_values)
        )

    @staticmethod
    def _candidate_sort_key(value):
        candidate, _, validation = value
        components = candidate["objective_components"]
        return (
            validation["metrics"]["rating_vector"],
            components["purchase_value"],
            components["tradeable_value"],
            components["tradeable_item_count"],
            -validation["metrics"]["chemistry"],
            tuple(candidate["item_ids"]),
        )

    @staticmethod
    def _empty_optimizer_result(status):
        return {
            "status": status,
            "complete": status == "INFEASIBLE_PROVEN",
            "proof": {
                "rating_optimality": "UNPROVEN",
                "policy_optimality": "UNPROVEN",
                "stages": [],
            },
            "solutions": [],
        }

    def _market_candidates(
        self, challenge, objective, player_count, proven_feasible_rating_cap
    ):
        max_overall = objective.get("max_item_overall")
        if proven_feasible_rating_cap is not None:
            max_overall = (
                min(int(max_overall), int(proven_feasible_rating_cap))
                if max_overall is not None
                else int(proven_feasible_rating_cap)
            )
        rows = self.catalog.sbc_catalog_candidates(
            challenge["constraints"], max_overall
        )
        card_ids = [int(row["card_ea_id"]) for row in rows]
        if not card_ids:
            return [], {
                "catalog_count": 0,
                "price_eligible_count": 0,
                "price_unavailable_count": 0,
                "modeled_market_count": 0,
                "price_source": None,
            }
        snapshot = self.price_client.current_prices(card_ids)
        persistence = self.runtime.record_reference_prices(snapshot["prices"])
        account = self.runtime.account_summary() or {}
        platform = str(account.get("platform") or "pc").lower()
        prices = {
            int(row["card_ea_id"]): row
            for row in snapshot["prices"]
            if row.get("platform") == platform
            and row.get("status") == "market_or_normal"
            and row.get("price") is not None
            and not row.get("is_extinct")
        }
        market_items = []
        for row in rows:
            card_id = int(row["card_ea_id"])
            price = prices.get(card_id)
            if price is None:
                continue
            market_items.append(
                {
                    **row,
                    "item_id": -card_id,
                    "source": "market",
                    "tradeable": False,
                    "protected": False,
                    "loan_uses_remaining": -1,
                    "tradeable_value": 0,
                    "purchase_price": int(price["price"]),
                    "price_observed_at": price["observed_at"],
                    "price_status": price["status"],
                    "platform": platform,
                }
            )
        modeled = self._reduce_market_candidates(market_items, player_count)
        return modeled, {
            "catalog_count": len(rows),
            "price_eligible_count": len(market_items),
            "price_unavailable_count": len(rows) - len(market_items),
            "modeled_market_count": len(modeled),
            "price_source": {
                "name": "FUT.GG",
                "observed_at": snapshot["observed_at"],
                "manifest_version": snapshot["manifest_version"],
                "hashes": snapshot["hashes"],
                "platform": platform,
                "persisted_changes": persistence,
            },
        }

    @staticmethod
    def _reduce_market_candidates(items, player_count):
        groups = {}
        for row in items:
            key = (
                int(row["overall"]),
                row.get("quality"),
                row.get("club_id"),
                row.get("league_id"),
                row.get("nation_id"),
                tuple(sorted(str(value).upper() for value in row.get("positions") or [])),
            )
            by_base = groups.setdefault(key, {})
            base_id = int(row["base_player_ea_id"])
            current = by_base.get(base_id)
            if current is None or (
                int(row["purchase_price"]), int(row["card_ea_id"])
            ) < (
                int(current["purchase_price"]), int(current["card_ea_id"])
            ):
                by_base[base_id] = row
        reduced = []
        for by_base in groups.values():
            reduced.extend(
                sorted(
                    by_base.values(),
                    key=lambda row: (
                        int(row["purchase_price"]),
                        int(row["card_ea_id"]),
                    ),
                )[:player_count]
            )
        reduced.sort(
            key=lambda row: (
                int(row["overall"]),
                int(row["purchase_price"]),
                int(row["card_ea_id"]),
            )
        )
        return reduced

    @staticmethod
    def _residual_market_columns(
        items,
        challenge,
        anchor_items,
        purchase_budget,
        *,
        excluded_card_ids,
    ):
        ordered = sorted(
            (
                row
                for row in items
                if int(row["card_ea_id"]) not in excluded_card_ids
            ),
            key=lambda row: (
                int(row["overall"]),
                int(row["purchase_price"]),
                int(row["card_ea_id"]),
            ),
        )
        positions = sorted({str(value).upper() for value in challenge["slots"]})
        position_index = {
            position: index for index, position in enumerate(positions)
        }
        explicit = {
            "club_id": set(),
            "league_id": set(),
            "nation_id": set(),
        }
        for constraint in challenge["constraints"]:
            mapping = {
                "specific_club_count": ("club_id", "club_ids"),
                "specific_league_count": ("league_id", "league_ids"),
                "specific_nation_count": ("nation_id", "nation_ids"),
            }.get(constraint["type"])
            if mapping is None:
                continue
            attribute, values_key = mapping
            explicit[attribute].update(
                int(value) for value in constraint[values_key]
            )

        anchor_counts = {
            attribute: Counter(
                row.get(attribute)
                for row in anchor_items
                if row.get(attribute) is not None
            )
            for attribute in ("club_id", "league_id", "nation_id")
        }
        grouped = defaultdict(list)
        for row in ordered:
            position_mask = 0
            for position in row.get("positions") or []:
                index = position_index.get(str(position).upper())
                if index is not None:
                    position_mask |= 1 << index
            explicit_hits = tuple(
                int(row.get(attribute) in explicit[attribute])
                for attribute in ("club_id", "league_id", "nation_id")
            )
            shared = tuple(
                min(anchor_counts[attribute].get(row.get(attribute), 0), 7)
                for attribute in ("club_id", "league_id", "nation_id")
            )
            role_key = (
                int(row["overall"]),
                row.get("quality"),
                explicit_hits,
                row.get("club_id") if shared[0] else None,
                row.get("league_id") if shared[1] else None,
                row.get("nation_id") if shared[2] else None,
                position_mask,
            )
            shared_strength = shared[0] * 3 + shared[1] * 2 + shared[2]
            grouped[role_key].append((shared_strength, row))

        relevant = []
        representatives_per_role = 2 if purchase_budget == 1 else 3
        for values in grouped.values():
            values.sort(
                key=lambda value: (
                    -value[0],
                    int(value[1]["purchase_price"]),
                    int(value[1]["card_ea_id"]),
                )
            )
            relevant.extend(row for _, row in values[:representatives_per_role])

        relevant.sort(
            key=lambda row: (
                int(row["overall"]),
                int(row["purchase_price"]),
                -sum(
                    anchor_counts[attribute].get(row.get(attribute), 0)
                    for attribute in ("club_id", "league_id", "nation_id")
                ),
                int(row["card_ea_id"]),
            )
        )
        selected = {}

        def retain(rows, limit):
            for row in rows[:limit]:
                selected[int(row["card_ea_id"])] = row

        first_level = purchase_budget == 1
        retain(relevant, 180 if first_level else 140)
        by_attribute = {
            attribute: defaultdict(list)
            for attribute in ("club_id", "league_id", "nation_id")
        }
        for row in relevant:
            for attribute in by_attribute:
                by_attribute[attribute][row.get(attribute)].append(row)

        explicit_limit = 80 if first_level else 60
        for attribute, values in explicit.items():
            for value in values:
                retain(by_attribute[attribute].get(value, []), explicit_limit)

        limits = (
            {"club_id": 8, "league_id": 24, "nation_id": 16}
            if first_level
            else {"club_id": 6, "league_id": 12, "nation_id": 10}
        )
        identity_limits = {"club_id": 4, "league_id": 4, "nation_id": 6}
        for attribute, counts in anchor_counts.items():
            for value, _ in counts.most_common(identity_limits[attribute]):
                retain(
                    by_attribute[attribute].get(value, []),
                    limits[attribute],
                )

        return sorted(
            selected.values(),
            key=lambda row: (
                int(row["overall"]),
                int(row["purchase_price"]),
                int(row["card_ea_id"]),
            ),
        )

    @staticmethod
    def _budget_summary(
        optimized_results,
        candidates,
        *,
        purchase_budget,
        branch_count,
        generated_column_count,
        modeled_domain_count,
        elapsed_seconds,
    ):
        best = candidates[0][2]["metrics"] if candidates else None
        statuses = [result["status"] for result in optimized_results]
        if candidates and purchase_budget == 0:
            solver_status = (
                "OPTIMAL_PROVEN"
                if all(
                    candidate[0]["status"] == "OPTIMAL_PROVEN"
                    for candidate in candidates
                )
                else "FEASIBLE_UNPROVEN"
            )
        elif candidates:
            candidate_statuses = {candidate[0]["status"] for candidate in candidates}
            if candidate_statuses <= {
                "OPTIMAL_PROVEN",
                "OPTIMAL_WITHIN_REALIZATION_SET",
            }:
                solver_status = "OPTIMAL_WITHIN_REALIZATION_SET"
            elif candidate_statuses == {"LOCAL_OPTIMUM"}:
                solver_status = "LOCAL_OPTIMUM"
            else:
                solver_status = "VALIDATED_FEASIBLE"
        elif "UNKNOWN_NO_SOLUTION_FOUND" in statuses:
            solver_status = "SEARCH_EXHAUSTED_WITHOUT_PLAN"
        elif statuses and all(status == "INFEASIBLE_PROVEN" for status in statuses):
            solver_status = "NO_REALIZATION_FOR_LEVEL"
        else:
            solver_status = statuses[0] if statuses else "NOT_RUN"
        return {
            "purchase_budget": purchase_budget,
            "solver_status": solver_status,
            "optimality_scope": (
                "eligible_owned_items"
                if purchase_budget == 0
                else "bounded_residual_realization_branches"
            ),
            "candidate_count": len(candidates),
            "branch_count": branch_count,
            "generated_column_count": generated_column_count,
            "modeled_domain_count": modeled_domain_count,
            "elapsed_seconds": round(float(elapsed_seconds), 3),
            "best_rating_vector": best["rating_vector"] if best else None,
            "best_chemistry": best["chemistry"] if best else None,
        }

    def _plan(
        self,
        challenge,
        items,
        validation,
        candidate,
        *,
        plan_type,
        purchase_budget,
        solution_id=None,
    ):
        owned_item_ids = [
            int(row["item_id"]) for row in items if row.get("source") != "market"
        ]
        purchase_targets = [
            {
                "card_ea_id": int(row["card_ea_id"]),
                "base_player_ea_id": int(row["base_player_ea_id"]),
                "name": row.get("card_name") or row.get("common_name"),
                "estimated_price": int(row["purchase_price"]),
                "price_observed_at": row.get("price_observed_at"),
                "platform": row.get("platform"),
            }
            for row in items
            if row.get("source") == "market"
        ]
        slot_indices = [int(value) for value in challenge.get("slot_indices") or []]
        slots = []
        for index, row in enumerate(items):
            slots.append(
                {
                    "slot_index": slot_indices[index],
                    "position": challenge["slots"][index],
                    "source": row.get("source", "owned"),
                    "item_id": (
                        int(row["item_id"])
                        if row.get("source") != "market"
                        else None
                    ),
                    "card_ea_id": int(row["card_ea_id"]),
                    "base_player_ea_id": int(row["base_player_ea_id"]),
                    "name": row.get("card_name") or row.get("common_name"),
                    "overall": int(row["overall"]),
                    "in_position": validation["metrics"]["in_position"][index],
                    "chemistry": validation["metrics"]["player_chemistry"][index],
                }
            )
        identity = canonical_json(
            {
                "challenge_id": challenge["challenge_id"],
                "slots": [
                    {
                        "slot_index": row["slot_index"],
                        "source": row["source"],
                        "item_id": row["item_id"],
                        "card_ea_id": row["card_ea_id"],
                    }
                    for row in slots
                ],
            }
        )
        account = self.runtime.account_summary() or {}
        return {
            "plan_id": "plan-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
            "plan_type": plan_type,
            "purchase_budget": purchase_budget,
            "purchase_count": len(purchase_targets),
            "owned_count": len(owned_item_ids),
            "owned_item_ids": owned_item_ids,
            "purchase_targets": purchase_targets,
            "estimated_purchase_cost": sum(
                row["estimated_price"] for row in purchase_targets
            ),
            "owned_tradeable_count": candidate["objective_components"][
                "tradeable_item_count"
            ],
            "owned_opportunity_cost": candidate["objective_components"][
                "tradeable_value"
            ],
            "metrics": validation["metrics"],
            "constraint_results": validation["constraint_results"],
            "slots": slots,
            "solver_status": candidate["status"],
            "optimality_scope": {
                "objective": "complete_descending_rating_vector",
                "candidate_domain": {
                    "club_only": "eligible_owned_items",
                }.get(plan_type, "bounded_residual_realization_branch"),
                "purchase_count": purchase_budget,
            },
            "proof": candidate["proof"],
            "snapshot_refs": {
                "inventory_sync_id": account.get("last_full_sync_id"),
                "challenge_observed_at": challenge.get("observed_at"),
                "price_observed_at": sorted(
                    {
                        row["price_observed_at"]
                        for row in purchase_targets
                        if row.get("price_observed_at")
                    }
                ),
            },
            "market_verification_required": bool(purchase_targets),
            "executable": not purchase_targets,
            "solution_id": solution_id,
        }

    @staticmethod
    def _deduplicate_plans(plans):
        by_structure = {}
        for plan in plans:
            key = tuple(
                (
                    row["slot_index"],
                    row["source"],
                    row["item_id"],
                    row["card_ea_id"],
                )
                for row in plan["slots"]
            )
            current = by_structure.get(key)
            if current is None or plan["purchase_budget"] < current[
                "purchase_budget"
            ]:
                by_structure[key] = plan
        return sorted(
            by_structure.values(),
            key=lambda plan: (
                plan["metrics"]["rating_vector"],
                plan["purchase_count"],
                plan["owned_tradeable_count"],
                plan["owned_opportunity_cost"],
                plan["estimated_purchase_cost"],
                plan["plan_id"],
            ),
        )

    @classmethod
    def _present(cls, value, include_raw):
        if include_raw:
            return value
        if isinstance(value, dict):
            return {
                key: cls._present(item, include_raw)
                for key, item in value.items()
                if key != "raw"
            }
        if isinstance(value, list):
            return [cls._present(item, include_raw) for item in value]
        return value

    def validate_solution(
        self, solution_id, expected_sync_id=None, *, reserved_item_ids=None
    ):
        solution = self.runtime.get_sbc_solution(solution_id)
        if solution is None:
            raise FC27Error("SBC_SOLUTION_NOT_FOUND", f"SBC solution {solution_id} was not found.")
        account = self.runtime.account_summary()
        if expected_sync_id is not None and account["last_full_sync_id"] != expected_sync_id:
            raise FC27Error(
                "STALE_CLUB_STATE",
                f"Expected sync {expected_sync_id}, current complete sync is {account['last_full_sync_id']}.",
                retryable=True,
                recovery="Sync the club and regenerate the SBC solution.",
            )
        challenge = self.runtime.get_sbc_challenge(solution["challenge_id"])
        expected_slot_indices = [
            int(value) for value in challenge.get("slot_indices") or []
        ]
        solution_slot_indices = [
            int(value["slot_index"]) for value in solution.get("slots") or []
        ]
        if expected_slot_indices != solution_slot_indices:
            raise FC27Error(
                "SBC_SLOT_LAYOUT_CHANGED",
                "The persisted solution no longer matches the challenge fillable slots.",
                retryable=True,
                recovery="Refresh the challenge and generate a new SBC solution.",
                details={
                    "expected_slot_indices": expected_slot_indices,
                    "solution_slot_indices": solution_slot_indices,
                },
            )
        item_rows = self.runtime.items_by_ids(solution["item_ids"])
        facts = self.catalog.sbc_item_facts([row["card_ea_id"] for row in item_rows])
        items = [{**row, **facts.get(row["card_ea_id"], {})} for row in item_rows]
        validation = self.validate(
            challenge,
            items,
            solution["objective"],
            reserved_item_ids=reserved_item_ids,
        )
        if not validation["valid"]:
            raise FC27Error(
                "SBC_NOT_ELIGIBLE",
                "The persisted solution no longer passes local validation.",
                recovery="Sync the club and generate a new solution.",
                details=validation,
            )
        return {"solution": solution, "challenge": challenge, "validation": validation}

    def validate(self, challenge, items, objective=None, *, reserved_item_ids=None):
        objective = objective or {}
        reserved_item_ids = {int(value) for value in reserved_item_ids or []}
        failures = []
        item_ids = [int(row["item_id"]) for row in items]
        player_count = self._player_count(challenge)
        if len(item_ids) != player_count:
            failures.append(
                {"type": "slot_count", "expected": player_count, "actual": len(item_ids)}
            )
        if len(item_ids) != len(set(item_ids)):
            failures.append({"type": "duplicate_item_ids"})
        base_player_ids = [
            int(row["base_player_ea_id"])
            for row in items
            if row.get("base_player_ea_id") is not None
        ]
        duplicate_base_player_ids = sorted(
            base_player_id
            for base_player_id in set(base_player_ids)
            if base_player_ids.count(base_player_id) > 1
        )
        if duplicate_base_player_ids:
            failures.append(
                {
                    "type": "duplicate_base_player_ids",
                    "base_player_ea_ids": duplicate_base_player_ids,
                }
            )
        for row in items:
            if row.get("protected"):
                failures.append({"type": "protected_item", "item_id": row["item_id"]})
            if row.get("loan_uses_remaining") not in (None, -1):
                failures.append({"type": "loan_item", "item_id": row["item_id"]})
            if row.get("is_evolution"):
                failures.append({"type": "evolution_item", "item_id": row["item_id"]})
            elif row.get("is_special"):
                failures.append({"type": "special_item", "item_id": row["item_id"]})
            if int(row["item_id"]) in reserved_item_ids:
                failures.append({"type": "active_squad_item", "item_id": row["item_id"]})
        tradeable_value = sum(int(row.get("tradeable_value") or 0) for row in items)
        limit = objective.get("max_tradeable_value")
        if limit is not None and tradeable_value > int(limit):
            failures.append({"type": "tradeable_value", "limit": int(limit), "actual": tradeable_value})
        required_item_ids = [int(value) for value in objective.get("required_item_ids", [])]
        missing_required = sorted(set(required_item_ids) - set(item_ids))
        if missing_required:
            failures.append(
                {"type": "required_item_ids", "missing_item_ids": missing_required}
            )
        metrics = self._metrics(items, challenge["slots"])
        constraint_results = []
        for constraint in challenge["constraints"]:
            actual = self._constraint_actual(constraint, items, metrics)
            passed = self._compare(
                actual, constraint["operator"], constraint["value"]
            )
            constraint_results.append(
                {
                    "source_key": constraint.get("source_key"),
                    "source_name": constraint.get("source_name"),
                    "type": constraint["type"],
                    "operator": constraint["operator"],
                    "required": constraint["value"],
                    "actual": actual,
                    "pass": passed,
                }
            )
            if not passed:
                failures.append({"type": "constraint", "constraint": constraint, "actual": actual})
        return {
            "valid": not failures,
            "item_ids": item_ids,
            "tradeable_value": tradeable_value,
            "metrics": metrics,
            "constraint_results": constraint_results,
            "failures": failures,
        }

    @staticmethod
    def _normalize_purchase_budget(value, player_count):
        if isinstance(value, bool) or not isinstance(value, int):
            raise FC27Error(
                "SBC_PURCHASE_BUDGET_INVALID",
                "purchase_budget must be an integer.",
                recovery=f"Use an integer from 0 through {player_count}.",
            )
        if not 0 <= value <= player_count:
            raise FC27Error(
                "SBC_PURCHASE_BUDGET_INVALID",
                f"purchase_budget {value} is outside this challenge's 0 through {player_count} range.",
                recovery=f"Use an integer from 0 through {player_count}.",
            )
        return value

    @staticmethod
    def _normalize_objective(objective):
        value = dict(objective or {})
        unknown = sorted(set(value) - OBJECTIVE_KEYS)
        if unknown:
            raise FC27Error(
                "SBC_OBJECTIVE_INVALID",
                "The SBC objective contains unsupported fields.",
                recovery=f"Use only: {', '.join(sorted(OBJECTIVE_KEYS))}.",
                details={"unknown_fields": unknown},
            )

        def item_ids(key, *, reject_duplicates=False):
            source = value.get(key)
            if source is None:
                return []
            if not isinstance(source, list):
                raise FC27Error(
                    "SBC_OBJECTIVE_INVALID", f"{key} must be an array of item IDs."
                )
            normalized = []
            for raw in source:
                if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
                    raise FC27Error(
                        "SBC_OBJECTIVE_INVALID",
                        f"{key} must contain positive integer item IDs.",
                    )
                normalized.append(int(raw))
            if reject_duplicates and len(normalized) != len(set(normalized)):
                raise FC27Error(
                    "SBC_OBJECTIVE_INVALID",
                    f"{key} must not contain duplicate item IDs.",
                )
            return list(dict.fromkeys(normalized))

        candidate_present = "candidate_item_ids" in value
        candidate_item_ids = item_ids("candidate_item_ids")
        if candidate_present and not candidate_item_ids:
            raise FC27Error(
                "SBC_OBJECTIVE_INVALID",
                "candidate_item_ids must contain at least one item when supplied.",
            )
        required_item_ids = item_ids("required_item_ids", reject_duplicates=True)
        exclude_item_ids = item_ids("exclude_item_ids")
        required = set(required_item_ids)
        excluded = set(exclude_item_ids)
        conflicts = sorted(required & excluded)
        if conflicts:
            raise FC27Error(
                "SBC_OBJECTIVE_CONFLICT",
                "Required SBC items cannot also be excluded.",
                recovery="Remove the conflicting item IDs from required_item_ids or exclude_item_ids.",
                details={"item_ids": conflicts},
            )
        if candidate_present:
            outside = sorted(required - set(candidate_item_ids))
            if outside:
                raise FC27Error(
                    "SBC_OBJECTIVE_CONFLICT",
                    "Every required SBC item must be included in candidate_item_ids.",
                    recovery="Add the reported item IDs to candidate_item_ids or remove the candidate restriction.",
                    details={"item_ids": outside},
                )
        max_tradeable_value = value.get("max_tradeable_value")
        if max_tradeable_value is not None and (
            isinstance(max_tradeable_value, bool)
            or not isinstance(max_tradeable_value, int)
            or max_tradeable_value < 0
        ):
            raise FC27Error(
                "SBC_OBJECTIVE_INVALID",
                "max_tradeable_value must be a non-negative integer.",
            )
        max_item_overall = value.get("max_item_overall")
        if max_item_overall is not None and (
            isinstance(max_item_overall, bool)
            or not isinstance(max_item_overall, int)
            or not 1 <= max_item_overall <= 99
        ):
            raise FC27Error(
                "SBC_OBJECTIVE_INVALID",
                "max_item_overall must be an integer from 1 through 99.",
            )
        return {
            "candidate_item_ids": candidate_item_ids if candidate_present else None,
            "required_item_ids": required_item_ids,
            "exclude_item_ids": exclude_item_ids,
            "max_tradeable_value": max_tradeable_value,
            "max_item_overall": max_item_overall,
        }

    def _validate_required_items(
        self, challenge, items, objective, automatic_exclusions
    ):
        required_item_ids = objective["required_item_ids"]
        player_count = self._player_count(challenge)
        if len(required_item_ids) > player_count:
            raise FC27Error(
                "SBC_OBJECTIVE_CONFLICT",
                "The objective requires more items than the challenge has squad slots.",
                details={
                    "required_items": len(required_item_ids),
                    "squad_slots": player_count,
                },
            )
        if not required_item_ids:
            return
        current_rows = {
            int(row["item_id"]): row
            for row in self.runtime.items_by_ids(required_item_ids)
        }
        missing = sorted(set(required_item_ids) - set(current_rows))
        if missing:
            raise FC27Error(
                "SBC_REQUIRED_ITEM_MISSING",
                "One or more required SBC items are not present in the current club mirror.",
                retryable=True,
                recovery="Run FC27:sync_club, resolve current item IDs with FC27:club_query, and retry.",
                details={"item_ids": missing},
            )
        reasons = []
        allowed_locations = {"club", "storage", "unassigned"}
        for item_id in required_item_ids:
            row = current_rows[item_id]
            if row["location"] not in allowed_locations:
                reasons.append({"item_id": item_id, "reason": "location", "location": row["location"]})
            if row["protected"]:
                reasons.append({"item_id": item_id, "reason": "protected"})
            if row["loan_uses_remaining"] not in (None, -1):
                reasons.append({"item_id": item_id, "reason": "loan"})
        eligible_by_id = {int(row["item_id"]): row for row in items}
        for item_id in required_item_ids:
            if item_id not in eligible_by_id and not any(
                reason["item_id"] == item_id for reason in reasons
            ):
                reasons.append(
                    {
                        "item_id": item_id,
                        "reason": automatic_exclusions.get(
                            item_id, "catalog_or_objective_filter"
                        ),
                    }
                )
        if reasons:
            raise FC27Error(
                "SBC_REQUIRED_ITEM_INELIGIBLE",
                "One or more required SBC items are ineligible for this solve.",
                recovery="Choose current unprotected non-loan items that satisfy the candidate and overall limits.",
                details={"items": reasons},
            )
        required_tradeable_value = sum(
            int(eligible_by_id[item_id].get("tradeable_value") or 0)
            for item_id in required_item_ids
        )
        limit = objective.get("max_tradeable_value")
        if limit is not None and required_tradeable_value > int(limit):
            raise FC27Error(
                "SBC_OBJECTIVE_CONFLICT",
                "Required SBC items already exceed max_tradeable_value.",
                recovery="Raise max_tradeable_value or choose lower-value required items.",
                details={
                    "required_tradeable_value": required_tradeable_value,
                    "max_tradeable_value": int(limit),
                },
            )

    def _candidate_items(self, objective, reserved_item_ids):
        reserved_item_ids = {int(value) for value in reserved_item_ids}
        rows = self.runtime.sbc_candidate_items(
            candidate_item_ids=objective.get("candidate_item_ids"),
            exclude_item_ids=objective.get("exclude_item_ids"),
        )
        facts = self.catalog.sbc_item_facts([row["card_ea_id"] for row in rows])
        items = []
        exclusions = {}
        for row in rows:
            fact = facts.get(row["card_ea_id"])
            if fact is None:
                continue
            item_id = int(row["item_id"])
            reason = None
            if item_id in reserved_item_ids:
                reason = "active_squad"
            elif fact.get("is_evolution"):
                reason = "evolution"
            elif fact.get("is_special"):
                reason = "special"
            if reason:
                exclusions[item_id] = reason
                continue
            value = 0
            if row["tradeable"]:
                value = row.get("acquisition_cost") or self.runtime.latest_reference_price(row["card_ea_id"]) or 0
            items.append(
                {
                    **row,
                    **fact,
                    "source": "owned",
                    "tradeable_value": int(value),
                    "purchase_price": 0,
                }
            )
        max_overall = objective.get("max_item_overall")
        if max_overall is not None:
            items = [row for row in items if row["overall"] <= int(max_overall)]
        items.sort(
            key=lambda row: (
                row["overall"],
                0 if not row["tradeable"] else 1,
                row["tradeable_value"],
                row["item_id"],
            )
        )
        counts = dict(sorted(Counter(exclusions.values()).items()))
        return items, {"by_item_id": exclusions, "counts": counts}

    @staticmethod
    def _apply_quality_prefilter(items, constraints):
        exact = [row["quality"] for row in constraints if row["type"] == "squad_quality" and row["operator"] == "exact"]
        if not exact:
            return items
        return [row for row in items if row.get("quality") == exact[0]]

    @staticmethod
    def _metrics(items, slots):
        ratings = sorted(
            (int(row["overall"]) for row in items), reverse=True
        )
        average = sum(ratings) / len(ratings) if ratings else 0
        adjusted = sum(ratings) + sum(max(rating - average, 0) for rating in ratings)
        chemistry = chemistry_score(items, slots)
        return {
            "team_rating": math.floor(adjusted / len(ratings)) if ratings else 0,
            "rating_vector": ratings,
            "max_overall": max(ratings, default=0),
            "total_overall": sum(ratings),
            "nation_count": len({row.get("nation_id") for row in items}),
            "league_count": len({row.get("league_id") for row in items}),
            "club_count": len({row.get("club_id") for row in items}),
            "same_nation_max": max(Counter(row.get("nation_id") for row in items).values(), default=0),
            "same_league_max": max(Counter(row.get("league_id") for row in items).values(), default=0),
            "same_club_max": max(Counter(row.get("club_id") for row in items).values(), default=0),
            "quality_counts": dict(Counter(row.get("quality") for row in items)),
            "chemistry": chemistry["total"],
            "player_chemistry": chemistry["per_player"],
            "in_position": chemistry["in_position"],
        }

    @staticmethod
    def _constraint_actual(constraint, items, metrics):
        kind = constraint["type"]
        if kind == "squad_quality":
            levels = [QUALITY_RANKS.get(row.get("quality"), 0) for row in items]
            threshold = QUALITY_RANKS[constraint["quality"]]
            if not levels:
                return 0
            if constraint["operator"] == "min":
                return int(min(levels) >= threshold)
            if constraint["operator"] == "max":
                return int(max(levels) <= threshold)
            return int(all(level == threshold for level in levels))
        if kind == "quality_count":
            return metrics["quality_counts"].get(constraint["quality"], 0)
        if kind == "overall_count":
            if constraint["overall_operator"] == "min":
                return sum(
                    int(row["overall"]) >= int(constraint["overall"])
                    for row in items
                )
            if constraint["overall_operator"] == "max":
                return sum(
                    int(row["overall"]) <= int(constraint["overall"])
                    for row in items
                )
            return sum(
                int(row["overall"]) == int(constraint["overall"])
                for row in items
            )
        if kind in (
            "specific_nation_count",
            "specific_league_count",
            "specific_club_count",
        ):
            attribute, ids_key = {
                "specific_nation_count": ("nation_id", "nation_ids"),
                "specific_league_count": ("league_id", "league_ids"),
                "specific_club_count": ("club_id", "club_ids"),
            }[kind]
            return sum(row.get(attribute) in constraint[ids_key] for row in items)
        if kind in ("same_nation_count", "same_league_count", "same_club_count"):
            return metrics[
                {
                    "same_nation_count": "same_nation_max",
                    "same_league_count": "same_league_max",
                    "same_club_count": "same_club_max",
                }[kind]
            ]
        if kind == "all_players_chemistry_points":
            values = metrics["player_chemistry"]
            if constraint["operator"] == "min":
                return min(values, default=0)
            if constraint["operator"] == "max":
                return max(values, default=0)
            return constraint["value"] if all(
                value == constraint["value"] for value in values
            ) else -1
        return metrics[kind]

    @staticmethod
    def _compare(actual, operator, expected):
        if operator == "min":
            return actual >= expected
        if operator == "max":
            return actual <= expected
        return actual == expected

    def _solution(self, challenge, items, objective, validation):
        item_ids = [int(row["item_id"]) for row in items]
        slot_indices = [int(value) for value in challenge.get("slot_indices") or []]
        if len(slot_indices) != len(item_ids):
            raise FC27Error(
                "SBC_SCHEMA_UNSUPPORTED",
                "The challenge does not expose one exact fillable slot for every selected item.",
                details={
                    "slot_indices": slot_indices,
                    "item_count": len(item_ids),
                },
            )
        identity = canonical_json({"challenge_id": challenge["challenge_id"], "objective": objective, "item_ids": item_ids})
        solution_id = "sbc-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        return {
            "solution_id": solution_id,
            "challenge_id": challenge["challenge_id"],
            "created_at": utc_now(),
            "estimated_cost": sum(int(row.get("acquisition_cost") or 0) for row in items),
            "tradeable_value": validation["tradeable_value"],
            "objective": objective,
            "validation": validation,
            "status": "validated",
            "item_ids": item_ids,
            "slots": [
                {
                    "slot_index": slot_indices[index],
                    "position": challenge["slots"][index],
                    "item_id": item_id,
                }
                for index, item_id in enumerate(item_ids)
            ],
        }

    @staticmethod
    def _player_count(challenge):
        value = challenge.get("player_count")
        if value is not None:
            return int(value)
        return len(challenge.get("slots") or [])

    def _normalize_set(self, value, observed_at):
        raw = value.get("raw") or value
        return {
            "set_id": str(value["id"]),
            "name": value.get("name") or f"SBC set {value['id']}",
            "status": value.get("status"),
            "expires_at": str(value.get("expires")) if value.get("expires") is not None else None,
            "repeatable": bool(value.get("repeatable")),
            "challenge_count": value.get("challenge_count") or raw.get("challengesCount"),
            "completed_count": value.get("completed_count", raw.get("challengesCompletedCount")),
            "times_completed": value.get("times_completed", raw.get("timesCompleted")),
            "rewards": value.get("rewards") or raw.get("awards") or [],
            "observed_at": observed_at,
            "raw": raw,
        }

    def _normalize_challenge(self, value, set_id, observed_at):
        raw = value.get("raw") or value
        requirements = value.get("requirements") or []
        constraints, unsupported = self._normalize_requirements(requirements)
        formation = value.get("formation")
        challenge_type = value.get("challenge_type") or raw.get("type")
        if challenge_type is not None:
            challenge_type = str(challenge_type).upper()
        player_count, player_count_source, player_count_error = self._resolve_player_count(
            value, raw, requirements, challenge_type
        )
        if player_count_error is not None:
            unsupported.append(player_count_error)
        slot_indices, slot_indices_source, slot_indices_error = self._resolve_slot_indices(
            value, raw, player_count
        )
        if slot_indices_error is not None:
            unsupported.append(slot_indices_error)
        slot_layout_error = value.get("slot_layout_error")
        if slot_layout_error is not None:
            unsupported.append(
                {
                    "type": "slot_indices",
                    "reason": "EA challenge slot layout is unavailable",
                    "error": slot_layout_error,
                }
            )
        slot_positions, slot_positions_source = self._resolve_slot_positions(
            value, raw, slot_indices
        )
        if player_count is None or not slot_indices:
            slots = []
        else:
            by_index = {
                int(position["slot_index"]): position for position in slot_positions
            }
            slots = [
                by_index.get(index, {}).get("general_position_name")
                or by_index.get(index, {}).get("position_name")
                or f"ITEM_{index + 1}"
                for index in slot_indices
            ]
        return {
            "challenge_id": str(value["id"]),
            "set_id": str(value.get("set_id") or set_id),
            "name": value.get("name") or f"SBC challenge {value['id']}",
            "status": value.get("status"),
            "expires_at": str(value.get("expires")) if value.get("expires") is not None else None,
            "repeatable": bool(value.get("repeatable")),
            "completed": bool(value.get("completed")),
            "times_completed": value.get("times_completed", raw.get("timesCompleted")),
            "challenge_type": challenge_type,
            "player_count": player_count,
            "player_count_source": player_count_source,
            "slot_indices": slot_indices,
            "slot_indices_source": slot_indices_source,
            "slot_layout_error": slot_layout_error,
            "slot_positions": slot_positions,
            "slot_positions_source": slot_positions_source,
            "formation": formation,
            "slots": slots,
            "rewards": value.get("rewards") or [],
            "constraints": constraints,
            "unsupported_constraints": unsupported,
            "observed_at": observed_at,
            "raw": raw,
        }

    @staticmethod
    def _resolve_player_count(value, raw, requirements, challenge_type):
        evidence = []
        for source, candidate in (
            ("challenge.player_count", value.get("player_count")),
            ("raw.playerCount", raw.get("playerCount")),
            ("raw.requiredPlayerCount", raw.get("requiredPlayerCount")),
            ("raw.maxPlayers", raw.get("maxPlayers")),
            ("raw.squadSize", raw.get("squadSize")),
            ("raw.numberOfPlayers", raw.get("numberOfPlayers")),
        ):
            if candidate is None:
                continue
            try:
                count = int(candidate)
            except (TypeError, ValueError):
                return None, None, {
                    "type": "player_count",
                    "reason": f"{source} is not an integer",
                    "value": candidate,
                }
            if not 1 <= count <= 11:
                return None, None, {
                    "type": "player_count",
                    "reason": f"{source} is outside 1 through 11",
                    "value": count,
                }
            evidence.append((source, count))
        provided_slot_indices = value.get("slot_indices")
        if provided_slot_indices is not None:
            if not isinstance(provided_slot_indices, list):
                return None, None, {
                    "type": "player_count",
                    "reason": "challenge slot indices are not an array",
                    "value": provided_slot_indices,
                }
            if not 1 <= len(provided_slot_indices) <= 11:
                return None, None, {
                    "type": "player_count",
                    "reason": "challenge slot indices are outside 1 through 11",
                    "value": len(provided_slot_indices),
                }
            evidence.append(("challenge.slot_indices", len(provided_slot_indices)))
        if challenge_type in VARIABLE_SQUAD_CHALLENGE_TYPES:
            positive_counts = []
            for requirement in requirements:
                count = requirement.get("count")
                if isinstance(count, int) and 1 <= count <= 11:
                    positive_counts.append(count)
            if positive_counts:
                evidence.append(("brick_requirement_count", max(positive_counts)))
        distinct = {count for _, count in evidence}
        if len(distinct) > 1:
            return None, None, {
                "type": "player_count",
                "reason": "player count evidence is contradictory",
                "evidence": [
                    {"source": source, "value": count} for source, count in evidence
                ],
            }
        if evidence:
            return evidence[0][1], "+".join(source for source, _ in evidence), None
        if challenge_type in VARIABLE_SQUAD_CHALLENGE_TYPES:
            return None, None, {
                "type": "player_count",
                "reason": "variable-size challenge has no bounded player count evidence",
            }
        return None, None, {
            "type": "player_count",
            "reason": "challenge has no bounded player count evidence",
        }

    @staticmethod
    def _resolve_slot_indices(value, raw, player_count):
        candidates = (
            ("challenge.slot_indices", value.get("slot_indices")),
            ("raw.slotIndices", raw.get("slotIndices")),
            ("raw.fillableSlotIndices", raw.get("fillableSlotIndices")),
        )
        evidence = []
        for source, candidate in candidates:
            if candidate is None:
                continue
            if not isinstance(candidate, list):
                return [], None, {
                    "type": "slot_indices",
                    "reason": f"{source} is not an array",
                    "value": candidate,
                }
            normalized = []
            for raw_index in candidate:
                if isinstance(raw_index, bool):
                    return [], None, {
                        "type": "slot_indices",
                        "reason": f"{source} contains a non-integer index",
                        "value": raw_index,
                    }
                try:
                    index = int(raw_index)
                except (TypeError, ValueError):
                    return [], None, {
                        "type": "slot_indices",
                        "reason": f"{source} contains a non-integer index",
                        "value": raw_index,
                    }
                if not 0 <= index < 11:
                    return [], None, {
                        "type": "slot_indices",
                        "reason": f"{source} contains an index outside 0 through 10",
                        "value": index,
                    }
                normalized.append(index)
            normalized = sorted(normalized)
            if len(normalized) != len(set(normalized)):
                return [], None, {
                    "type": "slot_indices",
                    "reason": f"{source} contains duplicate indices",
                    "value": candidate,
                }
            evidence.append((source, normalized))
        distinct = {tuple(indices) for _, indices in evidence}
        if len(distinct) > 1:
            return [], None, {
                "type": "slot_indices",
                "reason": "fillable slot evidence is contradictory",
                "evidence": [
                    {"source": source, "value": indices}
                    for source, indices in evidence
                ],
            }
        if evidence:
            source, indices = evidence[0]
            if player_count is not None and len(indices) != player_count:
                return [], None, {
                    "type": "slot_indices",
                    "reason": "fillable slot count does not match player count",
                    "player_count": player_count,
                    "slot_indices": indices,
                }
            return indices, "+".join(source for source, _ in evidence), None
        if player_count == 11:
            return list(range(11)), "all_field_slots_from_player_count", None
        if player_count is not None:
            return [], None, {
                "type": "slot_indices",
                "reason": "partial squad has no exact fillable slot evidence",
                "player_count": player_count,
            }
        return [], None, None

    @staticmethod
    def _resolve_slot_positions(value, raw, slot_indices):
        positions = value.get("slot_positions")
        source = value.get("slot_positions_source") or "challenge.slot_positions"
        if positions is None:
            positions = raw.get("slotPositions")
            source = "raw.slotPositions"
        if isinstance(positions, list):
            normalized = []
            for fallback_index, position in enumerate(positions):
                if not isinstance(position, dict):
                    continue
                slot_index = position.get("slot_index", position.get("index", fallback_index))
                try:
                    slot_index = int(slot_index)
                except (TypeError, ValueError):
                    continue
                if slot_index not in slot_indices:
                    continue
                position_name = (
                    position.get("position_name")
                    or position.get("general_position_name")
                    or position.get("name")
                )
                raw_position = position.get("position")
                if not position_name and isinstance(raw_position, str):
                    position_name = raw_position
                general_position_name = position.get("general_position_name")
                if not general_position_name and position_name:
                    general_position_name = {
                        "RCB": "CB",
                        "LCB": "CB",
                        "RCM": "CM",
                        "LCM": "CM",
                        "RDM": "CDM",
                        "LDM": "CDM",
                        "RAM": "CAM",
                        "LAM": "CAM",
                        "RST": "ST",
                        "LST": "ST",
                        "RS": "ST",
                        "LS": "ST",
                    }.get(str(position_name).upper(), position_name)
                normalized.append(
                    {
                        "slot_index": slot_index,
                        "position_id": position.get("position_id", position.get("id")),
                        "position_name": str(position_name).upper() if position_name else None,
                        "general_position": position.get("general_position"),
                        "general_position_name": str(general_position_name).upper()
                        if general_position_name
                        else None,
                    }
                )
            if normalized:
                normalized.sort(key=lambda row: row["slot_index"])
                return normalized, source

        slots = value.get("slots")
        source = "challenge.slots"
        if slots is None:
            slots = raw.get("slots")
            source = "raw.slots"
        if isinstance(slots, list) and slot_indices:
            normalized = []
            for slot_index in slot_indices:
                if slot_index >= len(slots):
                    return [], None
                position = slots[slot_index]
                if not isinstance(position, str) or not position.strip():
                    return [], None
                normalized.append(
                    {
                        "slot_index": slot_index,
                        "position_id": None,
                        "position_name": position.strip().upper(),
                        "general_position": None,
                        "general_position_name": None,
                    }
                )
            return normalized, source
        return [], None

    def _retain_cached_slot_contract(self, challenge):
        existing = self.runtime.get_sbc_challenge(challenge["challenge_id"])
        if (
            existing is None
            or not existing.get("slot_indices")
            or str(existing.get("formation")) != str(challenge.get("formation"))
        ):
            return challenge
        if challenge.get("slot_indices"):
            same_slots = [int(value) for value in challenge["slot_indices"]] == [
                int(value) for value in existing["slot_indices"]
            ]
            generic_positions = not challenge.get("slots") or any(
                str(value).startswith("ITEM_") for value in challenge.get("slots") or []
            )
            if same_slots and generic_positions and existing.get("slots"):
                challenge["slot_positions"] = existing.get("slot_positions") or []
                challenge["slot_positions_source"] = "persisted_ea_slot_contract"
                challenge["slots"] = existing["slots"]
            return challenge
        challenge["player_count"] = existing.get("player_count")
        challenge["player_count_source"] = existing.get("player_count_source")
        challenge["slot_indices"] = existing.get("slot_indices")
        challenge["slot_indices_source"] = "persisted_ea_slot_contract"
        challenge["slot_positions"] = existing.get("slot_positions") or []
        challenge["slot_positions_source"] = existing.get("slot_positions_source")
        challenge["slots"] = existing.get("slots") or []
        challenge["unsupported_constraints"] = [
            value
            for value in challenge["unsupported_constraints"]
            if value.get("type") not in ("player_count", "slot_indices")
        ]
        return challenge

    @staticmethod
    def _normalize_requirements(requirements):
        constraints = []
        unsupported = []
        for raw in requirements:
            collection = ((raw.get("kvPairs") or {}).get("_collection") or {})
            if len(collection) != 1:
                unsupported.append({"raw": raw, "reason": "requirement must contain one key"})
                continue
            key_text, values = next(iter(collection.items()))
            key = int(key_text)
            spec = REQUIREMENT_KEY_SPECS.get(key)
            if spec is None:
                unsupported.append({"raw": raw, "reason": f"unknown requirement key {key}"})
                continue
            if spec["family"] == "unsupported":
                unsupported.append(
                    {
                        "raw": raw,
                        "reason": f"known unsupported requirement {spec['name']} ({key})",
                    }
                )
                continue
            if not isinstance(values, list) or not values:
                unsupported.append({"raw": raw, "reason": "requirement must contain one or more values"})
                continue
            if spec["family"] != "specific" and len(values) != 1:
                unsupported.append({"raw": raw, "reason": "requirement must contain one value"})
                continue
            try:
                source_values = [int(value) for value in values]
            except (TypeError, ValueError):
                unsupported.append({"raw": raw, "reason": "requirement values must be integers"})
                continue
            source_value = source_values[0]
            operator = SCOPE_OPERATORS.get(int(raw.get("scope", -1)))
            if operator is None:
                unsupported.append({"raw": raw, "reason": "unknown scope"})
                continue
            constraint = {
                "source_key": key,
                "source_name": spec["name"],
                "operator": operator,
                "raw": raw,
            }
            family = spec["family"]
            if family in ("squad_quality", "quality_count"):
                quality = QUALITY_VALUES.get(source_value)
                if quality is None:
                    unsupported.append({"raw": raw, "reason": "unknown quality value"})
                    continue
                if family == "squad_quality":
                    constraint.update({"type": "squad_quality", "quality": quality, "value": 1})
                else:
                    count = raw.get("count")
                    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                        unsupported.append({"raw": raw, "reason": "quality requirement count must be positive"})
                        continue
                    constraint.update({"type": "quality_count", "quality": quality, "value": count})
            elif family == "overall_count":
                count = raw.get("count")
                if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                    unsupported.append(
                        {"raw": raw, "reason": "overall requirement count must be positive"}
                    )
                    continue
                constraint.update(
                    {
                        "type": "overall_count",
                        "overall_operator": spec["overall_operator"],
                        "overall": source_value,
                        "value": count,
                    }
                )
            elif family == "specific":
                count = raw.get("count")
                if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                    unsupported.append({"raw": raw, "reason": "specific requirement count must be positive"})
                    continue
                constraint.update(
                    {"type": spec["type"], spec["ids"]: source_values, "value": count}
                )
            else:
                constraint.update({"type": spec["type"], "value": source_value})
            constraints.append(constraint)
        return constraints, unsupported
