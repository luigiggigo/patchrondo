"""Supervision of an agent CLI's process tree (Phase 2.5, Step 09B), on real local processes.

`process.execute(supervise=True)` holds the whole tree of a recorded agent
call and confirms its end: on native Windows a job object made before the
process, which is created suspended, put in the job, recorded, and only then
resumed; the job stops everything in it when its last handle closes, so when
the run that holds it ends however it ends. On POSIX the process group. Once
the CLI exits by itself, what it left running is stopped and the end of the
tree confirmed. `procinfo.tree_state` reads what a process file or a launch
record names. Every process here is a small Python script of this test; a
"heartbeat" grandchild writes a file every 50 ms, so a stopped tree is one
whose heartbeat stops. No provider CLI is started: the engine tests reach a
scripted CLI (`support.ScriptedCLI`) through `patchrondo.providers.execute`.

Tests that need a job object run on native Windows only, and POSIX groups on
POSIX only: each platform skips what only the other can show, and a skip is
not a pass.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import ScriptedCLI  # noqa: E402
from test_auto_continuation import ContinuationCase  # noqa: E402

import patchrondo  # noqa: E402
from patchrondo import execution, procinfo, process, reconciliation, storage  # noqa: E402
from patchrondo.execution import Termination  # noqa: E402
from patchrondo.storage import TaskLock, save_json  # noqa: E402

WINDOWS = os.name == "nt"
only_windows = unittest.skipUnless(WINDOWS, "a job object exists on native Windows only")
only_posix = unittest.skipUnless(os.name == "posix", "process groups are POSIX")
SRC = str(Path(patchrondo.__file__).resolve().parent.parent)

TREE = r'''import pathlib, subprocess, sys, time
mode, folder = sys.argv[1], pathlib.Path(sys.argv[2])
if mode == "mark":            # shows that it ran, at once
    (folder / "ran").write_text("yes")
elif mode == "child_waited":  # a child that ends before its parent
    subprocess.run([sys.executable, "-c", "pass"], check=True)
elif mode == "grandchild":    # a child that starts the heartbeat and ends; the parent waits for the beat
    subprocess.run([sys.executable, __file__, "child", str(folder)], check=True)
    while not (folder / "beat").exists():
        time.sleep(0.02)
    if "--stay" in sys.argv:
        time.sleep(120)
elif mode == "child":
    beat = subprocess.Popen([sys.executable, __file__, "beat", str(folder)])
    (folder / "beat.pid").write_text(str(beat.pid))
elif mode == "beat":
    end = time.time() + 120
    while time.time() < end:
        (folder / "beat").write_text(repr(time.monotonic()))
        time.sleep(0.05)
'''

OWNER = r'''import json, pathlib, sys
sys.path.insert(0, sys.argv[3])
from patchrondo import process
folder, tree = pathlib.Path(sys.argv[1]), sys.argv[2]
launch = process.Launch(on_start=lambda: (folder / "meta.json").write_text(json.dumps(launch.recorded())))
process.execute([sys.executable, tree, "grandchild", str(folder), "--stay"], folder, timeout=120, supervise=True,
                launch=launch)
'''


def beating(folder: Path, name: str = "beat", seconds: float = 0.6) -> bool:
    """Whether the heartbeat in `folder` still beats: its file changes within `seconds`."""
    file = folder / name
    try:
        before = file.stat().st_mtime_ns
    except FileNotFoundError:
        return False
    time.sleep(seconds)
    return file.stat().st_mtime_ns != before


def stops(folder: Path, name: str = "beat", within: float = 5.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not beating(folder, name, 0.3):
            return True
    return False


def wait_for(file: Path, within: float = 30.0) -> None:
    deadline = time.monotonic() + within
    while not file.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"{file.name} never appeared")
        time.sleep(0.02)


class TreeCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name).resolve()
        self.tree = self.folder / "tree.py"
        self.tree.write_text(TREE, encoding="utf-8", newline="\n")
        self.pid_file = self.folder / "cli.active-process.json"
        self.addCleanup(self.stop_beats)

    def stop_beats(self):
        """Never leave a heartbeat behind, whatever a test showed."""
        for file in self.folder.rglob("beat.pid"):
            try:
                pid = int(file.read_text())
                if WINDOWS:
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)
                else:
                    os.kill(pid, signal.SIGKILL)
            except (OSError, ValueError):
                pass

    def execute(self, mode, *extra, timeout=60, observed=False, launch=None, folder=None):
        return process.execute([sys.executable, str(self.tree), mode, str(folder or self.folder), *extra], self.folder,
                               timeout=timeout, pid_file=self.pid_file, supervise=True, launch=launch,
                               **({"on_stdout": lambda chunk: None} if observed else {}))

    def expected_tree(self) -> str:
        return process.JOB if WINDOWS else process.GROUP


class SupervisedCallTests(TreeCase):
    def test_a_call_that_ends_by_itself_is_held_recorded_and_its_tree_confirmed_ended(self):
        seen = []

        def launching():
            seen.append(("launching", launch.pid, self.pid_file.exists()))

        def started():
            recorded = json.loads(self.pid_file.read_text(encoding="utf-8"))
            if WINDOWS:
                time.sleep(0.3)  # still suspended: it has run nothing of its own while its records are written
            seen.append(("started", recorded, (self.folder / "ran").exists()))

        def ended():
            seen.append(("ended", self.pid_file.exists(), launch.tree_ended))

        launch = process.Launch(on_start=started, on_launch=launching, on_end=ended)
        result = self.execute("mark", launch=launch)
        self.assertEqual((result.returncode, result.timed_out, result.kill_refused), (0, False, False))
        self.assertEqual((result.process_tree, result.tree_ended, result.left_behind), (self.expected_tree(), True, False))
        self.assertEqual(seen[0], ("launching", None, False))  # before anything existed
        recorded, ran = seen[1][1], seen[1][2]
        self.assertEqual({key: recorded[key] for key in ("pid", "tree", "kill_on_close", "owner_pid")},
                         {"pid": launch.pid, "tree": self.expected_tree(), "kill_on_close": WINDOWS,
                          "owner_pid": os.getpid()})
        self.assertTrue(isinstance(recorded["start"], str) and recorded["start"])
        self.assertEqual(recorded["owner_start"], procinfo.identity())
        if WINDOWS:
            self.assertFalse(ran)
        self.assertEqual(seen[2], ("ended", True, True))  # recorded before its process file is removed
        self.assertFalse(self.pid_file.exists())
        self.assertTrue((self.folder / "ran").exists())
        self.assertTrue(launch.supervised and not launch.stopped)

    def test_a_child_that_ended_before_its_parent_leaves_nothing_behind(self):
        result = self.execute("child_waited")
        self.assertEqual((result.returncode, result.tree_ended, result.left_behind), (0, True, False))

    def test_a_grandchild_left_running_is_stopped_and_said(self):
        for observed in (False, True):
            with self.subTest(observed=observed):
                folder = self.folder / ("observed" if observed else "plain")
                folder.mkdir()
                launch = process.Launch()
                result = self.execute("grandchild", observed=observed, launch=launch, folder=folder)
                self.assertEqual((result.returncode, result.tree_ended, result.left_behind, result.kill_refused),
                                 (0, True, True, False))
                self.assertFalse(launch.stopped)  # the CLI ended by itself; only what it left was stopped
                self.assertTrue(stops(folder))

    def test_what_a_cli_left_that_cannot_be_confirmed_stopped_is_said(self):
        launch = process.Launch()
        refused = patch.object(process._Job, "empty_within", return_value=False) if WINDOWS \
            else patch("patchrondo.process._kill_group", return_value=True)
        with refused:
            result = self.execute("grandchild", launch=launch)
        self.assertEqual((result.returncode, result.timed_out), (0, False))
        self.assertEqual((result.tree_ended, result.left_behind, result.kill_refused), (False, True, True))
        self.assertTrue(launch.stopped and launch.kill_refused)  # what it started may still run: never an ended tree

    def test_a_timeout_stops_the_whole_tree(self):
        launch = process.Launch()
        result = self.execute("grandchild", "--stay", timeout=4, launch=launch)
        self.assertTrue(result.timed_out)
        self.assertEqual((result.kill_refused, result.tree_ended), (False, True))
        self.assertTrue(launch.stopped and launch.tree_ended)
        self.assertTrue(stops(self.folder))
        self.assertFalse(self.pid_file.exists())

    def test_an_interruption_stops_the_whole_tree(self):
        real = subprocess.Popen.wait
        raised = []

        def wait(proc, timeout=None):
            if not raised and timeout is not None and timeout >= 30:  # the call's own wait, not the stop's
                wait_for(self.folder / "beat")
                raised.append(True)
                raise KeyboardInterrupt
            return real(proc, timeout)

        launch = process.Launch()
        with patch.object(subprocess.Popen, "wait", wait), self.assertRaises(KeyboardInterrupt):
            self.execute("grandchild", "--stay", observed=True, launch=launch)
        self.assertTrue(launch.stopped)
        self.assertEqual((launch.kill_refused, launch.tree_ended), (False, True))
        self.assertTrue(stops(self.folder))

    def test_a_stop_that_cannot_be_confirmed_is_never_taken_for_one(self):
        launch = process.Launch()
        refused = patch.object(process._Job, "empty_within", return_value=False) if WINDOWS \
            else patch("patchrondo.process._kill_group", return_value=True)
        with refused:
            result = self.execute("grandchild", "--stay", timeout=3, launch=launch)
        self.assertTrue(result.timed_out)
        self.assertEqual((result.kill_refused, result.tree_ended, launch.kill_refused), (True, False, True))
        self.assertTrue(stops(self.folder))  # the job (Windows) or the first signal stopped it; nothing said so

    def test_no_unrelated_process_is_stopped(self):
        other = self.folder / "other"
        other.mkdir()
        unrelated = subprocess.Popen([sys.executable, str(self.tree), "beat", str(other)],
                                     **({} if WINDOWS else {"start_new_session": True}))
        self.addCleanup(unrelated.kill)
        wait_for(other / "beat")
        result = self.execute("grandchild", "--stay", timeout=3)
        self.assertTrue(result.timed_out and result.tree_ended)
        self.assertTrue(stops(self.folder))
        self.assertIsNone(unrelated.poll())
        self.assertTrue(beating(other))

    def test_without_its_identity_a_process_is_never_shown_ended(self):
        launch = process.Launch()
        with patch("patchrondo.process.procinfo.identity", return_value=None):
            self.execute("mark", launch=launch)
        self.assertIsNone(launch.identity)
        found = procinfo.tree_state(launch.recorded())
        if WINDOWS:
            self.assertEqual(found, (procinfo.UNKNOWN, "no_identity"))
        # On any system an attempt is never settled from a PID alone.
        finding, _, unknown = reconciliation._judge([launch.recorded()])
        self.assertEqual((finding, unknown), (reconciliation.Finding.UNCERTAIN, ["no_identity"]))

    def test_a_recycled_pid_is_never_the_recorded_process(self):
        other = "win:1" if WINDOWS else "linux:1"  # an identity this process does not have
        self.assertEqual(procinfo.state(os.getpid(), other), procinfo.DEAD)
        found, code = procinfo.tree_state({"pid": os.getpid(), "start": other, "tree": process.CHILD})
        self.assertNotEqual(code, "process_running")
        self.assertEqual((found, code), (procinfo.UNKNOWN, "descendants_unknown"))

    def test_a_tree_nothing_held_stays_unknown(self):
        launch = process.Launch()
        self.execute("mark", launch=launch)
        recorded = {**launch.recorded(), "tree": process.CHILD, "kill_on_close": False}
        self.assertEqual(procinfo.tree_state(recorded), (procinfo.UNKNOWN, "descendants_unknown"))
        self.assertIsNotNone(storage.may_run(recorded, recorded["pid"]))
        self.assertIsNone(storage.may_run(recorded, recorded["pid"], leader_only=True))  # a release: the user's word


@only_windows
class WindowsJobTests(TreeCase):
    def test_a_job_that_refuses_the_process_runs_the_call_without_one(self):
        launch = process.Launch()
        with patch.object(process._Job, "assign", side_effect=OSError(5, "Access is denied")):
            result = self.execute("mark", launch=launch)
        self.assertEqual((result.returncode, result.process_tree, result.tree_ended), (0, process.CHILD, None))
        self.assertFalse(launch.kill_on_close)
        self.assertTrue((self.folder / "ran").exists())  # resumed, unsupervised, and said so

    def test_a_job_that_cannot_be_made_first_is_one_that_does_not_stop_its_processes(self):
        launch = process.Launch()
        with patch("patchrondo.process._supervised_job", return_value=None):
            result = self.execute("mark", launch=launch)
        self.assertEqual((result.process_tree, result.tree_ended), (process.JOB, True))
        self.assertFalse(launch.kill_on_close)
        self.assertEqual(procinfo.tree_state(launch.recorded())[1], "descendants_unknown")

    def test_a_process_that_cannot_be_resumed_never_runs(self):
        launch = process.Launch()
        with patch("patchrondo.process._resume", side_effect=OSError(0, "not resumed")), self.assertRaises(OSError):
            self.execute("mark", launch=launch)
        self.assertFalse((self.folder / "ran").exists())
        self.assertFalse(self.pid_file.exists())
        self.assertTrue(launch.stopped and launch.tree_ended)

    def test_a_live_process_of_the_job_keeps_it_alive_and_closing_the_job_stops_it(self):
        job = process._Job(kill_on_close=True)
        self.addCleanup(job.close)
        leader = subprocess.Popen([sys.executable, str(self.tree), "grandchild", str(self.folder)],
                                  creationflags=process.CREATE_SUSPENDED)
        job.assign(leader)
        token = procinfo.identity(leader.pid)
        process._resume(leader.pid)
        leader.wait(60)
        self.assertTrue(beating(self.folder))
        recorded = {"pid": leader.pid, "start": token, "tree": process.JOB, "kill_on_close": True,
                    "owner_pid": os.getpid(), "owner_start": procinfo.identity()}
        self.assertEqual(procinfo.tree_state(recorded), (procinfo.ALIVE, "owner_running"))
        runs = self.folder / "task" / "runs" / "iteration-001"
        runs.mkdir(parents=True)
        save_json(runs / "developer.active-process.json", recorded)
        save_json(self.folder / "task" / ".run.lock", {"pid": -1})
        with self.assertRaisesRegex(RuntimeError, "still active"):
            with TaskLock(self.folder / "task", force=True):
                pass
        self.assertEqual(job.active(), 1)
        job.close()  # as when the run that holds it ends: the system stops what is in it
        self.assertTrue(stops(self.folder))

    def test_when_the_run_holding_the_job_ends_the_system_stops_its_tree(self):
        owner = self.folder / "owner.py"
        owner.write_text(OWNER, encoding="utf-8", newline="\n")
        run = subprocess.Popen([sys.executable, str(owner), str(self.folder), str(self.tree), SRC])
        self.addCleanup(run.kill)
        wait_for(self.folder / "meta.json")
        wait_for(self.folder / "beat")
        recorded = json.loads((self.folder / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(procinfo.tree_state(recorded), (procinfo.ALIVE, "process_running"))
        run.kill()  # the run is lost, as in a crash: its job's last handle closes with it
        run.wait(30)
        self.assertTrue(stops(self.folder))
        deadline = time.monotonic() + 10
        while procinfo.tree_state(recorded)[0] != procinfo.DEAD and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(procinfo.tree_state(recorded), (procinfo.DEAD, "job_closed"))


@only_posix
class PosixGroupTests(TreeCase):
    def test_a_live_member_of_the_group_keeps_it_alive(self):
        leader = subprocess.Popen([sys.executable, str(self.tree), "grandchild", str(self.folder)], start_new_session=True)
        token = procinfo.identity(leader.pid)
        self.addCleanup(lambda: os.killpg(leader.pid, signal.SIGKILL) if procinfo.group_state(leader.pid)
                        == procinfo.ALIVE else None)
        leader.wait(60)
        self.assertTrue(beating(self.folder))
        self.assertEqual(procinfo.tree_state({"pid": leader.pid, "start": token}), (procinfo.ALIVE, "group_running"))
        # A process file of an earlier version names the PID only: its group counts too.
        self.assertEqual(storage.may_run({"pid": leader.pid}, leader.pid), "group_running")
        runs = self.folder / "task" / "runs" / "iteration-001"
        runs.mkdir(parents=True)
        save_json(runs / "developer.active-process.json", {"pid": leader.pid})
        save_json(self.folder / "task" / ".run.lock", {"pid": -1})
        with self.assertRaisesRegex(RuntimeError, "still active"):
            with TaskLock(self.folder / "task", force=True):
                pass
        os.killpg(leader.pid, signal.SIGKILL)
        self.assertTrue(stops(self.folder))
        deadline = time.monotonic() + 10
        while procinfo.group_state(leader.pid) != procinfo.DEAD and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(procinfo.tree_state({"pid": leader.pid, "start": token}), (procinfo.DEAD, "group_gone"))
        with TaskLock(self.folder / "task", force=True):
            pass

    def test_when_the_run_is_lost_its_group_goes_on_and_is_shown_running(self):
        """The difference from native Windows: a process group has nothing that stops it when the run ends."""
        owner = self.folder / "owner.py"
        owner.write_text(OWNER, encoding="utf-8", newline="\n")
        run = subprocess.Popen([sys.executable, str(owner), str(self.folder), str(self.tree), SRC])
        wait_for(self.folder / "meta.json")
        wait_for(self.folder / "beat")
        recorded = json.loads((self.folder / "meta.json").read_text(encoding="utf-8"))
        self.addCleanup(lambda: os.killpg(recorded["pid"], signal.SIGKILL)
                        if procinfo.group_state(recorded["pid"]) == procinfo.ALIVE else None)
        run.kill()
        run.wait(30)
        self.assertTrue(beating(self.folder))
        self.assertEqual(procinfo.tree_state(recorded)[0], procinfo.ALIVE)

    def test_a_lock_whose_pid_now_belongs_to_another_process_is_released(self):
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(other.kill)
        task = self.folder / "task"
        task.mkdir()
        save_json(task / ".run.lock", {"pid": other.pid, "start": procinfo.identity(other.pid)})
        with self.assertRaisesRegex(RuntimeError, "Task is still active"):
            with TaskLock(task, force=True):
                pass
        save_json(task / ".run.lock", {"pid": other.pid, "start": "linux:1"})  # the runner that took it had another one
        with TaskLock(task, force=True):
            pass


class EngineTests(ContinuationCase):
    """Whole runs whose CLI is a real process tree (`support.ScriptedCLI`), supervised as an agent call is."""

    def setUp(self):
        super().setUp()
        self.scripted = ScriptedCLI(Path(tempfile.mkdtemp(dir=self.home.parent)) / "cli")
        guard = patch("patchrondo.providers.execute", side_effect=self.scripted.execute)
        guard.start()
        self.addCleanup(guard.stop)
        self.configure(tests=False)

    def developer_calls(self) -> int:
        return sum(1 for call in self.scripted.calls() if call["role"] == "developer")

    def test_a_supervised_cli_at_its_turn_limit_is_continued(self):
        self.scripted.script(developer={"max_turns_once": True})
        task_id, path = self.task()
        state = self.go(task_id)
        self.assertEqual((state["last_error"]["kind"], state["phase"]), ("tests_disabled", "test"), state["last_error"])
        self.assertEqual(self.developer_calls(), 2)
        first = execution.load(state).step.attempts[0]  # paused for the test consent: the developer's step is current
        record = reconciliation.read_marker(self.folder(path, "develop", 1))
        self.assertEqual((record["stage"], record["tree"], record["tree_ended"], record["left_behind"], record["kill_refused"]),
                         (reconciliation.ENDED, self.expected_tree(), True, False, False))
        self.assertEqual((first.attempt_id, first.termination), ("CX-0001", Termination.EXITED))
        self.assertEqual(self.events(state).count("continuation_started"), 1)

    def test_a_cli_that_leaves_a_process_running_is_stopped_and_not_continued(self):
        self.scripted.script(developer={"max_turns_once": True, "leave_once": True})
        task_id, path = self.task()
        state = self.go(task_id)
        self.assertEqual(state["last_error"]["kind"], "max_turns")
        self.assertEqual(self.declined(state)[-1]["reason"], "descendants_left")
        self.assertEqual(self.developer_calls(), 1)
        left = int((self.scripted.folder / "developer.left.pid").read_text())
        deadline = time.monotonic() + 10
        while procinfo._inspect(left)[0] == procinfo.ALIVE and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertNotEqual(procinfo._inspect(left)[0], procinfo.ALIVE)
        ended = [entry for entry in state["history"] if entry["event"] == "attempt_ended"]
        self.assertTrue(ended[-1].get("left_behind"))

    @staticmethod
    def expected_tree() -> str:
        return process.JOB if WINDOWS else process.GROUP


if __name__ == "__main__":
    unittest.main()
