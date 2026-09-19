import json


SERVER_VERSION = "0.6.0"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2024-11-05")


def object_schema(properties=None, required=(), *, description=None, **extra):
    schema = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
        **extra,
    }
    if required:
        schema["required"] = list(required)
    if description:
        schema["description"] = description
    return schema


def described(schema, description):
    return {**schema, "description": description}


META_SCHEMA = {
    "type": "object",
    "required": ["request_id", "observed_at", "source", "complete"],
    "additionalProperties": True,
}

ERROR_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string"},
        "message": {"type": "string"},
        "retryable": {"type": "boolean"},
        "recovery": {"type": ["string", "null"]},
    },
    "required": ["code", "message", "retryable"],
    "additionalProperties": True,
}

OUTPUT_SCHEMA = {
    "oneOf": [
        object_schema(
            {
                "ok": {"const": True},
                "meta": META_SCHEMA,
                "data": {"type": "object", "additionalProperties": True},
            },
            ["ok", "meta", "data"],
        ),
        object_schema(
            {
                "ok": {"const": False},
                "meta": META_SCHEMA,
                "error": ERROR_SCHEMA,
            },
            ["ok", "meta", "error"],
        ),
    ]
}


ITEM_ID_ARRAY_SCHEMA = described(
    {
        "type": "array",
        "items": described(
            {"type": "integer", "minimum": 1},
            "Exact item_id of one owned account item.",
        ),
        "uniqueItems": True,
        "maxItems": 1000,
    },
    "Exact owned item IDs. item_id is not interchangeable with card_ea_id or trade_id.",
)

SBC_OBJECTIVE_SCHEMA = object_schema(
    {
        "candidate_item_ids": described(
            ITEM_ID_ARRAY_SCHEMA,
            "Optional complete candidate pool. Required items must also appear here.",
        ),
        "required_item_ids": described(
            {**ITEM_ID_ARRAY_SCHEMA, "maxItems": 11},
            "Exact owned items that every returned solution must contain.",
        ),
        "exclude_item_ids": described(
            ITEM_ID_ARRAY_SCHEMA, "Exact owned items that no solution may contain."
        ),
        "prefer_untradeable": described(
            {"type": "boolean", "default": True},
            "Prefer untradeable items before minimizing tradeable value.",
        ),
        "max_tradeable_value": described(
            {"type": "integer", "minimum": 0},
            "Maximum selected tradeable opportunity value.",
        ),
        "max_item_overall": described(
            {"type": "integer", "minimum": 1, "maximum": 99},
            "Exclude cards above this overall.",
        ),
    },
    description="Agent-selected deterministic SBC optimization objective.",
)


COMMON_ACTION_PROPERTIES = {
    "action_id": described(
        {"type": "string", "minLength": 1, "maxLength": 128},
        "Stable logical action ID. Reuse when reconciling or replaying it.",
    ),
    "idempotency_key": described(
        {"type": "string", "minLength": 1, "maxLength": 256},
        "Deduplication key for this exact payload. Change it when any parameter changes.",
    ),
}

SLOT_UPDATE_SCHEMA = object_schema(
    {
        "slot_index": described(
            {"type": "integer", "minimum": 0, "maximum": 23},
            "Web App squad slot index.",
        ),
        "item_id": described(
            {"type": ["integer", "null"], "minimum": 1},
            "Exact owned item_id, or null to clear the slot.",
        ),
    },
    ["slot_index", "item_id"],
)

TACTIC_INSTRUCTION_SCHEMA = object_schema(
    {
        "slot_index": described(
            {"type": "integer", "minimum": 0, "maximum": 10},
            "Starting-XI slot index.",
        ),
        "position_id": described(
            {"type": "integer"}, "Position ID returned by squad_query options."
        ),
        "role_id": described(
            {"type": "integer", "minimum": 0},
            "Role ID returned by squad_query options.",
        ),
        "variation_id": described(
            {"type": "integer", "minimum": 0},
            "Role variation ID returned by squad_query options.",
        ),
    },
    ["slot_index", "position_id", "role_id", "variation_id"],
)


def action_schema(action_type, properties=None, required=(), *, any_of=None):
    schema = object_schema(
        {
            **COMMON_ACTION_PROPERTIES,
            "type": {
                "const": action_type,
                "description": f"Execute the {action_type} operation.",
            },
            **(properties or {}),
        },
        ["action_id", "idempotency_key", "type", *required],
    )
    if any_of:
        schema["anyOf"] = any_of
    return schema


def sbc_action_schema(action_type):
    return action_schema(
        action_type,
        {
            "set_id": described(
                {"type": ["integer", "string"]}, "SBC set identity from sbc_query."
            ),
            "challenge_id": described(
                {"type": ["integer", "string"]},
                "SBC challenge identity from sbc_query.",
            ),
            "solution_id": described(
                {"type": "string", "minLength": 1},
                "Persisted solution identity returned by sbc_solve.",
            ),
            "item_ids": described(
                {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                    "minItems": 1,
                    "maxItems": 11,
                    "uniqueItems": True,
                },
                "Ordered exact owned items matching the persisted solution.",
            ),
        },
        ["set_id", "challenge_id", "solution_id", "item_ids"],
    )


ACTION_SCHEMAS = [
    action_schema(
        "buy_now",
        {
            "trade_id": described(
                {"type": "integer", "minimum": 1},
                "Concrete live listing ID returned by market_search.",
            ),
            "expected_card_ea_id": described(
                {"type": "integer", "minimum": 1},
                "Public card definition expected on the listing.",
            ),
            "max_price": described(
                {"type": "integer", "minimum": 1},
                "Maximum accepted buy-now price in coins.",
            ),
        },
        ["trade_id", "expected_card_ea_id", "max_price"],
    ),
    action_schema(
        "place_bid",
        {
            "trade_id": described(
                {"type": "integer", "minimum": 1},
                "Concrete live listing ID returned by market_search.",
            ),
            "expected_card_ea_id": described(
                {"type": "integer", "minimum": 1},
                "Public card definition expected on the listing.",
            ),
            "bid": described(
                {"type": "integer", "minimum": 1}, "Exact bid amount in coins."
            ),
        },
        ["trade_id", "expected_card_ea_id", "bid"],
    ),
    action_schema(
        "list_item",
        {
            "item_id": described(
                {"type": "integer", "minimum": 1},
                "Exact owned tradeable item to list.",
            ),
            "starting_bid": described(
                {"type": "integer", "minimum": 1}, "Starting bid in coins."
            ),
            "buy_now_price": described(
                {"type": "integer", "minimum": 1}, "Buy-now price in coins."
            ),
            "duration": described(
                {"type": "integer", "minimum": 3600, "default": 3600},
                "Listing duration in seconds.",
            ),
        },
        ["item_id", "starting_bid", "buy_now_price"],
    ),
    action_schema(
        "move_item",
        {
            "item_id": described(
                {"type": "integer", "minimum": 1}, "Exact owned item to move."
            ),
            "destination": described(
                {"type": "string", "enum": ["club", "tradepile"]},
                "Destination account area.",
            ),
        },
        ["item_id", "destination"],
    ),
    action_schema("relist_all"),
    action_schema("clear_sold"),
    sbc_action_schema("save_sbc_squad"),
    sbc_action_schema("submit_sbc"),
    action_schema(
        "set_active_squad",
        {
            "squad_id": described(
                {"type": "integer", "minimum": 1}, "Existing Web App squad ID."
            ),
            "expected_squad_hash": described(
                {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "Exact hash from a detailed squad_query read.",
            ),
        },
        ["squad_id", "expected_squad_hash"],
    ),
    action_schema(
        "save_squad",
        {
            "squad_id": described(
                {"type": "integer", "minimum": 1}, "Existing Web App squad ID."
            ),
            "expected_squad_hash": described(
                {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "Exact hash from a detailed squad_query read.",
            ),
            "formation_id": described(
                {"type": "integer", "minimum": 1},
                "Formation ID returned with include_options=true.",
            ),
            "slot_updates": described(
                {
                    "type": "array",
                    "items": SLOT_UPDATE_SCHEMA,
                    "minItems": 1,
                    "maxItems": 24,
                },
                "Atomic slot changes. Owned-item changes require expected_sync_id.",
            ),
        },
        ["squad_id", "expected_squad_hash"],
        any_of=[{"required": ["formation_id"]}, {"required": ["slot_updates"]}],
    ),
    action_schema(
        "save_squad_tactics",
        {
            "squad_id": described(
                {"type": "integer", "minimum": 1}, "Existing Web App squad ID."
            ),
            "expected_squad_hash": described(
                {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "Exact hash from a detailed squad_query read.",
            ),
            "tactic_id": described(
                {"type": "integer", "minimum": 6, "maximum": 10},
                "Web App tactics profile slot.",
            ),
            "name": described(
                {"type": "string", "maxLength": 64},
                "New tactics profile name.",
            ),
            "formation_id": described(
                {"type": "integer", "minimum": 1},
                "Formation ID returned with include_options=true.",
            ),
            "defensive_style": described(
                {"type": "integer", "minimum": 0, "maximum": 3},
                "Web App defensive style.",
            ),
            "defensive_line_height": described(
                {"type": "integer", "minimum": 1, "maximum": 100},
                "Defensive line height.",
            ),
            "build_up_play_style": described(
                {"type": "integer", "minimum": 0, "maximum": 2},
                "Web App build-up style.",
            ),
            "active": described(
                {"type": "boolean"},
                "Whether this profile becomes active; false is an explicit change.",
            ),
            "instructions": described(
                {
                    "type": "array",
                    "items": TACTIC_INSTRUCTION_SCHEMA,
                    "minItems": 1,
                    "maxItems": 11,
                },
                "Exact starting-XI role and variation assignments.",
            ),
        },
        ["squad_id", "expected_squad_hash", "tactic_id"],
        any_of=[
            {"required": ["name"]},
            {"required": ["formation_id"]},
            {"required": ["defensive_style"]},
            {"required": ["defensive_line_height"]},
            {"required": ["build_up_play_style"]},
            {"required": ["active"]},
            {"required": ["instructions"]},
        ],
    ),
]

ACTION_SCHEMA = {"oneOf": ACTION_SCHEMAS}


ATTRIBUTE_FILTER_SCHEMA = object_schema(
    {
        "pace_min": described({"type": "integer", "minimum": 0}, "Minimum pace."),
        "shooting_min": described(
            {"type": "integer", "minimum": 0}, "Minimum shooting."
        ),
        "passing_min": described(
            {"type": "integer", "minimum": 0}, "Minimum passing."
        ),
        "dribbling_min": described(
            {"type": "integer", "minimum": 0}, "Minimum dribbling."
        ),
        "defending_min": described(
            {"type": "integer", "minimum": 0}, "Minimum defending."
        ),
        "physicality_min": described(
            {"type": "integer", "minimum": 0}, "Minimum physicality."
        ),
        "weak_foot_min": described(
            {"type": "integer", "minimum": 1, "maximum": 5},
            "Minimum weak-foot stars.",
        ),
        "skill_moves_min": described(
            {"type": "integer", "minimum": 1, "maximum": 5},
            "Minimum skill-move stars.",
        ),
        "height_cm_min": described(
            {"type": "integer", "minimum": 1}, "Minimum height in centimeters."
        ),
        "height_cm_max": described(
            {"type": "integer", "minimum": 1}, "Maximum height in centimeters."
        ),
    }
)

CATALOG_FILTER_SCHEMA = object_schema(
    {
        "overall": object_schema(
            {
                "min": described(
                    {"type": "integer", "minimum": 1, "maximum": 99},
                    "Minimum overall, inclusive.",
                ),
                "max": described(
                    {"type": "integer", "minimum": 1, "maximum": 99},
                    "Maximum overall, inclusive.",
                ),
            },
            description="Inclusive overall range.",
        ),
        "positions": described(
            {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "Position codes. Values use OR semantics.",
        ),
        "attributes": described(
            ATTRIBUTE_FILTER_SCHEMA, "Minimum or maximum card/player attributes."
        ),
        "playstyles_plus": described(
            {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "Required PlayStyle+ names. Every listed name must match.",
        ),
        "roles_plusplus": described(
            {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "Required Role++ labels. Every listed label must match.",
        ),
        "quality": described(
            {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "Card qualities. Values use OR semantics.",
        ),
        "rarity_names": described(
            {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "Rarity names. Values use OR semantics.",
        ),
        **{
            key: described(
                {"type": "boolean"}, f"Require {key[3:].replace('_', ' ')} state."
            )
            for key in (
                "is_icon",
                "is_hero",
                "is_special",
                "is_dynamic",
                "is_evolution",
                "is_sbc",
                "is_objective",
            )
        },
        **{
            key: described(
                {
                    "type": "array",
                    "items": {"type": "integer"},
                    "uniqueItems": True,
                },
                f"{label} IDs. Values use OR semantics.",
            )
            for key, label in (
                ("club_ids", "Club"),
                ("league_ids", "League"),
                ("nation_ids", "Nation"),
            )
        },
    },
    description="Different filter categories use AND. ID, position, quality, and rarity lists use OR; every listed PlayStyle+ and Role++ must match.",
)


def content_branch(content_type, sources, sections, states, default_state):
    return object_schema(
        {
            "content_type": {
                "const": content_type,
                "description": f"Query {content_type} content.",
            },
            "source": described(
                {"type": "string", "enum": sources, "default": "auto"},
                "Data source. auto uses the best supported source.",
            ),
            "section": described(
                {"type": "string", "enum": sections, "default": "all"},
                "Content section.",
            ),
            "state": described(
                {"type": "string", "enum": states, "default": default_state},
                "Lifecycle state filter.",
            ),
            "text": described(
                {"type": "string"}, "Case-insensitive name/title filter."
            ),
            "detail": described(
                {
                    "type": "string",
                    "enum": ["summary", "detailed"],
                    "default": "summary",
                },
                "summary is compact; detailed includes tasks, rewards, or levels.",
            ),
            "limit": described(
                {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                "Maximum returned items.",
            ),
        },
        ["content_type"],
    )


CONTENT_QUERY_SCHEMA = {
    "oneOf": [
        content_branch(
            "season",
            ["auto", "ea"],
            ["all"],
            ["current", "all", "claimable", "completed"],
            "all",
        ),
        content_branch(
            "objective",
            ["auto", "ea"],
            [
                "all",
                "fc_objectives",
                "foundations",
                "milestones",
                "mastery",
                "seasonal",
                "fc_pro",
            ],
            ["current", "all", "claimable", "completed", "expired"],
            "current",
        ),
        content_branch(
            "evolution",
            ["auto", "ea", "futgg"],
            [
                "all",
                "my_evolutions",
                "training_camp",
                "rewards",
                "evolutions",
                "public",
            ],
            [
                "current",
                "all",
                "available",
                "started",
                "paused",
                "claimable",
                "completed",
                "expired",
            ],
            "current",
        ),
    ]
}

SBC_READ_PROPERTIES = {
    "set_id": described(
        {"type": ["integer", "string"]},
        "SBC set identity. Required with challenge_id.",
    ),
    "challenge_id": described(
        {"type": ["integer", "string"]}, "Exact SBC challenge identity."
    ),
    "include_raw": described(
        {"type": "boolean", "default": False},
        "Include raw EA evidence for unsupported-requirement diagnosis.",
    ),
}


def sbc_read_schema():
    return object_schema(
        SBC_READ_PROPERTIES,
        allOf=[
            {
                "if": {
                    "properties": {"challenge_id": {"not": {"type": "null"}}},
                    "required": ["challenge_id"],
                },
                "then": {"required": ["set_id"]},
            }
        ],
    )


SYNC_DEPENDENT_ACTIONS = [
    "buy_now",
    "place_bid",
    "list_item",
    "move_item",
    "relist_all",
    "clear_sold",
    "save_sbc_squad",
    "submit_sbc",
]

EXECUTE_ACTIONS_SCHEMA = object_schema(
    {
        "batch_id": described(
            {"type": "string", "minLength": 1, "maxLength": 128},
            "Stable ordered-batch ID. Reuse only to replay the exact batch.",
        ),
        "expected_sync_id": described(
            {"type": "integer"},
            "Latest complete sync. Required for inventory, market, SBC, and squad slot actions.",
        ),
        "stop_on_error": described(
            {"type": "boolean", "default": True},
            "Stop remaining actions after the first failure.",
        ),
        "confirmed": described(
            {"type": "boolean", "default": False},
            "True only after explicit user authorization for this exact batch. Never infer confirmation.",
        ),
        "actions": described(
            {"type": "array", "items": ACTION_SCHEMA, "minItems": 1},
            "Exact ordered actions. The daemon does not select targets or limits.",
        ),
    },
    ["batch_id", "actions"],
    allOf=[
        {
            "if": {
                "properties": {
                    "actions": {
                        "contains": {
                            "anyOf": [
                                {
                                    "properties": {
                                        "type": {"enum": SYNC_DEPENDENT_ACTIONS}
                                    },
                                    "required": ["type"],
                                },
                                {
                                    "properties": {
                                        "type": {"const": "save_squad"},
                                        "slot_updates": {"minItems": 1},
                                    },
                                    "required": ["type", "slot_updates"],
                                },
                            ]
                        }
                    }
                },
                "required": ["actions"],
            },
            "then": {"required": ["expected_sync_id"]},
        }
    ],
)


def tool(name, description, input_schema, annotations):
    return {
        "name": name,
        "description": description,
        "inputSchema": input_schema,
        "outputSchema": OUTPUT_SCHEMA,
        "annotations": annotations,
    }


TOOLS = [
    tool(
        "status",
        "Return service, bridge, account, policy, synchronization, and rate-limit readiness. Use when readiness is unknown, before the first account-dependent operation in a workflow, or after authentication, account, bridge, rate-limit, or synchronization errors. Reuse a recent successful result. Raw EA credentials are never returned.",
        object_schema(),
        {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    ),
    tool(
        "catalog_query",
        "Search or compare local public FC27 card definitions. Use for player/card facts and to resolve names to card_ea_id values; arbitrary SQL is rejected. Returns matching cards and missing requested IDs.",
        object_schema(
            {
                "text": described({"type": "string"}, "Player or card name text."),
                "card_ea_ids": described(
                    {
                        "type": "array",
                        "items": {"type": "integer"},
                        "maxItems": 100,
                        "uniqueItems": True,
                    },
                    "Exact public card definition IDs.",
                ),
                "base_player_ea_ids": described(
                    {
                        "type": "array",
                        "items": {"type": "integer"},
                        "maxItems": 100,
                        "uniqueItems": True,
                    },
                    "Exact base-player definition IDs.",
                ),
                "filters": CATALOG_FILTER_SCHEMA,
                "sort": described(
                    {
                        "type": "string",
                        "enum": [
                            "overall_desc",
                            "overall_asc",
                            "name_asc",
                            "pace_desc",
                            "shooting_desc",
                            "passing_desc",
                            "dribbling_desc",
                            "defending_desc",
                            "physicality_desc",
                        ],
                        "default": "overall_desc",
                    },
                    "Result ordering.",
                ),
                "limit": described(
                    {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                    },
                    "Maximum returned cards.",
                ),
                "detail": described(
                    {
                        "type": "string",
                        "enum": ["summary", "detailed"],
                        "default": "summary",
                    },
                    "summary returns compact facts; detailed returns complete local fields.",
                ),
            }
        ),
        {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    ),
    tool(
        "club_query",
        "Read the latest complete local owned-item mirror. Use to resolve concrete item_id values, locations, tradeability, and protection state. One card_ea_id may map to multiple item_id values.",
        object_schema(
            {
                "locations": described(
                    {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                    },
                    "Owned-item locations to include.",
                ),
                "card_ea_ids": described(
                    {
                        "type": "array",
                        "items": {"type": "integer"},
                        "uniqueItems": True,
                    },
                    "Public cards whose owned instances should be returned.",
                ),
                "tradeable": described(
                    {"type": ["boolean", "null"]},
                    "Filter by tradeability; null means either.",
                ),
                "protected": described(
                    {"type": ["boolean", "null"]},
                    "Filter by protection state; null means either.",
                ),
                "include_catalog": described(
                    {"type": "boolean", "default": True},
                    "Join compact public catalog facts.",
                ),
                "limit": described(
                    {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 100,
                    },
                    "Maximum returned items.",
                ),
                "cursor": described(
                    {"type": ["string", "null"]},
                    "Opaque cursor from the previous page.",
                ),
            }
        ),
        {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    ),
    tool(
        "squad_query",
        "Read authenticated Ultimate Team squads. Summary is the default for inspection. Use detailed output for exact slots, tactics, item_id values, and squad_hash before a write; set include_options=true only when formation, role, or variation IDs are needed.",
        object_schema(
            {
                "selection": described(
                    {
                        "type": "string",
                        "enum": ["all", "active", "exact"],
                        "default": "active",
                    },
                    "Select the list, active squad, or one exact squad.",
                ),
                "squad_id": described(
                    {"type": "integer", "minimum": 1},
                    "Exact squad ID; required when selection=exact.",
                ),
                "detail": described(
                    {
                        "type": "string",
                        "enum": ["summary", "detailed"],
                        "default": "summary",
                    },
                    "detailed includes complete hashable squad and tactics state.",
                ),
                "include_options": described(
                    {"type": "boolean", "default": False},
                    "Include formation, role, and variation catalogs; detailed mode only.",
                ),
            },
            allOf=[
                {
                    "if": {
                        "properties": {"selection": {"const": "exact"}},
                        "required": ["selection"],
                    },
                    "then": {"required": ["squad_id"]},
                },
                {
                    "if": {
                        "properties": {"include_options": {"const": True}},
                        "required": ["include_options"],
                    },
                    "then": {
                        "properties": {"detail": {"const": "detailed"}},
                        "required": ["detail"],
                    },
                },
            ],
        ),
        {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    ),
    tool(
        "sync_club",
        "Synchronize coins and owned-item areas from the authenticated Web App into the local runtime database. Use after login/account changes, when club state is stale, or when an error requests a new sync. Full sync commits only when every required area completes.",
        object_schema(
            {
                "mode": described(
                    {"type": "string", "enum": ["full"], "default": "full"},
                    "Public MCP synchronization mode.",
                ),
                "areas": described(
                    {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "coins",
                                "club",
                                "storage",
                                "unassigned",
                                "tradepile",
                                "watchlist",
                            ],
                        },
                        "uniqueItems": True,
                    },
                    "Omit for all required areas. A supplied full sync must include every required area.",
                ),
            }
        ),
        {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    ),
    tool(
        "market_search",
        "Search live EA Transfer Market listings for one resolved card_ea_id. Use for current availability, prices, or concrete trade_id values. Use catalog_query first for a name and price_context for historical/economic analysis. Returns facts, not a purchase decision.",
        object_schema(
            {
                "card_ea_id": described(
                    {"type": "integer", "minimum": 1},
                    "Public card definition to search.",
                ),
                "min_buy_now": described(
                    {"type": "integer", "minimum": 0},
                    "Minimum buy-now price in coins.",
                ),
                "max_buy_now": described(
                    {"type": "integer", "minimum": 0},
                    "Maximum buy-now price in coins.",
                ),
                "limit": described(
                    {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 21,
                        "default": 21,
                    },
                    "Maximum live listings.",
                ),
            },
            ["card_ea_id"],
        ),
        {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    ),
    tool(
        "price_context",
        "Return market and ownership context for explicit card_ea_id values: FUT.GG prices, local history, EA scans, holdings, acquisition costs, tax, and deterministic net proceeds. Use market_search for concrete current listings and trade_id values.",
        object_schema(
            {
                "card_ea_ids": described(
                    {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 1,
                        "maxItems": 100,
                        "uniqueItems": True,
                    },
                    "Public cards to compare.",
                ),
                "history_hours": described(
                    {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 2160,
                        "default": 72,
                    },
                    "Historical lookback in hours.",
                ),
            },
            ["card_ea_ids"],
        ),
        {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    ),
    tool(
        "content_query",
        "Discover FC Season rewards, objective groups, or Evolutions. Objective sections mirror FC Hub; Evolution source=auto merges authenticated progress with FUT.GG requirements/upgrades. Summary is compact; detailed includes tasks, rewards, or levels. Use sbc_query and sbc_refresh for SBCs.",
        CONTENT_QUERY_SCHEMA,
        {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    ),
    tool(
        "sbc_query",
        "Read persisted SBC sets, challenges, normalized constraints, slots, rewards, status, expiry, and unsupported-requirement reports from the local runtime database. Use sbc_refresh first when current EA state is required. Raw evidence is omitted unless include_raw=true.",
        sbc_read_schema(),
        {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    ),
    tool(
        "sbc_refresh",
        "Refresh SBC sets or challenges from the authenticated Web App, persist the normalized result, and return it. Use no IDs for sets, set_id for challenges, or set_id plus challenge_id for exact requirements. Raw evidence is omitted unless include_raw=true.",
        sbc_read_schema(),
        {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    ),
    tool(
        "sbc_solve",
        "Optimize and persist exact owned-item candidates for one persisted set_id and challenge_id. Use after sbc_refresh and a complete club sync. Resolve mandatory players through club_query and pass item_id values. Returns validated solutions and solver evidence; unsupported constraints block solving. It never saves or submits to EA.",
        object_schema(
            {
                "set_id": described(
                    {"type": ["integer", "string"]},
                    "SBC set identity from sbc_query.",
                ),
                "challenge_id": described(
                    {"type": ["integer", "string"]},
                    "SBC challenge identity from sbc_query.",
                ),
                "objective": SBC_OBJECTIVE_SCHEMA,
                "max_solutions": described(
                    {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 5,
                    },
                    "Maximum validated solutions to persist and return.",
                ),
            },
            ["set_id", "challenge_id"],
        ),
        {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    ),
    tool(
        "execute_actions",
        "Execute one exact ordered batch after policy, expected-state, protected-item, and idempotency checks. Use only after targets and limits are selected; do not use for discovery or strategy. confirmed=true must represent explicit authorization for this exact batch. After stale state, reread and rebuild. After an unknown write outcome, verify or reconcile and never automatically repeat the write.",
        EXECUTE_ACTIONS_SCHEMA,
        {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    ),
]


SERVER_INSTRUCTIONS = """FC27 provides factual game data, synchronized account state, deterministic calculations, validation, and exact operations. Strategy and value judgments belong to the Agent.

Identifier types are not interchangeable: card_ea_id identifies a public card definition; item_id identifies one concrete owned account item; trade_id identifies one live Transfer Market listing.

Use catalog_query for public card facts, club_query for owned items, squad_query for live squad state, market_search for current listings, price_context for market and economic context, content_query for Seasons, Objectives, and Evolutions, and sbc_query/sbc_refresh for SBCs.

Use write tools only after exact targets, limits, and expected state are established. Never infer user confirmation. After stale-state errors, read the new state and rebuild the operation. After an unknown write outcome, verify or reconcile state and never automatically repeat the write.

EA credentials and raw authenticated session headers are never exposed."""


def tool_result(value, is_error=False):
    return {
        "content": [
            {"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}
        ],
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
            requested_version = params.get("protocolVersion")
            protocol_version = (
                requested_version
                if requested_version in SUPPORTED_PROTOCOL_VERSIONS
                else SUPPORTED_PROTOCOL_VERSIONS[0]
            )
            return self._result(
                request_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "FC27", "version": SERVER_VERSION},
                    "instructions": SERVER_INSTRUCTIONS,
                },
            )
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            return self._result(request_id, {"tools": TOOLS})
        if method == "tools/call":
            value = self.daemon.call_tool(
                params.get("name"), params.get("arguments") or {}
            )
            return self._result(
                request_id, tool_result(value, not value.get("ok", False))
            )
        if request_id is None:
            return None
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    @staticmethod
    def _result(request_id, result):
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
