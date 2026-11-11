"""Progress signals (Phase 2.5, Step 11): what an observation window of a task established, read from its records.

A window runs from one observation of a task to a later one of the same
task and iteration: before and after a call of a step, or from the start of
an iteration to the end of its test stage or its review. This module says
what was established in between, and how reliably, from what PatchRondo
already keeps. It keeps no record of its own.

Two layers, as in `continuation.py`:

    observe  reads a task's saved state, its evidence ledger and the fingerprint of its files and
             returns an `Observation`: typed facts, no agent text, no session, no process and no
             path. It writes nothing, starts nothing and takes no lock.
    assess   compares two observations of one window and returns an `Assessment`. Pure: the same
             two observations give the same assessment, field for field.

Signals (`Signal`), which are not exclusive of each other:

    VERIFIED_PROGRESS     a demonstrable advancement on the files as they are now, from records of the
                          ledger that count and apply to them (`provenance`): a criterion verified that
                          was not, a check that failed and now passes, a check that passes for the first
                          time, a finding resolved by a record that verifies it
    OBSERVED_ACTIVITY     PatchRondo saw work: the files changed, an agent's call gave its answer, a review
                          was validated. Not proof that anything improved
    NO_VERIFIED_PROGRESS  nothing in the window established a verified advancement. Not stagnation and
                          not a failure by itself: verification may simply not have run yet
    REGRESSION            a deterioration that records show: a check that passed on the files at the
                          start failed on the files now, or a criterion verified at the start failed by
                          a record that counts. Evidence that went stale, or files that changed, are
                          never a regression
    UNKNOWN               the files could not be fingerprinted, a source on record cannot be read, the
                          ledger is not the one the start saw, records disagree, a check proved nothing
                          either way, or a call's effects are not known

What decides is fixed (`SIGNAL_OF`, `TRUST`): an agent's words are never
read, an answer received is `REPORTED`, and elapsed time, turns, tokens and
the size of an answer are not looked at. A verification applies only to the
snapshot it was made on: a record counts here only when the ledger counts it,
its validity is `VALID` against the files now, and it names the fingerprint
of those files. Without a fingerprint nothing is verified and nothing
regressed.

Signals decide nothing. Nothing here stops, retries, continues, approves or
completes a task, changes a budget, an attempt, a finding, a criterion or
the ledger, or is read by the gate (`gate.py`) or by `core._complete`. Step
12 may read them; until then nothing does.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import subprocess

from . import acceptance as criteria, evidence, execution, findings as reviews, gitops, provenance
from .continuation import Trust

VERSION = 1
SHORT = 16                  # characters of a fingerprint shown in a dump
NEGATIVE = {"command": "failed", "manual": "failed"}  # results that show a requirement not met (`evidence.POSITIVE`)
VERIFYING = ("command", "review", "manual")         # kinds of record a verification writes
_TAKE = object()            # `observe`: take the fingerprint of the files now


class Signal(StrEnum):
    VERIFIED_PROGRESS = "VERIFIED_PROGRESS"
    OBSERVED_ACTIVITY = "OBSERVED_ACTIVITY"
    NO_VERIFIED_PROGRESS = "NO_VERIFIED_PROGRESS"
    REGRESSION = "REGRESSION"
    UNKNOWN = "UNKNOWN"


# The one signal an assessment is classified as: the first, in this order, of those it has.
PRECEDENCE = (Signal.REGRESSION, Signal.VERIFIED_PROGRESS, Signal.UNKNOWN, Signal.OBSERVED_ACTIVITY,
              Signal.NO_VERIFIED_PROGRESS)
# Why a signal has its reliability: fixed words.
RELIABILITY = {
    Signal.VERIFIED_PROGRESS: "Records of the evidence ledger that count and apply to the files as they are now.",
    Signal.REGRESSION: "A record that counts and applies to the files now shows a failure of what a record that counted "
                       "on the files at the start showed met.",
    Signal.OBSERVED_ACTIVITY: "Measured by PatchRondo (fingerprints of the files, ended calls, validated reviews); it "
                              "proves no requirement met.",
    Signal.NO_VERIFIED_PROGRESS: "No record of the window established an advancement. This is not stagnation and not a "
                                 "failure by itself.",
    Signal.UNKNOWN: "Something needed to judge is missing, unreadable, contradictory or inconclusive.",
}


class Source(StrEnum):
    WORKSPACE = "workspace"          # `gitops.fingerprint` of the task's files
    LEDGER = "ledger"                # `evidence.jsonl`
    ACCEPTANCE = "acceptance"        # the criteria, judged again against the ledger
    FINDINGS = "findings"            # the findings of the task's reviews
    EXECUTION = "execution"          # the execution record of Adaptive Execution
    HISTORY = "history"              # the workflow history, for references only
    CONFIGURATION = "configuration"  # the project's checks, to compare a record's check with


class Presence(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"          # never on record for this task (a task from before it): nothing is missing
    UNREADABLE = "unreadable"  # on record, and it cannot be used
    PARTIAL = "partial"        # the ledger has problems: records from the first one on are not trusted


class Change(StrEnum):
    """One fact of a window. Its trust and the signal it gives are fixed (`TRUST`, `SIGNAL_OF`)."""
    # Advancement, by records that count on the files now.
    CRITERION_VERIFIED = "CRITERION_VERIFIED"        # not verified on the files at the start, verified on the files now
    CHECK_FIXED = "CHECK_FIXED"                      # failed on the files at the start, passed on the files now
    CHECK_ESTABLISHED = "CHECK_ESTABLISHED"          # no pass on record before; passed on the files now
    FINDING_RESOLVED = "FINDING_RESOLVED"            # resolved by a record that counts, positive, on the files now
    # Deterioration, by records that count.
    CHECK_REGRESSED = "CHECK_REGRESSED"              # passed on the files at the start, failed on the files now
    CRITERION_REGRESSED = "CRITERION_REGRESSED"      # verified at the start, failed by a record of the window
    # Observed.
    FILES_CHANGED = "FILES_CHANGED"
    FILES_UNCHANGED = "FILES_UNCHANGED"
    VERIFICATION_NOT_RUN = "VERIFICATION_NOT_RUN"    # no check, review or user's check was recorded in the window
    CHECK_REPEATED = "CHECK_REPEATED"                # passed at the start and again now: nothing new
    CHECK_REVERIFIED = "CHECK_REVERIFIED"            # an earlier pass had gone stale; passed again: kept, not advanced
    CHECK_FAILING = "CHECK_FAILING"                  # failed now, with no pass on the files at the start
    CHECK_NOT_CONCLUSIVE = "CHECK_NOT_CONCLUSIVE"    # error, blocked or timeout: proves nothing either way
    EVIDENCE_INVALIDATED = "EVIDENCE_INVALIDATED"    # applied at the start, not now: not a regression
    CRITERION_KEPT = "CRITERION_KEPT"                # verified at the start and now
    CRITERION_LAPSED = "CRITERION_LAPSED"            # verified at the start, not now, and nothing failed it
    CRITERION_UNMET = "CRITERION_UNMET"              # not verified at the start, nor now
    REVIEW_RECORDED = "REVIEW_RECORDED"              # a validated review; its verdict is a workflow decision
    REVIEW_UNUSABLE = "REVIEW_UNUSABLE"              # a reviewer's call whose answer could not be used: no review
    FINDING_NEW = "FINDING_NEW"
    FINDING_REPEATED = "FINDING_REPEATED"            # reported again by a review of the window
    FINDING_AWAITING_VERIFICATION = "FINDING_AWAITING_VERIFICATION"  # an approval left it out; nothing shows it gone
    CALL_ENDED = "CALL_ENDED"                        # a call of the window ended (its outcome in `detail`)
    ATTEMPTS_NOT_ON_RECORD = "ATTEMPTS_NOT_ON_RECORD"  # calls of the window whose records left with their step
    # Reported: an agent's statement, which proves nothing.
    ANSWER_REPORTED = "ANSWER_REPORTED"              # a developer's call gave its final answer (its handoff)
    FINDING_RESOLUTION_CLAIMED = "FINDING_RESOLUTION_CLAIMED"  # RESOLVED with no record that verifies it
    # Unknown.
    SNAPSHOT_UNKNOWN = "SNAPSHOT_UNKNOWN"            # the files could not be fingerprinted at the end
    SOURCE_UNREADABLE = "SOURCE_UNREADABLE"          # a source on record cannot be used, or only in part
    LEDGER_DISCONTINUOUS = "LEDGER_DISCONTINUOUS"    # the ledger at the end does not continue the one the start saw
    CHECK_CONTRADICTED = "CHECK_CONTRADICTED"        # records that count on the files now passed and failed it
    ATTEMPT_EFFECTS_UNKNOWN = "ATTEMPT_EFFECTS_UNKNOWN"  # a call that may still run, or was released or reconciled


_NOT_OBSERVED = {
    Change.CRITERION_VERIFIED: Trust.VERIFIED, Change.CHECK_FIXED: Trust.VERIFIED,
    Change.CHECK_ESTABLISHED: Trust.VERIFIED, Change.FINDING_RESOLVED: Trust.VERIFIED,
    Change.CHECK_REGRESSED: Trust.VERIFIED, Change.CRITERION_REGRESSED: Trust.VERIFIED,
    Change.ANSWER_REPORTED: Trust.REPORTED, Change.FINDING_RESOLUTION_CLAIMED: Trust.REPORTED,
    Change.SNAPSHOT_UNKNOWN: Trust.UNKNOWN, Change.SOURCE_UNREADABLE: Trust.UNKNOWN,
    Change.LEDGER_DISCONTINUOUS: Trust.UNKNOWN, Change.CHECK_CONTRADICTED: Trust.UNKNOWN,
    Change.ATTEMPT_EFFECTS_UNKNOWN: Trust.UNKNOWN, Change.ATTEMPTS_NOT_ON_RECORD: Trust.UNKNOWN,
}
# What a fact can be trusted as: VERIFIED, REPORTED or UNKNOWN as listed above, OBSERVED otherwise.
TRUST = {change: _NOT_OBSERVED.get(change, Trust.OBSERVED) for change in Change}

# The signal a fact gives, if any. A fact of trust VERIFIED is the only way to VERIFIED_PROGRESS and to REGRESSION.
SIGNAL_OF = {
    Change.CRITERION_VERIFIED: Signal.VERIFIED_PROGRESS, Change.CHECK_FIXED: Signal.VERIFIED_PROGRESS,
    Change.CHECK_ESTABLISHED: Signal.VERIFIED_PROGRESS, Change.FINDING_RESOLVED: Signal.VERIFIED_PROGRESS,
    Change.CHECK_REGRESSED: Signal.REGRESSION, Change.CRITERION_REGRESSED: Signal.REGRESSION,
    Change.FILES_CHANGED: Signal.OBSERVED_ACTIVITY, Change.REVIEW_RECORDED: Signal.OBSERVED_ACTIVITY,
    Change.ANSWER_REPORTED: Signal.OBSERVED_ACTIVITY,
    Change.SNAPSHOT_UNKNOWN: Signal.UNKNOWN, Change.SOURCE_UNREADABLE: Signal.UNKNOWN,
    Change.LEDGER_DISCONTINUOUS: Signal.UNKNOWN, Change.CHECK_CONTRADICTED: Signal.UNKNOWN,
    Change.CHECK_NOT_CONCLUSIVE: Signal.UNKNOWN, Change.ATTEMPT_EFFECTS_UNKNOWN: Signal.UNKNOWN,
}
BASIS = {Signal.VERIFIED_PROGRESS: Trust.VERIFIED, Signal.REGRESSION: Trust.VERIFIED,
         Signal.OBSERVED_ACTIVITY: Trust.OBSERVED, Signal.NO_VERIFIED_PROGRESS: Trust.OBSERVED,
         Signal.UNKNOWN: Trust.UNKNOWN}


class WindowMismatch(ValueError):
    """Two observations that do not make one window: another task, another iteration, or out of order."""


# --- what an observation holds ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceState:
    source: Source
    presence: Presence


@dataclass(frozen=True)
class Boundary:
    """Where an observation was taken: the task, its iteration and phase, and how far each record had gone."""
    task_id: str
    iteration: int
    phase: str
    fingerprint: str | None        # of the files when observed; None when it could not be taken
    attempts_made: int | None      # attempt IDs the task had given (`next_attempt - 1`); None without a record
    last_attempt: str | None       # the last attempt of the current step
    ledger_records: int            # records of the ledger read
    ledger_head: str               # the hash of its last record, "" when there is none or it is not trusted


@dataclass(frozen=True)
class RecordFact:
    """A record of the ledger as it stood when observed. Its validity was judged against the boundary's files."""
    id: str
    digest: str                    # the record's own hash, to tell that a later ledger continues this one
    kind: str
    result: str | None
    trusted: bool                  # the hash chain holds it (and the ledger still holds what the task state saw)
    counts: bool                   # `Ledger.counts`
    validity: str                  # `provenance.Validity` when observed
    causes: tuple[str, ...]
    check: str | None              # the configured check a command record is about
    snapshot: str | None           # the fingerprint of the files it was made on
    scoped: bool                   # whether it depends on the files (`scope` "code"); a decision of the user does not
    criteria: tuple[str, ...]


@dataclass(frozen=True)
class CriterionFact:
    id: str
    required: bool
    status: str                    # as judged against the ledger when observed (`acceptance.judged_now`)
    evidence: tuple[str, ...]      # the records it names


@dataclass(frozen=True)
class FindingFact:
    id: str
    status: str
    owner: str
    blocking: bool
    reported_in: tuple[str, ...]   # review records that reported it
    resting_on: tuple[str, ...]    # records its status names (`Finding.verification`)


@dataclass(frozen=True)
class AttemptFact:
    """An attempt of the current step. No session, no time, no process: none of it says anything about progress."""
    id: str
    number: int
    phase: str
    outcome: str | None
    failure_kind: str | None
    termination: str | None
    ended: bool
    uncertain: bool
    draft: bool
    workspace_before: str | None
    workspace_after: str | None


@dataclass(frozen=True)
class EventFact:
    id: str
    event: str


@dataclass(frozen=True)
class Observation:
    boundary: Boundary
    sources: tuple[SourceState, ...]
    records: tuple[RecordFact, ...]
    criteria: tuple[CriterionFact, ...] | None   # None: not tracked, or unreadable (`sources` says which)
    findings: tuple[FindingFact, ...] | None     # None: unreadable
    attempts: tuple[AttemptFact, ...] | None     # None: no execution record, or unreadable
    events: tuple[EventFact, ...]

    def presence(self, source: Source) -> Presence:
        return next((item.presence for item in self.sources if item.source is source), Presence.ABSENT)

    def applies(self, record: RecordFact | None) -> bool:
        """Whether a record proves something about the files as they were at this observation."""
        if record is None or not record.counts or record.validity != provenance.Validity.VALID:
            return False
        if self.boundary.fingerprint is None:
            return False  # without the files, nothing about them is established
        return not record.scoped or record.snapshot == self.boundary.fingerprint

    def record(self, identifier: str) -> RecordFact | None:
        return next((item for item in self.records if item.id == identifier), None)


# --- what an assessment holds ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Fact:
    change: Change
    subject: str                     # what it is about: a criterion, a check, a finding, an attempt, a source
    references: tuple[str, ...] = ()  # the records, attempts or findings it rests on
    detail: str = ""                 # fixed words of the backend (a status, a result, an outcome), never an agent's

    @property
    def trust(self) -> Trust:
        return TRUST[self.change]

    @property
    def signal(self) -> Signal | None:
        return SIGNAL_OF.get(self.change)

    def dump(self) -> dict:
        return {"change": str(self.change), "trust": str(self.trust), "subject": self.subject,
                "references": list(self.references), "detail": self.detail}


@dataclass(frozen=True)
class Indication:
    """One signal of a window, with the facts that give it and what it rests on."""
    signal: Signal
    basis: Trust
    reasons: tuple[Change, ...]
    references: tuple[str, ...]

    def dump(self) -> dict:
        return {"signal": str(self.signal), "basis": str(self.basis), "reasons": [str(item) for item in self.reasons],
                "references": list(self.references), "reliability": RELIABILITY[self.signal]}


@dataclass(frozen=True)
class Window:
    task_id: str
    iteration: int
    start: Boundary
    end: Boundary


@dataclass(frozen=True)
class Stale:
    id: str
    validity: str
    causes: tuple[str, ...]


@dataclass(frozen=True)
class Assessment:
    version: int
    window: Window
    classification: Signal
    signals: tuple[Indication, ...]   # in `PRECEDENCE` order
    facts: tuple[Fact, ...]
    sources: tuple[SourceState, ...]  # of the end; a problem of the start's is a SOURCE_UNREADABLE fact
    applicable: tuple[str, ...]       # records that count and apply to the files at the end
    stale: tuple[Stale, ...]          # evidence on record at the end that does not apply to the files then
    verification_ran: bool            # a check, a review or a user's check was recorded in the window
    files_changed: bool | None        # None: not known
    events: tuple[str, ...]           # workflow events recorded in the window: references only

    def has(self, signal: Signal) -> bool:
        return any(item.signal is signal for item in self.signals)

    def indication(self, signal: Signal) -> Indication | None:
        return next((item for item in self.signals if item.signal is signal), None)

    def of(self, change: Change) -> tuple[Fact, ...]:
        return tuple(item for item in self.facts if item.change is change)

    @property
    def unknown(self) -> tuple[Fact, ...]:
        """What remains unknown: every fact of trust UNKNOWN."""
        return tuple(item for item in self.facts if item.trust is Trust.UNKNOWN)

    @property
    def reported(self) -> tuple[Fact, ...]:
        return tuple(item for item in self.facts if item.trust is Trust.REPORTED)

    def dump(self) -> dict:
        """Plain data for a report or a later step: IDs, codes and shortened fingerprints only."""
        def boundary(item: Boundary) -> dict:
            return {"phase": item.phase, "fingerprint": item.fingerprint[:SHORT] if item.fingerprint else None,
                    "attempts_made": item.attempts_made, "last_attempt": item.last_attempt,
                    "ledger_records": item.ledger_records}
        return {"version": self.version, "task_id": self.window.task_id, "iteration": self.window.iteration,
                "start": boundary(self.window.start), "end": boundary(self.window.end),
                "classification": str(self.classification), "signals": [item.dump() for item in self.signals],
                "facts": [item.dump() for item in self.facts],
                "sources": {str(item.source): str(item.presence) for item in self.sources},
                "applicable": list(self.applicable),
                "stale": [{"id": item.id, "validity": item.validity, "causes": list(item.causes)} for item in self.stale],
                "verification_ran": self.verification_ran, "files_changed": self.files_changed,
                "events": list(self.events)}


# --- assess ------------------------------------------------------------------------------------------------------

def _check(start: Observation, end: Observation) -> None:
    a, b = start.boundary, end.boundary
    if a.task_id != b.task_id:
        raise WindowMismatch("a window holds observations of one task")
    if a.iteration != b.iteration:
        raise WindowMismatch("a window holds observations of one iteration")
    if a.attempts_made is not None and b.attempts_made is not None and b.attempts_made < a.attempts_made:
        raise WindowMismatch("the end of a window is not earlier than its start")


def _continuous(start: Observation, end: Observation) -> bool:
    """Whether the ledger at the end holds, unchanged, every record the start read, then only later ones.

    A record the start trusted must still be trusted: a ledger started again,
    one that lost or changed a record, or one the task state no longer
    anchors (`Ledger.for_state`) is not the ledger the start saw, and what
    is new in it cannot be told.
    """
    if len(end.records) < len(start.records):
        return False
    return all(x.id == y.id and x.digest == y.digest and (y.trusted or not x.trusted)
               for x, y in zip(start.records, end.records))


def _positive(record: RecordFact) -> bool:
    return evidence.POSITIVE.get(record.kind) == record.result


def _negative(record: RecordFact) -> bool:
    return NEGATIVE.get(record.kind) == record.result


def _verified(item: CriterionFact | None, seen: Observation) -> bool:
    """Whether a criterion is verified at an observation by records that each apply to the files then."""
    if item is None or item.status != criteria.Status.VERIFIED or not item.evidence:
        return False
    return all(seen.applies(seen.record(name)) for name in item.evidence)


def _files(start: Observation, end: Observation, attempts: list[AttemptFact], facts: list[Fact]) -> bool | None:
    a, b = start.boundary.fingerprint, end.boundary.fingerprint
    moved = [item.id for item in attempts if item.workspace_before is not None and item.workspace_after is not None
             and item.workspace_before != item.workspace_after]
    if a is not None and b is not None and a != b:
        facts.append(Fact(Change.FILES_CHANGED, "workspace", tuple(moved), "between the start and the end"))
        return True
    if moved:
        facts.append(Fact(Change.FILES_CHANGED, "workspace", tuple(moved), "during a call of the window"))
        return True
    if a is not None and b is not None:
        facts.append(Fact(Change.FILES_UNCHANGED, "workspace", (), "same fingerprint at the start and the end"))
        return False
    return None


def _attempts(start: Observation, end: Observation, facts: list[Fact]) -> list[AttemptFact]:
    if end.attempts is None:
        return []
    settled = {item.id for item in start.attempts or () if item.ended}
    window = [item for item in end.attempts if item.id not in settled]
    for item in window:
        if item.ended:
            facts.append(Fact(Change.CALL_ENDED, item.id, (item.id,), f"{item.phase}: {item.outcome}"))
        if item.uncertain or item.termination in (str(execution.Termination.RELEASED),
                                                  str(execution.Termination.RECONCILED)):
            facts.append(Fact(Change.ATTEMPT_EFFECTS_UNKNOWN, item.id, (item.id,),
                              "may still run" if item.uncertain else str(item.termination)))
        if item.draft or not item.ended:
            continue
        if item.outcome == execution.AttemptOutcome.COMPLETED and item.phase == execution.Phase.DEVELOP:
            facts.append(Fact(Change.ANSWER_REPORTED, item.id, (item.id,),
                              "the developer's handoff is an agent's statement"))
        if item.outcome == execution.AttemptOutcome.INVALID_FINAL_OUTPUT and item.phase == execution.Phase.REVIEW:
            facts.append(Fact(Change.REVIEW_UNUSABLE, item.id, (item.id,), str(item.failure_kind or "")))
    made, before = end.boundary.attempts_made, start.boundary.attempts_made
    if made is not None and before is not None:
        seen = sum(1 for item in end.attempts if item.number > before)
        if made - before > seen:
            facts.append(Fact(Change.ATTEMPTS_NOT_ON_RECORD, "execution", (), f"{made - before - seen} calls"))
    return window


def _checks(start: Observation, end: Observation, earlier: set[str], facts: list[Fact]) -> None:
    names = sorted({item.check for item in (*start.records, *end.records) if item.kind == "command" and item.check})
    for name in names:
        before = [item for item in start.records if item.kind == "command" and item.check == name]
        after = [item for item in end.records if item.kind == "command" and item.check == name]
        held = [item for item in before if start.applies(item)]
        current = [item for item in after if end.applies(item)]
        fresh = [item for item in current if item.id not in earlier]
        if not fresh:
            continue  # nothing new about this check on the files now
        results = {item.result for item in current}
        if "passed" in results and "failed" in results:
            facts.append(Fact(Change.CHECK_CONTRADICTED, name, tuple(item.id for item in current),
                              "passed and failed on the same files"))
            continue
        # The last result of the check that applied to the files at the start, and the last new one that applies now.
        last, was = fresh[-1], held[-1] if held else None
        passed_before = was is not None and _positive(was)
        failed_before = was is not None and _negative(was)
        references = tuple(item.id for item in ((was,) if was else ())) + (last.id,)
        if _positive(last):
            if passed_before:
                facts.append(Fact(Change.CHECK_REPEATED, name, references,
                                  "same files" if start.boundary.fingerprint == end.boundary.fingerprint
                                  else "passed on the files at the start and on the files now"))
            elif failed_before:
                facts.append(Fact(Change.CHECK_FIXED, name, references, "failed, then passed"))
            elif any(_positive(item) for item in before):
                facts.append(Fact(Change.CHECK_REVERIFIED, name, references,
                                  "its earlier pass did not apply to the files at the start"))
            else:
                facts.append(Fact(Change.CHECK_ESTABLISHED, name, references, "first pass on record"))
        elif _negative(last):
            # Deterioration only from a pass that applied to the files at the start: a stale pass proves nothing then.
            if passed_before:
                facts.append(Fact(Change.CHECK_REGRESSED, name, references, "passed, then failed"))
            else:
                facts.append(Fact(Change.CHECK_FAILING, name, references,
                                  "failed before" if failed_before else "no pass on the files at the start"))
        else:
            facts.append(Fact(Change.CHECK_NOT_CONCLUSIVE, name, (last.id,), str(last.result)))


def _criteria(start: Observation, end: Observation, earlier: set[str], facts: list[Fact]) -> None:
    if end.criteria is None or start.presence(Source.ACCEPTANCE) is Presence.UNREADABLE:
        return  # what the start held cannot be told: no criterion is taken to have advanced or fallen back
    then = {item.id: item for item in start.criteria or ()}
    for item in end.criteria:
        need = "required" if item.required else "optional"
        was, now = _verified(then.get(item.id), start), _verified(item, end)
        # Advanced only through a record of the window: older records the start could not apply establish nothing new.
        if now and not was and any(name not in earlier for name in item.evidence):
            facts.append(Fact(Change.CRITERION_VERIFIED, item.id, item.evidence, need))
        elif now:
            facts.append(Fact(Change.CRITERION_KEPT, item.id, item.evidence,
                              need if was else f"{need}: verified by records from before the window"))
        elif was:
            failed = tuple(record.id for record in end.records if record.id not in earlier and item.id in record.criteria
                           and _negative(record) and end.applies(record))
            facts.append(Fact(Change.CRITERION_REGRESSED, item.id, failed, need) if failed
                         else Fact(Change.CRITERION_LAPSED, item.id, then[item.id].evidence, f"{need}: {item.status}"))
        else:
            facts.append(Fact(Change.CRITERION_UNMET, item.id, (), f"{need}: {item.status}"))


def _findings(start: Observation, end: Observation, continuous: bool, facts: list[Fact]) -> None:
    if end.findings is None or start.presence(Source.FINDINGS) is Presence.UNREADABLE:
        return  # what the start held cannot be told: nothing is new, repeated or resolved
    then = {item.id: item for item in start.findings or ()}
    for item in end.findings:
        before = then.get(item.id)
        reported = before.reported_in if before is not None else ()
        new = tuple(name for name in item.reported_in if name not in reported)
        if new:
            facts.append(Fact(Change.FINDING_REPEATED if reported else Change.FINDING_NEW, item.id, new, item.owner))
        if before is not None and before.status == item.status:
            continue
        if item.status == reviews.Status.RESOLVED:
            proof = tuple(name for name in item.resting_on if continuous and _positive_now(end, name))
            facts.append(Fact(Change.FINDING_RESOLVED, item.id, proof) if proof
                         else Fact(Change.FINDING_RESOLUTION_CLAIMED, item.id, item.resting_on,
                                   "no record that counts verifies it on the files now"))
        elif item.status == reviews.Status.PENDING_VERIFICATION:
            facts.append(Fact(Change.FINDING_AWAITING_VERIFICATION, item.id, item.resting_on,
                              "an approval left it out; nothing shows it gone"))


def _positive_now(end: Observation, name: str) -> bool:
    record = end.record(name)
    return record is not None and _positive(record) and end.applies(record)


def _signals(facts: tuple[Fact, ...], judged: bool) -> tuple[Indication, ...]:
    found: dict[Signal, list[Fact]] = {}
    for item in facts:
        if item.signal is not None:
            found[item.signal] = [*found.get(item.signal, []), item]
    if Signal.VERIFIED_PROGRESS not in found:
        found[Signal.NO_VERIFIED_PROGRESS] = [item for item in facts if item.signal is None
                                              or item.signal in (Signal.UNKNOWN, Signal.OBSERVED_ACTIVITY)]
    indications = []
    for signal in PRECEDENCE:
        if signal not in found:
            continue
        reasons = tuple(dict.fromkeys(item.change for item in found[signal]))
        references = tuple(dict.fromkeys(name for item in found[signal] for name in item.references))
        basis = BASIS[signal] if judged or signal is not Signal.NO_VERIFIED_PROGRESS else Trust.UNKNOWN
        indications.append(Indication(signal, basis, reasons, references))
    return tuple(indications)


def assess(start: Observation, end: Observation) -> Assessment:
    """What the window from `start` to `end` established. Pure: reads and changes nothing but the two observations.

    Raises `WindowMismatch` for observations of two tasks or two iterations,
    or an end before its start.
    """
    _check(start, end)
    facts: list[Fact] = []
    for seen in (start, end):
        for item in seen.sources:
            if item.presence in (Presence.UNREADABLE, Presence.PARTIAL):
                facts.append(Fact(Change.SOURCE_UNREADABLE, str(item.source), (),
                                  f"{'start' if seen is start else 'end'}: {item.presence}"))
    if end.boundary.fingerprint is None:
        facts.append(Fact(Change.SNAPSHOT_UNKNOWN, "workspace", (), "nothing about the files is verified"))
    continuous = _continuous(start, end)
    if not continuous:
        facts.append(Fact(Change.LEDGER_DISCONTINUOUS, "ledger", (),
                          "the ledger at the end does not continue the one the start read"))
    attempts = _attempts(start, end, facts)
    changed = _files(start, end, attempts, facts)
    earlier = {item.id for item in start.records}
    fresh = [item for item in end.records if item.id not in earlier] if continuous else []
    ran = any(item.kind in VERIFYING and item.trusted for item in fresh)
    if not ran:
        facts.append(Fact(Change.VERIFICATION_NOT_RUN, "verification", (), "no check, review or user's check recorded"))
    if continuous:
        for item in start.records:
            if item.kind in provenance.JUDGED and start.applies(item) and end.boundary.fingerprint is not None \
                    and not end.applies(end.record(item.id)):
                now = end.record(item.id)
                facts.append(Fact(Change.EVIDENCE_INVALIDATED, item.id, (item.id,),
                                  f"{now.validity}: {', '.join(now.causes)}" if now else "no longer on record"))
        _checks(start, end, earlier, facts)
        _criteria(start, end, earlier, facts)
        for item in fresh:
            # A review invalidated later in the window still took place; whether it proves anything is the ledger's.
            if item.kind == "review" and item.trusted:
                facts.append(Fact(Change.REVIEW_RECORDED, item.id, (item.id,), str(item.result)))
    _findings(start, end, continuous, facts)
    order = list(Change)
    facts = tuple(sorted(dict.fromkeys(facts), key=lambda item: (order.index(item.change), item.subject,
                                                                 item.references, item.detail)))
    judged = continuous and end.boundary.fingerprint is not None
    signals = _signals(facts, judged)
    started = {item.id for item in start.events}
    return Assessment(
        VERSION, Window(end.boundary.task_id, end.boundary.iteration, start.boundary, end.boundary),
        next(item.signal for item in signals), signals, facts, end.sources,
        tuple(item.id for item in end.records if item.kind in provenance.JUDGED and end.applies(item)),
        tuple(Stale(item.id, item.validity, item.causes) for item in end.records
              if item.kind in provenance.JUDGED and not end.applies(item)),
        ran, changed, tuple(item.id for item in end.events if item.id not in started))


# --- consecutive windows -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Series:
    """Consecutive windows of one task, in order. What Step 12 may compare with `execution.no_progress_windows`.

    Nothing here applies that setting: the counts are only read.
    """
    windows: tuple[Assessment, ...]

    def __post_init__(self):
        windows = tuple(self.windows)
        object.__setattr__(self, "windows", windows)
        if not all(isinstance(item, Assessment) for item in windows):
            raise TypeError("a series holds assessments")
        if len({item.window.task_id for item in windows}) > 1:
            raise WindowMismatch("a series holds the windows of one task")
        for before, after in zip(windows, windows[1:]):
            a, b = before.window.end, after.window.start
            if b.iteration < a.iteration or (a.attempts_made is not None and b.attempts_made is not None
                                             and b.attempts_made < a.attempts_made):
                raise WindowMismatch("the windows of a series follow each other")

    @property
    def streak(self) -> tuple[Assessment, ...]:
        """The last windows, in order, none of which established verified progress."""
        kept = []
        for item in reversed(self.windows):
            if item.has(Signal.VERIFIED_PROGRESS):
                break
            kept.append(item)
        return tuple(reversed(kept))

    @property
    def unchanged_files(self) -> int:
        """How many of the last windows, in a row, saw the files unchanged."""
        count = 0
        for item in reversed(self.windows):
            if item.files_changed is not False:
                break
            count += 1
        return count

    @property
    def repeated_findings(self) -> tuple[str, ...]:
        """Findings reported again in every window of the streak, when it has any window."""
        streak = self.streak
        if not streak:
            return ()
        sets = [{fact.subject for fact in item.of(Change.FINDING_REPEATED)} for item in streak]
        return tuple(sorted(set.intersection(*sets)))


# --- observe -----------------------------------------------------------------------------------------------------

def _fingerprint(workspace: Path) -> str | None:
    try:
        return gitops.fingerprint(workspace, reader=True) if Path(workspace).is_dir() else None
    except (gitops.FingerprintError, gitops.GitError, OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return None


def _text(value) -> str | None:
    return value if isinstance(value, str) else None


def _names(value) -> tuple[str, ...]:
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()


def observe(task_path: Path, state: dict, workspace: Path, *, cfg: dict | None = None,
            fingerprint=_TAKE) -> Observation:
    """What is on record for a task now, as an `Observation`. Reads only; `state` is not changed.

    `fingerprint` is the fingerprint of the task's files when the caller
    already took it (None: it could not be); by default it is taken here,
    reading Git only (`gitops.READER`). `cfg`, the project's configuration,
    lets a record's check be compared with the check configured now; without
    it only the files and artifacts are. Sources that are not on record are
    `ABSENT`, sources that are and cannot be used `UNREADABLE`: neither stops
    the observation. Raises ValueError when `state` is not the task's.
    """
    task_path, workspace = Path(task_path), Path(workspace)
    task_id = state.get("id") if isinstance(state, dict) else None
    if not isinstance(task_id, str) or not execution.TASK_ID.fullmatch(task_id) or task_path.name != task_id:
        raise ValueError("the state or the folder given is not the task's")
    iteration = state.get("iteration")
    if type(iteration) is not int:
        raise ValueError("the state names no iteration")
    sources: dict[Source, Presence] = {}
    if fingerprint is _TAKE:
        fingerprint = _fingerprint(workspace)
    fingerprint = fingerprint if isinstance(fingerprint, str) and execution.FINGERPRINT.fullmatch(fingerprint) else None
    sources[Source.WORKSPACE] = Presence.PRESENT if fingerprint else Presence.UNREADABLE

    attempts, made, last = None, None, None
    try:
        record = execution.load(state)
    except execution.InvalidRecord:
        record, sources[Source.EXECUTION] = None, Presence.UNREADABLE
    else:
        if record is not None and record.task_id != task_id:
            record, sources[Source.EXECUTION] = None, Presence.UNREADABLE
        elif record is None:
            sources[Source.EXECUTION] = Presence.ABSENT
        else:
            sources[Source.EXECUTION] = Presence.PRESENT
    if record is not None:
        made = record.next_attempt - 1
        step = record.step.attempts if record.step is not None else ()
        last = step[-1].attempt_id if step else None
        attempts = tuple(AttemptFact(item.attempt_id, item.number, str(item.phase),
                                     str(item.outcome) if item.outcome else None, item.failure_kind,
                                     str(item.termination) if item.termination else None, item.ended, item.uncertain,
                                     item.draft is not None, item.workspace_before, item.workspace_after)
                         for item in step)

    try:
        accepted = criteria.Acceptance.load(state)
    except ValueError:
        accepted, sources[Source.ACCEPTANCE] = None, Presence.UNREADABLE
    else:
        sources[Source.ACCEPTANCE] = Presence.ABSENT if accepted is None else Presence.PRESENT
    ledger = evidence.read(task_path)
    if accepted is not None:
        ledger = ledger.for_state(accepted.ledger)  # a ledger that lost what the state saw confirms nothing
    sources[Source.LEDGER] = Presence.PARTIAL if ledger.problems else Presence.PRESENT if ledger.records \
        or (task_path / evidence.LEDGER).exists() else Presence.ABSENT
    if cfg is None:
        now, sources[Source.CONFIGURATION] = provenance.Current(fingerprint=fingerprint), Presence.ABSENT
    else:
        now, sources[Source.CONFIGURATION] = provenance.current(cfg, workspace, fingerprint), Presence.PRESENT
    records = []
    for item in ledger.records:
        found = provenance.assess(ledger, item, now)
        kind = str(item.get("kind"))
        records.append(RecordFact(
            str(item.get("id")), _text(item.get("hash")) or "", kind, _text(item.get("result")), bool(item["trusted"]),
            ledger.counts(item),
            str(found.validity), tuple(str(cause) for cause in found.causes), found.check,
            _text(item.get("fingerprint")), kind != "authorization" and item.get("scope", "code") == "code",
            _names(item.get("criteria"))))

    judged = None
    if accepted is not None:
        current, _ = criteria.judged_now(accepted, ledger)
        judged = tuple(CriterionFact(item.id, bool(item.required), str(item.status), tuple(item.evidence))
                       for item in current.criteria)

    try:
        registry = reviews.Findings.load(state)
    except ValueError:
        found_items, sources[Source.FINDINGS] = None, Presence.UNREADABLE
    else:
        sources[Source.FINDINGS] = Presence.PRESENT if "findings" in state else Presence.ABSENT
        found_items = tuple(FindingFact(item.id, str(item.status), item.owner, item.blocking, tuple(item.reported_in),
                                        tuple(value for key in ("review", "evidence")
                                              if isinstance(value := (item.verification or {}).get(key), str)
                                              and evidence.RECORD_ID.fullmatch(value)))
                            for item in registry.items)

    history = state.get("history")
    if history is None:
        events, sources[Source.HISTORY] = (), Presence.ABSENT
    elif isinstance(history, list):
        events = tuple(EventFact(item["id"], item["event"]) for item in history
                       if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("event"), str))
        sources[Source.HISTORY] = Presence.PRESENT
    else:
        events, sources[Source.HISTORY] = (), Presence.UNREADABLE

    boundary = Boundary(task_id, iteration, str(state.get("phase")), fingerprint, made, last, len(records), ledger.head)
    return Observation(boundary, tuple(SourceState(source, sources[source]) for source in Source if source in sources),
                       tuple(records), judged, found_items, attempts, events)
