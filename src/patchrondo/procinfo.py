"""Read-only process identity checks for status display.

`identity(pid)` returns an opaque token for one process instance (its start
time), so a recycled PID is not mistaken for the process that was recorded.
`state(pid, token)` answers "alive", "dead" or "unknown"; "unknown" is the
honest answer whenever the platform cannot confirm either.

These answers describe what the dashboard shows. They never unlock a task:
`storage.TaskLock` keeps its own, more conservative rule.
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
