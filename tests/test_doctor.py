"""CLI availability and login checks: no model call, no credential access, same output as before."""
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from patchrondo import doctor
from patchrondo.cli import main
from patchrondo.process import Result


def fake_cli(installed=("claude", "codex"), auth=None, versions=None):
    """Patches for shutil.which and execute describing which CLIs exist and what they answer."""
    auth = auth or {}
    calls = []

    def execute(argv, cwd, **kwargs):
        calls.append(argv)
        if argv[1:] == ["--version"]:
            return Result(0, (versions or {}).get(argv[0], f"{argv[0]} 1.2.3\nextra line"), "")
        return auth.get(argv[0], Result(0, json.dumps({"authMethod": "claude.ai"}) if argv[0] == "claude" else "Logged in", ""))

    which = patch("patchrondo.doctor.shutil.which", side_effect=lambda name: f"/bin/{name}" if name in installed else None)
    return which, patch("patchrondo.doctor.execute", side_effect=execute), calls


class DoctorTests(unittest.TestCase):
    def check(self, **options):
        which, execute, calls = fake_cli(**options)
        with which, execute:
            return doctor.check_all(Path.cwd()), calls

    def test_only_status_commands_are_run(self):
        status, calls = self.check()
        self.assertEqual(calls, [["claude", "--version"], ["claude", "auth", "status"], ["codex", "--version"], ["codex", "login", "status"]])
        self.assertEqual((status["claude"]["installed"], status["claude"]["authenticated"], status["claude"]["auth_method"],
                          status["claude"]["version"], status["claude"]["warning"], status["claude"]["hint"]),
                         (True, True, "claude.ai", "claude 1.2.3", None, None))
        self.assertEqual((status["codex"]["authenticated"], status["codex"]["path"]), (True, "/bin/codex"))

    def test_missing_cli_is_reported_with_how_to_install_it_and_nothing_is_run_for_it(self):
        status, calls = self.check(installed=("codex",))
        self.assertEqual((status["claude"]["installed"], status["claude"]["authenticated"], status["claude"]["path"]), (False, None, None))
        self.assertIn("claude auth login", status["claude"]["hint"])
        self.assertTrue(all(call[0] == "codex" for call in calls))

    def test_login_problems_and_api_billing_are_flagged(self):
        status, _ = self.check(auth={"claude": Result(0, json.dumps({"authMethod": "api_key"}), ""), "codex": Result(1, "", "Not logged in")})
        self.assertEqual((status["claude"]["authenticated"], status["claude"]["auth_method"]), (True, "api_key"))
        self.assertIn("API billing", status["claude"]["warning"])
        self.assertEqual(status["codex"]["authenticated"], False)
        self.assertIn("codex login", status["codex"]["hint"])
        status, _ = self.check(auth={"claude": Result(0, "not json", ""), "codex": Result(124, "", "", timed_out=True)})
        self.assertEqual(status["claude"]["auth_method"], "unknown")
        self.assertIsNone(status["codex"]["authenticated"])  # a timeout is not an answer

    def test_cli_that_cannot_be_started_is_not_called_ready(self):
        with patch("patchrondo.doctor.shutil.which", return_value="/bin/claude"), \
                patch("patchrondo.doctor.execute", side_effect=OSError("not executable")):
            status = doctor.check("claude", Path.cwd())
        self.assertEqual((status["installed"], status["authenticated"]), (True, None))
        self.assertIn("could not be started", status["warning"])

    def test_doctor_command_prints_the_same_lines_as_before(self):
        def run(**options):
            which, execute, _ = fake_cli(**options)
            with which, execute, patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(main(["doctor"]), 0)
            return out.getvalue().splitlines()

        self.assertEqual(run(), ["claude: installed · authenticated · method claude.ai", "codex: installed · auth OK"])
        self.assertEqual(run(installed=()), ["claude: NOT INSTALLED", "codex: NOT INSTALLED"])
        self.assertEqual(run(auth={"claude": Result(0, json.dumps({"authMethod": "api_key"}), ""), "codex": Result(1, "", "")}),
                         ["claude: installed · authenticated · method api_key", "  Warning: check whether API billing is being used",
                          "codex: installed · auth NOT CONFIRMED"])
        self.assertEqual(run(auth={"claude": Result(1, "", "")})[0], "claude: installed · auth NOT CONFIRMED")


if __name__ == "__main__":
    unittest.main()
