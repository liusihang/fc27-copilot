import time

from .errors import FC27Error


class ActionDispatcher:
    def __init__(self, bridge, runtime, sync_full):
        self.bridge = bridge
        self.runtime = runtime
        self.sync_full = sync_full

    def __call__(self, action):
        method = getattr(self, f"_{action['type']}", None)
        if method is None:
            raise FC27Error(
                "ACTION_TYPE_NOT_READY",
                f"Execution handler is not implemented for {action['type']}.",
                recovery="Keep this action type disabled in policy.json.",
            )
        return method(action)

    def _buy_now(self, action):
        before_items = {
            row["item_id"]
            for row in self.runtime.query_items(
                {"card_ea_ids": [action["expected_card_ea_id"]], "limit": 100}
            )["items"]
        }
        before_coins = self.runtime.account_summary()["coin_balance"]
        self._call(
            "buyNow",
            {
                "trade_id": action["trade_id"],
                "definition_id": action["expected_card_ea_id"],
                "buy_now_price": action["max_price"],
            },
        )
        sync = self.sync_full("post_action")
        after_items = self.runtime.query_items(
            {"card_ea_ids": [action["expected_card_ea_id"]], "limit": 100}
        )["items"]
        acquired = [row for row in after_items if row["item_id"] not in before_items]
        if len(acquired) != 1:
            raise FC27Error(
                "PURCHASE_READBACK_FAILED",
                "Buy Now completed but readback did not identify exactly one acquired item.",
                recovery="Do not retry the trade. Inspect coins, Unassigned, and the latest sync first.",
                details={"new_item_ids": [row["item_id"] for row in acquired]},
            )
        after_coins = self.runtime.account_summary()["coin_balance"]
        coin_delta = after_coins - before_coins
        if coin_delta >= 0:
            raise FC27Error(
                "PURCHASE_READBACK_FAILED",
                "The acquired item was found but the observed coin balance did not decrease.",
                recovery="Do not retry. Inspect the account and latest sync before another action.",
                details={"before_coins": before_coins, "after_coins": after_coins},
            )
        item = acquired[0]
        self.runtime.record_coin_transaction(
            transaction_id=f"purchase:{action['action_id']}",
            action_id=action["action_id"],
            item_id=item["item_id"],
            card_ea_id=action["expected_card_ea_id"],
            kind="purchase",
            price=-coin_delta,
            tax=0,
            coin_delta=coin_delta,
        )
        return {
            "trade_id": action["trade_id"],
            "item": item,
            "coins_before": before_coins,
            "coins_after": after_coins,
            "coin_delta": coin_delta,
            "sync": sync,
        }

    def _place_bid(self, action):
        before_coins = self.runtime.account_summary()["coin_balance"]
        self._call(
            "placeBid",
            {
                "trade_id": action["trade_id"],
                "definition_id": action["expected_card_ea_id"],
                "bid": action["bid"],
            },
        )
        sync = self.sync_full("post_action")
        watchlist = self._call("getWatchlist", {})
        listing = next(
            (
                row
                for row in watchlist.get("auctionInfo") or []
                if int(row.get("tradeId") or 0) == action["trade_id"]
            ),
            None,
        )
        if listing is None:
            raise FC27Error(
                "BID_READBACK_FAILED",
                "Bid response completed but the trade was not found in Watchlist readback.",
                recovery="Do not repeat the bid until the current trade state is inspected.",
            )
        after_coins = self.runtime.account_summary()["coin_balance"]
        return {
            "trade_id": action["trade_id"],
            "bid": action["bid"],
            "coins_before": before_coins,
            "coins_after": after_coins,
            "listing": listing,
            "sync": sync,
        }

    def _move_item(self, action):
        method = "sendToTradepile" if action["destination"] == "tradepile" else "sendToClub"
        self._call(method, {"item_id": action["item_id"]})
        sync = self.sync_full("post_action")
        item = self.runtime.current_item(action["item_id"])
        if item is None or item["location"] != action["destination"]:
            raise FC27Error(
                "ITEM_READBACK_FAILED",
                f"Item {action['item_id']} was not observed in {action['destination']} after the move.",
                recovery="Do not repeat the move until the latest club state is inspected.",
            )
        return {"item": item, "sync": sync}

    def _list_item(self, action):
        action_response = self._call(
            "listOnMarket",
            {
                "item_id": action["item_id"],
                "starting_bid": action["starting_bid"],
                "buy_now_price": action["buy_now_price"],
                "duration": action["duration"],
            },
        )
        observed_listing = None
        for attempt in range(6):
            tradepile = self._call("getTradepile", {})
            observed_listing = next(
                (
                    row
                    for row in tradepile.get("auctionInfo") or []
                    if row.get("itemData", {}).get("item_id") == action["item_id"]
                    and int(row.get("tradeId") or 0) > 0
                    and int(row.get("startingBid") or 0) == action["starting_bid"]
                    and int(row.get("buyNowPrice") or 0) == action["buy_now_price"]
                ),
                None,
            )
            if observed_listing is not None:
                break
            if attempt < 5:
                time.sleep(2)
        sync = self.sync_full("post_action")
        listing = self.runtime.listing_for_item(
            action["item_id"],
            starting_bid=action["starting_bid"],
            buy_now_price=action["buy_now_price"],
        )
        if listing is None:
            raise FC27Error(
                "LISTING_READBACK_FAILED",
                f"Item {action['item_id']} has no listing after readback.",
                recovery="Do not repeat the listing until Tradepile is inspected.",
                details={
                    "action_response": action_response,
                    "observed_listing": observed_listing,
                },
            )
        return {"listing": listing, "sync": sync}

    def _relist_all(self, action):
        self._call("relistAll", {})
        return {"sync": self.sync_full("post_action")}

    def _clear_sold(self, action):
        self._call("clearSold", {})
        return {"sync": self.sync_full("post_action")}

    def _call(self, method, params):
        response = self.bridge.call(method, params)
        if not response.get("ok", False):
            error = response.get("error") or {}
            raise FC27Error(
                error.get("code") or "EA_ACTION_FAILED",
                error.get("message") or f"EA action {method} failed.",
                retryable=False,
                recovery="Inspect the Web App and current account state before any retry.",
                details=error,
            )
        return response.get("data") or {}
