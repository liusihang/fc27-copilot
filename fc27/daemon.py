import json
import os
import signal
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .bridge import BrowserBridge
from .account import AccountReader
from .catalog import CatalogDB
from .errors import FC27Error
from .mcp import MCPServer
from .runtime import RuntimeManager


MAX_REQUEST_BYTES = 1024 * 1024


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class FC27Daemon:
    def __init__(self, catalog_path, web_root, bridge=None, accounts_root=None):
        self.catalog = CatalogDB(catalog_path)
        self.web_root = Path(web_root)
        self.bridge = bridge or BrowserBridge()
        self.accounts = RuntimeManager(
            accounts_root or Path(catalog_path).resolve().parent / "accounts"
        )
        self.mcp = MCPServer(self)
        self._catalog_refresh_lock = threading.Lock()

    def health(self):
        catalog_validation = self.catalog.validate()
        return {
            "ok": catalog_validation["ok"],
            "observed_at": utc_now(),
            "catalog": catalog_validation,
            "catalog_meta": self.catalog.metadata(),
            "browser_bridge": self.bridge.health(),
            "account": self.accounts.status(),
        }

    def rpc(self, request):
        method = request.get("method")
        params = request.get("params") or {}
        if method == "status":
            return self.health()
        if method == "catalog_query":
            return self.catalog.query(params)
        if method == "browser_call":
            browser_method = params.get("method")
            if not browser_method:
                raise FC27Error("INVALID_REQUEST", "browser_call requires params.method")
            return self.bridge.call(
                browser_method,
                params.get("params") or {},
                timeout_seconds=min(max(float(params.get("timeout_seconds", 30)), 1), 60),
            )
        raise FC27Error(
            "METHOD_NOT_FOUND",
            f"Unknown daemon RPC method: {method}",
            recovery="Use status, catalog_query, or browser_call.",
        )

    def call_tool(self, name, arguments):
        try:
            if name == "status":
                return self._envelope("fc27d", self.health())
            if name == "catalog_query":
                return self._envelope("catalog", self.catalog.query(arguments))
            if name == "catalog_refresh":
                return self._envelope("futgg", self._refresh_catalog())
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
                reader = AccountReader(self.bridge)
                identity = reader.identity()
                account = self.accounts.activate(identity)
                runtime = self.accounts.active
                sync_id = runtime.begin_sync("login_full")
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
                return self._envelope(
                    "ea_webapp", {"account": runtime.account_summary(), **summary}
                )
            if name == "market_search":
                data = self._browser_tool("searchTransferMarket", {
                    "definition_id": arguments.get("card_ea_id"),
                    "min_buy_now": arguments.get("min_buy_now"),
                    "max_buy_now": arguments.get("max_buy_now"),
                    "limit": arguments.get("limit", 21),
                })
                return self._envelope("ea_webapp", data)
            if name == "sbc_query":
                challenge_id = arguments.get("challenge_id")
                method = "getSbcChallenge" if challenge_id is not None else "getSbcSets"
                params = {"challenge_id": challenge_id} if challenge_id is not None else {}
                return self._envelope("ea_webapp", self._browser_tool(method, params))
            if name == "price_context":
                raise FC27Error(
                    "PRICE_CONTEXT_NOT_READY",
                    "Reference price persistence is scheduled for Milestone M4.",
                    recovery="Use FC27:catalog_query for card facts until price_context is implemented.",
                )
            if name == "sbc_solve":
                raise FC27Error(
                    "SBC_SCHEMA_UNSUPPORTED",
                    "No authenticated FC27 SBC requirement schema has been captured yet.",
                    recovery="Complete read-only SBC capture before requesting solutions.",
                )
            if name == "execute_actions":
                raise FC27Error(
                    "EXECUTION_DISABLED",
                    "Account actions are disabled while policy mode is observe.",
                    recovery="Complete read-only acceptance before separately enabling suggest mode.",
                )
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

    def _refresh_catalog(self):
        if not self._catalog_refresh_lock.acquire(blocking=False):
            raise FC27Error("CATALOG_REFRESH_RUNNING", "A catalog refresh is already running.", retryable=True)
        try:
            from scripts.refresh_catalog import refresh_catalog

            return refresh_catalog(self.catalog.path)
        finally:
            self._catalog_refresh_lock.release()

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
                envelope = self.server.fc27.bridge.poll()
                if envelope is None:
                    self.send_response(204)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                else:
                    self._json(200, envelope)
                return
            self._json(404, {"ok": False, "error": {"code": "NOT_FOUND"}})
        except FC27Error as error:
            self._json(503, {"ok": False, "error": error.as_dict()})
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
