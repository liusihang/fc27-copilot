import time

from .errors import FC27Error


PAGE_SIZE = 100
MAX_PAGES = 1000


class AccountReader:
    def __init__(self, bridge, page_delay_seconds=0.25):
        self.bridge = bridge
        self.page_delay_seconds = page_delay_seconds

    def identity(self):
        return self._call("getIdentity", {})

    def read(self, areas):
        results = {}
        for area in areas:
            results[area] = self.read_area(area)
        return results

    def read_area(self, area):
        if area == "coins":
            payload = self._call("getCoinBalance", {})
            return {
                    "area": area,
                    "page_count": 1,
                    "item_count": 0,
                    "complete": True,
                    "coin_balance": self._coin_balance(payload),
                    "items": [],
                    "listings": [],
            }
        if area == "club":
            return self._read_pages(area, "getClubPage", "start")
        if area == "storage":
            return self._read_pages(area, "getStoragePage", "offset")
        if area == "unassigned":
            return self._read_single(area, "getUnassigned")
        if area == "tradepile":
            return self._read_single(area, "getTradepile")
        if area == "watchlist":
            return self._read_single(area, "getWatchlist")
        raise FC27Error(
            "INVALID_SYNC_AREA",
            f"Unsupported account sync area: {area}",
            recovery="Use coins, club, storage, unassigned, tradepile, or watchlist.",
        )

    def _read_pages(self, area, method, offset_name):
        cumulative = area == "club"
        offset = 0
        page_count = 0
        items = []
        seen_item_ids = set()
        seen_signatures = set()
        while page_count < MAX_PAGES:
            payload = self._call(method, {offset_name: offset, "count": PAGE_SIZE})
            page_count += 1
            raw_items = self._raw_items(payload)
            normalized = [
                normalized_item
                for normalized_item in (
                    self._normalize_item(item, area) for item in raw_items
                )
                if normalized_item.get("item_type") in (None, "player")
            ]
            signature = tuple(item["item_id"] for item in normalized)
            complete = self._page_complete(payload, len(normalized))
            if signature and signature in seen_signatures and not complete:
                raise FC27Error(
                    "PAGINATION_REPEATED_PAGE",
                    f"{area} returned the same item page more than once at offset {offset}.",
                    recovery="Inspect the current FC27 pagination fields before retrying full sync.",
                )
            seen_signatures.add(signature)
            page_ids = [item["item_id"] for item in normalized]
            if len(page_ids) != len(set(page_ids)):
                raise FC27Error(
                    "DUPLICATE_ITEM_IN_AREA",
                    f"One {area} response contained duplicate item IDs.",
                    recovery="Inspect the EA response before accepting the sync as complete.",
                )
            if cumulative:
                current_ids = set(page_ids)
                if seen_item_ids and not seen_item_ids.issubset(current_ids):
                    raise FC27Error(
                        "PAGINATION_CACHE_REGRESSED",
                        f"{area} cumulative results lost previously observed items.",
                        recovery="Restart a full sync after the EA club cache stabilizes.",
                    )
                items = normalized
                seen_item_ids = current_ids
            else:
                for item in normalized:
                    if item["item_id"] in seen_item_ids:
                        raise FC27Error(
                            "DUPLICATE_ITEM_IN_AREA",
                            f"Item {item['item_id']} appeared more than once in {area}.",
                            recovery="Inspect page boundaries before accepting the sync as complete.",
                        )
                    seen_item_ids.add(item["item_id"])
                    items.append(item)
            if complete:
                return {
                    "area": area,
                    "page_count": page_count,
                    "item_count": len(items),
                    "complete": True,
                    "items": items,
                    "listings": self._listings(payload, area),
                }
            if not normalized:
                raise FC27Error(
                    "PAGINATION_EMPTY_PAGE",
                    f"{area} returned an empty page without a completion signal.",
                    recovery="Inspect the current FC27 pagination fields before retrying full sync.",
                )
            offset += PAGE_SIZE if cumulative else len(normalized)
            if self.page_delay_seconds > 0:
                time.sleep(self.page_delay_seconds)
        raise FC27Error(
            "PAGINATION_LIMIT_EXCEEDED",
            f"{area} exceeded {MAX_PAGES} pages without a completion signal.",
            recovery="Inspect the FC27 response schema and completion field.",
        )

    def _read_single(self, area, method):
        payload = self._call(method, {})
        items = [
            normalized_item
            for normalized_item in (
                self._normalize_item(item, area) for item in self._raw_items(payload)
            )
            if normalized_item.get("item_type") in (None, "player")
        ]
        ids = [item["item_id"] for item in items]
        if len(ids) != len(set(ids)):
            raise FC27Error(
                "DUPLICATE_ITEM_IN_AREA",
                f"{area} returned duplicate item IDs.",
                recovery="Inspect the raw EA response before accepting the sync.",
            )
        return {
            "area": area,
            "page_count": 1,
            "item_count": len(items),
            "complete": True,
            "items": items,
            "listings": self._listings(payload, area),
        }

    def _call(self, method, params):
        response = self.bridge.call(method, params)
        if not response.get("ok", False):
            error = response.get("error") or {}
            raise FC27Error(
                error.get("code") or "EA_REQUEST_FAILED",
                error.get("message") or f"EA method {method} failed.",
                retryable=error.get("status") not in (403, 461),
                recovery="Inspect the Web App session and response schema before retrying.",
                details=error,
            )
        return response.get("data") or {}

    @staticmethod
    def _coin_balance(payload):
        for key in ("credits", "coins", "coinBalance"):
            if payload.get(key) is not None:
                return int(payload[key])
        raise FC27Error(
            "EA_COIN_SCHEMA_INVALID",
            "Coin response did not contain credits, coins, or coinBalance.",
            recovery="Capture the current FC27 /user/credits response schema.",
        )

    @staticmethod
    def _raw_items(payload):
        for key in ("items", "itemData"):
            if isinstance(payload.get(key), list):
                return payload[key]
        auctions = payload.get("auctionInfo")
        if isinstance(auctions, list):
            return [row.get("itemData") or {} for row in auctions]
        response = payload.get("response")
        if isinstance(response, dict):
            return AccountReader._raw_items(response)
        return []

    @staticmethod
    def _page_complete(payload, item_count):
        if payload.get("retrievedAll") is not None:
            return bool(payload["retrievedAll"])
        if payload.get("end_of_list") is not None:
            return bool(payload["end_of_list"])
        if payload.get("endOfList") is not None:
            return bool(payload["endOfList"])
        return item_count < PAGE_SIZE

    @staticmethod
    def _normalize_item(item, location):
        item_id = item.get("item_id", item.get("id"))
        card_ea_id = item.get("card_ea_id", item.get("definitionId", item.get("defId")))
        if item_id is None or card_ea_id is None:
            raise FC27Error(
                "EA_ITEM_SCHEMA_INVALID",
                f"{location} item is missing item ID or card definition ID.",
                recovery="Capture the current FC27 item response and update the adapter mapping.",
                details={"keys": sorted(item)},
            )
        tradeable = item.get("tradeable")
        if tradeable is None:
            tradeable = not bool(item.get("untradeable"))
        return {
            "item_id": int(item_id),
            "card_ea_id": int(card_ea_id),
            "location": location,
            "tradeable": bool(tradeable),
            "loan_uses_remaining": item.get("loan_uses_remaining", item.get("loans")),
            "acquisition_cost": item.get("acquisition_cost", item.get("lastSalePrice")),
            "item_type": item.get("item_type", item.get("itemType")),
        }

    @staticmethod
    def _listings(payload, area):
        if area not in ("tradepile", "watchlist"):
            return []
        rows = []
        for auction in payload.get("auctionInfo") or []:
            item = auction.get("itemData") or {}
            if item.get("item_type", item.get("itemType")) not in (None, "player"):
                continue
            trade_id = auction.get("tradeId")
            if trade_id is None:
                continue
            try:
                trade_id = int(trade_id)
            except (TypeError, ValueError) as error:
                raise FC27Error(
                    "EA_TRADE_SCHEMA_INVALID",
                    "Trade listing ID is not an integer.",
                    recovery="Capture the current FC27 tradepile response and update the adapter mapping.",
                    details={"trade_id": trade_id},
                ) from error
            if trade_id <= 0:
                continue
            rows.append(
                {
                    "trade_id": trade_id,
                    "item_id": item.get("item_id", item.get("id")),
                    "starting_bid": auction.get("startingBid"),
                    "buy_now_price": auction.get("buyNowPrice"),
                    "current_bid": auction.get("currentBid"),
                    "status": auction.get("tradeState") or auction.get("bidState") or "active",
                    "expires": auction.get("expires"),
                }
            )
        return rows
