"""Availability and login of the official CLIs, without any model call.

Used by `patchrondo doctor`, the setup wizard and the Agents settings. The
checks run `--version` and the CLI's own login-status command; they never send
a prompt and never read credentials.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil

from .process import execute

PROVIDERS = {
    "claude": {
        "label": "Claude Code", "auth": ["claude", "auth", "status"], "version": ["claude", "--version"],
        "install": "Install Claude Code 2.1.248 or later (it must support --restricted), then run: claude auth login",
        "login": "claude auth login",
        "docs": "https://code.claude.com/docs/en/cli-reference",
    },
    "codex": {
        "label": "Codex CLI", "auth": ["codex", "login", "status"], "version": ["codex", "--version"],
        "install": "Install Codex CLI (npm install -g @openai/codex), then run: codex login",
        "login": "codex login",
        "docs": "https://learn.chatgpt.com/docs/auth",
    },
}
SUBSCRIPTION_METHODS = {"claude.ai", "oauth_token"}


def check(name: str, cwd: Path | None = None) -> dict:
    """Status of one provider CLI: installed, version, login and any billing warning."""
    spec = PROVIDERS[name]
    result = {"name": name, "label": spec["label"], "installed": False, "path": None, "version": None,
              "authenticated": None, "auth_method": None, "warning": None, "hint": spec["install"],
              "login_command": spec["login"], "docs": spec["docs"]}
    path = shutil.which(name)
    if not path:
        return result
    cwd = cwd or Path.cwd()
    result.update(installed=True, path=path, hint=None)
    try:
        version = execute(spec["version"], cwd, timeout=20)
        shown = (version.stdout.strip() or version.stderr.strip()).splitlines()
        if version.returncode == 0 and shown:
            result["version"] = shown[0][:80]
        login = execute(spec["auth"], cwd, timeout=20)
    except OSError as exc:
        result.update(warning=f"Found at {path} but it could not be started: {exc}",
                      hint="Check that the command on PATH is the official executable")
        return result
    if login.timed_out:
        result.update(hint=f"The login check timed out; run: {' '.join(spec['auth'])}")
        return result
    result["authenticated"] = login.returncode == 0
    if not result["authenticated"]:
        result["hint"] = f"Not logged in, or the login could not be confirmed. Run: {spec['login']}"
    elif name == "claude":
        try:
            method = json.loads(login.stdout).get("authMethod", "unknown")
        except (json.JSONDecodeError, AttributeError):
            method = "unknown"
        result["auth_method"] = method if isinstance(method, str) else "unknown"
        if result["auth_method"] not in SUBSCRIPTION_METHODS:
            result["warning"] = "Check whether API billing is being used instead of your subscription"
    return result


def check_all(cwd: Path | None = None) -> dict[str, dict]:
    return {name: check(name, cwd) for name in PROVIDERS}


def report_lines(status: dict) -> list[str]:
    """The lines `patchrondo doctor` prints for one provider."""
    name = status["name"]
    if not status["installed"]:
        return [f"{name}: NOT INSTALLED"]
    if status["authenticated"] is None and status["warning"]:
        return [f"{name}: installed · {status['warning']}"]
    if name == "claude" and status["authenticated"]:
        lines = [f"claude: installed · authenticated · method {status['auth_method']}"]
        if status["warning"]:
            lines.append("  Warning: check whether API billing is being used")
        return lines
    return [f"{name}: installed · auth {'OK' if status['authenticated'] else 'NOT CONFIRMED'}"]
