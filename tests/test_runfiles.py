"""Attempt folders of a task's runs (`runfiles`, Phase 2.5 Step 03) and every reader that must know them.

Nothing here starts a provider CLI: the adapter cases reach the scripted CLI of
`support` through `patchrondo.providers.execute`, and the workflow cases run the
real loop with the scripted agents of `test_acceptance`. No run of this version
writes attempt folders yet (Step 04 allocates them with their records), so the
readers are checked on folders laid out as `runfiles.allocate` lays them out.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from patchrondo import bundle, core, evidence, execution, runfiles, storage
from patchrondo.app import App
from patchrondo.core import run_task
from patchrondo.providers import OfficialCLI
from patchrondo.storage import LockBusy, TaskLock, read_json, save_json

from support import ScriptedCLI, ServerCase
from test_acceptance import GOOD, MO2, Agents, Case
from test_bundle import CLOCK, entries
from test_provenance import link_folder


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def finished_pid() -> int:
    """The PID of a process that has exited."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


class Folder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.task = Path(self.tmp.name).resolve() / "T-0123456789ab"
        self.task.mkdir()
        self.runs = self.task / "runs"


class AllocationTests(Folder):
    def test_each_attempt_gets_a_folder_of_its_own_numbered_per_step(self):
        first = runfiles.allocate(self.task, 1, "develop")
        second = runfiles.allocate(self.task, 1, "develop")
        review = runfiles.allocate(self.task, 1, "review")
        later = runfiles.allocate(self.task, 2, "develop")
        self.assertEqual([path.relative_to(self.runs).as_posix() for path in (first, second, review, later)],
                         ["iteration-001/develop/attempt-001", "iteration-001/develop/attempt-002",
                          "iteration-001/review/attempt-001", "iteration-002/develop/attempt-001"])
        self.assertEqual(runfiles.attempt_folder(self.task, 1, "develop", 2), second)
        if os.name == "posix":
            self.assertEqual(second.stat().st_mode & 0o777, 0o700)

    def test_an_existing_folder_is_never_reused_and_its_files_stay(self):
        first = runfiles.allocate(self.task, 1, "develop")
        write(first / "developer.stdout.log", "first attempt")
        with self.assertRaises(FileExistsError):
            runfiles.allocate(self.task, 1, "develop", ordinal=1)
        self.assertEqual((first / "developer.stdout.log").read_text(encoding="utf-8"), "first attempt")
        self.assertEqual(runfiles.allocate(self.task, 1, "develop", ordinal=3).name, "attempt-003")
        # A folder left by a crash that no state names is passed, never written into; other names do not count.
        (self.runs / "iteration-001" / "develop" / "attempt-007").mkdir()
        for name in ("attempt-000", "attempt-8", "attempt-0009", "attempt-01x"):
            (self.runs / "iteration-001" / "develop" / name).mkdir()
        self.assertEqual(runfiles.allocate(self.task, 1, "develop").name, "attempt-008")

    def test_a_step_has_at_most_999_attempt_folders(self):
        (self.runs / "iteration-001" / "review" / "attempt-999").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "at most 999"):
            runfiles.allocate(self.task, 1, "review")

    def test_malformed_parts_are_refused_before_anything_is_created(self):
        for iteration, phase, ordinal in ((0, "develop", None), (1000, "develop", None), (True, "develop", None),
                                          ("1", "develop", None), (1.0, "develop", None), (1, "test", None),
                                          (1, "../develop", None), (1, "Develop", None), (1, "develop/attempt-001", None),
                                          (1, "", None), (1, "develop", 0), (1, "develop", 1000), (1, "develop", True),
                                          (1, "develop", "2")):
            with self.subTest(iteration=iteration, phase=phase, ordinal=ordinal):
                with self.assertRaises(ValueError):
                    runfiles.allocate(self.task, iteration, phase, ordinal)
                with self.assertRaises(ValueError):
                    runfiles.attempt_folder(self.task, iteration, phase, 1 if ordinal is None else ordinal)
        self.assertFalse(self.runs.exists())

    def test_nothing_is_written_through_a_linked_or_foreign_folder(self):
        elsewhere = Path(self.tmp.name) / "elsewhere"
        elsewhere.mkdir()
        (self.runs / "iteration-001").mkdir(parents=True)
        link_folder(self.runs / "iteration-001" / "develop", elsewhere, self)
        with self.assertRaises(runfiles.UnsafeFolder):
            runfiles.allocate(self.task, 1, "develop")
        self.assertEqual(list(elsewhere.iterdir()), [])
        write(self.runs / "iteration-001" / "review", "a file where a folder belongs")
        with self.assertRaises(runfiles.UnsafeFolder):
            runfiles.allocate(self.task, 1, "review")

    def test_a_linked_iteration_folder_is_refused_too(self):
        elsewhere = Path(self.tmp.name) / "elsewhere"
        elsewhere.mkdir()
        self.runs.mkdir()
        link_folder(self.runs / "iteration-002", elsewhere, self)
        with self.assertRaises(runfiles.UnsafeFolder):
            runfiles.allocate(self.task, 2, "develop")
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertEqual(runfiles.attempts(self.runs / "iteration-002"), [])

    def test_the_layout_follows_the_execution_records(self):
        self.assertEqual(runfiles.PHASES, tuple(str(phase) for phase in execution.Phase))
        self.assertEqual(runfiles.ROLE_OF_PHASE, {str(phase): role for phase, role in execution.ROLE_OF.items()})
        self.assertEqual((runfiles.ITERATION_LIMIT, runfiles.ATTEMPT_LIMIT), (execution.ITERATION_LIMIT, execution.ORDINAL_LIMIT))
        self.assertIs(core.DRAFT_FOLDER, runfiles.DRAFT_FOLDER)


class NameTests(Folder):
    ACCEPTED = ("iteration-001/developer.stdout.log", "iteration-012/abc123-test-01.log",
                "iteration-001/develop/attempt-001/developer.stdout.log",
                "iteration-999/review/attempt-999/reviewer.reply.md")
    REFUSED = ("../outside.txt", "/etc/passwd", "C:/Windows/win.ini", "C:\\x.log", "iteration-001",
               "iteration-1/developer.log", "iteration-001/../state.json", "iteration-001/.hidden",
               "iteration-001\\develop\\attempt-001\\developer.reply.md", "iteration-001/test-draft-01/developer.reply.md",
               "iteration-001/develop/developer.reply.md", "iteration-001/develop/attempt-000/developer.reply.md",
               "iteration-001/develop/attempt-1/developer.reply.md", "iteration-001/develop/attempt-0001/developer.reply.md",
               "iteration-001/test/attempt-001/developer.reply.md", "iteration-001/develop/attempt-001/../../state.json",
               "iteration-001/develop/attempt-001/sub/developer.reply.md", "iteration-001/develop/attempt-001/",
               "iteration-001/develop/attempt-001//x.log", "iteration-001/develop/attempt-001/x.log\n")

    def test_names_readers_accept(self):
        for name in self.ACCEPTED:
            with self.subTest(name=name):
                self.assertTrue(runfiles.LOG_NAME.fullmatch(name))
                self.assertTrue(bundle.LOG_NAME.fullmatch(name))
        for name in self.REFUSED:
            with self.subTest(name=name):
                self.assertIsNone(runfiles.LOG_NAME.fullmatch(name))
        self.assertEqual([runfiles.attempt_ordinal(name) for name in ("attempt-001", "attempt-999", "attempt-000", "attempt-1",
                                                                      "attempt-0001", "attempt-01a", "Attempt-001")],
                         [1, 999, None, None, None, None, None])

    def test_the_log_a_record_names(self):
        flat = self.runs / "iteration-001"
        # What `_record_review` wrote before attempt folders existed, for the flat folder it still uses.
        self.assertEqual(runfiles.log_name(self.task, flat / "reviewer.reply.md"), f"{flat.name}/reviewer.reply.md")
        attempt = runfiles.attempt_folder(self.task, 1, "review", 2)
        self.assertEqual(runfiles.log_name(self.task, attempt / "reviewer.reply.md"),
                         "iteration-001/review/attempt-002/reviewer.reply.md")
        for file in (self.task / "state.json", Path(self.tmp.name) / "elsewhere" / "x.log",
                     self.runs / "iteration-001" / "test-draft-01" / "developer.reply.md",
                     self.runs / "iteration-001" / "develop" / "attempt-000" / "developer.reply.md"):
            with self.subTest(file=file):
                with self.assertRaises(ValueError):
                    runfiles.log_name(self.task, file)

    def test_a_review_record_names_the_log_of_the_folder_it_was_given(self):
        state = {"reviewer": "codex", "iteration": 1, "base_sha": "0" * 40}
        review = {"verdict": "APPROVED", "criteria": [], "issues": []}
        with patch("patchrondo.core.evidence.append", return_value={"id": "E-0001"}) as append:
            core._record_review(self.task, state, review, "f" * 64, self.runs / "iteration-001")
            core._record_review(self.task, state, review, "f" * 64, runfiles.attempt_folder(self.task, 1, "review", 2))
        self.assertEqual([call.kwargs["log"] for call in append.call_args_list],
                         ["iteration-001/reviewer.reply.md", "iteration-001/review/attempt-002/reviewer.reply.md"])


class LockTests(Folder):
    """`TaskLock(force=True)`, the `--unlock` of a run: never while a process file of the task's runs may name a live agent."""

    def setUp(self):
        super().setUp()
        save_json(self.task / ".run.lock", {"pid": -1})

    def unlock(self):
        with TaskLock(self.task, force=True):
            pass

    def refused(self, pattern="still active"):
        save_json(self.task / ".run.lock", {"pid": -1})  # a lock left by a runner that is gone, for each check anew
        with self.assertRaisesRegex(RuntimeError, pattern):
            self.unlock()
        self.assertTrue((self.task / ".run.lock").exists())  # never removed while in doubt

    def test_a_live_agent_in_any_attempt_folder_keeps_the_lock(self):
        for relative in ("iteration-001/develop/attempt-001/developer.active-process.json",
                         "iteration-003/review/attempt-012/reviewer.active-process.json",
                         "iteration-001/test-draft-01/developer.active-process.json",
                         "iteration-001/developer.active-process.json"):
            with self.subTest(file=relative):
                save_json(self.runs / relative, {"pid": os.getpid()})
                self.refused()
                (self.runs / relative).unlink()

    def test_a_process_file_in_a_folder_no_reader_lists_still_counts(self):
        for relative in ("iteration-001/develop/attempt-x/developer.active-process.json",
                         "iteration-001/develop/attempt-000/developer.active-process.json",
                         "iteration-001/other/inner/developer.active-process.json",
                         "iteration-001/develop/developer.active-process.json",
                         "iteration-7/review/attempt-001/reviewer.active-process.json"):
            with self.subTest(file=relative):
                save_json(self.runs / relative, {"pid": os.getpid()})
                self.refused()
                (self.runs / relative).unlink()

    def test_an_unreadable_process_file_keeps_the_lock(self):
        target = self.runs / "iteration-001" / "review" / "attempt-001"
        for content in ("not json", "[1, 2]", "{}", '{"pid": "x"}', '{"pid": null}', ""):
            with self.subTest(content=content):
                write(target / "reviewer.active-process.json", content)
                self.refused("Unreadable process file")
        (target / "reviewer.active-process.json").unlink()
        (target / "reviewer.active-process.json").mkdir()  # not a file at all
        self.refused("Unreadable process file")

    def test_an_unverifiable_process_on_native_windows_keeps_the_lock(self):
        save_json(self.runs / "iteration-001" / "develop" / "attempt-002" / "developer.active-process.json",
                  {"pid": finished_pid()})
        # Native Windows cannot tell a live PID from a dead or reused one here: it fails closed.
        windows = SimpleNamespace(**{**vars(os), "name": "nt"})
        with patch.object(storage, "os", windows):
            self.refused()
        if os.name == "nt":
            self.refused()

    @unittest.skipUnless(os.name == "posix", "POSIX can tell an exited process; native Windows fails closed")
    def test_an_exited_agent_of_an_attempt_no_longer_keeps_the_lock(self):
        save_json(self.runs / "iteration-001" / "develop" / "attempt-002" / "developer.active-process.json",
                  {"pid": finished_pid()})
        self.unlock()
        self.assertFalse((self.task / ".run.lock").exists())
        # The process file is left where it is: the unlock reads it and never removes it.
        self.assertTrue((self.runs / "iteration-001" / "develop" / "attempt-002" / "developer.active-process.json").exists())

    def test_without_unlock_the_lock_is_simply_busy(self):
        with self.assertRaises(LockBusy):
            with TaskLock(self.task):
                pass

    def test_process_files_are_found_at_the_depths_writers_use_and_no_deeper(self):
        names = ["iteration-001/developer.active-process.json", "iteration-001/test-draft-01/developer.active-process.json",
                 "iteration-001/develop/attempt-001/developer.active-process.json",
                 "iteration-002/review/attempt-002/reviewer.active-process.json"]
        for name in [*names, "iteration-001/develop/attempt-001/deeper/developer.active-process.json",
                     "elsewhere/developer.active-process.json"]:
            save_json(self.runs / name, {"pid": 1})
        write(self.runs / "iteration-001" / "develop" / "attempt-001" / "developer.stdout.log", "x")
        self.assertEqual(runfiles.process_files(self.runs), sorted(self.runs / name for name in names))
        self.assertEqual(runfiles.process_files(self.task / "missing"), [])


class AdapterTests(Folder):
    """The real adapter (`OfficialCLI`) writing into attempt folders: what Step 04 will hand it instead of the flat folder."""

    def setUp(self):
        super().setUp()
        self.workspace = Path(self.tmp.name) / "worktree"
        self.workspace.mkdir()
        self.cli = ScriptedCLI(Path(self.tmp.name) / "fixture")
        guard = patch("patchrondo.providers.execute", side_effect=self.cli.execute)
        guard.start()
        self.addCleanup(guard.stop)
        self.adapter = OfficialCLI(timeout=60)

    @staticmethod
    def files(folder: Path) -> dict[str, bytes]:
        return {file.name: file.read_bytes() for file in folder.iterdir() if file.is_file()}

    def test_two_attempts_of_a_step_and_the_review_keep_their_own_files(self):
        self.cli.script(developer={"reply": "first answer"})
        first = runfiles.allocate(self.task, 1, "develop")
        self.adapter.invoke("claude", "developer", "first prompt", self.workspace, first)
        kept = self.files(first)
        self.cli.script(developer={"reply": "second answer"})
        second = runfiles.allocate(self.task, 1, "develop")
        self.adapter.invoke("claude", "developer", "second prompt", self.workspace, second)
        review = runfiles.allocate(self.task, 1, "review")
        self.adapter.invoke("codex", "reviewer", "review prompt", self.workspace, review)
        self.assertEqual(self.files(first), kept)  # the second attempt changed nothing of the first
        for folder, prompt, answer in ((first, b"first prompt", b"first answer"), (second, b"second prompt", b"second answer")):
            files = self.files(folder)
            self.assertTrue({"developer.prompt.md", "developer.stdout.log", "developer.stderr.log",
                             "developer.reply.md"} <= set(files), sorted(files))
            self.assertIn(prompt, files["developer.prompt.md"])
            self.assertIn(answer, files["developer.reply.md"])
            self.assertFalse(any(name.startswith("reviewer.") for name in files))
        self.assertTrue(all(name.startswith("reviewer.") or name == "review.schema.json" for name in self.files(review)))
        self.assertFalse(any(path.is_file() for path in (self.runs / "iteration-001").iterdir()))  # nothing flat
        self.assertEqual(runfiles.process_files(self.runs), [])  # each removed when its CLI had exited

    def test_an_agent_still_running_in_an_attempt_folder_keeps_the_lock(self):
        self.cli.script(developer={"hold": ["working"]})
        folder = runfiles.allocate(self.task, 1, "develop")
        failures = []

        def call():
            try:
                self.adapter.invoke("claude", "developer", "prompt", self.workspace, folder)
            except Exception as exc:  # reported below
                failures.append(exc)

        worker = threading.Thread(target=call)
        worker.start()
        self.addCleanup(worker.join, 30)
        self.addCleanup(self.cli.release, "developer", "working")
        deadline = time.monotonic() + 30
        while not (self.cli.reached("developer", "working") and (folder / "developer.active-process.json").exists()):
            self.assertLess(time.monotonic(), deadline, "the scripted CLI did not start")
            time.sleep(0.02)
        save_json(self.task / ".run.lock", {"pid": -1})  # the runner that held it is gone
        with self.assertRaisesRegex(RuntimeError, "still active"):
            with TaskLock(self.task, force=True):
                pass
        self.assertTrue((self.task / ".run.lock").exists())
        self.cli.release("developer", "working")
        worker.join(30)
        self.assertEqual(failures, [])
        self.assertEqual(runfiles.process_files(self.runs), [])


class LogsTests(ServerCase):
    """The Logs tab: one list of every place a run writes, read only through IDs it listed."""

    def setUp(self):
        super().setUp()
        self.project, _ = self.add_project()
        self.url = f"/api/projects/{self.project['id']}/tasks"
        self.task_id, self.path = self.add_task(self.project)
        self.runs = self.path / "runs"

    def listed(self):
        return [(entry["id"], entry["group"]) for entry in self.get(f"{self.url}/{self.task_id}")[1]["logs"]]

    def read(self, name):
        return self.get(f"{self.url}/{self.task_id}/log?file={name}")

    def test_flat_requests_for_tests_and_attempt_folders_are_listed_side_by_side(self):
        iteration = self.runs / "iteration-001"
        write(iteration / "developer.stdout.log", "flat development")
        write(iteration / "test-draft-01" / "developer.reply.md", "a request for tests")
        for phase, role in (("review", "reviewer"), ("develop", "developer"), ("develop", "developer")):
            folder = runfiles.allocate(self.path, 1, phase)
            write(folder / f"{role}.stdout.log", f"{phase} {folder.name}")
            save_json(folder / f"{role}.active-process.json", {"pid": 1})  # never listed
            write(folder / "review.schema.json", "{}")                    # nor any other kind of file
        write(runfiles.allocate(self.path, 2, "develop") / "developer.reply.md", "second iteration")
        self.assertEqual(self.listed(), [
            ("iteration-001/developer.stdout.log", "Iteration 1"),
            ("iteration-001/test-draft-01/developer.reply.md", "Iteration 1 · tests written on request 1"),
            ("iteration-001/develop/attempt-001/developer.stdout.log", "Iteration 1 · development attempt 1"),
            ("iteration-001/develop/attempt-002/developer.stdout.log", "Iteration 1 · development attempt 2"),
            ("iteration-001/review/attempt-001/reviewer.stdout.log", "Iteration 1 · review attempt 1"),
            ("iteration-002/develop/attempt-001/developer.reply.md", "Iteration 2 · development attempt 1")])
        self.assertEqual(self.read("iteration-001/develop/attempt-002/developer.stdout.log")[1]["text"], "develop attempt-002")
        self.assertEqual(self.read("iteration-001/developer.stdout.log")[1]["text"], "flat development")
        for name in ("iteration-001/develop/attempt-001/developer.active-process.json",
                     "iteration-001/review/attempt-001/review.schema.json",
                     "iteration-001/develop/attempt-001/../attempt-002/developer.stdout.log",
                     "iteration-001/develop/attempt-001", "iteration-001/develop",
                     "iteration-001%2Fdevelop%2Fattempt-001%2F..%2F..%2F..%2Fstate.json",
                     "iteration-001%5Cdevelop%5Cattempt-001%5Cdeveloper.stdout.log"):
            with self.subTest(file=name):
                self.assertEqual(self.read(name)[0].status, 404)

    def test_folders_and_files_not_laid_out_by_allocate_are_never_listed(self):
        iteration = self.runs / "iteration-001"
        for relative in ("develop/attempt-000/developer.stdout.log", "develop/attempt-1/developer.stdout.log",
                         "develop/attempt-0001/developer.stdout.log", "test/attempt-001/developer.stdout.log",
                         "develop/developer.stdout.log", "develop/attempt-001/inner/developer.stdout.log",
                         "develop/attempt-001/.hidden.log", "develop/attempt-001/name with space.log"):
            write(iteration / relative, "not listed")
        self.assertEqual(self.listed(), [])
        self.assertEqual(self.read("iteration-001/develop/attempt-000/developer.stdout.log")[0].status, 404)

    def test_a_linked_attempt_folder_or_file_is_never_read_through(self):
        outside = Path(self.tmp.name) / "outside"
        write(outside / "developer.stdout.log", "PRIVATE outside the task")
        (self.runs / "iteration-001" / "develop").mkdir(parents=True)
        link_folder(self.runs / "iteration-001" / "develop" / "attempt-001", outside, self)
        self.assertEqual(self.listed(), [])
        self.assertEqual(self.read("iteration-001/develop/attempt-001/developer.stdout.log")[0].status, 404)
        folder = runfiles.allocate(self.path, 1, "develop")
        try:
            (folder / "developer.stdout.log").symlink_to(outside / "developer.stdout.log")
        except OSError:
            self.skipTest("Symbolic links to files cannot be created here")
        self.assertEqual(self.listed(), [])

    def test_a_linked_phase_folder_is_never_read_through(self):
        outside = Path(self.tmp.name) / "outside"
        write(outside / "attempt-001" / "reviewer.stdout.log", "PRIVATE outside the task")
        (self.runs / "iteration-001").mkdir(parents=True)
        link_folder(self.runs / "iteration-001" / "review", outside, self)
        self.assertEqual(self.listed(), [])

    def test_a_new_interface_reads_what_an_earlier_one_listed(self):
        folder = runfiles.allocate(self.path, 1, "review")
        write(folder / "reviewer.reply.md", "kept")
        before = [{key: value for key, value in entry.items() if key != "file"} for entry in self.app._logs(self.path)]
        again = App(self.home / "another-interface")._logs(self.path)  # nothing kept in memory decides
        self.assertEqual([{key: value for key, value in entry.items() if key != "file"} for entry in again], before)
        self.assertEqual(runfiles.allocate(self.path, 1, "review").name, "attempt-002")  # numbered from the disk


class WorkflowTests(Case):
    """The real loop is unchanged: it writes flat, names flat logs, and leaves attempt folders it finds alone."""

    def test_a_run_writes_no_attempt_folder_and_names_its_review_log_as_before(self):
        task_id, path, _ = self.task(GOOD)
        state = run_task(self.home, task_id, adapter=Agents())
        self.assertEqual((state["status"], state["gate"]["decision"]), ("done", "PASS"), state.get("last_error"))
        iteration = path / "runs" / "iteration-001"
        self.assertEqual([entry.name for entry in iteration.iterdir() if entry.is_dir()], [])
        review = next(record for record in evidence.read(path).records if record["kind"] == "review")
        self.assertEqual(review["log"], "iteration-001/reviewer.reply.md")
        self.assertNotIn("execution", state)

    def test_attempt_folders_beside_a_run_change_nothing_it_decides_or_records(self):
        task_id, path, _ = self.task(GOOD, MO2)
        old = runfiles.allocate(path, 1, "develop")
        write(old / "developer.stdout.log", "an attempt folder written before this run")
        save_json(old / "developer.active-process.json", {"pid": finished_pid()})  # left by a crash
        # Since Step 05.1 only a recorded call makes such a folder: a state without a record beside it has lost its
        # record, and the task is not run as if it never had one. Nothing is called and the folder is left as it is.
        held = run_task(self.home, task_id, adapter=Agents())
        self.assertEqual((held["status"], held["last_error"]["kind"]), ("paused", "execution_record_missing"))
        self.assertEqual((old / "developer.stdout.log").read_text(encoding="utf-8"), "an attempt folder written before this run")
        moved = path / "attempt-moved-away"
        os.replace(old, moved)  # the user moves it out of `runs`: the task was never recorded
        state = run_task(self.home, task_id, adapter=Agents())
        self.assertEqual((state["status"], state["phase"], state["gate"]["decision"]), ("paused", "validate", "BLOCKED"))
        self.assertEqual((moved / "developer.stdout.log").read_text(encoding="utf-8"), "an attempt folder written before this run")
        commands = [record for record in evidence.read(path).records if record["kind"] == "command"]
        self.assertTrue(commands)
        # The checks keep their flat files: no record names a file of an attempt folder.
        self.assertTrue(all(runfiles.LOG_NAME.fullmatch(record["log"]) and record["log"].count("/") == 1 for record in commands))
        made = bundle.build(path, "detailed", clock=CLOCK)
        self.assertIn(f"logs/{commands[0]['log']}", entries(made.data))


class BundleTests(Case):
    """A log in an attempt folder, named by a record, goes into a detailed bundle like a flat one; nothing else does."""

    def blocked(self):
        task_id, path, workspace = self.task(GOOD, MO2)
        state = run_task(self.home, task_id, adapter=Agents())
        self.assertEqual((state["status"], state["gate"]["decision"]), ("paused", "BLOCKED"))
        return path, state

    def named(self, path, state, name: str, text: str) -> dict:
        """A review record naming `name`, as Step 04's review of an attempt will name its reply."""
        log = path / "runs" / name
        if text is not None:
            write(log, text)
        digest = hashlib.sha256(log.read_bytes()).hexdigest() if log.is_file() else None
        return evidence.append(path, "review", executor="reviewer", provider="codex", result="approved", verdict="APPROVED",
                               iteration=1, fingerprint=state["gate"]["snapshot"], scope="code", log=name, log_sha256=digest)

    def test_a_nested_log_is_exported_masked_and_checked_against_its_record(self):
        path, state = self.blocked()
        secret = "sk-" + "b" * 32
        name = "iteration-001/review/attempt-002/reviewer.reply.md"
        record = self.named(path, state, name, f"The reviewer's reply, with {secret}\n")
        made = bundle.build(path, "detailed", clock=CLOCK)
        files = entries(made.data)
        self.assertNotIn(secret, files[f"logs/{name}"].decode())
        source = next(entry["source"] for entry in made.manifest["files"] if entry["path"] == f"logs/{name}")
        self.assertEqual((source["record"], source["matches_record"]), (record["id"], True))
        found = bundle.verify(made.data)
        self.assertFalse({"UNSAFE_NAME", "SOURCE_MISMATCH"} & {entry["code"] for entry in found["problems"]}, found)

    def test_a_nested_log_through_a_link_or_an_unsafe_name_is_left_out(self):
        path, state = self.blocked()
        outside = Path(self.tmp.name) / "outside"
        write(outside / "reviewer.reply.md", "PRIVATE outside the task")
        (path / "runs" / "iteration-001" / "review").mkdir(parents=True)
        link_folder(path / "runs" / "iteration-001" / "review" / "attempt-003", outside, self)
        self.named(path, state, "iteration-001/review/attempt-003/reviewer.reply.md", None)
        unsafe = ["iteration-001/review/attempt-001/../../../outside.txt", "iteration-001/review/attempt-000/reviewer.reply.md",
                  "iteration-001/other/attempt-001/reviewer.reply.md", "iteration-001/test-draft-01/developer.reply.md"]
        for name in unsafe:
            if ".." not in name:
                write(path / "runs" / name, "never exported")
            self.named(path, state, name, None)
        made = bundle.build(path, "detailed", clock=CLOCK)
        reasons = {entry["item"]: entry["reason"] for entry in made.manifest["omissions"]}
        self.assertEqual(reasons["logs/iteration-001/review/attempt-003/reviewer.reply.md"], "not_a_folder_of_the_task")
        for name in unsafe:
            self.assertEqual(reasons[f"logs/{name}"], "unsafe_name", name)
        self.assertFalse(any(b"PRIVATE" in data or b"never exported" in data for data in entries(made.data).values()))

    def test_a_linked_phase_folder_is_left_out(self):
        path, state = self.blocked()
        outside = Path(self.tmp.name) / "outside"
        write(outside / "attempt-001" / "reviewer.reply.md", "PRIVATE outside the task")
        link_folder(path / "runs" / "iteration-001" / "review", outside, self)
        self.named(path, state, "iteration-001/review/attempt-001/reviewer.reply.md", None)
        made = bundle.build(path, "detailed", clock=CLOCK)
        self.assertIn({"item": "logs/iteration-001/review/attempt-001/reviewer.reply.md", "reason": "not_a_folder_of_the_task"},
                      made.manifest["omissions"])
        self.assertFalse(any(b"PRIVATE" in data for data in entries(made.data).values()))

    def test_the_verification_center_finds_a_nested_log_its_record_names(self):
        path, state = self.blocked()
        name = "iteration-001/review/attempt-001/reviewer.reply.md"
        record = self.named(path, state, name, "reply")
        app = App(Path(self.tmp.name) / "ui-home")
        project = SimpleNamespace(id="P-0000aaaa", home=self.home)
        self.assertEqual(app.verification_record(project, path.name, record["id"])["log"], {"id": name, "available": True})


if __name__ == "__main__":
    unittest.main()
