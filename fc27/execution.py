import json
from datetime import datetime, timezone

from .errors import FC27Error
from .policy import ACTION_TYPES


PURCHASE_TYPES = ("buy_now", "place_bid")
ITEM_TYPES = ("list_item", "move_item")
SBC_TYPES = ("save_sbc_squad", "submit_sbc")
SQUAD_TYPES = ("set_active_squad", "save_squad", "save_squad_tactics")


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class ExecutionService:
    def __init__(self, runtime, policy_store, dispatcher=None):
        self.runtime = runtime
        self.policy_store = policy_store
        self.dispatcher = dispatcher

    def execute(self, request):
        request = request or {}
        batch_id = request.get("batch_id")
        if not isinstance(batch_id, str) or not batch_id.strip():
            raise FC27Error("INVALID_ACTION_BATCH", "batch_id must be a non-empty string.")
        actions = request.get("actions")
        if not isinstance(actions, list) or not actions:
            raise FC27Error("INVALID_ACTION_BATCH", "actions must contain at least one action.")
        normalized_actions = [self._normalize_action(action) for action in actions]
        replay = self._existing_batch(batch_id)
        if replay is not None:
            self._validate_replay(replay, request, normalized_actions)
            replay["replayed"] = True
            return replay

        policy = self.policy_store.load()
        if policy["execution_mode"] == "observe":
            raise FC27Error(
                "EXECUTION_DISABLED",
                "Account actions are disabled while policy mode is observe.",
                recovery="Review policy values and explicitly change execution_mode before retrying.",
            )
        if policy["execution_mode"] == "suggest" and request.get("confirmed") is not True:
            raise FC27Error(
                "CONFIRMATION_REQUIRED",
                "Suggest mode requires confirmed=true for this exact batch.",
                recovery="Review the exact action list, then retry the same batch with confirmed=true.",
            )

        self._validate_batch(request, normalized_actions, policy)
        self._insert_audit(request, normalized_actions, policy["execution_mode"])
        stop_on_error = request.get("stop_on_error", True) is not False
        completed = 0
        failures = 0
        stopped = False
        for sequence_no, action in enumerate(normalized_actions):
            if stopped:
                self._finish_action(
                    action["action_id"],
                    "skipped",
                    error_code="BATCH_STOPPED",
                    error_message="A previous action failed and stop_on_error is enabled.",
                )
                continue
            try:
                if self.dispatcher is None:
                    raise FC27Error(
                        "ACTION_TYPE_NOT_READY",
                        f"Execution handler is not implemented for {action['type']}.",
                        recovery="Keep policy in observe mode until the action implementation is accepted.",
                    )
                self._start_action(action["action_id"])
                dispatch_action = {
                    **action,
                    "_expected_sync_id": request.get("expected_sync_id"),
                }
                result = self.dispatcher(dispatch_action)
                self._finish_action(action["action_id"], "complete", result=result)
                completed += 1
            except FC27Error as error:
                self._finish_action(
                    action["action_id"],
                    "failed",
                    error_code=error.code,
                    error_message=error.message,
                    result={"error": error.as_dict()},
                )
                failures += 1
                stopped = stop_on_error
        status = "complete" if failures == 0 else ("partial" if completed else "failed")
        self._finish_batch(batch_id, status)
        return self._existing_batch(batch_id)

    def _validate_batch(self, request, actions, policy):
        expected_sync_id = request.get("expected_sync_id")
        sync_required = self._requires_sync(actions)
        if sync_required and (
            isinstance(expected_sync_id, bool) or not isinstance(expected_sync_id, int)
        ):
            raise FC27Error(
                "INVALID_ACTION_BATCH",
                "expected_sync_id is required for inventory, market, SBC, and squad slot actions.",
            )
        if expected_sync_id is not None and (
            isinstance(expected_sync_id, bool) or not isinstance(expected_sync_id, int)
        ):
            raise FC27Error(
                "INVALID_ACTION_BATCH", "expected_sync_id must be an integer."
            )
        state = self.runtime.account_summary()
        current_sync_id = state.get("last_full_sync_id") if state else None
        if expected_sync_id is not None and expected_sync_id != current_sync_id:
            raise FC27Error(
                "STALE_CLUB_STATE",
                f"Expected sync {expected_sync_id}, current complete sync is {current_sync_id}.",
                retryable=True,
                recovery="Run FC27:sync_club, inspect the new state, and rebuild the action list.",
            )
        if len(actions) > policy["maximum_batch_actions"]:
            self._policy_denied(
                f"Batch contains {len(actions)} actions; policy allows {policy['maximum_batch_actions']}."
            )
        action_ids = [action["action_id"] for action in actions]
        idempotency_keys = [action["idempotency_key"] for action in actions]
        if len(action_ids) != len(set(action_ids)):
            raise FC27Error("IDEMPOTENCY_CONFLICT", "action_id values must be unique in a batch.")
        if len(idempotency_keys) != len(set(idempotency_keys)):
            raise FC27Error(
                "IDEMPOTENCY_CONFLICT", "idempotency_key values must be unique in a batch."
            )
        submit_solution_ids = [
            action["solution_id"]
            for action in actions
            if action["type"] == "submit_sbc"
        ]
        if len(submit_solution_ids) != len(set(submit_solution_ids)):
            raise FC27Error(
                "SBC_SUBMIT_ALREADY_ATTEMPTED",
                "A batch cannot contain multiple submit actions for the same SBC solution.",
                recovery="Submit one exact solution in one separately confirmed batch.",
            )
        with self.runtime.connect() as connection:
            existing_action_ids = {
                row["action_id"]: row["batch_id"]
                for row in connection.execute(
                    f"SELECT action_id, batch_id FROM actions WHERE action_id IN ({','.join('?' for _ in action_ids)})",
                    action_ids,
                )
            }
            if existing_action_ids:
                raise FC27Error(
                    "IDEMPOTENCY_CONFLICT",
                    "One or more action IDs already belong to another batch.",
                    recovery="Replay the original batch_id or use new action IDs.",
                    details={"existing": existing_action_ids},
                )
            existing_keys = {
                row["idempotency_key"]: row["batch_id"]
                for row in connection.execute(
                    f"SELECT idempotency_key, batch_id FROM actions WHERE idempotency_key IN ({','.join('?' for _ in idempotency_keys)})",
                    idempotency_keys,
                )
            }
            if existing_keys:
                raise FC27Error(
                    "IDEMPOTENCY_CONFLICT",
                    "One or more idempotency keys already belong to another batch.",
                    recovery="Replay the original batch_id or use new keys for different actions.",
                    details={"existing": existing_keys},
                )
            protected_rows = {
                int(row["item_id"])
                for row in connection.execute(
                    "SELECT item_id FROM club_items WHERE protected = 1"
                )
            }
            item_rows = {
                int(row["item_id"]): dict(row)
                for row in connection.execute("SELECT * FROM club_items")
            }
            holding_counts = {
                int(row["card_ea_id"]): int(row["item_count"])
                for row in connection.execute(
                    "SELECT card_ea_id, COUNT(*) AS item_count FROM club_items GROUP BY card_ea_id"
                )
            }
            tradepile_count = connection.execute(
                "SELECT COUNT(*) FROM club_items WHERE location = 'tradepile'"
            ).fetchone()[0]
            daily_spend = connection.execute(
                """SELECT COALESCE(SUM(-coin_delta), 0) FROM coin_transactions
                   WHERE kind = 'purchase' AND coin_delta < 0
                     AND observed_at >= ?""",
                (datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z"),),
            ).fetchone()[0]

        protected = protected_rows | set(policy["protected_item_ids"])
        batch_spend = 0
        planned_card_counts = {}
        planned_tradepile_additions = 0
        for action in actions:
            if action["type"] not in policy["allowed_action_types"]:
                self._policy_denied(f"Action type {action['type']} is not allowed by policy.")
            item_id = action.get("item_id")
            item = item_rows.get(item_id) if item_id is not None else None
            if action["type"] in ITEM_TYPES and item is None:
                raise FC27Error(
                    "ITEM_NOT_FOUND",
                    f"Owned item {item_id} is absent from the latest complete state.",
                    retryable=True,
                    recovery="Run FC27:sync_club and choose an item from the new state.",
                )
            if item_id is not None and item_id in protected:
                self._policy_denied(f"Item {item_id} is protected.")
            if action["type"] in SBC_TYPES:
                solution = self.runtime.get_sbc_solution(action["solution_id"])
                if solution is None:
                    raise FC27Error(
                        "SBC_SOLUTION_NOT_FOUND",
                        f"SBC solution {action['solution_id']} was not found.",
                    )
                if str(solution["challenge_id"]) != str(action["challenge_id"]):
                    raise FC27Error("INVALID_ACTION", "SBC solution and challenge_id do not match.")
                if solution["item_ids"] != action["item_ids"]:
                    raise FC27Error("INVALID_ACTION", "SBC action item_ids must exactly match the persisted solution order.")
                if action["type"] == "submit_sbc":
                    self.runtime.require_new_sbc_submit_attempt(action["solution_id"])
                missing = [value for value in action["item_ids"] if value not in item_rows]
                if missing:
                    raise FC27Error(
                        "ITEM_NOT_FOUND",
                        "One or more SBC solution items are absent from the latest complete state.",
                        recovery="Sync the club and generate a new solution.",
                        details={"missing_item_ids": missing},
                    )
                denied = sorted(set(action["item_ids"]) & protected)
                if denied:
                    self._policy_denied(f"SBC solution contains protected items: {denied}.")
                if action["type"] == "save_sbc_squad":
                    self.runtime.require_new_sbc_save_attempt(action["solution_id"])
                if action["type"] == "submit_sbc" and solution["status"] != "saved":
                    raise FC27Error(
                        "SBC_NOT_ELIGIBLE",
                        "SBC submission requires a solution with saved EA readback evidence.",
                        recovery="Save and reread the exact solution before submitting it.",
                    )
                if action["type"] == "submit_sbc":
                    execution_evidence = solution.get("validation", {}).get("execution", {})
                    saved_sync_id = execution_evidence.get("saved_at_sync_id")
                    if saved_sync_id != expected_sync_id:
                        raise FC27Error(
                            "STALE_CLUB_STATE",
                            "The saved SBC squad was not read back against the current complete sync.",
                            retryable=True,
                            recovery="Regenerate and save the exact solution against the current sync before submitting.",
                            details={"saved_sync_id": saved_sync_id, "expected_sync_id": expected_sync_id},
                        )
                    if execution_evidence.get("ea_eligible") is not True:
                        raise FC27Error(
                            "SBC_NOT_ELIGIBLE",
                            "The saved SBC squad does not have positive EA eligibility readback.",
                            recovery="Inspect failed EA requirements, generate a new solution, and save it again.",
                        )
                    ea_readback = execution_evidence.get("ea") or {}
                    freshness = ea_readback.get("freshness") or {}
                    eligibility = (
                        ea_readback.get("squad", {}).get("eligibility_evidence") or {}
                    )
                    if (
                        execution_evidence.get("source") != "ea_webapp_fresh"
                        or freshness.get("sets_requested") is not True
                        or freshness.get("challenges_requested") is not True
                        or freshness.get("challenge_loaded") is not True
                        or eligibility.get("source") != "ea_challenge_requirements"
                        or eligibility.get("identity_match") is not True
                        or eligibility.get("all_requirements_met") is not True
                        or eligibility.get("submit_available") is not True
                    ):
                        raise FC27Error(
                            "SBC_NOT_ELIGIBLE",
                            "SBC submission requires fresh trusted EA save evidence.",
                            recovery="Run the guarded fresh verification for the saved action before submitting.",
                        )
                    self.runtime.require_completed_sbc_save(
                        action["solution_id"],
                        expected_sync_id,
                        action["set_id"],
                        action["challenge_id"],
                        action["item_ids"],
                    )
            if action["type"] in SQUAD_TYPES:
                for value in action.get("slot_updates") or []:
                    item_id = value.get("item_id")
                    if item_id is None:
                        continue
                    item = item_rows.get(item_id)
                    if item is None:
                        raise FC27Error(
                            "ITEM_NOT_FOUND",
                            f"Squad item {item_id} is absent from the latest complete state.",
                            retryable=True,
                            recovery="Run FC27:sync_club and rebuild the squad action from current item IDs.",
                        )
                    if item_id in protected:
                        self._policy_denied(f"Squad item {item_id} is protected.")
                    loan_uses = item.get("loan_uses_remaining")
                    if loan_uses is not None and int(loan_uses) >= 0:
                        self._policy_denied(f"Squad item {item_id} is a loan item.")
            if action["type"] == "list_item" and not item["tradeable"]:
                self._policy_denied(f"Item {item_id} is untradeable and cannot be listed.")
            if action["type"] in PURCHASE_TYPES:
                amount = action["max_price"] if action["type"] == "buy_now" else action["bid"]
                if amount > policy["maximum_single_purchase"]:
                    self._policy_denied(
                        f"Action {action['action_id']} amount {amount} exceeds maximum_single_purchase."
                    )
                batch_spend += amount
                card_ea_id = action["expected_card_ea_id"]
                planned_card_counts[card_ea_id] = planned_card_counts.get(card_ea_id, 0) + 1
            if action["type"] == "move_item" and item["location"] == action["destination"]:
                raise FC27Error(
                    "INVALID_ACTION",
                    f"Item {item_id} is already in {action['destination']}.",
                )
            if (
                action["type"] == "list_item" and item["location"] != "tradepile"
            ) or (
                action["type"] == "move_item"
                and action.get("destination") == "tradepile"
                and item["location"] != "tradepile"
            ):
                planned_tradepile_additions += 1
        if batch_spend > policy["maximum_batch_spend"]:
            self._policy_denied(
                f"Batch spend {batch_spend} exceeds maximum_batch_spend."
            )
        if daily_spend + batch_spend > policy["maximum_daily_spend"]:
            self._policy_denied(
                f"Daily spend would become {daily_spend + batch_spend}, above maximum_daily_spend."
            )
        coin_balance = state.get("coin_balance") if state else None
        if batch_spend and (
            coin_balance is None
            or coin_balance - batch_spend < policy["minimum_coin_reserve"]
        ):
            self._policy_denied("Batch would reduce coins below minimum_coin_reserve.")
        for card_ea_id, addition in planned_card_counts.items():
            resulting = holding_counts.get(card_ea_id, 0) + addition
            if resulting > policy["maximum_same_card_owned"]:
                self._policy_denied(
                    f"Card {card_ea_id} ownership would become {resulting}, above policy."
                )
        if tradepile_count + planned_tradepile_additions > policy["maximum_tradepile_usage"]:
            self._policy_denied(
                "Batch would exceed maximum_tradepile_usage."
            )

    @staticmethod
    def _normalize_action(action):
        if not isinstance(action, dict):
            raise FC27Error("INVALID_ACTION", "Each action must be a JSON object.")
        action_id = action.get("action_id")
        idempotency_key = action.get("idempotency_key")
        action_type = action.get("type")
        if not isinstance(action_id, str) or not action_id:
            raise FC27Error("INVALID_ACTION", "Each action requires a non-empty action_id.")
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise FC27Error("INVALID_ACTION", "Each action requires a non-empty idempotency_key.")
        if action_type not in ACTION_TYPES:
            raise FC27Error(
                "INVALID_ACTION",
                f"Unsupported action type: {action_type}",
                recovery=f"Use one of: {', '.join(ACTION_TYPES)}.",
            )
        normalized = dict(action)
        for key in ("item_id", "trade_id", "expected_card_ea_id"):
            if normalized.get(key) is not None:
                try:
                    normalized[key] = int(normalized[key])
                except (TypeError, ValueError) as error:
                    raise FC27Error(
                        "INVALID_ACTION", f"{key} must be an integer."
                    ) from error
        if action_type in PURCHASE_TYPES:
            required = ("trade_id", "expected_card_ea_id", "max_price" if action_type == "buy_now" else "bid")
            missing = [key for key in required if normalized.get(key) is None]
            if missing:
                raise FC27Error(
                    "INVALID_ACTION",
                    f"{action_type} is missing required fields: {', '.join(missing)}.",
                )
            price_key = "max_price" if action_type == "buy_now" else "bid"
            try:
                normalized[price_key] = int(normalized[price_key])
            except (TypeError, ValueError) as error:
                raise FC27Error(
                    "INVALID_ACTION", f"{price_key} must be an integer."
                ) from error
            if normalized[price_key] <= 0:
                raise FC27Error("INVALID_ACTION", f"{price_key} must be positive.")
        if action_type in ITEM_TYPES and normalized.get("item_id") is None:
            raise FC27Error("INVALID_ACTION", f"{action_type} requires item_id.")
        if action_type == "move_item":
            if normalized.get("destination") not in ("club", "tradepile"):
                raise FC27Error(
                    "INVALID_ACTION", "move_item destination must be club or tradepile."
                )
        if action_type == "list_item":
            missing = [
                key
                for key in ("starting_bid", "buy_now_price")
                if normalized.get(key) is None
            ]
            if missing:
                raise FC27Error(
                    "INVALID_ACTION",
                    f"list_item is missing required fields: {', '.join(missing)}.",
                )
            for key in ("starting_bid", "buy_now_price"):
                normalized[key] = int(normalized[key])
                if normalized[key] <= 0:
                    raise FC27Error("INVALID_ACTION", f"{key} must be positive.")
            if normalized["starting_bid"] > normalized["buy_now_price"]:
                raise FC27Error(
                    "INVALID_ACTION", "starting_bid cannot exceed buy_now_price."
                )
            normalized["duration"] = int(normalized.get("duration", 3600))
            if normalized["duration"] < 3600:
                raise FC27Error("INVALID_ACTION", "duration must be at least 3600 seconds.")
        if action_type in SBC_TYPES:
            for key in ("set_id", "challenge_id", "solution_id"):
                if normalized.get(key) in (None, ""):
                    raise FC27Error("INVALID_ACTION", f"{action_type} requires {key}.")
            normalized["set_id"] = str(normalized["set_id"])
            normalized["challenge_id"] = str(normalized["challenge_id"])
            item_ids = normalized.get("item_ids")
            if not isinstance(item_ids, list) or not 1 <= len(item_ids) <= 11:
                raise FC27Error(
                    "INVALID_ACTION",
                    f"{action_type} requires between 1 and 11 ordered item_ids.",
                )
            try:
                normalized["item_ids"] = [int(value) for value in item_ids]
            except (TypeError, ValueError) as error:
                raise FC27Error("INVALID_ACTION", "SBC item_ids must be integers.") from error
            if len(set(normalized["item_ids"])) != len(normalized["item_ids"]):
                raise FC27Error("INVALID_ACTION", "SBC item_ids must be unique.")
        if action_type in SQUAD_TYPES:
            for key in ("squad_id",):
                try:
                    normalized[key] = int(normalized[key])
                except (KeyError, TypeError, ValueError) as error:
                    raise FC27Error("INVALID_ACTION", f"{action_type} requires integer {key}.") from error
            expected_hash = normalized.get("expected_squad_hash")
            if not isinstance(expected_hash, str) or len(expected_hash) != 64 or any(
                character not in "0123456789abcdef" for character in expected_hash
            ):
                raise FC27Error(
                    "INVALID_ACTION",
                    f"{action_type} requires the 64-character expected_squad_hash from squad_query.",
                )
        if action_type == "save_squad":
            if normalized.get("formation_id") is None and not normalized.get("slot_updates"):
                raise FC27Error(
                    "INVALID_ACTION", "save_squad requires formation_id or slot_updates."
                )
            if normalized.get("formation_id") is not None:
                normalized["formation_id"] = int(normalized["formation_id"])
            updates = normalized.get("slot_updates") or []
            if not isinstance(updates, list) or len(updates) > 24:
                raise FC27Error("INVALID_ACTION", "slot_updates must contain at most 24 entries.")
            normalized_updates = []
            for value in updates:
                if not isinstance(value, dict):
                    raise FC27Error("INVALID_ACTION", "Each slot update must be an object.")
                slot_index = int(value.get("slot_index"))
                if not 0 <= slot_index <= 23:
                    raise FC27Error("INVALID_ACTION", "slot_index must be between 0 and 23.")
                item_id = value.get("item_id")
                normalized_updates.append(
                    {"slot_index": slot_index, "item_id": None if item_id is None else int(item_id)}
                )
            if len({value["slot_index"] for value in normalized_updates}) != len(normalized_updates):
                raise FC27Error("INVALID_ACTION", "slot_updates cannot repeat a slot_index.")
            item_ids = [value["item_id"] for value in normalized_updates if value["item_id"] is not None]
            if len(item_ids) != len(set(item_ids)):
                raise FC27Error("INVALID_ACTION", "slot_updates cannot assign one item to multiple slots.")
            normalized["slot_updates"] = normalized_updates
        if action_type == "save_squad_tactics":
            mutable = (
                "name",
                "formation_id",
                "defensive_style",
                "defensive_line_height",
                "build_up_play_style",
                "active",
                "instructions",
            )
            has_change = "active" in action or any(
                action.get(key) not in (None, [])
                for key in mutable
                if key != "active"
            )
            try:
                normalized["tactic_id"] = int(normalized["tactic_id"])
            except (KeyError, TypeError, ValueError) as error:
                raise FC27Error("INVALID_ACTION", "save_squad_tactics requires tactic_id.") from error
            if not 6 <= normalized["tactic_id"] <= 10:
                raise FC27Error("INVALID_ACTION", "tactic_id must be a Web App tactics slot from 6 through 10.")
            numeric_ranges = {
                "defensive_style": (0, 3),
                "defensive_line_height": (1, 100),
                "build_up_play_style": (0, 2),
            }
            for key, (minimum, maximum) in numeric_ranges.items():
                if normalized.get(key) is None:
                    continue
                normalized[key] = int(normalized[key])
                if not minimum <= normalized[key] <= maximum:
                    raise FC27Error("INVALID_ACTION", f"{key} must be between {minimum} and {maximum}.")
            if normalized.get("formation_id") is not None:
                normalized["formation_id"] = int(normalized["formation_id"])
            if "active" in normalized and not isinstance(normalized["active"], bool):
                raise FC27Error("INVALID_ACTION", "active must be a boolean.")
            instructions = normalized.get("instructions") or []
            normalized_instructions = []
            for value in instructions:
                normalized_instructions.append(
                    {
                        "slot_index": int(value["slot_index"]),
                        "position_id": int(value["position_id"]),
                        "role_id": int(value["role_id"]),
                        "variation_id": int(value["variation_id"]),
                    }
                )
            if any(not 0 <= value["slot_index"] <= 10 for value in normalized_instructions):
                raise FC27Error("INVALID_ACTION", "Tactic slot_index must be between 0 and 10.")
            if len({value["slot_index"] for value in normalized_instructions}) != len(normalized_instructions):
                raise FC27Error("INVALID_ACTION", "Tactic instructions cannot repeat a slot_index.")
            normalized["instructions"] = normalized_instructions
            if not has_change:
                raise FC27Error("INVALID_ACTION", "save_squad_tactics requires at least one change.")
        return normalized

    @staticmethod
    def _requires_sync(actions):
        for action in actions:
            if action["type"] not in SQUAD_TYPES:
                return True
            if action["type"] == "save_squad" and action.get("slot_updates"):
                return True
        return False

    def _insert_audit(self, request, actions, execution_mode):
        created_at = utc_now()
        with self.runtime.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO action_batches(
                     batch_id, execution_mode, expected_sync_id, created_at, status
                   ) VALUES (?, ?, ?, ?, 'running')""",
                (
                    request["batch_id"],
                    execution_mode,
                    request.get("expected_sync_id"),
                    created_at,
                ),
            )
            for sequence_no, action in enumerate(actions):
                connection.execute(
                    """INSERT INTO actions(
                         action_id, batch_id, sequence_no, action_type,
                         idempotency_key, item_id, trade_id, card_ea_id,
                         params_json, status
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                    (
                        action["action_id"],
                        request["batch_id"],
                        sequence_no,
                        action["type"],
                        action["idempotency_key"],
                        action.get("item_id"),
                        action.get("trade_id"),
                        action.get("expected_card_ea_id", action.get("card_ea_id")),
                        canonical_json(action),
                    ),
                )
            connection.commit()

    def _existing_batch(self, batch_id):
        with self.runtime.connect() as connection:
            batch = connection.execute(
                "SELECT * FROM action_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            if batch is None:
                return None
            actions = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM actions WHERE batch_id = ? ORDER BY sequence_no",
                    (batch_id,),
                )
            ]
        for action in actions:
            action["params"] = json.loads(action.pop("params_json"))
            result_json = action.pop("result_json")
            action["result"] = json.loads(result_json) if result_json is not None else None
        return {"batch": dict(batch), "actions": actions, "replayed": False}

    @staticmethod
    def _validate_replay(existing, request, actions):
        batch = existing["batch"]
        stored = [action["params"] for action in existing["actions"]]
        if batch["expected_sync_id"] != request.get("expected_sync_id") or stored != actions:
            raise FC27Error(
                "IDEMPOTENCY_CONFLICT",
                "batch_id was already used with different parameters.",
                recovery="Reuse the recorded result or choose a new batch_id and idempotency keys.",
            )

    def _start_action(self, action_id):
        with self.runtime.connect() as connection:
            connection.execute(
                "UPDATE actions SET status = 'running', started_at = ? WHERE action_id = ?",
                (utc_now(), action_id),
            )
            connection.commit()

    def _finish_action(
        self, action_id, status, *, error_code=None, error_message=None, result=None
    ):
        with self.runtime.connect() as connection:
            connection.execute(
                """UPDATE actions SET status = ?, finished_at = ?, error_code = ?,
                     error_message = ?, result_json = ? WHERE action_id = ?""",
                (
                    status,
                    utc_now(),
                    error_code,
                    error_message,
                    canonical_json(result) if result is not None else None,
                    action_id,
                ),
            )
            connection.commit()

    def _finish_batch(self, batch_id, status):
        with self.runtime.connect() as connection:
            connection.execute(
                "UPDATE action_batches SET status = ?, finished_at = ? WHERE batch_id = ?",
                (status, utc_now(), batch_id),
            )
            connection.commit()

    @staticmethod
    def _policy_denied(message):
        raise FC27Error(
            "POLICY_DENIED",
            message,
            recovery="Reduce the batch or update policy.json through a separate user-approved change.",
        )
