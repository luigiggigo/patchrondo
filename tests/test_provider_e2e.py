"""The real-provider check, exercised with simulated CLIs only: no model calls."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from patchrondo.process import Result
from patchrondo.storage import read_json

from support import outcomes

spec = importlib.util.spec_from_file_location(
    "provider_e2e", Path(__file__).resolve().parents[1] / "tools" / "provider_e2e.py")
provider_e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provider_e2e)

READY = {"path": "/synthetic/cli", "version": "0.0-test", "ready": True, "note": "login OK"}
GREETING = 'def greet(name):\n    return f"Hello, {name}!"\n'
APPROVAL = json.dumps({"verdict": "APPROVED", "summary": "Meets the criteria", "issues": []})


def simulated_cli(calls, *, reviewer_edits=False, developer_error="", silent=False):
    """Stand in for both CLIs at the adapter's subprocess boundary."""
    def run(argv, cwd, **kwargs):
        calls.append(argv)
        claude = argv[0] == "claude"
        live = "on_stdout" in kwargs and not silent

        def say(line):
            kwargs["on_stdout"]((json.dumps(line) + "\n").encode("utf-8"))

        if live:
            # What a CLI prints while it works; the answer still comes from its final result below.
            say({"type": "assistant", "message": {"content": [{"type": "text", "text": "Working"}]}} if claude else
                {"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": "Working"}})
        reviewer = "--json-schema" in argv if claude else argv[argv.index("--sandbox") + 1] == "read-only"
        if reviewer:
            if reviewer_edits:
                (Path(cwd) / "greeting.py").write_text("tampered = True\n", encoding="utf-8")
            text = json.dumps({**json.loads(APPROVAL), "criteria": outcomes(kwargs.get("stdin") or "")})
        elif developer_error:
            return Result(1, "", developer_error)
        else:
            (Path(cwd) / "greeting.py").write_text(GREETING, encoding="utf-8")
            text = "## Changes\n\nAdded greeting.py"
        if claude and live:
            say({"type": "result", "is_error": False, "result": text})  # the last line of a stream is its result
            return Result(0, "", "")
        if claude:
            return Result(0, json.dumps({"result": text}), "")
        Path(argv[argv.index("--output-last-message") + 1]).write_text(text, encoding="utf-8")
        return Result(0, "", "")
    return run


class ProviderCheckTests(unittest.TestCase):
    def check(self, *args, probe=None, **cli):
        """Run the tool in a temporary fixture root; return (exit code, output, calls, root)."""
        calls = []
        out = io.StringIO()
        with patch.object(provider_e2e, "probe", side_effect=probe or (lambda name: dict(READY))), \
             patch.object(provider_e2e.tempfile, "mkdtemp", return_value=str(self.root)), \
             patch("patchrondo.providers.execute", side_effect=simulated_cli(calls, **cli)), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = provider_e2e.main(list(args))
        return code, out.getvalue(), calls

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve() / "fixture"
        self.root.mkdir()

    def test_no_provider_call_without_authorization(self):
        for args, announced in ((["--run-fixture-tests"], "up to 4 provider calls per pairing"),
                                ([], "1 developer call per pairing; the reviewer is called only with --run-fixture-tests")):
            code, out, calls = self.check(*args)
            self.assertEqual(code, 0)
            self.assertEqual(calls, [])
            self.assertIn("No model calls were made", out)
            self.assertIn(announced, out)
            self.assertEqual(list(self.root.iterdir()), [])

    def test_no_provider_call_when_a_cli_is_not_ready(self):
        def probe(name):
            return dict(READY) if name == "claude" else {"path": None, "version": "", "ready": False, "note": "NOT INSTALLED"}
        for args in ([], ["--authorize-provider-calls"]):
            code, out, calls = self.check(*args, probe=probe)
            self.assertEqual(code, 1)
            self.assertEqual(calls, [])
            self.assertIn("codex: not on PATH", out)

    def test_without_the_fixture_tests_only_the_developer_is_called_and_checked(self):
        code, out, calls = self.check("--authorize-provider-calls", "--keep")
        self.assertEqual(code, 0, out)
        # One call per pairing, and each is the developer's: no review schema, no read-only sandbox.
        self.assertEqual([argv[0] for argv in calls], ["claude", "codex"])
        self.assertNotIn("--json-schema", calls[0])
        self.assertNotEqual(calls[1][calls[1].index("--sandbox") + 1], "read-only")
        self.assertIn("claude -> codex: PASS", out)
        self.assertIn("codex -> claude: PASS", out)
        self.assertNotIn("FAILED", out)
        self.assertEqual(out.count("[ok] developer CLI reported its activity while it worked (1 developer events)"), 2)
        self.assertEqual(out.count("[ok] loop paused at the missing test consent, before any review"), 2)
        # What the run did not reach is said, and is never printed as passed.
        for name in ("reviewer call returned a valid review", "reviewer left the worktree unchanged",
                     "reviewer CLI reported its activity while it worked"):
            self.assertEqual(out.count(f"[not exercised] {name}"), 2)
            self.assertNotIn(f"[ok] {name}", out)
        self.assertIn("RESULT: PASS · claude -> codex, codex -> claude · developer side only: "
                      "6 reviewer checks not exercised (add --run-fixture-tests)", out)
        tests = read_json(self.root / "state" / "config.json")["tests"]
        self.assertIs(tests["enabled"], False)
        self.assertIs(tests["trust_acknowledged"], False)
        states = [read_json(file) for file in (self.root / "state" / "tasks").glob("T-*/state.json")]
        self.assertEqual([state["last_error"]["kind"] for state in states], ["tests_disabled"] * 2)
        for state in states:
            self.assertNotIn("review_started", [entry["event"] for entry in state["history"]])

    def test_a_review_without_the_fixture_tests_fails_the_check(self):
        real = provider_e2e.run_task

        def past_the_consent(home, task_id):
            state = real(home, task_id)
            state["history"].append({"event": "review_started"})
            return state

        with patch.object(provider_e2e, "run_task", side_effect=past_the_consent):
            code, out, calls = self.check("--authorize-provider-calls", "--pairing", "claude-codex")
        self.assertEqual(code, 1)
        self.assertIn("[FAILED] loop paused at the missing test consent, before any review", out)
        self.assertIn("RESULT: FAIL", out)

    def test_fixture_tests_run_only_with_consent_and_complete_the_task(self):
        code, out, calls = self.check("--authorize-provider-calls", "--run-fixture-tests",
                                      "--pairing", "codex-claude", "--keep")
        self.assertEqual(code, 0, out)
        self.assertEqual([argv[0] for argv in calls], ["codex", "claude"])
        self.assertIn("task completed with passing tests and an approved review", out)
        for name in ("developer CLI reported its activity while it worked (1 developer events)",
                     "reviewer call returned a valid review (verdict APPROVED)", "reviewer left the worktree unchanged",
                     "reviewer CLI reported its activity while it worked (1 reviewer events)"):
            self.assertIn(f"[ok] {name}", out)
        self.assertNotIn("not exercised", out)
        self.assertIn("RESULT: PASS · codex -> claude\n", out)
        tests = read_json(self.root / "state" / "config.json")["tests"]
        self.assertIs(tests["enabled"], True)
        self.assertIs(tests["trust_acknowledged"], True)
        state, = [read_json(file) for file in (self.root / "state" / "tasks").glob("T-*/state.json")]
        self.assertEqual(state["status"], "done")
        self.assertEqual([item["status"] for item in state["tests"]], ["passed"])

    def test_a_cli_that_reports_no_activity_fails_the_check(self):
        code, out, calls = self.check("--authorize-provider-calls", "--run-fixture-tests", "--pairing", "claude-codex",
                                      silent=True)
        self.assertEqual(code, 1)
        self.assertIn("[FAILED] developer CLI reported its activity while it worked (0 developer events; "
                      "The CLI printed no activity", out)
        self.assertIn("[FAILED] reviewer CLI reported its activity while it worked (0 reviewer events; "
                      "The CLI printed no activity", out)
        self.assertIn("[ok] reviewer call returned a valid review", out)  # the loop itself still worked
        self.assertIn("[ok] task completed with passing tests and an approved review", out)

    def test_a_silent_developer_fails_the_check_without_the_fixture_tests_too(self):
        code, out, calls = self.check("--authorize-provider-calls", "--pairing", "claude-codex", silent=True)
        self.assertEqual((code, [argv[0] for argv in calls]), (1, ["claude"]))
        self.assertIn("[FAILED] developer CLI reported its activity while it worked (0 developer events; "
                      "The CLI printed no activity", out)
        self.assertIn("[not exercised] reviewer CLI reported its activity while it worked", out)

    def test_reviewer_that_edits_the_worktree_fails_the_check(self):
        code, out, calls = self.check("--authorize-provider-calls", "--run-fixture-tests", "--pairing", "claude-codex",
                                      reviewer_edits=True)
        self.assertEqual(code, 1)
        self.assertIn("[FAILED] reviewer left the worktree unchanged", out)
        self.assertIn("stale_tests", out)
        self.assertIn("RESULT: FAIL", out)
        self.assertTrue((self.root / "state").is_dir(), "a failed check must keep its files for diagnosis")

    def test_rejected_arguments_are_reported_with_the_cli_message(self):
        code, out, calls = self.check("--authorize-provider-calls", developer_error="error: unknown option '--restricted'")
        self.assertEqual(code, 1)
        self.assertIn("[FAILED] developer call returned a handoff", out)
        self.assertIn("unknown option '--restricted'", out)
        # An ordinary failure pauses before review but still lets the other pairing run.
        self.assertEqual([argv[0] for argv in calls], ["claude", "codex"])

    def test_quota_failure_stops_before_the_next_pairing(self):
        code, out, calls = self.check("--authorize-provider-calls", developer_error="Usage limit reached; resets at 9pm")
        self.assertEqual(code, 1)
        self.assertEqual([argv[0] for argv in calls], ["claude"])
        self.assertIn("Stopping", out)
        self.assertNotIn("codex -> claude", out.split("Stopping")[1].split("RESULT")[0])


if __name__ == "__main__":
    unittest.main()
