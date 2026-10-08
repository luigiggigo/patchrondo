import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from patchrondo.core import initialize, create_task, run_task, parse_review, config
from patchrondo.providers import AgentReply, AgentFailure
from patchrondo.storage import read_json, save_json


class FakeProvider:
    def __init__(self, *, fail_first=False, dev_replies=None, review_verdicts=None):
        self.calls = []
        self.fail_first = fail_first
        self.dev_replies = list(dev_replies or ["good"])
        self.review_verdicts = list(review_verdicts or ["APPROVED"])

    def invoke(self, provider, role, prompt, workspace, run_dir):
        self.calls.append((provider, role, prompt))
        if self.fail_first:
            self.fail_first = False
            raise AgentFailure("usage limit; resets at 14:00", "quota")
        if role == "developer":
            value = self.dev_replies.pop(0) if self.dev_replies else "good"
            (workspace / "feature.txt").write_text(value, encoding="utf-8")
            return AgentReply(text=f"Changed feature.txt to {value}", provider=provider)
        verdict = self.review_verdicts.pop(0) if self.review_verdicts else "APPROVED"
        return AgentReply(text=json.dumps({"verdict": verdict, "summary": "Checked worktree",
                                           "issues": []}), provider=provider)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        self.home = root / "state"
        self._git("init", "-q", cwd=self.repo)
        self._git("config", "user.name", "Test Runner", cwd=self.repo)
        self._git("config", "user.email", "tester@example.com", cwd=self.repo)
        (self.repo / "README.md").write_text("Initial repo\n", encoding="utf-8")
        self._git("add", "README.md", cwd=self.repo)
        self._git("commit", "-qm", "initial", cwd=self.repo)
        initialize(self.home, self.repo)
        cfg = read_json(self.home / "config.json")
        cfg["tests"] = {"enabled": True, "trust_acknowledged": True,
                        "commands": [[sys.executable, "-c",
                                      "from pathlib import Path; assert Path('feature.txt').read_text() == 'good'"]]}
        cfg["workflow"]["max_iterations"] = 3
        save_json(self.home / "config.json", cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def _git(self, *args, cwd):
        subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)

    def _task(self, dev="claude", rev="codex"):
        return create_task(self.home, title="Implement feature", description="Write feature.txt with 'good'",
                           acceptance=["File exists", "File contains good"], developer=dev, reviewer=rev)

    def test_success_and_isolation(self):
        task_id, workspace = self._task()
        branch = subprocess.check_output(
            ["git", "-C", str(workspace), "branch", "--show-current"], text=True).strip()
        self.assertEqual(branch, f"patchrondo/{task_id}")
        adapter = FakeProvider()
        state = run_task(self.home, task_id, adapter=adapter)
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["phase"], "complete")
        self.assertEqual([x[1] for x in adapter.calls], ["developer", "reviewer"])
        self.assertEqual(state["review"]["verdict"], "APPROVED")
        self.assertEqual(state["tests"][0]["status"], "passed")
        self.assertTrue((workspace / "feature.txt").exists())
        self.assertFalse((self.repo / "feature.txt").exists())
        self.assertIn("Changed files", (self.home / "tasks" / task_id / "report.md").read_text())
        self.assertTrue((self.home / "tasks" / task_id / "handoff.md").exists())

    def test_test_failure_forces_another_iteration_despite_approval(self):
        task_id, _ = self._task("codex", "claude")
        adapter = FakeProvider(dev_replies=["bad", "good"])
        state = run_task(self.home, task_id, adapter=adapter)
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["iteration"], 2)
        self.assertEqual([x[1] for x in adapter.calls], ["developer", "reviewer", "developer", "reviewer"])
        self.assertIn("tests_missing_or_failed", (self.home / "tasks" / task_id / "feedback.md").read_text())

    def test_quota_pause_and_resume(self):
        task_id, _ = self._task()
        first = FakeProvider(fail_first=True)
        paused = run_task(self.home, task_id, adapter=first)
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["phase"], "develop")
        self.assertEqual(paused["last_error"]["kind"], "quota")
        second = FakeProvider()
        done = run_task(self.home, task_id, adapter=second)
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["iteration"], 1)
        self.assertIsNone(done["last_error"])

    def test_resume_at_review_does_not_repeat_development(self):
        task_id, _ = self._task()
        class ReviewFailsOnce(FakeProvider):
            def invoke(self, provider, role, prompt, workspace, run_dir):
                if role == "reviewer" and not getattr(self, "failed_review", False):
                    self.failed_review = True
                    raise AgentFailure("rate limit", "quota")
                return super().invoke(provider, role, prompt, workspace, run_dir)
        adapter = ReviewFailsOnce()
        paused = run_task(self.home, task_id, adapter=adapter)
        self.assertEqual(paused["phase"], "review")
        self.assertEqual(paused["status"], "paused")
        done = run_task(self.home, task_id, adapter=adapter)
        self.assertEqual(done["status"], "done")
        self.assertEqual([x[1] for x in adapter.calls], ["developer", "reviewer"])

    def test_resume_rejects_tests_for_changed_files(self):
        task_id, workspace = self._task()

        class ReviewUnavailable(FakeProvider):
            def invoke(self, provider, role, prompt, workspace, run_dir):
                if role == "reviewer":
                    raise AgentFailure("rate limit", "quota")
                return super().invoke(provider, role, prompt, workspace, run_dir)

        paused = run_task(self.home, task_id, adapter=ReviewUnavailable())
        self.assertEqual(paused["phase"], "review")
        (workspace / "feature.txt").write_text("bad", encoding="utf-8")
        agent = FakeProvider()
        paused = run_task(self.home, task_id, adapter=agent)
        self.assertEqual(paused["last_error"]["kind"], "stale_tests")
        self.assertEqual(paused["phase"], "test")
        self.assertEqual(agent.calls, [])

    def test_reviewer_changes_cannot_pass_test_gate(self):
        task_id, _ = self._task()

        class MutatingReviewer(FakeProvider):
            def invoke(self, provider, role, prompt, workspace, run_dir):
                if role == "reviewer":
                    (workspace / "feature.txt").write_text("bad", encoding="utf-8")
                return super().invoke(provider, role, prompt, workspace, run_dir)

        state = run_task(self.home, task_id, adapter=MutatingReviewer())
        self.assertEqual(state["status"], "paused")
        self.assertEqual(state["phase"], "test")
        self.assertEqual(state["last_error"]["kind"], "stale_tests")

    def test_changed_test_config_invalidates_saved_results(self):
        for disable in (False, True):
            with self.subTest(disable=disable):
                task_id, _ = self._task()

                class ReviewUnavailable(FakeProvider):
                    def invoke(self, provider, role, prompt, workspace, run_dir):
                        if role == "reviewer":
                            raise AgentFailure("rate limit", "quota")
                        return super().invoke(provider, role, prompt, workspace, run_dir)

                run_task(self.home, task_id, adapter=ReviewUnavailable())
                cfg = read_json(self.home / "config.json")
                original = cfg["tests"]["commands"]
                if disable:
                    cfg["tests"]["enabled"] = False
                else:
                    cfg["tests"]["commands"] = [[sys.executable, "-c", "raise SystemExit(1)"]]
                save_json(self.home / "config.json", cfg)
                agent = FakeProvider()
                state = run_task(self.home, task_id, adapter=agent)
                self.assertEqual(state["status"], "paused")
                self.assertEqual(state["last_error"]["kind"], "stale_tests")
                self.assertEqual(agent.calls, [])
                cfg["tests"]["enabled"] = True
                cfg["tests"]["commands"] = original
                save_json(self.home / "config.json", cfg)

    def test_review_rejection_and_max_iterations(self):
        task_id, _ = self._task()
        adapter = FakeProvider(review_verdicts=["CHANGES_REQUESTED"] * 10)
        state = run_task(self.home, task_id, adapter=adapter)
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["iteration"], 3)
        self.assertEqual(state["last_error"], "max_iterations_reached")

    def test_missing_tests_requires_explicit_ack(self):
        cfg = read_json(self.home / "config.json")
        cfg["tests"]["trust_acknowledged"] = False
        save_json(self.home / "config.json", cfg)
        with self.assertRaisesRegex(ValueError, "trust_acknowledged"):
            config(self.home)

    def test_disabled_tests_pause_instead_of_consuming_more_iterations(self):
        cfg = read_json(self.home / "config.json")
        cfg["tests"]["enabled"] = False
        save_json(self.home / "config.json", cfg)
        task_id, _ = self._task()
        agent = FakeProvider()
        paused = run_task(self.home, task_id, adapter=agent)
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["phase"], "test")
        self.assertEqual(paused["iteration"], 1)
        self.assertEqual(len(agent.calls), 2)
        cfg["tests"]["enabled"] = True
        save_json(self.home / "config.json", cfg)
        finished = run_task(self.home, task_id, adapter=agent)
        self.assertEqual(finished["status"], "done")
        self.assertEqual([call[1] for call in agent.calls], ["developer", "reviewer", "reviewer"])

    def test_invalid_review_and_critical_issue_override(self):
        with self.assertRaises(AgentFailure):
            parse_review("APPROVED")
        obj = parse_review(json.dumps({"verdict": "APPROVED", "summary": "ok", "issues": [
            {"severity": "critical", "description": "Secret leak", "path": "server.py"}]}))
        self.assertEqual(obj["verdict"], "CHANGES_REQUESTED")

    def test_review_requires_string_path(self):
        for issue in ({"severity": "low", "description": "issue"},
                      {"severity": "low", "description": "issue", "path": 12}):
            with self.subTest(issue=issue), self.assertRaises(AgentFailure):
                parse_review(json.dumps({"verdict": "APPROVED", "summary": "ok", "issues": [issue]}))

    def test_unhashable_review_values_are_invalid_reviews(self):
        for review in ({"verdict": [], "summary": "ok", "issues": []},
                       {"verdict": "APPROVED", "summary": "ok", "issues": [
                           {"severity": [], "description": "issue", "path": "file.py"}]}):
            with self.subTest(review=review), self.assertRaises(AgentFailure) as caught:
                parse_review(json.dumps(review))
            self.assertEqual(caught.exception.kind, "invalid_review")

    def test_state_inside_repository_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            initialize(self.repo / ".patchrondo", self.repo)
        self.assertFalse((self.repo / ".patchrondo").exists())

    def test_config_rejects_truthy_non_boolean_consent(self):
        for field, value in (("trust_acknowledged", "false"), ("enabled", "true")):
            cfg = read_json(self.home / "config.json")
            cfg["tests"][field] = value
            save_json(self.home / "config.json", cfg)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "boolean"):
                config(self.home)
            cfg["tests"][field] = True
            save_json(self.home / "config.json", cfg)

    def test_config_rejects_boolean_iteration_limit(self):
        cfg = read_json(self.home / "config.json")
        cfg["workflow"]["max_iterations"] = True
        save_json(self.home / "config.json", cfg)
        with self.assertRaises(ValueError):
            config(self.home)

    def test_test_timeout_pauses_and_resume_retries_tests(self):
        from patchrondo.process import Result
        task_id, _ = self._task()
        agent = FakeProvider()
        with patch("patchrondo.core.execute", return_value=Result(124, "", "", timed_out=True)):
            paused = run_task(self.home, task_id, adapter=agent)
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["phase"], "test")
        self.assertEqual(paused["last_error"]["kind"], "timeout")
        self.assertEqual(paused["tests"][0]["status"], "timeout")
        self.assertEqual([x[1] for x in agent.calls], ["developer"])
        finished = run_task(self.home, task_id, adapter=agent)
        self.assertEqual(finished["status"], "done")
        self.assertEqual([x[1] for x in agent.calls], ["developer", "reviewer"])

    def test_invalid_phase_is_rejected_without_running_agents(self):
        task_id, _ = self._task()
        path = self.home / "tasks" / task_id
        state = read_json(path / "state.json")
        state["phase"] = "typo"
        save_json(path / "state.json", state)
        agent = FakeProvider()
        with self.assertRaisesRegex(ValueError, "Invalid task phase"):
            run_task(self.home, task_id, adapter=agent)
        self.assertEqual(agent.calls, [])
        self.assertFalse((path / ".run.lock").exists())

    def test_git_failure_pauses_at_checkpoint(self):
        task_id, _ = self._task()
        with patch("patchrondo.core.changes", side_effect=RuntimeError("Git unavailable")):
            with patch("patchrondo.report.changes", side_effect=RuntimeError("Git unavailable")):
                paused = run_task(self.home, task_id, adapter=FakeProvider())
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["phase"], "develop")
        report = self.home / "tasks" / task_id / "report.md"
        self.assertIn("Git unavailable", report.read_text(encoding="utf-8"))

    def test_task_id_traversal_rejected(self):
        from patchrondo.core import task_dir
        with self.assertRaises(ValueError):
            task_dir(self.home, "../../etc/passwd")

    def test_force_unlock_blocks_orphaned_agent_pid(self):
        from patchrondo.storage import TaskLock, private_dir
        import os
        task_id, _ = self._task()
        path = self.home / "tasks" / task_id
        save_json(path / ".run.lock", {"pid": -1})
        runs = private_dir(path / "runs" / "iteration-001")
        save_json(runs / "developer.active-process.json", {"pid": os.getpid()})
        with self.assertRaisesRegex(RuntimeError, "still active"):
            with TaskLock(path, force=True):
                pass


if __name__ == "__main__":
    unittest.main()
