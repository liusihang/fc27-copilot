import tempfile
import unittest
from pathlib import Path

from scripts.import_catalog import import_catalog
from scripts.validate_catalog import validate_catalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "data" / "source" / "fc27-v2.sqlite"


@unittest.skipUnless(SOURCE.exists(), "supplied FC27 v2 source database is not hydrated")
class FullCatalogImportTest(unittest.TestCase):
    def test_supplied_snapshot_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "catalog.sqlite"
            result = import_catalog(SOURCE, target)
            self.assertEqual(result["cards"], 19676)
            self.assertEqual(result["players"], 19595)
            validation = validate_catalog(target, 19676, 19595)
            self.assertTrue(validation["ok"], validation["errors"])
            self.assertEqual(validation["counts"]["rarities"], 11)
            self.assertEqual(validation["counts"]["card_positions"], 44423)
            self.assertEqual(validation["counts"]["card_playstyles"], 18256)
            self.assertEqual(validation["counts"]["card_roles"], 47258)


if __name__ == "__main__":
    unittest.main()
