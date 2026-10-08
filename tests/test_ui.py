import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import unittest.mock
from unittest.mock import patch

from patchrondo.cli import main
from patchrondo.core import DEFAULT_CONFIG, config
from patchrondo.storage import read_json, save_json
from patchrondo.ui import PACKAGE_ROOT, DashboardServer, RunsAtExit, join_command, overview, split_command, task_detail

TASK = "T-0123456789ab"


def make_home(root: Path, configured: bool = False) -> Path:
    home = root / "state"
    save_json(home / "tasks" / TASK / "state.json", {
        "id": TASK, "title": "Handle <b>expiry</b>", "status": "paused", "phase": "review",
        "iteration": 2, "developer": "claude", "reviewer": "codex",
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:05:00+00:00",
        "history": [], "tests": [], "review": {"verdict": "CHANGES_REQUESTED", "summary": "", "issues": []},
        "last_error": {"kind": "timeout", "message": "Agent timed out"}})
    (home / "tasks" / TASK / "task.md").write_text("# Handle expiry\n", encoding="utf-8")
    (home / "tasks" / "T-ffffffffffff").mkdir(parents=True)
    (home / "tasks" / "T-ffffffffffff" / "state.json").write_text("{partial", encoding="utf-8")
    if configured:
        save_json(home / "config.json", {**json.loads(json.dumps(DEFAULT_CONFIG)),
                                         "repository": str((root / "repo").resolve())})
    return home


def load_demo():
    spec = importlib.util.spec_from_file_location(
        "demo_dashboard", Path(__file__).resolve().parents[1] / "tools" / "demo_dashboard.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    return demo


class DataTests(unittest.TestCase):
    def test_overview_lists_readable_tasks_without_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = overview(make_home(Path(tmp)))
        self.assertIsNone(data["config"])
        self.assertIn("patchrondo init", data["config_error"])
        self.assertEqual([t["id"] for t in data["tasks"]], [TASK])
        self.assertEqual(data["tasks"][0]["verdict"], "CHANGES_REQUESTED")

    def test_detail_validates_task_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(Path(tmp))
            detail = task_detail(home, TASK)
            self.assertEqual(detail["documents"]["task"], "# Handle expiry\n")
            self.assertIsNone(detail["documents"]["report"])
            self.assertFalse(detail["locked"])
            for bad in ("../config", "T-0123456789AB", "T-ffffffffffff"):
                self.assertIsNone(task_detail(home, bad))

    def test_ui_command_requires_initialized_configuration(self):
        with tempfile.TemporaryDirectory() as tmp, patch("patchrondo.ui.DashboardServer") as server, \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(main(["--home", str(make_home(Path(tmp))), "ui", "--no-browser"]), 1)
        server.assert_not_called()
        self.assertIn("patchrondo init --repo", stderr.getvalue())

    def test_demo_script_creates_valid_configuration_and_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = load_demo().setup(Path(tmp))
            tests = config(home)["tests"]
            self.assertFalse(tests["enabled"] or tests["trust_acknowledged"])
            statuses = {t["status"] for t in overview(home)["tasks"]}
        self.assertEqual(statuses, {"ready", "running", "paused", "done", "blocked"})

    def run_demo(self, outcome, *args, lock=False):
        """Run the demo's main() with a fake serve(); return (exit code, whether files were deleted)."""
        demo = load_demo()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            root.mkdir()

            def fake_serve(home, *_, **__):
                if lock:
                    (home / "tasks" / "T-000000000005" / ".run.lock").write_text("{}", encoding="utf-8")
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

    def test_command_lines_keep_windows_paths(self):
        self.assertEqual(split_command(r'pytest tests\test_api.py "C:\My Dir\x.py"', windows=True),
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


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = make_home(Path(self.tmp.name), configured=True)
        self.server = DashboardServer(self.home, 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, token=None, host=None, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        all_headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            all_headers["X-PatchRondo-Token"] = token
        if body is not None:
            all_headers["Content-Type"] = "application/json"
            body = json.dumps(body).encode()
        all_headers.update(headers or {})
        conn.request(method, path, body=body, headers=all_headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, data

    def get(self, path, token=None, host=None):
        return self.request("GET", path, token, host)

    def post(self, path, body, **kwargs):
        return self.request("POST", path, self.server.token, body=body, **kwargs)

    def test_page_is_served_with_nonce_csp(self):
        response, body = self.get("/")
        self.assertEqual(response.status, 200)
        csp = response.getheader("Content-Security-Policy")
        nonce = csp.split("'nonce-")[1].split("'")[0]
        self.assertIn(f'nonce="{nonce}"'.encode(), body)
        self.assertNotIn(b"__NONCE__", body)
        self.assertNotIn(self.server.token.encode(), body)

    def test_mascot_is_inlined_without_extra_requests(self):
        response, body = self.get("/")
        self.assertIn("img-src 'self' data:", response.getheader("Content-Security-Policy"))
        self.assertNotIn(b"__RONDO", body)
        self.assertEqual(body.count(b'url("data:image/webp;base64,'), 3)
        self.assertIn(b'<link rel="icon" href="data:image/webp;base64,', body)
        self.assertEqual(self.get("/static/rondo.webp")[0].status, 404)

    def test_api_requires_session_token(self):
        self.assertEqual(self.get("/api/overview")[0].status, 401)
        self.assertEqual(self.get("/api/overview", "wrong")[0].status, 401)
        self.assertEqual(self.request("POST", "/api/tasks", body={})[0].status, 401)
        response, body = self.get("/api/overview", self.server.token)
        self.assertEqual(response.status, 200)
        data = json.loads(body)
        self.assertEqual(data["tasks"][0]["title"], "Handle <b>expiry</b>")
        self.assertFalse(data["config"]["tests_enabled"])

    def test_task_endpoint(self):
        response, body = self.get(f"/api/tasks/{TASK}", self.server.token)
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(body)["state"]["iteration"], 2)
        self.assertEqual(self.get("/api/tasks/..%2Fconfig", self.server.token)[0].status, 404)
        self.assertEqual(self.get("/api/tasks/T-aaaaaaaaaaaa", self.server.token)[0].status, 404)

    def test_foreign_host_is_rejected(self):
        self.assertEqual(self.get("/", host="evil.example")[0].status, 403)
        self.assertEqual(self.get("/api/overview", self.server.token, f"evil.example:{self.port}")[0].status, 403)

    def test_cross_origin_and_non_json_writes_are_rejected(self):
        evil = {"Origin": "http://evil.example"}
        self.assertEqual(self.post("/api/tasks", {}, headers=evil)[0].status, 403)
        plain = {"Content-Type": "text/plain"}
        self.assertEqual(self.post("/api/tasks", {}, headers=plain)[0].status, 415)
        self.assertEqual(self.request("PUT", "/api/tasks", self.server.token)[0].status, 501)

    def test_create_task(self):
        with patch("patchrondo.ui.create_task", return_value=("T-111111111111", Path("w"))) as create:
            response, body = self.post("/api/tasks", {
                "title": "Add cache", "description": "Cache lookups", "acceptance": [" Fast ", "", "Tested"],
                "developer": "codex", "reviewer": "claude"}, headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(response.status, 201, body)
        self.assertEqual(json.loads(body)["id"], "T-111111111111")
        create.assert_called_once_with(self.home, title="Add cache", description="Cache lookups",
                                       acceptance=["Fast", "Tested"], developer="codex", reviewer="claude")
        response, body = self.post("/api/tasks", {"title": 3})
        self.assertEqual(response.status, 400)
        self.assertIn("must be strings", json.loads(body)["error"])

    def test_run_starts_a_separate_cli_process(self):
        with patch("patchrondo.ui.subprocess.Popen") as popen, \
                patch.dict(os.environ, {"PYTHONPATH": "relative-entry"}):
            popen.return_value.poll.return_value = None
            popen.return_value.wait.side_effect = subprocess.TimeoutExpired("run", 1)
            response, _ = self.post(f"/api/tasks/{TASK}/run", {})
            self.assertEqual(response.status, 202)
            args = popen.call_args.args[0]
            self.assertEqual(args[-4:], ["--home", str(self.home), "run", TASK])
            # The child runs from the state directory, so every import path is absolute.
            paths = popen.call_args.kwargs["env"]["PYTHONPATH"].split(os.pathsep)
            self.assertEqual(paths, [PACKAGE_ROOT, os.path.abspath("relative-entry")])
            self.assertIn("run started from the dashboard",
                          (self.home / "tasks" / TASK / "ui-run.log").read_text(encoding="utf-8"))

            (self.home / "tasks" / TASK / ".run.lock").write_text("{}", encoding="utf-8")
            self.assertEqual(self.post(f"/api/tasks/{TASK}/run", {})[0].status, 409)
            (self.home / "tasks" / TASK / ".run.lock").unlink()

            state = read_json(self.home / "tasks" / TASK / "state.json")
            save_json(self.home / "tasks" / TASK / "state.json", {**state, "status": "done"})
            self.assertEqual(self.post(f"/api/tasks/{TASK}/run", {})[0].status, 409)
            self.assertEqual(self.post("/api/tasks/T-aaaaaaaaaaaa/run", {})[0].status, 404)
            self.assertEqual(popen.call_count, 1)

    def test_closing_waits_for_a_run_that_is_starting(self):
        def slow_start(*args, **kwargs):
            time.sleep(0.6)  # the dashboard is stopped while the run is still being started
            return unittest.mock.DEFAULT

        with patch("patchrondo.ui.subprocess.Popen", side_effect=slow_start) as popen:
            popen.return_value.poll.return_value = None
            popen.return_value.wait.side_effect = subprocess.TimeoutExpired("run", 1)
            client = threading.Thread(target=self.post, args=(f"/api/tasks/{TASK}/run", {}))
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

        with patch("patchrondo.ui.subprocess.Popen", side_effect=stuck_start) as popen:
            popen.return_value.poll.return_value = None
            popen.return_value.wait.side_effect = subprocess.TimeoutExpired("run", 1)
            client = threading.Thread(target=self.post, args=(f"/api/tasks/{TASK}/run", {}))
            client.start()
            time.sleep(0.2)
            self.server.shutdown()
            runs = self.server.close_runs(timeout=0.2)
            release.set()
            client.join(5)
        self.assertEqual(runs, RunsAtExit([], 1))
        self.assertTrue(load_demo().runs_may_be_active(self.home, runs))
        self.assertFalse(load_demo().runs_may_be_active(self.home, RunsAtExit([], 0)))

    def test_idle_connection_does_not_block_closing(self):
        idle = socket.create_connection(("127.0.0.1", self.port))  # never sends a request
        try:
            time.sleep(0.2)
            closer = threading.Thread(target=lambda: (self.server.shutdown(), self.server.close_runs(),
                                                      self.server.server_close()))
            started = time.monotonic()
            closer.start()
            closer.join(5)
            self.assertFalse(closer.is_alive(), "closing blocked on an idle connection")
            self.assertLess(time.monotonic() - started, 3)
        finally:
            idle.close()

    def test_run_is_refused_while_closing(self):
        self.assertEqual(self.server.close_runs(), RunsAtExit([], 0))
        with patch("patchrondo.ui.subprocess.Popen") as popen:
            response, body = self.post(f"/api/tasks/{TASK}/run", {})
        self.assertEqual(response.status, 409)
        self.assertIn("shutting down", json.loads(body)["error"])
        popen.assert_not_called()

    def test_run_that_fails_immediately_is_reported(self):
        def crash(*args, **kwargs):
            kwargs["stdout"].write(b"ModuleNotFoundError: No module named 'patchrondo'\n")
            kwargs["stdout"].flush()
            return unittest.mock.DEFAULT

        with patch("patchrondo.ui.subprocess.Popen", side_effect=crash) as popen:
            popen.return_value.wait.return_value = 1
            response, body = self.post(f"/api/tasks/{TASK}/run", {})
        self.assertEqual(response.status, 400)
        self.assertIn("No module named 'patchrondo'", json.loads(body)["error"])

    def test_test_settings_require_explicit_trust(self):
        before = (self.home / "config.json").read_text(encoding="utf-8")
        response, body = self.post("/api/settings/tests", {
            "enabled": True, "trust_acknowledged": False, "commands": ["python -m pytest -q"]})
        self.assertEqual(response.status, 400)
        self.assertIn("trust_acknowledged", json.loads(body)["error"])
        self.assertEqual((self.home / "config.json").read_text(encoding="utf-8"), before)
        self.assertEqual(self.post("/api/settings/tests", {
            "enabled": "yes", "trust_acknowledged": True, "commands": []})[0].status, 400)

        response, body = self.post("/api/settings/tests", {
            "enabled": True, "trust_acknowledged": True, "commands": ["python -m pytest -q", "", 'ruff check "src dir"']})
        self.assertEqual(response.status, 200, body)
        self.assertEqual(config(self.home)["tests"]["commands"],
                         [["python", "-m", "pytest", "-q"], ["ruff", "check", "src dir"]])
        shown = json.loads(body)["config"]["test_command_lines"][1]
        self.assertEqual(split_command(shown), ["ruff", "check", "src dir"])


if __name__ == "__main__":
    unittest.main()
