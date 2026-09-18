import hashlib
import json
import math
from collections import Counter

from .errors import FC27Error
from .runtime import utc_now


SCOPE_OPERATORS = {0: "min", 1: "max", 2: "exact"}
QUALITY_VALUES = {1: "bronze", 2: "silver", 3: "gold"}
FORMATION_SLOTS = {
    "f41212": ("GK", "LB", "CB", "CB", "RB", "CDM", "CM", "CM", "CAM", "ST", "ST"),
    "f4222": ("GK", "LB", "CB", "CB", "RB", "CDM", "CDM", "CAM", "CAM", "ST", "ST"),
    "f424": ("GK", "LB", "CB", "CB", "RB", "CM", "CM", "LW", "RW", "ST", "ST"),
    "f442": ("GK", "LB", "CB", "CB", "RB", "LM", "CM", "CM", "RM", "ST", "ST"),
    "f343": ("GK", "CB", "CB", "CB", "LM", "CM", "CM", "RM", "LW", "RW", "ST"),
    "f4141": ("GK", "LB", "CB", "CB", "RB", "CDM", "LM", "CM", "RM", "ST", "ST"),
    "f3142": ("GK", "CB", "CB", "CB", "CDM", "LM", "CM", "RM", "CAM", "ST", "ST"),
}


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class SbcService:
    def __init__(self, runtime, catalog):
        self.runtime = runtime
        self.catalog = catalog

    def capture_sets(self, payload):
        observed_at = utc_now()
        sets = [self._normalize_set(value, observed_at) for value in payload.get("sets") or []]
        self.runtime.upsert_sbc_sets(sets)
        return {"count": len(sets), "sets": sets, "observed_at": observed_at}

    def capture_challenges(self, payload):
        observed_at = utc_now()
        normalized_set = self._normalize_set(payload["set"], observed_at)
        challenges = [
            self._normalize_challenge(value, normalized_set["set_id"], observed_at)
            for value in payload.get("challenges") or []
        ]
        self.runtime.upsert_sbc_sets([normalized_set])
        self.runtime.upsert_sbc_challenges(challenges)
        return {
            "set": normalized_set,
            "count": len(challenges),
            "challenges": challenges,
            "observed_at": observed_at,
        }

    def capture_challenge(self, payload):
        observed_at = utc_now()
        normalized_set = self._normalize_set(payload["set"], observed_at)
        challenge = self._normalize_challenge(
            payload["challenge"], normalized_set["set_id"], observed_at
        )
        self.runtime.upsert_sbc_sets([normalized_set])
        self.runtime.upsert_sbc_challenges([challenge])
        return {"set": normalized_set, "challenge": challenge}

    def query(self, *, set_id=None, challenge_id=None):
        return self.runtime.query_sbcs(set_id=set_id, challenge_id=challenge_id)

    def solve(self, challenge_id, objective=None, max_solutions=5):
        objective = dict(objective or {})
        challenge = self.runtime.get_sbc_challenge(challenge_id)
        if challenge is None:
            raise FC27Error(
                "SBC_CHALLENGE_NOT_FOUND",
                f"SBC challenge {challenge_id} is not cached.",
                recovery="Call FC27:sbc_query for the challenge, then retry.",
            )
        unsupported = challenge["unsupported_constraints"]
        if unsupported:
            raise FC27Error(
                "SBC_SCHEMA_UNSUPPORTED",
                "The challenge contains constraints that cannot be validated locally.",
                recovery="Inspect unsupported_constraints and implement those requirement types before solving.",
                details={"unsupported_constraints": unsupported},
            )
        items = self._candidate_items(objective)
        items = self._apply_quality_prefilter(items, challenge["constraints"])
        if len(items) < len(challenge["slots"]):
            raise FC27Error(
                "SBC_NO_SOLUTION",
                "Not enough eligible owned items remain after applying the objective and challenge filters.",
                recovery="Sync the club, widen the objective, or acquire additional eligible items.",
                details={"eligible_items": len(items), "required_slots": len(challenge["slots"])},
            )
        solutions = []
        slot_count = len(challenge["slots"])
        for offset in range(max(1, len(items) - slot_count + 1)):
            selected = items[offset : offset + slot_count]
            if len(selected) != slot_count:
                break
            validation = self.validate(challenge, selected, objective)
            if not validation["valid"]:
                continue
            solution = self._solution(challenge, selected, objective, validation)
            if any(row["item_ids"] == solution["item_ids"] for row in solutions):
                continue
            self.runtime.save_sbc_solution(solution)
            solutions.append(solution)
            if len(solutions) >= max(1, min(int(max_solutions), 10)):
                break
        if not solutions:
            raise FC27Error(
                "SBC_NO_SOLUTION",
                "No deterministic candidate passed every normalized constraint.",
                recovery="Inspect the validation evidence, widen the objective, or choose a simpler challenge.",
            )
        return {
            "challenge": challenge,
            "objective": objective,
            "solution_count": len(solutions),
            "solutions": solutions,
        }

    def validate_solution(self, solution_id, expected_sync_id=None):
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
        item_rows = self.runtime.items_by_ids(solution["item_ids"])
        facts = self.catalog.sbc_item_facts([row["card_ea_id"] for row in item_rows])
        items = [{**row, **facts.get(row["card_ea_id"], {})} for row in item_rows]
        validation = self.validate(challenge, items, solution["objective"])
        if not validation["valid"]:
            raise FC27Error(
                "SBC_NOT_ELIGIBLE",
                "The persisted solution no longer passes local validation.",
                recovery="Sync the club and generate a new solution.",
                details=validation,
            )
        return {"solution": solution, "challenge": challenge, "validation": validation}

    def validate(self, challenge, items, objective=None):
        objective = objective or {}
        failures = []
        item_ids = [int(row["item_id"]) for row in items]
        if len(item_ids) != len(challenge["slots"]):
            failures.append({"type": "slot_count", "expected": len(challenge["slots"]), "actual": len(item_ids)})
        if len(item_ids) != len(set(item_ids)):
            failures.append({"type": "duplicate_item_ids"})
        for row in items:
            if row.get("protected"):
                failures.append({"type": "protected_item", "item_id": row["item_id"]})
            if row.get("loan_uses_remaining") not in (None, -1):
                failures.append({"type": "loan_item", "item_id": row["item_id"]})
        tradeable_value = sum(int(row.get("tradeable_value") or 0) for row in items)
        limit = objective.get("max_tradeable_value")
        if limit is not None and tradeable_value > int(limit):
            failures.append({"type": "tradeable_value", "limit": int(limit), "actual": tradeable_value})
        metrics = self._metrics(items)
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

    def _candidate_items(self, objective):
        rows = self.runtime.sbc_candidate_items(
            candidate_item_ids=objective.get("candidate_item_ids"),
            exclude_item_ids=objective.get("exclude_item_ids"),
        )
        facts = self.catalog.sbc_item_facts([row["card_ea_id"] for row in rows])
        items = []
        for row in rows:
            fact = facts.get(row["card_ea_id"])
            if fact is None:
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
        return items

    @staticmethod
    def _apply_quality_prefilter(items, constraints):
        exact = [row["quality"] for row in constraints if row["type"] == "squad_quality" and row["operator"] == "exact"]
        if not exact:
            return items
        return [row for row in items if row.get("quality") == exact[0]]

    @staticmethod
    def _metrics(items):
        ratings = [int(row["overall"]) for row in items]
        average = sum(ratings) / len(ratings) if ratings else 0
        adjusted = sum(ratings) + sum(max(rating - average, 0) for rating in ratings)
        return {
            "team_rating": math.floor(adjusted / len(ratings)) if ratings else 0,
            "nation_count": len({row.get("nation_id") for row in items}),
            "league_count": len({row.get("league_id") for row in items}),
            "club_count": len({row.get("club_id") for row in items}),
            "same_nation_max": max(Counter(row.get("nation_id") for row in items).values(), default=0),
            "same_league_max": max(Counter(row.get("league_id") for row in items).values(), default=0),
            "same_club_max": max(Counter(row.get("club_id") for row in items).values(), default=0),
            "quality_counts": dict(Counter(row.get("quality") for row in items)),
        }

    @staticmethod
    def _constraint_actual(constraint, items, metrics):
        kind = constraint["type"]
        if kind == "squad_quality":
            return 1 if all(row.get("quality") == constraint["quality"] for row in items) else 0
        if kind == "quality_count":
            return metrics["quality_counts"].get(constraint["quality"], 0)
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
                {"slot_index": index, "position": challenge["slots"][index], "item_id": item_id}
                for index, item_id in enumerate(item_ids)
            ],
        }

    def _normalize_set(self, value, observed_at):
        raw = value.get("raw") or value
        return {
            "set_id": str(value["id"]),
            "name": value.get("name") or f"SBC set {value['id']}",
            "status": value.get("status"),
            "expires_at": str(value.get("expires")) if value.get("expires") is not None else None,
            "repeatable": bool(value.get("repeatable")),
            "challenge_count": value.get("challenge_count") or raw.get("challengesCount"),
            "completed_count": raw.get("challengesCompletedCount"),
            "times_completed": raw.get("timesCompleted"),
            "rewards": value.get("rewards") or raw.get("awards") or [],
            "observed_at": observed_at,
            "raw": raw,
        }

    def _normalize_challenge(self, value, set_id, observed_at):
        constraints, unsupported = self._normalize_requirements(value.get("requirements") or [])
        formation = value.get("formation")
        slots = list(FORMATION_SLOTS.get(formation, ()))
        if not slots:
            unsupported.append({"type": "formation", "formation": formation, "reason": "formation slots are not mapped"})
        return {
            "challenge_id": str(value["id"]),
            "set_id": str(value.get("set_id") or set_id),
            "name": value.get("name") or f"SBC challenge {value['id']}",
            "status": value.get("status"),
            "expires_at": str(value.get("expires")) if value.get("expires") is not None else None,
            "repeatable": bool(value.get("repeatable")),
            "completed": bool(value.get("completed")),
            "formation": formation,
            "slots": slots,
            "rewards": value.get("rewards") or [],
            "constraints": constraints,
            "unsupported_constraints": unsupported,
            "observed_at": observed_at,
            "raw": value.get("raw") or value,
        }

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
            if not isinstance(values, list) or len(values) != 1:
                unsupported.append({"raw": raw, "reason": "requirement must contain one value"})
                continue
            key = int(key_text)
            source_value = int(values[0])
            operator = SCOPE_OPERATORS.get(int(raw.get("scope", -1)))
            if operator is None:
                unsupported.append({"raw": raw, "reason": "unknown scope"})
                continue
            constraint = {"source_key": key, "operator": operator, "raw": raw}
            if key in (3, 17):
                quality = QUALITY_VALUES.get(source_value)
                if quality is None:
                    unsupported.append({"raw": raw, "reason": "unknown quality value"})
                    continue
                if key == 3:
                    constraint.update({"type": "squad_quality", "quality": quality, "value": 1})
                else:
                    constraint.update({"type": "quality_count", "quality": quality, "value": int(raw.get("count", -1))})
            elif key == 19:
                constraint.update({"type": "team_rating", "value": source_value})
            elif key == 35:
                unsupported.append({"raw": raw, "reason": "chemistry requires EA eligibility validation"})
                continue
            elif key == 7:
                constraint.update({"type": "nation_count", "value": source_value})
            elif key == 8:
                constraint.update({"type": "league_count", "value": source_value})
            elif key == 4:
                constraint.update({"type": "same_league_max", "value": source_value})
            elif key == 5:
                constraint.update({"type": "same_nation_max", "value": source_value})
            elif key == 6:
                constraint.update({"type": "same_club_max", "value": source_value})
            else:
                unsupported.append({"raw": raw, "reason": f"unknown requirement key {key}"})
                continue
            constraints.append(constraint)
        return constraints, unsupported
