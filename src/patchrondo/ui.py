"""Local dashboard served with the Python standard library.

The dashboard reads persisted task state and can create tasks, start or resume
runs and edit the test settings. Runs are separate `patchrondo run` processes,
so the existing lock, checkpoint and pause/resume behavior applies unchanged and
a run continues if the dashboard stops. The server binds to the loopback
interface only, rejects foreign Host and Origin headers (DNS rebinding, CSRF)
and requires a per-session token for every API call.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
from importlib import resources
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import threading
from typing import NamedTuple
from urllib.parse import unquote, urlsplit
import webbrowser

from .core import config, create_task
from .storage import atomic_text, now, read_json, save_json

TASK_ID = re.compile(r"T-[a-f0-9]{12}")
TEXT_LIMIT = 200_000
MAX_BODY = 64_000
RUN_LOG = "ui-run.log"
STARTUP_GRACE_SECONDS = 1.5
# Directory containing the imported package, so child runs work without installation.
PACKAGE_ROOT = str(Path(__file__).resolve().parents[1])
SUMMARY_FIELDS = ("id", "title", "status", "phase", "iteration", "developer",
                  "reviewer", "created_at", "updated_at")
DOCUMENTS = {"task": "task.md", "handoff": "handoff.md", "feedback": "feedback.md",
             "report": "report.md", "log": RUN_LOG}
# Mascot images inlined as data: URIs, so the page needs no extra routes.
MASCOT = {"__RONDO__": "static/rondo.webp", "__RONDO_HEAD__": "static/rondo-head.webp"}
CSP = ("default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
       "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")


class RunsAtExit(NamedTuple):
    active: list[subprocess.Popen]
    pending: int  # run starts still in progress when the wait timed out

    @property
    def any(self) -> bool:
        return bool(self.active or self.pending)


class Conflict(Exception):
    """The requested action does not fit the current task state."""


def _text(path: Path, tail: bool = False) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-TEXT_LIMIT:] if tail else text[:TEXT_LIMIT]


def _split_windows(line: str) -> list[str]:
    """Parse a command line with the Microsoft C runtime rules that list2cmdline targets."""
    args: list[str] = []
    current: list[str] = []
    in_arg = quoted = False
    i = 0
    while i < len(line):
        char = line[i]
        if char == "\\":
            end = i
            while end < len(line) and line[end] == "\\":
                end += 1
            count = end - i
            if end < len(line) and line[end] == '"':
                # 2n backslashes + quote: n backslashes, quote toggles; 2n+1: n backslashes + literal quote.
                current.append("\\" * (count // 2))
                if count % 2:
                    current.append('"')
                    end += 1
            else:
                current.append("\\" * count)
            in_arg, i = True, end
            continue
        if char == '"':
            quoted, in_arg = not quoted, True
        elif char in " \t" and not quoted:
            if in_arg:
                args.append("".join(current))
                current, in_arg = [], False
        else:
            current.append(char)
            in_arg = True
        i += 1
    if quoted:
        raise ValueError("No closing quotation")
    if in_arg:
        args.append("".join(current))
    return args


def split_command(line: str, windows: bool | None = None) -> list[str]:
    """Split one test command line: POSIX shell rules, or Windows rules where backslashes are paths."""
    if windows is None:
        windows = os.name == "nt"
    return _split_windows(line) if windows else shlex.split(line)


def join_command(command: list[str], windows: bool | None = None) -> str:
    """Inverse of split_command, used to show saved commands for editing."""
    if windows is None:
        windows = os.name == "nt"
    return subprocess.list2cmdline(command) if windows else shlex.join(command)


def overview(home: Path) -> dict:
    try:
        cfg = config(home)
        tests = cfg["tests"]
        settings = {"repository": cfg["repository"], "tests_enabled": tests["enabled"],
                    "trust_acknowledged": tests["trust_acknowledged"],
                    "test_commands": len(tests.get("commands") or []),
                    "test_command_lines": [join_command(cmd) for cmd in tests.get("commands") or []
                                           if isinstance(cmd, list) and all(isinstance(x, str) for x in cmd)],
                    "max_iterations": cfg["workflow"]["max_iterations"],
                    "rag_enabled": cfg["rag"]["enabled"],
                    "recovery_enabled": cfg["recovery"]["enabled"]}
        error = None
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        settings, error = None, str(exc)
    tasks = []
    folder = home / "tasks"
    for file in folder.glob("T-*/state.json") if folder.is_dir() else []:
        try:
            state = read_json(file)
        except (OSError, ValueError):
            continue  # a task being written atomically is picked up on the next poll
        if not isinstance(state, dict):
            continue
        item = {key: state.get(key) for key in SUMMARY_FIELDS}
        item["verdict"] = (state.get("review") or {}).get("verdict")
        tasks.append(item)
    tasks.sort(key=lambda t: str(t.get("updated_at") or ""), reverse=True)
    return {"home": str(home), "config": settings, "config_error": error, "tasks": tasks}


def task_detail(home: Path, task_id: str) -> dict | None:
    if not TASK_ID.fullmatch(task_id):
        return None
    path = home / "tasks" / task_id
    try:
        state = read_json(path / "state.json")
    except (OSError, ValueError):
        return None
    return {"state": state, "locked": (path / ".run.lock").exists(),
            "documents": {key: _text(path / name, tail=key == "log") for key, name in DOCUMENTS.items()}}


def new_task(home: Path, payload: dict) -> str:
    fields = {key: payload.get(key) for key in ("title", "description", "developer", "reviewer")}
    if not all(isinstance(value, str) for value in fields.values()):
        raise ValueError("title, description, developer and reviewer must be strings")
    acceptance = payload.get("acceptance", [])
    if not isinstance(acceptance, list) or not all(isinstance(item, str) for item in acceptance):
        raise ValueError("acceptance must be a list of strings")
    task_id, _ = create_task(home, acceptance=[item.strip() for item in acceptance if item.strip()], **fields)
    return task_id


def start_run(home: Path, task_id: str, track=None) -> subprocess.Popen:
    """Start `patchrondo run` in its own process group; its lock prevents concurrent runs.

    A run that exits with an error during the first moments (for example an
    import or lock failure) is reported instead of being shown as started.
    `track` receives the process as soon as it exists, before that check.
    """
    detail = task_detail(home, task_id)
    if detail is None:
        raise FileNotFoundError("Task not found")
    status = detail["state"].get("status")
    if status in {"done", "blocked"}:
        raise Conflict(f"Task is {status} and cannot be run again")
    if detail["locked"]:
        raise Conflict("Task is already running")
    config(home)
    path = home / "tasks" / task_id
    fd = os.open(path / RUN_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "ab") as log:
        log.write(f"\n=== {now()} · run started from the dashboard ===\n".encode())
        log.flush()
        extra = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                 else {"start_new_session": True})
        # The child runs from the state directory: make inherited entries absolute.
        inherited = [os.path.abspath(entry) for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8",
               "PYTHONPATH": os.pathsep.join([PACKAGE_ROOT, *inherited])}
        process = subprocess.Popen([sys.executable, "-m", "patchrondo", "--home", str(home), "run", task_id],
                                   cwd=home, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   env=env, **extra)
    if track is not None:
        track(process)
    try:
        code = process.wait(timeout=STARTUP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        return process
    if code not in (0, 2):  # 0 = done, 2 = paused/blocked; anything else is a failure to run
        lines = (_text(path / RUN_LOG, tail=True) or "").strip().splitlines()
        raise RuntimeError(f"Run exited immediately (code {code}): {lines[-1] if lines else 'see the Run log'}")
    return process


def update_tests(home: Path, payload: dict) -> None:
    enabled, trust, lines = payload.get("enabled"), payload.get("trust_acknowledged"), payload.get("commands")
    if type(enabled) is not bool or type(trust) is not bool:
        raise ValueError("enabled and trust_acknowledged must be booleans")
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        raise ValueError("commands must be a list of strings")
    commands = [split_command(line) for line in lines if line.strip()]
    file = home / "config.json"
    previous = file.read_text(encoding="utf-8")
    cfg = json.loads(previous)
    cfg["tests"] = {"enabled": enabled, "trust_acknowledged": trust, "commands": commands}
    save_json(file, cfg)
    try:
        config(home)
    except (ValueError, RuntimeError, KeyError):
        atomic_text(file, previous)  # never leave an invalid configuration behind
        raise


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True  # idle connections never delay shutdown; run starts are awaited below

    def __init__(self, home: Path, port: int = 0):
        super().__init__(("127.0.0.1", port), DashboardHandler)
        self.home = home
        self.token = secrets.token_urlsafe(24)
        package = resources.files("patchrondo")
        self.page = package.joinpath("static/dashboard.html").read_text(encoding="utf-8")
        for placeholder, asset in MASCOT.items():
            data = base64.b64encode(package.joinpath(asset).read_bytes()).decode("ascii")
            self.page = self.page.replace(placeholder, f"data:image/webp;base64,{data}")
        self.children: list[subprocess.Popen] = []
        self.closing = False
        self.starting = 0
        self.runs = threading.Condition()  # guards children, closing and starting

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/#token={self.token}"

    def track(self, process: subprocess.Popen) -> None:
        with self.runs:
            # Reap finished runs so they do not linger as zombies on POSIX.
            self.children = [child for child in self.children if child.poll() is None] + [process]

    @contextmanager
    def run_slot(self):
        """Mark a run start in progress; refused once the dashboard is closing."""
        with self.runs:
            if self.closing:
                raise Conflict("The dashboard is shutting down")
            self.starting += 1
        try:
            yield
        finally:
            with self.runs:
                self.starting -= 1
                self.runs.notify_all()

    def close_runs(self, timeout: float = 30) -> RunsAtExit:
        """Refuse new run starts, wait for those in progress and report what may still run."""
        with self.runs:
            self.closing = True
            self.runs.wait_for(lambda: self.starting == 0, timeout)
            # After a timeout, `pending` starts may still launch a run: callers must keep its files.
            return RunsAtExit([child for child in self.children if child.poll() is None], self.starting)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer
    server_version = "PatchRondo"
    sys_version = ""
    timeout = 30  # seconds; a silent connection is dropped instead of holding a thread

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass  # keep the terminal quiet; requests can reveal task titles

    def _send(self, status: int, body: bytes, content_type: str, nonce: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP.format(nonce=nonce or secrets.token_urlsafe(16)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _local_host(self) -> bool:
        port = self.server.server_address[1]
        if self.headers.get("Host", "") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            self._json(403, {"error": "Unexpected Host header"})
            return False
        return True

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-PatchRondo-Token", "")
        if not secrets.compare_digest(supplied.encode(), self.server.token.encode()):
            self._json(401, {"error": "Missing or invalid session token"})
            return False
        return True

    def do_GET(self) -> None:
        if not self._local_host():
            return
        path = urlsplit(self.path).path
        if path == "/":
            nonce = secrets.token_urlsafe(16)
            page = self.server.page.replace("__NONCE__", nonce)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8", nonce)
            return
        if not path.startswith("/api/"):
            self._json(404, {"error": "Not found"})
            return
        if not self._authorized():
            return
        if path == "/api/overview":
            self._json(200, overview(self.server.home))
            return
        match = re.fullmatch(r"/api/tasks/([^/]+)", path)
        detail = task_detail(self.server.home, unquote(match.group(1))) if match else None
        if detail is None:
            self._json(404, {"error": "Task not found"})
            return
        self._json(200, detail)

    def do_POST(self) -> None:
        if not self._local_host() or not self._authorized():
            return
        port = self.server.server_address[1]
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
            self._json(403, {"error": "Unexpected Origin header"})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self._json(415, {"error": "Content-Type must be application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self._json(413, {"error": "Invalid request size"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        if not isinstance(payload, dict):
            self._json(400, {"error": "Request body must be a JSON object"})
            return
        home = self.server.home
        path = urlsplit(self.path).path
        run = re.fullmatch(r"/api/tasks/([^/]+)/run", path)
        try:
            if path == "/api/tasks":
                self._json(201, {"id": new_task(home, payload)})
            elif run:
                with self.server.run_slot():
                    start_run(home, unquote(run.group(1)), track=self.server.track)
                self._json(202, {"started": True})
            elif path == "/api/settings/tests":
                update_tests(home, payload)
                self._json(200, overview(home))
            else:
                self._json(404, {"error": "Not found"})
        except FileNotFoundError as exc:
            self._json(404, {"error": str(exc)})
        except Conflict as exc:
            self._json(409, {"error": str(exc)})
        except (ValueError, RuntimeError, KeyError, OSError) as exc:
            self._json(400, {"error": str(exc)})


def serve(home: Path, port: int = 8765, open_browser: bool = True) -> RunsAtExit:
    """Serve until Ctrl+C; report runs started here that are active or still starting."""
    config(home)  # require an initialized, valid configuration before serving
    with DashboardServer(home, port) as server:
        print(f"PatchRondo dashboard: {server.url}")
        print("Press Ctrl+C to stop. Keep the URL private: it contains the session token.")
        print("Runs started from the dashboard continue in the background if it stops.")
        if open_browser:
            webbrowser.open(server.url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nDashboard stopped.")
        return server.close_runs()
