"""The workflow history of a task: what PatchRondo decided or did, one event at a time, in `state.json`.

`state["history"]` lists in order what the orchestration did: a run started,
a step started or ended, a review was settled, the code was approved, the
task paused. Since October 17, 2026 every event written carries:

    version     the schema of the event: 1
    id          WE-0001, WE-0002, …: unique in its task, in the order of the list, never given twice
    task_id     the task it belongs to
    event       what happened, as before
    at          when it was recorded, in UTC
    iteration   the iteration of the task, as before
    phase       the phase the task was at when it was recorded
    truncated   only when a detail was cut: the path of each list or object cut, with its whole length

and the details of its kind. `append` sets these fields from the state and
from PatchRondo's clock; a detail can never set one, and nothing an agent
wrote sets any. Details are references and small summaries: the IDs of
ledger records (`E-0008`), of findings (`RF-001`) and of criteria
(`AC-01`), counts and fixed words of the backend. Text is bounded and passed
through `redact`, and what JSON cannot hold is written as bounded text. A
list or an object keeps its first 100 entries, over three levels, and is
never cut without saying so: `truncated` gives its path (`finding_ids`, or
`nested.a.b` inside a detail) and how many entries it had, 0 kept for one
past the third level. A text that was cut says so itself (`… [+N
characters]`).

An event is appended to the state in memory and saved with the checkpoint
that follows, in the same atomic write as the rest of the state. Its ID
exists once that write is done. No other file holds event IDs, so the ID of
an event the process never saved was never seen, and the next event gets
its number again. A new number is past every ID of the history and past its
length.

Events written before had `at`, `event`, `iteration` and their details only.
They are read as they are and never rewritten; the first new event of such a
task is numbered past them.

The history records what was done; it proves nothing. That a review was
completed says nothing about the code, that a development step ended says
nothing about a problem, and an event naming a ledger record is only a
reference to it: whether that record counts is the ledger's to say
(`evidence.py`). What the agents and the test runner show while they work is
in the activity journal (`activity.py`), never here. The engine never reads
the history to decide.
"""
from __future__ import annotations

import math
import re

from .activity import clip, redact
from .storage import now

VERSION = 1
EVENT_ID = re.compile(r"WE-(\d{4,})")
RESERVED = ("version", "id", "task_id", "event", "at", "iteration", "phase", "truncated")
TEXT_LIMIT = 600  # characters of a text detail
LIST_LIMIT = 100  # entries of a list, keys of an object, paths named in `truncated`
KEY_LIMIT = 60    # characters of a key
DEPTH = 3         # lists and objects inside a detail


def _number(entry) -> int:
    """The number of an event's ID; 0 for an event written before IDs, or for anything else."""
    name = entry.get("id") if isinstance(entry, dict) else None
    match = EVENT_ID.fullmatch(name) if isinstance(name, str) else None
    return int(match.group(1)) if match else 0


def _cut(cut: dict, path: str, length: int) -> None:
    """Note that the list or object at `path` was cut, and how many entries it had."""
    if len(cut) < LIST_LIMIT:
        cut[path] = length


def _bounded(value, cut: dict, path: str, depth: int = 0):
    """A detail as the history keeps it: JSON values, bounded, text redacted. What is cut is noted in `cut`."""
    if isinstance(value, str):
        return clip(redact(value), TEXT_LIMIT)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple, dict)) and depth >= DEPTH:
        _cut(cut, path, len(value))
        return None
    if isinstance(value, (list, tuple)):
        if len(value) > LIST_LIMIT:
            _cut(cut, path, len(value))
        return [_bounded(item, cut, f"{path}.{index}", depth + 1) for index, item in enumerate(value[:LIST_LIMIT])]
    if isinstance(value, dict):
        if len(value) > LIST_LIMIT:
            _cut(cut, path, len(value))
        kept = {}
        for key, item in list(value.items())[:LIST_LIMIT]:
            name = clip(str(key), KEY_LIMIT)
            kept[name] = _bounded(item, cut, f"{path}.{name}", depth + 1)
        return kept
    return clip(redact(str(value)), TEXT_LIMIT)


def append(state: dict, name: str, **details) -> dict:
    """Append one event to the history of `state` and return it. Saved with the state's next checkpoint.

    The caller holds the task lock. A detail named like a field of the event
    is a mistake of the caller: it raises ValueError and nothing is appended.
    """
    taken = sorted(set(details) & set(RESERVED))
    if taken:
        raise ValueError(f"Details cannot set the fields of an event: {', '.join(taken)}")
    events = state["history"]
    number = max([len(events), *(_number(entry) for entry in events)]) + 1
    task, cut = state.get("id"), {}
    entry = {"version": VERSION, "id": f"WE-{number:04d}", "task_id": task if isinstance(task, str) else None,
             "event": str(name), "at": now(), "iteration": state["iteration"], "phase": state.get("phase"),
             **{key: _bounded(value, cut, key) for key, value in details.items()}}
    if cut:
        entry["truncated"] = cut  # a list cut short is never taken for the whole of it
    events.append(entry)
    return entry
