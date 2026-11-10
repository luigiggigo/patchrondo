"""Read-only process identity checks for status display.

`identity(pid)` returns an opaque token for one process instance (its start
time), so a recycled PID is not mistaken for the process that was recorded.
`state(pid, token)` answers "alive", "dead" or "unknown"; "unknown" is the
honest answer whenever the platform cannot confirm either.

These answers describe what the dashboard shows. They never unlock a task
by themselves: `storage.TaskLock` keeps its own, more conservative rule for
the process that holds the lock.

`tree_state(entry)` (Phase 2.5, Step 09) is the one rule for whether the
process tree of a recorded call may still run, read from what its process
file or its attempt's launch record names (`process.Launch.recorded`). The
forced unlock, the release of an attempt and the reconciliation of an
uncertain attempt all use it. A process is never taken for gone from its PID
alone on native Windows, nor from its absence alone anywhere: what it
started is gone only when its group has no member (POSIX) or when the job
object that held it stopped everything in it, which a job set to stop its
processes when its last handle closes did once the process that held the
handle (its owner) is gone (native Windows).
"""
from __future__ import annotations

import os
import subprocess
import sys

ALIVE, DEAD, UNKNOWN = "alive", "dead", "unknown"


def _windows(pid: int) -> tuple[str, str | None]:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        # 87 = ERROR_INVALID_PARAMETER: no such process. Anything else (access denied) is not an answer.
        return (DEAD, None) if ctypes.get_last_error() == 87 else (UNKNOWN, None)
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            return UNKNOWN, None
        if code.value != 259:  # STILL_ACTIVE
            return DEAD, None
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return ALIVE, None
        return ALIVE, f"win:{(created.dwHighDateTime << 32) | created.dwLowDateTime}"
    finally:
        kernel.CloseHandle(handle)


def _procfs(pid: int) -> tuple[str, str | None] | None:
    """Linux: state and start time from /proc; None when /proc is not available."""
    if not os.path.isdir("/proc/self"):
        return None
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as stream:
            fields = stream.read().rsplit(")", 1)[1].split()
    except FileNotFoundError:
        return DEAD, None
    except (OSError, IndexError):
        return UNKNOWN, None
    if len(fields) < 20:
        return UNKNOWN, None
    if fields[0] in {"Z", "X"}:  # a zombie has exited; only its parent has not collected it yet
        return DEAD, None
    return ALIVE, f"linux:{fields[19]}"


def _posix(pid: int) -> tuple[str, str | None]:
    found = _procfs(pid)
    if found is not None:
        return found
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return DEAD, None
    except PermissionError:
        return UNKNOWN, None
    except OSError:
        return UNKNOWN, None
    try:
        # No /proc (macOS, BSD): the start time printed by ps identifies the instance.
        shown = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                               timeout=3, check=False, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return ALIVE, None
    started = shown.stdout.strip()
    return (ALIVE, f"ps:{started}") if shown.returncode == 0 and started else (ALIVE, None)


def _inspect(pid: int) -> tuple[str, str | None]:
    if type(pid) is not int or pid <= 0:
        return UNKNOWN, None
    try:
        return _windows(pid) if sys.platform == "win32" else _posix(pid)
    except Exception:  # noqa: BLE001 - a failed probe is "unknown", never a crash of the caller
        return UNKNOWN, None


def identity(pid: int | None = None) -> str | None:
    """Token of a live process instance (this process by default); None if it cannot be read."""
    return _inspect(os.getpid() if pid is None else pid)[1]


def state(pid, token: str | None) -> str:
    """Whether the recorded process instance still exists.

    Without a recorded token a live PID may belong to another program, so the
    answer is "unknown" rather than "alive".
    """
    found, current = _inspect(pid)
    if found != ALIVE:
        return found
    if not token or current is None:
        return UNKNOWN
    return ALIVE if current == token else DEAD


def group_state(pgid) -> str:
    """POSIX: whether the process group `pgid` still has a member: ALIVE, DEAD (the system knows none) or UNKNOWN.

    A member this user may not signal, or a group of zombies not yet reaped
    (macOS answers EPERM for those), is UNKNOWN, never DEAD.
    """
    if os.name != "posix" or type(pgid) is not int or pgid <= 1:
        return UNKNOWN
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return DEAD
    except OSError:
        return UNKNOWN
    return ALIVE


# The codes `tree_state` gives with its answer: fixed words, for diagnostics and records (never a PID).
LEADER_UNKNOWN = frozenset({"no_process_id", "no_identity", "process_unknown"})


def tree_state(entry) -> tuple[str, str]:
    """Whether the process tree a process file or a launch record names may still run: (ALIVE|DEAD|UNKNOWN, code).

    `entry` holds `pid` and, for a supervised call, `start` (its identity),
    `tree` (`process.GROUP`, `JOB` or `CHILD`), `kill_on_close`, `owner_pid`
    and `owner_start`. In this order: the process itself (by its identity;
    without one, on POSIX any process with its PID counts as it, and on
    native Windows nothing is known); then, once it is gone, what it
    started: on POSIX its group (its PID, since every call starts a new
    session), on native Windows only a job object that stops its processes
    when its last handle closes, once the owner that held that handle is gone
    too. DEAD only when all of that is shown; ALIVE when a process is shown
    running; UNKNOWN otherwise.
    """
    if not isinstance(entry, dict):
        return UNKNOWN, "no_process_id"
    pid = entry.get("pid")
    if type(pid) is not int or pid <= 0:
        return UNKNOWN, "no_process_id"
    token = entry.get("start") if isinstance(entry.get("start"), str) and entry.get("start") else None
    tree = entry.get("tree")
    if token is not None:
        leader = state(pid, token)
    elif sys.platform == "win32":
        return UNKNOWN, "no_identity"  # a PID alone is never enough here: it may already be another program's
    else:
        leader = _inspect(pid)[0]  # whichever process has that PID now counts as the one recorded
    if leader == ALIVE:
        return ALIVE, "process_running"
    if leader != DEAD:
        return UNKNOWN, "process_unknown"
    if sys.platform != "win32":
        if tree not in (None, "process_group"):
            return UNKNOWN, "descendants_unknown"
        return {ALIVE: (ALIVE, "group_running"), DEAD: (DEAD, "group_gone")}.get(group_state(pid),
                                                                              (UNKNOWN, "group_unknown"))
    if tree == "job_object" and entry.get("kill_on_close") is True:
        owner, mark = entry.get("owner_pid"), entry.get("owner_start")
        if type(owner) is int and owner > 0 and isinstance(mark, str) and mark:
            holder = state(owner, mark)
            if holder == DEAD:
                return DEAD, "job_closed"
            if holder == ALIVE:
                return ALIVE, "owner_running"
        return UNKNOWN, "owner_unknown"
    return UNKNOWN, "descendants_unknown"
