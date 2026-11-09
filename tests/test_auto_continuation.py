"""Automatic continuation of the developer (Phase 2.5, Step 07).

After a recorded developer call ends with Claude's own turn limit, a task
bound to a policy with `adaptive` and `continuation.checkpoint_resume`
continues the step at once: one new call, in a new attempt folder, with a
checkpoint brief and limits no larger than what the budgets have left. The
provider CLI is the fake of `test_attempts`, reached only through
`patchrondo.providers.execute`; no real CLI is started. The elapsed time of a
call is set through `core.time` where a time budget is tested.
"""
import copy
import hashlib
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_attempts import Crash  # noqa: E402
from test_budgets import BudgetCase, narrowed  # noqa: E402

from patchrondo import core, execution, process  # noqa: E402
from patchrondo.core import create_task, release_attempt  # noqa: E402
from patchrondo.execution import (AttemptOutcome, AttemptStatus, CallLimits, ContinuationMode, Declined,  # noqa: E402
                                  ExecutionProfile, ExecutionRecord, Phase, Scope, Source, Termination, Unit, Usage)
from patchrondo.providers import AgentFailure  # noqa: E402
from patchrondo.storage import save_json  # noqa: E402

ADAPTIVE = {"enabled": True, "profile": "standard", "adaptive": True}
BRIEF = "## Continuation of this step"


def adaptive(**limits) -> dict:
    return {**narrowed(**limits), "adaptive": True}


class Elapsed:
    """`core.time` with a monotonic clock the test moves: what a call measures as elapsed is what the test says."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float):
        def move():
            self.now += seconds
        return move

    def __getattr__(self, name):
        return getattr(time, name)


class ContinuationCase(BudgetCase):
    EXECUTION = ADAPTIVE

    def prompt(self, path: Path, ordinal: int) -> str:
        return (self.folder(path, "develop", ordinal) / "developer.prompt.md").read_text(encoding="utf-8")

    @staticmethod
    def turns(call: dict) -> str:
        return call["argv"][call["argv"].index("--max-turns") + 1]

    def declined(self, state: dict) -> list[dict]:
        return [entry for entry in state["history"] if entry["event"] == "continuation_declined"]

    def assert_not_continued(self, state: dict, kind: str, calls: int = 1) -> None:
        """Paused as before: the failure's kind, no continuation event, no other call."""
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", kind))
        self.assertFalse([name for name in self.events(state) if name.startswith("continuation_")])
        self.assertEqual(len(self.cli.calls), calls)

    def assert_declined(self, state: dict, reason: str, calls: int = 1, started: int = 0) -> dict:
        """Paused with `max_turns` as before, the reason in the history and the message, and no other call."""
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "max_turns"))
        self.assertIn("Automatic continuation was not started", state["last_error"]["message"])
        self.assertIn(core.DECLINED[Declined(reason)], state["last_error"]["message"])
        declined = self.declined(state)
        self.assertEqual([entry["reason"] for entry in declined], [reason])
        self.assertEqual(self.events(state).count("continuation_started"), started)
        self.assertEqual(len(self.cli.calls), calls)
        self.assertNotIn("development_completed", self.events(state))
        return declined[0]


class ContinuationTests(ContinuationCase):
    def test_a_turn_limit_is_continued_once_in_the_same_step_with_a_brief_and_a_smaller_call(self):
        self.configure(tests=False)  # stops at the test consent: the developer's step stays the current one
        task_id, path = self.task()
        kept = {}

        def keep():
            kept.update({entry.name: hashlib.sha256(entry.read_bytes()).hexdigest()
                         for entry in self.folder(path, "develop", 1).iterdir()})
            kept["checks"] = self.spy.checks

        self.cli.plan({"max_turns": True, "turns": 41}, {"before_start": keep})
        state = self.go(task_id)
        self.assertEqual((state["status"], state["last_error"]["kind"], state["phase"], state["iteration"]),
                         ("paused", "tests_disabled", "test", 1))
        # The run's check, and again before the continuation's provider call.
        self.assertEqual(kept.pop("checks"), 2)
        # Two calls of one step: the same iteration and phase, the second smaller (80 - 41 = 39 turns left).
        self.assertEqual([call["role"] for call in self.cli.calls], ["developer", "developer"])
        self.assertEqual([self.turns(call) for call in self.cli.calls], ["40", "39"])
        self.assertEqual([call["timeout"] for call in self.cli.calls], [2700, 2700])
        self.assertEqual([call["pid_file"].parent for call in self.cli.calls],
                         [self.folder(path, "develop", 1), self.folder(path, "develop", 2)])
        record = execution.load(state)
        step = record.step
        self.assertEqual((step.iteration, step.phase, step.call.turn_limit), (1, Phase.DEVELOP, 40))
        self.assertEqual([(item.attempt_id, item.ordinal, item.outcome, item.termination, item.continuation,
                           item.turn_limit, item.turns.value) for item in step.attempts],
                         [("CX-0001", 1, AttemptOutcome.TURN_LIMIT_REACHED, Termination.EXITED, ContinuationMode.NONE,
                           40, 41),
                          ("CX-0002", 2, AttemptOutcome.COMPLETED, Termination.EXITED,
                           ContinuationMode.CHECKPOINT_RESUME, 39, 3)])
        # Charged once per call, as observed: the 41 turns of the first stay 41.
        self.assertEqual((step.budget.line(Unit.CLAUDE_TURNS).used, step.budget.line(Unit.PROVIDER_CALLS).used,
                          record.budget.line(Unit.PROVIDER_CALLS).used), (44, 2, 2))
        events = self.events(state)
        self.assertEqual(events.count("development_started"), 1)
        self.assertEqual(events.count("development_completed"), 1)
        self.assertNotIn("task_paused", events[:events.index("development_completed")])
        started = [entry for entry in state["history"] if entry["event"] == "continuation_started"]
        self.assertEqual(len(started), 1)
        self.assertEqual({key: started[0][key] for key in ("attempt", "after", "mode", "continuation", "of", "turn_limit",
                                                            "timeout_seconds", "step_turn_limit", "step_timeout_seconds")},
                         {"attempt": "CX-0002", "after": "CX-0001", "mode": "checkpoint_resume", "continuation": 1, "of": 1,
                          "turn_limit": 39, "timeout_seconds": 2700, "step_turn_limit": 40, "step_timeout_seconds": 2700})
        self.assertIn({"scope": "phase", "unit": "claude_turns", "limit": 80, "used": 41, "unknown": 0, "remaining": 39},
                      started[0]["remaining"])
        attempts = [(entry["event"], entry["attempt"], entry.get("continuation")) for entry in state["history"]
                    if entry["event"] in ("attempt_started", "attempt_ended")]
        self.assertEqual(attempts, [("attempt_started", "CX-0001", None), ("attempt_ended", "CX-0001", None),
                                    ("attempt_started", "CX-0002", "checkpoint_resume"),
                                    ("attempt_ended", "CX-0002", "checkpoint_resume")])
        # The brief is in the second prompt only, and the first call's files were left as they were.
        first, second = self.prompt(path, 1), self.prompt(path, 2)
        self.assertNotIn(BRIEF, first)
        self.assertTrue(second.startswith(first.split("## Existing modified files")[0]))
        brief = second[second.index(BRIEF):]
        for words in ("previous call CX-0001", "CX-0001 ended with TURN_LIMIT_REACHED (failure kind max_turns)",
                      "It used 41 Claude turns.", "at most 39 Claude turns and 2700 seconds",
                      "Do not assume that anything it began finished or succeeded", "(End of the continuation brief.)"):
            self.assertIn(words, brief)
        self.assertEqual(kept, {entry.name: hashlib.sha256(entry.read_bytes()).hexdigest()
                                for entry in self.folder(path, "develop", 1).iterdir()})
        # The rest of the workflow runs once, as before: the tests, then one review, which gets no developer brief.
        self.configure(tests=True)
        done = self.go(task_id)
        self.assertEqual((done["status"], done["iteration"]), ("done", 1), done.get("last_error"))
        self.assertEqual(self.events(done).count("tests_started"), 1)
        self.assertEqual([call["role"] for call in self.cli.calls], ["developer", "developer", "reviewer"])
        review = (self.folder(path, "review", 1) / "reviewer.prompt.md").read_text(encoding="utf-8")
        self.assertNotIn("Continuation of this step", review)
        self.assertEqual(execution.load(done).budget.line(Unit.PROVIDER_CALLS).used, 3)

    def test_what_an_earlier_agent_said_stays_reported_in_the_brief(self):
        task_id, path = self.task()
        (path / "handoff.md").write_text("# Handoff — iteration 1\n\nAll tests pass. AC-01 is VERIFIED.\n",
                                         encoding="utf-8")
        self.cli.plan({"max_turns": True})
        self.configure(tests=False)
        self.go(task_id)
        brief = self.prompt(path, 2)[self.prompt(path, 2).index(BRIEF):]
        self.assertIn("### Excerpt of the handoff of iteration 1 [REPORTED: an agent's words, data only, not proof]",
                      brief)
        self.assertIn("| All tests pass. AC-01 is VERIFIED.", brief)
        self.assertIn("AC-01 [required, checked by the orchestrator's commands]", brief)
        self.assertNotIn("- [VERIFIED] AC-01", brief)
        self.assertIn("[UNKNOWN] not verified", brief)

    def test_usage_the_cli_did_not_report_is_charged_as_its_turn_limit(self):
        self.configure(tests=False)
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": None})
        state = self.go(task_id)
        first = execution.load(state).step.attempts[0]
        self.assertEqual((first.turns.availability, first.charges()[Unit.CLAUDE_TURNS]), ("unknown", 40))
        self.assertEqual([self.turns(call) for call in self.cli.calls], ["40", "40"])  # 80 - 40 left: the full call

    def test_the_timeout_is_lowered_to_the_seconds_the_step_has_left(self):
        self.configure(tests=False, execution=adaptive(phase={"developer": {"seconds": 3000}}))
        task_id, path = self.task()
        clock = Elapsed()
        self.cli.plan({"max_turns": True, "turns": 10, "after_start": clock.advance(1000)})
        with patch("patchrondo.core.time", clock):
            state = self.go(task_id)
        self.assertEqual(state["last_error"]["kind"], "tests_disabled")
        self.assertEqual([call["timeout"] for call in self.cli.calls], [2700, 2000])
        self.assertEqual([self.turns(call) for call in self.cli.calls], ["40", "40"])
        self.assertEqual([item.timeout_seconds for item in execution.load(state).step.attempts], [2700, 2000])

    def test_reviewer_and_test_consent_are_unchanged_by_a_continuation(self):
        task_id, path = self.task(developer="codex", reviewer="claude")
        self.cli.plan({}, {"max_turns": True})
        state = self.go(task_id)
        self.assertEqual((state["phase"], state["last_error"]["kind"]), ("review", "max_turns"))
        self.assert_not_continued(state, "max_turns", calls=2)
        self.assertNotIn("Automatic continuation", state["last_error"]["message"])

    def test_the_gate_still_decides_after_a_continuation(self):
        task_id, _ = create_task(self.home, title="Feature", description="Create feature.txt",
                                 acceptance=["Tests pass", "[manual] The colours look right"],
                                 developer="claude", reviewer="codex")
        self.cli.plan({"max_turns": True})
        state = self.go(task_id)
        self.assertEqual((state["status"], state["phase"], state["last_error"]["kind"]),
                         ("paused", "validate", "awaiting_validation"))
        self.assertIn("continuation_started", self.events(state))
        self.assertNotIn("task_completed", self.events(state))


class DeclinedTests(ContinuationCase):
    def test_without_the_option_a_turn_limit_pauses_as_before(self):
        for name, section in (("no section", None), ("adaptive off", {"enabled": True, "profile": "standard"}),
                              ("checkpoint resume off", {**ADAPTIVE, "continuation": {"checkpoint_resume": False}})):
            with self.subTest(name):
                self.configure(execution=section)
                self.cli.calls.clear()
                task_id, path = self.task()
                self.cli.plan({"max_turns": True})
                state = self.go(task_id)
                self.assert_not_continued(state, "max_turns")
                self.assertNotIn("Automatic continuation", state["last_error"]["message"])
                if section is None:
                    self.assertNotIn(execution.KEY, state)
                    self.assertEqual(self.turns(self.cli.calls[0]), "12")
                    self.assertFalse([name for name in self.events(state) if name.startswith("attempt_")])

    def test_quota_and_timeout_are_never_continued(self):
        for plan, kind in (({"quota": True}, "quota"), ({"timeout": True}, "timeout"), ({"error": True}, "agent_error")):
            with self.subTest(kind):
                self.cli.calls.clear()
                task_id, _ = self.task()
                self.cli.plan(plan)
                self.assert_not_continued(self.go(task_id, auto_resume=True), kind)

    def test_no_turn_budget_left_pauses_without_a_call(self):
        self.configure(execution=adaptive(phase={"developer": {"claude_turns": 40}}))
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 41})
        state = self.go(task_id)
        declined = self.assert_declined(state, "budget")
        self.assertEqual((declined["after"], declined["budget"]),
                         ("CX-0001", {"scope": "phase", "unit": "claude_turns", "limit": 40, "used": 41, "unknown": 0,
                                      "requested": 1}))
        record = execution.load(state)
        self.assertEqual((record.next_attempt, len(record.step.attempts)), (2, 1))
        self.assertFalse(self.folder(path, "develop", 2).exists())

    def test_too_few_seconds_left_pauses_without_a_call(self):
        self.configure(execution=adaptive(phase={"developer": {"seconds": 3000}}))
        task_id, _ = self.task()
        clock = Elapsed()
        self.cli.plan({"max_turns": True, "turns": 10, "after_start": clock.advance(2950)})
        with patch("patchrondo.core.time", clock):
            state = self.go(task_id)
        declined = self.assert_declined(state, "budget")
        self.assertEqual(declined["budget"], {"scope": "phase", "unit": "seconds", "limit": 3000, "used": 2950,
                                              "unknown": 0, "requested": 60})

    def test_a_step_with_no_call_left_pauses_without_a_call(self):
        self.configure(execution=adaptive(phase={"developer": {"provider_calls": 1}}))
        task_id, _ = self.task()
        self.cli.plan({"max_turns": True, "turns": 10})
        declined = self.assert_declined(self.go(task_id), "budget")
        self.assertEqual((declined["budget"]["scope"], declined["budget"]["unit"]), ("phase", "provider_calls"))

    def test_one_automatic_continuation_per_step_then_a_resume_is_an_ordinary_call(self):
        self.configure(execution={**ADAPTIVE, "profile": "deep_autonomous"})  # three calls per developer step
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 41}, {"max_turns": True, "turns": 41})
        state = self.go(task_id)
        declined = self.assert_declined(state, "limit_reached", calls=2, started=1)
        self.assertEqual((declined["after"], declined["made"]), ("CX-0002", 1))
        resumed = self.go(task_id)  # the user's Resume: one more call, not automatic, with the step's full limits
        self.assertEqual(resumed["status"], "done", resumed.get("last_error"))
        self.assertEqual([self.turns(call) for call in self.cli.calls if call["role"] == "developer"], ["50", "50", "50"])
        self.assertEqual(self.events(resumed).count("continuation_started"), 1)
        started = [entry for entry in resumed["history"] if entry["event"] == "attempt_started"]
        self.assertEqual([entry.get("continuation") for entry in started], [None, "checkpoint_resume", None, None])
        # Charged once per call, across runs: three developer calls and one review.
        self.assertEqual(execution.load(resumed).budget.line(Unit.PROVIDER_CALLS).used, len(self.cli.calls))

    def test_a_process_file_or_a_call_no_group_held_prevents_a_continuation(self):
        for plan, reason, detail in (
                ({"tree": process.CHILD}, "process_tree_unverified", {"process_tree": "child_only"}),
                ({"after_start": None}, "brief_unavailable", {"brief": "PROCESS_MAY_RUN"})):
            with self.subTest(reason):
                self.cli.calls.clear()
                task_id, path = self.task()
                if "after_start" in plan:
                    stray = path / "runs" / "iteration-001" / "check.active-process.json"
                    plan = {"after_start": lambda stray=stray: save_json(stray, {"pid": 1, "command": "x"})}
                self.cli.plan({"max_turns": True, **plan})
                declined = self.assert_declined(self.go(task_id), reason)
                self.assertEqual({key: declined[key] for key in detail}, detail)

    def test_a_released_call_of_the_step_is_never_followed_automatically(self):
        task_id, path = self.task()
        self.cli.plan({"crash": "running"})
        with self.assertRaises(Crash):
            self.go(task_id)
        self.assertEqual(self.go(task_id)["last_error"]["kind"], "attempt_uncertain")
        (self.folder(path, "develop", 1) / "developer.active-process.json").unlink()  # the person checked
        release_attempt(self.home, task_id, "CX-0001", confirm=True)
        self.cli.plan({"max_turns": True})
        state = self.go(task_id)
        self.assertEqual(self.assert_declined(state, "released_attempt", calls=2)["after"], "CX-0002")

    def test_a_task_keeps_the_policy_it_was_bound_to(self):
        deep, plain = {**ADAPTIVE, "profile": "deep_autonomous"}, {"enabled": True, "profile": "deep_autonomous"}
        for bound, later, continued in ((deep, plain, True), (plain, deep, False), (deep, None, True)):
            with self.subTest(bound=bound, later=later):
                self.configure(tests=False, execution=bound)
                self.cli.calls.clear()
                task_id, path = self.task()
                self.cli.plan({"quota": True})
                self.assertEqual(self.go(task_id)["last_error"]["kind"], "quota")  # the step and the policy are bound
                self.configure(execution=later)
                self.cli.plan({"max_turns": True})
                state = self.go(task_id)
                self.assertEqual("continuation_started" in self.events(state), continued)
                self.assertEqual(len(self.cli.calls), 3 if continued else 2)


class SafetyTests(ContinuationCase):
    def test_an_interrupted_continuation_is_not_continued_and_stays_held(self):
        for tree, termination in ((process.GROUP, Termination.STOPPED), (process.CHILD, Termination.UNVERIFIED)):
            with self.subTest(tree):
                self.cli.calls.clear()
                self.cli.plans.clear()
                task_id, path = self.task()
                self.cli.plan({"max_turns": True}, {"interrupt": True, "tree": tree})
                state = self.go(task_id)
                self.assertEqual(state["last_error"]["kind"], "interrupted")
                self.assertNotIn("continuation_declined", self.events(state))  # an interruption is never continued
                last = execution.load(state).step.last
                self.assertEqual((last.attempt_id, last.outcome, last.termination, last.continuation),
                                 ("CX-0002", AttemptOutcome.INTERRUPTED, termination, ContinuationMode.CHECKPOINT_RESUME))
                self.assertEqual(len(self.cli.calls), 2)
                if termination is Termination.UNVERIFIED:  # what it started may still run: nothing more starts
                    self.assertEqual((self.go(task_id)["last_error"]["kind"], len(self.cli.calls)),
                                     ("attempt_uncertain", 2))

    def test_a_continuation_that_fails_keeps_its_outcome(self):
        task_id, _ = self.task()
        self.cli.plan({"max_turns": True}, {"error": True})
        state = self.go(task_id)
        self.assertEqual(state["last_error"]["kind"], "agent_error")
        last = execution.load(state).step.last
        self.assertEqual((last.outcome, last.continuation), (AttemptOutcome.PROVIDER_ERROR, ContinuationMode.CHECKPOINT_RESUME))
        self.assertEqual(len(self.cli.calls), 2)

    def test_a_crash_never_starts_a_call_twice(self):
        task_id, path = self.task()
        self.cli.plan({"max_turns": True}, {"crash": "running"})
        with self.assertRaises(Crash):
            self.go(task_id)
        state = self.go(task_id)
        self.assertEqual((state["last_error"]["kind"], len(self.cli.calls)), ("attempt_uncertain", 2))
        self.assertEqual(execution.load(state).step.last.attempt_id, "CX-0002")

    def test_a_crash_while_the_continuation_is_saved_never_starts_a_call(self):
        task_id, path = self.task()
        saving = core._save

        def save(target, state):
            if any(entry["event"] == "continuation_started" for entry in state["history"]):
                raise Crash()
            saving(target, state)

        self.cli.plan({"max_turns": True, "turns": 41})
        with patch("patchrondo.core._save", side_effect=save), self.assertRaises(Crash):
            self.go(task_id)
        saved = self.saved(path)
        self.assertNotIn("continuation_started", self.events(saved))
        first = execution.load(saved).step.last
        state = self.go(task_id)
        self.assertEqual(len(self.cli.calls), 1)  # neither the continuation nor a duplicate of the first call started
        if first.ended:  # saved by the environment check before the continuation: a full call no longer fits
            self.assertEqual(state["last_error"]["kind"], "budget_exhausted")
        else:
            self.assertEqual(state["last_error"]["kind"], "attempt_uncertain")


class ControllerTests(ContinuationCase):
    """`core._next_call` on a task paused after a turn limit nothing continued: what it refuses, it never starts."""

    def setUp(self):
        super().setUp()
        self.task_id, self.path = self.task()
        self.cli.plan({"max_turns": True, "tree": process.CHILD})
        self.paused = self.go(self.task_id)
        self.workspace = Path(self.paused["worktree"])
        self.failure = AgentFailure("Turn limit", "max_turns", provider="claude")
        self.held = {"attempt": "CX-0001", "tree": process.GROUP, "refused": False}

    def ask(self, state=None, held=None):
        state = copy.deepcopy(self.paused) if state is None else state
        return state, core._next_call(self.path, state, self.workspace, held or self.held, self.failure)

    def refused(self, change, reason: str, brief: str | None = None) -> None:
        state = copy.deepcopy(self.paused)
        change(state)
        before = len(state["history"])
        with self.assertRaises(AgentFailure) as raised:
            self.ask(state)
        self.assertEqual((raised.exception.kind, raised.exception.__cause__), ("max_turns", self.failure))
        added = state["history"][before:]
        self.assertEqual([(entry["event"], entry["reason"]) for entry in added], [("continuation_declined", reason)])
        if brief is not None:
            self.assertEqual(added[0]["brief"], brief)

    def test_a_held_call_with_its_records_is_continued(self):
        state, (decided, text) = self.ask()
        self.assertTrue(decided.allowed)
        self.assertEqual((decided.trigger, decided.call.turn_limit), ("CX-0001", 39))  # the first reported 41 of 80
        self.assertTrue(text.startswith(BRIEF))
        self.assertEqual(state["history"], self.paused["history"])  # deciding writes no event

    def test_settings_iteration_and_phase_must_be_the_steps(self):
        def digest(state):
            state["agent_runs"]["001:developer"]["digest"] = "0" * 16
        self.refused(digest, "brief_unavailable", "SETTINGS_MISMATCH")
        self.refused(lambda state: state.update(iteration=2), "brief_unavailable", "ITERATION_MISMATCH")
        self.refused(lambda state: state.update(phase="test"), "brief_unavailable", "PHASE_MISMATCH")
        self.refused(lambda state: state.update(acceptance={"version": 99}), "brief_unavailable", "ACCEPTANCE_UNREADABLE")

    def test_a_failure_of_another_call_or_an_unreadable_record_is_never_continued(self):
        state = copy.deepcopy(self.paused)
        with self.assertRaises(AgentFailure) as raised:
            self.ask(state, held={**self.held, "attempt": "CX-0009"})
        self.assertIs(raised.exception, self.failure)  # raised again unchanged: no event, nothing added
        self.assertEqual(state["history"], self.paused["history"])
        state["execution"]["next_attempt"] = "two"
        with self.assertRaises(AgentFailure) as raised:
            self.ask(state)
        self.assertEqual(raised.exception.kind, "execution_unreadable")
        self.assertEqual(len(self.cli.calls), 1)

    def test_a_signal_refused_or_an_unknown_holder_is_never_continued(self):
        for held in ({**self.held, "refused": True}, {"attempt": "CX-0001"}):
            state = copy.deepcopy(self.paused)
            with self.subTest(held=held), self.assertRaises(AgentFailure):
                self.ask(state, held=held)
            self.assertEqual(state["history"][-1]["reason"], "process_tree_unverified")


TASK = "T-" + "a" * 12
STEP = {Unit.CLAUDE_TURNS: 80, Unit.PROVIDER_CALLS: 2, Unit.SECONDS: 5400}
CALL = CallLimits(40, Source.PROFILE, 2700, Source.PROFILE)


def stepped(policy=ADAPTIVE, task=None, step=None, provider="claude", phase=Phase.DEVELOP) -> ExecutionRecord:
    record = ExecutionRecord.open(TASK, task or {Unit.PROVIDER_CALLS: 60, Unit.SECONDS: 162000}, policy=policy)
    call = CALL if provider == "claude" else CallLimits(None, Source.NOT_APPLICABLE, 2700, Source.PROFILE)
    limits = dict(step or STEP)
    if provider != "claude":
        limits.pop(Unit.CLAUDE_TURNS, None)
    return record.begin_step(1, phase, provider, limits, call, ExecutionProfile.STANDARD)


def called(record: ExecutionRecord, outcome=AttemptOutcome.TURN_LIMIT_REACHED, termination=Termination.EXITED,
           turns=41, elapsed=100, **options) -> ExecutionRecord:
    record, item = record.new_attempt(started_at="2026-11-09T10:00:00+00:00", **options)
    kind = {AttemptOutcome.TURN_LIMIT_REACHED: "max_turns", AttemptOutcome.TIMEOUT: "timeout"}.get(outcome)
    used = Usage.reported(Unit.CLAUDE_TURNS, turns) if item.provider == "claude" else item.turns
    ended = item.end(outcome, ended_at="2026-11-09T10:05:00+00:00", elapsed_seconds=elapsed, termination=termination,
                     failure_kind=kind, turns=used)
    return record.update(ended)


class NextCallTests(unittest.TestCase):
    """The decision alone (`execution.next_call`), on records made by hand: pure, deterministic."""

    def test_the_call_is_sized_to_every_line_and_never_above_the_step(self):
        decided = execution.next_call(called(stepped()))
        self.assertTrue(decided.allowed)
        self.assertEqual((decided.trigger, decided.call.turn_limit, decided.call.timeout_seconds), ("CX-0001", 39, 2700))
        decided = execution.next_call(called(stepped(), turns=5))
        self.assertEqual(decided.call.turn_limit, 40)  # 75 left: the step's own limit, never more
        decided = execution.next_call(called(stepped(task={Unit.PROVIDER_CALLS: 60, Unit.SECONDS: 2750}), turns=5))
        self.assertEqual(decided.call.timeout_seconds, 2650)  # the task's seconds left

    def test_the_task_budget_refuses_it(self):
        for task, unit in (({Unit.PROVIDER_CALLS: 1, Unit.SECONDS: 162000}, Unit.PROVIDER_CALLS),
                           ({Unit.PROVIDER_CALLS: 60, Unit.SECONDS: 2750}, Unit.SECONDS)):
            with self.subTest(unit):
                decided = execution.next_call(called(stepped(task=task), elapsed=2700))  # 50 seconds left, 60 needed
                self.assertEqual((decided.declined, decided.refusal.scope, decided.refusal.unit),
                                 (Declined.BUDGET, Scope.TASK, unit))

    def test_each_condition_is_its_own_reason_in_order(self):
        plain = {"enabled": True, "profile": "standard"}
        cases = (
            (called(stepped(), outcome=AttemptOutcome.TIMEOUT, termination=Termination.STOPPED), Declined.NOT_A_TURN_LIMIT),
            (called(stepped(policy=None)), Declined.POLICY_UNBOUND),
            (called(stepped(policy=plain)), Declined.ADAPTIVE_OFF),
            (called(stepped(policy={**ADAPTIVE, "continuation": {"checkpoint_resume": False}})),
             Declined.CHECKPOINT_RESUME_OFF),
            # A reviewer's turn limit is not an answer that could not be used (Step 08): never followed, nothing said.
            (called(stepped(phase=Phase.REVIEW)), Declined.NOT_AN_UNUSABLE_ANSWER),
            (called(stepped(), draft="test-draft-01"), Declined.TEST_REQUEST),
            (called(stepped(), termination=Termination.UNVERIFIED), Declined.NOT_SETTLED),
            (called(called(stepped(step={**STEP, Unit.PROVIDER_CALLS: 3}), turns=10, continuation=ContinuationMode.CHECKPOINT_RESUME,
                           call=CALL), turns=10), Declined.LIMIT_REACHED),
            (called(stepped(step={**STEP, Unit.PROVIDER_CALLS: 1})), Declined.BUDGET),
        )
        for record, reason in cases:
            with self.subTest(reason):
                decided = execution.next_call(record)
                self.assertEqual((decided.declined, decided.allowed), (reason, False))
                self.assertEqual(decided.considered, reason not in execution.NOT_OPTED_IN)
        self.assertEqual(execution.next_call(stepped()).declined, Declined.NOT_A_TURN_LIMIT)  # no call yet

    def test_a_released_call_of_the_step_refuses_every_later_one(self):
        record = stepped(step={**STEP, Unit.PROVIDER_CALLS: 3})
        record, item = record.new_attempt(started_at="2026-11-09T10:00:00+00:00")
        record = record.release(item.attempt_id, ended_at="2026-11-09T11:00:00+00:00")
        self.assertEqual(execution.next_call(called(record, turns=10)).declined, Declined.RELEASED_ATTEMPT)

    def test_records_hold_a_smaller_call_only_for_a_continuation(self):
        record = called(stepped())
        small = CallLimits(39, Source.PROFILE, 2700, Source.PROFILE)
        with self.assertRaises(execution.InvalidRecord):
            record.new_attempt(started_at="2026-11-09T10:06:00+00:00", call=small)  # not a continuation
        with self.assertRaises(execution.InvalidRecord):
            record.new_attempt(started_at="2026-11-09T10:06:00+00:00", continuation=ContinuationMode.CHECKPOINT_RESUME,
                               call=CallLimits(41, Source.PROFILE, 2700, Source.PROFILE))  # above the step
        record, item = record.new_attempt(started_at="2026-11-09T10:06:00+00:00",
                                          continuation=ContinuationMode.CHECKPOINT_RESUME, call=small)
        self.assertEqual((item.turn_limit, item.status), (39, AttemptStatus.STARTING))
        self.assertEqual(execution.ExecutionRecord.from_dict(record.to_dict()), record)
        raw = record.to_dict()
        raw["step"]["attempts"][1]["continuation"] = "none"  # smaller limits without being a continuation
        with self.assertRaises(execution.InvalidRecord):
            execution.ExecutionRecord.from_dict(raw)
        raw = record.to_dict()
        raw["step"]["attempts"][1]["turn_limit"] = 41  # above the step's 40, the budget charged to match
        line = next(line for line in raw["step"]["budget"]["lines"] if line["unit"] == "claude_turns")
        line["used"] += 2
        with self.assertRaises(execution.InvalidRecord) as raised:
            execution.ExecutionRecord.from_dict(raw)
        self.assertIn("more than the limits of its step", str(raised.exception))
        with self.assertRaises(execution.InvalidRecord):
            stepped().new_attempt(started_at="2026-11-09T10:00:00+00:00", draft="test-draft-01",
                                  continuation=ContinuationMode.CHECKPOINT_RESUME)


if __name__ == "__main__":
    unittest.main()
