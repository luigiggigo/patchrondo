"""What a later run finds of an attempt that may still run, and what it may settle by itself (Phase 2.5, Step 09).

An attempt is uncertain when a run finds it without an end (the run that
started it was lost), or ended while what it started may still run
(`execution.AttemptRecord.uncertain`). Before Step 09 every such attempt
paused the task until the user released it. Now one assessment reads what
is on record, and only proof settles anything:

- `NEVER_STARTED`: no process of it can exist. Its folder was never made
  (a call is started only in its folder), or its launch record still says
  `prepared` (written before the CLI is asked to start; `launching` is
  written before the process is created, so `prepared` means it never was).
- `TERMINATED_UNRECORDED`: its process and everything it started are shown
  gone. From the launch record's `ended` stage when it confirmed the whole
  tree ended, or from the identity of the process (`procinfo.tree_state`):
  its group has no member (POSIX), or the job object that held it, set to
  stop everything in it when its last handle closes, was closed because the
  run that held it ended (native Windows).
- `ACTIVE`: a process of it is shown running.
- `UNCERTAIN`: nothing shows either: no launch record (a version before
  Step 09, an adapter that reports no process, or a run lost before it was
  written), a process about to be created that no record names, a process
  without its identity (a PID alone is never enough), what it started
  unknown (no group, no job object that stops its processes).
- `CORRUPT`: what is on record contradicts itself or cannot be read.
- `TERMINATED_RECORDED`: the attempt's end is saved and settled; nothing to
  do (shown by the diagnosis only).

The assessment reads files and asks the system about processes; it writes
nothing and changes no record, so a Resume while nothing changed does
nothing again. What the call did is never read from its files: a reply file
is not an answer, and an absent process file is not a stopped process.
`core._settle_attempts` acts on it: `NEVER_STARTED` ends the attempt as not
started (no provider call charged), `TERMINATED_UNRECORDED` reconciles it
(`Termination.RECONCILED`, its outcome unknown, its charges kept) and pauses
once, and every other finding pauses with `attempt_uncertain` and its
diagnosis. Sensitive process details (PIDs, start times) stay in the task's
private run folder; the codes below never name one.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import os
from pathlib import Path

from . import execution, procinfo, runfiles

MARKER = "launch.json"   # in the attempt's own folder; never shown in the Logs tab or put in a bundle (not a log)
VERSION = 1
PREPARED, LAUNCHING, STARTED, ENDED = "prepared", "launching", "started", "ended"
STAGES = (PREPARED, LAUNCHING, STARTED, ENDED)
_PROCESS_FIELDS = ("pid", "start", "tree", "kill_on_close", "owner_pid", "owner_start")


class Finding(StrEnum):
    NEVER_STARTED = "never_started"
    TERMINATED_RECORDED = "terminated_recorded"
    TERMINATED_UNRECORDED = "terminated_unrecorded"
    ACTIVE = "active"
    UNCERTAIN = "uncertain"
    CORRUPT = "corrupt"


# What each code says, for people. Fixed words: no PID, no path outside the task, nothing an agent wrote.
WORDS = {
    "folder_missing": "its folder was never made, and a call is only ever started in its own folder",
    "launch_not_reached": "its launch record shows that its CLI was never about to be started",
    "ended_confirmed": "its launch record shows that the call ended and that every process it started was confirmed ended",
    "settled": "its end is on record, and nothing it started may still run",
    "group_gone": "its CLI's process is gone, and its process group has no member left",
    "job_closed": ("its CLI's process is gone, and the job object that held it, set to stop every process in it, was "
                   "closed when the run that held it ended"),
    "process_running": "its CLI's process is running",
    "group_running": "a process of its process group is running",
    "owner_running": "the run that holds its job object is still running",
    "no_launch_record": ("its folder has no launch record: it was started by a version before this one, by an adapter "
                         "that reports no process, or the run stopped before the record was written"),
    "launch_unconfirmed": ("its launch record shows that its CLI was about to be started, and no record names the "
                           "process: it may have been created"),
    "no_identity": "its process is named by a number only, which another program may have now",
    "no_process_id": "no record names its process",
    "process_unknown": "the system did not say whether its CLI's process is running",
    "descendants_unknown": "nothing held what its CLI started (no process group, no job object), so it may still run",
    "owner_unknown": "the system did not say whether the run that held its job object has ended",
    "group_unknown": "the system did not say whether its process group still has members",
    "folder_unreadable": "its folder could not be read",
    "folder_not_plain": "its folder is not a plain folder (a link, a junction or a file)",
    "folder_missing_after_start": "its record says it started, and its folder is missing",
    "launch_record_unreadable": "its launch record cannot be read",
    "launch_record_other_attempt": "its launch record names another attempt",
    "stage_contradicts_status": "its launch record contradicts the saved status of the attempt",
    "process_file_unreadable": "a process file in its folder cannot be read",
}
ALIVE_CODES = frozenset({"process_running", "group_running", "owner_running"})


class Unreadable(ValueError):
    """A launch record that cannot be read or does not have the shape this version writes."""


def marker(attempt_id: str, stage: str, at: str, launch=None) -> dict:
    """The launch record of an attempt at `stage`; with a `process.Launch` once the process exists."""
    if stage not in STAGES:
        raise ValueError(f"unknown launch stage {stage!r:.20}")
    record = {"version": VERSION, "attempt": attempt_id, "stage": stage, "at": at}
    if launch is not None and launch.pid is not None:
        record.update({key: value for key, value in launch.recorded().items()}, supervised=launch.supervised)
        if stage == ENDED:
            record.update(stopped=launch.stopped, kill_refused=launch.kill_refused, tree_ended=launch.tree_ended,
                          left_behind=launch.left_behind)
    return record


def read_marker(folder: Path) -> dict | None:
    """The launch record in an attempt's folder; None when there is none. Raises `Unreadable`, changing nothing."""
    file = Path(folder) / MARKER
    try:
        if not runfiles.plain_file(file):
            if os.path.lexists(file):
                raise Unreadable("the launch record is not a plain file")
            return None
        raw = json.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Unreadable(f"the launch record cannot be read: {exc}") from None
    if not isinstance(raw, dict) or raw.get("version") != VERSION or raw.get("stage") not in STAGES \
            or not isinstance(raw.get("attempt"), str):
        raise Unreadable("the launch record is not one this version writes")
    pid = raw.get("pid")
    if pid is not None and (type(pid) is not int or pid <= 0):
        raise Unreadable("the launch record names no valid process")
    return raw


def held(record: dict | None, attempt_id: str) -> dict:
    """How the call's processes were held at its end, from its launch record: what `core._next_call` reads.

    Empty when the record is missing, of another attempt or not at its end:
    nothing is then known, and nothing is taken as held.
    """
    if not isinstance(record, dict) or record.get("attempt") != attempt_id or record.get("stage") != ENDED:
        return {}
    return {"attempt": attempt_id, "tree": record.get("tree"), "refused": record.get("kill_refused"),
            "ended": record.get("tree_ended"), "left": record.get("left_behind")}


@dataclass(frozen=True)
class Assessment:
    attempt: str
    finding: Finding
    folder: str                    # relative to `runs`
    evidence: tuple[str, ...]      # codes of `WORDS`: what was shown
    unknown: tuple[str, ...]       # codes of `WORDS`: what was not
    stage: str | None              # of the launch record, None without one
    ended: bool                    # the attempt has an end on record
    reply_file: bool = False       # a reply file is in its folder: never used as an answer

    @property
    def settles(self) -> bool:
        """Whether a run may settle the attempt by itself: only from proof."""
        return self.finding in (Finding.NEVER_STARTED, Finding.TERMINATED_UNRECORDED)

    def brief(self) -> dict:
        """Codes only, for the task's `last_error` and its history: no PID, no path, no agent text."""
        return {"attempt": self.attempt, "finding": str(self.finding), "evidence": list(self.evidence),
                "unknown": list(self.unknown), "launch_record": self.stage or "none"}

    def lines(self) -> list[str]:
        """The diagnosis, for people."""
        shown = [f"- {WORDS.get(code, code)}" for code in self.evidence]
        missing = [f"- {WORDS.get(code, code)}" for code in self.unknown]
        out = [f"Attempt {self.attempt} (runs/{self.folder}): {FINDING_WORDS[self.finding]}."]
        if shown:
            out += ["Shown:", *shown]
        if missing:
            out += ["Not known:", *missing]
        if self.reply_file:
            out.append("A reply file is in its folder; it is not used as an answer.")
        return out


FINDING_WORDS = {
    Finding.NEVER_STARTED: "no process of it was ever started",
    Finding.TERMINATED_RECORDED: "it ended and its end is on record",
    Finding.TERMINATED_UNRECORDED: "it ended, with everything it started, and how it ended was not recorded",
    Finding.ACTIVE: "a process of it is still running",
    Finding.UNCERTAIN: "whether a process of it is still running is not known",
    Finding.CORRUPT: "what is on record about it contradicts itself or cannot be read",
}


def _processes(folder: Path) -> list[dict]:
    """The process files in the attempt's folder, as recorded. Raises `Unreadable` for one that cannot be read."""
    found = []
    for name in sorted(os.listdir(folder)):
        if not name.endswith(runfiles.PROCESS_SUFFIX):
            continue
        try:
            raw = json.loads((folder / name).read_text(encoding="utf-8"))
            pid = raw["pid"]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
            raise Unreadable(name) from None
        if type(pid) is not int or pid <= 0 or not isinstance(raw, dict):
            raise Unreadable(name)
        found.append(raw)
    return found


def _judge(sources: list[dict]) -> tuple[Finding, list[str], list[str]]:
    """Every process named, by its identity: all shown gone, one shown running, or not known."""
    shown, unknown, seen = [], [], set()
    finding = Finding.TERMINATED_UNRECORDED
    for source in sources:
        key = (source.get("pid"), source.get("start"))
        if key in seen:
            continue
        seen.add(key)
        if not (isinstance(source.get("start"), str) and source.get("start")):
            unknown.append("no_identity")  # never settled from a PID alone, on any system
            finding = finding if finding is Finding.ACTIVE else Finding.UNCERTAIN
            continue
        state, code = procinfo.tree_state({name: source.get(name) for name in _PROCESS_FIELDS})
        if state == procinfo.DEAD:
            shown.append(code)
        elif state == procinfo.ALIVE:
            shown.append(code)
            finding = Finding.ACTIVE
        else:
            unknown.append(code)
            finding = finding if finding is Finding.ACTIVE else Finding.UNCERTAIN
    return finding, list(dict.fromkeys(shown)), list(dict.fromkeys(unknown))


def assess(task_path: Path, item: execution.AttemptRecord, folder_name: str) -> Assessment:
    """What is on record of `item`, an attempt of the task at `task_path` whose folder is `runs/<folder_name>`.

    Reads only. See the module's description for each finding.
    """
    folder = Path(task_path) / runfiles.RUNS / folder_name

    def found(finding: Finding, evidence=(), unknown=(), stage=None, reply=False) -> Assessment:
        return Assessment(item.attempt_id, finding, folder_name, tuple(evidence), tuple(unknown), stage, item.ended,
                          reply)

    if not item.uncertain:
        return found(Finding.TERMINATED_RECORDED, ("settled",))
    try:
        os.lstat(folder)
    except FileNotFoundError:
        if item.status is execution.AttemptStatus.STARTING:
            return found(Finding.NEVER_STARTED, ("folder_missing",))
        return found(Finding.CORRUPT, unknown=("folder_missing_after_start",))
    except OSError:
        return found(Finding.UNCERTAIN, unknown=("folder_unreadable",))
    if not runfiles.plain_folder(folder):
        return found(Finding.CORRUPT, unknown=("folder_not_plain",))
    try:
        launch = read_marker(folder)
    except Unreadable:
        return found(Finding.CORRUPT, unknown=("launch_record_unreadable",))
    stage = launch["stage"] if launch is not None else None
    try:
        processes = _processes(folder)
        reply = any(name.endswith(".reply.md") for name in os.listdir(folder))
    except Unreadable:
        return found(Finding.UNCERTAIN, unknown=("process_file_unreadable",), stage=stage)
    except OSError:
        return found(Finding.UNCERTAIN, unknown=("folder_unreadable",), stage=stage)
    if launch is not None and launch["attempt"] != item.attempt_id:
        return found(Finding.CORRUPT, unknown=("launch_record_other_attempt",), stage=stage, reply=reply)
    if stage == PREPARED:
        # Written after its folder and before the CLI is asked to start; `launching` comes before any process exists.
        if item.status is execution.AttemptStatus.STARTING and not processes:
            return found(Finding.NEVER_STARTED, ("launch_not_reached",), stage=stage, reply=reply)
        return found(Finding.CORRUPT, unknown=("stage_contradicts_status",), stage=stage, reply=reply)
    if item.status is execution.AttemptStatus.RUNNING and stage == LAUNCHING and not processes:
        return found(Finding.CORRUPT, unknown=("stage_contradicts_status",), stage=stage, reply=reply)
    if stage == ENDED and launch.get("tree_ended") is True and launch.get("kill_refused") is not True:
        return found(Finding.TERMINATED_UNRECORDED, ("ended_confirmed",), stage=stage, reply=reply)
    sources = ([launch] if launch is not None and launch.get("pid") is not None else []) + processes
    if not sources:
        why = "no_launch_record" if launch is None else "launch_unconfirmed"
        return found(Finding.UNCERTAIN, unknown=(why,), stage=stage, reply=reply)
    finding, shown, unknown = _judge(sources)
    if launch is None:
        unknown = ["no_launch_record", *unknown]
        finding = Finding.ACTIVE if finding is Finding.ACTIVE else Finding.UNCERTAIN
    return found(finding, shown, unknown, stage=stage, reply=reply)
