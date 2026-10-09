# Architecture

```text
MCP client and Agent (OpenClaw or another stdio client)
  │ MCP stdio
  ▼
FC27 MCP adapter
  │ localhost
  ▼
fc27d
  ├── Catalog module ── FUT.GG ── catalog.sqlite
  ├── Account module ── Chrome bridge ── EA Web App
  ├── Market module ── reference prices + EA scans
  ├── Content module ── EA account content + FUT.GG public evolutions
  ├── SBC module ── requirements + CP-SAT optimization + independent validation
  ├── Execution module ── policy + idempotency + readback
  └── runtime.sqlite per Persona
```

## Module boundaries

`fc27/catalog.py` owns local player/card queries and normalized lookup relations. Operator scripts import, refresh, and validate the bundled catalog independently of account synchronization.

`fc27/account.py`, `fc27/runtime.py`, and `fc27/auto_sync.py` own account reads, Persona-bound persistence, and serialized automatic synchronization. A partial read never removes items from the latest complete mirror; unchanged owned-item state retains its version.

`fc27/market.py` owns FUT.GG reference-price changes, EA market scan aggregates, and deterministic fee/profit calculations.

`fc27/content.py` normalizes Season levels, objective groups/tasks, account Evolution slots, and FUT.GG public Evolution definitions. SBC discovery is separate: `sbc_refresh` captures current EA facts and `sbc_query` reads persisted facts.

`fc27/sbc.py` and `fc27/sbc_optimizer.py` own requirement normalization, EA-native fillable slots, exact mandatory items, incremental purchase-budget branching, residual market candidates, bounded CP-SAT realization, owned-only solution persistence, and independent validation. Their Agent interface is `sbc_solve` with `purchase_budget=0` by default. The Agent selects a plan and decides whether another purchase level is useful. See the [SBC contract](sbc-contract.md) for chemistry, fixed slots, and proof limits.

`fc27/execution.py`, `fc27/policy.py`, and `fc27/actions.py` enforce policy, exact-batch confirmation, stale-state checks, idempotency, dispatch, and independent readback. `fc27/squad.py` normalizes live playing-squad state and its change hash.

`fc27/mcp.py` defines tool descriptions, schemas, Server instructions, and response envelopes. `mcp_stdio.py` forwards the stdio protocol to `fc27/daemon.py`; it does not launch the daemon. The browser extension keeps raw session credentials in page memory and exposes only structured operations and public session state.

## Database decision

The catalog and runtime databases are physically separate. Catalog refresh can build and atomically replace `catalog.sqlite` without replacing club state or operation history.

## Rarity identity

The supplied data uses FUT.GG `rarity_id=718` for Rare Gold, Rare Silver and Rare Bronze. Therefore `rarities` uses the composite key `(rarity_id, quality)`, and `cards` references both fields. A single-column `rarity_id` key would merge three distinct catalog values and lose information.

## Agent/tool boundary

The Agent decides targets, prices, priorities and whether a candidate is desirable. MCP returns facts, performs deterministic calculations, validates exact requests and executes authorized actions. No fixed trading strategy engine is part of the daemon.

## Browser lifecycle

The persistent Web App content script requests one bounded daemon poll through the extension service worker. This message-scoped request works with Manifest V3 suspension. `fc27d` requeues an exact pending request if the HTTP response connection closes before delivery. Public login and mutation events enter one daemon-owned synchronization coordinator that shares the execution lock with explicit actions.

## Verification boundary

Offline tests check catalog integrity, persistence behavior, MCP contracts, and bridge mechanics. They do not establish current EA endpoint behavior. Response fields, pagination, slots, chemistry, and browser operations need a fresh read-only comparison against an authenticated Web App. Writes require a separate exact approval, even during testing. See [CONTRIBUTING](../CONTRIBUTING.md#manual-read-only-verification).
