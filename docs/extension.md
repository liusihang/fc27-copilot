# Chrome extension

The extension is a thin adapter between `fc27d` and the authenticated FC27 Web App page.

## Build

```bash
npm run validate:extension
npm run build:extension
```

Load `/absolute/path/Documents/fc27-copilot/dist` as an unpacked extension from `chrome://extensions`.

## Connection flow

1. Start `python3 fc27d.py`.
2. Open `http://127.0.0.1:3926` in the same Chrome profile.
3. Paste the unpacked extension ID into the bridge page.
4. Open the FC27 Ultimate Team Web App.
5. The page adapter observes the Web App's own FC27 UTAS requests and captures the current API base and session headers.

The bridge page only receives structured call results. Raw `X-UT-SID` and `X-UT-PHISHING-TOKEN` values stay inside the injected page-script closure and are never written to Chrome storage, fc27d, SQLite, logs, or MCP output.

## Responsibilities

The extension owns:

- dynamic FC27 API-base discovery;
- current session-header capture in page memory;
- selected Persona/club identity reads;
- structured EA request execution;
- public connection/session status.

`fc27d` owns rate limits, policy, persistence, idempotency, synchronization, readback, and MCP. OpenClaw owns strategy decisions.

## Current acceptance boundary

Static validation and the unpacked build pass without an EA account. The first live acceptance remains GitHub Issue #12 and requires the user to log in. That test is read-only.
