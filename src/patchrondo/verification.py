"""Verification runner: the project's own checks, run by PatchRondo, and what each one showed.

A check is a command that the user configured for the project and allowed to
run (`tests.enabled` and `tests.trust_acknowledged`): its tests, its build, a
static analysis, a check of the repository. Nothing an agent writes becomes a
check, and nothing an agent says becomes a result. The runner starts the
command itself, without a shell, in the task's copy of the project; waits for
it to end; reads its report when the check has one; and writes what it saw
into the evidence ledger (`evidence.py`) as one `command` record, before the
next check starts and before anybody is told.

The result of a check is one of these (`Status`):

    passed   the command ran and ended with exit code 0; with a report, the report lists enough
             tests, every required one among them, and none failing
    failed   the command ran and showed a defect: a nonzero exit, failing tests, fewer tests than
             the check asks for, a required test missing or not passed, an artifact the build did
             not produce
    error    no result could be established: the time limit, a start that failed, a report that
             is missing, unreadable, left from before the run or contradicting the exit code, task
             files that the check changed
    blocked  the check was not started: not one of the project's checks, not allowed, a program
             or a folder that is not there, a location outside the project, a start refused

`timeout` is an error caused by the time limit, written as versions before
this one wrote it. `stale` is reserved for evidence that stopped counting:
no version writes it yet, and such evidence is named by an `invalidation`
record instead.

Only `passed` is ever proof (`evidence.POSITIVE`), and only while its record
counts. What a result means is decided by `judge_exit`, `parse_junit` and
`judge_report`, which read nothing but their arguments; running and
recording is the `Runner`'s.

The command under test runs with the user's rights: it can print anything
and write any report. The runner makes a forged result hard to pass off,
never impossible. It reads a report only from where the run was told to
write it, refuses one that was there before the run, compares it with the
exit code, and refuses a run that changed the task's files.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import functools
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import re
import stat
import subprocess
import sys
import time
import uuid
from xml.parsers import expat

from . import evidence, gitops, process, winacl
from .activity import clip, redact
from .storage import atomic_text, private_dir

VERSION = 1                   # of the `check` object of a `command` record
SWITCH = "PATCHRONDO_VERIFICATION"  # "0" runs the project's commands as the versions before this module did
TYPES = ("test", "build", "static_analysis", "repository_check")
FORMATS = ("junit",)
REPORT = "{report}"           # in a command: the path PatchRondo gives the report of this run
FIELDS = frozenset({"id", "type", "command", "cwd", "timeout_seconds", "report", "report_path", "min_tests",
                    "required_tests", "criteria", "artifacts", "shell"})
CHECK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}")
LEGACY_ID = re.compile(r"command-\d+", re.I)   # the names of the entries of tests.commands
CRITERION_ID = re.compile(r"AC-\d{2,4}")       # as `acceptance.CRITERION_ID`
# Programs that run what their arguments say as a script: allowed only with "shell": true.
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish", "cmd", "powershell", "pwsh", "wsl"})
MAX_CHECKS = 30
MAX_ARGUMENTS = 500
MAX_REQUIRED = 200
MAX_CRITERIA = 50
MAX_REPORT_BYTES = 32 * 1024 ** 2
MAX_CASES = 200_000
MAX_DEPTH = 64
TAIL = 6000                   # characters of output kept with the task state; the whole output is in the log
CHANGED_SHOWN = 20            # paths named when a check changed the task's files
SECRET_WORDS = ("PASSWORD", "PASSWD", "SECRET", "CREDENTIAL", "TOKEN", "API_KEY", "APIKEY", "PRIVATE_KEY", "ACCESS_KEY")


class Status(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    BLOCKED = "blocked"


TIMEOUT = "timeout"           # an ERROR caused by the time limit, as earlier versions recorded it
STALE = "stale"               # reserved; never written by this version
RESULTS = evidence.COMMAND_RESULTS  # what the `result` of a `command` record may be: every Status, and TIMEOUT
UNSETTLED = frozenset({str(Status.ERROR), str(Status.BLOCKED), TIMEOUT})  # nothing was learned about the code


def status_of(result) -> Status | None:
    """The typed result of a `command` record; None for a value no version writes."""
    if result == TIMEOUT:
        return Status.ERROR
    try:
        return Status(result)
    except ValueError:
        return None


def enabled() -> bool:
    return os.environ.get(SWITCH, "1") != "0"


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# --- the configured checks --------------------------------------------------------------

@dataclass(frozen=True)
class Check:
    """One check the project configured. Built only from the validated configuration (`parse_section`, `legacy`)."""
    id: str
    type: str
    command: tuple[str, ...]
    cwd: str = "."
    timeout: int = 180
    report: str | None = None            # "junit": the result is read from a JUnit XML report
    report_path: str | None = None       # where the command writes it, inside the project; otherwise `{report}`
    min_tests: int = 0
    required_tests: tuple[str, ...] = ()
    criteria: tuple[str, ...] = ()       # criteria this check may give evidence for: naming one proves nothing
    artifacts: tuple[str, ...] = ()      # files a build produces, as patterns inside the project
    shell: bool = False
    legacy: bool = False                 # an entry of tests.commands

    @property
    def adapter(self) -> str:
        return self.report or "exit_code"

    def identity(self) -> dict:
        """What decides what the check runs and how its result is read: a change makes earlier results stale."""
        return {"id": self.id, "type": self.type, "command": list(self.command), "cwd": self.cwd, "report": self.report,
                "report_path": self.report_path, "min_tests": self.min_tests, "required_tests": list(self.required_tests),
                "artifacts": list(self.artifacts), "shell": self.shell}


def program(argv) -> str:
    """The name of the program a command starts, as people call it: no folder, no extension, lowercase."""
    name = re.split(r"[\\/]", argv[0])[-1].lower()
    for suffix in (".exe", ".cmd", ".bat", ".com"):
        name = name.removesuffix(suffix)
    return name


def relative(value, what: str) -> str:
    """A location inside the task's copy of the project, as configured: relative, never `..`, never a drive."""
    if not isinstance(value, str) or not value.strip() or "\0" in value or len(value) > 400:
        raise ValueError(f"{what} must be a relative path inside the project")
    text = value.strip()
    if text.startswith(("/", "\\")) or PurePosixPath(text).is_absolute() or PureWindowsPath(text).drive:
        raise ValueError(f"{what} must be relative to the project folder: {text}")
    parts = [part for part in re.split(r"[\\/]+", text) if part not in {"", "."}]
    if any(part == ".." or ":" in part for part in parts):
        raise ValueError(f"{what} may not leave the project folder: {text}")
    return "/".join(parts) or "."


def _strings(value, what: str, limit: int, pattern=None) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > limit or not all(isinstance(item, str) and item.strip() and len(item) <= 300
                                                                    for item in value):
        raise ValueError(f"{what} must be a list of at most {limit} nonempty texts")
    items = [item.strip() for item in value]
    if pattern is not None and any(not pattern.fullmatch(item) for item in items):
        raise ValueError(f"{what} has an entry that is not a valid name")
    if len(set(items)) != len(items):
        raise ValueError(f"{what} names an entry twice")
    return tuple(items)


def _integer(value, what: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{what} must be an integer between {low} and {high}")
    return value


def parse_check(raw, *, default_timeout: int) -> Check:
    """One entry of verification.checks, validated. Raises ValueError for anything this version does not run."""
    if not isinstance(raw, dict):
        raise ValueError("must be a JSON object")
    unknown = sorted(set(raw) - FIELDS)
    if unknown:
        raise ValueError(f"unknown setting: {', '.join(unknown)}")
    name = raw.get("id")
    if not isinstance(name, str) or not CHECK_ID.fullmatch(name) or LEGACY_ID.fullmatch(name):
        raise ValueError("id must be 1 to 40 letters, digits, '.', '_' or '-', starting with a letter or a digit, "
                         "and not command-<n>, which names the entries of tests.commands")
    where = f"check {name}"
    if raw.get("type") not in TYPES:
        raise ValueError(f"{where}: type must be one of {', '.join(TYPES)}")
    command = raw.get("command")
    if not isinstance(command, list) or not command or len(command) > MAX_ARGUMENTS \
            or not all(isinstance(part, str) and part for part in command):
        raise ValueError(f"{where}: command must be a nonempty list of nonempty arguments")
    shell = raw.get("shell", False)
    if type(shell) is not bool:
        raise ValueError(f"{where}: shell must be a JSON boolean (true/false)")
    if program(command) in SHELLS and not shell:
        raise ValueError(f"{where}: {program(command)} runs its arguments as a script; set \"shell\": true to allow it")
    report = raw.get("report")
    if report is not None and report not in FORMATS:
        raise ValueError(f"{where}: report must be one of {', '.join(FORMATS)}")
    placeholder = any(REPORT in part for part in command)
    report_path = None
    if raw.get("report_path") is not None:
        if report is None:
            raise ValueError(f"{where}: report_path needs a report")
        report_path = relative(raw["report_path"], f"{where}: report_path")
        if report_path == ".":
            raise ValueError(f"{where}: report_path must name a file")
    if placeholder and (report is None or report_path is not None):
        raise ValueError(f"{where}: {REPORT} names the report PatchRondo reads: use it with a report and without report_path")
    if report is not None and not placeholder and report_path is None:
        raise ValueError(f"{where}: say where the report is written: {REPORT} in the command, or report_path")
    for key in ("min_tests", "required_tests"):
        if key in raw and report is None:
            raise ValueError(f"{where}: {key} needs a report: an exit code does not say which tests ran")
    return Check(
        id=name, type=raw["type"], command=tuple(command), cwd=relative(raw.get("cwd", "."), f"{where}: cwd"),
        timeout=_integer(raw.get("timeout_seconds", default_timeout), f"{where}: timeout_seconds", 1, 86400),
        report=report, report_path=report_path,
        min_tests=_integer(raw.get("min_tests", 1 if report else 0), f"{where}: min_tests", 0, 1_000_000),
        required_tests=_strings(raw.get("required_tests", []), f"{where}: required_tests", MAX_REQUIRED),
        criteria=_strings(raw.get("criteria", []), f"{where}: criteria", MAX_CRITERIA, CRITERION_ID),
        artifacts=tuple(relative(item, f"{where}: artifacts")
                        for item in _strings(raw.get("artifacts", []), f"{where}: artifacts", evidence.MAX_ARTIFACTS)),
        shell=shell)


def parse_section(section, *, default_timeout: int) -> list[Check]:
    """The `verification` section of a project configuration. Absent: no checks. Raises ValueError otherwise."""
    if section is None:
        return []
    if not isinstance(section, dict) or set(section) - {"checks"}:
        raise ValueError("verification may only contain checks")
    entries = section.get("checks", [])
    if not isinstance(entries, list) or len(entries) > MAX_CHECKS:
        raise ValueError(f"verification.checks must be a list of at most {MAX_CHECKS} checks")
    checks = []
    for number, raw in enumerate(entries, 1):
        try:
            checks.append(parse_check(raw, default_timeout=default_timeout))
        except ValueError as exc:
            raise ValueError(f"verification.checks[{number}]: {exc}") from None
    names = [check.id.lower() for check in checks]
    if len(set(names)) != len(names):
        raise ValueError("verification.checks names a check twice")
    return checks


def _timeout(cfg: dict) -> int:
    value = (cfg.get("workflow") or {}).get("test_timeout_seconds")
    return value if type(value) is int and value > 0 else 180


def legacy(cfg: dict) -> list[Check]:
    """The entries of tests.commands, as checks: tests read from their exit code, as they always were."""
    commands = (cfg.get("tests") or {}).get("commands") or []
    usable = [cmd for cmd in commands if isinstance(cmd, list) and cmd and all(isinstance(part, str) and part for part in cmd)]
    return [Check(id=f"command-{number}", type="test", command=tuple(cmd), timeout=_timeout(cfg), legacy=True)
            for number, cmd in enumerate(usable, 1)]


def checks(cfg: dict) -> list[Check]:
    """What one run of the project's commands runs, in order: tests.commands, then verification.checks.

    The configuration is the validated one. With the runner switched off
    (`PATCHRONDO_VERIFICATION=0`) only tests.commands run, as before.
    """
    found = legacy(cfg)
    if enabled():
        found += parse_section(cfg.get("verification"), default_timeout=_timeout(cfg))
    return found


def context(cfg: dict) -> list[dict]:
    """The identities of the configured checks beyond tests.commands, for the test context of a task."""
    return [check.identity() for check in checks(cfg) if not check.legacy]


# --- reading a JUnit XML report ---------------------------------------------------------

class ReportInvalid(ValueError):
    """A report that cannot be read as a JUnit XML report of this run."""


@dataclass(frozen=True)
class Case:
    id: str                    # "classname.name", or the name alone
    suites: tuple[str, ...]    # the names of the suites around it, outermost first
    failed: bool
    errored: bool
    skipped: bool

    @property
    def outcome(self) -> str:
        return "error" if self.errored else "failed" if self.failed else "skipped" if self.skipped else "passed"


@dataclass(frozen=True)
class Report:
    cases: tuple[Case, ...]
    suites: int
    hidden: tuple[str, ...] = ()      # suites that declare more failures or errors than their cases show
    mismatch: tuple[str, ...] = ()    # suites that declare another number of tests than they list

    def counts(self) -> dict:
        total = {"tests": len(self.cases), "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
        for case in self.cases:
            total[{"passed": "passed", "failed": "failed", "error": "errors", "skipped": "skipped"}[case.outcome]] += 1
        return total


def _declared(attrs: dict, name: str) -> int | None:
    value = attrs.get(name)
    return int(value) if isinstance(value, str) and value.strip().isdigit() else None


def parse_junit(data: bytes) -> Report:
    """Read a JUnit XML report. Raises ReportInvalid for anything else.

    The report is untrusted input: a document type declaration is refused
    before any entity could be defined, so nothing is expanded and nothing
    outside the file is fetched; size, depth and the number of test cases are
    bounded. Counts come from the test cases themselves; what the suites
    declare is only compared with them.
    """
    if len(data) > MAX_REPORT_BYTES:
        raise ReportInvalid(f"The report is larger than {MAX_REPORT_BYTES // 1024 ** 2} MiB")
    parser = expat.ParserCreate()
    cases: list[Case] = []
    open_: list[str] = []
    containers: list[dict] = []   # the testsuites and testsuite elements being read, outermost first
    finished: list[dict] = []
    current: dict | None = None
    suites = 0

    def refuse(*_):
        raise ReportInvalid("The report declares a document type, which a JUnit report never needs: it is not read")

    def start(name, attrs):
        nonlocal current, suites
        if len(open_) >= MAX_DEPTH:
            raise ReportInvalid("The report is nested too deeply")
        if not open_ and name not in {"testsuites", "testsuite"}:
            raise ReportInvalid(f"The report's root element is <{clip(name, 60)}>, not <testsuites> or <testsuite>")
        if name in {"testsuites", "testsuite"}:
            if current is not None:
                raise ReportInvalid("The report has a test suite inside a test case")
            suites += name == "testsuite"
            containers.append({"name": attrs.get("name", ""), "kind": name, "attrs": dict(attrs),
                               "tests": 0, "failures": 0, "errors": 0})
        elif name == "testcase":
            if current is not None:
                raise ReportInvalid("The report has a test case inside a test case")
            if not containers or containers[-1]["kind"] != "testsuite":
                raise ReportInvalid("The report has a test case outside a test suite")
            if len(cases) >= MAX_CASES:
                raise ReportInvalid(f"The report lists more than {MAX_CASES} test cases")
            classname, label = attrs.get("classname", ""), attrs.get("name", "")
            current = {"id": f"{classname}.{label}" if classname else label, "failed": False, "errored": False, "skipped": False}
        elif current is not None and open_[-1] == "testcase" and name in {"failure", "error", "skipped"}:
            current[{"failure": "failed", "error": "errored", "skipped": "skipped"}[name]] = True
        open_.append(name)

    def end(name):
        nonlocal current
        open_.pop()
        if name == "testcase" and current is not None:
            case = Case(current["id"], tuple(item["name"] for item in containers if item["kind"] == "testsuite"),
                        current["failed"], current["errored"], current["skipped"])
            cases.append(case)
            for item in containers:
                item["tests"] += 1
                item["failures"] += case.failed
                item["errors"] += case.errored
            current = None
        elif name in {"testsuites", "testsuite"}:
            finished.append(containers.pop())

    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise ReportInvalid(f"The report is not well-formed XML: {expat.ErrorString(exc.code)} (line {exc.lineno})") from None
    hidden, mismatch = [], []
    for item in finished:
        label = f"<{item['kind']} name=\"{clip(item['name'], 80)}\">"
        failures, errors, tests = (_declared(item["attrs"], key) for key in ("failures", "errors", "tests"))
        if (failures is not None and failures > item["failures"]) or (errors is not None and errors > item["errors"]):
            hidden.append(label)
        if tests is not None and tests != item["tests"]:
            mismatch.append(label)
    return Report(tuple(cases), suites, tuple(hidden), tuple(mismatch))


def normalize(identifier: str) -> str:
    """A test named as people write it ("tests/test_api.py::TestA::test_b") as a report names it (tests.test_api.TestA.test_b)."""
    text = identifier.strip().replace("\\", "/")
    head, separator, rest = text.partition("::")
    head = head.removesuffix(".py").replace("/", ".")
    return (head + ("." + rest.replace("::", ".") if separator else "")).strip(".")


def requirements(required, report: Report) -> tuple[list[str], list[str]]:
    """The required tests a report does not list, and those it lists without one passing or with one failing.

    A requirement names a test, a class, a module or a suite: it is met when
    at least one test case it names passed and none of them failed.
    """
    missing, unmet = [], []
    for name in required:
        wanted = normalize(name)
        matched = [case for case in report.cases if case.id == wanted or case.id.startswith((wanted + ".", wanted + "["))
                   or wanted in case.suites or name in case.suites]
        if not matched:
            missing.append(name)
        elif not any(case.outcome == "passed" for case in matched) or any(case.outcome in {"failed", "error"} for case in matched):
            unmet.append(name)
    return missing, unmet


def judge_exit(exit_code: int) -> tuple[Status, str]:
    """A check read from its exit code alone."""
    if exit_code == 0:
        return Status.PASSED, "The command ended with exit code 0"
    return Status.FAILED, f"The command ended with exit code {exit_code}"


def judge_report(check: Check, exit_code: int, report: Report) -> tuple[Status, str, dict]:
    """A check read from its JUnit report and its exit code. Anything ambiguous is an error, never a pass."""
    counts = report.counts()
    details: dict = {"counts": counts}
    failing = counts["failed"] + counts["errors"]
    if report.hidden:
        return Status.ERROR, f"The report declares failures that none of its test cases shows ({report.hidden[0]})", details
    if report.mismatch and not failing:
        return Status.ERROR, f"The report declares another number of tests than it lists ({report.mismatch[0]})", details
    if failing:
        if exit_code == 0:
            return Status.ERROR, (f"The command ended with exit code 0, and its report lists {failing} failing "
                                  f"test{'s' if failing != 1 else ''}"), details
        return Status.FAILED, f"{counts['failed']} failed and {counts['errors']} with errors, of {counts['tests']} tests", details
    if counts["tests"] < check.min_tests:
        return Status.FAILED, ("The report lists no test" if not counts["tests"] else
                               f"The report lists {counts['tests']} tests; the check asks for at least {check.min_tests}"), details
    missing, unmet = requirements(check.required_tests, report)
    if missing or unmet:
        details.update(missing_required=missing[:50], unmet_required=unmet[:50])
        parts = ([f"{len(missing)} required not in the report ({', '.join(missing[:5])})"] if missing else []) + \
                ([f"{len(unmet)} required without a pass ({', '.join(unmet[:5])})"] if unmet else [])
        return Status.FAILED, "Required tests: " + "; ".join(parts), details
    if exit_code != 0:
        return Status.ERROR, f"The command ended with exit code {exit_code}, and its report lists no failing test", details
    return Status.PASSED, f"{counts['tests']} tests: {counts['passed']} passed, {counts['skipped']} skipped", details


# --- what a run is about ------------------------------------------------------------------

def snapshot(worktree: Path) -> tuple[dict, frozenset]:
    """The state of the task's files as Git sees them, and the status lines behind it.

    A fact that cannot be read is said to be missing, never made up. The
    commit alone does not tell two copies apart: the fingerprint covers every
    file Git does not ignore.
    """
    try:
        head = gitops.git("rev-parse", "HEAD", cwd=worktree)
        branch = gitops.git("branch", "--show-current", cwd=worktree)
        git_dir = gitops.git("rev-parse", "--absolute-git-dir", cwd=worktree)
        lines = gitops.git("status", "--porcelain", "--untracked-files=all", cwd=worktree).splitlines()
        digest = gitops.fingerprint(worktree)
    except (gitops.GitError, OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "worktree": str(worktree),
                "error": evidence.text(f"Git could not describe the task's files: {exc}", 300)}, frozenset()
    return {"available": True, "worktree": str(worktree), "git_dir": git_dir, "head": head, "branch": branch or None,
            "dirty": bool(lines), "changes": len(lines), "fingerprint": digest}, frozenset(lines)


@functools.lru_cache(maxsize=1)
def environment() -> dict:
    """Where checks run: the system and PatchRondo's interpreter. The computer's name only inside a digest."""
    facts = {"system": platform.system(), "release": platform.release(), "machine": platform.machine(),
             "python": platform.python_version(), "wsl": "microsoft" in platform.release().lower()}
    seed = "|".join([platform.node(), sys.executable, *(str(value) for value in facts.values())])
    return {"id": "H-" + hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()[:16], **facts}


def known_secrets(environ=None) -> list[str]:
    """Values of this process's variables whose names say they hold a secret: what a log must not show.

    `process.safe_env` keeps them from the check, which can still find them
    elsewhere and print them.
    """
    environ = os.environ if environ is None else environ
    found = {value for key, value in environ.items()
             if len(value) >= 8 and any(word in key.upper().replace("-", "_") for word in SECRET_WORDS)}
    return sorted(found, key=len, reverse=True)


def scrub(text: str, secrets=()) -> str:
    """Output as it is kept: known secret values and values that look like credentials masked. Best effort."""
    for value in secrets:
        text = text.replace(value, "[redacted]")
    return redact(text)


def _inside(root: Path, location: str) -> Path | None:
    """`location` under `root` when no part of the way is a link or a junction and it stays inside; otherwise None."""
    base = root.resolve()
    target = base
    for part in [] if location == "." else location.split("/"):
        target = target / part
        try:
            if winacl.is_reparse_point(target):
                return None
        except FileNotFoundError:
            break  # what does not exist yet is not a link
        except OSError:
            return None
    target = base / location if location != "." else base
    try:
        return target if target.resolve().is_relative_to(base) else None
    except OSError:
        return None


def read_report(path: Path) -> tuple[bytes | None, tuple | None, str | None]:
    """The bytes of a report, what identifies that version of the file, and why it cannot be used.

    Returns (data, (size, mtime_ns, sha256), None), or (None, None, reason).
    A link or a junction is never followed, and a file larger than a report
    may be is not read.
    """
    try:
        if winacl.is_reparse_point(path):
            return None, None, "The report is a link, not a file the run wrote"
        info = path.stat()
    except FileNotFoundError:
        return None, None, None
    except OSError:
        return None, None, "The report could not be read"
    if not stat.S_ISREG(info.st_mode):
        return None, None, "The report is not a regular file"
    if info.st_size > MAX_REPORT_BYTES:
        return None, None, f"The report is larger than {MAX_REPORT_BYTES // 1024 ** 2} MiB"
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_REPORT_BYTES + 1)
    except OSError:
        return None, None, "The report could not be read"
    if len(data) > MAX_REPORT_BYTES:
        return None, None, f"The report is larger than {MAX_REPORT_BYTES // 1024 ** 2} MiB"
    return data, (len(data), info.st_mtime_ns, hashlib.sha256(data).hexdigest()), None


def _artifact(path: Path) -> dict | None:
    """The hash of a file a build produced, when it is a regular file and not a link."""
    try:
        if winacl.is_reparse_point(path) or not path.is_file():
            return None
        return evidence.fingerprint_file(path)
    except (OSError, ValueError):
        return None


# --- running ------------------------------------------------------------------------------

@dataclass
class Result:
    """What the runner observed for one check, and the ledger record it wrote about it."""
    check: Check
    index: int
    of: int
    status: str                       # one of RESULTS
    reason: str
    command: list[str]                # as it ran: {report} replaced
    exit_code: int | None = None
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    error: dict | None = None
    log: Path | None = None
    log_sha256: str | None = None
    output_tail: str = ""
    report: dict | None = None
    before: dict = field(default_factory=dict)
    after: dict | None = None
    changed: bool | None = None
    artifacts: list[dict] = field(default_factory=list)
    evidence: str | None = None       # ID of the ledger record
    lines: frozenset = field(default_factory=frozenset, repr=False)  # Git's status lines after the check
    process_tree: str | None = None   # `process.Result.process_tree`: None when the command did not start
    kill_refused: bool = False        # `process.Result.kill_refused`

    @property
    def passed(self) -> bool:
        return self.status == Status.PASSED

    def entry(self) -> dict:
        """The result as the task state keeps it with the latest run (`state["tests"]`)."""
        found = {"command": self.command, "status": self.status, "returncode": self.exit_code,
                 "log_file": str(self.log) if self.log else None, "output_tail": self.output_tail,
                 "check": self.check.id, "type": self.check.type, "evidence": self.evidence, "duration_ms": self.duration_ms}
        if not self.passed:
            found["message"] = self.reason
        if self.report and self.report.get("counts"):
            found["counts"] = self.report["counts"]
        return found


class Runner:
    """Runs a task's checks in order and puts each result on record as soon as it is known.

    `allowed` are the checks of the project's configuration, and `consent`
    whether the project allows its commands to run: a check that is not
    among them, or anything without the consent, is blocked before it starts.
    The caller holds the task lock, as for every write of the ledger.
    """

    def __init__(self, task_path: Path, workspace: Path, *, allowed, consent: bool, iteration: int, base: str | None = None,
                 run_dir: Path | None = None, journal=None, covers=None, secrets=None, execute=None):
        self.task_path, self.workspace = Path(task_path), Path(workspace)
        self.allowed, self.consent = list(allowed), consent is True
        self.iteration, self.base, self.journal = iteration, base, journal
        self.run_dir = run_dir if run_dir is not None else self.task_path / "runs" / f"iteration-{iteration:03d}"
        self.covers = covers or _covers
        self.execute = execute  # `process.execute` unless the caller starts commands through its own
        self.secrets = known_secrets() if secrets is None else list(secrets)
        self.token = uuid.uuid4().hex[:8]
        self.run = f"{iteration:03d}-{self.token}"

    def run_all(self, checks) -> list[Result]:
        """Every check until the first one that does not pass; later ones depend on it and are not started."""
        checks = list(checks)
        private_dir(self.run_dir)
        before, lines = snapshot(self.workspace)
        results = []
        for index, check in enumerate(checks, 1):
            result = self.run_one(check, index, len(checks), before, lines)
            results.append(result)
            if not result.passed:
                break
            before, lines = result.after or before, result.lines
        return results

    def run_one(self, check: Check, index: int, of: int, before: dict, lines: frozenset) -> Result:
        """One check: refused, started, observed, judged, then recorded. An interruption records nothing."""
        name = f"{self.token}-test-{index:02d}"
        result = Result(check, index, of, str(Status.BLOCKED), "", list(check.command), started_at=_stamp(), before=before,
                        lines=lines)
        self._emit("started", result)
        try:
            self._run(result, name, lines)
        finally:
            result.finished_at = result.finished_at or _stamp()
        self._record(result)
        self._emit(result.status, result)
        return result

    def _refusal(self, check: Check) -> str | None:
        if check not in self.allowed:
            return "It is not one of the checks configured for this project: only those run"
        if not self.consent:
            return "The project's commands are not allowed to run (tests.enabled and tests.trust_acknowledged)"
        if not check.legacy and program(check.command) in SHELLS and not check.shell:
            return f"{program(check.command)} runs its arguments as a script, and the check does not allow a shell"
        return None

    def _run(self, result: Result, name: str, lines: frozenset) -> None:
        check = result.check
        refusal = self._refusal(check)
        cwd = None if refusal else _inside(self.workspace, check.cwd)
        if refusal is None and (cwd is None or not cwd.is_dir()):
            refusal = f"Its folder {check.cwd} is not a folder inside the task's copy of the project"
        target, earlier = None, None
        if refusal is None and check.report:
            if check.report_path:
                target = _inside(self.workspace, check.report_path)
                if target is None:
                    refusal = f"Its report {check.report_path} would be read from outside the task's copy of the project"
                else:
                    earlier = read_report(target)[1]  # a report left by an earlier run is not this run's
            else:
                target = self.run_dir / f"{name}.junit.xml"
                if target.exists() or target.is_symlink():
                    refusal = "The report of this run exists before the run: it cannot be told apart from another"
        if refusal:
            result.reason, result.error = refusal, {"kind": "refused"}
            return
        command = [part.replace(REPORT, str(target)) for part in check.command] if check.report and not check.report_path \
            else list(check.command)
        result.command = command
        clock = time.monotonic()
        try:
            ran = (self.execute or process.execute)(command, cwd, timeout=check.timeout, tree=True,
                                                    pid_file=self.run_dir / f"{name}.active-process.json")
        except FileNotFoundError:
            result.reason, result.error = f"The program {clip(command[0], 200)} was not found", {"kind": "not_found"}
            return
        except PermissionError:
            result.reason, result.error = f"Starting {clip(command[0], 200)} was refused: permission denied", \
                {"kind": "permission_denied"}
            return
        except OSError as exc:
            result.status, result.reason = str(Status.ERROR), f"The command could not be started: {evidence.text(exc, 300)}"
            result.error = {"kind": "start_failed"}
            return
        finally:
            result.duration_ms = round((time.monotonic() - clock) * 1000)
            result.finished_at = _stamp()
        result.exit_code = ran.returncode
        result.process_tree = getattr(ran, "process_tree", None)
        result.kill_refused = getattr(ran, "kill_refused", False) is True
        output = scrub(ran.stdout + "\n" + ran.stderr, self.secrets)
        result.log = self.run_dir / f"{name}.log"
        atomic_text(result.log, output)
        result.log_sha256 = hashlib.sha256(result.log.read_bytes()).hexdigest()
        result.output_tail = process.short_log(output[-TAIL:])
        result.after, result.lines = snapshot(self.workspace)
        if ran.timed_out:
            result.status, result.reason = TIMEOUT, f"The command did not end within {check.timeout} seconds and was stopped"
            if result.process_tree == process.CHILD:
                result.reason += (". No job object held the processes it started: they were not stopped with it and "
                                  "may still be running")
            if result.kill_refused:
                result.reason += (". The system refused to signal what was left of its process group, so nothing says "
                                  "that the processes it started were stopped: they may still be running")
            result.error = {"kind": "timeout"}
            return
        if ran.returncode < 0:
            self._error(result, "killed", f"The command was stopped by signal {-ran.returncode} before it ended")
            return
        # Without Git before the run there is nothing to compare: the record says so, and binds itself to no files.
        if result.before.get("available") and not result.after.get("available"):
            self._error(result, "snapshot_unavailable", "The state of the task's files after the check could not be read")
            return
        if result.before.get("available"):
            result.changed = result.before["fingerprint"] != result.after["fingerprint"]
        if result.changed:
            touched = sorted(lines ^ result.lines)[:CHANGED_SHOWN]
            self._error(result, "workspace_modified", "The check changed the task's files, so its result is not about the "
                        "files it was given. Keep what it generates out of the project, or ignored by Git"
                        + (f": {', '.join(evidence.text(line[3:], 120) for line in touched)}" if touched else ""))
            return
        if check.report:
            self._report(result, target, earlier)
            if result.status == Status.ERROR:
                return
        else:
            status, result.reason = judge_exit(ran.returncode)
            result.status = str(status)
        if result.passed and check.artifacts:
            self._artifacts(result)

    def _error(self, result: Result, kind: str, reason: str) -> None:
        result.status, result.reason, result.error = str(Status.ERROR), reason, {"kind": kind}

    def _report(self, result: Result, target: Path, earlier: tuple | None) -> None:
        """Read the report of the run, keep a copy beside the log, and judge the check from it."""
        check = result.check
        if check.report_path and _inside(self.workspace, check.report_path) is None:
            self._error(result, "report_unsafe", "The way to the report became a link during the run: it is not read")
            return
        data, version, problem = read_report(target)
        if data is None:
            self._error(result, "report_invalid" if problem else "report_missing",
                        problem or "The command wrote no report where the check reads it")
            return
        if earlier is not None and earlier == version:
            self._error(result, "report_stale", "The report is the one that was there before the run: this run did not write it")
            return
        copy = self.run_dir / f"{result.log.name.removesuffix('.log')}.junit.xml"
        if copy != target:
            with copy.open("xb") as stream:  # a name of this run only: an existing file is never replaced
                stream.write(data)
        try:
            kept = evidence.fingerprint_file(copy)
        except ValueError:
            kept = None  # replaced by something else since it was read
        if kept is None or kept["sha256"] != version[2]:
            self._error(result, "report_invalid", "The report changed after it was read")
            return
        result.artifacts.append(kept)
        result.report = {"format": check.report, "path": f"{self.run_dir.name}/{copy.name}", "sha256": version[2],
                         "source": check.report_path or REPORT}
        try:
            parsed = parse_junit(data)
        except ReportInvalid as exc:
            self._error(result, "report_invalid", str(exc))
            return
        status, result.reason, details = judge_report(check, result.exit_code, parsed)
        result.report.update(details, suites=parsed.suites)
        result.status = str(status)
        if status == Status.ERROR:
            result.error = {"kind": "report_inconsistent"}

    def _artifacts(self, result: Result) -> None:
        """Hash the files a build produced. A pattern that matches none fails the check."""
        found, empty = [], []
        for pattern in result.check.artifacts:
            matched = []
            try:
                paths = sorted(self.workspace.glob(pattern))
            except (ValueError, OSError):
                paths = []  # a pattern Python does not accept matches nothing
            for path in paths:
                if len(found) + len(matched) >= evidence.MAX_ARTIFACTS:
                    break
                location = _inside(self.workspace, path.relative_to(self.workspace).as_posix())
                hashed = _artifact(location) if location is not None else None
                if hashed is not None:
                    matched.append(hashed)
            found += matched
            if not matched:
                empty.append(pattern)
        result.artifacts += found
        if empty:
            result.status = str(Status.FAILED)
            result.reason = f"The command ended with exit code 0 and produced nothing for {', '.join(empty)}"

    def _record(self, result: Result) -> None:
        """One `command` record in the ledger, written before the result is returned to anybody."""
        check = result.check
        entry = evidence.append(
            self.task_path, "command", executor="runtime", criteria=list(check.criteria), result=result.status,
            exit_code=result.exit_code, command=[evidence.text(part, 300) for part in result.command][:60],
            covers=self.covers(check), run=self.run, of=result.of, iteration=self.iteration,
            fingerprint=result.before.get("fingerprint"), base=self.base, scope="code",
            log=f"{self.run_dir.name}/{result.log.name}" if result.log else None, log_sha256=result.log_sha256,
            artifacts=result.artifacts or None,
            check={"version": VERSION, "id": check.id, "type": check.type, "adapter": check.adapter, "index": result.index,
                   "cwd": check.cwd, "timeout_seconds": check.timeout, "started_at": result.started_at,
                   "finished_at": result.finished_at, "duration_ms": result.duration_ms, "environment": environment(),
                   "process_tree": result.process_tree,
                   "kill_refused": result.kill_refused,
                   "snapshot": {"before": result.before, "after": result.after, "changed": result.changed},
                   "report": result.report, "reason": evidence.text(result.reason), "error": result.error})
        result.evidence = entry["id"]

    def _emit(self, status: str, result: Result) -> None:
        if self.journal is not None:
            self.journal.emit("test", status=status, index=result.index, command=" ".join(result.command), check=result.check.id,
                              exit_code=result.exit_code, evidence=result.evidence,
                              text=None if status in {"started", str(Status.PASSED)} else result.reason)


def _covers(check: Check) -> list[str]:
    from . import capabilities  # imported here: capabilities reads the checks of this module
    return capabilities.check_covers(check)


# --- what people and agents are told ----------------------------------------------------

def describe(record: dict) -> str:
    """One line about a `command` record, from the record alone: what ran, its result and why."""
    check = record.get("check") if isinstance(record.get("check"), dict) else {}
    parts = []
    if check.get("id"):
        parts.append(f"check {check['id']} ({check.get('type')})")
    report = check.get("report") if isinstance(check.get("report"), dict) else {}
    counts = report.get("counts") if isinstance(report.get("counts"), dict) else None
    if counts:
        parts.append(f"{counts.get('tests')} tests: {counts.get('passed')} passed, {counts.get('failed')} failed, "
                     f"{counts.get('errors')} errors, {counts.get('skipped')} skipped")
    if check.get("reason") and record.get("result") != Status.PASSED:
        parts.append(str(check["reason"]))
    if record.get("log"):
        parts.append(f"log {record['log']}")
    return "; ".join(parts)
