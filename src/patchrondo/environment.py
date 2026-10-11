"""Environment manager: prepare and verify what a task needs before any agent is called.

One check goes through three stages and stops at the first problem:

    agents    the CLIs of the task's developer and reviewer are installed and logged in
    project   the task worktree is there and Git accepts it
    sandbox   the reviewer can read the worktree and cannot write it (`preflight`)

A problem that `repair` may fix by itself is fixed and the stage is checked
again, at most `repair.MAX_REPAIRS` times; a repair that needs the user's
decision is applied only when that decision is passed in. The outcome is one
record, saved by the engine in the task state under `environment`:

    status                 ready | repairable | blocked
    error_kind             stable code of the problem, None when ready
    cause                  the exact finding behind it
    message                a sentence for a person
    repair_available       a repair exists that was not applied, or can be tried again
    repair_applied         a repair was applied during this check
    requires_user_action   nothing more happens until the user acts
    checks                 every check with its own result and technical detail
    repairs                what was changed, with the previous state of each change

Nothing in a check calls a model. The checks that start a real CLI (its login
status, its sandbox self-check) run only when `live`: with simulated providers
they are reported as skipped. Checks never run the target repository's tests
and never change the test consent.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from . import doctor, preflight, repair, winacl
from .gitops import GitError, git
from .storage import now

SWITCH = "PATCHRONDO_PREFLIGHT"
VERSION = 1
STAGES = ("agents", "project", "sandbox")
STAGE_LABELS = {"agents": "Checking the agents", "project": "Preparing the project", "sandbox": "Checking the safe environment"}
KEPT_REPAIRS = 20
# The reviewer of a provider listed here runs its commands in an operating-system sandbox.
SANDBOXED_REVIEWERS = {"codex"}


def enabled() -> bool:
    """False when the environment switches the checks off; a run then starts as it did before they existed."""
    return os.environ.get(SWITCH, "1").strip().lower() not in {"0", "false", "off", "no"}


def system() -> dict:
    """Operating system, Python and Git of this computer. Starts `git --version` and nothing else."""
    info = {"os": platform.system(), "release": platform.release(), "python": platform.python_version(),
            "python_supported": sys.version_info >= (3, 11),
            "git": {"installed": False, "path": shutil.which("git"), "version": None},
            "acl_policy": winacl.AVAILABLE}
    if info["git"]["path"]:
        try:
            done = subprocess.run(["git", "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=10, stdin=subprocess.DEVNULL, check=False)
            if done.returncode == 0:
                info["git"].update(installed=True, version=done.stdout.strip().removeprefix("git version ")[:60])
        except (OSError, subprocess.TimeoutExpired):
            pass
    return info


def _check(name: str, stage: str, label: str, status: str, detail: str = "") -> dict:
    return {"id": name, "stage": stage, "label": label, "status": status, "detail": detail[:500]}


def _skipped(stage: str, checks: dict[str, str], reason: str) -> list[dict]:
    return [_check(name, stage, label, "skipped", reason) for name, label in checks.items()]


class Manager:
    """Runs the checks. `sandbox`, `check_cli` and `acl` are replaceable for tests."""

    def __init__(self, *, sandbox: preflight.Sandbox | None = None, check_cli=None, acl=winacl):
        self.sandbox = sandbox or preflight.Sandbox()
        self.check_cli = check_cli or doctor.check
        self.acl = acl

    # --- what the result depends on -------------------------------------------------

    def signature(self, home: Path, worktree: Path, reviewer: str, live: bool) -> str:
        """Digest of what the sandbox stage depends on; a ready result is reused only while it is unchanged."""
        parts = [str(VERSION), os.name, reviewer, "live" if live else "static", os.path.normcase(os.path.realpath(worktree))]
        if reviewer in SANDBOXED_REVIEWERS:
            parts.append(repr(self.sandbox.identity()) if live else "")
            report = self.acl.inspect(home, worktree) if self.acl.AVAILABLE else {"status": "unavailable", "folders": []}
            parts.append(report["status"])
            parts += [folder["sddl"] for folder in report["folders"]]
        return hashlib.sha256("\0".join(parts).encode("utf-8", errors="replace")).hexdigest()

    # --- stages ---------------------------------------------------------------------

    def _agents(self, state: dict, worktree: Path, live: bool) -> tuple[list[dict], dict | None]:
        checks, problem = [], None
        for name in dict.fromkeys((state["developer"], state["reviewer"])):
            label = f"{doctor.PROVIDERS[name]['label']} is installed and logged in"
            if not live:
                checks.append(_check(f"cli.{name}", "agents", label, "skipped", "Provider commands are simulated"))
                continue
            status = self.check_cli(name, worktree)
            if not status["installed"]:
                checks.append(_check(f"cli.{name}", "agents", label, "failed", status["hint"] or "Not found on PATH"))
                problem = problem or {"kind": "cli_missing", "cause": "cli_not_on_path", "provider": name,
                                      "message": f"{status['label']} is not installed on this computer. {status['hint']}"}
            elif status["authenticated"] is False:
                checks.append(_check(f"cli.{name}", "agents", label, "failed", status["hint"] or "Not logged in"))
                problem = problem or {"kind": "authentication", "cause": "cli_not_logged_in", "provider": name,
                                      "message": f"{status['label']} is not logged in. Run this in a terminal, then "
                                                 f"resume: {status['login_command']}"}
            else:
                confirmed = status["authenticated"] is True
                detail = " · ".join(part for part in (status["version"], None if confirmed else "login not confirmed",
                                                      status["warning"]) if part)
                checks.append(_check(f"cli.{name}", "agents", label, "passed" if confirmed else "skipped", detail))
        return checks, problem

    def _project(self, worktree: Path) -> tuple[list[dict], dict | None, str | None]:
        label = "Git accepts the task folder"
        try:
            head = git("rev-parse", "HEAD", cwd=worktree)
        except GitError as exc:
            if exc.kind != "git_dubious_ownership":
                raise
            return ([_check("git.ownership", "project", label, "failed", str(exc))],
                    {"kind": "git_dubious_ownership", "cause": "git_dubious_ownership", "provider": None,
                     "message": repair.PROBLEMS["git_dubious_ownership"].message, "repair": "git_trust"}, None)
        return [_check("git.ownership", "project", label, "passed", f"HEAD {head[:12]}")], None, head

    def _static(self, home: Path, worktree: Path) -> tuple[list[dict], dict | None]:
        """What the access control lists say, without starting anything. Windows only.

        A folder the policy does not cover gets no verdict here: the check is
        skipped and the sandbox itself answers.
        """
        label = "The task folder is readable by the reviewer"
        if not self.acl.AVAILABLE:
            return [_check("acl.worktree", "sandbox", label, "skipped", "Access control lists are checked on Windows only")], None
        report = self.acl.inspect(home, worktree)
        folders = "; ".join(f"{folder['role']}: {folder['access']}" for folder in report["folders"])
        if report["status"] == "granted":
            return [_check("acl.worktree", "sandbox", label, "passed", folders)], None
        if report["status"] == "missing":
            return ([_check("acl.worktree", "sandbox", label, "failed",
                            f"{folders}. No access entry lets the reviewer's restricted token read these folders")],
                    {"kind": "sandbox_access_denied", "cause": "acl_missing_read", "provider": "codex", "repair": "acl",
                     "message": repair.PROBLEMS["sandbox_access_denied"].message})
        if report["status"] == "denied":
            return ([_check("acl.worktree", "sandbox", label, "failed",
                            f"{folders}. A deny entry was set on these folders; PatchRondo does not remove it")],
                    {"kind": "sandbox_access_denied", "cause": "acl_deny_entry", "provider": "codex",
                     "message": repair.PROBLEMS["sandbox_access_denied"].message})
        return [_check("acl.worktree", "sandbox", label, "skipped", report["reason"] or report["status"])], None

    def _sandbox(self, home: Path, worktree: Path, head: str | None, live: bool) -> tuple[list[dict], dict | None]:
        checks, problem = self._static(home, worktree)
        if problem is not None:
            return checks + _skipped("sandbox", preflight.CHECKS, "Not probed: the folder is not readable yet"), problem
        reason = "Provider commands are simulated" if not live else self.sandbox.unavailable(worktree)
        if reason:
            return checks + _skipped("sandbox", preflight.CHECKS, reason), None
        probed, kind = preflight.evaluate(self.sandbox.probe(home, worktree, head))
        checks += [{**check, "stage": "sandbox"} for check in probed]
        if kind is None:
            return checks, None
        failed = "; ".join(f"{check['id']}: {check['detail']}" for check in probed if check["status"] == "failed")
        problem = {"kind": kind, "cause": kind, "provider": "codex", "message": repair.PROBLEMS[kind].message,
                   "detail": failed[:800]}
        if repair.PROBLEMS[kind].repair == "acl":
            # The lists already grant reading, or the folder is not one the policy covers: nothing left to repair.
            covered = checks[0]["status"] == "passed"
            problem["cause"] = "sandbox_denied_despite_access_entries" if covered else "worktree_not_managed"
        elif repair.PROBLEMS[kind].repair:
            problem["repair"] = repair.PROBLEMS[kind].repair
        return checks, problem

    # --- the check ------------------------------------------------------------------

    def check(self, home: Path, state: dict, *, live: bool, confirmed=(), note=None, previous: dict | None = None,
              reviewer_only: bool = False, repository: Path | None = None) -> dict:
        """Check, repair what may be repaired, check again, and return the record.

        `confirmed` names the repairs the user decided for this check.
        `note(event, **fields)` is told each stage and repair as it happens.
        With `reviewer_only` the agents stage is not repeated, and a ready
        `previous` record is returned as it is while its signature still holds.
        """
        note = note or (lambda *_args, **_fields: None)
        worktree, reviewer = Path(state["worktree"]), state["reviewer"]
        sandboxed = reviewer in SANDBOXED_REVIEWERS
        record = {"version": VERSION, "status": "ready", "checked_at": now(), "live": bool(live), "signature": None,
                  "error_kind": None, "cause": None, "message": None, "provider": None, "repair": None,
                  "repair_available": False, "repair_applied": False, "requires_user_action": False,
                  "checks": [], "repairs": list((previous or {}).get("repairs") or [])[-KEPT_REPAIRS:], "attempts": 0}
        if reviewer_only and isinstance(previous, dict) and previous.get("status") == "ready" \
                and previous.get("signature") == self.signature(home, worktree, reviewer, live):
            return {**previous, "reused": True}

        def finish(problem: dict | None) -> dict:
            if problem is not None:
                available = bool(problem.get("repair"))
                record.update(status="repairable" if available else "blocked", error_kind=problem["kind"],
                              cause=problem.get("cause"), message=problem["message"], provider=problem.get("provider"),
                              repair=problem.get("repair"), repair_available=available,
                              requires_user_action=not available or problem.get("repair") not in repair.AUTOMATIC
                              or record["attempts"] > 0)
                if problem.get("detail"):
                    record["detail"] = problem["detail"]
            else:
                record["signature"] = self.signature(home, worktree, reviewer, live)
            record["repairs"] = record["repairs"][-KEPT_REPAIRS:]
            return record

        if not reviewer_only:
            note("stage", stage="agents", text=STAGE_LABELS["agents"])
            checks, problem = self._agents(state, worktree, live)
            record["checks"] += checks
            if problem is not None:
                return finish(problem)
        elif isinstance(previous, dict):
            record["checks"] += [check for check in previous.get("checks") or [] if check.get("stage") == "agents"]

        note("stage", stage="project", text=STAGE_LABELS["project"])
        applied: set[str] = set()
        while True:
            checks, problem, head = self._project(worktree)
            if problem is None or "git_trust" not in confirmed or "git_trust" in applied:
                break
            applied.add("git_trust")
            note("repair", repair="git_trust", text="Recording this project as trusted by Git")
            outcome = repair.git_trust([folder for folder in (repository, worktree) if folder is not None])
            record["repairs"].append(outcome)
            record["attempts"] += 1
            record["repair_applied"] = record["repair_applied"] or outcome["outcome"] == "applied"
        record["checks"] += checks
        if problem is not None:
            return finish(problem)

        note("stage", stage="sandbox", text=STAGE_LABELS["sandbox"])
        if not sandboxed:
            record["checks"] += _skipped("sandbox", {"sandbox.none": "The reviewer needs no sandbox check"},
                                         "This reviewer is limited by its tool list, not by an operating-system sandbox")
            return finish(None)
        while True:
            checks, problem = self._sandbox(home, worktree, head, live)
            wanted = problem.get("repair") if problem else None
            if wanted != "acl" or record["attempts"] >= repair.MAX_REPAIRS or "acl" in applied:
                break
            applied.add("acl")
            note("repair", repair="acl", text="Fixing an access problem with the project")
            outcome = repair.acl(home, worktree, self.acl)
            record["repairs"].append(outcome)
            record["attempts"] += 1
            record["repair_applied"] = record["repair_applied"] or outcome["outcome"] == "applied"
        record["checks"] += checks
        return finish(problem)
