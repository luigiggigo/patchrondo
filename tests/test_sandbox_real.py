"""The reviewer preflight in the real Codex sandbox, on real access control lists. No model is called.

These tests start the installed Codex CLI, but only its sandbox self-check
(`codex sandbox ...`): no prompt is sent and no quota is used. They are opt-in
because they start a real CLI and change the lists of temporary folders:

    PATCHRONDO_REAL_SANDBOX=1 python -m unittest discover -s tests -p test_sandbox_real.py -v

They are skipped on other systems, without the switch, and when the installed
CLI does not offer the self-check. A skip is not a pass.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from support import PROVIDERS, make_repo

from patchrondo import environment, preflight, winacl
from patchrondo.core import create_task, initialize
from patchrondo.storage import read_json
from patchrondo.workspace import Workspace

WANTED = os.name == "nt" and os.environ.get("PATCHRONDO_REAL_SANDBOX") == "1"
PROBED = ("sandbox.start", "process.cwd", "shell.cwd", "read.absolute", "read.relative", "git.run", "git.metadata",
          "write.canary", "write.create")


def restrict(*folders: Path) -> None:
    """The list a home created by Python 3.13 had before the policy existed: owner only, not inherited."""
    for folder in folders:
        winacl.restore(folder, winacl.OWNER_ONLY_SDDL)


@unittest.skipUnless(WANTED, "Set PATCHRONDO_REAL_SANDBOX=1 on native Windows to probe the real Codex sandbox")
class RealSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sandbox = preflight.Sandbox()
        reason = cls.sandbox.unavailable(Path.cwd())
        if reason:
            raise unittest.SkipTest(reason)
        version = subprocess.run(["codex", "--version"], capture_output=True, text=True).stdout.strip()
        print(f"\nReal sandbox: {version} · Python {sys.version.split()[0]} · user {winacl.current_user_sid()}", file=sys.stderr)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        # Everything is built in a folder like the user's profile: the user has full access by name and
        # nothing else is inherited. (The temporary folder of a computer where Codex has run carries
        # write entries of its sandboxes, and Python 3.13 makes its own temporary folders owner-only.)
        winacl.restore(self.root, f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{winacl.current_user_sid()})")
        self.manager = environment.Manager(sandbox=self.sandbox, check_cli=lambda name, cwd=None: PROVIDERS[name])

    def restricted_home(self, name="state"):
        """A project whose state directory is as restrictive as the one the problem was observed on."""
        repo = make_repo(self.root / f"repo-{len(list(self.root.iterdir()))}")
        home = self.root / name
        initialize(home, repo)
        restrict(home, home / "tasks", home / "worktrees", home / "empty-hooks")
        task_id, worktree = create_task(home, title="T", description="D", acceptance=[], developer="claude", reviewer="codex")
        return home, task_id, worktree, read_json(home / "tasks" / task_id / "state.json"), repo

    def probe(self, home, worktree):
        head = subprocess.run(["git", "-C", str(worktree), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        checks, kind = preflight.evaluate(self.sandbox.probe(home, worktree, head))
        return {check["id"]: check["status"] for check in checks}, kind, checks

    def in_sandbox(self, worktree, *command):
        code, output, errors, _timed_out = preflight._run(
            ["codex", "sandbox", "-P", ":read-only", "--include-managed-config", "-C", str(worktree), "--", *command],
            worktree, preflight.PROBE_TIMEOUT)
        return code, output.decode("utf-8", errors="replace") + errors

    def test_the_known_problem_is_found_repaired_and_verified_without_a_model(self):
        home, task_id, worktree, state, _repo = self.restricted_home()
        private = [home, home / "tasks", home / "tasks" / task_id, home / "tasks" / task_id / "state.json", home / "config.json"]
        lists = {path: winacl.read(path).sddl for path in private}
        # Before: the sandbox cannot read the worktree and the shell starts somewhere else.
        states, kind, _ = self.probe(home, worktree)
        self.assertEqual(kind, "sandbox_access_denied", states)
        self.assertEqual((states["read.absolute"], states["read.relative"], states["shell.cwd"], states["git.run"]),
                         ("failed",) * 4)
        self.assertEqual(winacl.inspect(home, worktree)["status"], "missing")
        # The check finds it from the lists alone, repairs it and verifies in the sandbox.
        record = self.manager.check(home, state, live=True)
        self.assertEqual((record["status"], record["error_kind"], record["repair_applied"], record["attempts"]),
                         ("ready", None, True, 1), record)
        states = {check["id"]: check["status"] for check in record["checks"]}
        self.assertEqual({name: states[name] for name in PROBED}, dict.fromkeys(PROBED, "passed"), record["checks"])
        self.assertEqual([change["role"] for change in record["repairs"][-1]["changed"]], ["container"])
        # Private state has the lists it had, and the sandbox still cannot read it.
        self.assertEqual({path: winacl.read(path).sddl for path in private}, lists)
        secret = home / "tasks" / task_id / "state.json"
        code, shown = self.in_sandbox(worktree, "cmd.exe", "/d", "/c", "type", str(secret))
        self.assertNotEqual(code, 0, shown)
        self.assertNotIn(task_id, shown.replace(str(secret), ""))
        # Checked again, nothing is repaired again; the worktree was left exactly as Git knows it.
        again = self.manager.check(home, state, live=True)
        self.assertEqual((again["status"], again["attempts"], again["repair_applied"]), ("ready", 0, False))
        status = subprocess.run(["git", "-C", str(worktree), "status", "--porcelain"], capture_output=True, text=True).stdout
        self.assertEqual(status, "")
        self.assertEqual([path.name for path in (home / "worktrees").iterdir()], [task_id])

    def test_a_worktree_readable_only_by_itself_starts_in_the_wrong_folder_until_its_parent_is_repaired(self):
        home, _task, worktree, state, _repo = self.restricted_home()
        winacl.grant(worktree, winacl.current_user_sid(), rights=winacl.READ_EXECUTE, inheritable=True)
        states, kind, checks = self.probe(home, worktree)
        self.assertEqual((kind, states["read.absolute"], states["shell.cwd"], states["read.relative"]),
                         ("sandbox_cwd_unavailable", "passed", "failed", "failed"), checks)
        record = self.manager.check(home, state, live=True)
        self.assertEqual((record["status"], record["repair_applied"]), ("ready", True), record)

    def test_a_project_of_a_multi_project_home(self):
        repo = make_repo(self.root / "repo")
        workspace = Workspace(self.root / "home")
        project = workspace.add(repo)
        restrict(workspace.root, workspace.root / "projects", project.home, project.home / "tasks", project.home / "worktrees")
        task_id, worktree = create_task(project.home, title="T", description="D", acceptance=[], developer="claude",
                                        reviewer="codex")
        state = read_json(project.home / "tasks" / task_id / "state.json")
        untouched = {path: winacl.read(path).sddl for path in (workspace.root, workspace.root / "projects.json",
                                                                project.home / "tasks", project.home / "config.json")}
        # Three levels nobody but the owner can see: the shell of the sandbox cannot reach the worktree.
        self.assertEqual(self.probe(project.home, worktree)[1], "sandbox_access_denied")
        record = self.manager.check(project.home, state, live=True)
        self.assertEqual((record["status"], record["repair_applied"]), ("ready", True), record)
        states = {check["id"]: check["status"] for check in record["checks"]}
        self.assertEqual({name: states[name] for name in PROBED}, dict.fromkeys(PROBED, "passed"), record["checks"])
        self.assertEqual([change["role"] for change in record["repairs"][-1]["changed"]], ["ancestor", "ancestor", "container"])
        self.assertEqual({path: winacl.read(path).sddl for path in untouched}, untouched)
        sid = winacl.current_user_sid()
        for folder in (workspace.root / "projects", project.home):
            # Seen to exist, and nothing more: not listable, not readable.
            after = winacl.read(folder)
            self.assertEqual((winacl.access(after, sid, rights=winacl.READ_ATTRIBUTES), winacl.access(after, sid, rights=winacl.LIST),
                              winacl.access(after, sid)), ("granted", "missing", "missing"), folder)
        code, shown = self.in_sandbox(worktree, "cmd.exe", "/d", "/c", "dir", str(project.home))
        self.assertNotEqual(code, 0, shown)
        self.assertNotIn("config.json", shown)

    def test_names_with_spaces_unicode_and_special_characters(self):
        for name in ("stato è ñ 日本", "it's & (x) %TEMP% ^y !z $env;"):
            with self.subTest(name=name):
                home, _task, worktree, state, _repo = self.restricted_home(name)
                record = self.manager.check(home, state, live=True)
                states = {check["id"]: check["status"] for check in record["checks"]}
                self.assertEqual((record["status"], record["repair_applied"]), ("ready", True), record)
                self.assertEqual({name: states[name] for name in PROBED}, dict.fromkeys(PROBED, "passed"), record["checks"])

    def test_a_home_made_by_this_python_needs_no_repair(self):
        repo = make_repo(self.root / "repo")
        home = self.root / "fresh"
        initialize(home, repo)
        task_id, _worktree = create_task(home, title="T", description="D", acceptance=[], developer="claude", reviewer="codex")
        record = self.manager.check(home, read_json(home / "tasks" / task_id / "state.json"), live=True)
        states = {check["id"]: check["status"] for check in record["checks"]}
        self.assertEqual((record["status"], record["repair_applied"], record["attempts"]), ("ready", False, 0), record)
        self.assertEqual({states[name] for name in PROBED} - {"passed"}, set(), record["checks"])


if __name__ == "__main__":
    unittest.main()
