"""Local checks for automatic quota recovery. Scripted or synthetic providers only.

protections  Remove one safeguard at a time from a temporary copy of the sources
             and confirm that the recovery and adapter tests fail. The working
             tree is never modified. A safeguard whose removal goes unnoticed,
             or whose source pattern no longer matches, fails the check.
real-clock   Run the supervisor with synthetic CLIs on the real clock: interrupt
             a 12-second wait with SIGINT, restart, and confirm that the plan
             was kept and no call preceded it. Requires POSIX signals; skipped
             on native Windows (use WSL2).

Without a command both run. Neither reaches a provider CLI or uses plan quota.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "patchrondo"
TESTS = ("test_recovery", "test_adapters")


class Mutation(NamedTuple):
    name: str
    file: str
    old: str
    new: str


# Each entry removes exactly one protection. `old` must occur once in `file`.
MUTATIONS = [
    Mutation("no due check under the lock", "recovery.py",
             "        if now < resume_at:\n            return False, False\n", ""),
    Mutation("retry for a replaced plan is accepted", "recovery.py",
             "    if retry_of is not None and not (scheduled and section.get(\"resume_at\") == retry_of):\n"
             "        return False, False\n", ""),
    Mutation("no retry limit", "recovery.py",
             "    if failures > cfg[\"max_consecutive_retries\"]:\n"
             "        return {\"action\": \"stop\", \"reason\": \"max_consecutive_retries\"}\n", ""),
    Mutation("no wait budget", "recovery.py",
             "    if resume_at > now and (resume_at - first_failure_at).total_seconds() > cfg[\"max_total_wait_seconds\"]:",
             "    if False:"),
    Mutation("expired reset accepted", "recovery.py",
             "    if retry_at is not None and retry_at > failure_at:", "    if retry_at is not None:"),
    Mutation("reset may shorten the backoff", "recovery.py",
             "        if reset >= resume_at:", "        if True:"),
    Mutation("no safety margin", "recovery.py",
             "            reset = retry_at + timedelta(seconds=cfg[\"reset_safety_margin_seconds\"])",
             "            reset = retry_at"),
    Mutation("non-quota failures retried", "recovery.py",
             "    if kind != \"quota\":\n", "    if False:\n"),
    Mutation("counter never resets on progress", "recovery.py",
             "        _audit(state, \"recovery_completed\", retries=section.get(\"consecutive_failures\"),\n"
             "               provider=section.get(\"provider\"))\n        state[\"recovery\"] = None\n",
             "        pass\n"),
    Mutation("counter never accumulates", "recovery.py",
             "failures=section[\"consecutive_failures\"] + 1 if same_point else 1", "failures=1"),
    Mutation("automatic retries reuse --unlock", "recovery.py",
             "force_unlock=force_unlock and retry_of is None", "force_unlock=True"),
    Mutation("manual run keeps the plan", "recovery.py",
             "        if section is not None and section.get(\"status\") in {\"scheduled\", \"retrying\"}:\n"
             "            _audit(state, \"recovery_cancelled\", reason=\"manual_run\")\n"
             "        state[\"recovery\"] = None\n        return True, False\n",
             "        return True, False\n"),
    Mutation("explicit automatic run ignores an earlier quota pause", "recovery.py",
             "    if not quota:\n        return True, False\n", "    return True, False\n"),
    Mutation("unreadable plan runs anyway", "recovery.py",
             "            _stop(state, section, \"invalid_schedule\")  # never guess a retry time\n"
             "            return False, True\n",
             "            section[\"status\"] = \"retrying\"\n            return True, True\n"),
    Mutation("lock held while waiting", "recovery.py",
             "            _record_wait(path, section[\"resume_at\"])\n"
             "            _wait(path, resume_at, (state[\"status\"], section), clock, sleep)\n",
             "            with TaskLock(path):\n"
             "                _wait(path, resume_at, (state[\"status\"], section), clock, sleep)\n"),
    Mutation("interrupt clears the plan", "recovery.py",
             "            return _reread(path, state)\n        if not active(config(home), auto_resume):",
             "            state[\"recovery\"] = None\n            save_json(path / \"state.json\", state)\n"
             "            return state\n        if not active(config(home), auto_resume):"),
    Mutation("recovery active by default", "recovery.py",
             "    return cfg[\"recovery\"][\"enabled\"] if flag is None else bool(flag)", "    return flag is not False"),
    Mutation("booleans accepted as integers", "recovery.py",
             "        if type(merged[field]) is not int or not low <= merged[field] <= high:",
             "        if not isinstance(merged[field], int) or not low <= merged[field] <= high:"),
    Mutation("quota_only may be false", "recovery.py",
             "    if not merged[\"quota_only\"]:", "    if False:"),
    Mutation("plan not saved with the pause", "core.py",
             "            recovery.on_failure(state, cfg, active=auto, now=failed_at)\n", ""),
    Mutation("plan not checked before the run starts", "core.py",
             "        proceed, changed = recovery.admit(state, cfg, active=auto, retry_of=retry_of, now=clock())\n",
             "        proceed, changed = True, False\n"),
    Mutation("stated reset saved truncated", "core.py",
             "error[\"retry_at\"] = recovery.stamp_up(retry_at)", "error[\"retry_at\"] = recovery.stamp(retry_at)"),
    Mutation("failure time saved truncated", "core.py",
             "\"at\": recovery.stamp_up(at)}", "\"at\": recovery.stamp(at)}"),
    Mutation("wait-only reset not saved as an instant", "core.py",
             "        if reset is not None:\n            error[\"retry_at\"] = recovery.stamp_up(reset)\n", ""),
    Mutation("wait-only reset ignored", "recovery.py",
             "            moments.append(failed_at + timedelta(seconds=seconds))", "            pass"),
    Mutation("plan reads only the absolute reset", "recovery.py",
             "retry_at=stated_reset(error), cfg=limits", "retry_at=parse_time(error.get(\"retry_at\")), cfg=limits"),
    Mutation("ambiguous time parsed", "providers.py",
             "    if moments:\n        hint[\"retry_at\"] = max(moments)",
             "    if \"9pm\" in text:\n        moments.append(observed_at + timedelta(hours=1))\n"
             "    if moments:\n        hint[\"retry_at\"] = max(moments)"),
    Mutation("UTC offset not required", "providers.py",
             "[re.compile(_CUE + date + _ZONE, re.I) for date in (",
             "[re.compile(_CUE + date + \"(?:\" + _ZONE + \")?\", re.I) for date in ("),
    Mutation("earliest stated reset wins", "providers.py", "max(moments)", "min(moments)"),
    Mutation("usage-limit wording wins over a login failure", "providers.py",
             "    return next((kind for kind, pattern in _ERROR_KINDS if pattern.search(lowered)), \"agent_error\")",
             "    return next((kind for kind, pattern in reversed(_ERROR_KINDS) if pattern.search(lowered)), "
             "\"agent_error\")"),
    Mutation("standard output ignored beside standard error", "providers.py",
             "streams[0], provider, self.clock(), \"\\n\".join(streams[1:]))",
             "streams[0], provider, self.clock())"),
    Mutation("standard output overrides a specific standard error", "providers.py",
             "    kind = _classify_error(detail)\n    if kind == \"agent_error\" and secondary:\n"
             "        kind = _classify_error(secondary)\n",
             "    kind = _classify_error(f\"{detail}\\n{secondary}\")\n"),
    Mutation("reset on the other stream dropped", "providers.py",
             "quota_hint(f\"{detail}\\n{secondary}\", observed_at, provider)",
             "quota_hint(detail, observed_at, provider)"),
    Mutation("Codex local reset time ignored", "providers.py",
             "    if provider == \"codex\":\n", "    if False:\n"),
    Mutation("zoneless local time accepted from any source", "providers.py",
             "    if provider == \"codex\":\n", "    if True:\n"),
    Mutation("Codex printed minute not rounded up", "providers.py",
             "        return local.astimezone(timezone.utc) + timedelta(minutes=1)",
             "        return local.astimezone(timezone.utc)"),
    Mutation("standard error ignored beside an is_error result", "providers.py",
             "\"claude\", observed_at, stderr.strip())", "\"claude\", observed_at)"),
    Mutation("lock error is a plain RuntimeError", "storage.py",
             "raise LockBusy(\"Task is already running", "raise RuntimeError(\"Task is already running"),
    Mutation("native Windows liveness assumed dead", "storage.py",
             "        # Cannot reliably determine liveness without optional dependencies on Windows.\n        return True",
             "        return False"),
]


def run_mutation(mutation: Mutation | None, tests: tuple[str, ...] = TESTS) -> tuple[bool, str]:
    """Run `tests` against a temporary copy of the sources; return (tests failed, last output line).

    With `mutation` the copy is changed first. None checks the unchanged copy.
    """
    work = Path(tempfile.mkdtemp(prefix="patchrondo-protection-"))
    try:
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(PACKAGE, work / "src" / "patchrondo", ignore=ignore)
        shutil.copytree(ROOT / "tests", work / "tests", ignore=ignore)
        if mutation is not None:
            target = work / "src" / "patchrondo" / mutation.file
            text = target.read_bytes().decode("utf-8")
            if text.count(mutation.old) != 1:
                return False, f"pattern occurs {text.count(mutation.old)} times in {mutation.file}"
            target.write_bytes(text.replace(mutation.old, mutation.new).encode("utf-8"))
        env = {**os.environ, "PYTHONPATH": str(work / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            run = subprocess.run([sys.executable, "-m", "unittest", *tests], cwd=work / "tests", env=env,
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        except subprocess.TimeoutExpired:
            return True, "tests timed out"
        lines = run.stderr.strip().splitlines()
        return run.returncode != 0, lines[-1] if lines else "no output"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def protections(jobs: int, only: str | None = None) -> int:
    failed, summary = run_mutation(None)
    if failed:
        print(f"The unchanged sources do not pass ({summary}); fix that first.")
        return 1
    chosen = [m for m in MUTATIONS if only is None or only in m.name]
    if not chosen:
        print(f"No protection matches {only!r}")
        return 1
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(run_mutation, chosen))
    missed = 0
    for mutation, (detected, summary) in zip(chosen, results):
        print(f"{'detected' if detected else 'NOT DETECTED'}: {mutation.name} ({summary})")
        missed += not detected
    print(f"RESULT: {len(chosen) - missed} of {len(chosen)} removed protections detected, {missed} missed "
          "(scripted providers)")
    return 1 if missed else 0


# Runs the real CLI entry point; provider commands can only reach the synthetic executable.
RUNNER = r'''
import json, os, shutil, sys, time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[3])
from patchrondo.cli import main
from patchrondo.process import execute
root = Path(sys.argv[1])
def synthetic(argv, cwd, **kwargs):
    if argv[0] not in {"claude", "codex"}:
        raise ValueError("Unexpected provider command")
    reviewer = "--json-schema" in argv or "--output-schema" in argv
    with (root / "real-calls.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": time.time(), "role": "reviewer" if reviewer else "developer"}) + "\n")
    return execute([sys.executable, str(root / "synthetic.py"), *argv], cwd, **kwargs)
free = os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep)
                       if d and not any(shutil.which(n, path=d) for n in ("claude", "codex")))
with patch.dict(os.environ, {"PATH": free}), patch("patchrondo.providers.execute", side_effect=synthetic):
    if any(shutil.which(n) for n in ("claude", "codex")):
        raise RuntimeError("A real provider CLI is still on PATH; refusing the synthetic run")
    raise SystemExit(main(["--home", str(root / "state"), "resume", sys.argv[2], "--auto-resume"]))
'''


def real_clock() -> int:
    if os.name != "posix":
        print("real-clock: SKIPPED (requires POSIX signals; run it under WSL2)")
        return 0
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "tools"))
    import reliability_e2e as fixture
    from patchrondo.storage import read_json, save_json

    checks = []

    def check(name, ok):
        checks.append(bool(ok))
        print(f"{'ok      ' if ok else 'FAILED  '}{name}", flush=True)

    root = Path(tempfile.mkdtemp(prefix="patchrondo-reliability-")).resolve()
    task_id, _ = fixture.setup(root, "auto-resume", "claude", "codex")
    cli = (root / "synthetic.py").read_text(encoding="utf-8")
    (root / "synthetic.py").write_text(cli.replace("Retry after 300 seconds", "Retry after 12 seconds"),
                                       encoding="utf-8", newline="\n")
    cfg = read_json(root / "state" / "config.json")
    cfg["recovery"] = {"initial_backoff_seconds": 10, "reset_safety_margin_seconds": 0}
    save_json(root / "state" / "config.json", cfg)
    path = root / "state" / "tasks" / task_id

    def start():
        return subprocess.Popen([sys.executable, "-c", RUNNER, str(root), task_id, str(ROOT / "src")],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                start_new_session=True)

    def calls():
        file = root / "real-calls.jsonl"
        return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()] if file.exists() else []

    started = time.time()
    first = start()
    output = ""
    try:
        state = {}
        deadline = time.time() + 30
        while time.time() < deadline and first.poll() is None:
            try:
                state = read_json(path / "state.json")
            except (OSError, ValueError):
                state = {}
            waiting = any(entry["event"] == "recovery_wait_started" for entry in state.get("history", []))
            if waiting and not (path / ".run.lock").exists():
                break
            time.sleep(0.05)
        plan = state.get("recovery") or {}
        resume_at = datetime.fromisoformat(plan["resume_at"]).timestamp() if plan.get("resume_at") else 0.0
        check("runner is waiting with a saved plan and no lock", first.poll() is None
              and plan.get("status") == "scheduled" and plan.get("schedule_source") == "provider_reset"
              and not (path / ".run.lock").exists())
        time.sleep(1.0)
        first.send_signal(signal.SIGINT)
        output, _ = first.communicate(timeout=30)
        interrupted_at = time.time()
        after = read_json(path / "state.json")
        check("Ctrl+C ended the wait before the planned time with exit status 2",
              first.returncode == 2 and interrupted_at < resume_at)
        check("plan and quota pause kept unchanged", after["status"] == "paused" and after["recovery"] == plan
              and (after.get("last_error") or {}).get("kind") == "quota")
        check("message says the plan stays saved", "stays saved" in output)
        check("no lock and no call beyond developer and reviewer", not (path / ".run.lock").exists()
              and [call["role"] for call in calls()] == ["developer", "reviewer"])
        second = start()
        output, _ = second.communicate(timeout=90)
        done = read_json(path / "state.json")
        log = calls()
        exact = [call["role"] for call in log] == ["developer", "reviewer", "reviewer"]
        check("restarted runner completed the task", second.returncode == 0 and done["status"] == "done")
        check("exactly one more call, not before the planned time", exact and log[2]["at"] >= resume_at)
        check("the retry came 12 s or more after the failed call", exact and log[2]["at"] - log[1]["at"] >= 12)
        events = [entry["event"] for entry in done["history"]]
        check("journal: one plan, two waits, one retry, completed", (
            events.count("recovery_scheduled"), events.count("recovery_wait_started"),
            events.count("recovery_retry_started"), events.count("recovery_completed")) == (1, 2, 1, 1))
        check("development and tests ran once",
              (events.count("development_completed"), events.count("tests_completed")) == (1, 1))
        if exact:
            print(f"real elapsed: {time.time() - started:.1f}s; retry {log[2]['at'] - resume_at:.2f}s after the planned time")
    finally:
        if first.poll() is None:
            first.kill()
            first.communicate()
    passed = bool(checks) and all(checks)
    if passed:
        fixture.cleanup(root)
    else:
        print(f"Evidence retained: {root}\n{output}")
    print(f"RESULT: real-clock {'PASS' if passed else 'FAIL'} ({sum(checks)}/{len(checks)} checks, synthetic providers)")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", choices=("protections", "real-clock"), help="Default: both")
    parser.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1),
                        help="Parallel test runs for protections (1-8)")
    parser.add_argument("--only", help="Run only protections whose name contains this text")
    args = parser.parse_args(argv)
    if not 1 <= args.jobs <= 8:
        parser.error("--jobs must be between 1 and 8")
    code = 0
    if args.command in (None, "protections"):
        code |= protections(args.jobs, args.only)
    if args.command in (None, "real-clock"):
        code |= real_clock()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
