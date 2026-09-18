# OpenClaw integration

Verified locally on 2026-09-18 with OpenClaw `2026.9.4 (3a9d69d)`.

## fc27d service

Install or refresh the macOS LaunchAgent:

```bash
python3 scripts/install_macos_service.py --proxy http://127.0.0.1:7897
```

Installed service:

- label: `io.github.liusihang.fc27d`;
- plist: `~/Library/LaunchAgents/io.github.liusihang.fc27d.plist`;
- bind: `127.0.0.1:3926`;
- log: `~/Library/Logs/fc27d.log`.

## MCP registration

OpenClaw manages this server under `mcp.servers.FC27`:

```bash
openclaw mcp add FC27 \
  --command /path/to/installation/opt/python@3.14/bin/python3.14 \
  --arg /absolute/path/Documents/fc27-copilot/mcp_stdio.py \
  --cwd /absolute/path/Documents/fc27-copilot \
  --connect-timeout 5 \
  --timeout 65 \
  --approval prompt
```

Validation commands:

```bash
openclaw config validate
openclaw mcp status
openclaw mcp probe FC27
```

## Accepted result

- OpenClaw discovered exactly 10 FC27 tools.
- `fc27d` survived a forced LaunchAgent restart and returned 19,676 cards afterward.
- The existing `fc-expert` Agent called `fc27__catalog_query` successfully.
- Query `card_ea_id=231747` returned Kylian Mbappé, overall 91, primary position ST.
- OpenClaw provider/model configuration and unrelated agent configuration were byte-for-byte equivalent after removing the newly added `mcp` object from comparison.

## Rollback

Remove the MCP registration:

```bash
openclaw mcp unset FC27
openclaw mcp reload
```

Stop and unload the daemon:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/io.github.liusihang.fc27d.plist
```

The pre-registration OpenClaw rollback file created during acceptance is:

```text
/absolute/path/.openclaw/backups/openclaw-pre-fc27-mcp-20260918T181851.json
```
