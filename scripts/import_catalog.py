#!/usr/bin/env python3
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fc27.schema import CATALOG_SCHEMA, CATALOG_SCHEMA_VERSION


def json_array(value):
    if value in (None, ""):
        return []
    parsed = json.loads(value) if isinstance(value, str) else value
    return parsed if isinstance(parsed, list) else []


def create_target(path):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.executescript(CATALOG_SCHEMA)
    return connection


def import_catalog(source_path, target_path):
    source_path = Path(source_path).resolve()
    target_path = Path(target_path).resolve()
    temporary_path = Path(f"{target_path}.new")
    temporary_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{temporary_path}{suffix}")
        if candidate.exists():
            candidate.unlink()

    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source_card_count = source.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    source_player_count = source.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    target = create_target(temporary_path)
    try:
        target.executemany(
            "INSERT INTO nations(nation_id, name) VALUES (?, ?)",
            source.execute(
                """SELECT nation_ea_id, MAX(nation_name)
                   FROM cards GROUP BY nation_ea_id ORDER BY nation_ea_id"""
            ),
        )
        target.executemany(
            "INSERT INTO leagues(league_id, name) VALUES (?, ?)",
            source.execute(
                """SELECT league_ea_id, MAX(league_name)
                   FROM cards GROUP BY league_ea_id ORDER BY league_ea_id"""
            ),
        )
        target.executemany(
            "INSERT INTO clubs(club_id, name) VALUES (?, ?)",
            source.execute(
                """SELECT club_ea_id, MAX(club_name)
                   FROM cards WHERE club_ea_id IS NOT NULL
                   GROUP BY club_ea_id ORDER BY club_ea_id"""
            ),
        )
        target.executemany(
            "INSERT INTO positions(position_id, code) VALUES (?, ?)",
            source.execute(
                """SELECT position_id, MAX(position)
                   FROM cards GROUP BY position_id ORDER BY position_id"""
            ),
        )
        target.executemany(
            """INSERT INTO rarities(rarity_id, quality, rarity_ea_id, name)
               VALUES (?, ?, ?, ?)""",
            source.execute(
                """SELECT rarity_id, quality, MAX(rarity_ea_id), MAX(rarity_name)
                   FROM cards GROUP BY rarity_id, quality
                   ORDER BY rarity_id, quality"""
            ),
        )

        target.executemany(
            """INSERT INTO players(
                 base_player_ea_id, slug, common_name, first_name, last_name,
                 nickname, gender, nation_id, foot, height_cm, age
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            source.execute(
                """WITH ranked AS (
                     SELECT base_player_ea_id, base_player_slug,
                            ROW_NUMBER() OVER (
                              PARTITION BY base_player_ea_id
                              ORDER BY overall DESC, futgg_id ASC
                            ) AS row_number
                     FROM cards
                   )
                   SELECT p.base_player_ea_id, r.base_player_slug,
                          COALESCE(p.common_name, p.nickname, p.last_name, CAST(p.base_player_ea_id AS TEXT)),
                          p.first_name, p.last_name, p.nickname, p.gender,
                          p.nation_ea_id, p.foot, p.height_cm, p.age
                   FROM players p
                   JOIN ranked r USING(base_player_ea_id)
                   WHERE r.row_number = 1
                   ORDER BY p.base_player_ea_id"""
            ),
        )

        target.executemany(
            """INSERT INTO playstyles(
                 playstyle_ea_id, futgg_id, name, category, who_has_it,
                 description, plus_description, image_url
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            source.execute(
                """SELECT ea_id, futgg_id, name, category, who_has_it,
                          description, plus_description, image_url
                   FROM playstyles ORDER BY ea_id"""
            ),
        )
        target.executemany(
            """INSERT INTO roles(
                 role_id, name, slug, position_id, description,
                 plus_ea_id, plus_plus_ea_id, focus_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            source.execute(
                """SELECT role_id, name, slug, position_id, description,
                          plus_ea_id, plus_plus_ea_id, focus_json
                   FROM roles ORDER BY role_id"""
            ),
        )

        card_sql = """INSERT INTO cards(
            card_ea_id, futgg_id, base_player_ea_id, slug, card_name,
            overall, quality, rarity_id, club_id, league_id,
            weak_foot, skill_moves, pace, shooting, passing, dribbling,
            defending, physicality, gk_diving, gk_handling, gk_kicking,
            gk_reflexes, gk_speed, gk_positioning, strength, accelerate_type,
            total_igs, is_icon, is_hero, is_special, is_dynamic, is_evolution,
            is_sbc, is_objective, created_at, image_url, card_image_url
        ) VALUES ({})""".format(",".join("?" for _ in range(37)))
        target.executemany(
            card_sql,
            source.execute(
                """SELECT ea_id, futgg_id, base_player_ea_id, slug, card_name,
                          overall, quality, rarity_id, club_ea_id, league_ea_id,
                          weak_foot, skill_moves, pace, shooting, passing, dribbling,
                          defending, physicality, gk_diving, gk_handling, gk_kicking,
                          gk_reflexes, gk_speed, gk_positioning, strength, accelerate_type,
                          total_igs, is_icon, is_hero, is_special, is_dynamic,
                          is_evolution, is_sbc, is_objective, created_at,
                          image_url, card_image_url
                   FROM cards ORDER BY ea_id"""
            ),
        )

        position_rows = []
        playstyle_rows = []
        role_rows = []
        plus_roles = {
            int(row[0]): int(row[1])
            for row in source.execute(
                "SELECT plus_ea_id, role_id FROM roles WHERE plus_ea_id IS NOT NULL"
            )
        }
        plusplus_roles = {
            int(row[0]): int(row[1])
            for row in source.execute(
                "SELECT plus_plus_ea_id, role_id FROM roles WHERE plus_plus_ea_id IS NOT NULL"
            )
        }
        for row in source.execute(
            """SELECT ea_id, position_id, alternative_position_ids,
                      playstyle_ids, playstyle_plus_ids,
                      roles_plus_ids, roles_plus_plus_ids
               FROM cards ORDER BY ea_id"""
        ):
            card_id = int(row[0])
            position_rows.append((card_id, int(row[1]), 1))
            for position_id in json_array(row[2]):
                position_tuple = (card_id, int(position_id), 0)
                if int(position_id) != int(row[1]):
                    position_rows.append(position_tuple)
            for playstyle_id in json_array(row[3]):
                playstyle_rows.append((card_id, int(playstyle_id), 1))
            for playstyle_id in json_array(row[4]):
                playstyle_rows.append((card_id, int(playstyle_id), 2))
            for role_ea_id in json_array(row[5]):
                role_rows.append((card_id, plus_roles[int(role_ea_id)], 1))
            for role_ea_id in json_array(row[6]):
                role_rows.append((card_id, plusplus_roles[int(role_ea_id)], 2))

        target.executemany(
            "INSERT INTO card_positions(card_ea_id, position_id, is_primary) VALUES (?, ?, ?)",
            position_rows,
        )
        target.executemany(
            """INSERT INTO card_playstyles(card_ea_id, playstyle_ea_id, tier)
               VALUES (?, ?, ?)""",
            playstyle_rows,
        )
        target.executemany(
            "INSERT INTO card_roles(card_ea_id, role_id, tier) VALUES (?, ?, ?)",
            role_rows,
        )

        source_meta = dict(source.execute("SELECT key, value FROM metadata"))
        metadata = {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "game_year": "27",
            "snapshot_started_at": source_meta.get("snapshot_started_utc", ""),
            "snapshot_finished_at": source_meta.get("snapshot_finished_utc", ""),
            "manifest_version": source_meta.get("manifest_version", ""),
            "manifest_hash": source_meta.get("manifest_hash", ""),
            "card_count": str(target.execute("SELECT COUNT(*) FROM cards").fetchone()[0]),
            "player_count": str(target.execute("SELECT COUNT(*) FROM players").fetchone()[0]),
            "source": "FUT.GG",
        }
        target.executemany(
            "INSERT INTO catalog_meta(key, value) VALUES (?, ?)", metadata.items()
        )
        target.commit()

        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = target.execute("PRAGMA foreign_key_check").fetchall()
        card_count = target.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        player_count = target.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        primary_count = target.execute(
            "SELECT COUNT(*) FROM card_positions WHERE is_primary = 1"
        ).fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"catalog integrity_check failed: {integrity}")
        if foreign_keys:
            raise RuntimeError(f"catalog foreign_key_check failed: {foreign_keys[:5]}")
        if (
            card_count != source_card_count
            or player_count != source_player_count
            or primary_count != card_count
        ):
            raise RuntimeError(
                "catalog count mismatch: "
                f"source_cards={source_card_count}, cards={card_count}, "
                f"source_players={source_player_count}, players={player_count}, "
                f"primary_positions={primary_count}"
            )
        target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        target.commit()
    finally:
        target.close()
        source.close()

    os.replace(temporary_path, target_path)
    for suffix in ("-wal", "-shm"):
        candidate = Path(f"{temporary_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    return {"cards": card_count, "players": player_count, "integrity": integrity}


def main():
    parser = argparse.ArgumentParser(description="Convert an FC27 source v2 database to catalog schema v3.")
    parser.add_argument(
        "--source",
        default=PROJECT_ROOT / "data" / "source" / "fc27-v2.sqlite",
        type=Path,
    )
    parser.add_argument(
        "--target", default=PROJECT_ROOT / "data" / "catalog.sqlite", type=Path
    )
    args = parser.parse_args()
    result = import_catalog(args.source, args.target)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
