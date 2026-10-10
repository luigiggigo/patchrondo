"""Reliability driver checks with real subprocesses and exclusively synthetic CLIs."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "reliability_e2e", Path(__file__).resolve().parents[1] / "tools" / "reliability_e2e.py")
reliability = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reliability)


class ReliabilityTests(unittest.TestCase):
    def scenario(self, scenario):
        for developer, reviewer in reliability.PAIRINGS:
            with self.subTest(developer=developer, reviewer=reviewer), tempfile.TemporaryDirectory() as folder:
                result = reliability.run_scenario(Path(folder).resolve(), scenario, developer, reviewer)
                self.assertEqual(result["status"], "passed", result)
                self.assertEqual(result["functional_cases_passed"], 6)
                self.assertEqual(result["changed_files"], 3)

    def test_review_feedback_drives_second_iteration(self):
        self.scenario("feedback")

    def test_developer_quota_then_resume(self):
        self.scenario("quota-develop")

    def test_reviewer_quota_then_resume_without_repeating_development(self):
        self.scenario("quota-review")

    def test_multiple_modules_and_json_entry_point(self):
        self.scenario("multi-file")

    @unittest.skipUnless(os.name == "posix", "POSIX signals and PID liveness checks")
    def test_interrupt_cleans_provider_and_resumes_review(self):
        self.scenario("interrupt")

    @unittest.skipUnless(os.name == "posix", "POSIX signals and PID liveness checks")
    def test_crash_requires_unlock_and_resumes_checkpoint(self):
        self.scenario("crash")

    def test_missing_feedback_is_detected(self):
        # Remove the review text at the synthetic CLI boundary. The second
        # developer must reject a prompt that lost the actionable feedback.
        original = reliability.SYNTHETIC_CLI
        broken = original.replace("prompt = sys.stdin.read()", "prompt = sys.stdin.read().replace(control['feedback'], '')")
        with tempfile.TemporaryDirectory() as folder, patch.object(reliability, "SYNTHETIC_CLI", broken):
            result = reliability.run_scenario(Path(folder).resolve(), "feedback", "claude", "codex")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(next(check["passed"] for check in result["checks"]
                              if check["name"] == "review feedback delivered before correction"))

    def test_functional_regression_is_detected_even_with_approval(self):
        broken = dict(reliability.IMPLEMENTATION)
        broken["greetings.py"] = "def greet_many(names):\n    return []\n"
        with tempfile.TemporaryDirectory() as folder, patch.object(reliability, "IMPLEMENTATION", broken):
            result = reliability.run_scenario(Path(folder).resolve(), "multi-file", "codex", "claude")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["functional_cases_passed"], 0)
        self.assertFalse(next(check["passed"] for check in result["checks"]
                              if check["name"] == "completed with passing tests and approval"))


if __name__ == "__main__":
    unittest.main()
