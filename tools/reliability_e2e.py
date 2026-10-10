"""Local reliability checks using synthetic CLIs, never authenticated providers.

Exercises feedback, quota recovery, interruption, stale locks and a three-file
Python task in real subprocesses. Reports objective fixture checks, not model
quality. State and transcripts stay in a temporary directory outside the repo.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from patchrondo.core import create_task, initialize  # noqa: E402
from patchrondo.gitops import fingerprint, git  # noqa: E402
from patchrondo.storage import read_json, save_json  # noqa: E402

SCENARIOS = ("feedback", "quota-develop", "quota-review", "interrupt", "crash", "multi-file")
PAIRINGS = (("claude", "codex"), ("codex", "claude"))
FEEDBACK = "Document blank-name validation in normalize_name."
IMPLEMENTATION = {
    "names.py": '''def normalize_name(name):
    """Strip surrounding whitespace and reject blank names."""
    value = name.strip()
    if not value:
        raise ValueError("name must not be blank")
    return value
''',
    "greetings.py": '''from names import normalize_name


def greet_many(names):
    return [f"Hello, {normalize_name(name)}!" for name in names]
''',
    "app.py": '''import json
import sys
from greetings import greet_many


if __name__ == "__main__":
    print(json.dumps(greet_many(json.load(sys.stdin)), ensure_ascii=False))
''',
}
FIXTURE_TEST = '''import json
import subprocess
import sys
import unittest
from greetings import greet_many


class GreetingTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(greet_many(["Rondo", "Ada"]), ["Hello, Rondo!", "Hello, Ada!"])

    def test_whitespace(self):
        self.assertEqual(greet_many([" Rondo "]), ["Hello, Rondo!"])

    def test_unicode(self):
        self.assertEqual(greet_many(["Zoë"]), ["Hello, Zoë!"])

    def test_empty(self):
        self.assertEqual(greet_many([]), [])

    def test_blank(self):
        with self.assertRaisesRegex(ValueError, "name must not be blank"):
            greet_many([" "])

    def test_cli(self):
        result = subprocess.run([sys.executable, "app.py"], input='["Ada"]',
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["Hello, Ada!"])
'''

# This executable receives the provider name explicitly. It does not search PATH.
SYNTHETIC_CLI = r'''import json, os, pathlib, sys, time
root = pathlib.Path(__file__).parent
control = json.loads((root / 'control.json').read_text(encoding='utf-8'))
provider, args = sys.argv[1], sys.argv[2:]
prompt = sys.stdin.read()
reviewer = ('--json-schema' in args if provider == 'claude' else '--output-schema' in args)
role = 'reviewer' if reviewer else 'developer'
with (root / 'calls.jsonl').open('a', encoding='utf-8') as stream:
    stream.write(json.dumps({'provider': provider, 'role': role, 'at': time.monotonic()}) + '\n')
scenario = control['scenario']
if scenario == ('quota-review' if reviewer else 'quota-develop') and not (root / 'quota.once').exists():
    (root / 'quota.once').touch()
    print('Usage limit reached; resets at 23:59' if provider == 'claude' else 'quota exceeded', file=sys.stderr)
    sys.exit(1)
if scenario == 'interrupt' and reviewer and not (root / 'interrupt.once').exists():
    (root / 'interrupt.once').touch()
    (root / 'ready.json').write_text(json.dumps({'pid': os.getpid()}), encoding='utf-8')
    time.sleep(120)
if reviewer:
    missing_doc = '"""' not in pathlib.Path('names.py').read_text(encoding='utf-8')
    verdict = 'CHANGES_REQUESTED' if scenario == 'feedback' and missing_doc else 'APPROVED'
    issues = [{'severity': 'medium', 'path': 'names.py', 'description': control['feedback']}] if verdict != 'APPROVED' else []
    payload = {'verdict': verdict, 'summary': 'Synthetic acceptance review', 'issues': issues}
    text = json.dumps(payload)
else:
    second = '## Current iteration\n2\n' in prompt
    if scenario == 'feedback' and second:
        assert control['feedback'] in prompt, 'Review feedback was not delivered to the developer'
        assert pathlib.Path('greetings.py').read_text(encoding='utf-8') == control['files']['greetings.py']
        (root / 'feedback.received').touch()
    for name, body in control['files'].items():
        if scenario == 'feedback' and not second and name == 'names.py':
            body = '\n'.join(line for line in body.split('\n') if '"""' not in line)
        # The correction must preserve the other files from the first iteration.
        if not (scenario == 'feedback' and second and name != 'names.py'):
            pathlib.Path(name).write_text(body, encoding='utf-8', newline='\n')
    text = 'Changes: implemented names.py, greetings.py and app.py. Suggested tests: unittest.'
if provider == 'claude':
    print(json.dumps({'structured_output': payload} if reviewer else {'result': text}))
else:
    pathlib.Path(args[args.index('--output-last-message') + 1]).write_text(text, encoding='utf-8')
'''


def worker(root: Path, task_id: str, unlock: bool) -> int:
    """Run the real CLI entry point with an exclusive route to synthetic CLIs."""
    from patchrondo.cli import main as cli_main
    from patchrondo.process import execute

    control = read_json(root / "control.json")

    def synthetic_execute(argv, cwd, **kwargs):
        if argv[0] not in {"claude", "codex"}:
            raise ValueError("Unexpected provider command")
        reviewer = "--json-schema" in argv or "--output-schema" in argv
        if control["scenario"] == "crash" and reviewer and not (root / "crash.once").exists():
            (root / "crash.once").touch()
            save_json(root / "ready.json", {"pid": os.getpid()})
            time.sleep(120)  # Kill the runner at the persisted review checkpoint.
        started = time.monotonic()
        try:
            return execute([sys.executable, str(root / "synthetic.py"), *argv], cwd, **kwargs)
        finally:
            with (root / "timings.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"provider": argv[0], "role": "reviewer" if reviewer else "developer",
                                         "seconds": round(time.monotonic() - started, 3)}) + "\n")

    provider_free_path = os.pathsep.join(
        directory for directory in os.environ.get("PATH", "").split(os.pathsep)
        if directory and not any(shutil.which(name, path=directory) for name in ("claude", "codex")))
    with patch.dict(os.environ, {"PATH": provider_free_path}), \
         patch("patchrondo.providers.execute", side_effect=synthetic_execute):
        if any(shutil.which(name) for name in ("claude", "codex")):
            raise RuntimeError("A real provider CLI is still on PATH; refusing the synthetic run")
        args = ["--home", str(root / "state"), "resume", task_id]
        return cli_main(args + (["--unlock"] if unlock else []))


def setup(root: Path, scenario: str, developer: str, reviewer: str) -> tuple[str, Path]:
    repo = root / "repo"
    repo.mkdir()
    git("init", "-q", cwd=repo)
    (repo / "README.md").write_text("# Synthetic reliability fixture\n", encoding="utf-8", newline="\n")
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8", newline="\n")
    (repo / "test_greetings.py").write_text(FIXTURE_TEST, encoding="utf-8", newline="\n")
    git("add", ".", cwd=repo)
    git("-c", "user.name=PatchRondo Check", "-c", "user.email=check@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture", cwd=repo)
    initialize(root / "state", repo)
    cfg = read_json(root / "state" / "config.json")
    cfg["workflow"].update(max_iterations=2, agent_timeout_seconds=30)
    cfg["tests"] = {"enabled": True, "trust_acknowledged": True,
                    "commands": [[sys.executable, "-m", "unittest", "-v", "test_greetings"]]}
    save_json(root / "state" / "config.json", cfg)
    save_json(root / "control.json", {"scenario": scenario, "files": IMPLEMENTATION, "feedback": FEEDBACK})
    (root / "synthetic.py").write_text(SYNTHETIC_CLI, encoding="utf-8", newline="\n")
    return create_task(root / "state", title="Implement batch greetings and JSON CLI",
                       description="Implement names.py validation, greetings.py batch greetings and app.py JSON I/O. "
                                   "Preserve tests and document blank-name validation in normalize_name.",
                       acceptance=["Six functional tests pass", "Validation is documented", "No unrelated edits"],
                       developer=developer, reviewer=reviewer)


def launch(root: Path, task_id: str, *, unlock: bool = False) -> subprocess.Popen:
    code = ("import sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r}); "
            "from reliability_e2e import worker; "
            "raise SystemExit(worker(Path(sys.argv[1]), sys.argv[2], sys.argv[3] == '1'))")
    return subprocess.Popen([sys.executable, "-c", code, str(root), task_id, "1" if unlock else "0"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            start_new_session=(os.name == "posix"))


def finish(proc: subprocess.Popen) -> tuple[int, str]:
    out, err = proc.communicate(timeout=60)
    return proc.returncode, out + err


def wait_ready(proc: subprocess.Popen, root: Path) -> dict:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if (root / "ready.json").exists():
            try:
                return read_json(root / "ready.json")
            except json.JSONDecodeError:
                pass
        if proc.poll() is not None:
            raise RuntimeError(f"Runner exited before interruption: {finish(proc)}")
        time.sleep(0.02)
    raise TimeoutError("Runner did not reach the interruption checkpoint")


def run_scenario(root: Path, scenario: str, developer: str, reviewer: str) -> dict:
    """Return checks and metrics; never retry real providers or wait for real quota."""
    if scenario not in SCENARIOS or (developer, reviewer) not in PAIRINGS:
        raise ValueError("Unknown scenario or pairing")
    result = {"scenario": scenario, "pairing": f"{developer}->{reviewer}", "checks": []}
    if scenario in {"interrupt", "crash"} and os.name != "posix":
        return {**result, "status": "skipped", "reason": "Requires POSIX signals and stale-lock PID checks"}
    started = time.monotonic()
    task_id, workspace = setup(root, scenario, developer, reviewer)
    path = root / "state" / "tasks" / task_id
    fixture_fingerprint = fingerprint(root / "repo")
    processes = []

    def check(name, ok):
        result["checks"].append({"name": name, "passed": bool(ok)})

    def run(*, unlock=False):
        proc = launch(root, task_id, unlock=unlock)
        processes.append(proc)
        return proc

    try:
        proc = run()
        if scenario in {"interrupt", "crash"}:
            ready = wait_ready(proc, root)
            if scenario == "interrupt":
                proc.send_signal(signal.SIGINT)
            else:
                proc.kill()
        code, output = finish(proc)
        (root / "first-run.txt").write_text(output, encoding="utf-8", newline="\n")
        first = read_json(path / "state.json")
        before_resume = fingerprint(workspace)
        if scenario.startswith("quota") or scenario == "interrupt":
            kind = "interrupted" if scenario == "interrupt" else "quota"
            phase = "develop" if scenario == "quota-develop" else "review"
            check("paused at the failed phase", code == 2 and first["status"] == "paused"
                  and first["phase"] == phase and first["last_error"]["kind"] == kind)
            check("lock and process markers removed", not (path / ".run.lock").exists()
                  and not list(path.glob("runs/iteration-*/*.active-process.json")))
            if scenario == "interrupt":
                try:
                    os.kill(ready["pid"], 0)
                    stopped = False
                except ProcessLookupError:
                    stopped = True
                check("interrupted provider process exited", stopped)
            code, output = finish(run())  # The driver resumes only after the injected failure.
        elif scenario == "crash":
            check("crash retained review checkpoint and lock", code != 0 and first["status"] == "running"
                  and first["phase"] == "review" and (path / ".run.lock").exists())
            refused, _ = finish(run())
            check("stale lock prevents ordinary resume", refused == 1)
            code, output = finish(run(unlock=True))
        state = read_json(path / "state.json")
        history = state["history"]
        calls = [json.loads(line) for line in (root / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
        events = [entry["event"] for entry in history]
        expected_roles = {
            "feedback": ["developer", "reviewer", "developer", "reviewer"],
            "quota-develop": ["developer", "developer", "reviewer"],
            "quota-review": ["developer", "reviewer", "reviewer"],
            "interrupt": ["developer", "reviewer", "reviewer"],
            "crash": ["developer", "reviewer"],
            "multi-file": ["developer", "reviewer"],
        }[scenario]
        check("exact provider call sequence", [call["role"] for call in calls] == expected_roles
              and all(call["provider"] == (developer if call["role"] == "developer" else reviewer) for call in calls))
        check("completed with passing tests and approval", code == 0 and state["status"] == "done"
              and state["phase"] == "complete" and state["review"]["verdict"] == "APPROVED"
              and bool(state["tests"]) and all(test["status"] == "passed" for test in state["tests"]))
        check("expected iterations", state["iteration"] == (2 if scenario == "feedback" else 1))
        if scenario == "feedback":
            check("review feedback delivered before correction", (root / "feedback.received").exists())
            check("rejection then approval with green tests in both iterations",
                  [e["verdict"] for e in history if e["event"] == "review_completed"] == ["CHANGES_REQUESTED", "APPROVED"]
                  and [e["statuses"] for e in history if e["event"] == "tests_completed"] == [["passed"], ["passed"]])
        if scenario in {"quota-review", "interrupt", "crash"}:
            check("resume preserved code and did not repeat completed stages", fingerprint(workspace) == before_resume
                  and events.count("development_completed") == 1 and events.count("tests_completed") == 1)
        check("main checkout and tests unchanged", fingerprint(root / "repo") == fixture_fingerprint
              and (workspace / "test_greetings.py").read_text(encoding="utf-8") == FIXTURE_TEST
              and git("rev-parse", "HEAD", cwd=workspace) == state["base_sha"])
        changed = git("ls-files", "--others", "--exclude-standard", cwd=workspace).splitlines()
        check("only the three requested files changed", sorted(changed) == sorted(IMPLEMENTATION)
              and not git("diff", "HEAD", "--name-only", cwd=workspace))
        check("validation behavior documented", bool(ast.get_docstring(ast.parse(
            (workspace / "names.py").read_text(encoding="utf-8")).body[0])))
        check("review left tested worktree unchanged", state["test_context"]["fingerprint"] == fingerprint(workspace))
        check("no remaining locks or process markers", not (path / ".run.lock").exists()
              and not list(path.glob("runs/iteration-*/*.active-process.json")))
        timings = [json.loads(line) for line in (root / "timings.jsonl").read_text(encoding="utf-8").splitlines()]
        result.update(iterations=state["iteration"], provider_calls=len(calls),
                      test_runs=events.count("tests_completed"), changed_files=len(changed),
                      functional_cases_passed=6 if state["tests"] and all(
                          test["status"] == "passed" for test in state["tests"]) else 0,
                      provider_timings=timings, final_status=state["status"])
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        # A failing interruption check must not leave its synthetic child alive.
        if os.name == "posix":
            for marker in path.glob("runs/iteration-*/*.active-process.json"):
                try:
                    os.killpg(read_json(marker)["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
    result["seconds"] = round(time.monotonic() - started, 3)
    result["status"] = "passed" if all(item["passed"] for item in result["checks"]) else "failed"
    return result


def cleanup(root: Path) -> None:
    """Remove only a fixture created under the system temporary directory."""
    if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith("patchrondo-reliability-"):
        raise ValueError("Refusing cleanup outside the reliability fixture directory")

    def writable_remove(function, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        function(path)

    shutil.rmtree(root, onerror=writable_remove)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, action="append", help="Repeatable; defaults to all")
    parser.add_argument("--repeat", type=int, default=1, help="Repetitions per scenario and pairing (1-20)")
    parser.add_argument("--output", type=Path, help="Optional JSON metrics report; no transcripts included")
    args = parser.parse_args(argv)
    if not 1 <= args.repeat <= 20:
        parser.error("--repeat must be between 1 and 20")
    report = {"at": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
              "python": platform.python_version(), "providers": "synthetic", "results": []}
    for repetition in range(1, args.repeat + 1):
        for scenario in dict.fromkeys(args.scenario or SCENARIOS):
            for developer, reviewer in PAIRINGS:
                # mkdtemp permits retaining failed fixtures without private tempfile APIs.
                root = Path(tempfile.mkdtemp(prefix="patchrondo-reliability-")).resolve()
                try:
                    result = run_scenario(root, scenario, developer, reviewer)
                except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                    result = {"scenario": scenario, "pairing": f"{developer}->{reviewer}",
                              "status": "failed", "error": str(exc)}
                result["repetition"] = repetition
                if result["status"] == "failed":
                    result["evidence"] = str(root)
                else:
                    try:
                        cleanup(root)
                    except OSError as exc:
                        result["cleanup_error"] = str(exc)
                        result["evidence"] = str(root)
                report["results"].append(result)
                print(f"{scenario} {developer}->{reviewer}: {result['status'].upper()} "
                      f"({result.get('seconds', 0)}s, {result.get('iterations', 0)} iterations, "
                      f"{result.get('provider_calls', 0)} calls)", flush=True)
                for item in result.get("checks", []):
                    if not item["passed"]:
                        print(f"  FAILED: {item['name']}")
                if "error" in result:
                    print(f"  {result['error']}")
                if "evidence" in result:
                    print(f"  Evidence retained: {root}")
    counts = {status: sum(result["status"] == status for result in report["results"])
              for status in ("passed", "failed", "skipped")}
    report["counts"] = counts
    if args.output:
        save_json(args.output.resolve(), report)
    print(f"RESULT: {counts['passed']} passed, {counts['failed']} failed, {counts['skipped']} skipped (synthetic providers)")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
