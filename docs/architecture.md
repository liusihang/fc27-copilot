# Architecture

Date: 2026-09-18

```text
OpenClaw
  │ MCP stdio
  ▼
FC27 MCP adapter
  │ localhost
  ▼
fc27d
  ├── Catalog module ── FUT.GG ── catalog.sqlite
  ├── Account module ── Chrome bridge ── EA Web App
  ├── Market module ── reference prices + EA scans
  ├── SBC module ── requirements + deterministic validation
  ├── Execution module ── policy + idempotency + readback
  └── runtime.sqlite per Persona
```

## Module boundaries

`CatalogModule` owns public player/card data, normalized lookup relations, catalog validation and atomic replacement.

`AccountStateModule` owns identity selection, full/targeted synchronization and current owned-item state.

`MarketModule` owns FUT.GG reference-price changes, EA market scan aggregates and deterministic fee/profit calculations.

`SbcModule` owns captured requirement normalization, candidate generation and validation. The Agent selects which solution to use.

`ExecutionModule` receives exact actions. It enforces policy, stale-state checks, idempotency and post-operation readback.

## Database decision

The catalog and runtime databases are physically separate. Catalog refresh can build and atomically replace `catalog.sqlite` without replacing club state or operation history.

## Schema correction discovered during source validation

The supplied data uses FUT.GG `rarity_id=718` for Rare Gold, Rare Silver and Rare Bronze. Therefore `rarities` uses the composite key `(rarity_id, quality)`, and `cards` references both fields. A single-column `rarity_id` key would merge three distinct catalog values and lose information.

## Agent/tool boundary

The Agent decides targets, prices, priorities and whether a candidate is desirable. MCP returns facts, performs deterministic calculations, validates exact requests and executes authorized actions. No fixed trading strategy engine is part of the daemon.

## Live acceptance boundary

Offline tests prove catalog integrity, persistence behavior, MCP contracts and bridge mechanics. EA endpoint paths, response fields, pagination completion and SBC structures require a read-only test using the user's authenticated FC27 Web App session.
