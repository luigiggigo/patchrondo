"""Browser end-to-end checks of the local interface. Scripted providers only.

Starts the real server in this process, drives it with a headless Chrome or
Edge, and checks the pages against the files on disk. Runs started from the
interface are real `patchrondo run` processes; their provider commands can only
reach a synthetic executable, and the driver refuses to start a run while a real
`claude` or `codex` is reachable from it. Nothing here uses plan quota.

Scenarios: first run and setup wizard; run with live updates; duplicate starts;
interface closed and reopened during a run; reconnection; quota recovery states;
two independent projects; a PatchRondo 0.1 home opened in place; responsive
layout, keyboard and accessibility basics; large logs and many tasks.

    python tools/ui_e2e.py
    python tools/ui_e2e.py --only wizard --screenshots .test-tmp/shots

Needs a local Chromium-based browser (set PATCHRONDO_BROWSER to choose one).
Without one the check is skipped with exit status 0 and says so.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import browser  # noqa: E402
from patchrondo import doctor  # noqa: E402
from patchrondo.commands import join_command  # noqa: E402
from patchrondo.core import config, create_task, initialize  # noqa: E402
from patchrondo.storage import read_json, save_json  # noqa: E402
from patchrondo.ui import DashboardServer  # noqa: E402

SCENARIOS = ("wizard", "run", "restart", "reconnect", "recovery", "projects", "legacy", "quality")
TEST_CODE = "from pathlib import Path; assert Path('feature.txt').read_text() == 'good'"

# Receives the provider name explicitly; it never searches PATH.
SYNTHETIC_CLI = r'''import json, pathlib, sys, time
root = pathlib.Path(__file__).parent
control = json.loads((root / "control.json").read_text(encoding="utf-8"))
provider, args = sys.argv[1], sys.argv[2:]
sys.stdin.read()
reviewer = ("--json-schema" in args) if provider == "claude" else ("--output-schema" in args)
with (root / "calls.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"provider": provider, "role": "reviewer" if reviewer else "developer", "cwd": str(pathlib.Path.cwd())}) + "\n")
time.sleep(control.get("review_seconds" if reviewer else "develop_seconds", 0))
if control.get("mode") == "quota" and reviewer:
    print("Rate limit exceeded. Retry after 3600 seconds.", file=sys.stderr)
    sys.exit(1)
if reviewer:
    payload = {"verdict": "APPROVED", "summary": "Synthetic review: the change matches the task.", "issues": []}
    text = json.dumps(payload)
else:
    pathlib.Path("feature.txt").write_text("good", encoding="utf-8")
    text = "## Changes\n\n- Created `feature.txt`\n\n## Suggested tests\n\n- Check its content"
if provider == "claude":
    print(json.dumps({"structured_output": payload} if reviewer else {"result": text}))
else:
    pathlib.Path(args[args.index("--output-last-message") + 1]).write_text(text, encoding="utf-8")
'''

# The real CLI entry point with provider commands routed to the synthetic executable only.
RUNNER = r'''
import os, shutil, sys
from pathlib import Path
from unittest.mock import patch
fixture, source = Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, source)
from patchrondo.cli import main
from patchrondo.process import execute
def synthetic(argv, cwd, **kwargs):
    if argv[0] not in {"claude", "codex"}:
        raise ValueError("Unexpected provider command")
    return execute([sys.executable, str(fixture / "synthetic.py"), *argv], cwd, **kwargs)
free = os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep)
                       if d and not any(shutil.which(n, path=d) for n in ("claude", "codex")))
with patch.dict(os.environ, {"PATH": free}), patch("patchrondo.providers.execute", side_effect=synthetic):
    if any(shutil.which(n) for n in ("claude", "codex")):
        raise RuntimeError("A real provider CLI is still on PATH; refusing the synthetic run")
    raise SystemExit(main(sys.argv[3:]))
'''

PROVIDERS = {name: {"name": name, "label": spec["label"], "installed": True, "path": f"/synthetic/bin/{name}",
                    "version": "0.0.0 (synthetic)", "authenticated": True, "auth_method": "claude.ai" if name == "claude" else None,
                    "warning": None, "hint": None, "login_command": spec["login"], "docs": spec["docs"]}
             for name, spec in doctor.PROVIDERS.items()}


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, timeout=60)
    return done.stdout.strip()


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    for args in (["init", "-q"], ["config", "user.name", "PatchRondo E2E"], ["config", "user.email", "e2e@example.invalid"],
                 ["config", "commit.gpgsign", "false"]):
        git(path, *args)
    (path / "README.md").write_text(f"# {path.name}\n", encoding="utf-8", newline="\n")
    (path / "src").mkdir()
    (path / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8", newline="\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "base")
    return path.resolve()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Env:
    """A temporary home, repositories, the server and the browser page."""

    def __init__(self, page: browser.Page, shots: Path | None):
        self.page, self.shots = page, shots
        self.root = Path(tempfile.mkdtemp(prefix="patchrondo-ui-")).resolve()
        self.home = self.root / "home"
        self.fixture = self.root / "fixture"
        self.fixture.mkdir()
        (self.fixture / "synthetic.py").write_text(SYNTHETIC_CLI, encoding="utf-8", newline="\n")
        self.control(mode="ok")
        self.server: DashboardServer | None = None
        self.thread: threading.Thread | None = None
        self.checks: list[tuple[str, bool, str]] = []
        self.timings: dict[str, float] = {}

    # --- fixture --------------------------------------------------------------------

    def control(self, **values) -> None:
        current = {"mode": "ok", "develop_seconds": 0.6, "review_seconds": 0.6}
        file = self.fixture / "control.json"
        if file.exists():
            current.update(json.loads(file.read_text(encoding="utf-8")))
        current.update(values)
        file.write_text(json.dumps(current), encoding="utf-8")

    def calls(self) -> list[dict]:
        file = self.fixture / "calls.jsonl"
        return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()] if file.exists() else []

    def run_command(self, project, task_id: str, flags: list[str]) -> list[str]:
        return [sys.executable, "-c", RUNNER, str(self.fixture), str(SOURCE), "--home", str(project.home), "run", task_id, *flags]

    def start(self, home: Path | None = None, port: int = 0, token: str | None = None) -> DashboardServer:
        self.server = DashboardServer(home or self.home, port, run_command=self.run_command, token=token)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.server

    def stop(self):
        server, self.server = self.server, None
        server.shutdown()
        runs = server.close_runs(5)
        server.server_close()
        return runs

    def open(self, route: str = "") -> None:
        """Load the interface with a fresh session link, optionally at a route."""
        self.page.goto("about:blank")
        self.page.goto(self.server.url + (f"&route={route}" if route else ""))
        self.page.wait("Boolean(q('.shell') || q('.fatal'))", 15, "the interface to start")

    def go(self, route: str) -> None:
        self.page.js(f"(location.hash = {json.dumps('#/' + route)}, true)")
        time.sleep(0.15)

    def api(self, method: str, path: str, body: dict | None = None):
        """Call the API from the page, with its session token, and return [status, json]."""
        return self.page.js(f"""(async () => {{
            const response = await fetch({json.dumps(path)}, {{ method: {json.dumps(method)}, cache: "no-store",
              headers: {{ "X-PatchRondo-Token": sessionStorage.getItem("patchrondo.token"), "Content-Type": "application/json" }},
              {'body: ' + json.dumps(json.dumps(body)) + ',' if body is not None else ''} }});
            return [response.status, await response.json().catch(() => null)];
        }})()""")

    def projects(self) -> list[dict]:
        return read_json(self.home / "projects.json")["projects"]

    def project_home(self, name: str) -> Path:
        entry = next(item for item in self.projects() if item["name"] == name)
        home = Path(entry["home"])
        return home if home.is_absolute() else self.home / home

    # --- reporting ------------------------------------------------------------------

    def check(self, name: str, ok, detail: str = "") -> bool:
        ok = bool(ok)
        self.checks.append((name, ok, detail))
        print(f"{'ok      ' if ok else 'FAILED  '}{name}{f' ({detail})' if detail and not ok else ''}", flush=True)
        return ok

    def expect(self, name: str, expression: str, timeout: float = 10) -> bool:
        try:
            self.page.wait(expression, timeout, name)
            return self.check(name, True)
        except browser.BrowserError:
            return self.check(name, False, self.page.js(
                "location.hash + ' | ' + (q('.dialog') || q('.view') || document.body).innerText.replace(/\\s+/g, ' ').slice(0, 900)"))

    def clean_console(self, name: str, allow: tuple[str, ...] = ()) -> None:
        problems = [item for item in self.page.take_problems() if not any(text in item for text in allow)]
        self.check(f"{name}: no console errors, exceptions or CSP violations", not problems, "; ".join(problems)[:2500])

    def shot(self, name: str) -> None:
        if self.shots:
            time.sleep(0.3)
            self.page.screenshot(self.shots / f"{name}.png")

    def text(self) -> str:
        return self.page.js("document.body.innerText")

    def wait_state(self, path: Path, predicate, timeout: float = 30) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            try:
                state = read_json(path / "state.json")
                if predicate(state):
                    return state
            except (OSError, ValueError):
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"state of {path.name} did not reach the expected value")
            time.sleep(0.02)

    def new_task(self, title: str, run: bool = False) -> str:
        """Create a task through the New task page; returns its ID."""
        self.go("new")
        self.page.wait("Boolean(q('form.form'))", 8, "the new task form")
        self.page.fill("form.form input[type=text]", title)
        self.page.fill("form.form textarea", "Create feature.txt containing the word good.")
        self.page.click(text="Create and run…" if run else "Create task")
        self.page.wait("location.hash.startsWith('#/tasks/P-')", 15, "the task page")
        return self.page.js("location.hash.split('/')[3].split('?')[0]")

    def run_task(self, recovery: bool | None = None) -> None:
        """Press Run or Resume on the open task page and confirm."""
        self.page.wait("Boolean(byText('Run', '.task-head button') || byText('Resume', '.task-head button'))", 10, "the Run button")
        self.page.js("(byText('Run', '.task-head button') || byText('Resume', '.task-head button')).click()")
        self.page.wait("Boolean(q('.dialog'))", 5, "the run confirmation")
        if recovery is not None and self.page.js("q('.dialog .switch').checked") != recovery:
            self.page.click(".dialog .switch")
        self.page.js("q('.dialog .btn.primary').click()")
        self.page.wait("!q('.dialog')", 15, "the run to be accepted")


# --- scenarios ----------------------------------------------------------------------

def scenario_wizard(env: Env) -> None:
    """Fresh installation -> patchrondo -> wizard -> project -> task."""
    page = env.page
    alpha = make_repo(env.root / "alpha")
    env.check("the home does not exist before the first start", not env.home.exists())
    env.start()
    env.open()
    env.expect("first start opens the setup wizard", "Boolean(q('.wizard')) && location.hash === '#/welcome'")
    env.check("the session token is removed from the address bar", "token" not in page.js("location.href"))
    env.check("nothing is written before setup finishes", not (env.home / "projects.json").exists() and not (env.home / "settings.json").exists())
    env.shot("wizard-1-welcome")
    page.click(text="Get started")
    env.expect("Continue is disabled until a repository is chosen", "byText('Continue').disabled === true")
    page.fill(".wizard input.mono", str(env.root / "missing"))
    env.expect("a missing folder is rejected with a reason", "q('.repo-status').innerText.includes('does not exist')")
    page.fill(".wizard input.mono", str(alpha / "src"))
    env.expect("a sub-folder is rejected and the repository root is offered", "q('.repo-status').innerText.includes('repository root') && Boolean(byText('Use ', '.repo-status button'))")
    page.js("byText('Use ', '.repo-status button').click()")
    env.expect("the repository root validates", "Boolean(q('.repo-ok')) && byText('Continue').disabled === false")
    env.shot("wizard-2-project")
    page.click(text="Continue")
    env.expect("the agents step shows both CLIs without calling a model", "qa('.wizard .list-row .badge').length === 2")
    env.shot("wizard-3-agents")
    page.click(text="Continue")
    env.expect("tests are off by default in the wizard", "q('.wizard .switch').checked === false")
    page.click(".wizard .switch")
    page.click(text="Continue")
    env.expect("enabling tests without a command is refused", "q('.wizard .form-error').innerText.includes('at least one command')")
    page.fill(".wizard textarea", join_command([sys.executable, "-c", TEST_CODE]))
    page.click(text="Continue")
    env.expect("enabling tests without explicit trust is refused", "q('.wizard .form-error').innerText.includes('trust')")
    page.click(".wizard .check")
    env.shot("wizard-4-tests")
    page.click(text="Continue")
    env.expect("the last step explains plan usage", "q('.wizard').innerText.includes('use your own plan')")
    env.shot("wizard-5-usage")
    page.click(text="Finish setup")
    env.expect("setup ends on the new task page", "location.hash.startsWith('#/new') && Boolean(q('form.form'))", 15)
    projects = env.projects()
    env.check("one project is registered with its own state directory", len(projects) == 1 and projects[0]["name"] == "alpha"
              and projects[0]["home"].startswith("projects/P-"))
    cfg = config(env.project_home("alpha"))
    env.check("tests were saved enabled, trusted and as an argument array", cfg["tests"] == {
        "enabled": True, "trust_acknowledged": True, "commands": [[sys.executable, "-c", TEST_CODE]]}, str(cfg["tests"]))
    settings = read_json(env.home / "settings.json")
    env.check("setup is recorded and the project selected", settings["onboarding_completed"] and settings["selected_project"] == projects[0]["id"])
    env.check("no provider was called during setup", not env.calls())

    page.click(text="Create task")
    env.expect("an empty form shows field errors and focuses the first one", "qa('form.form .field-error').filter((e) => e.textContent).length === 2 && document.activeElement === q('form.form input[type=text]')")
    page.fill("form.form input[type=text]", "Add the feature file")
    page.fill("form.form textarea", "Create feature.txt containing the word good.")
    page.fill(".criteria input", "feature.txt exists")
    page.press("Enter")
    page.call("Input.insertText", {"text": "The tests pass"})
    env.expect("Enter adds an acceptance criterion", "qa('.criteria input').length === 2")
    env.shot("new-task")
    page.click(text="Create and run…")
    env.expect("Create and run asks for confirmation before any agent is called", "location.hash.startsWith('#/tasks/P-') && Boolean(q('.dialog')) && q('.dialog').innerText.includes('uses your plan quota')", 15)
    env.shot("run-confirmation")
    page.click(text="Cancel")
    task_id = page.js("location.hash.split('/')[3].split('?')[0]")
    home = env.project_home("alpha")
    state = read_json(home / "tasks" / task_id / "state.json")
    task_md = (home / "tasks" / task_id / "task.md").read_text(encoding="utf-8")
    env.check("the task exists on disk, ready, with its worktree and criteria", state["status"] == "ready" and Path(state["worktree"]).is_dir()
              and "- feature.txt exists" in task_md and "- The tests pass" in task_md)
    env.check("cancelling the confirmation called no provider", not env.calls())
    env.expect("the task page shows a ready task", "q('.task-head').innerText.includes('Ready') && Boolean(byText('Run', '.task-head button'))")
    env.shot("task-ready")
    env.clean_console("wizard")
    env.stop()


def scenario_run(env: Env) -> None:
    """Run from the interface with live updates; duplicate starts; every panel shows real data."""
    page = env.page
    if not (env.home / "projects.json").exists():
        scenario_wizard(env)
    env.start()
    env.open()
    env.control(mode="ok", develop_seconds=1.2, review_seconds=1.2)
    task_id = env.new_task("Live run")
    home = env.project_home("alpha")
    path = home / "tasks" / task_id
    before = len(env.calls())
    project_id = page.js("location.hash.split('/')[2]")

    # Two start requests at the same moment: one run, one refusal.
    statuses = page.js(f"""Promise.all([0, 1].map(() => fetch('/api/projects/{project_id}/tasks/{task_id}/run', {{ method: 'POST', body: '{{}}',
        headers: {{ 'X-PatchRondo-Token': sessionStorage.getItem('patchrondo.token'), 'Content-Type': 'application/json' }} }}).then((r) => r.status)))""")
    env.check("two simultaneous starts give one run and one refusal", sorted(statuses) == [202, 409], str(statuses))
    env.expect("the page shows Running without a reload", "q('.task-head').innerText.includes('Running')", 8)
    env.check("the backend verifies the running process", page.js("q('.facts').innerText").count("verified") >= 1, page.js("q('.facts').innerText"))
    env.expect("Rondo accompanies the run", "q('.rondo-note').innerText.includes('developing') || q('.rondo-note').innerText.includes('tests') || q('.rondo-note').innerText.includes('reviewing')")
    third = env.api("POST", f"/api/projects/{project_id}/tasks/{task_id}/run", {})
    env.check("a start while running is refused", third[0] == 409, str(third))
    env.shot("task-running")
    page.click(text="Activity", selector="[role=tab]")
    env.expect("the timeline shows development starting", "q('.timeline').innerText.includes('started development')", 8)
    env.expect("the timeline shows tests and review as they happen", "q('.timeline').innerText.includes('Tests passed') && q('.timeline').innerText.includes('started the review')", 20)
    done = env.wait_state(path, lambda s: s["status"] == "done")
    seen = time.monotonic()
    env.expect("the page shows Done without a reload", "q('.task-head').innerText.includes('Done')", 5)
    latency = time.monotonic() - seen
    env.timings["done_event_to_page_seconds"] = round(latency, 3)
    env.check("the final state reached the page within one second", latency < 1.0, f"{latency:.2f}s")
    env.check("exactly one developer and one reviewer call were made", [c["role"] for c in env.calls()[before:]] == ["developer", "reviewer"], str(env.calls()[before:]))
    events = [entry["event"] for entry in done["history"]]
    order = page.js("qa('.timeline .event-text').map((e) => e.textContent)")
    env.check("the timeline lists the persisted events in order", len(order) == len(events) and "Run started" in order[0] and order[-1] == "Task completed", str(order))
    env.expect("Rondo celebrates a completed task", "q('.rondo-note').innerText.includes('All patched up') && Boolean(q('.rondo-note .mood-hop'))")
    env.shot("task-done-activity")

    page.click(text="Logs", selector="[role=tab]")
    env.expect("the run log is shown", "q('.log-view').innerText.includes('run started from the dashboard')", 8)
    env.check("iteration transcripts are listed", page.js("qa('.log-select option').some((o) => o.value.includes('developer.reply.md'))"))
    page.js("(q('.log-select').value = qa('.log-select option').find((o) => o.value.includes('developer.reply.md')).value, q('.log-select').dispatchEvent(new Event('change')), true)")
    env.expect("another log file can be opened", "q('.log-view').innerText.includes('Created')", 8)
    page.click(text="Tests", selector="[role=tab]")
    env.expect("the Tests tab shows the passed command", "q('.tab-panel').innerText.includes('passed') && q('.tab-panel').innerText.includes('exit 0')")
    page.click(text="Review", selector="[role=tab]")
    env.expect("the Review tab shows the verdict", "q('.tab-panel').innerText.includes('Approved') && q('.tab-panel').innerText.includes('Synthetic review')")
    page.click(text="Files changed", selector="[role=tab]")
    env.expect("the Files tab shows the new file and its content", "q('.tab-panel').innerText.includes('feature.txt') && q('.tab-panel').innerText.includes('New files')", 10)
    env.shot("task-files")
    page.click(text="Handoff", selector="[role=tab]")
    env.expect("the Handoff tab renders the developer's Markdown", "Boolean(q('.tab-panel .md code')) && q('.tab-panel').innerText.includes('Suggested tests')")
    page.click(text="Report", selector="[role=tab]")
    env.expect("the Report tab shows the report", "q('.tab-panel').innerText.includes('How to apply the changes')")
    page.click(text="Advanced", selector="[role=tab]")
    env.expect("the Advanced tab keeps the raw state readable", "q('.tab-panel').innerText.includes('\"status\": \"done\"')")
    page.click(text="Overview", selector="[role=tab]")
    env.expect("a finished task offers no Run button", "!byText('Run', '.task-head button') && !byText('Resume', '.task-head button')")
    env.go("")
    env.expect("the dashboard counts the finished task", "q('.stat[href*=\"filter=done\"] .stat-value').textContent !== '0'")
    env.shot("home-with-tasks")
    env.clean_console("run", allow=("status of 409", "net::ERR"))  # the refused duplicate starts, and the page of a stopped server
    env.stop()


def scenario_restart(env: Env) -> None:
    """Task started -> interface closed -> interface reopened -> state recovered."""
    page = env.page
    if not (env.home / "projects.json").exists():
        scenario_wizard(env)
    env.start()
    env.open()
    env.control(mode="ok", develop_seconds=5, review_seconds=0.5)
    task_id = env.new_task("Survives a restart")
    path = env.project_home("alpha") / "tasks" / task_id
    before = len(env.calls())
    env.run_task()
    env.expect("the run is active before the interface closes", "q('.task-head').innerText.includes('Running')", 8)
    route = page.js("location.hash.slice(1)")
    runs = env.stop()
    env.check("closing reports the run that keeps going", len(runs.active) == 1 and runs.pending == 0, str(runs))
    env.expect("the open page says the connection is lost instead of inventing a state", "Boolean(q('.banner')) && q('.banner').innerText.toLowerCase().includes('last')", 12)
    env.check("the lost page still shows the last confirmed state", "Running" in page.js("q('.task-head').innerText"))
    env.shot("connection-lost")
    env.check("the run is still alive without the interface", runs.active[0].poll() is None and (path / ".run.lock").exists())
    env.start()
    env.open(route)
    env.expect("the reopened interface shows the task as running, verified", "q('.task-head').innerText.includes('Running') && q('.facts') && q('.facts').innerText.includes('Running, verified')", 12)
    env.expect("the reopened interface offers no second start", "!byText('Run', '.task-head button') && !byText('Resume', '.task-head button')")
    project_id = route.split("/")[2]
    refused = env.api("POST", f"/api/projects/{project_id}/tasks/{task_id}/run", {})
    env.check("a start from the reopened interface is refused", refused[0] == 409, str(refused))
    env.wait_state(path, lambda s: s["status"] == "done", 40)
    env.expect("the reopened interface follows the run to completion", "q('.task-head').innerText.includes('Done')", 8)
    env.check("the run made one developer and one reviewer call in total", [c["role"] for c in env.calls()[before:]] == ["developer", "reviewer"])
    runs.active[0].wait(20)
    env.clean_console("restart", allow=("Failed to load resource", "net::ERR", "Failed to fetch"))
    env.stop()


def scenario_reconnect(env: Env) -> None:
    """The server goes away and comes back at the same address: the page resynchronizes by itself."""
    page = env.page
    if not (env.home / "projects.json").exists():
        scenario_wizard(env)
    server = env.start()
    env.open()
    port, token = server.server_address[1], server.token
    env.expect("the page is live", "q('.side-foot').innerText.includes('Live')")
    epoch = page.js("fetch('/api/snapshot', { headers: { 'X-PatchRondo-Token': sessionStorage.getItem('patchrondo.token') } }).then((r) => r.json()).then((s) => s.epoch)")
    env.stop()
    env.expect("a banner reports the lost connection", "Boolean(q('.banner')) && (q('.side-foot').innerText.includes('Reconnecting') || q('.side-foot').innerText.includes('Offline'))", 15)
    # A change made while the page is disconnected must appear after it reconnects.
    home = env.project_home("alpha")
    task_id, _ = create_task(home, title="Created while offline", description="Made by the CLI", acceptance=[], developer="claude", reviewer="codex")
    env.start(port=port, token=token)
    env.expect("the page reconnects by itself", "!q('.banner') && q('.side-foot').innerText.includes('Live')", 20)
    fresh = env.api("GET", "/api/snapshot")[1]["epoch"]
    env.check("the new server lifetime forced a fresh snapshot", fresh != epoch)
    env.go("tasks")
    env.expect("the task created while disconnected is listed", f"q('.task-list').innerText.includes('Created while offline')", 8)
    # Events are numbered: a change now arrives through the stream, not through a reload.
    state = read_json(home / "tasks" / task_id / "state.json")
    save_json(home / "tasks" / task_id / "state.json", {**state, "title": "Renamed on disk"})
    started = time.monotonic()
    env.expect("a later change on disk reaches the list through the stream", "q('.task-list').innerText.includes('Renamed on disk')", 5)
    env.timings["disk_change_to_page_seconds"] = round(time.monotonic() - started, 3)
    env.check("a change on disk reached the page within one second", env.timings["disk_change_to_page_seconds"] < 1.0, str(env.timings["disk_change_to_page_seconds"]))
    env.clean_console("reconnect", allow=("Failed to load resource", "net::ERR", "Failed to fetch"))
    env.stop()


def scenario_recovery(env: Env) -> None:
    """Provider at its limit: the five recovery states are told apart from real process state."""
    page = env.page
    if not (env.home / "projects.json").exists():
        scenario_wizard(env)
    env.start()
    env.open()
    home = env.project_home("alpha")
    env.control(mode="quota", develop_seconds=0.3, review_seconds=0.3)

    # (5) Manual intervention: recovery off, the limit simply pauses the task.
    manual = env.new_task("Quota without recovery")
    env.run_task(recovery=False)
    state = env.wait_state(home / "tasks" / manual, lambda s: s["status"] == "paused")
    env.check("without recovery a usage limit pauses the task with no plan", state["last_error"]["kind"] == "quota" and state["recovery"] is None)
    env.expect("the page shows a paused task that needs a person", "q('.task-head').innerText.includes('Paused') && q('.task-overview').innerText.includes('Usage limit reached')", 8)
    env.check("no automatic retry is claimed", "Waiting to retry" not in env.text() and "Quota recovery" not in page.js("q('.task-overview').innerText"))
    env.shot("recovery-manual")

    # (2) A live process is waiting for the retry.
    waiting = env.new_task("Quota with recovery")
    path = home / "tasks" / waiting
    env.run_task(recovery=True)
    plan = env.wait_state(path, lambda s: s["status"] == "paused" and (s.get("recovery") or {}).get("status") == "scheduled")["recovery"]
    env.expect("the page shows a verified process waiting to retry", "q('.task-head').innerText.includes('Waiting to retry') && q('.task-overview').innerText.includes('A process is waiting')", 10)
    card = page.js("q('.task-overview').innerText")
    env.check("the plan shows provider, source, next attempt and retries", all(text in card for text in (
        "Codex", "Reset time stated by the provider", "Next attempt", "1 of 3 used", "Usage limit")), card[:600])
    env.check("the wait holds no task lock", not (path / ".run.lock").exists())
    env.check("a saved plan is described as not being a service", "not a running service" in card)
    env.shot("recovery-waiting")
    project_id = page.js("location.hash.split('/')[2]")
    duplicate = env.api("POST", f"/api/projects/{project_id}/tasks/{waiting}/run", {"auto_resume": True})
    env.check("a second automatic run is refused while one waits", duplicate[0] == 409, str(duplicate))

    # (3) The same plan with no process: nothing will retry.
    if os.name == "posix":
        page.click(text="Stop waiting")
        page.wait("Boolean(q('.dialog'))", 5, "the stop confirmation")
        page.js("q('.dialog .btn.danger').click()")
    else:
        env.check("stopping is offered as unavailable on native Windows", page.js("byText('Stop', '.task-head button').disabled === true"))
        for child in env.server.app.children.get((project_id, waiting), []):
            child.kill()  # stands in for closing the terminal of the waiting run
    env.expect("with no process the page says nothing is waiting", "q('.task-head').innerText.includes('nothing waiting') && q('.task-overview').innerText.includes('no process is waiting')", 45)
    after = read_json(path / "state.json")
    env.check("the plan is kept unchanged when the waiting process ends", after["recovery"] == plan and after["status"] == "paused")
    env.check("the persisted status alone does not show as waiting", "Waiting to retry" not in page.js("q('.task-head').innerText"))
    env.shot("recovery-plan-only")
    page.js("byText('Resume', '.task-head button').click()")
    env.expect("resuming explains what the saved plan means for this run", "Boolean(q('.dialog')) && q('.dialog').innerText.includes('Automatic quota recovery')", 5)
    page.click(text="Cancel")

    # (4) Recovery ended: a reset beyond the wait budget is never brought forward.
    env.go(f"settings/{project_id}/recovery")
    env.expect("the recovery settings load", "qa('.settings-card input[type=number]').length === 5", 10)
    page.fill("input[aria-label='Max total wait']", "60")
    page.click(text="Save changes")
    env.expect("the recovery settings are saved", "byText('Save changes').disabled === true && !q('.btn.busy')", 8)
    env.check("the wait budget was written to the project configuration", config(home)["recovery"]["max_total_wait_seconds"] == 60)
    ended = env.new_task("Quota beyond the budget")
    env.run_task(recovery=True)
    stopped = env.wait_state(home / "tasks" / ended, lambda s: (s.get("recovery") or {}).get("status") == "stopped")
    env.check("the engine stopped recovery instead of retrying early", stopped["recovery"]["stop_reason"] == "reset_beyond_budget")
    env.expect("the page shows that recovery ended and why", "q('.task-head').innerText.includes('Recovery ended') && q('.task-overview').innerText.includes('beyond the total wait budget')", 10)
    env.shot("recovery-ended")
    calls = [c["role"] for c in env.calls() if waiting in c["cwd"]]
    env.check("no provider call was made for the task whose plan is not due", calls == ["developer", "reviewer"], str(calls))
    env.go("")
    env.expect("the dashboard lists these tasks as needing attention", "q('.stat[href*=\"filter=attention\"] .stat-value').textContent === '3'", 8)
    env.shot("home-attention")
    env.control(mode="ok")
    env.clean_console("recovery", allow=("status of 409", "net::ERR"))
    env.stop()


def scenario_projects(env: Env) -> None:
    """Two projects with independent settings and tasks, managed entirely from the interface."""
    page = env.page
    if not (env.home / "projects.json").exists():
        scenario_wizard(env)
    beta = make_repo(env.root / "work" / "beta")
    env.start()
    env.open()
    alpha_home = env.project_home("alpha")
    alpha_before = digest(alpha_home / "config.json")
    env.go("projects")
    page.click(text="Add project")
    page.wait("Boolean(q('.dialog'))", 5, "the add project dialog")
    page.click(text="Browse…")
    page.wait("Boolean(q('.browser-list'))", 8, "the folder list")
    page.fill(".dialog input.mono", str(beta.parent))
    page.click(text="Browse…")
    page.click(text="Browse…")
    env.expect("the folder picker lists local folders and marks repositories", "Boolean(byText('beta', '.browser-list .menu-item')) && byText('beta', '.browser-list .menu-item').innerText.includes('Git repository')", 8)
    env.shot("add-project-browse")
    page.js("byText('beta', '.browser-list .menu-item').click()")
    page.wait("Boolean(byText('Use this repository'))", 8, "the repository to be recognized")
    page.click(text="Use this repository")
    env.expect("the picked repository validates", "Boolean(q('.dialog .repo-ok'))", 8)
    page.js("byText('Add project', '.dialog button').click()")
    env.expect("the second project appears and becomes the selected one", "!q('.dialog') && qa('.project').length === 2 && q('.project.selected').innerText.includes('beta')", 15)
    env.shot("projects")
    beta_home = env.project_home("beta")
    env.check("the new project starts with tests disabled and untrusted", config(beta_home)["tests"] == {"enabled": False, "trust_acknowledged": False, "commands": []})
    duplicate = env.api("POST", "/api/projects", {"path": str(beta)})
    env.check("registering the same repository twice is refused", duplicate[0] == 400 and "already registered" in duplicate[1]["error"], str(duplicate))
    inside = env.api("POST", "/api/projects/inspect", {"path": str(alpha_home)})
    env.check("a folder that is not a repository is reported, not registered", inside[1]["ok"] is False)

    beta_id = next(item["id"] for item in env.projects() if item["name"] == "beta")
    env.go(f"settings/{beta_id}/workflow")
    env.expect("the workflow settings load", "qa('.settings-card input[type=number]').length === 4", 10)
    page.fill("input[aria-label='Max iterations']", "99")
    page.click(text="Save changes")
    env.expect("an out-of-range value is rejected next to its field", "q('.settings-card').innerText.includes('Must be between 1 and 50')")
    page.fill("input[aria-label='Max iterations']", "3")
    page.click(text="Save changes")
    env.expect("a valid value is saved", "byText('Save changes').disabled === true && !q('.btn.busy')", 8)
    env.shot("settings-workflow")
    env.check("the setting changed for the second project only", config(beta_home)["workflow"]["max_iterations"] == 3
              and digest(alpha_home / "config.json") == alpha_before)
    env.go(f"settings/{beta_id}/tests")
    env.expect("the test settings load", "Boolean(q('.settings-card textarea'))", 10)
    page.click(".settings-card .switch")
    page.fill(".settings-card textarea", "python -m pytest -q")
    page.click(text="Save changes")
    env.expect("tests cannot be enabled without explicit trust", "q('.settings-card .form-error').innerText.includes('trust')")
    env.check("nothing was written without consent", config(beta_home)["tests"]["enabled"] is False)
    refused = env.api("PATCH", f"/api/projects/{beta_id}/config", {"tests": {"enabled": True, "trust_acknowledged": False, "commands": ["python -m pytest -q"]}})
    env.check("the backend refuses enabled tests without trust too", refused[0] == 400 and config(beta_home)["tests"]["enabled"] is False, str(refused))
    env.shot("settings-tests")
    env.go(f"settings/{beta_id}/workflow")
    env.expect("leaving a section with unsaved changes asks first", "Boolean(q('.dialog')) && q('.dialog').innerText.includes('Discard unsaved changes')", 5)
    page.js("q('.dialog .btn.danger').click()")
    env.expect("discarding moves on to the requested section", "!q('.dialog') && location.hash.endsWith('/workflow') && qa('.settings-card input[type=number]').length === 4", 8)

    task_id = env.new_task("Task in beta")
    env.check("the task was created in the second project's own directory", (beta_home / "tasks" / task_id / "state.json").is_file()
              and not (alpha_home / "tasks" / task_id).exists())
    state = read_json(beta_home / "tasks" / task_id / "state.json")
    env.check("its worktree belongs to the second repository", Path(state["worktree"]).is_relative_to(beta_home)
              and git(Path(state["worktree"]), "rev-parse", "--abbrev-ref", "HEAD") == f"patchrondo/{task_id}"
              and f"patchrondo/{task_id}" in git(beta, "branch", "--list", "patchrondo/*"))
    env.go("tasks")
    env.expect("with a project selected only its tasks are listed", "qa('.task-row').length === 1 && q('.task-list').innerText.includes('Task in beta')", 8)
    page.click(".switcher")
    page.js("byText('All projects', '.popover .menu-item').click()")
    env.expect("All projects lists the tasks of both", "qa('.task-row').length > 1 && q('.task-list').innerText.includes('alpha') && q('.task-list').innerText.includes('beta')", 8)
    env.shot("tasks-all-projects")
    alpha_id = next(item["id"] for item in env.projects() if item["name"] == "alpha")
    cross = env.api("GET", f"/api/projects/{alpha_id}/tasks/{task_id}")
    env.check("a task cannot be read through another project", cross[0] == 404, str(cross))

    env.go("projects")
    page.js("q('.project:not(.selected) .project-head .icon-btn, .project .project-head .icon-btn').click()")
    env.api("PATCH", f"/api/projects/{beta_id}", {"name": "beta renamed"})
    env.expect("a project can be renamed", "q('.projects').innerText.includes('beta renamed')", 8)
    page.press("Escape")
    removed = env.api("DELETE", f"/api/projects/{beta_id}", {})
    env.check("removing a project keeps its repository and state on disk", removed[0] == 200 and beta.is_dir() and (beta_home / "config.json").is_file()
              and (beta_home / "tasks" / task_id / "state.json").is_file() and len(env.projects()) == 1, str(removed))
    env.expect("the removed project leaves the list", "qa('.project').length === 1", 8)
    back = env.api("POST", "/api/projects/import", {"home": str(beta_home)})
    env.check("its state directory can be imported again with its task", back[0] == 201 and back[1]["tasks"] == 1, str(back))
    env.clean_console("projects", allow=("status of 400", "status of 404", "net::ERR"))  # the refusals this scenario provokes
    env.stop()


def scenario_legacy(env: Env) -> None:
    """A PatchRondo 0.1 home opens in the new interface in place, without losing or rewriting anything."""
    page = env.page
    legacy = env.root / "legacy-home"
    repo = make_repo(env.root / "legacy-repo")
    initialize(legacy, repo)
    cfg = read_json(legacy / "config.json")
    for section in ("rag", "recovery"):
        cfg.pop(section)  # a configuration written before these sections existed
    cfg["tests"] = {"enabled": True, "trust_acknowledged": True, "commands": [["python", "-m", "pytest", "-q"]]}
    save_json(legacy / "config.json", cfg)
    task_id, worktree = create_task(legacy, title="Legacy task", description="Created by 0.1", acceptance=["Still here"],
                                    developer="codex", reviewer="claude")
    path = legacy / "tasks" / task_id
    state = read_json(path / "state.json")
    for key in ("recovery", "overrides"):
        state.pop(key, None)  # fields 0.1 never wrote
    state.update(status="paused", phase="review", iteration=2, last_error={"kind": "timeout", "message": "Agent timed out"},
                 review={"verdict": "CHANGES_REQUESTED", "summary": "Needs a test", "issues": [{"severity": "medium", "path": "a.py", "description": "Add a test"}]},
                 history=[{"at": "2026-10-01T10:00:00+00:00", "event": "run_started", "iteration": 1, "previous_status": "ready"},
                          {"at": "2026-10-01T10:05:00+00:00", "event": "task_paused", "iteration": 2, "reason": "timeout"}])
    save_json(path / "state.json", state)
    (path / "handoff.md").write_text("# Handoff — iteration 2\n\nLegacy handoff\n", encoding="utf-8", newline="\n")
    (path / "report.md").write_text("# Report: Legacy task\n", encoding="utf-8", newline="\n")
    (path / "runs" / "iteration-001").mkdir(parents=True)
    (path / "runs" / "iteration-001" / "developer.stdout.log").write_text("legacy log line\n", encoding="utf-8", newline="\n")
    watched = [legacy / "config.json", path / "state.json", path / "task.md", path / "handoff.md", path / "report.md",
               path / "runs" / "iteration-001" / "developer.stdout.log"]
    before = {file: digest(file) for file in watched}
    listing = subprocess.run([sys.executable, "-m", "patchrondo", "--home", str(legacy), "list"], capture_output=True, text=True,
                             encoding="utf-8", env={**os.environ, "PYTHONPATH": str(SOURCE)}, timeout=60)
    env.check("the 0.1 command line still lists the task and writes no registry", task_id in listing.stdout and not (legacy / "projects.json").exists(), listing.stdout + listing.stderr)

    env.start(home=legacy)
    env.open()
    env.expect("a configured 0.1 home opens on the dashboard, not the wizard", "location.hash !== '#/welcome' && Boolean(q('.hero'))", 10)
    env.go("projects")
    env.expect("the 0.1 home is listed as a project, marked as such", "qa('.project').length === 1 && q('.project').innerText.includes('legacy-repo') && q('.project').innerText.includes('0.1 home')", 8)
    registry = read_json(legacy / "projects.json")
    env.check("it is registered in place, not moved", registry["projects"][0]["home"] == "." and registry["projects"][0]["origin"] == "legacy")
    env.go(f"tasks/{registry['projects'][0]['id']}/{task_id}")
    env.expect("the legacy task opens with its saved state", "q('.task-head').innerText.includes('Legacy task') && q('.task-head').innerText.includes('Paused') && q('.task-overview').innerText.includes('Agent timed out')", 10)
    env.shot("legacy-task")
    page.click(text="Review", selector="[role=tab]")
    env.expect("its review is shown", "q('.tab-panel').innerText.includes('Changes requested') && q('.tab-panel').innerText.includes('Add a test')")
    page.click(text="Handoff", selector="[role=tab]")
    env.expect("its handoff is shown", "q('.tab-panel').innerText.includes('Legacy handoff')")
    page.click(text="Logs", selector="[role=tab]")
    env.expect("its logs are shown", "q('.log-view').innerText.includes('legacy log line')", 8)
    page.click(text="Activity", selector="[role=tab]")
    env.expect("its history is shown", "qa('.timeline .event').length === 2")
    env.go(f"settings/{registry['projects'][0]['id']}/rag")
    env.expect("a configuration without a retrieval section shows retrieval as off", "q('.settings-card .switch').checked === false", 10)
    env.go(f"settings/{registry['projects'][0]['id']}/tests")
    env.expect("previously enabled tests stay enabled", "q('.settings-card .switch').checked === true && q('.settings-card textarea').value.includes('pytest')", 10)
    env.check("opening the home rewrote none of its files", all(digest(file) == value for file, value in before.items()),
              ", ".join(file.name for file, value in before.items() if digest(file) != value))
    env.check("the worktree is where 0.1 created it", Path(worktree).is_dir() and read_json(path / "state.json")["worktree"] == str(worktree))
    status = subprocess.run([sys.executable, "-m", "patchrondo", "--home", str(legacy), "status", task_id], capture_output=True, text=True,
                            encoding="utf-8", env={**os.environ, "PYTHONPATH": str(SOURCE)}, timeout=60)
    env.check("the 0.1 command line still works on the home afterwards", status.returncode == 0 and json.loads(status.stdout)["title"] == "Legacy task", status.stderr)
    env.clean_console("legacy", allow=("net::ERR",))
    env.stop()


def scenario_quality(env: Env) -> None:
    """Responsive layout, keyboard, accessibility basics, large logs, many tasks, reduced motion."""
    page = env.page
    if not (env.home / "projects.json").exists():
        scenario_wizard(env)
    home = env.project_home("alpha")
    seed = read_json(next(home.glob("tasks/T-*/state.json")))
    now = time.time()
    for number in range(60):
        task_id = f"T-{number + 1:012x}"
        status, phase = (("done", "complete"), ("paused", "review"), ("ready", "develop"), ("blocked", "review"))[number % 4]
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - number * 600))
        save_json(home / "tasks" / task_id / "state.json", {**seed, "id": task_id, "title": f"Seeded task {number + 1} with a deliberately long title to test truncation in narrow layouts",
                  "status": status, "phase": phase, "iteration": 1 + number % 3, "updated_at": stamp, "created_at": stamp, "recovery": None,
                  "last_error": {"kind": "timeout", "message": "Agent timed out"} if status == "paused" else "max_iterations_reached" if status == "blocked" else None,
                  "history": [{"at": stamp, "event": "run_started", "iteration": 1, "previous_status": "ready"}]})
        (home / "tasks" / task_id / "task.md").write_text("# Seeded\n\n## Description\n\nSeeded.\n", encoding="utf-8", newline="\n")
    big = home / "tasks" / "T-000000000001"
    line = "2026-10-10 12:00:00 synthetic log line with some text to make it realistically long ✓\n"
    (big / "ui-run.log").write_text("FIRST LINE OF THE LOG\n" + line * 70000 + "LAST LINE OF THE LOG\n", encoding="utf-8", newline="\n")
    log_size = (big / "ui-run.log").stat().st_size
    env.start()
    env.open()
    project_id = env.projects()[0]["id"]
    page.js("fetch('/api/settings', { method: 'PATCH', body: JSON.stringify({ selected_project: null }), headers: { 'X-PatchRondo-Token': sessionStorage.getItem('patchrondo.token'), 'Content-Type': 'application/json' } }).then((r) => r.status)")

    # Many tasks: navigation and filtering stay immediate.
    env.go("")
    page.wait("Boolean(q('.hero'))", 8, "the dashboard")
    elapsed = page.js("""(async () => { const start = performance.now(); location.hash = '#/tasks';
        while (qa('.task-row').length < 60) await new Promise((r) => requestAnimationFrame(r));
        await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))); return performance.now() - start; })()""")
    env.timings["navigate_to_60_tasks_ms"] = round(elapsed, 1)
    env.check("navigating to a list of 60+ tasks takes under 300 ms", elapsed < 300, f"{elapsed:.0f} ms")
    feedback = page.js("""(async () => { const button = byText('Done', '.task-filters [role=radio]'); const start = performance.now(); button.click();
        while (!qa('.task-row').length || qa('.task-row').some((row) => !row.innerText.includes('Done'))) await new Promise((r) => requestAnimationFrame(r));
        await new Promise((r) => requestAnimationFrame(r)); return performance.now() - start; })()""")
    env.timings["filter_click_to_paint_ms"] = round(feedback, 1)
    env.check("a filter click is reflected within 100 ms", feedback < 100, f"{feedback:.0f} ms")
    page.fill(".task-search input", "Seeded task 17 ")
    env.expect("typing filters the list", "qa('.task-row').length === 0 || qa('.task-row').every((row) => row.innerText.includes('Seeded task 17 '))")
    page.fill(".task-search input", "no such task anywhere")
    env.expect("an empty result explains itself and offers a way out", "q('.page').innerText.includes('Nothing matches') && Boolean(byText('Clear filters'))")
    page.click(text="Clear filters")
    env.expect("clearing filters restores the list", "qa('.task-row').length >= 60")
    env.shot("tasks-many")

    # A large log: only the end is loaded and drawn; earlier output on request.
    started = time.monotonic()
    env.go(f"tasks/{project_id}/T-000000000001/logs")
    env.expect("the end of a large log is shown", "Boolean(q('.log-view')) && q('.log-view').innerText.includes('LAST LINE OF THE LOG')", 10)
    env.timings["open_large_log_seconds"] = round(time.monotonic() - started, 3)
    drawn = page.js("q('.log-view').innerText.length")
    env.check(f"a {log_size // 1_000_000} MB log opens in under a second and draws only its tail", env.timings["open_large_log_seconds"] < 1.0 and drawn < 400_000
              and not page.js("q('.log-view').innerText.includes('FIRST LINE OF THE LOG')"), f"{env.timings['open_large_log_seconds']}s, {drawn} characters")
    blocked = page.js("""(async () => { let worst = 0, last = performance.now(); let running = true;
        const tick = () => { const now = performance.now(); worst = Math.max(worst, now - last); last = now; if (running) requestAnimationFrame(tick); };
        requestAnimationFrame(tick); byText('Load earlier output').click();
        const before = q('.log-view').innerText.length; while (q('.log-view').innerText.length === before) await new Promise((r) => setTimeout(r, 10));
        await new Promise((r) => setTimeout(r, 100)); running = false; return worst; })()""")
    env.timings["load_earlier_longest_frame_ms"] = round(blocked, 1)
    env.check("loading earlier output keeps frames under 200 ms", blocked < 200, f"{blocked:.0f} ms")
    with (big / "ui-run.log").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write("APPENDED WHILE WATCHING\n")
    position = page.js("q('.log-view').scrollTop")
    env.expect("output appended to the file is loaded without reloading", "q('.log-view').textContent.includes('APPENDED WHILE WATCHING')", 6)
    env.check("a reader who scrolled up is not pulled to the end", page.js("q('.log-view').scrollTop") == position and page.js("q('.log-toolbar .switch').checked") is False)
    page.js("(q('.log-toolbar .switch').click(), true)")
    env.expect("turning Follow on jumps to the newest output", "q('.log-view').scrollHeight - q('.log-view').scrollTop - q('.log-view').clientHeight < 24")
    env.shot("task-logs-large")

    # Keyboard and focus.
    env.go("")
    page.wait("Boolean(q('.hero'))", 8, "the dashboard")
    page.press("k", modifiers=2)
    env.expect("Ctrl+K opens the command palette", "Boolean(q('.palette')) && document.activeElement === q('.palette-input')")
    page.call("Input.insertText", {"text": "go to settings"})
    page.press("Enter")
    env.expect("the palette navigates with the keyboard", "location.hash.startsWith('#/settings') && !q('.palette')")
    env.go("projects")
    page.wait("Boolean(byText('Add project'))", 8, "the projects page")
    page.js("(byText('Add project', '.page-head button').focus(), byText('Add project', '.page-head button').click(), true)")
    page.wait("Boolean(q('.dialog'))", 5, "the dialog")
    env.check("a dialog takes the focus", page.js("q('.dialog').contains(document.activeElement)"))
    for _ in range(14):
        page.press("Tab")
    env.check("Tab stays inside an open dialog", page.js("q('.dialog').contains(document.activeElement)"))
    page.press("Escape")
    env.expect("Escape closes the dialog and returns the focus to its opener", "!q('.dialog') && document.activeElement === byText('Add project', '.page-head button')")
    env.go(f"tasks/{project_id}/T-000000000002")
    page.wait("Boolean(q('[role=tablist]'))", 8, "the task tabs")
    page.js("(q('[role=tab][aria-selected=true]').focus(), true)")
    page.press("ArrowRight")
    env.expect("arrow keys move between tabs", "q('[role=tab][aria-selected=true]').textContent.includes('Activity') && document.activeElement === q('[role=tab][aria-selected=true]')")

    # Accessible names, landmarks, responsive layout and themes on every page.
    routes = ["", "tasks", f"tasks/{project_id}/T-000000000002", f"tasks/{project_id}/T-000000000002/activity", "new", "projects",
              "settings/global/general", "settings/global/agents", f"settings/{project_id}/workflow", f"settings/{project_id}/tests",
              f"settings/{project_id}/recovery", f"settings/{project_id}/rag", f"settings/{project_id}/advanced", "about", "welcome"]
    unnamed = """qa('button, a[href], input, select, textarea').filter(visible).filter((el) => {
        const label = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.textContent.trim() || el.getAttribute('title')
          || (el.id && q(`label[for="${el.id}"]`)?.textContent.trim()) || el.closest('label')?.textContent.trim() || el.getAttribute('placeholder');
        return !label; }).map((el) => el.outerHTML.slice(0, 120))"""
    overflow = """(() => { const view = q('.view') || document.documentElement; const wide = qa('.view *').filter(visible).filter((el) => {
        const box = el.getBoundingClientRect(); return box.right > innerWidth + 1 && !el.closest('.log-view, .diff-body, pre, .tabs, .task-filters, .settings-nav, .wizard-steps'); });
        return { page: document.documentElement.scrollWidth - innerWidth, view: view.scrollWidth - view.clientWidth, wide: wide.slice(0, 3).map((el) => el.className || el.tagName) }; })()"""
    failures, missing = [], []
    for width, height in ((1440, 900), (1024, 768), (820, 1180), (480, 860)):
        page.viewport(width, height)
        for route in routes:
            env.go(route)
            time.sleep(0.25)
            found = page.js(overflow)
            if found["page"] > 1 or found["view"] > 1 or found["wide"]:
                failures.append(f"{width}px #{route}: {found}")
            if width in (1440, 480):
                names = page.js(unnamed)
                if names:
                    missing.append(f"{width}px #{route}: {names[:2]}")
            if env.shots and route in ("", f"tasks/{project_id}/T-000000000002", "new", f"settings/{project_id}/tests", "projects"):
                env.shot(f"responsive-{width}-{(route or 'home').replace('/', '-')[:40]}")
    env.check("no page overflows horizontally from 1440 px down to 480 px", not failures, "; ".join(failures)[:700])
    env.check("every control has an accessible name", not missing, "; ".join(missing)[:700])
    page.viewport(480, 860)
    env.go("")
    env.expect("on a narrow window the sidebar becomes a drawer behind a menu button", "visible(q('.menu-btn')) && q('.sidebar').getBoundingClientRect().right <= 0")
    page.click(".menu-btn")
    env.expect("the menu button opens the navigation drawer", "q('.sidebar').getBoundingClientRect().left === 0")
    page.js("byText('Tasks', '.sidebar .nav-item').click()")
    env.expect("choosing a destination closes the drawer", "location.hash.startsWith('#/tasks') && q('.sidebar').getBoundingClientRect().right <= 0")
    page.viewport(1440, 900)
    env.go("")
    page.wait("Boolean(q('.hero'))", 8, "the dashboard")
    landmarks = page.js("[Boolean(q('main#main')), Boolean(q('nav[aria-label]')), Boolean(q('.skip-link')), qa('img').every((img) => img.hasAttribute('alt')), document.documentElement.lang === 'en', qa('h1').filter(visible).length === 1]")
    env.check("landmarks, a skip link, image alternatives, the language and one heading are present", all(landmarks), str(landmarks))
    page.click(".collapse-btn")
    env.expect("the sidebar collapses to a rail and keeps its names for assistive technology", "q('.shell').classList.contains('collapsed') && q('.sidebar').getBoundingClientRect().width < 80 && qa('.sidebar .nav-item').every((item) => item.textContent.trim())")
    page.click(".collapse-btn")
    page.js("(byText('Switch theme', '.icon-btn') || q('.side-tools .icon-btn')).click()")
    env.expect("the light theme can be switched on", "document.documentElement.dataset.theme === 'light'", 5)
    contrast = page.js("""(() => { const lum = (c) => { const v = c.match(/[\\d.]+/g).slice(0, 3).map((n) => n / 255).map((n) => n <= .03928 ? n / 12.92 : ((n + .055) / 1.055) ** 2.4); return .2126 * v[0] + .7152 * v[1] + .0722 * v[2]; };
        const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + .05) / (y + .05); };
        const body = getComputedStyle(document.body); const muted = getComputedStyle(q('.stat-hint'));
        return [ratio(body.color, body.backgroundColor), ratio(muted.color, getComputedStyle(q('.stat')).backgroundColor)]; })()""")
    env.check("light theme text contrast meets WCAG AA", contrast[0] >= 7 and contrast[1] >= 4.5, str(contrast))
    env.shot("home-light")
    page.js("q('.side-tools .icon-btn').click()")
    env.expect("and back to the dark theme", "document.documentElement.dataset.theme === 'dark'", 5)
    contrast = page.js("""(() => { const lum = (c) => { const v = c.match(/[\\d.]+/g).slice(0, 3).map((n) => n / 255).map((n) => n <= .03928 ? n / 12.92 : ((n + .055) / 1.055) ** 2.4); return .2126 * v[0] + .7152 * v[1] + .0722 * v[2]; };
        const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + .05) / (y + .05); };
        const body = getComputedStyle(document.body); const muted = getComputedStyle(q('.stat-hint'));
        return [ratio(body.color, body.backgroundColor), ratio(muted.color, getComputedStyle(q('.stat')).backgroundColor)]; })()""")
    env.check("dark theme text contrast meets WCAG AA", contrast[0] >= 7 and contrast[1] >= 4.5, str(contrast))
    page.reduced_motion(True)
    env.go("about")
    time.sleep(0.3)
    still = page.js("qa('.rondo, .dot.live, .rondo-face').every((el) => parseFloat(getComputedStyle(el).animationDuration) < 0.01)")
    env.check("animations are switched off when reduced motion is requested", still)
    page.reduced_motion(False)

    # Errors are shown, not swallowed.
    env.go(f"tasks/{project_id}/T-ffffffffffff")
    env.expect("an unknown task explains itself", "q('.view').innerText.includes('does not exist')", 8)
    env.go("nowhere")
    env.expect("an unknown address has its own page", "q('.view').innerText.includes('Page not found')")
    bad = env.api("POST", f"/api/projects/{project_id}/tasks", {"title": "x", "description": "y", "developer": "gpt", "reviewer": "codex"})
    env.check("invalid input is rejected with a uniform error", bad[0] == 400 and bad[1]["code"] == "invalid" and "claude, codex" in bad[1]["error"], str(bad))
    env.clean_console("quality", allow=("status of 400", "status of 404", "net::ERR"))
    env.stop()


SCENARIO_FUNCTIONS = {"wizard": scenario_wizard, "run": scenario_run, "restart": scenario_restart, "reconnect": scenario_reconnect,
                      "recovery": scenario_recovery, "projects": scenario_projects, "legacy": scenario_legacy, "quality": scenario_quality}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", action="append", choices=SCENARIOS, help="Run only this scenario (repeatable)")
    parser.add_argument("--screenshots", type=Path, help="Save screenshots of key moments in this directory")
    parser.add_argument("--output", type=Path, help="Write a JSON report with checks and timings")
    parser.add_argument("--keep", action="store_true", help="Keep the temporary files after a pass")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # page text is printed when a check fails
    executable = browser.find_browser()
    if executable is None:
        print("ui-e2e: SKIPPED (no Chromium-based browser found; set PATCHRONDO_BROWSER to its executable)")
        return 0
    chosen = args.only or list(SCENARIOS)
    page = browser.Page(executable)
    env = Env(page, args.screenshots.resolve() if args.screenshots else None)
    try:
        with patch("patchrondo.app.doctor.check_all", return_value=PROVIDERS):
            for name in chosen:
                print(f"--- {name}", flush=True)
                try:
                    SCENARIO_FUNCTIONS[name](env)
                except Exception as exc:  # noqa: BLE001 - reported as a failed check with the page text
                    try:
                        shown = page.js("document.body.innerText.split(/\\s+/).join(' ').slice(0, 300)")
                        detail = f"{type(exc).__name__}: {exc} | page: {shown}"
                    except Exception:  # noqa: BLE001
                        detail = f"{type(exc).__name__}: {exc}"
                    env.check(f"{name}: scenario completed", False, detail)
                    if env.shots:
                        try:
                            page.screenshot(env.shots / f"failure-{name}.png")
                        except Exception:  # noqa: BLE001
                            pass
                    if env.server is not None:
                        env.stop()
    finally:
        page.close()
    passed = sum(1 for _, ok, _ in env.checks if ok)
    failed = len(env.checks) - passed
    report = {"platform": platform.platform(), "python": platform.python_version(), "browser": executable, "scenarios": chosen,
              "passed": passed, "failed": failed, "timings": env.timings,
              "checks": [{"name": name, "passed": ok, "detail": detail} for name, ok, detail in env.checks]}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"timings: {json.dumps(env.timings)}")
    if failed or args.keep:
        print(f"Evidence retained: {env.root}")
    else:
        for _ in range(20):
            shutil.rmtree(env.root, ignore_errors=True)
            if not env.root.exists():
                break
            time.sleep(0.3)
    print(f"RESULT: ui-e2e {'PASS' if not failed else 'FAIL'} ({passed} passed, {failed} failed; scripted providers, "
          f"{Path(executable).stem} headless)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
