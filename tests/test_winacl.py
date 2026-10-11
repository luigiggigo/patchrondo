"""Access policy for the folders agents read: what counts as readable, what is changed, what is put back.

The first classes run everywhere on lists kept in memory. The last one uses
the real access control lists of temporary folders and runs on Windows only.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from patchrondo import winacl
from patchrondo.winacl import Acl, Entry

USER = "S-1-5-21-1111111111-2222222222-3333333333-1001"
OTHER = "S-1-5-21-1111111111-2222222222-3333333333-1002"
SYSTEM, ADMINS, OWNER_RIGHTS, EVERYONE, USERS = "S-1-5-18", "S-1-5-32-544", "S-1-3-4", "S-1-1-0", "S-1-5-32-545"
FULL, RX = 0x001F01FF, winacl.READ_EXECUTE
BOTH = winacl.INHERITABLE
GRANT = winacl.grant  # the real function, before any test replaces it


def acl(*entries, protected=True) -> Acl:
    return Acl("D:" + "".join(f"({e.sid};{e.mask:x};{e.flags:x})" for e in entries), protected, USER, tuple(entries))


def owner_only(protected=True) -> Acl:
    """The list Python 3.13 gives a private directory: nothing names the user."""
    return acl(Entry(SYSTEM, True, FULL, BOTH), Entry(ADMINS, True, FULL, BOTH), Entry(OWNER_RIGHTS, True, FULL, BOTH),
               protected=protected)


class AccessTests(unittest.TestCase):
    def test_owner_rights_do_not_make_a_folder_readable_for_the_reviewer(self):
        self.assertEqual(winacl.access(owner_only(), USER), "missing")
        self.assertEqual(winacl.access(owner_only(), USER, inheritable=True), "missing")

    def test_an_entry_for_the_user_or_a_broad_group_grants_reading(self):
        for sid in (USER, EVERYONE, USERS, "S-1-5-11"):
            with self.subTest(sid=sid):
                granted = acl(*owner_only().entries, Entry(sid, True, RX, BOTH))
                self.assertEqual(winacl.access(granted, USER, inheritable=True), "granted")
        # Another user's entry is not the reviewer's.
        self.assertEqual(winacl.access(acl(Entry(OTHER, True, FULL, BOTH)), USER), "missing")

    def test_the_grant_must_cover_reading_and_apply_where_asked(self):
        list_only = acl(Entry(USER, True, 0x1, BOTH))  # list the folder, nothing else
        self.assertEqual(winacl.access(list_only, USER), "missing")
        this_folder = acl(Entry(USER, True, RX, 0))
        self.assertEqual(winacl.access(this_folder, USER), "granted")
        self.assertEqual(winacl.access(this_folder, USER, inheritable=True), "missing")
        # An entry that only passes to children does not open the folder itself.
        children_only = acl(Entry(USER, True, RX, BOTH | winacl.INHERIT_ONLY))
        self.assertEqual(winacl.access(children_only, USER), "missing")
        self.assertEqual(winacl.access(acl(*this_folder.entries, *children_only.entries), USER, inheritable=True), "granted")
        # Generic rights stand for the file rights they map to.
        self.assertEqual(winacl.access(acl(Entry(USER, True, 0x80000000 | 0x20000000, BOTH)), USER, inheritable=True), "granted")
        self.assertEqual(winacl.access(Acl("", False, None, (), null=True), USER, inheritable=True), "granted")

    def test_a_deny_entry_is_reported_and_never_counted_as_missing(self):
        denied = acl(Entry(USER, False, 0x1, 0), Entry(USER, True, FULL, BOTH))
        self.assertEqual(winacl.access(denied, USER), "denied")
        self.assertEqual(winacl.access(acl(Entry(EVERYONE, False, RX, BOTH), Entry(USER, True, FULL, BOTH)), USER), "denied")
        # Denying only writes leaves reading alone; an entry of a type that is not interpreted decides nothing.
        self.assertEqual(winacl.access(acl(Entry(USER, False, 0x2, 0), Entry(USER, True, RX, BOTH)), USER), "granted")
        self.assertEqual(winacl.access(acl(Entry("", None, FULL, BOTH)), USER), "missing")

    def test_other_systems_have_nothing_to_check(self):
        with patch.object(winacl, "AVAILABLE", False):
            self.assertEqual(winacl.inspect(Path("home"), Path("home/worktrees/T-1"))["status"], "unavailable")
            self.assertTrue(winacl.prepare(Path("home")))
            self.assertEqual(winacl.repair(Path("home"), Path("home/worktrees/T-1"))["changed"], [])


class Lists:
    """Access control lists in memory, with the inheritance the policy relies on."""

    def __init__(self):
        self.lists: dict[str, Acl] = {}
        self.saved: dict[str, Acl] = {}
        self.writes: list[str] = []
        self.fail_on: set[str] = set()
        self.interrupt_on: set[str] = set()

    @staticmethod
    def key(path) -> str:
        return os.path.normcase(os.path.abspath(str(path)))

    def put(self, path, value: Acl) -> None:
        self.lists[self.key(path)] = value

    def read(self, path) -> Acl:
        if self.key(path) not in self.lists:
            raise winacl.AclError(None, "Access is denied", str(path), 5)
        value = self.lists[self.key(path)]
        token = f"SAVED-{len(self.saved)}"
        self.saved[token] = value
        return Acl(token, value.protected, value.owner, value.entries)

    def _children(self, path):
        prefix = self.key(path) + os.sep
        return [name for name in self.lists if name.startswith(prefix)]

    def _inherit(self, path) -> None:
        passed = tuple(Entry(e.sid, e.allow, e.mask, (e.flags & ~winacl.INHERIT_ONLY) | winacl.INHERITED)
                       for e in self.lists[self.key(path)].entries if e.flags & BOTH == BOTH)
        for name in sorted(self._children(path)):
            child = self.lists[name]
            if not child.protected:
                own = tuple(e for e in child.entries if not e.inherited)
                self.lists[name] = Acl(child.sddl, False, child.owner, own + passed)

    def grant(self, path, sid, *, rights, inheritable) -> None:
        if self.key(path) in self.interrupt_on:
            raise KeyboardInterrupt
        if self.key(path) in self.fail_on:
            raise winacl.AclError(None, "Access is denied", str(path), 5)
        current = self.lists[self.key(path)]
        self.put(path, Acl(current.sddl, current.protected, current.owner,
                           (Entry(sid, True, rights, BOTH if inheritable else 0), *current.entries)))
        self.writes.append(self.key(path))
        self._inherit(path)

    def restore(self, path, sddl, protected=None) -> None:
        self.put(path, self.saved[sddl])
        self.writes.append(self.key(path))
        self._inherit(path)


class PolicyTests(unittest.TestCase):
    """`inspect`, `repair` and `rollback` on lists in memory; the folders themselves are real."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "state"
        self.worktree = self.home / "worktrees" / "T-0123456789ab"
        for folder in (self.worktree, self.home / "tasks"):
            folder.mkdir(parents=True)
        self.lists = Lists()
        # The folder the state directory is in is the user's own, as a profile folder is: it can be listed.
        self.lists.put(self.home.parent, acl(Entry(USER, True, FULL, BOTH)))
        for folder in (self.home, self.home / "tasks", self.home / "worktrees"):
            self.lists.put(folder, owner_only())
        self.lists.put(self.worktree, owner_only(protected=False))
        for name in ("AVAILABLE", "read", "grant", "restore", "current_user_sid"):
            value = {"AVAILABLE": True, "current_user_sid": lambda: USER}.get(name, getattr(self.lists, name, None))
            patcher = patch.object(winacl, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def own(self, folder):
        return [entry for entry in self.lists.lists[Lists.key(folder)].entries if entry.sid == USER and not entry.inherited]

    def test_the_known_problem_is_found_and_repaired_with_one_entry(self):
        self.assertEqual(winacl.inspect(self.home, self.worktree)["status"], "missing")
        untouched = {name: self.lists.lists[Lists.key(self.home / name)] for name in (".", "tasks")}
        result = winacl.repair(self.home, self.worktree)
        self.assertEqual((result["status"], result["error"], result["restored"]), ("granted", None, False))
        # One write, on the worktrees folder; the worktree receives the entry by inheritance.
        self.assertEqual(self.lists.writes, [Lists.key(self.home / "worktrees")])
        self.assertEqual([change["role"] for change in result["changed"]], ["container"])
        self.assertEqual([(entry.allow, entry.mask, entry.flags) for entry in self.own(self.home / "worktrees")], [(True, RX, BOTH)])
        self.assertEqual(winacl.inspect(self.home, self.worktree)["status"], "granted")
        # Private folders keep exactly the list they had.
        for name, before in untouched.items():
            self.assertIs(self.lists.lists[Lists.key(self.home / name)], before)
        # Every change carries what restores it.
        self.assertTrue(all(change["previous_sddl"] and "previous_protected" in change for change in result["changed"]))

    def test_repairing_again_changes_nothing(self):
        winacl.repair(self.home, self.worktree)
        writes = list(self.lists.writes)
        again = winacl.repair(self.home, self.worktree)
        self.assertEqual((again["status"], again["changed"]), ("granted", []))
        self.assertEqual(self.lists.writes, writes)
        self.assertEqual(len(self.own(self.home / "worktrees")), 1)

    def test_a_worktree_with_its_own_list_gets_its_own_entry_and_keeps_the_others(self):
        custom = Entry(OTHER, True, RX, BOTH)
        self.lists.put(self.worktree, acl(*owner_only().entries, custom, protected=True))
        result = winacl.repair(self.home, self.worktree)
        self.assertEqual([change["role"] for change in result["changed"]], ["container", "worktree"])
        self.assertIn(custom, self.lists.lists[Lists.key(self.worktree)].entries)
        self.assertTrue(self.lists.lists[Lists.key(self.worktree)].protected)

    def test_a_parent_that_blocks_the_way_is_repaired_even_when_the_worktree_is_readable(self):
        self.lists.put(self.worktree, acl(*owner_only().entries, Entry(USER, True, RX, BOTH)))
        self.assertEqual(winacl.inspect(self.home, self.worktree)["status"], "missing")
        result = winacl.repair(self.home, self.worktree)
        self.assertEqual([change["role"] for change in result["changed"]], ["container"])

    def test_a_deny_entry_is_left_alone(self):
        self.lists.put(self.home / "worktrees", acl(Entry(USER, False, RX, BOTH), *owner_only().entries))
        result = winacl.repair(self.home, self.worktree)
        self.assertEqual((result["status"], result["changed"], self.lists.writes), ("denied", [], []))
        self.assertTrue(result["error"])

    def test_a_state_directory_nobody_can_see_gets_read_attributes_and_nothing_more(self):
        self.lists.put(self.home.parent, owner_only())  # as below a folder Python 3.13 made private
        report = winacl.inspect(self.home, self.worktree)
        self.assertEqual([(folder["role"], folder["access"]) for folder in report["folders"]],
                         [("ancestor", "missing"), ("container", "missing"), ("worktree", "missing")])
        before = self.lists.lists[Lists.key(self.home)].entries
        result = winacl.repair(self.home, self.worktree)
        self.assertEqual((result["status"], [change["role"] for change in result["changed"]]), ("granted", ["ancestor", "container"]))
        # This folder only, attributes only: no listing, no reading, nothing inherited by the private folders in it.
        self.assertEqual([(entry.allow, entry.mask, entry.flags) for entry in self.own(self.home)],
                         [(True, winacl.READ_ATTRIBUTES, 0)])
        self.assertEqual(self.lists.lists[Lists.key(self.home)].entries[1:], before)
        self.assertEqual(winacl.access(self.lists.lists[Lists.key(self.home)], USER, rights=winacl.LIST), "missing")
        self.assertEqual(winacl.access(self.lists.lists[Lists.key(self.home / "tasks")], USER), "missing")
        self.assertEqual(self.lists.lists[Lists.key(self.home / "tasks")].entries, owner_only().entries)
        self.assertNotIn(Lists.key(self.home.parent), self.lists.writes)  # what is above belongs to the user
        self.assertEqual(winacl.repair(self.home, self.worktree)["changed"], [])
        # Rolled back, the state directory has exactly the entries it had.
        self.assertTrue(winacl.rollback(result["changed"]))
        self.assertEqual(self.lists.lists[Lists.key(self.home)].entries, before)

    def test_a_deny_on_an_ancestor_is_left_alone_too(self):
        self.lists.put(self.home.parent, owner_only())
        self.lists.put(self.home, acl(Entry(USER, False, winacl.READ_ATTRIBUTES, 0), *owner_only().entries))
        result = winacl.repair(self.home, self.worktree)
        self.assertEqual((result["status"], result["changed"], self.lists.writes), ("denied", [], []))

    def test_only_the_two_rights_of_the_policy_can_be_granted(self):
        # The real function refuses before any system call, on every system.
        for rights in (FULL, 0x2, 0x10000, winacl.READ_EXECUTE | 0x2, 0):
            with self.subTest(rights=rights), self.assertRaises(ValueError):
                GRANT(self.home, USER, rights=rights, inheritable=False)
        for name in ("Everyone", "*", "S-1-1-0; S-1-5-11", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                GRANT(self.home, name, rights=winacl.READ_EXECUTE, inheritable=True)

    def test_a_failed_repair_puts_back_what_it_changed(self):
        self.lists.put(self.worktree, owner_only(protected=True))  # needs a second write, which fails
        before = self.lists.lists[Lists.key(self.home / "worktrees")]
        self.lists.fail_on.add(Lists.key(self.worktree))
        result = winacl.repair(self.home, self.worktree)
        self.assertEqual((result["status"], result["restored"]), ("missing", True))
        self.assertIn("Access is denied", result["error"])
        self.assertEqual(self.lists.lists[Lists.key(self.home / "worktrees")].entries, before.entries)
        self.assertEqual(self.own(self.home / "worktrees"), [])

    def test_a_repair_that_does_not_verify_is_undone(self):
        real = winacl.inspect
        answers = iter(["missing", "missing", "missing"])
        with patch.object(winacl, "inspect", side_effect=lambda *args: {**real(*args), "status": next(answers)}):
            result = winacl.repair(self.home, self.worktree)
        self.assertEqual((result["restored"], bool(result["error"])), (True, True))
        self.assertEqual(self.own(self.home / "worktrees"), [])

    def test_an_interrupted_repair_can_be_run_again_without_duplicates(self):
        self.lists.put(self.worktree, owner_only(protected=True))
        self.lists.interrupt_on.add(Lists.key(self.worktree))
        with self.assertRaises(KeyboardInterrupt):
            winacl.repair(self.home, self.worktree)
        self.lists.interrupt_on.clear()
        result = winacl.repair(self.home, self.worktree)
        self.assertEqual(result["status"], "granted")
        self.assertEqual([change["role"] for change in result["changed"]], ["worktree"])
        self.assertEqual((len(self.own(self.home / "worktrees")), len(self.own(self.worktree))), (1, 1))

    def test_only_a_worktree_directly_inside_the_state_directory_is_managed(self):
        outside = Path(self.tmp.name) / "elsewhere" / "worktrees" / self.worktree.name
        outside.mkdir(parents=True)
        nested = self.worktree / "sub"
        nested.mkdir()
        for target in (outside, nested, self.home / "tasks", self.home / "worktrees" / "missing"):
            with self.subTest(target=target):
                report = winacl.inspect(self.home, target)
                self.assertEqual((report["status"], report["folders"]), ("unmanaged", []))
                result = winacl.repair(self.home, target)
                self.assertEqual((result["changed"], self.lists.writes), ([], []))
                self.assertTrue(result["error"])

    def test_a_link_is_never_followed(self):
        target = Path(self.tmp.name) / "private"
        target.mkdir()
        link = self.home / "worktrees" / "T-aaaaaaaaaaaa"
        if not make_link(link, target):
            self.skipTest("This system cannot create a directory link here")
        self.lists.put(target, owner_only())
        self.lists.put(link, owner_only(protected=False))
        self.assertEqual(winacl.inspect(self.home, link)["status"], "unmanaged")
        self.assertIn("link", winacl.inspect(self.home, link)["reason"])
        self.assertEqual((winacl.repair(self.home, link)["changed"], self.lists.writes), ([], []))
        # The same when the worktrees folder itself is a link to somewhere else.
        other = Path(self.tmp.name) / "other-state"
        (other / "real" / "T-bbbbbbbbbbbb").mkdir(parents=True)
        if make_link(other / "worktrees", other / "real"):
            self.assertEqual(winacl.inspect(other, other / "worktrees" / "T-bbbbbbbbbbbb")["status"], "unmanaged")
            self.assertEqual((winacl.repair(other, other / "worktrees" / "T-bbbbbbbbbbbb")["changed"], self.lists.writes),
                             ([], []))


class ProjectHomeTests(unittest.TestCase):
    """A project of a home with several projects: `<root>/projects/<id>/worktrees/<task>`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "home"
        self.home = self.root / "projects" / "P-0123abcd"
        self.worktree = self.home / "worktrees" / "T-0123456789ab"
        self.worktree.mkdir(parents=True)
        (self.root / "projects.json").write_text("{}", encoding="utf-8")
        self.lists = Lists()
        self.lists.put(self.root.parent, acl(Entry(USER, True, FULL, BOTH)))
        for folder in (self.root, self.home, self.home / "worktrees"):
            self.lists.put(folder, owner_only())
        for folder in (self.root / "projects", self.worktree):
            self.lists.put(folder, owner_only(protected=False))
        for name in ("AVAILABLE", "read", "grant", "restore", "current_user_sid"):
            value = {"AVAILABLE": True, "current_user_sid": lambda: USER}.get(name, getattr(self.lists, name, None))
            patcher = patch.object(winacl, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_folders_patchrondo_created_are_recognized_and_nothing_above_them(self):
        self.assertEqual(winacl.ancestors(self.home), [self.root, self.root / "projects", self.home])
        (self.root / "projects.json").unlink()
        self.assertEqual(winacl.ancestors(self.home), [self.home])
        self.assertEqual(winacl.ancestors(self.root / "projects" / "not-a-project"), [self.root / "projects" / "not-a-project"])
        self.assertEqual(winacl.ancestors(self.root), [self.root])

    def test_each_hidden_level_gets_read_attributes_and_the_visible_one_is_left_alone(self):
        report = winacl.inspect(self.home, self.worktree)
        self.assertEqual([(Path(folder["path"]).name, folder["access"]) for folder in report["folders"]],
                         [("home", "granted"), ("projects", "missing"), ("P-0123abcd", "missing"), ("worktrees", "missing"),
                          ("T-0123456789ab", "missing")])
        result = winacl.repair(self.home, self.worktree)
        self.assertEqual(result["status"], "granted")
        self.assertEqual(self.lists.writes, [Lists.key(self.root / "projects"), Lists.key(self.home), Lists.key(self.home / "worktrees")])
        for folder in (self.root / "projects", self.home):
            own = [e for e in self.lists.lists[Lists.key(folder)].entries if e.sid == USER and not e.inherited]
            self.assertEqual([(e.mask, e.flags) for e in own], [(winacl.READ_ATTRIBUTES, 0)])
        self.assertEqual(self.lists.lists[Lists.key(self.root)].entries, owner_only().entries)
        self.assertEqual(winacl.repair(self.home, self.worktree)["changed"], [])


def make_link(link: Path, target: Path) -> bool:
    """A junction on Windows, a symbolic link elsewhere; False when neither can be made."""
    if os.name == "nt":
        return subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True).returncode == 0
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        return False
    return True


def restrict(folder: Path) -> None:
    """Give a folder the list observed on a home created by Python 3.13: owner only, not inherited."""
    winacl.restore(folder, winacl.OWNER_ONLY_SDDL)


@unittest.skipUnless(os.name == "nt", "Real access control lists exist on Windows only")
class WindowsAclTests(unittest.TestCase):
    """The same policy on the real lists of temporary folders."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup)
        self.root = Path(self.tmp.name)
        self.sid = winacl.current_user_sid()
        # The folder everything is in is the user's own and listable, as a profile folder is, on every Python.
        winacl.restore(self.root, f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{self.sid})")

    def cleanup(self):
        # Junctions are removed first, so that deleting the tree never reaches their targets.
        for folder, names, _files in os.walk(self.root):
            for name in list(names):
                if winacl.is_reparse_point(os.path.join(folder, name)):
                    os.rmdir(os.path.join(folder, name))
                    names.remove(name)
        self.tmp.cleanup()

    def home(self, name="state", worktree="T-0123456789ab") -> tuple[Path, Path]:
        """A state directory as Python 3.13 left it before the policy existed, with one worktree in it."""
        home = self.root / name
        for folder in (home, home / "tasks", home / "worktrees", home / "index"):
            folder.mkdir(parents=True)
            restrict(folder)
        (home / "config.json").write_text("{}", encoding="utf-8")
        (home / "tasks" / "state.json").write_text("{}", encoding="utf-8")
        target = home / "worktrees" / worktree
        (target / "src").mkdir(parents=True)
        (target / "src" / "module.py").write_text("print('hello')\n", encoding="utf-8")
        return home, target

    def own(self, folder) -> list[Entry]:
        return [entry for entry in winacl.read(folder).entries if entry.sid == self.sid and not entry.inherited]

    def test_the_current_user_is_identified(self):
        self.assertRegex(self.sid, r"^S-1-5-21-\d+-\d+-\d+-\d+$")
        shown = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True).stdout
        self.assertIn(self.sid, shown)
        self.assertEqual(winacl.current_user_sid(), self.sid)

    def test_a_home_made_by_this_python_has_a_readable_worktrees_folder_and_private_state(self):
        from support import make_repo
        from patchrondo.core import create_task, initialize

        repo = make_repo(self.root / "repo")
        home = self.root / "new-home"
        initialize(home, repo)
        _task, worktree = create_task(home, title="T", description="D", acceptance=[], developer="claude", reviewer="codex")
        report = winacl.inspect(home, worktree)
        self.assertEqual(report["status"], "granted", report)
        # A file the developer writes later is readable too, by inheritance.
        (worktree / "new.txt").write_text("x", encoding="utf-8")
        self.assertEqual(winacl.access(winacl.read(worktree / "new.txt"), self.sid), "granted")
        self.assertEqual(winacl.repair(home, worktree)["changed"], [])
        if sys.version_info >= (3, 13):
            # This Python restricts private folders to their owner: none of them names the user.
            for private in (home, home / "tasks", home / "tasks" / _task, home / "config.json"):
                with self.subTest(private=private.name):
                    self.assertEqual(winacl.access(winacl.read(private), self.sid), "missing")

    def test_an_existing_restricted_home_is_repaired_and_nothing_private_changes(self):
        home, worktree = self.home()
        self.assertEqual(winacl.read(home / "worktrees").sddl, winacl.OWNER_ONLY_SDDL.replace("D:P", "D:PAI"))
        private = [home, home / "tasks", home / "index", home / "config.json", home / "tasks" / "state.json"]
        before = {path: winacl.read(path).sddl for path in private}
        self.assertEqual(winacl.inspect(home, worktree)["status"], "missing")
        result = winacl.repair(home, worktree)
        self.assertEqual((result["status"], result["error"]), ("granted", None))
        self.assertEqual([change["role"] for change in result["changed"]], ["container"])
        self.assertEqual([(e.allow, e.mask, e.flags) for e in self.own(home / "worktrees")], [(True, RX, BOTH)])
        # The existing worktree and the files already in it received the entry.
        for path in (worktree, worktree / "src", worktree / "src" / "module.py"):
            self.assertEqual(winacl.access(winacl.read(path), self.sid), "granted", path)
        self.assertEqual({path: winacl.read(path).sddl for path in private}, before)
        for path in private:
            self.assertEqual(winacl.access(winacl.read(path), self.sid), "missing", path)
        # Only the user was added: no entry for Everyone, Users or Authenticated Users anywhere.
        for path in (home / "worktrees", worktree):
            self.assertFalse({entry.sid for entry in winacl.read(path).entries} & set(winacl.BROAD_READERS))
        self.assertTrue(winacl.read(home / "worktrees").protected)

    def test_repair_is_idempotent_on_real_lists(self):
        home, worktree = self.home()
        winacl.repair(home, worktree)
        first = {path: winacl.read(path).sddl for path in (home / "worktrees", worktree, worktree / "src" / "module.py")}
        for _ in range(3):
            self.assertEqual(winacl.repair(home, worktree)["changed"], [])
        self.assertEqual({path: winacl.read(path).sddl for path in first}, first)
        self.assertEqual(len(self.own(home / "worktrees")), 1)

    def test_rollback_restores_the_exact_previous_lists(self):
        home, worktree = self.home()
        before = {path: winacl.read(path).sddl for path in (home / "worktrees", worktree, worktree / "src" / "module.py")}
        result = winacl.repair(home, worktree)
        self.assertTrue(winacl.rollback(result["changed"]))
        self.assertEqual({path: winacl.read(path).sddl for path in before}, before)
        self.assertEqual(winacl.inspect(home, worktree)["status"], "missing")

    def test_a_customized_worktree_keeps_its_entries(self):
        home, worktree = self.home()
        custom = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)(A;OICI;0x1200a9;;;S-1-5-32-546)"  # Guests may read
        winacl.restore(worktree, custom)
        result = winacl.repair(home, worktree)
        self.assertEqual([change["role"] for change in result["changed"]], ["container", "worktree"])
        after = winacl.read(worktree)
        self.assertTrue(after.protected)
        self.assertIn("S-1-5-32-546", {entry.sid for entry in after.entries})
        self.assertEqual(len(after.entries), 5)
        self.assertEqual(winacl.inspect(home, worktree)["status"], "granted")

    def test_a_deny_entry_on_real_lists_is_left_alone(self):
        home, worktree = self.home()
        # Listing the folder is denied to the user; the list itself stays readable and restorable.
        denied = f"D:P(D;OICI;0x1;;;{self.sid})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)"
        winacl.restore(home / "worktrees", denied)
        self.addCleanup(winacl.restore, home / "worktrees", winacl.OWNER_ONLY_SDDL)
        before = winacl.read(home / "worktrees").sddl
        result = winacl.repair(home, worktree)
        self.assertEqual((result["status"], result["changed"]), ("denied", []))
        self.assertEqual(winacl.read(home / "worktrees").sddl, before)

    def test_a_failed_second_step_restores_the_first(self):
        home, worktree = self.home()
        restrict(worktree)  # its own list: the repair needs two writes
        before = winacl.read(home / "worktrees").sddl
        real, calls = winacl.grant, []

        def failing(path, sid, *, rights, inheritable):
            calls.append(path)
            if len(calls) == 2:
                raise winacl.AclError(None, "Access is denied", str(path), 5)
            return real(path, sid, rights=rights, inheritable=inheritable)

        with patch.object(winacl, "grant", failing):
            result = winacl.repair(home, worktree)
        self.assertEqual((result["status"], result["restored"], len(calls)), ("missing", True, 2))
        self.assertEqual(winacl.read(home / "worktrees").sddl, before)

    def test_paths_with_spaces_unicode_and_special_characters(self):
        for name in ("stato è ñ 日本", "it's & (x) %TEMP% ^y !z", "a" * 60 + " " + "b" * 60):
            with self.subTest(name=name):
                home, worktree = self.home(name, "T-0123456789ab")
                self.assertEqual(winacl.inspect(home, worktree)["status"], "missing")
                self.assertEqual(winacl.repair(home, worktree)["status"], "granted")
                self.assertEqual(winacl.repair(home, worktree)["changed"], [])

    def test_a_junction_is_neither_repaired_nor_crossed(self):
        home, worktree = self.home()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        restrict(outside)
        before = (winacl.read(outside).sddl, winacl.read(outside / "secret.txt").sddl)
        # A junction inside the worktree: the inherited entry stops at the junction.
        self.assertTrue(make_link(worktree / "escape", outside))
        self.assertEqual(winacl.repair(home, worktree)["status"], "granted")
        self.assertEqual((winacl.read(outside).sddl, winacl.read(outside / "secret.txt").sddl), before)
        # A junction as the worktree: not a managed folder, nothing is written.
        fake = home / "worktrees" / "T-aaaaaaaaaaaa"
        self.assertTrue(make_link(fake, outside))
        report = winacl.inspect(home, fake)
        self.assertEqual(report["status"], "unmanaged")
        self.assertEqual(winacl.repair(home, fake)["changed"], [])
        self.assertEqual((winacl.read(outside).sddl, winacl.read(outside / "secret.txt").sddl), before)
        # A junction as the worktrees folder, and a path outside the state directory.
        other, _ = self.home("other")
        os.rename(other / "worktrees", other / "moved")
        self.assertTrue(make_link(other / "worktrees", other / "moved"))
        self.assertEqual(winacl.inspect(other, other / "worktrees" / "T-0123456789ab")["status"], "unmanaged")
        self.assertEqual(winacl.inspect(home, outside)["status"], "unmanaged")
        self.assertEqual(winacl.repair(home, outside)["changed"], [])
        self.assertFalse(winacl.prepare(other))

    def test_a_state_directory_below_a_private_folder_gets_read_attributes_only(self):
        outer = self.root / "made private"
        outer.mkdir()
        restrict(outer)  # nobody but the owner can list it: what is inside cannot even be seen to exist
        home, worktree = self.home("made private/state")
        before = (winacl.read(outer).sddl, winacl.read(home).sddl, winacl.read(home / "tasks").sddl)
        report = winacl.inspect(home, worktree)
        self.assertEqual([(folder["role"], folder["access"]) for folder in report["folders"]],
                         [("ancestor", "missing"), ("container", "missing"), ("worktree", "missing")])
        result = winacl.repair(home, worktree)
        self.assertEqual((result["status"], [change["role"] for change in result["changed"]]), ("granted", ["ancestor", "container"]))
        entry, = self.own(home)
        self.assertEqual((entry.allow, entry.flags, entry.mask & winacl.READ_ATTRIBUTES), (True, 0, winacl.READ_ATTRIBUTES))
        self.assertFalse(entry.mask & (0x1 | 0x2 | 0x4 | 0x20 | 0x40 | 0x10000 | 0x40000 | 0x80000))  # no list, write, traverse, delete
        after = winacl.read(home)
        self.assertEqual((winacl.access(after, self.sid, rights=winacl.LIST), winacl.access(after, self.sid)), ("missing", "missing"))
        self.assertTrue(after.protected)
        # Nothing inside inherited it, and the user's own folder above was only read.
        self.assertEqual((winacl.read(outer).sddl, winacl.read(home / "tasks").sddl), (before[0], before[2]))
        self.assertEqual(winacl.access(winacl.read(home / "config.json"), self.sid, rights=winacl.READ_ATTRIBUTES), "missing")
        self.assertEqual(winacl.repair(home, worktree)["changed"], [])
        self.assertTrue(winacl.rollback(result["changed"]))
        self.assertEqual(winacl.read(home).sddl, before[1])

    def test_prepare_grants_once_and_keeps_protection(self):
        home, _worktree = self.home()
        self.assertTrue(winacl.prepare(home))
        once = winacl.read(home / "worktrees")
        self.assertTrue(winacl.prepare(home))
        self.assertEqual(winacl.read(home / "worktrees").sddl, once.sddl)
        self.assertTrue(once.protected)
        self.assertEqual(len(self.own(home / "worktrees")), 1)


if __name__ == "__main__":
    unittest.main()
