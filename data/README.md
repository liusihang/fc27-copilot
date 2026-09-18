# Local data directory

SQLite databases are runtime/build artifacts and are not committed to Git.

To reproduce the verified catalog from the supplied bundle:

```bash
unzip /absolute/path/Downloads/fc27_v2_bundle.zip -d /tmp/fc27-v2
python3 scripts/import_catalog.py \
  --source /tmp/fc27-v2/fc27.sqlite \
  --target data/catalog.sqlite
python3 scripts/validate_catalog.py data/catalog.sqlite \
  --expected-cards 19676 \
  --expected-players 19595
```

`catalog-manifest.json` records the accepted normalized output hash and validation counts from 2026-09-18.

Future account databases live under `data/accounts/<persona_id>/runtime.sqlite` and remain local.

To refresh from the current FUT.GG manifest:

```bash
HTTPS_PROXY=http://127.0.0.1:7897 \
HTTP_PROXY=http://127.0.0.1:7897 \
python3 scripts/refresh_catalog.py --out data/catalog.sqlite
```

The refresh builds a temporary source snapshot, validates all source mappings, converts it to schema v3, validates the normalized database, and replaces the target only after the converter's integrity gates pass.
