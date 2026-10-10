"""Per-task overrides, the reusable configuration check and the tests_started event."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from patchrondo import recovery
from patchrondo.core import (config, create_task, effective_config, initialize, run_task, task_config, validate_config,
                             validate_overrides)
from patchrondo.providers import AgentFailure, AgentReply
from patchrondo.storage import read_json, save_json

from support import make_repo

TEST_COMMAND = [sys.executable, "-c", "from pathlib import Path; assert Path('feature.txt').read_text() == 'good'"]


class Provider:
    def __init__(self, verdict="APPROVED", fail=None):
        self.verdict, self.fail, self.roles = verdict, fail, []

    def invoke(self, provider, role, prompt, workspace, run_dir):
        self.roles.append(role)
        if self.fail and role == "reviewer":
            raise self.fail
        if role == "developer":
            (workspace / "feature.txt").write_text("good", encoding="utf-8")
            return AgentReply(text="Changed feature.txt", provider=provider)
        return AgentReply(text=json.dumps({"verdict": self.verdict, "summary": "s", "issues": []}), provider=provider)


class OverrideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.home = root / "state"
        initialize(self.home, make_repo(root / "repo"))
        cfg = read_json(self.home / "config.json")
        cfg["tests"] = {"enabled": True, "trust_acknowledged": True, "commands": [TEST_COMMAND]}
        save_json(self.home / "config.json", cfg)
        guard = patch("patchrondo.providers.execute", side_effect=AssertionError("real provider call attempted"))
        guard.start()
        self.addCleanup(guard.stop)

    def task(self, overrides=None):
        task_id, _ = create_task(self.home, title="T", description="d", acceptance=[], developer="claude", reviewer="codex",
                                 overrides=overrides)
        return task_id, self.home / "tasks" / task_id

    def test_only_listed_settings_can_be_overridden_within_the_configuration_ranges(self):
        self.assertEqual(validate_overrides(None), {})
        self.assertEqual(validate_overrides({"workflow": {}, "recovery": None}), {})
        clean = validate_overrides({"workflow": {"max_iterations": 2, "agent_timeout_seconds": 60}, "recovery": {"enabled": True}})
        self.assertEqual(clean, {"workflow": {"max_iterations": 2, "agent_timeout_seconds": 60}, "recovery": {"enabled": True}})
        for bad in ({"tests": {"enabled": True}}, {"workflow": {"claude_max_turns": 3}}, {"workflow": {"allow_no_changes": True}},
                    {"workflow": {"max_iterations": 0}}, {"workflow": {"max_iterations": 51}}, {"workflow": {"max_iterations": True}},
                    {"workflow": {"agent_timeout_seconds": "60"}}, {"recovery": {"enabled": 1}}, {"recovery": {"quota_only": False}},
                    {"recovery": {"max_consecutive_retries": 99}}, {"workflow": []}, [], "x"):
            with self.subTest(overrides=bad), self.assertRaises(ValueError):
                validate_overrides(bad)

    def test_a_task_without_overrides_has_the_state_shape_of_earlier_versions(self):
        _, path = self.task()
        self.assertNotIn("overrides", read_json(path / "state.json"))
        cfg = config(self.home)
        self.assertIs(effective_config(cfg, read_json(path / "state.json")), cfg)

    def test_overrides_are_saved_once_and_applied_to_that_task_only(self):
        task_id, path = self.task({"workflow": {"max_iterations": 1, "test_timeout_seconds": 30}, "recovery": {"enabled": True}})
        other_id, _ = self.task()
        self.assertEqual(read_json(path / "state.json")["overrides"],
                         {"workflow": {"max_iterations": 1, "test_timeout_seconds": 30}, "recovery": {"enabled": True}})
        merged = task_config(self.home, task_id)
        self.assertEqual((merged["workflow"]["max_iterations"], merged["workflow"]["test_timeout_seconds"],
                          merged["workflow"]["agent_timeout_seconds"], merged["recovery"]["enabled"]), (1, 30, 900, True))
        plain = task_config(self.home, other_id)
        self.assertEqual((plain["workflow"]["max_iterations"], plain["recovery"]["enabled"]), (6, False))
        self.assertEqual(config(self.home)["workflow"]["max_iterations"], 6)  # the project file is untouched
        with self.assertRaises(ValueError):
            self.task({"workflow": {"max_iterations": 500}})
        self.assertEqual(len(list((self.home / "tasks").iterdir())), 2)  # the refused task left nothing behind

    def test_the_iteration_limit_of_the_task_decides_when_it_blocks(self):
        task_id, _ = self.task({"workflow": {"max_iterations": 1}})
        state = run_task(self.home, task_id, adapter=Provider("CHANGES_REQUESTED"))
        self.assertEqual((state["status"], state["last_error"], state["iteration"]), ("blocked", "max_iterations_reached", 1))
        other_id, _ = self.task({"workflow": {"max_iterations": 2}})
        state = run_task(self.home, other_id, adapter=Provider("CHANGES_REQUESTED"))
        self.assertEqual((state["status"], state["iteration"]), ("blocked", 2))

    def test_recovery_override_activates_automatic_resume_for_that_task_only(self):
        quota = AgentFailure("Usage limit reached. Retry after 300 seconds.", "quota", provider="codex", retry_after_seconds=300)
        with_recovery, _ = self.task({"recovery": {"enabled": True}})
        without, _ = self.task()
        state = run_task(self.home, with_recovery, adapter=Provider(fail=quota))
        self.assertEqual((state["status"], state["recovery"]["status"]), ("paused", "scheduled"))
        state = run_task(self.home, without, adapter=Provider(fail=quota))
        self.assertEqual((state["status"], state["recovery"]), ("paused", None))
        self.assertTrue(recovery.active(task_config(self.home, with_recovery), None))
        self.assertFalse(recovery.active(task_config(self.home, with_recovery), False))  # the command line still decides
        off, _ = self.task({"recovery": {"enabled": False}})
        cfg = read_json(self.home / "config.json")
        cfg["recovery"] = {"enabled": True}
        save_json(self.home / "config.json", cfg)
        self.assertFalse(recovery.active(task_config(self.home, off), None))
        self.assertTrue(recovery.active(task_config(self.home, without), None))

    def test_corrupted_overrides_stop_the_run_before_any_provider_call(self):
        task_id, path = self.task()
        state = read_json(path / "state.json")
        save_json(path / "state.json", {**state, "overrides": {"workflow": {"max_iterations": 9999}}})
        adapter = Provider()
        with self.assertRaises(ValueError):
            run_task(self.home, task_id, adapter=adapter)
        self.assertEqual(adapter.roles, [])
        self.assertFalse((path / ".run.lock").exists())

    def test_tests_started_is_recorded_only_when_tests_really_run(self):
        task_id, path = self.task()
        state = run_task(self.home, task_id, adapter=Provider())
        events = [(entry["event"], entry.get("commands")) for entry in state["history"]]
        names = [name for name, _ in events]
        self.assertEqual(names, ["run_started", "development_started", "development_completed", "tests_started",
                                 "tests_completed", "review_started", "review_completed", "task_completed"])
        self.assertIn(("tests_started", 1), events)
        cfg = read_json(self.home / "config.json")
        cfg["tests"] = {"enabled": False, "trust_acknowledged": False, "commands": []}
        save_json(self.home / "config.json", cfg)
        disabled, _ = self.task()
        state = run_task(self.home, disabled, adapter=Provider())
        self.assertNotIn("tests_started", [entry["event"] for entry in state["history"]])
        self.assertEqual(state["last_error"]["kind"], "tests_disabled")


class ConfigurationCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.home = root / "state"
        initialize(self.home, make_repo(root / "repo"))
        self.raw = read_json(self.home / "config.json")

    def test_a_candidate_is_checked_without_touching_the_file(self):
        before = (self.home / "config.json").read_bytes()
        candidate = json.loads(json.dumps(self.raw))
        candidate["tests"] = {"enabled": True, "trust_acknowledged": False, "commands": [["pytest"]]}
        with self.assertRaisesRegex(ValueError, "trust_acknowledged"):
            validate_config(candidate, self.home)
        candidate["tests"]["trust_acknowledged"] = True
        self.assertTrue(validate_config(candidate, self.home)["tests"]["enabled"])
        self.assertEqual((self.home / "config.json").read_bytes(), before)

    def test_optional_default_roles_are_validated(self):
        for agents in ({"developer": "codex"}, {"developer": "claude", "reviewer": "claude"}, {}):
            self.assertEqual(validate_config({**json.loads(json.dumps(self.raw)), "agents": agents}, self.home).get("agents"), agents)
        self.assertNotIn("agents", config(self.home))
        for agents in ({"developer": "gpt"}, {"judge": "claude"}, ["claude"], "claude"):
            with self.subTest(agents=agents), self.assertRaisesRegex(ValueError, "agents"):
                validate_config({**json.loads(json.dumps(self.raw)), "agents": agents}, self.home)


if __name__ == "__main__":
    unittest.main()
