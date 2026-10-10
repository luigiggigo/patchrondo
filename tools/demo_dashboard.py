"""Try the interface in one command: no setup needed.

Creates two throwaway Git repositories and a state directory, registers them as
projects, adds sample tasks in every saved status and opens the interface.
Everything is deleted on exit unless --keep is passed or a run started from
the interface is still active. Opening the demo makes no provider calls;
Run/Resume on a task created in the demo calls the real CLIs.

The sample tasks are saved states only: no process is attached to them, and the
interface says so. A saved "running" status without a process is shown as
interrupted, and a saved retry plan as not attended.

    python tools/demo_dashboard.py
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from patchrondo.storage import atomic_text, save_json  # noqa: E402
from patchrondo.ui import RunsAtExit, serve  # noqa: E402
from patchrondo.workspace import Workspace  # noqa: E402

TASK_TEXT = """# {title}

## Description

Implement the `exp` check in **auth/jwt.py** and reject expired tokens.

## Acceptance criteria

- Expired tokens are rejected
- The test suite passes
"""
HANDOFF = """# Handoff — iteration 2

## Changes

- Added `verify_expiry()`
- New tests in `tests/test_jwt.py`

```python
if claims["exp"] < now():
    raise ExpiredToken
```
"""


def repository(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True, timeout=30)
    (path / "README.md").write_text(f"# {path.name}\n", encoding="utf-8", newline="\n")
    # A first commit lets "New task" create real worktrees in the demo repository.
    for args in (["add", "README.md"], ["-c", "user.name=PatchRondo Demo", "-c", "user.email=demo@example.invalid",
                                      "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Demo repository"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, timeout=30)
    return path


def setup(root: Path) -> Path:
    """Create two demo repositories registered as projects; return the state directory."""
    workspace = Workspace(root / "state")
    # Tests stay disabled, as for any new project, until the user consents in the interface.
    shop = workspace.add(repository(root / "shop-api"), "Shop API")
    site = workspace.add(repository(root / "docs-site"), "Docs site")
    seed(shop.home)
    seed(site.home, second=True)
    workspace.update_settings({"onboarding_completed": True})
    return workspace.root


def seed(home: Path, second: bool = False) -> None:
    now = datetime.now(timezone.utc)

    def ago(minutes: int) -> str:
        return (now - timedelta(minutes=minutes)).isoformat(timespec="seconds")

    def task(number: int, title: str, status: str, phase: str, iteration: int, developer: str,
             reviewer: str, updated: int, review=None, tests=(), error=None, history=(), recovery=None) -> None:
        task_id = f"T-{number:012x}"
        path = home / "tasks" / task_id
        save_json(path / "state.json", {
            "id": task_id, "title": title, "status": status, "phase": phase, "iteration": iteration,
            "developer": developer, "reviewer": reviewer, "created_at": ago(updated + 90),
            "updated_at": ago(updated), "review": review, "tests": list(tests), "last_error": error,
            "history": list(history), "worktree": str(home / "worktrees" / task_id),
            "base_sha": "0" * 40, "handoff": None, "recovery": recovery})
        atomic_text(path / "task.md", TASK_TEXT.format(title=title))
        if iteration > 1:
            atomic_text(path / "handoff.md", HANDOFF)

    if second:
        task(0x11, "Fix broken links in the install guide", "ready", "develop", 1, "codex", "claude", 45)
        task(0x12, "Add a dark theme to code samples", "done", "complete", 1, "claude", "codex", 1500,
             tests=[{"command": ["npm", "test"], "status": "passed", "returncode": 0}],
             review={"verdict": "APPROVED", "summary": "Readable in both themes.", "issues": []})
        return
    passed = [{"command": ["python", "-m", "pytest", "-q"], "status": "passed", "returncode": 0},
              {"command": ["ruff", "check", "."], "status": "passed", "returncode": 0}]
    history = [
        {"at": ago(40), "event": "run_started", "iteration": 1, "previous_status": "ready"},
        {"at": ago(38), "event": "development_completed", "iteration": 1, "agent": "claude"},
        {"at": ago(30), "event": "tests_completed", "iteration": 1, "statuses": ["failed"]},
        {"at": ago(25), "event": "review_completed", "iteration": 1, "verdict": "CHANGES_REQUESTED"},
        {"at": ago(12), "event": "development_completed", "iteration": 2, "agent": "claude"},
        {"at": ago(8), "event": "tests_completed", "iteration": 2, "statuses": ["passed", "passed"]},
        {"at": ago(3), "event": "review_started", "iteration": 2},
    ]
    # Saved as running, with no process and no lock: the interface shows it as interrupted.
    task(1, "Handle JWT expiration", "running", "review", 2, "claude", "codex", 1, tests=passed, history=history,
         review={"verdict": "CHANGES_REQUESTED",
                 "summary": "The expiry check works, but clock skew is not handled and one error path leaks the token.",
                 "issues": [
                     {"severity": "high", "path": "auth/jwt.py", "description": "Exception message includes the raw token value."},
                     {"severity": "medium", "path": "auth/jwt.py", "description": "Allow a configurable leeway for clock skew."},
                     {"severity": "low", "path": "", "description": "Document the new ExpiredToken exception."}]})
    # A saved retry plan only: no supervisor is waiting for it in the demo.
    task(2, "Paginate the /orders endpoint", "paused", "develop", 3, "codex", "claude", 22,
         error={"kind": "quota", "message": "codex/developer exit=1: Usage limit reached. Try again in 3 hours.",
                "at": ago(22), "provider": "codex", "retry_at": ago(22 - 180), "retry_after_seconds": 10800},
         recovery={"status": "scheduled", "consecutive_failures": 1, "max_consecutive_retries": 3,
                   "first_failure_at": ago(22), "resume_at": ago(22 - 180), "phase": "develop", "iteration": 3,
                   "schedule_source": "provider_reset", "provider": "codex", "stop_reason": None},
         history=[{"at": ago(22), "event": "task_paused", "iteration": 3, "reason": "quota"},
                  {"at": ago(22), "event": "recovery_scheduled", "iteration": 3, "attempt": 1,
                   "resume_at": ago(22 - 180), "source": "provider_reset", "provider": "codex"}])
    task(3, "Migrate settings to pydantic v2", "done", "complete", 2, "claude", "codex", 180, tests=passed,
         review={"verdict": "APPROVED", "summary": "Clean migration with full test coverage.", "issues": []})
    task(4, "Remove legacy XML exporter", "blocked", "review", 6, "codex", "claude", 600,
         error="max_iterations_reached",
         review={"verdict": "CHANGES_REQUESTED", "summary": "Two callers still import the exporter.", "issues": [
             {"severity": "high", "path": "reports/legacy.py", "description": "Still imports XmlExporter."}]})
    task(5, "Add Redis cache for product lookups", "ready", "develop", 1, "claude", "codex", 3000)


def runs_may_be_active(home: Path, runs: RunsAtExit) -> bool:
    """True if a run is alive, was still starting when the interface closed, or holds a lock."""
    return runs.any or any(home.glob("tasks/*/.run.lock")) or any(home.glob("projects/*/tasks/*/.run.lock"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765, help="Loopback port (0 picks a free port)")
    parser.add_argument("--no-browser", action="store_true", help="Print the URL without opening a browser")
    parser.add_argument("--keep", action="store_true", help="Keep the demo files after exit")
    args = parser.parse_args(argv)
    root = Path(tempfile.mkdtemp(prefix="patchrondo-demo-"))
    try:
        home = setup(root)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)  # nothing can be running before the interface starts
        raise
    print(f"Demo state: {home}")
    # Files are deleted only once it is confirmed that no run is active or starting.
    # Any other exit (error, second Ctrl+C while closing) keeps them.
    safe_to_delete = False
    code = 0
    try:
        runs = serve(home, args.port, open_browser=not args.no_browser)
        if runs_may_be_active(home, runs):
            print("A run started from the interface may still be active; its files are kept.")
        else:
            safe_to_delete = not args.keep
    except KeyboardInterrupt:
        print("\nInterrupted while closing; files are kept in case a run is still starting.")
        code = 130
    finally:
        if safe_to_delete:
            shutil.rmtree(root, ignore_errors=True)
        else:
            print(f"Demo files kept in {root}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
