# Local data directory

SQLite databases and raw source inputs are local artifacts, not included in the code repository or covered by its MIT license. Use only data you are authorized to access.

## Build or refresh the catalog

From the project directory, when FUT.GG access is permitted:

```bash
.venv/bin/python scripts/refresh_catalog.py --out data/catalog.sqlite
.venv/bin/python scripts/validate_catalog.py data/catalog.sqlite
```

The refresh builds a temporary source snapshot, validates mappings, converts it to schema v3, and replaces the active catalog only after validation. Queries do not update it. Run maintenance when needed; no recurring updater is installed by these commands.

## Import an existing source database

For a compatible source snapshot, replace the placeholder path with your own file:

```bash
.venv/bin/python scripts/import_catalog.py \
  --source /path/to/source/fc27.sqlite \
  --target data/catalog.sqlite
.venv/bin/python scripts/validate_catalog.py data/catalog.sqlite
```

Do not point `--source` at a Persona runtime database. `catalog-manifest.json` records a historical accepted snapshot, not the expected counts or hash of every future catalog.

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
