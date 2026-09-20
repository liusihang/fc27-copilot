import time
from collections import Counter, defaultdict

from ortools.sat.python import cp_model


QUALITY_RANKS = {"bronze": 1, "silver": 2, "gold": 3}
CHEMISTRY_THRESHOLDS = {
    "club_id": (2, 4, 7),
    "nation_id": (2, 5, 8),
    "league_id": (3, 5, 8),
}


def chemistry_score(items, slots):
    if len(items) != len(slots):
        raise ValueError("Chemistry requires one assigned field slot per item.")
    in_position = []
    for row, slot in zip(items, slots):
        positions = {str(value).upper() for value in row.get("positions") or []}
        in_position.append(str(slot).upper() in positions)

    counts_by_attribute = {}
    for attribute, thresholds in CHEMISTRY_THRESHOLDS.items():
        counts = defaultdict(int)
        for row, active in zip(items, in_position):
            if not active:
                continue
            value = row.get(attribute)
            if value is not None:
                counts[value] += 1
        counts_by_attribute[attribute] = counts

    per_player = []
    for row, active in zip(items, in_position):
        if not active:
            per_player.append(0)
            continue
        points = 0
        for attribute, thresholds in CHEMISTRY_THRESHOLDS.items():
            value = row.get(attribute)
            count = counts_by_attribute[attribute].get(value, 0)
            points += sum(count >= threshold for threshold in thresholds)
        per_player.append(min(3, points))
    return {
        "total": sum(per_player),
        "per_player": per_player,
        "in_position": in_position,
    }


STATUS_NAMES = {
    cp_model.OPTIMAL: "optimal",
    cp_model.FEASIBLE: "feasible",
    cp_model.INFEASIBLE: "infeasible",
    cp_model.MODEL_INVALID: "model_invalid",
    cp_model.UNKNOWN: "unknown",
}


class SbcOptimizer:
    def __init__(self, *, time_limit_seconds=120.0, search_workers=4):
        self.time_limit_seconds = float(time_limit_seconds)
        self.search_workers = max(1, int(search_workers))

    def solve(
        self,
        challenge,
        items,
        objective,
        max_solutions,
        *,
        exact_purchase_count=None,
        fixed_item_ids=None,
        hint_slot_item_ids=None,
    ):
        slot_count = int(challenge.get("player_count") or len(challenge["slots"]))
        model = cp_model.CpModel()
        selected = [model.new_bool_var(f"item_{row['item_id']}") for row in items]
        model.add(sum(selected) == slot_count)
        for item_indexes in self._groups(
            items, "base_player_ea_id", include_none=False
        ).values():
            if len(item_indexes) > 1:
                model.add(sum(selected[index] for index in item_indexes) <= 1)
        chemistry_model = None
        in_position = None
        chemistry_required = any(
            constraint["type"] in ("chemistry", "all_players_chemistry_points")
            for constraint in challenge["constraints"]
        )
        if chemistry_required:
            in_position = self._add_hall_slot_constraints(
                model, selected, items, challenge, slot_count
            )
            chemistry_model = self._build_chemistry_model(
                model, items, in_position, slot_count
            )

        indexes_by_item_id = {
            int(row["item_id"]): index for index, row in enumerate(items)
        }
        for item_id in objective.get("required_item_ids", []):
            model.add(selected[indexes_by_item_id[int(item_id)]] == 1)
        for item_id in fixed_item_ids or []:
            model.add(selected[indexes_by_item_id[int(item_id)]] == 1)

        tradeable_value = sum(
            selected[index] * int(row.get("tradeable_value") or 0)
            for index, row in enumerate(items)
        )
        if objective.get("max_tradeable_value") is not None:
            model.add(tradeable_value <= int(objective["max_tradeable_value"]))
        purchase_count = sum(
            selected[index]
            for index, row in enumerate(items)
            if row.get("source") == "market"
        )
        if exact_purchase_count is not None:
            model.add(purchase_count == int(exact_purchase_count))

        self._add_challenge_constraints(
            model,
            selected,
            items,
            challenge,
            slot_count,
            chemistry_model,
        )
        self._add_solution_hint(
            model,
            selected,
            in_position,
            items,
            challenge["slots"][:slot_count],
            hint_slot_item_ids,
        )
        deadline = time.monotonic() + self.time_limit_seconds
        histograms = self._rating_histograms(model, selected, items, slot_count)
        proof_stages = []
        incumbent = None
        terminal_status = "unknown"
        rating_optimality = "UNPROVEN"

        ratings = sorted(histograms, reverse=True)
        for start in range(0, len(ratings), 10):
            tiers = ratings[start : start + 10]
            base = slot_count + 1
            objective_upper = base ** len(tiers) - 1
            stage_objective = model.new_int_var(
                0, objective_upper, f"rating_stage_{start // 10}"
            )
            model.add(
                stage_objective
                == sum(
                    base ** (len(tiers) - tier_index - 1) * histograms[rating]
                    for tier_index, rating in enumerate(tiers)
                )
            )
            model.minimize(stage_objective)
            status, solver = self._solve_model(model, deadline)
            terminal_status = STATUS_NAMES.get(status, "unknown")
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                break
            incumbent = self._candidate(
                solver,
                selected,
                in_position,
                items,
                challenge["slots"][:slot_count],
                rating_optimality="UNPROVEN",
                policy_optimality="UNPROVEN",
                proof_stages=proof_stages,
            )
            upper = int(solver.value(stage_objective))
            lower = self._integer_objective_lower_bound(solver)
            proven = status == cp_model.OPTIMAL and lower == upper
            proof_stages.append(
                {
                    "ratings": tiers,
                    "lower_bound": lower,
                    "upper_bound": upper,
                    "proven": proven,
                }
            )
            if not proven:
                break
            for rating in tiers:
                model.add(histograms[rating] == solver.value(histograms[rating]))
        else:
            rating_optimality = "PROVEN"

        if incumbent is None:
            return {
                "status": (
                    "INFEASIBLE_PROVEN"
                    if terminal_status == "infeasible"
                    else "UNKNOWN_NO_SOLUTION_FOUND"
                ),
                "complete": terminal_status == "infeasible",
                "proof": {
                    "rating_optimality": "UNPROVEN",
                    "policy_optimality": "UNPROVEN",
                    "stages": proof_stages,
                },
                "solutions": [],
            }

        if rating_optimality != "PROVEN":
            incumbent["proof"]["stages"] = proof_stages
            return {
                "status": "FEASIBLE_UNPROVEN",
                "complete": False,
                "proof": incumbent["proof"],
                "solutions": [incumbent],
            }

        policy_optimality = "PROVEN"
        policy_stages = self._policy_objectives(
            model, selected, items, slot_count
        )
        for name, expression in policy_stages[:-1]:
            model.minimize(expression)
            status, solver = self._solve_model(model, deadline)
            terminal_status = STATUS_NAMES.get(status, "unknown")
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                policy_optimality = "UNPROVEN"
                break
            incumbent = self._candidate(
                solver,
                selected,
                in_position,
                items,
                challenge["slots"][:slot_count],
                rating_optimality="PROVEN",
                policy_optimality="UNPROVEN",
                proof_stages=proof_stages,
            )
            value = int(solver.value(expression))
            lower = self._integer_objective_lower_bound(solver)
            if status != cp_model.OPTIMAL or lower != value:
                policy_optimality = "UNPROVEN"
                break
            model.add(expression == value)

        if policy_optimality != "PROVEN":
            incumbent["proof"]["stages"] = proof_stages
            return {
                "status": "FEASIBLE_UNPROVEN",
                "complete": False,
                "proof": incumbent["proof"],
                "solutions": [incumbent],
            }

        identity_expression = policy_stages[-1][1]
        solutions = []
        enumeration_complete = False
        while len(solutions) < max_solutions:
            model.minimize(identity_expression)
            status, solver = self._solve_model(model, deadline)
            terminal_status = STATUS_NAMES.get(status, "unknown")
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                enumeration_complete = status == cp_model.INFEASIBLE
                break
            candidate = self._candidate(
                solver,
                selected,
                in_position,
                items,
                challenge["slots"][:slot_count],
                rating_optimality="PROVEN",
                policy_optimality=(
                    "PROVEN" if status == cp_model.OPTIMAL else "UNPROVEN"
                ),
                proof_stages=proof_stages,
            )
            solutions.append(candidate)
            chosen_indexes = [
                index for index, variable in enumerate(selected) if solver.value(variable)
            ]
            model.add(sum(selected[index] for index in chosen_indexes) <= slot_count - 1)
            if status != cp_model.OPTIMAL:
                break

        result_status = (
            "OPTIMAL_PROVEN"
            if solutions and all(
                value["proof"]["policy_optimality"] == "PROVEN"
                for value in solutions
            )
            else "FEASIBLE_UNPROVEN"
        )
        return {
            "status": result_status,
            "complete": enumeration_complete,
            "proof": {
                "rating_optimality": "PROVEN",
                "policy_optimality": (
                    "PROVEN" if result_status == "OPTIMAL_PROVEN" else "UNPROVEN"
                ),
                "stages": proof_stages,
            },
            "solutions": solutions,
        }

    @staticmethod
    def _add_solution_hint(
        model, selected, in_position, items, slots, hint_slot_item_ids
    ):
        if not hint_slot_item_ids or len(hint_slot_item_ids) != len(slots):
            return
        hinted_slot_by_item = {
            int(item_id): slot_index
            for slot_index, item_id in enumerate(hint_slot_item_ids)
        }
        available_ids = {int(row["item_id"]) for row in items}
        if not set(hinted_slot_by_item).issubset(available_ids):
            return
        for item_index, row in enumerate(items):
            item_id = int(row["item_id"])
            chosen = item_id in hinted_slot_by_item
            model.add_hint(selected[item_index], 1 if chosen else 0)
            if in_position is None:
                continue
            if not chosen:
                model.add_hint(in_position[item_index], 0)
                continue
            slot = str(slots[hinted_slot_by_item[item_id]]).upper()
            positions = {
                str(value).upper() for value in row.get("positions") or []
            }
            model.add_hint(in_position[item_index], 1 if slot in positions else 0)

    def _solve_model(self, model, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return cp_model.UNKNOWN, None
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = remaining
        solver.parameters.num_search_workers = self.search_workers
        solver.parameters.random_seed = 0
        solver.parameters.absolute_gap_limit = 0.0
        solver.parameters.relative_gap_limit = 0.0
        status = solver.solve(model)
        return status, solver

    @staticmethod
    def _integer_objective_lower_bound(solver):
        response = solver.response_proto
        if not hasattr(response, "inner_objective_lower_bound"):
            raise RuntimeError(
                "The pinned OR-Tools build does not expose an integer objective bound."
            )
        return int(response.inner_objective_lower_bound)

    @staticmethod
    def _rating_histograms(model, selected, items, slot_count):
        histograms = {}
        for rating in sorted({int(row["overall"]) for row in items}, reverse=True):
            variable = model.new_int_var(0, slot_count, f"rating_count_{rating}")
            model.add(
                variable
                == sum(
                    selected[index]
                    for index, row in enumerate(items)
                    if int(row["overall"]) == rating
                )
            )
            histograms[rating] = variable
        return histograms

    @staticmethod
    def _bounded_sum(model, name, terms, upper):
        variable = model.new_int_var(0, max(0, int(upper)), name)
        model.add(variable == sum(terms))
        return variable

    def _policy_objectives(self, model, selected, items, slot_count):
        purchase_terms = [
            selected[index]
            for index, row in enumerate(items)
            if row.get("source") == "market"
        ]
        tradeable_terms = [
            selected[index]
            for index, row in enumerate(items)
            if row.get("source") != "market" and row.get("tradeable")
        ]
        owned_costs = [
            int(row.get("tradeable_value") or 0)
            if row.get("source") != "market"
            else 0
            for row in items
        ]
        purchase_costs = [
            int(row.get("purchase_price") or 0)
            if row.get("source") == "market"
            else 0
            for row in items
        ]
        ranks = {
            int(row["item_id"]): rank
            for rank, row in enumerate(
                sorted(items, key=lambda value: int(value["item_id"])), start=1
            )
        }
        purchase_count = self._bounded_sum(
            model, "policy_purchase_count", purchase_terms, slot_count
        )
        tradeable_count = self._bounded_sum(
            model, "policy_tradeable_count", tradeable_terms, slot_count
        )
        owned_cost = self._bounded_sum(
            model,
            "policy_owned_cost",
            [selected[index] * owned_costs[index] for index in range(len(items))],
            sum(sorted(owned_costs, reverse=True)[:slot_count]),
        )
        purchase_cost = self._bounded_sum(
            model,
            "policy_purchase_cost",
            [selected[index] * purchase_costs[index] for index in range(len(items))],
            sum(sorted(purchase_costs, reverse=True)[:slot_count]),
        )
        identity = self._bounded_sum(
            model,
            "policy_identity",
            [
                selected[index] * ranks[int(row["item_id"])]
                for index, row in enumerate(items)
            ],
            slot_count * len(items),
        )
        return [
            ("purchase_count", purchase_count),
            ("owned_tradeable_count", tradeable_count),
            ("owned_opportunity_cost", owned_cost),
            ("estimated_purchase_cost", purchase_cost),
            ("deterministic_identity", identity),
        ]

    def _candidate(
        self,
        solver,
        selected,
        in_position,
        items,
        slots,
        *,
        rating_optimality,
        policy_optimality,
        proof_stages,
    ):
        chosen_indexes = [
            index for index, variable in enumerate(selected) if solver.value(variable)
        ]
        chosen_items = [items[index] for index in chosen_indexes]
        slot_item_ids = self._recover_slot_item_ids(
            solver, selected, in_position, items, slots
        )
        return {
            "status": (
                "OPTIMAL_PROVEN"
                if rating_optimality == "PROVEN" and policy_optimality == "PROVEN"
                else "FEASIBLE_UNPROVEN"
            ),
            "item_ids": sorted(int(row["item_id"]) for row in chosen_items),
            "slot_item_ids": slot_item_ids,
            "objective_value": int(round(solver.objective_value)),
            "objective_components": self._objective_components(chosen_items),
            "proof": {
                "rating_optimality": rating_optimality,
                "policy_optimality": policy_optimality,
                "pareto_status": "NONDOMINATED_WITHIN_RETURNED",
                "frontier_complete": False,
                "stages": list(proof_stages),
            },
            "wall_time_seconds": solver.wall_time,
        }

    @staticmethod
    def _add_hall_slot_constraints(model, selected, items, challenge, slot_count):
        slots = [str(value).upper() for value in challenge.get("slots") or []]
        slots = slots[:slot_count]
        position_types = sorted(set(slots))
        position_index = {
            position: index for index, position in enumerate(position_types)
        }
        capacities = Counter(slots)
        full_mask = (1 << len(position_types)) - 1
        demand_terms = defaultdict(list)
        in_position = []

        for item_index, row in enumerate(items):
            positions = {str(value).upper() for value in row.get("positions") or []}
            position_mask = 0
            for position in positions:
                if position in position_index:
                    position_mask |= 1 << position_index[position]
            variable = model.new_bool_var(f"in_position_{item_index}")
            model.add(variable <= selected[item_index])
            in_position.append(variable)
            demand_terms[position_mask].append(variable)
            demand_terms[full_mask ^ position_mask].append(
                selected[item_index] - variable
            )

        demands = {}
        for mask, terms in demand_terms.items():
            demand = model.new_int_var(0, slot_count, f"slot_demand_{mask}")
            model.add(demand == sum(terms))
            demands[mask] = demand

        for allowed_mask in range(1 << len(position_types)):
            contained_demands = [
                demand
                for mask, demand in demands.items()
                if mask & ~allowed_mask == 0
            ]
            capacity = sum(
                capacities[position]
                for position, index in position_index.items()
                if allowed_mask & (1 << index)
            )
            model.add(sum(contained_demands) <= capacity)
        return in_position

    @staticmethod
    def _recover_slot_item_ids(solver, selected, in_position, items, slots):
        chosen_indexes = [
            index for index, variable in enumerate(selected) if solver.value(variable)
        ]
        if in_position is None:
            chosen_indexes.sort(key=lambda index: int(items[index]["item_id"]))
            return [int(items[index]["item_id"]) for index in chosen_indexes]

        allowed_by_item = {}
        for item_index in chosen_indexes:
            positions = {
                str(value).upper() for value in items[item_index].get("positions") or []
            }
            must_be_in_position = bool(solver.value(in_position[item_index]))
            allowed = [
                slot_index
                for slot_index, slot in enumerate(slots)
                if (str(slot).upper() in positions) == must_be_in_position
            ]
            allowed_by_item[item_index] = allowed

        ordered_items = sorted(
            chosen_indexes,
            key=lambda index: (
                len(allowed_by_item[index]),
                int(items[index]["item_id"]),
            ),
        )
        assignment = {}
        occupied = set()

        def assign(offset):
            if offset == len(ordered_items):
                return True
            item_index = ordered_items[offset]
            for slot_index in allowed_by_item[item_index]:
                if slot_index in occupied:
                    continue
                occupied.add(slot_index)
                assignment[item_index] = slot_index
                if assign(offset + 1):
                    return True
                assignment.pop(item_index, None)
                occupied.remove(slot_index)
            return False

        if not assign(0):
            raise RuntimeError(
                "The Hall slot model returned a state without a concrete slot matching."
            )
        by_slot = [None] * len(slots)
        for item_index, slot_index in assignment.items():
            by_slot[slot_index] = int(items[item_index]["item_id"])
        return by_slot

    def _add_challenge_constraints(
        self,
        model,
        selected,
        items,
        challenge,
        slot_count,
        chemistry_model,
    ):
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
                elif constraint["overall_operator"] == "max":
                    matching_indexes = [
                        item_index
                        for item_index, row in enumerate(items)
                        if int(row["overall"]) <= int(constraint["overall"])
                    ]
                else:
                    matching_indexes = [
                        item_index
                        for item_index, row in enumerate(items)
                        if int(row["overall"]) == int(constraint["overall"])
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
                values = set(
                    constraint[
                        {
                            "specific_nation_count": "nation_ids",
                            "specific_league_count": "league_ids",
                            "specific_club_count": "club_ids",
                        }[kind]
                    ]
                )
                matching = sum(
                    selected[item_index]
                    for item_index, row in enumerate(items)
                    if row.get(attribute) in values
                )
                self._add_comparison(
                    model, matching, constraint["operator"], int(constraint["value"])
                )
            elif kind in (
                "same_nation_count",
                "same_league_count",
                "same_club_count",
            ):
                attribute = {
                    "same_nation_count": "nation_id",
                    "same_league_count": "league_id",
                    "same_club_count": "club_id",
                }[kind]
                maximum = self._maximum_attribute_count(
                    model, selected, items, attribute, slot_count, index
                )
                self._add_comparison(
                    model, maximum, constraint["operator"], int(constraint["value"])
                )
            elif kind == "chemistry":
                self._add_comparison(
                    model,
                    chemistry_model["total"],
                    constraint["operator"],
                    int(constraint["value"]),
                )
            elif kind == "all_players_chemistry_points":
                value = int(constraint["value"])
                for item_index, chemistry in enumerate(
                    chemistry_model["per_item"]
                ):
                    if constraint["operator"] == "min":
                        model.add(chemistry >= value * selected[item_index])
                    elif constraint["operator"] == "max":
                        model.add(chemistry <= value)
                    else:
                        model.add(chemistry == value * selected[item_index])

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
            value = row.get(attribute)
            if not include_none and value is None:
                continue
            key = value if value is not None else ("missing", index)
            groups[key].append(index)
        return groups

    def _build_chemistry_model(self, model, items, in_position, slot_count):
        group_points = {}
        for attribute, thresholds in CHEMISTRY_THRESHOLDS.items():
            groups = self._groups(items, attribute, include_none=False)
            group_points[attribute] = {}
            for group_index, (value, item_indexes) in enumerate(groups.items()):
                count = sum(in_position[item_index] for item_index in item_indexes)
                points = model.new_int_var(
                    0,
                    len(thresholds),
                    f"chemistry_{attribute}_points_{group_index}",
                )
                model.add_allowed_assignments(
                    [count, points],
                    [
                        (size, sum(size >= threshold for threshold in thresholds))
                        for size in range(slot_count + 1)
                    ],
                )
                group_points[attribute][value] = points

        per_item = []
        for item_index, row in enumerate(items):
            attribute_points = [
                group_points[attribute].get(row.get(attribute), 0)
                for attribute in CHEMISTRY_THRESHOLDS
            ]
            raw_points = model.new_int_var(0, 9, f"chemistry_raw_{item_index}")
            model.add(raw_points == sum(attribute_points))
            capped_points = model.new_int_var(0, 3, f"chemistry_capped_{item_index}")
            model.add_min_equality(capped_points, [raw_points, 3])
            chemistry = model.new_int_var(0, 3, f"chemistry_item_{item_index}")
            model.add_multiplication_equality(
                chemistry, [capped_points, in_position[item_index]]
            )
            per_item.append(chemistry)
        return {"total": sum(per_item), "per_item": per_item}

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
    def _objective_components(items):
        rating_vector = sorted(
            (int(row["overall"]) for row in items), reverse=True
        )
        return {
            "rating_vector": rating_vector,
            "max_overall": max(rating_vector, default=0),
            "purchase_count": sum(1 for row in items if row.get("source") == "market"),
            "purchase_value": sum(int(row.get("purchase_price") or 0) for row in items),
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
