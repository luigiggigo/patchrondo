"""Opt-in automatic resume after a provider usage limit.

`providers.py` parses reset times; this module decides whether and when a
paused task may be retried, keeps that plan in `state.json` and waits for it
without holding the task lock. It only waits for a limit to reset. Message
classification can be wrong, so every retry is bounded by the configured caps.

`run_task` calls `admit`, `on_failure` and `note_progress` while it holds the
lock. `supervise` is the outer loop that waits between runs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import time

from .storage import LockBusy, TaskLock, now, read_json, save_json

DEFAULTS = {"enabled": False, "quota_only": True, "max_consecutive_retries": 3,
            "initial_backoff_seconds": 120, "max_backoff_seconds": 1800,
            "max_total_wait_seconds": 86400, "reset_safety_margin_seconds": 30}
LIMITS = {"max_consecutive_retries": (1, 10), "initial_backoff_seconds": (10, 3600),
          "max_backoff_seconds": (10, 86400), "max_total_wait_seconds": (60, 604800),
          "reset_safety_margin_seconds": (0, 3600)}
POLL_SECONDS = 30  # wall-clock recheck interval while waiting; also survives system suspend


class RecoveryAborted(RuntimeError):
    """Automatic recovery cannot continue safely; the persisted plan is left untouched."""


def settings(section) -> dict:
    """Validate the `recovery` configuration section; a missing section keeps recovery disabled."""
    if section is None:
        return dict(DEFAULTS)
    if not isinstance(section, dict):
        raise ValueError("recovery must be a JSON object")
    unknown = sorted(set(section) - set(DEFAULTS))
    if unknown:
        raise ValueError(f"Unknown recovery setting: {', '.join(unknown)}")
    merged = {**DEFAULTS, **section}
    for field in ("enabled", "quota_only"):
        if type(merged[field]) is not bool:
            raise ValueError(f"recovery.{field} must be a JSON boolean (true/false)")
    if not merged["quota_only"]:
        raise ValueError("recovery.quota_only must be true: only quota failures are retried automatically")
    for field, (low, high) in LIMITS.items():
        if type(merged[field]) is not int or not low <= merged[field] <= high:
            raise ValueError(f"recovery.{field} must be an integer between {low} and {high}")
    if merged["max_backoff_seconds"] < merged["initial_backoff_seconds"]:
        raise ValueError("recovery.max_backoff_seconds must not be below recovery.initial_backoff_seconds")
    return merged


def active(cfg: dict, flag: bool | None) -> bool:
    """`--auto-resume` / `--no-auto-resume` override `recovery.enabled`."""
    return cfg["recovery"]["enabled"] if flag is None else bool(flag)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def stamp_up(moment: datetime) -> str:
    """Whole-second timestamp rounded up: times a retry depends on must never be saved early."""
    if moment.microsecond:
        try:
            moment = moment.replace(microsecond=0) + timedelta(seconds=1)
        except OverflowError:
            pass  # the last second of year 9999 is beyond any budget either way
    return stamp(moment)


def parse_time(value) -> datetime | None:
    """Read a persisted UTC timestamp; anything without an explicit offset is rejected."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment.astimezone(timezone.utc) if moment.tzinfo else None


def plan(*, failures: int, first_failure_at: datetime, failure_at: datetime,
         retry_at: datetime | None, cfg: dict, now: datetime) -> dict:
    """Decide what follows the `failures`-th consecutive quota failure. Pure: no I/O.

    The retry time is the later of the exponential backoff and a provider reset
    plus the safety margin, so a stated reset can lengthen the wait but never
    shorten it. A plan that is not yet due and would exceed the wait budget
    stops instead of being moved earlier.
    """
    if failures > cfg["max_consecutive_retries"]:
        return {"action": "stop", "reason": "max_consecutive_retries"}
    backoff = min(cfg["initial_backoff_seconds"] * 2 ** (failures - 1), cfg["max_backoff_seconds"])
    resume_at, source = failure_at + timedelta(seconds=backoff), "backoff"
    # A reset counts only if it was still ahead when the failure was observed.
    if retry_at is not None and retry_at > failure_at:
        try:
            reset = retry_at + timedelta(seconds=cfg["reset_safety_margin_seconds"])
        except OverflowError:
            return {"action": "stop", "reason": "reset_beyond_budget"}
        if reset >= resume_at:
            resume_at, source = reset, "provider_reset"
    if resume_at.microsecond:
        resume_at = resume_at.replace(microsecond=0) + timedelta(seconds=1)
    if resume_at > now and (resume_at - first_failure_at).total_seconds() > cfg["max_total_wait_seconds"]:
        return {"action": "stop", "reason": "reset_beyond_budget" if source == "provider_reset"
                else "wait_budget_exceeded"}
    return {"action": "retry", "resume_at": resume_at, "source": source}


def _audit(state: dict, name: str, **details) -> None:
    state["history"].append({"at": now(), "event": name, "iteration": state["iteration"], **details})


def _section(state: dict) -> dict | None:
    section = state.get("recovery")
    return section if isinstance(section, dict) else None


def _stop(state: dict, section: dict, reason: str) -> None:
    section.update(status="stopped", stop_reason=reason, resume_at=None, schedule_source=None)
    state["recovery"] = section
    _audit(state, "recovery_stopped", reason=reason, failures=section.get("consecutive_failures"))


def _decide(state: dict, cfg: dict, *, failures: int, first: datetime, failure_at: datetime,
            now: datetime) -> None:
    """Replace the recovery section with the plan for the quota failure in `last_error`."""
    limits, error = cfg["recovery"], state["last_error"]
    section = {"status": "scheduled", "consecutive_failures": failures,
               "max_consecutive_retries": limits["max_consecutive_retries"],
               "first_failure_at": stamp(first), "provider": error.get("provider"),
               "phase": state["phase"], "iteration": state["iteration"],
               "resume_at": None, "schedule_source": None, "stop_reason": None}
    decision = plan(failures=failures, first_failure_at=first, failure_at=failure_at,
                    retry_at=parse_time(error.get("retry_at")), cfg=limits, now=now)
    if decision["action"] == "stop":
        _stop(state, section, decision["reason"])
        return
    section.update(resume_at=stamp(decision["resume_at"]), schedule_source=decision["source"])
    state["recovery"] = section
    _audit(state, "recovery_scheduled", attempt=failures, resume_at=section["resume_at"],
           source=decision["source"], provider=section["provider"])


def admit(state: dict, cfg: dict, *, active: bool, retry_of: str | None, now: datetime) -> tuple[bool, bool]:
    """Under the task lock: may this run call a provider now? Returns (proceed, state_changed).

    `retry_of` is the `resume_at` a supervisor waited for. Such a retry proceeds
    only if that exact plan is still persisted and due; a plan changed by
    another process is left to its owner. Without `retry_of` the run was
    requested explicitly: it still honors a pending plan while recovery is
    active, and replaces it when recovery is off (manual resume).
    """
    section = _section(state)
    if not active:
        if retry_of is not None:
            return False, False
        if section is not None and section.get("status") in {"scheduled", "retrying"}:
            _audit(state, "recovery_cancelled", reason="manual_run")
        state["recovery"] = None
        return True, False
    scheduled = section is not None and section.get("status") == "scheduled"
    if retry_of is not None and not (scheduled and section.get("resume_at") == retry_of):
        return False, False
    error = state.get("last_error")
    quota = state.get("status") == "paused" and isinstance(error, dict) and error.get("kind") == "quota"
    if scheduled:
        resume_at = parse_time(section.get("resume_at"))
        if not quota or resume_at is None or type(section.get("consecutive_failures")) is not int:
            _stop(state, section, "invalid_schedule")  # never guess a retry time
            return False, True
        if now < resume_at:
            return False, False
        section["status"] = "retrying"
        _audit(state, "recovery_retry_started", attempt=section["consecutive_failures"],
               scheduled_for=section["resume_at"])
        return True, True
    if section is not None and section.get("status") == "retrying":
        return True, False  # explicit rerun of a retry whose runner died
    # No plan in force. A task paused on a quota failure still waits for its reset or backoff.
    state["recovery"] = None
    if not quota:
        return True, False
    failure_at = min(parse_time(error.get("at")) or now, now)
    _decide(state, cfg, failures=1, first=failure_at, failure_at=failure_at, now=now)
    section = state["recovery"]
    if section["status"] != "scheduled" or now < parse_time(section["resume_at"]):
        return False, True
    section["status"] = "retrying"
    _audit(state, "recovery_retry_started", attempt=1, scheduled_for=section["resume_at"])
    return True, True


def on_failure(state: dict, cfg: dict, *, active: bool, now: datetime) -> None:
    """Under the task lock, after `last_error` is set: plan a retry or end the recovery."""
    section = _section(state)
    if not active:
        state["recovery"] = None
        return
    retrying = section is not None and section.get("status") == "retrying"
    kind = state["last_error"]["kind"]
    if kind != "quota":
        # Every other failure needs a person; keep why the automatic recovery ended.
        if retrying:
            _stop(state, section, kind)
        else:
            state["recovery"] = None
        return
    first = parse_time(section.get("first_failure_at")) if retrying else None
    same_point = (first is not None and type(section.get("consecutive_failures")) is int
                  and (section.get("phase"), section.get("iteration")) == (state["phase"], state["iteration"]))
    _decide(state, cfg, failures=section["consecutive_failures"] + 1 if same_point else 1,
            first=first if same_point else now, failure_at=now, now=now)


def note_progress(state: dict) -> None:
    """Before each checkpoint: a retry that moved the task on ends the failure streak."""
    section = _section(state)
    if section is None or section.get("status") != "retrying":
        return
    if state["status"] in {"done", "blocked"} or \
            (state["phase"], state["iteration"]) != (section.get("phase"), section.get("iteration")):
        _audit(state, "recovery_completed", retries=section.get("consecutive_failures"),
               provider=section.get("provider"))
        state["recovery"] = None


def describe(section) -> str | None:
    """One line for the CLI about a persisted recovery section."""
    if not isinstance(section, dict):
        return None
    used, limit = section.get("consecutive_failures"), section.get("max_consecutive_retries")
    provider = section.get("provider") or "provider"
    if section.get("status") == "scheduled":
        return (f"Quota recovery: retry {used}/{limit} for {provider} planned at {section.get('resume_at')} "
                f"({section.get('schedule_source')}). Nothing retries unless a run with automatic resume is waiting.")
    if section.get("status") == "stopped":
        return (f"Quota recovery stopped: {section.get('stop_reason')} after {used} consecutive "
                f"failure(s). Resume manually when ready.")
    return None


def _snapshot(path: Path) -> tuple | None:
    if (path / ".run.lock").exists():
        return None  # a run may be writing the state; do not read alongside it
    try:
        state = read_json(path / "state.json")
        return state.get("status"), state.get("recovery")
    except (OSError, ValueError, AttributeError):
        return None  # mid-replace or unreadable: the next poll or the locked reread decides


def _record_wait(path: Path, resume_at: str) -> None:
    """Journal that a supervisor started waiting. The lock is held only for this write."""
    try:
        with TaskLock(path):
            state = read_json(path / "state.json")
            section = _section(state)
            if state.get("status") == "paused" and section is not None \
                    and section.get("status") == "scheduled" and section.get("resume_at") == resume_at:
                _audit(state, "recovery_wait_started", resume_at=resume_at, supervisor_pid=os.getpid())
                state["updated_at"] = now()
                save_json(path / "state.json", state)
    except LockBusy:
        pass  # another process is active; the retry itself will meet the lock


def _wait(path: Path, resume_at: datetime, expected: tuple, clock, sleep) -> None:
    """Sleep until `resume_at` with no lock held; return early if the plan changed on disk."""
    while (remaining := (resume_at - clock()).total_seconds()) > 0:
        sleep(min(remaining, POLL_SECONDS))
        if _snapshot(path) not in (None, expected):
            return


def supervise(home: Path, task_id: str, *, adapter=None, force_unlock: bool = False,
              auto_resume: bool | None = None, clock=utcnow, sleep=time.sleep, notify=None) -> dict:
    """Run a task and, while recovery is active, wait for and perform its planned retries.

    This is a foreground loop, not a service: when it ends or is interrupted,
    the plan stays in `state.json` and nothing retries until a new run starts.
    """
    from .core import config, run_task, task_dir

    notify = notify or (lambda message: None)
    cfg = config(home)
    if not active(cfg, auto_resume):
        return run_task(home, task_id, adapter=adapter, force_unlock=force_unlock,
                        auto_resume=False, clock=clock)
    path = task_dir(home, task_id)
    # Each streak ends in progress or a stop, and progress is bounded by the iteration limit.
    cycles = 2 * (cfg["workflow"]["max_iterations"] * 3 + 1) * (cfg["recovery"]["max_consecutive_retries"] + 1)
    retry_of = None
    for _ in range(cycles):
        try:
            # --unlock is a human decision for the first run only; retries never unlock.
            state = run_task(home, task_id, adapter=adapter, force_unlock=force_unlock and retry_of is None,
                             auto_resume=auto_resume, clock=clock, retry_of=retry_of)
        except LockBusy as exc:
            if retry_of is None:
                raise
            raise RecoveryAborted(
                "Automatic recovery stopped: the task lock is present, so another run is active or "
                "its runner died. The lock was not removed and the retry plan is unchanged. Check "
                "the process, then resume manually (use --unlock only after confirming it exited)") from exc
        section = _section(state)
        if state["status"] != "paused" or section is None or section.get("status") != "scheduled":
            return state
        resume_at = parse_time(section.get("resume_at"))
        if resume_at is None:
            return state
        notify(f"{task_id} · paused on a {section.get('provider') or 'provider'} usage limit · automatic retry "
               f"{section.get('consecutive_failures')}/{section.get('max_consecutive_retries')} at "
               f"{section['resume_at']} ({section.get('schedule_source')}). Waiting without the task lock; "
               "Ctrl+C stops waiting and keeps the plan.")
        try:
            _record_wait(path, section["resume_at"])
            _wait(path, resume_at, (state["status"], section), clock, sleep)
        except KeyboardInterrupt:
            notify(f"Stopped waiting. The plan to retry at {section['resume_at']} stays saved, but nothing "
                   "is waiting for it now: start resume again to wait, or use --no-auto-resume to retry at once.")
            return _reread(path, state)
        if not active(config(home), auto_resume):
            notify("Automatic recovery was disabled in the configuration; the task stays paused.")
            return _reread(path, state)
        retry_of = section["resume_at"]
    notify("Automatic recovery stopped: supervisor cycle limit reached; the task stays paused.")
    return _reread(path, state)


def _reread(path: Path, fallback: dict) -> dict:
    try:
        state = read_json(path / "state.json")
        return state if isinstance(state, dict) else fallback
    except (OSError, ValueError):
        return fallback
