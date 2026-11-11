"""Native session resume of the provider CLIs: what is known, what is kept, and when it may be used (Phase 2.5, Step 10).

A continuation of a step (Steps 07-08) is a new call with a checkpoint brief
(`CHECKPOINT_RESUME`). A native resume (`NATIVE_RESUME`) would instead ask
the provider's CLI to go on in the conversation of the call it follows.
This module decides nothing on its own and starts nothing. It holds four
things, each conservative:

1. What an installed CLI can do (`Capability`), read only from what that
   binary printed for `--version` and `--help` (`claude_capability`,
   `codex_capability`). Documentation is never taken as proof that a given
   binary has a feature: what its help does not show is `UNKNOWN`, and an
   unknown capability is never used. The probe commands
   (`PROBE_COMMANDS`, run through `providers.probe`) take no prompt and
   create no session.
2. The session a call reported (`captured`): only from the typed fields of
   the CLI's own structured output (Claude's `system`/`init` and `result`
   lines, `session_id`; Codex's `thread.started` line, `thread_id`), never
   from what an agent wrote. An ID is a lowercase UUID; one missing,
   malformed or contradicted by another line of the same call is not used.
3. Where it is kept. The provider's ID stays in the attempt's private launch
   record (`reconciliation.MARKER`, `stored`), beside the PIDs, and never
   reaches the history, a report, the page, a bundle or a log. The task's
   state keeps only an opaque reference (`reference`): a digest bound to the
   task, the attempt, the provider and the role, which only proves that the
   kept ID is the one that call reported. Logs and error messages mask both
   fields (`mask`).
4. The choice for one continuation (`select`), made only after every
   condition of a checkpoint continuation held (`execution.next_call`,
   `core._next_call`): native resume never adds a continuation, it can only
   change how an allowed one is made. In a fixed order, the first reason
   that applies keeps the checkpoint resume (`Unavailable`): the task did
   not ask for it; a real CLI whose native resume was not validated with
   real providers (`VALIDATED_PROVIDERS`, empty: none was); an adapter that
   cannot resume; settings under which the CLI keeps no session (Claude
   without session persistence, Codex `--ephemeral`, its default); no
   session kept, or one that does not match its record; a capability that
   is unknown, unsupported or does not show that the step's restrictions and
   answer checks hold on resume; another CLI version than the one that made
   the session; other frozen settings.

Codex, codex-cli 0.161.0 (native Windows) and 0.160.1 (WSL2), observed
November 10, 2026: `codex exec resume --help` lists `--json`,
`--output-last-message`, `--output-schema`, `--ephemeral` and `--last`, and
no `--sandbox`, `--cd`, `--add-dir` or `--profile`: nothing that binary
shows holds a resumed call to the reviewer's read-only sandbox, so its
restrictions are `UNKNOWN` and native resume is unavailable for it, also
for sessions that are not ephemeral. Claude Code 2.1.295 (native Windows)
and 2.1.302 (WSL2), observed the same day: `-p, --print`, `-r, --resume
[value]` ("Resume a conversation by session ID"), `--session-id <uuid>`
("must be a valid UUID"), `--no-session-persistence` ("sessions will not be
saved to disk and cannot be resumed"), `--fork-session`, `--continue` and
every option of PatchRondo's command except `--max-turns` (documented, not in
its help; `agents.DOCUMENTED`). Whether a resumed print-mode call keeps those
options and reports the same session has not been confirmed with a real
call: that is the end-of-phase validation, and until it passes
`VALIDATED_PROVIDERS` stays empty and only simulated providers ever resume.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import re

PROVIDERS = ("claude", "codex")
# The providers whose native resume passed the end-of-phase validation with real providers. Empty: no real CLI is ever
# resumed natively by this version. Only a decision of the project's owner after that validation fills it.
VALIDATED_PROVIDERS: frozenset = frozenset()
REQUESTED = "probe_then_use"  # `execution.continuation.native_resume` that asks for native resume (`execution.NATIVE_RESUME`)

SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
REFERENCE = re.compile(r"S-[0-9a-f]{24}")
VERSION = re.compile(r"\d{1,4}\.\d{1,4}\.\d{1,6}(?:-[0-9A-Za-z.]{1,40})?")
EXECUTABLE = re.compile(r"[0-9a-f]{16}")
SEEN_LIMIT = 8  # session values one call may report before the rest is ignored (each is still compared)
HELP_LIMIT = 200_000
STORE_VERSION = 1

# The model-free questions that establish a capability, each an exact command (`providers.PROBES`).
PROBE_COMMANDS = {
    "claude": {"version": ("claude", "--version"), "help": ("claude", "--help")},
    "codex": {"version": ("codex", "--version"), "help": ("codex", "exec", "--help"),
              "resume_help": ("codex", "exec", "resume", "--help")},
}

# Logs, transcripts and error messages: the two fields that carry a provider's session, masked wherever they appear.
_MASKED = re.compile(r'("(?:session_id|thread_id)"\s*:\s*)"(?:[^"\\\n]|\\.){0,512}"')
MASK = "[session]"


class Support(StrEnum):
    SUPPORTED = "supported"      # the binary's own help shows it
    UNSUPPORTED = "unsupported"  # the binary's own help shows its absence
    UNKNOWN = "unknown"          # nothing the binary printed shows either: never used


class Evidence(StrEnum):
    HELP = "help"          # read from `--version` and `--help` of the installed binary
    FIXTURE = "fixture"    # read from saved help output of a named version (tests)
    NONE = "none"          # nothing was read


class Status(StrEnum):
    VERIFIED = "verified"        # every capability native resume needs is shown by the binary's help
    UNVERIFIED = "unverified"    # the binary resumes by ID, and something native resume needs is not shown
    UNSUPPORTED = "unsupported"  # the binary shows no noninteractive resume by ID
    UNKNOWN = "unknown"          # no version or no help could be read
    NOT_PROBED = "not_probed"    # nothing was asked: an earlier reason decided


class Unavailable(StrEnum):
    """Why a continuation is not a native resume. Fixed codes, in the order `select` asks them."""
    NOT_VALIDATED = "not_validated"                # a real CLI: native resume not validated with real providers
    ADAPTER_CANNOT_RESUME = "adapter_cannot_resume"  # the adapter of the run takes no session to resume
    NOT_DURABLE = "not_durable"                    # the step's settings keep no session (ephemeral, no persistence)
    SESSION_UNCONFIRMABLE = "session_unconfirmable"  # the resumed call could not report its session (no structured output)
    NO_SESSION = "no_session"                      # the call followed reported no session that could be trusted
    SESSION_UNREADABLE = "session_unreadable"      # the kept session cannot be read
    SESSION_MISMATCH = "session_mismatch"          # the kept session is not the one the record names for this call
    CAPABILITY_UNKNOWN = "capability_unknown"      # the installed CLI's version or help could not be read
    RESUME_UNSUPPORTED = "resume_unsupported"      # its help shows no noninteractive resume by session ID
    PERSISTENCE_UNKNOWN = "persistence_unknown"    # its help does not show that sessions are kept between processes
    RESTRICTIONS_UNVERIFIED = "restrictions_unverified"  # its help does not show the step's restrictions on resume
    OUTPUT_UNVERIFIED = "output_unverified"        # its help does not show the answer checks on resume
    CLI_CHANGED = "cli_changed"                    # another version or executable than the one that made the session
    SETTINGS_CHANGED = "settings_changed"          # the call followed was started with other frozen settings
    OPTIONS_UNSUPPORTED = "options_unsupported"    # the step's settings cannot be passed to the resume form


# --- capabilities -------------------------------------------------------------------------------------------

# An option line of a help text: at most six spaces before it (descriptions are indented further).
_OPTION = re.compile(r"^ {2,6}(?:-[A-Za-z0-9], )?(--[A-Za-z][A-Za-z0-9-]*)(?:, (--[A-Za-z][A-Za-z0-9-]*))?(?=[ ,<\[=]|$)",
                     re.M)
_CLAUDE_VERSION = re.compile(r"(\S+) \(Claude Code\)")
_CODEX_VERSION = re.compile(r"codex-cli (\S+)")
_CLAUDE_RESUME = re.compile(r"^ {2,6}-r, --resume \[value\] +Resume a conversation by session ID", re.M)
_CLAUDE_PERSISTENCE = re.compile(r"--no-session-persistence +Disable session persistence - sessions\s+will not be saved"
                                 r"\s+to disk and cannot be\s+resumed")
_CODEX_SUBCOMMAND = re.compile(r"^Commands:\n(?:  \S.*\n)*?  resume +Resume a previous session by id", re.M)
_CODEX_RESUME_USAGE = re.compile(r"^Usage: codex exec resume \[OPTIONS\] \[SESSION_ID\] \[PROMPT\]$", re.M)
_CODEX_STDIN = re.compile(r"Prompt to send after resuming the session\. If `-` is used, read from stdin")
_CODEX_EPHEMERAL = re.compile(r"--ephemeral\s+Run without persisting session files to disk")
# What the commands of `agents.command` need to be held on resume, by provider (`restrictions`, `output_validation`).
CLAUDE_RESTRICTIONS = frozenset({"--restricted", "--permission-mode", "--tools", "--allowedTools", "--disallowedTools"})
CLAUDE_OUTPUT = frozenset({"--print", "--output-format", "--json-schema"})
CODEX_RESTRICTIONS = frozenset({"--sandbox"})
CODEX_OUTPUT = frozenset({"--output-last-message", "--output-schema", "--json"})


def _plain(text) -> str | None:
    """A help text as the parsers read it: bounded, with line ends of one kind."""
    return text[:HELP_LIMIT].replace("\r\n", "\n") if isinstance(text, str) else None


def options_of(text: str) -> frozenset:
    """The long options a help text lists, each with its alias."""
    found = set()
    for match in _OPTION.finditer(_plain(text) or ""):
        found.update(name for name in match.groups() if name)
    return frozenset(found)


def version_of(provider: str, text) -> str | None:
    """The version a CLI's `--version` printed, or None when it is not in the form that CLI prints."""
    if not isinstance(text, str):
        return None
    match = (_CLAUDE_VERSION if provider == "claude" else _CODEX_VERSION).fullmatch(text.strip())
    return match.group(1) if match and VERSION.fullmatch(match.group(1)) else None


@dataclass(frozen=True)
class Capability:
    """What one installed CLI showed about native resume. Never the documentation's word for it."""
    provider: str
    version: str | None
    evidence: Evidence
    noninteractive: Support = Support.UNKNOWN     # a call that runs without a person and exits
    resume_by_id: Support = Support.UNKNOWN       # a session named by its exact ID can be resumed noninteractively
    persistence: Support = Support.UNKNOWN        # sessions are kept between processes unless switched off
    restrictions: Support = Support.UNKNOWN       # the step's sandbox, permissions and tools can be given on resume
    output_validation: Support = Support.UNKNOWN  # the structured answer and its checks are available on resume
    stdin_prompt: Support = Support.UNKNOWN       # the prompt can be read from stdin on resume
    latest_selection: bool = False                # a "most recent session" selector exists: never used
    options: frozenset = frozenset()              # the long options of the command a call uses (`codex exec`)
    resume_options: frozenset = frozenset()       # those of the resume form (Claude: the same command)
    executable: str | None = None                 # digest of the resolved executable, when probed live
    note: str = ""

    def __post_init__(self):
        if self.provider not in PROVIDERS:
            raise ValueError("unknown provider")
        if self.version is not None and not VERSION.fullmatch(self.version):
            raise ValueError("invalid version")
        if self.executable is not None and not EXECUTABLE.fullmatch(self.executable):
            raise ValueError("invalid executable digest")

    @property
    def status(self) -> Status:
        if self.evidence is Evidence.NONE or self.version is None:
            return Status.UNKNOWN
        if Support.UNSUPPORTED in (self.noninteractive, self.resume_by_id):
            return Status.UNSUPPORTED
        needed = (self.noninteractive, self.resume_by_id, self.persistence, self.restrictions, self.output_validation,
                  self.stdin_prompt)
        return Status.VERIFIED if all(item is Support.SUPPORTED for item in needed) else Status.UNVERIFIED

    def matrix(self) -> dict:
        """The capability as plain values, for a report or a fixture's expectation. No path, no session."""
        return {"provider": self.provider, "version": self.version, "evidence": str(self.evidence),
                "status": str(self.status), "noninteractive": str(self.noninteractive),
                "resume_by_id": str(self.resume_by_id), "persistence": str(self.persistence),
                "restrictions": str(self.restrictions), "output_validation": str(self.output_validation),
                "stdin_prompt": str(self.stdin_prompt), "latest_selection": self.latest_selection}


def unknown(provider: str, note: str = "") -> Capability:
    return Capability(provider, None, Evidence.NONE, note=note)


def _shown(present: bool) -> Support:
    return Support.SUPPORTED if present else Support.UNKNOWN


def claude_capability(version_text: str, help_text: str, *, evidence: Evidence = Evidence.HELP,
                      executable: str | None = None) -> Capability:
    """Claude Code from `claude --version` and `claude --help`. Every option is of the one command a call runs."""
    version, help_text = version_of("claude", version_text), _plain(help_text)
    if version is None or not isinstance(help_text, str) or "Usage: claude" not in help_text:
        return Capability("claude", version, Evidence.NONE if version is None else evidence,
                          note="the help could not be read")
    options = options_of(help_text)
    noninteractive = Support.SUPPORTED if "--print" in options else Support.UNSUPPORTED
    resume = Support.SUPPORTED if "--resume" in options and _CLAUDE_RESUME.search(help_text) else \
        Support.UNSUPPORTED if "--resume" not in options else Support.UNKNOWN
    return Capability(
        "claude", version, evidence, noninteractive=noninteractive, resume_by_id=resume,
        persistence=_shown(bool(_CLAUDE_PERSISTENCE.search(help_text))),
        restrictions=_shown(CLAUDE_RESTRICTIONS <= options), output_validation=_shown(CLAUDE_OUTPUT <= options),
        # The prompt reaches a print-mode call on stdin, as for every call of PatchRondo (`providers._step`).
        stdin_prompt=_shown("--print" in options), latest_selection="--continue" in options,
        options=options, resume_options=options, executable=executable)


def codex_capability(version_text: str, exec_help: str, resume_help: str, *, evidence: Evidence = Evidence.HELP,
                     executable: str | None = None) -> Capability:
    """Codex from `codex --version`, `codex exec --help` and `codex exec resume --help`.

    The resume form is a subcommand with options of its own: only what
    `codex exec resume --help` lists is taken as accepted there, whatever
    `codex exec` accepts. Its sandbox is the step's restriction.
    """
    version, exec_help, resume_help = version_of("codex", version_text), _plain(exec_help), _plain(resume_help)
    if version is None or not isinstance(exec_help, str) or "Usage: codex exec" not in exec_help:
        return Capability("codex", version, Evidence.NONE if version is None else evidence,
                          note="the help could not be read")
    options = options_of(exec_help)
    listed = bool(_CODEX_SUBCOMMAND.search(exec_help))
    if not listed:
        return Capability("codex", version, evidence, noninteractive=Support.SUPPORTED,
                          resume_by_id=Support.UNSUPPORTED, options=options, executable=executable)
    if not isinstance(resume_help, str) or not _CODEX_RESUME_USAGE.search(resume_help):
        return Capability("codex", version, evidence, noninteractive=Support.SUPPORTED, options=options,
                          executable=executable, note="the help of the resume form could not be read")
    resume_options = options_of(resume_help)
    return Capability(
        "codex", version, evidence, noninteractive=Support.SUPPORTED, resume_by_id=Support.SUPPORTED,
        persistence=_shown(bool(_CODEX_EPHEMERAL.search(resume_help))),
        restrictions=_shown(CODEX_RESTRICTIONS <= resume_options),
        output_validation=_shown(CODEX_OUTPUT <= resume_options),
        stdin_prompt=_shown(bool(_CODEX_STDIN.search(resume_help))), latest_selection="--last" in resume_options,
        options=options, resume_options=resume_options, executable=executable)


def capability_of(provider: str, texts: dict, *, evidence: Evidence = Evidence.HELP,
                  executable: str | None = None) -> Capability:
    """A capability from the outputs of `PROBE_COMMANDS[provider]`, by name; a missing output is unknown."""
    if provider == "claude":
        return claude_capability(texts.get("version"), texts.get("help"), evidence=evidence, executable=executable)
    if provider == "codex":
        return codex_capability(texts.get("version"), texts.get("help"), texts.get("resume_help"),
                                evidence=evidence, executable=executable)
    raise ValueError("unknown provider")


def executable_digest(path: str, size: int, modified_ns: int) -> str:
    """An identity of the resolved executable that names no path: its digest with its size and time."""
    return hashlib.sha256(json.dumps([path, size, modified_ns]).encode("utf-8")).hexdigest()[:16]


# --- the session of one call --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Captured:
    """The session one call reported: its ID when exactly one valid ID was reported, otherwise why none is used."""
    id: str | None
    problem: str | None = None  # "missing", "malformed", "contradictory"


def captured(values) -> Captured:
    """The session of a call from every value its structured output gave, in order. Pure."""
    values = list(values or [])
    if not values:
        return Captured(None, "missing")
    if any(type(value) is not str or not SESSION_ID.fullmatch(value) for value in values):
        return Captured(None, "malformed")
    if len(set(values)) != 1:
        return Captured(None, "contradictory")
    return Captured(values[0])


def from_output(provider: str, stdout) -> list:
    """The session values of a single-result output (Claude `--output-format json`); Codex prints none without `--json`."""
    if provider != "claude" or not isinstance(stdout, str):
        return []
    try:
        obj = json.loads(stdout)
    except ValueError:
        return []
    return [obj["session_id"]] if isinstance(obj, dict) and "session_id" in obj else []


def mask(text):
    """`text` with the values of `session_id` and `thread_id` replaced: for logs, transcripts and messages."""
    return _MASKED.sub(lambda match: f'{match.group(1)}"{MASK}"', text) if isinstance(text, str) else text


def reference(task_id: str, attempt_id: str, provider: str, role: str, session_id: str) -> str:
    """The opaque reference the task's state keeps for a session: bound to the call that reported it, naming nothing."""
    bound = json.dumps(["patchrondo-session", 1, task_id, attempt_id, provider, role, session_id])
    return "S-" + hashlib.sha256(bound.encode("utf-8")).hexdigest()[:24]


def durable(provider: str, settings: dict | None) -> bool:
    """Whether the step's frozen settings let the CLI keep its session: never switched on by PatchRondo."""
    settings = settings or {}
    if provider == "claude":
        return settings.get("session_persistence") is not False
    return settings.get("ephemeral") is False  # Codex runs `--ephemeral` unless the user turned it off


def stored(*, task_id: str, attempt_id: str, provider: str, role: str, session_id: str,
           capability: Capability) -> dict:
    """The entry of the attempt's launch record that keeps the session (`reconciliation.marker`). Private."""
    if not SESSION_ID.fullmatch(session_id):
        raise ValueError("a session ID is a UUID")
    return {"version": STORE_VERSION, "provider": provider, "role": role, "attempt": attempt_id, "id": session_id,
            "ref": reference(task_id, attempt_id, provider, role, session_id), "cli_version": capability.version,
            "executable": capability.executable}


class Unreadable(ValueError):
    pass


def kept(entry, *, task_id: str, attempt_id: str, provider: str, role: str, ref: str) -> dict:
    """The session kept for the attempt `attempt_id`, checked against its record; raises `Unreadable` or `Mismatch`."""
    fields = {"version", "provider", "role", "attempt", "id", "ref", "cli_version", "executable"}
    if not isinstance(entry, dict) or set(entry) != fields or entry.get("version") != STORE_VERSION:
        raise Unreadable("the kept session does not have the shape this version writes")
    if type(entry["id"]) is not str or not SESSION_ID.fullmatch(entry["id"]):
        raise Unreadable("the kept session ID is not a UUID")
    version, executable = entry["cli_version"], entry["executable"]
    if (version is not None and (type(version) is not str or not VERSION.fullmatch(version))) or \
            (executable is not None and (type(executable) is not str or not EXECUTABLE.fullmatch(executable))):
        raise Unreadable("the kept session names its CLI in another form")
    if (entry["attempt"], entry["provider"], entry["role"]) != (attempt_id, provider, role) \
            or entry["ref"] != ref or reference(task_id, attempt_id, provider, role, entry["id"]) != ref:
        raise Mismatch("the kept session is not the one the record names for this call")
    return entry


class Mismatch(ValueError):
    pass


# --- the choice for one continuation ------------------------------------------------------------------------

@dataclass(frozen=True)
class Selection:
    """How one allowed continuation is made: natively, or from a checkpoint brief, and why."""
    native: bool
    capability: Status
    durable: bool | None
    reason: Unavailable | None
    cli_version: str | None = None
    ref: str | None = None  # the session resumed (its reference), for a native resume only

    def __post_init__(self):
        if self.native != (self.reason is None):
            raise ValueError("a native resume has no reason against it; a checkpoint resume has one")
        if self.native and (self.ref is None or self.capability is not Status.VERIFIED or self.durable is not True):
            raise ValueError("a native resume names its session, on a verified CLI, under settings that keep it")

    @property
    def mode(self) -> str:
        return "native_resume" if self.native else "checkpoint_resume"

    def event(self) -> dict:
        """Fixed words for the history: what was asked, what was chosen and why. Never the session."""
        return {"requested": "native_resume", "selected": self.mode, "reason": None if self.reason is None
                else str(self.reason), "capability": str(self.capability)}


@dataclass(frozen=True)
class Resume:
    """What the adapter is given for a native resume: built only by `select`, never from settings or arguments."""
    provider: str
    role: str
    session_id: str = field(repr=False)
    capability: Capability = field(repr=False)


def _fallback(reason: Unavailable, capability: Capability | None = None, durable: bool | None = None) -> tuple:
    status = capability.status if capability is not None else Status.NOT_PROBED
    return Selection(False, status, durable, reason, capability.version if capability is not None else None), None


def select(*, requested: str, provider: str, role: str, live: bool, resumable: bool, settings: dict | None,
           live_output: bool, trigger_session: str | None, trigger_digest: str | None, current_digest: str | None,
           entry=None, entry_problem: str | None = None, capability=None, task_id: str = "",
           trigger_attempt: str = "") -> tuple[Selection | None, Resume | None]:
    """The way an allowed continuation is made: (the selection, what the adapter is given) or (None, None).

    None when the task did not ask for native resume: nothing is recorded and
    the continuation is a checkpoint resume exactly as before. Otherwise the
    first reason of `Unavailable` that applies keeps the checkpoint resume.
    `capability` is a function that returns the installed CLI's capability:
    asked only once every earlier reason passed. `entry` is the session kept
    in the launch record of the call followed (`trigger_attempt`), or
    `entry_problem` when that record could not be read. Pure apart from the
    one call of `capability`.
    """
    if requested != REQUESTED:
        return None, None
    if live and provider not in VALIDATED_PROVIDERS:
        return _fallback(Unavailable.NOT_VALIDATED)
    if not resumable:
        return _fallback(Unavailable.ADAPTER_CANNOT_RESUME)
    keeps = durable(provider, settings)
    if not keeps:
        return _fallback(Unavailable.NOT_DURABLE, durable=False)
    if provider == "codex" and not live_output:
        return _fallback(Unavailable.SESSION_UNCONFIRMABLE, durable=True)
    if trigger_session is None:
        return _fallback(Unavailable.NO_SESSION, durable=True)
    if entry_problem is not None or entry is None:
        return _fallback(Unavailable.SESSION_UNREADABLE, durable=True)
    try:
        entry = kept(entry, task_id=task_id, attempt_id=trigger_attempt, provider=provider, role=role,
                     ref=trigger_session)
    except Mismatch:
        return _fallback(Unavailable.SESSION_MISMATCH, durable=True)
    except Unreadable:
        return _fallback(Unavailable.SESSION_UNREADABLE, durable=True)
    try:
        found = capability() if capability is not None else None
    except Exception:  # noqa: BLE001 - a probe that fails shows nothing: unknown, never supported
        found = None
    if not isinstance(found, Capability) or found.provider != provider:
        found = unknown(provider)
    status = found.status
    if status is Status.UNKNOWN:
        return _fallback(Unavailable.CAPABILITY_UNKNOWN, found, True)
    if Support.SUPPORTED is not found.noninteractive or found.resume_by_id is not Support.SUPPORTED \
            or found.stdin_prompt is not Support.SUPPORTED:
        return _fallback(Unavailable.RESUME_UNSUPPORTED, found, True)
    if found.persistence is not Support.SUPPORTED:
        return _fallback(Unavailable.PERSISTENCE_UNKNOWN, found, True)
    if found.restrictions is not Support.SUPPORTED:
        return _fallback(Unavailable.RESTRICTIONS_UNVERIFIED, found, True)
    if found.output_validation is not Support.SUPPORTED:
        return _fallback(Unavailable.OUTPUT_UNVERIFIED, found, True)
    if entry["cli_version"] is None or entry["cli_version"] != found.version \
            or entry["executable"] != found.executable:
        return _fallback(Unavailable.CLI_CHANGED, found, True)
    if trigger_digest is None or trigger_digest != current_digest:  # settings that cannot be compared count as changed
        return _fallback(Unavailable.SETTINGS_CHANGED, found, True)
    return (Selection(True, status, True, None, found.version, trigger_session),
            Resume(provider, role, entry["id"], found))


def unavailable(selection: Selection, reason: Unavailable) -> Selection:
    """The same choice made a checkpoint resume for `reason`, found after `select` (the command could not be built)."""
    return Selection(False, selection.capability, selection.durable, reason, selection.cli_version)
