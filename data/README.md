# Catalog and local account data

The repository includes `catalog.sqlite`, a ready-to-use FUT.GG player-card snapshot, and `catalog-manifest.json`, its validation report. The snapshot completed on September 18, 2026 and contains 19,676 cards and 19,595 players in catalog schema v3. The database is 19,288,064 bytes (approximately 18.4 MiB) and works without SQLite WAL/SHM files.

It contains card definitions, player attributes, positions, PlayStyles, Roles, and their mappings. It does not contain account inventory, session credentials, action history, or market prices. This third-party data is not covered by the project's MIT license; use only data you are authorized to access.

For first installation, validate the bundled database without downloading anything:

```bash
.venv/bin/python scripts/validate_catalog.py data/catalog.sqlite
```

## Build or refresh the catalog

Run from the project directory when FUT.GG access is permitted. Stop the daemon first so no catalog queries or SBC planning overlap the replacement. For a foreground daemon, press `Ctrl+C`. For an installed macOS LaunchAgent, stop it with:

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.github.liusihang.fc27d.plist"
```

Back up the current catalog, fetch the new snapshot, and write its validation manifest:

```bash
cp data/catalog.sqlite data/catalog.sqlite.backup
.venv/bin/python scripts/refresh_catalog.py --out data/catalog.sqlite
.venv/bin/python scripts/validate_catalog.py data/catalog.sqlite \
  --write-manifest data/catalog-manifest.json
```

Check that validation reports `"ok": true`, `"integrity": "ok"`, and zero foreign-key or mapping errors. `metadata.snapshot_finished_at` records when the snapshot was fetched; the card and player counts may change. Restart a foreground daemon with:

```bash
.venv/bin/python fc27d.py
```

Or restart the previously installed macOS LaunchAgent without changing its saved settings:

```bash
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.github.liusihang.fc27d.plist"
```

Use only the same startup method as before, not both. The refresh downloads temporary source data, validates mappings, converts it to schema v3, and replaces the catalog after the converter's integrity, foreign-key, and count checks. Final validation produces counts, metadata, and a checksum. Source-download or pre-replacement validation failures leave the old catalog unchanged. If a later validation fails, keep the daemon stopped, investigate, or restore the backup and validate it before starting again:

```bash
cp data/catalog.sqlite.backup data/catalog.sqlite
.venv/bin/python scripts/validate_catalog.py data/catalog.sqlite \
  --write-manifest data/catalog-manifest.json
```

Neither `catalog_query` nor account synchronization updates this catalog. Maintenance requires network access, not EA login, and does not touch `data/accounts/`. Run it when you need new cards, or every few days at your discretion; these commands do not install a recurring updater.

On Windows, use `.\.venv\Scripts\python.exe` instead of `.venv/bin/python`, and `Copy-Item` instead of `cp`. After stopping the daemon, run in PowerShell:

```powershell
Copy-Item data/catalog.sqlite data/catalog.sqlite.backup
.\.venv\Scripts\python.exe scripts/refresh_catalog.py --out data/catalog.sqlite
.\.venv\Scripts\python.exe scripts/validate_catalog.py data/catalog.sqlite --write-manifest data/catalog-manifest.json
.\.venv\Scripts\python.exe fc27d.py
```

Run each step only after the previous one succeeds. If a network proxy is needed, see the settings below; persistent access errors or verification requirements must be resolved without bypassing provider controls.

The catalog and manifest are tracked in Git. A local refresh changes them and can conflict with a later code update. Back up both before pulling code; resolve which snapshot to keep rather than discarding local data blindly. SQLite sidecars, backups, temporary databases, raw source inputs, and account databases remain ignored. Maintainers should commit the validated catalog and its matching manifest together when publishing a new bundled snapshot.

## Import an existing source database

For a compatible source snapshot, replace the placeholder path with your own file:

```bash
.venv/bin/python scripts/import_catalog.py \
  --source /path/to/source/fc27.sqlite \
  --target data/catalog.sqlite
.venv/bin/python scripts/validate_catalog.py data/catalog.sqlite
```

Stop the daemon and back up the catalog before importing, just as for a refresh. Do not point `--source` at a Persona runtime database or the normalized `catalog.sqlite`: the importer expects the source v2 layout. After a successful import, regenerate `catalog-manifest.json` with `--write-manifest` and restart the daemon. The bundled manifest describes its corresponding snapshot, not fixed counts or hashes that every future catalog must have.

On Windows, use `.\.venv\Scripts\python.exe` instead of `.venv/bin/python`.

## Optional network settings

Only if the network requires your own proxy, set standard proxy environment variables. This loopback address is an example, not a required service:

```bash
HTTPS_PROXY=http://127.0.0.1:7897 \
HTTP_PROXY=http://127.0.0.1:7897 \
NO_PROXY=127.0.0.1,localhost \
.venv/bin/python scripts/refresh_catalog.py --out data/catalog.sqlite
```

## Account data

`data/accounts/<persona_id>/runtime.sqlite` holds the selected Persona's inventory, observations, SBC solutions, and execution records. Login and successful account mutations trigger synchronization. An unchanged owned-item state retains its sync version while observation times and coin data may change.

Keep these databases private. They contain account identifiers, inventory, and history even though raw EA session headers are not stored. Never include them or logs in an issue or release without reviewing and redacting their contents.
