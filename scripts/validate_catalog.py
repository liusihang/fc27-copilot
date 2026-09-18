#!/usr/bin/env python3
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


TABLES = (
    "cards",
    "players",
    "nations",
    "leagues",
    "clubs",
    "positions",
    "rarities",
    "card_positions",
    "playstyles",
    "card_playstyles",
    "roles",
    "card_roles",
)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_catalog(path, expected_cards=None, expected_players=None):
    path = Path(path).resolve()
    try:
        display_path = str(path.relative_to(Path.cwd().resolve()))
    except ValueError:
        display_path = str(path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLES
        }
        checks = {
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_errors": len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            ),
            "unique_card_ea_ids": connection.execute(
                "SELECT COUNT(DISTINCT card_ea_id) FROM cards"
            ).fetchone()[0],
            "unique_futgg_ids": connection.execute(
                "SELECT COUNT(DISTINCT futgg_id) FROM cards"
            ).fetchone()[0],
            "primary_positions": connection.execute(
                "SELECT COUNT(*) FROM card_positions WHERE is_primary = 1"
            ).fetchone()[0],
            "unmapped_playstyles": connection.execute(
                """SELECT COUNT(*) FROM card_playstyles rel
                   LEFT JOIN playstyles definition USING(playstyle_ea_id)
                   WHERE definition.playstyle_ea_id IS NULL"""
            ).fetchone()[0],
            "unmapped_roles": connection.execute(
                """SELECT COUNT(*) FROM card_roles rel
                   LEFT JOIN roles definition USING(role_id)
                   WHERE definition.role_id IS NULL"""
            ).fetchone()[0],
        }
        metadata = dict(connection.execute("SELECT key, value FROM catalog_meta"))
    finally:
        connection.close()

    errors = []
    if checks["integrity"] != "ok":
        errors.append(f"integrity={checks['integrity']}")
    if checks["foreign_key_errors"]:
        errors.append(f"foreign_key_errors={checks['foreign_key_errors']}")
    if checks["unique_card_ea_ids"] != counts["cards"]:
        errors.append("card_ea_id is not unique")
    if checks["unique_futgg_ids"] != counts["cards"]:
        errors.append("futgg_id is not unique")
    if checks["primary_positions"] != counts["cards"]:
        errors.append("not every card has exactly one primary position")
    if checks["unmapped_playstyles"]:
        errors.append(f"unmapped_playstyles={checks['unmapped_playstyles']}")
    if checks["unmapped_roles"]:
        errors.append(f"unmapped_roles={checks['unmapped_roles']}")
    if expected_cards is not None and counts["cards"] != expected_cards:
        errors.append(f"cards={counts['cards']} expected={expected_cards}")
    if expected_players is not None and counts["players"] != expected_players:
        errors.append(f"players={counts['players']} expected={expected_players}")

    return {
        "ok": not errors,
        "path": display_path,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "metadata": metadata,
        "counts": counts,
        "checks": checks,
        "errors": errors,
    }


def main():
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Validate a normalized FC27 catalog.")
    parser.add_argument(
        "path", nargs="?", default=project_root / "data" / "catalog.sqlite", type=Path
    )
    parser.add_argument("--expected-cards", type=int)
    parser.add_argument("--expected-players", type=int)
    parser.add_argument("--write-manifest", type=Path)
    args = parser.parse_args()
    result = validate_catalog(args.path, args.expected_cards, args.expected_players)
    serialized = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.write_manifest:
        args.write_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.write_manifest.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
