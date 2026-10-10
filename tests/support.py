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
