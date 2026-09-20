import sqlite3
import re
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from .errors import FC27Error


SORTS = {
    "overall_desc": "c.overall DESC, p.common_name COLLATE NOCASE, c.card_ea_id",
    "overall_asc": "c.overall ASC, p.common_name COLLATE NOCASE, c.card_ea_id",
    "name_asc": "p.common_name COLLATE NOCASE, c.overall DESC, c.card_ea_id",
    "pace_desc": "COALESCE(c.pace, 0) DESC, c.overall DESC, c.card_ea_id",
    "shooting_desc": "COALESCE(c.shooting, 0) DESC, c.overall DESC, c.card_ea_id",
    "passing_desc": "COALESCE(c.passing, 0) DESC, c.overall DESC, c.card_ea_id",
    "dribbling_desc": "COALESCE(c.dribbling, 0) DESC, c.overall DESC, c.card_ea_id",
    "defending_desc": "COALESCE(c.defending, 0) DESC, c.overall DESC, c.card_ea_id",
    "physicality_desc": "COALESCE(c.physicality, 0) DESC, c.overall DESC, c.card_ea_id",
}


def normalize_text(value):
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    ascii_text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", ascii_text)

ATTRIBUTE_COLUMNS = {
    "pace_min": "c.pace",
    "shooting_min": "c.shooting",
    "passing_min": "c.passing",
    "dribbling_min": "c.dribbling",
    "defending_min": "c.defending",
    "physicality_min": "c.physicality",
    "weak_foot_min": "c.weak_foot",
    "skill_moves_min": "c.skill_moves",
    "height_cm_min": "p.height_cm",
    "height_cm_max": "p.height_cm",
}


class CatalogDB:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        if not self.path.exists():
            raise FC27Error(
                "CATALOG_NOT_FOUND",
                f"Catalog database does not exist: {self.path}",
                recovery="Run scripts/import_catalog.py or scripts/refresh_catalog.py.",
            )
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.create_function("normalize_text", 1, normalize_text, deterministic=True)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def metadata(self):
        with self.connect() as connection:
            return dict(connection.execute("SELECT key, value FROM catalog_meta"))

    def validate(self):
        with self.connect() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("cards", "players", "playstyles", "roles", "card_positions")
            }
            missing_primary = connection.execute(
                """SELECT COUNT(*) FROM cards c
                   WHERE NOT EXISTS (
                     SELECT 1 FROM card_positions cp
                     WHERE cp.card_ea_id = c.card_ea_id AND cp.is_primary = 1
                   )"""
            ).fetchone()[0]
            return {
                "integrity": integrity,
                "foreign_key_errors": foreign_keys,
                "counts": counts,
                "cards_missing_primary_position": missing_primary,
                "ok": integrity == "ok" and not foreign_keys and missing_primary == 0,
            }

    def sbc_item_facts(self, card_ea_ids):
        card_ea_ids = sorted({int(value) for value in card_ea_ids})
        if not card_ea_ids:
            return {}
        placeholders = ",".join("?" for _ in card_ea_ids)
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT c.card_ea_id, c.overall, LOWER(c.quality) AS quality,
                           c.club_id, c.league_id, p.nation_id
                    FROM cards c JOIN players p USING(base_player_ea_id)
                    WHERE c.card_ea_id IN ({placeholders})""",
                card_ea_ids,
            ).fetchall()
            positions = {}
            for row in connection.execute(
                f"""SELECT cp.card_ea_id, pos.code
                    FROM card_positions cp JOIN positions pos USING(position_id)
                    WHERE cp.card_ea_id IN ({placeholders})
                    ORDER BY cp.card_ea_id, cp.is_primary DESC, pos.code""",
                card_ea_ids,
            ):
                positions.setdefault(int(row[0]), []).append(row[1])
        facts = {int(row["card_ea_id"]): dict(row) for row in rows}
        for card_ea_id, value in facts.items():
            value["positions"] = positions.get(card_ea_id, [])
        return facts

    def query(self, request):
        request = request or {}
        filters = request.get("filters") or {}
        detail = request.get("detail", "summary")
        if detail not in ("summary", "detailed"):
            raise FC27Error("INVALID_DETAIL", "detail must be summary or detailed")
        limit = max(1, min(int(request.get("limit", 20)), 100))
        sort = request.get("sort", "overall_desc")
        if sort not in SORTS:
            raise FC27Error(
                "INVALID_SORT",
                f"Unsupported sort: {sort}",
                recovery=f"Use one of: {', '.join(sorted(SORTS))}.",
            )

        where = []
        params = []
        text = normalize_text(request.get("text"))
        if text:
            where.append(
                "(normalize_text(p.common_name) LIKE ? "
                "OR normalize_text(p.first_name) LIKE ? "
                "OR normalize_text(p.last_name) LIKE ? "
                "OR normalize_text(p.nickname) LIKE ? "
                "OR normalize_text(c.card_name) LIKE ?)"
            )
            params.extend([f"%{text}%"] * 5)

        card_ids = [int(value) for value in request.get("card_ea_ids") or []]
        if len(card_ids) > 100:
            raise FC27Error(
                "TOO_MANY_CARD_IDS",
                "card_ea_ids accepts at most 100 values per call.",
                recovery="Split the comparison into batches of at most 100 card IDs.",
            )
        if card_ids:
            where.append(f"c.card_ea_id IN ({','.join('?' for _ in card_ids)})")
            params.extend(card_ids)
            limit = max(limit, len(card_ids))

        base_player_ids = [int(value) for value in request.get("base_player_ea_ids") or []]
        if base_player_ids:
            where.append(
                f"c.base_player_ea_id IN ({','.join('?' for _ in base_player_ids)})"
            )
            params.extend(base_player_ids)

        overall = filters.get("overall") or {}
        if overall.get("min") is not None:
            where.append("c.overall >= ?")
            params.append(int(overall["min"]))
        if overall.get("max") is not None:
            where.append("c.overall <= ?")
            params.append(int(overall["max"]))

        positions = [str(value).upper() for value in filters.get("positions") or []]
        if positions:
            where.append(
                "EXISTS (SELECT 1 FROM card_positions cp JOIN positions pos USING(position_id) "
                f"WHERE cp.card_ea_id = c.card_ea_id AND pos.code IN ({','.join('?' for _ in positions)}))"
            )
            params.extend(positions)

        for key, value in (filters.get("attributes") or {}).items():
            if key not in ATTRIBUTE_COLUMNS:
                raise FC27Error(
                    "INVALID_ATTRIBUTE_FILTER",
                    f"Unsupported attribute filter: {key}",
                    recovery=f"Use one of: {', '.join(sorted(ATTRIBUTE_COLUMNS))}.",
                )
            operator = "<=" if key.endswith("_max") else ">="
            where.append(f"{ATTRIBUTE_COLUMNS[key]} {operator} ?")
            params.append(int(value))

        self._append_named_relation_filter(
            where, params, filters.get("playstyles_plus") or [], "playstyles", 2
        )
        self._append_named_relation_filter(
            where, params, filters.get("roles_plusplus") or [], "roles", 2
        )

        for key in ("quality", "rarity_names"):
            values = [str(value) for value in filters.get(key) or []]
            if not values:
                continue
            column = "c.quality" if key == "quality" else "r.name"
            where.append(
                f"LOWER({column}) IN ({','.join('LOWER(?)' for _ in values)})"
            )
            params.extend(values)

        for key in (
            "is_icon",
            "is_hero",
            "is_special",
            "is_dynamic",
            "is_evolution",
            "is_sbc",
            "is_objective",
        ):
            if filters.get(key) is not None:
                where.append(f"c.{key} = ?")
                params.append(1 if filters[key] else 0)

        for column, key in (
            ("c.club_id", "club_ids"),
            ("c.league_id", "league_ids"),
            ("p.nation_id", "nation_ids"),
        ):
            values = [int(value) for value in filters.get(key) or []]
            if values:
                where.append(f"{column} IN ({','.join('?' for _ in values)})")
                params.extend(values)

        sql = """
            SELECT c.*, p.common_name, p.first_name, p.last_name, p.nickname,
                   p.gender, p.nation_id, p.foot, p.height_cm, p.age,
                   n.name AS nation_name, l.name AS league_name,
                   cl.name AS club_name, r.name AS rarity_name
            FROM cards c
            JOIN players p USING(base_player_ea_id)
            JOIN nations n ON n.nation_id = p.nation_id
            JOIN leagues l ON l.league_id = c.league_id
            LEFT JOIN clubs cl ON cl.club_id = c.club_id
            JOIN rarities r ON r.rarity_id = c.rarity_id AND r.quality = c.quality
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {SORTS[sort]} LIMIT ?"
        params.append(limit)

        with self.connect() as connection:
            rows = [dict(row) for row in connection.execute(sql, params)]
            self._decorate(connection, rows, detail)
        found_ids = {row["card_ea_id"] for row in rows}
        return {
            "count": len(rows),
            "cards": rows,
            "missing_card_ea_ids": [card_id for card_id in card_ids if card_id not in found_ids],
        }

    def _append_named_relation_filter(self, where, params, names, relation, tier):
        for name in names:
            if relation == "playstyles":
                where.append(
                    "EXISTS (SELECT 1 FROM card_playstyles rel "
                    "JOIN playstyles definition USING(playstyle_ea_id) "
                    "WHERE rel.card_ea_id = c.card_ea_id AND rel.tier = ? "
                    "AND normalize_text(definition.name) = ?)"
                )
                params.extend([tier, normalize_text(name)])
            else:
                where.append(
                    "EXISTS (SELECT 1 FROM card_roles rel JOIN roles definition USING(role_id) "
                    "WHERE rel.card_ea_id = c.card_ea_id AND rel.tier = ? "
                    "AND (normalize_text(definition.name) = ? "
                    "OR normalize_text(definition.slug) = ? "
                    "OR normalize_text(UPPER(SUBSTR(definition.slug, 1, INSTR(definition.slug, '-') - 1)) "
                    "|| ' ' || definition.name) = ?))"
                )
                normalized = normalize_text(name)
                params.extend([tier, normalized, normalized, normalized])

    def _decorate(self, connection, rows, detail):
        if not rows:
            return
        card_ids = [row["card_ea_id"] for row in rows]
        placeholders = ",".join("?" for _ in card_ids)
        positions = {card_id: [] for card_id in card_ids}
        for row in connection.execute(
            f"""SELECT cp.card_ea_id, pos.code, cp.is_primary
                FROM card_positions cp JOIN positions pos USING(position_id)
                WHERE cp.card_ea_id IN ({placeholders})
                ORDER BY cp.card_ea_id, cp.is_primary DESC, pos.code""",
            card_ids,
        ):
            positions[row[0]].append({"code": row[1], "primary": bool(row[2])})

        playstyles = {card_id: {"normal": [], "plus": []} for card_id in card_ids}
        for row in connection.execute(
            f"""SELECT rel.card_ea_id, definition.name, rel.tier
                FROM card_playstyles rel JOIN playstyles definition USING(playstyle_ea_id)
                WHERE rel.card_ea_id IN ({placeholders})
                ORDER BY rel.card_ea_id, rel.tier, definition.name""",
            card_ids,
        ):
            playstyles[row[0]]["plus" if row[2] == 2 else "normal"].append(row[1])

        roles = {card_id: {"plus": [], "plusplus": []} for card_id in card_ids}
        for row in connection.execute(
            f"""SELECT rel.card_ea_id, definition.name, definition.slug, rel.tier
                FROM card_roles rel JOIN roles definition USING(role_id)
                WHERE rel.card_ea_id IN ({placeholders})
                ORDER BY rel.card_ea_id, rel.tier, definition.name""",
            card_ids,
        ):
            label = row[1]
            if row[2]:
                position = row[2].split("-", 1)[0].upper()
                label = f"{position} {label}"
            roles[row[0]]["plusplus" if row[3] == 2 else "plus"].append(label)

        summary_fields = {
            "card_ea_id", "futgg_id", "base_player_ea_id", "slug", "card_name",
            "overall", "quality", "rarity_name", "common_name", "club_id",
            "club_name", "league_id", "league_name", "nation_id", "nation_name",
            "pace", "shooting", "passing", "dribbling", "defending", "physicality",
            "weak_foot", "skill_moves", "image_url", "card_image_url",
            "is_icon", "is_hero", "is_special", "is_dynamic", "is_evolution",
            "is_sbc", "is_objective",
        }
        for row in rows:
            card_id = row["card_ea_id"]
            row["primary_position"] = next(
                (entry["code"] for entry in positions[card_id] if entry["primary"]), None
            )
            row["alternative_positions"] = [
                entry["code"] for entry in positions[card_id] if not entry["primary"]
            ]
            row["playstyles"] = playstyles[card_id]
            row["roles"] = roles[card_id]
            row["futgg_url"] = f"https://www.fut.gg/players/{row['slug']}" if row.get("slug") else None
            for key in (
                "is_icon", "is_hero", "is_special", "is_dynamic",
                "is_evolution", "is_sbc", "is_objective",
            ):
                row[key] = bool(row[key])
            if detail == "summary":
                for key in list(row):
                    if key not in summary_fields and key not in (
                        "playstyles",
                        "roles",
                        "futgg_url",
                        "primary_position",
                        "alternative_positions",
                    ):
                        row.pop(key, None)
