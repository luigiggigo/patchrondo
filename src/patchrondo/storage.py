"""Private, crash-consistent task persistence."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        path.chmod(0o700)
    return path


def atomic_text(path: Path, data: str) -> None:
    private_dir(path.parent)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        if os.name == "posix":
            path.chmod(0o600)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_json(path: Path, obj: dict) -> None:
    atomic_text(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class LockBusy(RuntimeError):
    """The task lock exists: a run is active, or it is stale and needs a manual check."""


class TaskLock:
    """Single host lock; manual recovery after power loss with --unlock."""

    def __init__(self, task_dir: Path, force: bool = False):
        self.path = task_dir / ".run.lock"
        self.force = force
        self.owner = False

    def __enter__(self):
        if self.force and self.path.exists():
            # If the Python runner was SIGKILLed, an agent subprocess may
            # still be editing the worktree. Fail closed until it exits.
            for entry in (self.path.parent / "runs").glob("iteration-*/*.active-process.json"):
                try:
                    child_pid = int(read_json(entry)["pid"])
                except (ValueError, KeyError, OSError, json.JSONDecodeError):
                    raise RuntimeError(f"Unreadable process file: {entry}. Check manually")
                if _alive(child_pid):
                    raise RuntimeError(f"Agent/test process is still active (PID {child_pid}); do not unlock the task")
            try:
                pid = int(read_json(self.path)["pid"])
            except (ValueError, KeyError, OSError, json.JSONDecodeError):
                pid = -1
            if pid > 0 and _alive(pid):
                raise RuntimeError(f"Task is still active (PID {pid}): cannot unlock")
            self.path.unlink(missing_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise LockBusy("Task is already running or the lock is stale. Check the process before using --unlock") from exc
        self.owner = True
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "time": now()}, stream)
        return self

    def __exit__(self, *_):
        if self.owner:
            self.path.unlink(missing_ok=True)
            self.owner = False


def _alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if os.name != "posix":
        # Cannot reliably determine liveness without optional dependencies on Windows.
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
