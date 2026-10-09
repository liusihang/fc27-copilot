# Contributing

FC27 Copilot is a local, single-user MCP integration. Keep changes focused and retain the boundary between Agent decisions, objective facts and calculations, and explicitly confirmed account writes.

## Development setup

Follow the [installation guide](docs/install.md) ([简体中文](docs/install.zh-CN.md)) to create the Python environment. The only direct Python dependency is pinned in `requirements.txt`; the extension has no npm dependencies. Tests do not require an EA login.

Run from the project root:

```bash
npm run check
.venv/bin/python -m unittest discover -s tests
.venv/bin/python scripts/validate_catalog.py data/catalog.sqlite
```

On Windows, use `.\.venv\Scripts\python.exe`. If no `python3` alias exists, run Python compilation and `npm run validate:extension` separately as shown in the installation guide. After extension changes, also run `npm run build:extension`, reload the unpacked extension, and refresh the Web App.

The Python tests cover catalog queries, runtime persistence and synchronization, policy and action execution with fixtures, SBC solving and validation, squad state, and MCP/HTTP contracts. An optional import round-trip test skips when the original local v2 source database is absent. Preserve the regression fixtures, including variable-size SBCs and exact field-slot cases.

CI runs on a clean Ubuntu checkout with Python 3.10 and Node.js 22. It installs the pinned Python dependency, runs offline checks/tests and catalog validation, and builds the extension. It does not call EA, claim rewards, purchase players, or submit SBCs. JavaScript syntax and source-contract checks do not prove current Web App behavior.

## Change guidelines

- Keep strategies and user choices in the Agent. Do not add fixed trading decisions to MCP tools.
- Distinguish player, card, owned item, trade, squad, and challenge identities. Preserve freshness, completeness, and proof-scope information.
- Never guess an unsupported EA requirement, field slot, or chemistry modifier. Add captured, sanitized evidence and a regression before supporting it.
- Keep writes behind the existing policy, exact confirmation, state checks, and audit/readback flow. Saving an SBC must not imply approval to submit it.
- Add a regression for a demonstrated defect; do not remove a fixture merely because it exposes one.
- Do not add third-party code or data without checking its license, provenance, and required notices. Keep direct dependencies pinned.
- Update the relevant public contract and both READMEs when user-visible behavior changes. Add an unreleased changelog entry. Keep internal plans, account logs, and handoffs outside the public repository.

## Reporting a problem

Include the commit/component version, operating system, Python/Node versions, browser and MCP client, exact steps, expected result, actual result, and error code. For a tool failure, include a small sanitized request and the relevant `meta.complete`, observation time, and warnings. For an unsupported SBC, provide normalized constraints and the smallest sanitized raw requirement needed to reproduce it.

Remove session headers, credentials, Persona/club identifiers, owned-item/trade IDs, machine paths, and private network addresses. Keep public card IDs, requirement keys, and slot indices where they explain the defect. Use consistent synthetic identities across the example; do not post a runtime database or complete account dump.

For solver performance, include candidate/domain counts, purchase budget, solver status, reported proof scope, and elapsed time. A feasible or local optimum is not a claim of global minimum market cost.

## Manual read-only verification

For changes to the browser adapter or EA payload handling, use your own account and keep the check read-only:

1. Start the updated daemon, rebuild/reload the extension, refresh the Web App, and sign in personally.
2. Inspect `status` for bridge readiness and automatic-sync completion. Compare `club_query` area counts and completeness with the Web App; do not interpret an incomplete result as an empty club.
3. Compare the relevant objective sections, Season levels, and Evolution progress with the UI, including completed, claimable, and unmatched records.
4. Refresh one available SBC and verify its actual player count, fillable indices, fixed slots, and normalized requirements. Solve owned-only first. Preserve explicit unsupported results rather than ignoring them.
5. For squad changes, read detailed squad state and current options. Verify the formation, slot order, tactics fields, and canonical hash without writing.

Do not buy, list, move, save, submit, claim, or start an Evolution as part of this checklist. A separate write test requires an exact target, explicit user approval, and independent readback; reversible changes also need a reviewed restoration plan. Resolve unknown outcomes through the original action rather than retrying.

## Publication boundaries

Automated publication checks cover distributed text in the current checkout and public-document file links. They are not a full credential audit and do not inspect Git history, GitHub discussions, issue attachments, or outside copies. Prior published copies and hosting references can remain outside the cleaned repository history.

The bundled player catalog is third-party data, not MIT-licensed project code. Its metadata records provenance, not a redistribution grant. Follow [NOTICE](NOTICE.md) and establish applicable data permissions before redistributing it.

A release requires an explicit maintainer decision. Verify component versions, changelog, clean-checkout tests, catalog/manifest correspondence, and the extension build before creating a tag or release. Do not describe a source snapshot or offline CI as a fresh live-account acceptance.
