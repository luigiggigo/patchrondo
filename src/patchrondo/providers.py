"""Official CLI adapters, with explicit least-privilege tool settings."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import json
import re

from . import acceptance, agents, streams
from .process import KILL_REFUSED, Result, execute, short_log, stop_refused, with_refusal
from .storage import atomic_text


class AgentFailure(RuntimeError):
    """A failed step. Quota failures may carry the provider and its stated reset."""

    def __init__(self, message: str, kind: str = "agent_error", *, provider: str | None = None,
                 retry_at: datetime | None = None, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.provider = provider
        # Either form is enough for recovery: an aware instant, or a positive wait in seconds
        # counted from the failure. None when no reset was stated explicitly.
        self.retry_at = retry_at
        self.retry_after_seconds = retry_after_seconds


@dataclass
class AgentReply:
    text: str
    provider: str


# Checked in this order; the first match decides. Authentication comes first: waiting
# cannot fix a login problem, so a message naming both must never look retryable.
_ERROR_KINDS = (
    ("authentication", re.compile(r"not logged in|authentication failed|unauthorized|please login|login required")),
    ("quota", re.compile(r"rate.?limit|usage.?limit|quota|too many requests|out of credits|insufficient credits"
                         r"|limit reached|resets at")),
    # General wording of a provider that refuses the model it was asked for, not a form observed in an
    # authorized run. It comes last: a login or a usage limit named in the same message still decides.
    ("model_unavailable", re.compile(r"model_not_found|issue with the selected model|(?:invalid|unknown|unsupported) model"
                                     r"|model\b[^\n]{0,80}(?:does not exist|not found|do not have access|is not supported"
                                     r"|is not available|not available)")),
)
# A CLI that stops at its own arguments names the option it refused. Shapes of the two argument parsers:
# "unknown option '--x'" and "option '--x <v>' argument 'y' is invalid" (Claude Code), "unexpected argument
# '--x' found" and "invalid value 'y' for '--x <V>'" (Codex).
_OPTION_REFUSED = re.compile(r"(?:unknown|unexpected|unrecognized|invalid)\b[^\n]{0,60}(?:option|argument|value)[^\n]{0,120}--?[a-z]"
                             r"|option '--?[^'\n]+' argument '[^'\n]*' is invalid", re.I)


def _classify_error(body: str) -> str:
    lowered = body.lower()
    return next((kind for kind, pattern in _ERROR_KINDS if pattern.search(lowered)), "agent_error")


# Claude Code ends a run that used all its turns with a `result` line of this subtype. Evidence: an
# observed failure, the logs of a task that stopped at its configured 12-turn limit (docs/REVIEW.md,
# "Local streaming failure correction", October 11, 2026). It says nothing about the plan's usage:
# it is its own failure and is never retried as a quota failure.
CLAUDE_TURN_LIMIT = "error_max_turns"

# The real runner. Tests and the synthetic runners replace `execute` in this module, and
# everything that starts a provider CLI goes through it or through `probe` below.
_execute = execute


def simulated() -> bool:
    """Whether provider commands are routed away from the real CLIs."""
    return execute is not _execute


# The questions a provider CLI may be asked besides the sandbox self-check, each an exact command:
# its help and, for Codex, its model catalog. None of them takes a prompt.
PROBES = (("claude", "--help"), ("codex", "--help"), ("codex", "exec", "--help"), ("codex", "debug", "models"),
          ("codex", "debug", "models", "--bundled"))


def probe(argv: list[str], cwd: Path, *, timeout: int, on_stdout=None) -> Result:
    """Run a model-free command of a provider CLI: its help, its model catalog or its sandbox self-check.

    Never used for a prompt. Refused while provider commands are simulated, so
    a test or a synthetic run cannot start a real CLI and no call is counted
    in one.
    """
    if simulated():
        raise RuntimeError("Provider commands are simulated: no real CLI is started")
    if tuple(argv) not in PROBES and (argv[:1] != ["codex"] or argv[1:2] != ["sandbox"]):
        raise ValueError("Only the help, the model catalog and the sandbox self-check of a provider CLI may be probed")
    return _execute(argv, cwd, timeout=timeout, **({"on_stdout": on_stdout} if on_stdout is not None else {}))


_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")
_UNITS = {"seconds": 1, "second": 1, "secs": 1, "sec": 1, "s": 1,
          "minutes": 60, "minute": 60, "mins": 60, "min": 60, "m": 60,
          "hours": 3600, "hour": 3600, "hrs": 3600, "hr": 3600, "h": 3600,
          "days": 86400, "day": 86400, "d": 86400}
_MAX_RELATIVE_SECONDS = 366 * 86400
# A reset is read only next to wording that announces one, and only with an explicit offset.
_CUE = r"\b(?:resets?|retry|try\s+again|available(?:\s+again)?|until)\b[^\n.;|]{0,30}?"
_ZONE = r"\s?(?P<zone>Z|(?:UTC|GMT)\s?[+-]\d{1,2}(?::?\d{2})?|UTC|GMT|[+-]\d{2}(?::?\d{2})?)(?![\w:+-])"
_CLOCK = r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2})(?P<fraction>[.,]\d+)?)?"
_HALF = r"(?:\s?(?P<half>[ap])\.?m\.?)?"
_ABSOLUTE = [re.compile(_CUE + date + _ZONE, re.I) for date in (
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})[T ]" + _CLOCK,
    r"(?P<month>[a-z]{3,9})\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<year>\d{4})(?:,|\s+at)?\s+" + _CLOCK + _HALF,
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?P<month>[a-z]{3,9})\.?,?\s+(?P<year>\d{4})(?:,|\s+at)?\s+" + _CLOCK + _HALF,
)]
_PART = r"(\d+(?:\.\d+)?)\s*(" + "|".join(_UNITS) + r")(?![a-z])"
_RELATIVE = re.compile(
    r"\b(?:retry[\s-]+after|retry\s+in|try\s+again\s+(?:in|after)|resets?\s+in|available(?:\s+again)?\s+in)"
    r"\s*[:=]?\s*((?:" + _PART + r"[\s,]*(?:and\s+)?)+)", re.I)
# The HTTP header form is the only one read without a unit: it is defined in seconds.
_HEADER = re.compile(r"\bretry-after\s*[:=]\s*(\d{1,9})(?=\s*(?:$|[\n.,;)\]}\"']))", re.I | re.M)


def _moment(match: re.Match) -> datetime | None:
    """Build the UTC instant of one absolute match; None for impossible dates or offsets."""
    part = match.groupdict()
    month = part["month"].lower()
    if not month.isdigit():
        month = next((index for index, name in enumerate(_MONTHS, 1) if name.startswith(month)), 0)
    hour = int(part["hour"])
    half = (part.get("half") or "").lower()
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half == "p" else 0)
    zone = part["zone"].upper().removeprefix("UTC").removeprefix("GMT").strip()
    offset = timedelta(0)
    if zone not in {"", "Z"}:
        digits = zone[1:].replace(":", "")
        hours, minutes = (int(digits[:-2]), int(digits[-2:])) if len(digits) > 2 else (int(digits), 0)
        if hours > 14 or minutes > 59:
            return None
        offset = timedelta(hours=hours, minutes=minutes) * (-1 if zone[0] == "-" else 1)
    try:
        moment = datetime(int(part["year"]), int(month), int(part["day"]), hour, int(part["minute"]),
                          int(part["second"] or 0), tzinfo=timezone(offset)).astimezone(timezone.utc)
        # Round a fractional second up: a retry must never precede the stated reset.
        return moment + timedelta(seconds=1) if part["fraction"] else moment
    except (ValueError, OverflowError):
        return None


def _relative_seconds(text: str) -> int | None:
    """Longest explicitly stated wait, such as "retry after 2 hours 30 minutes"."""
    waits = [float(match.group(1)) for match in _HEADER.finditer(text)]
    for match in _RELATIVE.finditer(text):
        waits.append(sum(float(number) * _UNITS[unit.lower()]
                         for number, unit in re.findall(_PART, match.group(1), flags=re.I)))
    waits = [wait for wait in waits if 0 < wait <= _MAX_RELATIVE_SECONDS]
    return math.ceil(max(waits)) if waits else None


# Codex names no zone: it prints the reset in the local time of its own process, as
# "try again at 3:45 PM." on the same local day and "try again at Oct 12th, 2026 3:45 PM."
# otherwise (format_retry_timestamp in codex-rs/protocol/src/error.rs, read at rust-v0.160.1).
# PatchRondo starts that process on the same host with the same TZ, so the same zone applies.
_CODEX_RESET = re.compile(
    r"\btry again at (?:(?P<month>[a-z]{3}) (?P<day>\d{1,2})(?:st|nd|rd|th), (?P<year>\d{4}) )?"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2}) (?P<half>[ap])m(?![a-z])", re.I)


def _codex_moment(match: re.Match, observed_at: datetime) -> datetime | None:
    """UTC instant of a Codex local reset time; None when it is not a real date or time."""
    part = match.groupdict()
    hour = int(part["hour"])
    if not 1 <= hour <= 12:
        return None
    hour = hour % 12 + (12 if part["half"].lower() == "p" else 0)
    try:
        if part["month"]:
            month = next((index for index, name in enumerate(_MONTHS, 1) if name.startswith(part["month"].lower())), 0)
            year, day = int(part["year"]), int(part["day"])
        else:
            today = observed_at.astimezone()  # the short form means the local day of the failure
            year, month, day = today.year, today.month, today.day
        # fold=1: in an hour repeated by a clock change, take the later instant.
        local = datetime(year, month, day, hour, int(part["minute"]), fold=1)
        # Seconds are not printed: the reset may be up to a minute after the printed time.
        return local.astimezone(timezone.utc) + timedelta(minutes=1)
    except (ValueError, OverflowError, OSError):
        return None


def quota_hint(text: str, observed_at: datetime | None = None, provider: str | None = None) -> dict:
    """Read a provider reset from a quota message, as AgentFailure keyword arguments.

    Recognized from any source: a date and time with an explicit UTC offset, or
    a relative wait with units. A time without a date or offset ("resets at
    9pm") yields nothing, except in the form Codex is known to print in local
    time. This only parses; recovery.plan decides whether the result may be used.
    """
    observed_at = observed_at or datetime.now(timezone.utc)
    moments = [moment for pattern in _ABSOLUTE for match in pattern.finditer(text)
               if (moment := _moment(match)) is not None]
    if provider == "codex":
        moments += [moment for match in _CODEX_RESET.finditer(text)
                    if (moment := _codex_moment(match, observed_at)) is not None]
    hint: dict = {}
    seconds = _relative_seconds(text)
    if seconds is not None:
        hint["retry_after_seconds"] = seconds
        moments.append(observed_at + timedelta(seconds=seconds))
    if moments:
        hint["retry_at"] = max(moments)  # with several resets stated, wait for the last one
    return hint


def classified_failure(message: str, detail: str, provider: str,
                       observed_at: datetime | None = None, secondary: str = "") -> AgentFailure:
    """Classify CLI error output into a kind and, for quota failures, a parsed reset.

    `message` is the caller's diagnostic, usually a bounded excerpt of the same
    output; it is stored with the failure. `secondary` is the other output stream. It decides the kind only when
    `detail` is inconclusive, so agent text cannot override a specific error;
    a reset stated on either stream is kept.
    """
    kind = _classify_error(detail)
    if kind == "agent_error" and secondary:
        kind = _classify_error(secondary)
    hint = quota_hint(f"{detail}\n{secondary}", observed_at, provider) if kind == "quota" else {}
    return AgentFailure(message, kind, provider=provider, **hint)


def _claude_result(obj, observed_at: datetime | None = None, stderr: str = "") -> str | None:
    """The answer in Claude's result object; None when the object does not carry one."""
    if not isinstance(obj, dict):
        return None
    if obj.get("is_error"):
        # The message is saved in task state: keep it an excerpt, as for a failed exit.
        raise classified_failure(short_log(str(obj.get("result", "Claude returned is_error")), 1200),
                                 str(obj.get("result", "")), "claude", observed_at, stderr.strip())
    if obj.get("structured_output") is not None:
        structured = obj["structured_output"]
        return json.dumps(structured, ensure_ascii=False) if not isinstance(structured, str) else structured
    return obj["result"] if isinstance(obj.get("result"), str) else None


def _claude_subtype(final) -> str | None:
    """Subtype of Claude's result, given the parsed object of a stream or the text of a single-result output."""
    if isinstance(final, str):
        try:
            final = json.loads(final)
        except json.JSONDecodeError:
            return None
    subtype = final.get("subtype") if isinstance(final, dict) else None
    return subtype if isinstance(subtype, str) else None


def _claude_text(body: str, observed_at: datetime | None = None, stderr: str = "") -> str:
    try:
        text = _claude_result(json.loads(body), observed_at, stderr)
    except json.JSONDecodeError:
        text = None
    return body if text is None else text


def _noted(*_args, **_fields) -> None:
    """Stands in for a journal when a step is run without one."""


class OfficialCLI:
    journals = True  # `invoke` accepts the journal of the attempt and records the CLI's activity in it
    configurable = True  # `invoke` accepts the agent settings of the step and starts the CLI with them

    def __init__(self, timeout: int = 600, claude_turns: int = 12, clock=None):
        self.timeout = timeout
        self.claude_turns = claude_turns
        # Reference instant for relative waits such as "retry after 120 seconds".
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def invoke(self, provider: str, role: str, prompt: str, workspace: Path, run_dir: Path,
               journal=None, settings: dict | None = None) -> AgentReply:
        """Run one step with the provider's CLI and return its final answer.

        The CLI is asked to print its activity while it works; what it prints
        is recorded in `journal` and never decides the outcome. If the CLI
        refuses the streaming options before it starts, or live activity is
        switched off, the step runs with the options used before streaming
        existed and the journal says so.

        `settings` are the agent settings of the step for this role and
        provider, as `agents.clean_block` validated them. Without them the
        CLI is started with the command PatchRondo always used.
        """
        if provider not in {"claude", "codex"} or role not in {"developer", "reviewer"}:
            raise ValueError("Invalid provider or role")
        settings = settings or {}
        emit = journal.emit if journal is not None else _noted
        if not streams.enabled() or settings.get("live_activity") is False:
            switch = f"({streams.SWITCH})" if not streams.enabled() else "in the agent settings of this role"
            emit("status", status="notice", code="stream_off",
                 text=f"Live activity is switched off {switch}: this step shows its result when it ends.")
            return self._step(provider, role, prompt, workspace, run_dir, emit, settings, live=False)
        try:
            return self._step(provider, role, prompt, workspace, run_dir, emit, settings, live=True)
        except _StreamRefused as refusal:
            emit("status", status="notice", code="stream_unsupported",
                 text=f"This {provider} CLI did not accept the options for live activity ({refusal}). "
                      "The step runs without them and shows its result when it ends.")
            return self._step(provider, role, prompt, workspace, run_dir, emit, settings, live=False)

    def verify(self, provider: str, model: str, effort: str | None, folder: Path) -> dict:
        """Ask a model for one word, to learn whether this login can use it: {"ok", "kind", "message"}.

        A real call, the smallest there is: no tool that writes, an empty
        folder. It uses a little of the plan, so it runs only when a person asks.
        """
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / "last-message.txt"
        out.unlink(missing_ok=True)
        try:
            result = execute(agents.verification(provider, model, effort, folder=folder, last_message=out), folder,
                             stdin="Reply with the single word OK.", timeout=min(self.timeout, 180))
            if result.timed_out:
                raise AgentFailure(f"No answer from {model} in time", "timeout", provider=provider)
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip()
                refused = next((line for line in detail.splitlines()[:40] if _OPTION_REFUSED.search(line)), None)
                if refused and _classify_error(detail) == "agent_error":
                    raise AgentFailure(short_log(refused, 300), "unsupported_option", provider=provider)
                raise classified_failure(short_log(detail, 600), detail, provider, self.clock(), result.stdout.strip())
            text = _claude_text(result.stdout, self.clock(), result.stderr) if provider == "claude" else \
                out.read_text(encoding="utf-8") if out.is_file() else ""
            if not text.strip():
                raise AgentFailure(f"{model} returned nothing", "empty_output", provider=provider)
        except FileNotFoundError:
            return {"ok": False, "kind": "configuration", "message": f"CLI not found: {provider}"}
        except AgentFailure as failure:
            return {"ok": False, "kind": failure.kind, "message": short_log(str(failure), 600)}
        finally:
            out.unlink(missing_ok=True)
        return {"ok": True, "kind": None, "message": short_log(text.strip(), 200)}

    def _step(self, provider: str, role: str, prompt: str, workspace: Path, run_dir: Path, emit,
              settings: dict | None = None, *, live: bool) -> AgentReply:
        settings = settings or {}
        timeout = settings.get("timeout_seconds") or self.timeout
        turns = settings.get("max_turns") or self.claude_turns
        run_dir.mkdir(parents=True, exist_ok=True)
        atomic_text(run_dir / f"{role}.prompt.md", prompt)
        # A verdict about the code, and one outcome for each acceptance criterion: what the backend validates.
        schema = acceptance.REVIEW_SCHEMA
        missing = agents.missing_folders(settings)
        if missing:
            raise AgentFailure(f"The agent settings of the {role} name a folder or a file that is not there: "
                               f"{', '.join(missing)}. Create it or change the settings, then resume.",
                               "configuration", provider=provider)
        out = run_dir / f"{role}.last-message.txt"
        schema_text = json.dumps(schema, separators=(",", ":"))
        if provider != "claude":
            out.unlink(missing_ok=True)
            schema_text = str(run_dir / "review.schema.json")
            if role == "reviewer":
                atomic_text(run_dir / "review.schema.json", json.dumps(schema, indent=2))
        # The settings decide the model and the options of the call; the command itself is built in one place.
        argv = agents.command(provider, role, settings, workspace=workspace, live=live, turns=self.claude_turns,
                              schema=schema_text, last_message=out)
        if settings:
            # The command this step was started with, for the Logs tab: credentials masked, long values shortened.
            atomic_text(run_dir / f"{role}.command.txt", agents.preview(argv)["line"] + "\n")
        stream = streams.open_stream(provider, role, workspace, emit) if live else None
        try:
            result = execute(argv, workspace, stdin=prompt, timeout=timeout,
                             pid_file=run_dir / f"{role}.active-process.json",
                             **({"on_stdout": stream.feed} if stream is not None else {}))
        except FileNotFoundError as exc:
            raise AgentFailure(f"CLI not found: {argv[0]}. Install the official CLI and authenticate it.",
                               "configuration", provider=provider) from exc
        finally:
            if stream is not None:
                stream.close()  # also when the step is interrupted: what was read so far stays recorded
        # With a stream the log is its transcript: bounded like the captured output, without internal reasoning.
        observed = stream is not None and stream.fed
        stdout = stream.transcript.text() if observed else result.stdout
        # These logs live outside the repository in a private directory.
        atomic_text(run_dir / f"{role}.stdout.log", short_log(stdout))
        refused = stop_refused(result)  # stopped, and the system refused its group the last signal
        errors = short_log(result.stderr)
        atomic_text(run_dir / f"{role}.stderr.log", with_refusal(errors) if refused else errors)
        if result.timed_out:
            raise AgentFailure(f"Timeout after {timeout}s for {provider}/{role}" + (f". {KILL_REFUSED}" if refused else ""),
                               "timeout", provider=provider)
        turn_limit = None
        if provider == "claude" and _claude_subtype(stream.result if stream is not None else result.stdout) == CLAUDE_TURN_LIMIT \
                and _classify_error(result.stderr) == "agent_error":
            # Decided from the CLI's own result, never from wording in its activity. A login or usage
            # diagnostic on stderr still decides, as for every other failure.
            turn_limit = AgentFailure(f"{provider}/{role} used all {turns} turns allowed for one step "
                                      f"without finishing ({CLAUDE_TURN_LIMIT}). Raise workflow.claude_max_turns or "
                                      "narrow the task, then resume.", "max_turns", provider=provider)
        reported = "\n".join(stream.errors) if stream is not None else ""
        if result.returncode != 0:
            if stream is not None and not stream.recognized and streams.refused(provider, result.stderr or result.stdout):
                refusal = next(iter((result.stderr or result.stdout).strip().splitlines()), "")
                raise _StreamRefused(short_log(refusal, 200))
            started = stream is not None and stream.recognized
            refused = None if started else next((line.strip() for line in (result.stderr or result.stdout).splitlines()[:40]
                                                 if _OPTION_REFUSED.search(line)), None)
            if refused and _classify_error(result.stderr) == "agent_error":
                # The CLI stopped at its own arguments: nothing was sent to a model. Waiting cannot fix it.
                raise AgentFailure(f"The {provider} CLI did not accept an option of this step: {short_log(refused, 300)} "
                                   "Open the agent settings and press Test configuration, then resume.",
                                   "unsupported_option", provider=provider)
            if turn_limit is not None:
                raise turn_limit
            # Either stream may carry the cause or the reset; stderr comes first when both do.
            outputs = [part.strip() for part in (result.stderr, stdout) if part.strip()] or [""]
            shown = outputs
            if reported:
                # Classify only the CLI's error and stderr, even if neither names a known failure.
                # Activity text and event names are not diagnostics (for example rate_limit_event).
                outputs = ["\n".join(part for part in (reported, result.stderr.strip()) if part)]
                shown = outputs
            shown = "\n".join(short_log(part, 1200 // len(shown)) for part in shown)
            raise classified_failure(f"{provider}/{role} exit={result.returncode}: {shown}",
                                     outputs[0], provider, self.clock(), "\n".join(outputs[1:]))
        if turn_limit is not None:
            raise turn_limit  # a result without an answer: the step ended at the limit, whatever the exit code
        if stream is not None and not stream.recognized:
            emit("status", status="notice", code="stream_empty",
                 text="The CLI printed no activity for this step; only its result is available.")
        # The answer comes from the CLI's own final result, never from a message seen while it worked.
        if provider == "claude":
            text = _claude_result(stream.result, self.clock(), result.stderr) if stream is not None else None
            if text is None and stream is not None and stream.recognized:
                raise AgentFailure(f"Missing final result from {provider}/{role}", "empty_output", provider=provider)
            if text is None:
                text = _claude_text(result.stdout, self.clock(), result.stderr)
        else:
            outfile = run_dir / f"{role}.last-message.txt"
            if not outfile.is_file():
                if reported:
                    raise classified_failure(f"{provider}/{role} returned no final response: {short_log(reported, 1200)}",
                                             reported, provider, self.clock(), result.stderr.strip())
                raise AgentFailure(f"Missing final response from {provider}/{role}", "empty_output", provider=provider)
            if outfile.stat().st_size > 240000:
                raise AgentFailure("Final response is too large", "invalid_output", provider=provider)
            text = outfile.read_text(encoding="utf-8")
        if not text.strip():
            raise AgentFailure(f"Empty output from {provider}/{role}", "empty_output", provider=provider)
        atomic_text(run_dir / f"{role}.reply.md", short_log(text, 60000))
        return AgentReply(text=text, provider=provider)


class _StreamRefused(Exception):
    """The CLI stopped at the streaming options before starting; nothing was sent to a model."""
