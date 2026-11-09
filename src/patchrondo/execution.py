"""Records of agent execution: the data contracts of Phase 2.5, Adaptive Agent Execution (Steps 01-08).

It defines what `core.run_task` saves and reads under `state["execution"]`,
the optional `execution` section of a project's configuration and the limits
it resolves to. It calls no agent, runs nothing, reads no file and writes
nothing. `core.validate_config` refuses an `execution` section this version
cannot use; `core._step_settings`, `core._attempt` and `core.release_attempt`
(Step 04) record each call of a step whose project enabled execution, and
apply its call limits. Since Step 05 the outcome of every call is read from
one table here (`outcome_of`, `termination_of`), and no attempt can be added
that its step's budget or the task's cannot take (`ExecutionRecord.admission`,
enforced by `new_attempt`). The command of a step (`agents.command`), quota
recovery and the gate do not know this module, and nothing here starts a
call: a budget only ever refuses one. Since Step 07 `next_call` decides,
from the record alone, whether a developer call that reached Claude's turn
limit may be followed at once by one continuation of the same step, with
limits no larger than the step's and no larger than the budgets have left
(`ExecutionRecord.sized_call`); since Step 08 also whether a reviewer call
whose answer could not be used as a review may be followed by one new,
independent review of the same files. `core._continued` starts it, or not.

Units of work, from the largest:

    task        `T-…`: the user's request, its criteria and its worktree (`state.json`).
    iteration   one develop → test → review cycle of the workflow (`state["iteration"]`).
    phase       the part of an iteration that calls an agent: `develop` (the developer) or
                `review` (the reviewer). An iteration and a phase name one step, as
                `agents.step_key` does (`001:developer`).
    attempt     one start of a provider CLI for that step. Its ID (`CX-0001`) is given by
                PatchRondo, numbered in the task and never given twice; its `ordinal`
                counts the attempts of its step (1, 2, …). Several attempts of one step
                are not several iterations.
    session     the provider's own conversation, when its CLI reports one: an opaque
                reference that only the provider understands. It is never an ID of
                PatchRondo, and nothing resumes it until a native resume is validated.

The outcome of an attempt describes the CLI call only. Three things are kept
apart. The CLI's own end is its termination (`EXITED`: it ended by itself).
A usable answer is the outcome `COMPLETED`: the CLI gave a final answer in the
form its role needs (a developer's handoff that is not empty; a reviewer's
answer that `core.parse_review` reads and that gives each of its criteria an
outcome). A CLI that exited with an answer the workflow cannot read is
`INVALID_FINAL_OUTPUT`, never `COMPLETED`. The end of a phase is the
workflow's: the verdict, the tests and `run_task` moving on decide it, and a
task is done only through `core._complete` with the gate's `PASS`. No record
here has a field for the status of a task or for the end of a phase.

Budgets name their unit. Claude turns, provider calls and seconds are
enforced; tokens are only observed and never limited. A Codex call has no
Claude turns (`NOT_APPLICABLE`), and usage a CLI did not report is
`UNKNOWN`, never 0. Where a call's usage is unknown, its charge is the most
the call was allowed (its turn limit, its timeout); a limited budget with an
uncharged unknown left in it allows nothing more. A call is admitted only when
every limited line of its step's budget and of the task's can take the most
that call may use (one provider call, its timeout, its turn limit), since
that is what it is charged while it runs.

Each attempt has a status (`STARTING`, saved before anything is created or
started; `RUNNING`, saved once the CLI's process exists and its process file
is written; `ENDED`), an outcome (how the call ended) and, once ended, a
termination (whether its process and what it started are known to have
stopped). The lifecycle a person reads is derived from them, with no status
of its own: an answer received is `ENDED` with `COMPLETED`; a failure is
`ENDED` with another outcome; an attempt is uncertain (`uncertain`) when a
later run finds it without an end, or when it ended with its termination
`UNVERIFIED`. No call starts beside an uncertain attempt until the user
releases it (`ExecutionRecord.release`).

Records are versioned (schema 1). Since Step 05.1 two records also have a
schema 2: a task's record that names the execution policy the task is bound
to (`ExecutionRecord.policy`), and an attempt that is a request for tests
(`AttemptRecord.draft`). A record without them is still saved as schema 1,
so nothing saved earlier is rewritten. Loading is strict: an unknown or missing
field, a value of another type (`True` is not a number), an unknown enum
value, a newer or older schema version and a record that contradicts itself
raise `InvalidRecord`. The caller keeps the saved value as it is; nothing
here replaces, migrates or defaults it. A state without an `execution` key
is a task from before these records, and it has none.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
import json
import re
import subprocess
from types import MappingProxyType
from typing import Mapping

from .agents import PROVIDERS, ROLES

VERSION = 1
KEY = "execution"  # the key a later step saves the record under in `state.json`

# `CX-` is used by nothing else: tasks `T-`, criteria `AC-`, journal attempts `A-`, journals `J-`, presets `U-`,
# ledger records `E-`/`E2-`, findings `RF-`, workflow events `WE-`, gate evaluations `GE-`, bundles `EB-`,
# check snapshots `H-`, projects `P-`. No pattern of those fullmatches an attempt ID, nor the reverse.
ATTEMPT_ID = re.compile(r"CX-(\d{4,6})")
ATTEMPT_LIMIT = 999_999
TASK_ID = re.compile(r"T-[a-f0-9]{12}")       # as `app.TASK_ID`
DIGEST = re.compile(r"[0-9a-f]{16}")         # `agents.digest` of a frozen step record
FINGERPRINT = re.compile(r"[0-9a-f]{64}")    # `gitops.fingerprint`
SESSION_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
FAILURE_KIND = re.compile(r"[a-z][a-z0-9_]{0,63}")
DRAFT_NAME = re.compile(r"test-draft-\d{2,6}")  # `runfiles.DRAFT_FOLDER`
POLICY_CHARS = 20_000  # the most a bound execution section may take as JSON
ITERATION_LIMIT = 999  # `runs/iteration-NNN`, `agents.step_key`
ORDINAL_LIMIT = 999
CALL_LIMIT = 86_400    # the bounds of a call's turns and timeout (`agents.py`, `core.WORKFLOW_LIMITS`)
BUDGET_LIMIT = 10_000_000
COUNT_LIMIT = 10**15


class InvalidRecord(ValueError):
    """A record this version cannot use. `newer` when it was written by a later schema. The saved value stays as it is."""

    def __init__(self, message: str, *, newer: bool = False):
        super().__init__(message)
        self.newer = newer


class Phase(StrEnum):
    DEVELOP = "develop"
    REVIEW = "review"


ROLE_OF = {Phase.DEVELOP: "developer", Phase.REVIEW: "reviewer"}


class Unit(StrEnum):
    CLAUDE_TURNS = "claude_turns"      # turns of Claude Code; never a unit of Codex
    PROVIDER_CALLS = "provider_calls"  # starts of a provider CLI, any provider
    SECONDS = "seconds"                # elapsed time measured by PatchRondo, rounded up
    TOKENS = "tokens"                  # as a CLI reported them; observed only, never a limit


ENFORCEABLE = frozenset({Unit.CLAUDE_TURNS, Unit.PROVIDER_CALLS, Unit.SECONDS})


class Availability(StrEnum):
    OBSERVED = "observed"              # reported by the CLI's final result or measured by PatchRondo
    UNKNOWN = "unknown"                # not reported: never read as 0
    NOT_APPLICABLE = "not_applicable"  # the unit does not exist for this provider (Claude turns of Codex)


class AttemptStatus(StrEnum):
    STARTING = "starting"  # saved before the CLI is started
    RUNNING = "running"    # the CLI was started
    ENDED = "ended"        # the call returned or failed, and its outcome is known


class AttemptOutcome(StrEnum):
    """How one CLI call ended. Never the status of a phase or of a task."""
    COMPLETED = "COMPLETED"                        # a final answer was received; the workflow still judges it
    TURN_LIMIT_REACHED = "TURN_LIMIT_REACHED"
    INCOMPLETE_HANDOFF = "INCOMPLETE_HANDOFF"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    TIMEOUT = "TIMEOUT"
    INTERRUPTED = "INTERRUPTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    ENVIRONMENT_BLOCKED = "ENVIRONMENT_BLOCKED"
    INVALID_FINAL_OUTPUT = "INVALID_FINAL_OUTPUT"
    UNSUPPORTED_RESUME = "UNSUPPORTED_RESUME"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    UNKNOWN = "UNKNOWN"

    @property
    def answered(self) -> bool:
        """Whether the CLI gave a final answer. Not whether the answer is acceptable."""
        return self is AttemptOutcome.COMPLETED


class Termination(StrEnum):
    """Whether the process of an ended attempt, and what it started, are known to have stopped. Not its outcome."""
    EXITED = "exited"            # the CLI ended by itself
    STOPPED = "stopped"          # PatchRondo stopped it and everything it started (a process group, a job object)
    UNVERIFIED = "unverified"    # stopped, and what it started may still run (no job object; a signal refused)
    NOT_STARTED = "not_started"  # no process was started for it
    RELEASED = "released"        # was uncertain; the user stated that its processes stopped (`ExecutionRecord.release`)


SETTLED = frozenset({Termination.EXITED, Termination.STOPPED, Termination.NOT_STARTED, Termination.RELEASED})


class ContinuationMode(StrEnum):
    NONE = "none"                            # a first call
    CHECKPOINT_RESUME = "checkpoint_resume"  # a new call with a brief of the work so far
    NATIVE_RESUME = "native_resume"          # the provider's session resumed; not validated, not used


class StepStatus(StrEnum):
    """Where the calls of one step stand, read from its attempts. Not the status of the phase or of the task."""
    NOT_STARTED = "not_started"
    RUNNING = "running"                  # its last attempt has not ended (after a crash: uncertain)
    ANSWER_RECEIVED = "answer_received"  # its last attempt ended with a final answer, for the workflow to judge
    STOPPED = "stopped"                  # its last attempt ended without one


class Scope(StrEnum):
    PHASE = "phase"  # the attempts of one step
    TASK = "task"    # every attempt of the task


# `AgentFailure.kind` of a failed agent call, as the adapters, `core._invoke`'s caller and `core._failure` give it
# today. The kinds stay as they are; this table only reads them.
OUTCOME_OF_KIND = {
    "max_turns": AttemptOutcome.TURN_LIMIT_REACHED,  # Claude's own `error_max_turns` result
    "quota": AttemptOutcome.QUOTA_EXHAUSTED,         # recovery.py stays its only retrier
    "timeout": AttemptOutcome.TIMEOUT,
    "interrupted": AttemptOutcome.INTERRUPTED,
    "empty_output": AttemptOutcome.INVALID_FINAL_OUTPUT,
    "invalid_output": AttemptOutcome.INVALID_FINAL_OUTPUT,
    "invalid_review": AttemptOutcome.INVALID_FINAL_OUTPUT,
    "authentication": AttemptOutcome.ENVIRONMENT_BLOCKED,
    "configuration": AttemptOutcome.ENVIRONMENT_BLOCKED,
    "unsupported_option": AttemptOutcome.ENVIRONMENT_BLOCKED,
    # Kinds of the environment check (`repair.PROBLEMS`), raised before a call as `EnvironmentBlocked`.
    "cli_missing": AttemptOutcome.ENVIRONMENT_BLOCKED,
    "git_dubious_ownership": AttemptOutcome.ENVIRONMENT_BLOCKED,
    "sandbox_access_denied": AttemptOutcome.PERMISSION_DENIED,
    "sandbox_cwd_unavailable": AttemptOutcome.ENVIRONMENT_BLOCKED,
    "sandbox_not_read_only": AttemptOutcome.ENVIRONMENT_BLOCKED,
    "sandbox_unverified": AttemptOutcome.ENVIRONMENT_BLOCKED,
    "sandbox_unavailable": AttemptOutcome.ENVIRONMENT_BLOCKED,
    "sandbox_git_failed": AttemptOutcome.ENVIRONMENT_BLOCKED,
    "model_unavailable": AttemptOutcome.PROVIDER_ERROR,
    "agent_error": AttemptOutcome.PROVIDER_ERROR,
    "system_error": AttemptOutcome.UNKNOWN,  # an OSError or RuntimeError: what the call did is not known
}
# A kind a more precise error of the system narrows, never the reverse. `system_error` is what `core._failure` saves
# for any OSError or RuntimeError; a PermissionError among them is the system refusing access (the CLI could not be
# started, or a file of the call could not be written), which the saved kind does not say.
REFINED = {"system_error": frozenset({AttemptOutcome.PERMISSION_DENIED})}
# Kinds of the engine's own pauses: no agent call ends with them, so no attempt does.
ENGINE_KINDS = frozenset({"tests_disabled", "verification_error", "verification_blocked", "stale_tests",
                          "completion_refused", "awaiting_validation", "awaiting_authorization",
                          "acceptance_unreadable", "validation_failed",
                          # Step 04: pauses before a call is started, never the end of one.
                          "attempt_uncertain", "attempt_folder_exists", "attempt_limit_reached",
                          "execution_unreadable",
                          # Step 05: a budget refused the next call before anything was started.
                          "budget_exhausted",
                          # Step 05.1: a recorded task whose record is missing, or whose policy is not known.
                          "execution_record_missing", "execution_policy_missing"})


def outcome_for_failure(kind) -> AttemptOutcome | None:
    """The outcome of a call that failed with `kind`. None for an engine pause; UNKNOWN for a kind this table lacks."""
    if isinstance(kind, str) and kind in ENGINE_KINDS:
        return None
    return OUTCOME_OF_KIND.get(kind, AttemptOutcome.UNKNOWN) if isinstance(kind, str) else AttemptOutcome.UNKNOWN


def outcomes_for_failure(kind) -> frozenset:
    """Every outcome a call that failed with `kind` may be recorded with: its outcome, and what `REFINED` narrows it to."""
    outcome = outcome_for_failure(kind)
    return frozenset() if outcome is None else frozenset({outcome}) | REFINED.get(kind, frozenset())


def outcome_of(failure: BaseException | None) -> tuple[AttemptOutcome, str | None]:
    """How one call ended, and the failure kind kept with it: the one mapping every attempt is ended with.

    `failure` is what the call raised, None when it returned an answer that
    its role accepts. The kind is the one `core._failure` saves as
    `last_error.kind`, read through `OUTCOME_OF_KIND`; no kind or pause text
    changes here. In this order: an answer; an interruption; a timeout of the
    process; a failure that names its kind (an `AgentFailure`: the adapters
    decide it from the CLI's own result first, Claude's `error_max_turns`
    subtype or an `is_error` result, and read output text only after that, and
    never what the agent wrote while it worked); an error of the system,
    narrowed to `PERMISSION_DENIED` for a refused access. Anything else is
    `UNKNOWN`, never an answer: a malformed kind keeps no kind.
    """
    if failure is None:
        return AttemptOutcome.COMPLETED, None
    if isinstance(failure, KeyboardInterrupt):
        return AttemptOutcome.INTERRUPTED, "interrupted"
    if isinstance(failure, subprocess.TimeoutExpired):
        return AttemptOutcome.TIMEOUT, "timeout"
    if hasattr(failure, "kind"):
        kind = failure.kind
        if type(kind) is not str or not FAILURE_KIND.fullmatch(kind):
            return AttemptOutcome.UNKNOWN, None
        outcome = outcome_for_failure(kind)
        return (AttemptOutcome.UNKNOWN, None) if outcome is None else (outcome, kind)
    if isinstance(failure, PermissionError):
        return AttemptOutcome.PERMISSION_DENIED, "system_error"
    if isinstance(failure, (OSError, RuntimeError)):
        return AttemptOutcome.UNKNOWN, "system_error"
    return AttemptOutcome.UNKNOWN, None


def termination_of(outcome: AttemptOutcome, *, failed: bool, observed: bool, started: bool = False,
                   stopped: bool = False, whole: bool = False) -> Termination:
    """Whether a call's process, and what it started, are known to have stopped: the one rule for every attempt.

    `failed`: the call raised. `observed`: the adapter reported its process
    (`providers.Observation`), and then `started` (a process was created),
    `stopped` (PatchRondo had to stop it) and `whole` (a process group or a job
    object took the last signal, and no signal was refused). Agents run
    without a job object on native Windows, so a call stopped there is
    `UNVERIFIED`. An adapter that reports no process is taken at its word only
    for an ending of its own, and a timeout no process showed is never taken
    for a clean end.
    """
    if not failed:
        return Termination.EXITED
    if not observed:
        stopping = outcome in (AttemptOutcome.TIMEOUT, AttemptOutcome.INTERRUPTED, AttemptOutcome.UNKNOWN)
        return Termination.UNVERIFIED if stopping else Termination.EXITED
    if not started:
        found = Termination.NOT_STARTED
    elif stopped:
        found = Termination.STOPPED if whole else Termination.UNVERIFIED
    else:
        found = Termination.EXITED
    if outcome is AttemptOutcome.TIMEOUT and found in (Termination.EXITED, Termination.NOT_STARTED):
        return Termination.UNVERIFIED
    return found


# --- validation -----------------------------------------------------------------------------------------------

def _set(record, name: str, value) -> None:
    object.__setattr__(record, name, value)


def _int(value, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise InvalidRecord(f"{name} must be a whole number from {low} to {high}")
    return value


def _optional_int(value, name: str, low: int, high: int) -> int | None:
    return None if value is None else _int(value, name, low, high)


def _enum(kind, value, name: str):
    if isinstance(value, kind):
        return value
    if type(value) is str:
        try:
            return kind(value)
        except ValueError:
            pass
    raise InvalidRecord(f"{name} must be one of: {', '.join(kind)}")


def _match(value, pattern: re.Pattern, name: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or not pattern.fullmatch(value):
        raise InvalidRecord(f"{name} is not valid")
    return value


def _instant(value, name: str, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if type(value) is not str or len(value) > 40:
        raise InvalidRecord(f"{name} must be a timestamp with its time zone")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        moment = None
    if moment is None or moment.tzinfo is None:
        raise InvalidRecord(f"{name} must be a timestamp with its time zone")
    return moment


def _fields(raw, name: str, fields: tuple[str, ...], versioned: bool = True) -> dict:
    """The fields of a saved record, exactly: a newer version first, then any missing or unexpected field."""
    if not isinstance(raw, dict):
        raise InvalidRecord(f"{name} is not a record")
    if versioned:
        version = raw.get("version")
        if type(version) is not int:
            raise InvalidRecord(f"{name} has no schema version")
        if version > VERSION:
            raise InvalidRecord(f"{name} was written by a newer version (schema {version}); this version reads {VERSION}",
                                newer=True)
        if version != VERSION:
            raise InvalidRecord(f"{name} is of schema version {version}; this version reads {VERSION}")
    expected = set(fields) | ({"version"} if versioned else set())
    missing, unexpected = sorted(expected - set(raw)), sorted(set(raw) - expected)
    if missing or unexpected:
        detail = "; ".join(part for part in (f"missing: {', '.join(missing)}" if missing else "",
                                             f"unexpected: {', '.join(map(str, unexpected))[:200]}" if unexpected else "")
                           if part)
        raise InvalidRecord(f"{name} does not have the fields of schema {VERSION} ({detail})")
    return raw


def _versioned(raw, name: str, shapes: Mapping[int, tuple[str, ...]]) -> tuple[dict, int]:
    """The fields of a record that one of several schema versions may have written: (raw, its version).

    A version above every one in `shapes` is newer (`InvalidRecord(newer=True)`);
    one that is not in `shapes` is refused; the fields must be exactly those of
    its version. A version-1 record is never rewritten as another version.
    """
    if not isinstance(raw, dict):
        raise InvalidRecord(f"{name} is not a record")
    version = raw.get("version")
    if type(version) is not int:
        raise InvalidRecord(f"{name} has no schema version")
    if version > max(shapes):
        raise InvalidRecord(f"{name} was written by a newer version (schema {version}); this version reads "
                            f"{', '.join(map(str, sorted(shapes)))}", newer=True)
    if version not in shapes:
        raise InvalidRecord(f"{name} is of schema version {version}; this version reads "
                            f"{', '.join(map(str, sorted(shapes)))}")
    expected = set(shapes[version]) | {"version"}
    missing, unexpected = sorted(expected - set(raw)), sorted(set(raw) - expected)
    if missing or unexpected:
        detail = "; ".join(part for part in (f"missing: {', '.join(missing)}" if missing else "",
                                             f"unexpected: {', '.join(map(str, unexpected))[:200]}" if unexpected else "")
                           if part)
        raise InvalidRecord(f"{name} does not have the fields of schema {version} ({detail})")
    return raw, version


def _list(value, name: str, limit: int) -> list:
    if not isinstance(value, list) or len(value) > limit:
        raise InvalidRecord(f"{name} must be a list of at most {limit} entries")
    return value


# --- usage and budgets ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Usage:
    """One quantity of one call, in its unit: a value only when observed."""
    unit: Unit
    availability: Availability
    value: int | None = None

    FIELDS = ("unit", "availability", "value")

    def __post_init__(self):
        _set(self, "unit", _enum(Unit, self.unit, "usage unit"))
        _set(self, "availability", _enum(Availability, self.availability, f"availability of {self.unit}"))
        if self.availability is Availability.OBSERVED:
            _int(self.value, f"observed {self.unit}", 0, COUNT_LIMIT)
        elif self.value is not None:
            raise InvalidRecord(f"{self.unit} that is {self.availability} has no value")

    @classmethod
    def unknown(cls, unit: Unit) -> "Usage":
        return cls(unit, Availability.UNKNOWN)

    def to_dict(self) -> dict:
        return {"unit": str(self.unit), "availability": str(self.availability), "value": self.value}

    @classmethod
    def from_dict(cls, raw, name: str) -> "Usage":
        raw = _fields(raw, name, cls.FIELDS, versioned=False)
        return cls(raw["unit"], raw["availability"], raw["value"])

    @classmethod
    def reported(cls, unit: "Unit", value) -> "Usage":
        """What a CLI reported in `unit`: observed only for a whole number in range, otherwise unknown, never 0."""
        if type(value) is int and 0 <= value <= COUNT_LIMIT:
            return cls(unit, Availability.OBSERVED, value)
        return cls.unknown(unit)


@dataclass(frozen=True)
class BudgetLine:
    """What a budget allows in one unit, what was charged to it, and how many charges were unknown."""
    unit: Unit
    limit: int | None = None  # None: not limited in this unit
    used: int = 0
    unknown: int = 0          # calls whose usage in this unit could not be charged

    FIELDS = ("unit", "limit", "used", "unknown")

    def __post_init__(self):
        _set(self, "unit", _enum(Unit, self.unit, "budget unit"))
        if self.limit is not None and self.unit not in ENFORCEABLE:
            raise InvalidRecord(f"{self.unit} are observed only and cannot be limited")
        _optional_int(self.limit, f"limit of {self.unit}", 1, BUDGET_LIMIT)
        _int(self.used, f"used {self.unit}", 0, COUNT_LIMIT)
        _int(self.unknown, f"unknown charges of {self.unit}", 0, COUNT_LIMIT)

    @property
    def remaining(self) -> int | None:
        """What is left; None when not limited. 0 while an unknown charge is in it: compliance cannot be shown."""
        if self.limit is None:
            return None
        return 0 if self.unknown else max(0, self.limit - self.used)

    def allows(self, amount: int) -> bool:
        _int(amount, f"requested {self.unit}", 0, COUNT_LIMIT)
        return self.limit is None or (not self.unknown and self.used + amount <= self.limit)

    def to_dict(self) -> dict:
        return {"unit": str(self.unit), "limit": self.limit, "used": self.used, "unknown": self.unknown}


@dataclass(frozen=True)
class BudgetState:
    """The budget of a step (`PHASE`) or of a task (`TASK`): one line per unit. Calls and seconds are always kept."""
    scope: Scope
    lines: tuple[BudgetLine, ...]

    FIELDS = ("scope", "lines")
    REQUIRED = (Unit.PROVIDER_CALLS, Unit.SECONDS)

    def __post_init__(self):
        _set(self, "scope", _enum(Scope, self.scope, "budget scope"))
        lines = tuple(self.lines) if isinstance(self.lines, (list, tuple)) else None
        if lines is None or not all(isinstance(line, BudgetLine) for line in lines):
            raise InvalidRecord("a budget must hold budget lines")
        units = [line.unit for line in lines]
        if len(set(units)) != len(units):
            raise InvalidRecord("a budget names a unit twice")
        if any(unit not in units for unit in self.REQUIRED):
            raise InvalidRecord("a budget must keep provider calls and seconds")
        _set(self, "lines", tuple(sorted(lines, key=lambda line: list(Unit).index(line.unit))))

    @classmethod
    def open(cls, scope: Scope, limits: Mapping[Unit, int | None]) -> "BudgetState":
        """A budget with nothing charged. Units not named are kept unlimited where they are required."""
        named = {_enum(Unit, unit, "budget unit"): limit for unit, limit in limits.items()}
        for unit in cls.REQUIRED:
            named.setdefault(unit, None)
        return cls(scope, tuple(BudgetLine(unit, limit) for unit, limit in named.items()))

    def line(self, unit: Unit) -> BudgetLine | None:
        return next((line for line in self.lines if line.unit == unit), None)

    def charged(self, charges: Mapping[Unit, int | None], withdrawn: Mapping[Unit, int | None] | None = None) -> "BudgetState":
        """A copy with `charges` added (None: an unknown charge) and `withdrawn` taken back, in the units it keeps."""
        lines = []
        for line in self.lines:
            used, unknown = line.used, line.unknown
            for sign, amounts in ((1, charges), (-1, withdrawn or {})):
                if line.unit in amounts:
                    amount = amounts[line.unit]
                    if amount is None:
                        unknown += sign
                    else:
                        used += sign * _int(amount, f"charge of {line.unit}", 0, COUNT_LIMIT)
            lines.append(replace(line, used=used, unknown=unknown))
        return replace(self, lines=tuple(lines))

    def to_dict(self) -> dict:
        return {"version": VERSION, "scope": str(self.scope), "lines": [line.to_dict() for line in self.lines]}

    @classmethod
    def from_dict(cls, raw, name: str = "budget") -> "BudgetState":
        raw = _fields(raw, name, cls.FIELDS)
        lines = [BudgetLine(**_fields(line, f"{name} line", BudgetLine.FIELDS, versioned=False))
                 for line in _list(raw["lines"], f"{name} lines", len(Unit))]
        return cls(raw["scope"], tuple(lines))


# --- attempts -------------------------------------------------------------------------------------------------

def attempt_number(attempt_id: str) -> int:
    match = ATTEMPT_ID.fullmatch(attempt_id) if type(attempt_id) is str else None
    if match is None or not 1 <= int(match.group(1)) <= ATTEMPT_LIMIT:
        raise InvalidRecord("an attempt ID is not valid")
    return int(match.group(1))


def attempt_id(number: int) -> str:
    return f"CX-{_int(number, 'attempt number', 1, ATTEMPT_LIMIT):04d}"


@dataclass(frozen=True)
class AttemptRecord:
    """One start of a provider CLI for one step, as PatchRondo observed it. Its outcome is the call's, nothing more."""
    attempt_id: str
    task_id: str
    iteration: int
    phase: Phase
    ordinal: int                    # 1 for the first attempt of its step
    provider: str
    settings_digest: str | None     # `digest` of the step's frozen agent settings; None when the step has none
    turn_limit: int | None          # Claude's `--max-turns` of this call; None for Codex
    timeout_seconds: int
    continuation: ContinuationMode
    session: str | None             # the provider's session, opaque and private; None when not reported
    status: AttemptStatus
    started_at: str
    workspace_before: str | None    # `gitops.fingerprint` before the call; None when it could not be taken
    turns: Usage
    tokens: Usage
    outcome: AttemptOutcome | None = None
    failure_kind: str | None = None  # the `AgentFailure.kind` it ended with, as given
    ended_at: str | None = None
    elapsed_seconds: int | None = None  # measured; for an end nobody saw, its timeout (the most it was allowed)
    workspace_after: str | None = None
    termination: Termination | None = None  # once ended: whether its processes are known to have stopped
    # Step 05.1: a request for tests made to the developer of this step (`core.draft_tests`), in the folder
    # `runs/iteration-NNN/<draft>` (`test-draft-KK`). None: a call of the step itself. Saved as schema 2.
    draft: str | None = None

    FIELDS = ("attempt_id", "task_id", "iteration", "phase", "role", "ordinal", "provider", "settings_digest",
              "turn_limit", "timeout_seconds", "continuation", "session", "status", "started_at", "workspace_before",
              "turns", "tokens", "outcome", "failure_kind", "ended_at", "elapsed_seconds", "workspace_after",
              "termination")
    SHAPES = {1: FIELDS, 2: FIELDS + ("draft",)}  # schema 1 never names a request for tests

    def __post_init__(self):
        attempt_number(self.attempt_id)
        if self.draft is not None:
            _match(self.draft, DRAFT_NAME, "folder of a request for tests")
            if _enum(Phase, self.phase, "phase") is not Phase.DEVELOP:
                raise InvalidRecord("a request for tests is made to the developer")
        _match(self.task_id, TASK_ID, "task ID")
        _int(self.iteration, "iteration", 1, ITERATION_LIMIT)
        _set(self, "phase", _enum(Phase, self.phase, "phase"))
        _int(self.ordinal, "ordinal", 1, ORDINAL_LIMIT)
        if type(self.provider) is not str or self.provider not in PROVIDERS:
            raise InvalidRecord(f"provider must be one of: {', '.join(PROVIDERS)}")
        _match(self.settings_digest, DIGEST, "settings digest", optional=True)
        claude = self.provider == "claude"
        if claude:
            _int(self.turn_limit, "turn limit", 1, CALL_LIMIT)
        elif self.turn_limit is not None:
            raise InvalidRecord("a Codex call has no Claude turn limit")
        _int(self.timeout_seconds, "timeout", 1, CALL_LIMIT)
        _set(self, "continuation", _enum(ContinuationMode, self.continuation, "continuation mode"))
        _match(self.session, SESSION_REF, "session reference", optional=True)
        if self.continuation is ContinuationMode.NATIVE_RESUME and self.session is None:
            raise InvalidRecord("a native resume names the session it resumed")
        if self.draft is not None and self.continuation is not ContinuationMode.NONE:
            raise InvalidRecord("a request for tests is never a continuation of its step")
        _set(self, "status", _enum(AttemptStatus, self.status, "attempt status"))
        started = _instant(self.started_at, "start time")
        _match(self.workspace_before, FINGERPRINT, "fingerprint before the call", optional=True)
        for name, unit in (("turns", Unit.CLAUDE_TURNS), ("tokens", Unit.TOKENS)):
            usage = getattr(self, name)
            if not isinstance(usage, Usage) or usage.unit is not unit:
                raise InvalidRecord(f"{name} must be a usage in {unit}")
        if (self.turns.availability is Availability.NOT_APPLICABLE) == claude:
            raise InvalidRecord("Claude turns apply to Claude calls only" if not claude else "a Claude call counts turns")
        if self.tokens.availability is Availability.NOT_APPLICABLE:
            raise InvalidRecord("tokens are observed or unknown")
        if self.status is not AttemptStatus.ENDED:
            if any(value is not None for value in (self.outcome, self.failure_kind, self.ended_at,
                                                   self.elapsed_seconds, self.workspace_after, self.termination)):
                raise InvalidRecord("an attempt that has not ended has no outcome, end, termination or fingerprint after it")
            if Availability.OBSERVED in (self.turns.availability, self.tokens.availability):
                raise InvalidRecord("an attempt that has not ended has observed nothing")
            return
        _set(self, "outcome", _enum(AttemptOutcome, self.outcome, "outcome"))
        _set(self, "termination", _enum(Termination, self.termination, "termination"))
        if self.outcome is AttemptOutcome.COMPLETED and self.termination is not Termination.EXITED:
            raise InvalidRecord("a call that gave its final answer ended by itself")
        if self.outcome is AttemptOutcome.TIMEOUT and self.termination in (Termination.EXITED, Termination.NOT_STARTED):
            raise InvalidRecord("a call that timed out was stopped")
        if self.termination is Termination.NOT_STARTED and any(
                usage.availability is Availability.OBSERVED and usage.value for usage in (self.turns, self.tokens)):
            raise InvalidRecord("a call that never started used nothing")
        if _instant(self.ended_at, "end time") < started:
            raise InvalidRecord("an attempt ends after it starts")
        _int(self.elapsed_seconds, "elapsed seconds", 0, COUNT_LIMIT)
        _match(self.workspace_after, FINGERPRINT, "fingerprint after the call", optional=True)
        if self.failure_kind is not None:
            _match(self.failure_kind, FAILURE_KIND, "failure kind")
            expected = outcome_for_failure(self.failure_kind)
            if expected is None:
                raise InvalidRecord("an engine pause is not the outcome of an agent call")
            if self.outcome not in outcomes_for_failure(self.failure_kind):
                raise InvalidRecord(f"failure kind {self.failure_kind} is the outcome {expected}, not {self.outcome}")
        elif self.outcome is AttemptOutcome.QUOTA_EXHAUSTED or self.outcome is AttemptOutcome.TURN_LIMIT_REACHED:
            raise InvalidRecord(f"{self.outcome} is read from the failure the call ended with")

    @property
    def role(self) -> str:
        return ROLE_OF[self.phase]

    @property
    def number(self) -> int:
        return attempt_number(self.attempt_id)

    @property
    def step_key(self) -> str:
        """The step's key in `agent_runs`, as `agents.step_key` gives it."""
        return f"{self.iteration:03d}:{self.role}"

    @property
    def ended(self) -> bool:
        return self.status is AttemptStatus.ENDED

    @property
    def uncertain(self) -> bool:
        """Whether something this call started may still run: it has no end on record, or it was stopped unverified.

        Found by a later run, an attempt without an end is one whose run was
        lost (a crash, a power cut): whether its CLI was started, and whether
        it stopped, is not known. Neither is ever taken for "not running".
        """
        return not self.ended or self.termination is Termination.UNVERIFIED

    def charges(self) -> dict[Unit, int | None]:
        """What this call costs each unit. Conservative while unknown: the call's turn limit, its timeout.

        None is an unknown charge (tokens not reported). An attempt that has
        not ended is charged what it may still use. A call that never started
        a process (`NOT_STARTED`) costs no provider call.
        """
        charges: dict[Unit, int | None] = {
            Unit.PROVIDER_CALLS: 0 if self.termination is Termination.NOT_STARTED else 1,
            Unit.SECONDS: self.elapsed_seconds if self.ended else self.timeout_seconds,
            Unit.TOKENS: self.tokens.value if self.tokens.availability is Availability.OBSERVED else None,
        }
        if self.provider == "claude":
            charges[Unit.CLAUDE_TURNS] = self.turns.value if self.turns.availability is Availability.OBSERVED \
                else self.turn_limit
        return charges

    def running(self) -> "AttemptRecord":
        if self.status is not AttemptStatus.STARTING:
            raise InvalidRecord("only a starting attempt becomes running")
        return replace(self, status=AttemptStatus.RUNNING)

    def end(self, outcome: AttemptOutcome, *, ended_at: str, elapsed_seconds: int, termination: Termination,
            failure_kind: str | None = None, workspace_after: str | None = None, turns: Usage | None = None,
            tokens: Usage | None = None) -> "AttemptRecord":
        """The same attempt, ended. An attempt ends once."""
        if self.ended:
            raise InvalidRecord("this attempt has already ended")
        return replace(self, status=AttemptStatus.ENDED, outcome=outcome, failure_kind=failure_kind, ended_at=ended_at,
                       elapsed_seconds=elapsed_seconds, workspace_after=workspace_after,
                       turns=turns or self.turns, tokens=tokens or self.tokens, termination=termination)

    def released(self, *, ended_at: str) -> "AttemptRecord":
        """The attempt after the user stated that its processes stopped. Only an uncertain attempt is released.

        One found without an end ends here with the outcome `UNKNOWN` and the
        most it was allowed (its timeout, its turn limit, tokens unknown): what
        it did is not known, and its charges stay what they were. One stopped
        unverified keeps its outcome and everything it observed; only its
        termination changes. Nothing else of an ended attempt is ever changed.
        """
        if not self.uncertain:
            raise InvalidRecord("only an attempt that may still run is released")
        if self.ended:
            return replace(self, termination=Termination.RELEASED)
        return self.end(AttemptOutcome.UNKNOWN, ended_at=ended_at, elapsed_seconds=self.timeout_seconds,
                        termination=Termination.RELEASED)

    def to_dict(self) -> dict:
        return {"version": 1 if self.draft is None else 2, "attempt_id": self.attempt_id, "task_id": self.task_id,
                "iteration": self.iteration,
                "phase": str(self.phase), "role": self.role, "ordinal": self.ordinal, "provider": self.provider,
                "settings_digest": self.settings_digest, "turn_limit": self.turn_limit,
                "timeout_seconds": self.timeout_seconds, "continuation": str(self.continuation), "session": self.session,
                "status": str(self.status), "started_at": self.started_at, "workspace_before": self.workspace_before,
                "turns": self.turns.to_dict(), "tokens": self.tokens.to_dict(),
                "outcome": None if self.outcome is None else str(self.outcome), "failure_kind": self.failure_kind,
                "ended_at": self.ended_at, "elapsed_seconds": self.elapsed_seconds,
                "workspace_after": self.workspace_after,
                "termination": None if self.termination is None else str(self.termination),
                **({} if self.draft is None else {"draft": self.draft})}

    @classmethod
    def from_dict(cls, raw, name: str = "attempt") -> "AttemptRecord":
        raw, version = _versioned(raw, name, cls.SHAPES)
        if version == 2 and raw["draft"] is None:
            raise InvalidRecord(f"{name} of schema 2 names the folder of its request for tests")
        values = {key: raw[key] for key in cls.SHAPES[version] if key not in ("role", "turns", "tokens")}
        record = cls(**values, turns=Usage.from_dict(raw["turns"], f"{name} turns"),
                     tokens=Usage.from_dict(raw["tokens"], f"{name} tokens"))
        if raw["role"] != record.role:
            raise InvalidRecord(f"{name} names the role {str(raw['role'])[:20]} for the phase {record.phase}")
        return record


# --- a step and the task's record -----------------------------------------------------------------------------

@dataclass(frozen=True)
class CallLimits:
    """What each call of a step is allowed, frozen when the step first starts (decision D2), and where it came from.

    Every attempt of the step is started with these values, whatever the
    project's configuration or the agent settings say by then: a resume, a
    retry and a quota recovery never resolve them again, and an agent setting
    frozen or refreshed with other values never replaces them in a call.
    """
    turn_limit: int | None   # Claude's `--max-turns`; None for Codex
    turn_source: "Source"
    timeout_seconds: int
    timeout_source: "Source"

    FIELDS = ("turn_limit", "turn_source", "timeout_seconds", "timeout_source")

    def __post_init__(self):
        _set(self, "turn_source", _enum(Source, self.turn_source, "source of the turn limit"))
        _set(self, "timeout_source", _enum(Source, self.timeout_source, "source of the timeout"))
        if self.turn_limit is None:
            if self.turn_source is not Source.NOT_APPLICABLE:
                raise InvalidRecord("a call without a turn limit has none to come from anywhere")
        else:
            _int(self.turn_limit, "turn limit", 1, CALL_LIMIT)
            if self.turn_source is Source.NOT_APPLICABLE:
                raise InvalidRecord("a turn limit comes from somewhere")
        _int(self.timeout_seconds, "timeout", 1, CALL_LIMIT)
        if self.timeout_source is Source.NOT_APPLICABLE:
            raise InvalidRecord("a timeout comes from somewhere")

    @classmethod
    def of(cls, limits: "EffectiveLimits") -> "CallLimits":
        return cls(limits.call_turns.value, limits.call_turns.source, limits.call_seconds.value, limits.call_seconds.source)

    def to_dict(self) -> dict:
        return {"turn_limit": self.turn_limit, "turn_source": str(self.turn_source),
                "timeout_seconds": self.timeout_seconds, "timeout_source": str(self.timeout_source)}

    @classmethod
    def from_dict(cls, raw, name: str) -> "CallLimits":
        raw = _fields(raw, name, cls.FIELDS, versioned=False)
        return cls(raw["turn_limit"], raw["turn_source"], raw["timeout_seconds"], raw["timeout_source"])


@dataclass(frozen=True)
class StepExecution:
    """The attempts of one step, in order, its frozen call limits and its budget. The budget's charges are its attempts'."""
    iteration: int
    phase: Phase
    provider: str
    profile: "ExecutionProfile"
    call: CallLimits
    budget: BudgetState
    attempts: tuple[AttemptRecord, ...] = ()

    FIELDS = ("iteration", "phase", "role", "provider", "profile", "call", "budget", "attempts")

    def __post_init__(self):
        _int(self.iteration, "iteration", 1, ITERATION_LIMIT)
        _set(self, "phase", _enum(Phase, self.phase, "phase"))
        if type(self.provider) is not str or self.provider not in PROVIDERS:
            raise InvalidRecord(f"provider must be one of: {', '.join(PROVIDERS)}")
        _set(self, "profile", _enum(ExecutionProfile, self.profile, "execution profile"))
        if not isinstance(self.call, CallLimits):
            raise InvalidRecord("a step keeps the limits of its calls")
        if (self.call.turn_limit is not None) != (self.provider == "claude"):
            raise InvalidRecord("a Claude step has a turn limit, a Codex step none")
        if not isinstance(self.budget, BudgetState) or self.budget.scope is not Scope.PHASE:
            raise InvalidRecord("a step has a budget of scope phase")
        if self.provider != "claude" and self.budget.line(Unit.CLAUDE_TURNS) is not None:
            raise InvalidRecord("Claude turns are not a unit of Codex")
        attempts = tuple(self.attempts) if isinstance(self.attempts, (list, tuple)) else None
        if attempts is None or not all(isinstance(item, AttemptRecord) for item in attempts):
            raise InvalidRecord("a step holds attempt records")
        _set(self, "attempts", attempts)
        for index, item in enumerate(attempts, 1):
            if (item.iteration, item.phase, item.provider) != (self.iteration, self.phase, self.provider):
                raise InvalidRecord(f"attempt {item.attempt_id} belongs to another step")
            # A call never has more than its step's frozen limits. Only a continuation may have less (Step 07): what
            # the budgets had left when it was admitted (`ExecutionRecord.sized_call`).
            if item.timeout_seconds > self.call.timeout_seconds or (
                    item.turn_limit is not None and item.turn_limit > self.call.turn_limit):
                raise InvalidRecord(f"attempt {item.attempt_id} was started with more than the limits of its step")
            if (item.turn_limit, item.timeout_seconds) != (self.call.turn_limit, self.call.timeout_seconds) \
                    and item.continuation is ContinuationMode.NONE:
                raise InvalidRecord(f"attempt {item.attempt_id} was not started with the limits of its step")
            if item.ordinal != index:
                raise InvalidRecord("the attempts of a step are numbered 1, 2, … in order")
            if index < len(attempts) and not item.ended:
                raise InvalidRecord("only the last attempt of a step may not have ended")
        numbers = [item.number for item in attempts]
        if numbers != sorted(set(numbers)) or len({item.task_id for item in attempts}) > 1:
            raise InvalidRecord("the attempts of a step have IDs of one task, in increasing order")
        expected = BudgetState(Scope.PHASE, tuple(replace(line, used=0, unknown=0) for line in self.budget.lines))
        for item in attempts:
            expected = expected.charged(item.charges())
        if expected != self.budget:
            raise InvalidRecord("the budget of the step is not what its attempts were charged")

    @property
    def role(self) -> str:
        return ROLE_OF[self.phase]

    @property
    def key(self) -> str:
        return f"{self.iteration:03d}:{self.role}"

    @property
    def last(self) -> AttemptRecord | None:
        return self.attempts[-1] if self.attempts else None

    @property
    def status(self) -> StepStatus:
        last = self.last
        if last is None:
            return StepStatus.NOT_STARTED
        if not last.ended:
            return StepStatus.RUNNING
        return StepStatus.ANSWER_RECEIVED if last.outcome.answered else StepStatus.STOPPED

    @property
    def uncertain(self) -> AttemptRecord | None:
        """The attempt of this step that may still run, if any: only ever its last one."""
        return self.last if self.last is not None and self.last.uncertain else None

    def to_dict(self) -> dict:
        return {"iteration": self.iteration, "phase": str(self.phase), "role": self.role, "provider": self.provider,
                "profile": str(self.profile), "call": self.call.to_dict(), "budget": self.budget.to_dict(),
                "attempts": [item.to_dict() for item in self.attempts]}

    @classmethod
    def from_dict(cls, raw, name: str = "step") -> "StepExecution":
        raw = _fields(raw, name, cls.FIELDS, versioned=False)
        attempts = tuple(AttemptRecord.from_dict(item, f"{name} attempt")
                         for item in _list(raw["attempts"], f"{name} attempts", ORDINAL_LIMIT))
        step = cls(raw["iteration"], raw["phase"], raw["provider"], raw["profile"],
                   CallLimits.from_dict(raw["call"], f"{name} call limits"),
                   BudgetState.from_dict(raw["budget"], f"{name} budget"), attempts)
        if raw["role"] != step.role:
            raise InvalidRecord(f"{name} names the role {str(raw['role'])[:20]} for the phase {step.phase}")
        return step


# --- admission of a call (Step 05) ----------------------------------------------------------------------------

# The order in which the lines of the two budgets are asked; the first that cannot take the call refuses it.
ADMISSION_ORDER = ((Scope.PHASE, Unit.PROVIDER_CALLS), (Scope.PHASE, Unit.CLAUDE_TURNS), (Scope.PHASE, Unit.SECONDS),
                   (Scope.TASK, Unit.PROVIDER_CALLS), (Scope.TASK, Unit.CLAUDE_TURNS), (Scope.TASK, Unit.SECONDS))


@dataclass(frozen=True)
class Refusal:
    """Why a call cannot start: the first limited line, in `ADMISSION_ORDER`, that cannot take what the call may use."""
    scope: Scope
    unit: Unit
    limit: int
    used: int
    unknown: int     # calls whose usage in this unit is unknown: while any is in the line, it admits nothing
    requested: int   # the most the call may use in this unit (one call, its timeout, its turn limit)

    def to_dict(self) -> dict:
        return {"scope": str(self.scope), "unit": str(self.unit), "limit": self.limit, "used": self.used,
                "unknown": self.unknown, "requested": self.requested}


class Inadmissible(InvalidRecord):
    """A new attempt that its step's budget or the task's cannot take. Nothing was added to the record."""

    def __init__(self, refusal: Refusal):
        super().__init__(f"the {refusal.scope} budget cannot admit another call ({refusal.unit}: {refusal.used} of "
                         f"{refusal.limit} used, {refusal.requested} more asked)")
        self.refusal = refusal


def call_request(call: CallLimits) -> dict[Unit, int]:
    """The most one call with these frozen limits may use: what it is charged while it runs, and what it must fit in."""
    request = {Unit.PROVIDER_CALLS: 1, Unit.SECONDS: call.timeout_seconds}
    if call.turn_limit is not None:
        request[Unit.CLAUDE_TURNS] = call.turn_limit
    return request


def smallest_request(provider: str) -> dict[Unit, int]:
    """The least any call of `provider` uses: one call, one second and, for Claude, one turn."""
    request = {Unit.PROVIDER_CALLS: 1, Unit.SECONDS: 1}
    if provider == "claude":
        request[Unit.CLAUDE_TURNS] = 1
    return request


def _refused(budgets: Mapping[Scope, BudgetState], request: Mapping[Unit, int]) -> Refusal | None:
    for scope, unit in ADMISSION_ORDER:
        budget = budgets.get(scope)
        line = budget.line(unit) if budget is not None else None
        if line is None or unit not in request:
            continue
        if not line.allows(request[unit]):
            return Refusal(scope, unit, line.limit, line.used, line.unknown, request[unit])
    return None


@dataclass(frozen=True)
class ExecutionRecord:
    """The execution record of a task: the next attempt number, the task's budget and the current step.

    Attempts of earlier steps are not kept here; the task's budget keeps
    what they were charged. `next_attempt` is past every attempt ID the task
    gave, so an ID is never given twice.
    """
    task_id: str
    next_attempt: int
    budget: BudgetState
    step: StepExecution | None = None
    # Step 05.1: the project's `execution` section as it was when the task was first recorded, validated, with
    # `enabled: true`. Every later step of the task resolves its limits from it, whatever the project says now
    # (`governed`). None in a record saved before the binding existed (schema 1). Saved as schema 2.
    policy: dict | None = None

    FIELDS = ("task_id", "next_attempt", "budget", "step")
    SHAPES = {1: FIELDS, 2: FIELDS + ("policy",)}

    def __post_init__(self):
        _match(self.task_id, TASK_ID, "task ID")
        _int(self.next_attempt, "next attempt number", 1, ATTEMPT_LIMIT + 1)
        if not isinstance(self.budget, BudgetState) or self.budget.scope is not Scope.TASK:
            raise InvalidRecord("a task has a budget of scope task")
        if self.policy is not None:
            _set(self, "policy", bound_policy(self.policy))
        if self.step is None:
            return
        if not isinstance(self.step, StepExecution):
            raise InvalidRecord("the step of a task is a step record")
        for item in self.step.attempts:
            if item.task_id != self.task_id:
                raise InvalidRecord(f"attempt {item.attempt_id} belongs to another task")
            if item.number >= self.next_attempt:
                raise InvalidRecord("the next attempt number would give an ID already in use")
        for line in self.step.budget.lines:
            total = self.budget.line(line.unit)
            if total is not None and (total.used < line.used or total.unknown < line.unknown):
                raise InvalidRecord(f"the task's budget was charged less {line.unit} than its current step")

    @classmethod
    def open(cls, task_id: str, limits: Mapping[Unit, int | None], policy: dict | None = None) -> "ExecutionRecord":
        """A task's first record: its budget with nothing charged, and the execution policy it is bound to."""
        return cls(task_id, 1, BudgetState.open(Scope.TASK, limits), policy=policy)

    def begin_step(self, iteration: int, phase: Phase, provider: str, limits: Mapping[Unit, int | None],
                   call: CallLimits, profile: "ExecutionProfile") -> "ExecutionRecord":
        """A new current step with its call limits frozen. The previous step's attempts must all have ended and none
        may still run; they leave this record, and the task's budget keeps what they were charged."""
        if self.step is not None and self.step.uncertain is not None:
            raise InvalidRecord("the current step has an attempt that may still run")
        step = StepExecution(iteration, phase, provider, profile, call, BudgetState.open(Scope.PHASE, limits))
        return replace(self, step=step)

    def new_attempt(self, *, started_at: str, settings_digest: str | None = None,
                    continuation: ContinuationMode = ContinuationMode.NONE, session: str | None = None,
                    workspace_before: str | None = None, draft: str | None = None,
                    call: CallLimits | None = None) -> tuple["ExecutionRecord", AttemptRecord]:
        """The record with a new `STARTING` attempt of the current step, charged in advance, and that attempt.

        The attempt is started with the step's frozen call limits. None is
        added while an attempt of the step may still run, nor one that the
        step's budget or the task's cannot take (`Inadmissible`, `admission`).
        `draft` names the folder of a request for tests made to this step's
        developer: it is admitted and charged like any call of the step.
        `call` (Step 07) gives a continuation smaller limits than the step's,
        never larger (`sized_call`); it is admitted with those limits.
        """
        step = self.step
        if step is None:
            raise InvalidRecord("there is no current step")
        if step.uncertain is not None:
            raise InvalidRecord("the current step has an attempt that may still run")
        if call is not None and continuation is ContinuationMode.NONE:
            raise InvalidRecord("only a continuation is started with other limits than its step's")
        refusal = self.admission(call)
        if refusal is not None:
            raise Inadmissible(refusal)
        limits = call or step.call
        claude = step.provider == "claude"
        item = AttemptRecord(
            attempt_id(self.next_attempt), self.task_id, step.iteration, step.phase, len(step.attempts) + 1,
            step.provider, settings_digest, limits.turn_limit, limits.timeout_seconds, continuation, session,
            AttemptStatus.STARTING, started_at, workspace_before,
            Usage.unknown(Unit.CLAUDE_TURNS) if claude else Usage(Unit.CLAUDE_TURNS, Availability.NOT_APPLICABLE),
            Usage.unknown(Unit.TOKENS), draft=draft)
        record = replace(self, next_attempt=self.next_attempt + 1, budget=self.budget.charged(item.charges()),
                         step=replace(step, budget=step.budget.charged(item.charges()), attempts=step.attempts + (item,)))
        return record, item

    def admission(self, call: CallLimits | None = None) -> Refusal | None:
        """Whether one more call of the current step fits its budget and the task's. Reads only; None: it does.

        The call is asked for what it may use with the step's frozen limits
        (`call_request`): one provider call, its timeout and, for Claude, its
        turn limit, which is also what it is charged while it runs. Every
        limited line is asked, in `ADMISSION_ORDER`; a line with an unknown
        charge in it admits nothing (`BudgetLine.allows`). The limits are the
        ones frozen in the record, whatever the configuration says now: a
        budget is never raised, lowered or resolved again here. `call`, a
        continuation's smaller limits (`sized_call`), is asked instead; it
        may never be above the step's.
        """
        step = self.step
        if step is None:
            raise InvalidRecord("there is no current step")
        if call is not None and (call.timeout_seconds > step.call.timeout_seconds
                                 or (call.turn_limit is None) != (step.call.turn_limit is None)
                                 or (call.turn_limit is not None and call.turn_limit > step.call.turn_limit)):
            raise InvalidRecord("a call is never given more than the limits frozen with its step")
        return _refused({Scope.PHASE: step.budget, Scope.TASK: self.budget}, call_request(call or step.call))

    def sized_call(self) -> tuple[CallLimits | None, Refusal | None]:
        """The largest call of the current step its budgets admit now: (its limits, None) or (None, why none fits).

        Step 07. Each limit is the step's frozen one, lowered to what every
        limited line of the step's budget and the task's has left in its unit
        (`BudgetLine.remaining`: nothing while an unknown charge is in it):
        Claude turns to the turns left, the timeout to the seconds left. A
        limit is never raised, a budget never changed, and what was charged
        stays as it was (a call that used 41 turns of 40 keeps its 41). No
        call fits when a line has no provider call left, no Claude turn left,
        or fewer seconds than `CONTINUATION_SECONDS` (or the step's timeout,
        when lower): such a call could do nothing useful, so none is made.
        Reads only.
        """
        step = self.step
        if step is None:
            raise InvalidRecord("there is no current step")
        budgets = {Scope.PHASE: step.budget, Scope.TASK: self.budget}
        least = {Unit.PROVIDER_CALLS: 1, Unit.SECONDS: min(CONTINUATION_SECONDS, step.call.timeout_seconds)}
        if step.call.turn_limit is not None:
            least[Unit.CLAUDE_TURNS] = 1
        refusal = _refused(budgets, least)
        if refusal is not None:
            return None, refusal
        turns, seconds = step.call.turn_limit, step.call.timeout_seconds
        for scope, unit in ADMISSION_ORDER:
            line = budgets[scope].line(unit)
            if line is None or line.limit is None:
                continue
            if unit is Unit.CLAUDE_TURNS and turns is not None:
                turns = min(turns, line.remaining)
            elif unit is Unit.SECONDS:
                seconds = min(seconds, line.remaining)
        call = replace(step.call, turn_limit=turns, timeout_seconds=seconds)
        refusal = self.admission(call)  # the lines above admit it: asked again, by the one rule that admits calls
        return (None, refusal) if refusal is not None else (call, None)

    def task_admission(self, request: Mapping[Unit, int]) -> Refusal | None:
        """Whether the task's budget alone could take a call asking for `request`. Reads only; None: it could.

        For a call that is not an attempt of the current step: a step about to
        begin (asked with `smallest_request`, before its limits are resolved;
        its own `admission` decides at its call), and a request for tests."""
        return _refused({Scope.TASK: self.budget}, request)

    def update(self, item: AttemptRecord) -> "ExecutionRecord":
        """The record with the current step's last attempt replaced by a later state of it; charges follow."""
        step = self.step
        last = step.last if step is not None else None
        if last is None or item.attempt_id != last.attempt_id:
            raise InvalidRecord("only the last attempt of the current step can be updated")
        if last.ended:
            raise InvalidRecord("an attempt that has ended is not changed")
        # A call keeps what it started with: its limits and settings are never changed afterwards.
        kept = ("task_id", "ordinal", "started_at", "settings_digest", "turn_limit", "timeout_seconds", "continuation",
                "workspace_before", "draft")
        if any(getattr(item, name) != getattr(last, name) for name in kept):
            raise InvalidRecord("an update keeps the identity, limits and settings of its attempt")
        before, after = last.charges(), item.charges()
        return replace(self, budget=self.budget.charged(after, withdrawn=before),
                       step=replace(step, budget=step.budget.charged(after, withdrawn=before),
                                    attempts=step.attempts[:-1] + (item,)))

    def release(self, attempt: str, *, ended_at: str) -> "ExecutionRecord":
        """The record after the user stated that the processes of `attempt`, the uncertain last one, stopped.

        Charges are unchanged: an attempt found without an end was charged the
        most it was allowed, and keeps that charge.
        """
        step = self.step
        last = step.last if step is not None else None
        if last is None or last.attempt_id != attempt:
            raise InvalidRecord("only the last attempt of the current step can be released")
        item = last.released(ended_at=ended_at)
        before, after = last.charges(), item.charges()
        return replace(self, budget=self.budget.charged(after, withdrawn=before),
                       step=replace(step, budget=step.budget.charged(after, withdrawn=before),
                                    attempts=step.attempts[:-1] + (item,)))

    def to_dict(self) -> dict:
        # A record without a bound policy stays schema 1, as it was saved: nothing is rewritten in a newer shape.
        return {"version": 1 if self.policy is None else 2, "task_id": self.task_id, "next_attempt": self.next_attempt,
                "budget": self.budget.to_dict(), "step": None if self.step is None else self.step.to_dict(),
                **({} if self.policy is None else {"policy": json.loads(json.dumps(self.policy))})}

    @classmethod
    def from_dict(cls, raw, name: str = "execution record") -> "ExecutionRecord":
        raw, version = _versioned(raw, name, cls.SHAPES)
        if version == 2 and raw["policy"] is None:
            raise InvalidRecord(f"{name} of schema 2 names the execution policy its task is bound to")
        step = None if raw["step"] is None else StepExecution.from_dict(raw["step"], f"{name} step")
        return cls(raw["task_id"], raw["next_attempt"], BudgetState.from_dict(raw["budget"], f"{name} budget"), step,
                   policy=raw.get("policy"))


# --- automatic continuation of a step (Steps 07-08) -----------------------------------------------------------
#
# After a call of a recorded step ends without the answer its role needs, one more call of the same step may be
# started at once, with a checkpoint brief (`continuation.py`), smaller limits when the budgets have less left
# (`ExecutionRecord.sized_call`) and a folder of its own. Only two endings are followed (`FOLLOWED`): a developer call
# that reached Claude's own turn limit (Step 07), and a reviewer call whose final answer could not be used as a review
# (Step 08: empty, too large, not the JSON the review needs, or an approval that leaves a criterion without an outcome).
# A review that was read is never followed, whatever its verdict: a request for changes is the workflow going on.
# `next_call` decides from the record alone; `core._continued` adds what only the run can see (how the previous call's
# processes were held, the brief, the files, the environment check).

# The temporary bound of Steps 07-08: automatic continuations of one step, whatever its budgets allow. Without a
# measure of progress (Steps 11-12), a provider that keeps ending the same way must not use up a step's calls one after
# the other. Step 12 replaces this constant with its policy; the budgets still apply below it.
AUTOMATIC_CONTINUATIONS = 1
# The fewest seconds a continuation is started with (or the step's timeout, when lower): with less it could do nothing.
CONTINUATION_SECONDS = 60
# The ending a continuation may follow, by phase: the outcome and the failure kinds the CLI's final result and the
# backend's reading of it gave (`outcome_of`), never what an agent wrote while it worked. Turn limits are Claude's only.
FOLLOWED = {Phase.DEVELOP: (AttemptOutcome.TURN_LIMIT_REACHED, frozenset({"max_turns"})),
            Phase.REVIEW: (AttemptOutcome.INVALID_FINAL_OUTPUT, frozenset({"empty_output", "invalid_output",
                                                                           "invalid_review"}))}


class Declined(StrEnum):
    """Why no automatic continuation follows a call. Fixed codes; the first that applies is given."""
    # The task did not opt in, or the call is not one that may be continued: nothing is said in the history.
    NOT_A_TURN_LIMIT = "not_a_turn_limit"            # a developer call that did not end with Claude's own turn limit
    NOT_AN_UNUSABLE_ANSWER = "not_an_unusable_answer"  # a reviewer call that did not end with an answer it could not use
    POLICY_UNBOUND = "policy_unbound"                # a record of schema 1: no policy of the task to read it from
    ADAPTIVE_OFF = "adaptive_off"                    # `execution.adaptive` of the task's policy is false
    CHECKPOINT_RESUME_OFF = "checkpoint_resume_off"  # `execution.continuation.checkpoint_resume` is false
    # The task opted in, and this continuation is refused: the history says why.
    NOT_THE_DEVELOPER = "not_the_developer"          # Step 07's code for a reviewer's step; no longer given since Step 08
    TEST_REQUEST = "test_request"                    # a request for tests is not a call of the step
    NOT_SETTLED = "not_settled"                      # the call did not end by itself (`Termination.EXITED`)
    RELEASED_ATTEMPT = "released_attempt"            # a call of the step was released by the user: its effects are unknown
    WORKSPACE_CHANGED = "workspace_changed"          # a review call changed the files, or they could not be compared
    LIMIT_REACHED = "limit_reached"                  # `AUTOMATIC_CONTINUATIONS` of the step were made
    BUDGET = "budget"                                # no call fits what the budgets have left (`sized_call`)
    # Decided by `core._next_call` from what the run observed.
    PROCESS_TREE_UNVERIFIED = "process_tree_unverified"  # neither a process group nor a job object held the call
    BRIEF_UNAVAILABLE = "brief_unavailable"          # `continuation.prepare` refused: the brief would not hold


NOT_OPTED_IN = frozenset({Declined.NOT_A_TURN_LIMIT, Declined.NOT_AN_UNUSABLE_ANSWER, Declined.POLICY_UNBOUND,
                          Declined.ADAPTIVE_OFF, Declined.CHECKPOINT_RESUME_OFF})


@dataclass(frozen=True)
class NextCall:
    """Whether the current step's last call is followed at once by a continuation, and with which limits.

    Not a status and not a decision of the gate (`gate.Action.CONTINUE` is
    the workflow going on): only whether one more call of this step may be
    started now. `call` is set when it may; `declined` when it may not, with
    `refusal` when a budget is why. `trigger` is the call it would follow.
    """
    trigger: str | None
    call: CallLimits | None = None
    declined: Declined | None = None
    refusal: Refusal | None = None
    made: int = 0  # automatic continuations the step already made

    @property
    def allowed(self) -> bool:
        return self.call is not None and self.declined is None

    @property
    def considered(self) -> bool:
        """Whether the task opted in and the call could have been continued: then the history records the decision."""
        return self.declined not in NOT_OPTED_IN

    def remaining(self, record: "ExecutionRecord") -> list[dict]:
        """What each limited line of `record`'s two budgets has left, before the next call is charged: plain details."""
        step = record.step
        return [{"scope": str(scope), "unit": str(line.unit), "limit": line.limit, "used": line.used,
                 "unknown": line.unknown, "remaining": line.remaining}
                for scope, budget in ((Scope.PHASE, step.budget), (Scope.TASK, record.budget))
                for line in budget.lines if line.limit is not None]


def next_call(record: ExecutionRecord) -> NextCall:
    """Whether the current step's last call may be followed at once by a continuation. Pure: reads `record` only.

    In this order: the last call ended as its phase allows (`FOLLOWED`): a
    developer call with Claude's own turn limit (`TURN_LIMIT_REACHED`), a
    reviewer call with an answer that could not be used as a review
    (`INVALID_FINAL_OUTPUT` of an empty, too large or unreadable answer);
    the task is bound to a policy (schema 2) whose `adaptive` and
    `continuation.checkpoint_resume` are both true, as the task was bound to
    them, whatever the project says now; the call was not a request for
    tests and ended by itself (`Termination.EXITED`); no call of the step was
    released by the user; for a review, the call left the files as it found
    them, both fingerprints known (a reviewer is read only, and a second
    review must be of the files the first was asked about); fewer than
    `AUTOMATIC_CONTINUATIONS` continuations were made in the step; and a call
    fits what the budgets have left (`sized_call`: Claude turns only for
    Claude, calls and seconds for every provider). The first condition that
    does not hold is the reason.
    """
    step = record.step
    last = step.last if step is not None else None
    outcome, kinds = FOLLOWED[step.phase] if step is not None else FOLLOWED[Phase.DEVELOP]
    if last is None or not last.ended or last.outcome is not outcome or last.failure_kind not in kinds:
        none = Declined.NOT_AN_UNUSABLE_ANSWER if step is not None and step.phase is Phase.REVIEW \
            else Declined.NOT_A_TURN_LIMIT
        return NextCall(None if last is None else last.attempt_id, declined=none)
    trigger = last.attempt_id
    if record.policy is None:
        return NextCall(trigger, declined=Declined.POLICY_UNBOUND)
    settings = ExecutionSettings.parse(record.policy)  # valid: `bound_policy` checked it when the record was made
    if not settings.adaptive:
        return NextCall(trigger, declined=Declined.ADAPTIVE_OFF)
    if not settings.checkpoint_resume:
        return NextCall(trigger, declined=Declined.CHECKPOINT_RESUME_OFF)
    made = sum(1 for item in step.attempts if item.continuation is ContinuationMode.CHECKPOINT_RESUME)
    if last.draft is not None:
        return NextCall(trigger, declined=Declined.TEST_REQUEST, made=made)
    if last.termination is not Termination.EXITED:
        return NextCall(trigger, declined=Declined.NOT_SETTLED, made=made)
    if any(item.termination is Termination.RELEASED for item in step.attempts):
        return NextCall(trigger, declined=Declined.RELEASED_ATTEMPT, made=made)
    if step.phase is Phase.REVIEW and (last.workspace_before is None or last.workspace_after != last.workspace_before):
        return NextCall(trigger, declined=Declined.WORKSPACE_CHANGED, made=made)
    if made >= AUTOMATIC_CONTINUATIONS:
        return NextCall(trigger, declined=Declined.LIMIT_REACHED, made=made)
    call, refusal = record.sized_call()
    if call is None:
        return NextCall(trigger, declined=Declined.BUDGET, refusal=refusal, made=made)
    return NextCall(trigger, call=call, made=made)


# --- reading and writing --------------------------------------------------------------------------------------

def load(state) -> ExecutionRecord | None:
    """The execution record of a task's state; None for a task without one. Raises InvalidRecord, changing nothing.

    Only a state without the key has none: a null, another schema version
    or a field of another shape is refused, and the caller leaves the saved
    value as it is.
    """
    if not isinstance(state, dict):
        raise InvalidRecord("a task state is a JSON object")
    if KEY not in state:
        return None
    return ExecutionRecord.from_dict(state[KEY])


def _refuse_constant(value: str):
    raise InvalidRecord(f"JSON value {value} is not allowed")


def _unique(pairs: list) -> dict:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise InvalidRecord("a JSON object names a key twice")
    return dict(pairs)


def parse(text) -> dict:
    """A JSON object from text, refusing NaN, infinities and repeated keys."""
    if not isinstance(text, str):
        raise InvalidRecord("a record is JSON text")
    try:
        value = json.loads(text, parse_constant=_refuse_constant, object_pairs_hook=_unique)
    except json.JSONDecodeError as exc:
        raise InvalidRecord(f"the record is not valid JSON (line {exc.lineno})") from None
    except RecursionError:
        raise InvalidRecord("the record is nested too deeply") from None
    if not isinstance(value, dict):
        raise InvalidRecord("a record is a JSON object")
    return value


def dumps(record) -> str:
    """The JSON text of a record, keys sorted."""
    return json.dumps(record.to_dict(), sort_keys=True, ensure_ascii=False, allow_nan=False)


# --- configuration (Step 02) ----------------------------------------------------------------------------------
#
# The optional `execution` section of a project's `config.json`:
#
#     "execution": {
#       "enabled": true,                      required, a JSON boolean
#       "profile": "standard",                fast | standard | deep_autonomous | custom; required when enabled
#       "adaptive": false,                    optional, default false
#       "limits": {                           optional; complete for custom; for a named profile it may only narrow
#         "call":  {"developer": {"claude_turns": 40, "seconds": 2700}, "reviewer": {...}},
#         "phase": {"developer": {"claude_turns": 80, "provider_calls": 2, "seconds": 5400}, "reviewer": {...}},
#         "task":  {"provider_calls": 60, "seconds": 162000}
#       },
#       "continuation": {"checkpoint_resume": true, "native_resume": "off"},   optional, these defaults
#       "no_progress_windows": 2              optional, 1-10, default 2
#     }
#
# The section is validated whatever `enabled` says, so a configuration never becomes unusable when it is switched
# on. Its call limits and budgets apply to each step that first starts while it is enabled, frozen with that step
# (Steps 04-05). Since Step 07 `adaptive: true` with `continuation.checkpoint_resume: true`, as bound to a task, lets
# a developer call that reached Claude's turn limit be followed at once by one continuation (`next_call`), and since
# Step 08 a reviewer call whose answer could not be used as a review;
# `no_progress_windows` is not applied by this version. It is read from the project's configuration only: a task
# cannot override it, and there is no global level.

class InvalidSettings(ValueError):
    """An `execution` section this version cannot use. The configuration is refused; nothing is repaired or defaulted."""


class ExecutionProfile(StrEnum):
    """The budgets of Adaptive Execution, chosen under `execution.profile`.

    Not an agent preset (`agents.BUILTIN_PRESETS` sets model and effort, and
    one of them is also called "fast") and not a gate policy (`gate.POLICY`
    is fixed to "standard"): a profile sets limits of calls, steps and tasks,
    nothing else. It never changes a model, a tool, a permission or a sandbox.
    """
    FAST = "fast"
    STANDARD = "standard"
    DEEP_AUTONOMOUS = "deep_autonomous"
    CUSTOM = "custom"


SCALES = ("call", "phase", "task")
SCALE_UNITS = {"call": (Unit.CLAUDE_TURNS, Unit.SECONDS),
               "phase": (Unit.CLAUDE_TURNS, Unit.PROVIDER_CALLS, Unit.SECONDS),
               "task": (Unit.PROVIDER_CALLS, Unit.SECONDS)}
# The most each limit may be, whatever the profile. A call's turns and timeout have the bounds of
# `workflow.claude_max_turns`, `workflow.agent_timeout_seconds` and the agent settings; the calls of a step are
# numbered up to ORDINAL_LIMIT and those of a task up to ATTEMPT_LIMIT; budgets fit a `BudgetLine`.
HARD_LIMITS = {("call", Unit.CLAUDE_TURNS): CALL_LIMIT, ("call", Unit.SECONDS): CALL_LIMIT,
               ("phase", Unit.CLAUDE_TURNS): BUDGET_LIMIT, ("phase", Unit.PROVIDER_CALLS): ORDINAL_LIMIT,
               ("phase", Unit.SECONDS): BUDGET_LIMIT,
               ("task", Unit.PROVIDER_CALLS): ATTEMPT_LIMIT, ("task", Unit.SECONDS): BUDGET_LIMIT}
NO_PROGRESS_WINDOWS = (1, 10)
NATIVE_RESUME = ("off",)  # no provider's own resume has been validated (audit I9, I10): "off" is the only value
# `core.DEFAULT_CONFIG["workflow"]`, which a test compares: the values `initialize` writes into every configuration.
LEGACY_DEFAULTS = {"claude_max_turns": 12, "agent_timeout_seconds": 900}


def _profile(turns: dict, calls: dict, seconds: int, task_calls: int) -> dict:
    """A named profile from the specification's values: turns per call and per step for each role, the timeout of a
    call and the calls of a task. A step's calls are derived (enough calls for its turns, and at least one
    continuation); a step's seconds are its calls times the timeout, and a task's seconds its calls times the timeout,
    so they never limit more than the calls already do."""
    return {"call": {role: {Unit.CLAUDE_TURNS: turns[role][0], Unit.SECONDS: seconds} for role in ROLES},
            "phase": {role: {Unit.CLAUDE_TURNS: turns[role][1], Unit.PROVIDER_CALLS: calls[role],
                             Unit.SECONDS: calls[role] * seconds} for role in ROLES},
            "task": {Unit.PROVIDER_CALLS: task_calls, Unit.SECONDS: task_calls * seconds}}


# Specification §5.1 (revision 1.0, November 8, 2026): Claude turns per call and per step, the timeout of one call,
# the calls of a task. Starting values to calibrate, not a statement of what a CLI or a subscription allows. Its
# rows for iterations and for continuation are not limits here: `workflow.max_iterations` stays the user's (audit I5),
# and continuation is bounded by these budgets.
PROFILES = {
    ExecutionProfile.FAST: _profile({"developer": (25, 25), "reviewer": (12, 12)},
                                    {"developer": 2, "reviewer": 2}, 30 * 60, 20),
    ExecutionProfile.STANDARD: _profile({"developer": (40, 80), "reviewer": (15, 30)},
                                        {"developer": 2, "reviewer": 2}, 45 * 60, 60),
    ExecutionProfile.DEEP_AUTONOMOUS: _profile({"developer": (50, 150), "reviewer": (25, 50)},
                                               {"developer": 3, "reviewer": 2}, 60 * 60, 150),
}


def _name(scale: str, role: str | None, unit: Unit) -> str:
    return f"execution.limits.{scale}.{role + '.' if role else ''}{unit}"


def _object(value, name: str, allowed: tuple[str, ...]) -> dict:
    if not isinstance(value, dict):
        raise InvalidSettings(f"{name} must be a JSON object")
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        raise InvalidSettings(f"{name} has unknown fields: {', '.join(unknown)[:200]}")
    return value


def _setting_int(value, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise InvalidSettings(f"{name} must be a whole number from {low} to {high}")
    return value


def _flag(value, name: str) -> bool:
    if type(value) is not bool:
        raise InvalidSettings(f"{name} must be a JSON boolean (true/false)")
    return value


def _units(raw, scale: str, role: str | None, complete: bool) -> dict:
    units = SCALE_UNITS[scale]
    name = f"execution.limits.{scale}{'.' + role if role else ''}"
    if isinstance(raw, dict) and str(Unit.TOKENS) in raw:
        raise InvalidSettings(f"{name}: tokens are observed only and cannot be limited")
    block = _object(raw, name, tuple(str(unit) for unit in units))
    if complete and len(block) != len(units):
        missing = [str(unit) for unit in units if str(unit) not in block]
        raise InvalidSettings(f"{name} must set every limit of the custom profile (missing: {', '.join(missing)})")
    return {Unit(key): _setting_int(value, _name(scale, role, Unit(key)), 1, HARD_LIMITS[scale, Unit(key)])
            for key, value in block.items()}


def _explicit(raw, complete: bool) -> dict:
    """The limits written under `execution.limits`, typed and within the hard limits, as {(scale, role, unit): n}."""
    block = _object(raw, "execution.limits", SCALES)
    if complete and len(block) != len(SCALES):
        raise InvalidSettings("execution.limits must set call, phase and task for the custom profile")
    found = {}
    for scale, value in block.items():
        if scale == "task":
            found.update({(scale, None, unit): n for unit, n in _units(value, scale, None, complete).items()})
            continue
        roles = _object(value, f"execution.limits.{scale}", ROLES)
        if complete and len(roles) != len(ROLES):
            raise InvalidSettings(f"execution.limits.{scale} must set both roles for the custom profile")
        for role, units in roles.items():
            found.update({(scale, role, unit): n for unit, n in _units(units, scale, role, complete).items()})
    return found


@dataclass(frozen=True)
class ExecutionLimits:
    """The limits of a profile in their units: a call and a step (`phase`) per role, and the task."""
    call: Mapping[str, Mapping[Unit, int]]
    phase: Mapping[str, Mapping[Unit, int]]
    task: Mapping[Unit, int]

    @classmethod
    def of(cls, table: Mapping) -> "ExecutionLimits":
        return cls(*(MappingProxyType({role: MappingProxyType(dict(table[scale][role])) for role in ROLES})
                     for scale in ("call", "phase")), MappingProxyType(dict(table["task"])))

    def value(self, scale: str, role: str | None, unit: Unit) -> int:
        return self.task[unit] if scale == "task" else getattr(self, scale)[role][unit]

    def check(self) -> "ExecutionLimits":
        """Refuse limits that contradict each other: a call above its step, a step above the task."""
        for role in ROLES:
            for (small, large) in ((("call", role, Unit.CLAUDE_TURNS), ("phase", role, Unit.CLAUDE_TURNS)),
                                   (("call", role, Unit.SECONDS), ("phase", role, Unit.SECONDS)),
                                   (("phase", role, Unit.PROVIDER_CALLS), ("task", None, Unit.PROVIDER_CALLS)),
                                   (("phase", role, Unit.SECONDS), ("task", None, Unit.SECONDS))):
                if self.value(*small) > self.value(*large):
                    raise InvalidSettings(f"execution limits are inconsistent: {_name(*small)} ({self.value(*small)}) "
                                          f"is above {_name(*large)} ({self.value(*large)})")
        return self

    def to_dict(self) -> dict:
        return {"call": {role: {str(unit): n for unit, n in self.call[role].items()} for role in ROLES},
                "phase": {role: {str(unit): n for unit, n in self.phase[role].items()} for role in ROLES},
                "task": {str(unit): n for unit, n in self.task.items()}}


for _limits in PROFILES.values():  # the table holds itself to the rules a configuration is held to
    ExecutionLimits.of(_limits).check()


@dataclass(frozen=True)
class ExecutionSettings:
    """A validated `execution` section. `limits` are those in effect for its profile; None without a profile."""
    enabled: bool
    profile: ExecutionProfile | None
    adaptive: bool
    limits: ExecutionLimits | None
    explicit: frozenset   # (scale, role or None, unit) of every limit written under `execution.limits`
    checkpoint_resume: bool
    native_resume: str
    no_progress_windows: int

    FIELDS = ("enabled", "profile", "adaptive", "limits", "continuation", "no_progress_windows")

    @classmethod
    def parse(cls, raw) -> "ExecutionSettings":
        section = _object(raw, "execution", cls.FIELDS)
        if "enabled" not in section:
            raise InvalidSettings("execution.enabled is required")
        enabled = _flag(section["enabled"], "execution.enabled")
        profile = None
        if "profile" in section:
            if type(section["profile"]) is not str or section["profile"] not in set(ExecutionProfile):
                raise InvalidSettings(f"execution.profile must be one of: {', '.join(ExecutionProfile)}")
            profile = ExecutionProfile(section["profile"])
        elif enabled:
            raise InvalidSettings("execution.profile is required when execution is enabled: enabling it chooses the "
                                  "limits of a profile")
        adaptive = _flag(section.get("adaptive", False), "execution.adaptive")
        custom = profile is ExecutionProfile.CUSTOM
        if "limits" in section and profile is None:
            raise InvalidSettings("execution.limits need an execution.profile")
        if custom and "limits" not in section:
            raise InvalidSettings("the custom profile needs execution.limits with every limit")
        explicit = _explicit(section["limits"], custom) if "limits" in section else {}
        limits = None
        if custom:
            limits = ExecutionLimits.of(_table(explicit)).check()
        elif profile is not None:
            base = PROFILES[profile]
            for (scale, role, unit), value in explicit.items():
                allowed = base[scale][unit] if scale == "task" else base[scale][role][unit]
                if value > allowed:
                    raise InvalidSettings(f"{_name(scale, role, unit)} ({value}) is above the {profile} profile's "
                                          f"{allowed}: a named profile can only be narrowed; use the custom profile "
                                          "for larger limits")
            limits = ExecutionLimits.of(_table(explicit, base)).check()
        continuation = _object(section.get("continuation", {}), "execution.continuation",
                               ("checkpoint_resume", "native_resume"))
        checkpoint = _flag(continuation.get("checkpoint_resume", True), "execution.continuation.checkpoint_resume")
        native = continuation.get("native_resume", "off")
        if native not in NATIVE_RESUME or type(native) is not str:
            raise InvalidSettings("execution.continuation.native_resume can only be \"off\": no provider's own session "
                                  "resume has been validated")
        windows = _setting_int(section.get("no_progress_windows", 2), "execution.no_progress_windows",
                               *NO_PROGRESS_WINDOWS)
        return cls(enabled, profile, adaptive, limits, frozenset(explicit), checkpoint, native, windows)


def _table(explicit: Mapping, base: Mapping | None = None) -> dict:
    """A table of limits: `base` (a profile) with the explicit values written over it."""
    table = {scale: ({role: dict(base[scale][role]) if base else {} for role in ROLES} if scale != "task"
                     else dict(base[scale]) if base else {}) for scale in SCALES}
    for (scale, role, unit), value in explicit.items():
        (table[scale] if scale == "task" else table[scale][role])[unit] = value
    return table


def settings_of(cfg) -> ExecutionSettings | None:
    """The `execution` section of a configuration, validated; None when the configuration has none.

    Only a configuration without the key has none: a null or a section of
    another shape raises InvalidSettings. The configuration is not changed.
    """
    if not isinstance(cfg, dict):
        raise InvalidSettings("a configuration is a JSON object")
    return ExecutionSettings.parse(cfg[KEY]) if KEY in cfg else None


def bound_policy(raw) -> dict:
    """A copy of the `execution` section a task is bound to: JSON only, bounded, valid and enabled. Raises InvalidRecord.

    Kept in the task's record from its first recorded step (Step 05.1): the
    policy and the task's budget govern every later step of that task, so
    turning execution off or choosing another profile in the project applies
    to tasks recorded afterwards, never to this one.
    """
    try:
        text = json.dumps(raw, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise InvalidRecord("the execution policy of the task is not plain JSON") from None
    if len(text) > POLICY_CHARS:
        raise InvalidRecord(f"the execution policy of the task is longer than {POLICY_CHARS} characters")
    policy = json.loads(text)
    try:
        settings = ExecutionSettings.parse(policy)
    except InvalidSettings as exc:
        raise InvalidRecord(f"the execution policy of the task cannot be used: {exc}") from None
    if not settings.enabled:
        raise InvalidRecord("the execution policy a task is bound to is enabled")
    return policy


def governed(cfg: Mapping, policy: dict) -> dict:
    """The configuration the steps of a bound task are resolved with: the project's, with the task's own policy.

    Only the `execution` section is the task's: workflow values and agent
    settings still come from the configuration in effect, and `resolve` only
    ever lets them narrow a limit.
    """
    return {**cfg, KEY: bound_policy(policy)}


# --- effective limits -----------------------------------------------------------------------------------------

class Source(StrEnum):
    """Where an effective limit comes from."""
    WORKFLOW = "workflow"                  # `workflow.claude_max_turns` or `workflow.agent_timeout_seconds` of the project
    TASK_OVERRIDE = "task_override"        # `workflow.agent_timeout_seconds` set by the task (`core.TASK_OVERRIDES`)
    AGENT_SETTINGS = "agent_settings"      # the step's `max_turns` or `timeout_seconds` (global, project or task level)
    PROFILE = "profile"                    # the value of the execution profile
    EXECUTION_LIMITS = "execution_limits"  # a value written under `execution.limits`
    NOT_APPLICABLE = "not_applicable"      # Claude turns of a Codex call


@dataclass(frozen=True)
class Limit:
    value: int | None
    source: Source
    level: str | None = None  # the level of an agent setting, when the caller knows it

    def to_dict(self) -> dict:
        return {"value": self.value, "source": str(self.source), **({"level": self.level} if self.level else {})}


@dataclass(frozen=True)
class Note:
    """A configured or default value that the execution profile did not keep, said so instead of silently."""
    setting: str
    value: int
    effective: int
    reason: str  # "default_replaced": PatchRondo's default, replaced by the profile; "capped": above the profile's limit

    def to_dict(self) -> dict:
        return {"setting": self.setting, "value": self.value, "effective": self.effective, "reason": self.reason}


@dataclass(frozen=True)
class EffectiveLimits:
    """The limits of one step of one role, resolved. `phase` and `task` are None while execution is off: no budget."""
    enabled: bool
    profile: ExecutionProfile | None
    provider: str
    role: str
    call_turns: Limit       # Claude's `--max-turns`; NOT_APPLICABLE with no value for Codex
    call_seconds: Limit     # the timeout of one call
    phase: Mapping[Unit, Limit] | None
    task: Mapping[Unit, Limit] | None
    notes: tuple[Note, ...] = ()

    def phase_limits(self) -> dict[Unit, int] | None:
        """For `BudgetState.open(Scope.PHASE, …)` and `ExecutionRecord.begin_step`."""
        return None if self.phase is None else {unit: limit.value for unit, limit in self.phase.items()}

    def task_limits(self) -> dict[Unit, int] | None:
        """For `BudgetState.open(Scope.TASK, …)` and `ExecutionRecord.open`."""
        return None if self.task is None else {unit: limit.value for unit, limit in self.task.items()}

    def to_dict(self) -> dict:
        def scale(limits):
            return None if limits is None else {str(unit): limit.to_dict() for unit, limit in limits.items()}
        return {"enabled": self.enabled, "profile": None if self.profile is None else str(self.profile),
                "provider": self.provider, "role": self.role, "call_turns": self.call_turns.to_dict(),
                "call_seconds": self.call_seconds.to_dict(), "phase": scale(self.phase), "task": scale(self.task),
                "notes": [note.to_dict() for note in self.notes]}


def _agent_value(values: Mapping, key: str) -> int | None:
    value = values.get(key)
    if value is None:
        return None
    return _setting_int(value, f"agent setting {key}", 1, CALL_LIMIT)


def resolve(cfg: Mapping, provider: str, role: str, agent_values: Mapping | None = None, *,
            agent_sources: Mapping | None = None, overrides: Mapping | None = None) -> EffectiveLimits:
    """The effective limits of a step: deterministic, from what is given, reading and writing nothing.

    `cfg` is the configuration in effect for the task (`core.effective_config`);
    `agent_values` the step's agent settings (a thawed frozen record, or
    `agents.resolve(...)["values"]`), of which only `max_turns` and
    `timeout_seconds` are read; `agent_sources` where they came from;
    `overrides` the task's validated overrides, which tell a timeout the task
    set from the project's.

    Off (no section, or `enabled: false`): a call's turns and timeout are what
    the adapter passes today (`providers.OfficialCLI._step`): the agent
    setting when it is set, otherwise the workflow's value. No step or task
    budget exists.

    On: the profile (or `execution.limits`) gives the limits of the call, the
    step and the task. A call's turns and timeout are the smallest of that
    value, an agent setting that is set, and a workflow value that was
    configured: one that differs from what `initialize` writes, or a timeout
    the task overrides. A workflow value equal to PatchRondo's default is
    replaced by the profile, which is what enabling a profile opts into. A
    profile never raises what was configured; each value it replaced or
    capped is listed in `notes`.
    """
    if provider not in PROVIDERS or role not in ROLES:
        raise InvalidSettings("invalid provider or role")
    values, sources = agent_values or {}, agent_sources or {}
    workflow = cfg.get("workflow") if isinstance(cfg, Mapping) else None
    if not isinstance(workflow, Mapping):
        raise InvalidSettings("the configuration has no workflow section")
    claude = provider == "claude"
    agent_turns = _agent_value(values, "max_turns")
    if agent_turns is not None and not claude:
        raise InvalidSettings("Codex has no Claude turns: an agent setting max_turns is for Claude only")
    agent_seconds = _agent_value(values, "timeout_seconds")
    workflow_turns = _setting_int(workflow.get("claude_max_turns"), "workflow.claude_max_turns", 1, CALL_LIMIT)
    workflow_seconds = _setting_int(workflow.get("agent_timeout_seconds"), "workflow.agent_timeout_seconds", 1,
                                    CALL_LIMIT)
    overridden = "agent_timeout_seconds" in ((overrides or {}).get("workflow") or {})
    timeout_source = Source.TASK_OVERRIDE if overridden else Source.WORKFLOW
    turns_agent = Limit(agent_turns, Source.AGENT_SETTINGS, sources.get("max_turns"))
    seconds_agent = Limit(agent_seconds, Source.AGENT_SETTINGS, sources.get("timeout_seconds"))
    not_applicable = Limit(None, Source.NOT_APPLICABLE)
    chosen = settings_of(cfg)
    if chosen is None or not chosen.enabled:
        turns = (turns_agent if agent_turns is not None else Limit(workflow_turns, Source.WORKFLOW)) if claude \
            else not_applicable
        seconds = seconds_agent if agent_seconds is not None else Limit(workflow_seconds, timeout_source)
        return EffectiveLimits(False, None, provider, role, turns, seconds, None, None)

    limits = chosen.limits

    def given(scale, unit, owner):
        source = Source.EXECUTION_LIMITS if chosen.profile is ExecutionProfile.CUSTOM or \
            (scale, owner, unit) in chosen.explicit else Source.PROFILE
        return Limit(limits.value(scale, owner, unit), source)

    notes: list[Note] = []

    def narrowest(base: Limit, agent: Limit, configured: Limit, default: bool, setting: str, agent_setting: str) -> Limit:
        # A workflow value at PatchRondo's default is no restriction of the user's: the profile replaces it.
        candidates = [limit for limit in (agent, None if default else configured) if limit and limit.value is not None]
        effective = min([*candidates, base], key=lambda limit: limit.value)  # the first of equal values: the user's
        # Said only when the default was in effect before: with an agent setting set, the workflow value was not used.
        if default and agent.value is None and configured.value != effective.value:
            notes.append(Note(setting, configured.value, effective.value, "default_replaced"))
        for limit, name in ((agent, agent_setting), (configured, setting)):
            if limit in candidates and limit.value > effective.value:
                notes.append(Note(name, limit.value, effective.value, "capped"))
        return effective

    turns = narrowest(given("call", Unit.CLAUDE_TURNS, role), turns_agent, Limit(workflow_turns, Source.WORKFLOW),
                      workflow_turns == LEGACY_DEFAULTS["claude_max_turns"], "workflow.claude_max_turns",
                      "agent setting max_turns") if claude else not_applicable
    seconds = narrowest(given("call", Unit.SECONDS, role), seconds_agent, Limit(workflow_seconds, timeout_source),
                        not overridden and workflow_seconds == LEGACY_DEFAULTS["agent_timeout_seconds"],
                        "workflow.agent_timeout_seconds", "agent setting timeout_seconds")
    phase = {unit: given("phase", unit, role) for unit in SCALE_UNITS["phase"] if claude or unit is not Unit.CLAUDE_TURNS}
    task = {unit: given("task", unit, None) for unit in SCALE_UNITS["task"]}
    return EffectiveLimits(True, chosen.profile, provider, role, turns, seconds, MappingProxyType(phase),
                           MappingProxyType(task), tuple(notes))
