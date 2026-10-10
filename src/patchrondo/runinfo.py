"""What is actually attached to a task: its lock and the processes running or waiting for it.

`status` in `state.json` says what the last checkpoint recorded, not whether a
process exists. A `run`/`resume` process records itself in
`<task>/attached/<pid>.json` for as long as it lives, including while it waits
for a quota retry without the lock. `runtime()` combines those markers, the
lock and the persisted state into one answer, and says "unverified" whenever
the host cannot confirm a process.

Markers and this module are for display and for refusing duplicate starts.
They never unlock a task and never authorize a provider call.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess

from . import procinfo
from .storage import now, read_json, save_json

MARKERS = "attached"
# Activities a person has to act on before the task can make progress.
NEEDS_ATTENTION = {"stale_lock", "interrupted", "plan_only", "plan_unverified", "recovery_stopped",
                   "paused", "blocked"}
ACTIVE = {"starting", "running", "running_unverified", "waiting_retry"}


@contextmanager
def attached(task_path: Path, auto_resume: bool):
    """Record this process as attached to a task. Best effort: a failed write never stops a run."""
    file = task_path / MARKERS / f"{os.getpid()}.json"
    try:
        save_json(file, {"pid": os.getpid(), "start": procinfo.identity(), "since": now(),
                         "auto_resume": bool(auto_resume)})
    except OSError:
        file = None
    try:
        yield
    finally:
        if file is not None:
            try:
                file.unlink(missing_ok=True)
                file.parent.rmdir()  # only succeeds when no other process is attached
            except OSError:
                pass


def _lock(path: Path) -> dict | None:
    file = path / ".run.lock"
    if not file.exists():
        return None
    try:
        data = read_json(file)
        pid, token, since = data.get("pid"), data.get("start"), data.get("time")
    except (OSError, ValueError, AttributeError):
        pid = token = since = None
    known = type(pid) is int and pid > 0
    return {"pid": pid if known else None, "since": since if isinstance(since, str) else None,
            "holder": procinfo.state(pid, token if isinstance(token, str) else None) if known else procinfo.UNKNOWN}


def processes(path: Path, owned: list[subprocess.Popen] | None = None) -> list[dict]:
    """Processes recorded for a task: `owned` children of this dashboard, then attached markers."""
    found: dict[int, dict] = {}
    for child in owned or []:
        found[child.pid] = {"pid": child.pid, "since": None, "auto_resume": None, "owned": True,
                            "state": procinfo.ALIVE if child.poll() is None else procinfo.DEAD}
    folder = path / MARKERS
    for file in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            data = read_json(file)
            pid = data["pid"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if type(pid) is not int or pid in found:
            continue
        token = data.get("start") if isinstance(data.get("start"), str) else None
        state = procinfo.state(pid, token)
        if state == procinfo.DEAD:
            file.unlink(missing_ok=True)  # our own display marker of a process that is gone
            continue
        found[pid] = {"pid": pid, "since": data.get("since"), "owned": False, "state": state,
                      "auto_resume": data.get("auto_resume") if type(data.get("auto_resume")) is bool else None}
    return [entry for entry in found.values() if entry["state"] != procinfo.DEAD]


def runtime(path: Path, state: dict, owned: list[subprocess.Popen] | None = None) -> dict:
    """Process truth for one task, next to (not instead of) its persisted status."""
    lock = _lock(path)
    attached_now = processes(path, owned)
    alive = [entry for entry in attached_now if entry["state"] == procinfo.ALIVE]
    unverified = [entry for entry in attached_now if entry["state"] == procinfo.UNKNOWN]
    status = state.get("status")
    plan = state.get("recovery") if isinstance(state.get("recovery"), dict) else None
    plan_status = plan.get("status") if plan else None

    if lock is not None:
        if lock["holder"] == procinfo.ALIVE or alive:
            activity = "running"
        elif lock["holder"] == procinfo.DEAD:
            activity = "stale_lock"
        else:
            activity = "running_unverified"
    elif status == "running":
        activity = "interrupted"  # a checkpoint says running, but nothing holds the lock
    elif status in {"done", "blocked"}:
        activity = status
    elif status == "paused" and plan_status == "scheduled":
        if any(entry["auto_resume"] is not False for entry in alive):
            activity = "waiting_retry"
        elif any(entry["auto_resume"] is not False for entry in unverified):
            activity = "plan_unverified"
        else:
            activity = "plan_only"
    elif alive:
        activity = "starting"  # a process exists and has not taken the lock yet
    elif status == "ready":
        activity = "ready"
    elif status == "paused":
        activity = "recovery_stopped" if plan_status == "stopped" else "paused"
    else:
        activity = "unknown"

    startable = lock is None and status in {"ready", "paused", "running"} and activity != "starting"
    return {
        "activity": activity,
        "lock": lock,
        "processes": attached_now,
        "needs_attention": activity in NEEDS_ATTENTION,
        "active": activity in ACTIVE,
        "can_start": startable,
        # A waiting process makes a second automatic run pointless; a manual run stays possible.
        "waiting_process": activity == "waiting_retry",
        "can_stop": os.name == "posix" and bool(alive),
        # The engine re-checks every recorded PID before it removes a lock; on native Windows it cannot.
        "can_unlock": os.name == "posix" and activity == "stale_lock",
    }
