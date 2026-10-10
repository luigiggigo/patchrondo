"""Local interface served with the Python standard library.

The server binds to the loopback interface only, rejects foreign Host and
Origin headers (DNS rebinding, CSRF), requires a per-session token for every
API call and accepts writes only as bounded JSON. The page and its scripts,
styles and images are files of this package: nothing is loaded from elsewhere
and the page is served with a nonce-based Content-Security-Policy.

Every operation is implemented in `app.py`; this module only maps HTTP to it.
Runs are separate `patchrondo run` processes, so the lock, checkpoint and
pause/resume behavior of the engine applies unchanged and a run continues if
the interface stops.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
from urllib.parse import parse_qs, unquote, urlsplit
import webbrowser

from .app import PACKAGE_ROOT, App, Conflict, RunsAtExit, browse, inspect_repository  # noqa: F401 - re-exported
from .commands import join_command, split_command  # noqa: F401 - re-exported
from .live import Hub
from .workspace import NotFound

MAX_BODY = 256_000
HEARTBEAT_SECONDS = 10
ASSET_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
               ".webp": "image/webp", ".svg": "image/svg+xml"}
CSP = ("default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
       "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")
PROJECT = r"/api/projects/(P-[a-f0-9]{8})"
TASK = PROJECT + r"/tasks/([^/]+)"


def _load_assets() -> tuple[str, dict[str, tuple[bytes, str]]]:
    """Read the page and its assets once. Requests can only name a file collected here."""
    static = resources.files("patchrondo").joinpath("static")
    assets: dict[str, tuple[bytes, str]] = {}

    def walk(folder, prefix: str) -> None:
        for item in folder.iterdir():
            if item.is_dir():
                walk(item, f"{prefix}{item.name}/")
            elif (suffix := os.path.splitext(item.name)[1]) in ASSET_TYPES:
                assets[prefix + item.name] = (item.read_bytes(), ASSET_TYPES[suffix])

    walk(static, "")
    return static.joinpath("index.html").read_text(encoding="utf-8"), assets


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True  # idle connections and event streams never delay shutdown; run starts are awaited
    # On Windows SO_REUSEADDR would let a second server share a port already in use.
    allow_reuse_address = os.name != "nt"

    def __init__(self, home: Path, port: int = 0, run_command=None, token: str | None = None):
        super().__init__(("127.0.0.1", port), DashboardHandler)
        self.home = Path(home)
        self.token = token or secrets.token_urlsafe(24)
        self.app = App(self.home, run_command=run_command)
        self.hub = Hub(self.app)
        self.page, self.assets = _load_assets()
        self.reload_assets = os.environ.get("PATCHRONDO_DEV_ASSETS") == "1"  # development: edit without restart

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/#token={self.token}"

    def close_runs(self, timeout: float = 30) -> RunsAtExit:
        """Refuse new run starts, wait for those in progress and report what may still run."""
        self.hub.close()
        return self.app.close_runs(timeout)

    def handle_error(self, request, client_address) -> None:
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return  # the browser closed the connection or went silent: nothing to report
        super().handle_error(request, client_address)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer
    server_version = "PatchRondo"
    sys_version = ""
    timeout = 30  # seconds; a silent connection is dropped instead of holding a thread

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass  # keep the terminal quiet; requests can reveal task titles

    # --- responses ------------------------------------------------------------------

    def _headers(self, status: int, content_type: str, length: int | None, nonce: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP.format(nonce=nonce or secrets.token_urlsafe(16)))
        self.end_headers()

    def _send(self, status: int, body: bytes, content_type: str, nonce: str = "") -> None:
        self._headers(status, content_type, len(body), nonce)
        self.wfile.write(body)

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str, code: str) -> None:
        self._json(status, {"error": message, "code": code})

    # --- checks ---------------------------------------------------------------------

    def _local_host(self) -> bool:
        port = self.server.server_address[1]
        if self.headers.get("Host", "") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            self._error(403, "Unexpected Host header", "forbidden")
            return False
        return True

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-PatchRondo-Token", "")
        if not secrets.compare_digest(supplied.encode(), self.server.token.encode()):
            self._error(401, "Missing or invalid session token", "unauthorized")
            return False
        return True

    def _guarded(self, action) -> None:
        """Run one API action and translate its outcome uniformly."""
        try:
            status, body = action()
            self._json(status, body)
        except (NotFound, FileNotFoundError) as exc:
            self._error(404, str(exc) or "Not found", "not_found")
        except Conflict as exc:
            self._error(409, str(exc), "conflict")
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._error(400, str(exc), "invalid")
        except (RuntimeError, OSError, sqlite3.Error) as exc:
            self._error(400, str(exc), "failed")
        except Exception as exc:  # noqa: BLE001 - an unexpected fault still gets an answer in the same shape
            print(f"PatchRondo: unexpected {type(exc).__name__} while handling {self.command} {urlsplit(self.path).path}: {exc}",
                  file=sys.stderr)
            self._error(500, "PatchRondo hit an unexpected error; details are in the terminal that runs it", "internal")

    # --- reads ----------------------------------------------------------------------

    def do_GET(self) -> None:
        if not self._local_host():
            return
        url = urlsplit(self.path)
        path = url.path
        if path == "/":
            nonce = secrets.token_urlsafe(16)
            page = _load_assets()[0] if self.server.reload_assets else self.server.page
            self._send(200, page.replace("__NONCE__", nonce).encode("utf-8"), "text/html; charset=utf-8", nonce)
            return
        if path.startswith("/assets/"):
            assets = _load_assets()[1] if self.server.reload_assets else self.server.assets
            asset = assets.get(unquote(path[len("/assets/"):]))
            if asset is None:
                self._error(404, "Not found", "not_found")
            else:
                self._send(200, asset[0], asset[1])
            return
        if not path.startswith("/api/"):
            self._error(404, "Not found", "not_found")
            return
        if not self._authorized():
            return
        if path == "/api/events":
            self._events(parse_qs(url.query))
            return
        self._guarded(lambda: self._read(path, parse_qs(url.query)))

    def _read(self, path: str, query: dict) -> tuple[int, dict]:
        app, hub = self.server.app, self.server.hub
        if path == "/api/snapshot":
            return 200, {**hub.snapshot(), "about": app.about()}
        if path == "/api/settings":
            return 200, app.ws.settings()
        if path == "/api/providers":
            return 200, app.providers.view()
        if match := re.fullmatch(PROJECT, path):
            return 200, app.project_detail(app.ws.get(match.group(1)))
        if match := re.fullmatch(TASK, path):
            return 200, app.task_detail(app.ws.get(match.group(1)), unquote(match.group(2)))
        if match := re.fullmatch(TASK + "/files", path):
            return 200, app.task_files(app.ws.get(match.group(1)), unquote(match.group(2)))
        if match := re.fullmatch(TASK + "/log", path):
            def first(name: str, default: str) -> str:
                return (query.get(name) or [default])[0]
            return 200, app.log_chunk(app.ws.get(match.group(1)), unquote(match.group(2)), first("file", "run"),
                                      first("offset", "0"), first("limit", "200000"))
        raise FileNotFoundError("Not found")

    def _events(self, query: dict) -> None:
        """Server-sent events: numbered changes after `since`, with heartbeats; ends with "resync" when lost."""
        hub = self.server.hub
        try:
            since = int((query.get("since") or ["0"])[0])
        except ValueError:
            since = -1
        epoch = (query.get("epoch") or [""])[0]
        self._headers(200, "text/event-stream; charset=utf-8", None)
        hub.subscribe()
        try:
            while not hub.closed:
                events = hub.wait(epoch, since, HEARTBEAT_SECONDS)
                if events is None:
                    self.wfile.write(b"event: resync\ndata: {}\n\n")
                    self.wfile.flush()
                    return
                if not events and not hub.closed:
                    self.wfile.write(b": keep-alive\n\n")
                for event in events:
                    since = event["seq"]
                    data = json.dumps(event, ensure_ascii=False)
                    self.wfile.write(f"id: {since}\ndata: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
        except OSError:
            pass  # the client went away
        finally:
            hub.unsubscribe()

    # --- writes ---------------------------------------------------------------------

    def _payload(self) -> dict | None:
        """Common checks for every write: token, Host, Origin, JSON content type and a bounded body."""
        if not self._local_host() or not self._authorized():
            return None
        port = self.server.server_address[1]
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
            self._error(403, "Unexpected Origin header", "forbidden")
            return None
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self._error(415, "Content-Type must be application/json", "invalid")
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self._error(413, "Invalid request size", "invalid")
            return None
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        if not isinstance(payload, dict):
            self._error(400, "Request body must be a JSON object", "invalid")
            return None
        return payload

    def do_POST(self) -> None:
        payload = self._payload()
        if payload is not None:
            self._guarded(lambda: self._post(urlsplit(self.path).path, payload))

    def do_PATCH(self) -> None:
        payload = self._payload()
        if payload is not None:
            self._guarded(lambda: self._patch(urlsplit(self.path).path, payload))

    def do_DELETE(self) -> None:
        payload = self._payload()
        if payload is not None:
            self._guarded(lambda: self._delete(urlsplit(self.path).path))

    def _post(self, path: str, payload: dict) -> tuple[int, dict]:
        app = self.server.app
        if path == "/api/projects":
            return 201, app.add_project(payload)
        if path == "/api/projects/import":
            return 201, app.import_project(payload)
        if path == "/api/projects/inspect":
            return 200, inspect_repository(app.ws, payload.get("path"))
        if path == "/api/fs/list":
            return 200, browse(payload.get("path"))
        if path == "/api/providers/refresh":
            return 202, app.providers.view(refresh=True)
        if match := re.fullmatch(PROJECT + "/index", path):
            return 202, app.start_index(app.ws.get(match.group(1)))
        if match := re.fullmatch(PROJECT + "/tasks", path):
            return 201, app.new_task(app.ws.get(match.group(1)), payload)
        if match := re.fullmatch(TASK + "/run", path):
            return 202, app.start_run(app.ws.get(match.group(1)), unquote(match.group(2)), payload)
        if match := re.fullmatch(TASK + "/stop", path):
            return 202, app.stop_run(app.ws.get(match.group(1)), unquote(match.group(2)))
        raise FileNotFoundError("Not found")

    def _patch(self, path: str, payload: dict) -> tuple[int, dict]:
        app = self.server.app
        if path == "/api/settings":
            return 200, app.update_settings(payload)
        if match := re.fullmatch(PROJECT, path):
            return 200, app.rename_project(app.ws.get(match.group(1)), payload)
        if match := re.fullmatch(PROJECT + "/config", path):
            return 200, app.update_config(app.ws.get(match.group(1)), payload)
        raise FileNotFoundError("Not found")

    def _delete(self, path: str) -> tuple[int, dict]:
        app = self.server.app
        if match := re.fullmatch(PROJECT, path):
            return 200, app.remove_project(app.ws.get(match.group(1)))
        raise FileNotFoundError("Not found")


def serve(home: Path, port: int = 8765, open_browser: bool = True) -> RunsAtExit:
    """Serve until Ctrl+C; report runs started here that are active or still starting.

    Works on a home without any configuration: the interface then opens its
    setup wizard. If the preferred port is taken, a free one is used instead.
    """
    try:
        server = DashboardServer(home, port)
    except OSError:
        if port == 0:
            raise
        server = DashboardServer(home, 0)
        print(f"Port {port} is in use; using {server.server_address[1]} instead.")
    with server:
        print(f"PatchRondo: {server.url}")
        print("Press Ctrl+C to stop. Keep the URL private: it contains the session token.")
        # Flushed: with redirected output the link would otherwise stay in a buffer until exit.
        print("Runs started from the interface continue in the background if it stops.", flush=True)
        if open_browser:
            webbrowser.open(server.url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nPatchRondo interface stopped.")
        return server.close_runs()
