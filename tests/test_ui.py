"""The local interface server: protection of every endpoint, projects, settings, tasks, runs and the demo."""
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from unittest.mock import patch

from patchrondo import __version__
from patchrondo.app import PACKAGE_ROOT, RunsAtExit, browse
from patchrondo.cli import main
from patchrondo.commands import join_command, split_command
from patchrondo.core import config, initialize
from patchrondo.storage import read_json, save_json
from patchrondo.ui import CSP, serve

from support import ServerCase, make_repo

SOURCE = str(Path(__file__).resolve().parents[1] / "src")
# A stand-in for `patchrondo run`: takes the task lock like a real run and holds it until told to stop.
HOLDER = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from patchrondo import runinfo
from patchrondo.storage import TaskLock
path = Path(sys.argv[1])
try:
    with runinfo.attached(path, sys.argv[3] == "auto"), TaskLock(path):
        (path / "holding").touch()
        while not (path / "release").exists():
            time.sleep(0.02)
except KeyboardInterrupt:
    (path / "interrupted").touch()
    raise SystemExit(2)
"""


def load_demo():
    spec = importlib.util.spec_from_file_location(
        "demo_dashboard", Path(__file__).resolve().parents[1] / "tools" / "demo_dashboard.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    return demo


def wait_for(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


class CommandLineTests(unittest.TestCase):
    def test_command_lines_keep_windows_paths(self):
        self.assertEqual(split_command(r'pytest tests\\test_api.py "C:\\My Dir\\x.py"'.replace("\\\\", "\\"), windows=True),
                         ["pytest", r"tests\test_api.py", r"C:\My Dir\x.py"])
        self.assertEqual(split_command("ruff check 'src dir'", windows=False), ["ruff", "check", "src dir"])
        with self.assertRaises(ValueError):
            split_command('pytest "unclosed', windows=True)
        for windows in (True, False):
            for command in (["python", "-m", "pytest", "src dir", r"tests\a.py" if windows else "tests/a.py"],
                            ["python", "-c", 'print("hello")'], ["x", "C:\\My Dir\\"], ["a", "", "b c"],
                            [r"\\server\share\x", 'say \\"hi\\"']):
                self.assertEqual(split_command(join_command(command, windows), windows), command)

    def test_command_lines_round_trip_for_any_argument(self):
        rng = random.Random(1234)
        alphabet = ["a", "Z", " ", "\t", "\\", '"', "'", "é", "-", "="]
        for _ in range(3000):
            command = ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 7)))
                       for _ in range(rng.randint(1, 4))]
            for windows in (True, False):
                self.assertEqual(split_command(join_command(command, windows), windows), command)


class ProtectionTests(ServerCase):
    def test_page_and_assets_are_served_with_a_nonce_csp_and_no_token(self):
        response, body = self.get("/", token=False)
        self.assertEqual(response.status, 200)
        csp = response.getheader("Content-Security-Policy")
        nonce = csp.split("'nonce-")[1].split("'")[0]
        self.assertEqual(csp, CSP.format(nonce=nonce))
        self.assertIn("script-src 'nonce-", csp)
        self.assertNotIn("unsafe", csp)
        self.assertNotIn("data:", csp)
        self.assertEqual(body.count(f'nonce="{nonce}"'.encode()), 4)  # three style sheets and one module script
        self.assertNotIn(b"__NONCE__", body)
        self.assertNotIn(self.server.token.encode(), body)
        other = self.get("/", token=False)[0].getheader("Content-Security-Policy")
        self.assertNotEqual(other, csp)  # a fresh nonce for every response
        for path, kind in (("/assets/js/app.js", "text/javascript"), ("/assets/css/tokens.css", "text/css"),
                           ("/assets/rondo.webp", "image/webp"), ("/assets/js/views/task.js", "text/javascript")):
            response, _ = self.get(path, token=False)
            self.assertEqual(response.status, 200, path)
            self.assertTrue(response.getheader("Content-Type").startswith(kind))
            self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
            self.assertEqual(response.getheader("Cache-Control"), "no-store")

    def test_only_packaged_assets_can_be_requested(self):
        for path in ("/assets/../ui.py", "/assets/%2e%2e/ui.py", "/assets/..%2f..%2fcli.py", "/assets/js/../../cli.py",
                     "/assets/index.html", "/assets/", "/assets/js/missing.js", "/static/rondo.webp", "/ui.py",
                     "/assets/C:%5CWindows%5Cwin.ini", "/assets//etc/passwd"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path, token=False)[0].status, 404)

    def test_every_api_route_requires_the_session_token(self):
        routes = [("GET", "/api/snapshot"), ("GET", "/api/settings"), ("GET", "/api/providers"), ("GET", "/api/events"),
                  ("GET", "/api/projects/P-00000000"), ("GET", "/api/projects/P-00000000/tasks/T-000000000000"),
                  ("GET", "/api/projects/P-00000000/tasks/T-000000000000/files"),
                  ("GET", "/api/projects/P-00000000/tasks/T-000000000000/log"),
                  ("POST", "/api/projects"), ("POST", "/api/projects/import"), ("POST", "/api/projects/inspect"),
                  ("POST", "/api/fs/list"), ("POST", "/api/providers/refresh"), ("POST", "/api/projects/P-00000000/index"),
                  ("POST", "/api/projects/P-00000000/tasks"), ("POST", "/api/projects/P-00000000/tasks/T-000000000000/run"),
                  ("POST", "/api/projects/P-00000000/tasks/T-000000000000/stop"), ("PATCH", "/api/settings"),
                  ("PATCH", "/api/projects/P-00000000"), ("PATCH", "/api/projects/P-00000000/config"),
                  ("DELETE", "/api/projects/P-00000000")]
        for method, path in routes:
            for token in (False, "wrong", self.server.token + "x"):
                with self.subTest(method=method, path=path, token=token):
                    response, data = self.request(method, path, None if method == "GET" else {}, token=token)
                    self.assertEqual((response.status, data["code"]), (401, "unauthorized"))
        self.assertFalse(self.home.exists())  # none of the refused requests touched the disk
        self.assertEqual(self.get("/api/snapshot")[0].status, 200)

    def test_foreign_hosts_origins_and_content_types_are_rejected(self):
        self.assertEqual(self.get("/", token=False, host="evil.example")[0].status, 403)
        self.assertEqual(self.get("/assets/js/app.js", token=False, host=f"evil.example:{self.port}")[0].status, 403)
        self.assertEqual(self.get("/api/snapshot", host=f"evil.example:{self.port}")[0].status, 403)
        self.assertEqual(self.get("/api/snapshot", host=f"localhost:{self.port}")[0].status, 200)
        for method in ("POST", "PATCH", "DELETE"):
            with self.subTest(method=method):
                path = "/api/settings" if method == "PATCH" else "/api/projects/P-00000000" if method == "DELETE" else "/api/projects"
                self.assertEqual(self.request(method, path, {}, headers={"Origin": "http://evil.example"})[0].status, 403)
                self.assertEqual(self.request(method, path, {}, headers={"Origin": f"http://127.0.0.1:{self.port + 1}"})[0].status, 403)
                self.assertEqual(self.request(method, path, {}, host="evil.example")[0].status, 403)
                self.assertEqual(self.request(method, path, {}, headers={"Content-Type": "text/plain"})[0].status, 415)
                self.assertEqual(self.request(method, path, raw=b"{}", headers={"Content-Type": "application/x-www-form-urlencoded"})[0].status, 415)
                self.assertEqual(self.request(method, path, raw=b"[1]", headers={"Content-Type": "application/json"})[0].status, 400)
                self.assertEqual(self.request(method, path, raw=b"{not json", headers={"Content-Type": "application/json"})[0].status, 400)
                self.assertEqual(self.declared_size(method, path, "999999999"), 413)
                self.assertEqual(self.declared_size(method, path, "-5"), 413)
                self.assertEqual(self.declared_size(method, path, "many"), 413)
        self.assertEqual(self.request("PATCH", "/api/settings", {"theme": "light"}, headers={"Origin": f"http://127.0.0.1:{self.port}"})[0].status, 200)
        self.assertEqual(self.request("PUT", "/api/settings", {})[0].status, 501)

    def declared_size(self, method, path, length):
        """Status for a write that only announces its body size: it must be refused before any body is read."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as client:
            client.sendall((f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nX-PatchRondo-Token: {self.server.token}\r\n"
                            f"Content-Type: application/json\r\nContent-Length: {length}\r\n\r\n").encode())
            return int(client.recv(64).split()[1])

    def test_an_unexpected_fault_is_answered_in_the_same_shape(self):
        with patch.object(self.app.ws, "settings", side_effect=TypeError("boom")), patch("sys.stderr", new_callable=io.StringIO) as err:
            response, data = self.get("/api/settings")
        self.assertEqual((response.status, data["code"]), (500, "internal"))
        self.assertNotIn("boom", data["error"])
        self.assertIn("TypeError", err.getvalue())

    def test_errors_have_one_shape(self):
        for response, data, status, code in (
                (*self.get("/api/projects/P-00000000"), 404, "not_found"), (*self.get("/api/nothing"), 404, "not_found"),
                (*self.post("/api/nothing"), 404, "not_found"), (*self.patch_("/api/settings", {"theme": "neon"}), 400, "invalid"),
                (*self.post("/api/projects", {"path": "relative/path"}), 400, "invalid"),
                (*self.delete("/api/projects/not-an-id"), 404, "not_found")):
            self.assertEqual((response.status, data.get("code"), isinstance(data.get("error"), str)), (status, code, True))

    def test_idle_connection_does_not_block_closing(self):
        idle = socket.create_connection(("127.0.0.1", self.port))  # never sends a request
        try:
            time.sleep(0.2)
            started = time.monotonic()
            closer = threading.Thread(target=self.stop)
            closer.start()
            closer.join(5)
            self.assertFalse(closer.is_alive(), "closing blocked on an idle connection")
            self.assertLess(time.monotonic() - started, 3)
        finally:
            idle.close()


class BootstrapTests(ServerCase):
    def test_a_new_installation_serves_without_any_file(self):
        response, data = self.get("/api/snapshot")
        self.assertEqual(response.status, 200)
        self.assertEqual((data["projects"], data["tasks"], data["error"]), ([], [], None))
        self.assertEqual(data["settings"]["onboarding_completed"], False)
        self.assertEqual(data["about"]["version"], __version__)
        self.assertEqual(data["about"]["platform"]["stop_supported"], os.name == "posix")
        self.assertFalse(self.home.exists())

    def test_serve_starts_without_configuration_and_falls_back_to_a_free_port(self):
        seen = {}

        raw = io.BytesIO()
        piped = io.TextIOWrapper(raw, encoding="utf-8")  # block-buffered, like output redirected to a file or pipe

        def fake_forever(server):
            seen["port"] = server.server_address[1]
            seen["home"] = server.home
            seen["printed"] = raw.getvalue().decode("utf-8")  # what a reader of the pipe has received so far
            raise KeyboardInterrupt

        with patch("patchrondo.ui.DashboardServer.serve_forever", fake_forever), patch("patchrondo.ui.webbrowser.open") as opened, \
                patch("sys.stdout", piped):
            runs = serve(self.root / "fresh", self.port, open_browser=True)  # the port of this test's server is taken
        self.assertEqual(runs, RunsAtExit([], 0))
        self.assertNotEqual(seen["port"], self.port)
        self.assertIn(f"Port {self.port} is in use", seen["printed"])
        self.assertIn(f"http://127.0.0.1:{seen['port']}/#token=", seen["printed"])  # the link is out before serving starts
        opened.assert_called_once()
        self.assertIn("#token=", opened.call_args.args[0])
        self.assertNotIn("?", opened.call_args.args[0])  # the token travels in the fragment, never in a query string
        self.assertFalse((self.root / "fresh").exists())

    def test_ui_command_no_longer_needs_init(self):
        with patch("patchrondo.ui.serve") as fake, patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(main(["--home", str(self.root / "fresh"), "ui", "--no-browser"]), 0)
        fake.assert_called_once()
        self.assertEqual(err.getvalue(), "")

    def test_provider_status_is_cached_and_checked_in_the_background(self):
        self.get("/api/snapshot")
        self.assertTrue(wait_for(lambda: self.get("/api/providers")[1]["providers"] is not None))
        data = self.get("/api/providers")[1]
        self.assertEqual((data["checking"], data["providers"]["claude"]["authenticated"]), (False, True))
        for _ in range(5):
            self.get("/api/snapshot")
        self.assertEqual(self.check_all.call_count, 1)
        self.assertEqual(self.post("/api/providers/refresh")[0].status, 202)
        self.assertTrue(wait_for(lambda: self.check_all.call_count == 2))


class ProjectTests(ServerCase):
    def test_register_inspect_rename_and_remove(self):
        repo = make_repo(self.root / "repos" / "alpha")
        response, found = self.post("/api/projects/inspect", {"path": str(repo)})
        self.assertEqual((response.status, found["ok"], found["name"], found["dirty"]), (200, True, "alpha", False))
        self.assertFalse(self.home.exists())  # inspecting registers nothing
        (repo / "sub").mkdir()
        (repo / "dirty.txt").write_text("x", encoding="utf-8")
        found = self.post("/api/projects/inspect", {"path": str(repo / "sub")})[1]
        self.assertEqual((found["ok"], found["is_root"], found["root"]), (False, False, str(repo)))
        found = self.post("/api/projects/inspect", {"path": str(repo)})[1]
        self.assertEqual((found["ok"], found["dirty"], len(found["warnings"])), (True, True, 1))
        for path in ("", "relative", str(self.root / "missing"), str(self.root), 5):
            self.assertFalse(self.post("/api/projects/inspect", {"path": path})[1]["ok"])
        (repo / "dirty.txt").unlink()

        response, project = self.post("/api/projects", {"path": str(repo), "name": "  Alpha  API "})
        self.assertEqual((response.status, project["name"], project["origin"], project["repository"]), (201, "Alpha API", "managed", str(repo)))
        self.assertEqual(project["config"]["tests_enabled"], False)
        self.assertEqual(project["config"]["trust_acknowledged"], False)
        self.assertEqual(project["repo"]["dirty"], False)
        self.assertEqual(self.post("/api/projects/inspect", {"path": str(repo)})[1]["registered"], "Alpha API")
        response, data = self.post("/api/projects", {"path": str(repo)})
        self.assertEqual(response.status, 400)
        self.assertIn("already registered", data["error"])

        response, renamed = self.patch_(f"/api/projects/{project['id']}", {"name": "Renamed"})
        self.assertEqual((response.status, renamed["name"], renamed["id"]), (200, "Renamed", project["id"]))
        self.assertEqual(self.patch_(f"/api/projects/{project['id']}", {"name": ""})[0].status, 400)
        self.assertEqual(self.patch_(f"/api/projects/{project['id']}", {"repository": "/elsewhere"})[0].status, 400)
        self.assertEqual(config(Path(project["home"]))["repository"], str(repo))

        response, removed = self.delete(f"/api/projects/{project['id']}")
        self.assertEqual((response.status, removed["kept"]), (200, {"repository": str(repo), "state": project["home"]}))
        self.assertTrue((Path(project["home"]) / "config.json").is_file() and (repo / "README.md").is_file())
        self.assertEqual(self.get("/api/snapshot")[1]["projects"], [])
        response, imported = self.post("/api/projects/import", {"home": project["home"]})
        self.assertEqual((response.status, imported["origin"], imported["tasks"]), (201, "imported", 0))

    def test_a_repeated_request_registers_or_creates_only_once(self):
        repo = make_repo(self.root / "repos" / "alpha")
        body = {"path": str(repo), "request_id": "11111111-2222-3333-4444-555555555555"}
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.post("/api/projects", body))) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual([response.status for response, _ in results], [201] * 4)
        self.assertEqual(len({data["id"] for _, data in results}), 1)
        self.assertEqual(len(self.get("/api/snapshot")[1]["projects"]), 1)
        project = results[0][1]
        task = {"title": "Once", "description": "d", "developer": "claude", "reviewer": "codex", "request_id": "task-request-0001"}
        first = self.post(f"/api/projects/{project['id']}/tasks", task)[1]
        second = self.post(f"/api/projects/{project['id']}/tasks", task)[1]
        self.assertEqual(first, second)
        self.assertEqual(len(list((Path(project["home"]) / "tasks").iterdir())), 1)

    def test_two_projects_keep_settings_and_tasks_apart(self):
        alpha, _ = self.add_project("alpha")
        beta, _ = self.add_project("beta")
        before = (Path(alpha["home"]) / "config.json").read_bytes()
        response, saved = self.patch_(f"/api/projects/{beta['id']}/config", {"workflow": {"max_iterations": 2}, "agents": {"developer": "codex"}})
        self.assertEqual((response.status, saved["workflow"]["max_iterations"], saved["agents"]), (200, 2, {"developer": "codex"}))
        self.assertEqual((Path(alpha["home"]) / "config.json").read_bytes(), before)
        task_id, path = self.add_task(beta, "In beta")
        self.assertTrue(path.is_relative_to(Path(beta["home"])))
        self.assertEqual(self.get(f"/api/projects/{beta['id']}/tasks/{task_id}")[0].status, 200)
        self.assertEqual(self.get(f"/api/projects/{alpha['id']}/tasks/{task_id}")[0].status, 404)
        self.assertEqual(self.get(f"/api/projects/{alpha['id']}/tasks/{task_id}/files")[0].status, 404)
        self.assertEqual(self.post(f"/api/projects/{alpha['id']}/tasks/{task_id}/run")[0].status, 404)
        snapshot = self.get("/api/snapshot")[1]
        self.assertEqual([(task["project"], task["title"], task["max_iterations"]) for task in snapshot["tasks"]], [(beta["id"], "In beta", 2)])
        self.assertEqual({project["id"]: project["config"]["max_iterations"] for project in snapshot["projects"]}, {alpha["id"]: 6, beta["id"]: 2})

    def test_removing_a_project_with_an_active_task_is_refused(self):
        project, _ = self.add_project()
        task_id, path = self.add_task(project)
        (path / ".run.lock").write_text("{}", encoding="utf-8")
        response, data = self.delete(f"/api/projects/{project['id']}")
        self.assertEqual((response.status, data["code"]), (409, "conflict"))
        self.assertEqual(len(self.get("/api/snapshot")[1]["projects"]), 1)

    def test_folder_listing_shows_folder_names_only(self):
        repo = make_repo(self.root / "repos" / "alpha")
        (self.root / "repos" / "plain").mkdir()
        (self.root / "repos" / ".hidden").mkdir()
        (self.root / "repos" / "secret.txt").write_text("do not list", encoding="utf-8")
        response, data = self.post("/api/fs/list", {"path": str(self.root / "repos")})
        self.assertEqual(response.status, 200)
        self.assertEqual([(entry["name"], entry["repo"]) for entry in data["entries"]], [("alpha", True), ("plain", False)])
        self.assertEqual((data["path"], data["parent"], data["repo"]), (str(self.root / "repos"), str(self.root), False))
        self.assertNotIn("do not list", json.dumps(data))
        self.assertTrue(self.post("/api/fs/list", {"path": str(repo)})[1]["repo"])
        self.assertEqual(self.post("/api/fs/list", {})[1]["path"], str(Path.home().resolve()))
        self.assertEqual(self.post("/api/fs/list", {"path": "relative"})[0].status, 400)
        self.assertEqual(self.post("/api/fs/list", {"path": str(self.root / "missing")})[0].status, 404)
        self.assertEqual(self.post("/api/fs/list", {"path": str(self.root / "repos" / "secret.txt")})[0].status, 404)
        with self.assertRaises(ValueError):
            browse(7)


class SettingsTests(ServerCase):
    def test_global_settings_persist_across_restarts(self):
        project, _ = self.add_project()
        response, saved = self.patch_("/api/settings", {"theme": "light", "defaults": {"developer": "codex"}, "selected_project": project["id"]})
        self.assertEqual((response.status, saved["defaults"]), (200, {"developer": "codex", "reviewer": "codex"}))
        for bad in ({"theme": "neon"}, {"defaults": {"developer": "gpt"}}, {"selected_project": "P-00000000"}, {"token": "x"}):
            with self.subTest(patch=bad):
                self.assertIn(self.patch_("/api/settings", bad)[0].status, (400, 404))
        self.stop()
        self.start()
        self.assertEqual(self.get("/api/settings")[1], saved)
        self.assertEqual(self.get("/api/snapshot")[1]["settings"]["selected_project"], project["id"])
        self.assertNotIn(self.server.token, (self.home / "settings.json").read_text(encoding="utf-8"))

    def test_test_settings_require_explicit_trust_every_time(self):
        project, _ = self.add_project()
        file = Path(project["home"]) / "config.json"
        url = f"/api/projects/{project['id']}/config"
        before = file.read_bytes()
        refused = [
            {"tests": {"enabled": True, "trust_acknowledged": False, "commands": ["python -m pytest -q"]}},
            {"tests": {"enabled": True, "commands": ["python -m pytest -q"]}},  # consent is never implied
            {"tests": {"enabled": "yes", "trust_acknowledged": True, "commands": ["pytest"]}},
            {"tests": {"enabled": True, "trust_acknowledged": 1, "commands": ["pytest"]}},
            {"tests": {"enabled": True, "trust_acknowledged": True, "commands": []}},
            {"tests": {"enabled": True, "trust_acknowledged": True, "commands": [["pytest"]]}},
            {"tests": {"enabled": True, "trust_acknowledged": True, "commands": ['pytest "unclosed']}},
            {"tests": {"enabled": True, "trust_acknowledged": True, "commands": ["pytest"], "shell": True}},
        ]
        for body in refused:
            with self.subTest(body=body):
                response, data = self.patch_(url, body)
                self.assertEqual(response.status, 400, data)
                self.assertEqual(file.read_bytes(), before)
        response, saved = self.patch_(url, {"tests": {"enabled": True, "trust_acknowledged": True,
                                                       "commands": ["python -m pytest -q", "", 'ruff check "src dir"']}})
        self.assertEqual(response.status, 200, saved)
        self.assertEqual(config(Path(project["home"]))["tests"], {"enabled": True, "trust_acknowledged": True,
                         "commands": [["python", "-m", "pytest", "-q"], ["ruff", "check", "src dir"]]})
        self.assertEqual(split_command(saved["tests"]["command_lines"][1]), ["ruff", "check", "src dir"])
        # Saved consent does not carry over to a later edit that leaves it out.
        enabled = file.read_bytes()
        self.assertEqual(self.patch_(url, {"tests": {"enabled": True, "commands": ["rm -rf /"]}})[0].status, 400)
        self.assertEqual(file.read_bytes(), enabled)
        self.assertEqual(self.patch_(url, {"tests": {"enabled": False, "trust_acknowledged": False, "commands": []}})[0].status, 200)
        self.assertFalse(config(Path(project["home"]))["tests"]["enabled"])

    def test_every_section_is_validated_before_anything_is_written(self):
        project, _ = self.add_project()
        file = Path(project["home"]) / "config.json"
        url = f"/api/projects/{project['id']}/config"
        before = file.read_bytes()
        for body in ({"workflow": {"max_iterations": 0}}, {"workflow": {"max_iterations": 51}}, {"workflow": {"max_iterations": True}},
                     {"workflow": {"agent_timeout_seconds": "900"}}, {"workflow": {"allow_no_changes": "no"}}, {"workflow": {"shell": True}},
                     {"rag": {"max_chunks": 0}}, {"rag": {"max_chars": 5}}, {"rag": {"enabled": 1}}, {"rag": {"model": "x"}},
                     {"recovery": {"quota_only": False}}, {"recovery": {"max_consecutive_retries": 11}}, {"recovery": {"enabled": "true"}},
                     {"recovery": {"initial_backoff_seconds": 600, "max_backoff_seconds": 300}}, {"recovery": {"retry_everything": True}},
                     {"agents": {"developer": "gpt"}}, {"agents": {"judge": "claude"}}, {"repository": "/elsewhere"}, {"version": 2},
                     {"workflow": []}, {"workflow": {"max_iterations": 3}, "rag": {"max_chunks": 0}}):
            with self.subTest(body=body):
                response, data = self.patch_(url, body)
                self.assertEqual((response.status, data["code"]), (400, "invalid"), data)
                self.assertEqual(file.read_bytes(), before)
        response, saved = self.patch_(url, {"workflow": {"max_iterations": 4, "allow_no_changes": True},
                                            "rag": {"enabled": False, "max_chunks": 3},
                                            "recovery": {"enabled": True, "max_consecutive_retries": 2}})
        self.assertEqual(response.status, 200, saved)
        cfg = config(Path(project["home"]))
        self.assertEqual((cfg["workflow"]["max_iterations"], cfg["workflow"]["allow_no_changes"], cfg["workflow"]["agent_timeout_seconds"]), (4, True, 900))
        self.assertEqual(cfg["rag"], {"enabled": False, "max_chunks": 3, "max_chars": 12000})
        self.assertEqual((cfg["recovery"]["enabled"], cfg["recovery"]["max_consecutive_retries"], cfg["recovery"]["quota_only"]), (True, 2, True))
        self.assertEqual(saved["limits"]["recovery"]["max_consecutive_retries"], [1, 10])
        self.assertEqual(self.patch_(url, {"agents": {"developer": "codex", "reviewer": None}})[1]["agents"], {"developer": "codex"})
        self.assertEqual(self.patch_(url, {"agents": None})[1]["agents"], {})
        self.assertNotIn("agents", read_json(file))

    def test_a_0_1_configuration_keeps_its_shape_until_a_section_is_edited(self):
        legacy = self.root / "legacy"
        initialize(legacy, make_repo(self.root / "repos" / "old"))
        raw = read_json(legacy / "config.json")
        del raw["rag"], raw["recovery"]
        save_json(legacy / "config.json", raw)
        project = self.post("/api/projects/import", {"home": str(legacy)})[1]
        url = f"/api/projects/{project['id']}/config"
        view = self.get(f"/api/projects/{project['id']}")[1]["settings"]
        self.assertEqual((view["rag"]["enabled"], view["recovery"]["enabled"]), (False, False))
        self.assertEqual(self.patch_(url, {"workflow": {"max_iterations": 5}})[0].status, 200)
        saved = read_json(legacy / "config.json")
        self.assertEqual((saved["workflow"]["max_iterations"], "rag" in saved, "recovery" in saved), (5, False, False))
        self.assertEqual(self.patch_(url, {"rag": {"enabled": True}})[0].status, 200)
        self.assertEqual(read_json(legacy / "config.json")["rag"], {"enabled": True, "max_chunks": 8, "max_chars": 12000})

    def test_an_unreadable_configuration_is_reported_and_never_overwritten(self):
        project, _ = self.add_project()
        file = Path(project["home"]) / "config.json"
        file.write_text("{broken", encoding="utf-8")
        info = next(item for item in self.get("/api/snapshot")[1]["projects"] if item["id"] == project["id"])
        self.assertIsNone(info["config"])
        self.assertTrue(info["config_error"])
        self.assertEqual(self.patch_(f"/api/projects/{project['id']}/config", {"workflow": {"max_iterations": 3}})[0].status, 400)
        self.assertEqual(file.read_text(encoding="utf-8"), "{broken")
        self.assertIsNone(self.get(f"/api/projects/{project['id']}")[1]["settings"])

    def test_index_can_be_updated_on_request_without_any_provider(self):
        project, repo = self.add_project()
        self.assertEqual(self.get(f"/api/projects/{project['id']}")[1]["index"]["last"], None)
        with patch("patchrondo.providers.execute", side_effect=AssertionError("provider call")):
            self.assertEqual(self.post(f"/api/projects/{project['id']}/index")[0].status, 202)
            self.assertTrue(wait_for(lambda: (self.get(f"/api/projects/{project['id']}")[1]["index"]["last"] or {}).get("finished_at")))
        index = self.get(f"/api/projects/{project['id']}")[1]["index"]
        self.assertEqual((index["running"], index["last"]["status"], index["last"]["stats"]["files"]), (False, "ok", 1))
        self.assertEqual((index["database"]["exists"], index["database"]["files"]), (True, 1))
        self.assertTrue(Path(index["database"]["path"]).is_relative_to(Path(project["home"])))


class TaskTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.project, self.repo = self.add_project()
        self.url = f"/api/projects/{self.project['id']}/tasks"

    def test_create_with_validation_and_overrides(self):
        for body in ({"title": 3}, {"title": "x", "description": "y", "developer": "gpt", "reviewer": "codex"},
                     {"title": " ", "description": "y", "developer": "claude", "reviewer": "codex"},
                     {"title": "x" * 201, "description": "y", "developer": "claude", "reviewer": "codex"},
                     {"title": "x", "description": "y", "developer": "claude", "reviewer": "codex", "acceptance": "one"},
                     {"title": "x", "description": "y", "developer": "claude", "reviewer": "codex", "overrides": {"tests": {"enabled": True}}},
                     {"title": "x", "description": "y", "developer": "claude", "reviewer": "codex", "overrides": {"workflow": {"max_iterations": 99}}}):
            with self.subTest(body=body):
                self.assertEqual(self.post(self.url, body)[0].status, 400)
        self.assertFalse(any((Path(self.project["home"]) / "tasks").glob("T-*")))
        task_id, path = self.add_task(self.project, "Add cache", acceptance=[" Fast ", "", "Tested"],
                                      overrides={"workflow": {"max_iterations": 2}, "recovery": {"enabled": True}})
        state = read_json(path / "state.json")
        self.assertEqual((state["status"], state["overrides"]), ("ready", {"workflow": {"max_iterations": 2}, "recovery": {"enabled": True}}))
        self.assertIn("- Fast\n- Tested\n", (path / "task.md").read_text(encoding="utf-8"))
        detail = self.get(f"{self.url}/{task_id}")[1]
        self.assertEqual((detail["effective"]["max_iterations"], detail["effective"]["recovery_enabled"], detail["effective"]["tests_enabled"]), (2, True, False))
        self.assertEqual((detail["summary"]["runtime"]["activity"], detail["summary"]["max_iterations"], detail["worktree_exists"]), ("ready", 2, True))
        self.assertEqual(detail["documents"]["handoff"], None)
        for bad in ("..%2Fconfig", "T-0123456789AB", "T-aaaaaaaaaaaa", "T-%2e%2e"):
            self.assertEqual(self.get(f"{self.url}/{bad}")[0].status, 404)

    def test_a_dirty_repository_is_reported_when_creating_a_task(self):
        (self.repo / "uncommitted.txt").write_text("x", encoding="utf-8")
        response, data = self.post(self.url, {"title": "x", "description": "y", "developer": "claude", "reviewer": "codex"})
        self.assertEqual(response.status, 400)
        self.assertIn("not clean", data["error"])

    def test_logs_are_listed_and_read_in_bounded_chunks(self):
        task_id, path = self.add_task(self.project)
        self.assertEqual(self.get(f"{self.url}/{task_id}")[1]["logs"], [])
        text = "".join(f"riga {index} ✓ è\n" for index in range(400))
        (path / "ui-run.log").write_bytes(text.encode("utf-8"))
        run_dir = path / "runs" / "iteration-002"
        run_dir.mkdir(parents=True)
        (run_dir / "developer.stdout.log").write_text("developer output", encoding="utf-8")
        (run_dir / "developer.active-process.json").write_text("{}", encoding="utf-8")
        (run_dir / "review.schema.json").write_text("{}", encoding="utf-8")
        logs = self.get(f"{self.url}/{task_id}")[1]["logs"]
        self.assertEqual([(entry["id"], entry["group"]) for entry in logs], [("run", "Run"), ("iteration-002/developer.stdout.log", "Iteration 2")])
        self.assertNotIn("file", logs[0])
        size = len(text.encode("utf-8"))
        collected, offset = "", 0
        while True:  # sequential chunks never split a character and add up to the file
            chunk = self.get(f"{self.url}/{task_id}/log?file=run&offset={offset}&limit=97")[1]
            self.assertNotIn("\ufffd", chunk["text"])
            collected += chunk["text"]
            offset = chunk["next"]
            if chunk["eof"]:
                break
        self.assertEqual((collected, offset), (text, size))
        tail = self.get(f"{self.url}/{task_id}/log?file=run&offset=-50&limit=1000")[1]
        self.assertTrue(text.endswith(tail["text"]) and tail["eof"] and tail["size"] == size and "\ufffd" not in tail["text"])
        self.assertEqual(self.get(f"{self.url}/{task_id}/log?file=run&offset={size}")[1]["text"], "")
        self.assertEqual(self.get(f"{self.url}/{task_id}/log?file=iteration-002/developer.stdout.log")[1]["text"], "developer output")
        self.assertLessEqual(len(self.get(f"{self.url}/{task_id}/log?file=run&limit=99999999")[1]["text"]), 256_000)
        for name in ("../state.json", "state.json", "iteration-002/../../state.json", "iteration-002/review.schema.json",
                     "iteration-002/developer.active-process.json", "..%2F..%2Fconfig.json", "C:%5CWindows%5Cwin.ini", "RUN"):
            with self.subTest(file=name):
                self.assertEqual(self.get(f"{self.url}/{task_id}/log?file={name}")[0].status, 404)
        self.assertEqual(self.get(f"{self.url}/{task_id}/log?file=run&offset=abc")[0].status, 400)

    def test_changed_files_show_new_and_modified_files_without_leaving_the_worktree(self):
        task_id, path = self.add_task(self.project)
        worktree = Path(read_json(path / "state.json")["worktree"])
        self.assertEqual(self.get(f"{self.url}/{task_id}/files")[1]["files"], [])
        (worktree / "README.md").write_text("# Changed\n", encoding="utf-8", newline="\n")
        (worktree / "new file.txt").write_text("brand new\n", encoding="utf-8", newline="\n")
        (worktree / "blob.bin").write_bytes(b"\x00\x01\x02")
        outside = self.root / "outside-secret.txt"
        outside.write_text("must not be shown", encoding="utf-8")
        linked = False
        try:
            os.symlink(outside, worktree / "link.txt")
            linked = True
        except (OSError, NotImplementedError):
            pass  # symbolic links need a privilege on Windows
        data = self.get(f"{self.url}/{task_id}/files")[1]
        self.assertTrue(data["available"])
        self.assertEqual(data["branch"], f"patchrondo/{task_id}")
        self.assertIn({"code": "M", "path": "README.md"}, data["files"])
        self.assertIn("+# Changed", data["diff"])
        self.assertIn("README.md", data["stat"])
        untracked = {entry["path"].strip('"'): entry for entry in data["untracked"]}
        self.assertEqual(untracked["new file.txt"]["text"], "brand new\n")
        self.assertEqual((untracked["blob.bin"]["text"], untracked["blob.bin"]["note"]), (None, "Binary file"))
        if linked:
            self.assertIsNone(untracked["link.txt"]["text"])
        self.assertNotIn("must not be shown", json.dumps(data))
        state = read_json(path / "state.json")
        save_json(path / "state.json", {**state, "worktree": str(self.root / "gone")})
        gone = self.get(f"{self.url}/{task_id}/files")[1]
        self.assertEqual((gone["available"], gone["reason"]), (False, "The task worktree was not found"))

    def test_summaries_carry_process_truth_not_only_the_saved_status(self):
        task_id, path = self.add_task(self.project)
        state = read_json(path / "state.json")
        save_json(path / "state.json", {**state, "status": "running", "phase": "review"})
        summary = self.get("/api/snapshot")[1]["tasks"][0]
        self.assertEqual((summary["status"], summary["runtime"]["activity"], summary["runtime"]["active"]), ("running", "interrupted", False))
        (path / "tasks-are-not-listed-twice").touch()
        (Path(self.project["home"]) / "tasks" / "T-ffffffffffff").mkdir()
        (Path(self.project["home"]) / "tasks" / "T-ffffffffffff" / "state.json").write_text("{partial", encoding="utf-8")
        (Path(self.project["home"]) / "tasks" / "not-a-task").mkdir()
        self.assertEqual([task["id"] for task in self.get("/api/snapshot")[1]["tasks"]], [task_id])


class RunTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.project, _ = self.add_project()
        self.task_id, self.path = self.add_task(self.project)
        self.run_url = f"/api/projects/{self.project['id']}/tasks/{self.task_id}/run"

    def mocked(self, popen):
        popen.return_value.poll.return_value = None
        popen.return_value.pid = 4242
        popen.return_value.wait.side_effect = subprocess.TimeoutExpired("run", 1)

    def test_run_starts_a_separate_cli_process(self):
        with patch("patchrondo.app.subprocess.Popen") as popen, patch.dict(os.environ, {"PYTHONPATH": "relative-entry"}):
            self.mocked(popen)
            response, data = self.post(self.run_url)
            self.assertEqual((response.status, data["started"], data["auto_resume"]), (202, True, False))
            args = popen.call_args.args[0]
            self.assertEqual(args, [sys.executable, "-m", "patchrondo", "--home", self.project["home"], "run", self.task_id])
            kwargs = popen.call_args.kwargs
            self.assertEqual(kwargs["cwd"], Path(self.project["home"]))
            self.assertNotIn("shell", kwargs)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            # The child runs from the state directory, so every import path is absolute.
            self.assertEqual(kwargs["env"]["PYTHONPATH"].split(os.pathsep), [PACKAGE_ROOT, os.path.abspath("relative-entry")])
            self.assertEqual(kwargs["env"]["PATCHRONDO_UI_CHILD"], "1")
            self.assertTrue(kwargs.get("start_new_session") or kwargs.get("creationflags"))
            self.assertIn("run started from the dashboard", (self.path / "ui-run.log").read_text(encoding="utf-8"))

    def test_run_options_become_explicit_flags(self):
        with patch("patchrondo.app.subprocess.Popen") as popen:
            self.mocked(popen)
            popen.return_value.poll.return_value = 0  # each start below finds the previous child gone
            for body, flags, auto in (({"auto_resume": True}, ["--auto-resume"], True), ({"auto_resume": False}, ["--no-auto-resume"], False),
                                      ({"auto_resume": None}, [], False)):
                with self.subTest(body=body):
                    response, data = self.post(self.run_url, body)
                    self.assertEqual((response.status, data["auto_resume"]), (202, auto), data)
                    self.assertEqual(popen.call_args.args[0][7:], flags)
            for body in ({"auto_resume": "yes"}, {"unlock": "yes"}, {"shell": True}, {"argv": ["rm"]}, {"unlock": True}):
                with self.subTest(body=body):
                    self.assertIn(self.post(self.run_url, body)[0].status, (400, 409))
            self.assertEqual(popen.call_count, 3)
            self.patch_(f"/api/projects/{self.project['id']}/config", {"recovery": {"enabled": True}})
            self.assertEqual(self.post(self.run_url, {})[1]["auto_resume"], True)

    def test_run_is_refused_when_it_cannot_or_must_not_start(self):
        with patch("patchrondo.app.subprocess.Popen") as popen:
            self.mocked(popen)
            (self.path / ".run.lock").write_text("{}", encoding="utf-8")
            response, data = self.post(self.run_url)
            self.assertEqual((response.status, data["code"]), (409, "conflict"))
            self.assertEqual(self.post(self.run_url, {"unlock": True})[0].status, 409)  # holder cannot be verified
            (self.path / ".run.lock").unlink()
            state = read_json(self.path / "state.json")
            for status in ("done", "blocked"):
                save_json(self.path / "state.json", {**state, "status": status})
                self.assertEqual(self.post(self.run_url)[0].status, 409)
            save_json(self.path / "state.json", {**state, "worktree": str(self.root / "gone")})
            self.assertEqual(self.post(self.run_url)[0].status, 409)
            save_json(self.path / "state.json", state)
            self.assertEqual(self.post(f"/api/projects/{self.project['id']}/tasks/T-aaaaaaaaaaaa/run")[0].status, 404)
            self.assertEqual(self.post(f"/api/projects/P-00000000/tasks/{self.task_id}/run")[0].status, 404)
            (Path(self.project["home"]) / "config.json").write_text("{broken", encoding="utf-8")
            self.assertEqual(self.post(self.run_url)[0].status, 400)
            popen.assert_not_called()

    def test_unlock_is_passed_on_only_for_a_lock_whose_holder_is_verified_gone(self):
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait(30)
        save_json(self.path / ".run.lock", {"pid": gone.pid, "time": "x", "start": "linux:1"})
        with patch("patchrondo.app.subprocess.Popen") as popen:
            self.mocked(popen)
            self.assertEqual(self.post(self.run_url)[0].status, 409)  # never without an explicit request
            response, data = self.post(self.run_url, {"unlock": True})
            if os.name == "posix":
                self.assertEqual(response.status, 202, data)
                self.assertEqual(popen.call_args.args[0][7:], ["--unlock"])  # the engine repeats the check itself
            else:
                self.assertEqual(response.status, 409, data)  # the engine cannot verify processes on native Windows
                popen.assert_not_called()
        self.assertTrue((self.path / ".run.lock").exists())  # the interface itself never deletes a lock

    def test_simultaneous_starts_create_one_process(self):
        def slow(*args, **kwargs):
            time.sleep(0.4)
            return unittest.mock.DEFAULT

        with patch("patchrondo.app.subprocess.Popen", side_effect=slow) as popen:
            self.mocked(popen)
            results = []
            threads = [threading.Thread(target=lambda: results.append(self.post(self.run_url)[0].status)) for _ in range(5)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(15)
            self.assertEqual(sorted(results), [202, 409, 409, 409, 409])
            self.assertEqual(popen.call_count, 1)
            # The started child is still alive and has not taken the lock yet: a repeat is refused too.
            self.assertEqual(self.post(self.run_url)[0].status, 409)
            self.assertEqual(popen.call_count, 1)

    def test_a_waiting_process_blocks_a_second_automatic_run_only(self):
        from patchrondo import procinfo, runinfo
        state = read_json(self.path / "state.json")
        save_json(self.path / "state.json", {**state, "status": "paused", "last_error": {"kind": "quota", "message": "limit"},
                  "recovery": {"status": "scheduled", "resume_at": "2099-01-01T00:00:00+00:00", "consecutive_failures": 1,
                               "max_consecutive_retries": 3, "provider": "codex", "schedule_source": "backoff", "stop_reason": None}})
        save_json(self.path / runinfo.MARKERS / f"{os.getpid()}.json", {"pid": os.getpid(), "start": procinfo.identity(), "auto_resume": True})
        self.assertEqual(self.get("/api/snapshot")[1]["tasks"][0]["runtime"]["activity"], "waiting_retry")
        with patch("patchrondo.app.subprocess.Popen") as popen:
            self.mocked(popen)
            for body in ({}, {"auto_resume": True}):
                response, data = self.post(self.run_url, body)
                self.assertEqual(response.status, 409, data)
                self.assertIn("already waiting", data["error"])
            popen.assert_not_called()
            self.assertEqual(self.post(self.run_url, {"auto_resume": False})[0].status, 202)  # a deliberate manual run
            self.assertEqual(popen.call_args.args[0][-1], "--no-auto-resume")

    def test_run_that_fails_immediately_is_reported(self):
        def crash(*args, **kwargs):
            kwargs["stdout"].write(b"ModuleNotFoundError: No module named 'patchrondo'\n")
            kwargs["stdout"].flush()
            return unittest.mock.DEFAULT

        with patch("patchrondo.app.subprocess.Popen", side_effect=crash) as popen:
            popen.return_value.wait.return_value = 1
            popen.return_value.poll.return_value = 1
            response, data = self.post(self.run_url)
        self.assertEqual((response.status, data["code"]), (400, "failed"))
        self.assertIn("No module named 'patchrondo'", data["error"])
        with patch("patchrondo.app.subprocess.Popen") as popen:
            popen.return_value.pid = 4242
            popen.return_value.wait.return_value = 2  # paused or blocked at once is a normal outcome
            popen.return_value.poll.return_value = 2
            response, data = self.post(self.run_url)
        self.assertEqual((response.status, data.get("finished"), data.get("code")), (202, True, 2))

    def test_closing_waits_for_a_run_that_is_starting(self):
        def slow_start(*args, **kwargs):
            time.sleep(0.6)  # the interface is stopped while the run is still being started
            return unittest.mock.DEFAULT

        with patch("patchrondo.app.subprocess.Popen", side_effect=slow_start) as popen:
            self.mocked(popen)
            client = threading.Thread(target=self.post, args=(self.run_url,))
            client.start()
            time.sleep(0.2)
            self.server.shutdown()
            # What serve() returns on exit; the demo decides from it whether to delete files.
            runs = self.server.close_runs()
            client.join(5)
        self.assertEqual(runs, RunsAtExit([popen.return_value], 0))

    def test_timed_out_wait_reports_the_pending_start_and_demo_keeps_files(self):
        release = threading.Event()

        def stuck_start(*args, **kwargs):
            release.wait(5)  # still starting when the shutdown wait gives up
            return unittest.mock.DEFAULT

        with patch("patchrondo.app.subprocess.Popen", side_effect=stuck_start) as popen:
            self.mocked(popen)
            client = threading.Thread(target=self.post, args=(self.run_url,))
            client.start()
            time.sleep(0.2)
            self.server.shutdown()
            runs = self.server.close_runs(timeout=0.2)
            release.set()
            client.join(5)
        self.assertEqual(runs, RunsAtExit([], 1))
        self.assertTrue(load_demo().runs_may_be_active(self.home, runs))
        self.assertFalse(load_demo().runs_may_be_active(self.home, RunsAtExit([], 0)))

    def test_run_is_refused_while_closing(self):
        self.assertEqual(self.server.close_runs(), RunsAtExit([], 0))
        with patch("patchrondo.app.subprocess.Popen") as popen:
            response, data = self.post(self.run_url)
        self.assertEqual(response.status, 409)
        self.assertIn("shutting down", data["error"])
        popen.assert_not_called()


class RealProcessTests(ServerCase):
    """Synthetic child processes stand in for `patchrondo run`: real locks, real markers, no provider."""

    def setUp(self):
        grace = patch("patchrondo.app.STARTUP_GRACE_SECONDS", 0.3)
        grace.start()
        self.addCleanup(grace.stop)
        self.mode = "manual"
        super().setUp()
        self.project, _ = self.add_project()
        self.task_id, self.path = self.add_task(self.project)
        self.base = f"/api/projects/{self.project['id']}/tasks/{self.task_id}"
        self.addCleanup(lambda: (self.path / "release").touch())

    def start(self, **options):
        return super().start(run_command=lambda project, task_id, flags: [
            sys.executable, "-c", HOLDER, str(Path(project.home) / "tasks" / task_id), SOURCE, self.mode], **options)

    def activity(self):
        return self.get(self.base)[1]["summary"]["runtime"]

    def test_interface_restart_during_a_run_recovers_the_real_state(self):
        self.assertEqual(self.post(f"{self.base}/run")[0].status, 202)
        self.assertTrue(wait_for(lambda: (self.path / "holding").exists()))
        runtime = self.activity()
        self.assertEqual((runtime["activity"], runtime["lock"]["holder"], runtime["active"], runtime["can_start"]), ("running", "alive", True, False))
        self.assertEqual(self.post(f"{self.base}/run")[0].status, 409)
        runs = self.stop()
        self.assertEqual((len(runs.active), runs.pending), (1, 0))  # the run outlives the interface
        child = runs.active[0]
        self.assertIsNone(child.poll())

        self.start()  # a new interface process: it did not start this run and holds no handle to it
        self.assertEqual(self.app.children, {})
        runtime = self.activity()
        self.assertEqual((runtime["activity"], runtime["lock"]["holder"], runtime["can_start"]), ("running", "alive", False))
        self.assertEqual([entry["owned"] for entry in runtime["processes"]], [False])
        self.assertEqual(self.post(f"{self.base}/run")[0].status, 409)
        self.assertEqual(self.delete(f"/api/projects/{self.project['id']}")[0].status, 409)
        (self.path / "release").touch()
        child.wait(30)
        self.assertTrue(wait_for(lambda: self.activity()["activity"] == "ready"))
        self.assertEqual(self.activity()["processes"], [])

    def test_a_dead_runner_is_reported_as_a_stale_lock_not_as_running(self):
        self.assertEqual(self.post(f"{self.base}/run")[0].status, 202)
        self.assertTrue(wait_for(lambda: (self.path / "holding").exists()))
        pid = read_json(self.path / ".run.lock")["pid"]
        for child in self.app.children[(self.project["id"], self.task_id)]:
            child.kill()
            child.wait(30)
        if os.name == "nt":  # the venv launcher was killed; the interpreter it started is the lock holder
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)
        self.assertTrue(wait_for(lambda: self.activity()["activity"] == "stale_lock"))
        runtime = self.activity()
        self.assertEqual((runtime["lock"]["holder"], runtime["active"], runtime["needs_attention"], runtime["can_start"]), ("dead", False, True, False))
        self.assertEqual(runtime["can_unlock"], os.name == "posix")
        self.assertEqual(self.post(f"{self.base}/run")[0].status, 409)
        self.assertTrue((self.path / ".run.lock").exists())  # never removed by looking at it

    @unittest.skipUnless(os.name == "posix", "an interrupt can be delivered to a detached run only on POSIX")
    def test_stop_interrupts_the_verified_process_like_ctrl_c(self):
        self.mode = "auto"
        self.assertEqual(self.post(f"{self.base}/run")[0].status, 202)
        self.assertTrue(wait_for(lambda: (self.path / "holding").exists()))
        self.assertTrue(self.activity()["can_stop"])
        response, data = self.post(f"{self.base}/stop")
        self.assertEqual(response.status, 202, data)
        self.assertTrue(wait_for(lambda: (self.path / "interrupted").exists()))
        self.assertTrue(wait_for(lambda: self.activity()["activity"] == "ready"))
        self.assertFalse((self.path / ".run.lock").exists())
        response, data = self.post(f"{self.base}/stop")
        self.assertEqual((response.status, data["code"]), (409, "conflict"))

    @unittest.skipUnless(os.name == "nt", "native Windows only")
    def test_stop_is_refused_with_an_explanation_on_native_windows(self):
        self.assertEqual(self.post(f"{self.base}/run")[0].status, 202)
        self.assertTrue(wait_for(lambda: (self.path / "holding").exists()))
        self.assertFalse(self.activity()["can_stop"])
        response, data = self.post(f"{self.base}/stop")
        self.assertEqual(response.status, 409)
        self.assertIn("not available on native Windows", data["error"])
        self.assertFalse((self.path / "interrupted").exists())


class LiveTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.project, _ = self.add_project()
        self.task_id, self.path = self.add_task(self.project, "Watched")
        self.hub = self.server.hub
        # The CLI status check finishes in the background and is an event of its own: let it settle first.
        self.hub.snapshot()
        self.assertTrue(wait_for(lambda: self.app.providers.view()["providers"] is not None and not self.app.providers.view()["checking"]))
        self.hub.scan()

    def change(self, **values):
        state = read_json(self.path / "state.json")
        save_json(self.path / "state.json", {**state, **values})

    def test_snapshot_then_numbered_events_carry_complete_values(self):
        snapshot = self.hub.snapshot()
        self.assertEqual([task["title"] for task in snapshot["tasks"]], ["Watched"])
        self.assertEqual(self.hub.wait(snapshot["epoch"], snapshot["seq"], 0.05), [])
        self.change(title="Renamed", status="paused")
        self.hub.scan()
        events = self.hub.wait(snapshot["epoch"], snapshot["seq"], 0.05)
        self.assertEqual([(event["seq"], event["type"], event["key"]) for event in events],
                         [(snapshot["seq"] + 1, "task", f"{self.project['id']}/{self.task_id}")])
        self.assertEqual((events[0]["data"]["title"], events[0]["data"]["runtime"]["activity"]), ("Renamed", "paused"))
        self.hub.scan()
        self.assertEqual(self.hub.wait(snapshot["epoch"], events[-1]["seq"], 0.05), [])  # nothing changed, nothing sent
        # A lock appears without the state file changing: the process state is part of the view.
        (self.path / ".run.lock").write_text("{}", encoding="utf-8")
        self.hub.scan()
        locked = self.hub.wait(snapshot["epoch"], events[-1]["seq"], 0.05)
        self.assertEqual(locked[0]["data"]["runtime"]["activity"], "running_unverified")
        (self.path / ".run.lock").unlink()
        self.patch_(f"/api/projects/{self.project['id']}", {"name": "Other name"})
        self.hub.scan()
        kinds = [(event["type"], (event["data"] or {}).get("name")) for event in self.hub.wait(snapshot["epoch"], locked[-1]["seq"], 0.05)]
        self.assertIn(("project", "Other name"), kinds)
        seq = self.hub.snapshot()["seq"]
        self.delete(f"/api/projects/{self.project['id']}")
        self.hub.scan()
        removed = {(event["type"], event["key"]) for event in self.hub.wait(snapshot["epoch"], seq, 0.05)}
        self.assertEqual(removed, {("project_removed", self.project["id"]), ("task_removed", f"{self.project['id']}/{self.task_id}")})

    def test_a_rewrite_within_the_timestamp_granularity_is_still_seen(self):
        """Two saves can leave a file with the same time and size: a recently written file is always read again."""
        file = self.path / "state.json"
        with patch("patchrondo.app._signature", return_value=(time.time_ns(), 100, 1)):
            self.change(title="First")
            first = self.app._state(file)["title"]
            self.change(title="Other")
            second = self.app._state(file)["title"]
        self.assertEqual((first, second), ("First", "Other"))
        with patch("patchrondo.app._signature", return_value=(time.time_ns() - 10 ** 10, 100, 1)):
            self.change(title="Settled")
            self.assertEqual(self.app._state(file)["title"], "Settled")  # a different signature: read
            self.assertIs(self.app._state(file), self.app._state(file))  # unchanged and old enough: cached

    def test_a_client_that_missed_events_is_told_to_resynchronize(self):
        snapshot = self.hub.snapshot()
        self.assertIsNone(self.hub.wait("another-epoch", snapshot["seq"], 0.05))  # the server was restarted
        self.assertIsNone(self.hub.wait(snapshot["epoch"], snapshot["seq"] + 5, 0.05))  # a number from the future
        self.hub.events = type(self.hub.events)(self.hub.events, maxlen=2)
        for index in range(5):
            self.change(title=f"Change {index}")
            self.hub.scan()
        self.assertIsNone(self.hub.wait(snapshot["epoch"], snapshot["seq"], 0.05))  # the gap is no longer kept
        latest = self.hub.snapshot()
        self.assertEqual((latest["seq"], latest["tasks"][0]["title"]), (snapshot["seq"] + 5, "Change 4"))
        self.assertEqual(len(self.hub.wait(snapshot["epoch"], latest["seq"] - 2, 0.05)), 2)

    def stream(self, query):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", f"/api/events?{query}", headers={"X-PatchRondo-Token": self.server.token})
        response = conn.getresponse()
        self.addCleanup(conn.close)
        return response

    def frame(self, response):
        lines = []
        while True:
            line = response.fp.readline().decode("utf-8")
            if line in ("\n", ""):
                return lines
            lines.append(line.rstrip("\n"))

    def test_event_stream_delivers_changes_within_a_second(self):
        snapshot = self.get("/api/snapshot")[1]
        response = self.stream(f"epoch={snapshot['epoch']}&since={snapshot['seq']}")
        self.assertEqual(response.status, 200)
        self.assertTrue(response.getheader("Content-Type").startswith("text/event-stream"))
        self.assertIn("default-src 'none'", response.getheader("Content-Security-Policy"))
        started = time.monotonic()
        self.change(title="Seen live")
        frame = self.frame(response)
        elapsed = time.monotonic() - started
        self.assertEqual(frame[0], f"id: {snapshot['seq'] + 1}")
        event = json.loads(frame[1].removeprefix("data: "))
        self.assertEqual((event["type"], event["data"]["title"]), ("task", "Seen live"))
        self.assertLess(elapsed, 1.5)
        stale = self.stream("epoch=old&since=0")
        self.assertEqual(self.frame(stale), ["event: resync", "data: {}"])
        self.assertEqual(stale.fp.readline(), b"")  # and the stream ends: the client loads a snapshot
        self.assertEqual(self.frame(self.stream(f"epoch={snapshot['epoch']}&since=abc")), ["event: resync", "data: {}"])

    def test_closing_ends_open_streams(self):
        snapshot = self.get("/api/snapshot")[1]
        response = self.stream(f"epoch={snapshot['epoch']}&since={snapshot['seq']}")
        self.assertTrue(wait_for(lambda: self.hub.listeners == 1))
        started = time.monotonic()
        self.stop()
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(response.fp.read(), b"")


class DemoTests(unittest.TestCase):
    def test_demo_creates_two_projects_with_tests_disabled_and_untrusted(self):
        from patchrondo.app import App
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            home = load_demo().setup(Path(tmp).resolve())
            app = App(home)
            projects = app.ws.projects()
            self.assertEqual([project.name for project in projects], ["Shop API", "Docs site"])
            for project in projects:
                tests = config(project.home)["tests"]
                self.assertFalse(tests["enabled"] or tests["trust_acknowledged"])
            with patch("patchrondo.app.doctor.check_all", return_value={}):
                tasks = app.collect()["tasks"].values()
            self.assertEqual({task["status"] for task in tasks}, {"ready", "running", "paused", "done", "blocked"})
            # Saved states only: nothing in the demo may be presented as a live process.
            self.assertFalse(any(task["runtime"]["active"] for task in tasks))
            activities = {task["title"]: task["runtime"]["activity"] for task in tasks}
            self.assertEqual(activities["Handle JWT expiration"], "interrupted")
            self.assertEqual(activities["Paginate the /orders endpoint"], "plan_only")
            self.assertTrue(app.ws.settings()["onboarding_completed"])

    def run_demo(self, outcome, *args, lock=False):
        """Run the demo's main() with a fake serve(); return (exit code, whether files were deleted)."""
        demo = load_demo()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp).resolve() / "demo"
            root.mkdir()

            def fake_serve(home, *_, **__):
                if lock:
                    task = next(home.glob("projects/*/tasks/T-000000000005"))
                    (task / ".run.lock").write_text("{}", encoding="utf-8")
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome

            with patch.object(demo.tempfile, "mkdtemp", return_value=str(root)), \
                    patch.object(demo, "serve", side_effect=fake_serve), \
                    patch.object(demo.shutil, "rmtree") as rmtree, \
                    patch("sys.stdout", new_callable=io.StringIO):
                try:
                    code = demo.main(["--no-browser", *args])
                except KeyboardInterrupt:
                    code = None  # escaped main(): recorded so the deletion check still runs
            deleted = any(call.args[0] == root for call in rmtree.call_args_list)
        return code, deleted

    def test_demo_deletes_files_only_after_a_confirmed_clean_exit(self):
        self.assertEqual(self.run_demo(RunsAtExit([], 0)), (0, True))
        # A second Ctrl+C while closing gives no result: files must be kept.
        self.assertEqual(self.run_demo(KeyboardInterrupt()), (130, False))
        self.assertEqual(self.run_demo(RunsAtExit([], 1)), (0, False))
        self.assertEqual(self.run_demo(RunsAtExit([], 0), lock=True), (0, False))
        self.assertEqual(self.run_demo(RunsAtExit([], 0), "--keep"), (0, False))


if __name__ == "__main__":
    unittest.main()
