# FC27 MCP contract

Date: 2026-09-19

Server name: `FC27`

Server version: `0.6.0`

The stdio adapter implements the initialize-based MCP lifecycle and negotiates only `2025-11-25`, `2025-06-18`, or `2024-11-05`. An unsupported requested version receives the newest version implemented by this server.

OpenClaw should refer to tools with fully qualified names such as `FC27:catalog_query`.

## Response envelope

Successful calls return:

```json
{
  "ok": true,
  "meta": {
    "request_id": "uuid",
    "observed_at": "2026-09-18T08:00:00Z",
    "source": "catalog",
    "complete": true,
    "catalog_snapshot_at": "2026-09-17T17:06:37Z",
    "club_sync_id": null
  },
  "data": {}
}
```

Failed calls return:

```json
{
  "ok": false,
  "meta": {
    "request_id": "uuid",
    "observed_at": "2026-09-18T08:00:00Z",
    "source": "fc27d",
    "complete": false,
    "catalog_snapshot_at": "2026-09-17T17:06:37Z",
    "club_sync_id": null
  },
  "error": {
    "code": "EA_SESSION_REQUIRED",
    "message": "An authenticated FC27 Web App session is required.",
    "retryable": true,
    "recovery": "Log in to the FC27 Web App, connect the Chrome bridge, then retry."
  }
}
```

`source`, observation time, catalog snapshot, and club sync provenance appear once in `meta` instead of being repeated for every row.

## Tools

### `FC27:status`

Returns daemon/database health, browser bridge state, public EA session status, active Persona, coins, catalog metadata, latest complete sync, policy mode, and active rate-limit/backoff state. Use it when readiness is unknown, before the first account-dependent operation in a workflow, or after a readiness-related error. Reuse a recent successful result. It never returns raw session headers.

Status also returns automatic synchronization state: pending/running flags, triggering reason, bounded retry attempt, last event, last successful synchronization and last error.

Input: empty object.

### `FC27:catalog_query`

Searches local catalog data by text, exact card IDs, positions, overall range, attributes, PlayStyle+, Role++, club, league, or nation. Supports bounded sorting and `summary` or `detailed` output. It does not accept SQL.

```json
{
  "text": "Mbappe",
  "card_ea_ids": [],
  "filters": {
    "positions": ["ST"],
    "overall": {"min": 85, "max": 95},
    "attributes": {"pace_min": 90},
    "playstyles_plus": ["Quick Step"],
    "roles_plusplus": ["ST Advanced Forward"]
  },
  "sort": "overall_desc",
  "limit": 20,
  "detail": "summary"
}
```

### `FC27:club_query`

Reads the latest complete local club mirror. It can filter locations, card IDs, tradeability, and local protection state. Pagination uses an opaque cursor. `include_catalog=true` joins catalog facts.

### `FC27:squad_query`

Reads current Ultimate Team squads directly through the authenticated Web App.

- `selection=all`: squad list.
- `selection=active`: current playing squad.
- `selection=exact`: one `squad_id`.
- `detail=summary`: identity, formation, rating, chemistry and active state.
- `detail=summary`: the default; identity, formation, rating, chemistry and active state.
- `detail=detailed`: ordered starting, substitute, reserve and manager slots; exact owned `item_id` values; five tactics profiles; and `squad_hash`.
- `include_options=true`: with detailed mode, additionally returns current Web App formations, style enums, and position-compatible role/variation options.

Every detailed squad includes a canonical `squad_hash` over active state, formation, ordered slots and tactics. Summary results omit the hash because they do not contain the complete hashed state. Squad-changing actions must use the detailed hash and fail with `STALE_SQUAD_STATE` if the live squad changed.

### `FC27:sync_club`

Runs a full synchronization through the authenticated browser bridge and updates the local runtime database. It does not change the EA account. Full mode records per-area completeness and commits only after all required areas finish.

Default areas: `coins`, `club`, `storage`, `unassigned`, and `tradepile`.

### `FC27:market_search`

Searches current EA listings for one explicit `card_ea_id` and bounded price range. It returns concrete `trade_id` values and aggregate sample statistics, and records the aggregate scan in the local runtime database. It does not choose a purchase.

### `FC27:price_context`

Returns current and historical FUT.GG prices, EA scan history, holdings, listed count, recent acquisition costs, EA tax, and deterministic net-profit calculations for explicit card IDs.

### `FC27:content_query`

Discovers FC Season levels, objective groups, or Evolution slots through one bounded list interface.

- `content_type=season`: compact standard and Premium FC Season level rewards, required SP, remaining SP, unlock, claimable, and claimed state.
- `content_type=objective`: EA account categories, groups, task progress and rewards. `section` selects `fc_objectives`, `foundations`, `milestones`, `mastery`, `seasonal`, or `fc_pro`.
- `content_type=evolution`: EA account slots/progress, FUT.GG public definitions, or a merged result. `section` selects `my_evolutions`, `training_camp`, `rewards`, `evolutions`, or unmatched `public` entries.
- `source=auto`: use EA for Season and objectives; merge EA account state with FUT.GG public facts for Evolutions. If one Evolution source is unavailable, return the remaining source with `complete=false` and a merge warning.
- `source=ea`: require the authenticated Web App.
- `source=futgg`: currently available for evolutions through the content-hashed manifest dataset.
- `state`: filter current, available, started, paused, claimable, completed, expired, or all content where applicable. Invalid content-type combinations return an actionable error.
- `detail=summary`: compact bounded facts.
- `detail=detailed`: Season rewards, objective tasks, or merged Evolution levels, progress, requirements, upgrades, and lifecycle evidence.

FUT.GG `all-evolutions` means every Evolution in the current manifest dataset. It is not described as historical completeness when the active and all datasets are identical.

Use `sbc_refresh` and `sbc_query` for all SBC discovery and requirements.

### `FC27:sbc_query`

Reads persisted SBC sets/challenges from the local runtime database. It returns normalized constraints, formation slots, rewards, status, expiry, repeatability, observation time, and explicit unsupported-constraint reports. It does not contact EA or modify local state. Raw evidence is omitted unless `include_raw=true`.

### `FC27:sbc_refresh`

Reads current SBC state from the authenticated Web App, normalizes and persists it, then returns the result. Omit IDs to refresh sets, pass `set_id` to refresh that set's challenges, or pass both `set_id` and `challenge_id` for exact requirements. Raw evidence is omitted unless `include_raw=true`.

### `FC27:sbc_solve`

Optimizes and persists multiple exact-item candidates from the latest owned-item mirror with OR-Tools CP-SAT. Protected, loan, stale, duplicate, Tradepile, and explicitly excluded items are rejected. Every candidate is independently revalidated before persistence. Results distinguish `optimal`, deadline-bounded `feasible`, `infeasible`, and `unknown` solver outcomes and never save or submit.

The Agent can require concrete owned items:

```json
{
  "set_id": "4",
  "challenge_id": "16",
  "objective": {
    "required_item_ids": [800013],
    "exclude_item_ids": [],
    "prefer_untradeable": true,
    "max_tradeable_value": 0,
    "max_item_overall": 82
  },
  "max_solutions": 5
}
```

`required_item_ids` contains exact current club `item_id` values. When the user names a player or card, the Agent first calls `FC27:club_query`, selects the intended owned instance, and passes its `item_id`. Every returned candidate contains every required item. Required items do not bypass protection, loan, location, candidate-pool, exclusion, overall, value, stale-state, or unsupported-requirement checks.

Supported objective fields are:

- `candidate_item_ids`: restrict the complete candidate pool;
- `required_item_ids`: pin exact current owned items up to the challenge's captured player count;
- `exclude_item_ids`: reject exact owned items;
- `prefer_untradeable`: prioritize fewer tradeable items before their value;
- `max_tradeable_value`: hard upper bound for selected tradeable opportunity cost;
- `max_item_overall`: preserve cards above a caller-selected overall.

### `FC27:execute_actions`

Executes an ordered list of exact operations. The Agent must provide targets and limits; the daemon does not select players, listings, prices, or SBC solutions.

Required batch controls:

- `batch_id`;
- `stop_on_error`;
- one unique `action_id` and `idempotency_key` per action;
- `confirmed=true` when policy mode requires confirmation.

`expected_sync_id` is required for market, inventory, SBC, and squad-slot actions. `set_active_squad`, `save_squad_tactics`, and a formation-only `save_squad` use `expected_squad_hash` without an unrelated club synchronization dependency.

`confirmed=true` means the user explicitly authorized this exact batch. The Agent must not infer confirmation from expected value or benefit.

Supported action types include `buy_now`, `place_bid`, `list_item`, `move_item`, `relist_all`, `clear_sold`, `save_sbc_squad`, `submit_sbc`, `set_active_squad`, `save_squad`, and `save_squad_tactics`.

Squad action fields follow the current Web App contract:

- `set_active_squad`: `squad_id`, `expected_squad_hash`.
- `save_squad`: `squad_id`, `expected_squad_hash`, optional `formation_id`, and optional `slot_updates` containing exact `slot_index` 0–23 plus owned `item_id` or `null`.
- `save_squad_tactics`: `squad_id`, `expected_squad_hash`, tactics profile `tactic_id` 6–10, and one or more supported changes: name, formation, defensive style 0–3, defensive line height 1–100, build-up style 0–2, active profile state, or exact role/variation instructions for slots 0–10.

These actions apply one coherent Web App save and then independently reload the target squad. Timeout results use `SQUAD_WRITE_OUTCOME_UNKNOWN`; callers read `squad_query` and never retry automatically.

Catalog rebuild is an operator maintenance action and is not advertised to ordinary Agents. Run `scripts/refresh_catalog.py`; replacement occurs only after complete validation.

## Operator-only daemon RPCs

Save and submit reconciliation are narrow localhost daemon RPCs, not additional MCP tools. `reconcile_sbc_save`, `verify_sbc_save`, and `reconcile_sbc_submit` accept only the original `action_id`. The daemon derives all targets and performs its own browser reads under the execution lock. Caller-provided set IDs, challenge IDs, item IDs, sync IDs, or evidence are ignored.

## Error catalog

| Code | Meaning | Recovery |
| --- | --- | --- |
| `CATALOG_NOT_FOUND` | Local catalog has not been built. | Run `scripts/import_catalog.py` or `scripts/refresh_catalog.py`. |
| `CATALOG_INVALID` | Integrity, mapping, or count validation failed. | Keep the active catalog and inspect validation details. |
| `DAEMON_UNAVAILABLE` | MCP adapter cannot reach fc27d. | Start fc27d and retry. |
| `BRIDGE_NOT_CONNECTED` | The extension is not polling the local daemon. | Open the FC27 Web App with the extension enabled. |
| `EA_SESSION_REQUIRED` | FC27 session headers or API base are unavailable. | Log in and navigate within the FC27 Web App. |
| `EA_VERIFICATION_REQUIRED` | EA returned a verification/captcha response. | Resolve it manually in the Web App. |
| `EA_RATE_LIMITED` | EA returned 429 and local backoff is active. | Wait until `retry_after` and retry once. |
| `EA_TRANSFER_RESTRICTED` | EA returned 461 and the local hard stop is active. | Do not retry until the reported stop time. |
| `ACCOUNT_MISMATCH` | Logged-in Persona differs from the selected runtime database. | Switch to the matching account database before syncing. |
| `SYNC_INCOMPLETE` | One or more required areas did not complete. | Inspect failed areas and retry a full sync. |
| `STALE_CLUB_STATE` | `expected_sync_id` is older than current state. | Sync, inspect the new state, and rebuild the action list. |
| `STALE_SQUAD_STATE` | The live squad hash differs from the Agent-observed hash. | Run `squad_query` and rebuild the exact action. |
| `SQUAD_WRITE_OUTCOME_UNKNOWN` | A squad write timed out before its outcome was known. | Read the exact squad and compare its hash; never retry automatically. |
| `SQUAD_READBACK_FAILED` | EA acknowledged a write but fresh squad state does not match the requested fields. | Inspect `squad_query` before another write. |
| `POLICY_DENIED` | The request exceeds user policy. | Reduce or remove the denied actions; Agent parameters cannot override policy. |
| `EXECUTION_DISABLED` | Policy mode is `observe`. | Complete read-only acceptance and change policy separately. |
| `IDEMPOTENCY_CONFLICT` | A key was reused with different parameters. | Use the recorded result or a new key for a different operation. |
| `ITEM_NOT_FOUND` | Exact owned item is absent from current state. | Sync and choose an existing item ID. |
| `TRADE_NOT_FOUND` | Exact market trade is no longer available. | Search again and choose a current trade ID. |
| `CONTENT_SOURCE_UNAVAILABLE` | The selected provider does not expose that content type through a stable machine-readable dataset. | Use EA for objectives/SBCs or FUT.GG for evolutions. |
| `SBC_SCHEMA_UNSUPPORTED` | Captured requirement type is not normalized. | Inspect raw evidence and implement that requirement before solving. |
| `SBC_OBJECTIVE_INVALID` | The Agent objective has an unknown field, invalid type, duplicate required ID, or invalid limit. | Correct the reported field and retry. |
| `SBC_OBJECTIVE_CONFLICT` | Required, excluded, candidate, slot, overall, or value limits contradict one another. | Remove the reported conflict or widen the objective. |
| `SBC_REQUIRED_ITEM_MISSING` | A required item ID is absent from the latest current club mirror. | Run `sync_club`, resolve the current item through `club_query`, and retry. |
| `SBC_REQUIRED_ITEM_INELIGIBLE` | A required item is protected, loaned, in an unsupported location, absent from the catalog, or filtered by the objective. | Choose an eligible owned item or revise the explicit objective. |
| `SBC_NO_SOLUTION` | CP-SAT proved that no owned-item combination satisfies the normalized challenge and objective. | Review fixed items and exclusions, widen the pool, or acquire eligible cards. |
| `SBC_SOLVER_TIMEOUT` | The bounded optimizer reached its deadline without a feasible candidate. | Narrow the pool or retry with a simpler objective. |
| `SBC_SOLVER_VALIDATION_FAILED` | A model result failed the independent validator. | Treat this as an implementation defect and inspect the returned evidence. |
| `SBC_NOT_ELIGIBLE` | Saved squad fails local or EA validation. | Inspect failed constraints and rebuild the squad. |
| `SBC_SUBMIT_ALREADY_ATTEMPTED` | The solution already has an active, completed, or unresolved submit. | Replay or reconcile the original action ID. |
| `SBC_SUBMIT_OUTCOME_UNKNOWN` | The submit write timed out before its outcome was known. | Reconcile the original action; never submit a new batch. |
| `SBC_SUBMIT_READBACK_PENDING` | EA acknowledged submit but full sync or challenge readback is incomplete. | Reconcile the original action through fresh read-only evidence. |
| `SBC_SUBMIT_CONFIRMED_NOT_APPLIED` | Fresh evidence proves the submit did not apply. | Review the refreshed saved squad, then create a separately confirmed batch if desired. |
| `SBC_SUBMIT_STILL_UNKNOWN` | Fresh evidence remains incomplete or contradictory. | Keep submit disabled and reconcile the same action later. |

## Tool-selection rule

Catalog facts use `catalog_query`; owned-item state uses `club_query`; playing squad, formation and tactics state uses `squad_query`; current listings use `market_search`; combined historical/economic context uses `price_context`; Seasons/objectives/Evolutions use `content_query`; current SBC capture uses `sbc_refresh`; persisted SBC reads use `sbc_query`.

Every advertised tool includes parameter descriptions and a common `outputSchema` for the success/error envelope. `execute_actions.actions` is a closed discriminated union: each action type exposes only its valid fields and requires its own exact parameters.
