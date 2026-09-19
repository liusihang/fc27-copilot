import time

from .errors import FC27Error
from .sbc import SbcService


SBC_TIMEOUT_CODES = {
    "BRIDGE_TIMEOUT",
    "PAGE_BRIDGE_TIMEOUT",
    "EA_SERVICE_TIMEOUT",
}


class ActionDispatcher:
    def __init__(self, bridge, runtime, sync_full, catalog=None):
        self.bridge = bridge
        self.runtime = runtime
        self.sync_full = sync_full
        self.catalog = catalog

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

    def _save_sbc_squad(self, action):
        service = self._sbc_service()
        expected_sync_id = action["_expected_sync_id"]
        validation = service.validate_solution(
            action["solution_id"], expected_sync_id
        )
        slot_indices = [
            int(value["slot_index"])
            for value in validation["solution"]["slots"]
        ]
        try:
            save_response = self._call(
                "saveSbcSquad",
                {
                    "set_id": action["set_id"],
                    "challenge_id": action["challenge_id"],
                    "item_ids": action["item_ids"],
                    "slot_indices": slot_indices,
                },
            )
        except FC27Error as error:
            if error.code not in SBC_TIMEOUT_CODES:
                raise
            raise FC27Error(
                "SBC_SAVE_OUTCOME_UNKNOWN",
                "The SBC save request timed out before its outcome could be confirmed.",
                recovery="Do not save again. Reconcile this action_id through a fresh read-only EA request.",
                details={"save_error": error.as_dict()},
            ) from error
        try:
            response = self._call(
                "readSavedSbcSquad",
                {
                    "set_id": action["set_id"],
                    "challenge_id": action["challenge_id"],
                    "slot_indices": slot_indices,
                },
            )
            saved_item_ids = [int(value) for value in response.get("saved_item_ids") or []]
            if saved_item_ids != action["item_ids"]:
                raise FC27Error(
                    "SBC_SAVE_READBACK_FAILED",
                    "Fresh EA SBC readback did not preserve the exact confirmed item order.",
                    details={"saved_item_ids": saved_item_ids},
                )
            saved_slot_indices = [
                int(value) for value in response.get("saved_slot_indices") or []
            ]
            if saved_slot_indices != slot_indices:
                raise FC27Error(
                    "SBC_SAVE_READBACK_FAILED",
                    "Fresh EA SBC readback did not preserve the exact fillable slot layout.",
                    details={
                        "saved_slot_indices": saved_slot_indices,
                        "expected_slot_indices": slot_indices,
                    },
                )
            eligibility = response.get("squad", {}).get("eligibility_evidence") or {}
            freshness = response.get("freshness") or {}
            if (
                response.get("source") != "ea_webapp_fresh"
                or freshness.get("sets_requested") is not True
                or freshness.get("challenges_requested") is not True
                or freshness.get("challenge_loaded") is not True
                or response.get("squad", {}).get("eligible") is not True
                or eligibility.get("source") != "ea_challenge_requirements"
                or eligibility.get("identity_match") is not True
                or eligibility.get("all_requirements_met") is not True
                or eligibility.get("submit_available") is not True
            ):
                raise FC27Error(
                    "SBC_SAVE_READBACK_FAILED",
                    "Fresh EA SBC readback did not provide complete positive eligibility evidence.",
                    details={"eligibility_evidence": eligibility, "freshness": freshness},
                )
        except FC27Error as error:
            raise FC27Error(
                "SBC_SAVE_READBACK_PENDING",
                "EA acknowledged the SBC save, but fresh readback did not complete successfully.",
                recovery="Do not save again. Reconcile this action_id through a fresh read-only EA request.",
                details={"save": save_response, "readback_error": error.as_dict()},
            ) from error
        captured = service.capture_challenge(response)
        evidence = {
            "saved_at_sync_id": expected_sync_id,
            "saved_item_ids": saved_item_ids,
            "saved_slot_indices": saved_slot_indices,
            "ea_eligible": True,
            "reconciled": False,
            "source": "ea_webapp_fresh",
            "save": save_response,
            "ea": response,
        }
        self.runtime.mark_sbc_solution_saved(
            action["solution_id"], expected_sync_id, evidence
        )
        return {"validation": validation["validation"], "capture": captured, **evidence}

    def _submit_sbc(self, action):
        service = self._sbc_service()
        expected_sync_id = action["_expected_sync_id"]
        validation = service.validate_solution(
            action["solution_id"], expected_sync_id
        )
        slot_indices = [
            int(value["slot_index"])
            for value in validation["solution"]["slots"]
        ]
        pre_submit = self._call(
            "readSavedSbcSquad",
            {
                "set_id": action["set_id"],
                "challenge_id": action["challenge_id"],
                "slot_indices": slot_indices,
            },
        )
        self.runtime.record_sbc_submit_checkpoint(
            action["action_id"], expected_sync_id, pre_submit
        )
        try:
            response = self._call(
                "submitSbc",
                {
                    "set_id": action["set_id"],
                    "challenge_id": action["challenge_id"],
                    "item_ids": action["item_ids"],
                    "slot_indices": slot_indices,
                    "expected_counters": {
                        "challenge_times_completed": (
                            pre_submit.get("challenge") or {}
                        ).get("times_completed"),
                        "set_times_completed": (
                            pre_submit.get("set") or {}
                        ).get("times_completed"),
                        "set_completed_count": (
                            pre_submit.get("set") or {}
                        ).get("completed_count"),
                    },
                },
            )
        except FC27Error as error:
            if error.code not in SBC_TIMEOUT_CODES:
                raise
            raise FC27Error(
                "SBC_SUBMIT_OUTCOME_UNKNOWN",
                "The SBC submission request timed out before its outcome could be confirmed.",
                recovery="Do not submit again. Reconcile this action_id through fresh challenge and inventory reads.",
                details={"pre_submit": pre_submit, "submit_error": error.as_dict()},
            ) from error
        sync = None
        post_submit = None
        try:
            submitted_item_ids = [
                int(value) for value in response.get("submitted_item_ids") or []
            ]
            if submitted_item_ids != action["item_ids"]:
                raise FC27Error(
                    "SBC_SUBMIT_RESPONSE_MISMATCH",
                    "EA submit response did not preserve the exact confirmed item order.",
                    details={"submitted_item_ids": submitted_item_ids},
                )
            submitted_slot_indices = [
                int(value) for value in response.get("submitted_slot_indices") or []
            ]
            if submitted_slot_indices != slot_indices:
                raise FC27Error(
                    "SBC_SUBMIT_RESPONSE_MISMATCH",
                    "EA submit response did not preserve the exact fillable slot layout.",
                    details={
                        "submitted_slot_indices": submitted_slot_indices,
                        "expected_slot_indices": slot_indices,
                    },
                )
            sync = self.sync_full("post_sbc_submit")
            remaining = {
                row["item_id"]
                for row in self.runtime.items_by_ids(action["item_ids"])
            }
            if remaining:
                raise FC27Error(
                    "SBC_SUBMIT_READBACK_FAILED",
                    "One or more confirmed items remain after the post-submit synchronization.",
                    details={"remaining_item_ids": sorted(remaining)},
                )
            post_submit = self._call(
                "readSbcSubmissionState",
                {
                    "set_id": action["set_id"],
                    "challenge_id": action["challenge_id"],
                },
            )
            captured = service.capture_challenge(post_submit)
            evidence = {
                "submitted_at_sync_id": sync["sync_id"],
                "submitted_item_ids": action["item_ids"],
                "submitted_slot_indices": slot_indices,
                "source": "ea_webapp_fresh",
                "pre_submit": pre_submit,
                "post_submit": post_submit,
                "ea": response,
                "sync": sync,
                "challenge": captured,
            }
            self.runtime.mark_sbc_solution_submitted(
                action["solution_id"],
                expected_sync_id,
                action["set_id"],
                action["challenge_id"],
                action["item_ids"],
                evidence,
            )
        except FC27Error as error:
            raise FC27Error(
                "SBC_SUBMIT_READBACK_PENDING",
                "EA acknowledged the SBC submission, but fresh post-submit evidence is incomplete.",
                recovery="Do not submit again. Reconcile this action_id through fresh challenge and inventory reads.",
                details={
                    "pre_submit": pre_submit,
                    "submit": response,
                    "sync": sync,
                    "post_submit": post_submit,
                    "readback_error": error.as_dict(),
                },
            ) from error
        return {"validation": validation["validation"], **evidence}

    def _sbc_service(self):
        if self.catalog is None:
            raise FC27Error(
                "SBC_SERVICE_UNAVAILABLE",
                "SBC execution requires the catalog service.",
                recovery="Initialize ActionDispatcher with the active catalog.",
            )
        return SbcService(self.runtime, self.catalog)

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
