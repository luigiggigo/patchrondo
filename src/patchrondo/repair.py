"""Auto-repair: what each environment problem is, and the one thing that may be done about it.

`PROBLEMS` gives every problem a stable code, a sentence a person can read and
a category:

- `repairable`  PatchRondo may fix it by itself, inside the policy of `winacl`
- `consent`     a fix exists, and it is a decision only the user can take
- `action`      only the user can do it: install a CLI, log in
- `temporary`   it passes by waiting (a usage limit); `recovery` handles it
- `permanent`   retrying cannot change it; the task stops where it can resume

Two repairs exist. `acl` runs unattended: it adds read access for the current
user to the worktrees folder of the project, nothing else, and puts the
previous state back if the result does not verify. `git_trust` runs only when
the user confirmed it for that task: it records the task's own repository and
worktree, by their exact paths, as trusted by Git for this user. Nothing here
weakens a sandbox, skips an approval, elevates, or enables tests, and no
repair is attempted more than `MAX_REPAIRS` times in one check.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess

from . import winacl
from .storage import now

MAX_REPAIRS = 2  # repairs applied in one environment check, each followed by a new check
AUTOMATIC = frozenset({"acl"})
GIT_TIMEOUT = 20


@dataclass(frozen=True)
class Problem:
    category: str
    message: str
    repair: str | None = None  # the repair that addresses it, when its exact cause allows one


PROBLEMS = {
    "sandbox_access_denied": Problem("repairable", "Codex cannot read the copy of the project prepared for this task.", "acl"),
    "sandbox_cwd_unavailable": Problem("repairable", "Codex would start outside the copy of the project prepared for "
                                       "this task.", "acl"),
    "sandbox_not_read_only": Problem("permanent", "The review environment of Codex could change files here, so the "
                                     "review was not started."),
    "sandbox_unverified": Problem("permanent", "PatchRondo could not confirm that the review environment of Codex "
                                  "is read-only, so the review was not started."),
    "sandbox_unavailable": Problem("permanent", "Codex could not start its protected review environment on this computer."),
    "sandbox_git_failed": Problem("permanent", "Codex cannot use Git inside its protected review environment."),
    "git_dubious_ownership": Problem("consent", "Git does not trust the folder of this project because it belongs to "
                                     "another user of this computer.", "git_trust"),
    "cli_missing": Problem("action", "An agent this task needs is not installed on this computer."),
    "authentication": Problem("action", "An agent this task needs is not logged in."),
    "quota": Problem("temporary", "The provider reported a usage limit."),
    "max_turns": Problem("permanent", "Claude used all the turns allowed for one step."),
    "tests_disabled": Problem("consent", "The project's tests are not authorized."),
}


def category(kind) -> str | None:
    problem = PROBLEMS.get(kind) if isinstance(kind, str) else None
    return problem.category if problem else None


def acl(home: Path, worktree: Path, manager=winacl) -> dict:
    """The unattended repair: read access for the current user on the worktrees folder and the worktree."""
    outcome = manager.repair(home, worktree)
    record = {"repair": "acl", "at": now(), "changed": outcome["changed"], "restored": outcome["restored"],
              "error": outcome["error"]}
    record["outcome"] = ("applied" if outcome["status"] == "granted" and outcome["changed"] else
                         "unchanged" if outcome["status"] == "granted" else
                         "rolled_back" if outcome["restored"] and outcome["changed"] else "failed")
    return record


def _safe_directories() -> list[str]:
    done = subprocess.run(["git", "config", "--global", "--get-all", "safe.directory"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT, check=False)
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]  # exit 1 means the key is not set


def git_trust(folders: list[Path]) -> dict:
    """The confirmed repair: tell Git that these exact folders are trusted for this user.

    This is the remedy Git itself prints, applied to the listed folders only.
    A wildcard is never written, and a folder already listed is not added again.
    """
    record = {"repair": "git_trust", "at": now(), "changed": [], "restored": False, "error": None, "outcome": "unchanged"}
    try:
        listed = {os.path.normcase(entry) for entry in _safe_directories()}
        for folder in folders:
            folder = Path(folder)
            if not folder.is_absolute() or not folder.is_dir():
                raise ValueError(f"Not an existing folder: {folder}")
            entry = folder.resolve().as_posix()
            if "*" in entry or os.path.normcase(entry) in listed:
                continue
            done = subprocess.run(["git", "config", "--global", "--add", "safe.directory", entry], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT, check=False)
            if done.returncode:
                raise RuntimeError(done.stderr.strip() or "git config failed")
            listed.add(os.path.normcase(entry))
            record["changed"].append({"path": entry, "added": "safe.directory entry in the user's Git configuration",
                                      "undo": "git config --global --fixed-value --unset-all safe.directory "
                                              f'"{entry}"'})
        record["outcome"] = "applied" if record["changed"] else "unchanged"
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        record.update(outcome="failed", error=str(exc)[:300])
    return record
