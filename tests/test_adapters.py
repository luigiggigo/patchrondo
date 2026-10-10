from datetime import datetime, timedelta, timezone
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from patchrondo.providers import OfficialCLI, AgentFailure, _claude_text
from patchrondo.process import Result, safe_env

OBSERVED = datetime(2026, 10, 10, 14, 0, tzinfo=timezone.utc)


class AdapterTests(unittest.TestCase):
    def test_claude_developer_is_restricted_and_never_bypasses(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            with patch("patchrondo.providers.execute", return_value=Result(0, json.dumps({"result": "done"}), "")) as execute:
                out = OfficialCLI(timeout=10).invoke("claude", "developer", "task", path, path / "runs")
                argv = execute.call_args.args[0]
                self.assertEqual(out.text, "done")
                self.assertIn("--restricted", argv)
                self.assertIn("--tools", argv)
                self.assertIn("--permission-mode", argv)
                self.assertNotIn("--dangerously-skip-permissions", argv)
                self.assertNotIn("Bash", argv)

    def test_codex_reviewer_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            def mock_run(argv, cwd, **kwargs):
                (path / "runs" / "reviewer.last-message.txt").write_text('{"verdict":"APPROVED"}')
                return Result(0, "", "")
            with patch("patchrondo.providers.execute", side_effect=mock_run) as execute:
                reply = OfficialCLI().invoke("codex", "reviewer", "review", path, path / "runs")
                argv = execute.call_args.args[0]
                self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
                self.assertEqual(argv[argv.index("--ask-for-approval") + 1], "never")
                self.assertLess(argv.index("--ask-for-approval"), argv.index("exec"))
                self.assertEqual(reply.text, '{"verdict":"APPROVED"}')

    def test_codex_resume_rejects_stale_reply(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            runs = path / "runs"
            runs.mkdir()
            reply = runs / "reviewer.last-message.txt"
            reply.write_text('{"verdict":"APPROVED","summary":"old","issues":[]}')
            with patch("patchrondo.providers.execute", return_value=Result(0, "event stream", "")):
                with self.assertRaises(AgentFailure) as caught:
                    OfficialCLI().invoke("codex", "reviewer", "review", path, runs)
            self.assertEqual(caught.exception.kind, "empty_output")
            self.assertFalse(reply.exists())

    def test_no_shell_for_api_key_stripping(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "billing-key", "OPENAI_API_KEY": "billing-key", "GITHUB_TOKEN": "secret"}):
            env = safe_env()
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("GITHUB_TOKEN", env)

    def failure(self, provider, result):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            adapter = OfficialCLI(clock=lambda: OBSERVED)
            with patch("patchrondo.providers.execute", return_value=result):
                with self.assertRaises(AgentFailure) as caught:
                    adapter.invoke(provider, "developer", "task", path, path / "runs")
        return caught.exception

    def test_quota_on_stdout_is_found_when_stderr_is_not_empty(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                error = self.failure(provider, Result(1, "Usage limit reached. Retry after 3600 seconds.",
                                                      "Error: request failed"))
                self.assertEqual((error.kind, error.provider, error.retry_after_seconds, error.retry_at),
                                 ("quota", provider, 3600, OBSERVED + timedelta(seconds=3600)))
                # Both streams stay visible in the saved diagnostic.
                self.assertIn("Error: request failed", str(error))
                self.assertIn("Usage limit reached", str(error))

    def test_reset_stated_on_the_other_stream_is_kept(self):
        reset = OBSERVED + timedelta(hours=4)
        cases = {
            "quota on stderr, reset on stdout": Result(1, "Limit resets at 2026-10-10T18:00:00Z", "Usage limit reached"),
            "quota on stdout, reset on stderr": Result(1, "Usage limit reached", "Limit resets at 2026-10-10T18:00:00Z"),
            # Claude can exit with status 0 and report the failure in its JSON result.
            "is_error result, reset on stderr": Result(0, json.dumps({"is_error": True, "result": "Usage limit reached"}),
                                                       "Limit resets at 2026-10-10T18:00:00Z"),
        }
        for name, result in cases.items():
            with self.subTest(case=name):
                error = self.failure("claude", result)
                self.assertEqual((error.kind, error.retry_at), ("quota", reset))

    def test_conclusive_stderr_is_not_overridden_by_stdout(self):
        # Agent text on stdout may mention limits; a login failure on stderr must stay a login failure.
        error = self.failure("codex", Result(1, "Implemented the rate limit middleware. Retry after 5 seconds.",
                                             "Not logged in"))
        self.assertEqual((error.kind, error.retry_at), ("authentication", None))
        # A long stderr does not hide the other stream from classification, and the message stays bounded.
        error = self.failure("codex", Result(1, "quota exceeded", "trace line\n" * 2000))
        self.assertEqual(error.kind, "quota")
        self.assertIn("quota exceeded", str(error))
        self.assertLess(len(str(error)), 1400)
        # A single stream keeps the whole diagnostic budget, as before.
        error = self.failure("codex", Result(1, "", "x" * 1000))
        self.assertIn("x" * 1000, str(error))
        self.assertEqual(self.failure("codex", Result(1, "", "")).kind, "agent_error")

    def test_claude_error_result_is_bounded_in_the_diagnostic(self):
        # The message is saved in task state: it must stay an excerpt on this path too.
        body = json.dumps({"is_error": True, "result": "Usage limit reached. " + "x" * 5000})
        error = self.failure("claude", Result(0, body, ""))
        self.assertEqual(error.kind, "quota")
        self.assertTrue(str(error).startswith("Usage limit reached. xxx"))
        self.assertLess(len(str(error)), 1300)

    def test_claude_structured_output_and_errors(self):
        self.assertEqual(_claude_text('{"structured_output":{"verdict":"APPROVED"}}'), '{"verdict": "APPROVED"}')
        with self.assertRaises(AgentFailure):
            _claude_text('{"is_error":true,"result":"usage limit"}')


if __name__ == "__main__":
    unittest.main()
