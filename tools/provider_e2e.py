"""Check the real Claude Code and Codex CLIs end to end. Opt-in: it uses plan quota.

Runs the regular develop -> test -> review loop on a throwaway repository, once
per pairing (Claude -> Codex and Codex -> Claude), and checks what simulated
providers cannot: that the installed CLIs accept the adapter's arguments, that
the developer can write inside the worktree, that the reviewer leaves it
untouched, that both replies are extracted and parsed, and that both CLIs
accept the streaming options and report their activity while they work.

Without --authorize-provider-calls it only reports installation, versions and
login, and makes no model calls. With it, each pairing costs one developer
call: the fixture's tests are not authorized, so the loop pauses at the missing
test consent before any review. That run checks the developer side only, and
the reviewer's checks are printed as "not exercised", never as passed. Add
--run-fixture-tests to consent to running the fixture's unit test on this
machine against the agent-written code: the reviewer is then called and
checked too, and a completed, approved task is required (at most two
iterations, so up to four calls per pairing).

    python tools/provider_e2e.py
    python tools/provider_e2e.py --authorize-provider-calls
    python tools/provider_e2e.py --authorize-provider-calls --run-fixture-tests --pairing codex-claude
"""
from __future__ import annotations

import argparse
import ast
from datetime import date
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from patchrondo import activity  # noqa: E402
from patchrondo.core import create_task, initialize, run_task  # noqa: E402
from patchrondo.gitops import changes, fingerprint, git  # noqa: E402
from patchrondo.process import execute  # noqa: E402
from patchrondo.storage import read_json, save_json  # noqa: E402

PAIRINGS = {"claude-codex": ("claude", "codex"), "codex-claude": ("codex", "claude")}
LOGIN = {"claude": ["claude", "auth", "status"], "codex": ["codex", "login", "status"]}
# Failures the next pairing would only repeat, at the cost of more quota.
STOP_KINDS = {"interrupted", "quota", "authentication"}
FIXTURE_TEST = '''import unittest

from greeting import greet


class GreetingTests(unittest.TestCase):
    def test_greets_by_name(self):
        self.assertEqual(greet("Rondo"), "Hello, Rondo!")


if __name__ == "__main__":
    unittest.main()
'''
DESCRIPTION = ("Create `greeting.py` in the repository root with a function `greet(name)` that returns "
               "`Hello, <name>!`. The existing `test_greeting.py` must pass unchanged. Change nothing else.")
ACCEPTANCE = ['`greet("Rondo")` returns `Hello, Rondo!`', "`test_greeting.py` is not modified"]
NOT_EXERCISED = "the loop stops before the review without --run-fixture-tests"
MARKS = {True: "ok", False: "FAILED", None: "not exercised"}


def probe(name: str) -> dict:
    """Installation, version and login of one CLI, started the way the adapter starts it. No model calls."""
    info = {"path": shutil.which(name), "version": "", "ready": False, "note": "NOT INSTALLED"}
    if not info["path"]:
        return info
    try:
        version = execute([name, "--version"], Path.cwd(), timeout=30)
        login = execute(LOGIN[name], Path.cwd(), timeout=30)
    except OSError as exc:
        # For example a Windows .cmd shim: on PATH, but not startable without a shell.
        info["note"] = f"cannot be started: {exc}"
        return info
    info["version"] = (version.stdout.strip().splitlines() or [""])[0]
    info["ready"] = login.returncode == 0
    info["note"] = "login OK" if info["ready"] else "login NOT CONFIRMED"
    if name == "claude" and info["ready"]:
        try:
            method = json.loads(login.stdout).get("authMethod", "unknown")
        except (json.JSONDecodeError, AttributeError):
            method = "unknown"
        info["note"] += f" · method {method}"
        if method not in {"claude.ai", "oauth_token"}:
            info["note"] += " (check whether API billing is being used)"
    return info


def build_fixture(root: Path, *, run_tests: bool, agent_timeout: int) -> tuple[Path, Path]:
    """Create the throwaway repository and its state directory; return (repository, state directory)."""
    repo = root / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True, timeout=30)
    files = {"README.md": "# PatchRondo provider check fixture\n", ".gitignore": "__pycache__/\n*.pyc\n",
             "test_greeting.py": FIXTURE_TEST}
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8", newline="\n")
    for args in (["add", "."], ["-c", "user.name=PatchRondo Check", "-c", "user.email=check@example.invalid",
                                "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Provider check fixture"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=30)
    home = root / "state"
    initialize(home, repo)
    cfg = read_json(home / "config.json")
    cfg["workflow"].update(max_iterations=2 if run_tests else 1, agent_timeout_seconds=agent_timeout)
    if run_tests:
        # Explicit consent through --run-fixture-tests: this runs agent-written code on the host.
        cfg["tests"] = {"enabled": True, "trust_acknowledged": True,
                        "commands": [[sys.executable, "-m", "unittest", "-v", "test_greeting"]]}
    save_json(home / "config.json", cfg)
    return repo, home


def defines_greet(path: Path) -> bool:
    """Static check only: agent-written code is never imported or run here."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return False
    return any(isinstance(node, ast.FunctionDef) and node.name == "greet" for node in tree.body)


def recorded_activity(task_path: Path) -> list[dict]:
    """Every event in a task's activity journal, read page by page from its start."""
    journal = activity.read(task_path, limit=1)["journal"]
    events, cursor = [], f"{journal}:0" if journal else None
    while cursor:
        page = activity.read(task_path, cursor)
        events += page["events"]
        cursor = page["next"] if page["more"] else None
    return events


def run_pairing(repo: Path, home: Path, developer: str, reviewer: str, *, run_tests: bool) -> dict:
    """Run one task with the real adapter and check each stage of the loop that the run reaches.

    A check is True, False or, when the run cannot reach it, None: without the
    fixture's tests the loop stops before the review, so nothing is learned
    about the reviewer.
    """
    task_id, workspace = create_task(home, title="Add a greeting helper", description=DESCRIPTION,
                                     acceptance=ACCEPTANCE, developer=developer, reviewer=reviewer)
    started = time.monotonic()
    state = run_task(home, task_id)
    seconds = round(time.monotonic() - started)
    events = [entry["event"] for entry in state["history"]]
    error = state.get("last_error")
    kind = error.get("kind") if isinstance(error, dict) else error
    message = error.get("message", "") if isinstance(error, dict) else ""
    reply = home / "tasks" / task_id / "runs" / "iteration-001" / "developer.reply.md"
    review = state.get("review") or {}
    if run_tests:
        expected, ended = "task completed with passing tests and an approved review", state["status"] == "done"
    else:
        # A reviewer call here would mean that the loop went past the missing consent.
        expected = "loop paused at the missing test consent, before any review"
        ended = state["status"] == "paused" and kind == "tests_disabled" and "review_started" not in events
    outcome = f"status {state['status']}" + (f" · {kind}" if kind else "")
    recorded = recorded_activity(home / "tasks" / task_id)

    def reported(role: str) -> tuple[bool, str]:
        seen = sum(event["role"] == role and event["type"] in {"message", "tool", "file"} for event in recorded)
        notices = [str(event.get("text", ""))[:200] for event in recorded
                   if event["role"] == role and event.get("status") == "notice"]
        return seen > 0, f"{seen} {role} events" + (f"; {notices[0]}" if notices else "")

    def of_reviewer(ok: bool, detail: str = "") -> tuple[bool | None, str]:
        """A reviewer check: not exercised, rather than passed or failed, in a run that stops before the review."""
        return (ok, detail) if run_tests else (None, NOT_EXERCISED)

    checks = [
        ("developer call returned a handoff",
         "development_completed" in events and reply.is_file() and reply.stat().st_size > 0, ""),
        ("developer wrote greeting.py inside the worktree", defines_greet(workspace / "greeting.py"),
         "changed: " + (", ".join(changes(workspace)) or "nothing")),
        ("developer made no commits and the main checkout is clean",
         git("rev-parse", "HEAD", cwd=workspace) == state["base_sha"] == git("rev-parse", "HEAD", cwd=repo)
         and not git("status", "--porcelain", cwd=repo), ""),
        ("developer CLI reported its activity while it worked", *reported("developer")),
        ("reviewer call returned a valid review",
         *of_reviewer("review_completed" in events, f"verdict {review.get('verdict')}" if review else "")),
        ("reviewer left the worktree unchanged", *of_reviewer(
            "review_started" in events and state.get("test_context", {}).get("fingerprint") == fingerprint(workspace))),
        ("reviewer CLI reported its activity while it worked", *of_reviewer(*reported("reviewer"))),
        (expected, ended, outcome if ended or not message else f"{outcome}: {message[:600]}"),
    ]
    return {"pairing": f"{developer} -> {reviewer}", "seconds": seconds, "checks": checks,
            "passed": all(ok is not False for _, ok, _ in checks),
            "not_exercised": sum(ok is None for _, ok, _ in checks), "stop": kind in STOP_KINDS}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--authorize-provider-calls", action="store_true",
                        help="Consent to real Claude/Codex calls that use your plan quota")
    parser.add_argument("--run-fixture-tests", action="store_true",
                        help="Consent to running the fixture's unit test on this machine against agent-written code; "
                             "without it the loop stops before the review and the reviewer is not checked")
    parser.add_argument("--pairing", action="append", choices=sorted(PAIRINGS),
                        help="Developer-reviewer pairing to run (repeatable; default: both)")
    parser.add_argument("--agent-timeout", type=int, default=600, help="Seconds allowed for each provider call")
    parser.add_argument("--keep", action="store_true", help="Keep the fixture and private task state after a pass")
    args = parser.parse_args(argv)
    pairings = [PAIRINGS[name] for name in dict.fromkeys(args.pairing or PAIRINGS)]
    plan = ", ".join(f"{developer} -> {reviewer}" for developer, reviewer in pairings)

    print(f"PatchRondo provider check · {date.today().isoformat()} · {platform.platform()} · "
          f"Python {platform.python_version()}")
    ready = True
    for name in sorted({agent for pair in pairings for agent in pair}):
        info = probe(name)
        ready = ready and info["ready"]
        print(f"{name}: {info['path'] or 'not on PATH'} · {info['version'] or 'version unknown'} · {info['note']}")
    if not args.authorize_provider_calls:
        calls = ("up to 4 provider calls per pairing" if args.run_fixture_tests else
                 "1 developer call per pairing; the reviewer is called only with --run-fixture-tests")
        print(f"No model calls were made. --authorize-provider-calls would run {plan} ({calls}).")
        return 0 if ready else 1
    if not ready:
        print("Error: a required CLI is missing or not logged in; no model calls were made.", file=sys.stderr)
        return 1

    root = Path(tempfile.mkdtemp(prefix="patchrondo-e2e-")).resolve()
    results = []
    try:
        repo, home = build_fixture(root, run_tests=args.run_fixture_tests, agent_timeout=args.agent_timeout)
        for developer, reviewer in pairings:
            print(f"\nRunning {developer} -> {reviewer} ...", flush=True)
            result = run_pairing(repo, home, developer, reviewer, run_tests=args.run_fixture_tests)
            results.append(result)
            print(f"{result['pairing']}: {'PASS' if result['passed'] else 'FAIL'} ({result['seconds']}s)")
            for name, ok, detail in result["checks"]:
                print(f"  [{MARKS[ok]}] {name}" + (f" ({detail})" if detail else ""))
            if result["stop"]:
                print("Stopping: another pairing would repeat this failure and use more quota.")
                break
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
    passed = len(results) == len(pairings) and all(result["passed"] for result in results)
    if passed and not args.keep:
        shutil.rmtree(root, ignore_errors=True)
    if root.exists():
        print(f"\nFixture and private task state, including provider transcripts, kept in {root}")
    skipped = sum(result["not_exercised"] for result in results)
    # A check the run could not reach is counted beside the verdict, never inside it.
    scope = f" · developer side only: {skipped} reviewer checks not exercised (add --run-fixture-tests)" if skipped else ""
    print(f"\nRESULT: {'PASS' if passed else 'FAIL'} · {plan}{scope}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
