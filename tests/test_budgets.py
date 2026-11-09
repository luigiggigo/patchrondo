"""Budgets, outcomes and releases of recorded agent calls (Phase 2.5, Step 05).

Every call of a recorded step is admitted against its step's budget and the
task's, as they were frozen, before anything is started; quota recovery
plans and starts no retry that a budget could not take; an uncertain call is
released only on the user's explicit acknowledgement. The provider CLI is the
fake of `test_attempts` (reached only through `patchrondo.providers.execute`),
the clock of recovery is injected, and no real CLI is started.
"""
import dataclasses
from datetime import datetime, timedelta, timezone
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_attempts import AttemptCase, Crash  # noqa: E402

from patchrondo import agents, cli, core, environment, execution, recovery, runfiles  # noqa: E402
from patchrondo.core import create_task, draft_tests, refresh_agent_settings, release_attempt, run_task  # noqa: E402
from patchrondo.execution import AttemptOutcome, Termination, Unit  # noqa: E402
from patchrondo.storage import read_json, save_json  # noqa: E402

STANDARD = {"enabled": True, "profile": "standard"}


def narrowed(**limits) -> dict:
    """The standard profile with some limits lowered, as `execution.limits` writes them."""
    return {"enabled": True, "profile": "standard", "limits": limits}


class Clock:
    """Recovery's clock: it moves only when somebody sleeps."""

    def __init__(self):
        self.now = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Spy(environment.Manager):
    """The environment check, counted: a budget refusal must come before it."""

    def __init__(self):
        super().__init__()
        self.checks = 0

    def check(self, home, state, *, live, **options):
        self.checks += 1
        return environment.Manager.check(self, home, state, live=False, **options)


class BudgetCase(AttemptCase):
    EXECUTION = STANDARD

    def setUp(self):
        super().setUp()
        self.spy = Spy()

    def go(self, task_id: str, **options) -> dict:
        return run_task(self.home, task_id, environment=self.spy, **options)

    def events(self, state: dict) -> list[str]:
        return [entry["event"] for entry in state["history"]]

    def charge_seconds(self, path: Path, seconds: int) -> None:
        """Rewrite the elapsed seconds of the current step's last attempt, keeping every budget equal to its charges."""
        state = self.saved(path)
        record = execution.load(state)
        old = record.step.last
        new = dataclasses.replace(old, elapsed_seconds=seconds)
        step = dataclasses.replace(record.step, budget=record.step.budget.charged(new.charges(), withdrawn=old.charges()),
                                   attempts=record.step.attempts[:-1] + (new,))
        record = dataclasses.replace(record, budget=record.budget.charged(new.charges(), withdrawn=old.charges()),
                                     step=step)
        state[execution.KEY] = record.to_dict()
        save_json(path / "state.json", state)

    def assert_exhausted(self, state: dict, path: Path, budget: dict, calls: int) -> None:
        """Paused with the typed reason, nothing more started, nothing completed, and nothing charged by the refusal."""
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "budget_exhausted"))
        self.assertEqual(state["last_error"]["budget"], budget)
        paused = [entry for entry in state["history"] if entry["event"] == "task_paused"][-1]
        self.assertEqual((paused["reason"], paused["budget"]), ("budget_exhausted", budget))
        self.assertEqual(len(self.cli.calls), calls)
        self.assertNotIn("task_completed", self.events(state))
        record = execution.load(state)
        charged = sum(1 for entry in state["history"]
                      if entry["event"] == "attempt_ended" and entry["termination"] != "not_started")
        self.assertEqual(record.budget.line(Unit.PROVIDER_CALLS).used, charged)
        message = state["last_error"]["message"]
        self.assertIn("No agent was called and nothing was started", message)
        self.assertIn({"provider_calls": "provider call", "claude_turns": "Claude turn", "seconds": "second"}[budget["unit"]],
                      message)

    def assert_idempotent(self, task_id: str, path: Path, state: dict, **options) -> None:
        calls, checks = len(self.cli.calls), self.spy.checks
        again = self.go(task_id, **options)
        self.assertEqual(again, state)
        self.assertEqual(self.saved(path), state)
        self.assertEqual((len(self.cli.calls), self.spy.checks), (calls, checks))


class AdmissionTests(BudgetCase):
    def test_the_phase_call_budget_refuses_the_third_call_before_anything_starts(self):
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 10}, {"max_turns": True, "turns": 10})
        for _ in range(2):
            self.assertEqual(self.go(task_id)["last_error"]["kind"], "max_turns")
        checks = self.spy.checks
        state = self.go(task_id)
        self.assert_exhausted(state, path, {"scope": "phase", "unit": "provider_calls", "limit": 2, "used": 2,
                                            "unknown": 0, "requested": 1}, calls=2)
        self.assertEqual(self.spy.checks, checks)  # refused before the environment check could start a CLI
        self.assertEqual((state["phase"], state["iteration"]), ("develop", 1))
        record = execution.load(state)
        self.assertEqual([item.attempt_id for item in record.step.attempts], ["CX-0001", "CX-0002"])
        self.assertEqual(record.next_attempt, 3)  # no ID was given to a refused call
        self.assertFalse(self.folder(path, "develop", 3).exists())
        self.assert_idempotent(task_id, path, state)
        self.assert_idempotent(task_id, path, state, auto_resume=True)

    def test_the_phase_turn_budget_counts_unknown_turns_as_the_most_allowed(self):
        for turns, used in ((41, 41), (None, 40)):  # reported, or not reported and charged the call's turn limit
            with self.subTest(turns=turns):
                self.configure(execution=narrowed(phase={"developer": {"claude_turns": 60}}))
                self.cli.calls.clear()
                task_id, path = self.task()
                self.cli.plan({"max_turns": True, "turns": turns})
                self.go(task_id)
                item = self.record(path).step.last
                self.assertEqual((item.outcome, item.turns.value), (AttemptOutcome.TURN_LIMIT_REACHED, turns))
                state = self.go(task_id)
                self.assert_exhausted(state, path, {"scope": "phase", "unit": "claude_turns", "limit": 60, "used": used,
                                                    "unknown": 0, "requested": 40}, calls=1)

    def test_the_phase_time_budget_refuses_a_call_whose_timeout_does_not_fit(self):
        self.configure(execution=narrowed(phase={"developer": {"seconds": 3000}}))
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 10})
        self.go(task_id)
        self.charge_seconds(path, 400)  # 400 + the next call's 2700-second timeout is above 3000
        state = self.go(task_id)
        self.assert_exhausted(state, path, {"scope": "phase", "unit": "seconds", "limit": 3000, "used": 400,
                                            "unknown": 0, "requested": 2700}, calls=1)
        self.assertIn("its timeout", state["last_error"]["message"])

    def test_the_task_call_budget_refuses_a_new_step_before_it_begins(self):
        self.configure(execution=narrowed(task={"provider_calls": 2}))
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 10})
        self.go(task_id)
        checks = self.spy.checks
        state = self.go(task_id)  # the second developer call answers; the review would be a third call of the task
        self.assert_exhausted(state, path, {"scope": "task", "unit": "provider_calls", "limit": 2, "used": 2,
                                            "unknown": 0, "requested": 1}, calls=2)
        self.assertEqual(state["phase"], "review")
        self.assertNotIn("review_started", self.events(state))
        self.assertNotIn("001:reviewer", state["agent_runs"])  # the review step never began
        self.assertEqual(execution.load(state).step.phase, "develop")
        self.assertEqual(self.spy.checks, checks + 1)  # the run's first check only: not the one before the review
        self.assert_idempotent(task_id, path, state)

    def test_the_task_time_budget_refuses_the_call_of_a_step_that_began(self):
        self.configure(tests=False, execution=narrowed(task={"seconds": 6000}))
        task_id, path = self.task()
        self.assertEqual(self.go(task_id)["last_error"]["kind"], "tests_disabled")
        self.charge_seconds(path, 3500)
        self.configure(tests=True)
        state = self.go(task_id)
        self.assert_exhausted(state, path, {"scope": "task", "unit": "seconds", "limit": 6000, "used": 3500,
                                            "unknown": 0, "requested": 2700}, calls=1)
        record = execution.load(state)
        self.assertEqual((record.step.phase, record.step.attempts), ("review", ()))  # began, no call admitted
        self.assertFalse((path / "runs" / "iteration-001" / "review").exists())
        self.assertIn("review_started", self.events(state))
        self.assert_idempotent(task_id, path, state)

    def test_frozen_budgets_survive_configuration_changes_and_disabling(self):
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 10}, {"max_turns": True, "turns": 10})
        self.go(task_id)
        # Narrower, then off: the recorded step keeps its budget, its limits and its lifecycle.
        self.configure(execution={"enabled": False, "profile": "fast"}, claude_max_turns=5, agent_timeout_seconds=60)
        self.assertEqual(self.go(task_id)["last_error"]["kind"], "max_turns")
        second = self.cli.calls[1]
        self.assertTrue(second["launch"])
        self.assertEqual((second["argv"][second["argv"].index("--max-turns") + 1], second["timeout"]), ("40", 2700))
        self.assertEqual(second["pid_file"].parent, self.folder(path, "develop", 2))
        # Wider: a profile with three calls per step does not raise the budget of a step that began with two.
        self.configure(execution={"enabled": True, "profile": "deep_autonomous"})
        state = self.go(task_id)
        self.assert_exhausted(state, path, {"scope": "phase", "unit": "provider_calls", "limit": 2, "used": 2,
                                            "unknown": 0, "requested": 1}, calls=2)
        self.configure(execution=None)
        self.assert_idempotent(task_id, path, state)

    def test_a_refreshed_agent_setting_never_lifts_the_limits_of_a_call(self):
        task_id, _ = create_task(self.home, title="Feature", description="Create feature.txt", acceptance=["Tests pass"],
                                 developer="claude", reviewer="codex",
                                 agent_settings={"developer": {"claude": {"max_turns": 30}}})
        path = self.home / "tasks" / task_id
        self.cli.plan({"max_turns": True, "turns": 5})
        self.go(task_id)
        call = self.record(path).step.call
        self.assertEqual((call.turn_limit, str(call.turn_source)), (30, "agent_settings"))
        state = self.saved(path)
        state["agent_settings"] = {"developer": {"claude": {"max_turns": 100, "timeout_seconds": 9000}}}
        self.assertEqual(refresh_agent_settings(state), ["developer"])
        save_json(path / "state.json", state)
        done = self.go(task_id)
        self.assertEqual(done["status"], "done", done.get("last_error"))
        self.assertEqual(done["agent_runs"]["001:developer"]["values"]["max_turns"], 100)  # frozen again, as asked
        second = self.cli.calls[1]
        self.assertEqual((second["argv"][second["argv"].index("--max-turns") + 1], second["timeout"]), ("30", 2700))
        self.assertEqual(second["argv"].count("--max-turns"), 1)

    def test_an_adapter_that_cannot_take_the_limits_starts_no_recorded_call(self):
        class Plain:  # not `configurable`: it would never be given the step's turn limit and timeout
            calls = 0

            def invoke(self, *args, **kwargs):
                Plain.calls += 1
                raise AssertionError("a recorded call reached an adapter that cannot take its limits")

        task_id, path = self.task()
        state = self.go(task_id, adapter=Plain())
        self.assertEqual((state["status"], state["last_error"]["kind"], Plain.calls), ("paused", "configuration", 0))
        self.assertEqual(execution.load(state).step.attempts, ())

    def test_a_task_without_a_record_has_no_budget(self):
        self.configure(execution=None)
        task_id, path = self.task()
        self.cli.plan({"max_turns": True}, {"max_turns": True})
        for _ in range(2):
            self.assertEqual(self.go(task_id)["last_error"]["kind"], "max_turns")
        state = self.go(task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        self.assertEqual([call["role"] for call in self.cli.calls], ["developer"] * 3 + ["reviewer"])
        self.assertNotIn(execution.KEY, state)
        self.assertIsNone(core._refusal({**state, "phase": "develop"}, core.task_config(self.home, task_id)))


class RequestForTestsTests(BudgetCase):
    """Step 05.1: a request for tests of a recorded task is an attempt of the developer's step, charged once."""

    def setUp(self):
        super().setUp()
        self.configure(tests=False, execution=narrowed(task={"seconds": 6000}))
        self.task_id, self.path = self.task()
        self.assertEqual(self.go(self.task_id)["last_error"]["kind"], "tests_disabled")

    def draft(self) -> dict:
        return draft_tests(self.home, self.task_id, environment=self.spy)

    def test_a_request_is_an_attempt_of_the_developer_step_charged_once(self):
        state = self.draft()
        self.assertEqual(state["test_draft"]["status"], "completed", state["test_draft"])
        self.assertEqual(state["test_draft"]["transcripts"], "iteration-001/test-draft-01")
        call = self.cli.calls[-1]
        self.assertEqual((call["role"], call["argv"][call["argv"].index("--max-turns") + 1], call["timeout"]),
                         ("developer", "40", 2700))
        self.assertEqual(call["pid_file"].parent, self.path / "runs" / "iteration-001" / "test-draft-01")
        record = execution.load(state)
        item = record.step.last
        self.assertEqual((item.attempt_id, item.ordinal, item.draft, item.outcome, item.phase),
                         ("CX-0002", 2, "test-draft-01", AttemptOutcome.COMPLETED, "develop"))
        self.assertEqual(item.to_dict()["version"], 2)
        self.assertEqual(record.budget.line(Unit.PROVIDER_CALLS).used, 2)
        self.assertEqual(record.step.budget.line(Unit.PROVIDER_CALLS).used, 2)
        started = [entry for entry in state["history"] if entry["event"] == "attempt_started"][-1]
        self.assertEqual((started["purpose"], started["folder"]), ("test_draft", "iteration-001/test-draft-01"))
        # Still waiting for the consent: no test ran, no review was asked for, no iteration began.
        self.assertEqual((state["status"], state["phase"], state["iteration"], state["last_error"]["kind"]),
                         ("paused", "test", 1, "tests_disabled"))
        self.assertNotIn("review_started", self.events(state))
        self.assertFalse((self.path / "runs" / "iteration-001" / "develop" / "attempt-002").exists())
        # With the consent the loop goes on as before; the request was not a review, a test result or an iteration.
        self.configure(tests=True)
        done = self.go(self.task_id)
        self.assertEqual((done["status"], done["iteration"]), ("done", 1), done.get("last_error"))
        self.assertEqual(execution.load(done).budget.line(Unit.PROVIDER_CALLS).used, 3)
        self.assertEqual([call["role"] for call in self.cli.calls], ["developer", "developer", "reviewer"])

    def test_a_request_beyond_the_step_budget_starts_no_cli(self):
        self.assertEqual(self.draft()["test_draft"]["status"], "completed")
        budget = self.record(self.path).budget
        state = self.draft()  # the standard developer step allows two calls: the development and one request
        self.assertEqual((state["test_draft"]["status"], state["test_draft"]["kind"]), ("failed", "budget_exhausted"))
        self.assertEqual(len(self.cli.calls), 2)
        self.assertEqual(execution.load(state).budget, budget)
        self.assertFalse((self.path / "runs" / "iteration-001" / "test-draft-02").exists())
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "tests_disabled"))

    def test_the_task_budget_refuses_a_request(self):
        self.charge_seconds(self.path, 4000)  # 4000 + 2700 is above the task's 6000 seconds
        state = self.draft()
        self.assertEqual((state["test_draft"]["status"], state["test_draft"]["kind"]), ("failed", "budget_exhausted"))
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "tests_disabled"))
        self.assertEqual(len(self.cli.calls), 1)

    def test_a_failed_or_timed_out_request_keeps_its_charge(self):
        for plan, kind, outcome, termination in (({"error": True}, "agent_error", AttemptOutcome.PROVIDER_ERROR,
                                                  Termination.EXITED),
                                                 ({"timeout": True}, "timeout", AttemptOutcome.TIMEOUT,
                                                  Termination.STOPPED)):
            with self.subTest(kind=kind):
                self.cli.plans.clear()
                self.cli.plan({}, plan)
                self.task_id, self.path = self.task()
                self.go(self.task_id)
                state = self.draft()
                self.assertEqual((state["test_draft"]["status"], state["test_draft"]["kind"]), ("failed", kind))
                item = execution.load(state).step.last
                self.assertEqual((item.draft, item.outcome, item.termination), ("test-draft-01", outcome, termination))
                self.assertEqual(execution.load(state).budget.line(Unit.PROVIDER_CALLS).used, 2)
                self.assertEqual(state["test_draft"]["transcripts"], "iteration-001/test-draft-01")
                self.assertEqual(self.saved(self.path), state)  # its end was saved with the failure

    def test_a_request_lost_in_a_crash_is_held_charged_and_never_charged_twice(self):
        self.cli.plan({"crash": "running"})
        with self.assertRaises(Crash):
            self.draft()
        lost = self.saved(self.path)
        item = execution.load(lost).step.last
        self.assertEqual((item.draft, item.status), ("test-draft-01", execution.AttemptStatus.RUNNING))
        charged = execution.load(lost).budget
        state = self.draft()  # held: nothing starts beside a call that may still run
        self.assertEqual((state["test_draft"]["status"], state["test_draft"]["kind"]), ("failed", "attempt_uncertain"))
        self.assertEqual(len(self.cli.calls), 2)
        self.assertEqual(execution.load(state).budget, charged)
        marker = self.path / "runs" / "iteration-001" / "test-draft-01" / "developer.active-process.json"
        self.assertTrue(marker.exists())
        marker.unlink()  # checked by the operator
        released = release_attempt(self.home, self.task_id, item.attempt_id, confirm=True)
        last = execution.load(released).step.last
        self.assertEqual((last.outcome, last.termination), (AttemptOutcome.UNKNOWN, Termination.RELEASED))
        self.assertEqual(execution.load(released).budget, charged)  # the lost call keeps its most-allowed charge
        state = self.draft()  # the step's two calls are used: the development and the lost request
        self.assertEqual(state["test_draft"]["kind"], "budget_exhausted")
        self.assertEqual(len(self.cli.calls), 2)

    def test_a_request_saved_but_never_started_costs_nothing(self):
        with patch("patchrondo.core._make_draft", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.draft()
        self.assertEqual(self.record(self.path).step.last.status, execution.AttemptStatus.STARTING)
        state = self.draft()  # the next request closes it as never started: its folder was never made
        self.assertEqual(state["test_draft"]["status"], "completed", state["test_draft"])
        first, second = execution.load(state).step.attempts[1:]
        self.assertEqual((first.termination, second.outcome), (Termination.NOT_STARTED, AttemptOutcome.COMPLETED))
        self.assertEqual(execution.load(state).budget.line(Unit.PROVIDER_CALLS).used, 2)

    def test_a_task_that_lost_its_record_gets_no_request(self):
        state = self.saved(self.path)
        del state[execution.KEY]
        save_json(self.path / "state.json", state)
        drafted = self.draft()
        self.assertEqual((drafted["test_draft"]["status"], drafted["test_draft"]["kind"]),
                         ("failed", "execution_record_missing"))
        self.assertEqual(len(self.cli.calls), 1)
        self.assertNotIn(execution.KEY, drafted)

    def test_a_task_never_recorded_asks_as_before(self):
        self.configure(execution=None)
        self.task_id, self.path = self.task()
        self.go(self.task_id)
        state = self.draft()
        self.assertEqual(state["test_draft"]["status"], "completed", state["test_draft"])
        self.assertNotIn(execution.KEY, state)
        call = self.cli.calls[-1]
        self.assertEqual((call["launch"], call["timeout"]), (False, 900))
        self.assertEqual(call["pid_file"].parent.name, "test-draft-01")


class BindingTests(BudgetCase):
    """Step 05.1: a recorded task stays governed by its policy and its budget, whatever the project says later."""

    def test_turning_execution_off_does_not_release_a_task_from_its_policy(self):
        for section in ({"enabled": False, "profile": "standard"}, None):
            with self.subTest(section=section):
                self.configure(tests=False, execution=STANDARD)
                self.cli.calls.clear()
                task_id, path = self.task()
                self.assertEqual(self.go(task_id)["last_error"]["kind"], "tests_disabled")
                self.configure(tests=True, execution=section)
                state = self.go(task_id)  # a new phase, the review, after the project turned execution off
                self.assertEqual(state["status"], "done", state.get("last_error"))
                record = execution.load(state)
                self.assertEqual(record.policy, STANDARD)
                self.assertEqual((record.step.phase, record.step.last.attempt_id), ("review", "CX-0002"))
                self.assertEqual(record.budget.line(Unit.PROVIDER_CALLS).used, 2)
                review = self.cli.calls[-1]
                self.assertTrue(review["launch"])
                self.assertEqual(review["pid_file"].parent, self.folder(path, "review", 1))
                self.assertEqual(review["timeout"], 2700)  # the standard profile's call, not the workflow's 900

    def test_the_task_budget_still_refuses_after_the_project_turned_execution_off(self):
        self.configure(tests=False, execution=narrowed(task={"provider_calls": 2}))
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 10})
        self.go(task_id)
        self.assertEqual(self.go(task_id)["last_error"]["kind"], "tests_disabled")
        self.configure(tests=True, execution={"enabled": False, "profile": "deep_autonomous"})
        state = self.go(task_id)
        self.assert_exhausted(state, path, {"scope": "task", "unit": "provider_calls", "limit": 2, "used": 2,
                                            "unknown": 0, "requested": 1}, calls=2)
        self.assertEqual(execution.load(state).budget.line(Unit.PROVIDER_CALLS).limit, 2)  # never raised
        self.configure(execution=None)
        self.assert_idempotent(task_id, path, state)

    def test_a_wider_project_profile_never_widens_a_bound_task(self):
        self.configure(tests=False)
        task_id, path = self.task(developer="codex", reviewer="claude")
        self.go(task_id)
        self.configure(tests=True, execution={"enabled": True, "profile": "deep_autonomous"})
        state = self.go(task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        review = self.cli.calls[-1]["argv"]
        self.assertEqual(review[review.index("--max-turns") + 1], "15")  # standard's reviewer call, not deep's 25
        self.assertEqual(execution.load(state).budget.line(Unit.PROVIDER_CALLS).limit, 60)
        self.assertEqual(review[review.index("--tools") + 1], "Read,Glob,Grep")  # the reviewer stays read-only

    def test_a_record_saved_before_policies_were_bound(self):
        self.configure(tests=False)
        task_id, path = self.task()
        self.go(task_id)
        state = self.saved(path)
        old = dict(state[execution.KEY], version=1)
        del old["policy"]
        state[execution.KEY] = old
        save_json(path / "state.json", state)  # as Step 04 saved it
        self.configure(tests=True, execution={"enabled": False, "profile": "standard"})
        paused = self.go(task_id)
        self.assertEqual((paused["status"], paused["last_error"]["kind"]), ("paused", "execution_policy_missing"))
        self.assertEqual(len(self.cli.calls), 1)
        self.assertEqual(paused[execution.KEY], old)  # kept as it was saved
        self.assert_idempotent(task_id, path, paused)
        self.configure(execution=STANDARD)  # the project's policy, while it is on; not written into the record
        done = self.go(task_id)
        self.assertEqual(done["status"], "done", done.get("last_error"))
        self.assertEqual(done[execution.KEY]["version"], 1)
        self.assertNotIn("policy", done[execution.KEY])

    def test_a_missing_or_corrupt_record_never_falls_back_to_running_without_one(self):
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 10})
        self.go(task_id)
        state = self.saved(path)
        del state[execution.KEY]
        save_json(path / "state.json", state)
        for section in (STANDARD, None):
            self.configure(execution=section)
            paused = self.go(task_id)
            self.assertEqual((paused["status"], paused["last_error"]["kind"]), ("paused", "execution_record_missing"))
            self.assertEqual(len(self.cli.calls), 1)
            self.assertNotIn(execution.KEY, paused)
            self.assert_idempotent(task_id, path, paused)
        for corrupt in ({"version": 1}, None, {"version": 2, "task_id": task_id}):
            with self.subTest(corrupt=corrupt):
                state = self.saved(path)
                state[execution.KEY] = corrupt
                save_json(path / "state.json", state)
                paused = self.go(task_id)
                self.assertEqual(paused["last_error"]["kind"], "execution_unreadable")
                self.assertEqual((paused[execution.KEY], len(self.cli.calls)), (corrupt, 1))

    def test_a_step_frozen_without_its_record_is_recorded_not_run_without_one(self):
        self.configure(tests=False)
        task_id, path = self.task()
        self.go(task_id)
        state = self.saved(path)
        cfg = core.task_config(self.home, task_id)
        layers = agents.layers(agents.workspace_root(self.home), cfg, state)
        state["agent_runs"][agents.step_key(state, "reviewer")] = agents.freeze("codex", "reviewer", layers)
        save_json(path / "state.json", state)  # as a pause between freezing and recording the step leaves it
        self.configure(tests=True, execution=None)
        done = self.go(task_id)
        self.assertEqual(done["status"], "done", done.get("last_error"))
        record = execution.load(done)
        self.assertEqual((record.step.phase, record.step.last.attempt_id), ("review", "CX-0002"))
        self.assertTrue(self.cli.calls[-1]["launch"])

    def test_a_task_never_recorded_stays_as_it_was(self):
        self.configure(execution=None)
        task_id, path = self.task()
        state = self.go(task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        self.configure(execution=STANDARD)  # a task created before stays without a record
        self.assertNotIn(execution.KEY, self.saved(path))
        self.assertFalse(core._record_missing(path, self.saved(path)))
        self.assertTrue(all(not call["launch"] for call in self.cli.calls))


class ReleaseMeaningTests(BudgetCase):
    """Step 05.1: `--confirm-stopped` is the operator's assertion; it proves and decides nothing else."""

    def test_a_released_timeout_stays_a_timeout_and_keeps_its_charge(self):
        task_id, path = self.task()
        self.cli.plan({"timeout": True, "tree": "child_only"})
        held = self.go(task_id)
        held = self.go(task_id)
        self.assertEqual(held["last_error"]["kind"], "attempt_uncertain")
        before = execution.load(held)
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--home", str(self.home), "release-attempt", task_id, "CX-0001", "--confirm-stopped"])
        self.assertEqual(code, 0)
        self.assertIn("you stated that its processes stopped", out.getvalue())
        self.assertIn("what it did is not known", out.getvalue())
        released = self.saved(path)
        record = execution.load(released)
        item = record.step.last
        self.assertEqual((item.outcome, item.termination), (AttemptOutcome.TIMEOUT, Termination.RELEASED))
        self.assertIs(record.step.status, execution.StepStatus.STOPPED)  # never an answer received
        self.assertEqual((record.budget, record.step.budget), (before.budget, before.step.budget))
        entry = released["history"][-1]
        self.assertEqual((entry["previously"], entry["effects"], entry["outcome"]), ("unverified", "unknown", "TIMEOUT"))
        self.assertNotIn("task_completed", self.events(released))
        self.assertEqual(released["status"], "paused")


class RecoveryTests(BudgetCase):
    """Quota recovery stays the only retrier, and every retry is held to the same budgets as any call."""

    def supervise(self, task_id: str, clock: Clock) -> dict:
        return recovery.supervise(self.home, task_id, auto_resume=True, clock=clock, sleep=clock.sleep)

    def test_a_retry_the_budget_can_take_is_planned_once_and_charged_once(self):
        task_id, path = self.task()
        self.cli.plan({"quota": True})
        state = self.supervise(task_id, Clock())
        self.assertEqual(state["status"], "done", state.get("last_error"))
        events = self.events(state)
        for name in ("recovery_scheduled", "recovery_retry_started", "recovery_completed"):
            self.assertEqual(events.count(name), 1, name)
        ended = [entry for entry in state["history"] if entry["event"] == "attempt_ended"]
        self.assertEqual([(entry["attempt"], entry["outcome"]) for entry in ended],
                         [("CX-0001", "QUOTA_EXHAUSTED"), ("CX-0002", "COMPLETED"), ("CX-0003", "COMPLETED")])
        self.assertEqual(execution.load(state).budget.line(Unit.PROVIDER_CALLS).used, 3)
        # The streak ends when the task leaves the step, not when the retried call answers.
        answered = next(index for index, entry in enumerate(state["history"])
                        if entry["event"] == "attempt_ended" and entry["attempt"] == "CX-0002")
        self.assertGreater(events.index("recovery_completed"), events.index("development_completed"))
        self.assertGreater(events.index("recovery_completed"), answered)
        reviewer = self.cli.calls[-1]["argv"]
        self.assertEqual(reviewer[reviewer.index("--sandbox") + 1], "read-only")

    def test_at_the_phase_budget_boundary_no_retry_is_planned_and_the_pause_says_why(self):
        task_id, path = self.task()
        self.cli.plan({"quota": True}, {"quota": True})
        state = self.supervise(task_id, Clock())
        self.assertEqual(len(self.cli.calls), 2)  # one failure, one retry: the standard step's two calls
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "quota"))  # what happened
        self.assertEqual((state["recovery"]["status"], state["recovery"]["stop_reason"], state["recovery"]["consecutive_failures"]),
                         ("stopped", "budget_exhausted", 2))
        stopped = [entry for entry in state["history"] if entry["event"] == "recovery_stopped"][-1]
        self.assertEqual(stopped["budget"], {"scope": "phase", "unit": "provider_calls"})
        self.assertEqual(self.events(state).count("recovery_scheduled"), 1)
        item = execution.load(state).step.last
        self.assertEqual((item.outcome, item.failure_kind), (AttemptOutcome.QUOTA_EXHAUSTED, "quota"))
        # An explicit run calls nothing and pauses with the budget; from then on a Resume changes nothing.
        paused = self.go(task_id)
        self.assert_exhausted(paused, path, {"scope": "phase", "unit": "provider_calls", "limit": 2, "used": 2,
                                             "unknown": 0, "requested": 1}, calls=2)
        self.assert_idempotent(task_id, path, paused, auto_resume=True)
        self.assertEqual(self.supervise(task_id, Clock()), paused)

    def test_a_saved_plan_no_budget_could_take_is_not_launched(self):
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 10}, {"quota": True})
        self.go(task_id)
        clock = Clock()
        with patch("patchrondo.core._refusal", return_value=None):  # a plan saved by a version without budgets
            planned = self.go(task_id, auto_resume=True, clock=clock)
        self.assertEqual(planned["recovery"]["status"], "scheduled")
        due = recovery.parse_time(planned["recovery"]["resume_at"])
        state = self.go(task_id, auto_resume=True, clock=lambda: due, retry_of=planned["recovery"]["resume_at"])
        self.assertEqual((state["recovery"]["status"], state["recovery"]["stop_reason"]), ("stopped", "budget_exhausted"))
        added = self.events(state)[len(planned["history"]):]
        self.assertEqual(added, ["recovery_stopped"])  # no retry started, no run started
        self.assertEqual(len(self.cli.calls), 2)

    def test_quota_and_a_turn_limit_reported_together(self):
        task_id, path = self.task()
        # The turn limit is the CLI's own result; usage wording in what the agent printed is not a diagnostic.
        self.cli.plan({"max_turns": True, "turns": 10, "say": "rate limit: usage limit reached, resets at 5pm"})
        state = self.go(task_id, auto_resume=True)
        item = execution.load(state).step.last
        self.assertEqual((state["last_error"]["kind"], item.outcome, state["recovery"]),
                         ("max_turns", AttemptOutcome.TURN_LIMIT_REACHED, None))
        # A usage diagnostic on the CLI's stderr decides first, as for every failure: quota, left to recovery.
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 10, "stderr": "Claude AI usage limit reached\n"})
        state = self.go(task_id, auto_resume=True)
        item = execution.load(state).step.last
        self.assertEqual((state["last_error"]["kind"], item.outcome, state["recovery"]["status"]),
                         ("quota", AttemptOutcome.QUOTA_EXHAUSTED, "scheduled"))

    def test_a_retry_meets_a_call_that_may_still_run(self):
        task_id, path = self.task()
        self.cli.plan({"quota": True})
        planned = self.go(task_id, auto_resume=True, clock=Clock())
        state = self.saved(path)
        record, _ = execution.load(state).new_attempt(started_at="2026-11-08T12:00:00+00:00")
        state[execution.KEY] = record.to_dict()
        runfiles.allocate(path, 1, "develop", 2)  # its folder exists: whether it started is not known
        save_json(path / "state.json", state)
        due = recovery.parse_time(planned["recovery"]["resume_at"])
        held = self.go(task_id, auto_resume=True, clock=lambda: due, retry_of=planned["recovery"]["resume_at"])
        self.assertEqual(held["last_error"]["kind"], "attempt_uncertain")
        self.assertEqual((held["recovery"]["status"], held["recovery"]["stop_reason"]), ("stopped", "attempt_uncertain"))
        self.assertEqual(len(self.cli.calls), 1)


class ReleaseTests(BudgetCase):
    def lost(self) -> tuple[str, Path, dict]:
        task_id, path = self.task()
        self.cli.plan({"crash": "running"})
        with self.assertRaises(Crash):
            run_task(self.home, task_id)
        (self.folder(path, "develop", 1) / "developer.active-process.json").unlink()  # an absent file proves nothing
        return task_id, path, self.saved(path)

    def test_a_release_needs_an_explicit_acknowledgement(self):
        task_id, path, _ = self.lost()
        held = self.go(task_id)
        for confirm in (False, None, "yes", 1):
            with self.subTest(confirm=confirm), self.assertRaisesRegex(ValueError, "--confirm-stopped"):
                release_attempt(self.home, task_id, "CX-0001", confirm=confirm)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["--home", str(self.home), "release-attempt", task_id, "CX-0001"])
        self.assertNotEqual(code, 0)
        self.assertEqual(self.saved(path), held)

    def test_a_release_checks_the_task_the_attempt_and_its_state(self):
        task_id, path, lost = self.lost()
        with self.assertRaisesRegex(ValueError, "not paused"):  # a run left it running: resume first
            release_attempt(self.home, task_id, "CX-0001", confirm=True)
        held = self.go(task_id)
        for name in ("cx-0001", "CX-1", "CX-0001 ", "", "../CX-0001"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "not an attempt ID"):
                release_attempt(self.home, task_id, name, confirm=True)
        other, _ = self.task()
        with self.assertRaisesRegex(ValueError, "not paused"):  # another task, never run: nothing of it is held
            release_attempt(self.home, other, "CX-0001", confirm=True)
        state = self.saved(path)
        save_json(path / "state.json", {**state, "id": other})
        with self.assertRaisesRegex(ValueError, "another task"):
            release_attempt(self.home, task_id, "CX-0001", confirm=True)
        save_json(path / "state.json", state)
        self.assertEqual(self.saved(path), held)

    def test_a_folder_that_cannot_be_inspected_is_never_taken_for_one_without_processes(self):
        task_id, path, _ = self.lost()
        held = self.go(task_id)
        folder = self.folder(path, "develop", 1)
        shutil.rmtree(folder)
        folder.write_text("not a folder", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "not a plain folder"):
            release_attempt(self.home, task_id, "CX-0001", confirm=True)
        self.assertEqual(self.saved(path), held)

    def test_a_release_records_what_was_checked_and_claims_nothing_about_the_call(self):
        task_id, path, _ = self.lost()
        held = self.go(task_id)
        before = execution.load(held)
        released = release_attempt(self.home, task_id, "CX-0001", confirm=True)
        entry = released["history"][-1]
        self.assertEqual({key: entry[key] for key in ("event", "attempt", "acknowledged", "previously", "process_files",
                                                      "outcome", "effects")},
                         {"event": "attempt_released", "attempt": "CX-0001", "acknowledged": "processes_stopped",
                          "previously": "no_end", "process_files": 0, "outcome": "UNKNOWN", "effects": "unknown"})
        record = execution.load(released)
        item = record.step.last
        self.assertEqual((item.outcome, item.termination, item.workspace_after), (AttemptOutcome.UNKNOWN,
                                                                                  Termination.RELEASED, None))
        self.assertEqual((record.budget, record.step.budget), (before.budget, before.step.budget))  # charges kept
        self.assertEqual(released["status"], "paused")
        # The released call keeps its most-allowed charge: 40 turns and 2700 seconds of the step's budget.
        self.assertEqual(record.step.budget.line(Unit.CLAUDE_TURNS).used, 40)
        self.assertEqual(self.go(task_id)["status"], "done")

    def test_a_live_process_still_blocks_a_confirmed_release(self):
        task_id, path = self.task()
        self.cli.plan({"crash": "running"})
        with self.assertRaises(Crash):
            run_task(self.home, task_id)
        held = self.go(task_id)
        with patch("patchrondo.storage._alive", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "may still be running"):
                release_attempt(self.home, task_id, "CX-0001", confirm=True)
        self.assertEqual(self.saved(path), held)
        self.assertTrue((self.folder(path, "develop", 1) / "developer.active-process.json").exists())


class OutcomeTests(BudgetCase):
    def test_a_refused_start_is_permission_denied_and_keeps_the_saved_kind(self):
        task_id, path = self.task()
        self.cli.plan({"denied": True})
        state = self.go(task_id)
        item = execution.load(state).step.last
        self.assertEqual((state["last_error"]["kind"], item.outcome, item.failure_kind, item.termination),
                         ("system_error", AttemptOutcome.PERMISSION_DENIED, "system_error", Termination.NOT_STARTED))
        self.assertEqual(execution.load(state).budget.line(Unit.PROVIDER_CALLS).used, 0)

    def test_an_answer_ends_the_call_not_the_phase_or_the_task(self):
        self.configure(tests=False)
        task_id, path = self.task()
        state = self.go(task_id)
        item = execution.load(state).step.last
        self.assertEqual((item.outcome, item.termination), (AttemptOutcome.COMPLETED, Termination.EXITED))
        self.assertEqual((state["status"], state["phase"], state["last_error"]["kind"]), ("paused", "test", "tests_disabled"))
        self.assertNotIn("task_completed", self.events(state))


if __name__ == "__main__":
    unittest.main()
