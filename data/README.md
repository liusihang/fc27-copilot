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
