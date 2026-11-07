"""Evidence ledger: what was actually verified for a task, by whom and on which files.

Each task has one private, append-only file, `evidence.jsonl`, next to its
state. A record is written when something was checked, never when somebody
says it was: a command the runtime ran, a review the backend validated, a
check the user did by hand and attached, a decision the user took about an
operation. A criterion is verified only through records of this file.

Kinds of record:

    command        one configured command of the project, run by PatchRondo: exit code, log, what it covers
    review         one validated review: the verdict about the code and the outcome given to each criterion
    manual         a check done by the user, with where its proof can be found; a file is hashed by PatchRondo
    authorization  the user's decision about an operation that needs consent: granted or denied
    invalidation   earlier records that no longer count, and why (the files changed, an artifact changed)
    restart        the ledger was started again because the previous file failed its integrity check

Records are never edited. One that stops counting is named by a later
`invalidation` record, so the file tells the whole story in order.

Every record carries the hash of the record before it and its own, so a
record that was changed, removed or inserted is noticed when the file is
read: from the first one that does not fit, records are not trusted. This
makes silent edits visible. It is not a signature: the files belong to the
user, and somebody who rewrites the whole chain is not stopped by it.

Writers hold the task lock. Text is bounded and passed through `redact`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from .activity import clip, redact
from .storage import FileMutex, _unshared, now, private_dir

LEDGER = "evidence.jsonl"
ARCHIVED = "evidence.damaged-*.jsonl"  # ledgers `restart` put aside, unchanged
VERSION = 1
KINDS = ("command", "review", "manual", "authorization", "invalidation", "restart")
# The one executor that writes each kind. A command record is the runtime's: what an agent or a person says about a
# command is never one.
EXECUTORS = {"command": "runtime", "review": "reviewer", "manual": "user", "authorization": "user",
             "invalidation": "runtime", "restart": "runtime"}
# What a command record may say it showed (`verification.Status`, and `timeout` as earlier versions wrote it).
COMMAND_RESULTS = ("passed", "failed", "error", "blocked", "timeout")
RECORD_ID = re.compile(r"E\d*-\d{4,}")  # what `append` names a record: E-0001, or E2-0001 in a ledger started again
MAX_RECORDS = 5000            # records read from one ledger; a task never comes near
TEXT_LIMIT = 2000             # characters of a summary
FIELD_LIMIT = 600             # characters of any other text
MAX_DEPTH = 64                # levels of nesting of a value read from a file; what `append`'s writers write needs fewer than 10
MAX_ARTIFACTS = 8
MAX_ARTIFACT_BYTES = 8 * 1024 ** 3  # a file PatchRondo hashes for a manual check
# Results that count as proof, per kind. Anything else is recorded and proves nothing.
POSITIVE = {"command": "passed", "manual": "passed", "authorization": "granted"}
UNANCHORED = "The evidence ledger no longer holds the records the task state had seen"


def _canonical(record: dict) -> str:
    return json.dumps({key: value for key, value in record.items() if key != "hash"}, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def _digest(record: dict) -> str:
    """Hash of a record. It covers `prev`, so it also fixes everything before the record."""
    return hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()


def text(value, limit: int = FIELD_LIMIT) -> str:
    """Text as the ledger stores it: one bounded, redacted string."""
    return clip(redact(str(value).strip()), limit)


@dataclass(frozen=True)
class Ledger:
    """A ledger as read: every record with what is known about it, and what is wrong with the file.

    Each record carries two fields that are not in the file: `trusted`
    (False from the first record that does not fit the chain) and
    `invalidated` (the later record that says it no longer counts, or None).
    """
    records: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    head: str = ""
    incomplete: bool = False  # the file ends with a line that was never finished

    @property
    def count(self) -> int:
        return len(self.records)

    def get(self, identifier: str) -> dict | None:
        return next((record for record in self.records if record.get("id") == identifier), None)

    def sound(self, record: dict | None) -> bool:
        """Whether a record was read intact and has a shape this version writes.

        A record of a schema version this one does not know, of a kind it does
        not know, or written by another executor than the one of its kind
        (`EXECUTORS`), is never read as proof: `append` writes none of them, so
        such a record comes from a later version or from somebody's hand.
        """
        if not record or not record["trusted"]:
            return False
        kind = record.get("kind")
        return type(record.get("version")) is int and record["version"] == VERSION and kind in KINDS \
            and record.get("executor") == EXECUTORS[kind] and (kind != "command" or record.get("result") in COMMAND_RESULTS)

    def counts(self, record: dict | None) -> bool:
        """Whether a record can be used as proof: read intact, of a shape this version writes, and not invalidated since."""
        return self.sound(record) and record["invalidated"] is None

    def last(self, kind: str, criterion: str | None = None) -> dict | None:
        """The most recent record of a kind, optionally about one criterion, whatever its state."""
        return next((record for record in reversed(self.records) if record.get("kind") == kind
                     and (criterion is None or criterion in listed(record, "criteria"))), None)

    def anchored(self, count, head) -> bool:
        """Whether the ledger still holds, unchanged, the records a task state saw (`count` of them, ending at `head`)."""
        if type(count) is not int or count <= 0:
            return True
        return len(self.records) >= count and self.records[count - 1].get("hash") == head and self.records[count - 1]["trusted"]

    def for_state(self, seen: dict) -> Ledger:
        """The ledger as proof for a task state that saw the records `seen` names (its `count` and `head`).

        A ledger that no longer holds them unchanged trusts none of its
        records: the one removed or changed may be the invalidation that made
        another stop counting. A run starts such a ledger again; a reader,
        which changes nothing, counts none of its records instead.
        """
        if self.anchored(seen.get("count"), seen.get("head")):
            return self
        return Ledger([{**record, "trusted": False} for record in self.records], self.problems or [UNANCHORED], "", self.incomplete)


def read(task_path: Path) -> Ledger:
    """Read and check the ledger of a task. A missing file is an empty ledger."""
    file = task_path / LEDGER
    try:
        data = file.read_bytes()
    except FileNotFoundError:
        return Ledger()
    except OSError as exc:
        return Ledger(problems=[f"The evidence ledger cannot be read: {exc}"])
    return parse(data)


def shallow(value, limit: int = MAX_DEPTH) -> bool:
    """Whether a value read from JSON nests `limit` levels at most. Without recursion: it is the check before any."""
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, (dict, list)):
            if depth > limit:
                return False
            pending.extend((child, depth + 1) for child in (item.values() if isinstance(item, dict) else item))
    return True


def listed(record: dict, key: str) -> list:
    """A field of a record that `append`'s writers fill with a list, or an empty list when it holds anything else.

    Read from a file, a record is data: a value of another shape names
    nothing, and is never iterated, hashed or searched as if it were a list.
    """
    value = record.get(key)
    return value if isinstance(value, list) else []


def _named(of, earlier: dict[str, dict]) -> tuple[list[str], bool]:
    """The earlier records an invalidation makes stop counting, and whether it names them as its writers do.

    Every writer names a list of record IDs, never empty (`provenance.invalidate`,
    `core._sync`, the `validate` stage). Each ID of an earlier record in the
    list is taken, also beside values of another kind (fewer proofs, never
    more); an ID of no earlier record names nothing, as before. Anything but a
    non-empty list of text is not well formed: what else the invalidation
    meant cannot be known.
    """
    items = of if isinstance(of, list) else []
    named = [item for item in items if isinstance(item, str) and item in earlier]
    return named, bool(items) and all(isinstance(item, str) for item in items)


def parse(data: bytes) -> Ledger:
    """Check the bytes of a ledger as `read` does: the file read once by somebody else (an export, a bundle's copy).

    Every line is untrusted data. Besides the hash chain, a record does not
    fit when it takes the ID of an earlier record (one ID, one record: a later
    one could otherwise stand for an invalidated one) or when it is an
    invalidation whose `of` is not a list of record IDs (`_named`). Either is
    a problem of the ledger like a broken chain, from that record on, never
    read as naming nothing: the ledger then proves nothing to the gate
    (`ERROR`), a run starts it again, and a task state that saw the record
    is no longer anchored (`for_state`). What a malformed invalidation does
    name of earlier records still stops counting.
    """
    lines = data.split(b"\n")
    incomplete = bool(lines[-1])  # every record ends with a newline: what follows the last one was cut short
    records, problems, previous, trusted = [], [], "", True
    earlier: dict[str, dict] = {}  # the records read so far that fit, by ID
    for number, line in enumerate(lines[:-1][:MAX_RECORDS], 1):
        try:
            record = json.loads(line.decode("utf-8"))
            # Nested deeper than any writer nests (`shallow`), or than Python reads (RecursionError): it cannot be read.
            record = record if shallow(record) else None
            digest = _digest(record) if isinstance(record, dict) else None
        except (ValueError, RecursionError):
            record = None
        if not isinstance(record, dict) or not isinstance(record.get("hash"), str) or not isinstance(record.get("id"), str):
            if trusted:
                problems.append(f"Record {number} of the evidence ledger cannot be read")
            trusted = False
            continue
        if trusted and (record.get("prev") != previous or record["hash"] != digest or record.get("seq") != number):
            problems.append(f"Record {number} of the evidence ledger ({record['id']}) does not fit the records before it: "
                            "it was changed, or a record was removed or inserted")
            trusted = False
        if trusted and record["id"] in earlier:
            problems.append(f"Record {number} of the evidence ledger ({record['id']}) has the ID of an earlier record: an ID "
                            "names one record only")
            trusted = False
        named, formed = _named(record.get("of"), earlier) if trusted and record.get("kind") == "invalidation" else ([], True)
        if not formed:
            problems.append(f"Record {number} of the evidence ledger ({record['id']}) is an invalidation that does not name "
                            "its records as a list of record IDs: what it makes stop counting cannot be known")
            trusted = False
        previous = record["hash"]
        entry = {**record, "trusted": trusted, "invalidated": None}
        for identifier in named:
            if earlier[identifier]["invalidated"] is None:
                earlier[identifier]["invalidated"] = {"by": record["id"], "at": record.get("at"), "reason": record.get("reason")}
        records.append(entry)
        if trusted:
            earlier[record["id"]] = entry
    if len(lines) - 1 > MAX_RECORDS:
        problems.append(f"The evidence ledger has more than {MAX_RECORDS} records; the rest is not read")
    return Ledger(records, problems, previous if trusted else "", incomplete)


def append(task_path: Path, kind: str, **fields) -> dict:
    """Append one record and return it. The caller holds the task lock and has read a ledger without problems.

    The executor of a record is the one `EXECUTORS` names for its kind: one
    left out is filled in, another is refused. Writers of one ledger take
    turns (a brief `FileMutex`), so two of them never number a record alike.
    """
    if kind not in KINDS:
        raise ValueError(f"Unknown kind of evidence: {kind}")
    executor = fields.get("executor") or EXECUTORS[kind]
    if executor != EXECUTORS[kind]:
        raise ValueError(f"A {kind} record is written by the {EXECUTORS[kind]} only, never by {executor}")
    if kind == "command" and fields.get("result") not in COMMAND_RESULTS:
        raise ValueError(f"A command record says one of {', '.join(COMMAND_RESULTS)}, not {fields.get('result')!r}")
    if kind == "invalidation" and not _named(fields.get("of"), {})[1]:
        raise ValueError("An invalidation names the records it makes stop counting as a list of record IDs")
    fields["executor"] = executor
    file = private_dir(task_path) / LEDGER
    with FileMutex(file):
        ledger = read(task_path)
        if ledger.problems:
            raise RuntimeError("The evidence ledger failed its integrity check and must be restarted before it is written")
        # A ledger that was started again numbers its records apart ("E2-0003"), so that an ID kept elsewhere
        # can never come to mean another record.
        first = ledger.records[0] if ledger.records else fields
        generation = first.get("generation") if kind == "restart" or first.get("kind") == "restart" else None
        record = {"version": VERSION, "id": f"E{generation or ''}-{ledger.count + 1:04d}", "seq": ledger.count + 1, "at": now(),
                  "kind": kind, **{key: value for key, value in fields.items() if value is not None}, "prev": ledger.head}
        record["hash"] = _digest(record)
        line = (json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        fd = os.open(file, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "r+b") as stream:
            if ledger.incomplete:
                # A record cut short by a crash was never referred to by a saved state: it is dropped, not repaired.
                stream.seek(stream.read().rfind(b"\n") + 1)
                stream.truncate()
            else:
                stream.seek(0, os.SEEK_END)
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
    return {**record, "trusted": True, "invalidated": None}


def restart(task_path: Path, problems: list[str]) -> dict:
    """Put a ledger that failed its check aside, unchanged, and start a new one that says so.

    Nothing is deleted. The records of the old file prove nothing any more,
    so what they verified has to be verified again.
    """
    file = task_path / LEDGER
    generation = len(list(task_path.glob(ARCHIVED))) + 2
    kept = None
    if file.exists():
        kept = file.with_name(f"evidence.damaged-{generation - 1}-{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}.jsonl")
        _unshared(lambda: os.replace(file, kept))  # the interface may be reading it at this moment (Windows)
    return append(task_path, "restart", executor="runtime", result="restarted", generation=generation,
                  previous=kept.name if kept else None,
                  reason=text("; ".join(problems) or "The ledger could not be used", TEXT_LIMIT))


def fingerprint_file(path: Path) -> dict:
    """Size and SHA-256 of one regular file, read by PatchRondo itself. Raises ValueError when it cannot be used."""
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"{path} is not a regular file")
        info = path.stat()
        if info.st_size > MAX_ARTIFACT_BYTES:
            raise ValueError(f"{path} is larger than {MAX_ARTIFACT_BYTES // 1024 ** 3} GiB: PatchRondo does not hash it")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ValueError(f"{path} cannot be read: {exc}") from exc
    return {"path": str(path), "sha256": digest.hexdigest(), "size": info.st_size, "mtime_ns": info.st_mtime_ns}


INTACT, CHANGED, MISSING = "intact", "changed", "missing"


def artifact_state(artifact: dict) -> str:
    """Whether a file recorded as an artifact is still the file that was hashed: intact, changed or missing.

    A file that became a link is changed, whatever it points to: what was
    hashed was a regular file. Same size and time as when it was hashed is
    taken as unchanged without reading it again; somebody who can write the
    file can also set its time, which no check here prevents.
    """
    try:
        path = Path(artifact["path"])
        info = os.lstat(path)
    except FileNotFoundError:
        return MISSING
    except (OSError, ValueError, KeyError, TypeError):
        return CHANGED
    try:
        if not stat.S_ISREG(info.st_mode) or info.st_size != artifact["size"]:
            return CHANGED
        if info.st_mtime_ns == artifact.get("mtime_ns"):
            return INTACT
        return INTACT if fingerprint_file(path)["sha256"] == artifact["sha256"] else CHANGED
    except (OSError, ValueError, KeyError, TypeError):
        return CHANGED


def artifact_changed(artifact: dict) -> bool:
    """Whether a file recorded as an artifact is gone or no longer the file that was hashed."""
    return artifact_state(artifact) != INTACT


def outdated(ledger: Ledger, fingerprint: str | None) -> dict[str, list[str]]:
    """Records that stopped counting, by reason: {reason: [record IDs]}.

    A record that depends on the files (`scope` "code") is out of date when
    the fingerprint of the task's files is no longer the one it was recorded
    on. A record with artifacts is out of date when one of them changed.
    Decisions of the user do not depend on files.
    """
    found: dict[str, list[str]] = {}
    for record in ledger.records:
        if not ledger.counts(record) or record.get("kind") not in {"command", "review", "manual"}:
            continue
        if fingerprint is not None and record.get("scope", "code") == "code" and record.get("fingerprint") != fingerprint:
            found.setdefault("The files of the task changed after this was recorded", []).append(record["id"])
        elif any(artifact_changed(artifact) for artifact in listed(record, "artifacts") if isinstance(artifact, dict)):
            found.setdefault("A file this evidence refers to changed or is no longer there", []).append(record["id"])
    return found
