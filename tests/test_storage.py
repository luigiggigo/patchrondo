"""Saving and unlocking while another process has the file open.

Windows refuses to replace or delete a file that is open for reading. The
interface reads `state.json` and `.run.lock` of a task while its run saves and
releases them, so the writer has to wait for the reader instead of failing.
"""
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from patchrondo import storage
from patchrondo.storage import TaskLock, read_json, save_json


def denied(times: int, real):
    """A stand-in that refuses `times` calls as Windows does, then calls `real`."""
    calls = []

    def action(*args, **kwargs):
        calls.append(args)
        if len(calls) <= times:
            raise PermissionError(13, "Access is denied")
        return real(*args, **kwargs)

    return action, calls


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        self.file = self.dir / "state.json"
        save_json(self.file, {"n": 1})

    def held(self, file: Path, seconds: float = 0.3):
        """Open `file` for reading, as another process would, and close it after `seconds`."""
        handle = open(file, encoding="utf-8")
        handle.read()
        timer = threading.Timer(seconds, handle.close)
        self.addCleanup(handle.close)
        self.addCleanup(timer.cancel)
        timer.start()

    def leftovers(self) -> list:
        return sorted(entry.name for entry in self.dir.iterdir() if entry.name.startswith(".tmp-"))


class SaveTests(Case):
    def test_save_waits_for_a_reader_that_has_the_file_open(self):
        self.held(self.file)  # a real handle: on Windows the replace is refused until it is closed
        save_json(self.file, {"n": 2})
        self.assertEqual(read_json(self.file), {"n": 2})
        self.assertEqual(self.leftovers(), [])

    def test_a_refused_replace_is_repeated_until_it_succeeds(self):
        action, calls = denied(3, os.replace)
        with patch.object(storage, "SHARING_RETRY_SECONDS", 30.0), patch("patchrondo.storage.os.replace", side_effect=action):
            save_json(self.file, {"n": 2})
        self.assertEqual(len(calls), 4)
        self.assertEqual(read_json(self.file), {"n": 2})
        self.assertEqual(self.leftovers(), [])

    def test_a_replace_refused_for_the_whole_wait_fails_and_keeps_the_previous_file(self):
        action, calls = denied(200, os.replace)  # finite, so a missing bound ends in a save, not in a hang
        with patch.object(storage, "SHARING_RETRY_SECONDS", 0.2), patch("patchrondo.storage.os.replace", side_effect=action), \
                self.assertRaises(PermissionError):
            save_json(self.file, {"n": 2})
        self.assertTrue(1 < len(calls) < 200, len(calls))
        self.assertEqual(read_json(self.file), {"n": 1})
        self.assertEqual(self.leftovers(), [])

    def test_without_a_wait_a_refusal_fails_at_once(self):
        action, calls = denied(50, os.replace)
        with patch.object(storage, "SHARING_RETRY_SECONDS", 0.0), patch("patchrondo.storage.os.replace", side_effect=action), \
                self.assertRaises(PermissionError):
            save_json(self.file, {"n": 2})
        self.assertEqual(len(calls), 1)
        self.assertEqual(storage.SHARING_RETRY_SECONDS, 2.0 if os.name == "nt" else 0.0)  # only Windows waits

    def test_other_errors_are_not_repeated(self):
        calls = []

        def failing(*args):
            calls.append(args)
            raise OSError(28, "No space left on device")

        with patch.object(storage, "SHARING_RETRY_SECONDS", 30.0), patch("patchrondo.storage.os.replace", side_effect=failing), \
                self.assertRaises(OSError):
            save_json(self.file, {"n": 2})
        self.assertEqual(len(calls), 1)
        self.assertEqual(read_json(self.file), {"n": 1})
        self.assertEqual(self.leftovers(), [])


class LockTests(Case):
    def test_lock_release_waits_for_a_reader_that_has_the_file_open(self):
        with TaskLock(self.dir) as lock:
            self.held(lock.path)
        self.assertFalse(lock.path.exists())
        with TaskLock(self.dir):  # and the task can be run again
            pass

    def test_a_refused_lock_release_is_repeated_until_it_succeeds(self):
        action, calls = denied(3, Path.unlink)
        with patch.object(storage, "SHARING_RETRY_SECONDS", 30.0):
            with TaskLock(self.dir) as lock:
                with patch.object(Path, "unlink", autospec=True, side_effect=action):
                    lock.__exit__(None, None, None)
        self.assertEqual(len(calls), 4)
        self.assertFalse(lock.path.exists())


if __name__ == "__main__":
    unittest.main()
