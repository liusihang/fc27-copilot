import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

from .errors import FC27Error


R2_BASE = "https://r2.fut.gg/27"
USER_AGENT = "Mozilla/5.0 (compatible; FC27-Copilot/0.4)"
PLATFORMS = ("ps5", "pc")
STATUS_NAMES = {
    0: "market_or_normal",
    1: "sbc",
    2: "objective",
    4: "token_or_special",
}


def utc_now():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def iso_before(hours):
    return (
        (datetime.now(timezone.utc) - timedelta(hours=hours))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


class FutggPriceClient:
    def __init__(self, base_url=R2_BASE, fetch_json=None):
        self.base_url = base_url.rstrip("/")
        self.fetch_json = fetch_json or self._fetch_json

    def current_prices(self, card_ea_ids):
        card_ea_ids = sorted({int(value) for value in card_ea_ids})
        manifest = self.fetch_json(f"{self.base_url}/manifest.json")
        version = manifest.get("_version", 1)
        names = {
            "index": "player-prices-index",
            "ps5": "player-prices-ps5-dyn",
            "pc": "player-prices-pc-dyn",
        }
        missing_manifest_keys = [name for name in names.values() if not manifest.get(name)]
        if missing_manifest_keys:
            raise FC27Error(
                "FUTGG_PRICE_SCHEMA_INVALID",
                "FUT.GG manifest is missing required price blobs.",
                recovery="Inspect the current FC27 FUT.GG manifest before retrying.",
                details={"missing_keys": missing_manifest_keys},
            )

        blobs = {
            key: self.fetch_json(
                f"{self.base_url}/{name}.v{version}.{manifest[name]}.json"
            )
            for key, name in names.items()
        }
        price_index = self._build_index(blobs["index"])
        expected_slots = len(price_index)
        for platform in PLATFORMS:
            prices = blobs[platform].get("p")
            statuses = blobs[platform].get("s")
            if (
                not isinstance(prices, list)
                or not isinstance(statuses, list)
                or len(prices) != expected_slots
                or len(statuses) != expected_slots
            ):
                raise FC27Error(
                    "FUTGG_PRICE_SCHEMA_INVALID",
                    f"FUT.GG {platform} price blob does not align with the price index.",
                    recovery="Keep existing price history and inspect the current FUT.GG blob schema.",
                    details={
                        "platform": platform,
                        "index_slots": expected_slots,
                        "price_slots": len(prices) if isinstance(prices, list) else None,
                        "status_slots": len(statuses) if isinstance(statuses, list) else None,
                    },
                )

        observed_at = utc_now()
        rows = []
        missing_card_ea_ids = []
        for card_ea_id in card_ea_ids:
            position = price_index.get(card_ea_id)
            if position is None:
                missing_card_ea_ids.append(card_ea_id)
                continue
            token_cost = self._dict_lookup(blobs["index"].get("tkn"), card_ea_id)
            for platform in PLATFORMS:
                raw_price = blobs[platform]["p"][position]
                status_code = int(blobs[platform]["s"][position])
                price = int(raw_price) if raw_price else None
                if status_code == 1 or (status_code == 4 and token_cost is None):
                    price = None
                rows.append(
                    {
                        "card_ea_id": card_ea_id,
                        "platform": platform,
                        "observed_at": observed_at,
                        "price": price,
                        "status_code": status_code,
                        "status": STATUS_NAMES.get(status_code, f"unknown_{status_code}"),
                        "is_extinct": status_code == 0 and price is None,
                    }
                )

        return {
            "observed_at": observed_at,
            "manifest_version": version,
            "hashes": {
                "index": manifest[names["index"]],
                "ps5": manifest[names["ps5"]],
                "pc": manifest[names["pc"]],
            },
            "price_index_count": expected_slots,
            "prices": rows,
            "missing_card_ea_ids": missing_card_ea_ids,
        }

    @staticmethod
    def _build_index(blob):
        if blob.get("id0") is None or not isinstance(blob.get("d"), list):
            raise FC27Error(
                "FUTGG_PRICE_SCHEMA_INVALID",
                "FUT.GG price index must contain id0 and delta list d.",
                recovery="Inspect the current FC27 FUT.GG price-index blob.",
            )
        current = int(blob["id0"])
        index = {current: 0}
        for position, delta in enumerate(blob["d"], start=1):
            current += int(delta)
            index[current] = position
        return index

    @staticmethod
    def _dict_lookup(value, key):
        if not isinstance(value, dict):
            return None
        return value.get(str(key), value.get(key))

    @staticmethod
    def _fetch_json(url):
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Referer": "https://www.fut.gg/",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:
            raise FC27Error(
                "FUTGG_REQUEST_FAILED",
                "Could not read current FUT.GG reference prices.",
                retryable=True,
                recovery="Check FUT.GG connectivity and the local proxy route before retrying.",
                details={"url": url, "exception": type(error).__name__, "message": str(error)},
            ) from error


class MarketService:
    def __init__(self, runtime, client=None):
        self.runtime = runtime
        self.client = client or FutggPriceClient()

    def price_context(self, card_ea_ids, history_hours=72):
        card_ea_ids = sorted({int(value) for value in card_ea_ids})
        if not card_ea_ids:
            raise FC27Error("INVALID_PRICE_CONTEXT", "card_ea_ids cannot be empty.")
        if len(card_ea_ids) > 100:
            raise FC27Error(
                "TOO_MANY_CARD_IDS",
                "price_context accepts at most 100 card IDs per call.",
                recovery="Split the request into batches of at most 100 card IDs.",
            )
        history_hours = max(1, min(int(history_hours), 2160))
        snapshot = self.client.current_prices(card_ea_ids)
        persistence = self.runtime.record_reference_prices(snapshot["prices"])
        facts = self.runtime.price_facts(card_ea_ids, iso_before(history_hours))
        live_prices = {
            (row["card_ea_id"], row["platform"]): row
            for row in snapshot["prices"]
        }

        cards = []
        for card_ea_id in card_ea_ids:
            holdings = facts["holdings"].get(card_ea_id, [])
            cost_counts = Counter(
                item["acquisition_cost"]
                for item in holdings
                if item["acquisition_cost"] is not None
            )
            current = {
                platform: live_prices.get((card_ea_id, platform))
                for platform in PLATFORMS
            }
            cards.append(
                {
                    "card_ea_id": card_ea_id,
                    "current": current,
                    "history": facts["history"].get(card_ea_id, []),
                    "holdings": {
                        "count": len(holdings),
                        "tradeable_count": sum(item["tradeable"] for item in holdings),
                        "tradepile_count": sum(
                            item["location"] == "tradepile" for item in holdings
                        ),
                        "active_listing_count": facts["active_listing_counts"].get(
                            card_ea_id, 0
                        ),
                        "recent_items": holdings[:20],
                        "acquisition_costs": [
                            {"cost": cost, "quantity": quantity}
                            for cost, quantity in sorted(cost_counts.items())
                        ],
                    },
                    "net_at_reference_price": [
                        {
                            "acquisition_cost": cost,
                            "quantity": quantity,
                            "platforms": {
                                platform: self._net_result(
                                    current[platform]["price"]
                                    if current[platform]
                                    else None,
                                    cost,
                                )
                                for platform in PLATFORMS
                            },
                        }
                        for cost, quantity in sorted(cost_counts.items())
                    ],
                }
            )

        return {
            "source": {
                "name": "FUT.GG",
                "observed_at": snapshot["observed_at"],
                "manifest_version": snapshot["manifest_version"],
                "hashes": snapshot["hashes"],
                "price_index_count": snapshot["price_index_count"],
            },
            "history_hours": history_hours,
            "tax_rate": 0.05,
            "persisted_changes": persistence,
            "missing_card_ea_ids": snapshot["missing_card_ea_ids"],
            "cards": cards,
        }

    @staticmethod
    def _net_result(gross_sale_price, acquisition_cost):
        if gross_sale_price is None:
            return None
        tax = gross_sale_price // 20
        net_sale_proceeds = gross_sale_price - tax
        return {
            "gross_sale_price": gross_sale_price,
            "tax": tax,
            "net_sale_proceeds": net_sale_proceeds,
            "net_profit": net_sale_proceeds - acquisition_cost,
        }
