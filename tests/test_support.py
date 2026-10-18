"""The shared fixtures of the tests: each test gets a repository of its own, even when it is copied."""
from pathlib import Path
import subprocess
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
