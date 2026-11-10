"""Bounded, non-shell subprocesses with child-process cleanup."""
from __future__ import annotations

from dataclasses import dataclass
import io
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time

from . import procinfo

CHUNK_BYTES = 65536
LINE_BYTES = 2_000_000  # one line of streamed output; a longer one is dropped whole
# After the process has exited its pipes are read to the end. A descendant that kept them
# open could delay that forever, so reading stops this long after the exit.
DRAIN_SECONDS = 2.0
# How long the last SIGKILL of a stopped command's process group is repeated while the system refuses it (`_kill_group`).
GROUP_SECONDS = 2.0


# How the processes a command starts are held, so that stopping it stops them too (`Result.process_tree`).
GROUP = "process_group"   # POSIX: always
JOB = "job_object"        # native Windows, with `tree`
CHILD = "child_only"      # native Windows: no job object was asked for, or none could hold the child


# Supervised calls (`execute(supervise=True)`, Phase 2.5 Step 09): how long the tree of a CLI that exited by itself
# may take to show itself empty before what is left is taken for processes it left running.
SETTLE_SECONDS = 0.5
CREATE_SUSPENDED = 0x00000004  # Windows `CreateProcess`: the first thread waits until it is resumed


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    process_tree: str | None = None  # GROUP, JOB or CHILD; None from a caller's own `execute`
    # POSIX: stopping it, the system refused to the end to signal what was left of its group. Supervised on Windows:
    # stopping its job object, the job was not seen empty in time. Either way nothing says that what it started stopped.
    kill_refused: bool = False
    # Supervised calls only (None otherwise): whether every process of its tree was confirmed ended when it returned,
    # and whether processes it started were still running after it exited by itself (they were then stopped).
    tree_ended: bool | None = None
    left_behind: bool | None = None


# What the message and the log of a stopped command say when the system refused its group the last signal.
KILL_REFUSED = ("The system refused to signal what was left of its process group, so nothing says that the processes "
                "it started were stopped: they may still be running")


class Launch:
    """What `execute` tells its caller about the process it starts, while it happens. It decides nothing.

    `pid` and `process_tree` are set as soon as the process exists; `stopped`
    when `execute` had to stop it (its time limit, an interruption), with
    `kill_refused` as in `Result`. `on_start` is called once per start, after
    the process file is written: if it raises, the process is stopped and the
    error goes on, so no process runs that its caller could not record.

    Step 09: `on_launch` is called right before the process is created (if
    it raises, nothing is created), and `on_end` once the call is over and
    before its process file is removed (its errors are contained: observing
    never fails a call). A supervised call also sets `identity` (the
    `procinfo` token of the process), `owner` (this process's PID and token:
    what holds its job object or group), `kill_on_close` (native Windows: the
    job stops everything in it when its last handle closes, so when the
    owner ends however it ends), `tree_ended` and `left_behind` (as in
    `Result`). `recorded()` is that metadata, for a process file.
    """

    def __init__(self, on_start=None, *, on_launch=None, on_end=None):
        self.on_start, self.on_launch, self.on_end = on_start, on_launch, on_end
        self.pid: int | None = None
        self.process_tree: str | None = None
        self.stopped = False
        self.kill_refused = False
        self.supervised = False
        self.identity: str | None = None
        self.owner: tuple[int, str | None] | None = None
        self.kill_on_close = False
        self.tree_ended: bool | None = None
        self.left_behind: bool | None = None

    def _launching(self) -> None:
        if self.on_launch is not None:
            self.on_launch()

    def _began(self, pid: int, held: str, identity: str | None = None, owner: tuple | None = None,
               kill_on_close: bool = False) -> None:
        self.pid, self.process_tree = pid, held
        if owner is not None:
            self.supervised, self.identity, self.owner, self.kill_on_close = True, identity, owner, kill_on_close

    def _started(self) -> None:
        if self.on_start is not None:
            self.on_start()

    def _ended(self, refused: bool) -> None:
        self.stopped, self.kill_refused = True, refused

    def _finished(self, ended: bool | None, left: bool | None) -> None:
        self.tree_ended, self.left_behind = ended, left
        if self.on_end is not None:
            try:
                self.on_end()
            except Exception:  # noqa: BLE001 - what records the end never fails the call
                pass

    def recorded(self) -> dict:
        """What identifies the process and what holds it, for its process file and its attempt (`procinfo.tree_state`)."""
        owner = self.owner or (None, None)
        return {"pid": self.pid, "start": self.identity, "tree": self.process_tree, "kill_on_close": self.kill_on_close,
                "owner_pid": owner[0], "owner_start": owner[1]}


def stop_refused(result) -> bool:
    """Whether a command stopped at its time limit has a group the system refused the last signal (`kill_refused`).

    False for a command that ended by itself, and for a result without the
    field, such as one a caller's own `execute` returns.
    """
    return getattr(result, "timed_out", False) is True and getattr(result, "kill_refused", False) is True


def with_refusal(log: str) -> str:
    """`log` followed by `KILL_REFUSED` on a line of its own, marked as PatchRondo's, not the command's."""
    return log + ("" if not log or log.endswith("\n") else "\n") + f"[PatchRondo] {KILL_REFUSED}.\n"


def safe_env() -> dict[str, str]:
    env = os.environ.copy()
    # Prevent silently charging API accounts instead of subscribed CLIs.
    # Also remove common keys from child environments (not a full OS sandbox).
    for key in list(env):
        upper = key.upper()
        if any(s in upper for s in ("PASSWORD", "SECRET", "CREDENTIAL", "TOKEN", "API_KEY", "PRIVATE_KEY")) or upper in {
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY",
            "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
            "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
            "GITHUB_TOKEN", "GH_TOKEN", "GIT_ASKPASS", "SSH_ASKPASS",
            "AWS_PROFILE", "AWS_CONFIG_FILE", "GOOGLE_APPLICATION_CREDENTIALS",
            "PYTHONPATH", "PYTHONSTARTUP", "NODE_OPTIONS", "BASH_ENV", "ENV",
        } or upper.startswith("GIT_CONFIG_"):
            env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _kernel():
    """kernel32 with the signatures the job object and the start of a supervised process use (native Windows)."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                 ctypes.POINTER(wintypes.DWORD))
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    return ctypes, wintypes, kernel


class _Job:
    """A Windows job object holding a child and every process it starts, so that stopping it stops all of them.

    POSIX gets the same from the process group. A child that cannot be put
    in a job (the call is refused) is stopped alone, as without one, and its
    `Result` says so (`CHILD`).

    `kill_on_close` (supervised calls, Step 09) sets
    `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` before any process is put in it: the
    system stops every process of the job when its last handle closes, which
    is when this process ends however it ends (the handle is not inherited
    and the job has no name). Breakaway is never allowed, so a descendant
    cannot leave the job. `active()` reads how many of its processes are
    still running (`JobObjectBasicAccountingInformation`).
    """

    EXTENDED_LIMITS, ACCOUNTING, KILL_ON_JOB_CLOSE = 9, 1, 0x2000

    def __init__(self, proc: subprocess.Popen | None = None, *, kill_on_close: bool = False):
        self.ctypes, wintypes, self.kernel = _kernel()
        self.handle = self.kernel.CreateJobObjectW(None, None)
        self.kill_on_close = False
        if not self.handle:
            raise OSError(self.ctypes.get_last_error(), "The job object could not be created")
        try:
            if kill_on_close:
                self._close_kills()
            if proc is not None:
                self.assign(proc)
        except BaseException:
            self.close()
            raise

    def _limits(self):
        ctypes = self.ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("Read", "Write", "Other", "ReadBytes", "WriteBytes",
                                                             "OtherBytes")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Counters), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]
        return Extended()

    def _close_kills(self) -> None:
        """Set KILL_ON_JOB_CLOSE, and read it back: the job is taken for one that does only once the system says so."""
        ctypes = self.ctypes
        limits = self._limits()
        limits.BasicLimitInformation.LimitFlags = self.KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, self.EXTENDED_LIMITS, ctypes.byref(limits),
                                                   ctypes.sizeof(limits)):
            raise OSError(ctypes.get_last_error(), "The job object could not be set to stop its processes")
        read = self._limits()
        if not self.kernel.QueryInformationJobObject(self.handle, self.EXTENDED_LIMITS, ctypes.byref(read),
                                                     ctypes.sizeof(read), None):
            raise OSError(ctypes.get_last_error(), "The limits of the job object could not be read")
        if not read.BasicLimitInformation.LimitFlags & self.KILL_ON_JOB_CLOSE:
            raise OSError(0, "The job object does not stop its processes when it is closed")
        self.kill_on_close = True

    def assign(self, proc: subprocess.Popen) -> None:
        if getattr(proc, "_handle", None) is None:
            raise OSError(0, "The process has no handle to put in a job object")
        if not self.kernel.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise OSError(self.ctypes.get_last_error(), "The process could not be put in a job object")

    def active(self) -> int | None:
        """How many processes of the job are still running; None when the system does not say."""
        ctypes = self.ctypes
        from ctypes import wintypes

        class Accounting(ctypes.Structure):
            _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                        ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                        ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                        ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]
        if not self.handle:
            return None
        found = Accounting()
        if not self.kernel.QueryInformationJobObject(self.handle, self.ACCOUNTING, ctypes.byref(found),
                                                     ctypes.sizeof(found), None):
            return None
        return int(found.ActiveProcesses)

    def empty_within(self, seconds: float) -> bool:
        """Whether the job is seen with no running process within `seconds`. False when the system does not say."""
        deadline = time.monotonic() + seconds
        while True:
            count = self.active()
            if count == 0:
                return True
            if count is None or time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    def kill(self) -> bool:
        """Ask the system to stop every process of the job. The answer is not proof: `empty_within` is."""
        return bool(self.handle) and bool(self.kernel.TerminateJobObject(self.handle, 1))

    def close(self) -> None:
        handle, self.handle = self.handle, None
        if handle:
            self.kernel.CloseHandle(handle)


def _resume(pid: int) -> None:
    """Resume the threads of a process created suspended (native Windows). Raises OSError when none was resumed.

    Python does not keep the handle of the first thread, so the threads of the
    process are found by its ID (`CreateToolhelp32Snapshot`); a process
    created suspended has that one thread.
    """
    ctypes, wintypes, kernel = _kernel()

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ThreadID", wintypes.DWORD),
                    ("th32OwnerProcessID", wintypes.DWORD), ("tpBasePri", ctypes.c_long),
                    ("tpDeltaPri", ctypes.c_long), ("dwFlags", wintypes.DWORD)]
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = kernel.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(Entry))
    kernel.Thread32First.restype = kernel.Thread32Next.restype = wintypes.BOOL
    kernel.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel.ResumeThread.restype = wintypes.DWORD
    snapshot = kernel.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "The threads of the process could not be listed")
    resumed = 0
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        more = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == pid:
                thread = kernel.OpenThread(0x0002, False, entry.th32ThreadID)  # THREAD_SUSPEND_RESUME
                if thread:
                    try:
                        if kernel.ResumeThread(thread) != 0xFFFFFFFF:
                            resumed += 1
                    finally:
                        kernel.CloseHandle(thread)
            more = kernel.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    if not resumed:
        raise OSError(0, "The process created suspended could not be resumed")


def _job(proc: subprocess.Popen, tree: bool) -> tuple[_Job | None, str]:
    """The job object that holds the child, if any, and how what it starts is held (`Result.process_tree`)."""
    if os.name == "posix":
        return None, GROUP
    if not tree or getattr(proc, "_handle", None) is None:
        return None, CHILD
    try:
        return _Job(proc), JOB
    except (OSError, AttributeError, ValueError):
        return None, CHILD  # the command still runs; stopping it stops it alone, and the result says so


def _supervised_job() -> _Job | None:
    """A job object that stops its processes when closed, made before the process it will hold; None when it cannot."""
    if os.name == "posix":
        return None
    try:
        return _Job(kill_on_close=True)
    except (OSError, AttributeError, ValueError):
        return None  # the call is started as without one (`CHILD`), and its result says so


def _spawn(argv, cwd, *, supervise: bool, launch: Launch, **streams) -> tuple[subprocess.Popen, _Job | None]:
    """Create the process. Supervised on native Windows: suspended, with its job object already made (`_hold`)."""
    launch._launching()  # before anything is created: the caller records that a process may exist from now on
    job = _supervised_job() if supervise else None
    extra = {"creationflags": CREATE_SUSPENDED} if job is not None else {}
    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=safe_env(), start_new_session=(os.name == "posix"), **streams,
                                **extra)
    except BaseException:
        if job is not None:
            job.close()
        raise
    return proc, job


def _hold(proc: subprocess.Popen, job: _Job | None, *, tree: bool, supervise: bool,
          launch: Launch) -> tuple[_Job | None, str, bool]:
    """Put the process where stopping it stops what it starts: (its job or None, `Result.process_tree`, suspended).

    A supervised process on native Windows is still suspended here: it is put
    in its job before it runs one instruction of its own, so nothing it
    starts can be outside it. When the system refuses that, it runs as
    without a job (`CHILD`), and its result says so. `launch` learns the
    process and, for a supervised call, its identity and owner.
    """
    suspended = job is not None
    if suspended:
        try:
            job.assign(proc)
            held = JOB
        except OSError:
            job.close()
            job, held = None, CHILD
    else:
        job, held = _job(proc, tree or supervise)
    if supervise:
        identity = procinfo.identity(proc.pid)  # its start time: a later reader tells it from a recycled PID
        launch._began(proc.pid, held, identity, (os.getpid(), procinfo.identity()),
                      kill_on_close=job is not None and job.kill_on_close)
    else:
        launch._began(proc.pid, held)
    return job, held, suspended


def _process_file(pid_file: Path, proc: subprocess.Popen, argv, launch: Launch, supervise: bool) -> None:
    from .storage import save_json
    save_json(pid_file, {"pid": proc.pid, "command": argv[0], **({key: value for key, value in launch.recorded().items()
                                                                   if key != "pid"} if supervise else {})})


def _settle_tree(proc: subprocess.Popen, job: _Job | None, held: str, launch: Launch) -> tuple[bool | None, bool | None]:
    """After a supervised CLI exited by itself: (its whole tree confirmed ended, processes it left running).

    Processes it left are stopped: a CLI that answered must not leave work
    going on in the worktree. On native Windows the job is asked how many of
    its processes still run, given `SETTLE_SECONDS` to reach none; on POSIX
    the group is probed (signal 0). The CLI itself still ended by itself;
    only a tree that cannot be confirmed ended makes the call stopped with
    its stop refused (`Launch.kill_refused`). (None, None) when nothing held
    the tree (`CHILD`).
    """
    if held == JOB and job is not None:
        if job.empty_within(SETTLE_SECONDS):
            return True, False
        job.kill()
        ended = job.empty_within(GROUP_SECONDS)
        if not ended:
            launch._ended(True)
        return ended, True
    if held == GROUP and os.name == "posix":
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            return True, False
        except PermissionError:
            pass  # members this user may not signal, or zombies not yet reaped (macOS): settled below
        refused = _kill_group(proc.pid)
        if refused:
            launch._ended(True)
        return not refused, True
    return None, None


def _kill_group(pgid: int) -> bool:
    """SIGKILL what is left of a process group. True when the system still refused it after `GROUP_SECONDS`.

    The signal reaching the group, or ESRCH (no such group: none of its
    processes is left, zombies included), settles it. EPERM settles nothing:
    macOS answers it while the only members left are zombies not yet reaped
    (XNU's `killpg1` skips zombies and answers EPERM when it finds no other
    member: bsd/kern/kern_sig.c of apple-oss-distributions/xnu, read on
    October 18, 2026), and any system answers it when no member may be
    signalled by this user. So the call is repeated until it settles; a group
    still refused at the end is said to be, never taken for one that stopped.
    """
    deadline = time.monotonic() + GROUP_SECONDS
    while True:
        try:
            os.killpg(pgid, signal.SIGKILL)
            return False
        except ProcessLookupError:
            return False
        except PermissionError:
            if time.monotonic() >= deadline:
                return True
        time.sleep(0.02)


def _terminate(proc: subprocess.Popen, job: _Job | None = None, *, confirm: bool = False) -> bool:
    """Stop the command and what it started. True when the system refused the last signal to its group (`_kill_group`).

    `confirm` (supervised calls, native Windows): the stop of a job object
    counts only once the job is seen with no running process; until then, or
    when the system does not say, it is taken for refused.
    """
    if job is not None:
        job.kill()  # the child and what it started; what follows waits for the child and reaps it
    if confirm and job is not None:
        try:
            _terminate(proc)  # the child alone, as below; the job's own count decides
        finally:
            confirmed = job.empty_within(GROUP_SECONDS)
        return not confirmed
    if os.name != "posix" and proc.poll() is not None:
        return False
    refused = False
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass
        proc.wait()
    finally:
        if os.name == "posix":
            # The group may outlive its leader or contain children ignoring TERM.
            refused = _kill_group(proc.pid)
    return refused


def _captured(stream, limit: int = 60000) -> str:
    size = stream.seek(0, os.SEEK_END)
    stream.seek(0)
    if size <= limit:
        return stream.read().decode("utf-8", errors="replace")
    head = stream.read(limit // 2)
    stream.seek(-limit // 2, os.SEEK_END)
    tail = stream.read(limit // 2)
    return head.decode("utf-8", errors="replace") + "\n...[output truncated]...\n" + tail.decode("utf-8", errors="replace")


def execute(argv: list[str], cwd: Path, *, stdin: str = "", timeout: int = 600,
            pid_file: Path | None = None, on_stdout=None, on_stderr=None, tree: bool = False,
            launch: Launch | None = None, supervise: bool = False) -> Result:
    """Run a command to its end and return its bounded output.

    `on_stdout` and `on_stderr` receive the bytes of each stream as they are
    written, before the process ends. A chunk can end anywhere, also inside a
    line or a UTF-8 sequence: `LineReader` puts lines back together. Observers
    cannot change the outcome: one that raises is no longer called. Without
    observers the process writes to files exactly as it always did.

    `tree` stops, on native Windows, every process the command started when
    it is stopped (`_Job`); on POSIX the process group always does.
    `Result.process_tree` says which of the two held them, or `CHILD` when
    neither did: stopping the command then stops it alone.
    `Result.kill_refused` says that, stopping it on POSIX, the system never
    confirmed that its group was signalled or gone: nothing then says that
    what it started was stopped. Either way it is a result, never an error
    of the start, which is raised only by starting the program.

    `launch` (`Launch`) learns the process as it is started and stopped; only
    callers that record an attempt pass one.

    `supervise` (Phase 2.5 Step 09; recorded agent calls only) holds the
    whole tree and confirms its end. On native Windows the process is
    created suspended, put in a job object that stops everything in it when
    its last handle closes (`_Job(kill_on_close=True)`), its process file
    written with its identity, and only then resumed; a stop counts once the
    job is seen empty. On both systems, once the CLI exited by itself, what
    it left running is stopped and the tree confirmed ended (`_settle_tree`).
    `Result.tree_ended` and `left_behind` say what was found; a tree that
    could not be confirmed ended sets `kill_refused`. Without `supervise`
    nothing of this changes.
    """
    if not argv or not all(isinstance(x, str) and x for x in argv):
        raise ValueError("Invalid command: expected a list of nonempty arguments")
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        if on_stdout is None and on_stderr is None:
            return _execute(argv, cwd, stdin=stdin, timeout=timeout, pid_file=pid_file,
                            output=output, errors=errors, tree=tree, launch=launch or Launch(), supervise=supervise)
        return _observed(argv, cwd, stdin=stdin, timeout=timeout, pid_file=pid_file,
                         output=output, errors=errors, observers=(on_stdout, on_stderr), tree=tree,
                         launch=launch or Launch(), supervise=supervise)


def _execute(argv, cwd, *, stdin, timeout, pid_file, output, errors, tree=False, launch: Launch,
             supervise: bool = False) -> Result:
    proc, job = _spawn(argv, cwd, supervise=supervise, launch=launch, stdin=subprocess.PIPE, stdout=output,
                       stderr=errors, text=True, encoding="utf-8", errors="replace")
    job, held, suspended = _hold(proc, job, tree=tree, supervise=supervise, launch=launch)
    try:
        if pid_file is not None:
            _process_file(pid_file, proc, argv, launch, supervise)
        launch._started()
        if suspended:
            _resume(proc.pid)  # its process file and its record exist: now it may run
        proc.communicate(input=stdin, timeout=timeout)
        ended, left = _settle_tree(proc, job, held, launch) if supervise else (None, None)
        launch._finished(ended, left)
        return Result(proc.returncode, _captured(output), _captured(errors), process_tree=held,
                      kill_refused=ended is False, tree_ended=ended, left_behind=left)
    except subprocess.TimeoutExpired:
        refused = _terminate(proc, job, confirm=supervise)
        launch._ended(refused)
        launch._finished(not refused if supervise and held != CHILD else None, None)
        proc.communicate()
        return Result(proc.returncode or 124, _captured(output), _captured(errors), timed_out=True, process_tree=held,
                      kill_refused=refused, tree_ended=launch.tree_ended)
    except BaseException:
        refused = _terminate(proc, job, confirm=supervise)
        launch._ended(refused)
        launch._finished(not refused if supervise and held != CHILD else None, None)
        raise
    finally:
        if job is not None:
            job.close()
        if pid_file is not None and proc.poll() is not None:
            pid_file.unlink(missing_ok=True)


class _Capture:
    """Where the reader threads put what they read; closed before the files behind it are."""

    def __init__(self):
        self.lock = threading.Lock()
        self.closed = False


def _pump(source, sink, observer, capture: _Capture) -> None:
    """Copy one pipe into its file, showing each chunk to the observer. Runs in its own thread."""
    try:
        while True:
            chunk = source.read1(CHUNK_BYTES)  # returns what is available; empty only at the end
            if not chunk:
                return
            with capture.lock:
                if capture.closed:
                    return
                sink.write(chunk)
            if observer is not None:
                try:
                    observer(chunk)
                except Exception:  # noqa: BLE001 - observing must never disturb the process observed
                    observer = None
    except (OSError, ValueError):
        pass  # the pipe was closed under the reader
    finally:
        try:
            source.close()
        except OSError:
            pass


def _feed(pipe, text: str) -> None:
    """Write the input and close it, as `communicate` does, while the output is being read."""
    try:
        # The same wrapper `Popen(text=True)` builds, so the child receives the same bytes.
        with io.TextIOWrapper(pipe, encoding="utf-8", errors="replace", write_through=True) as stream:
            stream.write(text)
    except (OSError, ValueError):
        pass  # the child closed its input early


def _drain(threads: list[threading.Thread], seconds: float) -> None:
    deadline = time.monotonic() + seconds
    for thread in threads:
        if thread.ident is not None:
            thread.join(max(0.0, deadline - time.monotonic()))


def _observed(argv, cwd, *, stdin, timeout, pid_file, output, errors, observers, tree=False, launch: Launch,
              supervise: bool = False) -> Result:
    """`_execute` with both streams on pipes, each read by its own thread so neither can fill up."""
    proc, job = _spawn(argv, cwd, supervise=supervise, launch=launch, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    job, held, suspended = _hold(proc, job, tree=tree, supervise=supervise, launch=launch)
    capture = _Capture()
    readers = [threading.Thread(target=_pump, args=(source, sink, observer, capture), daemon=True,
                                name=f"patchrondo-read-{name}")
               for name, source, sink, observer in (("stdout", proc.stdout, output, observers[0]),
                                                    ("stderr", proc.stderr, errors, observers[1]))]
    writer = threading.Thread(target=_feed, args=(proc.stdin, stdin), daemon=True, name="patchrondo-write-stdin")

    def captured(timed_out: bool, refused: bool = False, ended: bool | None = None, left: bool | None = None) -> Result:
        _drain(readers, DRAIN_SECONDS)
        with capture.lock:
            capture.closed = True  # a reader still blocked on a descendant's pipe writes nothing more
            code = (proc.returncode or 124) if timed_out else proc.returncode
            return Result(code, _captured(output), _captured(errors), timed_out=timed_out, process_tree=held,
                          kill_refused=refused, tree_ended=ended, left_behind=left)

    try:
        if pid_file is not None:
            _process_file(pid_file, proc, argv, launch, supervise)
        launch._started()
        for thread in (*readers, writer):
            thread.start()
        if suspended:
            _resume(proc.pid)  # its process file and its record exist: now it may run
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            refused = _terminate(proc, job, confirm=supervise)
            launch._ended(refused)
            launch._finished(not refused if supervise and held != CHILD else None, None)
            proc.wait()
            return captured(True, refused, launch.tree_ended)
        # What it left running is stopped before its pipes are read to the end: a descendant holding them is gone.
        ended, left = _settle_tree(proc, job, held, launch) if supervise else (None, None)
        launch._finished(ended, left)
        return captured(False, ended is False, ended, left)
    except BaseException:
        refused = _terminate(proc, job, confirm=supervise)
        launch._ended(refused)
        launch._finished(not refused if supervise and held != CHILD else None, None)
        _drain(readers, DRAIN_SECONDS)
        with capture.lock:
            capture.closed = True
        raise
    finally:
        if job is not None:
            job.close()
        if pid_file is not None and proc.poll() is not None:
            pid_file.unlink(missing_ok=True)


class LineReader:
    """Turn byte chunks of any shape into complete text lines.

    Bytes are kept until their newline arrives, so a line or a multi-byte
    character split between two chunks is decoded whole. A line longer than
    `limit` bytes is dropped and counted in `oversized`, which bounds memory
    whatever the process prints. `close` hands over a last line without a newline.
    """

    def __init__(self, on_line, limit: int = LINE_BYTES):
        self.on_line, self.limit = on_line, limit
        self.pending = bytearray()
        self.skipping = False
        self.oversized = 0

    def _keep(self, piece: bytes) -> None:
        if self.skipping:
            return
        if len(self.pending) + len(piece) > self.limit:
            self.pending.clear()
            self.skipping = True
        else:
            self.pending += piece

    def _end(self) -> None:
        if self.skipping:
            self.skipping = False
            self.oversized += 1
            return
        line = self.pending.decode("utf-8", errors="replace")
        self.pending.clear()
        self.on_line(line[:-1] if line.endswith("\r") else line)

    def feed(self, chunk: bytes) -> None:
        start = 0
        while True:
            end = chunk.find(b"\n", start)  # a newline byte is never part of a longer UTF-8 sequence
            if end < 0:
                self._keep(chunk[start:])
                return
            self._keep(chunk[start:end])
            self._end()
            start = end + 1

    def close(self) -> None:
        if self.pending or self.skipping:
            self._end()


def short_log(text: str, length: int = 24000) -> str:
    if len(text) <= length:
        return text
    return text[:length] + "\n...[output truncated]...\n"
