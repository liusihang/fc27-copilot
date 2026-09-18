#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fc27.catalog import CatalogDB
from fc27.errors import FC27Error


def main():
    parser = argparse.ArgumentParser(description="Query the normalized FC27 catalog.")
    parser.add_argument(
        "--catalog", default=PROJECT_ROOT / "data" / "catalog.sqlite", type=Path
    )
    parser.add_argument(
        "request",
        nargs="?",
        help="JSON request. If omitted, one JSON object is read from standard input.",
    )
    args = parser.parse_args()
    raw = args.request if args.request is not None else sys.stdin.read()
    request = json.loads(raw or "{}")
    try:
        result = CatalogDB(args.catalog).query(request)
    except FC27Error as error:
        print(json.dumps({"ok": False, "error": error.as_dict()}, ensure_ascii=False))
        raise SystemExit(2)
    print(json.dumps({"ok": True, "data": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
