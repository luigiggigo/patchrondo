"""Availability and login of the official CLIs, without any model call.

Used by `patchrondo doctor`, the setup wizard and the Agents settings. The
checks run `--version` and the CLI's own login-status command; they never send
a prompt and never read credentials.

`discover` asks an installed CLI what it offers: the options its help names
and, for Codex, its model catalog and the profiles of its configuration. It
calls no model either, and it starts the real CLI only through
`providers.probe`, which refuses while provider commands are simulated.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil

from . import providers
from .process import execute
from .storage import now

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
HELP_COMMANDS = {"claude": (("claude", "--help"),), "codex": (("codex", "--help"), ("codex", "exec", "--help"))}
# `codex debug models` ("Render the raw model catalog as JSON") and its `--bundled` form ("Skip refresh
# and dump only the bundled catalog shipped with this binary"), read from the help of codex-cli 0.161.0
# on native Windows on October 12, 2026; the bundled form was run there the same day. Claude Code has no
# command that lists models.
CATALOG_COMMANDS = ((("codex", "debug", "models"), "cli"), (("codex", "debug", "models", "--bundled"), "bundled"))
HELP_BYTES, CATALOG_BYTES = 400_000, 8_000_000
FLAG = re.compile(r"(?<![\w-])--[A-Za-z][A-Za-z0-9-]+")
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,127}")
PROFILE_FILE = re.compile(r"([A-Za-z0-9][A-Za-z0-9_.-]{0,63})\.config\.toml")


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


def _asked(argv: tuple, cwd: Path, timeout: int, limit: int) -> tuple[int | None, str]:
    """Standard output of one model-free command, up to `limit` bytes: (exit code or None on timeout, text)."""
    chunks: list[bytes] = []
    size = 0

    def keep(chunk: bytes) -> None:
        nonlocal size
        if size < limit:
            chunks.append(chunk[:limit - size])
            size += len(chunks[-1])

    result = providers.probe(list(argv), cwd, timeout=timeout, on_stdout=keep)
    return (None if result.timed_out else result.returncode), b"".join(chunks).decode("utf-8", errors="replace")


def catalog_models(obj) -> list[dict]:
    """The models of a Codex catalog that the CLI itself lists, with the reasoning levels each advertises."""
    entries = obj.get("models") if isinstance(obj, dict) else None
    models = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("slug"), str) or not MODEL_ID.fullmatch(entry["slug"]) \
                or entry.get("visibility", "list") != "list":
            continue
        levels = entry.get("supported_reasoning_levels")
        efforts = [level["effort"] for level in levels if isinstance(level, dict) and isinstance(level.get("effort"), str)
                   and re.fullmatch(r"[a-z]{2,16}", level["effort"])] if isinstance(levels, list) else []
        models.append({"id": entry["slug"], "label": str(entry.get("display_name") or entry["slug"])[:80],
                       "description": str(entry.get("description") or "")[:200], "efforts": efforts or None,
                       "default_effort": entry.get("default_reasoning_level") if entry.get("default_reasoning_level") in efforts else None})
    return models[:100]


def codex_profiles() -> list[str]:
    """Names of the profile files in the Codex home (`<name>.config.toml`). Only file names are read."""
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        return sorted(match.group(1) for file in home.iterdir() if (match := PROFILE_FILE.fullmatch(file.name)))[:40]
    except OSError:
        return []


def discover(status: dict, cwd: Path | None = None) -> dict | None:
    """What an installed CLI offers, asked without any model call.

    None when there is nothing to ask: the CLI is not installed, the
    executable the check found is not a file, or provider commands are
    simulated. A question that fails leaves its part empty and a note; an
    empty part means "not known", never "not supported".
    """
    name = status.get("name")
    if name not in PROVIDERS or not status.get("installed") or not status.get("path") \
            or not Path(status["path"]).is_file() or providers.simulated():
        return None
    cwd = cwd or Path.cwd()
    found = {"flags": [], "models": [], "models_source": None, "profiles": [], "notes": [], "checked_at": now(),
             "version": status.get("version")}
    flags: set[str] = set()
    for argv in HELP_COMMANDS[name]:
        try:
            code, text = _asked(argv, cwd, 20, HELP_BYTES)
        except (OSError, RuntimeError, ValueError) as exc:
            code, text = None, str(exc)
        if code == 0:
            flags.update(FLAG.findall(text))
        else:
            found["notes"].append(f"{' '.join(argv)} did not answer")
    found["flags"] = sorted(flags)
    if name == "codex":
        for argv, source in CATALOG_COMMANDS:
            try:
                code, text = _asked(argv, cwd, 30, CATALOG_BYTES)
                models = catalog_models(json.loads(text)) if code == 0 else []
            except (OSError, RuntimeError, ValueError):
                models = []
            if models:
                found.update(models=models, models_source=source)
                break
            found["notes"].append(f"{' '.join(argv)} gave no model list")
        found["profiles"] = codex_profiles()
    return found


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
