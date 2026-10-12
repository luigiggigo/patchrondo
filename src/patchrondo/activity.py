"""Live activity journal: what the agents and the test runner do, recorded while they do it.

Each task has one private, append-only file, `activity.jsonl`, next to its
state. A run appends one JSON line per event while it holds the task lock; a
retry or a resume appends after what is there and never rewrites it. Every
event names its task, iteration, role, provider and attempt, and carries a
sequence number that only grows within the task.

The journal is an observation, not state. The engine never reads it, and a
journal that cannot be written switches itself off without stopping the run.
What an agent says while it works is progress: the verdict of a review comes
only from the validated final answer, which is not taken from here.

Readers page through the file with a cursor made of the journal's identity and
a byte offset, so a reader that reconnects continues exactly where it stopped,
also after the server was restarted. A cursor that no longer fits the file is
answered with the end of the journal and `reset`.

Text written by agents is bounded and passed through `redact`, a best-effort
filter for values that look like credentials. Internal reasoning is left out
by the provider parsers in `streams.py`, before anything reaches this module.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

JOURNAL = "activity.jsonl"
VERSION = 1
TEXT_LIMIT = 4000        # characters of a message or an output excerpt
FIELD_LIMIT = 400        # characters of any other text field
LIST_LIMIT = 40          # entries of a list or keys of an object
RECORD_BYTES = 24_000    # one line of the journal
ATTEMPT_EVENTS = 5000    # events one attempt records; later ones are counted, not stored
PAGE_EVENTS = 300        # events in one answer to a reader
PAGE_BYTES = 1_000_000   # bytes a reader looks at for one answer
LONG_FIELDS = {"text", "output"}
ALWAYS = {"completion", "error"}  # recorded even after the per-attempt limit
JOURNAL_ID = re.compile(r"J-[0-9a-f]{12}")
CURSOR = re.compile(r"(J-[0-9a-f]{12}):(\d{1,15})")

_NAME = r"[A-Za-z0-9_.-]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API[_-]?KEY|PRIVATE[_-]?KEY|ACCESS[_-]?KEY|CREDENTIAL)[A-Za-z0-9_.-]*"
_SECRETS = (
    # NAME=value and "name": "value", for names that say they hold a secret
    (re.compile(r"(?i)\b(" + _NAME + r"[\"']?\s*[=:]\s*)(\"[^\"\n]{0,400}\"|'[^'\n]{0,400}'|[^\s\"',;]{1,400})"),
     r"\1[redacted]"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"), r"\1[redacted]"),
    (re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
                r"|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}"
                r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"), "[redacted]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
     "[redacted private key]"),
)


def redact(text: str) -> str:
    """Mask values that look like credentials. Best effort: it recognizes common shapes only."""
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}… [+{len(text) - limit} characters]"


def _clean(value, key: str = "", depth: int = 0):
    """A bounded copy of an event field: short redacted text, numbers, flags and small collections."""
    if isinstance(value, str):
        limit = TEXT_LIMIT if key in LONG_FIELDS else FIELD_LIMIT
        if len(value) <= limit:
            return redact(value)
        # Redact a little past the cut, so a secret that straddles it is still recognized.
        return f"{redact(value[:limit + 400])[:limit]}… [+{len(value) - limit} characters]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth >= 3:
        return None
    if isinstance(value, (list, tuple)):
        return [_clean(item, key, depth + 1) for item in value[:LIST_LIMIT]]
    if isinstance(value, dict):
        return {str(name)[:60]: _clean(item, str(name), depth + 1) for name, item in list(value.items())[:LIST_LIMIT]}
    return clip(redact(str(value)), FIELD_LIMIT)


def _seq(line: bytes) -> int:
    try:
        record = json.loads(line)
    except ValueError:
        return 0
    seq = record.get("seq") if isinstance(record, dict) else None
    return seq if type(seq) is int and seq > 0 else 0


def _last_seq(file: Path, size: int) -> int:
    """Highest sequence number already in the journal; lines damaged by a crash are skipped."""
    with file.open("rb") as stream:
        start = max(0, size - 262_144)
        stream.seek(start)
        found = max((_seq(line) for line in stream.read().split(b"\n")), default=0)
        if found or start == 0:
            return found
        stream.seek(0)
        return max((_seq(line) for line in stream), default=0)


class Journal:
    """Appends the events of one attempt. It never raises: failing to record must not fail the work."""

    def __init__(self, task_path: Path, *, iteration: int, role: str, provider: str, clock=None):
        self.file = Path(task_path) / JOURNAL
        self.base = {"task": Path(task_path).name, "iteration": iteration, "role": role, "provider": provider,
                     "attempt": "A-" + uuid.uuid4().hex[:12]}
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.Lock()
        self.fd: int | None = None
        self.seq = self.count = self.dropped = 0
        try:
            self._open()
        except Exception:  # noqa: BLE001 - an unusable journal is switched off, whatever the reason
            self._off()

    def _open(self) -> None:
        self.fd = os.open(self.file, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o600)
        size = os.fstat(self.fd).st_size
        if size == 0:
            header = {"v": VERSION, "type": "journal", "id": "J-" + uuid.uuid4().hex[:12], "task": self.base["task"],
                      "at": self._now()}
            os.write(self.fd, (json.dumps(header, separators=(",", ":")) + "\n").encode("utf-8"))
            return
        with self.file.open("rb") as stream:
            stream.seek(size - 1)
            if stream.read(1) != b"\n":
                os.write(self.fd, b"\n")  # a run that died in the middle of a line: end the fragment
        self.seq = _last_seq(self.file, size)

    def _off(self) -> None:
        fd, self.fd = self.fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def _now(self) -> str:
        return self.clock().astimezone(timezone.utc).isoformat(timespec="milliseconds")

    def emit(self, kind: str, /, **fields) -> None:
        """Append one event of type `kind`. Text is bounded and redacted here, whatever the caller passes."""
        try:
            with self.lock:
                if self.fd is None:
                    return
                if self.count >= ATTEMPT_EVENTS and kind not in ALWAYS:
                    self.dropped += 1
                    return
                record = {"v": VERSION, "seq": self.seq + 1, "at": self._now(), **self.base, "type": str(kind)[:40]}
                record.update({name: _clean(value, name) for name, value in fields.items()
                               if value is not None and name not in record})
                line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                if len(line.encode("utf-8")) > RECORD_BYTES:
                    # Too long as a whole: the collections go first, then everything but the event's identity.
                    record = {name: value for name, value in record.items() if not isinstance(value, (list, dict))}
                    record["truncated"] = True
                    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                if len(line.encode("utf-8")) > RECORD_BYTES:
                    kept = {name: record[name] for name in ("v", "seq", "at", *self.base, "type", "truncated")}
                    line = json.dumps(kept, ensure_ascii=False, separators=(",", ":"))
                os.write(self.fd, (line + "\n").encode("utf-8"))  # one write per line: readers never see half of two
                self.seq += 1
                self.count += 1
        except Exception:  # noqa: BLE001
            self._off()

    def close(self) -> None:
        with self.lock:
            self._off()


@contextmanager
def attempt(task_path: Path, *, iteration: int, role: str, provider: str, clock=None, details: dict | None = None):
    """Record one attempt of a step: its start, what the caller emits, and how it ended.

    Whatever ends the attempt is raised again unchanged. `completed` means the
    step returned; whether its result is accepted is decided by the engine afterwards.
    `details` are facts about the attempt known when it starts, such as the
    model it was configured with; they are recorded with its start.
    """
    journal = Journal(task_path, iteration=iteration, role=role, provider=provider, clock=clock)
    started = time.monotonic()
    journal.emit("status", status="started", **(details or {}))
    outcome, kind = "completed", None
    try:
        yield journal
    except BaseException as exc:
        interrupted = isinstance(exc, KeyboardInterrupt)
        kind = str(getattr(exc, "kind", "interrupted" if interrupted else
                           "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "system_error"))
        outcome = "interrupted" if interrupted else "timeout" if kind == "timeout" else "failed"
        journal.emit("error", kind=kind, text=str(exc) or "Interrupted by the user")
        raise
    finally:
        journal.emit("completion", outcome=outcome, kind=kind, seconds=round(time.monotonic() - started, 1),
                     unrecorded=journal.dropped or None)
        journal.close()


# --- reading ------------------------------------------------------------------------

def _identity(stream) -> tuple[str | None, int]:
    """The journal's identity and the offset of its first event; (None, 0) while it has no complete line."""
    stream.seek(0)
    first = stream.readline(4096)
    if not first.endswith(b"\n"):
        return None, 0
    try:
        header = json.loads(first)
        if isinstance(header, dict) and header.get("type") == "journal" and JOURNAL_ID.fullmatch(str(header.get("id"))):
            return header["id"], len(first)
    except ValueError:
        pass
    # A journal whose first line was damaged still gets a stable identity.
    return "J-" + hashlib.sha256(first).hexdigest()[:12], 0


def _offset(stream, journal: str, first: int, size: int, token) -> int | None:
    """The byte offset a cursor names, or None when it does not fit this journal."""
    match = CURSOR.fullmatch(token) if isinstance(token, str) else None
    if match is None or match.group(1) != journal:
        return None
    offset = int(match.group(2))
    if offset <= first:
        return first if offset in (0, first) else None
    if offset > size:
        return None
    stream.seek(offset - 1)
    return offset if stream.read(1) == b"\n" else None  # a cursor always sits at the start of a line


def _back(stream, first: int, end: int, limit: int) -> int:
    """Start of the last `limit` complete lines before `end`, looking back a bounded distance."""
    floor = max(first, end - PAGE_BYTES)
    position, found, lowest = end, 0, None
    while position > floor:
        step = min(65536, position - floor)
        stream.seek(position - step)
        block = stream.read(step)
        index = len(block)
        while (index := block.rfind(b"\n", 0, index)) >= 0:
            lowest = position - step + index + 1
            found += 1
            if found == limit + 1:  # the first newline found ends the last line itself
                return lowest
        position -= step
    if floor == first:
        return first
    return end if lowest is None else lowest


def _forward(stream, start: int, end: int, limit: int) -> tuple[list[dict], int, int, bool]:
    """Events of the complete lines in [start, end): (events, offset after them, lines skipped, more to read)."""
    stream.seek(start)
    data = stream.read(min(end - start, PAGE_BYTES))
    full = len(data) == PAGE_BYTES
    cut = data.rfind(b"\n")
    if cut < 0:
        # No complete line: a line still being written, or damage longer than a page, which is stepped over.
        return [], start + (len(data) if full else 0), int(full), full
    events, offset, skipped = [], start, 0
    for line in data[:cut].split(b"\n"):
        if len(events) == limit:
            return events, offset, skipped, True
        offset += len(line) + 1
        try:
            record = json.loads(line)
        except ValueError:
            record = None
        if isinstance(record, dict) and type(record.get("seq")) is int and isinstance(record.get("type"), str):
            events.append(record)
        elif line.strip():
            skipped += 1
    return events, offset, skipped, full and offset < end


def read(task_path: Path, cursor=None, *, before=None, limit: int = PAGE_EVENTS) -> dict:
    """One page of a task's journal.

    Without a cursor: the last `limit` events. With `cursor`: the events after
    it. With `before`: the events that precede it. `next` continues forward and
    `start` continues backward. A cursor that does not fit the journal (another
    journal, a truncated file, an offset inside a line) is answered with the
    last events and `reset`, so the reader replaces what it shows.
    """
    limit = min(max(int(limit), 1), PAGE_EVENTS)
    page = {"journal": None, "events": [], "start": None, "next": None, "size": 0, "more": False,
            "more_before": False, "skipped": 0, "reset": cursor is not None or before is not None}
    try:
        stream = (Path(task_path) / JOURNAL).open("rb")
    except OSError:
        return page  # nothing was recorded yet
    with stream:
        journal, first = _identity(stream)
        if journal is None:
            return page
        # Measured after the first line was read, and never below it: a writer may have created the
        # file and written that line between two looks, and what is read must not end before it starts.
        size = max(os.fstat(stream.fileno()).st_size, first)
        end = size
        if before is not None:
            end = _offset(stream, journal, first, size, before)
            start = None if end is None else _back(stream, first, end, limit)
        elif cursor is not None:
            start = _offset(stream, journal, first, size, cursor)
        else:
            start = _back(stream, first, size, limit)
        page["reset"] = start is None
        if start is None:
            start, end = _back(stream, first, size, limit), size
        events, offset, skipped, more = _forward(stream, start, end, limit)
    page.update(journal=journal, events=events, start=f"{journal}:{start}", next=f"{journal}:{offset}", size=size,
                more=more, more_before=start > first, skipped=skipped)
    return page
