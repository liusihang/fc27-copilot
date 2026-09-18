import sqlite3
import unittest

from fc27.schema import CATALOG_SCHEMA


class CatalogSchemaTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(CATALOG_SCHEMA)
        self.connection.execute("INSERT INTO nations VALUES (1, 'Nation')")
        self.connection.execute("INSERT INTO leagues VALUES (1, 'League')")
        self.connection.execute("INSERT INTO clubs VALUES (1, 'Club')")
        self.connection.execute("INSERT INTO positions VALUES (25, 'ST')")
        self.connection.execute("INSERT INTO positions VALUES (27, 'LW')")
        self.connection.execute(
            """INSERT INTO players(
                 base_player_ea_id, slug, common_name, nation_id
               ) VALUES (100, 'player', 'Player', 1)"""
        )

    def tearDown(self):
        self.connection.close()

    def test_rarity_id_is_scoped_by_quality(self):
        self.connection.execute(
            "INSERT INTO rarities VALUES (718, 'GOLD', 0, 'Rare')"
        )
        self.connection.execute(
            "INSERT INTO rarities VALUES (718, 'SILVER', 0, 'Rare')"
        )
        count = self.connection.execute(
            "SELECT COUNT(*) FROM rarities WHERE rarity_id = 718"
        ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_only_one_primary_position_is_allowed_per_card(self):
        self.connection.execute(
            "INSERT INTO rarities VALUES (718, 'GOLD', 0, 'Rare')"
        )
        self.connection.execute(
            """INSERT INTO cards(
                 card_ea_id, futgg_id, base_player_ea_id, overall, quality,
                 rarity_id, club_id, league_id
               ) VALUES (200, 300, 100, 90, 'GOLD', 718, 1, 1)"""
        )
        self.connection.execute(
            "INSERT INTO card_positions VALUES (200, 25, 1)"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO card_positions VALUES (200, 27, 1)"
            )


if __name__ == "__main__":
    unittest.main()
