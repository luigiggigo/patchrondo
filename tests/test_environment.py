"""Environment check, repair and recheck: the manager, the engine around it and the interface.

Providers are scripted and the sandbox, the access lists and the CLI status are
replaced: nothing here starts a provider CLI or changes a real access list.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from support import PROVIDERS, SYSTEM, ServerCase, make_repo

from patchrondo import activity, environment, recovery, repair
from patchrondo import core, gitops
from patchrondo.core import create_task, initialize, run_task
from patchrondo.providers import AgentFailure, AgentReply
from patchrondo.storage import read_json, save_json

USER = "S-1-5-21-1-2-3-1001"


def wait_for(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def facts(**changes) -> dict:
    """What a probe reports when the sandbox works; `changes` break one thing."""
    base = {"token": "t", "begin": "t", "end": "t", "head": None, "expected": True, "fixture": True, "language": "ConstrainedLanguage",
            "cwd_ok": "True", "abs_ok": "True", "rel_ok": "True", "git_exit": "0", "status_exit": "0", "canary_seen": "True",
            "overwrite": "refused", "delete": "refused", "create": "refused", "canary_intact": True, "created": False,
            "process_cwd": "worktree", "process_cwd_ok": True}
    return {**base, **changes}


class FakeLists:
    """Stands in for `winacl`: one status, and a repair that works or not."""
    AVAILABLE = True

    def __init__(self, status="granted", fixable=True, effective=True):
        self.status, self.fixable, self.effective = status, fixable, effective
        self.inspections = self.repairs = self.version = 0

    def inspect(self, home, worktree, sid=None):
        self.inspections += 1
        return {"status": self.status, "reason": "not a managed folder" if self.status == "unmanaged" else None, "sid": USER,
                "folders": [] if self.status == "unmanaged" else [
                    {"path": str(Path(home) / "worktrees"), "role": "container", "access": self.status, "protected": True,
                     "sddl": f"SDDL-{self.version}-{self.status}", "owner": USER}]}

    def repair(self, home, worktree, sid=None):
        self.repairs += 1
        change = {"path": str(Path(home) / "worktrees"), "role": "container", "previous_sddl": "D:P(OLD)",
                  "previous_protected": True, "added": f"allow {USER} read+execute"}
        if not self.fixable:
            return {"changed": [change], "restored": True, "error": "Access is denied", "status": "missing"}
        if self.effective:
            self.status = "granted"
        return {"changed": [change], "restored": False, "error": None, "status": "granted"}


class FakeSandbox:
    def __init__(self, reason=None, **broken):
        self.reason, self.broken, self.probes, self.log = reason, broken, 0, None

    def identity(self):
        return ("codex", 1, 1)

    def unavailable(self, cwd):
        return self.reason

    def probe(self, home, worktree, head):
        self.probes += 1
        if self.log is not None:
            self.log.append("probe")
        return facts(**{"head": head, "git_head": head, **self.broken})


class CliStatus:
    def __init__(self, **changes):
        self.changes, self.calls = changes, []

    def __call__(self, name, cwd=None):
        self.calls.append(name)
        return {**PROVIDERS[name], **self.changes.get(name, {})}


class Provider:
    """Scripted adapter. `on_develop` runs when the developer is called; the reviewer can be interrupted once."""

    def __init__(self, verdicts=("APPROVED",), on_develop=None, interrupt_review=False):
        self.calls, self.verdicts, self.on_develop, self.interrupt_review = [], list(verdicts), on_develop, interrupt_review

    def invoke(self, provider, role, prompt, workspace, run_dir):
        self.calls.append(role)
        if role == "developer":
            (workspace / "feature.txt").write_text("good", encoding="utf-8")
            if self.on_develop:
                self.on_develop()
            return AgentReply(text="Changes: wrote feature.txt", provider=provider)
        if self.interrupt_review:
            self.interrupt_review = False
            raise KeyboardInterrupt
        verdict = self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]
        return AgentReply(text=json.dumps({"verdict": verdict, "summary": "Read the worktree", "issues": []}), provider=provider)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.repo = make_repo(root / "repo")
        self.home = root / "state"
        initialize(self.home, self.repo)
        self.allow_tests(True)
        self.lists, self.sandbox, self.cli = FakeLists(), FakeSandbox(), CliStatus()

    def allow_tests(self, enabled: bool) -> None:
        cfg = read_json(self.home / "config.json")
        cfg["tests"] = {"enabled": enabled, "trust_acknowledged": enabled, "commands": [[
            sys.executable, "-c", "from pathlib import Path; assert Path('feature.txt').read_text() == 'good'"]] if enabled else []}
        save_json(self.home / "config.json", cfg)

    def manager(self, **parts):
        return environment.Manager(sandbox=parts.get("sandbox", self.sandbox), check_cli=parts.get("cli", self.cli),
                                   acl=parts.get("acl", self.lists))

    def task(self, developer="claude", reviewer="codex"):
        task_id, worktree = create_task(self.home, title="Feature", description="Write feature.txt", acceptance=["Done"],
                                        developer=developer, reviewer=reviewer)
        return task_id, self.home / "tasks" / task_id, worktree

    def state(self, task_id):
        return read_json(self.home / "tasks" / task_id / "state.json")

    def events(self, task_id):
        return [entry["event"] for entry in self.state(task_id)["history"]]


class ManagerTests(Base):
    def check(self, live=True, **options):
        task_id, _path, _worktree = self.task(**options.pop("roles", {}))
        return self.manager().check(self.home, self.state(task_id), live=live, repository=self.repo, **options)

    def states(self, record):
        return {check["id"]: check["status"] for check in record["checks"]}

    def test_everything_in_order_is_ready_and_says_what_was_checked(self):
        record = self.check()
        self.assertEqual((record["status"], record["error_kind"], record["repair_applied"], record["attempts"]),
                         ("ready", None, False, 0))
        states = self.states(record)
        self.assertEqual((states["cli.claude"], states["cli.codex"], states["git.ownership"], states["acl.worktree"]),
                         ("passed",) * 4)
        self.assertEqual({states[name] for name in ("read.absolute", "read.relative", "shell.cwd", "process.cwd", "git.run",
                                                    "git.metadata", "write.canary", "write.create", "sandbox.start")}, {"passed"})
        self.assertEqual((self.cli.calls, self.sandbox.probes, self.lists.repairs), (["claude", "codex"], 1, 0))
        self.assertTrue(record["signature"])
        self.assertEqual({check["stage"] for check in record["checks"]}, {"agents", "project", "sandbox"})

    def test_with_simulated_providers_no_cli_and_no_sandbox_is_started(self):
        record = self.check(live=False)
        states = self.states(record)
        self.assertEqual((record["status"], self.cli.calls, self.sandbox.probes), ("ready", [], 0))
        self.assertEqual((states["cli.claude"], states["cli.codex"], states["read.absolute"], states["write.canary"]),
                         ("skipped",) * 4)  # skipped, never reported as passed
        self.assertEqual(states["acl.worktree"], "passed")

    def test_a_missing_cli_stops_before_anything_else(self):
        self.cli = CliStatus(codex={"installed": False, "hint": "Install Codex CLI, then run: codex login"})
        record = self.check()
        self.assertEqual((record["status"], record["error_kind"], record["provider"]), ("blocked", "cli_missing", "codex"))
        self.assertEqual((record["repair_available"], record["requires_user_action"]), (False, True))
        self.assertIn("Install Codex CLI", record["message"])
        self.assertEqual((self.sandbox.probes, self.lists.inspections), (0, 0))

    def test_a_cli_that_is_not_logged_in_is_named_with_its_login_command(self):
        self.cli = CliStatus(claude={"authenticated": False, "hint": "Not logged in"})
        record = self.check()
        self.assertEqual((record["error_kind"], record["provider"], record["status"]), ("authentication", "claude", "blocked"))
        self.assertIn("claude auth login", record["message"])
        self.assertNotIn("token", json.dumps(record).lower())
        # A login that could not be confirmed either way does not stop the run: the step itself decides.
        self.cli = CliStatus(codex={"authenticated": None})
        record = self.check()
        self.assertEqual((record["status"], self.states(record)["cli.codex"]), ("ready", "skipped"))

    def test_unreadable_folders_are_repaired_before_the_sandbox_is_asked(self):
        self.lists = FakeLists("missing")
        order = self.sandbox.log = []
        notes = []
        record = self.check(note=lambda event, **fields: (notes.append((event, fields.get("stage") or fields.get("repair"))),
                                                           order.append(event)))
        self.assertEqual((record["status"], record["repair_applied"], record["attempts"], self.lists.repairs), ("ready", True, 1, 1))
        self.assertEqual(notes, [("stage", "agents"), ("stage", "project"), ("stage", "sandbox"), ("repair", "acl")])
        self.assertEqual(order[-2:], ["repair", "probe"])  # the probe runs once, after the repair
        entry = record["repairs"][-1]
        self.assertEqual((entry["repair"], entry["outcome"], entry["changed"][0]["previous_sddl"]), ("acl", "applied", "D:P(OLD)"))

    def test_a_repair_that_fails_leaves_one_guided_action_and_is_not_tried_again(self):
        self.lists = FakeLists("missing", fixable=False)
        record = self.check()
        self.assertEqual((record["status"], record["error_kind"], record["cause"]),
                         ("repairable", "sandbox_access_denied", "acl_missing_read"))
        self.assertEqual((record["repair"], record["repair_available"], record["repair_applied"], record["requires_user_action"]),
                         ("acl", True, False, True))
        self.assertEqual((self.lists.repairs, record["attempts"], self.sandbox.probes), (1, 1, 0))
        self.assertEqual(record["repairs"][-1]["outcome"], "rolled_back")
        self.assertNotIn("Access is denied", record["message"])  # the sentence for people has no system error in it

    def test_a_repair_without_effect_does_not_loop(self):
        self.lists = FakeLists("missing", effective=False)
        record = self.check()
        self.assertEqual((record["status"], self.lists.repairs, record["attempts"]), ("repairable", 1, 1))
        self.assertLessEqual(record["attempts"], repair.MAX_REPAIRS)

    def test_a_sandbox_that_denies_despite_the_lists_is_blocked_without_widening_access(self):
        self.sandbox = FakeSandbox(abs_ok="False", rel_ok="False", cwd_ok="False")
        record = self.check()
        self.assertEqual((record["status"], record["error_kind"], record["cause"]),
                         ("blocked", "sandbox_access_denied", "sandbox_denied_despite_access_entries"))
        self.assertEqual((record["repair_available"], self.lists.repairs), (False, 0))
        self.assertIn("read.absolute", record["detail"])

    def test_a_sandbox_that_can_write_is_never_accepted(self):
        for broken, kind in (({"created": True}, "sandbox_not_read_only"), ({"canary_intact": False}, "sandbox_not_read_only"),
                             ({"begin": None, "exit": 1}, "sandbox_unavailable"),
                             ({"git_exit": "128", "git_head": "fatal: bad"}, "sandbox_git_failed")):
            with self.subTest(broken=broken):
                self.sandbox, self.lists = FakeSandbox(**broken), FakeLists()
                record = self.check()
                self.assertEqual((record["status"], record["error_kind"], record["repair_available"]), ("blocked", kind, False))
                self.assertEqual(self.lists.repairs, 0)
                self.assertEqual(repair.category(kind), "permanent")

    def test_a_deny_entry_is_reported_and_left_alone(self):
        self.lists = FakeLists("denied")
        record = self.check()
        self.assertEqual((record["status"], record["cause"], self.lists.repairs, self.sandbox.probes),
                         ("blocked", "acl_deny_entry", 0, 0))

    def test_an_unmanaged_folder_is_judged_by_the_sandbox_alone(self):
        self.lists = FakeLists("unmanaged")
        self.assertEqual((self.check()["status"], self.lists.repairs), ("ready", 0))
        self.sandbox = FakeSandbox(abs_ok="False")
        record = self.check()
        self.assertEqual((record["status"], record["cause"], self.lists.repairs), ("blocked", "worktree_not_managed", 0))

    def test_a_sandbox_that_cannot_be_probed_is_reported_as_skipped(self):
        self.sandbox = FakeSandbox(reason="This Codex CLI does not list the options the sandbox self-check needs")
        record = self.check()
        self.assertEqual((record["status"], self.sandbox.probes, self.states(record)["write.canary"]), ("ready", 0, "skipped"))
        self.assertIn("does not list", next(c["detail"] for c in record["checks"] if c["id"] == "write.canary"))

    def test_a_claude_reviewer_needs_no_sandbox_check(self):
        record = self.check(roles={"developer": "codex", "reviewer": "claude"})
        self.assertEqual((record["status"], self.lists.inspections, self.sandbox.probes), ("ready", 0, 0))
        self.assertEqual(self.states(record)["sandbox.none"], "skipped")

    def test_a_ready_result_is_reused_only_while_nothing_it_depends_on_changed(self):
        task_id, _path, _worktree = self.task()
        manager, state = self.manager(), self.state(task_id)
        first = manager.check(self.home, state, live=True, repository=self.repo)
        again = manager.check(self.home, state, live=True, previous=first, reviewer_only=True, repository=self.repo)
        self.assertEqual((again.get("reused"), self.sandbox.probes, self.cli.calls), (True, 1, ["claude", "codex"]))
        self.lists.version += 1  # the lists of the folders changed
        fresh = manager.check(self.home, state, live=True, previous=first, reviewer_only=True, repository=self.repo)
        self.assertEqual((fresh.get("reused"), fresh["status"], self.sandbox.probes), (None, "ready", 2))
        self.assertEqual(self.cli.calls, ["claude", "codex"])  # the agents stage is not repeated before a review
        self.assertEqual([c["id"] for c in fresh["checks"] if c["stage"] == "agents"], ["cli.claude", "cli.codex"])
        self.assertNotEqual(fresh["signature"], first["signature"])
        # A result that was not ready is never reused, and neither is one made with simulated providers.
        blocked = {**first, "status": "blocked"}
        self.assertIsNone(manager.check(self.home, state, live=True, previous=blocked, reviewer_only=True,
                                        repository=self.repo).get("reused"))
        self.assertIsNone(manager.check(self.home, state, live=False, previous=fresh, reviewer_only=True,
                                        repository=self.repo).get("reused"))

    def test_the_checks_can_be_switched_off(self):
        with patch.dict(os.environ, {environment.SWITCH: "0"}):
            self.assertFalse(environment.enabled())
        with patch.dict(os.environ):
            os.environ.pop(environment.SWITCH, None)
            self.assertTrue(environment.enabled())

    def test_the_system_report_names_git_and_python(self):
        report = environment.system()
        self.assertTrue(report["git"]["installed"])
        self.assertRegex(report["git"]["version"], r"^\d+\.\d+")
        self.assertEqual((report["python"], report["python_supported"]), (".".join(map(str, sys.version_info[:3])), True))

    def test_every_problem_has_a_category_and_a_sentence(self):
        self.assertEqual({problem.category for problem in repair.PROBLEMS.values()},
                         {"repairable", "consent", "action", "temporary", "permanent"})
        self.assertEqual((repair.category("quota"), repair.category("max_turns"), repair.category("tests_disabled"),
                          repair.category("sandbox_access_denied"), repair.category("unknown"), repair.category(None)),
                         ("temporary", "permanent", "consent", "repairable", None, None))
        self.assertEqual(repair.AUTOMATIC, {"acl"})  # nothing that is a decision of the user runs unattended
        for kind, problem in repair.PROBLEMS.items():
            with self.subTest(kind=kind):
                self.assertNotRegex(problem.message, r"WinError|PermissionError|ACL|SID|PowerShell|Traceback")


class GitOwnershipTests(Base):
    """Git itself is asked to treat the fixture as owned by someone else; the user's Git configuration is not touched."""

    def setUp(self):
        super().setUp()
        self.task_id, self.path, self.worktree = self.task()
        self.global_config = Path(self.tmp.name) / "gitconfig"
        self.global_config.write_text("", encoding="utf-8")
        # GIT_CONFIG_NOSYSTEM: a safe.directory entry of this computer must not decide the test.
        env = patch.dict(os.environ, {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1", "GIT_CONFIG_GLOBAL": str(self.global_config),
                                      "GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)
        try:
            gitops.git("rev-parse", "HEAD", cwd=self.worktree)
        except gitops.GitError as exc:
            if exc.kind != "git_dubious_ownership":
                raise
        else:
            self.skipTest("This Git does not simulate another owner")

    def trusted(self):
        done = subprocess.run(["git", "config", "--global", "--get-all", "safe.directory"], capture_output=True, text=True)
        return done.stdout.split("\n")[:-1]

    def test_the_failure_is_classified_with_the_folder_git_named(self):
        with self.assertRaises(gitops.GitError) as caught:
            gitops.changes(self.worktree)
        self.assertEqual(caught.exception.kind, "git_dubious_ownership")
        self.assertTrue(caught.exception.folder)
        self.assertIsInstance(caught.exception, RuntimeError)
        self.assertEqual(gitops.git_failure("fatal: not a git repository").kind, "system_error")

    def test_the_run_stops_before_any_agent_with_its_own_kind(self):
        adapter = Provider()
        state = run_task(self.home, self.task_id, adapter=adapter, environment=self.manager())
        self.assertEqual((state["status"], state["phase"], state["last_error"]["kind"], adapter.calls),
                         ("paused", "develop", "git_dubious_ownership", []))
        record = state["environment"]
        self.assertEqual((record["status"], record["repair"], record["repair_available"], record["requires_user_action"]),
                         ("repairable", "git_trust", True, True))
        self.assertEqual(self.trusted(), [])  # nothing was trusted on the user's behalf
        self.assertIsNone(state["recovery"])
        # Without the check, the same failure still gets its own kind, never "system_error".
        with patch.dict(os.environ, {environment.SWITCH: "0"}):
            state = run_task(self.home, self.task_id, adapter=adapter)
        self.assertEqual((state["last_error"]["kind"], adapter.calls), ("git_dubious_ownership", []))

    def test_trust_is_recorded_only_when_confirmed_and_only_for_the_task_folders(self):
        manager, state = self.manager(), self.state(self.task_id)
        record = manager.check(self.home, state, live=False, repository=self.repo)
        self.assertEqual((record["status"], record["attempts"], self.trusted()), ("repairable", 0, []))
        record = manager.check(self.home, state, live=False, confirmed=("git_trust",), repository=self.repo)
        self.assertEqual((record["status"], record["repair_applied"], record["attempts"]), ("ready", True, 1))
        self.assertEqual(sorted(self.trusted()), sorted([Path(self.repo).as_posix(), Path(self.worktree).as_posix()]))
        self.assertNotIn("*", self.trusted())
        changed = record["repairs"][-1]["changed"]
        self.assertTrue(all("--unset-all safe.directory" in change["undo"] for change in changed))
        # Confirming again adds nothing.
        again = manager.check(self.home, state, live=False, confirmed=("git_trust",), repository=self.repo)
        self.assertEqual((again["status"], again["attempts"], len(self.trusted())), ("ready", 0, 2))

    def test_trust_refuses_a_wildcard_and_folders_that_do_not_exist(self):
        for folders in ([Path("*")], [Path("relative")], [self.home / "missing"]):
            with self.subTest(folders=folders):
                self.assertEqual(repair.git_trust(folders)["outcome"], "failed")
        self.assertEqual(self.trusted(), [])


class EngineTests(Base):
    def run_with(self, task_id, adapter, **parts):
        return run_task(self.home, task_id, adapter=adapter, environment=self.manager(**parts))

    def journal(self, path):
        return [(event["role"], event["type"], event.get("status") or event.get("outcome") or event.get("kind"))
                for event in activity.read(path)["events"]]

    def test_a_failed_preflight_stops_the_run_before_any_provider_call(self):
        self.lists = FakeLists("missing", fixable=False)
        task_id, path, _ = self.task()
        adapter = Provider()
        state = self.run_with(task_id, adapter)
        self.assertEqual(adapter.calls, [])
        self.assertEqual((state["status"], state["phase"], state["iteration"]), ("paused", "develop", 1))
        error = state["last_error"]
        self.assertEqual((error["kind"], error["source"], error["provider"]), ("sandbox_access_denied", "environment", "codex"))
        self.assertEqual(error["message"], repair.PROBLEMS["sandbox_access_denied"].message)
        record = state["environment"]
        self.assertEqual((record["status"], record["cause"], record["repair_available"]), ("repairable", "acl_missing_read", True))
        self.assertEqual(self.events(task_id), ["run_started", "environment_repaired", "environment_blocked", "task_paused"])
        self.assertIsNone(state["recovery"])
        report = (path / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Environment check", report)
        self.assertIn("sandbox_access_denied", report)
        self.assertIn("rolled_back", report)
        # What happened is in the journal as an attempt of its own, ended as failed.
        recorded = self.journal(path)
        self.assertEqual({role for role, _type, _status in recorded}, {"environment"})
        self.assertIn(("environment", "completion", "failed"), recorded)
        self.assertIn(("environment", "status", "check_failed"), recorded)

    def test_a_successful_repair_lets_the_workflow_continue(self):
        self.lists = FakeLists("missing")
        task_id, path, _ = self.task()
        adapter = Provider()
        state = self.run_with(task_id, adapter)
        self.assertEqual((state["status"], adapter.calls, self.lists.repairs), ("done", ["developer", "reviewer"], 1))
        events = self.events(task_id)
        self.assertLess(events.index("environment_repaired"), events.index("development_started"))
        self.assertNotIn("environment_blocked", events)
        record = state["environment"]
        self.assertEqual((record["status"], record["repair_applied"], record["repairs"][-1]["outcome"]), ("ready", True, "applied"))
        self.assertIn(("environment", "status", "repaired"), self.journal(path))

    def test_nothing_is_recorded_when_nothing_happened(self):
        task_id, path, _ = self.task()
        state = self.run_with(task_id, Provider())
        self.assertEqual(state["status"], "done")
        self.assertFalse([event for event in self.events(task_id) if event.startswith("environment_")])
        self.assertNotIn("environment", {role for role, _type, _status in self.journal(path)})
        self.assertEqual((state["environment"]["status"], state["environment"]["live"]), ("ready", False))
        self.assertNotIn("## Environment check", (path / "report.md").read_text(encoding="utf-8"))

    def test_a_problem_before_the_review_pauses_there_and_resume_keeps_the_work(self):
        task_id, path, worktree = self.task()

        def breaks():  # the folders stop being readable after the developer worked
            self.lists.status, self.lists.fixable = "missing", False

        adapter = Provider(on_develop=breaks)
        state = self.run_with(task_id, adapter)
        self.assertEqual(adapter.calls, ["developer"])  # the reviewer was not called for a review bound to fail
        self.assertEqual((state["status"], state["phase"], state["last_error"]["kind"]), ("paused", "review", "sandbox_access_denied"))
        self.assertEqual(state["tests"][0]["status"], "passed")
        handoff = (path / "handoff.md").read_text(encoding="utf-8")
        self.assertNotIn("review_started", self.events(task_id))
        # Still broken: resuming checks again, calls nobody and stays where it was.
        again = self.run_with(task_id, adapter)
        self.assertEqual((adapter.calls, again["phase"], again["last_error"]["kind"]), (["developer"], "review", "sandbox_access_denied"))
        # Fixed: the run continues from the review with the developer's work intact.
        self.lists.fixable = True
        done = self.run_with(task_id, adapter)
        self.assertEqual((done["status"], adapter.calls), ("done", ["developer", "reviewer"]))
        self.assertEqual(((path / "handoff.md").read_text(encoding="utf-8"), (worktree / "feature.txt").read_text()), (handoff, "good"))
        self.assertIsNone(done["last_error"])

    def test_an_access_problem_is_not_hidden_behind_disabled_tests(self):
        self.allow_tests(False)
        task_id, _path, _ = self.task()

        def breaks():
            self.lists.status, self.lists.fixable = "missing", False

        adapter = Provider(verdicts=["CHANGES_REQUESTED"], on_develop=breaks)
        state = self.run_with(task_id, adapter)
        self.assertEqual((state["last_error"]["kind"], adapter.calls), ("sandbox_access_denied", ["developer"]))
        self.assertNotIn("tests_disabled", json.dumps(state["last_error"]))
        self.assertEqual(state["tests"][0]["status"], "skipped")  # tests were not run, and not enabled by anything
        self.assertEqual((state["phase"], state["environment"]["error_kind"]), ("test", "sandbox_access_denied"))
        self.lists.fixable = True
        state = self.run_with(task_id, adapter)
        # With access restored the consent rule stops the task, before the review and without a reviewer call.
        self.assertEqual((state["last_error"]["kind"], adapter.calls), ("tests_disabled", ["developer"]))
        self.assertNotIn("review_started", self.events(task_id))
        self.assertEqual((state["environment"]["status"], state["environment"]["error_kind"]), ("ready", None))
        # Resuming again without the consent checks nothing and calls nobody.
        inspections, history = self.lists.inspections, state["history"]
        again = self.run_with(task_id, adapter)
        self.assertEqual((again["history"], adapter.calls, self.lists.inspections), (history, ["developer"], inspections))
        cfg = read_json(self.home / "config.json")["tests"]
        self.assertEqual((cfg["enabled"], cfg["trust_acknowledged"]), (False, False))

    def test_trust_alone_never_enables_tests(self):
        cfg = read_json(self.home / "config.json")
        cfg["tests"] = {"enabled": False, "trust_acknowledged": True, "commands": [[sys.executable, "-c", "raise SystemExit(9)"]]}
        save_json(self.home / "config.json", cfg)
        task_id, _path, _ = self.task()
        adapter = Provider(verdicts=["APPROVED"])
        state = self.run_with(task_id, adapter)
        self.assertEqual((state["status"], state["last_error"]["kind"], state["tests"][0]["status"]),
                         ("paused", "tests_disabled", "skipped"))
        self.assertNotIn("tests_started", self.events(task_id))

    def test_every_review_is_preceded_by_a_check_and_a_change_is_probed_again(self):
        task_id, _path, _ = self.task()
        order = self.sandbox.log = []

        class Recording(Provider):
            def invoke(inner, provider, role, prompt, workspace, run_dir):
                order.append(role)
                return Provider.invoke(inner, provider, role, prompt, workspace, run_dir)

        adapter = Recording(verdicts=["CHANGES_REQUESTED", "APPROVED"])
        manager = self.manager()
        with patch.object(manager, "check", wraps=manager.check) as check:
            state = run_task(self.home, task_id, adapter=adapter, environment=manager)
        self.assertEqual(state["status"], "done")
        self.assertEqual([call.kwargs.get("reviewer_only", False) for call in check.call_args_list], [False, True, True])
        self.assertEqual(self.lists.repairs, 0)
        self.assertEqual(order, ["developer", "reviewer", "developer", "reviewer"])

    def test_a_run_resumed_after_a_crash_is_checked_before_its_next_call(self):
        task_id, path, _ = self.task()
        adapter = Provider(interrupt_review=True)
        paused = self.run_with(task_id, adapter)
        self.assertEqual((paused["phase"], paused["last_error"]["kind"]), ("review", "interrupted"))
        crashed = self.state(task_id)
        crashed["status"] = "running"  # as a runner killed between two checkpoints leaves it
        crashed["environment"] = {"version": 1, "status": "checking", "stage": "sandbox", "repairs": []}
        save_json(path / "state.json", crashed)
        order = []
        manager = self.manager()
        real = manager.check

        def checked(*args, **kwargs):
            order.append("check")
            return real(*args, **kwargs)

        class Recording(Provider):
            def invoke(inner, provider, role, prompt, workspace, run_dir):
                order.append(role)
                return Provider.invoke(inner, provider, role, prompt, workspace, run_dir)

        with patch.object(manager, "check", side_effect=checked):
            state = run_task(self.home, task_id, adapter=Recording(), environment=manager)
        self.assertEqual((state["status"], order), ("done", ["check", "check", "reviewer"]))
        self.assertEqual(state["environment"]["status"], "ready")  # the unfinished check was not taken as a result

    def test_automatic_resume_does_not_retry_a_blocked_environment(self):
        self.lists = FakeLists("missing", fixable=False)
        task_id, _path, _ = self.task()
        adapter, manager, sleeps = Provider(), self.manager(), []
        with patch.object(core, "EnvironmentManager", return_value=manager), \
                patch.object(manager, "check", wraps=manager.check) as check:
            state = recovery.supervise(self.home, task_id, adapter=adapter, auto_resume=True, sleep=sleeps.append)
        self.assertEqual((state["status"], state["last_error"]["kind"], state["recovery"]), ("paused", "sandbox_access_denied", None))
        self.assertEqual((check.call_count, self.lists.repairs, adapter.calls, sleeps), (1, 1, [], []))
        self.assertNotIn("recovery_scheduled", self.events(task_id))

    def test_a_quota_retry_goes_through_the_check_again(self):
        task_id, _path, _ = self.task()

        class Limited(Provider):
            def invoke(inner, provider, role, prompt, workspace, run_dir):
                if not inner.calls:
                    inner.calls.append("quota")
                    raise AgentFailure("Usage limit reached", "quota", provider=provider, retry_after_seconds=60)
                return Provider.invoke(inner, provider, role, prompt, workspace, run_dir)

        from datetime import datetime, timedelta, timezone
        moment = [datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)]
        adapter, manager = Limited(), self.manager()
        with patch.object(core, "EnvironmentManager", return_value=manager), \
                patch.object(manager, "check", wraps=manager.check) as check:
            state = recovery.supervise(self.home, task_id, adapter=adapter, auto_resume=True, clock=lambda: moment[0],
                                       sleep=lambda seconds: moment.__setitem__(0, moment[0] + timedelta(seconds=seconds)))
        self.assertEqual((state["status"], adapter.calls), ("done", ["quota", "developer", "reviewer"]))
        full = [call for call in check.call_args_list if not call.kwargs.get("reviewer_only")]
        self.assertEqual(len(full), 2)  # the first run and the automatic retry each start with the whole check

    def test_real_cli_checks_belong_to_real_runs_only(self):
        task_id, _path, _ = self.task()
        seen = []

        class Spy(environment.Manager):
            def check(inner, home, state, *, live, **options):
                seen.append(live)
                return environment.Manager.check(inner, home, state, live=False, **options)

        spy = Spy(sandbox=self.sandbox, check_cli=self.cli, acl=self.lists)
        run_task(self.home, task_id, adapter=Provider(), environment=spy)  # a given adapter: not live
        task_id, _path, _ = self.task()
        with patch.object(core, "OfficialCLI", return_value=Provider()):
            with patch("patchrondo.providers.execute", side_effect=AssertionError("a provider command was started")):
                run_task(self.home, task_id, environment=spy)  # the real adapter, but provider commands are simulated
            task_id, _path, _ = self.task()
            run_task(self.home, task_id, environment=spy)  # the real adapter and real commands
        self.assertEqual(seen, [False, False, False, False, True, True])
        self.assertEqual((self.cli.calls, self.sandbox.probes), ([], 0))

    def test_switched_off_the_run_is_as_it_was_before(self):
        self.lists = FakeLists("missing", fixable=False)
        task_id, _path, _ = self.task()
        with patch.dict(os.environ, {environment.SWITCH: "0"}), \
                patch.object(core, "EnvironmentManager", side_effect=AssertionError("the manager was created")):
            state = run_task(self.home, task_id, adapter=Provider())
        self.assertEqual(state["status"], "done")
        self.assertNotIn("environment", state)

    def test_a_check_that_cannot_finish_is_not_saved_as_a_result(self):
        task_id, _path, _ = self.task()
        manager = self.manager()
        with patch.object(manager, "check", side_effect=ValueError("Control characters cannot be passed to the probe")):
            state = run_task(self.home, task_id, adapter=Provider(), environment=manager)
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "system_error"))
        self.assertIn("environment check could not run", state["last_error"]["message"])
        self.assertNotIn("environment", state)


class InterfaceTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.project, self.repo = self.add_project()
        self.base = f"/api/projects/{self.project['id']}"
        self.lists, self.sandbox = FakeLists(), FakeSandbox()
        self.app.environment = environment.Manager(sandbox=self.sandbox, check_cli=CliStatus(), acl=self.lists)
        simulated = patch("patchrondo.providers.execute", side_effect=AssertionError("a provider command was started"))
        simulated.start()
        self.addCleanup(simulated.stop)

        # A run is a child that only leaves a mark: one line per start, no engine and no provider.
        self.marks = self.root / "starts.txt"
        self.app.run_command = lambda project, task_id, flags: [
            sys.executable, "-c", "import sys; open(sys.argv[1], 'a').write(sys.argv[2] + ' ')", str(self.marks), task_id]

    def starts(self):
        return self.marks.read_text().split() if self.marks.exists() else []

    def tasks(self):
        return sorted(path.name for path in (Path(self.project["home"]) / "tasks").glob("T-*"))

    def test_one_request_creates_the_task_and_starts_it(self):
        body = {"description": "# Add a --json option\n\nto the export command.", "request_id": "quick-start-0001"}
        response, data = self.post(f"{self.base}/start", body)
        self.assertEqual((response.status, data.get("started"), data.get("project")), (201, True, self.project["id"]), data)
        state = read_json(Path(self.project["home"]) / "tasks" / data["id"] / "state.json")
        self.assertEqual((state["title"], state["developer"], state["reviewer"]), ("Add a --json option", "claude", "codex"))
        self.assertEqual(self.starts(), [data["id"]])
        # Pressing twice, or a repeated request, neither creates nor starts anything again.
        again = self.post(f"{self.base}/start", body)[1]
        self.assertEqual((again["id"], self.tasks(), self.starts()), (data["id"], [data["id"]], [data["id"]]))
        # Starting is never consent to run the project's tests.
        tests = read_json(Path(self.project["home"]) / "config.json")["tests"]
        self.assertEqual((tests["enabled"], tests["trust_acknowledged"]), (False, False))

    def test_roles_follow_the_project_then_the_global_defaults_and_can_be_chosen(self):
        self.patch_("/api/settings", {"defaults": {"developer": "codex", "reviewer": "claude"}})
        first = self.post(f"{self.base}/start", {"description": "x " * 200})[1]
        self.patch_(f"{self.base}/config", {"agents": {"reviewer": "codex"}})
        second = self.post(f"{self.base}/start", {"description": "Second", "title": "Named", "developer": "claude"})[1]
        home = Path(self.project["home"])
        one, two = (read_json(home / "tasks" / item["id"] / "state.json") for item in (first, second))
        self.assertEqual((one["developer"], one["reviewer"], len(one["title"])), ("codex", "claude", 80))
        self.assertTrue(one["title"].endswith("…"))
        self.assertEqual((two["developer"], two["reviewer"], two["title"]), ("claude", "codex", "Named"))

    def test_a_start_that_cannot_happen_is_reported_and_nothing_is_started_twice(self):
        for body in ({}, {"description": "  "}, {"description": "x", "title": 3}, {"description": "x", "shell": True},
                     {"description": "x", "developer": "gpt"}):
            with self.subTest(body=body):
                self.assertEqual(self.post(f"{self.base}/start", body)[0].status, 400)
        self.assertEqual(self.tasks(), [])
        self.app.run_command = lambda project, task_id, flags: [str(self.root / "no-such-program"), task_id]
        response, data = self.post(f"{self.base}/start", {"description": "Created, not started"})
        self.assertEqual((response.status, data.get("started")), (201, False), data)
        self.assertTrue(data["error"])
        self.assertEqual((self.tasks(), self.starts()), ([data["id"]], []))

    def test_the_summary_shows_the_saved_check_and_the_kind_of_problem(self):
        task_id, path = self.add_task(self.project)
        state = read_json(path / "state.json")
        state.update(status="paused", last_error={"kind": "sandbox_access_denied", "message": "Codex cannot read", "source": "environment"},
                     environment={"version": 1, "status": "repairable", "error_kind": "sandbox_access_denied", "cause": "acl_missing_read",
                                  "message": "Codex cannot read", "provider": "codex", "repair": "acl", "repair_available": True,
                                  "repair_applied": False, "requires_user_action": True, "checked_at": "2026-10-11T10:00:00+00:00",
                                  "checks": [{"id": "acl.worktree", "status": "failed", "detail": "container: missing"}], "repairs": []})
        save_json(path / "state.json", state)
        summary = self.get(f"{self.base}/tasks/{task_id}")[1]["summary"]
        self.assertEqual((summary["error_kind"], summary["error_category"]), ("sandbox_access_denied", "repairable"))
        self.assertEqual((summary["environment"]["status"], summary["environment"]["repair"], summary["environment"]["repair_available"]),
                         ("repairable", "acl", True))
        self.assertNotIn("checks", summary["environment"])  # the list stays in the detail, not in every live event
        self.assertEqual(self.get(f"{self.base}/tasks/{task_id}")[1]["state"]["environment"]["checks"][0]["id"], "acl.worktree")
        listed = next(item for item in self.get("/api/snapshot")[1]["tasks"] if item["id"] == task_id)
        self.assertEqual(listed["environment"]["error_kind"], "sandbox_access_denied")

    def test_the_repair_button_really_repairs_checks_again_and_resumes(self):
        task_id, path = self.add_task(self.project)
        url = f"{self.base}/tasks/{task_id}/repair"
        self.lists.status, self.lists.fixable = "missing", False
        response, data = self.post(url, {"resume": True})
        self.assertEqual((response.status, data["ready"], "run" in data), (200, False, False))
        self.assertEqual((data["environment"]["status"], data["environment"]["repair_available"]), ("repairable", True))
        saved = read_json(path / "state.json")
        self.assertEqual((saved["environment"]["status"], self.lists.repairs), ("repairable", 1))
        self.assertEqual([entry["event"] for entry in saved["history"]], ["environment_repaired", "environment_blocked"])
        self.lists.fixable = True
        response, data = self.post(url, {"resume": True})
        self.assertEqual((response.status, data.get("ready"), (data.get("run") or {}).get("started"), self.starts()),
                         (200, True, True, [task_id]), data)
        saved = read_json(path / "state.json")
        self.assertEqual((saved["environment"]["status"], saved["environment"]["repair_applied"], self.lists.repairs),
                         ("ready", True, 2))
        self.assertEqual(saved["history"][-1]["event"], "environment_repaired")
        self.assertEqual(self.sandbox.probes, 0)  # provider commands are simulated here: no sandbox was started

    def test_repair_is_refused_when_it_must_not_run(self):
        task_id, path = self.add_task(self.project)
        url = f"{self.base}/tasks/{task_id}/repair"
        for body in ({"confirm": ["acl"]}, {"confirm": "git_trust"}, {"resume": "yes"}, {"elevate": True}, {"confirm": ["*"]}):
            with self.subTest(body=body):
                self.assertEqual(self.post(url, body)[0].status, 400)
        (path / ".run.lock").write_text("{}", encoding="utf-8")
        self.assertEqual(self.post(url, {})[0].status, 409)  # a run holds the task: nothing is checked beside it
        (path / ".run.lock").unlink()
        state = read_json(path / "state.json")
        state["status"] = "done"
        save_json(path / "state.json", state)
        self.assertEqual(self.post(url, {})[0].status, 409)
        self.assertEqual((self.lists.inspections, self.lists.repairs), (0, 0))
        self.assertEqual(self.post(f"{self.base}/tasks/T-000000000000/repair", {})[0].status, 404)

    def test_a_confirmed_repair_reaches_the_manager_and_an_unconfirmed_one_does_not(self):
        task_id, _path = self.add_task(self.project)
        url = f"{self.base}/tasks/{task_id}/repair"
        with patch.object(self.app.environment, "check", wraps=self.app.environment.check) as check:
            self.post(url, {})
            self.post(url, {"confirm": ["git_trust"]})
        self.assertEqual([call.kwargs["confirmed"] for call in check.call_args_list], [(), ("git_trust",)])
        self.assertEqual([call.kwargs["live"] for call in check.call_args_list], [False, False])

    def test_the_computer_report_travels_with_the_agent_status(self):
        self.post("/api/providers/refresh")
        self.assertTrue(wait_for(lambda: self.get("/api/providers")[1]["system"] is not None))
        # What the server reports is what `environment.system` returned (replaced by the fixture here;
        # the real report is checked in ManagerTests), in the status and in the snapshot alike.
        self.assertEqual(self.get("/api/providers")[1]["system"], SYSTEM)
        self.assertEqual(self.get("/api/snapshot")[1]["providers"]["system"], SYSTEM)


if __name__ == "__main__":
    unittest.main()
