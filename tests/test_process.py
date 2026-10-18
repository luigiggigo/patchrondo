import errno
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from patchrondo import process
from patchrondo.process import LineReader, execute

from support import refusing


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


class ProcessTreeTests(unittest.TestCase):
    """`Result.process_tree` says how the processes a command started were held, also when nothing could hold them."""

    def both(self, code, **options):
        """The result without observers and with them: the two ways a command runs."""
        with tempfile.TemporaryDirectory() as d:
            argv = [sys.executable, "-c", code]
            return execute(argv, Path(d), **options), execute(argv, Path(d), on_stdout=lambda chunk: None, **options)

    def test_the_result_says_how_the_processes_were_held(self):
        expected = {True: process.GROUP, False: process.GROUP} if os.name == "posix" else \
            {True: process.JOB, False: process.CHILD}
        for tree, held in expected.items():
            for result in self.both("pass", tree=tree):
                with self.subTest(tree=tree):
                    self.assertEqual((result.returncode, result.process_tree), (0, held))

    @unittest.skipUnless(os.name == "nt", "job objects are native Windows")
    def test_a_child_no_job_object_can_hold_runs_alone_and_says_so(self):
        with patch("patchrondo.process._Job", side_effect=OSError(5, "Access is denied")) as job:
            finished = self.both("pass", tree=True)
            stopped = self.both("import time; time.sleep(30)", tree=True, timeout=0.5)
        self.assertEqual(job.call_count, 4)
        for result in finished:
            self.assertEqual((result.returncode, result.timed_out, result.process_tree), (0, False, process.CHILD))
        for result in stopped:
            self.assertEqual((result.timed_out, result.process_tree), (True, process.CHILD))


# A child that ignores TERM, so that only the last SIGKILL of its group stops it; it says it started.
STUBBORN = ("import os, pathlib, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "pathlib.Path('child.pid').write_text(str(os.getpid())); time.sleep(2); pathlib.Path('leaked').write_text('alive')")
# A leader whose group keeps one zombie once it is gone: a holder in a group of its own starts a member of the
# leader's group and reaps it `hold` seconds after the leader went (argv: folder, hold, holder code).
LEADER = """
import os, pathlib, subprocess, sys, time
subprocess.Popen([sys.executable, "-c", sys.argv[3], str(os.getpgrp()), sys.argv[1], sys.argv[2]], process_group=0)
while not pathlib.Path(sys.argv[1], "ready").exists():
    time.sleep(0.01)
time.sleep(60)
"""
HOLDER = """
import os, pathlib, subprocess, sys, time
group, folder, hold = int(sys.argv[1]), pathlib.Path(sys.argv[2]), float(sys.argv[3])
(folder / "holder.pid").write_text(str(os.getpid()))
member = subprocess.Popen([sys.executable, "-c", "pass"], process_group=group, stdout=subprocess.PIPE)
member.stdout.read()  # the end of its output: it has exited, and nothing reaps it yet
(folder / "ready").write_text(str(member.pid))
parent = os.getppid()
while os.getppid() == parent:
    time.sleep(0.01)
time.sleep(hold)
member.wait()
time.sleep(60)
"""


@unittest.skipUnless(os.name == "posix", "POSIX process groups")
class RefusedGroupTests(unittest.TestCase):
    """The last SIGKILL of a stopped command's group can be refused (EPERM): macOS answers so while what is left of the
    group are zombies not yet reaped (CI run 39135998928). A refusal is no error of the command, is never taken for a
    stopped group, and never takes the place of an interruption."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)

    def run_in(self, folder, code, observed, **options):
        folder.mkdir()
        return execute([sys.executable, "-c", code], folder, **({"on_stdout": lambda chunk: None} if observed else {}),
                       **options)

    def test_a_refusal_that_ends_is_waited_for_and_what_was_started_is_stopped(self):
        parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {STUBBORN!r}]); time.sleep(30)"
        for observed in (False, True):
            folder = self.path / f"observed-{observed}"
            killpg, answers = refusing(2)
            with self.subTest(observed=observed), patch("patchrondo.process.os.killpg", killpg):
                result = self.run_in(folder, parent, observed, timeout=1)
                self.assertEqual((result.timed_out, result.kill_refused), (True, False))
                self.assertEqual(answers, ["sent", "EPERM", "EPERM", "sent"])  # TERM, refused twice, then KILL sent
                self.assertTrue((folder / "child.pid").exists())
        time.sleep(2.5)  # each child would have written its marker by now
        for observed in (False, True):
            self.assertFalse((self.path / f"observed-{observed}" / "leaked").exists(), "a child outlived its group")

    def test_a_refusal_to_the_end_is_said_and_is_no_error_of_the_command(self):
        for observed in (False, True):
            folder, killpg, answers = self.path / f"observed-{observed}", *refusing()
            marker = self.path / f"active-process-{observed}.json"
            started = time.monotonic()
            with self.subTest(observed=observed), patch("patchrondo.process.os.killpg", killpg), \
                    patch("patchrondo.process.GROUP_SECONDS", 0.3):
                result = self.run_in(folder, "import time; time.sleep(30)", observed, timeout=0.5, pid_file=marker)
                self.assertEqual((result.timed_out, result.kill_refused, result.process_tree), (True, True, process.GROUP))
                self.assertGreater(answers.count("EPERM"), 2)  # asked again until the time ran out
                self.assertEqual(answers[0], "sent")  # the leader was signalled, and stopped
                self.assertLess(time.monotonic() - started, 10)
                self.assertFalse(marker.exists())

    def test_an_interruption_is_raised_again_whatever_the_group_answers(self):
        real_wait, real_communicate = subprocess.Popen.wait, subprocess.Popen.communicate
        code = "import os, pathlib, time\npathlib.Path('pid').write_text(str(os.getpid()))\ntime.sleep(60)"

        def ready(folder):
            deadline = time.monotonic() + 20
            while not (folder / "pid").exists() and time.monotonic() < deadline:
                time.sleep(0.01)

        def waited(proc, timeout=None):
            if timeout == 77:  # the wait of `execute` itself, with observers: the user presses Ctrl+C
                ready(self.folder)
                raise KeyboardInterrupt
            return real_wait(proc, timeout)

        def communicated(proc, input=None, timeout=None):
            if timeout == 77:  # the same without observers, once the (empty) input was written and closed
                proc.stdin.close()
                ready(self.folder)
                raise KeyboardInterrupt
            return real_communicate(proc, input, timeout)

        for observed in (False, True):
            self.folder, (killpg, answers) = self.path / f"observed-{observed}", refusing()
            marker = self.path / f"active-process-{observed}.json"
            with self.subTest(observed=observed), patch("patchrondo.process.os.killpg", killpg), \
                    patch("patchrondo.process.GROUP_SECONDS", 0.3), patch.object(subprocess.Popen, "wait", waited), \
                    patch.object(subprocess.Popen, "communicate", communicated):
                with self.assertRaises(KeyboardInterrupt):
                    self.run_in(self.folder, code, observed, timeout=77, pid_file=marker)
                self.assertIn("EPERM", answers)
                self.assertFalse(marker.exists())  # the process ended: the marker went with it

    @unittest.skipUnless(sys.platform == "darwin" or sys.platform.startswith("linux"), "the answer of macOS or Linux")
    def test_a_group_left_with_a_zombie_is_stopped_as_the_system_answers(self):
        """No replaced call: what the system answers for a group whose last member is a zombie that is not reaped.

        macOS refuses (EPERM) until the zombie is reaped, then says the group is
        gone (ESRCH); Linux signals the zombie. Either way nothing is raised.
        """
        real = os.killpg
        for hold, refused in ((0.3, False), (60, sys.platform == "darwin")):
            folder, answers = self.path / f"hold-{hold}", []
            folder.mkdir()

            def killpg(pgid, sig):
                try:
                    real(pgid, sig)
                except OSError as exc:
                    answers.append(errno.errorcode.get(exc.errno, str(exc.errno)))
                    raise
                answers.append("sent")

            try:
                with self.subTest(hold=hold), patch("patchrondo.process.os.killpg", killpg), \
                        patch("patchrondo.process.GROUP_SECONDS", 1.5):
                    result = execute([sys.executable, "-c", LEADER, str(folder), str(hold), HOLDER], folder, timeout=2)
                    self.assertTrue((folder / "ready").exists(), "the zombie was not there before the time limit")
                    self.assertEqual((result.timed_out, result.kill_refused), (True, refused), answers)
                    self.assertEqual(answers[0], "sent", answers)  # TERM, while the leader was there
                    if sys.platform == "darwin":
                        self.assertEqual(answers[-1], "EPERM" if refused else "ESRCH", answers)
                    elif hold == 60:
                        self.assertEqual(answers, ["sent", "sent"])
                    else:  # the holder may have reaped it already
                        self.assertIn(answers[1:], (["sent"], ["ESRCH"]))
            finally:
                try:
                    os.kill(int((folder / "holder.pid").read_text()), signal.SIGKILL)
                except (OSError, ValueError):
                    pass


SLOW_WRITER = """
import pathlib, sys, time
print(repr(time.time()), flush=True)
while not pathlib.Path("release").exists():
    time.sleep(0.01)
print("second", flush=True)
"""


class ObservedProcessTests(unittest.TestCase):
    """`execute` with observers: output is seen while the process runs, and nothing else changes."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)

    def python(self, code, **options):
        return execute([sys.executable, "-c", code], self.path, **options)

    def test_output_is_seen_before_the_process_ends(self):
        seen, first, results = [], threading.Event(), []

        def observer(chunk):
            seen.append((time.time(), chunk))
            first.set()

        marker = self.path / "active-process.json"
        runner = threading.Thread(target=lambda: results.append(self.python(SLOW_WRITER, on_stdout=observer, pid_file=marker)))
        runner.start()
        try:
            self.assertTrue(first.wait(20), "nothing was observed while the process was running")
            received, chunk = seen[0]
            self.assertLess(received - float(chunk.decode().strip()), 1.0)  # within a second of being written
            self.assertTrue(runner.is_alive())
            self.assertTrue(marker.exists())  # the process is still recorded as running
            self.assertEqual(results, [])
        finally:
            (self.path / "release").touch()
            runner.join(20)
        self.assertFalse(runner.is_alive())
        self.assertEqual(results[0].returncode, 0)
        self.assertEqual(results[0].stdout.split()[1], "second")
        self.assertEqual(b"".join(chunk for _, chunk in seen).decode(), results[0].stdout)
        self.assertFalse(marker.exists())

    def test_slow_progressive_output_arrives_progressively(self):
        times = []
        code = "import time\nfor n in range(5):\n    print('line', n, flush=True)\n    time.sleep(0.15)"
        result = self.python(code, on_stdout=lambda chunk: times.append(time.monotonic()))
        self.assertEqual(result.stdout.split(), ["line", "0", "line", "1", "line", "2", "line", "3", "line", "4"])
        self.assertGreaterEqual(len(times), 3)
        self.assertGreater(times[-1] - times[0], 0.3)  # not delivered in one piece at the end

    def test_both_streams_at_once_and_very_large_do_not_block_each_other(self):
        sizes = {"out": 0, "err": 0}
        code = ("import sys\nfor n in range(400):\n    sys.stdout.write('o' * 5000)\n    sys.stderr.write('e' * 5000)\n"
                "sys.stdout.write('END-OUT')\nsys.stderr.write('END-ERR')")
        started = time.monotonic()
        result = self.python(code, timeout=120, on_stdout=lambda chunk: sizes.__setitem__("out", sizes["out"] + len(chunk)),
                             on_stderr=lambda chunk: sizes.__setitem__("err", sizes["err"] + len(chunk)))
        self.assertLess(time.monotonic() - started, 60)
        self.assertEqual((result.returncode, result.timed_out), (0, False))
        self.assertEqual(sizes, {"out": 2_000_007, "err": 2_000_007})  # observers see everything
        self.assertLess(len(result.stdout), 61000)  # the result stays bounded, as without observers
        self.assertLess(len(result.stderr), 61000)
        self.assertTrue(result.stdout.endswith("END-OUT") and result.stderr.endswith("END-ERR"))
        self.assertIn("...[output truncated]...", result.stdout)

    def test_large_input_while_the_child_is_already_writing(self):
        code = ("import sys\nsys.stdout.write('x' * 300000)\nsys.stdout.flush()\n"
                "data = sys.stdin.buffer.read()\nprint('READ', len(data.decode('utf-8').replace('\\r\\n', '\\n')))")
        text = "riga ↔ é\n" * 120000
        result = self.python(code, stdin=text, timeout=120, on_stdout=lambda chunk: None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.rstrip().endswith(f"READ {len(text)}"))

    def test_a_character_split_between_two_writes_is_decoded_whole(self):
        code = ("import sys, time\nout = sys.stdout.buffer\nout.write(b'{\"text\": \"caff\\xc3'); out.flush(); time.sleep(0.3)\n"
                "out.write(b'\\xa8 \\xe2\\x86'); out.flush(); time.sleep(0.3)\nout.write(b'\\x94\"}\\n{\"half\": '); out.flush()")
        found, chunks = [], []
        reader = LineReader(found.append)

        def observer(chunk):
            chunks.append(chunk)
            reader.feed(chunk)

        result = self.python(code, on_stdout=observer)
        reader.close()
        self.assertGreaterEqual(len(chunks), 3)
        self.assertEqual(found, ['{"text": "caffè ↔"}', '{"half": '])  # the unfinished line is handed over at the end
        self.assertEqual(result.stdout, '{"text": "caffè ↔"}\n{"half": ')

    def test_observers_do_not_change_what_callers_get(self):
        code = ("import sys\ndata = sys.stdin.buffer.read()\nprint(repr(data))\nprint('second line')\n"
                "sys.stderr.write('warning\\n')\nsys.exit(3)")
        plain = self.python(code, stdin="one\ntwo ↔\n")
        observed = self.python(code, stdin="one\ntwo ↔\n", on_stdout=lambda chunk: None, on_stderr=lambda chunk: None)
        self.assertEqual(observed, plain)
        self.assertEqual(plain.returncode, 3)
        with self.assertRaises(FileNotFoundError):
            execute(["patchrondo-no-such-command"], self.path, on_stdout=lambda chunk: None)
        with self.assertRaises(ValueError):
            execute([], self.path, on_stdout=lambda chunk: None)

    def test_an_observer_that_fails_is_dropped_and_the_run_is_unaffected(self):
        calls, errors = [], []

        def broken(chunk):
            calls.append(chunk)
            raise RuntimeError("observer bug")

        code = "import sys, time\nfor n in range(3):\n    print('out', n, flush=True); print('err', n, file=sys.stderr, flush=True); time.sleep(0.1)"
        result = self.python(code, on_stdout=broken, on_stderr=errors.append)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)  # not called again after it raised
        self.assertEqual(result.stdout.split(), ["out", "0", "out", "1", "out", "2"])
        self.assertEqual(b"".join(errors).decode().split(), ["err", "0", "err", "1", "err", "2"])

    def test_timeout_stops_the_process_and_keeps_what_it_had_written(self):
        seen = []
        marker = self.path / "active-process.json"
        started = time.monotonic()
        result = self.python("import time\nprint('before the timeout', flush=True)\ntime.sleep(60)", timeout=1.5,
                             pid_file=marker, on_stdout=seen.append)
        self.assertLess(time.monotonic() - started, 20)
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("before the timeout", result.stdout)
        self.assertEqual(b"".join(seen).decode().strip(), "before the timeout")
        self.assertFalse(marker.exists())

    def test_an_interrupt_stops_the_process_and_is_raised_again(self):
        real, seen = subprocess.Popen.wait, threading.Event()

        def interrupted(process, timeout=None):
            if timeout == 77:  # the wait of `execute` itself: the user presses Ctrl+C while output is flowing
                seen.wait(20)
                raise KeyboardInterrupt
            return real(process, timeout)

        marker = self.path / "active-process.json"
        code = "import os, pathlib, time\npathlib.Path('pid').write_text(str(os.getpid()))\nprint('working', flush=True)\ntime.sleep(60)"
        started = time.monotonic()
        with patch.object(subprocess.Popen, "wait", interrupted), self.assertRaises(KeyboardInterrupt):
            self.python(code, timeout=77, pid_file=marker, on_stdout=lambda chunk: seen.set())
        self.assertLess(time.monotonic() - started, 20)
        self.assertFalse(marker.exists())  # removed only once the process is known to have ended
        self.assertTrue((self.path / "pid").exists())

    def test_a_descendant_holding_the_pipe_does_not_hold_the_caller(self):
        child = "import os, pathlib, time; pathlib.Path('child.pid').write_text(str(os.getpid())); time.sleep(20)"
        code = (f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', {child!r}], stdout=sys.stdout, stderr=sys.stderr)\n"
                "import pathlib\nwhile not pathlib.Path('child.pid').exists():\n    time.sleep(0.01)\nprint('parent done', flush=True)")
        started = time.monotonic()
        try:
            with patch("patchrondo.process.DRAIN_SECONDS", 0.5):
                result = self.python(code, on_stdout=lambda chunk: None, on_stderr=lambda chunk: None)
            self.assertLess(time.monotonic() - started, 12)
            self.assertEqual((result.returncode, result.stdout.strip()), (0, "parent done"))
        finally:
            pid = int((self.path / "child.pid").read_text())
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            time.sleep(0.3)  # let it release the folder before it is removed

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_timeout_kills_descendants_also_when_observed(self):
        child = "import signal,time,pathlib; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(3); pathlib.Path('leaked').write_text('alive')"
        parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)"
        result = self.python(parent, timeout=1, on_stdout=lambda chunk: None)
        self.assertTrue(result.timed_out)
        time.sleep(3)
        self.assertFalse((self.path / "leaked").exists())


if __name__ == "__main__":
    unittest.main()
