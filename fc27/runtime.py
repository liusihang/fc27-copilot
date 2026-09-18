import re
import sqlite3
import json
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

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

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
                change_type = None
                details = None
                if old is None:
                    change_type = "added"
                elif old["location"] != item["location"]:
                    change_type = "moved"
                else:
                    changed = {
                        key: [old[key], value]
                        for key, value in (
                            ("card_ea_id", item["card_ea_id"]),
                            ("tradeable", 1 if item["tradeable"] else 0),
                            ("loan_uses_remaining", item.get("loan_uses_remaining")),
                        )
                        if old[key] != value
                    }
                    if changed:
                        change_type = "attributes_changed"
                        details = json.dumps(changed, separators=(",", ":"), sort_keys=True)
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

            removed_ids = sorted(set(old_items) - set(new_items))
            for item_id in removed_ids:
                old = old_items[item_id]
                connection.execute(
                    """INSERT INTO inventory_changes(
                         sync_id, item_id, change_type, from_location, to_location
                       ) VALUES (?, ?, 'removed', ?, NULL)""",
                    (sync_id, item_id, old["location"]),
                )
                connection.execute("DELETE FROM club_items WHERE item_id = ?", (item_id,))

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
            "item_count": len(new_items),
            "added": len(set(new_items) - set(old_items)),
            "removed": len(removed_ids),
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
