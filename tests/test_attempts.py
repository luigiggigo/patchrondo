"""The attempts of a step (Phase 2.5, Step 04): each call recorded, in a folder of its own, before anything starts.

The provider CLI is emulated in this process (`FakeCLI`), reached only
through `patchrondo.providers.execute`: it does what `process.execute` does
around a real process (the `Launch` learns a PID and what holds it, the
process file is written, the start is reported) and then prints what the
plan of that call says. Crashes are a `Crash`, which no run catches: the
state on disk is then exactly what was saved before it, as after a power
cut once the lock is out of the way.
"""
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import make_repo, outcomes  # noqa: E402

from patchrondo import bundle, cli, core, evidence, execution, process, runfiles  # noqa: E402
from patchrondo.core import create_task, draft_tests, initialize, release_attempt, run_task  # noqa: E402
from patchrondo.execution import AttemptOutcome, AttemptStatus, Availability, Termination, Unit  # noqa: E402
from patchrondo.process import Result  # noqa: E402
from patchrondo.storage import read_json, save_json  # noqa: E402

PID = 4242
KEEP = object()  # `configure`: leave the execution section as it is
TEST = [sys.executable, "-c", "from pathlib import Path; assert Path('feature.txt').read_text() == 'good'"]


class Crash(BaseException):
    """The run stops here and saves nothing more: no handler of the run catches it."""


class FakeCLI:
    """Claude Code and Codex as their adapter sees them, one plan per call; every call is kept in `calls`."""

    def __init__(self):
        self.plans: list[dict] = []
        self.calls: list[dict] = []

    def plan(self, *plans: dict) -> None:
        self.plans.extend(plans)

    def __call__(self, argv, cwd, *, stdin="", timeout=600, pid_file=None, on_stdout=None, on_stderr=None, tree=False,
                 launch=None):
        plan = self.plans.pop(0) if self.plans else {}
        provider = argv[0]
        reviewer = "--json-schema" in argv or "--output-schema" in argv
        self.calls.append({"argv": list(argv), "launch": launch is not None, "timeout": timeout, "pid_file": pid_file,
                           "role": "reviewer" if reviewer else "developer"})
        if plan.get("missing"):
            raise FileNotFoundError(argv[0])
        if plan.get("denied"):  # the system refused to start it (not executable, access denied)
            raise PermissionError(13, "Permission denied", argv[0])
        held = plan.get("tree", process.GROUP)
        if launch is not None:
            launch._began(PID, held)
        if "before_start" in plan:
            plan["before_start"]()
        if plan.get("crash") == "before_identity":
            raise Crash()
        if pid_file is not None:
            save_json(pid_file, {"pid": PID, "command": argv[0]})
        if launch is not None:
            launch._started()
        if "after_start" in plan:
            plan["after_start"]()
        if plan.get("crash") == "running":
            raise Crash()
        if plan.get("interrupt") or plan.get("timeout"):
            refused = bool(plan.get("refused"))
            if launch is not None:
                launch._ended(refused)
            if pid_file is not None:
                pid_file.unlink(missing_ok=True)
            if plan.get("interrupt"):
                raise KeyboardInterrupt
            return Result(124, "", "", timed_out=True, process_tree=held, kill_refused=refused)
        live = "stream-json" in argv or "--json" in argv
        lines, code, stderr = (self.claude if provider == "claude" else self.codex)(argv, cwd, stdin, reviewer, plan)
        stdout = "".join(json.dumps(line) + "\n" for line in lines) if live else (json.dumps(lines[-1]) + "\n"
                                                                                  if lines and provider == "claude" else "")
        if live and on_stdout is not None:
            on_stdout(stdout.encode("utf-8"))
        if pid_file is not None:
            pid_file.unlink(missing_ok=True)
        return Result(code, stdout, stderr, process_tree=held)

    @staticmethod
    def _develop(cwd) -> None:
        (Path(cwd) / "feature.txt").write_text("good", encoding="utf-8")

    def claude(self, argv, cwd, stdin, reviewer, plan):
        if plan.get("error"):
            return [], 1, "boom: the CLI failed\n"
        lines = [{"type": "system", "subtype": "init", "model": "fake", "tools": ["Read"]},
                 {"type": "assistant", "message": {"content": [{"type": "text", "text": plan.get("say", "Working")}]}}]
        if plan.get("quota"):
            return lines + [{"type": "result", "subtype": "success", "is_error": True,
                             "result": "Usage limit reached. Retry after 3600 seconds."}], 1, ""
        if plan.get("max_turns"):
            return lines + [{"type": "result", "subtype": "error_max_turns", "is_error": True,
                             "num_turns": plan.get("turns", 41)}], 1, plan.get("stderr", "")
        if plan.get("no_result"):
            return lines, 0, ""
        final = {"type": "result", "subtype": "success", "is_error": False}
        if plan.get("turns", 3) is not None:
            final["num_turns"] = plan.get("turns", 3)
        if reviewer:
            payload = {"verdict": "APPROVED", "summary": "Fake review.", "issues": [], "criteria": outcomes(stdin)}
            final.update(structured_output=payload, result="") if not plan.get("invalid") else final.update(result="no")
        else:
            self._develop(cwd)
            final["result"] = "## Changes\n\n- Created `feature.txt`"
        return lines + [final], 0, ""

    def codex(self, argv, cwd, stdin, reviewer, plan):
        if plan.get("error"):
            return [], 1, "boom: the CLI failed\n"
        if reviewer:
            text = json.dumps({"verdict": "APPROVED", "summary": "Fake review.", "issues": [], "criteria": outcomes(stdin)})
        else:
            self._develop(cwd)
            text = "## Changes\n\n- Created `feature.txt`"
        usage = {} if plan.get("tokens") == "absent" else {"input_tokens": 10, "output_tokens": 5}
        lines = [{"type": "thread.started", "thread_id": "fake"}, {"type": "turn.started"},
                 {"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": "Working"}},
                 {"type": "turn.completed", "usage": usage}]
        if not plan.get("no_result"):
            Path(argv[argv.index("--output-last-message") + 1]).write_text(text, encoding="utf-8")
        return lines, 0, ""


class AttemptCase(unittest.TestCase):
    EXECUTION = {"enabled": True, "profile": "standard"}

    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        root = Path(folder.name).resolve()
        self.repo, self.home = root / "repo", root / "state"
        make_repo(self.repo)
        initialize(self.home, self.repo)
        self.configure(tests=True, execution=self.EXECUTION)
        self.cli = FakeCLI()
        guard = patch("patchrondo.providers.execute", side_effect=self.cli)
        guard.start()
        self.addCleanup(guard.stop)

    def configure(self, *, tests=None, execution=KEEP, **workflow) -> None:
        cfg = read_json(self.home / "config.json")
        if tests is not None:
            cfg["tests"].update(enabled=tests, trust_acknowledged=tests, commands=[TEST] if tests else [])
        cfg["workflow"].update(max_iterations=2, **workflow)
        cfg["rag"]["enabled"] = False
        if execution is None:
            cfg.pop("execution", None)
        elif execution is not KEEP:
            cfg["execution"] = execution
        save_json(self.home / "config.json", cfg)

    def task(self, developer="claude", reviewer="codex") -> tuple[str, Path]:
        task_id, _ = create_task(self.home, title="Feature", description="Create feature.txt", acceptance=["Tests pass"],
                                 developer=developer, reviewer=reviewer)
        return task_id, self.home / "tasks" / task_id

    def saved(self, path: Path) -> dict:
        return read_json(path / "state.json")

    def record(self, path: Path) -> execution.ExecutionRecord:
        return execution.load(self.saved(path))

    @staticmethod
    def named(state: dict, prefix: str = "attempt_") -> list[dict]:
        return [entry for entry in state["history"] if entry["event"].startswith(prefix)]

    def folder(self, path: Path, phase: str, ordinal: int, iteration: int = 1) -> Path:
        return runfiles.attempt_folder(path, iteration, phase, ordinal)


class LifecycleTests(AttemptCase):
    def test_each_call_is_saved_starting_before_its_process_and_running_once_it_exists(self):
        task_id, path = self.task()
        seen = {}

        def look(name):
            def inspect():
                item = self.record(path).step.last
                seen[name] = (item.attempt_id, item.status, sorted(entry.name for entry in self.folder(path, "develop", 1).iterdir()))
            return inspect

        self.cli.plan({"before_start": look("before"), "after_start": look("after")})
        state = run_task(self.home, task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        # STARTING was on disk, with its folder made, before the process existed; RUNNING once its process file was.
        self.assertEqual(seen["before"][:2], ("CX-0001", AttemptStatus.STARTING))
        self.assertIn("developer.prompt.md", seen["before"][2])
        self.assertNotIn("developer.active-process.json", seen["before"][2])
        self.assertEqual(seen["after"][:2], ("CX-0001", AttemptStatus.RUNNING))
        self.assertIn("developer.active-process.json", seen["after"][2])
        record = execution.load(state)
        self.assertEqual((record.next_attempt, record.step.phase, record.step.last.attempt_id), (3, "review", "CX-0002"))
        started = [entry for entry in self.named(state) if entry["event"] == "attempt_started"]
        self.assertEqual([(entry["attempt"], entry["folder"]) for entry in started],
                         [("CX-0001", "iteration-001/develop/attempt-001"), ("CX-0002", "iteration-001/review/attempt-001")])
        for phase, role in (("develop", "developer"), ("review", "reviewer")):
            files = {entry.name for entry in self.folder(path, phase, 1).iterdir()}
            self.assertTrue({f"{role}.prompt.md", f"{role}.stdout.log", f"{role}.stderr.log", f"{role}.reply.md"} <= files)
            self.assertNotIn(f"{role}.active-process.json", files)
        self.assertFalse(list((path / "runs" / "iteration-001").glob("developer.*")))  # nothing flat
        self.assertTrue(all(call["launch"] for call in self.cli.calls))

    def test_manual_resumes_add_attempts_with_unique_ids_and_folders_in_one_iteration(self):
        self.configure(execution={"enabled": True, "profile": "deep_autonomous"})  # three developer calls per step
        task_id, path = self.task()
        self.cli.plan({"max_turns": True}, {"max_turns": True})
        for _ in range(2):
            paused = run_task(self.home, task_id)
            self.assertEqual((paused["status"], paused["last_error"]["kind"], paused["iteration"]),
                             ("paused", "max_turns", 1))
        done = run_task(self.home, task_id)
        self.assertEqual((done["status"], done["iteration"]), ("done", 1), done.get("last_error"))
        ended = [entry for entry in self.named(done) if entry["event"] == "attempt_ended"]
        self.assertEqual([(entry["attempt"], entry["outcome"]) for entry in ended],
                         [("CX-0001", "TURN_LIMIT_REACHED"), ("CX-0002", "TURN_LIMIT_REACHED"), ("CX-0003", "COMPLETED"),
                          ("CX-0004", "COMPLETED")])
        folders = [entry for entry in sorted((path / "runs" / "iteration-001" / "develop").iterdir())]
        self.assertEqual([entry.name for entry in folders], ["attempt-001", "attempt-002", "attempt-003"])
        # Each attempt kept its own transcript: nothing of an earlier one was overwritten.
        self.assertFalse((folders[0] / "developer.reply.md").exists())
        self.assertIn("error_max_turns", (folders[0] / "developer.stdout.log").read_text(encoding="utf-8"))
        self.assertIn("Created `feature.txt`", (folders[2] / "developer.reply.md").read_text(encoding="utf-8"))
        # One call per run: nothing continued by itself, and one development step for all three attempts.
        self.assertEqual([call["role"] for call in self.cli.calls], ["developer"] * 3 + ["reviewer"])
        self.assertEqual(sum(entry["event"] == "development_started" for entry in done["history"]), 3)
        budget = execution.load(done).budget
        self.assertEqual(budget.line(Unit.PROVIDER_CALLS).used, 4)

    def test_developer_and_reviewer_attempts_are_apart_and_the_reviewer_stays_read_only(self):
        task_id, path = self.task()
        state = run_task(self.home, task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        developer, reviewer = self.cli.calls
        self.assertEqual(developer["pid_file"].parent, self.folder(path, "develop", 1))
        self.assertEqual(reviewer["pid_file"].parent, self.folder(path, "review", 1))
        self.assertIn("read-only", reviewer["argv"][reviewer["argv"].index("--sandbox") + 1])
        # The review record names the log of the attempt that answered.
        review = next(entry for entry in evidence.read(path).records if entry["kind"] == "review")
        self.assertEqual(review["log"], "iteration-001/review/attempt-001/reviewer.reply.md")

    def test_a_claude_reviewer_keeps_its_tools_and_gets_the_profile_turns(self):
        task_id, _ = self.task(developer="codex", reviewer="claude")
        state = run_task(self.home, task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        argv = self.cli.calls[1]["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Glob,Grep")
        self.assertIn("--restricted", argv)
        self.assertEqual(argv[argv.index("--max-turns") + 1], "15")  # the standard profile's reviewer call
        record = execution.load(state)
        self.assertEqual((record.step.last.turns.availability, record.step.last.turns.value), (Availability.OBSERVED, 3))

    def test_no_call_follows_a_turn_limit_by_itself(self):
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 41})
        state = run_task(self.home, task_id, auto_resume=True)
        self.assertEqual((state["status"], state["last_error"]["kind"], state["recovery"]), ("paused", "max_turns", None))
        self.assertEqual(len(self.cli.calls), 1)
        item = execution.load(state).step.last
        self.assertEqual((item.outcome, item.failure_kind, item.termination), (AttemptOutcome.TURN_LIMIT_REACHED, "max_turns",
                                                                               Termination.EXITED))
        self.assertEqual((item.turns.availability, item.turns.value), (Availability.OBSERVED, 41))
        self.assertIn("max_turns", (path / "state.json").read_text(encoding="utf-8"))


class LimitTests(AttemptCase):
    def test_limits_frozen_with_the_step_survive_resume_and_a_changed_configuration(self):
        task_id, _ = self.task()
        self.cli.plan({"max_turns": True, "turns": 10})  # leaves the step's 80 turns room for a second 40-turn call
        run_task(self.home, task_id)
        first = self.cli.calls[0]
        self.assertEqual((first["argv"][first["argv"].index("--max-turns") + 1], first["timeout"]), ("40", 2700))
        # Narrower everything afterwards: the step goes on with what it started with.
        self.configure(execution={"enabled": True, "profile": "fast"}, claude_max_turns=7, agent_timeout_seconds=60)
        state = run_task(self.home, task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        second, review = self.cli.calls[1:]
        self.assertEqual((second["argv"][second["argv"].index("--max-turns") + 1], second["timeout"]), ("40", 2700))
        # The next step is a new one: it starts with the configuration of now.
        self.assertEqual(review["timeout"], 60)
        call = execution.load(state).step.call
        self.assertEqual((call.timeout_seconds, str(call.timeout_source)), (60, "workflow"))

    def test_a_step_started_before_execution_was_enabled_runs_as_it_started(self):
        self.configure(execution=None)
        task_id, path = self.task()
        self.cli.plan({"max_turns": True})
        run_task(self.home, task_id)
        frozen = self.saved(path)["agent_runs"]
        self.configure(execution=self.EXECUTION)
        state = run_task(self.home, task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        first, second, review = self.cli.calls
        for call in (first, second):  # flat, with the arguments of before: no launch handle, no frozen limits
            self.assertEqual((call["pid_file"].parent.name, call["launch"]), ("iteration-001", False))
            self.assertEqual(call["argv"][call["argv"].index("--max-turns") + 1], "12")
        self.assertEqual(state["agent_runs"]["001:developer"], frozen["001:developer"])
        record = execution.load(state)
        self.assertEqual((record.step.phase, record.step.last.attempt_id, record.next_attempt), ("review", "CX-0001", 2))
        self.assertTrue(review["launch"])
        self.assertTrue((path / "runs" / "iteration-001" / "developer.reply.md").exists())


class OutcomeTests(AttemptCase):
    def run_once(self, plan: dict, *, developer="claude", reviewer="codex", **options):
        task_id, path = self.task(developer, reviewer)
        self.cli.plan(plan)
        return task_id, path, run_task(self.home, task_id, **options)

    def test_codex_usage_is_calls_seconds_and_observed_tokens(self):
        _, _, state = self.run_once({}, developer="codex", reviewer="claude")
        record = execution.load(state)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        # The developer's step left the record; the task's budget kept its charges.
        self.assertEqual(record.budget.line(Unit.PROVIDER_CALLS).used, 2)
        self.assertIsNone(record.budget.line(Unit.CLAUDE_TURNS))
        events = [entry for entry in self.named(state) if entry["event"] == "attempt_ended"]
        self.assertEqual((events[0]["tokens"], events[0]["turns"]), (15, None))  # Codex: tokens observed, no turns

    def test_unknown_usage_stays_unknown_and_is_charged_the_most_allowed(self):
        _, _, state = self.run_once({"turns": None}, developer="claude", reviewer="codex")
        events = [entry for entry in self.named(state) if entry["event"] == "attempt_ended"]
        self.assertEqual(events[0]["turns"], None)
        with patch.dict(os.environ, {"PATCHRONDO_LIVE_ACTIVITY": "0"}):
            task_id, path = self.task(developer="codex", reviewer="codex")
            self.cli.plan({}, {})
            again = run_task(self.home, task_id)
        item = execution.load(again).step.last
        self.assertEqual((item.tokens.availability, item.tokens.value), (Availability.UNKNOWN, None))  # never 0
        self.assertIsNone(execution.load(again).budget.line(Unit.TOKENS))  # observed only: no budget keeps them

    def test_a_claude_answer_without_a_turn_count_charges_the_turn_limit(self):
        task_id, path = self.task()
        self.cli.plan({"turns": None})
        self.configure(tests=False)
        state = run_task(self.home, task_id)
        self.assertEqual(state["last_error"]["kind"], "tests_disabled")
        step = execution.load(state).step
        self.assertEqual((step.last.turns.availability, step.budget.line(Unit.CLAUDE_TURNS).used),
                         (Availability.UNKNOWN, 40))

    def test_missing_or_truncated_final_answers_are_invalid_output(self):
        for developer, reviewer in (("claude", "codex"), ("codex", "claude")):
            with self.subTest(developer=developer):
                _, _, state = self.run_once({"no_result": True}, developer=developer, reviewer=reviewer)
                self.assertEqual(state["last_error"]["kind"], "empty_output")
                item = execution.load(state).step.last
                self.assertEqual((item.outcome, item.termination), (AttemptOutcome.INVALID_FINAL_OUTPUT, Termination.EXITED))

    def test_an_unreadable_review_is_an_answer_the_workflow_refuses(self):
        task_id, path = self.task(developer="codex", reviewer="claude")
        self.cli.plan({}, {"invalid": True})
        state = run_task(self.home, task_id)
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "invalid_review"))
        item = execution.load(state).step.last
        # The CLI exited by itself with an answer the workflow cannot read: not COMPLETED (Step 05).
        self.assertEqual((item.phase, item.outcome, item.failure_kind, item.termination),
                         ("review", AttemptOutcome.INVALID_FINAL_OUTPUT, "invalid_review", Termination.EXITED))
        self.assertNotIn("code_approved", [entry["event"] for entry in state["history"]])
        self.assertFalse([entry for entry in evidence.read(path).records if entry["kind"] == "review"])

    def test_provider_errors_and_a_missing_cli(self):
        _, _, state = self.run_once({"error": True})
        item = execution.load(state).step.last
        self.assertEqual((state["last_error"]["kind"], item.outcome, item.termination),
                         ("agent_error", AttemptOutcome.PROVIDER_ERROR, Termination.EXITED))
        _, _, state = self.run_once({"missing": True})
        record = execution.load(state)
        item = record.step.last
        self.assertEqual((state["last_error"]["kind"], item.outcome, item.termination),
                         ("configuration", AttemptOutcome.ENVIRONMENT_BLOCKED, Termination.NOT_STARTED))
        self.assertEqual(record.step.budget.line(Unit.PROVIDER_CALLS).used, 0)  # nothing was started
        self.assertEqual((item.turns.value, item.tokens.value), (0, 0))

    def test_quota_is_left_to_recovery_with_the_attempt_ended_in_the_same_write(self):
        task_id, path, state = self.run_once({"quota": True}, auto_resume=True)
        saved = self.saved(path)
        self.assertEqual((saved["last_error"]["kind"], saved["recovery"]["status"]), ("quota", "scheduled"))
        item = execution.load(saved).step.last
        self.assertEqual((item.outcome, item.failure_kind, item.termination),
                         (AttemptOutcome.QUOTA_EXHAUSTED, "quota", Termination.EXITED))
        done = run_task(self.home, task_id, auto_resume=False)  # a manual run: a new attempt of the same step
        self.assertEqual(done["status"], "done", done.get("last_error"))
        self.assertEqual([entry["attempt"] for entry in self.named(done) if entry["event"] == "attempt_started"],
                         ["CX-0001", "CX-0002", "CX-0003"])

    def test_a_timeout_stopped_with_everything_it_started_does_not_hold_the_next_call(self):
        task_id, path, state = self.run_once({"timeout": True, "tree": process.GROUP})
        item = execution.load(state).step.last
        self.assertEqual((state["last_error"]["kind"], item.outcome, item.termination),
                         ("timeout", AttemptOutcome.TIMEOUT, Termination.STOPPED))
        done = run_task(self.home, task_id)
        self.assertEqual(done["status"], "done", done.get("last_error"))

    def test_an_interrupted_call_is_recorded_and_the_task_resumes(self):
        task_id, path, state = self.run_once({"interrupt": True, "tree": process.GROUP})
        item = execution.load(state).step.last
        self.assertEqual((state["last_error"]["kind"], item.outcome, item.failure_kind, item.termination),
                         ("interrupted", AttemptOutcome.INTERRUPTED, "interrupted", Termination.STOPPED))
        self.assertEqual(run_task(self.home, task_id)["status"], "done")


class UncertainTests(AttemptCase):
    """A call that may still be running holds every other call until the user releases it."""

    def assert_held(self, task_id: str, path: Path, attempt: str) -> dict:
        calls = len(self.cli.calls)
        state = run_task(self.home, task_id)
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "attempt_uncertain"))
        self.assertIn(f"patchrondo release-attempt {task_id} {attempt}", state["last_error"]["message"])
        again = run_task(self.home, task_id)  # idempotent: nothing written, nothing started
        self.assertEqual(again, state)
        self.assertEqual(self.saved(path), state)
        self.assertEqual(len(self.cli.calls), calls)
        return state

    def test_a_timeout_on_native_windows_holds_the_next_call(self):
        for case in ({"timeout": True, "tree": process.CHILD}, {"timeout": True, "tree": process.GROUP, "refused": True},
                     {"interrupt": True, "tree": process.CHILD}):
            with self.subTest(case=case):
                task_id, path = self.task()
                self.cli.plan(case)
                run_task(self.home, task_id)
                self.assertIs(self.record(path).step.last.termination, Termination.UNVERIFIED)
                self.assert_held(task_id, path, "CX-0001")
                released = release_attempt(self.home, task_id, "CX-0001", confirm=True)
                item = execution.load(released).step.last
                self.assertEqual((item.termination, item.outcome), (Termination.RELEASED, case.get("timeout") and
                                                                    AttemptOutcome.TIMEOUT or AttemptOutcome.INTERRUPTED))
                self.assertEqual(released["status"], "paused")  # the user's statement changes no status
                self.assertEqual(run_task(self.home, task_id)["status"], "done")
                self.cli.plans.clear()

    def test_a_request_for_tests_is_refused_while_a_call_may_still_run(self):
        self.configure(tests=False)
        task_id, path = self.task()
        run_task(self.home, task_id)  # paused for the test consent
        state = self.saved(path)
        record = execution.load(state)
        record = execution.ExecutionRecord.from_dict(record.to_dict())
        record = record.begin_step(1, execution.Phase.REVIEW, "codex", {}, execution.CallLimits(
            None, execution.Source.NOT_APPLICABLE, 60, execution.Source.PROFILE), execution.ExecutionProfile.STANDARD)
        record, _ = record.new_attempt(started_at="2026-11-08T10:00:00+00:00")
        state[execution.KEY] = record.to_dict()
        runfiles.allocate(path, 1, "review", 1)
        save_json(path / "state.json", state)
        drafted = draft_tests(self.home, task_id)
        self.assertEqual((drafted["test_draft"]["status"], drafted["test_draft"]["kind"]), ("failed", "attempt_uncertain"))
        self.assertEqual(len(self.cli.calls), 1)


class CrashTests(AttemptCase):
    """A run lost at each boundary of a call: what the next run can prove, and what it refuses to assume."""

    def crash(self, task_id: str, *patches) -> None:
        with self.assertRaises(Crash):
            for target in patches:
                target.start()
            try:
                run_task(self.home, task_id)
            finally:
                for target in patches:
                    target.stop()

    def test_before_the_attempt_is_saved_nothing_is_left(self):
        task_id, path = self.task()
        self.crash(task_id, patch.object(execution.ExecutionRecord, "new_attempt", side_effect=Crash()))
        self.assertEqual(self.record(path).step.attempts, ())
        self.assertFalse((path / "runs" / "iteration-001" / "develop").exists())
        self.assertEqual(run_task(self.home, task_id)["status"], "done")
        self.assertEqual(self.record(path).budget.line(Unit.PROVIDER_CALLS).used, 2)

    def test_saved_but_without_its_folder_it_provably_never_started(self):
        task_id, path = self.task()
        self.crash(task_id, patch("patchrondo.core.runfiles.allocate", side_effect=Crash()))
        item = self.record(path).step.last
        self.assertEqual(item.status, AttemptStatus.STARTING)
        state = run_task(self.home, task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        first = next(entry for entry in self.named(state) if entry["event"] == "attempt_ended")
        self.assertEqual((first["attempt"], first["termination"], first["found"]), ("CX-0001", "not_started", True))
        self.assertEqual(execution.load(state).budget.line(Unit.PROVIDER_CALLS).used, 2)  # the one never started: 0
        self.assertEqual(sorted(entry.name for entry in (path / "runs" / "iteration-001" / "develop").iterdir()),
                         ["attempt-002"])

    def test_with_its_folder_and_no_process_on_record_it_is_uncertain(self):
        task_id, path = self.task()
        real = runfiles.allocate

        def allocated(*args, **kwargs):
            real(*args, **kwargs)
            raise Crash()

        self.crash(task_id, patch("patchrondo.core.runfiles.allocate", side_effect=allocated))
        UncertainTests.assert_held(self, task_id, path, "CX-0001")
        released = release_attempt(self.home, task_id, "CX-0001", confirm=True)
        item = execution.load(released).step.last
        self.assertEqual((item.outcome, item.termination, item.elapsed_seconds), (AttemptOutcome.UNKNOWN,
                                                                                  Termination.RELEASED, 2700))
        state = run_task(self.home, task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        self.assertEqual(sorted(entry.name for entry in (path / "runs" / "iteration-001" / "develop").iterdir()),
                         ["attempt-001", "attempt-002"])

    def test_started_before_its_identity_was_recorded_it_is_uncertain(self):
        task_id, path = self.task()
        self.cli.plan({"crash": "before_identity"})
        self.crash(task_id)
        self.assertEqual(self.record(path).step.last.status, AttemptStatus.STARTING)
        UncertainTests.assert_held(self, task_id, path, "CX-0001")

    def test_lost_while_its_cli_ran_it_is_uncertain_and_its_process_file_is_kept(self):
        task_id, path = self.task()
        self.cli.plan({"crash": "running"})
        self.crash(task_id)
        self.assertEqual(self.record(path).step.last.status, AttemptStatus.RUNNING)
        held = UncertainTests.assert_held(self, task_id, path, "CX-0001")
        marker = self.folder(path, "develop", 1) / "developer.active-process.json"
        self.assertTrue(marker.exists())
        with patch("patchrondo.storage._alive", return_value=True):  # native Windows, or a live PID
            with self.assertRaisesRegex(RuntimeError, "may still be running"):
                release_attempt(self.home, task_id, "CX-0001", confirm=True)
        self.assertTrue(marker.exists())  # never removed by PatchRondo
        self.assertEqual(self.saved(path), held)
        marker.write_text("not json", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Unreadable process file"):
            release_attempt(self.home, task_id, "CX-0001", confirm=True)
        save_json(marker, {"pid": PID, "command": "claude"})
        with patch("patchrondo.storage._alive", return_value=False):  # POSIX: no such process
            release_attempt(self.home, task_id, "CX-0001", confirm=True)
        self.assertEqual(run_task(self.home, task_id)["status"], "done")

    def test_lost_after_its_cli_exited_it_is_still_uncertain(self):
        task_id, path = self.task()
        real, calls = core._fingerprint_or_none, []

        def fingerprinted(workspace):
            calls.append(workspace)
            if len(calls) == 2:  # the files after the call: the CLI has exited, its end is not saved yet
                raise Crash()
            return real(workspace)

        self.crash(task_id, patch("patchrondo.core._fingerprint_or_none", side_effect=fingerprinted))
        self.assertEqual(self.record(path).step.last.status, AttemptStatus.RUNNING)
        self.assertTrue((self.folder(path, "develop", 1) / "developer.reply.md").exists())  # its answer is not used
        UncertainTests.assert_held(self, task_id, path, "CX-0001")

    def test_release_is_refused_for_anything_but_the_uncertain_attempt(self):
        task_id, path = self.task()
        self.cli.plan({"crash": "running"})
        self.crash(task_id)
        self.assertEqual(run_task(self.home, task_id)["last_error"]["kind"], "attempt_uncertain")  # paused, naming it
        for name in ("CX-0002", "CX-9999", "AC-01"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "nothing to release"):
                release_attempt(self.home, task_id, name, confirm=True)
        (self.folder(path, "develop", 1) / "developer.active-process.json").unlink()
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--home", str(self.home), "release-attempt", task_id, "CX-0001", "--confirm-stopped"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("CX-0001 released", out.getvalue())
        with self.assertRaisesRegex(ValueError, "nothing to release"):
            release_attempt(self.home, task_id, "CX-0001", confirm=True)  # once only


class FolderTests(AttemptCase):
    def test_an_existing_attempt_folder_is_never_reused_and_stops_one_run_only(self):
        self.configure(execution={"enabled": True, "profile": "deep_autonomous"})  # three developer calls per step
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 10})
        run_task(self.home, task_id)  # the task has its record and attempt-001
        stranger = runfiles.allocate(path, 1, "develop", 2)
        (stranger / "note.txt").write_text("not ours", encoding="utf-8")
        state = run_task(self.home, task_id)
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "attempt_folder_exists"))
        self.assertEqual(sorted(entry.name for entry in stranger.iterdir()), ["note.txt"])
        self.assertEqual(len(self.cli.calls), 1)
        item = execution.load(state).step.last
        self.assertEqual((item.outcome, item.termination), (AttemptOutcome.ENVIRONMENT_BLOCKED, Termination.NOT_STARTED))
        done = run_task(self.home, task_id)  # the next attempt gets a folder of its own
        self.assertEqual(done["status"], "done", done.get("last_error"))
        self.assertTrue((self.folder(path, "develop", 3) / "developer.reply.md").exists())

    def test_a_phase_folder_that_is_not_a_folder_stops_the_run_before_anything_starts(self):
        task_id, path = self.task()
        iteration = path / "runs" / "iteration-001"
        iteration.mkdir(parents=True)
        (iteration / "develop").write_text("a file", encoding="utf-8")
        (iteration / "attempt-1").mkdir()  # a name no reader takes
        state = run_task(self.home, task_id)
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "system_error"))
        self.assertEqual(self.cli.calls, [])
        self.assertIs(execution.load(state).step.last.termination, Termination.NOT_STARTED)
        self.assertEqual((iteration / "develop").read_text(encoding="utf-8"), "a file")


class LegacyTests(AttemptCase):
    def scenario(self, section) -> tuple[dict, list[dict]]:
        self.configure(execution=section)
        self.cli.calls.clear()
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 10})
        run_task(self.home, task_id)
        return run_task(self.home, task_id), list(self.cli.calls)

    def test_without_execution_or_with_it_off_runs_and_files_are_as_before(self):
        absent, calls = self.scenario(None)
        off, calls_off = self.scenario({"enabled": False, "profile": "deep_autonomous"})
        for state, made in ((absent, calls), (off, calls_off)):
            self.assertEqual(state["status"], "done", state.get("last_error"))
            self.assertNotIn(execution.KEY, state)
            self.assertFalse(self.named(state))
            self.assertTrue(all(not call["launch"] and call["pid_file"].parent.name == "iteration-001" for call in made))
            self.assertEqual(made[0]["argv"][made[0]["argv"].index("--max-turns") + 1], "12")
            self.assertEqual(made[0]["timeout"], 900)
        names = lambda state: [entry["event"] for entry in state["history"]]  # noqa: E731
        self.assertEqual(names(absent), names(off))
        self.assertEqual([call["argv"][0] for call in calls], [call["argv"][0] for call in calls_off])

    def test_the_loop_decides_as_without_attempts(self):
        absent, _ = self.scenario(None)
        on, _ = self.scenario(self.EXECUTION)
        names = lambda state: [entry["event"] for entry in state["history"] if not entry["event"].startswith("attempt_")]  # noqa: E731
        self.assertEqual(names(on), names(absent))
        self.assertEqual((on["status"], on["phase"], on["iteration"]), (absent["status"], absent["phase"], absent["iteration"]))
        self.assertIn("task_completed", names(on))  # through the gate, as always
        kinds = lambda state: [entry["kind"] for entry in evidence.read(self.home / "tasks" / state["id"]).records]  # noqa: E731
        self.assertEqual(kinds(on), kinds(absent))

    def test_tests_disabled_still_pause_before_any_reviewer_call(self):
        self.configure(tests=False)
        task_id, path = self.task()
        state = run_task(self.home, task_id)
        self.assertEqual((state["last_error"]["kind"], state["phase"]), ("tests_disabled", "test"))
        self.assertEqual(execution.load(state).step.phase, "develop")
        self.assertEqual([call["role"] for call in self.cli.calls], ["developer"])

    def test_an_unreadable_record_is_kept_and_nothing_is_called(self):
        task_id, path = self.task()
        state = self.saved(path)
        state[execution.KEY] = {"version": 2, "task_id": task_id}
        save_json(path / "state.json", state)
        paused = run_task(self.home, task_id)
        self.assertEqual((paused["status"], paused["last_error"]["kind"]), ("paused", "execution_unreadable"))
        self.assertEqual(paused[execution.KEY], {"version": 2, "task_id": task_id})
        self.assertEqual(run_task(self.home, task_id), paused)
        self.assertEqual(self.cli.calls, [])


class RecordsTests(AttemptCase):
    def test_logs_bundles_and_reports_find_attempts_and_keep_processes_private(self):
        task_id, path = self.task()
        state = run_task(self.home, task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        made = bundle.build(path, "detailed")
        self.assertIn("logs/iteration-001/review/attempt-001/reviewer.reply.md",
                      [entry["path"] for entry in made.manifest["files"]])
        self.assertEqual(bundle.verify(made.data)["status"], "INTACT")
        for name in ("state.json", "report.md"):
            self.assertNotIn(str(PID), (path / name).read_text(encoding="utf-8"))
        journal = [json.loads(line) for line in (path / "activity.jsonl").read_text(encoding="utf-8").splitlines()[1:]]
        starts = [entry.get("attempt_id") for entry in journal if entry.get("status") == "started"
                  and entry.get("role") in {"developer", "reviewer"}]
        self.assertEqual(starts, ["CX-0001", "CX-0002"])


class ProcessTests(unittest.TestCase):
    """`process.Launch` with real processes: the caller learns the start, and a start it cannot record is stopped."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_the_caller_learns_the_process_after_its_file_is_written(self):
        for observed in (False, True):
            with self.subTest(observed=observed):
                marker, seen = self.root / f"child-{observed}.active-process.json", []
                launch = process.Launch(on_start=lambda: seen.append((launch.pid, marker.exists())))
                result = process.execute([sys.executable, "-c", "print('hi')"], self.root, timeout=60, pid_file=marker,
                                         launch=launch, **({"on_stdout": lambda chunk: None} if observed else {}))
                self.assertEqual((result.returncode, seen), (0, [(launch.pid, True)]))
                self.assertIsNotNone(launch.process_tree)
                self.assertFalse(launch.stopped)
                self.assertFalse(marker.exists())

    def test_a_start_the_caller_cannot_record_stops_the_process(self):
        script = "import time; time.sleep(4); print('finished')"
        for observed in (False, True):
            with self.subTest(observed=observed):
                launch = process.Launch(on_start=lambda: (_ for _ in ()).throw(OSError("disk full")))
                with self.assertRaisesRegex(OSError, "disk full"):
                    process.execute([sys.executable, "-c", script], self.root, timeout=60, launch=launch,
                                    **({"on_stdout": lambda chunk: None} if observed else {}))
                self.assertTrue(launch.stopped)

    def test_a_timeout_says_the_process_was_stopped(self):
        launch = process.Launch()
        result = process.execute([sys.executable, "-c", "import time; time.sleep(30)"], self.root, timeout=1, launch=launch)
        self.assertTrue(result.timed_out)
        self.assertEqual((launch.stopped, launch.process_tree, launch.kill_refused),
                         (True, result.process_tree, result.kill_refused))


if __name__ == "__main__":
    unittest.main()
