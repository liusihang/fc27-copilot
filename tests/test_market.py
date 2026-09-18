import tempfile
import unittest
from pathlib import Path

from fc27.errors import FC27Error
from fc27.market import FutggPriceClient, MarketService, utc_now
from fc27.runtime import RuntimeManager


class FutggPriceClientTest(unittest.TestCase):
    def test_decodes_delta_index_and_platform_statuses(self):
        base = "https://prices.example/27"
        payloads = {
            f"{base}/manifest.json": {
                "_version": 1,
                "player-prices-index": "index",
                "player-prices-ps5-dyn": "ps5",
                "player-prices-pc-dyn": "pc",
            },
            f"{base}/player-prices-index.v1.index.json": {
                "id0": 100,
                "d": [100],
                "tkn": {},
            },
            f"{base}/player-prices-ps5-dyn.v1.ps5.json": {
                "p": [1000, 5000],
                "s": [0, 1],
            },
            f"{base}/player-prices-pc-dyn.v1.pc.json": {
                "p": [1200, 0],
                "s": [0, 0],
            },
        }
        client = FutggPriceClient(base, payloads.__getitem__)
        result = client.current_prices([100, 200, 300])
        by_key = {
            (row["card_ea_id"], row["platform"]): row for row in result["prices"]
        }
        self.assertEqual(result["price_index_count"], 2)
        self.assertEqual(result["missing_card_ea_ids"], [300])
        self.assertEqual(by_key[(100, "ps5")]["price"], 1000)
        self.assertEqual(by_key[(100, "pc")]["price"], 1200)
        self.assertIsNone(by_key[(200, "ps5")]["price"])
        self.assertEqual(by_key[(200, "ps5")]["status"], "sbc")
        self.assertTrue(by_key[(200, "pc")]["is_extinct"])

    def test_rejects_price_blob_with_wrong_slot_count(self):
        base = "https://prices.example/27"
        payloads = {
            f"{base}/manifest.json": {
                "_version": 1,
                "player-prices-index": "index",
                "player-prices-ps5-dyn": "ps5",
                "player-prices-pc-dyn": "pc",
            },
            f"{base}/player-prices-index.v1.index.json": {"id0": 100, "d": [100]},
            f"{base}/player-prices-ps5-dyn.v1.ps5.json": {"p": [1000], "s": [0]},
            f"{base}/player-prices-pc-dyn.v1.pc.json": {
                "p": [1200, 1300],
                "s": [0, 0],
            },
        }
        with self.assertRaises(FC27Error) as context:
            FutggPriceClient(base, payloads.__getitem__).current_prices([100])
        self.assertEqual(context.exception.code, "FUTGG_PRICE_SCHEMA_INVALID")


class FakePriceClient:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)

    def current_prices(self, card_ea_ids):
        return self.snapshots.pop(0)


def snapshot(ps5_price, pc_price, observed_at):
    return {
        "observed_at": observed_at,
        "manifest_version": 1,
        "hashes": {"index": "index", "ps5": "ps5", "pc": "pc"},
        "price_index_count": 1,
        "prices": [
            {
                "card_ea_id": 100,
                "platform": "ps5",
                "observed_at": observed_at,
                "price": ps5_price,
                "status_code": 0,
                "status": "market_or_normal",
                "is_extinct": False,
            },
            {
                "card_ea_id": 100,
                "platform": "pc",
                "observed_at": observed_at,
                "price": pc_price,
                "status_code": 0,
                "status": "market_or_normal",
                "is_extinct": False,
            },
        ],
        "missing_card_ea_ids": [],
    }


class MarketServiceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        manager = RuntimeManager(Path(self.directory.name) / "accounts")
        manager.activate({"persona_id": "123", "platform": "pc", "club_name": "Club"})
        self.runtime = manager.active
        observed_at = utc_now()
        with self.runtime.connect() as connection:
            connection.execute(
                """INSERT INTO sync_runs(sync_id, kind, started_at, finished_at, status)
                   VALUES (1, 'fixture', ?, ?, 'complete')""",
                (observed_at, observed_at),
            )
            connection.executemany(
                """INSERT INTO club_items(
                     item_id, card_ea_id, location, tradeable,
                     acquisition_cost, first_seen_at, last_seen_at, last_sync_id
                   ) VALUES (?, 100, ?, 1, ?, ?, ?, 1)""",
                [
                    (1, "tradepile", 700, observed_at, observed_at),
                    (2, "club", 750, observed_at, observed_at),
                ],
            )
            connection.executemany(
                """INSERT INTO trade_listings(
                     trade_id, item_id, status, last_seen_at
                   ) VALUES (?, ?, ?, ?)""",
                [(11, 1, "active", observed_at), (12, 2, "expired", observed_at)],
            )
            connection.commit()

    def tearDown(self):
        self.directory.cleanup()

    def test_persists_only_changes_and_returns_holdings_tax_and_profit(self):
        client = FakePriceClient(
            [
                snapshot(1000, 1200, "2026-09-18T12:00:00.000Z"),
                snapshot(1000, 1200, "2026-09-18T12:01:00.000Z"),
                snapshot(1100, 1200, "2026-09-18T12:02:00.000Z"),
            ]
        )
        service = MarketService(self.runtime, client)

        first = service.price_context([100], 72)
        self.assertEqual(first["persisted_changes"], {"inserted": 2, "unchanged": 0})
        card = first["cards"][0]
        self.assertEqual(card["holdings"]["count"], 2)
        self.assertEqual(card["holdings"]["tradepile_count"], 1)
        self.assertEqual(card["holdings"]["active_listing_count"], 1)
        first_cost = card["net_at_reference_price"][0]
        self.assertEqual(first_cost["acquisition_cost"], 700)
        self.assertEqual(first_cost["platforms"]["ps5"]["tax"], 50)
        self.assertEqual(first_cost["platforms"]["ps5"]["net_profit"], 250)

        second = service.price_context([100], 72)
        self.assertEqual(second["persisted_changes"], {"inserted": 0, "unchanged": 2})

        third = service.price_context([100], 72)
        self.assertEqual(third["persisted_changes"], {"inserted": 1, "unchanged": 1})
        self.assertEqual(len(third["cards"][0]["history"]), 3)


if __name__ == "__main__":
    unittest.main()
