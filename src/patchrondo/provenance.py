"""Provenance and invalidation: whether a record of the evidence ledger can still be used, and if not, why.

A record says what was observed when it was written; it is never rewritten.
Whether it can still prove anything is judged again each time, from the
record and from what is current, and kept apart from its result: a check
that `passed` stays passed when its evidence becomes stale.

    VALID    read intact, of a shape this version writes, and about the task as it is now
    STALE    true when it was written, and about something that changed since: the files, the
             configuration of its check, the inputs it declared, the environment it needed, an
             artifact it names
    INVALID  the record or what it rests on cannot be trusted: the hash chain does not hold it, its
             shape is not one this version writes, the copy of its report that PatchRondo kept changed
    UNKNOWN  what is needed to judge it is missing: the state of the files it was made on, which
             configuration of its check ran, the digest of the inputs it declared

Only VALID evidence counts. When the files cannot be looked at, nothing
about them is judged here, and the caller decides nothing on them either:
`gitops.fingerprint` raises instead of giving a digest.

What can be judged from the record and what is current is put on record:
`invalidate` appends one `invalidation` record per cause, naming the
records, the cause, the state of the files before and now, and the checks
that give that evidence again (`reverify`). A record already invalidated no
longer counts and is never named again, so a Resume writes nothing twice,
and an invalidation lost with a crash is simply written by the next look.

Decisions of the user (`authorization`) depend on no files. A check by the
user depends on the files unless it was recorded as `independent`, and on
the files it names.

What this establishes, and what it does not: integrity (the hash chain:
an edit that leaves the chain broken is seen), the shape of a record,
observed provenance (only the runtime writes a `command` record, and the
record names the run, the check, its configuration, the files and the
environment), applicability to the files as they are now. Not authenticity:
nothing is signed, and somebody with the user's rights can rewrite the
whole chain. Not the correctness of the code.

No model is asked anything here. `PATCHRONDO_PROVENANCE=0` keeps the
invalidation of the versions before it (`evidence.outdated`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import os
from pathlib import Path

from . import evidence, verification

SWITCH = "PATCHRONDO_PROVENANCE"
DETECTOR = "provenance"         # what an invalidation record says found the change
JUDGED = ("command", "review", "manual", "authorization")
SHOWN = 12                      # records listed in a brief
PREVIOUS = 20                   # states of the files named by one invalidation


def enabled() -> bool:
    return os.environ.get(SWITCH, "1") != "0"


class Validity(StrEnum):
    VALID = "VALID"
    STALE = "STALE"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class Cause(StrEnum):
    RECORD_UNTRUSTED = "RECORD_UNTRUSTED"          # the hash chain does not hold it, or the ledger lost what the state saw
    RECORD_UNKNOWN_SHAPE = "RECORD_UNKNOWN_SHAPE"  # a version, kind, executor or result this version does not write
    REPORT_CHANGED = "REPORT_CHANGED"              # the copy of its report that PatchRondo kept changed or is gone
    WORKSPACE_CHANGED = "WORKSPACE_CHANGED"
    CHECK_CHANGED = "CHECK_CHANGED"
    CHECK_REMOVED = "CHECK_REMOVED"
    INPUTS_CHANGED = "INPUTS_CHANGED"
    ENVIRONMENT_CHANGED = "ENVIRONMENT_CHANGED"
    ARTIFACT_CHANGED = "ARTIFACT_CHANGED"
    ARTIFACT_MISSING = "ARTIFACT_MISSING"
    HANDED_OVER = "HANDED_OVER"                    # a failed check by the user, handed to the developer once
    SNAPSHOT_MISSING = "SNAPSHOT_MISSING"          # it names no state of the files it was made on
    CHECK_UNIDENTIFIED = "CHECK_UNIDENTIFIED"      # it does not say which configuration of its check ran
    INPUTS_UNKNOWN = "INPUTS_UNKNOWN"              # the inputs its check declares cannot be compared
    INVALIDATED = "INVALIDATED"                    # named by an invalidation written before causes were recorded


VALIDITY = {Cause.RECORD_UNTRUSTED: Validity.INVALID, Cause.RECORD_UNKNOWN_SHAPE: Validity.INVALID,
            Cause.REPORT_CHANGED: Validity.INVALID, Cause.SNAPSHOT_MISSING: Validity.UNKNOWN,
            Cause.CHECK_UNIDENTIFIED: Validity.UNKNOWN, Cause.INPUTS_UNKNOWN: Validity.UNKNOWN}
# What an invalidation record says, for people. The first two are the words `evidence.outdated` wrote.
REASONS = {
    Cause.WORKSPACE_CHANGED: "The files of the task changed after this was recorded",
    Cause.ARTIFACT_CHANGED: "A file this evidence refers to changed or is no longer there",
    Cause.ARTIFACT_MISSING: "A file this evidence refers to is no longer there",
    Cause.REPORT_CHANGED: "The copy of the report that PatchRondo kept for this check changed or is no longer there",
    Cause.CHECK_CHANGED: "The configuration of the check changed after it ran",
    Cause.CHECK_REMOVED: "The check is no longer configured for the project",
    Cause.INPUTS_CHANGED: "A file the check declares as its input changed after it ran",
    Cause.ENVIRONMENT_CHANGED: "The check counts only where it ran, and it ran on another computer or interpreter",
    Cause.SNAPSHOT_MISSING: "The record does not say which state of the task's files it was made on",
    Cause.CHECK_UNIDENTIFIED: "The record does not say which configuration of its check ran",
    Cause.INPUTS_UNKNOWN: "The inputs the check declares cannot be compared with those it ran on",
    Cause.RECORD_UNTRUSTED: "The evidence ledger does not hold this record intact",
    Cause.RECORD_UNKNOWN_SHAPE: "The record is not one this version of PatchRondo writes",
    Cause.HANDED_OVER: "Handed to the developer as a request for changes",
    Cause.INVALIDATED: "Named by an earlier invalidation",
}
_FROM_REASON = {text: cause for cause, text in REASONS.items()}


def validity_of(cause: str) -> Validity:
    return VALIDITY.get(cause, Validity.STALE)


# --- what is current -----------------------------------------------------------------------

@dataclass(frozen=True)
class Current:
    """What evidence is compared with. A part left None is not looked at, and nothing about it is judged."""
    fingerprint: str | None = None                  # `gitops.fingerprint` of the task's files now
    checks: dict[str, str] | None = None            # each configured check: the digest of its configuration
    legacy: tuple[tuple[str, ...], ...] = ()        # tests.commands as a record keeps them, in order
    inputs: dict[str, str | None] = field(default_factory=dict)  # checks with inputs: their digest now, None when unknown
    environment: str | None = None                  # `verification.environment()["id"]`
    same_environment: frozenset = frozenset()       # checks whose result counts only where it ran


def kept(command) -> tuple[str, ...]:
    """A command as a record keeps it (bounded and redacted), to compare one with the other."""
    return tuple(evidence.text(part, 300) for part in command)[:60]


def current(cfg: dict, workspace: Path | None, fingerprint: str | None) -> Current:
    """What is current for a task: its files' fingerprint, its configured checks, and the inputs they declare.

    Reads the declared inputs from `workspace` (`verification.inputs_digest`);
    nothing is run. An input that cannot be hashed now is unknown, never
    taken as unchanged.
    """
    checks = verification.checks(cfg)
    inputs = {}
    for check in checks:
        if check.inputs:
            found = verification.inputs_digest(check, workspace)[0] if workspace is not None else None
            inputs[check.id] = found["sha256"] if found else None
    return Current(fingerprint=fingerprint, checks={check.id: check.digest() for check in checks},
                   legacy=tuple(kept(check.command) for check in checks if check.legacy), inputs=inputs,
                   environment=verification.environment()["id"],
                   same_environment=frozenset(check.id for check in checks if check.same_environment))


# --- judging one record ---------------------------------------------------------------------

@dataclass(frozen=True)
class Assessment:
    evidence: str
    kind: str
    result: str | None            # what the record says was observed, unchanged
    validity: str
    causes: tuple[str, ...] = ()
    check: str | None = None      # for a command record: the check that gives this evidence again
    invalidated_by: str | None = None

    @property
    def usable(self) -> bool:
        return self.validity == Validity.VALID

    def dump(self) -> dict:
        return {"evidence": self.evidence, "kind": self.kind, "result": self.result, "validity": str(self.validity),
                "causes": [str(cause) for cause in self.causes], "check": self.check, "invalidated_by": self.invalidated_by,
                "reverification_required": self.check is not None and not self.usable}


def _check_of(record: dict) -> dict | None:
    return record.get("check") if isinstance(record.get("check"), dict) else None


def _position(ledger: evidence.Ledger, record: dict) -> int | None:
    """The place of a command record in its run, as the test stage before checks wrote them: 1 for the first."""
    run = record.get("run")
    if not isinstance(run, str) or not run:
        return None
    return sum(1 for other in ledger.records if other.get("kind") == "command" and other.get("run") == run
               and isinstance(other.get("seq"), int) and other["seq"] <= record["seq"])


def check_name(ledger: evidence.Ledger, record: dict) -> str | None:
    """The configured check a command record is about: its `check.id`, or `command-<n>` for one written before checks."""
    if record.get("kind") != "command":
        return None
    check = _check_of(record)
    if check is not None:
        return check["id"] if isinstance(check.get("id"), str) and check["id"] else None
    position = _position(ledger, record)
    return f"command-{position}" if position else None


def recorded(ledger: evidence.Ledger, record: dict) -> Assessment:
    """What the ledger alone says of a record: whether it is intact and of a known shape, and whether it was invalidated."""
    identifier, kind = str(record.get("id")), str(record.get("kind"))
    check = check_name(ledger, record) if ledger.sound(record) else None
    if not record.get("trusted"):
        return Assessment(identifier, kind, record.get("result"), Validity.INVALID, (Cause.RECORD_UNTRUSTED,), check)
    if not ledger.sound(record):
        return Assessment(identifier, kind, record.get("result"), Validity.INVALID, (Cause.RECORD_UNKNOWN_SHAPE,), check)
    if record["invalidated"] is not None:
        by = ledger.get(record["invalidated"]["by"]) or {}
        cause = by.get("cause") if by.get("cause") in Cause.__members__ else _FROM_REASON.get(by.get("reason"), Cause.INVALIDATED)
        return Assessment(identifier, kind, record.get("result"), validity_of(cause), (Cause(cause),), check,
                          record["invalidated"]["by"])
    return Assessment(identifier, kind, record.get("result"), Validity.VALID, (), check)


def _configuration(ledger: evidence.Ledger, record: dict, now: Current) -> list[Cause]:
    """Whether a command record is about the check as it is configured now: its configuration, inputs and environment."""
    check, name = _check_of(record), check_name(ledger, record)
    if name is None:
        return [Cause.CHECK_UNIDENTIFIED]
    if name not in now.checks:
        return [Cause.CHECK_REMOVED]
    digest = check.get("config_sha256") if check is not None else None
    if isinstance(digest, str):
        if digest != now.checks[name]:
            return [Cause.CHECK_CHANGED]
    elif verification.LEGACY_ID.fullmatch(name):
        # Written before records named their configuration: an entry of tests.commands is its command alone.
        number = int(name.split("-")[1])
        if number > len(now.legacy) or now.legacy[number - 1] != tuple(record.get("command") or ()):
            return [Cause.CHECK_CHANGED]
    else:
        return [Cause.CHECK_UNIDENTIFIED]
    causes = []
    if name in now.inputs:
        before = (check or {}).get("inputs") if isinstance((check or {}).get("inputs"), dict) else {}
        if now.inputs[name] is None or not isinstance(before.get("sha256"), str):
            causes.append(Cause.INPUTS_UNKNOWN)
        elif before["sha256"] != now.inputs[name]:
            causes.append(Cause.INPUTS_CHANGED)
    if name in now.same_environment:
        environment = (check or {}).get("environment") if isinstance((check or {}).get("environment"), dict) else {}
        if environment.get("id") != now.environment:
            causes.append(Cause.ENVIRONMENT_CHANGED)
    return causes


def _report_copy(record: dict, artifact: dict) -> bool:
    report = (_check_of(record) or {}).get("report")
    return isinstance(report, dict) and report.get("sha256") == artifact.get("sha256")


def assess(ledger: evidence.Ledger, record: dict, now: Current | None = None, *, artifacts=evidence.artifact_state) -> Assessment:
    """Whether a record can be used now. Pure but for `artifacts`, which looks at the files a record names."""
    found = recorded(ledger, record)
    if not found.usable or record.get("kind") not in JUDGED or record["kind"] == "authorization":
        return found  # a decision of the user depends on no files
    now = now or Current()
    causes: list[Cause] = []
    if now.fingerprint is not None and record.get("scope", "code") == "code":
        fingerprint = record.get("fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            causes.append(Cause.SNAPSHOT_MISSING)
        elif fingerprint != now.fingerprint:
            causes.append(Cause.WORKSPACE_CHANGED)
    if record["kind"] == "command" and now.checks is not None:
        causes += _configuration(ledger, record, now)
    for artifact in record.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        state = artifacts(artifact)
        if state != evidence.INTACT:
            causes.append(Cause.REPORT_CHANGED if _report_copy(record, artifact)
                          else Cause.ARTIFACT_MISSING if state == evidence.MISSING else Cause.ARTIFACT_CHANGED)
    if not causes:
        return found
    causes = sorted(dict.fromkeys(causes), key=list(Cause).index)
    worst = min((validity_of(cause) for cause in causes), key=[Validity.INVALID, Validity.STALE, Validity.UNKNOWN].index)
    return Assessment(found.evidence, found.kind, found.result, worst, tuple(causes), found.check)


def assess_all(ledger: evidence.Ledger, now: Current | None = None, *, artifacts=evidence.artifact_state) -> list[Assessment]:
    """Every record that is evidence (not an invalidation or a restart), in the order of the ledger."""
    return [assess(ledger, record, now, artifacts=artifacts) for record in ledger.records if record.get("kind") in JUDGED]


# --- putting invalidations on record ------------------------------------------------------

def stale(ledger: evidence.Ledger, now: Current, *, artifacts=evidence.artifact_state) -> dict[Cause, list[Assessment]]:
    """Records that count in the ledger and can no longer be used, by their first cause. Nothing is written."""
    found: dict[Cause, list[Assessment]] = {}
    for record in ledger.records:
        if record.get("kind") in JUDGED and ledger.counts(record):
            result = assess(ledger, record, now, artifacts=artifacts)
            if not result.usable:
                found.setdefault(Cause(result.causes[0]), []).append(result)
    return found


def invalidate(task_path: Path, ledger: evidence.Ledger, now: Current, *, iteration: int,
               artifacts=evidence.artifact_state) -> list[dict]:
    """Append one invalidation for each cause that makes counting records unusable. Under the task lock.

    Returns the records written. A record that no longer counts is never
    named again, so a second look writes nothing.
    """
    written = []
    for cause, results in stale(ledger, now, artifacts=artifacts).items():
        named = [result.evidence for result in results]
        snapshot = None
        if cause == Cause.WORKSPACE_CHANGED:
            before = sorted({value for name in named if isinstance(value := ledger.get(name).get("fingerprint"), str)})
            snapshot = {"previous": before[:PREVIOUS], "current": now.fingerprint}
        checks = sorted({result.check for result in results if result.check and now.checks and result.check in now.checks})
        written.append(evidence.append(task_path, "invalidation", executor="runtime", result="invalidated", of=named,
                                       reason=REASONS[cause], cause=str(cause), validity=str(validity_of(cause)),
                                       detector=DETECTOR, snapshot=snapshot, reverify=checks or None, iteration=iteration))
    return written


# --- what people and agents are told --------------------------------------------------------

def overview(ledger: evidence.Ledger) -> dict:
    """The evidence of a ledger by validity, from the ledger alone, and the checks whose evidence no longer counts.

    `reverify` names each check with no usable record left, and the records
    that no longer count for it: the runtime runs it again with the
    project's checks, under the consent, and nobody else does.
    """
    by = {str(validity): [] for validity in Validity}
    for record in ledger.records:
        if record.get("kind") in JUDGED:
            result = recorded(ledger, record)
            by[str(result.validity)].append(result)
    usable = {result.check for result in by[Validity.VALID] if result.check}
    reverify: dict[str, list[Assessment]] = {}
    for validity in (Validity.STALE, Validity.INVALID, Validity.UNKNOWN):
        for result in by[validity]:
            if result.check and result.check not in usable:
                reverify.setdefault(result.check, []).append(result)
    return {**by, "reverify": reverify}


def brief(ledger: evidence.Ledger) -> list[str]:
    """Lines for a prompt: the evidence that no longer counts and why, and the checks that need a new result. IDs only."""
    seen = overview(ledger)
    order = {str(record.get("id")): number for number, record in enumerate(ledger.records)}
    apart = sorted((result for validity in (Validity.STALE, Validity.INVALID, Validity.UNKNOWN) for result in seen[validity]),
                   key=lambda result: order.get(result.evidence, 0))
    lines = []
    if apart:
        lines += ["", "## Evidence that no longer counts (it proves nothing now)"]
        lines += [f"- {result.evidence} {result.kind}" + (f" of check {result.check}" if result.check else "")
                  + f": {result.result} when recorded; {result.validity} ({', '.join(result.causes)})" for result in apart[-SHOWN:]]
        if len(apart) > SHOWN:
            lines.append(f"- … and {len(apart) - SHOWN} earlier ones")
    if seen["reverify"]:
        lines += ["", "## Checks without a usable result (the orchestrator runs them again; nobody else may)"]
        lines += [f"- {name}: " + ", ".join(sorted({cause for result in results for cause in result.causes}))
                  for name, results in list(seen["reverify"].items())[:SHOWN]]
    return lines
