# Installation and maintenance

English | [简体中文](install.zh-CN.md)

FC27 Copilot runs on the same computer as the EA Web App browser. It requires Python 3.10+, Node.js 18+, Chrome or Edge, and an MCP client with stdio support. Live-account checks have covered macOS and Chromium browsers; offline CI does not prove live EA compatibility on another platform.

This is a source installation, not an npm package, browser-store extension, or hosted MCP service. All commands below run from the project directory.

## 1. Install dependencies

```bash
git clone https://github.com/liusihang/fc27-copilot.git
cd fc27-copilot
```

macOS / Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The extension has no npm dependencies, so `npm install` is not required.

## 2. Check the bundled catalog

The repository includes `data/catalog.sqlite`, a FUT.GG snapshot completed on September 18, 2026 with 19,676 cards and 19,595 players. Initial installation does not need a catalog download or EA login.

```bash
.venv/bin/python scripts/validate_catalog.py data/catalog.sqlite
```

On Windows, replace `.venv/bin/python` with `.\.venv\Scripts\python.exe`. For newer cards, follow the [catalog maintenance guide](../data/README.md#build-or-refresh-the-catalog). Catalog maintenance and account synchronization are separate operations.

## 3. Build the extension and start the daemon

```bash
npm run check
npm run build:extension
```

If Windows has no `python3` alias, run these checks instead, followed by the same build command:

```powershell
.\.venv\Scripts\python.exe -m compileall -q fc27 tests scripts fc27d.py mcp_stdio.py
npm run validate:extension
npm run build:extension
```

Start the daemon and keep it running:

```bash
.venv/bin/python fc27d.py
```

The default address is `http://127.0.0.1:3926`. Open `/health` at that address to inspect readiness. The stdio adapter does not start the daemon.

On macOS, a background service is optional:

```bash
.venv/bin/python scripts/install_macos_service.py
```

Use either the foreground daemon or the background service, not both on the same port. Service and optional proxy settings are described in the [OpenClaw guide](openclaw.md).

Open `chrome://extensions` or `edge://extensions`, enable Developer mode, and load this project's `dist` directory as an unpacked extension. Open the official Ultimate Team Web App and sign in yourself. The extension connects and synchronizes automatically. No extension ID, copied EA token, or separate bridge page is required. Change the extension's server-address setting only if using another local port.

## 4. Register the stdio MCP server

For clients using the `mcpServers` format, replace both absolute-path placeholders:

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

On Windows, use the absolute path to `.venv\Scripts\python.exe`. JSON backslashes must be doubled, or use forward slashes. Other clients may use a different configuration wrapper with the same command and arguments.

Set the client tool timeout to at least **240 seconds** when supported. The stdio adapter uses a 240-second request timeout; the owned-only solver has a 180-second window. Positive purchase budgets can take longer, so compare them incrementally. Keep approval prompts enabled for `execute_actions`.

Reload the MCP connection. It should discover 12 tools. Begin with `status` and `catalog_query`; after login, inspect `club_query`. Use `sync_club` explicitly when the mirror is stale or reports a synchronization error. OpenClaw CLI commands are in the [integration guide](openclaw.md).

## 5. Confirm account writes

The default `suggest` policy permits implemented writes within local limits. Before every new batch, the Agent must present exact targets, price limits or irreversible effects, ask the user, and obtain explicit approval before declaring `confirmed=true`.

Querying, synchronizing, and solving do not approve purchases, saves, or submission. SBC save and submit require separate approvals. If the client does not expose Server instructions, add the [confirmation rules](execution-policy.md#user-confirmation) to its Agent instructions. Never let an Agent change `policy.json` merely to bypass a rejected action.

## Updating

1. Finish or reconcile any active account operation, then stop the daemon. Press `Ctrl+C` for a foreground daemon. For the installed macOS service:

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.github.liusihang.fc27d.plist"
```

2. Inspect `git status --short`. Keep an external private backup of local policy, a locally refreshed catalog and manifest, and account data. `policy.json`, `data/catalog.sqlite`, and `data/catalog-manifest.json` are tracked files; local edits can conflict with an upstream update. If Git refuses the update, preserve and resolve those changes before proceeding. Do not force an update with a destructive reset.
3. Update the code, dependencies, and generated extension. Run each command only after the preceding command succeeds:

```bash
git pull --ff-only
.venv/bin/python -m pip install -r requirements.txt
npm run check
npm run build:extension
.venv/bin/python scripts/validate_catalog.py data/catalog.sqlite
```

Windows uses the interpreter and check commands shown above. A code update uses the selected bundled or local catalog; fetch newer cards separately when needed.

4. Restart using the previous method. Start `.venv/bin/python fc27d.py` in a foreground terminal, or restore the installed macOS service without changing its saved settings:

```bash
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.github.liusihang.fc27d.plist"
```

5. Reload the unpacked extension on the browser's extension page, refresh the Web App, and sign in if required. Reconnect or reload the client's MCP connection, then check `status` and `club_query`.

**Reloading an old `dist` without running `npm run build:extension` does not install the new extension code.** The build replaces only `dist`; it does not change account databases.

## Troubleshooting and removal

- `DAEMON_UNAVAILABLE`: check the daemon, configured address, and proxy routing. Keep `127.0.0.1,localhost` in `NO_PROXY` when using a proxy.
- `EA_SESSION_REQUIRED` or a disconnected bridge: open the Web App, sign in, or reload the extension. Complete verification yourself.
- Incomplete results: inspect `meta.complete`, warnings, observation time, and pagination. An empty page does not prove that no tasks exist.
- Unknown write outcome: verify or reconcile the original action before any new batch. A timeout does not prove the action failed at EA.
- To uninstall, remove `FC27` from the MCP client, stop the daemon/background service, and remove the extension. Retain account databases as needed; uninstalling does not require deleting them.

Keep local ports off the public internet. Code licensing, provider data permissions, and account rules are distinct; see [NOTICE](../NOTICE.md).
