# Database schema contract

## Identifier rules

| Field | Scope | Rule |
| --- | --- | --- |
| `base_player_ea_id` | player identity | Stable identity shared by all versions of one real player. |
| `card_ea_id` | card definition | Primary key used by EA card/market queries. |
| `futgg_id` | source record | Unique FUT.GG record used for refresh reconciliation. |
| `item_id` | owned item | Concrete account-owned instance. Never substitutes for `card_ea_id`. |
| `trade_id` | auction | Concrete market listing. Never substitutes for `item_id`. |
| `sync_id` | owned-item state | Version of the latest complete mirror; unchanged owned-item state retains it. |
| `action_id` | operation | Caller-visible operation identity. |
| `batch_id` | operation group | Caller-visible ordered batch identity. |

## `catalog.sqlite`

`players` stores real-player identity once. `player_card_summary` derives card count and minimum/maximum overall.

`cards` stores one row per card definition. It references `players`, `clubs`, `leagues`, and `rarities` and contains only card-specific attributes and flags.

`nations`, `leagues`, `clubs`, `positions`, and `rarities` are dictionaries. `rarities` has composite key `(rarity_id, quality)` because the supplied snapshot uses `rarity_id=718` for Rare Gold, Rare Silver, and Rare Bronze.

`card_positions` stores primary and alternate positions. A partial unique index permits exactly one primary position per card.

`playstyles` and `card_playstyles` store definitions and card membership. Tier `1` means normal PlayStyle; tier `2` means PlayStyle+.

`roles` and `card_roles` store definitions and card membership. Tier `1` means Role+; tier `2` means Role++.

`catalog_meta` contains only schema version, game year, snapshot times, manifest identity, counts, and source.

## `runtime.sqlite`

Each database binds to exactly one Persona. The Persona ID belongs in `account_state`, not in every table.

| Table | Responsibility | Redundancy decision |
| --- | --- | --- |
| `account_state` | Persona, club, platform, coins, latest complete sync | One row per runtime DB. |
| `sync_runs` | Synchronization lifecycle and state versions | Keeps completion and provenance evidence. |
| `sync_parts` | Per-area page/item counts and completeness | Required to prove whether removal is safe. |
| `club_items` | Current owned-item state | Does not repeat catalog names or ratings. |
| `inventory_changes` | Added, moved, removed, or changed items | Stores changes only, not full snapshots. |
| `trade_listings` | Listing lifecycle for owned items | Keeps trade facts after an item leaves current inventory. |
| `reference_prices` | FUT.GG price/status changes | Adds a row only when value or status changes. |
| `market_scans` | EA listing sample aggregates | Does not retain all transient listings. |
| `action_batches` | Batch lifecycle and expected sync | One row per exact Agent request. |
| `actions` | Idempotent atomic operations | Common target IDs are indexed; variable parameters/results use JSON. |
| `coin_transactions` | Durable purchase/sale/reward adjustments | Keeps `card_ea_id` as a historical snapshot. |
| `sbc_sets` | Current/cached set state | Retains raw JSON for source evidence. |
| `sbc_challenges` | Current/cached challenge and requirements | Normalized fields plus variable raw requirements. |
| `sbc_solutions` | Objective and validation evidence | One row per candidate solution. |
| `sbc_solution_items` | Exact item assignment by slot | Stores `item_id`; catalog attributes remain joined data. |

## JSON fields

JSON is permitted for structures whose shape changes across endpoints or action types:

- SBC raw requirements, objectives, and validation evidence;
- Role focus data;
- action parameters and results;
- raw SBC source evidence.

Names, ratings, positions, IDs, current locations, prices, and queryable flags remain typed columns or normalized relations.

## Synchronization invariant

A full synchronization updates and removes current items only when every requested required area has `complete=1`. Any incomplete area marks the run failed and preserves the previous complete state.

Local `protected`, `acquisition_cost`, and `first_seen_at` values survive synchronization. EA observations update location, tradeability, loan usage, last-seen time, and provenance.

If authoritative owned-item state is unchanged, another complete observation reuses its `sync_id`. Coin values, observation times, and listing observations may update without changing the owned-item version. Consumers must check both completeness and observation time; an unchanged version does not mean no observation occurred.

## Catalog replacement invariant

Refresh downloads temporary source data and converts it through `catalog.sqlite.new`. The converter validates integrity, foreign keys, source counts, primary positions, and normalized mappings before checkpointing and atomic replacement. A final validation reports unique IDs, mapping coverage, counts, metadata, and checksum after replacement. Stop the daemon and keep a backup until final validation succeeds. Runtime databases are never part of this replacement; see the [data guide](../data/README.md#build-or-refresh-the-catalog).
