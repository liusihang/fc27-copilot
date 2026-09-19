import json


SERVER_VERSION = "0.5.0"


def object_schema(properties, required=()):
    schema = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return schema


ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 256},
        "type": {"type": "string", "enum": ["buy_now", "place_bid", "list_item", "move_item", "relist_all", "clear_sold", "save_sbc_squad", "submit_sbc"]},
        "item_id": {"type": "integer"},
        "trade_id": {"type": "integer"},
        "expected_card_ea_id": {"type": "integer"},
        "max_price": {"type": "integer", "minimum": 1},
        "bid": {"type": "integer", "minimum": 1},
        "starting_bid": {"type": "integer", "minimum": 1},
        "buy_now_price": {"type": "integer", "minimum": 1},
        "duration": {"type": "integer", "minimum": 3600},
        "destination": {"type": "string", "enum": ["club", "tradepile"]},
        "challenge_id": {"type": ["integer", "string"]},
        "set_id": {"type": ["integer", "string"]},
        "solution_id": {"type": "string"},
        "item_ids": {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 1, "maxItems": 11, "uniqueItems": True},
    },
    "required": ["action_id", "idempotency_key", "type"],
    "additionalProperties": True,
}

ITEM_ID_ARRAY_SCHEMA = {
    "type": "array",
    "items": {"type": "integer", "minimum": 1},
    "uniqueItems": True,
    "maxItems": 1000,
}

SBC_OBJECTIVE_SCHEMA = object_schema(
    {
        "candidate_item_ids": ITEM_ID_ARRAY_SCHEMA,
        "required_item_ids": {
            **ITEM_ID_ARRAY_SCHEMA,
            "maxItems": 11,
        },
        "exclude_item_ids": ITEM_ID_ARRAY_SCHEMA,
        "prefer_untradeable": {"type": "boolean", "default": True},
        "max_tradeable_value": {"type": "integer", "minimum": 0},
        "max_item_overall": {"type": "integer", "minimum": 1, "maximum": 99},
    }
)


TOOLS = [
    {
        "name": "status",
        "description": "Return fc27d, catalog, browser bridge, account, policy, and synchronization status. Use before any EA-dependent workflow. Raw EA session headers are never returned.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    },
    {
        "name": "catalog_query",
        "description": "Search or compare local FC27 catalog cards by text, exact IDs, positions, rating, attributes, PlayStyle+, Role++, club, league, or nation. Use for public card facts; arbitrary SQL is not accepted.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "card_ea_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 100},
                "base_player_ea_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 100},
                "filters": {"type": "object"},
                "sort": {"type": "string", "default": "overall_desc"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "detail": {"type": "string", "enum": ["summary", "detailed"], "default": "summary"},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "club_query",
        "description": "Query the latest complete local owned-item mirror. Use for concrete item IDs and locations after a successful club sync.",
        "inputSchema": object_schema({"locations": {"type": "array", "items": {"type": "string"}}, "card_ea_ids": {"type": "array", "items": {"type": "integer"}}, "tradeable": {"type": ["boolean", "null"]}, "protected": {"type": ["boolean", "null"]}, "include_catalog": {"type": "boolean", "default": True}, "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 100}, "cursor": {"type": ["string", "null"]}}),
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "sync_club",
        "description": "Synchronize coins and requested FC27 inventory areas through the authenticated browser bridge. Full sync commits only when every required area is complete.",
        "inputSchema": object_schema({"mode": {"type": "string", "enum": ["full", "targeted"], "default": "full"}, "areas": {"type": "array", "items": {"type": "string", "enum": ["coins", "club", "storage", "unassigned", "tradepile", "watchlist"]}}}),
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    },
    {
        "name": "market_search",
        "description": "Search current EA listings for one explicit FC27 card and bounded price range. Returns listings and observations; it never chooses whether to buy.",
        "inputSchema": object_schema({"card_ea_id": {"type": "integer"}, "min_buy_now": {"type": "integer", "minimum": 0}, "max_buy_now": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 21, "default": 21}}, ["card_ea_id"]),
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    },
    {
        "name": "price_context",
        "description": "Return FUT.GG prices, local price history, EA scan history, holdings, costs, tax, and deterministic net results for explicit card IDs.",
        "inputSchema": object_schema({"card_ea_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 100}, "history_hours": {"type": "integer", "minimum": 1, "maximum": 2160, "default": 72}}, ["card_ea_ids"]),
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    },
    {
        "name": "content_query",
        "description": "Discover FC27 Season levels, objective groups, Evolution slots, or SBC sets. Use content_type=season for compact standard/Premium Season reward tracks. Objective section values mirror FC Hub tabs. Evolution source=auto merges authenticated EA progress with FUT.GG public requirements and upgrades; source=ea or futgg returns one source only. State filters lifecycle status and defaults by content type. Detailed mode includes objective tasks, Season rewards, or Evolution level progress. Use sbc_query for exact SBC requirements, persistence, solving, save, or submit evidence.",
        "inputSchema": object_schema({"content_type": {"type": "string", "enum": ["season", "objective", "evolution", "sbc"]}, "source": {"type": "string", "enum": ["auto", "ea", "futgg"], "default": "auto"}, "section": {"type": "string", "enum": ["all", "fc_objectives", "foundations", "milestones", "mastery", "seasonal", "fc_pro", "my_evolutions", "training_camp", "rewards", "evolutions", "public"], "default": "all"}, "state": {"type": "string", "enum": ["current", "all", "available", "started", "paused", "claimable", "completed", "expired"]}, "text": {"type": "string"}, "detail": {"type": "string", "enum": ["summary", "detailed"], "default": "summary"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}}, ["content_type"]),
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    },
    {
        "name": "sbc_query",
        "description": "Refresh and persist FC27 SBC sets or challenges from the authenticated Web App, then return normalized constraints, formation slots, rewards, status, expiry, raw evidence, and unsupported requirement reports. Use challenge_id with set_id for one challenge; use live=false to query only the local cache.",
        "inputSchema": object_schema({"set_id": {"type": ["integer", "string", "null"]}, "challenge_id": {"type": ["integer", "string", "null"]}, "live": {"type": "boolean", "default": True}}),
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    },
    {
        "name": "sbc_solve",
        "description": "Optimize and persist exact owned-item SBC candidates with OR-Tools CP-SAT. Use after sbc_query and a complete club sync. The Agent may pin concrete club instances with objective.required_item_ids; resolve names or cards through club_query and pass item_id values, not card_ea_id values. Every returned squad contains all required items, satisfies every supported local constraint, and includes solver plus independent validation evidence. Protected, missing, loan, duplicate, stale, Tradepile, and explicitly excluded items are rejected. Unsupported EA constraints remain blocking. The tool never saves or submits.",
        "inputSchema": object_schema({"challenge_id": {"type": ["integer", "string"]}, "objective": SBC_OBJECTIVE_SCHEMA, "max_solutions": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5}}, ["challenge_id"]),
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "execute_actions",
        "description": "Execute one exact ordered action batch after immutable policy, current-sync, protected-item, spend, capacity, ownership, and idempotency checks. Use only after the Agent has selected concrete item/trade IDs and prices. buy_now requires trade_id, expected_card_ea_id, and max_price; place_bid requires trade_id, expected_card_ea_id, and bid; item actions require item_id. Suggest mode also requires confirmed=true. Replaying the same batch_id and actions returns the recorded audit result. Observe mode rejects before contacting EA.",
        "inputSchema": object_schema({"batch_id": {"type": "string", "minLength": 1, "maxLength": 128}, "expected_sync_id": {"type": "integer"}, "stop_on_error": {"type": "boolean", "default": True}, "confirmed": {"type": "boolean", "default": False}, "actions": {"type": "array", "items": ACTION_SCHEMA, "minItems": 1}}, ["batch_id", "expected_sync_id", "actions"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    },
    {
        "name": "catalog_refresh",
        "description": "Atomically rebuild the local FC27 catalog from the current FUT.GG manifest and replace it only after integrity and mapping validation.",
        "inputSchema": object_schema({"force": {"type": "boolean", "default": False}}),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    },
]


def tool_result(value, is_error=False):
    return {
        "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}],
        "structuredContent": value,
        "isError": is_error,
    }


class MCPServer:
    def __init__(self, daemon):
        self.daemon = daemon

    def handle(self, message):
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None
        if method == "initialize":
            return self._result(
                request_id,
                {
                    "protocolVersion": params.get("protocolVersion") or "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "FC27", "version": SERVER_VERSION},
                    "instructions": "FC27 facts, synchronization, validation, and exact operations. Strategy decisions belong to the Agent. Account writes are disabled in observe mode.",
                },
            )
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            return self._result(request_id, {"tools": TOOLS})
        if method == "tools/call":
            value = self.daemon.call_tool(params.get("name"), params.get("arguments") or {})
            return self._result(request_id, tool_result(value, not value.get("ok", False)))
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}

    @staticmethod
    def _result(request_id, result):
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
