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

spec = importlib.util.spec_from_file_location(
    "provider_e2e", Path(__file__).resolve().parents[1] / "tools" / "provider_e2e.py")
provider_e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provider_e2e)

READY = {"path": "/synthetic/cli", "version": "0.0-test", "ready": True, "note": "login OK"}
GREETING = 'def greet(name):\n    return f"Hello, {name}!"\n'
APPROVAL = json.dumps({"verdict": "APPROVED", "summary": "Meets the criteria", "issues": []})


def simulated_cli(calls, *, reviewer_edits=False, developer_error=""):
    """Stand in for both CLIs at the adapter's subprocess boundary."""
    def run(argv, cwd, **kwargs):
        calls.append(argv)
        claude = argv[0] == "claude"
        reviewer = "--json-schema" in argv if claude else argv[argv.index("--sandbox") + 1] == "read-only"
        if reviewer:
            if reviewer_edits:
                (Path(cwd) / "greeting.py").write_text("tampered = True\n", encoding="utf-8")
            text = APPROVAL
        elif developer_error:
            return Result(1, "", developer_error)
        else:
            (Path(cwd) / "greeting.py").write_text(GREETING, encoding="utf-8")
            text = "## Changes\n\nAdded greeting.py"
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
        code, out, calls = self.check("--run-fixture-tests")
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertIn("No model calls were made", out)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_no_provider_call_when_a_cli_is_not_ready(self):
        def probe(name):
            return dict(READY) if name == "claude" else {"path": None, "version": "", "ready": False, "note": "NOT INSTALLED"}
        for args in ([], ["--authorize-provider-calls"]):
            code, out, calls = self.check(*args, probe=probe)
            self.assertEqual(code, 1)
            self.assertEqual(calls, [])
            self.assertIn("codex: not on PATH", out)

    def test_both_pairings_pause_at_the_disabled_test_gate(self):
        code, out, calls = self.check("--authorize-provider-calls", "--keep")
        self.assertEqual(code, 0, out)
        self.assertEqual([argv[0] for argv in calls], ["claude", "codex", "codex", "claude"])
        self.assertIn("claude -> codex: PASS", out)
        self.assertIn("codex -> claude: PASS", out)
        self.assertNotIn("FAILED", out)
        tests = read_json(self.root / "state" / "config.json")["tests"]
        self.assertIs(tests["enabled"], False)
        self.assertIs(tests["trust_acknowledged"], False)
        states = [read_json(file) for file in (self.root / "state" / "tasks").glob("T-*/state.json")]
        self.assertEqual([state["last_error"]["kind"] for state in states], ["tests_disabled"] * 2)

    def test_fixture_tests_run_only_with_consent_and_complete_the_task(self):
        code, out, calls = self.check("--authorize-provider-calls", "--run-fixture-tests",
                                      "--pairing", "codex-claude", "--keep")
        self.assertEqual(code, 0, out)
        self.assertEqual([argv[0] for argv in calls], ["codex", "claude"])
        self.assertIn("task completed with passing tests and an approved review", out)
        tests = read_json(self.root / "state" / "config.json")["tests"]
        self.assertIs(tests["enabled"], True)
        self.assertIs(tests["trust_acknowledged"], True)
        state, = [read_json(file) for file in (self.root / "state" / "tasks").glob("T-*/state.json")]
        self.assertEqual(state["status"], "done")
        self.assertEqual([item["status"] for item in state["tests"]], ["passed"])

    def test_reviewer_that_edits_the_worktree_fails_the_check(self):
        code, out, calls = self.check("--authorize-provider-calls", "--pairing", "claude-codex", reviewer_edits=True)
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
