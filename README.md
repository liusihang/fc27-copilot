# FC27 Copilot

English | [简体中文](README.zh-CN.md)

A self-hosted Model Context Protocol (MCP) server for EA SPORTS FC 27 Ultimate Team. FC27 Copilot connects an authenticated Web App session to a local player catalog, club inventory, SBC planner, and user-confirmed account operations.

The Agent selects strategies and evaluates alternatives. The MCP server supplies facts, state, calculations, validation, and explicit operations. Users sign in through the official EA Web App; raw session credentials remain in page memory.

The project is distributed as source for local, single-user deployment. Current components identify version `0.6.0`; no tagged release or browser-store package is published. See the [changelog](CHANGELOG.md) for changes and the [contribution guide](CONTRIBUTING.md) for testing and issue reports.

## Features

- **Player and inventory queries** — Search card definitions and identify exact owned copies across the Club, SBC Storage, Unassigned items, and Transfer List.
- **Automatic club synchronization** — Refresh inventory after login and successful account changes. Unchanged inventory retains its state version.
- **SBC planning** — Find owned-only squads first, compare alternatives requiring one or more purchases, require specific owned players, and respect challenge-native fillable slots.
- **Objectives and Evolutions** — Inspect Season rewards, task requirements and account progress across FC Objectives, Foundations, Milestones, Mastery, Seasonal, FC Pro, and Evolutions.
- **Squad management** — Read formations, player slots, tactics, and supported Web App options; apply explicitly approved changes.
- **Market analysis** — Combine FUT.GG reference prices, live EA listing samples, local observations, acquisition costs, and tax-adjusted calculations.
- **Controlled execution** — Purchase, bid, list, move, relist, clear sold items, save or submit SBC squads, and update squads or tactics within local policy limits.

## Getting started

### Requirements

- Python 3.10 or later.
- Node.js 18 or later.
- Chrome or Edge with the browser extension enabled.
- An MCP client that supports stdio.

The repository includes a ready-to-use player catalog. No initial catalog download or EA login is required for offline player queries.

Live-account validation currently covers macOS and Chromium-based browsers. Commands below use a POSIX shell; Windows equivalents are provided in the [installation guide](docs/install.md).

### Install and start the local service

Clone the repository, then create the Python environment:

```bash
git clone https://github.com/liusihang/fc27-copilot.git
cd fc27-copilot
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Validate the bundled catalog and prepare the extension:

```bash
.venv/bin/python scripts/validate_catalog.py data/catalog.sqlite
npm run check
npm run build:extension
```

Start the daemon and leave it running:

```bash
.venv/bin/python fc27d.py
```

The default address is `http://127.0.0.1:3926`. Check `/health` for service and catalog status. A macOS background-service installation is available in the [OpenClaw and service guide](docs/openclaw.md).

### Connect the browser and MCP client

1. Open `chrome://extensions` or `edge://extensions`, enable Developer mode, and load this project's `dist` directory as an unpacked extension.
2. Open the [official Ultimate Team Web App](https://www.ea.com/ea-sports-fc/ultimate-team/web-app/) and sign in. The extension connects and synchronizes automatically.
3. Register the stdio adapter with your MCP client. For clients using the `mcpServers` format, replace both placeholder paths below with your project paths:

```json
{
  "mcpServers": {
    "FC27": {
      "command": "/absolute/path/fc27-copilot/.venv/bin/python",
      "args": ["/absolute/path/fc27-copilot/mcp_stdio.py"]
    }
  }
}
```

Keep the daemon running: the stdio adapter does not start it. Set the client tool timeout to at least **240 seconds** when supported, and retain approval prompts for `execute_actions`. Start with `status` and `catalog_query`, then inspect `club_query` after login.

No extension ID or copied EA token is required. The extension exposes one optional setting for a different local server address. The internal `/mcp` endpoint is not advertised as a general-purpose Streamable HTTP transport.

See the [installation guide](docs/install.md) for Windows commands, updates, troubleshooting, and removal, or the [OpenClaw guide](docs/openclaw.md) for CLI registration.

When updating, stop the daemon, preserve local policy and catalog changes, update the code and Python dependencies, and run the checks plus `npm run build:extension`. Then restart the daemon, reload the unpacked extension, refresh the Web App, and reconnect the MCP client. Reloading an old `dist` directory does not update the extension. The [upgrade procedure](docs/install.md#updating) includes commands and conflict handling.

## Player catalog and updates

The bundled `data/catalog.sqlite` is a FUT.GG snapshot completed on **September 18, 2026**. It contains **19,676 cards** and **19,595 players**, uses catalog schema v3, and is approximately **18.4 MiB**. It contains card definitions and mappings, not account inventory, credentials, or market-price history. Its counts, metadata, and checksum are recorded in `data/catalog-manifest.json`.

The bundled snapshot is not live data. `catalog_query` reads it locally; login and `sync_club` update account inventory, not this catalog. To obtain newer cards, run maintenance from the project directory when access is permitted by the data provider:

1. Stop the daemon before replacing its database. For a foreground daemon, press `Ctrl+C`; for the macOS background service, use the stop/start commands in the [data guide](data/README.md#build-or-refresh-the-catalog).
2. Keep a backup, rebuild from FUT.GG, and validate the new snapshot:

```bash
cp data/catalog.sqlite data/catalog.sqlite.backup
.venv/bin/python scripts/refresh_catalog.py --out data/catalog.sqlite
.venv/bin/python scripts/validate_catalog.py data/catalog.sqlite \
  --write-manifest data/catalog-manifest.json
```

3. Confirm that validation reports `"ok": true`, then restart the daemon using the same method as before. For a foreground daemon:

```bash
.venv/bin/python fc27d.py
```

The refresh downloads temporary source data, converts it to the normalized catalog, and replaces the database after the converter's integrity, mapping, and count checks. The final validation reports the snapshot dates, counts, and checksum. It does not modify `data/accounts/` or require an EA login. If maintenance fails, inspect the error before restarting; keep the backup until the new catalog has passed validation.

Update when new cards are needed, or run maintenance every few days at your discretion. No scheduled catalog updater is installed. Windows commands, optional proxy settings, source imports, and recovery steps are in the [data guide](data/README.md). Because this file is now tracked, a locally refreshed catalog may conflict with a later Git update; back up your catalog and manifest before updating the code.

## MCP tools

The server is named `FC27` and exposes 12 tools. Client-specific name prefixes may differ.

| Tool | Purpose |
| --- | --- |
| `status` | Inspect service, session, synchronization, policy, and backoff readiness. |
| `catalog_query` | Search and compare local player-card definitions. |
| `club_query` | Read the latest complete local owned-item mirror. |
| `squad_query` | Read live squad slots, formations, tactics, and supported options. |
| `sync_club` | Refresh coins and inventory into the local account database. |
| `market_search` | Obtain live EA listings and concrete trade IDs. |
| `price_context` | Refresh reference prices and compare history, holdings, costs, and net proceeds. |
| `content_query` | Read Season, objective, and Evolution content and progress. |
| `sbc_query` | Read cached SBC sets, challenges, requirements, and status. |
| `sbc_refresh` | Refresh current SBC state from the authenticated Web App. |
| `sbc_solve` | Generate validated owned-only and purchase-budget plans. |
| `execute_actions` | Perform an exact, user-approved batch of account operations. |

Full parameter, result, and error definitions are documented in the [MCP contract](docs/mcp-contract.md).

## Execution policy

The default `suggest` policy permits implemented account operations, but **the Agent must ask for explicit user approval before every new execution batch**. It must first present the exact targets, player items, price limits or tactical changes, and irreversible effects.

- A request to complete an SBC authorizes discovery and planning, not purchases or submission.
- Saving and submitting an SBC require separate approvals. A save-only request must not consume players.
- Changed targets, limits, or a rebuilt stale-state batch require renewed approval.
- Queries, local price recording, automatic synchronization, and planning do not require account-write approval.
- Unknown write outcomes require verification or reconciliation of the original action, not an automatic retry.

`policy.json` is the execution authority; the Agent cannot override it. Defaults allow one action per batch and cap individual purchases, batch spend, and daily spend at 700 coins each. Users may adjust these local limits or select `observe` to disable all account writes. See the [execution policy](docs/execution-policy.md).

The server requires `confirmed=true` for new writes in every enabled mode, including `auto`. This value declares approval; it does not independently prove human consent. Tool descriptions and Server instructions cannot guarantee model behavior. Use a client that visibly presents and requests approval for account writes.

## Data and limitations

- **Catalog:** `data/catalog.sqlite` is the bundled card-definition snapshot. Queries do not refresh it; catalog maintenance is a separate operator action.
- **Account state:** `data/accounts/<persona_id>/runtime.sqlite` stores Persona-specific inventory, price observations, SBC solutions, and action history.
- **Private data:** Account databases, raw input archives, session credentials, logs, and SQLite sidecar files are not distributed with the code. Only the player catalog is included. Redact diagnostic material before sharing it.
- **Solver scope:** Automatic SBC candidates exclude protected, loan, special, Evolution, and current active-squad items. Unsupported requirements block solving; not every SBC is supported.
- **Result interpretation:** Optimality applies only to the reported candidate domain or local neighborhood. Reference prices do not guarantee availability, sale proceeds, or profit. Higher purchase budgets may exceed the request timeout; compare levels incrementally.
- **Deployment scope:** This is a local, single-user system, not a public multi-user service. Do not expose its local ports to the internet.

## Development

```bash
npm run check
.venv/bin/python -m unittest discover -s tests
```

`check` validates Python syntax and the browser extension. Automated tests use local fixtures and do not perform live EA account operations; source-database tests may skip when their input is unavailable. Live installation acceptance requires the user to sign in to the Web App.

CI runs offline tests, catalog validation, and extension checks/build on a clean checkout. It does not validate live EA behavior or prove that prior Git history is free of private information. See [CONTRIBUTING](CONTRIBUTING.md) for the manual read-only checklist and publication boundaries.

## Documentation

- [Installation](docs/install.md)
- [OpenClaw integration and macOS service](docs/openclaw.md)
- [MCP contract](docs/mcp-contract.md)
- [Execution policy](docs/execution-policy.md)
- [SBC planning and execution](docs/sbc-contract.md)
- [Architecture](docs/architecture.md)
- [Database schema](docs/database-schema.md)
- [Contributing and testing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## License and third-party services

Original project code and documentation are licensed under the [MIT License](LICENSE). Third-party source, data, artwork, trademarks, and service access remain subject to their respective licenses and terms.

This project is unofficial and is not affiliated with or endorsed by Electronic Arts or FUT.GG. Automated access may violate applicable service rules or result in account restrictions. The project does not bypass CAPTCHA, verification, rate limits, transfer restrictions, or access controls. Review [NOTICE](NOTICE.md) and the applicable service terms before use.
