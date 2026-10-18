"""The shared fixtures of the tests: each test gets a repository of its own, even when it is copied."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from support import copied, make_repo


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


class CopiedRepositoryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()

    def test_each_copy_is_a_clean_repository_of_its_own(self):
        first, second = make_repo(self.root / "first"), make_repo(self.root / "with space" / "second")
        self.assertEqual(git(first, "rev-parse", "HEAD"), git(second, "rev-parse", "HEAD"))
        for repo in (first, second):
            self.assertEqual(git(repo, "status", "--porcelain", "--untracked-files=all"), "")
            self.assertEqual(Path(git(repo, "rev-parse", "--absolute-git-dir")).resolve(), repo / ".git")
            self.assertEqual(Path(git(repo, "rev-parse", "--show-toplevel")).resolve(), repo)
            self.assertEqual(git(repo, "log", "--format=%an <%ae> %s"), "T <t@example.invalid> base")
        (first / "feature.txt").write_text("good", encoding="utf-8")
        git(first, "add", "feature.txt")
        git(first, "commit", "-qm", "change")
        git(first, "worktree", "add", "-q", "-b", "task", str(self.root / "worktree"))
        self.assertNotEqual(git(first, "rev-parse", "HEAD"), git(second, "rev-parse", "HEAD"))
        self.assertEqual(git(second, "status", "--porcelain", "--untracked-files=all"), "")
        self.assertEqual(git(second, "worktree", "list", "--porcelain").count("worktree "), 1)
        # What one test did is in no later copy either.
        third = make_repo(self.root / "third")
        self.assertEqual(git(third, "rev-parse", "HEAD"), git(second, "rev-parse", "HEAD"))
        self.assertFalse((third / "feature.txt").exists())
        with self.assertRaises(FileExistsError):
            make_repo(third)

    def test_a_folder_is_built_once_per_process_and_copied_after(self):
        calls = []

        def build(path: Path) -> None:
            calls.append(path)
            path.mkdir(parents=True)
            (path / "file.txt").write_text("built", encoding="utf-8")

        copies = [copied(build, self.root / f"copy-{n}") for n in range(3)]
        self.assertEqual(len(calls), 1)
        self.assertNotIn(calls[0], copies)
        self.assertEqual([(copy / "file.txt").read_text(encoding="utf-8") for copy in copies], ["built"] * 3)

    def test_two_builders_of_one_name_keep_their_own_folders(self):
        calls = []

        def factory(value: str):
            def build(path: Path) -> None:
                calls.append(value)
                path.mkdir(parents=True)
                (path / "value.txt").write_text(value, encoding="utf-8")
            return build

        first, second = factory("A"), factory("B")
        # Each makes the same folder whenever it is called; only the builder tells them apart, not its name.
        self.assertEqual((first.__module__, first.__qualname__), (second.__module__, second.__qualname__))
        copies = [copied(build, self.root / f"copy-{n}") for n, build in enumerate((first, second, first, second))]
        self.assertEqual([(copy / "value.txt").read_text(encoding="utf-8") for copy in copies], ["A", "B", "A", "B"])
        self.assertEqual(calls, ["A", "B"])  # each built once, then copied

    def test_a_folder_is_built_without_git_maintenance_left_running_on_it(self):
        # A commit can start Git's maintenance in the background, still at work on the folder while it is copied:
        # on GitHub's runners (Git 2.55 and 2.56) a copy met a lock file that went away under it.
        seen = {}

        def build(path: Path) -> None:
            path.mkdir(parents=True)

            def run(*args: str, **env: str) -> subprocess.CompletedProcess:
                return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True,
                                      env={**os.environ, **env})

            run("init", "-q")
            seen.update({name: run("config", "--get", name).stdout.strip()
                         for name in ("user.name", "maintenance.auto", "gc.auto")})
            (path / "file.txt").write_text("built", encoding="utf-8")
            run("add", ".")
            seen["commit"] = run("-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm",
                                 "base", GIT_TRACE="1")

        settings = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "Kept"}
        with patch.dict(os.environ, settings):
            copy = copied(build, self.root / "copy")
            # What the build had is gone once it is built: a test's own Git commands run as they did before.
            self.assertEqual({name: value for name, value in os.environ.items() if name.startswith("GIT_CONFIG")},
                             settings)
        self.assertEqual(seen["commit"].returncode, 0, seen["commit"].stderr)
        self.assertEqual([seen["user.name"], seen["maintenance.auto"], seen["gc.auto"]], ["Kept", "false", "0"])
        self.assertNotIn("maintenance run", seen["commit"].stderr)
        self.assertNotIn("gc --auto", seen["commit"].stderr)
        self.assertEqual(git(copy, "log", "--format=%an %s"), "Kept base")
        self.assertEqual(subprocess.run(["git", "-C", str(copy), "config", "--local", "--get", "maintenance.auto"],
                                        capture_output=True).returncode, 1)  # nothing of it is in the copy


if __name__ == "__main__":
    unittest.main()
