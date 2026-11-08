"""Records of agent execution: the data contracts of Phase 2.5, Adaptive Agent Execution (Step 01).

Nothing imports this module yet. It defines what later steps will save and
read; it calls no agent, runs nothing, reads no file and writes nothing. The
workflow (`core.run_task`), the command of a step (`agents.command`), the
adapters (`providers.py`), quota recovery and the gate do not know it.

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

The outcome of an attempt describes the CLI call only. `COMPLETED` means that
the CLI gave a final answer: the workflow still parses and judges it, a phase
ends only when `run_task` moves on, and a task is done only through
`core._complete` with the gate's `PASS`. No record here has a field for the
status of a task or for the end of a phase.

Budgets name their unit. Claude turns, provider calls and seconds can be
enforced; tokens are only observed and never limited. A Codex call has no
Claude turns (`NOT_APPLICABLE`), and usage a CLI did not report is
`UNKNOWN`, never 0. Where a call's usage is unknown, its charge is the most
the call was allowed (its turn limit, its timeout); a limited budget with an
uncharged unknown left in it allows nothing more.

Records are versioned (schema 1). Loading is strict: an unknown or missing
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
from typing import Mapping

from .agents import PROVIDERS

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
# Kinds of the engine's own pauses: no agent call ends with them, so no attempt does.
ENGINE_KINDS = frozenset({"tests_disabled", "verification_error", "verification_blocked", "stale_tests",
                          "completion_refused", "awaiting_validation", "awaiting_authorization",
                          "acceptance_unreadable", "validation_failed"})


def outcome_for_failure(kind) -> AttemptOutcome | None:
    """The outcome of a call that failed with `kind`. None for an engine pause; UNKNOWN for a kind this table lacks."""
    if isinstance(kind, str) and kind in ENGINE_KINDS:
        return None
    return OUTCOME_OF_KIND.get(kind, AttemptOutcome.UNKNOWN) if isinstance(kind, str) else AttemptOutcome.UNKNOWN


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
    elapsed_seconds: int | None = None
    workspace_after: str | None = None

    FIELDS = ("attempt_id", "task_id", "iteration", "phase", "role", "ordinal", "provider", "settings_digest",
              "turn_limit", "timeout_seconds", "continuation", "session", "status", "started_at", "workspace_before",
              "turns", "tokens", "outcome", "failure_kind", "ended_at", "elapsed_seconds", "workspace_after")

    def __post_init__(self):
        attempt_number(self.attempt_id)
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
                                                   self.elapsed_seconds, self.workspace_after)):
                raise InvalidRecord("an attempt that has not ended has no outcome, end or fingerprint after it")
            if Availability.OBSERVED in (self.turns.availability, self.tokens.availability):
                raise InvalidRecord("an attempt that has not ended has observed nothing")
            return
        _set(self, "outcome", _enum(AttemptOutcome, self.outcome, "outcome"))
        if _instant(self.ended_at, "end time") < started:
            raise InvalidRecord("an attempt ends after it starts")
        _int(self.elapsed_seconds, "elapsed seconds", 0, COUNT_LIMIT)
        _match(self.workspace_after, FINGERPRINT, "fingerprint after the call", optional=True)
        if self.failure_kind is not None:
            _match(self.failure_kind, FAILURE_KIND, "failure kind")
            expected = outcome_for_failure(self.failure_kind)
            if expected is None:
                raise InvalidRecord("an engine pause is not the outcome of an agent call")
            if expected is not self.outcome:
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

    def charges(self) -> dict[Unit, int | None]:
        """What this call costs each unit. Conservative while unknown: the call's turn limit, its timeout.

        None is an unknown charge (tokens not reported). An attempt that has
        not ended is charged what it may still use.
        """
        charges: dict[Unit, int | None] = {
            Unit.PROVIDER_CALLS: 1,
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

    def end(self, outcome: AttemptOutcome, *, ended_at: str, elapsed_seconds: int, failure_kind: str | None = None,
            workspace_after: str | None = None, turns: Usage | None = None, tokens: Usage | None = None) -> "AttemptRecord":
        """The same attempt, ended. An attempt ends once."""
        if self.ended:
            raise InvalidRecord("this attempt has already ended")
        return replace(self, status=AttemptStatus.ENDED, outcome=outcome, failure_kind=failure_kind, ended_at=ended_at,
                       elapsed_seconds=elapsed_seconds, workspace_after=workspace_after,
                       turns=turns or self.turns, tokens=tokens or self.tokens)

    def to_dict(self) -> dict:
        return {"version": VERSION, "attempt_id": self.attempt_id, "task_id": self.task_id, "iteration": self.iteration,
                "phase": str(self.phase), "role": self.role, "ordinal": self.ordinal, "provider": self.provider,
                "settings_digest": self.settings_digest, "turn_limit": self.turn_limit,
                "timeout_seconds": self.timeout_seconds, "continuation": str(self.continuation), "session": self.session,
                "status": str(self.status), "started_at": self.started_at, "workspace_before": self.workspace_before,
                "turns": self.turns.to_dict(), "tokens": self.tokens.to_dict(),
                "outcome": None if self.outcome is None else str(self.outcome), "failure_kind": self.failure_kind,
                "ended_at": self.ended_at, "elapsed_seconds": self.elapsed_seconds,
                "workspace_after": self.workspace_after}

    @classmethod
    def from_dict(cls, raw, name: str = "attempt") -> "AttemptRecord":
        raw = _fields(raw, name, cls.FIELDS)
        values = {key: raw[key] for key in cls.FIELDS if key not in ("role", "turns", "tokens")}
        record = cls(**values, turns=Usage.from_dict(raw["turns"], f"{name} turns"),
                     tokens=Usage.from_dict(raw["tokens"], f"{name} tokens"))
        if raw["role"] != record.role:
            raise InvalidRecord(f"{name} names the role {str(raw['role'])[:20]} for the phase {record.phase}")
        return record


# --- a step and the task's record -----------------------------------------------------------------------------

@dataclass(frozen=True)
class StepExecution:
    """The attempts of one step, in order, and its budget. The budget's charges are those of its attempts."""
    iteration: int
    phase: Phase
    provider: str
    budget: BudgetState
    attempts: tuple[AttemptRecord, ...] = ()

    FIELDS = ("iteration", "phase", "role", "provider", "budget", "attempts")

    def __post_init__(self):
        _int(self.iteration, "iteration", 1, ITERATION_LIMIT)
        _set(self, "phase", _enum(Phase, self.phase, "phase"))
        if type(self.provider) is not str or self.provider not in PROVIDERS:
            raise InvalidRecord(f"provider must be one of: {', '.join(PROVIDERS)}")
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

    def to_dict(self) -> dict:
        return {"iteration": self.iteration, "phase": str(self.phase), "role": self.role, "provider": self.provider,
                "budget": self.budget.to_dict(), "attempts": [item.to_dict() for item in self.attempts]}

    @classmethod
    def from_dict(cls, raw, name: str = "step") -> "StepExecution":
        raw = _fields(raw, name, cls.FIELDS, versioned=False)
        attempts = tuple(AttemptRecord.from_dict(item, f"{name} attempt")
                         for item in _list(raw["attempts"], f"{name} attempts", ORDINAL_LIMIT))
        step = cls(raw["iteration"], raw["phase"], raw["provider"], BudgetState.from_dict(raw["budget"], f"{name} budget"),
                   attempts)
        if raw["role"] != step.role:
            raise InvalidRecord(f"{name} names the role {str(raw['role'])[:20]} for the phase {step.phase}")
        return step


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

    FIELDS = ("task_id", "next_attempt", "budget", "step")

    def __post_init__(self):
        _match(self.task_id, TASK_ID, "task ID")
        _int(self.next_attempt, "next attempt number", 1, ATTEMPT_LIMIT + 1)
        if not isinstance(self.budget, BudgetState) or self.budget.scope is not Scope.TASK:
            raise InvalidRecord("a task has a budget of scope task")
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
    def open(cls, task_id: str, limits: Mapping[Unit, int | None]) -> "ExecutionRecord":
        return cls(task_id, 1, BudgetState.open(Scope.TASK, limits))

    def begin_step(self, iteration: int, phase: Phase, provider: str,
                   limits: Mapping[Unit, int | None]) -> "ExecutionRecord":
        """A new current step. The attempts of the previous one must all have ended; they leave this record."""
        if self.step is not None and self.step.last is not None and not self.step.last.ended:
            raise InvalidRecord("the current step still has an attempt that has not ended")
        step = StepExecution(iteration, phase, provider, BudgetState.open(Scope.PHASE, limits))
        return replace(self, step=step)

    def new_attempt(self, *, started_at: str, turn_limit: int | None, timeout_seconds: int,
                    settings_digest: str | None = None, continuation: ContinuationMode = ContinuationMode.NONE,
                    session: str | None = None, workspace_before: str | None = None) -> tuple["ExecutionRecord", AttemptRecord]:
        """The record with a new `STARTING` attempt of the current step, charged in advance, and that attempt."""
        step = self.step
        if step is None:
            raise InvalidRecord("there is no current step")
        if step.last is not None and not step.last.ended:
            raise InvalidRecord("the current step still has an attempt that has not ended")
        claude = step.provider == "claude"
        item = AttemptRecord(
            attempt_id(self.next_attempt), self.task_id, step.iteration, step.phase, len(step.attempts) + 1,
            step.provider, settings_digest, turn_limit, timeout_seconds, continuation, session, AttemptStatus.STARTING,
            started_at, workspace_before,
            Usage.unknown(Unit.CLAUDE_TURNS) if claude else Usage(Unit.CLAUDE_TURNS, Availability.NOT_APPLICABLE),
            Usage.unknown(Unit.TOKENS))
        record = replace(self, next_attempt=self.next_attempt + 1, budget=self.budget.charged(item.charges()),
                         step=replace(step, budget=step.budget.charged(item.charges()), attempts=step.attempts + (item,)))
        return record, item

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
                "workspace_before")
        if any(getattr(item, name) != getattr(last, name) for name in kept):
            raise InvalidRecord("an update keeps the identity, limits and settings of its attempt")
        before, after = last.charges(), item.charges()
        return replace(self, budget=self.budget.charged(after, withdrawn=before),
                       step=replace(step, budget=step.budget.charged(after, withdrawn=before),
                                    attempts=step.attempts[:-1] + (item,)))

    def to_dict(self) -> dict:
        return {"version": VERSION, "task_id": self.task_id, "next_attempt": self.next_attempt,
                "budget": self.budget.to_dict(), "step": None if self.step is None else self.step.to_dict()}

    @classmethod
    def from_dict(cls, raw, name: str = "execution record") -> "ExecutionRecord":
        raw = _fields(raw, name, cls.FIELDS)
        step = None if raw["step"] is None else StepExecution.from_dict(raw["step"], f"{name} step")
        return cls(raw["task_id"], raw["next_attempt"], BudgetState.from_dict(raw["budget"], f"{name} budget"), step)


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
