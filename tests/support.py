"""Shared fixtures for the interface tests: temporary repositories and a served application."""
import http.client
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from patchrondo import doctor
from patchrondo.ui import DashboardServer

PROVIDERS = {name: {"name": name, "label": spec["label"], "installed": True, "path": f"/synthetic/{name}",
                    "version": "0.0.0", "authenticated": True, "auth_method": "claude.ai" if name == "claude" else None,
                    "warning": None, "hint": None, "login_command": spec["login"], "docs": spec["docs"]}
             for name, spec in doctor.PROVIDERS.items()}


def make_repo(path: Path) -> Path:
    """A Git repository with one commit."""
    path.mkdir(parents=True)
    for args in (["init", "-q"], ["config", "user.name", "T"], ["config", "user.email", "t@example.invalid"],
                 ["config", "commit.gpgsign", "false"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    (path / "README.md").write_text("# Test\n", encoding="utf-8", newline="\n")
    for args in (["add", "."], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return path.resolve()


class ServerCase(unittest.TestCase):
    """Serves a temporary home. CLI status checks are replaced: no provider CLI is ever started."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / "home"
        checks = patch("patchrondo.app.doctor.check_all", return_value=PROVIDERS)
        self.check_all = checks.start()
        self.addCleanup(checks.stop)
        self.addCleanup(self.tmp.cleanup)
        self.server = None
        self.start()

    def start(self, **options):
        self.server = DashboardServer(self.home, 0, **options)
        self.port = self.server.server_address[1]
        self.app = self.server.app
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.stop)
        return self.server

    def stop(self):
        server, self.server = self.server, None
        if server is not None:
            server.shutdown()
            runs = server.close_runs(2)
            server.server_close()
            return runs
        return None

    def request(self, method, path, body=None, token=True, host=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        all_headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            all_headers["X-PatchRondo-Token"] = self.server.token if token is True else token
        data = raw
        if body is not None:
            all_headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        all_headers.update(headers or {})
        conn.request(method, path, body=data, headers=all_headers)
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        try:
            parsed = json.loads(payload)
        except ValueError:
            parsed = payload
        return response, parsed

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, body=None, **kwargs):
        return self.request("POST", path, {} if body is None else body, **kwargs)

    def patch_(self, path, body, **kwargs):
        return self.request("PATCH", path, body, **kwargs)

    def delete(self, path, **kwargs):
        return self.request("DELETE", path, {}, **kwargs)

    def add_project(self, name="alpha"):
        repo = make_repo(self.root / "repos" / name)
        response, data = self.post("/api/projects", {"path": str(repo)})
        self.assertEqual(response.status, 201, data)
        return data, repo

    def add_task(self, project, title="Task", **extra):
        response, data = self.post(f"/api/projects/{project['id']}/tasks", {
            "title": title, "description": "Do it", "acceptance": ["Done"], "developer": "claude", "reviewer": "codex", **extra})
        self.assertEqual(response.status, 201, data)
        return data["id"], Path(project["home"]) / "tasks" / data["id"]


# A stand-in for both provider CLIs that behaves like them at the process boundary: it prints
# its activity line by line while it works when asked to stream, and a single result otherwise.
# What it does is scripted per role in control.json next to it; it never searches PATH.
STREAMING_CLI = r'''import json, os, pathlib, sys, time
root = pathlib.Path(__file__).parent
control = json.loads((root / "control.json").read_text(encoding="utf-8"))
provider, args = sys.argv[1], sys.argv[2:]
prompt = sys.stdin.read()
reviewer = ("--json-schema" in args) if provider == "claude" else ("--output-schema" in args)
role = "reviewer" if reviewer else "developer"
live = ("stream-json" in args) if provider == "claude" else ("--json" in args)
plan = control.get(role, {})
with (root / "calls.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"provider": provider, "role": role, "live": live, "args": args}) + "\n")

def say(obj):
    if live:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

def hold(name):
    if name in plan.get("hold", []):
        (root / f"{role}.{name}.reached").touch()
        while not (root / f"{role}.{name}.release").exists():
            time.sleep(0.01)

def once(name):
    marker = root / f"{role}.{name}.once"
    if plan.get(name) and not marker.exists():
        marker.touch()
        return True
    return False

if plan.get("refuse_stream") and live:
    sys.stderr.write("error: unknown option '--verbose'\n" if provider == "claude" else "error: unexpected argument '--json' found\n")
    sys.exit(2)
words = plan.get("says", [f"Working as {role} — in corso."])
if provider == "claude":
    say({"type": "system", "subtype": "init", "model": "synthetic-model", "tools": ["Read", "Write"]})
else:
    say({"type": "thread.started", "thread_id": "synthetic"})
    say({"type": "turn.started"})
for index, text in enumerate(words):
    if provider == "claude":
        say({"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "PRIVATE-REASONING of the model"},
                                                          {"type": "text", "text": text}]}})
    else:
        say({"type": "item.completed", "item": {"id": f"r{index}", "type": "reasoning", "text": "PRIVATE-REASONING of the model"}})
        say({"type": "item.completed", "item": {"id": f"m{index}", "type": "agent_message", "text": text}})
    time.sleep(plan.get("pause", 0))
hold("working")
if once("quota_once") or plan.get("quota"):
    message = "Usage limit reached. Retry after 3600 seconds."
    if live and provider == "claude":
        say({"type": "result", "subtype": "success", "is_error": True, "result": message})
    elif live:
        say({"type": "error", "message": message})
        say({"type": "turn.failed", "error": {"message": message}})
    elif provider == "claude":
        print(json.dumps({"type": "result", "is_error": True, "result": message}))
    else:
        sys.stderr.write(message + "\n")
    sys.exit(1)
if once("crash_once"):
    sys.stdout.write('{"type": "assistant", "message": {"content": [{"type": "text", "text": "cut in the mid')
    sys.stdout.flush()
    os._exit(9)
if reviewer:
    payload = {"verdict": plan.get("verdict", "APPROVED"), "summary": "Synthetic review of the change.", "issues": []}
    text = json.dumps(payload)
else:
    target = str(pathlib.Path.cwd() / "feature.txt")
    if provider == "claude":
        say({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_w", "name": "Write",
                                                          "input": {"file_path": target, "content": "good"}}]}})
    pathlib.Path("feature.txt").write_text(plan.get("writes", "good"), encoding="utf-8")
    if provider == "claude":
        say({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_w", "content": "File created"}]}})
    else:
        say({"type": "item.completed", "item": {"id": "f0", "type": "file_change", "status": "completed",
                                                "changes": [{"path": target, "kind": "add"}]}})
    text = plan.get("reply", "## Changes\n\n- Created `feature.txt`\n\n## Suggested tests\n\n- Check its content")
hold("answer")
if provider == "claude":
    final = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 2, "duration_ms": 10}
    final.update({"structured_output": payload, "result": ""} if reviewer else {"result": text})
    if plan.get("no_result"):
        sys.exit(0)
    if live:
        say(final)
    else:
        print(json.dumps(final))
else:
    say({"type": "item.completed", "item": {"id": "final", "type": "agent_message", "text": text}})
    say({"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5,
                                             "reasoning_output_tokens": 0}})
    if not plan.get("no_result"):
        pathlib.Path(args[args.index("--output-last-message") + 1]).write_text(text, encoding="utf-8")
'''


class ScriptedCLI:
    """The scripted CLI in a folder of its own, reached only through `patchrondo.providers.execute`."""

    def __init__(self, folder: Path):
        from patchrondo.process import execute
        self.folder, self._execute = folder, execute
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "cli.py").write_text(STREAMING_CLI, encoding="utf-8", newline="\n")
        self.script()

    def script(self, **roles) -> None:
        (self.folder / "control.json").write_text(json.dumps(roles), encoding="utf-8")

    def execute(self, argv, cwd, **kwargs):
        if argv[0] not in {"claude", "codex"}:
            raise ValueError("Unexpected provider command")
        import sys
        return self._execute([sys.executable, str(self.folder / "cli.py"), *argv], cwd, **kwargs)

    def calls(self) -> list[dict]:
        file = self.folder / "calls.jsonl"
        return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()] if file.exists() else []

    def reached(self, role: str, point: str) -> bool:
        return (self.folder / f"{role}.{point}.reached").exists()

    def release(self, role: str, point: str) -> None:
        (self.folder / f"{role}.{point}.release").touch()
