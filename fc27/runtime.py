import re
import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .errors import FC27Error
from .schema import RUNTIME_SCHEMA, RUNTIME_SCHEMA_VERSION


PERSONA_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
OWNED_AREAS = ("club", "storage", "unassigned", "tradepile")


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class RuntimeDB:
    def __init__(self, path, persona_id):
        self.path = Path(path)
        self.persona_id = str(persona_id)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self, identity):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.path.exists()
        with self.connect() as connection:
            if is_new:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = NORMAL")
                connection.executescript(RUNTIME_SCHEMA)
                connection.execute(
                    "INSERT INTO runtime_meta(key, value) VALUES ('schema_version', ?)",
                    (RUNTIME_SCHEMA_VERSION,),
                )
            self._validate_schema(connection)
            rows = connection.execute("SELECT persona_id FROM account_state").fetchall()
            if rows and any(str(row[0]) != self.persona_id for row in rows):
                raise FC27Error(
                    "ACCOUNT_MISMATCH",
                    f"Runtime database belongs to Persona {rows[0][0]}, not {self.persona_id}.",
                    recovery="Select the runtime database directory matching the logged-in Persona.",
                )
            connection.execute(
                """INSERT INTO account_state(persona_id, club_id, club_name, platform)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(persona_id) DO UPDATE SET
                     club_id = excluded.club_id,
                     club_name = excluded.club_name,
                     platform = excluded.platform""",
                (
                    self.persona_id,
                    identity.get("club_id"),
                    identity.get("club_name"),
                    identity["platform"],
                ),
            )
            connection.commit()
        return self.account_summary()

    def account_summary(self):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM account_state WHERE persona_id = ?", (self.persona_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["runtime_path"] = str(self.path)
        return result

    def validate_existing(self):
        with self.connect() as connection:
            self._validate_schema(connection)
            rows = connection.execute("SELECT persona_id FROM account_state").fetchall()
        if len(rows) != 1 or str(rows[0][0]) != self.persona_id:
            raise FC27Error(
                "ACCOUNT_MISMATCH",
                f"Runtime database is not bound to Persona {self.persona_id}.",
                recovery="Inspect the account directory and account_state row before using it.",
            )
        return self.account_summary()

    def begin_sync(self, kind="login_full"):
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO sync_runs(kind, started_at, status) VALUES (?, ?, 'running')",
                (kind, utc_now()),
            )
            connection.commit()
            return cursor.lastrowid

    def record_sync_part(self, sync_id, result):
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO sync_parts(sync_id, area, page_count, item_count, complete, error_code)
                   VALUES (?, ?, ?, ?, ?, NULL)
                   ON CONFLICT(sync_id, area) DO UPDATE SET
                     page_count = excluded.page_count,
                     item_count = excluded.item_count,
                     complete = excluded.complete,
                     error_code = NULL""",
                (
                    sync_id,
                    result["area"],
                    result["page_count"],
                    result["item_count"],
                    1 if result["complete"] else 0,
                ),
            )
            connection.commit()

    def fail_sync(self, sync_id, error, area=None):
        with self.connect() as connection:
            if area:
                connection.execute(
                    """INSERT INTO sync_parts(sync_id, area, complete, error_code)
                       VALUES (?, ?, 0, ?)
                       ON CONFLICT(sync_id, area) DO UPDATE SET complete = 0, error_code = excluded.error_code""",
                    (sync_id, area, error.code),
                )
            connection.execute(
                """UPDATE sync_runs SET finished_at = ?, status = 'failed',
                     error_code = ?, error_message = ? WHERE sync_id = ?""",
                (utc_now(), error.code, error.message, sync_id),
            )
            connection.commit()

    def commit_full_sync(self, sync_id, results, required_areas):
        required_areas = tuple(required_areas)
        missing = [
            area
            for area in required_areas
            if area not in results or not results[area].get("complete")
        ]
        if missing:
            raise FC27Error(
                "SYNC_INCOMPLETE",
                f"Required sync areas are incomplete: {', '.join(missing)}.",
                retryable=True,
                recovery="Retry a full sync and inspect the failed areas.",
            )
        observed_at = utc_now()
        new_items = {}
        for area in OWNED_AREAS:
            for item in (results.get(area) or {}).get("items", []):
                item_id = item["item_id"]
                if item_id in new_items:
                    raise FC27Error(
                        "DUPLICATE_ITEM_ACROSS_AREAS",
                        f"Item {item_id} appeared in both {new_items[item_id]['location']} and {area}.",
                        recovery="Inspect area responses before accepting this sync.",
                    )
                new_items[item_id] = item

        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            parts = {
                row["area"]: row
                for row in connection.execute(
                    "SELECT * FROM sync_parts WHERE sync_id = ?", (sync_id,)
                )
            }
            invalid = [
                area for area in required_areas if area not in parts or not parts[area]["complete"]
            ]
            if invalid:
                connection.rollback()
                raise FC27Error(
                    "SYNC_INCOMPLETE",
                    f"Persisted sync parts are incomplete: {', '.join(invalid)}.",
                    retryable=True,
                    recovery="Retry the full sync; current inventory was not changed.",
                )
            old_items = {
                row["item_id"]: dict(row)
                for row in connection.execute("SELECT * FROM club_items")
            }
            account = connection.execute(
                "SELECT last_full_sync_id FROM account_state WHERE persona_id = ?",
                (self.persona_id,),
            ).fetchone()
            current_sync_id = account["last_full_sync_id"] if account else None
            added_ids = sorted(set(new_items) - set(old_items))
            removed_ids = sorted(set(old_items) - set(new_items))
            item_changes = {}
            for item_id, item in new_items.items():
                old = old_items.get(item_id)
                acquisition_cost = (
                    old["acquisition_cost"]
                    if old and old["acquisition_cost"] is not None
                    else item.get("acquisition_cost")
                )
                if old is None:
                    item_changes[item_id] = {
                        "change_type": "added",
                        "details": None,
                        "acquisition_cost": acquisition_cost,
                    }
                    continue
                if old["location"] != item["location"]:
                    item_changes[item_id] = {
                        "change_type": "moved",
                        "details": None,
                        "acquisition_cost": acquisition_cost,
                    }
                    continue
                changed = {
                    key: [old[key], value]
                    for key, value in (
                        ("card_ea_id", item["card_ea_id"]),
                        ("tradeable", 1 if item["tradeable"] else 0),
                        ("loan_uses_remaining", item.get("loan_uses_remaining")),
                        ("acquisition_cost", acquisition_cost),
                    )
                    if old[key] != value
                }
                if changed:
                    item_changes[item_id] = {
                        "change_type": "attributes_changed",
                        "details": json.dumps(
                            changed, separators=(",", ":"), sort_keys=True
                        ),
                        "acquisition_cost": acquisition_cost,
                    }

            state_changed = bool(item_changes or removed_ids) or current_sync_id is None

            for result in results.values():
                for listing in result.get("listings", []):
                    if listing.get("trade_id") is None or listing.get("item_id") is None:
                        continue
                    connection.execute(
                        """INSERT INTO trade_listings(
                             trade_id, item_id, starting_bid, buy_now_price,
                             current_bid, status, expires_at, last_seen_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(trade_id) DO UPDATE SET
                             current_bid = excluded.current_bid,
                             status = excluded.status,
                             expires_at = excluded.expires_at,
                             last_seen_at = excluded.last_seen_at""",
                        (
                            listing["trade_id"],
                            listing["item_id"],
                            listing.get("starting_bid"),
                            listing.get("buy_now_price"),
                            listing.get("current_bid"),
                            listing.get("status") or "active",
                            str(listing.get("expires")) if listing.get("expires") is not None else None,
                            observed_at,
                        ),
                    )

            coins = (results.get("coins") or {}).get("coin_balance")
            if not state_changed:
                connection.execute(
                    """UPDATE account_state SET coin_balance = ?, coin_observed_at = ?,
                         last_full_sync_at = ? WHERE persona_id = ?""",
                    (coins, observed_at, observed_at, self.persona_id),
                )
                connection.execute("DELETE FROM sync_runs WHERE sync_id = ?", (sync_id,))
                connection.commit()
                return {
                    "sync_id": int(current_sync_id),
                    "observed_at": observed_at,
                    "item_count": len(new_items),
                    "added": 0,
                    "removed": 0,
                    "changed": False,
                    "complete": True,
                }

            for item_id, item in new_items.items():
                old = old_items.get(item_id)
                acquisition_cost = (
                    old["acquisition_cost"]
                    if old and old["acquisition_cost"] is not None
                    else item.get("acquisition_cost")
                )
                first_seen_at = old["first_seen_at"] if old else observed_at
                protected = old["protected"] if old else 0
                connection.execute(
                    """INSERT INTO club_items(
                         item_id, card_ea_id, location, tradeable,
                         loan_uses_remaining, acquisition_cost, protected,
                         first_seen_at, last_seen_at, last_sync_id
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(item_id) DO UPDATE SET
                         card_ea_id = excluded.card_ea_id,
                         location = excluded.location,
                         tradeable = excluded.tradeable,
                         loan_uses_remaining = excluded.loan_uses_remaining,
                         acquisition_cost = excluded.acquisition_cost,
                         last_seen_at = excluded.last_seen_at,
                         last_sync_id = excluded.last_sync_id""",
                    (
                        item_id,
                        item["card_ea_id"],
                        item["location"],
                        1 if item["tradeable"] else 0,
                        item.get("loan_uses_remaining"),
                        acquisition_cost,
                        protected,
                        first_seen_at,
                        observed_at,
                        sync_id,
                    ),
                )
                change = item_changes.get(item_id)
                change_type = change["change_type"] if change else None
                details = change["details"] if change else None
                if change_type:
                    connection.execute(
                        """INSERT INTO inventory_changes(
                             sync_id, item_id, change_type, from_location, to_location, details_json
                           ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            sync_id,
                            item_id,
                            change_type,
                            old["location"] if old else None,
                            item["location"],
                            details,
                        ),
                    )

            for item_id in removed_ids:
                old = old_items[item_id]
                connection.execute(
                    """INSERT INTO inventory_changes(
                         sync_id, item_id, change_type, from_location, to_location
                       ) VALUES (?, ?, 'removed', ?, NULL)""",
                    (sync_id, item_id, old["location"]),
                )
                connection.execute("DELETE FROM club_items WHERE item_id = ?", (item_id,))

            connection.execute(
                """UPDATE account_state SET coin_balance = ?, coin_observed_at = ?,
                     last_full_sync_id = ?, last_full_sync_at = ?
                   WHERE persona_id = ?""",
                (coins, observed_at, sync_id, observed_at, self.persona_id),
            )
            connection.execute(
                "UPDATE sync_runs SET finished_at = ?, status = 'complete' WHERE sync_id = ?",
                (observed_at, sync_id),
            )
            connection.commit()
        return {
            "sync_id": sync_id,
            "observed_at": observed_at,
            "item_count": len(new_items),
            "added": len(added_ids),
            "removed": len(removed_ids),
            "changed": True,
            "complete": True,
        }

    def query_items(self, request):
        request = request or {}
        limit = max(1, min(int(request.get("limit", 100)), 100))
        offset = int(request.get("cursor") or 0)
        where = []
        params = []
        for key, column in (("locations", "location"), ("card_ea_ids", "card_ea_id")):
            values = request.get(key) or []
            if values:
                where.append(f"{column} IN ({','.join('?' for _ in values)})")
                params.extend(values)
        for key in ("tradeable", "protected"):
            if request.get(key) is not None:
                where.append(f"{key} = ?")
                params.append(1 if request[key] else 0)
        base_sql = " FROM club_items"
        if where:
            base_sql += " WHERE " + " AND ".join(where)
        sql = "SELECT *" + base_sql
        sql += " ORDER BY item_id LIMIT ? OFFSET ?"
        with self.connect() as connection:
            total_count = connection.execute(
                "SELECT COUNT(*)" + base_sql, params
            ).fetchone()[0]
            rows = [
                dict(row)
                for row in connection.execute(sql, [*params, limit + 1, offset])
            ]
            state = self.account_summary()
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            "sync_id": state["last_full_sync_id"] if state else None,
            "count": len(rows),
            "total_count": total_count,
            "items": rows,
            "next_cursor": str(offset + limit) if has_more else None,
        }

    def record_reference_prices(self, rows):
        inserted = 0
        unchanged = 0
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for row in rows:
                latest = connection.execute(
                    """SELECT price, status FROM reference_prices
                       WHERE card_ea_id = ? AND platform = ?
                       ORDER BY observed_at DESC LIMIT 1""",
                    (row["card_ea_id"], row["platform"]),
                ).fetchone()
                if latest and latest["price"] == row["price"] and latest["status"] == row["status"]:
                    unchanged += 1
                    continue
                connection.execute(
                    """INSERT INTO reference_prices(
                         card_ea_id, platform, observed_at, price, status
                       ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        row["card_ea_id"],
                        row["platform"],
                        row["observed_at"],
                        row["price"],
                        row["status"],
                    ),
                )
                inserted += 1
            connection.commit()
        return {"inserted": inserted, "unchanged": unchanged}

    def price_facts(self, card_ea_ids, history_since):
        card_ea_ids = [int(value) for value in card_ea_ids]
        placeholders = ",".join("?" for _ in card_ea_ids)
        with self.connect() as connection:
            history_rows = [
                dict(row)
                for row in connection.execute(
                    f"""SELECT card_ea_id, platform, observed_at, price, status
                        FROM reference_prices
                        WHERE card_ea_id IN ({placeholders}) AND observed_at >= ?
                        ORDER BY card_ea_id, observed_at DESC, platform""",
                    [*card_ea_ids, history_since],
                )
            ]
            holding_rows = [
                dict(row)
                for row in connection.execute(
                    f"""SELECT item_id, card_ea_id, location, tradeable,
                               acquisition_cost, last_seen_at
                        FROM club_items
                        WHERE card_ea_id IN ({placeholders})
                        ORDER BY card_ea_id, last_seen_at DESC, item_id""",
                    card_ea_ids,
                )
            ]
            active_listing_counts = {
                int(row["card_ea_id"]): int(row["listing_count"])
                for row in connection.execute(
                    f"""SELECT ci.card_ea_id, COUNT(DISTINCT tl.trade_id) AS listing_count
                        FROM club_items ci
                        JOIN trade_listings tl ON tl.item_id = ci.item_id
                        WHERE ci.card_ea_id IN ({placeholders})
                          AND LOWER(tl.status) NOT IN ('expired', 'closed', 'sold', 'inactive')
                        GROUP BY ci.card_ea_id""",
                    card_ea_ids,
                )
            }
            market_scan_rows = [
                dict(row)
                for row in connection.execute(
                    f"""SELECT scan_id, card_ea_id, observed_at, sample_count,
                               min_buy_now, median_buy_now, p25_buy_now,
                               p75_buy_now, min_bid
                        FROM market_scans
                        WHERE card_ea_id IN ({placeholders}) AND observed_at >= ?
                        ORDER BY card_ea_id, observed_at DESC, scan_id DESC""",
                    [*card_ea_ids, history_since],
                )
            ]
            account = connection.execute(
                "SELECT platform FROM account_state WHERE persona_id = ?",
                (self.persona_id,),
            ).fetchone()
        history = {card_ea_id: [] for card_ea_id in card_ea_ids}
        for row in history_rows:
            history[row["card_ea_id"]].append(row)
        holdings = {card_ea_id: [] for card_ea_id in card_ea_ids}
        for row in holding_rows:
            row["tradeable"] = bool(row["tradeable"])
            holdings[row["card_ea_id"]].append(row)
        market_scans = {card_ea_id: [] for card_ea_id in card_ea_ids}
        for row in market_scan_rows:
            row["source"] = "ea_webapp"
            row["platform"] = account["platform"] if account else None
            market_scans[row["card_ea_id"]].append(row)
        return {
            "history": history,
            "holdings": holdings,
            "active_listing_counts": active_listing_counts,
            "market_scans": market_scans,
        }

    def record_market_scan(self, scan):
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO market_scans(
                     card_ea_id, observed_at, sample_count, min_buy_now,
                     median_buy_now, p25_buy_now, p75_buy_now, min_bid
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scan["card_ea_id"],
                    scan["observed_at"],
                    scan["sample_count"],
                    scan["min_buy_now"],
                    scan["median_buy_now"],
                    scan["p25_buy_now"],
                    scan["p75_buy_now"],
                    scan["min_bid"],
                ),
            )
            connection.commit()
            return cursor.lastrowid

    def current_item(self, item_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM club_items WHERE item_id = ?", (int(item_id),)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["tradeable"] = bool(result["tradeable"])
        result["protected"] = bool(result["protected"])
        return result

    def listing_for_item(self, item_id, *, starting_bid=None, buy_now_price=None):
        clauses = [
            "item_id = ?",
            "trade_id > 0",
            "LOWER(status) NOT IN ('expired', 'closed', 'sold', 'inactive')",
        ]
        values = [int(item_id)]
        if starting_bid is not None:
            clauses.append("starting_bid = ?")
            values.append(int(starting_bid))
        if buy_now_price is not None:
            clauses.append("buy_now_price = ?")
            values.append(int(buy_now_price))
        with self.connect() as connection:
            row = connection.execute(
                f"""SELECT * FROM trade_listings WHERE {' AND '.join(clauses)}
                    ORDER BY last_seen_at DESC LIMIT 1""",
                values,
            ).fetchone()
        return dict(row) if row else None

    def record_coin_transaction(
        self,
        *,
        transaction_id,
        action_id,
        item_id,
        card_ea_id,
        kind,
        price,
        tax,
        coin_delta,
    ):
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO coin_transactions(
                     transaction_id, action_id, item_id, card_ea_id, kind,
                     price, tax, coin_delta, observed_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    transaction_id,
                    action_id,
                    item_id,
                    card_ea_id,
                    kind,
                    price,
                    tax,
                    coin_delta,
                    utc_now(),
                ),
            )
            connection.commit()

    def reconcile_listing_actions(self):
        reconciled = []
        with self.connect() as connection:
            failed = connection.execute(
                """SELECT action_id, batch_id, params_json FROM actions
                   WHERE action_type = 'list_item' AND status = 'failed'
                     AND error_code = 'LISTING_READBACK_FAILED'"""
            ).fetchall()
            for row in failed:
                params = json.loads(row["params_json"])
                listing = connection.execute(
                    """SELECT * FROM trade_listings WHERE item_id = ? AND trade_id > 0
                       AND starting_bid = ? AND buy_now_price = ?
                       AND LOWER(status) NOT IN ('expired', 'closed', 'sold', 'inactive')
                       ORDER BY last_seen_at DESC LIMIT 1""",
                    (
                        params["item_id"],
                        params["starting_bid"],
                        params["buy_now_price"],
                    ),
                ).fetchone()
                if listing is None:
                    continue
                result = {
                    "reconciled": True,
                    "listing": dict(listing),
                    "evidence_sync_id": self.account_summary()["last_full_sync_id"],
                }
                connection.execute(
                    """UPDATE actions SET status = 'complete', error_code = NULL,
                         error_message = NULL, result_json = ? WHERE action_id = ?""",
                    (
                        json.dumps(result, separators=(",", ":"), sort_keys=True),
                        row["action_id"],
                    ),
                )
                remaining = connection.execute(
                    """SELECT COUNT(*) FROM actions
                       WHERE batch_id = ? AND status != 'complete'""",
                    (row["batch_id"],),
                ).fetchone()[0]
                if remaining == 0:
                    connection.execute(
                        "UPDATE action_batches SET status = 'complete' WHERE batch_id = ?",
                        (row["batch_id"],),
                    )
                reconciled.append(row["action_id"])
            connection.commit()
        return reconciled

    def sbc_save_reconciliation_target(self, action_id):
        with self.connect() as connection:
            state = self._sbc_save_reconciliation_state(connection, action_id)
        return {
            "action_id": action_id,
            "set_id": state["params"]["set_id"],
            "challenge_id": state["params"]["challenge_id"],
            "solution_id": state["params"]["solution_id"],
            "expected_sync_id": state["batch"]["expected_sync_id"],
        }

    def sbc_save_verification_target(self, action_id):
        with self.connect() as connection:
            state = self._sbc_saved_verification_state(connection, action_id)
        return {
            "action_id": action_id,
            "set_id": state["params"]["set_id"],
            "challenge_id": state["params"]["challenge_id"],
            "solution_id": state["params"]["solution_id"],
            "expected_sync_id": state["batch"]["expected_sync_id"],
        }

    def sbc_submit_reconciliation_target(self, action_id):
        with self.connect() as connection:
            action = connection.execute(
                """SELECT action_type, status, error_code, result_json
                   FROM actions WHERE action_id = ?""",
                (action_id,),
            ).fetchone()
            if action is not None and action["action_type"] != "submit_sbc":
                raise FC27Error(
                    "INVALID_ACTION", f"Action {action_id} is not an SBC submit action."
                )
            if action is not None and (
                action["status"] == "complete"
                or action["error_code"] == "SBC_SUBMIT_CONFIRMED_NOT_APPLIED"
            ):
                return {
                    "action_id": action_id,
                    "resolved": True,
                    "result": (
                        json.loads(action["result_json"])
                        if action["result_json"] is not None
                        else None
                    ),
                }
            state = self._sbc_submit_reconciliation_state(connection, action_id)
        return {
            "action_id": action_id,
            "resolved": False,
            "set_id": state["params"]["set_id"],
            "challenge_id": state["params"]["challenge_id"],
            "solution_id": state["params"]["solution_id"],
            "item_ids": [int(value) for value in state["params"]["item_ids"]],
            "expected_sync_id": state["batch"]["expected_sync_id"],
        }

    def verify_sbc_saved_action(self, action_id, response):
        saved_item_ids = self._require_trusted_sbc_readback(response)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._sbc_saved_verification_state(connection, action_id)
            action = state["action"]
            batch = state["batch"]
            solution = state["solution"]
            params = state["params"]
            expected_item_ids = [int(value) for value in params["item_ids"]]
            saved_slot_indices = [
                int(value) for value in response.get("saved_slot_indices") or []
            ]
            if saved_item_ids != expected_item_ids:
                connection.rollback()
                raise FC27Error(
                    "SBC_SAVE_READBACK_FAILED",
                    "Fresh EA SBC verification does not preserve the exact confirmed item order.",
                    details={
                        "saved_item_ids": saved_item_ids,
                        "expected_item_ids": expected_item_ids,
                    },
                )
            challenge = response.get("challenge") or {}
            set_value = response.get("set") or {}
            if (
                str(challenge.get("id")) != str(params["challenge_id"])
                or str(set_value.get("id")) != str(params["set_id"])
            ):
                connection.rollback()
                raise FC27Error(
                    "SBC_SAVE_READBACK_FAILED",
                    "Fresh EA SBC verification returned a different set or challenge.",
                )
            solution_items = connection.execute(
                """SELECT slot_index, item_id FROM sbc_solution_items
                   WHERE solution_id = ? ORDER BY slot_index""",
                (params["solution_id"],),
            ).fetchall()
            solution_item_ids = [int(row["item_id"]) for row in solution_items]
            solution_slot_indices = [
                int(row["slot_index"]) for row in solution_items
            ]
            if (
                solution_item_ids != expected_item_ids
                or saved_slot_indices != solution_slot_indices
            ):
                connection.rollback()
                raise FC27Error(
                    "SBC_SAVE_READBACK_FAILED",
                    "The persisted SBC solution no longer matches the completed save action or fillable slots.",
                )
            evidence = {
                "saved_at_sync_id": int(batch["expected_sync_id"]),
                "saved_item_ids": saved_item_ids,
                "saved_slot_indices": saved_slot_indices,
                "ea_eligible": True,
                "verified": True,
                "source": "ea_webapp_fresh",
                "ea": response,
            }
            original_result = (
                json.loads(action["result_json"])
                if action["result_json"] is not None
                else None
            )
            result = {**evidence, "original_result": original_result}
            finished_at = utc_now()
            action_update = connection.execute(
                """UPDATE actions SET result_json = ?, finished_at = ?
                   WHERE action_id = ? AND status = 'complete'""",
                (
                    json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                    finished_at,
                    action_id,
                ),
            )
            validation = json.loads(solution["validation_json"])
            validation["execution"] = evidence
            solution_update = connection.execute(
                """UPDATE sbc_solutions SET validation_json = ?
                   WHERE solution_id = ? AND status = 'saved'""",
                (
                    json.dumps(validation, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                    params["solution_id"],
                ),
            )
            if action_update.rowcount != 1 or solution_update.rowcount != 1:
                connection.rollback()
                raise FC27Error(
                    "SBC_SAVE_VERIFICATION_CONFLICT",
                    "The completed SBC save action or solution changed during verification.",
                )
            connection.commit()
        return result

    def reconcile_sbc_save_action(self, action_id, response):
        saved_item_ids = self._require_trusted_sbc_readback(response)

        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._sbc_save_reconciliation_state(connection, action_id)
            action = state["action"]
            batch = state["batch"]
            solution = state["solution"]
            params = state["params"]
            expected_item_ids = [int(value) for value in params["item_ids"]]
            saved_slot_indices = [
                int(value) for value in response.get("saved_slot_indices") or []
            ]
            if saved_item_ids != expected_item_ids:
                connection.rollback()
                raise FC27Error(
                    "SBC_SAVE_READBACK_FAILED",
                    "Fresh EA SBC readback does not preserve the exact confirmed item order.",
                    details={
                        "saved_item_ids": saved_item_ids,
                        "expected_item_ids": expected_item_ids,
                    },
                )
            challenge = response.get("challenge") or {}
            set_value = response.get("set") or {}
            if (
                str(challenge.get("id")) != str(params["challenge_id"])
                or str(set_value.get("id")) != str(params["set_id"])
            ):
                connection.rollback()
                raise FC27Error(
                    "SBC_SAVE_READBACK_FAILED",
                    "Fresh EA SBC readback returned a different set or challenge.",
                )
            solution_items = connection.execute(
                """SELECT slot_index, item_id FROM sbc_solution_items
                   WHERE solution_id = ? ORDER BY slot_index""",
                (params["solution_id"],),
            ).fetchall()
            solution_item_ids = [int(row["item_id"]) for row in solution_items]
            solution_slot_indices = [
                int(row["slot_index"]) for row in solution_items
            ]
            if (
                solution_item_ids != expected_item_ids
                or saved_slot_indices != solution_slot_indices
            ):
                connection.rollback()
                raise FC27Error(
                    "SBC_SAVE_READBACK_FAILED",
                    "The persisted SBC solution no longer matches the failed action or fillable slots.",
                )
            evidence = {
                "saved_at_sync_id": int(batch["expected_sync_id"]),
                "saved_item_ids": saved_item_ids,
                "saved_slot_indices": saved_slot_indices,
                "ea_eligible": True,
                "reconciled": True,
                "source": "ea_webapp_fresh",
                "ea": response,
            }
            result = {"reconciled": True, **evidence}
            finished_at = utc_now()
            action_update = connection.execute(
                """UPDATE actions SET status = 'complete', finished_at = ?,
                     error_code = NULL, error_message = NULL, result_json = ?
                   WHERE action_id = ? AND status = 'failed'
                     AND error_code IN (
                       'BRIDGE_TIMEOUT',
                       'SBC_SAVE_OUTCOME_UNKNOWN',
                       'SBC_SAVE_READBACK_PENDING'
                     )""",
                (
                    finished_at,
                    json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                    action_id,
                ),
            )
            validation = json.loads(solution["validation_json"])
            validation["execution"] = evidence
            solution_update = connection.execute(
                """UPDATE sbc_solutions SET status = 'saved', validation_json = ?
                   WHERE solution_id = ? AND status = 'validated'""",
                (
                    json.dumps(validation, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                    params["solution_id"],
                ),
            )
            if action_update.rowcount != 1 or solution_update.rowcount != 1:
                connection.rollback()
                raise FC27Error(
                    "SBC_SAVE_RECONCILIATION_CONFLICT",
                    "The SBC save action or solution changed during reconciliation.",
                )
            statuses = [
                row["status"]
                for row in connection.execute(
                    "SELECT status FROM actions WHERE batch_id = ?",
                    (action["batch_id"],),
                )
            ]
            if statuses and all(status == "complete" for status in statuses):
                batch_status = "complete"
            elif any(status == "complete" for status in statuses):
                batch_status = "partial"
            elif any(status == "failed" for status in statuses):
                batch_status = "failed"
            else:
                batch_status = "running"
            connection.execute(
                "UPDATE action_batches SET status = ?, finished_at = ? WHERE batch_id = ?",
                (
                    batch_status,
                    finished_at if batch_status in ("complete", "partial", "failed") else None,
                    action["batch_id"],
                ),
            )
            connection.commit()
        return result

    def reconcile_sbc_submit_action(
        self, action_id, sync, post_submit, saved_squad=None
    ):
        self._require_trusted_sbc_submission_state(post_submit)
        sync_id = sync.get("sync_id")
        if sync.get("complete") is not True or not isinstance(sync_id, int):
            raise FC27Error(
                "SBC_SUBMIT_STILL_UNKNOWN",
                "Submission reconciliation requires a newer complete club synchronization.",
            )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._sbc_submit_reconciliation_state(connection, action_id)
            action = state["action"]
            batch = state["batch"]
            solution = state["solution"]
            params = state["params"]
            pre_submit = state["pre_submit"]
            expected_item_ids = [int(value) for value in params["item_ids"]]
            current_sync_id = connection.execute(
                "SELECT last_full_sync_id FROM account_state WHERE persona_id = ?",
                (self.persona_id,),
            ).fetchone()[0]
            present_item_ids = [
                int(row["item_id"])
                for row in connection.execute(
                    f"SELECT item_id FROM club_items WHERE item_id IN ({','.join('?' for _ in expected_item_ids)}) ORDER BY item_id",
                    expected_item_ids,
                )
            ]
            if current_sync_id != sync_id or sync_id < batch["expected_sync_id"]:
                connection.rollback()
                raise FC27Error(
                    "SBC_SUBMIT_STILL_UNKNOWN",
                    "The reconciliation sync does not represent the current club state after the original submit attempt.",
                    details={
                        "expected_sync_id": batch["expected_sync_id"],
                        "evidence_sync_id": sync_id,
                        "current_sync_id": current_sync_id,
                    },
                )
            progress = self._classify_sbc_submit_outcome(
                pre_submit,
                post_submit,
                expected_item_ids,
                present_item_ids,
                saved_squad,
            )
            removal_evidence = self._sbc_removed_item_evidence(
                connection,
                expected_item_ids,
                batch["expected_sync_id"],
                sync_id,
            )
            progress.update(removal_evidence)
            if (
                progress["outcome"] == "success"
                and removal_evidence["removed_item_ids"] != sorted(expected_item_ids)
            ):
                progress["outcome"] = "unknown"
                progress["reason"] = "complete removal history is missing"
            evidence = {
                "reconciled": True,
                "source": "ea_webapp_fresh",
                "submitted_at_sync_id": sync_id,
                "submitted_item_ids": expected_item_ids,
                "pre_submit": pre_submit,
                "post_submit": post_submit,
                "sync": sync,
                "progress": progress,
            }
            if saved_squad is not None:
                evidence["saved_squad"] = saved_squad
            finished_at = utc_now()
            allowed_errors = (
                "BRIDGE_TIMEOUT",
                "PAGE_BRIDGE_TIMEOUT",
                "EA_SERVICE_TIMEOUT",
                "SBC_SUBMIT_OUTCOME_UNKNOWN",
                "SBC_SUBMIT_READBACK_PENDING",
                "SBC_SUBMIT_STILL_UNKNOWN",
            )
            placeholders = ",".join("?" for _ in allowed_errors)
            if progress["outcome"] == "success":
                stored_result = {
                    **evidence,
                    "action_status": "complete",
                    "error_code": None,
                }
                validation = json.loads(solution["validation_json"])
                validation["execution"] = evidence
                solution_update = connection.execute(
                    """UPDATE sbc_solutions SET status = 'submitted', validation_json = ?
                       WHERE solution_id = ? AND status IN ('saved', 'submitted')""",
                    (
                        json.dumps(
                            validation,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        params["solution_id"],
                    ),
                )
                action_update = connection.execute(
                    f"""UPDATE actions SET status = 'complete', finished_at = ?,
                         error_code = NULL, error_message = NULL, result_json = ?
                       WHERE action_id = ? AND (
                         status = 'running'
                         OR (status = 'failed' AND error_code IN ({placeholders}))
                       )""",
                    (
                        finished_at,
                        json.dumps(
                            stored_result,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        action_id,
                        *allowed_errors,
                    ),
                )
                if solution_update.rowcount != 1 or action_update.rowcount != 1:
                    connection.rollback()
                    raise FC27Error(
                        "SBC_SUBMIT_RECONCILIATION_CONFLICT",
                        "The submit action or solution changed during successful reconciliation.",
                    )
            elif progress["outcome"] == "not_applied":
                stored_result = {
                    **evidence,
                    "action_status": "failed",
                    "error_code": "SBC_SUBMIT_CONFIRMED_NOT_APPLIED",
                }
                refreshed_save_evidence = {
                    "saved_at_sync_id": sync_id,
                    "saved_item_ids": expected_item_ids,
                    "ea_eligible": True,
                    "verified": True,
                    "reconciled_from_submit": True,
                    "source": "ea_webapp_fresh",
                    "ea": saved_squad,
                }
                validation = json.loads(solution["validation_json"])
                validation["execution"] = refreshed_save_evidence
                solution_update = connection.execute(
                    """UPDATE sbc_solutions SET validation_json = ?
                       WHERE solution_id = ? AND status = 'saved'""",
                    (
                        json.dumps(
                            validation,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        params["solution_id"],
                    ),
                )
                action_update = connection.execute(
                    f"""UPDATE actions SET status = 'failed', finished_at = ?,
                         error_code = 'SBC_SUBMIT_CONFIRMED_NOT_APPLIED',
                         error_message = ?, result_json = ?
                       WHERE action_id = ? AND (
                         status = 'running'
                         OR (status = 'failed' AND error_code IN ({placeholders}))
                       )""",
                    (
                        finished_at,
                        "Fresh inventory and challenge evidence prove the submit was not applied.",
                        json.dumps(
                            stored_result,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        action_id,
                        *allowed_errors,
                    ),
                )
                if action_update.rowcount != 1 or solution_update.rowcount != 1:
                    connection.rollback()
                    raise FC27Error(
                        "SBC_SUBMIT_RECONCILIATION_CONFLICT",
                        "The submit action or saved solution changed during failure reconciliation.",
                    )
            else:
                stored_result = {
                    **evidence,
                    "action_status": "failed",
                    "error_code": "SBC_SUBMIT_STILL_UNKNOWN",
                }
                action_update = connection.execute(
                    f"""UPDATE actions SET status = 'failed', finished_at = ?,
                         error_code = 'SBC_SUBMIT_STILL_UNKNOWN',
                         error_message = ?, result_json = ?
                       WHERE action_id = ? AND (
                         status = 'running'
                         OR (status = 'failed' AND error_code IN ({placeholders}))
                       )""",
                    (
                        finished_at,
                        "Fresh evidence does not prove whether exactly one SBC submission completed.",
                        json.dumps(
                            stored_result,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        action_id,
                        *allowed_errors,
                    ),
                )
                if action_update.rowcount != 1 or solution["status"] != "saved":
                    connection.rollback()
                    raise FC27Error(
                        "SBC_SUBMIT_RECONCILIATION_CONFLICT",
                        "The submit action or saved solution changed during unknown reconciliation.",
                    )
            statuses = [
                row["status"]
                for row in connection.execute(
                    "SELECT status FROM actions WHERE batch_id = ?",
                    (action["batch_id"],),
                )
            ]
            if statuses and all(status == "complete" for status in statuses):
                batch_status = "complete"
            elif any(status == "complete" for status in statuses):
                batch_status = "partial"
            elif any(status == "failed" for status in statuses):
                batch_status = "failed"
            else:
                batch_status = "running"
            connection.execute(
                "UPDATE action_batches SET status = ?, finished_at = ? WHERE batch_id = ?",
                (
                    batch_status,
                    finished_at if batch_status in ("complete", "partial", "failed") else None,
                    action["batch_id"],
                ),
            )
            connection.commit()
        return stored_result

    @staticmethod
    def _require_trusted_sbc_readback(response):
        saved_item_ids = [int(value) for value in response.get("saved_item_ids") or []]
        saved_slot_indices = [
            int(value) for value in response.get("saved_slot_indices") or []
        ]
        squad = response.get("squad") or {}
        eligibility = squad.get("eligibility_evidence") or {}
        freshness = response.get("freshness") or {}
        requirements = eligibility.get("requirements") or []
        trusted_readback = (
            response.get("source") == "ea_webapp_fresh"
            and freshness.get("sets_requested") is True
            and freshness.get("challenges_requested") is True
            and freshness.get("challenge_loaded") is True
            and squad.get("eligible") is True
            and eligibility.get("source") == "ea_challenge_requirements"
            and eligibility.get("identity_match") is True
            and eligibility.get("all_requirements_met") is True
            and eligibility.get("submit_available") is True
            and bool(requirements)
            and all(value.get("met") is True for value in requirements)
            and len(saved_slot_indices) == len(saved_item_ids)
            and len(saved_slot_indices) == len(set(saved_slot_indices))
            and all(0 <= value < 11 for value in saved_slot_indices)
        )
        if not trusted_readback:
            raise FC27Error(
                "SBC_SAVE_READBACK_FAILED",
                "SBC save verification requires a fresh trusted EA readback with complete positive eligibility evidence.",
                details={"source": response.get("source"), "eligibility": eligibility},
            )
        return saved_item_ids

    @staticmethod
    def _require_trusted_sbc_submission_state(response):
        freshness = response.get("freshness") or {}
        set_value = response.get("set") or {}
        challenge = response.get("challenge") or {}
        if (
            response.get("source") != "ea_webapp_fresh"
            or freshness.get("sets_requested") is not True
            or freshness.get("challenges_requested") is not True
            or set_value.get("id") is None
            or challenge.get("id") is None
        ):
            raise FC27Error(
                "SBC_SUBMIT_STATE_INVALID",
                "SBC submission reconciliation requires a fresh matching set and challenge read.",
                details={"source": response.get("source"), "freshness": freshness},
            )
        return {"set": set_value, "challenge": challenge}

    @staticmethod
    def _sbc_counter(value):
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _classify_sbc_submit_outcome(
        cls, pre_submit, post_submit, expected_item_ids, present_item_ids, saved_squad=None
    ):
        saved_item_ids = cls._require_trusted_sbc_readback(pre_submit)
        state = cls._require_trusted_sbc_submission_state(post_submit)
        expected_item_ids = [int(value) for value in expected_item_ids]
        present_item_ids = sorted(int(value) for value in present_item_ids)
        if saved_item_ids != expected_item_ids:
            raise FC27Error(
                "SBC_SUBMIT_STATE_INVALID",
                "The pre-submit saved squad does not match the confirmed item order.",
            )
        pre_set = pre_submit.get("set") or {}
        pre_challenge = pre_submit.get("challenge") or {}
        post_set = state["set"]
        post_challenge = state["challenge"]
        if (
            str(pre_set.get("id")) != str(post_set.get("id"))
            or str(pre_challenge.get("id")) != str(post_challenge.get("id"))
        ):
            raise FC27Error(
                "SBC_SUBMIT_STATE_INVALID",
                "The post-submit set or challenge identity does not match the baseline.",
            )
        counters = {}
        incomplete_counter_sources = []
        for source, before, after in (
            (
                "challenge.times_completed",
                pre_challenge.get("times_completed"),
                post_challenge.get("times_completed"),
            ),
            (
                "set.times_completed",
                pre_set.get("times_completed"),
                post_set.get("times_completed"),
            ),
        ):
            before_value = cls._sbc_counter(before)
            after_value = cls._sbc_counter(after)
            if before_value is not None and after_value is not None:
                counters[source] = {
                    "before": before_value,
                    "after": after_value,
                    "delta": after_value - before_value,
                }
            elif before is not None or after is not None:
                incomplete_counter_sources.append(source)
        counter_deltas = {value["delta"] for value in counters.values()}
        counter_consistent = (
            bool(counters)
            and not incomplete_counter_sources
            and len(counter_deltas) == 1
        )
        counter_delta = next(iter(counter_deltas)) if counter_consistent else None
        counter_source = next(iter(counters)) if len(counters) == 1 else None
        before_counter = (
            counters[counter_source]["before"] if counter_source is not None else None
        )
        after_counter = (
            counters[counter_source]["after"] if counter_source is not None else None
        )
        cycle_progress = {
            "before": cls._sbc_counter(pre_set.get("completed_count")),
            "after": cls._sbc_counter(post_set.get("completed_count")),
        }
        repeatable = bool(
            pre_challenge.get("repeatable") or pre_set.get("repeatable")
        )
        all_absent = not present_item_ids
        all_present = present_item_ids == sorted(expected_item_ids)
        if repeatable:
            success = counter_consistent and counter_delta == 1 and all_absent
            not_applied = counter_consistent and counter_delta == 0 and all_present
        else:
            pre_completed = bool(
                pre_challenge.get("completed") or pre_set.get("completed")
            )
            post_completed = bool(
                post_challenge.get("completed") or post_set.get("completed")
            )
            success = not pre_completed and post_completed and all_absent
            not_applied = not pre_completed and not post_completed and all_present
        trusted_saved_squad = False
        if not_applied and saved_squad is not None:
            trusted_saved_squad = (
                cls._require_trusted_sbc_readback(saved_squad) == expected_item_ids
            )
            not_applied = trusted_saved_squad
        elif not_applied:
            not_applied = False
        outcome = "success" if success else ("not_applied" if not_applied else "unknown")
        return {
            "outcome": outcome,
            "repeatable": repeatable,
            "counter_source": counter_source,
            "counters": counters,
            "incomplete_counter_sources": incomplete_counter_sources,
            "counter_consistent": counter_consistent,
            "before_counter": before_counter,
            "after_counter": after_counter,
            "counter_delta": counter_delta,
            "cycle_progress": cycle_progress,
            "present_item_ids": present_item_ids,
            "missing_item_ids": sorted(set(expected_item_ids) - set(present_item_ids)),
            "trusted_saved_squad": trusted_saved_squad,
        }

    @staticmethod
    def _sbc_removed_item_evidence(
        connection, item_ids, after_sync_id, through_sync_id
    ):
        rows = connection.execute(
            f"""SELECT ic.item_id, ic.sync_id
                FROM inventory_changes AS ic
                JOIN sync_runs AS sr ON sr.sync_id = ic.sync_id
                WHERE ic.change_type = 'removed'
                  AND ic.item_id IN ({','.join('?' for _ in item_ids)})
                  AND ic.sync_id > ? AND ic.sync_id <= ?
                  AND sr.status = 'complete'
                ORDER BY ic.item_id, ic.sync_id""",
            [*item_ids, after_sync_id, through_sync_id],
        ).fetchall()
        return {
            "removed_item_ids": sorted({int(row["item_id"]) for row in rows}),
            "removal_sync_ids": sorted({int(row["sync_id"]) for row in rows}),
        }

    def _sbc_save_reconciliation_state(self, connection, action_id):
        action = connection.execute(
            "SELECT * FROM actions WHERE action_id = ?", (action_id,)
        ).fetchone()
        if action is None:
            raise FC27Error("ACTION_NOT_FOUND", f"Action {action_id} was not found.")
        if action["action_type"] != "save_sbc_squad":
            raise FC27Error("INVALID_ACTION", f"Action {action_id} is not an SBC save action.")
        if action["status"] != "failed" or action["error_code"] not in (
            "BRIDGE_TIMEOUT",
            "SBC_SAVE_OUTCOME_UNKNOWN",
            "SBC_SAVE_READBACK_PENDING",
        ):
            raise FC27Error(
                "SBC_SAVE_RECONCILIATION_NOT_ALLOWED",
                "Only an ambiguous or pending SBC save readback can be reconciled.",
            )
        batch = connection.execute(
            "SELECT * FROM action_batches WHERE batch_id = ?", (action["batch_id"],)
        ).fetchone()
        if batch is None or batch["status"] not in ("failed", "partial"):
            raise FC27Error(
                "SBC_SAVE_RECONCILIATION_NOT_ALLOWED",
                "The containing action batch is not in a failed or partial state.",
            )
        params = json.loads(action["params_json"])
        solution = connection.execute(
            "SELECT * FROM sbc_solutions WHERE solution_id = ?",
            (params["solution_id"],),
        ).fetchone()
        if solution is None:
            raise FC27Error(
                "SBC_SOLUTION_NOT_FOUND",
                f"SBC solution {params['solution_id']} was not found.",
            )
        if str(solution["challenge_id"]) != str(params["challenge_id"]):
            raise FC27Error(
                "SBC_SAVE_RECONCILIATION_NOT_ALLOWED",
                "The failed action challenge does not match its persisted solution.",
            )
        if solution["status"] != "validated":
            raise FC27Error(
                "SBC_SAVE_RECONCILIATION_NOT_ALLOWED",
                f"SBC solution status {solution['status']} cannot transition to saved.",
            )
        current_sync_id = connection.execute(
            "SELECT last_full_sync_id FROM account_state WHERE persona_id = ?",
            (self.persona_id,),
        ).fetchone()[0]
        if current_sync_id != batch["expected_sync_id"]:
            raise FC27Error(
                "STALE_CLUB_STATE",
                f"Failed save expected sync {batch['expected_sync_id']}, current complete sync is {current_sync_id}.",
                recovery="Regenerate and save a new SBC solution against the current complete sync.",
            )
        return {
            "action": action,
            "batch": batch,
            "solution": solution,
            "params": params,
        }

    def _sbc_saved_verification_state(self, connection, action_id):
        action = connection.execute(
            "SELECT * FROM actions WHERE action_id = ?", (action_id,)
        ).fetchone()
        if action is None:
            raise FC27Error("ACTION_NOT_FOUND", f"Action {action_id} was not found.")
        if action["action_type"] != "save_sbc_squad" or action["status"] != "complete":
            raise FC27Error(
                "SBC_SAVE_VERIFICATION_NOT_ALLOWED",
                "Fresh verification requires a completed SBC save action.",
            )
        batch = connection.execute(
            "SELECT * FROM action_batches WHERE batch_id = ?", (action["batch_id"],)
        ).fetchone()
        if batch is None or batch["status"] not in ("complete", "partial"):
            raise FC27Error(
                "SBC_SAVE_VERIFICATION_NOT_ALLOWED",
                "The containing action batch is not complete or partial.",
            )
        params = json.loads(action["params_json"])
        solution = connection.execute(
            "SELECT * FROM sbc_solutions WHERE solution_id = ?",
            (params["solution_id"],),
        ).fetchone()
        if solution is None:
            raise FC27Error(
                "SBC_SOLUTION_NOT_FOUND",
                f"SBC solution {params['solution_id']} was not found.",
            )
        if (
            str(solution["challenge_id"]) != str(params["challenge_id"])
            or solution["status"] != "saved"
        ):
            raise FC27Error(
                "SBC_SAVE_VERIFICATION_NOT_ALLOWED",
                "The completed action does not reference the expected saved solution.",
            )
        current_sync_id = connection.execute(
            "SELECT last_full_sync_id FROM account_state WHERE persona_id = ?",
            (self.persona_id,),
        ).fetchone()[0]
        if current_sync_id != batch["expected_sync_id"]:
            raise FC27Error(
                "STALE_CLUB_STATE",
                f"Saved action expected sync {batch['expected_sync_id']}, current complete sync is {current_sync_id}.",
                recovery="Regenerate and save a new SBC solution against the current complete sync.",
            )
        return {
            "action": action,
            "batch": batch,
            "solution": solution,
            "params": params,
        }

    @staticmethod
    def _sbc_submit_checkpoint(action):
        result = json.loads(action["result_json"]) if action["result_json"] else {}
        if result.get("pre_submit") is not None:
            return result["pre_submit"]
        return (
            result.get("error", {}).get("details", {}).get("pre_submit")
        )

    def _sbc_submit_reconciliation_state(self, connection, action_id):
        action = connection.execute(
            "SELECT * FROM actions WHERE action_id = ?", (action_id,)
        ).fetchone()
        if action is None:
            raise FC27Error("ACTION_NOT_FOUND", f"Action {action_id} was not found.")
        if action["action_type"] != "submit_sbc":
            raise FC27Error(
                "INVALID_ACTION", f"Action {action_id} is not an SBC submit action."
            )
        allowed_errors = (
            "BRIDGE_TIMEOUT",
            "PAGE_BRIDGE_TIMEOUT",
            "EA_SERVICE_TIMEOUT",
            "SBC_SUBMIT_OUTCOME_UNKNOWN",
            "SBC_SUBMIT_READBACK_PENDING",
            "SBC_SUBMIT_STILL_UNKNOWN",
        )
        if not (
            action["status"] == "running"
            or (
                action["status"] == "failed"
                and action["error_code"] in allowed_errors
            )
        ):
            raise FC27Error(
                "SBC_SUBMIT_RECONCILIATION_NOT_ALLOWED",
                "Only a running or unresolved SBC submit action can be reconciled.",
            )
        batch = connection.execute(
            "SELECT * FROM action_batches WHERE batch_id = ?", (action["batch_id"],)
        ).fetchone()
        if batch is None or batch["status"] not in ("running", "failed", "partial"):
            raise FC27Error(
                "SBC_SUBMIT_RECONCILIATION_NOT_ALLOWED",
                "The containing submit batch is not running, failed, or partial.",
            )
        params = json.loads(action["params_json"])
        solution = connection.execute(
            "SELECT * FROM sbc_solutions WHERE solution_id = ?",
            (params["solution_id"],),
        ).fetchone()
        if solution is None:
            raise FC27Error(
                "SBC_SOLUTION_NOT_FOUND",
                f"SBC solution {params['solution_id']} was not found.",
            )
        if (
            str(solution["challenge_id"]) != str(params["challenge_id"])
            or solution["status"] not in ("saved", "submitted")
        ):
            raise FC27Error(
                "SBC_SUBMIT_RECONCILIATION_NOT_ALLOWED",
                "The submit action does not reference the expected saved or submitted solution.",
            )
        expected_item_ids = [int(value) for value in params["item_ids"]]
        solution_items = connection.execute(
            """SELECT slot_index, item_id FROM sbc_solution_items
               WHERE solution_id = ? ORDER BY slot_index""",
            (params["solution_id"],),
        ).fetchall()
        persisted_item_ids = [int(row["item_id"]) for row in solution_items]
        persisted_slot_indices = [
            int(row["slot_index"]) for row in solution_items
        ]
        if persisted_item_ids != expected_item_ids:
            raise FC27Error(
                "SBC_SUBMIT_RECONCILIATION_NOT_ALLOWED",
                "The persisted solution item order no longer matches the submit action.",
            )
        pre_submit = self._sbc_submit_checkpoint(action)
        if pre_submit is None:
            raise FC27Error(
                "SBC_SUBMIT_RECONCILIATION_NOT_ALLOWED",
                "The submit action has no trusted pre-submit checkpoint.",
            )
        saved_item_ids = self._require_trusted_sbc_readback(pre_submit)
        saved_slot_indices = [
            int(value) for value in pre_submit.get("saved_slot_indices") or []
        ]
        if (
            saved_item_ids != expected_item_ids
            or saved_slot_indices != persisted_slot_indices
            or str((pre_submit.get("set") or {}).get("id"))
            != str(params["set_id"])
            or str((pre_submit.get("challenge") or {}).get("id"))
            != str(params["challenge_id"])
        ):
            raise FC27Error(
                "SBC_SUBMIT_RECONCILIATION_NOT_ALLOWED",
                "The pre-submit checkpoint does not match the exact action target.",
            )
        current_sync_id = connection.execute(
            "SELECT last_full_sync_id FROM account_state WHERE persona_id = ?",
            (self.persona_id,),
        ).fetchone()[0]
        if current_sync_id < batch["expected_sync_id"]:
            raise FC27Error(
                "STALE_CLUB_STATE",
                "The current club synchronization predates the original submit batch.",
            )
        return {
            "action": action,
            "batch": batch,
            "solution": solution,
            "params": params,
            "pre_submit": pre_submit,
        }

    def upsert_sbc_sets(self, sets):
        with self.connect() as connection:
            for value in sets:
                connection.execute(
                    """INSERT INTO sbc_sets(
                         set_id, name, status, expires_at, observed_at, raw_json
                       ) VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(set_id) DO UPDATE SET
                         name = excluded.name,
                         status = excluded.status,
                         expires_at = excluded.expires_at,
                         observed_at = excluded.observed_at,
                         raw_json = excluded.raw_json""",
                    (
                        value["set_id"],
                        value["name"],
                        value.get("status"),
                        value.get("expires_at"),
                        value["observed_at"],
                        json.dumps(
                            {
                                "repeatable": value["repeatable"],
                                "challenge_count": value["challenge_count"],
                                "completed_count": value["completed_count"],
                                "times_completed": value["times_completed"],
                                "rewards": value["rewards"],
                                "raw": value["raw"],
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    ),
                )
            connection.commit()

    def upsert_sbc_challenges(self, challenges):
        with self.connect() as connection:
            for value in challenges:
                connection.execute(
                    """INSERT INTO sbc_challenges(
                         challenge_id, set_id, name, status, repeatable,
                         requirements_json, observed_at, raw_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(challenge_id) DO UPDATE SET
                         set_id = excluded.set_id,
                         name = excluded.name,
                         status = excluded.status,
                         repeatable = excluded.repeatable,
                         requirements_json = excluded.requirements_json,
                         observed_at = excluded.observed_at,
                         raw_json = excluded.raw_json""",
                    (
                        value["challenge_id"],
                        value["set_id"],
                        value["name"],
                        value.get("status"),
                        1 if value["repeatable"] else 0,
                        json.dumps(
                            {
                                "constraints": value["constraints"],
                                "unsupported_constraints": value["unsupported_constraints"],
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        value["observed_at"],
                        json.dumps(
                            {
                                "completed": value["completed"],
                                "times_completed": value.get("times_completed"),
                                "expires_at": value["expires_at"],
                                "challenge_type": value.get("challenge_type"),
                                "player_count": value.get("player_count"),
                                "player_count_source": value.get("player_count_source"),
                                "slot_indices": value.get("slot_indices"),
                                "slot_indices_source": value.get("slot_indices_source"),
                                "slot_layout_error": value.get("slot_layout_error"),
                                "formation": value["formation"],
                                "slots": value["slots"],
                                "rewards": value["rewards"],
                                "raw": value["raw"],
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    ),
                )
            connection.commit()

    def query_sbcs(self, *, set_id=None, challenge_id=None):
        with self.connect() as connection:
            if challenge_id is not None:
                row = connection.execute(
                    "SELECT * FROM sbc_challenges WHERE challenge_id = ?",
                    (str(challenge_id),),
                ).fetchone()
                return {"challenge": self._decode_sbc_challenge(row) if row else None}
            if set_id is not None:
                set_row = connection.execute(
                    "SELECT * FROM sbc_sets WHERE set_id = ?", (str(set_id),)
                ).fetchone()
                challenges = connection.execute(
                    "SELECT * FROM sbc_challenges WHERE set_id = ? ORDER BY challenge_id",
                    (str(set_id),),
                ).fetchall()
                return {
                    "set": self._decode_sbc_set(set_row) if set_row else None,
                    "challenges": [self._decode_sbc_challenge(row) for row in challenges],
                }
            rows = connection.execute("SELECT * FROM sbc_sets ORDER BY set_id").fetchall()
        return {"sets": [self._decode_sbc_set(row) for row in rows]}

    def get_sbc_challenge(self, challenge_id):
        return self.query_sbcs(challenge_id=challenge_id)["challenge"]

    def sbc_candidate_items(self, candidate_item_ids=None, exclude_item_ids=None):
        where = ["location IN ('club', 'storage', 'unassigned')"]
        params = []
        if candidate_item_ids:
            ids = [int(value) for value in candidate_item_ids]
            where.append(f"item_id IN ({','.join('?' for _ in ids)})")
            params.extend(ids)
        if exclude_item_ids:
            ids = [int(value) for value in exclude_item_ids]
            where.append(f"item_id NOT IN ({','.join('?' for _ in ids)})")
            params.extend(ids)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM club_items WHERE " + " AND ".join(where) + " ORDER BY item_id",
                params,
            ).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["tradeable"] = bool(value["tradeable"])
            value["protected"] = bool(value["protected"])
            if value["protected"] or value["loan_uses_remaining"] not in (None, -1):
                continue
            result.append(value)
        return result

    def items_by_ids(self, item_ids):
        ids = [int(value) for value in item_ids]
        if not ids:
            return []
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM club_items WHERE item_id IN ({','.join('?' for _ in ids)})",
                ids,
            ).fetchall()
        by_id = {int(row["item_id"]): dict(row) for row in rows}
        return [by_id[item_id] for item_id in ids if item_id in by_id]

    def latest_reference_price(self, card_ea_id):
        with self.connect() as connection:
            account = connection.execute(
                "SELECT platform FROM account_state WHERE persona_id = ?",
                (self.persona_id,),
            ).fetchone()
            row = connection.execute(
                """SELECT price FROM reference_prices
                   WHERE card_ea_id = ? AND platform = ? AND price IS NOT NULL
                   ORDER BY observed_at DESC LIMIT 1""",
                (int(card_ea_id), account["platform"] if account else "pc"),
            ).fetchone()
        return int(row["price"]) if row else None

    def save_sbc_solution(self, solution):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO sbc_solutions(
                     solution_id, challenge_id, created_at, estimated_cost,
                     tradeable_value, objective_json, validation_json, status
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(solution_id) DO UPDATE SET
                     estimated_cost = excluded.estimated_cost,
                     tradeable_value = excluded.tradeable_value,
                     objective_json = excluded.objective_json,
                     validation_json = excluded.validation_json,
                     status = excluded.status""",
                (
                    solution["solution_id"],
                    solution["challenge_id"],
                    solution["created_at"],
                    solution["estimated_cost"],
                    solution["tradeable_value"],
                    json.dumps(solution["objective"], ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                    json.dumps(solution["validation"], ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                    solution["status"],
                ),
            )
            connection.execute(
                "DELETE FROM sbc_solution_items WHERE solution_id = ?",
                (solution["solution_id"],),
            )
            connection.executemany(
                "INSERT INTO sbc_solution_items(solution_id, slot_index, item_id) VALUES (?, ?, ?)",
                [
                    (solution["solution_id"], row["slot_index"], row["item_id"])
                    for row in solution["slots"]
                ],
            )
            connection.commit()

    def get_sbc_solution(self, solution_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sbc_solutions WHERE solution_id = ?", (solution_id,)
            ).fetchone()
            if row is None:
                return None
            items = connection.execute(
                """SELECT slot_index, item_id FROM sbc_solution_items
                   WHERE solution_id = ? ORDER BY slot_index""",
                (solution_id,),
            ).fetchall()
        result = dict(row)
        result["objective"] = json.loads(result.pop("objective_json"))
        result["validation"] = json.loads(result.pop("validation_json"))
        result["item_ids"] = [int(value["item_id"]) for value in items]
        result["slots"] = [dict(value) for value in items]
        return result

    def record_sbc_submit_checkpoint(self, action_id, expected_sync_id, response):
        saved_item_ids = self._require_trusted_sbc_readback(response)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            action = connection.execute(
                "SELECT * FROM actions WHERE action_id = ?", (action_id,)
            ).fetchone()
            if (
                action is None
                or action["action_type"] != "submit_sbc"
                or action["status"] != "running"
            ):
                connection.rollback()
                raise FC27Error(
                    "SBC_SUBMIT_CHECKPOINT_CONFLICT",
                    "The running SBC submit action was not found for checkpointing.",
                )
            batch = connection.execute(
                "SELECT * FROM action_batches WHERE batch_id = ?",
                (action["batch_id"],),
            ).fetchone()
            params = json.loads(action["params_json"])
            solution = connection.execute(
                "SELECT * FROM sbc_solutions WHERE solution_id = ?",
                (params["solution_id"],),
            ).fetchone()
            current_sync_id = connection.execute(
                "SELECT last_full_sync_id FROM account_state WHERE persona_id = ?",
                (self.persona_id,),
            ).fetchone()[0]
            expected_item_ids = [int(value) for value in params["item_ids"]]
            solution_items = connection.execute(
                """SELECT slot_index, item_id FROM sbc_solution_items
                   WHERE solution_id = ? ORDER BY slot_index""",
                (params["solution_id"],),
            ).fetchall()
            persisted_item_ids = [int(row["item_id"]) for row in solution_items]
            persisted_slot_indices = [
                int(row["slot_index"]) for row in solution_items
            ]
            saved_slot_indices = [
                int(value) for value in response.get("saved_slot_indices") or []
            ]
            set_value = response.get("set") or {}
            challenge = response.get("challenge") or {}
            if (
                batch is None
                or batch["status"] != "running"
                or int(batch["expected_sync_id"]) != expected_sync_id
                or current_sync_id != expected_sync_id
                or solution is None
                or solution["status"] != "saved"
                or saved_item_ids != expected_item_ids
                or persisted_item_ids != expected_item_ids
                or saved_slot_indices != persisted_slot_indices
                or str(set_value.get("id")) != str(params["set_id"])
                or str(challenge.get("id")) != str(params["challenge_id"])
            ):
                connection.rollback()
                raise FC27Error(
                    "SBC_SUBMIT_CHECKPOINT_CONFLICT",
                    "The submit action, saved solution, sync, or fresh squad changed before checkpointing.",
                )
            checkpoint = {
                "phase": "pre_submit_verified",
                "expected_sync_id": expected_sync_id,
                "pre_submit": response,
                "saved_item_ids": saved_item_ids,
                "saved_slot_indices": saved_slot_indices,
            }
            updated = connection.execute(
                """UPDATE actions SET result_json = ?
                   WHERE action_id = ? AND status = 'running'""",
                (
                    json.dumps(
                        checkpoint,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    action_id,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise FC27Error(
                    "SBC_SUBMIT_CHECKPOINT_CONFLICT",
                    "The submit action changed during checkpoint persistence.",
                )
            connection.commit()
        return checkpoint

    def require_new_sbc_save_attempt(self, solution_id):
        with self.connect() as connection:
            solution = connection.execute(
                "SELECT status FROM sbc_solutions WHERE solution_id = ?",
                (solution_id,),
            ).fetchone()
            if solution is None:
                raise FC27Error(
                    "SBC_SOLUTION_NOT_FOUND", f"SBC solution {solution_id} was not found."
                )
            if solution["status"] != "validated":
                raise FC27Error(
                    "SBC_SAVE_STATE_CONFLICT",
                    f"SBC solution status {solution['status']} cannot start a new save.",
                    recovery="Use the recorded saved action or generate a new solution.",
                )
            prior = connection.execute(
                """SELECT action_id, status, error_code FROM actions
                   WHERE action_type = 'save_sbc_squad'
                     AND json_extract(params_json, '$.solution_id') = ?
                     AND (
                       status IN ('pending', 'running', 'complete')
                       OR error_code IN (
                         'BRIDGE_TIMEOUT',
                         'SBC_SAVE_OUTCOME_UNKNOWN',
                         'SBC_SAVE_READBACK_PENDING'
                       )
                     )
                   ORDER BY sequence_no DESC LIMIT 1""",
                (solution_id,),
            ).fetchone()
        if prior is not None:
            raise FC27Error(
                "SBC_SAVE_ALREADY_ATTEMPTED",
                "This SBC solution already has an active, completed, or ambiguous save action.",
                recovery="Replay the original batch or reconcile its original action_id. Do not send another save.",
                details={
                    "action_id": prior["action_id"],
                    "status": prior["status"],
                    "error_code": prior["error_code"],
                },
            )

    def require_new_sbc_submit_attempt(self, solution_id):
        with self.connect() as connection:
            solution = connection.execute(
                "SELECT status FROM sbc_solutions WHERE solution_id = ?",
                (solution_id,),
            ).fetchone()
            if solution is None:
                raise FC27Error(
                    "SBC_SOLUTION_NOT_FOUND", f"SBC solution {solution_id} was not found."
                )
            prior = connection.execute(
                """SELECT action_id, batch_id, status, error_code FROM actions
                   WHERE action_type = 'submit_sbc'
                     AND json_extract(params_json, '$.solution_id') = ?
                     AND (
                       status IN ('pending', 'running', 'complete')
                       OR error_code IN (
                         'BRIDGE_TIMEOUT',
                         'PAGE_BRIDGE_TIMEOUT',
                         'EA_SERVICE_TIMEOUT',
                         'SBC_SUBMIT_OUTCOME_UNKNOWN',
                         'SBC_SUBMIT_READBACK_PENDING',
                         'SBC_SUBMIT_STILL_UNKNOWN'
                       )
                     )
                   ORDER BY started_at DESC, action_id DESC LIMIT 1""",
                (solution_id,),
            ).fetchone()
        if prior is not None:
            raise FC27Error(
                "SBC_SUBMIT_ALREADY_ATTEMPTED",
                "This SBC solution already has an active, completed, or unresolved submit action.",
                recovery="Replay or reconcile the original action_id. Do not submit through a new batch.",
                details={
                    "action_id": prior["action_id"],
                    "batch_id": prior["batch_id"],
                    "status": prior["status"],
                    "error_code": prior["error_code"],
                },
            )

    def require_completed_sbc_save(
        self, solution_id, expected_sync_id, set_id, challenge_id, item_ids
    ):
        expected_item_ids = [int(value) for value in item_ids]
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT a.action_id, a.params_json, a.result_json,
                          b.batch_id, b.expected_sync_id
                   FROM actions AS a
                   JOIN action_batches AS b ON b.batch_id = a.batch_id
                   WHERE a.action_type = 'save_sbc_squad'
                     AND a.status = 'complete'
                     AND b.status = 'complete'
                     AND json_extract(a.params_json, '$.solution_id') = ?
                   ORDER BY a.finished_at DESC""",
                (solution_id,),
            ).fetchall()
            solution_items = connection.execute(
                """SELECT slot_index, item_id FROM sbc_solution_items
                   WHERE solution_id = ? ORDER BY slot_index""",
                (solution_id,),
            ).fetchall()
        expected_slot_indices = [
            int(row["slot_index"]) for row in solution_items
        ]
        for row in rows:
            params = json.loads(row["params_json"])
            result = json.loads(row["result_json"]) if row["result_json"] else {}
            if (
                int(row["expected_sync_id"]) != expected_sync_id
                or str(params.get("set_id")) != str(set_id)
                or str(params.get("challenge_id")) != str(challenge_id)
                or [int(value) for value in params.get("item_ids") or []]
                != expected_item_ids
                or [int(value) for value in result.get("saved_item_ids") or []]
                != expected_item_ids
                or [int(value) for value in result.get("saved_slot_indices") or []]
                != expected_slot_indices
                or result.get("saved_at_sync_id") != expected_sync_id
                or result.get("source") != "ea_webapp_fresh"
            ):
                continue
            self._require_trusted_sbc_readback(result.get("ea") or {})
            return {
                "action_id": row["action_id"],
                "batch_id": row["batch_id"],
            }
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT a.action_id, a.params_json, a.result_json, a.batch_id
                   FROM actions AS a
                   WHERE a.action_type = 'submit_sbc'
                     AND a.status = 'failed'
                     AND a.error_code = 'SBC_SUBMIT_CONFIRMED_NOT_APPLIED'
                     AND json_extract(a.params_json, '$.solution_id') = ?
                   ORDER BY a.finished_at DESC""",
                (solution_id,),
            ).fetchall()
        for row in rows:
            params = json.loads(row["params_json"])
            result = json.loads(row["result_json"]) if row["result_json"] else {}
            saved_squad = result.get("saved_squad") or {}
            try:
                saved_item_ids = self._require_trusted_sbc_readback(saved_squad)
            except FC27Error:
                continue
            if (
                str(params.get("set_id")) != str(set_id)
                or str(params.get("challenge_id")) != str(challenge_id)
                or [int(value) for value in params.get("item_ids") or []]
                != expected_item_ids
                or result.get("submitted_at_sync_id") != expected_sync_id
                or saved_item_ids != expected_item_ids
                or [
                    int(value)
                    for value in saved_squad.get("saved_slot_indices") or []
                ]
                != expected_slot_indices
            ):
                continue
            return {
                "action_id": row["action_id"],
                "batch_id": row["batch_id"],
                "source": "confirmed_not_applied_submit",
            }
        raise FC27Error(
            "SBC_SAVE_AUDIT_INCOMPLETE",
            "SBC submission requires a matching completed save action and completed batch.",
            recovery="Finish or reconcile the original save audit before submitting.",
        )

    def mark_sbc_solution_submitted(
        self,
        solution_id,
        expected_sync_id,
        set_id,
        challenge_id,
        item_ids,
        evidence,
    ):
        expected_item_ids = [int(value) for value in item_ids]
        sync = evidence.get("sync") or {}
        submitted_sync_id = sync.get("sync_id")
        if sync.get("complete") is not True or not isinstance(submitted_sync_id, int):
            raise FC27Error(
                "SBC_SUBMIT_READBACK_FAILED",
                "SBC submission requires a newer complete post-submit synchronization.",
            )
        post_submit = evidence.get("post_submit") or {}
        pre_submit = evidence.get("pre_submit") or {}
        self._require_trusted_sbc_submission_state(post_submit)
        saved_item_ids = self._require_trusted_sbc_readback(pre_submit)
        saved_slot_indices = [
            int(value) for value in pre_submit.get("saved_slot_indices") or []
        ]
        submitted_slot_indices = [
            int(value)
            for value in evidence.get("submitted_slot_indices") or []
        ]
        if saved_item_ids != expected_item_ids:
            raise FC27Error(
                "SBC_SUBMIT_STATE_INVALID",
                "The pre-submit squad no longer matches the confirmed item order.",
            )
        if (
            str((pre_submit.get("set") or {}).get("id")) != str(set_id)
            or str((pre_submit.get("challenge") or {}).get("id"))
            != str(challenge_id)
            or str((post_submit.get("set") or {}).get("id")) != str(set_id)
            or str((post_submit.get("challenge") or {}).get("id"))
            != str(challenge_id)
        ):
            raise FC27Error(
                "SBC_SUBMIT_STATE_INVALID",
                "The pre-submit or post-submit set/challenge identity does not match the action.",
            )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = connection.execute(
                "SELECT last_full_sync_id FROM account_state WHERE persona_id = ?",
                (self.persona_id,),
            ).fetchone()
            current_sync_id = account["last_full_sync_id"] if account else None
            present_item_ids = [
                int(row["item_id"])
                for row in connection.execute(
                    f"SELECT item_id FROM club_items WHERE item_id IN ({','.join('?' for _ in expected_item_ids)}) ORDER BY item_id",
                    expected_item_ids,
                )
            ]
            solution = connection.execute(
                "SELECT status, validation_json FROM sbc_solutions WHERE solution_id = ?",
                (solution_id,),
            ).fetchone()
            solution_items = connection.execute(
                """SELECT slot_index, item_id FROM sbc_solution_items
                   WHERE solution_id = ? ORDER BY slot_index""",
                (solution_id,),
            ).fetchall()
            persisted_item_ids = [int(row["item_id"]) for row in solution_items]
            persisted_slot_indices = [
                int(row["slot_index"]) for row in solution_items
            ]
            if (
                current_sync_id != submitted_sync_id
                or submitted_sync_id <= expected_sync_id
                or solution is None
                or solution["status"] != "saved"
                or persisted_item_ids != expected_item_ids
                or saved_slot_indices != persisted_slot_indices
                or submitted_slot_indices != persisted_slot_indices
            ):
                connection.rollback()
                raise FC27Error(
                    "SBC_SUBMIT_STATE_CONFLICT",
                    "The solution, item order, or post-submit synchronization changed before finalization.",
                )
            progress = self._classify_sbc_submit_outcome(
                pre_submit,
                post_submit,
                expected_item_ids,
                present_item_ids,
            )
            removal_evidence = self._sbc_removed_item_evidence(
                connection, expected_item_ids, expected_sync_id, submitted_sync_id
            )
            progress.update(removal_evidence)
            if (
                progress["outcome"] == "success"
                and removal_evidence["removed_item_ids"] != sorted(expected_item_ids)
            ):
                connection.rollback()
                raise FC27Error(
                    "SBC_SUBMIT_PROGRESS_UNPROVEN",
                    "Post-submit sync history does not record removal of every confirmed item.",
                    details=progress,
                )
            if progress["outcome"] != "success":
                connection.rollback()
                raise FC27Error(
                    "SBC_SUBMIT_PROGRESS_UNPROVEN",
                    "Post-submit inventory and challenge progress do not prove exactly one completion.",
                    details=progress,
                )
            canonical_evidence = {**evidence, "progress": progress}
            validation = json.loads(solution["validation_json"])
            validation["execution"] = canonical_evidence
            updated = connection.execute(
                """UPDATE sbc_solutions SET status = 'submitted', validation_json = ?
                   WHERE solution_id = ? AND status = 'saved'""",
                (
                    json.dumps(
                        validation,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    solution_id,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise FC27Error(
                    "SBC_SUBMIT_STATE_CONFLICT",
                    "The SBC solution changed during submit finalization.",
                )
            connection.commit()
        evidence["progress"] = progress
        return canonical_evidence

    def mark_sbc_solution_saved(self, solution_id, expected_sync_id, evidence):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = connection.execute(
                "SELECT last_full_sync_id FROM account_state WHERE persona_id = ?",
                (self.persona_id,),
            ).fetchone()
            current_sync_id = account["last_full_sync_id"] if account else None
            if current_sync_id != expected_sync_id:
                connection.rollback()
                raise FC27Error(
                    "STALE_CLUB_STATE",
                    f"Expected sync {expected_sync_id}, current complete sync is {current_sync_id}.",
                    retryable=True,
                    recovery="Regenerate and save the SBC solution against the current complete sync.",
                )
            row = connection.execute(
                "SELECT status, validation_json FROM sbc_solutions WHERE solution_id = ?",
                (solution_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise FC27Error(
                    "SBC_SOLUTION_NOT_FOUND", f"SBC solution {solution_id} was not found."
                )
            if row["status"] != "validated":
                connection.rollback()
                raise FC27Error(
                    "SBC_SAVE_STATE_CONFLICT",
                    f"SBC solution status {row['status']} cannot transition to saved.",
                )
            validation = json.loads(row["validation_json"])
            validation["execution"] = evidence
            updated = connection.execute(
                """UPDATE sbc_solutions SET status = 'saved', validation_json = ?
                   WHERE solution_id = ? AND status = 'validated'""",
                (
                    json.dumps(validation, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                    solution_id,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise FC27Error(
                    "SBC_SAVE_STATE_CONFLICT",
                    "The SBC solution changed during save finalization.",
                )
            connection.commit()

    def update_sbc_solution_status(self, solution_id, status, evidence):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT validation_json FROM sbc_solutions WHERE solution_id = ?",
                (solution_id,),
            ).fetchone()
            if row is None:
                raise FC27Error("SBC_SOLUTION_NOT_FOUND", f"SBC solution {solution_id} was not found.")
            validation = json.loads(row["validation_json"])
            validation["execution"] = evidence
            connection.execute(
                "UPDATE sbc_solutions SET status = ?, validation_json = ? WHERE solution_id = ?",
                (
                    status,
                    json.dumps(validation, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                    solution_id,
                ),
            )
            connection.commit()

    @staticmethod
    def _decode_sbc_set(row):
        value = dict(row)
        raw = json.loads(value.pop("raw_json"))
        value.update(raw)
        return value

    @staticmethod
    def _decode_sbc_challenge(row):
        value = dict(row)
        requirements = json.loads(value.pop("requirements_json"))
        raw = json.loads(value.pop("raw_json"))
        value["repeatable"] = bool(value["repeatable"])
        value.update(requirements)
        value.update(raw)
        return value

    def _validate_schema(self, connection):
        try:
            version = connection.execute(
                "SELECT value FROM runtime_meta WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.OperationalError as error:
            raise FC27Error(
                "RUNTIME_SCHEMA_INVALID",
                f"Runtime database schema is missing required tables: {error}",
                recovery="Move the invalid database aside and create a new Persona runtime database.",
            ) from error
        if version is None or version[0] != RUNTIME_SCHEMA_VERSION:
            raise FC27Error(
                "RUNTIME_SCHEMA_INVALID",
                f"Expected runtime schema {RUNTIME_SCHEMA_VERSION}, found {version[0] if version else 'missing'}.",
                recovery="Use a runtime database created by the current fc27d version.",
            )


class RuntimeManager:
    def __init__(self, accounts_root):
        self.accounts_root = Path(accounts_root)
        self.active = None
        candidates = sorted(self.accounts_root.glob("*/runtime.sqlite"))
        if len(candidates) == 1:
            runtime = RuntimeDB(candidates[0], candidates[0].parent.name)
            runtime.validate_existing()
            self.active = runtime

    def activate(self, identity):
        persona_id = str(identity.get("persona_id") or "")
        if not persona_id or not PERSONA_ID_PATTERN.fullmatch(persona_id):
            raise FC27Error(
                "INVALID_PERSONA_ID",
                "EA Persona ID is empty or contains unsupported path characters.",
                recovery="Re-read identity from the authenticated FC27 Web App session.",
            )
        platform = str(identity.get("platform") or "").lower()
        if platform not in ("pc", "ps5"):
            raise FC27Error(
                "INVALID_PLATFORM",
                f"Unsupported FC27 platform: {platform or 'missing'}.",
                recovery="Re-read identity from the selected EA Persona.",
            )
        normalized = {**identity, "persona_id": persona_id, "platform": platform}
        runtime = RuntimeDB(self.accounts_root / persona_id / "runtime.sqlite", persona_id)
        summary = runtime.initialize(normalized)
        self.active = runtime
        return summary

    def status(self):
        return self.active.account_summary() if self.active else None
