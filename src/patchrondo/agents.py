"""Agent settings: the model and the CLI options each role is started with.

A role (developer, reviewer) has its own settings for each provider that can
play it, so one provider can run both roles with two models. A setting is
looked up in three places, the nearest first:

    task      `agent_settings` in the task's state.json
    project   `agent_settings` in the project's config.json
    global    `settings` in <workspace>/agents.json

and falls back to PatchRondo's default, which starts the CLI exactly as the
versions without these settings did. In a level a missing key inherits, `null`
asks for the default, and anything else is the value. `resolve` returns every
value with the level it came from.

Settings never reach a CLI's own files: `command` turns them into arguments of
one invocation. A step keeps the settings it started with (`freeze`), so a
resume, a retry or an automatic recovery of that step runs with the same
command; an edit applies from the next step.

What may be set is a fixed table per provider, each entry typed and validated.
Sources, read on October 12, 2026:

- Claude Code: `claude --help` of 2.1.295 on native Windows and the CLI
  reference (https://code.claude.com/docs/en/cli-reference), which says that
  `--help` does not list every flag; `--max-turns` is one of those. Model
  aliases and effort levels: https://code.claude.com/docs/en/model-config.
- Codex: `codex --help`, `codex exec --help` and `codex debug models --help` of
  codex-cli 0.161.0 on native Windows, and the configuration reference
  (https://learn.chatgpt.com/docs/config-file/config-reference) for the keys
  passed with `-c`.

None of these options had been confirmed by a call to an authenticated CLI on
that day.

Limits that no setting moves: the reviewer stays read-only (Codex in its
`read-only` sandbox, Claude with its three reading tools), Claude always runs
`--restricted`, and an option that widens what a developer can touch is saved
only together with `risk_acknowledged: true` in the same place.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid

from .activity import clip, redact
from .commands import join_command
from .storage import FileMutex, now, read_json, save_json

ROLES = ("developer", "reviewer")
PROVIDERS = ("claude", "codex")
LEVELS = ("global", "project", "task")
STORE = "agents.json"
WORKSPACE = "PATCHRONDO_WORKSPACE"  # set for runs started by the interface: where agents.json is
ACK = "risk_acknowledged"
KEPT_RUNS = 60

MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,127}")
TOOL = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
DENY_RULE = re.compile(r"[A-Za-z_][A-Za-z0-9_*]{0,63}(?:\([^()\x00-\x1f]{1,200}\))?")
MCP_TOOL = re.compile(r"mcp__[A-Za-z0-9_-]{1,64}(?:__[A-Za-z0-9_*-]{1,64})?")
PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
CONFIG_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}(?:\.[A-Za-z0-9_-]{1,64}){0,5}")
PRESET_ID = re.compile(r"U-[a-f0-9]{8}")
MAX_ITEMS = 40

DEFAULT_TOOLS = {"developer": ("Read", "Glob", "Grep", "Edit", "Write"), "reviewer": ("Read", "Glob", "Grep")}
# Tools that only read or edit files inside the working directories. Any other tool (Bash, PowerShell,
# WebFetch, …) runs commands or reaches the network and is saved only with the acknowledgement.
FILE_TOOLS = frozenset({"Read", "Glob", "Grep", "Edit", "Write", "MultiEdit", "NotebookEdit", "TodoWrite"})
EFFORTS = {"claude": ("low", "medium", "high", "xhigh", "max"), "codex": ("low", "medium", "high", "xhigh", "max", "ultra")}

# Keys of the Codex configuration that a `-c` override may set. `CONFIG_REVIEWED` are documented keys
# that change how the model answers and nothing else; they need no acknowledgement.
CONFIG_REVIEWED = {
    "model_reasoning_summary": ("auto", "concise", "detailed", "none"), "model_verbosity": ("low", "medium", "high"),
    "service_tier": str, "tool_output_token_limit": int, "model_context_window": int,
    "model_auto_compact_token_limit": int, "hide_agent_reasoning": bool, "developer_instructions": str,
}
CONFIG_MANAGED = {"model": "Model", "model_reasoning_effort": "Reasoning effort", "sandbox_mode": "Sandbox",
                  "approval_policy": "Approval policy", "web_search": "Web search", "profile": "Profile"}
# Never passed, with or without acknowledgement: they weaken the sandbox, start a program, send the
# session to another endpoint, or show internal reasoning.
CONFIG_BLOCKED = ("sandbox_permissions", "sandbox_workspace_write", "shell_environment_policy", "model_provider",
                  "model_providers", "notify", "mcp_servers", "hooks", "projects", "profiles", "features", "permissions",
                  "default_permissions", "show_raw_agent_reasoning", "chatgpt_base_url", "openai_base_url")

# Custom arguments. `MANAGED` are the options PatchRondo passes itself or offers as a setting; `BLOCKED`
# are never passed. What is left is unknown to PatchRondo: developer only, with the acknowledgement.
ARGS_MANAGED = {
    "claude": {"--print", "--output-format", "--verbose", "--restricted", "--permission-mode", "--tools", "--allowedTools",
               "--allowed-tools", "--disallowedTools", "--disallowed-tools", "--max-turns", "--json-schema", "--model",
               "--effort", "--fallback-model", "--add-dir", "--max-budget-usd", "--append-system-prompt", "--mcp-config",
               "--strict-mcp-config", "--no-session-persistence"},
    "codex": {"--ask-for-approval", "--sandbox", "--cd", "--json", "--ephemeral", "--output-last-message",
              "--output-schema", "--model", "--profile", "--config", "--add-dir", "--ignore-user-config"},
}
ARGS_BLOCKED = {
    "claude": {"--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "--permission-prompt-tool",
               "--permission-prompts", "--include-partial-messages", "--forward-subagent-text", "--bare", "--settings",
               "--setting-sources", "--plugin-dir", "--plugin-url", "--agents", "--agent", "--bg", "--background", "--exec",
               "--cloud", "--remote", "--remote-control", "--rc", "--teleport", "--desktop", "--chrome", "--ide",
               "--worktree", "--tmux", "--resume", "--continue", "--fork-session", "--from-pr", "--session-id",
               "--input-format", "--replay-user-messages", "--debug-file", "--system-prompt", "--system-prompt-file",
               "--append-system-prompt-file", "--channels", "--dangerously-load-development-channels", "--init",
               "--init-only", "--maintenance", "--environment", "--ref", "--safe-mode"},
    "codex": {"--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust", "--approve-for-me",
              "--full-auto", "--yolo", "--oss", "--local-provider", "--remote", "--remote-auth-token-env", "--worktree",
              "--ignore-rules", "--skip-git-repo-check", "--enable", "--disable", "--search", "--image", "--color"},
}
# Options a CLI accepts without listing them in its help. Evidence: the Claude Code CLI reference
# (`--max-turns`, "print mode only") beside `claude --help` of 2.1.295, which does not print it.
DOCUMENTED = {"claude": {"--max-turns"}, "codex": set()}

CATEGORIES = (("model", "Model"), ("permissions", "Execution and permissions"), ("limits", "Limits"),
              ("access", "Folders and tools"), ("prompt", "Prompt"), ("session", "Session and streaming"),
              ("config", "Configuration overrides"), ("custom", "Custom arguments"))
FIXED = {"developer": "Only the developer can have this", "reviewer": "Only the reviewer can have this"}
READ_ONLY = "The reviewer is read-only by design: the environment check verifies exactly this before every review"


def _o(key: str, kind: str, label: str, text: str, category: str, **more) -> dict:
    return {"key": key, "kind": kind, "label": label, "help": text, "category": category, **more}


def _shared(stream_flag: str) -> list[dict]:
    """Settings that exist for both providers."""
    return [
        _o("timeout_seconds", "int", "Step timeout", "Longest single call of this agent. Unset: the project's agent timeout.",
           "limits", low=1, high=86400, unit="seconds"),
        _o("instructions", "text", "Extra instructions",
           "Added at the end of the prompt PatchRondo writes for this role. The task, the role and the answer format stay "
           "as they are.", "prompt", max=8000, multiline=True),
        _o("live_activity", "bool", "Live activity",
           "Ask the CLI to print what it does while it works. Off: the step shows its result when it ends. Partial "
           "messages stay off in both cases: they would carry the model's internal reasoning.", "session", default=True,
           flag=stream_flag),
        _o("extra_args", "args", "Custom arguments",
           "One argument per line, passed to the CLI as written, without a shell. Options PatchRondo sets itself and "
           "options that remove a protection are refused; long option names only.", "custom", roles=("developer",),
           locked="The reviewer's command line is fixed apart from the settings on this page",
           risk="Custom arguments are passed as they are: PatchRondo cannot check what they do"),
    ]


OPTIONS = {
    "claude": {option["key"]: option for option in [
        _o("model", "model", "Model", "An alias such as sonnet, or a full model name. Unset: the CLI's own default.", "model",
           flag="--model", basic=True, preset=True),
        _o("effort", "choice", "Effort", "How much the model reasons. Levels a model does not have run as the highest "
           "level it has at or below the chosen one.", "model", flag="--effort", choices=EFFORTS["claude"], basic=True,
           preset=True),
        _o("fallback_model", "text", "Fallback model", "Used when the model is overloaded or not available. One model, or "
           "several separated by commas, tried in order.", "model", flag="--fallback-model", preset=True,
           pattern=r"[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,127}(?:,[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,127}){0,4}"),
        _o("permission_mode", "choice", "Permission mode",
           "How tool use is decided. A step has nobody to answer a prompt, so only modes that never ask are offered.",
           "permissions", flag="--permission-mode", default="dontAsk",
           choices=("dontAsk", "acceptEdits", "plan", "auto"), only={"reviewer": ("dontAsk", "plan")},
           only_reason={"reviewer": "The reviewer never edits files"},
           refused={"bypassPermissions": "Refused by restricted mode, which PatchRondo always uses",
                    "manual": "Nobody can answer a permission prompt during a step",
                    "default": "Nobody can answer a permission prompt during a step"}),
        _o("tools", "list", "Tools", "The built-in tools the developer may use. File tools stay inside the task's copy of "
           "the project; a tool that runs commands or reaches the network is not sandboxed.", "access", flag="--tools",
           item=TOOL, minimum=1, roles=("developer",), locked="The reviewer keeps its three reading tools: Read, Glob, Grep",
           defaults={role: list(tools) for role, tools in DEFAULT_TOOLS.items()},
           risk="A tool outside the file tools runs commands or reaches the network on your computer, unsandboxed",
           safe_items=sorted(FILE_TOOLS), risky=lambda value: any(tool not in FILE_TOOLS for tool in value)),
        _o("disallowed_tools", "list", "Denied tools", "Deny rules added to the one PatchRondo always passes for MCP tools. "
           "A bare name removes the tool; Name(pattern) denies matching calls.", "access", flag="--disallowedTools",
           item=DENY_RULE),
        _o("add_dirs", "paths", "Additional folders", "Folders outside the task's copy that the file tools may also reach. "
           "The reviewer can read them; the developer can also change them.", "access", flag="--add-dir",
           risk={"developer": "The developer can change files in these folders, outside the task's own copy"}),
        _o("mcp_config", "path", "MCP configuration file", "A JSON file naming MCP servers. Only the servers in it are "
           "started (--strict-mcp-config), and only the MCP tools listed below may be used.", "access", flag="--mcp-config",
           roles=("developer",), locked="MCP servers can write and run programs; the reviewer has none",
           risk="MCP servers are programs that run on your computer with your permissions"),
        _o("mcp_tools", "list", "Allowed MCP tools", "Names such as mcp__server or mcp__server__tool. Used only with an "
           "MCP configuration file.", "access", flag="--allowedTools", item=MCP_TOOL, roles=("developer",),
           locked="MCP servers can write and run programs; the reviewer has none"),
        _o("max_turns", "int", "Max turns", "Turns one step may take. Unset: “Claude max turns” of the project's workflow "
           "settings.", "limits", flag="--max-turns", low=1, high=86400, unit="turns", preset=True),
        _o("max_budget_usd", "number", "Max budget", "Stops the step when Claude Code's own estimate of API spend reaches "
           "this amount. It is an estimate of API prices: with a subscription login it does not measure your plan's "
           "usage, and its meaning depends on how the CLI is logged in.", "limits", flag="--max-budget-usd", low=0.01,
           high=10000, unit="USD"),
        _o("append_system_prompt", "text", "Appended system prompt", "Text added after Claude Code's default system "
           "prompt, which stays in place.", "prompt", flag="--append-system-prompt", max=8000, multiline=True),
        _o("session_persistence", "bool", "Keep the session transcript", "On: Claude Code saves the session of each step in "
           "its own folder, as it always did. Off: nothing is saved by the CLI. PatchRondo never resumes a CLI session: "
           "every step is a new call that starts from the task's files.", "session", flag="--no-session-persistence",
           default=True),
        *_shared("--output-format"),
    ]},
    "codex": {option["key"]: option for option in [
        _o("model", "model", "Model", "A model of the CLI's catalog, or any model ID. Unset: the CLI's own default.", "model",
           flag="--model", basic=True, preset=True),
        _o("effort", "choice", "Reasoning effort", "Passed as the model_reasoning_effort override. A model accepts only the "
           "levels it advertises.", "model", flag="--config", choices=EFFORTS["codex"], basic=True, preset=True),
        _o("sandbox", "choice", "Sandbox", "What the commands of the agent may touch. workspace-write: the task's copy of "
           "the project. read-only: nothing. danger-full-access: the whole computer.", "permissions", flag="--sandbox",
           defaults={"developer": "workspace-write", "reviewer": "read-only"},
           choices=("read-only", "workspace-write", "danger-full-access"), only={"reviewer": ("read-only",)},
           only_reason={"reviewer": READ_ONLY},
           risk="danger-full-access removes the sandbox: the developer's commands can change anything you can",
           risky_values=["danger-full-access"], risky=lambda value: value == "danger-full-access"),
        _o("approval_policy", "choice", "Approval policy", "never: a command the sandbox refuses fails and the model is "
           "told. on-request: the model may ask, but a step has nobody to answer.", "permissions",
           flag="--ask-for-approval", default="never", choices=("never", "on-request")),
        _o("profile", "text", "Profile", "A profile of your Codex configuration, layered on your base configuration. The "
           "sandbox and the approval policy set here still apply.", "permissions", flag="--profile", pattern=PROFILE.pattern),
        _o("ignore_user_config", "bool", "Ignore the user configuration", "Do not load config.toml of the Codex home; the "
           "login is still used.", "permissions", flag="--ignore-user-config", default=False),
        _o("web_search", "choice", "Web search", "disabled: none. cached: an index kept by OpenAI, no external access. "
           "indexed, live: the model may read the web, which can carry instructions written by others.", "access",
           flag="--config", choices=("disabled", "cached", "indexed", "live"), preset=True),
        _o("add_dirs", "paths", "Additional folders", "Folders the developer may write beside the task's copy of the "
           "project.", "access", flag="--add-dir", roles=("developer",), locked=READ_ONLY,
           risk="The developer can change files in these folders, outside the task's own copy"),
        _o("config_overrides", "pairs", "Configuration overrides", "Keys of the Codex configuration for this call only "
           "(-c key=value); your config.toml is not changed. Keys that weaken the sandbox, start a program or redirect "
           "the session are refused.", "config", flag="--config",
           risk="A configuration key PatchRondo has not reviewed is passed as it is",
           safe_keys=sorted(CONFIG_REVIEWED), risky=lambda value: any(pair["key"] not in CONFIG_REVIEWED for pair in value)),
        _o("ephemeral", "bool", "Ephemeral session", "On: Codex saves no session files for the step, as PatchRondo always "
           "asked. Off: the session is kept by the CLI. PatchRondo never resumes a CLI session.", "session",
           flag="--ephemeral", default=True),
        *_shared("--json"),
    ]},
}
PRESET_KEYS = {name: tuple(key for key, option in options.items() if option.get("preset") or key == "timeout_seconds")
               for name, options in OPTIONS.items()}
BUILTIN_PRESETS = (
    {"id": "default", "name": "CLI default", "description": "Nothing is passed: each CLI uses its own model and effort.",
     "values": {"claude": {"model": None, "effort": None}, "codex": {"model": None, "effort": None}}},
    {"id": "fast", "name": "Fast", "description": "Quick answers for small, well-defined changes.",
     "values": {"claude": {"model": "haiku", "effort": "low"}, "codex": {"effort": "low"}}},
    {"id": "balanced", "name": "Balanced", "description": "Everyday work.",
     "values": {"claude": {"model": "sonnet", "effort": "medium"}, "codex": {"effort": "medium"}}},
    {"id": "quality", "name": "Max quality", "description": "Slower and more thorough; uses more of your plan.",
     "values": {"claude": {"model": "opus", "effort": "high"}, "codex": {"effort": "high"}}},
)
# Claude Code has no command that lists models. These are the aliases its documentation names; an
# alias follows the newest model of its family, and whether an account may use it is not known here.
CLAUDE_CATALOG = (
    ("sonnet", "Sonnet", "The latest Sonnet model, for daily coding tasks"),
    ("opus", "Opus", "The latest Opus model, for complex reasoning tasks"),
    ("haiku", "Haiku", "The fast Haiku model, for simple tasks"),
    ("fable", "Fable", "The Fable model, for the hardest and longest tasks; needs Fable access"),
    ("best", "Best available", "Fable where it is available to you, otherwise Opus"),
    ("opusplan", "Opus plan", "Opus while planning, Sonnet while executing"),
)


# --- validation ---------------------------------------------------------------------

def _text(value, label: str, limit: int, multiline: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{label} must have at most {limit} characters")
    if any(ord(char) < 32 and not (multiline and char in "\n\t") for char in value) or "\x7f" in value:
        raise ValueError(f"{label} must not contain control characters")
    # Prose is masked wherever it is shown; a short value that looks like a credential is not kept at all.
    if not multiline and redact(value) != value:
        raise ValueError(f"{label} looks like it contains a credential. PatchRondo does not store secrets: the CLIs use "
                         "their own login")
    return value


def _folder(value, label: str) -> str:
    text = _text(value, label, 1000)
    if not text or not Path(text).expanduser().is_absolute() or text.startswith(("\\\\", "//")):
        raise ValueError(f"{label}: use the absolute path of a local folder")
    return str(Path(text).expanduser())


def _items(value, label: str) -> list:
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise ValueError(f"{label} must be a list of at most {MAX_ITEMS} entries")
    return value


def _pairs(value, label: str) -> list[dict]:
    clean, seen = [], set()
    for pair in _items(value, label):
        if not isinstance(pair, dict) or set(pair) != {"key", "value"} or not isinstance(pair["key"], str) \
                or not CONFIG_KEY.fullmatch(pair["key"]):
            raise ValueError(f"{label}: each entry needs a key such as model_verbosity and a value")
        key, item = pair["key"], pair["value"]
        head = key.split(".")[0]
        if key in CONFIG_MANAGED:
            raise ValueError(f"{label}: {key} has its own setting ({CONFIG_MANAGED[key]}); set it there")
        if head in CONFIG_BLOCKED or key in CONFIG_BLOCKED:
            raise ValueError(f"{label}: {key} is not passed by PatchRondo. It could weaken the sandbox, start a program, "
                             "redirect the session or show internal reasoning")
        if key in seen:
            raise ValueError(f"{label}: {key} is set twice")
        seen.add(key)
        if isinstance(item, str):
            item = _text(item, f"{label} ({key})", 4000, multiline=key == "developer_instructions")
        elif type(item) not in (bool, int, float) or (isinstance(item, float) and not math.isfinite(item)):
            raise ValueError(f"{label}: the value of {key} must be text, a number or true/false")
        expected = CONFIG_REVIEWED.get(key)
        if isinstance(expected, tuple) and item not in expected:
            raise ValueError(f"{label}: {key} must be one of {', '.join(expected)}")
        if isinstance(expected, type) and (type(item) is not expected or (expected is int and item <= 0)):
            raise ValueError(f"{label}: {key} must be {'true or false' if expected is bool else 'a positive whole number' if expected is int else 'text'}")
        clean.append({"key": key, "value": item})
    return clean


def _args(provider: str, value, label: str) -> list[str]:
    clean = []
    for index, arg in enumerate(_items(value, label)):
        arg = _text(arg, label, 2000)
        if not arg:
            raise ValueError(f"{label}: an argument is empty")
        name = arg.split("=", 1)[0]
        if arg.startswith("--") and len(name) > 2:
            if name in ARGS_MANAGED[provider]:
                raise ValueError(f"{label}: {name} is set by PatchRondo or has its own setting on this page")
            if name in ARGS_BLOCKED[provider]:
                raise ValueError(f"{label}: {name} is never passed. It removes a protection, changes how the step is "
                                 "started or where its output goes")
        elif arg.startswith("-") and not re.fullmatch(r"-\d+(?:\.\d+)?", arg):
            raise ValueError(f"{label}: use the long form of {arg} (short options are refused: they can hide another one)")
        elif index == 0 or not clean[-1].startswith("--"):
            raise ValueError(f"{label}: {arg!r} is not an option. A value must follow the option it belongs to")
        clean.append(arg)
    return clean


def choices(option: dict, role: str) -> tuple:
    return option.get("only", {}).get(role) or option["choices"]


def default(option: dict, role: str):
    """PatchRondo's own value of a setting for a role; None when nothing is passed to the CLI."""
    return copy.deepcopy(option.get("defaults", {}).get(role, option.get("default")))


def _value(provider: str, role: str, option: dict, value):
    kind, label = option["kind"], option["label"]
    if kind == "model":
        if not isinstance(value, str) or not MODEL.fullmatch(value):
            raise ValueError(f"{label}: use a model name of letters, digits and . _ : / @ [ ] -, starting with a letter or a digit")
        return value
    if kind == "choice":
        if value in option.get("refused", {}):
            raise ValueError(f"{label}: {value} is not available. {option['refused'][value]}")
        if value not in option["choices"]:
            raise ValueError(f"{label} must be one of: {', '.join(option['choices'])}")
        if value not in choices(option, role):
            raise ValueError(f"{label}: {value} is not available for the {role}. {option['only_reason'][role]}")
        return value
    if kind == "text":
        text = _text(value, label, option.get("max", 200), option.get("multiline", False))
        if option.get("pattern") and not re.fullmatch(option["pattern"], text):
            raise ValueError(f"{label}: {text!r} is not a valid value")
        return text
    if kind in {"int", "number"}:
        if type(value) is not int and not (kind == "number" and type(value) is float and math.isfinite(value)):
            raise ValueError(f"{label} must be a {'whole ' if kind == 'int' else ''}number")
        if not option["low"] <= value <= option["high"]:
            raise ValueError(f"{label} must be between {option['low']} and {option['high']}")
        return value
    if kind == "bool":
        if type(value) is not bool:
            raise ValueError(f"{label} must be a JSON boolean (true/false)")
        return value
    if kind == "path":
        return _folder(value, label)
    if kind == "paths":
        return list(dict.fromkeys(_folder(item, label) for item in _items(value, label)))
    if kind == "pairs":
        return _pairs(value, label)
    if kind == "args":
        return _args(provider, value, label)
    entries = list(dict.fromkeys(_items(value, label)))
    if len(entries) < option.get("minimum", 0) or not all(isinstance(item, str) and option["item"].fullmatch(item) for item in entries):
        raise ValueError(f"{label}: {'at least one entry is needed and ' if option.get('minimum') else ''}every entry must "
                         "be a valid name")
    return entries


def risks(provider: str, role: str, values: dict) -> list[dict]:
    """What in `values` widens what the agent can touch: [{key, text}]. Empty for the defaults."""
    found = []
    for key, option in OPTIONS[provider].items():
        value, text = values.get(key), option.get("risk")
        if isinstance(text, dict):
            text = text.get(role)
        if text and value not in (None, "", [], False) and option.get("risky", bool)(value):
            found.append({"key": key, "text": text})
    return found


def clean_block(provider: str, role: str, raw, *, preset: bool = False) -> dict:
    """Validated settings of one role played by one provider, as one level stores them."""
    if raw is None:
        return {}
    if provider not in PROVIDERS or role not in ROLES:
        raise ValueError("Unknown provider or role")
    if not isinstance(raw, dict):
        raise ValueError(f"The {provider} settings of the {role} must be a JSON object")
    options = OPTIONS[provider]
    unknown = sorted(set(raw) - set(options) - {ACK})
    if unknown:
        raise ValueError(f"Unknown {provider} setting: {', '.join(unknown)}")
    clean = {}
    for key, value in raw.items():
        if key == ACK:
            continue
        option = options[key]
        if preset and key not in PRESET_KEYS[provider]:
            raise ValueError(f"A preset cannot hold {option['label']}")
        if role not in option.get("roles", ROLES):
            raise ValueError(f"{option['label']} cannot be set for the {role}. {option.get('locked') or FIXED[option['roles'][0]]}")
        clean[key] = None if value is None else _value(provider, role, option, value)
    found = risks(provider, role, clean)
    if found:
        if role == "reviewer":
            # No acknowledgement opens this: what the reviewer can reach is not the user's to widen from here.
            raise ValueError(f"{OPTIONS[provider][found[0]['key']]['label']}: not available for the reviewer. "
                             f"{found[0]['text']}, and the reviewer keeps to what PatchRondo has reviewed")
        if raw.get(ACK) is not True:
            raise ValueError(f"{OPTIONS[provider][found[0]['key']]['label']} needs your explicit acknowledgement "
                             f"({ACK} must be true): {found[0]['text']}")
        clean[ACK] = True  # kept only beside what it was given for
    return clean


def clean_layer(raw) -> dict:
    """Validated settings of one level: {role: {provider: {setting: value}}}, empty parts left out."""
    if raw is None:
        return {}
    if not isinstance(raw, dict) or set(raw) - set(ROLES):
        raise ValueError("Agent settings may only contain developer and reviewer")
    clean: dict = {}
    for role, blocks in raw.items():
        if blocks is None:
            continue
        if not isinstance(blocks, dict) or set(blocks) - set(PROVIDERS):
            raise ValueError(f"The settings of the {role} may only contain claude and codex")
        for provider, block in blocks.items():
            values = clean_block(provider, role, block)
            if values:
                clean.setdefault(role, {})[provider] = values
    return clean


def check_folders(provider: str, role: str, values: dict, *, private: tuple = (), repository: Path | None = None) -> None:
    """Refuse an additional folder that would give an agent PatchRondo's private state or the user's own checkout."""
    for text in values.get("add_dirs") or []:
        folder = Path(text).resolve()
        for other in private:
            other = Path(other).resolve()
            if folder == other or folder.is_relative_to(other) or other.is_relative_to(folder):
                raise ValueError(f"Additional folders: {text} holds PatchRondo's private state (tasks, logs and the copies "
                                 "of other tasks)")
        if role == "developer" and repository is not None:
            other = Path(repository).resolve()
            if folder == other or folder.is_relative_to(other) or other.is_relative_to(folder):
                raise ValueError(f"Additional folders: {text} is your own checkout of the project. A task works in its "
                                 "own copy so that your files are never touched")


# --- resolution ---------------------------------------------------------------------

def resolve(provider: str, role: str, layers) -> dict:
    """The settings in effect: {"values": every setting, "sources": {setting: level or "default"}}.

    `layers` is [(level, layer)] from the farthest to the nearest; the nearest
    level that has a key decides it, and `null` there means PatchRondo's default.
    """
    values = {key: default(option, role) for key, option in OPTIONS[provider].items()}
    sources = dict.fromkeys(values, "default")
    for level, layer in layers:
        block = ((layer or {}).get(role) or {}).get(provider) or {}
        for key, value in block.items():
            if key in values:
                values[key] = default(OPTIONS[provider][key], role) if value is None else copy.deepcopy(value)
                sources[key] = level
    return {"provider": provider, "role": role, "values": values, "sources": sources}


def sparse(provider: str, role: str, values: dict) -> dict:
    """Only the settings that differ from PatchRondo's defaults."""
    options = OPTIONS[provider]
    return {key: value for key, value in values.items() if key in options and value != default(options[key], role)}


def digest(provider: str, values: dict) -> str:
    return hashlib.sha256(json.dumps([provider, values], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def freeze(provider: str, role: str, layers) -> dict:
    """The record a step keeps from its first start: what differs from the defaults, and where each value came from."""
    resolved = resolve(provider, role, layers)
    values = sparse(provider, role, resolved["values"])
    if risks(provider, role, values):
        values[ACK] = True  # every level was validated with its own acknowledgement before it got here
    return {"provider": provider, "values": values, "frozen_at": now(), "digest": digest(provider, values),
            "sources": {key: resolved["sources"][key] for key in values if key != ACK}}


def thaw(record, provider: str, role: str) -> dict | None:
    """The values of a frozen record, validated again; None when the record is not one for this provider."""
    if not isinstance(record, dict) or record.get("provider") != provider or not isinstance(record.get("values"), dict):
        return None
    return clean_block(provider, role, record["values"])


def step_key(state: dict, role: str) -> str:
    """Name of a step in `agent_runs`. Padded, so the sorted keys of the saved file are in the order of the steps."""
    return f"{state['iteration']:03d}:{role}"


# --- commands -----------------------------------------------------------------------

def toml(value) -> str:
    """A value as the TOML literal `codex -c key=value` parses."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid TOML basic string


def command(provider: str, role: str, values: dict, *, workspace: Path, live: bool, turns: int = 12, schema: str = "",
            last_message: Path | str = "") -> list[str]:
    """The argument array of one step. With no settings it is the command PatchRondo always ran.

    `schema` is the review schema: its JSON text for Claude, the path of its
    file for Codex. Arguments are never joined into a command line: each one
    reaches the CLI as it is, whatever characters it holds. The CLIs are
    started by name, which on Windows finds an executable and never a
    `.cmd` launcher, so no command interpreter reads these arguments either.
    """
    if provider not in PROVIDERS or role not in ROLES:
        raise ValueError("Invalid provider or role")
    get = values.get
    reviewer = role == "reviewer"
    custom = [] if reviewer else list(get("extra_args") or [])
    if provider == "claude":
        tools = list(DEFAULT_TOOLS[role] if reviewer or not get("tools") else get("tools"))
        mcp = None if reviewer else get("mcp_config")
        deny = ([] if mcp else ["mcp__*"]) + list(get("disallowed_tools") or [])
        # Streaming only changes how output is printed: tools, permissions, sandbox and login are untouched.
        argv = ["claude", "-p", "--output-format", *(("stream-json", "--verbose") if live else ("json",)),
                "--restricted",
                "--permission-mode", get("permission_mode") or "dontAsk", "--tools", ",".join(tools),
                "--allowedTools", ",".join(tools + (list(get("mcp_tools") or []) if mcp else [])),
                *(["--disallowedTools", *deny] if deny else []),
                "--max-turns", str(get("max_turns") or turns)]
        if reviewer:
            argv += ["--json-schema", schema]
        for flag, key in (("--model", "model"), ("--effort", "effort"), ("--fallback-model", "fallback_model")):
            if get(key):
                argv += [flag, get(key)]
        for folder in get("add_dirs") or []:
            argv += ["--add-dir", folder]
        if get("max_budget_usd") is not None:
            argv += ["--max-budget-usd", repr(get("max_budget_usd"))]
        if get("append_system_prompt"):
            argv += ["--append-system-prompt", get("append_system_prompt")]
        if mcp:
            argv += ["--mcp-config", mcp, "--strict-mcp-config"]
        if get("session_persistence") is False:
            argv.append("--no-session-persistence")
        return argv + custom
    sandbox = "read-only" if reviewer else get("sandbox") or "workspace-write"
    argv = ["codex", "--ask-for-approval", get("approval_policy") or "never", "exec", *(("--json",) if live else ()), "--sandbox",
            sandbox,
            "--cd", str(workspace),
            *(() if get("ephemeral") is False else ("--ephemeral",)), "--output-last-message", str(last_message)]
    if reviewer:
        argv += ["--output-schema", schema]
    for flag, key in (("--model", "model"), ("--profile", "profile")):
        if get(key):
            argv += [flag, get(key)]
    if get("ignore_user_config"):
        argv.append("--ignore-user-config")
    overrides = [(name, get(key)) for name, key in (("model_reasoning_effort", "effort"), ("web_search", "web_search")) if get(key)]
    overrides += [(pair["key"], pair["value"]) for pair in get("config_overrides") or []]
    for key, value in overrides:
        argv += ["-c", f"{key}={toml(value)}"]
    for folder in () if reviewer else get("add_dirs") or []:
        argv += ["--add-dir", folder]
    return argv + custom + ["-"]


def verification(provider: str, model: str, effort: str | None, *, folder: Path, last_message: Path) -> list[str]:
    """The smallest call that shows whether a login can use a model: no tools, nothing written, one short answer.

    Claude gets its one reading tool in an empty folder (the runner passes no
    empty argument, so the documented `--tools ""` is not used);
    `--skip-git-repo-check` ("Allow running Codex outside a Git repository")
    is from `codex exec --help` of codex-cli 0.161.0.
    """
    if provider == "claude":
        return ["claude", "-p", "--output-format", "json", "--restricted", "--permission-mode", "dontAsk", "--tools", "Read",
                "--allowedTools", "Read", "--disallowedTools", "mcp__*", "--max-turns", "1", "--no-session-persistence",
                "--model", model,
                *(["--effort", effort] if effort else [])]
    return ["codex", "--ask-for-approval", "never", "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--cd", str(folder),
            "--ephemeral", "--output-last-message", str(last_message), "--model", model,
            *(["-c", f"model_reasoning_effort={toml(effort)}"] if effort else []), "-"]


def preview(argv: list[str]) -> dict:
    """A command as people read it: credentials masked, long values shortened. Never used to start anything."""
    shown, previous = [], ""
    for arg in argv:
        if previous == "--json-schema":
            arg = "<review schema>"
        shown.append(clip(redact(arg).replace("\n", " "), 160))
        previous = arg
    return {"argv": shown, "line": join_command(shown)}


def missing_folders(values: dict) -> list[str]:
    """Folders and files the command names that are not there; the CLIs refuse to start without them."""
    missing = [folder for folder in values.get("add_dirs") or [] if not Path(folder).is_dir()]
    if values.get("mcp_config") and not Path(values["mcp_config"]).is_file():
        missing.append(values["mcp_config"])
    return missing


# --- the store of the workspace ------------------------------------------------------

def workspace_root(home: Path) -> Path:
    """The folder whose agents.json holds the global settings of a project's state directory.

    A run started by the interface is told (`PATCHRONDO_WORKSPACE`). Otherwise
    a project created by 0.2 sits in `<workspace>/projects/<id>`, and any
    other state directory is its own workspace, as a 0.1 home was.
    """
    told = os.environ.get(WORKSPACE, "").strip()
    if told and Path(told).is_absolute():
        return Path(told)
    home = Path(home)
    if home.parent.name == "projects" and (home.parent.parent / "projects.json").is_file():
        return home.parent.parent
    return home


def clean_preset(raw) -> dict:
    if not isinstance(raw, dict) or set(raw) - {"id", "name", "values", "description"}:
        raise ValueError("A preset has a name and values")
    name = " ".join(raw["name"].split()) if isinstance(raw.get("name"), str) else ""
    if not 1 <= len(name) <= 60:
        raise ValueError("The preset name must have 1 to 60 characters")
    if name.casefold() in {preset["name"].casefold() for preset in BUILTIN_PRESETS} | {"custom"}:
        raise ValueError(f"“{name}” is the name of a built-in preset")
    values = raw.get("values")
    if not isinstance(values, dict) or not values or set(values) - set(PROVIDERS):
        raise ValueError("A preset holds values for claude, codex or both")
    # Checked as the reviewer would be: a preset fits both roles and never carries a risky option.
    clean = {provider: clean_block(provider, "reviewer", block, preset=True) for provider, block in values.items()}
    clean = {provider: block for provider, block in clean.items() if block}
    if not clean:
        raise ValueError("A preset needs at least one value")
    identifier = raw.get("id")
    if identifier is not None and not (isinstance(identifier, str) and PRESET_ID.fullmatch(identifier)):
        raise ValueError("Invalid preset ID")
    return {"id": identifier or "U-" + uuid.uuid4().hex[:8], "name": name, "values": clean}


def _store(raw) -> dict:
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError(f"Invalid agent settings file ({STORE}): version must be 1")
    models = raw.get("models") if isinstance(raw.get("models"), dict) else {}
    custom, verified = {}, {}
    for provider in PROVIDERS:
        entries = (models.get("custom") or {}).get(provider) or []
        custom[provider] = list(dict.fromkeys(item for item in entries if isinstance(item, str) and MODEL.fullmatch(item)))[:MAX_ITEMS]
        marks = (models.get("verified") or {}).get(provider) or {}
        verified[provider] = {name: mark for name, mark in marks.items()
                              if isinstance(name, str) and MODEL.fullmatch(name) and isinstance(mark, dict)} if isinstance(marks, dict) else {}
    presets = raw.get("presets") or []
    if not isinstance(presets, list):
        raise ValueError(f"Invalid agent settings file ({STORE}): presets must be a list")
    return {"version": 1, "settings": clean_layer(raw.get("settings")), "presets": [clean_preset(preset) for preset in presets][:MAX_ITEMS],
            "models": {"custom": custom, "verified": verified}}


def read_store(root: Path) -> dict:
    """Global settings, custom presets and what is known about models. A missing file is the defaults."""
    file = Path(root) / STORE
    if not file.is_file():
        return _store({"version": 1})
    try:
        return _store(read_json(file))
    except ValueError as exc:
        # Said with the way out: nothing here can be repaired from the page while the file cannot be read.
        raise ValueError(f"{file} cannot be used ({exc}). Correct it in an editor, or delete it to go back to the "
                         "defaults: PatchRondo does not overwrite a file it cannot read") from exc


def update_store(root: Path, change) -> dict:
    """Read, change and atomically rewrite agents.json under a cross-process mutex; the result is validated first."""
    file = Path(root) / STORE
    with FileMutex(file):
        store = read_store(root)
        change(store)
        store = _store(store)
        save_json(file, store)
    return store


def layers(root: Path, cfg: dict | None, state: dict | None) -> list[tuple[str, dict]]:
    """The three levels, validated, from the farthest to the nearest. Raises ValueError when one cannot be used."""
    found = [("global", read_store(root)["settings"])]
    for level, holder in (("project", cfg), ("task", state)):
        try:
            found.append((level, clean_layer((holder or {}).get("agent_settings"))))
        except ValueError as exc:
            raise ValueError(f"The {level}'s agent settings cannot be used: {exc}") from exc
    return found


# --- what the CLIs offer --------------------------------------------------------------

def compatibility(provider: str, capabilities: dict | None) -> dict:
    """For every setting, whether the installed CLI is known to accept the option it becomes.

    listed       the CLI's own help names the option
    documented   the documentation names it and says the help does not list everything
    unlisted     the help was read and does not name it: the setting is not offered
    unknown      the help could not be read
    """
    flags = (capabilities or {}).get("flags")
    result = {}
    for key, option in OPTIONS[provider].items():
        flag = option.get("flag")
        if flag is None:
            result[key] = "builtin"
        elif not isinstance(flags, list) or not flags:
            result[key] = "unknown"
        else:
            result[key] = "listed" if flag in flags else "documented" if flag in DOCUMENTED[provider] else "unlisted"
    return result


def catalog(provider: str, capabilities: dict | None, store: dict) -> dict:
    """Models to choose from. Being listed here never means that the account may use a model."""
    models, source = [], "none"
    if provider == "claude":
        source = "documented"
        models = [{"id": name, "label": label, "description": text, "efforts": list(EFFORTS["claude"]), "source": "documented"}
                  for name, label, text in CLAUDE_CATALOG]
    else:
        listed = (capabilities or {}).get("models")
        if isinstance(listed, list) and listed:
            source = (capabilities or {}).get("models_source") or "cli"
            models = [{**{key: entry.get(key) for key in ("id", "label", "description", "efforts", "default_effort")},
                       "source": source} for entry in listed if isinstance(entry, dict) and isinstance(entry.get("id"), str)
                      and MODEL.fullmatch(entry["id"])]
    known = {entry["id"] for entry in models}
    models += [{"id": name, "label": name, "description": "Added by you", "efforts": None, "source": "custom"}
               for name in store["models"]["custom"][provider] if name not in known]
    marks = store["models"]["verified"][provider]
    for entry in models:
        entry["verified"] = marks.get(entry["id"])
    return {"source": source, "models": models, "verified": marks}


def presets(store: dict) -> list[dict]:
    return [{**copy.deepcopy(preset), "builtin": True} for preset in BUILTIN_PRESETS] + \
           [{**copy.deepcopy(preset), "builtin": False} for preset in store["presets"]]


def preset_of(provider: str, values: dict, available: list[dict]) -> str:
    """The preset whose values for this provider are all in effect, or "custom"."""
    for preset in available:
        block = preset["values"].get(provider)
        if block and all(values.get(key) == value for key, value in block.items()):
            return preset["id"]
    return "custom"


def schema(provider: str, role: str, capabilities: dict | None) -> list[dict]:
    """The settings of one provider as the interface draws them for a role."""
    status = compatibility(provider, capabilities)
    fields = []
    for key, option in OPTIONS[provider].items():
        allowed = role in option.get("roles", ROLES)
        # `risky_values`, `safe_items` and `safe_keys` say which values of a setting need the acknowledgement.
        field = {name: option[name] for name in ("key", "kind", "label", "help", "category", "flag", "low", "high", "unit", "max",
                                                 "multiline", "basic", "risky_values", "safe_items", "safe_keys") if name in option}
        risk = option.get("risk")
        field.update(default=default(option, role), status=status[key], available=allowed,
                     locked=None if allowed else option.get("locked") or FIXED[option["roles"][0]],
                     risk=risk.get(role) if isinstance(risk, dict) else risk, preset=key in PRESET_KEYS[provider])
        if option["kind"] == "choice":
            offered = choices(option, role)
            field["choices"] = [{"value": value, "disabled": None if value in offered else option["only_reason"][role]}
                                for value in option["choices"]] + \
                               [{"value": value, "disabled": reason} for value, reason in option.get("refused", {}).items()]
        fields.append(field)
    return fields


# --- test configuration ---------------------------------------------------------------

def _finding(name: str, label: str, status: str, detail: str = "", fix: str = "") -> dict:
    return {"id": name, "label": label, "status": status, "detail": detail, "fix": fix}


def diagnose(provider: str, role: str, values: dict, *, status: dict | None, capabilities: dict | None, models: dict,
             folders: dict | None = None) -> list[dict]:
    """Checks that start nothing: installation, login, options, model, folders and risks of one configuration.

    `status` is the doctor's last answer for the CLI, `folders` names
    {label: path} that the step needs. A `warning` is something a person
    should know; only `failed` means the step would not start.
    """
    label = (status or {}).get("label") or provider
    found = []
    if not status or status.get("installed") is None:
        found.append(_finding("cli.installed", f"{label} is installed", "skipped", "The CLI has not been checked yet", "Press Refresh"))
    elif not status["installed"]:
        found.append(_finding("cli.installed", f"{label} is installed", "failed", "Not found on PATH", status.get("hint") or ""))
    else:
        found.append(_finding("cli.installed", f"{label} is installed", "passed", " · ".join(
            part for part in (status.get("version"), status.get("path")) if part)))
        login = status.get("authenticated")
        found.append(_finding("cli.login", f"{label} is logged in", "passed" if login else "failed" if login is False else "skipped",
                              (status.get("auth_method") or "") if login else "The login could not be confirmed",
                              "" if login else f"Run in a terminal: {status.get('login_command')}"))
    support = compatibility(provider, capabilities)
    used = sparse(provider, role, values)
    refused = [key for key in used if support.get(key) == "unlisted"]
    version = (status or {}).get("version") or "the installed version"
    if refused:
        found.append(_finding("options", "The installed CLI accepts these options", "failed", "; ".join(
            f"{OPTIONS[provider][key]['label']} ({OPTIONS[provider][key]['flag']})" for key in refused) +
            f" · not listed by {version}", "Update the CLI, or reset these settings"))
    elif any(state == "unknown" for state in support.values()):
        found.append(_finding("options", "The installed CLI accepts these options", "skipped",
                              "The CLI's help has not been read", "Press Refresh"))
    else:
        found.append(_finding("options", "The installed CLI accepts these options", "passed",
                              f"{len([key for key in used if key != ACK])} set, all named by {version} or its documentation"))
    model, effort = values.get("model"), values.get("effort")
    entry = next((item for item in models["models"] if item["id"] == model), None)
    mark = models["verified"].get(model) if model else None
    if not model:
        found.append(_finding("model", "Model", "passed", "The CLI's own default model is used"))
    elif mark and mark.get("ok"):
        found.append(_finding("model", "Model", "passed", f"{model} answered a test call on {mark.get('at')}"))
    elif mark:
        found.append(_finding("model", "Model", "failed", f"{model} failed a test call on {mark.get('at')}: {mark.get('message') or mark.get('kind')}",
                              "Choose another model, or test it again"))
    else:
        where = {"documented": "named by the documentation", "custom": "added by you"}.get((entry or {}).get("source"), "listed by the CLI") \
            if entry else "not in the list"
        found.append(_finding("model", "Model", "warning", f"{model} is {where}, but nothing confirms that your account can use it",
                              "Use “Test with a real call” to find out before a task does"))
    if effort and entry and entry.get("efforts") and effort not in entry["efforts"]:
        found.append(_finding("effort", "Effort", "failed" if provider == "codex" else "warning",
                              f"{model} advertises {', '.join(entry['efforts'])}, not {effort}", "Choose one of those levels"))
    for name, path in (folders or {}).items():
        there = Path(path).is_dir()
        found.append(_finding(f"folder.{name}", f"The {name} is there", "passed" if there else "failed", str(path),
                              "" if there else "The folder was not found"))
    gone = missing_folders(values)
    if gone:
        found.append(_finding("folders", "Additional folders and files exist", "failed", ", ".join(gone), "Create them, or remove them from the settings"))
    for risk in risks(provider, role, values):
        found.append(_finding(f"risk.{risk['key']}", f"{OPTIONS[provider][risk['key']]['label']}: acknowledged risk", "warning", risk["text"]))
    return found
