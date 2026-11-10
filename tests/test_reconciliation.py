"""Crash reconciliation of attempts (Phase 2.5, Step 09A).

A run that finds an attempt that may still run reads what is on record of
it (`reconciliation.assess`) and settles it only from proof: never started
(its folder never made, or its launch record never past `prepared`), or
ended with everything it started (its launch record, or the identity of its
processes and what held them). Everything else keeps the task held, with a
diagnosis, until the processes are shown gone or the user releases the call.

The provider CLI is the fake of `test_attempts`, reached only through
`patchrondo.providers.execute`; crashes are its `Crash`, which no run
catches. Whether a recorded process tree still runs is the one rule
`procinfo.tree_state`, replaced here by a fixed answer where a test says
what the system would answer; `test_supervision` runs it on real processes.
No real CLI is started.
"""
import dataclasses
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_attempts  # noqa: E402  (as a module: its test classes are not collected here again)
from test_attempts import PID, Crash  # noqa: E402
from test_auto_continuation import ADAPTIVE, BRIEF, ContinuationCase  # noqa: E402
from test_budgets import narrowed  # noqa: E402

from patchrondo import app, bundle, cli, core, execution, procinfo, process, reconciliation, runfiles  # noqa: E402
from patchrondo.core import release_attempt  # noqa: E402
from patchrondo.execution import (AttemptOutcome, AttemptStatus, ContinuationMode, Declined, Termination,  # noqa: E402
                                  Unit)
from patchrondo.storage import TaskLock, save_json  # noqa: E402

IDENTITY = "test:identity-of-the-cli"  # what `procinfo.identity` gives a supervised process in the fake's plans


def system(answer: str, code: str):
    """What the system says of every recorded process tree: `procinfo.tree_state`, the one rule, fixed."""
    return patch("patchrondo.procinfo.tree_state", return_value=(answer, code))


class ReconciliationCase(ContinuationCase):
    EXECUTION = ADAPTIVE

    def crash(self, task_id: str, *patches) -> None:
        with self.assertRaises(Crash):
            for target in patches:
                target.start()
            try:
                self.go(task_id)
            finally:
                for target in patches:
                    target.stop()

    def launch_record(self, path: Path, ordinal: int = 1, phase: str = "develop") -> dict | None:
        return reconciliation.read_marker(self.folder(path, phase, ordinal))

    def assert_paused(self, state: dict, kind: str) -> None:
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", kind), state.get("last_error"))

    def calls_charged(self, state: dict) -> int:
        return execution.load(state).budget.line(Unit.PROVIDER_CALLS).used


class NeverStartedTests(ReconciliationCase):
    def test_a_folder_made_before_the_launch_is_never_started_and_costs_no_call(self):
        task_id, path = self.task()
        real = core._mark

        def mark(folder, item, stage, launch=None):
            if stage == reconciliation.LAUNCHING:
                raise Crash()  # lost after the folder and its `prepared` record, before the CLI was asked to start
            real(folder, item, stage, launch)

        self.crash(task_id, patch("patchrondo.core._mark", side_effect=mark))
        saved = self.saved(path)
        self.assertEqual(execution.load(saved).step.last.status, AttemptStatus.STARTING)
        self.assertEqual(self.launch_record(path)["stage"], reconciliation.PREPARED)
        self.assertEqual(self.cli.calls, [])
        state = self.go(task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        ended = [entry for entry in state["history"] if entry["event"] == "attempt_ended" and entry["attempt"] == "CX-0001"]
        self.assertEqual([(entry["outcome"], entry["termination"], entry.get("found"), entry.get("evidence"))
                          for entry in ended], [("INTERRUPTED", "not_started", True, ["launch_not_reached"])])
        # One developer call (CX-0002) and one review: CX-0001 charged no call.
        self.assertEqual([call["role"] for call in self.cli.calls], ["developer", "reviewer"])
        self.assertEqual(self.calls_charged(state), 2)
        self.assertNotIn("attempt_reconciled", self.events(state))

    def test_a_folder_never_made_stays_the_rule_it_was(self):
        task_id, path = self.task()
        self.crash(task_id, patch("patchrondo.core.runfiles.allocate", side_effect=Crash()))
        state = self.go(task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        ended = [entry for entry in state["history"] if entry["event"] == "attempt_ended" and entry["attempt"] == "CX-0001"]
        self.assertEqual([(entry["termination"], entry.get("found"), entry.get("evidence")) for entry in ended],
                         [("not_started", True, None)])


class HeldTests(ReconciliationCase):
    def test_a_crash_while_the_cli_was_being_created_is_held_with_its_diagnosis(self):
        task_id, path = self.task()
        self.cli.plan({"crash": "launching"})
        self.crash(task_id)
        self.assertEqual(self.launch_record(path)["stage"], reconciliation.LAUNCHING)
        state = test_attempts.UncertainTests.assert_held(self, task_id, path, "CX-0001")
        self.assertEqual(state["last_error"]["reconciliation"],
                         {"attempt": "CX-0001", "finding": "uncertain", "evidence": [], "unknown": ["launch_unconfirmed"],
                          "launch_record": "launching"})
        self.assertIn("Not known:", state["last_error"]["message"])
        self.assertIn(reconciliation.WORDS["launch_unconfirmed"], state["last_error"]["message"])
        self.assertNotIn(str(4242), json.dumps(state["last_error"]))  # no PID in what is saved for people
        released = release_attempt(self.home, task_id, "CX-0001", confirm=True)
        item = execution.load(released).step.last
        self.assertEqual((item.outcome, item.termination), (AttemptOutcome.UNKNOWN, Termination.RELEASED))
        self.assertEqual(self.go(task_id)["status"], "done")

    def test_a_process_not_yet_named_by_a_record_is_held(self):
        task_id, path = self.task()
        self.cli.plan({"crash": "before_identity", "identity": IDENTITY})
        self.crash(task_id)
        state = test_attempts.UncertainTests.assert_held(self, task_id, path, "CX-0001")
        self.assertEqual(state["last_error"]["reconciliation"]["unknown"], ["launch_unconfirmed"])

    def test_a_process_named_by_its_number_only_is_never_settled(self):
        task_id, path = self.task()
        self.cli.plan({"crash": "running"})  # a process file and a launch record without the process's identity
        self.crash(task_id)
        with system(procinfo.DEAD, "group_gone"):  # even when the system would say that PID is gone
            state = test_attempts.UncertainTests.assert_held(self, task_id, path, "CX-0001")
        self.assertEqual(state["last_error"]["reconciliation"]["unknown"], ["no_identity"])

    def test_a_cli_still_running_keeps_the_task_held_until_it_is_shown_ended(self):
        task_id, path = self.task()
        self.cli.plan({"crash": "running", "identity": IDENTITY, "tree": process.JOB})
        self.crash(task_id)
        started = self.launch_record(path)
        self.assertEqual((started["stage"], started["start"], started["tree"], started["kill_on_close"]),
                         (reconciliation.STARTED, IDENTITY, process.JOB, True))
        with system(procinfo.ALIVE, "process_running"):
            state = test_attempts.UncertainTests.assert_held(self, task_id, path, "CX-0001")
            self.assertEqual(state["last_error"]["reconciliation"]["finding"], "active")
            self.assertNotIn("--confirm-stopped", state["last_error"]["message"])  # nothing to release while it runs
            with self.assertRaisesRegex(RuntimeError, "may still be running"):
                release_attempt(self.home, task_id, "CX-0001", confirm=True)
            save_json(path / ".run.lock", {"pid": -1})  # a lock left by the lost run
            with self.assertRaisesRegex(RuntimeError, "still active"):
                with TaskLock(path, force=True):
                    pass
            (path / ".run.lock").unlink()
        self.assertEqual(self.saved(path), state)
        calls = len(self.cli.calls)
        with system(procinfo.DEAD, "job_closed"):
            reconciled = self.go(task_id)
        self.assert_paused(reconciled, "attempt_reconciled")
        item = execution.load(reconciled).step.last
        self.assertEqual((item.attempt_id, item.outcome, item.termination, item.elapsed_seconds),
                         ("CX-0001", AttemptOutcome.UNKNOWN, Termination.RECONCILED, 2700))
        self.assertEqual(execution.load(reconciled).step.budget.line(Unit.CLAUDE_TURNS).used, 40)  # charged, kept
        event = [entry for entry in reconciled["history"] if entry["event"] == "attempt_reconciled"]
        self.assertEqual([(entry["evidence"], entry["previously"], entry["effects"]) for entry in event],
                         [(["job_closed"], "no_end", "unknown")])
        self.assertIn(reconciliation.WORDS["job_closed"], reconciled["last_error"]["message"])
        self.assertEqual(len(self.cli.calls), calls)
        # Repeated Resumes: the reconciliation is not done again, nothing is charged twice, one new call of the step.
        done = self.go(task_id)
        self.assertEqual(done["status"], "done", done.get("last_error"))
        self.assertEqual(self.events(done).count("attempt_reconciled"), 1)
        self.assertEqual(len(self.cli.calls), calls + 2)
        self.assertEqual(self.go(task_id), done)

    def test_a_reconciled_call_is_never_followed_automatically_in_its_step(self):
        task_id, path = self.task()
        self.cli.plan({"crash": "running", "identity": IDENTITY, "tree": process.JOB})
        self.crash(task_id)
        with system(procinfo.DEAD, "job_closed"):
            self.assert_paused(self.go(task_id), "attempt_reconciled")
        self.cli.plan({"max_turns": True, "turns": 2})  # the user's Resume: a new call of the step, which runs out of turns
        state = self.go(task_id)
        self.assertEqual(self.declined(state)[-1]["reason"], "reconciled_attempt")
        self.assertIn(core.DECLINED[Declined.RECONCILED_ATTEMPT], state["last_error"]["message"])
        self.assertEqual([item.continuation for item in execution.load(state).step.attempts],
                         [ContinuationMode.NONE, ContinuationMode.NONE])

    def test_an_unverified_stop_shown_ended_later_is_reconciled_with_its_outcome(self):
        task_id, path = self.task()
        self.cli.plan({"timeout": True, "tree": process.JOB, "refused": True, "identity": IDENTITY})
        state = self.go(task_id)
        self.assert_paused(state, "timeout")
        self.assertIs(execution.load(state).step.last.termination, Termination.UNVERIFIED)
        ended = self.launch_record(path)
        self.assertEqual((ended["stage"], ended["tree_ended"], ended["kill_refused"]), (reconciliation.ENDED, False, True))
        with system(procinfo.UNKNOWN, "owner_unknown"):
            held = self.go(task_id)
        self.assert_paused(held, "attempt_uncertain")
        self.assertEqual(held["last_error"]["reconciliation"]["unknown"], ["owner_unknown"])
        with system(procinfo.DEAD, "job_closed"):
            reconciled = self.go(task_id)
        self.assert_paused(reconciled, "attempt_reconciled")
        item = execution.load(reconciled).step.last
        self.assertEqual((item.outcome, item.termination), (AttemptOutcome.TIMEOUT, Termination.RECONCILED))
        self.assertEqual([entry["previously"] for entry in reconciled["history"]
                          if entry["event"] == "attempt_reconciled"], ["unverified"])
        self.assertEqual(self.go(task_id)["status"], "done")

    def test_a_record_that_contradicts_itself_or_cannot_be_read_is_held(self):
        task_id, path = self.task()
        self.cli.plan({"crash": "running", "identity": IDENTITY, "tree": process.JOB})
        self.crash(task_id)
        folder = self.folder(path, "develop", 1)
        record = folder / reconciliation.MARKER
        good = record.read_text(encoding="utf-8")
        record.write_text("{not json", encoding="utf-8")
        with system(procinfo.DEAD, "job_closed"):
            state = test_attempts.UncertainTests.assert_held(self, task_id, path, "CX-0001")
        self.assertEqual((state["last_error"]["reconciliation"]["finding"], state["last_error"]["reconciliation"]["unknown"]),
                         ("corrupt", ["launch_record_unreadable"]))
        item = execution.load(state).step.last
        cases = {
            "launch_record_other_attempt": lambda: record.write_text(good.replace("CX-0001", "CX-0099"), encoding="utf-8"),
            "stage_contradicts_status": lambda: record.write_text(good.replace('"started"', '"prepared"'), encoding="utf-8"),
            "process_file_unreadable": lambda: (record.write_text(good, encoding="utf-8"),
                                                (folder / "developer.active-process.json").write_text("x", encoding="utf-8")),
        }
        for code, change in cases.items():
            with self.subTest(code=code), system(procinfo.DEAD, "job_closed"):
                change()
                found = reconciliation.assess(path, item, core._folder_of(item))
                self.assertFalse(found.settles)
                self.assertEqual(found.unknown, (code,))
        self.assertEqual(self.saved(path), state)  # reading it changed nothing


class ReleaseTests(ReconciliationCase):
    def test_without_the_confirmation_the_command_only_shows_what_is_on_record(self):
        task_id, path = self.task()
        self.cli.plan({"crash": "launching"})
        self.crash(task_id)
        held = self.go(task_id)
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--home", str(self.home), "release-attempt", task_id, "CX-0001"])
        self.assertNotEqual(code, 0)
        shown = out.getvalue()
        for words in ("Attempt CX-0001", "Not known:", reconciliation.WORDS["launch_unconfirmed"],
                      "The task's lock is free.", f"release-attempt {task_id} CX-0001 --confirm-stopped"):
            self.assertIn(words, shown)
        self.assertEqual(self.saved(path), held)  # nothing released, nothing written
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--home", str(self.home), "release-attempt", task_id, "CX-0001",
                                       "--confirm-stopped"]), 0)
        released = self.saved(path)
        self.assertEqual([entry["effects"] for entry in released["history"] if entry["event"] == "attempt_released"],
                         ["unknown"])
        # The released call is never followed automatically in its step: its effects are unknown.
        self.cli.plan({"max_turns": True, "turns": 2})
        state = self.go(task_id)
        self.assertEqual(self.declined(state)[-1]["reason"], "released_attempt")
        self.assertEqual(execution.load(state).step.last.continuation, ContinuationMode.NONE)

    def test_a_release_never_settles_more_than_it_says(self):
        task_id, path = self.task()
        self.cli.plan({"crash": "running", "identity": IDENTITY, "tree": process.JOB})
        self.crash(task_id)
        with system(procinfo.UNKNOWN, "owner_unknown"):
            self.go(task_id)
            released = release_attempt(self.home, task_id, "CX-0001", confirm=True)  # the process itself is not shown
        item = execution.load(released).step.last
        self.assertEqual((item.outcome, item.termination, item.turns.availability.value),
                         (AttemptOutcome.UNKNOWN, Termination.RELEASED, "unknown"))
        self.assertEqual(released["status"], "paused")
        self.assertNotIn("development_completed", self.events(released))


class ContinuationAfterACrashTests(ReconciliationCase):
    def lose_the_continuation(self, task_id: str) -> None:
        real = runfiles.allocate

        def allocate(path, iteration, phase, ordinal):
            if ordinal == 2:
                raise Crash()  # the continuation is saved as STARTING, with its decision, and never gets a folder
            return real(path, iteration, phase, ordinal)

        self.crash(task_id, patch("patchrondo.core.runfiles.allocate", side_effect=allocate))

    def test_a_saved_continuation_that_never_started_is_carried_out_once_at_its_size(self):
        self.configure(tests=False)
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 41})
        self.lose_the_continuation(task_id)
        saved = execution.load(self.saved(path)).step
        self.assertEqual([(item.status, item.continuation) for item in saved.attempts],
                         [(AttemptStatus.ENDED, ContinuationMode.NONE),
                          (AttemptStatus.STARTING, ContinuationMode.CHECKPOINT_RESUME)])
        state = self.go(task_id)
        self.assertEqual((state["last_error"]["kind"], state["phase"]), ("tests_disabled", "test"))
        step = execution.load(state).step
        self.assertEqual([(item.attempt_id, item.continuation, item.termination, item.turn_limit)
                          for item in step.attempts],
                         [("CX-0001", ContinuationMode.NONE, Termination.EXITED, 40),
                          ("CX-0002", ContinuationMode.CHECKPOINT_RESUME, Termination.NOT_STARTED, 39),
                          ("CX-0003", ContinuationMode.CHECKPOINT_RESUME, Termination.EXITED, 39)])
        # Two calls in all: the first and the continuation, at the size the budgets admitted, never a full one.
        self.assertEqual([self.turns(call) for call in self.cli.calls], ["40", "39"])
        started = [entry for entry in state["history"] if entry["event"] == "continuation_started"]
        self.assertEqual([(entry["attempt"], entry["after"], entry.get("resumed")) for entry in started],
                         [("CX-0002", "CX-0001", None), ("CX-0003", "CX-0001", True)])
        self.assertEqual(self.calls_charged(state), 2)  # the one that never started charged no call
        self.assertIn(BRIEF, self.prompt(path, 3))

    def test_the_bound_of_one_continuation_per_step_still_holds(self):
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 41})
        self.lose_the_continuation(task_id)
        self.cli.plan({"max_turns": True, "turns": 39})
        state = self.go(task_id)
        self.assert_declined(state, "limit_reached", calls=2, started=2)

    def test_a_continuation_due_at_a_resume_is_admitted_at_its_size(self):
        # The step's budget can no longer take a full call (80 turns, 41 used: 39 left), only the continuation.
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 41})
        self.lose_the_continuation(task_id)
        self.assertIsNotNone(execution.load(self.saved(path)).admission())  # a full call would be refused
        state = self.go(task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        self.assertNotIn("budget_exhausted", [entry.get("reason") for entry in state["history"]])
        self.assertEqual([self.turns(call) for call in self.cli.calls[:2]], ["40", "39"])

    def test_after_a_reconciliation_a_budget_too_small_refuses_the_next_call(self):
        self.configure(execution={**narrowed(phase={"developer": {"provider_calls": 1}}), "adaptive": True})
        task_id, path = self.task()
        self.cli.plan({"crash": "running", "identity": IDENTITY, "tree": process.JOB})
        self.crash(task_id)
        with system(procinfo.DEAD, "job_closed"):
            self.assert_paused(self.go(task_id), "attempt_reconciled")
        state = self.go(task_id)
        self.assert_paused(state, "budget_exhausted")
        self.assertEqual(state["last_error"]["budget"], {"scope": "phase", "unit": "provider_calls", "limit": 1,
                                                         "used": 1, "unknown": 0, "requested": 1})
        self.assertEqual(len(self.cli.calls), 1)  # the reconciled call is charged once, and nothing else was started
        self.assertEqual(self.calls_charged(state), 1)
        self.assert_idempotent(task_id, path, state)


class HeldCallTests(ReconciliationCase):
    """What `_next_call` reads of how a call's processes were held (D4 as Step 09 narrowed it)."""

    HELD = {"attempt": "CX-0001", "tree": process.GROUP, "refused": False, "ended": True, "left": False}

    def test_only_a_tree_held_and_confirmed_ended_with_nothing_left_may_be_followed(self):
        self.assertEqual(core._tree_declined(self.HELD, False), (None, {}))
        cases = [({"tree": process.CHILD}, Declined.PROCESS_TREE_UNVERIFIED),
                 ({"tree": None}, Declined.PROCESS_TREE_UNVERIFIED),
                 ({"refused": True}, Declined.PROCESS_TREE_UNVERIFIED),
                 ({"ended": None}, Declined.PROCESS_TREE_UNVERIFIED),
                 ({"ended": False}, Declined.PROCESS_TREE_UNVERIFIED),
                 ({"left": True}, Declined.DESCENDANTS_LEFT),
                 ({"left": None}, Declined.DESCENDANTS_LEFT)]
        for change, reason in cases:
            with self.subTest(change=change):
                self.assertIs(core._tree_declined({**self.HELD, **change}, False)[0], reason)
        self.assertIs(core._tree_declined({}, False)[0], Declined.PROCESS_TREE_UNVERIFIED)  # a legacy call: nothing known

    def test_a_real_cli_on_native_windows_waits_for_its_validation(self):
        held = {**self.HELD, "tree": process.JOB}
        with patch("patchrondo.core.os") as fake:
            fake.name = "nt"
            self.assertIs(core._tree_declined(held, True)[0], Declined.WINDOWS_NOT_VALIDATED)
            self.assertEqual(core._tree_declined(held, False), (None, {}))  # simulated providers, real job objects
            with patch("patchrondo.core.WINDOWS_REAL_CONTINUATION", True):
                self.assertEqual(core._tree_declined(held, True), (None, {}))
        with patch("patchrondo.core.os") as fake:
            fake.name = "posix"
            self.assertEqual(core._tree_declined({**self.HELD}, True), (None, {}))

    def test_a_cli_that_left_a_process_running_is_not_followed(self):
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "left_behind": True})
        state = self.go(task_id)
        declined = self.assert_declined(state, "descendants_left")
        self.assertEqual(declined["process_tree"], process.GROUP)
        ended = [entry for entry in state["history"] if entry["event"] == "attempt_ended"]
        self.assertTrue(ended[-1]["left_behind"])
        record = self.launch_record(path)
        self.assertEqual((record["stage"], record["left_behind"], record["tree_ended"]), (reconciliation.ENDED, True, True))

    def test_a_cli_whose_tree_could_not_be_confirmed_ended_gives_no_answer(self):
        task_id, path = self.task()
        self.cli.plan({"left_behind": True, "tree_ended": False})  # it answered; what it left could not be stopped
        state = self.go(task_id)
        self.assert_paused(state, "process_tree_unconfirmed")
        item = execution.load(state).step.last
        self.assertEqual((item.outcome, item.termination), (AttemptOutcome.UNKNOWN, Termination.UNVERIFIED))
        self.assertNotIn("development_completed", self.events(state))
        self.assertFalse((path / "handoff.md").exists())
        self.assert_paused(self.go(task_id), "attempt_uncertain")  # held until shown ended or released

    def test_the_launch_record_tells_a_later_run_how_a_call_was_held(self):
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "tree": process.CHILD})
        state = self.go(task_id)
        item = execution.load(state).step.last
        self.assertEqual(core._held_of(path, item), {"attempt": "CX-0001", "tree": process.CHILD, "refused": False,
                                                     "ended": None, "left": None})
        other = dataclasses.replace(item, attempt_id="CX-0042")
        self.assertEqual(core._held_of(path, other), {})  # a record of another call says nothing about this one


class AssessmentTests(ReconciliationCase):
    def test_reading_what_is_on_record_writes_nothing(self):
        task_id, path = self.task()
        self.cli.plan({"crash": "running", "identity": IDENTITY, "tree": process.JOB})
        self.crash(task_id)
        before = {file: file.read_bytes() for file in path.rglob("*") if file.is_file()}
        with system(procinfo.ALIVE, "owner_running"):
            first = core._assessment(path, self.saved(path))
            again = core._assessment(path, self.saved(path))
        self.assertEqual(first, again)
        self.assertEqual((first.finding, first.evidence), (reconciliation.Finding.ACTIVE, ("owner_running",)))
        self.assertEqual({file: file.read_bytes() for file in path.rglob("*") if file.is_file()}, before)

    def test_findings_from_the_folder_and_its_launch_record(self):
        task_id, path = self.task()
        self.cli.plan({"crash": "running", "identity": IDENTITY, "tree": process.JOB})
        self.crash(task_id)
        item = execution.load(self.saved(path)).step.last
        folder = self.folder(path, "develop", 1)
        starting = dataclasses.replace(item, status=AttemptStatus.STARTING)
        moved = folder.with_name("elsewhere")
        folder.rename(moved)
        self.assertEqual(reconciliation.assess(path, starting, core._folder_of(item)).finding,
                         reconciliation.Finding.NEVER_STARTED)
        self.assertEqual(reconciliation.assess(path, item, core._folder_of(item)).unknown,
                         ("folder_missing_after_start",))
        folder.write_text("not a folder", encoding="utf-8")
        self.assertEqual(reconciliation.assess(path, item, core._folder_of(item)).unknown, ("folder_not_plain",))
        folder.unlink()
        moved.rename(folder)
        (folder / reconciliation.MARKER).unlink()
        (folder / "developer.active-process.json").unlink()
        self.assertEqual(reconciliation.assess(path, item, core._folder_of(item)).unknown, ("no_launch_record",))
        save_json(folder / reconciliation.MARKER, reconciliation.marker("CX-0001", reconciliation.ENDED, "x"))
        found = reconciliation.assess(path, item, core._folder_of(item))
        self.assertEqual((found.finding, found.unknown), (reconciliation.Finding.UNCERTAIN, ("launch_unconfirmed",)))
        ended = {**reconciliation.marker("CX-0001", reconciliation.ENDED, "x"), "tree_ended": True, "kill_refused": False}
        save_json(folder / reconciliation.MARKER, ended)
        found = reconciliation.assess(path, item, core._folder_of(item))
        self.assertEqual((found.finding, found.evidence), (reconciliation.Finding.TERMINATED_UNRECORDED,
                                                           ("ended_confirmed",)))
        save_json(folder / reconciliation.MARKER, {**ended, "kill_refused": True})  # its stop was refused: not shown
        self.assertFalse(reconciliation.assess(path, item, core._folder_of(item)).settles)
        for broken in ({**ended, "version": 2}, {**ended, "stage": "later"}, {**ended, "pid": True}, ["list"]):
            with self.subTest(record=broken):
                (folder / reconciliation.MARKER).write_text(json.dumps(broken), encoding="utf-8")
                self.assertEqual(reconciliation.assess(path, item, core._folder_of(item)).unknown,
                                 ("launch_record_unreadable",))

    def test_a_settled_attempt_is_reported_as_recorded(self):
        task_id, path = self.task()
        state = self.go(task_id)
        item = [entry for entry in execution.load(state).step.attempts][-1]
        found = reconciliation.assess(path, item, core._folder_of(item))
        self.assertEqual((found.finding, found.evidence), (reconciliation.Finding.TERMINATED_RECORDED, ("settled",)))
        shown = core.diagnose_attempt(self.home, task_id)
        self.assertEqual((shown["attempt"], shown["finding"]), (item.attempt_id, "terminated_recorded"))
        self.assertTrue(any("as they were when the call started" in line or "changed since" in line
                            for line in shown["lines"]))

    def test_the_launch_record_stays_in_the_private_run_folder(self):
        task_id, path = self.task()
        state = self.go(task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        record = self.launch_record(path)
        self.assertEqual((record["stage"], record["pid"]), (reconciliation.ENDED, PID))
        made = bundle.build(path, "detailed")
        self.assertEqual([entry["path"] for entry in made.manifest["files"]
                          if entry["path"].endswith(reconciliation.MARKER)], [])  # never exported
        self.assertNotIn(Path(reconciliation.MARKER).suffix, app.LOG_SUFFIXES)  # never listed in the Logs tab
        for name in ("state.json", "report.md"):
            self.assertNotIn(str(PID), (path / name).read_text(encoding="utf-8"))

    def test_a_marker_records_only_what_its_stage_knows(self):
        launch = process.Launch()
        self.assertEqual(reconciliation.marker("CX-0001", reconciliation.LAUNCHING, "t", launch),
                         {"version": 1, "attempt": "CX-0001", "stage": "launching", "at": "t"})
        launch._began(7, process.JOB, "win:1", (8, "win:2"), kill_on_close=True)
        launch._finished(True, False)
        ended = reconciliation.marker("CX-0001", reconciliation.ENDED, "t", launch)
        self.assertEqual({key: ended[key] for key in ("pid", "start", "tree", "kill_on_close", "owner_pid", "owner_start",
                                                      "supervised", "tree_ended", "left_behind", "kill_refused")},
                         {"pid": 7, "start": "win:1", "tree": process.JOB, "kill_on_close": True, "owner_pid": 8,
                          "owner_start": "win:2", "supervised": True, "tree_ended": True, "left_behind": False,
                          "kill_refused": False})
        with self.assertRaises(ValueError):
            reconciliation.marker("CX-0001", "later", "t")


class RecordTests(unittest.TestCase):
    """`Termination.RECONCILED` in the record: like a release, never an answer, never followed."""

    def test_reconciled_is_settled_and_its_effects_unknown(self):
        self.assertIn(Termination.RECONCILED, execution.SETTLED)
        self.assertEqual(execution.EFFECTS_UNKNOWN, {Termination.RELEASED, Termination.RECONCILED})
        self.assertIn("attempt_reconciled", execution.ENGINE_KINDS)
        self.assertIs(execution.outcome_for_failure("process_tree_unconfirmed"), AttemptOutcome.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
