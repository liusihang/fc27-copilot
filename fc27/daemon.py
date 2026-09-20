import json
import os
import signal
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .bridge import BrowserBridge
from .auto_sync import AutoSyncCoordinator
from .account import AccountReader
from .actions import ActionDispatcher
from .catalog import CatalogDB
from .content import ContentService, FutggContentClient
from .errors import FC27Error
from .execution import ExecutionService
from .mcp import MCPServer
from .market import MarketService
from .policy import PolicyStore
from .runtime import RuntimeManager
from .sbc import SbcService
from .squad import SquadService


MAX_REQUEST_BYTES = 1024 * 1024
BROWSER_POLL_SECONDS = 10


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class FC27Daemon:
    def __init__(
        self,
        catalog_path,
        web_root,
        bridge=None,
        accounts_root=None,
        policy_path=None,
    ):
        self.catalog = CatalogDB(catalog_path)
        self.web_root = Path(web_root)
        self.bridge = bridge or BrowserBridge()
        self.accounts = RuntimeManager(
            accounts_root or Path(catalog_path).resolve().parent / "accounts"
        )
        project_root = Path(__file__).resolve().parents[1]
        self.policy = PolicyStore(policy_path or project_root / "policy.json")
        self.content = ContentService()
        self.squads = SquadService()
        self.futgg_content = FutggContentClient()
        self.mcp = MCPServer(self)
        self._execution_lock = threading.Lock()
        self.auto_sync = AutoSyncCoordinator(self._sync_full, self._execution_lock)

    def health(self):
        catalog_validation = self.catalog.validate()
        return {
            "ok": catalog_validation["ok"],
            "observed_at": utc_now(),
            "catalog": catalog_validation,
            "catalog_meta": self.catalog.metadata(),
            "browser_bridge": self.bridge.health(),
            "auto_sync": self.auto_sync.health(),
            "account": self.accounts.status(),
        }

    def browser_event(self, event):
        return self.auto_sync.accept(event)

    def rpc(self, request):
        method = request.get("method")
        params = request.get("params") or {}
        if method == "status":
            return self.health()
        if method == "catalog_query":
            return self.catalog.query(params)
        if method == "reconcile_sbc_save":
            if not self.accounts.active:
                raise FC27Error(
                    "ACCOUNT_NOT_INITIALIZED",
                    "No EA Persona runtime database has been selected yet.",
                )
            action_id = params.get("action_id")
            if not action_id:
                raise FC27Error("INVALID_REQUEST", "reconcile_sbc_save requires params.action_id")
            with self._execution_lock:
                target = self.accounts.active.sbc_save_reconciliation_target(action_id)
                response = self._browser_tool(
                    "readSavedSbcSquad",
                    {
                        "set_id": target["set_id"],
                        "challenge_id": target["challenge_id"],
                    },
                )
                return self.accounts.active.reconcile_sbc_save_action(action_id, response)
        if method == "verify_sbc_save":
            if not self.accounts.active:
                raise FC27Error(
                    "ACCOUNT_NOT_INITIALIZED",
                    "No EA Persona runtime database has been selected yet.",
                )
            action_id = params.get("action_id")
            if not action_id:
                raise FC27Error("INVALID_REQUEST", "verify_sbc_save requires params.action_id")
            with self._execution_lock:
                target = self.accounts.active.sbc_save_verification_target(action_id)
                response = self._browser_tool(
                    "readSavedSbcSquad",
                    {
                        "set_id": target["set_id"],
                        "challenge_id": target["challenge_id"],
                    },
                )
                return self.accounts.active.verify_sbc_saved_action(action_id, response)
        if method == "reconcile_sbc_submit":
            if not self.accounts.active:
                raise FC27Error(
                    "ACCOUNT_NOT_INITIALIZED",
                    "No EA Persona runtime database has been selected yet.",
                )
            action_id = params.get("action_id")
            if not action_id:
                raise FC27Error(
                    "INVALID_REQUEST", "reconcile_sbc_submit requires params.action_id"
                )
            with self._execution_lock:
                target = self.accounts.active.sbc_submit_reconciliation_target(
                    action_id
                )
                if target.get("resolved"):
                    return target["result"]
                sync = self._sync_full("sbc_submit_reconcile")
                post_submit = self._browser_tool(
                    "readSbcSubmissionState",
                    {
                        "set_id": target["set_id"],
                        "challenge_id": target["challenge_id"],
                    },
                )
                self._sbc_service().capture_challenge(post_submit)
                saved_squad = None
                if len(self.accounts.active.items_by_ids(target["item_ids"])) == len(
                    target["item_ids"]
                ):
                    try:
                        saved_squad = self._browser_tool(
                            "readSavedSbcSquad",
                            {
                                "set_id": target["set_id"],
                                "challenge_id": target["challenge_id"],
                            },
                        )
                    except FC27Error:
                        saved_squad = None
                return self.accounts.active.reconcile_sbc_submit_action(
                    action_id, sync, post_submit, saved_squad
                )
        raise FC27Error(
            "METHOD_NOT_FOUND",
            f"Unknown daemon RPC method: {method}",
            recovery="Use status, catalog_query, reconcile_sbc_save, verify_sbc_save, or reconcile_sbc_submit.",
        )

    def call_tool(self, name, arguments):
        try:
            if name == "status":
                data = self.health()
                policy = self.policy.load()
                data["policy"] = {
                    **policy,
                    "account_writes_enabled": policy["execution_mode"] != "observe",
                }
                if data["browser_bridge"]["connected"]:
                    try:
                        data["ea_session"] = self._browser_tool("getSessionStatus", {})
                    except FC27Error as error:
                        data["ea_session"] = {
                            "authenticated": False,
                            "error": error.as_dict(),
                        }
                else:
                    data["ea_session"] = {
                        "webAppConnected": False,
                        "authenticated": False,
                        "sidCaptured": False,
                        "phishingTokenCaptured": False,
                        "apiBaseUrl": None,
                        "apiHost": None,
                        "gameVersion": None,
                        "capturedAt": None,
                    }
                return self._envelope("fc27d", data)
            if name == "catalog_query":
                return self._envelope("catalog", self.catalog.query(arguments))
            if name == "club_query":
                account = self.accounts.status()
                if account:
                    data = self.accounts.active.query_items(arguments)
                    if arguments.get("include_catalog", True) and data["items"]:
                        card_ids = sorted({item["card_ea_id"] for item in data["items"]})
                        catalog = self.catalog.query(
                            {"card_ea_ids": card_ids, "limit": len(card_ids), "detail": "summary"}
                        )
                        by_id = {card["card_ea_id"]: card for card in catalog["cards"]}
                        for item in data["items"]:
                            item["catalog"] = by_id.get(item["card_ea_id"])
                    return self._envelope("runtime", data)
                raise FC27Error(
                    "ACCOUNT_NOT_INITIALIZED",
                    "No EA Persona runtime database has been selected yet.",
                    retryable=True,
                    recovery="Log in, connect the browser bridge, then call FC27:sync_club.",
                )
            if name == "squad_query":
                if not self.bridge.health()["connected"]:
                    raise FC27Error(
                        "EA_SESSION_REQUIRED",
                        "An authenticated FC27 Web App session is required for squad data.",
                        retryable=True,
                        recovery="Open the FC27 Web App and sign in; the extension connects automatically.",
                    )
                options = self.squads.validate_arguments(arguments)
                raw = self._browser_tool(
                    "getSquads",
                    {
                        "detail": options["detail"],
                        "squad_id": options["squad_id"]
                        if options["selection"] == "exact"
                        else None,
                    },
                )
                return self._envelope(
                    "ea_webapp", self.squads.normalize(raw, options)
                )
            if name == "sync_club":
                if not self.bridge.health()["connected"]:
                    raise FC27Error(
                        "EA_SESSION_REQUIRED",
                        "An authenticated FC27 Web App session and connected browser bridge are required.",
                        retryable=True,
                        recovery="Start fc27d, connect the extension at http://127.0.0.1:3926, log in to FC27, then retry.",
                    )
                mode = arguments.get("mode", "full")
                if mode != "full":
                    raise FC27Error(
                        "TARGETED_SYNC_NOT_READY",
                        "Targeted synchronization is introduced with post-action readback.",
                        recovery="Use mode=full until execution workflows are enabled.",
                    )
                required = ("coins", "club", "storage", "unassigned", "tradepile")
                requested = tuple(arguments.get("areas") or required)
                missing = [area for area in required if area not in requested]
                if missing:
                    raise FC27Error(
                        "INVALID_FULL_SYNC_AREAS",
                        f"Full synchronization requires: {', '.join(missing)}.",
                        recovery="Include coins, club, storage, unassigned, and tradepile.",
                    )
                with self._execution_lock:
                    data = self._sync_full("login_full", requested)
                return self._envelope("ea_webapp", data)
            if name == "market_search":
                if not self.accounts.active:
                    raise FC27Error(
                        "ACCOUNT_NOT_INITIALIZED",
                        "No EA Persona runtime database has been selected yet.",
                        retryable=True,
                        recovery="Run FC27:sync_club after login, then retry market_search.",
                    )
                raw = self._browser_tool("searchTransferMarket", {
                    "definition_id": arguments.get("card_ea_id"),
                    "min_buy_now": arguments.get("min_buy_now"),
                    "max_buy_now": arguments.get("max_buy_now"),
                    "limit": arguments.get("limit", 21),
                })
                data = MarketService(self.accounts.active).record_ea_market_scan(
                    arguments.get("card_ea_id"), raw
                )
                return self._envelope("ea_webapp", data)
            if name == "sbc_query":
                challenge_id = arguments.get("challenge_id")
                set_id = arguments.get("set_id")
                service = self._sbc_service()
                if challenge_id is not None and set_id is None:
                    raise FC27Error(
                        "INVALID_REQUEST",
                        "sbc_query requires set_id when challenge_id is provided.",
                        recovery="Pass the set_id returned by the set or challenge-list query.",
                    )
                return self._envelope(
                    "runtime",
                    service.query(
                        set_id=set_id,
                        challenge_id=challenge_id,
                        include_raw=arguments.get("include_raw", False),
                    ),
                )
            if name == "sbc_refresh":
                challenge_id = arguments.get("challenge_id")
                set_id = arguments.get("set_id")
                include_raw = arguments.get("include_raw", False)
                service = self._sbc_service()
                if challenge_id is not None and set_id is None:
                    raise FC27Error(
                        "INVALID_REQUEST",
                        "sbc_refresh requires set_id when challenge_id is provided.",
                        recovery="Pass the set_id returned by the set or challenge-list query.",
                    )
                if challenge_id is not None:
                    raw = self._browser_tool(
                        "getSbcChallenge",
                        {"challenge_id": challenge_id, "set_id": set_id},
                    )
                    raw = self._enrich_sbc_slot_positions(raw)
                    data = service.capture_challenge(raw, include_raw=include_raw)
                elif set_id is not None:
                    raw = self._browser_tool("getSbcChallenges", {"set_id": set_id})
                    raw = self._enrich_sbc_slot_positions(raw)
                    data = service.capture_challenges(raw, include_raw=include_raw)
                else:
                    raw = self._browser_tool("getSbcSets", {})
                    data = service.capture_sets(raw, include_raw=include_raw)
                return self._envelope("ea_webapp", data)
            if name == "price_context":
                if not self.accounts.active:
                    raise FC27Error(
                        "ACCOUNT_NOT_INITIALIZED",
                        "No EA Persona runtime database has been selected yet.",
                        retryable=True,
                        recovery="Run FC27:sync_club after login, then retry price_context.",
                    )
                data = MarketService(self.accounts.active).price_context(
                    arguments.get("card_ea_ids") or [],
                    arguments.get("history_hours", 72),
                )
                catalog = self.catalog.query(
                    {
                        "card_ea_ids": arguments.get("card_ea_ids") or [],
                        "limit": len(arguments.get("card_ea_ids") or []) or 1,
                        "detail": "summary",
                    }
                )
                catalog_by_id = {
                    card["card_ea_id"]: card for card in catalog["cards"]
                }
                for card in data["cards"]:
                    card["catalog"] = catalog_by_id.get(card["card_ea_id"])
                return self._envelope("futgg", data)
            if name == "content_query":
                options = self.content.validate_arguments(arguments)
                content_type = options["content_type"]
                source = options["source"]
                if source == "futgg" and content_type != "evolution":
                    raise FC27Error(
                        "CONTENT_SOURCE_UNAVAILABLE",
                        "FUT.GG currently exposes a stable manifest dataset for evolutions only.",
                        recovery="Use source=ea for Season or objective account content.",
                    )
                if source == "futgg":
                    raw = self.futgg_content.evolutions(
                        self.content.futgg_scope(options)
                    )
                    return self._envelope(
                        "futgg",
                        self.content.normalize_futgg_evolutions(raw, options),
                    )
                browser_method = {
                    "season": "getObjectives",
                    "objective": "getObjectives",
                    "evolution": "getEvolutions",
                }[content_type]
                if source == "ea":
                    if not self.bridge.health()["connected"]:
                        raise FC27Error(
                            "EA_SESSION_REQUIRED",
                            "An authenticated FC27 Web App session is required for this account content.",
                            retryable=True,
                            recovery="Open the FC27 Web App and sign in; the extension connects automatically.",
                        )
                    raw = self._browser_tool(browser_method, {})
                    return self._envelope(
                        "ea_webapp",
                        self.content.normalize_ea(content_type, raw, options),
                    )
                if content_type == "evolution":
                    ea_raw = None
                    ea_error = None
                    if self.bridge.health()["connected"]:
                        try:
                            ea_raw = self._browser_tool(browser_method, {})
                        except FC27Error as error:
                            ea_error = error
                    futgg_raw = None
                    futgg_error = None
                    try:
                        futgg_raw = self.futgg_content.evolutions(
                            self.content.futgg_scope(options)
                        )
                    except FC27Error as error:
                        futgg_error = error
                    if ea_raw is not None and futgg_raw is not None:
                        return self._envelope(
                            "ea_webapp+futgg",
                            self.content.merge_evolutions(ea_raw, futgg_raw, options),
                        )
                    if ea_raw is not None:
                        data = self.content.normalize_ea(content_type, ea_raw, options)
                        data["source_meta"]["merge_warning"] = {
                            "missing_source": "futgg",
                            "error": futgg_error.as_dict() if futgg_error else None,
                        }
                        return self._envelope("ea_webapp", data, complete=False)
                    if futgg_raw is not None:
                        data = self.content.normalize_futgg_evolutions(futgg_raw, options)
                        data["source_meta"]["merge_warning"] = {
                            "missing_source": "ea",
                            "error": ea_error.as_dict() if ea_error else None,
                        }
                        return self._envelope("futgg", data, complete=False)
                    raise ea_error or futgg_error or FC27Error(
                        "CONTENT_SOURCE_UNAVAILABLE",
                        "Neither EA nor FUT.GG Evolution content is currently available.",
                        retryable=True,
                        recovery="Open the authenticated Web App and verify FUT.GG connectivity before retrying.",
                    )
                if self.bridge.health()["connected"]:
                    raw = self._browser_tool(browser_method, {})
                    return self._envelope(
                        "ea_webapp",
                        self.content.normalize_ea(content_type, raw, options),
                    )
                raise FC27Error(
                    "EA_SESSION_REQUIRED",
                    "An authenticated FC27 Web App session is required for this account content.",
                    retryable=True,
                    recovery="Open the FC27 Web App and sign in; the extension connects automatically.",
                )
            if name == "sbc_solve":
                if not self.accounts.active:
                    raise FC27Error(
                        "ACCOUNT_NOT_INITIALIZED",
                        "No EA Persona runtime database has been selected yet.",
                        retryable=True,
                        recovery="Run FC27:sync_club and FC27:sbc_refresh before solving.",
                    )
                return self._envelope(
                    "runtime",
                    self._sbc_service().solve(
                        arguments.get("set_id"),
                        arguments.get("challenge_id"),
                        arguments.get("objective") or {},
                        arguments.get("max_solutions", 3),
                        purchase_budget=arguments.get("purchase_budget", 0),
                        reserved_item_ids=self._active_squad_item_ids(),
                    ),
                )
            if name == "execute_actions":
                if not self.accounts.active:
                    if self.policy.load()["execution_mode"] == "observe":
                        raise FC27Error(
                            "EXECUTION_DISABLED",
                            "Account actions are disabled while policy mode is observe.",
                            recovery="Review policy values and explicitly change execution_mode before retrying.",
                        )
                    raise FC27Error(
                        "ACCOUNT_NOT_INITIALIZED",
                        "No EA Persona runtime database has been selected yet.",
                        retryable=True,
                        recovery="Run FC27:sync_club after login, then retry the exact batch.",
                    )
                dispatcher = ActionDispatcher(
                    self.bridge, self.accounts.active, self._sync_full, self.catalog
                )
                with self._execution_lock:
                    data = ExecutionService(
                        self.accounts.active, self.policy, dispatcher=dispatcher
                    ).execute(arguments)
                return self._envelope("execution", data)
            raise FC27Error("TOOL_NOT_FOUND", f"Unknown FC27 tool: {name}")
        except FC27Error as error:
            return self._error_envelope(error)
        except Exception as error:
            return self._error_envelope(
                FC27Error(
                    "INTERNAL_ERROR",
                    "fc27d could not complete the tool call.",
                    retryable=False,
                    recovery="Inspect the fc27d stderr log and retry only after resolving the reported failure.",
                    details={"exception": type(error).__name__, "message": str(error)},
                )
            )

    def _sync_full(self, kind="login_full", requested=None):
        required = ("coins", "club", "storage", "unassigned", "tradepile")
        requested = tuple(requested or required)
        reader = AccountReader(self.bridge)
        identity = reader.identity()
        self.accounts.activate(identity)
        runtime = self.accounts.active
        sync_id = runtime.begin_sync(kind)
        results = {}
        for area in requested:
            try:
                result = reader.read_area(area)
                results[area] = result
                runtime.record_sync_part(sync_id, result)
            except FC27Error as error:
                runtime.fail_sync(sync_id, error, area)
                raise
        try:
            summary = runtime.commit_full_sync(sync_id, results, required)
        except FC27Error as error:
            runtime.fail_sync(sync_id, error)
            raise
        reconciled_actions = runtime.reconcile_listing_actions()
        self.auto_sync.note_full_sync()
        return {
            "account": runtime.account_summary(),
            "reconciled_actions": reconciled_actions,
            **summary,
        }

    def _browser_tool(self, method, params):
        response = self.bridge.call(method, params)
        if not response.get("ok", False):
            error = response.get("error") or {}
            raise FC27Error(
                error.get("code") or "EA_REQUEST_FAILED",
                error.get("message") or "The EA Web App request failed.",
                retryable=error.get("status") not in (403, 461),
                recovery="Inspect FC27:status and the Web App before retrying.",
                details=error,
            )
        return response.get("data")

    def _sbc_service(self):
        if not self.accounts.active:
            raise FC27Error(
                "ACCOUNT_NOT_INITIALIZED",
                "No EA Persona runtime database has been selected yet.",
                retryable=True,
                recovery="Run FC27:sync_club after login, then retry the SBC request.",
            )
        return SbcService(self.accounts.active, self.catalog)

    def _active_squad_item_ids(self):
        if not self.bridge.health()["connected"]:
            raise FC27Error(
                "EA_SESSION_REQUIRED",
                "SBC solving requires a fresh active-squad read from the authenticated Web App.",
                retryable=True,
                recovery="Open the FC27 Web App and sign in; the extension connects automatically.",
            )
        options = self.squads.validate_arguments(
            {"selection": "active", "detail": "detailed", "include_options": False}
        )
        raw = self._browser_tool("getSquads", {"detail": "detailed", "squad_id": None})
        normalized = self.squads.normalize(raw, options)
        item_ids = {
            int((slot.get("item") or {}).get("item_id"))
            for squad in normalized["squads"]
            for slot in squad.get("slots") or []
            if slot.get("section") != "manager"
            and (slot.get("item") or {}).get("item_id") is not None
            and (slot.get("item") or {}).get("item_type") in (None, "player")
        }
        return sorted(item_ids)

    def _enrich_sbc_slot_positions(self, payload):
        squad_state = self._browser_tool(
            "getSquads", {"detail": "detailed", "squad_id": None}
        )
        formations = {
            str(value.get("name")): value.get("positions") or []
            for value in (squad_state.get("catalog") or {}).get("formations") or []
            if value.get("name")
        }
        challenges = []
        if isinstance(payload.get("challenge"), dict):
            challenges.append(payload["challenge"])
        challenges.extend(
            value
            for value in payload.get("challenges") or []
            if isinstance(value, dict)
        )
        for challenge in challenges:
            if challenge.get("slot_positions"):
                continue
            positions = formations.get(str(challenge.get("formation")))
            if not positions:
                continue
            challenge["slot_positions"] = [dict(value) for value in positions]
            challenge["slot_positions_source"] = "ea_formation_repository"
        return payload

    def _envelope(self, source, data, complete=True):
        metadata = self.catalog.metadata()
        account = self.accounts.status()
        return {
            "ok": True,
            "meta": {
                "request_id": str(uuid.uuid4()),
                "observed_at": utc_now(),
                "source": source,
                "complete": complete,
                "catalog_snapshot_at": metadata.get("snapshot_finished_at"),
                "club_sync_id": account.get("last_full_sync_id") if account else None,
            },
            "data": data,
        }

    def _error_envelope(self, error):
        metadata = self.catalog.metadata()
        account = self.accounts.status()
        return {
            "ok": False,
            "meta": {
                "request_id": str(uuid.uuid4()),
                "observed_at": utc_now(),
                "source": "fc27d",
                "complete": False,
                "catalog_snapshot_at": metadata.get("snapshot_finished_at"),
                "club_sync_id": account.get("last_full_sync_id") if account else None,
            },
            "error": error.as_dict(),
        }


class FC27HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, daemon):
        super().__init__(address, FC27RequestHandler)
        self.fc27 = daemon


class FC27RequestHandler(BaseHTTPRequestHandler):
    server_version = "fc27d/0.1"

    def do_GET(self):
        try:
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._serve_bridge_page()
                return
            if path == "/health":
                self._json(200, self.server.fc27.health())
                return
            if path == "/browser/poll":
                envelope = self.server.fc27.bridge.poll(BROWSER_POLL_SECONDS)
                if envelope is None:
                    self.send_response(204)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                else:
                    try:
                        self._json(200, envelope)
                    except (BrokenPipeError, ConnectionResetError):
                        self.server.fc27.bridge.requeue(envelope)
                return
            self._json(404, {"ok": False, "error": {"code": "NOT_FOUND"}})
        except FC27Error as error:
            self._json(503, {"ok": False, "error": error.as_dict()})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self._internal_error()

    def do_POST(self):
        try:
            payload = self._read_json()
            if self.path == "/browser/respond":
                accepted = self.server.fc27.bridge.respond(
                    payload.get("request_id"), payload.get("payload")
                )
                if not accepted:
                    raise FC27Error(
                        "UNKNOWN_REQUEST",
                        "The bridge request is unknown or already expired.",
                    )
                self._json(200, {"ok": True})
                return
            if self.path == "/browser/event":
                result = self.server.fc27.browser_event(payload)
                self._json(202, {"ok": True, "data": result})
                return
            if self.path == "/rpc":
                self._json(200, {"ok": True, "data": self.server.fc27.rpc(payload)})
                return
            if self.path == "/mcp":
                self._json(200, self.server.fc27.mcp.handle(payload))
                return
            self._json(404, {"ok": False, "error": {"code": "NOT_FOUND"}})
        except FC27Error as error:
            self._json(400, {"ok": False, "error": error.as_dict()})
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            self._json(
                400,
                {
                    "ok": False,
                    "error": {"code": "INVALID_JSON", "message": str(error), "retryable": False},
                },
            )
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self._internal_error()

    def log_message(self, message_format, *args):
        print(f"[fc27d] {self.address_string()} {message_format % args}")

    def _serve_bridge_page(self):
        path = self.server.fc27.web_root / "index.html"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_REQUEST_BYTES:
            raise FC27Error("REQUEST_TOO_LARGE", "Request body exceeds 1 MiB.")
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def _json(self, status, value):
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _internal_error(self):
        self._json(
            500,
            {
                "ok": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "fc27d could not complete the local request.",
                    "retryable": False,
                    "recovery": "Inspect the fc27d stderr log for the underlying exception.",
                },
            },
        )


def serve(host=None, port=None, catalog_path=None, web_root=None):
    project_root = Path(__file__).resolve().parents[1]
    host = host or os.environ.get("FC27D_HOST", "127.0.0.1")
    port = int(port or os.environ.get("FC27D_PORT", "3926"))
    catalog_path = Path(catalog_path or project_root / "data" / "catalog.sqlite")
    web_root = Path(web_root or project_root / "web")
    daemon = FC27Daemon(catalog_path, web_root)
    server = FC27HTTPServer((host, port), daemon)

    def stop_server(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop_server)
    signal.signal(signal.SIGTERM, stop_server)
    print(f"[fc27d] listening on http://{host}:{server.server_address[1]}")
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
