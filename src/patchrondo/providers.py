"""Official CLI adapters, with explicit least-privilege tool settings."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import json
import re

from .process import execute, short_log
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
)


def _classify_error(body: str) -> str:
    lowered = body.lower()
    return next((kind for kind, pattern in _ERROR_KINDS if pattern.search(lowered)), "agent_error")


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


def _claude_text(body: str, observed_at: datetime | None = None, stderr: str = "") -> str:
    try:
        obj = json.loads(body)
        if isinstance(obj, dict):
            if obj.get("is_error"):
                # The message is saved in task state: keep it an excerpt, as for a failed exit.
                raise classified_failure(short_log(str(obj.get("result", "Claude returned is_error")), 1200),
                                         str(obj.get("result", "")), "claude", observed_at, stderr.strip())
            if obj.get("structured_output") is not None:
                structured = obj["structured_output"]
                return json.dumps(structured, ensure_ascii=False) if not isinstance(structured, str) else structured
            if isinstance(obj.get("result"), str):
                return obj["result"]
    except json.JSONDecodeError:
        pass
    return body


class OfficialCLI:
    def __init__(self, timeout: int = 600, claude_turns: int = 12, clock=None):
        self.timeout = timeout
        self.claude_turns = claude_turns
        # Reference instant for relative waits such as "retry after 120 seconds".
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def invoke(self, provider: str, role: str, prompt: str, workspace: Path, run_dir: Path) -> AgentReply:
        if provider not in {"claude", "codex"} or role not in {"developer", "reviewer"}:
            raise ValueError("Invalid provider or role")
        run_dir.mkdir(parents=True, exist_ok=True)
        atomic_text(run_dir / f"{role}.prompt.md", prompt)
        schema = {
            "type": "object", "additionalProperties": False,
            "required": ["verdict", "summary", "issues"],
            "properties": {
                "verdict": {"type": "string", "enum": ["APPROVED", "CHANGES_REQUESTED", "BLOCKED"]},
                "summary": {"type": "string"},
                "issues": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["severity", "path", "description"],
                    "properties": {
                        "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                        "path": {"type": "string"}, "description": {"type": "string"},
                    },
                }},
            },
        }
        if provider == "claude":
            tools = "Read,Glob,Grep,Edit,Write" if role == "developer" else "Read,Glob,Grep"
            argv = ["claude", "-p", "--output-format", "json", "--restricted",
                    "--permission-mode", "dontAsk", "--tools", tools,
                    "--allowedTools", tools, "--disallowedTools", "mcp__*",
                    "--max-turns", str(self.claude_turns)]
            if role == "reviewer":
                argv += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
        else:
            out = run_dir / f"{role}.last-message.txt"
            out.unlink(missing_ok=True)
            argv = ["codex", "--ask-for-approval", "never", "exec", "--sandbox",
                    "workspace-write" if role == "developer" else "read-only",
                    "--cd", str(workspace),
                    "--ephemeral", "--output-last-message", str(out)]
            if role == "reviewer":
                schema_path = run_dir / "review.schema.json"
                atomic_text(schema_path, json.dumps(schema, indent=2))
                argv += ["--output-schema", str(schema_path)]
            argv.append("-")
        try:
            result = execute(argv, workspace, stdin=prompt, timeout=self.timeout,
                             pid_file=run_dir / f"{role}.active-process.json")
        except FileNotFoundError as exc:
            raise AgentFailure(f"CLI not found: {argv[0]}. Install the official CLI and authenticate it.",
                               "configuration", provider=provider) from exc
        # These logs live outside the repository in a private directory.
        atomic_text(run_dir / f"{role}.stdout.log", short_log(result.stdout))
        atomic_text(run_dir / f"{role}.stderr.log", short_log(result.stderr))
        if result.timed_out:
            raise AgentFailure(f"Timeout after {self.timeout}s for {provider}/{role}", "timeout", provider=provider)
        if result.returncode != 0:
            # Either stream may carry the cause or the reset; stderr comes first when both do.
            streams = [part.strip() for part in (result.stderr, result.stdout) if part.strip()] or [""]
            shown = "\n".join(short_log(part, 1200 // len(streams)) for part in streams)
            raise classified_failure(f"{provider}/{role} exit={result.returncode}: {shown}",
                                     streams[0], provider, self.clock(), "\n".join(streams[1:]))
        if provider == "claude":
            text = _claude_text(result.stdout, self.clock(), result.stderr)
        else:
            outfile = run_dir / f"{role}.last-message.txt"
            if not outfile.is_file():
                raise AgentFailure(f"Missing final response from {provider}/{role}", "empty_output", provider=provider)
            if outfile.stat().st_size > 240000:
                raise AgentFailure("Final response is too large", "invalid_output", provider=provider)
            text = outfile.read_text(encoding="utf-8")
        if not text.strip():
            raise AgentFailure(f"Empty output from {provider}/{role}", "empty_output", provider=provider)
        atomic_text(run_dir / f"{role}.reply.md", short_log(text, 60000))
        return AgentReply(text=text, provider=provider)
