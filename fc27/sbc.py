import hashlib
import json
import math
from collections import Counter

from .errors import FC27Error
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
    "prefer_untradeable",
    "max_tradeable_value",
    "max_item_overall",
}


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class SbcService:
    def __init__(self, runtime, catalog):
        self.runtime = runtime
        self.catalog = catalog

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
        reserved_item_ids=None,
    ):
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
        player_count = self._player_count(challenge)
        if len(items) < player_count:
            raise FC27Error(
                "SBC_NO_SOLUTION",
                "Not enough eligible owned items remain after applying the objective and challenge filters.",
                recovery="Sync the club, widen the objective, or acquire additional eligible items.",
                details={"eligible_items": len(items), "required_slots": player_count},
            )
        maximum_solutions = max(1, min(int(max_solutions), 10))
        optimized = SbcOptimizer().solve(
            challenge, items, objective, maximum_solutions
        )
        solutions = []
        by_item_id = {int(row["item_id"]): row for row in items}
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
                "wall_time_seconds": candidate["wall_time_seconds"],
            }
            validation["candidate_pool"] = {
                "eligible_count": len(items),
                "excluded_counts": automatic_exclusions["counts"],
            }
            solution = self._solution(challenge, selected, objective, validation)
            self.runtime.save_sbc_solution(solution)
            solutions.append(solution)
        if not solutions:
            if optimized["status"] == "unknown":
                raise FC27Error(
                    "SBC_SOLVER_TIMEOUT",
                    "The SBC optimizer reached its deadline before finding a feasible squad.",
                    retryable=True,
                    recovery="Narrow candidate_item_ids, reduce exclusions, or retry with a simpler objective.",
                    details={"required_item_ids": objective["required_item_ids"]},
                )
            raise FC27Error(
                "SBC_NO_SOLUTION",
                "No owned-item combination satisfies the normalized challenge and Agent objective.",
                recovery="Inspect required_item_ids and exclusions, widen candidate_item_ids, or acquire eligible items.",
                details={
                    "solver_status": optimized["status"],
                    "required_item_ids": objective["required_item_ids"],
                },
            )
        return {
            "challenge": challenge,
            "objective": objective,
            "candidate_pool": {
                "eligible_count": len(items),
                "excluded_counts": automatic_exclusions["counts"],
            },
            "solver": {
                "engine": "or-tools-cp-sat",
                "status": optimized["status"],
                "complete": optimized["complete"],
            },
            "solution_count": len(solutions),
            "solutions": solutions,
        }

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
        for constraint in challenge["constraints"]:
            actual = self._constraint_actual(constraint, items, metrics)
            if not self._compare(actual, constraint["operator"], constraint["value"]):
                failures.append({"type": "constraint", "constraint": constraint, "actual": actual})
        return {
            "valid": not failures,
            "item_ids": item_ids,
            "tradeable_value": tradeable_value,
            "metrics": metrics,
            "failures": failures,
        }

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
        prefer_untradeable = value.get("prefer_untradeable", True)
        if not isinstance(prefer_untradeable, bool):
            raise FC27Error(
                "SBC_OBJECTIVE_INVALID", "prefer_untradeable must be a boolean."
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
            "prefer_untradeable": prefer_untradeable,
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
            items.append({**row, **fact, "tradeable_value": int(value)})
        max_overall = objective.get("max_item_overall")
        if max_overall is not None:
            items = [row for row in items if row["overall"] <= int(max_overall)]
        prefer_untradeable = objective.get("prefer_untradeable", True) is not False
        items.sort(
            key=lambda row: (
                0 if (prefer_untradeable and not row["tradeable"]) else 1,
                row["tradeable_value"],
                row["overall"],
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
        ratings = [int(row["overall"]) for row in items]
        average = sum(ratings) / len(ratings) if ratings else 0
        adjusted = sum(ratings) + sum(max(rating - average, 0) for rating in ratings)
        chemistry = chemistry_score(items, slots)
        return {
            "team_rating": math.floor(adjusted / len(ratings)) if ratings else 0,
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
        source = "challenge.slot_positions"
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
                normalized.append(
                    {
                        "slot_index": slot_index,
                        "position_id": position.get("position_id"),
                        "position_name": str(position_name).upper() if position_name else None,
                        "general_position": position.get("general_position"),
                        "general_position_name": position.get("general_position_name"),
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
        if challenge.get("slot_indices"):
            return challenge
        existing = self.runtime.get_sbc_challenge(challenge["challenge_id"])
        if (
            existing is None
            or not existing.get("slot_indices")
            or str(existing.get("formation")) != str(challenge.get("formation"))
        ):
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
