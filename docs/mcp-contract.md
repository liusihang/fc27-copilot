# FC27 MCP contract

Date: 2026-09-18

Server name: `FC27`

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

Returns daemon/database health, browser bridge state, public EA session status, active Persona, coins, catalog metadata, latest complete sync, policy mode, and active rate-limit/backoff state. It never returns raw session headers.

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

### `FC27:sync_club`

Runs a read-only full or targeted synchronization through the authenticated browser bridge. Full mode records per-area completeness and commits only after all required areas finish.

Default areas: `coins`, `club`, `storage`, `unassigned`, and `tradepile`.

### `FC27:market_search`

Searches current EA listings for one explicit `card_ea_id` and bounded price range. It returns concrete `trade_id` values and aggregate sample statistics. It does not choose a purchase.

### `FC27:price_context`

Returns current and historical FUT.GG prices, EA scan history, holdings, listed count, recent acquisition costs, EA tax, and deterministic net-profit calculations for explicit card IDs.

### `FC27:sbc_query`

Lists cached/live SBC sets or returns one challenge with raw and normalized requirements, current status, slots, and source observation time.

### `FC27:sbc_solve`

Generates locally validated candidate squads from exact owned item IDs under a caller-provided objective. It returns multiple candidates and validation evidence. It never saves or submits.

### `FC27:execute_actions`

Executes an ordered list of exact operations. The Agent must provide targets and limits; the daemon does not select players, listings, prices, or SBC solutions.

Required batch controls:

- `batch_id`;
- `expected_sync_id`;
- `stop_on_error`;
- one unique `action_id` and `idempotency_key` per action;
- `confirmed=true` when policy mode requires confirmation.

Supported action types are introduced only after their own acceptance Issues: `buy_now`, `place_bid`, `list_item`, `move_item`, `relist_all`, `clear_sold`, `save_sbc_squad`, and `submit_sbc`.

### `FC27:catalog_refresh`

Checks the FUT.GG manifest and rebuilds the catalog when requested or due. Replacement occurs only after complete validation. The tool returns old/new snapshot identity and validation counts.

## Error catalog

| Code | Meaning | Recovery |
| --- | --- | --- |
| `CATALOG_NOT_FOUND` | Local catalog has not been built. | Run `FC27:catalog_refresh` or the import script. |
| `CATALOG_INVALID` | Integrity, mapping, or count validation failed. | Keep the active catalog and inspect validation details. |
| `DAEMON_UNAVAILABLE` | MCP adapter cannot reach fc27d. | Start fc27d and retry. |
| `BRIDGE_NOT_CONNECTED` | Local browser bridge page is not connected. | Open the bridge page and connect the extension. |
| `EA_SESSION_REQUIRED` | FC27 session headers or API base are unavailable. | Log in and navigate within the FC27 Web App. |
| `EA_VERIFICATION_REQUIRED` | EA returned a verification/captcha response. | Resolve it manually in the Web App. |
| `EA_RATE_LIMITED` | EA returned 429 and local backoff is active. | Wait until `retry_after` and retry once. |
| `EA_TRANSFER_RESTRICTED` | EA returned 461 and the local hard stop is active. | Do not retry until the reported stop time. |
| `ACCOUNT_MISMATCH` | Logged-in Persona differs from the selected runtime database. | Switch to the matching account database before syncing. |
| `SYNC_INCOMPLETE` | One or more required areas did not complete. | Inspect failed areas and retry a full sync. |
| `STALE_CLUB_STATE` | `expected_sync_id` is older than current state. | Sync, inspect the new state, and rebuild the action list. |
| `POLICY_DENIED` | The request exceeds user policy. | Reduce or remove the denied actions; Agent parameters cannot override policy. |
| `EXECUTION_DISABLED` | Policy mode is `observe`. | Complete read-only acceptance and change policy separately. |
| `IDEMPOTENCY_CONFLICT` | A key was reused with different parameters. | Use the recorded result or a new key for a different operation. |
| `ITEM_NOT_FOUND` | Exact owned item is absent from current state. | Sync and choose an existing item ID. |
| `TRADE_NOT_FOUND` | Exact market trade is no longer available. | Search again and choose a current trade ID. |
| `SBC_SCHEMA_UNSUPPORTED` | Captured requirement type is not normalized. | Inspect raw evidence and implement that requirement before solving. |
| `SBC_NOT_ELIGIBLE` | Saved squad fails local or EA validation. | Inspect failed constraints and rebuild the squad. |

## Tool-selection rule

Catalog facts use `catalog_query`; owned-item state uses `club_query`; current listings use `market_search`; combined historical/economic context uses `price_context`. This division prevents overlapping tools from returning subtly different meanings for the same question.
