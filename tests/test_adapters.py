import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from patchrondo.providers import OfficialCLI, AgentFailure, _claude_text
from patchrondo.process import Result, safe_env


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

    def test_claude_structured_output_and_errors(self):
        self.assertEqual(_claude_text('{"structured_output":{"verdict":"APPROVED"}}'), '{"verdict": "APPROVED"}')
        with self.assertRaises(AgentFailure):
            _claude_text('{"is_error":true,"result":"usage limit"}')


if __name__ == "__main__":
    unittest.main()
