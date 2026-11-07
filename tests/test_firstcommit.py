"""A Git repository and its first commit, made on request from the Add project form."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from patchrondo import firstcommit
from patchrondo.app import inspect_repository
from patchrondo.workspace import Workspace

from support import ServerCase, make_repo

WHO = {"GIT_AUTHOR_NAME": "Ada Tester", "GIT_AUTHOR_EMAIL": "ada@example.invalid",
       "GIT_COMMITTER_NAME": "Ada Tester", "GIT_COMMITTER_EMAIL": "ada@example.invalid"}
NOBODY = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.useConfigOnly", "GIT_CONFIG_VALUE_0": "true"}


def isolated_git(folder: Path, *, identity: bool = True, hooks: Path | None = None):
    """Git settings of the test alone: no global or system file; a known author, or Git refusing to guess one."""
    empty = folder / "gitconfig"
    empty.write_text("", encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_", "GIT_CONFIG")) and key != "EMAIL"}
    env.update(GIT_CONFIG_GLOBAL=str(empty), GIT_CONFIG_NOSYSTEM="1", **(WHO if identity else NOBODY))
    if hooks is not None:
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
        env.update({"GIT_CONFIG_COUNT": str(count + 1), f"GIT_CONFIG_KEY_{count}": "core.hooksPath",
                    f"GIT_CONFIG_VALUE_{count}": hooks.as_posix()})
    return patch.dict(os.environ, env, clear=True)


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True,
                          encoding="utf-8").stdout


def tracked(path: Path) -> set[str]:
    return set(git(path, "-c", "core.quotepath=false", "ls-files").splitlines())


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.state = self.root / "state"          # PatchRondo's home in these tests
        self.folder = self.root / "project"
        self.folder.mkdir()
        self.env = isolated_git(self.root)
        self.env.start()
        self.addCleanup(self.env.stop)

    def fill(self, folder: Path | None = None) -> Path:
        folder = folder or self.folder
        (folder / "src").mkdir()
        (folder / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
        (folder / "a b è.txt").write_text("words\n", encoding="utf-8")
        (folder / "[ab].txt").write_text("brackets\n", encoding="utf-8")
        (folder / "debug.log").write_text("noise\n", encoding="utf-8")
        (folder / ".gitignore").write_text("*.log\n", encoding="utf-8")
        return folder


class PlanTests(Case):
    def test_the_plan_lists_what_git_would_commit_and_writes_nothing(self):
        self.fill()
        before = sorted(p.relative_to(self.folder).as_posix() for p in self.folder.rglob("*"))
        plan = firstcommit.plan(str(self.folder), self.state)
        self.assertEqual((plan["mode"], plan["message"], plan["path"]), ("init", "Initial commit", str(self.folder)))
        self.assertEqual([f["name"] for f in plan["files"]], [".gitignore", "[ab].txt", "a b è.txt", "src/main.py"])
        self.assertEqual((plan["count"], plan["more"]), (4, 0))
        self.assertEqual(plan["bytes"], sum(f["size"] for f in plan["files"]))
        self.assertEqual(plan["identity"], "Ada Tester <ada@example.invalid>")
        self.assertEqual(sorted(p.relative_to(self.folder).as_posix() for p in self.folder.rglob("*")), before)
        self.assertFalse((self.folder / ".git").exists())
        self.assertEqual(firstcommit.plan(str(self.folder), self.state)["digest"], plan["digest"])

    def test_long_lists_are_shown_in_part_and_counted_whole(self):
        for number in range(firstcommit.SHOWN + 5):
            (self.folder / f"f{number:04}.txt").write_text("x", encoding="utf-8")
        plan = firstcommit.plan(str(self.folder), self.state)
        self.assertEqual((len(plan["files"]), plan["count"], plan["more"]), (firstcommit.SHOWN, firstcommit.SHOWN + 5, 5))

    def test_folders_it_must_not_touch_are_refused(self):
        repo = make_repo(self.root / "repo")
        (repo / "sub").mkdir()
        (self.state / "inside").mkdir(parents=True)
        cases = {
            "relative": "relative/path", "missing": str(self.root / "missing"), "empty": " ", "not text": 5,
            "a drive": self.root.anchor, "the home folder": str(self.folder),
            "PatchRondo's state": str(self.state), "inside PatchRondo's state": str(self.state / "inside"),
            "above PatchRondo's state": str(self.root), "Git's data": str(repo / ".git"),
            "inside a repository": str(repo / "sub"), "a repository with commits": str(repo),
        }
        with patch("pathlib.Path.home", return_value=self.folder):
            for label, raw in cases.items():
                with self.subTest(label):
                    with self.assertRaises(firstcommit.Refused):
                        firstcommit.plan(raw, self.state)
                    self.assertIsNone(firstcommit.offer(raw, self.state))
        self.fill()
        self.assertEqual(firstcommit.offer(str(self.folder), self.state), "init")

    def test_a_repository_git_does_not_open_is_not_initialized_again(self):
        # A `.git` above that Git refuses (here a broken gitfile) is a repository: never a new one inside it.
        outer = self.root / "outer"
        (outer / "inner").mkdir(parents=True)
        (outer / ".git").write_text("not a gitfile\n", encoding="utf-8")
        with self.assertRaises(firstcommit.Refused) as caught:
            firstcommit.plan(str(outer / "inner"), self.state)
        self.assertIn("does not open here", str(caught.exception))
        self.assertFalse((outer / "inner" / ".git").exists())

    def test_too_many_files_or_bytes_are_refused(self):
        self.fill()
        with patch.object(firstcommit, "LIMIT", 3), self.assertRaises(firstcommit.Refused):
            firstcommit.plan(str(self.folder), self.state)
        with patch.object(firstcommit, "SIZE_LIMIT", 10), self.assertRaises(firstcommit.Refused):
            firstcommit.plan(str(self.folder), self.state)


class MakeTests(Case):
    def test_git_init_and_a_commit_of_exactly_the_reviewed_files(self):
        self.fill()
        plan = firstcommit.plan(str(self.folder), self.state)
        self.assertEqual(firstcommit.make(str(self.folder), self.state, plan["digest"]), self.folder)
        self.assertEqual(tracked(self.folder), {f["name"] for f in plan["files"]})
        self.assertEqual(git(self.folder, "log", "--format=%s|%an|%ae").strip(), "Initial commit|Ada Tester|ada@example.invalid")
        self.assertEqual(git(self.folder, "status", "--porcelain"), "")
        found = inspect_repository(Workspace(self.state), str(self.folder))
        self.assertEqual((found["ok"], found["first_commit"]), (True, None))

    def test_a_folder_that_changed_after_the_review_is_refused_untouched(self):
        self.fill()
        plan = firstcommit.plan(str(self.folder), self.state)
        (self.folder / "secret.env").write_text("TOKEN=x\n", encoding="utf-8")
        with self.assertRaises(firstcommit.Changed):
            firstcommit.make(str(self.folder), self.state, plan["digest"])
        self.assertFalse((self.folder / ".git").exists())
        for digest in (None, "", 5):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                firstcommit.make(str(self.folder), self.state, digest)
        self.assertFalse((self.folder / ".git").exists())

    def test_an_empty_folder_gets_an_empty_first_commit(self):
        plan = firstcommit.plan(str(self.folder), self.state)
        self.assertEqual((plan["count"], plan["files"]), (0, []))
        firstcommit.make(str(self.folder), self.state, plan["digest"])
        self.assertEqual((tracked(self.folder), git(self.folder, "log", "--format=%s").strip()), (set(), "Initial commit"))

    def test_a_repository_without_commits_commits_its_index_and_its_new_files(self):
        self.fill()
        git(self.folder, "init", "--quiet")
        (self.folder / "staged-then-deleted.txt").write_text("x", encoding="utf-8")
        git(self.folder, "add", "src/main.py", "staged-then-deleted.txt")
        (self.folder / "staged-then-deleted.txt").unlink()
        self.assertEqual(firstcommit.offer(str(self.folder), self.state), "commit")
        plan = firstcommit.plan(str(self.folder), self.state)
        self.assertEqual(plan["mode"], "commit")
        self.assertEqual([f["name"] for f in plan["files"]], [".gitignore", "[ab].txt", "a b è.txt", "src/main.py"])
        firstcommit.make(str(self.folder), self.state, plan["digest"])
        self.assertEqual(tracked(self.folder), {f["name"] for f in plan["files"]})
        self.assertEqual(git(self.folder, "status", "--porcelain"), "")

    def test_without_a_git_identity_the_typed_one_is_used_for_this_commit_only(self):
        self.env.stop()
        nobody = isolated_git(self.root, identity=False)
        nobody.start()
        self.addCleanup(nobody.stop)
        self.fill()
        plan = firstcommit.plan(str(self.folder), self.state)
        self.assertIsNone(plan["identity"])
        for name, email in ((None, None), ("", "ada@example.invalid"), ("Ada <x>", "ada@example.invalid"),
                            ("Ada", "no-at-sign"), ("Ada", "ada @example.invalid")):
            with self.subTest(name=name, email=email), self.assertRaises(ValueError):
                firstcommit.make(str(self.folder), self.state, plan["digest"], name, email)
            self.assertFalse((self.folder / ".git").exists())  # refused before git init
        firstcommit.make(str(self.folder), self.state, plan["digest"], " Ada Lovelace ", "ada@example.invalid")
        self.assertEqual(git(self.folder, "log", "--format=%an|%ae|%cn").strip(), "Ada Lovelace|ada@example.invalid|Ada Lovelace")
        saved = subprocess.run(["git", "-C", str(self.folder), "config", "--local", "--get-regexp", r"^user\."],
                               capture_output=True, check=False)
        self.assertEqual(saved.returncode, 1)  # no name or email written into the repository's settings

    def test_a_commit_git_refuses_leaves_the_repository_and_says_so(self):
        hooks = self.root / "hooks"
        hooks.mkdir()
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\necho 'refused by the hook' >&2\nexit 1\n", encoding="utf-8", newline="\n")
        hook.chmod(0o755)
        self.env.stop()
        refusing = isolated_git(self.root, hooks=hooks)
        refusing.start()
        self.fill()
        plan = firstcommit.plan(str(self.folder), self.state)
        try:
            with self.assertRaises(RuntimeError) as caught:
                firstcommit.make(str(self.folder), self.state, plan["digest"])
        finally:
            refusing.stop()
        self.assertIn("Git created the repository but did not make the first commit", str(caught.exception))
        self.assertTrue((self.folder / ".git").is_dir())  # left in place, never deleted
        self.env.start()
        self.assertEqual(firstcommit.offer(str(self.folder), self.state), "commit")
        again = firstcommit.plan(str(self.folder), self.state)
        firstcommit.make(str(self.folder), self.state, again["digest"])
        self.assertEqual(tracked(self.folder), {f["name"] for f in plan["files"]})


class RouteTests(ServerCase):
    def setUp(self):
        super().setUp()
        env = isolated_git(self.root)
        env.start()
        self.addCleanup(env.stop)

    def test_inspect_offers_it_and_the_routes_make_it(self):
        folder = self.root / "plain"
        folder.mkdir()
        (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
        found = self.post("/api/projects/inspect", {"path": str(folder)})[1]
        self.assertEqual((found["ok"], found["first_commit"]), (False, "init"))
        self.assertIsNone(self.post("/api/projects/inspect", {"path": str(make_repo(self.root / "r"))})[1]["first_commit"])

        response, plan = self.post("/api/projects/first-commit/plan", {"path": str(folder)})
        self.assertEqual((response.status, [f["name"] for f in plan["files"]]), (200, ["app.py"]))
        (folder / "late.py").write_text("y = 2\n", encoding="utf-8")
        response, data = self.post("/api/projects/first-commit", {"path": str(folder), "digest": plan["digest"]})
        self.assertEqual((response.status, data["code"]), (409, "conflict"))
        self.assertFalse((folder / ".git").exists())

        plan = self.post("/api/projects/first-commit/plan", {"path": str(folder)})[1]
        response, found = self.post("/api/projects/first-commit", {"path": str(folder), "digest": plan["digest"]})
        self.assertEqual((response.status, found["ok"], found["first_commit"], found["path"]), (201, True, None, str(folder)))
        response, data = self.post("/api/projects/first-commit/plan", {"path": str(folder)})
        self.assertEqual((response.status, data["error"]), (400, "That repository already has commits"))
        self.assertEqual(self.post("/api/projects", {"path": str(folder)})[0].status, 201)


if __name__ == "__main__":
    unittest.main()
