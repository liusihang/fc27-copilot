import json
from pathlib import Path

from .errors import FC27Error


EXECUTION_MODES = ("observe", "suggest", "auto")
ACTION_TYPES = (
    "buy_now",
    "place_bid",
    "list_item",
    "move_item",
    "relist_all",
    "clear_sold",
    "save_sbc_squad",
    "submit_sbc",
    "set_active_squad",
    "save_squad",
    "save_squad_tactics",
)
INTEGER_LIMITS = (
    "minimum_coin_reserve",
    "maximum_single_purchase",
    "maximum_batch_spend",
    "maximum_daily_spend",
    "maximum_batch_actions",
    "maximum_same_card_owned",
    "maximum_tradepile_usage",
)
POLICY_KEYS = {
    "execution_mode",
    *INTEGER_LIMITS,
    "protected_item_ids",
    "allowed_action_types",
}


class PolicyStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            policy = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise FC27Error(
                "POLICY_NOT_FOUND",
                f"Execution policy does not exist: {self.path}",
                recovery="Create policy.json from the shipped observe-mode policy.",
            ) from error
        except json.JSONDecodeError as error:
            raise FC27Error(
                "POLICY_INVALID",
                "policy.json is not valid JSON.",
                recovery="Correct the reported JSON syntax before retrying.",
                details={"line": error.lineno, "column": error.colno},
            ) from error
        if not isinstance(policy, dict):
            raise FC27Error("POLICY_INVALID", "policy.json must contain one JSON object.")
        unknown = sorted(set(policy) - POLICY_KEYS)
        missing = sorted(POLICY_KEYS - set(policy))
        if unknown or missing:
            raise FC27Error(
                "POLICY_INVALID",
                "policy.json fields do not match the execution contract.",
                recovery="Use exactly the fields in the shipped policy.json.",
                details={"unknown": unknown, "missing": missing},
            )
        mode = policy["execution_mode"]
        if mode not in EXECUTION_MODES:
            raise FC27Error(
                "POLICY_INVALID",
                f"Unsupported execution_mode: {mode}",
                recovery=f"Use one of: {', '.join(EXECUTION_MODES)}.",
            )
        for key in INTEGER_LIMITS:
            value = policy[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise FC27Error(
                    "POLICY_INVALID",
                    f"{key} must be a non-negative integer.",
                )
        if policy["maximum_batch_actions"] < 1:
            raise FC27Error(
                "POLICY_INVALID", "maximum_batch_actions must be at least 1."
            )
        protected = policy["protected_item_ids"]
        if not isinstance(protected, list) or any(
            isinstance(value, bool) or not isinstance(value, int) for value in protected
        ):
            raise FC27Error(
                "POLICY_INVALID", "protected_item_ids must be an array of integers."
            )
        allowed = policy["allowed_action_types"]
        if not isinstance(allowed, list) or any(value not in ACTION_TYPES for value in allowed):
            raise FC27Error(
                "POLICY_INVALID",
                "allowed_action_types contains an unsupported action type.",
                recovery=f"Use values from: {', '.join(ACTION_TYPES)}.",
            )
        policy["protected_item_ids"] = sorted(set(protected))
        policy["allowed_action_types"] = sorted(set(allowed))
        return policy
