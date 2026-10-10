"""Process truth: what holds a task's lock and what is attached to it, verified or admitted unknown."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from patchrondo import procinfo, runinfo
from patchrondo.storage import TaskLock, read_json, save_json

PLAN = {"status": "scheduled", "resume_at": "2099-01-01T00:00:00+00:00", "consecutive_failures": 1,
        "max_consecutive_retries": 3, "provider": "codex", "schedule_source": "backoff", "stop_reason": None}


def finished_process() -> subprocess.Popen:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(30)
    return process


class ProcessIdentityTests(unittest.TestCase):
    def test_this_process_is_alive_with_its_own_token(self):
        token = procinfo.identity()
        self.assertIsNotNone(token, "this platform should be able to identify its own processes")
        self.assertEqual(procinfo.state(os.getpid(), token), "alive")

    def test_a_reused_pid_is_not_mistaken_for_the_recorded_process(self):
        self.assertEqual(procinfo.state(os.getpid(), "not-the-token-of-this-process"), "dead")

    def test_without_a_token_a_live_pid_is_unknown_not_alive(self):
        self.assertEqual(procinfo.state(os.getpid(), None), "unknown")

    def test_a_finished_process_is_dead(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            token = procinfo.identity(child.pid)
            self.assertEqual(procinfo.state(child.pid, token), "alive")
        finally:
            child.kill()
            child.wait(30)
        self.assertEqual(procinfo.state(child.pid, token), "dead")
        self.assertIsNone(procinfo.identity(child.pid))

    def test_invalid_pids_and_probe_failures_are_unknown(self):
        for pid in (0, -1, None, "12", True):
            self.assertEqual(procinfo.state(pid, "x"), "unknown")
        target = "_windows" if sys.platform == "win32" else "_posix"
        with patch.object(procinfo, target, side_effect=OSError("probe failed")):
            self.assertEqual(procinfo.state(os.getpid(), "x"), "unknown")
            self.assertIsNone(procinfo.identity())


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)

    def runtime(self, status, recovery=None, owned=None):
        return runinfo.runtime(self.path, {"status": status, "recovery": recovery}, owned)

    def marker(self, pid, token, auto_resume=True):
        save_json(self.path / runinfo.MARKERS / f"{pid}.json", {"pid": pid, "start": token, "since": "x", "auto_resume": auto_resume})

    def test_saved_status_without_processes(self):
        for status, activity in (("ready", "ready"), ("done", "done"), ("blocked", "blocked"), ("paused", "paused")):
            with self.subTest(status=status):
                found = self.runtime(status)
                self.assertEqual(found["activity"], activity)
                self.assertEqual(found["active"], False)
                self.assertEqual(found["needs_attention"], status in {"paused", "blocked"})
                self.assertEqual(found["can_start"], status in {"ready", "paused"})

    def test_running_status_without_a_lock_is_interrupted_not_running(self):
        found = self.runtime("running")
        self.assertEqual((found["activity"], found["active"], found["can_start"]), ("interrupted", False, True))

    def test_real_lock_is_running_and_verified(self):
        with TaskLock(self.path):
            lock = read_json(self.path / ".run.lock")
            self.assertEqual(lock["start"], procinfo.identity())  # the lock records which process instance holds it
            found = self.runtime("running")
            self.assertEqual((found["activity"], found["lock"]["holder"], found["lock"]["pid"]), ("running", "alive", os.getpid()))
            self.assertFalse(found["can_start"] or found["can_unlock"])
        self.assertEqual(self.runtime("paused")["lock"], None)

    def test_lock_of_a_gone_process_is_stale_and_needs_a_person(self):
        child = finished_process()
        save_json(self.path / ".run.lock", {"pid": child.pid, "time": "x", "start": "linux:1"})
        found = self.runtime("running")
        self.assertEqual((found["activity"], found["lock"]["holder"]), ("stale_lock", "dead"))
        self.assertTrue(found["needs_attention"])
        self.assertFalse(found["can_start"])
        self.assertEqual(found["can_unlock"], os.name == "posix")  # the engine cannot verify processes on native Windows

    def test_lock_that_cannot_be_verified_is_not_called_running_verified(self):
        for content in ('{"pid": %d}' % os.getpid(), "{}", "not json"):  # 0.1 lock without a token, or unreadable
            with self.subTest(lock=content):
                (self.path / ".run.lock").write_text(content, encoding="utf-8")
                found = self.runtime("running")
                self.assertEqual((found["activity"], found["lock"]["holder"]), ("running_unverified", "unknown"))
                self.assertFalse(found["can_start"] or found["can_unlock"])

    def test_plan_with_a_live_waiting_process_is_waiting(self):
        self.marker(os.getpid(), procinfo.identity())
        found = self.runtime("paused", PLAN)
        self.assertEqual((found["activity"], found["active"], found["waiting_process"]), ("waiting_retry", True, True))
        self.assertTrue(found["can_start"])  # a manual run stays possible; the API refuses a second automatic one
        self.assertEqual(found["can_stop"], os.name == "posix")

    def test_plan_without_a_process_is_only_a_plan(self):
        found = self.runtime("paused", PLAN)
        self.assertEqual((found["activity"], found["active"], found["needs_attention"], found["waiting_process"]),
                         ("plan_only", False, True, False))
        # A process that is attached without automatic resume will not retry either.
        self.marker(os.getpid(), procinfo.identity(), auto_resume=False)
        self.assertEqual(self.runtime("paused", PLAN)["activity"], "plan_only")

    def test_marker_of_a_gone_process_is_removed_and_does_not_count(self):
        child = finished_process()
        self.marker(child.pid, "linux:1")
        found = self.runtime("paused", PLAN)
        self.assertEqual((found["activity"], found["processes"]), ("plan_only", []))
        self.assertFalse((self.path / runinfo.MARKERS / f"{child.pid}.json").exists())

    def test_marker_that_cannot_be_verified_is_reported_as_such(self):
        self.marker(os.getpid(), None)
        found = self.runtime("paused", PLAN)
        self.assertEqual((found["activity"], found["active"], found["can_stop"]), ("plan_unverified", False, False))

    def test_stopped_recovery_and_owned_children(self):
        self.assertEqual(self.runtime("paused", {**PLAN, "status": "stopped", "stop_reason": "max_consecutive_retries"})["activity"],
                         "recovery_stopped")
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            found = self.runtime("ready", owned=[child])
            self.assertEqual((found["activity"], found["can_start"]), ("starting", False))  # started, lock not taken yet
            self.assertEqual(self.runtime("done", owned=[child])["activity"], "done")
        finally:
            child.kill()
            child.wait(30)
        self.assertEqual(self.runtime("ready", owned=[child])["activity"], "ready")

    def test_attached_marks_this_process_only_while_it_runs(self):
        with runinfo.attached(self.path, True):
            entries = runinfo.processes(self.path)
            self.assertEqual([(e["pid"], e["state"], e["auto_resume"], e["owned"]) for e in entries], [(os.getpid(), "alive", True, False)])
        self.assertEqual(runinfo.processes(self.path), [])
        self.assertFalse((self.path / runinfo.MARKERS).exists())
        with patch("patchrondo.runinfo.save_json", side_effect=OSError("read-only")), runinfo.attached(self.path, False):
            pass  # a marker that cannot be written never stops a run


class CommandAttachmentTests(unittest.TestCase):
    """`patchrondo run` records itself for as long as it lives, and stays interruptible under the interface."""

    def setUp(self):
        from patchrondo.core import create_task, initialize
        from support import make_repo
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.home = root / "state"
        initialize(self.home, make_repo(root / "repo"))
        self.task_id, _ = create_task(self.home, title="T", description="d", acceptance=[], developer="claude", reviewer="codex")
        self.path = self.home / "tasks" / self.task_id

    def run_command(self, *flags, environment=None):
        from patchrondo.cli import main
        seen = {}

        def supervise(home, task_id, **options):
            seen["processes"] = runinfo.processes(self.path)
            seen["handler"] = signal.getsignal(signal.SIGINT)
            return {**read_json(self.path / "state.json"), "status": "paused"}

        with patch("patchrondo.recovery.supervise", side_effect=supervise), patch("sys.stdout"), \
                patch.dict(os.environ, environment or {}):
            code = main(["--home", str(self.home), "run", self.task_id, *flags])
        return code, seen

    def test_run_is_attached_while_it_runs_with_the_recovery_mode_in_effect(self):
        code, seen = self.run_command()
        self.assertEqual(code, 2)
        self.assertEqual([(e["pid"], e["state"], e["auto_resume"]) for e in seen["processes"]], [(os.getpid(), "alive", False)])
        self.assertEqual(runinfo.processes(self.path), [])  # gone as soon as the command ends
        self.assertTrue(self.run_command("--auto-resume")[1]["processes"][0]["auto_resume"])
        cfg = read_json(self.home / "config.json")
        save_json(self.home / "config.json", {**cfg, "recovery": {"enabled": True}})
        self.assertTrue(self.run_command()[1]["processes"][0]["auto_resume"])
        self.assertFalse(self.run_command("--no-auto-resume")[1]["processes"][0]["auto_resume"])

    def test_a_run_started_by_the_interface_can_always_be_interrupted(self):
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)  # as inherited from a detached parent
        try:
            self.assertEqual(self.run_command()[1]["handler"], signal.SIG_IGN)  # a terminal user's choice is kept
            self.assertEqual(self.run_command(environment={"PATCHRONDO_UI_CHILD": "1"})[1]["handler"], signal.default_int_handler)
        finally:
            signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    unittest.main()
