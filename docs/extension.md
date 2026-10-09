# Chrome extension

The extension is a thin adapter between `fc27d` and the authenticated FC27 Web App page.

## Build

```bash
npm run validate:extension
npm run build:extension
```

Load this project's `dist` directory as an unpacked extension from `chrome://extensions` or `edge://extensions`. See the [generic installation guide](install.md).

## Connection flow

1. Keep `fc27d` installed as the macOS LaunchAgent or start it with the project virtual-environment Python.
2. Load this project's `dist` directory as an unpacked extension once.
3. Open the FC27 Ultimate Team Web App and sign in.

The Web App content script drives one bounded localhost long poll at a time, so Manifest V3 may suspend and resume the service worker without losing the bridge. The default FC27 server is `http://127.0.0.1:3926`. The popup exposes one optional loopback-address field when a different local port is required.

No localhost bridge page or extension ID is required. Raw `X-UT-SID` and `X-UT-PHISHING-TOKEN` values stay inside the injected page-script closure and are never written to Chrome storage, fc27d, SQLite, logs, or MCP output.

## Automatic synchronization

- A new authenticated session schedules a complete club synchronization after Web App services initialize.
- Successful EA writes under account mutation paths schedule one debounced synchronization.
- Login synchronization retries retryable service-readiness errors at most three times.
- A complete explicit post-action synchronization suppresses a redundant event-triggered run.
- Browser request delivery is requeued when the long-poll HTTP connection closes before the daemon writes the response.

## Responsibilities

The extension owns:

- dynamic FC27 API-base discovery;
- current session-header capture in page memory;
- selected Persona/club identity reads;
- structured EA request execution;
- public connection/session status.
- direct localhost polling and public change-event delivery.

`fc27d` owns rate limits, policy, persistence, idempotency, synchronization, readback, and MCP. The MCP client Agent owns strategy decisions and must ask the user before every new account-write batch.

## Updates and validation

After updating source code, run `npm run validate:extension` and `npm run build:extension`, reload the unpacked extension, and refresh the Web App. Reloading `dist` without rebuilding it uses the previous files. See the [upgrade procedure](install.md#updating).

Automated checks validate the manifest, JavaScript syntax, imports, and selected bridge contracts. They do not replace a read-only comparison with the current EA Web App. Follow the [manual checklist](../CONTRIBUTING.md#manual-read-only-verification) after changing the page adapter. Account writes always require separate exact user approval.
