# OpenClaw integration

Complete the [generic installation](install.md) first. The registration command below was originally accepted with OpenClaw `2026.9.4`; check your installed CLI help if its options differ.

## Register the stdio server

Run from the FC27 Copilot project directory on macOS or Linux:

```bash
openclaw mcp add FC27 \
  --command "$PWD/.venv/bin/python" \
  --arg "$PWD/mcp_stdio.py" \
  --cwd "$PWD" \
  --connect-timeout 5 \
  --timeout 240 \
  --approval prompt
```

Windows users configure the same absolute script path with the project `.venv\Scripts\python.exe` interpreter through their client's supported configuration interface.

The daemon must already be running. Keep approval prompts enabled. The server requires the Agent to ask for explicit approval of each exact new account-write batch; approving a broad task or allowing the MCP server does not approve future writes.

Validation:

```bash
openclaw config validate
openclaw mcp status
openclaw mcp probe FC27
```

The probe should discover 12 tools. Use `status` and `catalog_query` for the first read-only checks, then inspect `club_query` after logging in to the Web App. Tool prefixes are assigned by the client; the server publishes unprefixed names such as `catalog_query`.

## Optional macOS service

From the project directory:

```bash
.venv/bin/python scripts/install_macos_service.py
```

The installer uses the current project directory and interpreter. Its application label is `io.github.liusihang.fc27d`, a project namespace, not a local user account. It listens on `127.0.0.1:3926` and writes its log to `~/Library/Logs/fc27d.log`.

A proxy is optional; use your own loopback proxy URL only when needed:

```bash
.venv/bin/python scripts/install_macos_service.py --proxy http://127.0.0.1:7897
```

Do not run a manual daemon and the LaunchAgent on the same port simultaneously.

## Remove the registration or stop the service

```bash
openclaw mcp unset FC27
openclaw mcp reload
```

Stopping the macOS service does not delete its database:

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.github.liusihang.fc27d.plist"
```

After a source update, follow the [upgrade procedure](install.md#updating), then reload the MCP connection and probe it again. A successful probe checks discovery, not the current EA login or inventory. Use `status` and fresh account reads to establish readiness.
