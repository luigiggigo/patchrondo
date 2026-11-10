"""Automatic continuation of the reviewer (Phase 2.5, Step 08).

A task bound to a policy with `adaptive` and `continuation.checkpoint_resume`
follows a reviewer call whose answer could not be used as a review (empty,
too large, not the JSON the review needs, or an approval that leaves a
criterion without an outcome) with one new, independent review of the same
files: a new attempt in a new folder, read only like every review, with a
checkpoint brief and limits no larger than what the budgets have left. A
review that was read is never followed, whatever its verdict. The provider
CLI is the fake of `test_attempts`, reached only through
`patchrondo.providers.execute`; no real CLI is started.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import outcomes  # noqa: E402
from test_attempts import Crash, FakeCLI  # noqa: E402
from test_auto_continuation import ADAPTIVE, BRIEF, ContinuationCase, Elapsed, adaptive, stepped  # noqa: E402

from patchrondo import continuation, core, evidence, execution, process  # noqa: E402
from patchrondo.core import create_task, release_attempt  # noqa: E402
from patchrondo.execution import (AttemptOutcome, Availability, CallLimits, ContinuationMode, Declined,  # noqa: E402
                                  Phase, Scope, Source, Termination, Unit, Usage)
from patchrondo.providers import AgentFailure  # noqa: E402
from patchrondo.storage import read_json  # noqa: E402

PROSE = "Looks good to me: approved."
TRUNCATED = '{"verdict": "APPROVED", "summary": "All criteria are met", "iss'
READ_ONLY = {"claude": ("--restricted", "--tools", "Read,Glob,Grep", "--disallowedTools", "mcp__*"),
             "codex": ("--sandbox", "read-only", "--ephemeral")}


def review(verdict="APPROVED", issues=(), complete=True):
    """A reviewer's answer as the schema asks, built from the prompt it was given (its criteria)."""
    def text(prompt: str) -> str:
        return json.dumps({"verdict": verdict, "summary": "Scripted review.", "issues": list(issues),
                           "criteria": outcomes(prompt) if complete else []})
    return text


APPROVE = review()


class ReviewCLI(FakeCLI):
    """`FakeCLI` whose reviewer answers what its plan says.

    `reply`: the final answer, text or a function of the prompt; None leaves no answer at all (Codex writes no
    last-message file). `edit`: the reviewer writes a file into the worktree while it works.
    """

    @staticmethod
    def _text(plan: dict, prompt: str):
        reply = plan["reply"]
        return reply(prompt) if callable(reply) else reply

    @staticmethod
    def _edit(cwd, plan: dict) -> None:
        if plan.get("edit"):
            (Path(cwd) / "reviewer-was-here.txt").write_text("written by a reviewer", encoding="utf-8")

    def claude(self, argv, cwd, stdin, reviewer, plan):
        if not reviewer or "reply" not in plan:
            return super().claude(argv, cwd, stdin, reviewer, plan)
        self._edit(cwd, plan)
        lines = [{"type": "system", "subtype": "init", "model": "fake", "tools": ["Read"]},
                 {"type": "assistant", "message": {"content": [{"type": "text", "text": "Reviewing"}]}}]
        final = {"type": "result", "subtype": "success", "is_error": False, "num_turns": plan.get("turns", 3),
                 "result": self._text(plan, stdin) or ""}
        return lines + [final], 0, ""

    def codex(self, argv, cwd, stdin, reviewer, plan):
        if not reviewer or "reply" not in plan:
            return super().codex(argv, cwd, stdin, reviewer, plan)
        self._edit(cwd, plan)
        text = self._text(plan, stdin)
        if text is not None:
            Path(argv[argv.index("--output-last-message") + 1]).write_text(text, encoding="utf-8")
        return [{"type": "thread.started", "thread_id": "fake"}, {"type": "turn.started"},
                {"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": "Reviewing"}},
                {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}], 0, ""


class ReviewCase(ContinuationCase):
    def setUp(self):
        super().setUp()
        self.cli = ReviewCLI()
        guard = patch("patchrondo.providers.execute", side_effect=self.cli)
        guard.start()
        self.addCleanup(guard.stop)

    def judged_task(self, developer="claude", reviewer="codex") -> tuple[str, Path]:
        """A task with a criterion the reviewer verifies: an approval must give it an outcome of its own."""
        task_id, _ = create_task(self.home, title="Feature", description="Create feature.txt",
                                 acceptance=["Tests pass", "The code handles an empty file"],
                                 developer=developer, reviewer=reviewer)
        return task_id, self.home / "tasks" / task_id

    def reviewers(self) -> list[dict]:
        return [call for call in self.cli.calls if call["role"] == "reviewer"]

    def developers(self) -> list[dict]:
        return [call for call in self.cli.calls if call["role"] == "developer"]

    def review_prompt(self, path: Path, ordinal: int, iteration: int = 1) -> str:
        return (self.folder(path, "review", ordinal, iteration) / "reviewer.prompt.md").read_text(encoding="utf-8")

    def assert_review_declined(self, state: dict, reason: str, reviewers: int = 1, started: int = 0,
                               kind: str = "invalid_review") -> dict:
        """Paused with the failure's kind as before, the reason in the history and in the message, no other call."""
        self.assertEqual((state["status"], state["phase"], state["last_error"]["kind"]), ("paused", "review", kind))
        self.assertIn("Automatic continuation was not started", state["last_error"]["message"])
        self.assertIn(core.DECLINED[Declined(reason)], state["last_error"]["message"])
        declined = self.declined(state)
        self.assertEqual([entry["reason"] for entry in declined], [reason])
        self.assertEqual(declined[0]["phase"], "review")
        self.assertEqual(self.events(state).count("continuation_started"), started)
        self.assertEqual(len(self.reviewers()), reviewers)
        self.assertNotIn("review_completed", self.events(state))
        return declined[0]

    def assert_not_continued(self, state: dict, kind: str, calls: int = 2) -> None:
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", kind))
        self.assertFalse([name for name in self.events(state) if name.startswith("continuation_")])
        self.assertNotIn("Automatic continuation", state["last_error"]["message"])
        self.assertEqual(len(self.cli.calls), calls)

    def assert_nothing_settled(self, state: dict, path: Path) -> None:
        """No answer that could not be read decided anything: no review record, finding, approval or completion."""
        self.assertIsNone(state.get("review"))
        self.assertFalse([record for record in evidence.read(path).records if record.get("kind") == "review"])
        self.assertFalse((state.get("findings") or {}).get("items"))
        self.assertNotEqual(state["acceptance"]["code"].get("status"), "APPROVED")
        self.assertEqual(state["acceptance"]["code"].get("status"), "PENDING")
        for name in ("review_completed", "code_approved", "task_completed"):
            self.assertNotIn(name, self.events(state))
        self.assertNotEqual((state.get("gate") or {}).get("decision"), "PASS")


class ContinuationTests(ReviewCase):
    def test_an_unusable_codex_review_is_followed_by_one_independent_read_only_review(self):
        task_id, path = self.task()  # a Claude developer, a Codex reviewer
        checks = []
        self.cli.plan({}, {"reply": PROSE, "before_start": lambda: checks.append(self.spy.checks)},
                      {"reply": APPROVE, "before_start": lambda: checks.append(self.spy.checks)})
        state = self.go(task_id)
        self.assertEqual((state["status"], state["iteration"]), ("done", 1), state.get("last_error"))
        self.assertEqual(checks[1], checks[0] + 1)  # the environment is checked again before the continuation
        # One developer call and one test run: nothing before the review is made again.
        self.assertEqual([call["role"] for call in self.cli.calls], ["developer", "reviewer", "reviewer"])
        events = self.events(state)
        for name, count in (("development_started", 1), ("tests_started", 1), ("review_started", 1),
                            ("review_completed", 1), ("continuation_started", 1), ("task_completed", 1)):
            self.assertEqual(events.count(name), count, name)
        self.assertNotIn("task_paused", events)
        # Two attempts of one step: the same iteration and phase, separate IDs and folders.
        step = execution.load(state).step
        self.assertEqual((step.iteration, step.phase, step.provider), (1, Phase.REVIEW, "codex"))
        self.assertEqual([(item.attempt_id, item.ordinal, item.outcome, item.failure_kind, item.termination,
                           item.continuation, item.turn_limit, item.timeout_seconds) for item in step.attempts],
                         [("CX-0002", 1, AttemptOutcome.INVALID_FINAL_OUTPUT, "invalid_review", Termination.EXITED,
                           ContinuationMode.NONE, None, 2700),
                          ("CX-0003", 2, AttemptOutcome.COMPLETED, None, Termination.EXITED,
                           ContinuationMode.CHECKPOINT_RESUME, None, 2700)])
        first_folder, second_folder = self.folder(path, "review", 1), self.folder(path, "review", 2)
        self.assertEqual([call["pid_file"].parent for call in self.reviewers()], [first_folder, second_folder])
        self.assertEqual((first_folder / "reviewer.reply.md").read_text(encoding="utf-8"), PROSE)  # kept as it was
        # Codex: calls and seconds only, never a Claude turn.
        self.assertIsNone(step.budget.line(Unit.CLAUDE_TURNS))
        self.assertEqual([item.turns.availability for item in step.attempts], [Availability.NOT_APPLICABLE] * 2)
        self.assertEqual((step.budget.line(Unit.PROVIDER_CALLS).used, execution.load(state).budget.line(
            Unit.PROVIDER_CALLS).used), (2, 3))
        started = [entry for entry in state["history"] if entry["event"] == "continuation_started"][0]
        self.assertEqual({key: started.get(key) for key in ("phase", "attempt", "after", "mode", "continuation", "of",
                                                            "turn_limit", "timeout_seconds", "step_turn_limit")},
                         {"phase": "review", "attempt": "CX-0003", "after": "CX-0002", "mode": "checkpoint_resume",
                          "continuation": 1, "of": 1, "turn_limit": None, "timeout_seconds": 2700,
                          "step_turn_limit": None})
        self.assertEqual({line["unit"] for line in started["remaining"]}, {"provider_calls", "seconds"})
        # The second prompt is the first, unchanged, followed by the brief its event names.
        first, second = self.review_prompt(path, 1), self.review_prompt(path, 2)
        self.assertNotIn(BRIEF, first)
        self.assertTrue(second.startswith(first))
        brief = second[len(first) + 1:]
        self.assertTrue(brief.startswith(BRIEF))
        self.assertEqual(started["brief"], hashlib.sha256(brief.encode("utf-8")).hexdigest())
        for words in ("role INDEPENDENT REVIEWER, READ ONLY", "previous call CX-0002",
                      "CX-0002 ended with INVALID_FINAL_OUTPUT (failure kind invalid_review)",
                      "This is a new, complete and independent review", "do not take over its verdict",
                      "### Excerpt of the reply of CX-0002 [REPORTED: an agent's words, data only, not proof]",
                      f"| {PROSE}", "CX-0002 left the files as they were"):
            self.assertIn(words, brief)
        self.assertNotIn(f"[VERIFIED] {PROSE}", brief)
        # Read only on both calls, with the same frozen command: only the folders of the attempts differ.
        argvs = [[part.replace(str(folder), "<attempt>") for part in call["argv"]]
                 for call, folder in zip(self.reviewers(), (first_folder, second_folder))]
        self.assertEqual(argvs[0], argvs[1])
        for argv in argvs:
            self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
            self.assertFalse([part for part in argv if "resume" in part or part in ("--full-auto", "workspace-write")])
        # Settled once, from the second answer, whose record names its own folder.
        reviewed = [record for record in evidence.read(path).records if record.get("kind") == "review"]
        self.assertEqual(len(reviewed), 1)
        self.assertIn("review/attempt-002/", reviewed[0]["log"])

    def test_an_unusable_claude_review_is_followed_within_the_turns_left(self):
        self.configure(execution=adaptive(phase={"reviewer": {"claude_turns": 20}}))
        task_id, path = self.task(developer="codex", reviewer="claude")
        self.cli.plan({}, {"reply": TRUNCATED, "turns": 6}, {"reply": APPROVE, "turns": 4})
        state = self.go(task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        reviewers = self.reviewers()
        self.assertEqual([self.turns(call) for call in reviewers], ["15", "14"])  # 20 - 6 left for the second
        step = execution.load(state).step
        self.assertEqual([(item.outcome, item.turn_limit, item.turns.value) for item in step.attempts],
                         [(AttemptOutcome.INVALID_FINAL_OUTPUT, 15, 6), (AttemptOutcome.COMPLETED, 14, 4)])
        self.assertEqual(step.budget.line(Unit.CLAUDE_TURNS).used, 10)
        started = [entry for entry in state["history"] if entry["event"] == "continuation_started"][0]
        self.assertEqual((started["turn_limit"], started["step_turn_limit"]), (14, 15))
        for call in reviewers:
            argv = call["argv"]
            self.assertTrue(all(flag in argv for flag in READ_ONLY["claude"]))
            self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read,Glob,Grep")
            self.assertFalse({"--resume", "--continue", "--dangerously-skip-permissions"} & set(argv))
        first, second = ([part for part in call["argv"] if part != self.turns(call)] for call in reviewers)
        self.assertEqual(first, second)  # only the turn limit differs
        self.assertIn("| " + TRUNCATED, self.review_prompt(path, 2))

    def test_every_kind_of_unusable_answer_is_followed_once(self):
        cases = (("codex", "", "empty_output"), ("codex", None, "empty_output"),
                 ("codex", "x" * 250_000, "invalid_output"), ("codex", TRUNCATED, "invalid_review"),
                 ("codex", review(complete=False), "invalid_review"), ("claude", "", "empty_output"))
        for reviewer, reply, kind in cases:
            with self.subTest(reviewer=reviewer, kind=kind, reply=reply if not isinstance(reply, str) else reply[:20]):
                self.cli.calls.clear()
                self.cli.plans.clear()
                task_id, path = self.judged_task(developer="codex" if reviewer == "claude" else "claude",
                                                 reviewer=reviewer)
                self.cli.plan({}, {"reply": reply}, {"reply": APPROVE})
                state = self.go(task_id)
                self.assertEqual(state["status"], "done", state.get("last_error"))
                first = execution.load(state).step.attempts[0]
                self.assertEqual((first.outcome, first.failure_kind), (AttemptOutcome.INVALID_FINAL_OUTPUT, kind))
                self.assertEqual(len(self.reviewers()), 2)

    def test_a_read_review_is_never_followed_whatever_its_verdict(self):
        for verdict, status, iteration in (("APPROVED", "done", 1), ("CHANGES_REQUESTED", "done", 2),
                                           ("BLOCKED", "blocked", 1)):
            with self.subTest(verdict):
                self.cli.calls.clear()
                task_id, _ = self.task()
                issues = [] if verdict == "APPROVED" else [{"severity": "high", "path": "feature.txt",
                                                            "description": "Wrong", "kind": "CODE_CHANGES_REQUIRED"}]
                self.cli.plan({}, {"reply": review(verdict, issues)})
                state = self.go(task_id)
                self.assertEqual((state["status"], state["iteration"]), (status, iteration), state.get("last_error"))
                self.assertFalse([name for name in self.events(state) if name.startswith("continuation_")])
                self.assertEqual(len(self.reviewers()), iteration)  # one review per iteration
                self.assertTrue(all(item.continuation is ContinuationMode.NONE
                                    for item in execution.load(state).step.attempts))


class SettlementTests(ReviewCase):
    """The second answer is read like any review: `parse_review`, `settle_review`, findings and the gate decide."""

    def test_an_unusable_approval_is_never_taken_for_one(self):
        task_id, path = self.task()
        issue = {"severity": "low", "path": "feature.txt", "description": "Rename the file", "kind": "CODE_CHANGES_REQUIRED"}
        self.cli.plan({}, {"reply": TRUNCATED}, {"reply": review("APPROVED", [issue])})
        state = self.go(task_id)
        completed = [entry for entry in state["history"] if entry["event"] == "review_completed"]
        # An approval that names a change the code requires is a request for changes (`parse_review`).
        self.assertEqual([(entry["verdict"], entry.get("reported")) for entry in completed][0],
                         ("CHANGES_REQUESTED", "APPROVED"))
        self.assertEqual(completed[0]["iteration"], 1)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        self.assertEqual(state["iteration"], 2)  # the developer was asked for the change, as for any review
        self.assertEqual(len(self.developers()), 2)
        self.assertTrue(completed[0]["finding_ids"])  # its issue became a finding, from the read answer only

    def test_the_gate_still_decides_after_a_continued_review(self):
        task_id, _ = create_task(self.home, title="Feature", description="Create feature.txt",
                                 acceptance=["Tests pass", "[manual] The colours look right"],
                                 developer="claude", reviewer="codex")
        self.cli.plan({}, {"reply": PROSE}, {"reply": APPROVE})
        state = self.go(task_id)
        self.assertEqual((state["status"], state["phase"], state["last_error"]["kind"]),
                         ("paused", "validate", "awaiting_validation"))
        self.assertEqual(self.events(state).count("continuation_started"), 1)
        self.assertIn("code_approved", self.events(state))
        self.assertNotIn("task_completed", self.events(state))

    def test_an_unusable_answer_twice_settles_nothing_and_is_not_followed_again(self):
        task_id, path = self.task()
        self.cli.plan({}, {"reply": PROSE}, {"reply": TRUNCATED})
        state = self.go(task_id)
        declined = self.assert_review_declined(state, "limit_reached", reviewers=2, started=1)
        self.assertEqual((declined["after"], declined["made"]), ("CX-0003", 1))
        self.assertIn("Review is not valid JSON", state["last_error"]["message"])
        self.assert_nothing_settled(state, path)
        self.assertEqual([item.outcome for item in execution.load(state).step.attempts],
                         [AttemptOutcome.INVALID_FINAL_OUTPUT] * 2)
        # A Resume is an ordinary call, held to the budget: the standard profile gives a review step two calls.
        resumed = self.go(task_id)
        self.assertEqual((resumed["last_error"]["kind"], len(self.reviewers())), ("budget_exhausted", 2))
        self.assert_nothing_settled(resumed, path)

    def test_an_incomplete_approval_after_a_continuation_is_still_refused(self):
        task_id, path = self.judged_task()
        self.cli.plan({}, {"reply": ""}, {"reply": review(complete=False)})
        state = self.go(task_id)
        self.assert_review_declined(state, "limit_reached", reviewers=2, started=1)
        self.assertIn("Incomplete review", state["last_error"]["message"])
        self.assert_nothing_settled(state, path)


class DeclinedTests(ReviewCase):
    def test_without_the_option_an_unusable_review_pauses_as_before(self):
        for name, section in (("no section", None), ("adaptive off", {"enabled": True, "profile": "standard"}),
                              ("checkpoint resume off", {**ADAPTIVE, "continuation": {"checkpoint_resume": False}})):
            with self.subTest(name):
                self.configure(execution=section)
                self.cli.calls.clear()
                task_id, path = self.task()
                self.cli.plan({}, {"reply": PROSE})
                state = self.go(task_id)
                self.assert_not_continued(state, "invalid_review")
                self.assertTrue(state["last_error"]["message"].startswith("Review is not valid JSON"))
                if section is None:
                    self.assertNotIn(execution.KEY, state)
                    self.assertEqual(self.reviewers()[0]["pid_file"].parent, path / "runs" / "iteration-001")

    def test_quota_timeout_errors_and_a_turn_limit_are_never_followed(self):
        for plan, kind in (({"quota": True}, "quota"), ({"timeout": True}, "timeout"), ({"error": True}, "agent_error"),
                           ({"max_turns": True}, "max_turns"), ({"interrupt": True}, "interrupted"),
                           ({"denied": True}, "system_error")):
            with self.subTest(kind):
                self.cli.calls.clear()
                task_id, _ = self.task(developer="codex", reviewer="claude")
                self.cli.plan({}, plan)
                self.assert_not_continued(self.go(task_id, auto_resume=True), kind)

    def test_insufficient_budgets_prevent_the_launch(self):
        for name, section, budget in (
                ("phase calls", adaptive(phase={"reviewer": {"provider_calls": 1}}),
                 {"scope": "phase", "unit": "provider_calls", "limit": 1, "used": 1, "unknown": 0, "requested": 1}),
                ("task calls", adaptive(task={"provider_calls": 2}),
                 {"scope": "task", "unit": "provider_calls", "limit": 2, "used": 2, "unknown": 0, "requested": 1})):
            with self.subTest(name):
                self.configure(execution=section)
                self.cli.calls.clear()
                task_id, path = self.task()
                self.cli.plan({}, {"reply": PROSE})
                declined = self.assert_review_declined(self.go(task_id), "budget")
                self.assertEqual(declined["budget"], budget)
                self.assertFalse(self.folder(path, "review", 2).exists())

    def test_too_few_seconds_left_prevent_the_launch_and_fewer_lower_the_timeout(self):
        for advance, timeouts in ((2960, None), (1000, [2700, 2000])):
            with self.subTest(advance=advance):
                self.configure(execution=adaptive(phase={"reviewer": {"seconds": 3000}}))
                self.cli.calls.clear()
                task_id, _ = self.task()
                clock = Elapsed()
                self.cli.plan({}, {"reply": PROSE, "after_start": clock.advance(advance)}, {"reply": APPROVE})
                with patch("patchrondo.core.time", clock):
                    state = self.go(task_id)
                if timeouts is None:
                    declined = self.assert_review_declined(state, "budget")
                    self.assertEqual(declined["budget"], {"scope": "phase", "unit": "seconds", "limit": 3000,
                                                          "used": 2960, "unknown": 0, "requested": 60})
                    self.cli.plans.clear()
                else:
                    self.assertEqual(state["status"], "done", state.get("last_error"))
                    self.assertEqual([call["timeout"] for call in self.reviewers()], timeouts)
                    self.assertEqual([item.timeout_seconds for item in execution.load(state).step.attempts], timeouts)

    def test_a_call_no_group_held_is_not_followed_as_on_native_windows(self):
        task_id, _ = self.task()
        self.cli.plan({}, {"reply": PROSE, "tree": process.CHILD})
        declined = self.assert_review_declined(self.go(task_id), "process_tree_unverified")
        self.assertEqual(declined["process_tree"], process.CHILD)

    def test_agents_run_without_a_job_object_on_native_windows(self):
        """Decision D4 rests on what `process.execute` reports for an agent's call (no `tree`): a real child, no CLI."""
        launch = process.Launch()
        result = process.execute([sys.executable, "-c", "pass"], Path.cwd(), timeout=60, launch=launch)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(launch.process_tree, process.CHILD if os.name == "nt" else process.GROUP)

    def test_a_reviewer_that_changed_the_files_is_not_followed(self):
        task_id, path = self.task()
        self.cli.plan({}, {"reply": PROSE, "edit": True})
        state = self.go(task_id)
        declined = self.assert_review_declined(state, "workspace_changed")
        self.assertEqual(declined["workspace"], "changed_by_call")
        first = execution.load(state).step.last
        self.assertIsNotNone(first.workspace_after)
        self.assertNotEqual(first.workspace_before, first.workspace_after)  # kept for whoever checks it
        self.assertTrue((Path(state["worktree"]) / "reviewer-was-here.txt").exists())  # nothing was undone
        # A Resume finds the tests about other files: they run again before any review.
        resumed = self.go(task_id)
        self.assertEqual((resumed["last_error"]["kind"], resumed["phase"], len(self.reviewers())),
                         ("stale_tests", "test", 1))

    def test_a_released_call_of_the_review_step_is_never_followed(self):
        task_id, path = self.task()
        self.cli.plan({}, {"crash": "running"})
        with self.assertRaises(Crash):
            self.go(task_id)
        self.assertEqual(self.go(task_id)["last_error"]["kind"], "attempt_uncertain")
        (self.folder(path, "review", 1) / "reviewer.active-process.json").unlink()  # the person checked
        release_attempt(self.home, task_id, "CX-0002", confirm=True)
        self.cli.plan({"reply": PROSE})
        declined = self.assert_review_declined(self.go(task_id), "released_attempt", reviewers=2)
        self.assertEqual(declined["after"], "CX-0003")


class CrashTests(ReviewCase):
    def test_a_crash_during_the_continuation_never_starts_a_review_twice(self):
        task_id, path = self.task()
        self.cli.plan({}, {"reply": PROSE}, {"crash": "running"})
        with self.assertRaises(Crash):
            self.go(task_id)
        for _ in range(2):  # nothing is started while the continuation may still run, however often it is resumed
            state = self.go(task_id)
            self.assertEqual((state["last_error"]["kind"], len(self.reviewers())), ("attempt_uncertain", 2))
        last = execution.load(state).step.last
        self.assertEqual((last.attempt_id, last.continuation, last.ended), ("CX-0003", ContinuationMode.CHECKPOINT_RESUME,
                                                                           False))
        self.assertEqual(len(self.developers()), 1)
        self.assert_nothing_settled(state, path)

    def test_a_crash_while_the_continuation_is_saved_never_starts_it(self):
        task_id, path = self.task()
        saving = core._save

        def save(target, state):
            if any(entry["event"] == "continuation_started" for entry in state["history"]):
                raise Crash()
            saving(target, state)

        self.cli.plan({}, {"reply": PROSE})
        with patch("patchrondo.core._save", side_effect=save), self.assertRaises(Crash):
            self.go(task_id)
        saved = self.saved(path)
        self.assertNotIn("continuation_started", self.events(saved))
        self.assertEqual(len(self.reviewers()), 1)
        first = execution.load(saved).step.last
        self.assertEqual(first.attempt_id, "CX-0002")
        self.assertFalse(self.folder(path, "review", 2).exists())
        if not first.ended:  # its end was not saved: what it did is not known, so nothing is started
            self.assertIn(self.go(task_id)["last_error"]["kind"], ("attempt_reconciled", "attempt_uncertain"))
            self.assertEqual(len(self.reviewers()), 1)
            return
        # Saved by the check before the continuation, which the lost run never started. Step 09: the Resume carries
        # that continuation out (a new, independent review with its brief), once, never as an ordinary call instead.
        self.cli.plan({"reply": APPROVE})
        state = self.go(task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        self.assertEqual([(item.attempt_id, item.continuation) for item in execution.load(state).step.attempts],
                         [("CX-0002", ContinuationMode.NONE), ("CX-0003", ContinuationMode.CHECKPOINT_RESUME)])
        started = [entry for entry in state["history"] if entry["event"] == "continuation_started"]
        self.assertEqual([(entry["after"], entry.get("resumed")) for entry in started], [("CX-0002", True)])


class CompatibilityTests(ReviewCase):
    def test_each_step_has_its_own_continuation(self):
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 41}, {}, {"reply": PROSE}, {"reply": APPROVE})
        state = self.go(task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        self.assertEqual([call["role"] for call in self.cli.calls], ["developer", "developer", "reviewer", "reviewer"])
        started = [entry for entry in state["history"] if entry["event"] == "continuation_started"]
        self.assertEqual([(entry["phase"], entry["attempt"], entry["after"]) for entry in started],
                         [("develop", "CX-0002", "CX-0001"), ("review", "CX-0004", "CX-0003")])
        self.assertEqual(self.events(state).count("tests_started"), 1)
        # The reviewer's brief is its own: never the developer's instructions.
        brief = self.review_prompt(path, 2)[self.review_prompt(path, 2).index(BRIEF):]
        self.assertNotIn("IMPLEMENTER", brief)
        self.assertNotIn("Continue this step from the files", brief)


class ControllerTests(ReviewCase):
    """`core._next_call` for the reviewer, on a task paused after an unusable review nothing followed."""

    def setUp(self):
        super().setUp()
        self.task_id, self.path = self.task()
        self.cli.plan({}, {"reply": PROSE, "tree": process.CHILD})
        self.paused = self.go(self.task_id)
        self.workspace = Path(self.paused["worktree"])
        self.failure = AgentFailure("Review is not valid JSON", "invalid_review", provider="codex")
        # As `_attempt` records it from the call's `Launch` (Step 09: its whole tree confirmed ended, nothing left).
        self.held = {"attempt": "CX-0002", "tree": process.GROUP, "refused": False, "ended": True, "left": False}

    def ask(self, state=None, role="reviewer"):
        state = copy.deepcopy(self.paused) if state is None else state
        return state, core._next_call(self.path, state, self.workspace, self.held, self.failure, role)

    def test_a_brief_is_available_after_an_unusable_answer(self):
        state, (decided, text) = self.ask()
        self.assertTrue(decided.allowed)
        self.assertEqual((decided.trigger, decided.call.turn_limit, decided.call.timeout_seconds), ("CX-0002", None, 2700))
        self.assertTrue(text.startswith(BRIEF))
        self.assertIn("INDEPENDENT REVIEWER, READ ONLY", text)
        self.assertEqual(state["history"], self.paused["history"])  # deciding writes no event

    def test_files_changed_since_the_call_are_never_reviewed_again_automatically(self):
        (self.workspace / "feature.txt").write_text("changed after the review", encoding="utf-8")
        state = copy.deepcopy(self.paused)
        with self.assertRaises(AgentFailure) as raised:
            self.ask(state)
        self.assertEqual((raised.exception.kind, raised.exception.__cause__), ("invalid_review", self.failure))
        self.assertEqual((state["history"][-1]["reason"], state["history"][-1]["workspace"]),
                         ("workspace_changed", "changed_after_call"))

    def test_a_developer_brief_is_never_the_reviewers(self):
        state = copy.deepcopy(self.paused)
        with self.assertRaises(AgentFailure) as raised:
            self.ask(state, role="developer")  # the step is the reviewer's: nothing is continued for the developer
        self.assertIs(raised.exception, self.failure)
        self.assertEqual(state["history"], self.paused["history"])
        with self.assertRaises(continuation.Unavailable) as refused:
            continuation.prepare(self.path, self.paused, self.workspace, task_id=self.task_id, iteration=1,
                                 role="developer")
        self.assertEqual(refused.exception.reason, continuation.Reason.PHASE_MISMATCH)
        brief = continuation.prepare(self.path, self.paused, self.workspace, task_id=self.task_id, iteration=1,
                                     role="reviewer")
        with self.assertRaises(continuation.Unavailable):
            brief.section(task_id=self.task_id, iteration=1, role="developer")
        self.assertEqual(brief.section(task_id=self.task_id, iteration=1, role="reviewer"), brief.text)
        self.assertEqual(brief.previous_outcome, "INVALID_FINAL_OUTPUT")

    def test_no_brief_follows_a_review_that_was_read(self):
        task_id, _ = create_task(self.home, title="Feature", description="Create feature.txt",
                                 acceptance=["Tests pass", "[manual] The colours look right"],
                                 developer="claude", reviewer="codex")
        self.cli.plan({}, {"reply": APPROVE})
        state = self.go(task_id)
        self.assertEqual(state["phase"], "validate")
        state = copy.deepcopy(state)
        state["phase"] = "review"  # as if it were still at the review: the answer it gave is what refuses the brief
        with self.assertRaises(continuation.Unavailable) as refused:
            continuation.prepare(self.home / "tasks" / task_id, state, Path(state["worktree"]), task_id=task_id,
                                 iteration=1, role="reviewer")
        self.assertEqual(refused.exception.reason, continuation.Reason.ANSWER_RECEIVED)


FINGERPRINT_A, FINGERPRINT_B = "a" * 64, "b" * 64
KINDS = {AttemptOutcome.INVALID_FINAL_OUTPUT: "invalid_review", AttemptOutcome.TURN_LIMIT_REACHED: "max_turns",
         AttemptOutcome.QUOTA_EXHAUSTED: "quota", AttemptOutcome.TIMEOUT: "timeout",
         AttemptOutcome.INTERRUPTED: "interrupted", AttemptOutcome.PROVIDER_ERROR: "agent_error",
         AttemptOutcome.ENVIRONMENT_BLOCKED: "configuration", AttemptOutcome.PERMISSION_DENIED: "system_error",
         AttemptOutcome.COMPLETED: None}


def reviewed(record, outcome=AttemptOutcome.INVALID_FINAL_OUTPUT, kind=KINDS, termination=Termination.EXITED,
             before=FINGERPRINT_A, after=FINGERPRINT_A, elapsed=100, **options):
    """`record` with one more call of its review step, ended as asked."""
    record, item = record.new_attempt(started_at="2026-11-09T10:00:00+00:00", workspace_before=before, **options)
    turns = Usage.reported(Unit.CLAUDE_TURNS, 5) if item.provider == "claude" else item.turns
    ended = item.end(outcome, ended_at="2026-11-09T10:05:00+00:00", elapsed_seconds=elapsed, termination=termination,
                     failure_kind=KINDS[outcome] if kind is KINDS else kind, workspace_after=after, turns=turns)
    return record.update(ended)


def review_step(provider="codex", **options):
    return stepped(phase=Phase.REVIEW, provider=provider, **options)


class DecisionTests(unittest.TestCase):
    """`execution.next_call` for a review step, on records made by hand: pure, deterministic."""

    def test_an_unusable_answer_of_either_provider_is_followed(self):
        for provider, turns in (("codex", None), ("claude", 40)):
            for kind in ("empty_output", "invalid_output", "invalid_review"):
                with self.subTest(provider=provider, kind=kind):
                    decided = execution.next_call(reviewed(review_step(provider), kind=kind))
                    self.assertTrue(decided.allowed)
                    self.assertEqual((decided.trigger, decided.call.turn_limit, decided.call.timeout_seconds),
                                     ("CX-0001", turns, 2700))

    def test_no_other_ending_is_followed_and_nothing_is_said(self):
        for outcome in KINDS:
            if outcome is AttemptOutcome.INVALID_FINAL_OUTPUT:
                continue
            termination = Termination.STOPPED if outcome in (AttemptOutcome.TIMEOUT, AttemptOutcome.INTERRUPTED) \
                else Termination.EXITED
            with self.subTest(outcome):
                decided = execution.next_call(reviewed(review_step("claude"), outcome=outcome, termination=termination))
                self.assertEqual((decided.declined, decided.considered), (Declined.NOT_AN_UNUSABLE_ANSWER, False))
        self.assertEqual(execution.next_call(review_step()).declined, Declined.NOT_AN_UNUSABLE_ANSWER)  # no call yet

    def test_each_condition_is_its_own_reason(self):
        plain = {"enabled": True, "profile": "standard"}
        small = CallLimits(None, Source.NOT_APPLICABLE, 2000, Source.PROFILE)
        cases = (
            (reviewed(review_step(policy=None)), Declined.POLICY_UNBOUND),
            (reviewed(review_step(policy=plain)), Declined.ADAPTIVE_OFF),
            (reviewed(review_step(policy={**ADAPTIVE, "continuation": {"checkpoint_resume": False}})),
             Declined.CHECKPOINT_RESUME_OFF),
            (reviewed(review_step(), termination=Termination.UNVERIFIED), Declined.NOT_SETTLED),
            (reviewed(review_step(), after=FINGERPRINT_B), Declined.WORKSPACE_CHANGED),
            (reviewed(review_step(), after=None), Declined.WORKSPACE_CHANGED),
            (reviewed(review_step(), before=None, after=None), Declined.WORKSPACE_CHANGED),
            (reviewed(reviewed(review_step(step={Unit.PROVIDER_CALLS: 3, Unit.SECONDS: 9000}),
                               continuation=ContinuationMode.CHECKPOINT_RESUME, call=small)), Declined.LIMIT_REACHED),
            (reviewed(review_step(step={Unit.PROVIDER_CALLS: 1, Unit.SECONDS: 9000})), Declined.BUDGET),
            (reviewed(review_step(task={Unit.PROVIDER_CALLS: 60, Unit.SECONDS: 2750}), elapsed=2700), Declined.BUDGET),
        )
        for record, reason in cases:
            with self.subTest(reason):
                decided = execution.next_call(record)
                self.assertEqual((decided.declined, decided.allowed), (reason, False))
                self.assertEqual(decided.considered, reason not in execution.NOT_OPTED_IN)

    def test_a_released_call_of_the_step_refuses_every_later_one(self):
        record = review_step(step={Unit.PROVIDER_CALLS: 3, Unit.SECONDS: 9000})
        record, item = record.new_attempt(started_at="2026-11-09T10:00:00+00:00")
        record = record.release(item.attempt_id, ended_at="2026-11-09T11:00:00+00:00")
        self.assertEqual(execution.next_call(reviewed(record)).declined, Declined.RELEASED_ATTEMPT)

    def test_the_budget_refusal_names_its_line(self):
        decided = execution.next_call(reviewed(review_step(step={Unit.PROVIDER_CALLS: 1, Unit.SECONDS: 9000})))
        self.assertEqual((decided.refusal.scope, decided.refusal.unit), (Scope.PHASE, Unit.PROVIDER_CALLS))


if __name__ == "__main__":
    unittest.main()
