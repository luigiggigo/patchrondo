"""Reviewer preflight: prove, without a model, that the reviewer can read a worktree and cannot write it.

A Codex reviewer runs its commands in the CLI's own read-only sandbox. Before
a review is requested, this module asks that same sandbox to run two small
commands in the task worktree and compares what they report with what is on
disk. No prompt is sent and no quota is used.

The command is the sandbox self-check of the CLI:

    codex sandbox -P :read-only --include-managed-config -C <worktree> -- <command>

read from `codex sandbox --help` of codex-cli 0.161.0 on native Windows and
confirmed there by running it on October 11, 2026. A CLI whose help does not
list these options is not probed: the checks that need it are reported as
skipped, never as passed. On Windows the sandbox starts PowerShell in
constrained language mode, so the probe uses cmdlets only.

What is checked, each with its own result:

- `sandbox.start`   the sandbox ran the probe to its end
- `process.cwd`     the working directory of a process started in the sandbox
- `shell.cwd`       the location PowerShell actually starts in
- `read.absolute`   a file of the worktree read by its absolute path
- `read.relative`   the same file read by a relative path
- `git.run`         Git runs and names the same commit as outside the sandbox
- `git.metadata`    Git reads the worktree's index and status
- `write.canary`    a file PatchRondo created is still intact after the sandbox
                    tried to overwrite and to delete it
- `write.create`    the sandbox could not create a file in the worktree

A write is "refused" only when the file on disk proves it: a command that
merely failed is not evidence. The target repository's tests are never run.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import secrets
import shutil
import time

from . import providers, winacl
from .process import short_log

HELP_TIMEOUT = 20
PROBE_TIMEOUT = 45
OUTPUT_BYTES = 200_000
FIXTURE = ".patchrondo-preflight-"
STALE_SECONDS = 900  # a fixture this old was left by a probe that died
OPTIONS = ("--permission-profile", "--include-managed-config", "--cd")
PROFILE = ":read-only"
CHECKS = {
    "sandbox.start": "The review sandbox starts",
    "process.cwd": "Processes start in the task folder",
    "shell.cwd": "The shell starts in the task folder",
    "read.absolute": "Files can be read by full path",
    "read.relative": "Files can be read by relative path",
    "git.run": "Git runs",
    "git.metadata": "Git reads the task's history and status",
    "write.canary": "Existing files cannot be changed or deleted",
    "write.create": "New files cannot be created",
}
# Marks PowerShell reads as a single quote, besides the ASCII one.
_QUOTES = "'‘’‚‛"


def quote(text: str) -> str:
    """`text` as a PowerShell single-quoted literal. Nothing inside it is expanded."""
    if any(ord(char) < 32 for char in text):
        raise ValueError("Control characters cannot be passed to the probe")
    for mark in _QUOTES:
        text = text.replace(mark, mark * 2)
    return f"'{text}'"


def script(*, root: str, marker: str | None, expected: str | None, canary: str | None, fresh: str, token: str) -> str:
    """The PowerShell probe. Every answer is an ASCII line `PR key=value`; paths are compared inside it."""
    lines = [
        "$ErrorActionPreference = 'Continue'",
        "$ProgressPreference = 'SilentlyContinue'",
        f"$root = {quote(root)}",
        f"'PR begin={token}'",
        "'PR language=' + $ExecutionContext.SessionState.LanguageMode",
        "$here = (Get-Location).ProviderPath",
        "'PR cwd_ok=' + ($here -eq $root)",
        "'PR cwd=' + $here",
    ]
    if marker is not None and expected is not None:
        lines += [
            f"$expected = {quote(expected)}",
            f"try {{ $line = Get-Content -LiteralPath {quote(marker)} -TotalCount 1 -ErrorAction Stop; "
            "'PR abs_ok=' + ($line -ceq $expected) } catch { 'PR abs_ok=False'; 'PR abs_error=' + $_ }",
            f"try {{ $line = Get-Content -LiteralPath {quote(os.path.basename(marker))} -TotalCount 1 -ErrorAction Stop; "
            "'PR rel_ok=' + ($line -ceq $expected) } catch { 'PR rel_ok=False'; 'PR rel_error=' + $_ }",
        ]
    lines += [
        "try { $head = & git -C $root rev-parse HEAD 2>&1; 'PR git_exit=' + $LASTEXITCODE; "
        "'PR git_head=' + ($head -join ' ') } catch { 'PR git_exit=missing'; 'PR git_head=' + $_ }",
        "try { $status = & git status --porcelain 2>&1; 'PR status_exit=' + $LASTEXITCODE; "
        "'PR status=' + ($status | Select-Object -First 1) } catch { 'PR status_exit=missing'; 'PR status=' + $_ }",
    ]
    if canary is not None:
        lines += [
            f"$canary = {quote(canary)}",
            f"try {{ $seen = Get-Content -LiteralPath $canary -TotalCount 1 -ErrorAction Stop; "
            f"'PR canary_seen=' + ($seen -ceq {quote(token)}) }} catch {{ 'PR canary_seen=False' }}",
            "try { Set-Content -LiteralPath $canary -Value 'tampered' -ErrorAction Stop; 'PR overwrite=done' } "
            "catch { 'PR overwrite=refused' }",
            "try { Remove-Item -LiteralPath $canary -Force -ErrorAction Stop; 'PR delete=done' } "
            "catch { 'PR delete=refused' }",
        ]
    lines += [
        f"try {{ Set-Content -LiteralPath {quote(fresh)} -Value 'created' -ErrorAction Stop; 'PR create=done' }} "
        "catch { 'PR create=refused' }",
        f"'PR end={token}'",
    ]
    return "\n".join(lines) + "\n"


def encode(text: str) -> str:
    """The form `powershell.exe -EncodedCommand` takes: no quoting can change what runs."""
    return base64.b64encode(text.encode("utf-16-le")).decode("ascii")


def parse(output: bytes) -> dict[str, str]:
    facts: dict[str, str] = {}
    for line in output.decode("utf-8", errors="replace").splitlines():
        if line.startswith("PR ") and "=" in line:
            key, _, value = line[3:].partition("=")
            facts.setdefault(key.strip(), value.strip()[:600])
    return facts


def supported(help_text: str) -> bool:
    """Whether `codex sandbox --help` lists every option the probe passes."""
    return all(option in help_text for option in OPTIONS)


def _run(argv: list[str], cwd: Path, timeout: int) -> tuple[int, bytes, str, bool]:
    """Run one model-free CLI command through the provider seam and keep its raw standard output."""
    chunks: list[bytes] = []
    size = [0]

    def keep(chunk: bytes) -> None:
        if size[0] < OUTPUT_BYTES:
            chunks.append(chunk)
            size[0] += len(chunk)

    result = providers.probe(argv, cwd, timeout=timeout, on_stdout=keep)
    return result.returncode, b"".join(chunks), result.stderr, result.timed_out


def _same_folder(shown: str, root: str) -> bool:
    shown, root = shown.strip().rstrip("\\/"), root.rstrip("\\/")
    if os.path.normcase(shown) == os.path.normcase(root):
        return True
    try:
        return bool(shown) and os.path.normcase(os.path.realpath(shown)) == os.path.normcase(os.path.realpath(root))
    except OSError:
        return False


class Sandbox:
    """The Codex sandbox self-check of this computer.

    `run(argv, cwd, timeout)` returns (exit code, standard output bytes,
    standard error text, timed out); tests pass a scripted one.
    """

    def __init__(self, run=None, which=shutil.which, system: str = os.name):
        self.run, self.which, self.system = run or _run, which, system
        self._help: dict[tuple, bool] = {}

    def identity(self) -> tuple | None:
        """What identifies the installed CLI: a new version is probed again."""
        path = self.which("codex")
        if not path:
            return None
        try:
            info = os.stat(path)
            return (path, info.st_size, info.st_mtime_ns)
        except OSError:
            return (path, 0, 0)

    def unavailable(self, cwd: Path) -> str | None:
        """Why the sandbox cannot be probed here; None when it can."""
        if self.system != "nt":
            return "The Codex sandbox is probed on native Windows only"
        identity = self.identity()
        if identity is None:
            return "Codex CLI was not found on PATH"
        if identity not in self._help:
            try:
                code, output, errors, timed_out = self.run(["codex", "sandbox", "--help"], cwd, HELP_TIMEOUT)
            except OSError as exc:
                return f"Codex CLI could not be started: {exc}"
            if timed_out or code != 0:
                return "This Codex CLI has no sandbox self-check (codex sandbox --help failed)"
            self._help[identity] = supported(output.decode("utf-8", errors="replace") + errors)
        if not self._help[identity]:
            return ("This Codex CLI does not list the options the sandbox self-check needs "
                    f"({', '.join(OPTIONS)})")
        return None

    def _command(self, root: str, command: list[str]) -> list[str]:
        return ["codex", "sandbox", "-P", PROFILE, "--include-managed-config", "-C", root, "--", *command]

    def probe(self, home: Path, worktree: Path, head: str | None) -> dict:
        """Run the probe in the sandbox and return what it and the disk say afterwards."""
        root = os.path.realpath(worktree)
        token = secrets.token_hex(8)
        marker = Path(root) / ".git"
        expected = None
        try:
            if marker.is_file():
                # The link file of a Git worktree: always there, never part of the tracked content.
                expected = marker.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
        except (OSError, IndexError):
            expected = None
        fresh = Path(root) / f"{FIXTURE}{token}.tmp"
        container = Path(home) / "worktrees"
        fixture = container / f"{FIXTURE}{token}" if winacl.managed_worktree(home, worktree) is None else None
        facts: dict = {"token": token, "head": head, "root": root, "expected": expected is not None,
                       "fixture": fixture is not None}
        try:
            if fixture is not None:
                _sweep(container)
                # Created like any folder there, so it inherits what a worktree inherits.
                fixture.mkdir()
                (fixture / "canary.txt").write_text(token + "\n", encoding="ascii", newline="\n")
            text = script(root=root, marker=str(marker) if expected is not None else None, expected=expected,
                          canary=str(fixture / "canary.txt") if fixture is not None else None, fresh=str(fresh),
                          token=token)
            code, output, errors, timed_out = self.run(
                self._command(root, ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encode(text)]),
                Path(root), PROBE_TIMEOUT)
            facts.update(parse(output))
            facts.update(exit=code, timed_out=timed_out, stderr=short_log(_plain(errors), 400))
            if fixture is not None:
                try:
                    facts["canary_intact"] = (fixture / "canary.txt").read_text(encoding="ascii") == token + "\n"
                except (OSError, ValueError):
                    facts["canary_intact"] = False
            facts["created"] = os.path.lexists(fresh)
            # A second, separate process: its directory is the one the sandbox gave it, not the shell's choice.
            if facts.get("end") == token:
                code, output, _errors, timed_out = self.run(self._command(root, ["cmd.exe", "/d", "/u", "/c", "cd"]),
                                                            Path(root), PROBE_TIMEOUT)
                shown = output.decode("utf-16-le", errors="replace").strip() if code == 0 and not timed_out else ""
                facts["process_cwd"] = shown[:600]
                facts["process_cwd_ok"] = _same_folder(shown, root) if shown else None
        finally:
            for leftover in (fresh, *([fixture / "canary.txt", fixture] if fixture is not None else [])):
                try:
                    leftover.rmdir() if leftover.is_dir() else leftover.unlink(missing_ok=True)
                except OSError:
                    pass
        return facts


def _plain(errors: str) -> str:
    """Standard error without PowerShell's progress records, which are not diagnostics."""
    return "\n".join(line for line in errors.splitlines() if not line.startswith(("#< CLIXML", "<Objs "))).strip()


def _sweep(container: Path) -> None:
    """Remove fixtures a previous probe left behind when it was killed."""
    try:
        for folder in container.glob(f"{FIXTURE}*"):
            if time.time() - folder.stat().st_mtime > STALE_SECONDS and not winacl.is_reparse_point(folder):
                shutil.rmtree(folder, ignore_errors=True)
    except OSError:
        pass


def _check(name: str, status: str, detail: str = "") -> dict:
    return {"id": name, "label": CHECKS[name], "status": status, "detail": detail[:500]}


def evaluate(facts: dict) -> tuple[list[dict], str | None]:
    """Turn what a probe reported into one result per check and the first problem found, by its stable code."""
    token = facts.get("token")
    if facts.get("begin") != token or facts.get("end") != token:
        reason = "it did not finish in time" if facts.get("timed_out") else f"exit code {facts.get('exit')}"
        detail = f"The probe did not complete ({reason}). {facts.get('stderr') or ''}".strip()
        return ([_check("sandbox.start", "failed", detail)] +
                [_check(name, "skipped", "The sandbox did not run the probe") for name in list(CHECKS)[1:]],
                "sandbox_unavailable")

    def yes(key: str) -> bool:
        return facts.get(key) == "True"

    checks = [_check("sandbox.start", "passed", f"PowerShell language mode: {facts.get('language', 'unknown')}")]
    process = facts.get("process_cwd_ok")
    checks.append(_check("process.cwd", "skipped" if process is None else "passed" if process else "failed",
                         facts.get("process_cwd") or "The process directory could not be read"))
    checks.append(_check("shell.cwd", "passed" if yes("cwd_ok") else "failed", f"PowerShell started in {facts.get('cwd', '?')}"))
    if facts.get("expected"):
        checks.append(_check("read.absolute", "passed" if yes("abs_ok") else "failed", facts.get("abs_error", "")))
        checks.append(_check("read.relative", "passed" if yes("rel_ok") else "failed", facts.get("rel_error", "")))
    else:
        note = "The worktree has no Git link file to read"
        checks += [_check("read.absolute", "skipped", note), _check("read.relative", "skipped", note)]
    head = facts.get("head")
    ran = facts.get("git_exit") == "0" and (head is None or facts.get("git_head") == head)
    checks.append(_check("git.run", "passed" if ran else "failed",
                         "" if ran else f"git rev-parse: exit {facts.get('git_exit')}: {facts.get('git_head', '')}"))
    listed = facts.get("status_exit") == "0"
    checks.append(_check("git.metadata", "passed" if listed else "failed",
                         "" if listed else f"git status: exit {facts.get('status_exit')}: {facts.get('status', '')}"))
    written = facts.get("created") or facts.get("canary_intact") is False
    if not facts.get("fixture"):
        checks.append(_check("write.canary", "skipped", "The worktree is not in a folder PatchRondo manages"))
    elif not facts.get("canary_intact"):
        checks.append(_check("write.canary", "failed", "The canary file was changed or deleted from inside the sandbox"))
    elif yes("canary_seen") and "overwrite" in facts and "delete" in facts:
        checks.append(_check("write.canary", "passed", "Overwrite and delete were attempted; the file is identical"))
    else:
        checks.append(_check("write.canary", "skipped", "The canary file was not visible from the sandbox"))
    if facts.get("created"):
        checks.append(_check("write.create", "failed", "A file was created in the worktree from inside the sandbox"))
    elif "create" in facts:
        checks.append(_check("write.create", "passed", "Creation was attempted; no file exists"))
    else:
        checks.append(_check("write.create", "skipped", "The attempt was not made"))
    state = {check["id"]: check["status"] for check in checks}
    git_text = f"{facts.get('git_head', '')} {facts.get('status', '')}".lower()
    if written:
        kind = "sandbox_not_read_only"
    elif state["read.absolute"] == "failed":
        kind = "sandbox_access_denied"
    elif "failed" in (state["shell.cwd"], state["read.relative"], state["process.cwd"]):
        kind = "sandbox_cwd_unavailable"
    elif "dubious ownership" in git_text:
        kind = "git_dubious_ownership"
    elif "failed" in (state["git.run"], state["git.metadata"]):
        kind = "sandbox_git_failed"
    elif "passed" not in (state["write.canary"], state["write.create"]):
        kind = "sandbox_unverified"  # nothing proved that writing is refused
    else:
        kind = None
    return checks, kind
