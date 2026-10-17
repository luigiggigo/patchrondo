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
import time

from .activity import clip, redact
from .storage import _unshared, now, private_dir

LEDGER = "evidence.jsonl"
VERSION = 1
KINDS = ("command", "review", "manual", "authorization", "invalidation", "restart")
RECORD_ID = re.compile(r"E\d*-\d{4,}")  # what `append` names a record: E-0001, or E2-0001 in a ledger started again
MAX_RECORDS = 5000            # records read from one ledger; a task never comes near
TEXT_LIMIT = 2000             # characters of a summary
FIELD_LIMIT = 600             # characters of any other text
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

    def counts(self, record: dict | None) -> bool:
        """Whether a record can be used as proof: read intact, and not invalidated since."""
        return bool(record) and record["trusted"] and record["invalidated"] is None

    def last(self, kind: str, criterion: str | None = None) -> dict | None:
        """The most recent record of a kind, optionally about one criterion, whatever its state."""
        return next((record for record in reversed(self.records) if record.get("kind") == kind
                     and (criterion is None or criterion in (record.get("criteria") or []))), None)

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
    lines = data.split(b"\n")
    incomplete = bool(lines[-1])  # every record ends with a newline: what follows the last one was cut short
    records, problems, previous, trusted = [], [], "", True
    for number, line in enumerate(lines[:-1][:MAX_RECORDS], 1):
        try:
            record = json.loads(line.decode("utf-8"))
        except ValueError:
            record = None
        if not isinstance(record, dict) or not isinstance(record.get("hash"), str) or not isinstance(record.get("id"), str):
            if trusted:
                problems.append(f"Record {number} of the evidence ledger cannot be read")
            trusted = False
            continue
        if trusted and (record.get("prev") != previous or record["hash"] != _digest(record) or record.get("seq") != number):
            problems.append(f"Record {number} of the evidence ledger ({record['id']}) does not fit the records before it: "
                            "it was changed, or a record was removed or inserted")
            trusted = False
        previous = record["hash"]
        records.append({**record, "trusted": trusted, "invalidated": None})
    if len(lines) - 1 > MAX_RECORDS:
        problems.append(f"The evidence ledger has more than {MAX_RECORDS} records; the rest is not read")
    by_id = {record["id"]: record for record in records}
    for record in records:
        if record.get("kind") == "invalidation" and record["trusted"]:
            for identifier in record.get("of") or []:
                target = by_id.get(identifier)
                if target is not None and target["invalidated"] is None and target["seq"] < record["seq"]:
                    target["invalidated"] = {"by": record["id"], "at": record.get("at"), "reason": record.get("reason")}
    return Ledger(records, problems, previous if trusted else "", incomplete)


def append(task_path: Path, kind: str, **fields) -> dict:
    """Append one record and return it. The caller holds the task lock and has read a ledger without problems."""
    if kind not in KINDS:
        raise ValueError(f"Unknown kind of evidence: {kind}")
    file = private_dir(task_path) / LEDGER
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
    generation = len(list(task_path.glob("evidence.damaged-*.jsonl"))) + 2
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


def artifact_changed(artifact: dict) -> bool:
    """Whether a file recorded as an artifact is gone or no longer the file that was hashed."""
    try:
        path = Path(artifact["path"])
        info = path.stat()
        if info.st_size != artifact["size"]:
            return True
        if info.st_mtime_ns == artifact.get("mtime_ns"):
            return False  # same size and time as when it was hashed: not read again
        return fingerprint_file(path)["sha256"] != artifact["sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        return True


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
        elif any(artifact_changed(artifact) for artifact in record.get("artifacts") or [] if isinstance(artifact, dict)):
            found.setdefault("A file this evidence refers to changed or is no longer there", []).append(record["id"])
    return found
