"""Evidence bundles: a portable ZIP of what PatchRondo recorded about one task, and the check of such a file.

An export reads the task's private files and writes nothing: no lock is taken,
no file of the task is changed, no command or model is run, and the worktree
is not read. What goes in:

    manifest.json               what the bundle holds: every file with its SHA-256 and size, what was left out and why
    summary.md                  the same for people
    task/contract.json          the task, its description and its criteria as saved and as the exported ledger judges them
    gate/evaluation.json        the acceptance gate as the engine last saved it, and whether it is about the exported ledger
    evidence/ledger.jsonl       the evidence ledger, byte for byte (its hash chain can be checked again)
    evidence/validity.json      the validity of each record, from the ledger alone (`provenance.recorded`)
    reviews/references.json     the last review's verdict and references, and the findings as saved
    verification/checks.json    the results of the last test stage, without their output
    history/events.json         the workflow history as saved
    logs/…, artifacts/…         detailed bundles only: sanitized copies of the logs and report copies the ledger names

A consistent view: the state and the ledger are each read whole, then both
again; the export goes on only when the second reading equals the first, so
the bundle holds a state and a ledger that were on disk together at one
moment. A task that keeps changing is refused (`Inconsistent`) after a few
attempts. A gate saved before the ledger moved on is kept as saved and named
as such: the bundle is then incomplete, never silently coherent.

A summary bundle (the default) holds records, decisions and references. A
detailed bundle adds the logs and report copies the ledger names, read from
the task's own `runs` folder only (no link, junction or other file followed),
cut to their last part and masked (`verification.scrub`): masking recognizes
common shapes of secrets only, so a detailed bundle is meant for people the
user would show the logs to.

What a bundle establishes, and what it does not: each file's SHA-256 and the
manifest's own content digest show whether the file was changed after the
export, as far as somebody did not recompute them; `verify` also checks the
ledger's hash chain and that the gate, validity and counts agree with the
ledger they describe. Nothing is signed (`signature` is null): integrity is
not authenticity, and a bundle with matching hashes does not prove that
PatchRondo produced it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
import zipfile

from . import __version__, acceptance as criteria, evidence, provenance, verification, winacl
from .storage import now

FORMAT = "patchrondo-evidence-bundle"
VERSION = 1
MODES = ("summary", "detailed")
MANIFEST = "manifest.json"
ATTEMPTS = 4                      # readings of the state and the ledger before an export is refused
EXPORT_SECONDS = 60               # an export that takes longer is stopped
DESCRIPTION_CHARS = 64_000        # of task.md
LOG_BYTES = 256 * 1024            # the last part of each log kept in a detailed bundle
REPORT_BYTES = 1024 * 1024        # of each report copy
MAX_LOGS = 60                     # logs and report copies of one detailed bundle
DETAIL_BYTES = 24 * 1024 ** 2     # of all of them together
HASH_BYTES = 256 * 1024 ** 2      # a log larger than this is not hashed to compare it with its record
# What `verify` reads at most.
MAX_ARCHIVE_BYTES = 128 * 1024 ** 2
MAX_ENTRIES = 400
MAX_ENTRY_BYTES = 64 * 1024 ** 2
MAX_TOTAL_BYTES = 192 * 1024 ** 2
MAX_RATIO = 250                   # uncompressed / compressed, for an entry over RATIO_FLOOR
RATIO_FLOOR = 1024 * 1024
LOG_NAME = re.compile(r"iteration-\d{3}/[A-Za-z0-9][A-Za-z0-9._-]{0,120}")
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}(/[A-Za-z0-9][A-Za-z0-9._-]{0,120}){0,4}")
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
INTEGRITY = ("Each file is listed with its SHA-256 and size, and content_sha256 is the SHA-256 of this manifest without "
             "bundle_id, content_sha256 and created_at. This shows changes made after the export by somebody who did not "
             "recompute them. Nothing is signed: it does not prove that PatchRondo produced the bundle.")
LIMITS = {"log_bytes": LOG_BYTES, "report_bytes": REPORT_BYTES, "max_logs": MAX_LOGS, "detail_bytes": DETAIL_BYTES,
          "description_chars": DESCRIPTION_CHARS, "ledger_records": evidence.MAX_RECORDS}


class BundleError(ValueError):
    """The export cannot be made: what was asked is not valid, or a limit was reached."""


class Inconsistent(RuntimeError):
    """The task kept changing while it was read: no coherent view of it could be taken."""


def canonical(value) -> bytes:
    """The one serialization of every JSON file of a bundle: sorted keys, two-space indent, UTF-8, a final newline."""
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _compact(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def content_digest(manifest: dict) -> str:
    """The SHA-256 of a manifest without its own identity and its creation time: equal content, equal digest."""
    return hashlib.sha256(_compact({key: value for key, value in manifest.items()
                                    if key not in {"bundle_id", "content_sha256", "created_at"}})).hexdigest()


def gate_id(saved: dict) -> str | None:
    """The ID a saved gate evaluation should carry (`gate.Gate.id`): `GE-` and the SHA-256 of what it was decided from."""
    body = {key: value for key, value in saved.items() if key not in {"id", "message", "at"}}
    try:
        return f"GE-{hashlib.sha256(_compact(body)).hexdigest()[:16]}"
    except (TypeError, ValueError):
        return None


# --- reading the task once ------------------------------------------------------------------

def _bytes(file: Path) -> bytes | None:
    try:
        return file.read_bytes()
    except FileNotFoundError:
        return None


def consistent(task_path: Path, attempts: int = ATTEMPTS, pause: float = 0.05) -> tuple[bytes, bytes]:
    """The bytes of state.json and of the ledger as they were on disk together, or `Inconsistent`.

    Each file is read whole, then both again. Equal readings mean that each
    file stayed the same over an interval that covers the other's reading:
    there was a moment when both held these bytes. A missing ledger is empty.
    """
    for attempt in range(attempts):
        first = (_bytes(task_path / "state.json"), _bytes(task_path / evidence.LEDGER) or b"")
        second = (_bytes(task_path / "state.json"), _bytes(task_path / evidence.LEDGER) or b"")
        if first[0] is None:
            raise FileNotFoundError("Task not found")
        if first == second:
            return first
        time.sleep(pause * (attempt + 1))
    raise Inconsistent("The task changed while it was being read for the export. Nothing was exported: try again, or "
                       "export it when its run pauses")


def _masked(value, secrets):
    """A copy of saved data with every string masked (`verification.scrub`)."""
    if isinstance(value, str):
        return verification.scrub(value, secrets)
    if isinstance(value, list):
        return [_masked(item, secrets) for item in value]
    if isinstance(value, dict):
        return {str(key): _masked(item, secrets) for key, item in value.items()}
    return value


def _part(task_path: Path, folder: str, name: str, limit: int) -> tuple[bytes | None, dict]:
    """The last `limit` bytes of one file of the task's runs, what is known about it, or why it was left out.

    Only `runs/iteration-NNN/<name>`: every part of the way is checked not to
    be a link or a junction, the file must be a regular file, and the file
    opened must be the one checked (no swap in between). The whole file is
    hashed to say whether it is still the one its record names.
    """
    if not LOG_NAME.fullmatch(name):
        return None, {"reason": "unsafe_name"}
    runs = task_path / "runs"
    target = runs / name
    try:
        for part in (runs, target.parent):
            info = os.lstat(part)
            if stat.S_ISLNK(info.st_mode) or winacl.is_reparse_point(part) or not stat.S_ISDIR(info.st_mode):
                return None, {"reason": "not_a_folder_of_the_task"}
        checked = os.lstat(target)
        if stat.S_ISLNK(checked.st_mode) or winacl.is_reparse_point(target):
            return None, {"reason": "link_refused"}
        if not stat.S_ISREG(checked.st_mode):
            return None, {"reason": "not_a_regular_file"}
        if not target.resolve().is_relative_to(task_path.resolve()):
            return None, {"reason": "outside_the_task"}
        with open(target, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size) != (checked.st_dev, checked.st_ino, checked.st_size):
                return None, {"reason": "changed_during_export"}
            digest, size = hashlib.sha256(), opened.st_size
            hashed = size <= HASH_BYTES
            if hashed:
                for chunk in iter(lambda: stream.read(1 << 20), b""):
                    digest.update(chunk)
            stream.seek(max(0, size - limit))
            data = stream.read(limit)
            after = os.fstat(stream.fileno())
        if (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
            return None, {"reason": "changed_during_export"}
    except FileNotFoundError:
        return None, {"reason": "missing"}
    except OSError:
        return None, {"reason": "unreadable"}
    return data, {"size": size, "sha256": digest.hexdigest() if hashed else None, "truncated": size > limit, "folder": folder}


def _sanitized(data: bytes, truncated: bool, size: int, secrets) -> bytes:
    text = data.decode("utf-8", errors="replace")
    if truncated:
        cut = text.find("\n")
        text = text[cut + 1:] if 0 <= cut < 4096 else text
        text = f"[PatchRondo] Only the end of this file is in the bundle: it holds {size} bytes.\n" + text
    return verification.scrub(text, secrets).encode("utf-8")


# --- what goes in -------------------------------------------------------------------------

@dataclass
class Bundle:
    name: str
    data: bytes
    manifest: dict


def validity_rows(ledger: evidence.Ledger) -> list[dict]:
    rows = []
    for record in ledger.records:
        if record.get("kind") not in provenance.JUDGED:
            continue
        found = provenance.recorded(ledger, record)
        rows.append({"id": found.evidence, "seq": record.get("seq"), "kind": found.kind, "result": found.result,
                     "validity": str(found.validity), "causes": [str(cause) for cause in found.causes], "check": found.check,
                     "invalidated_by": found.invalidated_by, "criteria": [str(item) for item in record.get("criteria") or []],
                     "run": record.get("run"), "at": record.get("at"), "fingerprint": record.get("fingerprint")})
    return rows


def counts(rows: list[dict]) -> dict:
    found = {str(validity): 0 for validity in provenance.Validity}
    for row in rows:
        found[row["validity"]] = found.get(row["validity"], 0) + 1
    return found


def _sources(ledger: evidence.Ledger) -> list[tuple[str, str, str, dict]]:
    """Logs and report copies the records name: (folder of the bundle, name, record ID, what the record says of it)."""
    found, seen = [], set()
    for record in ledger.records:
        if not ledger.sound(record):
            continue
        name = record.get("log")
        if isinstance(name, str) and ("logs", name) not in seen:
            seen.add(("logs", name))
            found.append(("logs", name, record["id"], {"sha256": record.get("log_sha256")}))
        report = (record.get("check") or {}).get("report") if isinstance(record.get("check"), dict) else None
        if isinstance(report, dict) and isinstance(report.get("path"), str) and ("artifacts", report["path"]) not in seen:
            seen.add(("artifacts", report["path"]))
            found.append(("artifacts", report["path"], record["id"], {"sha256": report.get("sha256")}))
    return found


def _summary(manifest: dict, contract: dict, gate_file: dict | None) -> bytes:
    """summary.md: what the bundle says, for people. Fixed words and saved values only; deterministic."""
    task = contract["task"]
    gate_ = manifest["gate"]
    lines = [f"# Evidence bundle of {task['id']}", "", f"Task: {task.get('title') or '(no title)'}",
             f"Saved status: {task.get('status')} · phase {task.get('phase')} · iteration {task.get('iteration')}", ""]
    if gate_ is None:
        lines += ["## Acceptance gate", "", "Not evaluated: the task has no saved gate evaluation. Nothing is said about whether "
                  "it may be completed.", ""]
    else:
        lines += ["## Acceptance gate", "", f"Decision: {gate_['decision']} (evaluation {gate_['id']}, {gate_.get('at')})",
                  f"Completion allowed: {'yes' if gate_['completion_allowed'] else 'no'} · next action: {gate_.get('next_action')}"]
        if not gate_["about_exported_ledger"]:
            lines.append("This evaluation was saved before the ledger reached the state exported here: it does not describe "
                         "the exported evidence.")
        for entry in (gate_file or {}).get("saved", {}).get("conditions") or []:
            if isinstance(entry, dict):
                named = ", ".join([*(entry.get("criteria") or []), *(entry.get("evidence") or [])])
                lines.append(f"- {entry.get('decision')}: {entry.get('reason')} → {entry.get('action')}" + (f" ({named})" if named else ""))
        lines.append("")
    judged = contract.get("judged")
    if judged:
        lines += ["## Criteria", "", f"Code: {judged['code']} · task as a whole: {judged['outcome']}", "",
                  "| ID | Required | Status | Evidence |", "|---|---|---|---|"]
        for item in judged["criteria"]:
            lines.append(f"| {item['id']} | {'yes' if item['required'] else 'no'} | {item['status']} | {', '.join(item['evidence']) or '—'} |")
        lines.append("")
    else:
        lines += ["## Criteria", "", "Not tracked: the task was created before criteria were recorded.", ""]
    ledger = manifest["ledger"]
    lines += ["## Evidence", "", f"{ledger['count']} records in the ledger" + (f", {ledger['problems']} problem(s) found when it was read"
                                                                                 if ledger["problems"] else ""),
              "Validity: " + ", ".join(f"{name} {number}" for name, number in manifest["validity"].items()), ""]
    lines += ["A passed check that is not VALID passed when it was recorded and does not count for the task as it is now.", ""]
    if manifest["omissions"]:
        lines += ["## Left out", ""] + [f"- {entry['item']}: {entry['reason']}" for entry in manifest["omissions"]] + [""]
    lines += ["## Integrity and authenticity", "", INTEGRITY, "",
              "Check this file with: patchrondo bundle verify <file.zip>", ""]
    return "\n".join(lines).encode("utf-8")


def build(task_path: Path, mode: str = "summary", *, clock=now, secrets=None, deadline: float | None = None) -> Bundle:
    """Export the evidence of one task. Reads only; raises `BundleError`, `Inconsistent` or FileNotFoundError."""
    if mode not in MODES:
        raise BundleError(f"mode must be one of {', '.join(MODES)}")
    deadline = deadline if deadline is not None else time.monotonic() + EXPORT_SECONDS
    secrets = verification.known_secrets() if secrets is None else secrets
    state_bytes, ledger_bytes = consistent(task_path)
    try:
        state = json.loads(state_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BundleError("The task state cannot be read") from exc
    if not isinstance(state, dict):
        raise BundleError("The task state cannot be read")
    task_id = task_path.name
    # Only the lines the ledger finished writing: a record cut short was never referred to by a saved state.
    whole = ledger_bytes[:ledger_bytes.rfind(b"\n") + 1]
    if len(whole) > MAX_ENTRY_BYTES:
        raise BundleError(f"The evidence ledger is larger than {MAX_ENTRY_BYTES} bytes: a bundle of it could not be checked")
    raw = evidence.parse(whole)
    omissions = [{"item": "evidence/ledger.jsonl", "reason": "a record being written when the ledger was read is left out"}] \
        if whole != ledger_bytes else []
    try:
        record = criteria.Acceptance.load(state)
    except ValueError:
        record = None
        omissions.append({"item": "task/contract.json#judged", "reason": "the saved criteria cannot be read by this version"})
    ledger = raw.for_state(record.ledger) if record is not None else raw
    if record is None and "acceptance" not in state:
        omissions.append({"item": "task/contract.json#judged", "reason": "the task was created before criteria were tracked"})
    judged = None
    if record is not None:
        current, saved = criteria.judged_now(record, raw, developing=state.get("status") == "running" and state.get("phase") == "develop")
        judged = {"code": current.code.get("status"), "outcome": str(current.outcome), "summary": current.summary(),
                  "criteria": [{"id": item.id, "text": item.text, "required": item.required, "status": str(item.status),
                                "saved_status": saved[item.id], "basis": current.basis.get(item.id), "reason": item.reason,
                                "evidence": list(item.evidence), "categories": list(item.categories),
                                "verifier": item.verifier} for item in current.criteria]}
    description = (_bytes(task_path / "task.md") or b"").decode("utf-8", errors="replace")
    contract = _masked({"task": {"id": task_id, **{key: state.get(key) for key in (
        "title", "status", "phase", "iteration", "developer", "reviewer", "created_at", "updated_at", "base_sha")}},
        "description": description[:DESCRIPTION_CHARS], "description_truncated": len(description) > DESCRIPTION_CHARS,
        "acceptance": state.get("acceptance"), "judged": judged}, secrets)
    files: dict[str, bytes] = {"task/contract.json": canonical(contract)}
    roles = {"task/contract.json": "contract"}

    saved_gate = state.get("gate") if isinstance(state.get("gate"), dict) else None
    head = {"count": ledger.count, "head": ledger.head}
    gate_entry, gate_file = None, None
    if saved_gate is None:
        omissions.append({"item": "gate/evaluation.json", "reason": "the task has no saved gate evaluation"})
    else:
        seen = saved_gate.get("ledger") if isinstance(saved_gate.get("ledger"), dict) else {}
        about = {"count": seen.get("count"), "head": seen.get("head")} == {"count": raw.count, "head": raw.head}
        gate_file = {"saved": saved_gate, "about_exported_ledger": about, "exported_ledger": {"count": raw.count, "head": raw.head}}
        files["gate/evaluation.json"] = canonical(gate_file)
        roles["gate/evaluation.json"] = "gate"
        gate_entry = {"id": saved_gate.get("id"), "decision": saved_gate.get("decision"),
                      # Only a PASS allows completion (`gate.Gate.completion_allowed`), whatever else a saved state says.
                      "completion_allowed": saved_gate.get("completion_allowed") is True and saved_gate.get("decision") == "PASS",
                      "next_action": saved_gate.get("next_action"), "at": saved_gate.get("at"), "about_exported_ledger": about}
        if not about:
            omissions.append({"item": "gate/evaluation.json", "reason": "the saved evaluation is about another state of the "
                              "ledger: no evaluation of the exported evidence was saved"})

    validity = validity_rows(ledger)
    files["evidence/ledger.jsonl"] = whole
    files["evidence/validity.json"] = canonical({"anchored": ledger is raw, "records": validity})
    roles.update({"evidence/ledger.jsonl": "ledger", "evidence/validity.json": "validity"})
    review = state.get("review") if isinstance(state.get("review"), dict) else None
    files["reviews/references.json"] = canonical(_masked({
        "review": {key: review.get(key) for key in ("verdict", "reported_verdict", "normalized", "refused_changes", "references")}
        | {"issues": len(review.get("issues") or [])} if review else None,
        "records": [{"id": item["id"], "verdict": item.get("verdict"), "fingerprint": item.get("fingerprint"),
                     "references": item.get("references"), "findings": item.get("findings")}
                    for item in raw.records if item.get("kind") == "review"],
        "findings": state.get("findings")}, secrets))
    tests = state.get("tests") if isinstance(state.get("tests"), list) else []
    files["verification/checks.json"] = canonical(_masked({
        "results": [{key: value for key, value in item.items() if key not in {"output_tail", "log_file"}}
                    for item in tests if isinstance(item, dict)],
        "test_context": state.get("test_context")}, secrets))
    history = state.get("history") if isinstance(state.get("history"), list) else []
    files["history/events.json"] = canonical(_masked(history, secrets))
    roles.update({"reviews/references.json": "reviews", "verification/checks.json": "checks", "history/events.json": "history"})

    sources = _sources(raw)
    detail: dict[str, dict] = {}
    excluded = []
    if mode == "summary":
        if sources:
            excluded.append({"item": "logs and report copies", "count": len(sources), "reason": "summary bundle"})
    else:
        total = 0
        for folder, name, owner, said in sources:
            if time.monotonic() > deadline:
                raise BundleError(f"The export took longer than {EXPORT_SECONDS} seconds and was stopped")
            path = f"{folder}/{name}"
            if len(detail) >= MAX_LOGS or total >= DETAIL_BYTES:
                omissions.append({"item": path, "reason": "limit_reached"})
                continue
            data, info = _part(task_path, folder, name, LOG_BYTES if folder == "logs" else REPORT_BYTES)
            if data is None:
                omissions.append({"item": path, "reason": info["reason"]})
                continue
            copy = _sanitized(data, info["truncated"], info["size"], secrets)
            total += len(copy)
            files[path] = copy
            roles[path] = "log" if folder == "logs" else "report"
            detail[path] = {"record": owner, "sanitized": True, "truncated": info["truncated"], "source_size": info["size"],
                            "matches_record": None if info["sha256"] is None or not said.get("sha256")
                            else info["sha256"] == said["sha256"]}
            if info["truncated"]:
                omissions.append({"item": path, "reason": f"only the last {LOG_BYTES if folder == 'logs' else REPORT_BYTES} bytes are kept"})

    manifest = {
        "format": FORMAT, "schema_version": VERSION, "created_at": clock(),
        "created_by": {"name": "patchrondo", "version": __version__}, "mode": mode, "task_id": task_id,
        "snapshot": saved_gate.get("snapshot") if saved_gate else None, "gate": gate_entry,
        "ledger": {**head, "problems": len(ledger.problems), "anchored": ledger is raw,
                   "incomplete_tail": whole != ledger_bytes},
        "validity": counts(validity), "omissions": omissions, "excluded_by_mode": excluded, "limits": LIMITS,
        "signature": None, "integrity": INTEGRITY,
    }
    manifest["complete"] = not omissions
    files["summary.md"] = _summary(manifest, contract, gate_file)
    roles["summary.md"] = "summary"
    manifest["files"] = [{"path": path, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "role": roles[path],
                          **({"source": detail[path]} if path in detail else {})} for path, data in sorted(files.items())]
    manifest["content_sha256"] = content_digest(manifest)
    manifest["bundle_id"] = f"EB-{manifest['content_sha256'][:16]}"
    files[MANIFEST] = canonical(manifest)
    return Bundle(f"patchrondo-evidence-{task_id}-{mode}.zip", _zip(files), manifest)


def _zip(files: dict[str, bytes]) -> bytes:
    """A ZIP whose bytes depend on the files alone: fixed times, permissions and system, the manifest first, then by name."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in [MANIFEST, *sorted(name for name in files if name != MANIFEST)]:
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, files[name], compresslevel=9)
    return buffer.getvalue()


# --- checking a bundle -----------------------------------------------------------------------

class Verdict(StrEnum):
    INTACT = "INTACT"                # every file matches the manifest and the records agree with each other
    INCOMPLETE = "INCOMPLETE"        # what is there matches, and something is missing or was left out at the export
    MODIFIED = "MODIFIED"            # a file differs from the manifest, one was added, or the records contradict each other
    MALFORMED = "MALFORMED"          # not a ZIP, an unsafe or duplicate name, or a manifest that cannot be read
    UNSUPPORTED = "UNSUPPORTED"      # another format, or a schema version this one does not know
    UNVERIFIABLE = "UNVERIFIABLE"    # the file cannot be read, or it is larger than the limits allow


EXIT_CODES = {Verdict.INTACT: 0, Verdict.INCOMPLETE: 1, Verdict.MODIFIED: 2, Verdict.MALFORMED: 3, Verdict.UNSUPPORTED: 4,
              Verdict.UNVERIFIABLE: 5}
_RANK = [Verdict.UNVERIFIABLE, Verdict.MALFORMED, Verdict.UNSUPPORTED, Verdict.MODIFIED, Verdict.INCOMPLETE]
AUTHENTICITY = ("Not established: the bundle is not signed. Matching hashes show that its files agree with its manifest, "
                "not who produced them.")


def safe_name(name: str) -> bool:
    """A name a bundle may hold: relative, forward slashes, no `..`, drive, colon or backslash, a few known characters."""
    return isinstance(name, str) and len(name) <= 255 and SAFE_NAME.fullmatch(name) is not None \
        and all(part not in {".", ".."} for part in PurePosixPath(name).parts)


def verify(source) -> dict:
    """Check a bundle (a path, or its bytes) without extracting anything. Returns the verdict, its exit code and why."""
    problems: list[dict] = []

    def problem(verdict: Verdict, code: str, detail: str, path: str | None = None) -> None:
        problems.append({"verdict": str(verdict), "code": code, "detail": detail, **({"path": path} if path else {})})

    def result(manifest: dict | None = None, checked: int = 0) -> dict:
        verdict = next((candidate for candidate in _RANK if any(entry["verdict"] == candidate for entry in problems)), Verdict.INTACT)
        shown = {key: manifest.get(key) for key in ("bundle_id", "task_id", "mode", "schema_version", "created_at", "gate",
                                                    "complete", "omissions", "snapshot")} if isinstance(manifest, dict) else None
        return {"status": str(verdict), "exit_code": EXIT_CODES[verdict], "problems": problems, "manifest": shown,
                "files_checked": checked, "authenticity": "NOT_ESTABLISHED", "note": AUTHENTICITY}

    try:
        if isinstance(source, (bytes, bytearray)):
            data = bytes(source)
            if len(data) > MAX_ARCHIVE_BYTES:
                raise OverflowError
        else:
            path = Path(source)
            if path.stat().st_size > MAX_ARCHIVE_BYTES:
                raise OverflowError
            data = path.read_bytes()
    except OverflowError:
        problem(Verdict.UNVERIFIABLE, "ARCHIVE_TOO_LARGE", f"The file is larger than {MAX_ARCHIVE_BYTES} bytes")
        return result()
    except OSError as exc:
        problem(Verdict.UNVERIFIABLE, "UNREADABLE", f"The file cannot be read ({type(exc).__name__})")
        return result()
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        entries = archive.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, EOFError):
        problem(Verdict.MALFORMED, "NOT_A_ZIP", "The file is not a readable ZIP archive")
        return result()
    if len(entries) > MAX_ENTRIES:
        problem(Verdict.UNVERIFIABLE, "TOO_MANY_ENTRIES", f"The archive holds {len(entries)} entries; at most {MAX_ENTRIES} are read")
        return result()
    names, folded, declared = {}, set(), 0
    for info in entries:
        name = info.filename
        kind = (info.external_attr >> 16) & 0o170000
        # The name as the archive holds it: on Windows `zipfile` turns a backslash into a slash before anybody sees it.
        if not safe_name(info.orig_filename) or info.orig_filename != name or name.endswith("/") or info.is_dir():
            problem(Verdict.MALFORMED, "UNSAFE_NAME", "An entry has a name that is not a plain relative path", name[:120])
            continue
        if kind == stat.S_IFLNK:
            problem(Verdict.MALFORMED, "LINK_ENTRY", "An entry is a symbolic link", name)
            continue
        if info.flag_bits & 0x1:
            problem(Verdict.MALFORMED, "ENCRYPTED_ENTRY", "An entry is encrypted", name)
            continue
        if name in names or name.casefold() in folded:
            problem(Verdict.MALFORMED, "DUPLICATE_NAME", "Two entries have the same name", name)
            continue
        names[name] = info
        folded.add(name.casefold())
        declared += info.file_size
        if info.file_size > MAX_ENTRY_BYTES:
            problem(Verdict.UNVERIFIABLE, "ENTRY_TOO_LARGE", f"An entry is larger than {MAX_ENTRY_BYTES} bytes", name)
        elif info.file_size > RATIO_FLOOR and info.file_size > MAX_RATIO * max(info.compress_size, 1):
            problem(Verdict.UNVERIFIABLE, "COMPRESSION_RATIO", "An entry expands far beyond its compressed size", name)
    if declared > MAX_TOTAL_BYTES:
        problem(Verdict.UNVERIFIABLE, "ARCHIVE_EXPANDS_TOO_MUCH", f"The entries add up to more than {MAX_TOTAL_BYTES} bytes")
    if any(entry["verdict"] in {Verdict.MALFORMED, Verdict.UNVERIFIABLE} for entry in problems):
        return result()

    def read(name: str) -> bytes | None:
        info = names[name]
        try:
            with archive.open(info) as stream:
                content = stream.read(MAX_ENTRY_BYTES + 1)
        except (zipfile.BadZipFile, OSError, ValueError, EOFError, RuntimeError, NotImplementedError):
            problem(Verdict.MODIFIED, "ENTRY_UNREADABLE", "An entry cannot be read: its data does not match its checksum", name)
            return None
        if len(content) > MAX_ENTRY_BYTES:
            problem(Verdict.UNVERIFIABLE, "ENTRY_TOO_LARGE", f"An entry is larger than {MAX_ENTRY_BYTES} bytes", name)
            return None
        return content

    if MANIFEST not in names:
        problem(Verdict.MALFORMED, "NO_MANIFEST", "The archive has no manifest.json")
        return result()
    raw_manifest = read(MANIFEST)
    try:
        manifest = json.loads(raw_manifest.decode("utf-8")) if raw_manifest is not None else None
    except (UnicodeDecodeError, ValueError):
        manifest = None
    if not isinstance(manifest, dict):
        if raw_manifest is not None:
            problem(Verdict.MALFORMED, "MANIFEST_UNREADABLE", "manifest.json is not a JSON object")
        return result()
    if manifest.get("format") != FORMAT:
        problem(Verdict.UNSUPPORTED, "UNKNOWN_FORMAT", "manifest.json does not describe a PatchRondo evidence bundle")
        return result(manifest)
    version = manifest.get("schema_version")
    if type(version) is not int or version != VERSION:
        problem(Verdict.UNSUPPORTED, "UNKNOWN_SCHEMA", f"Schema version {version!r} is not one this version of PatchRondo checks "
                f"(it checks {VERSION}); nothing in it is taken as verified")
        return result(manifest)
    listed = manifest.get("files")
    if not isinstance(listed, list) or not all(isinstance(entry, dict) and isinstance(entry.get("path"), str)
                                               and isinstance(entry.get("sha256"), str) and type(entry.get("size")) is int
                                               for entry in listed):
        problem(Verdict.MALFORMED, "MANIFEST_FILES", "manifest.json does not list its files with a path, a SHA-256 and a size")
        return result(manifest)
    paths = [entry["path"] for entry in listed]
    if len(set(paths)) != len(paths) or MANIFEST in paths or not all(safe_name(name) for name in paths):
        problem(Verdict.MALFORMED, "MANIFEST_FILES", "manifest.json lists a name twice, itself, or a name that is not safe")
        return result(manifest)

    checked, content = 0, {}
    for entry in listed:
        name = entry["path"]
        if name not in names:
            problem(Verdict.INCOMPLETE, "FILE_MISSING", "A file the manifest lists is not in the archive", name)
            continue
        found = read(name)
        if found is None:
            continue
        checked += 1
        if len(found) != entry["size"] or hashlib.sha256(found).hexdigest() != entry["sha256"]:
            problem(Verdict.MODIFIED, "HASH_MISMATCH", "A file differs from what the manifest says of it", name)
            continue
        content[name] = found
    for name in sorted(set(names) - set(paths) - {MANIFEST}):
        problem(Verdict.MODIFIED, "UNEXPECTED_FILE", "The archive holds a file the manifest does not list", name)
    if manifest.get("content_sha256") != content_digest(manifest) or manifest.get("bundle_id") != f"EB-{str(manifest.get('content_sha256'))[:16]}":
        problem(Verdict.MODIFIED, "MANIFEST_DIGEST", "The manifest's content digest or bundle ID does not match the manifest")
    if manifest.get("complete") is not True or manifest.get("omissions"):
        problem(Verdict.INCOMPLETE, "DECLARED_INCOMPLETE", "The export left something out; the manifest says what")
    _agree(manifest, content, problem)
    return result(manifest, checked)


def _json(content: dict, name: str):
    try:
        return json.loads(content[name].decode("utf-8")) if name in content else None
    except (UnicodeDecodeError, ValueError):
        return ValueError


def _agree(manifest: dict, content: dict, problem) -> None:
    """Whether the records of a bundle agree with each other and with the manifest, as far as they are there."""
    contract, gate_file, validity = (_json(content, name) for name in ("task/contract.json", "gate/evaluation.json",
                                                                         "evidence/validity.json"))
    for name, value in (("task/contract.json", contract), ("gate/evaluation.json", gate_file), ("evidence/validity.json", validity)):
        if value is ValueError:
            problem(Verdict.MODIFIED, "RECORD_UNREADABLE", "A file of the bundle is not valid JSON", name)
    if isinstance(contract, dict) and (contract.get("task") or {}).get("id") != manifest.get("task_id"):
        problem(Verdict.MODIFIED, "TASK_MISMATCH", "The contract is about another task than the manifest", "task/contract.json")
    ledger = None
    if "evidence/ledger.jsonl" in content:
        ledger = evidence.parse(content["evidence/ledger.jsonl"])
        said = manifest.get("ledger") if isinstance(manifest.get("ledger"), dict) else {}
        saved = (contract.get("acceptance") if isinstance(contract, dict) else None)
        anchored = ledger
        try:
            record = criteria.Acceptance.load({"acceptance": saved}) if isinstance(saved, dict) else None
        except ValueError:
            record = None
        if record is not None:
            anchored = ledger.for_state(record.ledger)
        if (said.get("count"), said.get("head"), said.get("problems")) != (anchored.count, anchored.head, len(anchored.problems)):
            problem(Verdict.MODIFIED, "LEDGER_MISMATCH", "The ledger's records, hash chain or problems are not those the manifest names",
                    "evidence/ledger.jsonl")
        if isinstance(validity, dict) and isinstance(validity.get("records"), list):
            if validity["records"] != validity_rows(anchored):
                problem(Verdict.MODIFIED, "VALIDITY_MISMATCH", "The validity of the records does not follow from the ledger",
                        "evidence/validity.json")
            elif manifest.get("validity") != counts(validity["records"]):
                problem(Verdict.MODIFIED, "VALIDITY_MISMATCH", "The manifest counts records by validity otherwise than the bundle",
                        "evidence/validity.json")
        for entry in manifest.get("files") or []:
            source = entry.get("source") if isinstance(entry, dict) else None
            if isinstance(source, dict) and entry["path"] in content:
                owner = ledger.get(source.get("record")) if isinstance(source.get("record"), str) else None
                name = entry["path"].split("/", 1)[1] if "/" in entry["path"] else ""
                report = ((owner or {}).get("check") or {}).get("report") if isinstance((owner or {}).get("check"), dict) else None
                if owner is None or name not in {owner.get("log"), (report or {}).get("path") if isinstance(report, dict) else None}:
                    problem(Verdict.MODIFIED, "SOURCE_MISMATCH", "A log or report names a record that does not name it", entry["path"])
    shown = manifest.get("gate")
    if isinstance(gate_file, dict):
        saved_gate = gate_file.get("saved") if isinstance(gate_file.get("saved"), dict) else {}
        if gate_id(saved_gate) != saved_gate.get("id"):
            problem(Verdict.MODIFIED, "GATE_ID_MISMATCH", "The saved gate evaluation does not carry the ID of what it says it decided",
                    "gate/evaluation.json")
        allowed = saved_gate.get("completion_allowed") is True and saved_gate.get("decision") == "PASS"
        if not isinstance(shown, dict) or (shown.get("id"), shown.get("decision"), shown.get("about_exported_ledger"),
                                           shown.get("completion_allowed")) != (
                saved_gate.get("id"), saved_gate.get("decision"), gate_file.get("about_exported_ledger"), allowed):
            problem(Verdict.MODIFIED, "GATE_MISMATCH", "The manifest names another gate decision, or another permission to "
                    "complete, than the bundle holds", "gate/evaluation.json")
        seen = saved_gate.get("ledger") if isinstance(saved_gate.get("ledger"), dict) else {}
        if ledger is not None and gate_file.get("about_exported_ledger") != (
                {"count": seen.get("count"), "head": seen.get("head")} == {"count": ledger.count, "head": ledger.head}):
            problem(Verdict.MODIFIED, "GATE_MISMATCH", "The bundle says the gate is about the exported ledger otherwise than it is",
                    "gate/evaluation.json")
    elif shown is not None and "gate/evaluation.json" not in {entry.get("path") for entry in manifest.get("files") or [] if isinstance(entry, dict)}:
        problem(Verdict.MODIFIED, "GATE_MISMATCH", "The manifest names a gate decision the bundle does not hold")
