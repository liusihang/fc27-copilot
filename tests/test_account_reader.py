import unittest

from fc27.account import AccountReader
from fc27.errors import FC27Error


def item(item_id):
    return {"id": item_id, "definitionId": 1000 + item_id, "untradeable": item_id % 2 == 0}


class FixtureBridge:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        response = self.responses[method]
        if callable(response):
            response = response(params)
        return {"ok": True, "data": response}


class AccountReaderTest(unittest.TestCase):
    def test_paginates_until_explicit_completion(self):
        def club_page(params):
            if params["start"] == 0:
                return {"itemData": [item(value) for value in range(1, 101)], "retrievedAll": False}
            return {"itemData": [item(value) for value in range(1, 103)], "retrievedAll": True}

        bridge = FixtureBridge({"getClubPage": club_page})
        result = AccountReader(bridge, page_delay_seconds=0).read(["club"])["club"]
        self.assertTrue(result["complete"])
        self.assertEqual(result["page_count"], 2)
        self.assertEqual(result["item_count"], 102)
        self.assertEqual(bridge.calls[1][1]["start"], 100)

    def test_storage_uses_end_of_list(self):
        bridge = FixtureBridge(
            {"getStoragePage": {"items": [item(1), item(2)], "end_of_list": True}}
        )
        result = AccountReader(bridge, page_delay_seconds=0).read(["storage"])["storage"]
        self.assertEqual(result["item_count"], 2)
        self.assertEqual(result["items"][0]["location"], "storage")

    def test_tradepile_extracts_items_and_listings(self):
        bridge = FixtureBridge(
            {
                "getTradepile": {
                    "auctionInfo": [
                        {"tradeId": 77, "itemData": item(1), "startingBid": 1000, "buyNowPrice": 1200, "tradeState": "active"}
                    ]
                }
            }
        )
        result = AccountReader(bridge, page_delay_seconds=0).read(["tradepile"])["tradepile"]
        self.assertEqual(result["items"][0]["item_id"], 1)
        self.assertEqual(result["listings"][0]["trade_id"], 77)

    def test_non_player_tradepile_items_are_excluded(self):
        player = {**item(1), "item_type": "player"}
        vanity = {**item(2), "item_type": "vanity"}
        bridge = FixtureBridge(
            {
                "getTradepile": {
                    "auctionInfo": [
                        {"tradeId": 77, "itemData": player},
                        {"tradeId": 78, "itemData": vanity},
                    ]
                }
            }
        )
        result = AccountReader(bridge).read(["tradepile"])["tradepile"]
        self.assertEqual([row["item_id"] for row in result["items"]], [1])
        self.assertEqual([row["trade_id"] for row in result["listings"]], [77])

    def test_zero_trade_id_is_not_a_listing(self):
        bridge = FixtureBridge(
            {
                "getTradepile": {
                    "auctionInfo": [{"tradeId": "0", "itemData": {**item(1), "item_type": "player"}}]
                }
            }
        )
        result = AccountReader(bridge).read(["tradepile"])["tradepile"]
        self.assertEqual(result["item_count"], 1)
        self.assertEqual(result["listings"], [])

    def test_non_integer_trade_id_is_rejected(self):
        bridge = FixtureBridge(
            {
                "getTradepile": {
                    "auctionInfo": [{"tradeId": "invalid", "itemData": item(1)}]
                }
            }
        )
        with self.assertRaises(FC27Error) as context:
            AccountReader(bridge).read(["tradepile"])
        self.assertEqual(context.exception.code, "EA_TRADE_SCHEMA_INVALID")

    def test_repeated_page_is_rejected(self):
        bridge = FixtureBridge(
            {"getClubPage": {"itemData": [item(value) for value in range(1, 101)], "retrievedAll": False}}
        )
        with self.assertRaises(FC27Error) as context:
            AccountReader(bridge, page_delay_seconds=0).read(["club"])
        self.assertEqual(context.exception.code, "PAGINATION_REPEATED_PAGE")

    def test_missing_definition_id_is_rejected(self):
        bridge = FixtureBridge({"getUnassigned": {"itemData": [{"id": 1}]}})
        with self.assertRaises(FC27Error) as context:
            AccountReader(bridge, page_delay_seconds=0).read(["unassigned"])
        self.assertEqual(context.exception.code, "EA_ITEM_SCHEMA_INVALID")


if __name__ == "__main__":
    unittest.main()
