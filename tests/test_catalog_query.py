import sqlite3
import tempfile
import unittest
from pathlib import Path

from fc27.catalog import CatalogDB
from fc27.errors import FC27Error
from fc27.schema import CATALOG_SCHEMA


class CatalogQueryTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "catalog.sqlite"
        connection = sqlite3.connect(self.path)
        connection.executescript(CATALOG_SCHEMA)
        connection.execute("INSERT INTO nations VALUES (1, 'France')")
        connection.execute("INSERT INTO leagues VALUES (10, 'League')")
        connection.execute("INSERT INTO clubs VALUES (20, 'Club')")
        connection.executemany(
            "INSERT INTO positions VALUES (?, ?)", [(25, "ST"), (27, "LW")]
        )
        connection.execute(
            "INSERT INTO rarities VALUES (718, 'GOLD', 0, 'Rare')"
        )
        connection.execute(
            """INSERT INTO players(
                 base_player_ea_id, slug, common_name, first_name, last_name,
                 gender, nation_id, foot, height_cm, age
               ) VALUES (100, 'kylian-mbappe', 'Kylian Mbappé', 'Kylian',
                         'Mbappé', 0, 1, 'Right', 178, 27)"""
        )
        connection.execute(
            """INSERT INTO cards(
                 card_ea_id, futgg_id, base_player_ea_id, slug, overall,
                 quality, rarity_id, club_id, league_id, weak_foot,
                 skill_moves, pace, shooting, passing, dribbling,
                 defending, physicality, is_special
               ) VALUES (200, 300, 100, 'kylian-mbappe', 91, 'GOLD', 718,
                         20, 10, 4, 5, 97, 90, 82, 93, 40, 80, 1)"""
        )
        connection.executemany(
            "INSERT INTO card_positions VALUES (?, ?, ?)",
            [(200, 25, 1), (200, 27, 0)],
        )
        connection.execute(
            """INSERT INTO playstyles(
                 playstyle_ea_id, futgg_id, name
               ) VALUES (1000, 1, 'Quick Step')"""
        )
        connection.execute(
            "INSERT INTO card_playstyles VALUES (200, 1000, 2)"
        )
        connection.execute(
            """INSERT INTO roles(
                 role_id, name, slug, position_id, plus_ea_id, plus_plus_ea_id
               ) VALUES (50, 'Advanced Forward', 'st-advanced-forward', 25, 500, 501)"""
        )
        connection.execute("INSERT INTO card_roles VALUES (200, 50, 2)")
        connection.execute(
            "INSERT INTO catalog_meta VALUES ('snapshot_finished_at', '2026-09-17T17:06:37Z')"
        )
        connection.commit()
        connection.close()
        self.catalog = CatalogDB(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def test_name_search_is_accent_insensitive(self):
        result = self.catalog.query({"text": "Mbappe"})
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["cards"][0]["common_name"], "Kylian Mbappé")

    def test_structured_relations_filter_and_render(self):
        result = self.catalog.query(
            {
                "filters": {
                    "positions": ["LW"],
                    "playstyles_plus": ["Quick Step+"],
                    "roles_plusplus": ["ST Advanced Forward"],
                    "attributes": {"pace_min": 95},
                }
            }
        )
        self.assertEqual(result["count"], 1)
        card = result["cards"][0]
        self.assertEqual(card["primary_position"], "ST")
        self.assertEqual(card["alternative_positions"], ["LW"])
        self.assertEqual(card["playstyles"]["plus"], ["Quick Step"])
        self.assertEqual(card["roles"]["plusplus"], ["ST Advanced Forward"])

    def test_exact_lookup_reports_missing_ids(self):
        result = self.catalog.query({"card_ea_ids": [200, 999]})
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["missing_card_ea_ids"], [999])

    def test_invalid_sort_is_actionable(self):
        with self.assertRaises(FC27Error) as context:
            self.catalog.query({"sort": "price_desc"})
        self.assertEqual(context.exception.code, "INVALID_SORT")
        self.assertIn("overall_desc", context.exception.recovery)


if __name__ == "__main__":
    unittest.main()
