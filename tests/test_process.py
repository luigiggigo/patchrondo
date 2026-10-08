import os
from pathlib import Path
import sys
import tempfile
import unittest

from patchrondo.process import execute


class ProcessTests(unittest.TestCase):
    def test_utf8_stdin_and_output(self):
        with tempfile.TemporaryDirectory() as d:
            result = execute([sys.executable, "-c",
                              "import sys; print(sys.stdin.buffer.read().decode('utf-8'))"],
                             Path(d), stdin="café ↔ résumé")
        self.assertEqual(result.returncode, 0)
        # Explicit child encoding makes this independent of the system locale.
        self.assertIn("café", result.stdout)

    def test_large_output_is_bounded_and_keeps_tail(self):
        with tempfile.TemporaryDirectory() as d:
            result = execute([sys.executable, "-c",
                              "import sys; sys.stdout.write('x' * 2000000 + 'END'); sys.stderr.write('y' * 2000000)"], Path(d))
        self.assertEqual(result.returncode, 0)
        self.assertLess(len(result.stdout), 61000)
        self.assertLess(len(result.stderr), 61000)
        self.assertTrue(result.stdout.endswith("END"))

    def test_timeout_cleans_process_marker(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            marker = path / "active-process.json"
            result = execute([sys.executable, "-c", "import time; time.sleep(30)"],
                             path, timeout=0.1, pid_file=marker)
            self.assertTrue(result.timed_out)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_timeout_kills_child_ignoring_sigterm(self):
        import time
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            child = "import signal,time,pathlib; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(3); pathlib.Path('leaked').write_text('alive')"
            parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)"
            result = execute([sys.executable, "-c", parent], path, timeout=1)
            self.assertTrue(result.timed_out)
            time.sleep(3)
            self.assertFalse((path / "leaked").exists())


if __name__ == "__main__":
    unittest.main()
