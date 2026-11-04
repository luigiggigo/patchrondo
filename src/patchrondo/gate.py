"""The acceptance gate: whether a task may be completed now, decided by fixed rules from what the backend recorded.

The reviewer judges the code; the acceptance record (`acceptance.py`) judges
each criterion from the evidence ledger; this module answers the one question
the workflow asks before a task becomes `done`, and says what is missing when
the answer is no. It reads only what the backend wrote, calls no model and
runs nothing:

    the acceptance record   the approval of the code, each criterion's status and why (`Acceptance.basis`)
    the evidence ledger     every record that proves something, and the validity of the ones that no longer
                            count (`provenance.recorded`: STALE, INVALID or UNKNOWN, with typed causes)
    the files               whether the test results are about the files as they are now (`fresh`), and the
                            fingerprint they were judged on (`snapshot`)
    the configuration       whether a switch turned off what the configured checks rely on (`protections`)
    the findings            reported, never read to decide (see below)

Decisions, in the order they take precedence:

    ERROR                   the ledger failed its check, or a criterion counted as verified names a record that
                            does not count: nothing can be concluded
    FAIL                    the review asked for a change to the code, or a required criterion's check failed
    NEEDS_REVERIFICATION    evidence that proved a required criterion, or the approval of the code, no longer
                            counts, or the test results are about other files or other checks
    PENDING                 the workflow still has work its agents and checks can do
    BLOCKED                 what is left needs the user: a decision, a capability, a check by a person or one
                            outside the project, or a switch put back
    PASS                    the code is approved, every required criterion is verified by records that count,
                            the test results are about these files, and nothing above holds

Every record the gate relies on is about its own `snapshot`: the review that
approved the code, and each record a verified required criterion names, must
name that fingerprint (`_elsewhere`, the rule of `provenance.assess`). The
engine invalidates records about other files before it asks the gate, so
this decides nothing there; it keeps an approval or a result about other
files from passing whatever a caller did (NEEDS_REVERIFICATION). An approval
that names no approving review record is ERROR.

Only PASS allows completion (`Gate.completion_allowed`). Optional criteria
are shown and never block. No profile, switch or answer of an agent lowers a
required criterion: the gate has no input that could.

Findings are reported (`findings`) and decide nothing, as AGENTS.md and
`findings.py` require. The condition "no blocking finding is open" holds by
construction: a finding is blocking for a change the code needs, which keeps
the code from being approved, or for a required criterion that is not
verified, which keeps the gate from PASS. A finding whose criterion was
verified since is listed as settled by that criterion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json

from . import evidence, provenance, verification
from .acceptance import RUNTIME_PROOFS, Acceptance, Basis, Code, Outcome, Status, _Proofs
from .findings import Findings, Status as FindingStatus
from .storage import now

VERSION = 1
POLICY = "standard"  # the one policy of this version; a later profile may only ask for more
MAX_REFS = 20        # evidence IDs a reviewer may cite for one criterion
REF_LIMIT = 40       # characters of one


class Decision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"
    BLOCKED = "BLOCKED"
    NEEDS_REVERIFICATION = "NEEDS_REVERIFICATION"
    ERROR = "ERROR"


class Action(StrEnum):
    COMPLETE = "COMPLETE"
    DEVELOP = "DEVELOP"                                  # the developer changes the code
    RUN_CHECKS = "RUN_CHECKS"                            # the runtime runs the project's checks again, under the consent
    REVIEW = "REVIEW"                                    # the reviewer reads the code again
    CONTINUE = "CONTINUE"                                # the workflow goes on as it is
    WAIT_FOR_AUTHORIZATION = "WAIT_FOR_AUTHORIZATION"
    PROVIDE_CAPABILITY = "PROVIDE_CAPABILITY"            # a tool, a command or a configuration nobody in the workflow has
    WAIT_FOR_USER_VALIDATION = "WAIT_FOR_USER_VALIDATION"
    WAIT_FOR_EXTERNAL_VERIFICATION = "WAIT_FOR_EXTERNAL_VERIFICATION"
    RESTORE_CONFIGURATION = "RESTORE_CONFIGURATION"      # a switch turned off a protection the configured checks need
    REPAIR_EVIDENCE = "REPAIR_EVIDENCE"


class Reason(StrEnum):
    """What keeps the gate from PASS, beside the criteria's own `Basis`."""
    LEDGER_PROBLEM = "LEDGER_PROBLEM"
    EVIDENCE_INCONSISTENT = "EVIDENCE_INCONSISTENT"      # a verified criterion names a record that does not count
    CODE_CHANGES_REQUIRED = "CODE_CHANGES_REQUIRED"
    CODE_NOT_APPROVED = "CODE_NOT_APPROVED"
    APPROVAL_NO_LONGER_COUNTS = "APPROVAL_NO_LONGER_COUNTS"
    TESTS_NOT_CURRENT = "TESTS_NOT_CURRENT"              # the test results are about other files, checks or inputs
    FILES_NOT_CHECKED = "FILES_NOT_CHECKED"              # nobody compared the files with the test results here
    EVIDENCE_STALE = "EVIDENCE_STALE"
    EVIDENCE_INVALID = "EVIDENCE_INVALID"
    EVIDENCE_UNKNOWN = "EVIDENCE_UNKNOWN"
    VERIFICATION_DISABLED = "VERIFICATION_DISABLED"
    PROVENANCE_DISABLED = "PROVENANCE_DISABLED"


ORDER = (Decision.ERROR, Decision.FAIL, Decision.NEEDS_REVERIFICATION, Decision.PENDING, Decision.BLOCKED)

# What each reason of a criterion means for the gate, and what comes next for it.
_CRITERIA = {
    Basis.CODE_CHANGE_REQUIRED: (Decision.FAIL, Action.DEVELOP),
    Basis.CHECK_FAILED: (Decision.FAIL, Action.DEVELOP),
    Basis.USER_CHECK_FAILED: (Decision.FAIL, Action.DEVELOP),
    Basis.WAITING_FOR_DEPENDENCY: (Decision.PENDING, Action.CONTINUE),
    Basis.IN_DEVELOPMENT: (Decision.PENDING, Action.CONTINUE),
    Basis.CHECK_UNSETTLED: (Decision.PENDING, Action.RUN_CHECKS),
    Basis.AWAITING_CHECKS: (Decision.PENDING, Action.RUN_CHECKS),
    Basis.AWAITING_REVIEW: (Decision.PENDING, Action.REVIEW),
    Basis.AUTHORIZATION_REQUIRED: (Decision.BLOCKED, Action.WAIT_FOR_AUTHORIZATION),
    Basis.AUTHORIZATION_DENIED: (Decision.BLOCKED, Action.WAIT_FOR_AUTHORIZATION),
    Basis.CHECKS_NOT_ALLOWED: (Decision.BLOCKED, Action.WAIT_FOR_AUTHORIZATION),
    Basis.NO_TEST_COMMANDS: (Decision.BLOCKED, Action.PROVIDE_CAPABILITY),
    Basis.TOOL_MISSING: (Decision.BLOCKED, Action.PROVIDE_CAPABILITY),
    Basis.NO_COMMAND: (Decision.BLOCKED, Action.PROVIDE_CAPABILITY),
    Basis.REVIEWER_CAPABILITY_BLOCKED: (Decision.BLOCKED, Action.PROVIDE_CAPABILITY),
    Basis.UNCLASSIFIED: (Decision.BLOCKED, Action.WAIT_FOR_USER_VALIDATION),
    Basis.HANDED_OVER_BY_REVIEW: (Decision.BLOCKED, Action.WAIT_FOR_USER_VALIDATION),
    Basis.MANUAL_VALIDATION_REQUIRED: (Decision.BLOCKED, Action.WAIT_FOR_USER_VALIDATION),
    Basis.EXTERNAL_VERIFICATION_REQUIRED: (Decision.BLOCKED, Action.WAIT_FOR_EXTERNAL_VERIFICATION),
}
# Which action comes first when several hold within one decision.
_PRIORITY = (Action.REPAIR_EVIDENCE, Action.DEVELOP, Action.RUN_CHECKS, Action.REVIEW, Action.CONTINUE,
             Action.RESTORE_CONFIGURATION, Action.WAIT_FOR_AUTHORIZATION, Action.PROVIDE_CAPABILITY,
             Action.WAIT_FOR_EXTERNAL_VERIFICATION, Action.WAIT_FOR_USER_VALIDATION)
_VALIDITY = {provenance.Validity.STALE: Reason.EVIDENCE_STALE, provenance.Validity.INVALID: Reason.EVIDENCE_INVALID,
             provenance.Validity.UNKNOWN: Reason.EVIDENCE_UNKNOWN}
MESSAGES = {
    Decision.ERROR: "The completion gate could not conclude anything from the evidence recorded for this task",
    Decision.FAIL: "The code or a required check failed",
    Decision.NEEDS_REVERIFICATION: "Evidence this task relied on no longer counts and has to be produced again",
    Decision.PENDING: "The workflow still has work to do before the task can be completed",
    Decision.BLOCKED: "The task cannot be completed until you act",
    Decision.PASS: "Every condition for completing the task holds",
}


def protections(cfg: dict) -> list[dict]:
    """Protections a switch turned off while the configured checks rely on them. Reads the validated configuration only.

    `PATCHRONDO_VERIFICATION=0` runs tests.commands alone: the checks of the
    `verification` section (their reports, minimum and required tests,
    artifacts) do not run, and the legacy commands would prove the tests
    without them. `PATCHRONDO_PROVENANCE=0` leaves out the inputs and the
    environment checks declare, so a result about other inputs or another
    computer would count. Either way the task may not be completed until the
    switch is put back; a project that configured none of these is not
    concerned, and keeps the behavior the switches document.
    """
    try:
        section = verification.parse_section(cfg.get("verification"), default_timeout=verification._timeout(cfg))
    except ValueError:
        section = []  # the configuration was validated when it was read; a run refuses it before any check
    missing = []
    if section and not verification.enabled():
        missing.append({"reason": str(Reason.VERIFICATION_DISABLED), "switch": verification.SWITCH,
                        "checks": [check.id for check in section]})
    tied = [check.id for check in [*verification.legacy(cfg), *section] if check.inputs or check.same_environment]
    if tied and not provenance.enabled():
        missing.append({"reason": str(Reason.PROVENANCE_DISABLED), "switch": provenance.SWITCH, "checks": tied})
    return missing


@dataclass
class Gate:
    task_id: str | None
    decision: str
    next_action: str
    code_review: str | None
    review: str | None                 # the review record that approved the code, while it counts
    snapshot: str | None               # the fingerprint of the files the gate was evaluated on; None when not taken
    fresh: bool | None                 # whether the test results are about those files; None when not compared
    ledger: dict                       # count and head of the ledger it read
    criteria: list[dict]
    conditions: list[dict]             # what keeps it from PASS, in order of precedence
    findings: dict
    protections: list[dict] = field(default_factory=list)
    at: str = ""

    @property
    def completion_allowed(self) -> bool:
        return self.decision == Decision.PASS

    @property
    def id(self) -> str:
        """`GE-` and the start of the SHA-256 of everything it was decided from and decided, time aside: equal inputs, equal ID."""
        body = {key: value for key, value in self._body().items() if key != "at"}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        return f"GE-{digest.hexdigest()[:16]}"

    def _body(self) -> dict:
        return {"version": VERSION, "policy": POLICY, "task_id": self.task_id, "decision": str(self.decision),
                "completion_allowed": self.completion_allowed, "next_action": str(self.next_action),
                "code_review": self.code_review, "review": self.review, "snapshot": self.snapshot, "fresh": self.fresh,
                "ledger": dict(self.ledger), "criteria": self.criteria, "conditions": self.conditions,
                "findings": self.findings, "protections": self.protections, "at": self.at}

    def dump(self) -> dict:
        return {"id": self.id, **self._body(), "message": self.message()}

    def blocking(self) -> list[str]:
        """The required criteria that keep the task from completion."""
        return [entry["id"] for entry in self.criteria if entry["required"] and entry["status"] != Status.VERIFIED]

    def brief(self) -> dict:
        """What an event of the history says about the gate: references and fixed words, no text of an agent."""
        return brief_of(self.dump())

    def message(self) -> str:
        """One sentence for people, from fixed words and IDs only."""
        text = MESSAGES[Decision(self.decision)]
        named = self.blocking()
        if named:
            text += f" ({', '.join(named[:6])}{'…' if len(named) > 6 else ''})"
        switches = [entry["switch"] for entry in self.protections]
        if switches:
            text += f". Put back {' and '.join(switches)} (unset, or not 0): the configured checks rely on what they turn off"
        return text + f". Next: {self.next_action}."


def brief_of(saved: dict) -> dict:
    """The part of a saved gate an event names: its ID, decision, next action, the required criteria it waits for, and why."""
    rows = saved.get("criteria") if isinstance(saved.get("criteria"), list) else []
    conditions = saved.get("conditions") if isinstance(saved.get("conditions"), list) else []
    ledger = saved.get("ledger") if isinstance(saved.get("ledger"), dict) else {}
    return {"id": saved.get("id"), "decision": saved.get("decision"), "next_action": saved.get("next_action"),
            "criteria": [row.get("id") for row in rows if isinstance(row, dict) and row.get("required")
                         and row.get("status") != Status.VERIFIED],
            "reasons": list(dict.fromkeys(entry.get("reason") for entry in conditions if isinstance(entry, dict))),
            "snapshot": saved.get("snapshot"), "ledger": ledger.get("count")}


def _findings(state_findings, acceptance: Acceptance) -> dict:
    """Findings as the gate reports them; read to explain, never to decide."""
    if state_findings is None:
        return {"open_blocking": [], "settled": [], "pending_verification": []}
    try:
        registry = Findings.load({"findings": state_findings})
    except ValueError:
        return {"unreadable": True}
    statuses = {criterion.id: criterion.status for criterion in acceptance.criteria}
    approved = acceptance.code.get("status") == Code.APPROVED
    open_, settled, pending = [], [], []
    for item in registry.items:
        if item.status == FindingStatus.PENDING_VERIFICATION:
            pending.append(item.id)
        elif item.status == FindingStatus.OPEN and item.blocking:
            criterion = item.criterion_id
            if criterion and statuses.get(criterion) == Status.VERIFIED and item.owner != "developer":
                settled.append(item.id)
            elif item.owner == "developer" and approved:
                settled.append(item.id)  # a later approval of the code read the code again; it waits for no criterion
            else:
                open_.append(item.id)
    return {"open_blocking": open_, "settled": settled, "pending_verification": pending}


def _worst(found) -> provenance.Validity:
    """The validity that says most against a set of records (`Assessment`s or their saved rows): INVALID, UNKNOWN, STALE."""
    seen = {str(item["validity"] if isinstance(item, dict) else item.validity) for item in found}
    return next((validity for validity in (provenance.Validity.INVALID, provenance.Validity.UNKNOWN) if validity in seen),
                provenance.Validity.STALE)


def _elsewhere(ledger: evidence.Ledger, record: dict | None, snapshot: str | None) -> provenance.Assessment | None:
    """Why a record that counts in the ledger is not about the files at `snapshot`; None when it is, or cannot be told.

    The rule is the invalidation's own (`provenance.assess` on the fingerprint
    alone): a record about the task's files names the fingerprint it was made
    on, and it must be `snapshot`. The engine invalidates such records before
    it asks the gate, so this never decides there; it keeps the gate from
    passing on evidence or an approval about other files whatever its caller
    did. The gate reads no file: artifacts are left to that invalidation.
    """
    if snapshot is None or record is None or not ledger.counts(record):
        return None
    found = provenance.assess(ledger, record, provenance.Current(fingerprint=snapshot), artifacts=lambda _artifact: evidence.INTACT)
    return None if found.usable else found


def evaluate(acceptance: Acceptance, ledger: evidence.Ledger, *, task_id: str | None = None, fresh: bool | None = None,
             snapshot: str | None = None, missing: list[dict] | None = None, findings=None) -> Gate:
    """Decide from a record judged against `ledger` (`Acceptance.refresh`) whether its task may be completed now.

    `fresh` is whether the task's test results are about its files as they
    are now, and `snapshot` their fingerprint: unknown (None), the gate never
    passes. `missing` is `protections(cfg)`. `findings` is the saved findings
    record of the task, reported only. Nothing is written or run.
    """
    proofs = _Proofs(ledger, implicit=not acceptance.explicit_outcomes)
    conditions: list[dict] = []

    def hold(decision: Decision, reason: str, action: Action, **detail) -> None:
        conditions.append({"decision": str(decision), "reason": str(reason), "action": str(action), **detail})

    if ledger.problems:
        hold(Decision.ERROR, Reason.LEDGER_PROBLEM, Action.REPAIR_EVIDENCE, problems=len(ledger.problems))
    rows = []
    for criterion in acceptance.criteria:
        basis = acceptance.basis.get(criterion.id)
        row = {"id": criterion.id, "required": criterion.required, "status": str(criterion.status), "basis": basis,
               "evidence": list(criterion.evidence)}
        if criterion.status == Status.VERIFIED:
            # The presence of an ID proves nothing: each one must still count, as the ledger says now.
            broken = [name for name in criterion.evidence if not ledger.counts(ledger.get(name))]
            if broken:
                row["broken"] = broken
                if criterion.required:
                    hold(Decision.ERROR, Reason.EVIDENCE_INCONSISTENT, Action.REPAIR_EVIDENCE, criteria=[criterion.id],
                         evidence=broken)
            # And each one must be about the files the gate is evaluated on, not only about files it once saw.
            elsewhere = [found for name in criterion.evidence if name not in broken
                         and (found := _elsewhere(ledger, ledger.get(name), snapshot)) is not None]
            if elsewhere:
                row["elsewhere"] = [{"evidence": found.evidence, "validity": str(found.validity),
                                     "causes": [str(cause) for cause in found.causes]} for found in elsewhere]
                if criterion.required:
                    kinds = {found.kind for found in elsewhere}
                    hold(Decision.NEEDS_REVERIFICATION, _VALIDITY[_worst(elsewhere)],
                         Action.RUN_CHECKS if "command" in kinds else Action.REVIEW if "review" in kinds
                         else Action.WAIT_FOR_USER_VALIDATION, criteria=[criterion.id],
                         evidence=[found.evidence for found in elsewhere])
            rows.append(row)
            continue
        # Evidence that proved it when it was written and no longer counts: say which, and why, as the ledger says.
        lapsed = []
        for proof in criterion.missing:
            for record in proofs.sources(proof, criterion.id):
                if not ledger.counts(record) and proofs.positive(record, criterion.id):
                    found = provenance.recorded(ledger, record)
                    lapsed.append({"evidence": found.evidence, "validity": str(found.validity),
                                   "causes": [str(cause) for cause in found.causes], "proof": proof})
        if lapsed:
            row["lapsed"] = lapsed
        if criterion.required:
            decision, action = _CRITERIA.get(basis, (Decision.ERROR, Action.REPAIR_EVIDENCE))
            if lapsed and decision != Decision.FAIL and basis != Basis.WAITING_FOR_DEPENDENCY:
                worst = _worst(lapsed)
                again = {entry["proof"] for entry in lapsed}
                action = (Action.RUN_CHECKS if again & set(RUNTIME_PROOFS) else Action.REVIEW if "review" in again
                          else Action.WAIT_FOR_USER_VALIDATION)
                hold(Decision.NEEDS_REVERIFICATION, _VALIDITY[worst], action, criteria=[criterion.id],
                     evidence=[entry["evidence"] for entry in lapsed])
            else:
                hold(decision, basis or Reason.EVIDENCE_INCONSISTENT, action, criteria=[criterion.id])
        rows.append(row)

    code = acceptance.code.get("status")
    review = acceptance.code.get("evidence") if code == Code.APPROVED else None
    if code == Code.APPROVED:
        # The approval is about the files the reviewer read: it counts here only for those very files.
        approval = ledger.get(review) if isinstance(review, str) else None
        if approval is None or approval.get("kind") != "review" or approval.get("verdict") != "APPROVED":
            hold(Decision.ERROR, Reason.EVIDENCE_INCONSISTENT, Action.REPAIR_EVIDENCE, evidence=[review])
            review = None
        elif (found := _elsewhere(ledger, approval, snapshot)) is not None:
            hold(Decision.NEEDS_REVERIFICATION, Reason.APPROVAL_NO_LONGER_COUNTS, Action.REVIEW, evidence=[found.evidence],
                 validity=str(found.validity), causes=[str(cause) for cause in found.causes])
            review = None
    if code == Code.CHANGES_REQUIRED:
        hold(Decision.FAIL, Reason.CODE_CHANGES_REQUIRED, Action.DEVELOP)
    elif code != Code.APPROVED:
        last = ledger.last("review")
        if last is not None and last.get("verdict") == "APPROVED" and not ledger.counts(last):
            found = provenance.recorded(ledger, last)
            hold(Decision.NEEDS_REVERIFICATION, Reason.APPROVAL_NO_LONGER_COUNTS, Action.REVIEW, evidence=[found.evidence],
                 validity=str(found.validity), causes=[str(cause) for cause in found.causes])
        else:
            hold(Decision.PENDING, Reason.CODE_NOT_APPROVED, Action.REVIEW)
    if fresh is False:
        hold(Decision.NEEDS_REVERIFICATION, Reason.TESTS_NOT_CURRENT, Action.RUN_CHECKS)
    elif fresh is None or snapshot is None:
        hold(Decision.PENDING, Reason.FILES_NOT_CHECKED, Action.CONTINUE)
    for entry in missing or []:
        hold(Decision.BLOCKED, entry["reason"], Action.RESTORE_CONFIGURATION, switch=entry["switch"], checks=entry["checks"])
    if not conditions and acceptance.outcome != Outcome.RELEASE_READY:
        # The record and the gate read the same statuses; they cannot disagree unless the record was not judged.
        hold(Decision.ERROR, Reason.EVIDENCE_INCONSISTENT, Action.REPAIR_EVIDENCE)

    decision = next((candidate for candidate in ORDER if any(entry["decision"] == candidate for entry in conditions)),
                    Decision.PASS)
    actions = {entry["action"] for entry in conditions if entry["decision"] == decision}
    action = next((candidate for candidate in _PRIORITY if candidate in actions), Action.COMPLETE)
    return Gate(task_id=task_id, decision=str(decision), next_action=str(action), code_review=code, review=review,
                snapshot=snapshot, fresh=fresh, ledger={"count": ledger.count, "head": ledger.head}, criteria=rows,
                conditions=conditions, findings=_findings(findings, acceptance), protections=list(missing or []), at=now())


# --- what a reviewer cites ----------------------------------------------------------------

def references(review: dict, acceptance: Acceptance, ledger: evidence.Ledger) -> dict:
    """Check the evidence IDs a settled review cites for each criterion. They are recorded, and never proof.

    Accepted only when the record exists in this task's ledger, still counts
    (`provenance.recorded` is VALID), is one the plan reads for that criterion
    (`_Proofs.sources`, or the user's decision about its operation) and says
    it proved it. Anything else is refused with a fixed reason: UNKNOWN_RECORD
    (not an ID of this ledger: another task's, or invented), STALE, INVALID or
    UNKNOWN (with its causes), NOT_FOR_CRITERION, NOT_POSITIVE. Returns
    {criterion: {"accepted": [...], "refused": [{"id", "reason", "causes"?}]}}
    for the criteria that cited something.
    """
    proofs = _Proofs(ledger, implicit=not acceptance.explicit_outcomes)
    known = {criterion.id: criterion for criterion in acceptance.criteria}
    checked: dict[str, dict] = {}
    for entry in review.get("criteria") or []:
        cited = entry.get("evidence_refs") or []
        criterion = known.get(entry.get("id"))
        if criterion is None or not cited:
            continue
        pertinent = {record["id"]: record for proof in criterion.proof for record in proofs.sources(proof, criterion.id)
                     if isinstance(record.get("id"), str)}
        decision = ledger.last("authorization", criterion.id)
        if decision is not None:
            pertinent[decision["id"]] = decision
        accepted, refused = [], []
        for name in dict.fromkeys(cited):
            record = ledger.get(name) if isinstance(name, str) and evidence.RECORD_ID.fullmatch(name) else None
            shown = name[:REF_LIMIT] if isinstance(name, str) else ""
            if record is None:
                refused.append({"id": shown, "reason": "UNKNOWN_RECORD"})
                continue
            found = provenance.recorded(ledger, record)
            if not found.usable:
                refused.append({"id": name, "reason": str(found.validity), "causes": [str(cause) for cause in found.causes]})
            elif name not in pertinent:
                refused.append({"id": name, "reason": "NOT_FOR_CRITERION"})
            elif not (proofs.positive(record, criterion.id) or (record.get("kind") == "authorization"
                                                                and record.get("result") == "granted")):
                refused.append({"id": name, "reason": "NOT_POSITIVE"})
            else:
                accepted.append(name)
        checked[criterion.id] = {"accepted": accepted, "refused": refused}
    return checked
