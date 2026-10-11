"""Live activity through the adapter and the engine, with scripted CLIs only.

The first group feeds a stream to the adapter at its subprocess boundary. The
second runs whole tasks whose provider commands reach a scripted CLI started as
a real process, so activity is observed while that process is still running.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from patchrondo import activity, streams
from patchrondo.core import create_task, initialize, run_task
from patchrondo.process import Result
from patchrondo.providers import AgentFailure, OfficialCLI
from patchrondo.storage import read_json, save_json

from support import ScriptedCLI

OBSERVED = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
APPROVED = {"verdict": "APPROVED", "summary": "ok", "issues": []}
CHANGES = {"verdict": "CHANGES_REQUESTED", "summary": "not yet", "issues": []}


def jsonl(*objects) -> bytes:
    return "".join(json.dumps(item) + "\n" for item in objects).encode("utf-8")


def text(message):
    return {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "PRIVATE-REASONING"},
                                                         {"type": "text", "text": message}]}}


def said(message, item="m"):
    return {"type": "item.completed", "item": {"id": item, "type": "agent_message", "text": message}}


class Recorder:
    def __init__(self):
        self.events = []

    def emit(self, kind, /, **fields):
        self.events.append({"type": kind, **{name: value for name, value in fields.items() if value is not None}})

    def codes(self):
        return [event.get("code") for event in self.events if event.get("status") == "notice"]


class StreamingAdapterTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        self.runs = self.path / "runs"
        self.journal = Recorder()
        self.calls = []

    def cli(self, *steps):
        """`steps` are (stdout bytes, result, last message for Codex) per call; the bytes reach the observer first."""
        pending = list(steps)

        def run(argv, cwd, **kwargs):
            self.calls.append((argv, kwargs))
            stdout, result, last = pending.pop(0)
            if isinstance(result, BaseException):
                if "on_stdout" in kwargs:
                    kwargs["on_stdout"](stdout)
                raise result
            if "on_stdout" in kwargs:
                for index in range(0, len(stdout), 7):  # fragments that cut lines and characters
                    kwargs["on_stdout"](stdout[index:index + 7])
            if last is not None and argv[0] == "codex":
                Path(argv[argv.index("--output-last-message") + 1]).write_text(last, encoding="utf-8")
            return result

        return patch("patchrondo.providers.execute", side_effect=run)

    def invoke(self, provider, role, *steps):
        with self.cli(*steps):
            return OfficialCLI(timeout=30, clock=lambda: OBSERVED).invoke(provider, role, "prompt", self.path, self.runs,
                                                                          journal=self.journal)

    def failure(self, provider, role, *steps):
        with self.assertRaises(AgentFailure) as caught:
            self.invoke(provider, role, *steps)
        return caught.exception

    def test_claude_is_asked_to_stream_with_the_same_safety_options(self):
        finals = {"developer": {"type": "result", "is_error": False, "result": "handoff"},
                  "reviewer": {"type": "result", "is_error": False, "structured_output": APPROVED}}
        for role, final in finals.items():
            with self.subTest(role=role):
                self.invoke("claude", role, (jsonl(text("Working"), final), Result(0, "", ""), None))
                argv, kwargs = self.calls[-1]
                self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
                self.assertIn("--verbose", argv)
                self.assertNotIn("--include-partial-messages", argv)  # one line per token, thinking deltas included
                self.assertTrue(callable(kwargs["on_stdout"]))
                self.assertNotIn("on_stderr", kwargs)
                # Everything that restricts the agent is as before.
                tools = "Read,Glob,Grep,Edit,Write" if role == "developer" else "Read,Glob,Grep"
                for option, value in (("--permission-mode", "dontAsk"), ("--tools", tools), ("--allowedTools", tools),
                                      ("--disallowedTools", "mcp__*"), ("--max-turns", "12")):
                    self.assertEqual(argv[argv.index(option) + 1], value)
                self.assertIn("--restricted", argv)
                self.assertNotIn("--dangerously-skip-permissions", argv)
                self.assertEqual("--json-schema" in argv, role == "reviewer")

    def test_codex_is_asked_to_stream_with_the_same_sandbox_and_result_files(self):
        for role, sandbox in (("developer", "workspace-write"), ("reviewer", "read-only")):
            with self.subTest(role=role):
                self.invoke("codex", role, (jsonl(said("Working")), Result(0, "", ""), json.dumps(APPROVED)))
                argv, kwargs = self.calls[-1]
                self.assertEqual(argv[:5], ["codex", "--ask-for-approval", "never", "exec", "--json"])
                self.assertEqual(argv[argv.index("--sandbox") + 1], sandbox)
                self.assertEqual(argv[argv.index("--output-last-message") + 1], str(self.runs / f"{role}.last-message.txt"))
                self.assertIn("--ephemeral", argv)
                self.assertEqual("--output-schema" in argv, role == "reviewer")
                self.assertEqual(argv[-1], "-")
                self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", argv)
                self.assertTrue(callable(kwargs["on_stdout"]))

    def test_the_answer_is_the_final_result_and_the_stream_is_only_recorded(self):
        stream = jsonl({"type": "system", "subtype": "init", "model": "m"}, text("I will write the file"),
                       {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "result": "## Changes\n\nDone — è fatto."})
        reply = self.invoke("claude", "developer", (stream, Result(0, "", ""), None))
        self.assertEqual(reply.text, "## Changes\n\nDone — è fatto.")
        self.assertEqual([(event["type"], event.get("status")) for event in self.journal.events],
                         [("status", "session"), ("message", None), ("status", "result")])
        self.assertEqual((self.runs / "developer.reply.md").read_text(encoding="utf-8"), reply.text)
        log = (self.runs / "developer.stdout.log").read_text(encoding="utf-8")
        self.assertIn("I will write the file", log)
        self.assertNotIn("PRIVATE-REASONING", log)  # the saved transcript has no internal reasoning either
        self.assertNotIn("PRIVATE-REASONING", json.dumps(self.journal.events))

    def test_a_reviewer_is_never_approved_by_what_it_says_while_it_works(self):
        chatter = (text("Verdict: APPROVED"), text(json.dumps(APPROVED)),
                   {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t", "name": "StructuredOutput", "input": APPROVED}]}})
        # The stream says APPROVED three times; the validated final result says otherwise.
        reply = self.invoke("claude", "reviewer", (jsonl(*chatter, {"type": "result", "is_error": False, "structured_output": CHANGES}),
                                                   Result(0, "", ""), None))
        self.assertEqual(json.loads(reply.text)["verdict"], "CHANGES_REQUESTED")
        self.assertNotIn("APPROVED", json.dumps(self.journal.events))
        # No final result at all: the step fails, however often the stream said APPROVED.
        self.assertEqual(self.failure("claude", "reviewer", (jsonl(*chatter), Result(0, "", ""), None)).kind, "empty_output")
        # A result without an answer is not completed from the stream either.
        empty = {"type": "result", "subtype": "error_max_turns", "is_error": False}
        self.assertEqual(self.failure("claude", "reviewer", (jsonl(*chatter, empty), Result(0, "", ""), None)).kind, "empty_output")
        # Codex: the answer is the last-message file, not a message in the stream.
        said_approved = jsonl(said("APPROVED"), said(json.dumps(APPROVED), "final"))
        reply = self.invoke("codex", "reviewer", (said_approved, Result(0, "", ""), json.dumps(CHANGES)))
        self.assertEqual(json.loads(reply.text)["verdict"], "CHANGES_REQUESTED")
        self.assertEqual(self.failure("codex", "reviewer", (said_approved, Result(0, "", ""), None)).kind, "empty_output")
        self.assertNotIn("APPROVED", json.dumps(self.journal.events))

    def test_failures_reported_in_the_stream_keep_their_classification(self):
        limit = "Usage limit reached. Retry after 3600 seconds."
        cases = {
            "claude error result, exit 1": ("claude", jsonl(text("Implemented the login check"), {"type": "result", "is_error": True, "result": limit}), Result(1, "", "")),
            "claude error result, exit 0": ("claude", jsonl(text("Working"), {"type": "result", "is_error": True, "result": limit}), Result(0, "", "")),
            "codex error events": ("codex", jsonl(said("Working on the unauthorized handler"), {"type": "error", "message": limit},
                                                  {"type": "turn.failed", "error": {"message": limit}}), Result(1, "", "")),
            "codex error events, exit 0, no answer": ("codex", jsonl({"type": "turn.failed", "error": {"message": limit}}), Result(0, "", "")),
        }
        for name, (provider, stream, result) in cases.items():
            with self.subTest(case=name):
                error = self.failure(provider, "developer", (stream, result, None))
                self.assertEqual((error.kind, error.provider, error.retry_after_seconds), ("quota", provider, 3600))
                self.assertEqual(error.retry_at.isoformat(), "2026-10-11T10:00:00+00:00")
                self.assertIn("Usage limit reached", str(error))
                self.assertNotIn("PRIVATE-REASONING", str(error))
                self.assertLess(len(str(error)), 1400)
        # A login failure wins over usage-limit wording, also when the two arrive on different channels.
        mixed = jsonl({"type": "error", "message": "Usage limit data unavailable"})
        error = self.failure("codex", "developer", (mixed, Result(1, "", "Not logged in"), None))
        self.assertEqual((error.kind, error.retry_at), ("authentication", None))
        error = self.failure("claude", "developer", (jsonl({"type": "result", "is_error": True, "result": "Not logged in · Please run /login"}),
                                                     Result(1, "", ""), None))
        self.assertEqual(error.kind, "authentication")
        # What the CLI reports decides before agent text in the stream can.
        talk = jsonl(said("I added a rate limit to the API"), {"type": "error", "message": "Not logged in"})
        self.assertEqual(self.failure("codex", "developer", (talk, Result(1, "", ""), None)).kind, "authentication")
        # A Codex reset in local time is still read from the error event.
        local = jsonl({"type": "error", "message": "You've hit your usage limit. Try again at 3:45 PM."})
        error = self.failure("codex", "developer", (local, Result(1, "", ""), None))
        self.assertEqual(error.kind, "quota")
        self.assertIsNotNone(error.retry_at)
        # An error item that did not end the run decides nothing: without anything else it stays a plain failure.
        soft = jsonl({"type": "item.completed", "item": {"id": "e", "type": "error", "message": "skipped a hook"}})
        self.assertEqual(self.failure("codex", "developer", (soft, Result(1, "", "Segmentation fault"), None)).kind, "agent_error")

    def test_a_timeout_keeps_its_kind_and_what_was_seen(self):
        error = self.failure("claude", "developer", (jsonl(text("Reading files")), Result(124, "", "", timed_out=True), None))
        self.assertEqual((error.kind, error.provider), ("timeout", "claude"))
        self.assertEqual([event["type"] for event in self.journal.events], ["message"])
        self.assertIn("Reading files", (self.runs / "developer.stdout.log").read_text(encoding="utf-8"))

    def test_an_interrupt_passes_through_and_what_was_seen_stays_recorded(self):
        with self.assertRaises(KeyboardInterrupt):
            self.invoke("codex", "developer", (jsonl(said("Halfway")) + b'{"type": "item.comp', KeyboardInterrupt(), None))
        self.assertEqual([event["text"] for event in self.journal.events if event["type"] == "message"], ["Halfway"])

    def test_a_cli_that_refuses_the_streaming_options_runs_as_before_and_says_so(self):
        refusals = {"claude": "error: unknown option '--verbose'\n", "codex": "error: unexpected argument '--json' found\n"}
        for provider, stderr in refusals.items():
            with self.subTest(provider=provider):
                self.calls.clear()
                self.journal.events.clear()
                legacy = (json.dumps({"result": "handoff"}).encode(), Result(0, json.dumps({"result": "handoff"}), ""), "handoff")
                reply = self.invoke(provider, "developer", (b"", Result(2, "", stderr), None), legacy)
                self.assertEqual(reply.text, "handoff")
                first, second = (argv for argv, _ in self.calls)
                self.assertTrue("--verbose" in first or "--json" in first)
                self.assertEqual([option for option in ("--verbose", "stream-json", "--json") if option in second], [])
                if provider == "claude":
                    self.assertEqual(second[second.index("--output-format") + 1], "json")
                self.assertNotIn("on_stdout", self.calls[1][1])  # exactly the call made before streaming existed
                self.assertEqual(self.journal.codes(), ["stream_unsupported"])
                self.assertIn(stderr.strip(), self.journal.events[0]["text"])
        # The same words after the CLI has started working are a failure of the step, not a reason to run it again.
        self.calls.clear()
        error = self.failure("claude", "developer", (jsonl(text("Working")), Result(1, "", refusals["claude"]), None))
        self.assertEqual((error.kind, len(self.calls)), ("agent_error", 1))
        # And an ordinary failure that names no streaming option is never retried.
        self.calls.clear()
        self.assertEqual(self.failure("codex", "developer", (b"", Result(1, "", "Not logged in"), None)).kind, "authentication")
        self.assertEqual(len(self.calls), 1)

    def test_the_switch_restores_the_previous_command_lines(self):
        with patch.dict(os.environ, {streams.SWITCH: "0"}):
            reply = self.invoke("claude", "developer", (b"", Result(0, json.dumps({"result": "handoff"}), ""), None))
            self.invoke("codex", "reviewer", (b"", Result(0, "", ""), json.dumps(APPROVED)))
        self.assertEqual(reply.text, "handoff")
        claude, codex = (argv for argv, _ in self.calls)
        self.assertEqual(claude[:5], ["claude", "-p", "--output-format", "json", "--restricted"])
        self.assertEqual(codex[:5], ["codex", "--ask-for-approval", "never", "exec", "--sandbox"])
        self.assertTrue(all("on_stdout" not in kwargs for _, kwargs in self.calls))
        self.assertEqual(self.journal.codes(), ["stream_off", "stream_off"])

    def test_a_cli_that_prints_no_activity_still_gives_its_result_and_nothing_is_made_up(self):
        body = json.dumps({"result": "handoff from a single object"})
        reply = self.invoke("claude", "developer", (body.encode(), Result(0, body, ""), None))
        self.assertEqual(reply.text, "handoff from a single object")
        self.assertEqual(self.journal.events, [{"type": "status", "status": "notice", "code": "stream_empty",
                                                "text": "The CLI printed no activity for this step; only its result is available."}])
        self.journal.events.clear()
        self.invoke("codex", "developer", (b"plain progress text\n", Result(0, "plain progress text\n", ""), "handoff"))
        self.assertEqual(self.journal.codes(), ["stream_empty"])

    def test_a_step_works_without_a_journal(self):
        stream = jsonl(text("Working"), {"type": "result", "is_error": False, "result": "handoff"})
        with self.cli((stream, Result(0, "", ""), None)):
            reply = OfficialCLI().invoke("claude", "developer", "prompt", self.path, self.runs)
        self.assertEqual(reply.text, "handoff")


class EngineCase(unittest.TestCase):
    """A task whose provider commands reach the scripted CLI, started as a real process."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        root = Path(folder.name).resolve()
        self.repo, self.home = root / "repo", root / "state"
        self.repo.mkdir()
        for args in (["init", "-q"], ["config", "user.name", "T"], ["config", "user.email", "t@example.invalid"],
                     ["config", "commit.gpgsign", "false"]):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        (self.repo / "README.md").write_text("# Test\n", encoding="utf-8", newline="\n")
        for args in (["add", "."], ["commit", "-qm", "base"]):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        initialize(self.home, self.repo)
        cfg = read_json(self.home / "config.json")
        cfg["tests"] = {"enabled": True, "trust_acknowledged": True, "commands": [[
            sys.executable, "-c", "from pathlib import Path; assert Path('feature.txt').read_text() == 'good'"]]}
        cfg["workflow"].update(max_iterations=1, agent_timeout_seconds=60)
        cfg["rag"]["enabled"] = False
        save_json(self.home / "config.json", cfg)
        self.cli = ScriptedCLI(root / "fixture")
        guard = patch("patchrondo.providers.execute", side_effect=self.cli.execute)
        guard.start()
        self.addCleanup(guard.stop)

    def task(self, developer="claude", reviewer="codex"):
        task_id, _ = create_task(self.home, title="Feature", description="Create feature.txt", acceptance=["Tests pass"],
                                 developer=developer, reviewer=reviewer)
        return task_id, self.home / "tasks" / task_id

    def events(self, path):
        file = path / activity.JOURNAL
        if not file.exists():
            return []
        return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()[1:]]

    def wait(self, condition, timeout=30):
        deadline = time.monotonic() + timeout
        while not condition():
            self.assertLess(time.monotonic(), deadline, "the expected activity did not appear")
            time.sleep(0.01)


class EngineActivityTests(EngineCase):
    def test_activity_is_visible_while_the_agent_process_is_still_running(self):
        for developer, reviewer in (("claude", "codex"), ("codex", "claude")):
            with self.subTest(developer=developer):
                self.cli.script(developer={"says": ["Reading the task", "Writing the file"], "hold": ["working"]})
                task_id, path = self.task(developer, reviewer)
                results = []
                runner = threading.Thread(target=lambda: results.append(run_task(self.home, task_id)))
                started = time.monotonic()
                runner.start()
                try:
                    self.wait(lambda: any(event.get("text") == "Writing the file" for event in self.events(path)))
                    seen_after = time.monotonic() - started
                    # The agent process has not ended: it is held, its marker exists, the engine is mid-step.
                    self.assertTrue(self.cli.reached("developer", "working") or runner.is_alive())
                    self.assertTrue(runner.is_alive())
                    self.assertTrue((path / "runs" / "iteration-001" / "developer.active-process.json").exists())
                    state = read_json(path / "state.json")
                    self.assertEqual((state["status"], state["phase"], state["handoff"]), ("running", "develop", None))
                    self.assertFalse((path / "handoff.md").exists())
                    live = self.events(path)
                    self.assertEqual([(event["type"], event.get("status")) for event in live[:2]], [("status", "started"), ("status", "session")])
                    self.assertTrue(all(event["role"] == "developer" and event["provider"] == developer for event in live))
                    self.assertNotIn("completion", [event["type"] for event in live])
                    self.assertLess(seen_after, 20)
                finally:
                    self.cli.release("developer", "working")
                    runner.join(60)
                    for marker in self.cli.folder.glob("developer.working.*"):
                        marker.unlink()
                self.assertEqual(results[0]["status"], "done", results[0].get("last_error"))
                ended = self.events(path)
                self.assertEqual([event["seq"] for event in ended], list(range(1, len(ended) + 1)))
                roles = [(event["role"], event["outcome"]) for event in ended if event["type"] == "completion"]
                self.assertEqual(roles, [("developer", "completed"), ("tests", "completed"), ("reviewer", "completed")])
                self.assertEqual([event["path"] for event in ended if event["type"] == "file"], ["feature.txt"])
                self.assertEqual([(event["status"], event.get("exit_code")) for event in ended if event["type"] == "test"],
                                 [("started", None), ("passed", 0)])
                self.assertNotIn("PRIVATE-REASONING", (path / activity.JOURNAL).read_text(encoding="utf-8"))
                for log in path.glob("runs/iteration-*/*.stdout.log"):
                    self.assertNotIn("PRIVATE-REASONING", log.read_text(encoding="utf-8"))

    def test_an_event_reaches_the_journal_within_a_second_of_being_printed(self):
        self.cli.script(developer={"says": ["one", "two", "three", "four"], "pause": 0.4})
        task_id, path = self.task()
        seen = {}
        done = threading.Event()

        def watch():
            while not done.is_set():
                for event in self.events(path):
                    if event["type"] == "message" and event["role"] == "developer":
                        seen.setdefault(event["text"], time.monotonic())
                time.sleep(0.01)

        watcher = threading.Thread(target=watch)
        watcher.start()
        try:
            state = run_task(self.home, task_id)
        finally:
            done.set()
            watcher.join()
        self.assertEqual(state["status"], "done", state.get("last_error"))
        times = [seen[name] for name in ("one", "two", "three", "four")]
        gaps = [later - earlier for earlier, later in zip(times, times[1:])]
        # Printed 0.4 s apart and seen 0.4 s apart: nothing waited for the end of the process.
        self.assertTrue(all(0.2 < gap < 1.4 for gap in gaps), gaps)

    def test_a_reviewer_is_not_approved_before_its_answer_is_validated(self):
        self.cli.script(reviewer={"says": ["Verdict: APPROVED", json.dumps(APPROVED)], "hold": ["answer"], "verdict": "CHANGES_REQUESTED"})
        task_id, path = self.task()
        results = []
        runner = threading.Thread(target=lambda: results.append(run_task(self.home, task_id)))
        runner.start()
        try:
            self.wait(lambda: self.cli.reached("reviewer", "answer")
                      and sum(event["role"] == "reviewer" and event["type"] == "message" for event in self.events(path)) == 2)
            state = read_json(path / "state.json")
            # The reviewer has said APPROVED twice and is still running: nothing is approved.
            self.assertEqual((state["status"], state["phase"], state["review"]), ("running", "review", None))
            self.assertNotIn("review_completed", [entry["event"] for entry in state["history"]])
            journal = (path / activity.JOURNAL).read_text(encoding="utf-8")
            self.assertNotIn("APPROVED", journal)
            self.assertEqual(journal.count('"withheld":true'), 2)
        finally:
            self.cli.release("reviewer", "answer")
            runner.join(60)
        state = results[0]
        self.assertEqual((state["status"], state["review"]["verdict"]), ("blocked", "CHANGES_REQUESTED"))
        self.assertNotIn("task_completed", [entry["event"] for entry in state["history"]])
        self.assertEqual([event["outcome"] for event in self.events(path) if event["type"] == "completion"][-1], "completed")

    def test_a_reviewer_without_a_final_answer_is_not_approved(self):
        for reviewer in ("claude", "codex"):
            with self.subTest(reviewer=reviewer):
                self.cli.script(reviewer={"says": [json.dumps(APPROVED)], "no_result": True})
                task_id, path = self.task("codex" if reviewer == "claude" else "claude", reviewer)
                state = run_task(self.home, task_id)
                self.assertEqual((state["status"], state["phase"], state["review"], state["last_error"]["kind"]),
                                 ("paused", "review", None, "empty_output"))
                self.assertEqual([event["outcome"] for event in self.events(path) if event["type"] == "completion"][-1], "failed")

    def test_the_task_ends_exactly_as_without_live_activity(self):
        def outcome(switch):
            self.cli.script()
            with patch.dict(os.environ, {streams.SWITCH: switch}):
                task_id, path = self.task()
                state = run_task(self.home, task_id)
            calls = self.cli.calls()[-2:]
            return ({key: state[key] for key in ("status", "phase", "iteration", "review", "last_error", "recovery")},
                    [entry["event"] for entry in state["history"]], [test["status"] for test in state["tests"]],
                    (path / "handoff.md").read_text(encoding="utf-8"),
                    re.sub(r"\d{4}-\d\d-\d\dT[0-9:.+]+", "TIME", (path / "report.md").read_text(encoding="utf-8").replace(task_id, "T-x")),
                    sorted(file.name for file in (path / "runs" / "iteration-001").iterdir()),
                    [call["live"] for call in calls])

        live, legacy = outcome("1"), outcome("0")
        self.assertEqual((live[-1], legacy[-1]), ([True, True], [False, False]))
        self.assertEqual(live[:-1], legacy[:-1])
        self.assertEqual((live[0]["status"], live[0]["review"]["verdict"]), ("done", "APPROVED"))
        self.assertIn("Created `feature.txt`", live[3])

    def test_retry_and_resume_append_new_attempts_and_keep_the_old_ones(self):
        self.cli.script(developer={"quota_once": True})
        task_id, path = self.task("codex", "claude")
        paused = run_task(self.home, task_id)
        self.assertEqual((paused["status"], paused["last_error"]["kind"], paused["last_error"]["retry_after_seconds"]),
                         ("paused", "quota", 3600))
        before = (path / activity.JOURNAL).read_bytes()
        first = self.events(path)
        self.assertEqual([(event["type"], event.get("outcome") or event.get("kind")) for event in first[-2:]],
                         [("error", "quota"), ("completion", "failed")])
        done = run_task(self.home, task_id)
        self.assertEqual(done["status"], "done", done.get("last_error"))
        self.assertTrue((path / activity.JOURNAL).read_bytes().startswith(before))  # appended to, never rewritten
        events = self.events(path)
        attempts = list(dict.fromkeys(event["attempt"] for event in events))
        self.assertEqual([next(event["role"] for event in events if event["attempt"] == attempt) for attempt in attempts],
                         ["developer", "developer", "tests", "reviewer"])
        self.assertEqual([event["seq"] for event in events], list(range(1, len(events) + 1)))
        self.assertEqual([call["role"] for call in self.cli.calls()], ["developer", "developer", "reviewer"])

    def test_quota_recovery_plans_the_same_retry_with_live_activity(self):
        self.cli.script(reviewer={"quota": True})
        clock = lambda: OBSERVED  # noqa: E731
        task_id, path = self.task()
        state = run_task(self.home, task_id, auto_resume=True, clock=clock)
        plan = state["recovery"]
        self.assertEqual((state["status"], state["phase"], plan["status"], plan["provider"], plan["schedule_source"]),
                         ("paused", "review", "scheduled", "codex", "provider_reset"))
        self.assertEqual(plan["resume_at"], "2026-10-11T10:00:30+00:00")  # the stated reset plus the safety margin
        self.assertFalse((path / ".run.lock").exists())
        # Not due: a second run makes no provider call, with or without a journal.
        calls = len(self.cli.calls())
        again = run_task(self.home, task_id, auto_resume=True, clock=clock)
        self.assertEqual((again["recovery"], len(self.cli.calls())), (plan, calls))

    def test_a_crash_in_the_middle_of_a_line_pauses_the_task_and_keeps_the_journal_readable(self):
        self.cli.script(developer={"crash_once": True, "says": ["Before the crash"]})
        task_id, path = self.task()
        paused = run_task(self.home, task_id)
        self.assertEqual((paused["status"], paused["phase"], paused["last_error"]["kind"]), ("paused", "develop", "agent_error"))
        events = self.events(path)
        messages = [event["text"] for event in events if event["type"] == "message"]
        self.assertEqual(messages, ["Before the crash"])  # half a line is not a message
        self.assertEqual(events[-1]["outcome"], "failed")
        self.assertFalse(list(path.glob("runs/iteration-*/*.active-process.json")))
        done = run_task(self.home, task_id)
        self.assertEqual(done["status"], "done", done.get("last_error"))
        page = activity.read(path)
        self.assertEqual((page["skipped"], page["reset"]), (0, False))
        self.assertEqual([event["seq"] for event in page["events"]], list(range(1, len(page["events"]) + 1)))

    def test_an_agent_timeout_is_recorded_and_pauses_as_before(self):
        cfg = read_json(self.home / "config.json")
        cfg["workflow"]["agent_timeout_seconds"] = 2
        save_json(self.home / "config.json", cfg)
        self.cli.script(developer={"says": ["Still working"], "hold": ["working"]})  # never released
        task_id, path = self.task()
        paused = run_task(self.home, task_id)
        self.assertEqual((paused["status"], paused["last_error"]["kind"]), ("paused", "timeout"))
        events = self.events(path)
        self.assertIn("Still working", [event.get("text") for event in events])
        self.assertEqual((events[-1]["type"], events[-1]["outcome"], events[-1]["kind"]), ("completion", "timeout", "timeout"))
        self.assertFalse((path / ".run.lock").exists())

    def test_a_cli_without_streaming_runs_and_the_journal_says_why(self):
        self.cli.script(developer={"refuse_stream": True}, reviewer={"refuse_stream": True})
        task_id, path = self.task()
        state = run_task(self.home, task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        events = self.events(path)
        notices = [event for event in events if event.get("code") == "stream_unsupported"]
        self.assertEqual([event["role"] for event in notices], ["developer", "reviewer"])
        self.assertEqual([event["type"] for event in events if event["role"] == "developer"], ["status", "status", "completion"])
        self.assertEqual([call["live"] for call in self.cli.calls()], [True, False, True, False])

    def test_an_unwritable_journal_does_not_fail_the_task(self):
        task_id, path = self.task()
        (path / activity.JOURNAL).mkdir()  # nothing can be appended to a folder
        state = run_task(self.home, task_id)
        self.assertEqual((state["status"], state["review"]["verdict"]), ("done", "APPROVED"))
        self.assertEqual(activity.read(path)["events"], [])

    def test_a_step_interrupted_by_the_user_is_recorded_as_interrupted(self):
        task_id, path = self.task()

        class Interrupted:
            def invoke(self, provider, role, prompt, workspace, run_dir):
                raise KeyboardInterrupt

        state = run_task(self.home, task_id, adapter=Interrupted())
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "interrupted"))
        self.assertEqual([(event["type"], event.get("outcome")) for event in self.events(path)],
                         [("status", None), ("error", None), ("completion", "interrupted")])


if __name__ == "__main__":
    unittest.main()
