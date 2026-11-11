"""Native session resume in a run (Phase 2.5, Step 10): which continuation is made, what is kept, what is refused.

The provider CLI is the fake of `test_attempts`, reached only through
`patchrondo.providers.execute`, made to report a session in the typed fields
its real output has (Claude's `session_id` in `system`/`init` and `result`,
Codex's `thread_id` in `thread.started`) and to answer a `--resume` /
`exec resume` call. The installed CLI's capability is the help saved in
`tests/fixtures/cli`, given through `OfficialCLI.resume_capability`. Every
run is simulated: none of this is evidence that a real CLI resumes a
session, which is the end-of-phase validation with real providers.
"""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_attempts  # noqa: E402
import test_auto_continuation  # noqa: E402
from test_attempts import Crash  # noqa: E402
from test_sessions import capability  # noqa: E402

from patchrondo import core, execution, providers, reconciliation, runfiles, sessions  # noqa: E402
from patchrondo.execution import AttemptOutcome, ContinuationMode, Termination, Unit  # noqa: E402
from patchrondo.storage import read_json, save_json  # noqa: E402

NATIVE = {**test_auto_continuation.ADAPTIVE, "continuation": {"native_resume": "probe_then_use"}}
BRIEF = test_auto_continuation.BRIEF


class SessionCLI(test_attempts.FakeCLI):
    """The fake CLI, reporting a session per call: a new one, or the one a resume names (plan `session` overrides)."""

    def __init__(self):
        super().__init__()
        self.made = 0

    def new(self) -> str:
        self.made += 1
        return f"5e55{self.made:04x}-0000-4000-8000-000000000000"

    @staticmethod
    def resumed(argv) -> str | None:
        if "--resume" in argv:
            return argv[argv.index("--resume") + 1]
        return argv[-2] if "resume" in argv[:6] else None

    def reported(self, argv, plan) -> tuple:
        if "sessions" in plan:
            return plan["sessions"]
        session = plan["session"] if "session" in plan else self.resumed(argv) or self.new()
        return (session, session)

    def claude(self, argv, cwd, stdin, reviewer, plan):
        if plan.get("lost") and "--resume" in argv:
            return [], 1, f"No conversation found with session ID: {argv[argv.index('--resume') + 1]}\n"
        lines, code, stderr = super().claude(argv, cwd, stdin, reviewer, plan)
        first, last = self.reported(argv, plan)
        for line in lines:
            if line.get("subtype") == "init" and first is not None:
                line["session_id"] = first
            if line.get("type") == "result" and last is not None:
                line["session_id"] = last
        return lines, code, stderr

    def codex(self, argv, cwd, stdin, reviewer, plan):
        lines, code, stderr = super().codex(argv, cwd, stdin, reviewer, plan)
        first, _ = self.reported(argv, plan)
        for line in lines:
            if line.get("type") == "thread.started":
                line.pop("thread_id", None)
                if first is not None:
                    line["thread_id"] = first
        return lines, code, stderr


class NativeCase(test_auto_continuation.ContinuationCase):
    EXECUTION = NATIVE
    CAPABILITY = {"claude": "claude-2.1.295", "codex": "codex-0.161.0"}

    def setUp(self):
        super().setUp()
        self.cli = SessionCLI()
        guard = patch("patchrondo.providers.execute", side_effect=self.cli)
        guard.start()
        self.addCleanup(guard.stop)
        self.asked = []

        def resume_capability(adapter, provider):
            self.asked.append(provider)
            found = self.CAPABILITY.get(provider)
            return capability(found) if found else sessions.unknown(provider)

        guard = patch.object(providers.OfficialCLI, "resume_capability", resume_capability)
        guard.start()
        self.addCleanup(guard.stop)

    def attempts(self, state: dict) -> list:
        return list(execution.load(state).step.attempts)

    def launch(self, path: Path, ordinal: int, phase: str = "develop") -> dict:
        return read_json(self.folder(path, phase, ordinal) / reconciliation.MARKER)

    def assert_private(self, path: Path, state: dict, *ids: str) -> None:
        """No raw session ID in the state, the history, the journal or a log; only launch records keep them."""
        shown = [json.dumps(state)] + [file.read_text(encoding="utf-8", errors="replace")
                                       for file in path.rglob("*") if file.is_file() and file.name != reconciliation.MARKER
                                       and file.suffix in (".log", ".md", ".jsonl", ".json", ".txt")]
        for session in ids:
            self.assertFalse([text for text in shown if session in text], session)

    def setting(self, role: str, provider: str, **values) -> None:
        cfg = read_json(self.home / "config.json")
        cfg["agent_settings"] = {role: {provider: values}}
        save_json(self.home / "config.json", cfg)


class NativeResumeTests(NativeCase):
    def test_a_turn_limit_is_continued_in_the_same_session_with_the_steps_settings(self):
        self.configure(tests=False)
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 41})
        state = self.go(task_id)
        self.assertEqual((state["status"], state["last_error"]["kind"], state["phase"]), ("paused", "tests_disabled", "test"))
        first, second = self.cli.calls
        session = first_id = SessionCLI.resumed(second["argv"])
        self.assertEqual(session, "5e550001-0000-4000-8000-000000000000")
        # The continuation is the step's command with the session added: same tools, permissions and checks, its
        # smaller turn limit only (80 - 41 = 39 left), never --continue, --fork-session or a new session ID.
        expected = first["argv"][:2] + ["--resume", session] + first["argv"][2:]
        expected[expected.index("--max-turns") + 1] = "39"
        self.assertEqual(second["argv"], expected)
        one, two = self.attempts(state)
        self.assertEqual((one.continuation, two.continuation), (ContinuationMode.NONE, ContinuationMode.NATIVE_RESUME))
        self.assertRegex(one.session, sessions.REFERENCE)
        self.assertEqual(two.session, one.session)
        self.assertEqual(two.resume.to_dict(), {"requested": "native_resume", "selected": "native_resume",
                                                "capability": "verified", "cli_version": "2.1.295", "durable": True,
                                                "reason": None})
        self.assertEqual((two.outcome, two.termination, two.turn_limit), (AttemptOutcome.COMPLETED, Termination.EXITED, 39))
        # Charged once per call, as any continuation.
        record = execution.load(state)
        self.assertEqual((record.step.budget.line(Unit.PROVIDER_CALLS).used, record.budget.line(Unit.PROVIDER_CALLS).used,
                          record.step.budget.line(Unit.CLAUDE_TURNS).used), (2, 2, 44))
        started = [entry for entry in state["history"] if entry["event"] == "continuation_started"]
        self.assertEqual([(entry["mode"], entry["resume"]) for entry in started],
                         [("native_resume", {"requested": "native_resume", "selected": "native_resume", "reason": None,
                                             "capability": "verified"})])
        self.assertIn(BRIEF, self.prompt(path, 2))
        # The ID is kept in the first call's launch record only, with what made it; nowhere a person or a bundle reads.
        kept = self.launch(path, 1)["session"]
        self.assertEqual((kept["id"], kept["ref"], kept["cli_version"], kept["attempt"]),
                         (first_id, one.session, "2.1.295", "CX-0001"))
        self.assertNotIn("session", self.launch(path, 2))  # the resumed call keeps no second copy
        command = (self.folder(path, "develop", 2) / "developer.command.txt").read_text(encoding="utf-8")
        self.assertIn(sessions.MASK, command.split("--resume", 1)[1].split()[0])  # quoted on POSIX, not on Windows
        self.assertNotIn(first_id, command)
        self.assert_private(path, state, first_id)
        self.assertIn(sessions.MASK, (self.folder(path, "develop", 1) / "developer.stdout.log").read_text(encoding="utf-8"))
        self.assertEqual(self.asked, ["claude", "claude"])  # the record of the first call, the choice of the second

    def test_after_a_lost_run_the_same_session_is_resumed_once(self):
        self.configure(tests=False)
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 41})
        real = runfiles.allocate

        def allocate(target, iteration, phase, ordinal):
            if ordinal == 2:
                raise Crash()  # the native resume is saved as STARTING and never gets a folder
            return real(target, iteration, phase, ordinal)

        with patch("patchrondo.core.runfiles.allocate", side_effect=allocate), self.assertRaises(Crash):
            self.go(task_id)
        saved = self.attempts(self.saved(path))
        self.assertEqual([item.continuation for item in saved], [ContinuationMode.NONE, ContinuationMode.NATIVE_RESUME])
        state = self.go(task_id)
        self.assertEqual(state["last_error"]["kind"], "tests_disabled", state.get("last_error"))
        one, lost, third = self.attempts(state)
        self.assertEqual((lost.termination, third.continuation, third.session, third.turn_limit),
                         (Termination.NOT_STARTED, ContinuationMode.NATIVE_RESUME, one.session, 39))
        self.assertEqual(len(self.cli.calls), 2)  # the first call and one resume: never a second copy of either
        self.assertEqual(SessionCLI.resumed(self.cli.calls[1]["argv"]), self.launch(path, 1)["session"]["id"])
        self.assertEqual(execution.load(state).budget.line(Unit.PROVIDER_CALLS).used, 2)

    def test_a_resume_its_cli_does_not_confirm_is_never_taken_for_progress(self):
        for reported, problem in ((None, "reported no session"),
                                  ("5e55ffff-0000-4000-8000-000000000000", "reported another session"),
                                  ("not-a-session", "in a form PatchRondo does not read")):
            with self.subTest(reported=reported):
                self.configure(tests=False)
                task_id, path = self.task()
                self.cli.calls.clear()
                self.cli.plan({"max_turns": True, "turns": 41}, {"session": reported})
                state = self.go(task_id)
                self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "resume_unconfirmed"))
                self.assertIn(problem, state["last_error"]["message"])
                self.assertIn("Its answer was not used", state["last_error"]["message"])
                self.assertNotIn("development_completed", self.events(state))
                two = self.attempts(state)[1]
                self.assertEqual((two.outcome, two.failure_kind, two.continuation),
                                 (AttemptOutcome.UNSUPPORTED_RESUME, "resume_unconfirmed", ContinuationMode.NATIVE_RESUME))
                self.assertEqual(len(self.cli.calls), 2)
                # Both calls were charged: the step's two provider calls of the standard profile are used, so the
                # user's Resume is refused by the budget and nothing is started, natively or otherwise.
                record = execution.load(state)
                self.assertEqual((record.step.budget.line(Unit.PROVIDER_CALLS).used,
                                  record.budget.line(Unit.PROVIDER_CALLS).used), (2, 2))
                again = self.go(task_id)
                self.assertEqual(again["last_error"]["kind"], "budget_exhausted")
                self.assertEqual(len(self.cli.calls), 2)
                self.assertEqual(len(self.attempts(again)), 2)

    def test_a_resume_that_fails_after_its_launch_starts_nothing_else(self):
        for plan, kind in (({"lost": True}, "agent_error"), ({"error": True}, "agent_error"),
                           ({"quota": True}, "quota"), ({"timeout": True}, "timeout"),
                           ({"max_turns": True, "turns": 39}, "max_turns")):
            with self.subTest(plan=plan):
                self.configure(tests=False)
                task_id, path = self.task()
                self.cli.calls.clear()
                self.cli.plan({"max_turns": True, "turns": 41}, plan)
                state = self.go(task_id)
                self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", kind))
                self.assertEqual(len(self.cli.calls), 2)
                self.assertIn("--resume", self.cli.calls[1]["argv"])
                one, two = self.attempts(state)
                self.assertEqual(two.continuation, ContinuationMode.NATIVE_RESUME)
                self.assertNotEqual(two.outcome, AttemptOutcome.COMPLETED)
                self.assertEqual(execution.load(state).budget.line(Unit.PROVIDER_CALLS).used, 2)
                self.assertNotIn("development_completed", self.events(state))

    def test_quota_and_timeouts_are_never_continued_natively_either(self):
        for plan, kind in (({"quota": True}, "quota"), ({"timeout": True}, "timeout")):
            with self.subTest(kind=kind):
                task_id, path = self.task()
                self.cli.calls.clear()
                self.cli.plan(plan)
                state = self.go(task_id)
                self.assert_not_continued(state, kind)


class FallbackTests(NativeCase):
    def checkpoint(self, state: dict, reason: str, *, calls: int = 2) -> execution.AttemptRecord:
        self.assertEqual(state["last_error"]["kind"], "tests_disabled", state.get("last_error"))
        self.assertEqual(len(self.cli.calls), calls)
        self.assertFalse([call for call in self.cli.calls if "--resume" in call["argv"] or "resume" in call["argv"][:6]])
        item = self.attempts(state)[1]
        self.assertEqual((item.continuation, item.resume.selected, item.resume.reason),
                         (ContinuationMode.CHECKPOINT_RESUME, ContinuationMode.CHECKPOINT_RESUME, reason))
        self.assertTrue(item.resume.fallback)
        self.assertIn(BRIEF, self.prompt(Path(self.home / "tasks" / state["id"]), 2))
        started = [entry for entry in state["history"] if entry["event"] == "continuation_started"]
        self.assertEqual([(entry["mode"], entry["resume"]["selected"], entry["resume"]["reason"]) for entry in started],
                         [("checkpoint_resume", "checkpoint_resume", reason)])
        return item

    def run_turn_limit(self, *plans) -> tuple:
        self.configure(tests=False)
        task_id, path = self.task()
        self.cli.calls.clear()
        self.cli.plan({"max_turns": True, "turns": 41}, *plans)
        return self.go(task_id), path

    def test_an_unknown_capability_falls_back_before_anything_starts(self):
        self.CAPABILITY = {}
        state, path = self.run_turn_limit()
        item = self.checkpoint(state, "capability_unknown")
        self.assertEqual((item.resume.capability, item.resume.cli_version), ("unknown", None))
        # The first call's session was kept with an unknown version: no resume could ever match it.
        self.assertIsNone(self.launch(path, 1)["session"]["cli_version"])

    def test_a_call_that_reported_no_trustworthy_session_falls_back(self):
        # Missing, contradicted between its init and result lines, malformed: each in a task of its own.
        for first in ({"session": None}, {"sessions": ("5e55aaaa-0000-4000-8000-000000000000",
                                                       "5e55bbbb-0000-4000-8000-000000000000")},
                      {"session": "SYNTHETIC"}):
            with self.subTest(first=first):
                self.configure(tests=False)
                task_id, path = self.task()
                self.cli.calls.clear()
                self.cli.plans.clear()
                self.cli.plan({"max_turns": True, "turns": 41, **first})
                state = self.go(task_id)
                self.assertIsNone(self.attempts(state)[0].session)
                self.assertNotIn("session", self.launch(path, 1))
                self.checkpoint(state, "no_session")

    def test_settings_that_keep_no_session_fall_back_and_keep_nothing(self):
        self.setting("developer", "claude", session_persistence=False)
        state, path = self.run_turn_limit()
        self.checkpoint(state, "not_durable")
        self.assertIsNone(self.attempts(state)[0].session)
        self.assertNotIn("session", self.launch(path, 1))
        self.assertIn("--no-session-persistence", self.cli.calls[1]["argv"])  # the user's setting, kept as it was

    def test_another_cli_version_than_the_one_that_made_the_session_falls_back(self):
        versions = iter(["claude-2.1.295", "claude-2.1.302"])

        def resume_capability(adapter, provider):
            return capability(next(versions))

        with patch.object(providers.OfficialCLI, "resume_capability", resume_capability):
            state, _ = self.run_turn_limit()
        self.checkpoint(state, "cli_changed")

    def test_a_kept_session_that_does_not_match_its_record_falls_back(self):
        reading = reconciliation.read_marker

        def read_marker(folder):
            found = reading(folder)
            if found and "session" in found:  # another ID put in its place, as a copy from another task would be
                found = {**found, "session": {**found["session"], "id": "5e55cccc-0000-4000-8000-000000000000"}}
            return found

        with patch("patchrondo.core.reconciliation.read_marker", side_effect=read_marker):
            state, _ = self.run_turn_limit()
        self.checkpoint(state, "session_mismatch")

    def test_options_the_resume_form_does_not_list_fall_back_before_the_call(self):
        self.setting("developer", "claude", extra_args=["--frobnicate"], risk_acknowledged=True)
        state, _ = self.run_turn_limit()
        self.checkpoint(state, "options_unsupported")
        self.assertIn("--frobnicate", self.cli.calls[1]["argv"])

    def test_a_real_cli_is_never_resumed_and_its_sessions_are_not_kept(self):
        record = execution.ExecutionRecord.open("T-0123456789ab", {}, policy=NATIVE)
        self.assertTrue(core._captures(record, "claude", live=False))
        self.assertFalse(core._captures(record, "claude", live=True))  # `VALIDATED_PROVIDERS` is empty
        self.assertFalse(core._captures(execution.ExecutionRecord.open("T-0123456789ab", {},
                                                                       policy=test_auto_continuation.ADAPTIVE),
                                        "claude", live=False))
        self.assertFalse(core._captures(execution.ExecutionRecord.open("T-0123456789ab", {}), "claude", live=False))
        self.assertFalse(core.WINDOWS_REAL_CONTINUATION)
        self.assertEqual(sessions.VALIDATED_PROVIDERS, frozenset())


class CodexTests(NativeCase):
    def test_an_ephemeral_codex_session_is_never_resumed_or_kept(self):
        # A Codex call reaches no Claude turn limit: a reviewer's unusable answer is the continuation here.
        task_id, path = self.task(developer="claude", reviewer="codex")
        self.cli.plan({}, {"no_result": True})
        state = self.go(task_id)
        review = [item for item in execution.load(state).step.attempts]
        self.assertEqual([item.continuation for item in review], [ContinuationMode.NONE, ContinuationMode.CHECKPOINT_RESUME])
        self.assertEqual((review[0].session, review[1].resume.reason, review[1].resume.durable),
                         (None, "not_durable", False))
        self.assertNotIn("session", self.launch(path, 1, "review"))
        reviewer_calls = [call["argv"] for call in self.cli.calls if call["role"] == "reviewer"]
        self.assertEqual(len(reviewer_calls), 2)
        for argv in reviewer_calls:
            self.assertIn("--ephemeral", argv)
            self.assertNotIn("resume", argv[:6])
            self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")

    def test_a_durable_codex_session_of_the_observed_versions_is_not_resumed(self):
        self.setting("reviewer", "codex", ephemeral=False)
        task_id, path = self.task(developer="claude", reviewer="codex")
        self.cli.plan({}, {"no_result": True})
        state = self.go(task_id)
        first, second = execution.load(state).step.attempts
        self.assertRegex(first.session, sessions.REFERENCE)  # kept: the session was not ephemeral
        self.assertEqual((second.continuation, second.resume.reason, second.resume.capability),
                         (ContinuationMode.CHECKPOINT_RESUME, "restrictions_unverified", "unverified"))
        self.assertNotIn("--ephemeral", self.cli.calls[-1]["argv"])
        self.assertNotIn("resume", self.cli.calls[-1]["argv"][:6])
        self.assertEqual(self.cli.calls[-1]["argv"][self.cli.calls[-1]["argv"].index("--sandbox") + 1], "read-only")


class ReviewerTests(NativeCase):
    def test_a_claude_reviewer_is_resumed_read_only_and_its_answer_is_still_parsed(self):
        task_id, path = self.task(developer="codex", reviewer="claude")
        self.cli.plan({}, {"invalid": True})
        state = self.go(task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        reviews = [call["argv"] for call in self.cli.calls if call["role"] == "reviewer"]
        self.assertEqual(len(reviews), 2)
        self.assertNotIn("--resume", reviews[0])
        session = SessionCLI.resumed(reviews[1])
        self.assertIsNotNone(session)
        for argv in reviews:
            self.assertEqual(argv[argv.index("--tools") + 1], "Read,Glob,Grep")
            self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read,Glob,Grep")
            self.assertIn("--json-schema", argv)
            self.assertIn("--restricted", argv)
        attempts = execution.load(state).step.attempts
        self.assertEqual([(item.continuation, item.outcome) for item in attempts],
                         [(ContinuationMode.NONE, AttemptOutcome.INVALID_FINAL_OUTPUT),
                          (ContinuationMode.NATIVE_RESUME, AttemptOutcome.COMPLETED)])
        self.assertEqual(attempts[1].session, attempts[0].session)
        self.assertEqual(state["review"]["verdict"], "APPROVED")
        self.assertEqual(state["gate"]["decision"], "PASS")
        self.assert_private(path, state, session)

    def test_a_developer_session_is_never_resumed_as_the_reviewer(self):
        entry = sessions.stored(task_id="T-0123456789ab", attempt_id="CX-0001", provider="claude", role="developer",
                                session_id="5e550001-0000-4000-8000-000000000000",
                                capability=capability("claude-2.1.295"))
        with self.assertRaises(sessions.Mismatch):
            sessions.kept(entry, task_id="T-0123456789ab", attempt_id="CX-0001", provider="claude", role="reviewer",
                          ref=entry["ref"])


class LegacyTests(NativeCase):
    EXECUTION = test_auto_continuation.ADAPTIVE  # adaptive, native resume not asked for: Steps 07-09 as they were

    def test_without_the_request_nothing_is_kept_chosen_or_resumed(self):
        self.configure(tests=False)
        task_id, path = self.task()
        self.cli.plan({"max_turns": True, "turns": 41})
        state = self.go(task_id)
        self.assertEqual(state["last_error"]["kind"], "tests_disabled")
        one, two = self.attempts(state)
        self.assertEqual([(item.continuation, item.session, item.resume, item.to_dict()["version"]) for item in (one, two)],
                         [(ContinuationMode.NONE, None, None, 1), (ContinuationMode.CHECKPOINT_RESUME, None, None, 1)])
        self.assertNotIn("--resume", self.cli.calls[1]["argv"])
        self.assertNotIn("session", self.launch(path, 1))
        started = [entry for entry in state["history"] if entry["event"] == "continuation_started"]
        self.assertNotIn("resume", started[0])
        self.assertEqual(self.asked, [])  # the installed CLI is never asked
        # Logs mask the session for every task, asked for or not.
        self.assert_private(path, state, "5e550001-0000-4000-8000-000000000000")


if __name__ == "__main__":
    unittest.main()
