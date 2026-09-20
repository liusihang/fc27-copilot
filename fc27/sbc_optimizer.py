import time
from collections import defaultdict

from ortools.sat.python import cp_model


QUALITY_RANKS = {"bronze": 1, "silver": 2, "gold": 3}
CHEMISTRY_THRESHOLDS = {
    "club_id": (2, 4, 7),
    "nation_id": (2, 5, 8),
    "league_id": (3, 5, 8),
}


def chemistry_score(items):
    total = 0
    for attribute, thresholds in CHEMISTRY_THRESHOLDS.items():
        counts = defaultdict(int)
        for row in items:
            value = row.get(attribute)
            if value is not None:
                counts[value] += 1
        for count in counts.values():
            total += count * sum(count >= threshold for threshold in thresholds)
    return total


STATUS_NAMES = {
    cp_model.OPTIMAL: "optimal",
    cp_model.FEASIBLE: "feasible",
    cp_model.INFEASIBLE: "infeasible",
    cp_model.MODEL_INVALID: "model_invalid",
    cp_model.UNKNOWN: "unknown",
}


class SbcOptimizer:
    def __init__(self, *, time_limit_seconds=5.0):
        self.time_limit_seconds = float(time_limit_seconds)

    def solve(self, challenge, items, objective, max_solutions):
        slot_count = int(challenge.get("player_count") or len(challenge["slots"]))
        model = cp_model.CpModel()
        selected = [model.new_bool_var(f"item_{row['item_id']}") for row in items]
        model.add(sum(selected) == slot_count)

        indexes_by_item_id = {
            int(row["item_id"]): index for index, row in enumerate(items)
        }
        for item_id in objective.get("required_item_ids", []):
            model.add(selected[indexes_by_item_id[int(item_id)]] == 1)

        tradeable_value = sum(
            selected[index] * int(row.get("tradeable_value") or 0)
            for index, row in enumerate(items)
        )
        if objective.get("max_tradeable_value") is not None:
            model.add(tradeable_value <= int(objective["max_tradeable_value"]))

        self._add_challenge_constraints(model, selected, items, challenge, slot_count)
        model.minimize(
            self._objective_expression(
                selected,
                items,
                slot_count,
                prefer_untradeable=objective.get("prefer_untradeable", True) is not False,
            )
        )

        deadline = time.monotonic() + self.time_limit_seconds
        solutions = []
        terminal_status = "unknown"
        while len(solutions) < max_solutions:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                terminal_status = "unknown"
                break
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = remaining
            solver.parameters.num_search_workers = 1
            solver.parameters.random_seed = 0
            status = solver.solve(model)
            terminal_status = STATUS_NAMES.get(status, "unknown")
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                break
            chosen_indexes = [
                index for index, variable in enumerate(selected) if solver.value(variable)
            ]
            chosen_items = [items[index] for index in chosen_indexes]
            chosen_items.sort(key=lambda row: int(row["item_id"]))
            solutions.append(
                {
                    "status": terminal_status,
                    "item_ids": [int(row["item_id"]) for row in chosen_items],
                    "objective_value": int(round(solver.objective_value)),
                    "objective_components": self._objective_components(chosen_items),
                    "wall_time_seconds": solver.wall_time,
                }
            )
            model.add(sum(selected[index] for index in chosen_indexes) <= slot_count - 1)

        return {
            "status": solutions[0]["status"] if solutions else terminal_status,
            "complete": terminal_status == "infeasible",
            "solutions": solutions,
        }

    def _add_challenge_constraints(self, model, selected, items, challenge, slot_count):
        for index, constraint in enumerate(challenge["constraints"]):
            kind = constraint["type"]
            if kind == "squad_quality":
                threshold = QUALITY_RANKS[constraint["quality"]]
                operator = constraint["operator"]
                if operator == "min":
                    matching = sum(
                        selected[item_index]
                        for item_index, row in enumerate(items)
                        if QUALITY_RANKS.get(row.get("quality"), 0) >= threshold
                    )
                elif operator == "max":
                    matching = sum(
                        selected[item_index]
                        for item_index, row in enumerate(items)
                        if 0 < QUALITY_RANKS.get(row.get("quality"), 0) <= threshold
                    )
                else:
                    matching = sum(
                        selected[item_index]
                        for item_index, row in enumerate(items)
                        if QUALITY_RANKS.get(row.get("quality"), 0) == threshold
                    )
                model.add(matching == slot_count)
            elif kind == "quality_count":
                count = sum(
                    selected[item_index]
                    for item_index, row in enumerate(items)
                    if row.get("quality") == constraint["quality"]
                )
                self._add_comparison(
                    model, count, constraint["operator"], int(constraint["value"])
                )
            elif kind == "overall_count":
                if constraint["overall_operator"] == "min":
                    matching_indexes = [
                        item_index
                        for item_index, row in enumerate(items)
                        if int(row["overall"]) >= int(constraint["overall"])
                    ]
                else:
                    matching_indexes = [
                        item_index
                        for item_index, row in enumerate(items)
                        if int(row["overall"]) <= int(constraint["overall"])
                    ]
                count = sum(selected[item_index] for item_index in matching_indexes)
                self._add_comparison(
                    model, count, constraint["operator"], int(constraint["value"])
                )
            elif kind == "team_rating":
                self._add_team_rating_constraint(
                    model,
                    selected,
                    items,
                    slot_count,
                    constraint["operator"],
                    int(constraint["value"]),
                    index,
                )
            elif kind in ("nation_count", "league_count", "club_count"):
                attribute = {
                    "nation_count": "nation_id",
                    "league_count": "league_id",
                    "club_count": "club_id",
                }[kind]
                distinct = self._distinct_count(
                    model, selected, items, attribute, slot_count, index
                )
                self._add_comparison(
                    model, distinct, constraint["operator"], int(constraint["value"])
                )
            elif kind in (
                "specific_nation_count",
                "specific_league_count",
                "specific_club_count",
            ):
                attribute = {
                    "specific_nation_count": "nation_id",
                    "specific_league_count": "league_id",
                    "specific_club_count": "club_id",
                }[kind]
                values = (
                    {constraint["nation_id"]}
                    if kind == "specific_nation_count"
                    else {constraint["league_id"]}
                    if kind == "specific_league_count"
                    else set(constraint["club_ids"])
                )
                matching = sum(
                    selected[item_index]
                    for item_index, row in enumerate(items)
                    if row.get(attribute) in values
                )
                self._add_comparison(
                    model, matching, constraint["operator"], int(constraint["value"])
                )
            elif kind in ("same_nation_max", "same_league_max", "same_club_max"):
                attribute = {
                    "same_nation_max": "nation_id",
                    "same_league_max": "league_id",
                    "same_club_max": "club_id",
                }[kind]
                maximum = self._maximum_attribute_count(
                    model, selected, items, attribute, slot_count, index
                )
                self._add_comparison(
                    model, maximum, constraint["operator"], int(constraint["value"])
                )
            elif kind == "chemistry":
                chemistry = self._chemistry_expression(
                    model, selected, items, slot_count, index
                )
                self._add_comparison(
                    model, chemistry, constraint["operator"], int(constraint["value"])
                )

    @staticmethod
    def _add_comparison(model, expression, operator, value):
        if operator == "min":
            model.add(expression >= value)
        elif operator == "max":
            model.add(expression <= value)
        else:
            model.add(expression == value)

    def _add_team_rating_constraint(
        self, model, selected, items, slot_count, operator, value, constraint_index
    ):
        rating_sum = sum(
            selected[index] * int(row["overall"]) for index, row in enumerate(items)
        )
        selected_excess = []
        maximum_scaled_rating = 99 * slot_count
        for item_index, row in enumerate(items):
            raw_excess = model.new_int_var(
                -maximum_scaled_rating,
                maximum_scaled_rating,
                f"constraint_{constraint_index}_raw_excess_{item_index}",
            )
            model.add(
                raw_excess == int(row["overall"]) * slot_count - rating_sum
            )
            positive_excess = model.new_int_var(
                0,
                maximum_scaled_rating,
                f"constraint_{constraint_index}_positive_excess_{item_index}",
            )
            model.add_max_equality(positive_excess, [raw_excess, 0])
            active_excess = model.new_int_var(
                0,
                maximum_scaled_rating,
                f"constraint_{constraint_index}_active_excess_{item_index}",
            )
            model.add_multiplication_equality(
                active_excess, [positive_excess, selected[item_index]]
            )
            selected_excess.append(active_excess)
        scaled_team_rating = rating_sum * slot_count + sum(selected_excess)
        scale = slot_count * slot_count
        if operator == "min":
            model.add(scaled_team_rating >= value * scale)
        elif operator == "max":
            model.add(scaled_team_rating <= (value + 1) * scale - 1)
        else:
            model.add(scaled_team_rating >= value * scale)
            model.add(scaled_team_rating <= (value + 1) * scale - 1)

    @staticmethod
    def _groups(items, attribute, *, include_none=True):
        groups = defaultdict(list)
        for index, row in enumerate(items):
            if not include_none and row.get(attribute) is None:
                continue
            groups[row.get(attribute)].append(index)
        return groups

    def _chemistry_expression(self, model, selected, items, slot_count, index):
        expressions = []
        for attribute, thresholds in CHEMISTRY_THRESHOLDS.items():
            groups = self._groups(items, attribute, include_none=False)
            for group_index, item_indexes in enumerate(groups.values()):
                count = sum(selected[item_index] for item_index in item_indexes)
                points = model.new_int_var(
                    0,
                    len(thresholds),
                    f"constraint_{index}_{attribute}_points_{group_index}",
                )
                model.add_allowed_assignments(
                    [count, points],
                    [
                        (size, sum(size >= threshold for threshold in thresholds))
                        for size in range(slot_count + 1)
                    ],
                )
                contribution = model.new_int_var(
                    0,
                    slot_count * len(thresholds),
                    f"constraint_{index}_{attribute}_contribution_{group_index}",
                )
                model.add_multiplication_equality(contribution, [count, points])
                expressions.append(contribution)
        return sum(expressions)

    def _distinct_count(self, model, selected, items, attribute, slot_count, index):
        used = []
        for group_index, item_indexes in enumerate(
            self._groups(items, attribute).values()
        ):
            count = sum(selected[item_index] for item_index in item_indexes)
            present = model.new_bool_var(
                f"constraint_{index}_{attribute}_present_{group_index}"
            )
            model.add(count >= present)
            model.add(count <= slot_count * present)
            used.append(present)
        return sum(used)

    def _maximum_attribute_count(
        self, model, selected, items, attribute, slot_count, index
    ):
        counts = [
            sum(selected[item_index] for item_index in item_indexes)
            for item_indexes in self._groups(items, attribute).values()
        ]
        maximum = model.new_int_var(
            0, slot_count, f"constraint_{index}_{attribute}_maximum"
        )
        model.add_max_equality(maximum, counts)
        return maximum

    @staticmethod
    def _objective_expression(selected, items, slot_count, *, prefer_untradeable):
        ranks = {
            int(row["item_id"]): rank
            for rank, row in enumerate(
                sorted(items, key=lambda value: int(value["item_id"])), start=1
            )
        }
        maximum_rank_sum = slot_count * len(items)
        overall_weight = maximum_rank_sum + 1
        lower_span = 99 * slot_count * overall_weight + maximum_rank_sum
        value_weight = lower_span + 1
        maximum_value = sum(
            sorted(
                (int(row.get("tradeable_value") or 0) for row in items),
                reverse=True,
            )[:slot_count]
        )
        tradeable_count_weight = maximum_value * value_weight + lower_span + 1
        expression = 0
        for index, row in enumerate(items):
            coefficient = int(row.get("tradeable_value") or 0) * value_weight
            coefficient += int(row["overall"]) * overall_weight
            coefficient += ranks[int(row["item_id"])]
            if prefer_untradeable and row.get("tradeable"):
                coefficient += tradeable_count_weight
            expression += selected[index] * coefficient
        return expression

    @staticmethod
    def _objective_components(items):
        return {
            "tradeable_item_count": sum(1 for row in items if row.get("tradeable")),
            "tradeable_value": sum(
                int(row.get("tradeable_value") or 0) for row in items
            ),
            "total_overall": sum(int(row["overall"]) for row in items),
            "item_ids": [int(row["item_id"]) for row in items],
        }
