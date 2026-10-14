"""Capabilities: what each executor of a task can do, as far as that can be observed.

A task is carried out by four executors, and a capability always belongs to
one of them, never to the task as a whole:

    developer   the agent that edits the task's copy of the project
    reviewer    the agent that reads it and judges the code
    runtime     PatchRondo itself: the commands the project authorized, and what Python does alone
    user        the person: consent, and checks in an environment PatchRondo cannot see

So a developer without a shell does not stop a criterion that asks for passing
tests: running them is a capability of the runtime.

Nothing here asks a model, and nothing is started to find out. A capability
has one of four states:

    verified   observed on this computer: a program found on PATH, a sandbox check that passed,
               a command that already ran for this task
    declared   follows from a configuration PatchRondo passes or enforces: a tool list, a sandbox
               mode, the consent to run the project's commands
    absent     not there, or forbidden by PatchRondo's own rules
    unknown    cannot be told from here

Detection never grants anything. What an agent may do is decided by its agent
settings, with their acknowledgements, and by the consent to run the project's
commands; this module only reads them.
"""
from __future__ import annotations

from pathlib import Path
import re
import shutil

from . import agents
from .activity import clip, redact
from .storage import now

VERSION = 1
VERIFIED, DECLARED, ABSENT, UNKNOWN = "verified", "declared", "absent", "unknown"
AVAILABLE = (VERIFIED, DECLARED)
EXECUTORS = ("developer", "reviewer", "runtime", "user")
EXECUTOR_LABELS = {"developer": "Developer", "reviewer": "Reviewer", "runtime": "PatchRondo", "user": "You"}

FILES_READ, FILES_WRITE, SHELL, NETWORK = "files.read", "files.write", "shell", "network"
GIT_COMMIT, GIT_PUSH = "git.commit", "git.push"
TESTS, BUILD, PACKAGE, HASH = "commands.tests", "commands.build", "commands.package", "hash"
CONSENT, MANUAL, EXTERNAL = "consent", "manual", "external"
LABELS = {
    FILES_READ: "Read the project's files", FILES_WRITE: "Change the project's files", SHELL: "Run commands",
    NETWORK: "Reach the network", GIT_COMMIT: "Commit", GIT_PUSH: "Push or open a pull request",
    TESTS: "Run the project's test commands", BUILD: "Run a build command of the project",
    PACKAGE: "Run a packaging command of the project", HASH: "Compute the SHA-256 of a file",
    CONSENT: "Authorize an operation", MANUAL: "Check something by hand and attach the evidence",
    EXTERNAL: "Use a program, a device or an account outside the project",
}
# Kinds of work a configured command does, and the capability of the runtime each one gives.
COMMAND_KINDS = {"tests": TESTS, "build": BUILD, "package": PACKAGE}

# Programs that build or package, by the name they are started with. A configured command is
# classified by its own words only: nothing is run to find out what it does.
BUILD_PROGRAMS = frozenset({"cmake", "make", "gmake", "nmake", "mingw32-make", "ninja", "msbuild", "cl", "g++", "gcc", "clang",
                            "clang++", "c++", "cc", "meson", "bazel", "scons", "xcodebuild", "tsc", "javac", "ant", "build"})
PACKAGE_PROGRAMS = frozenset({"zip", "7z", "7za", "tar", "cpack", "makensis", "iscc"})
# Tools with sub-commands: the first word after the program says what it does.
MULTI_PROGRAMS = frozenset({"cargo", "go", "dotnet", "mvn", "mvnw", "gradle", "gradlew", "npm", "yarn", "pnpm", "bun"})
BUILD_WORDS = frozenset({"build", "compile", "assemble"})
PACKAGE_WORDS = frozenset({"pack", "package", "publish"})
BUILD_MODULES = frozenset({"compileall"})
PACKAGE_MODULES = frozenset({"build", "zipfile", "tarfile"})
_PYTHON = re.compile(r"py(?:thon)?[\d.]*w?")
_SCRIPT = (".exe", ".cmd", ".bat", ".ps1", ".sh")

# Programs looked for on PATH when a criterion names a language or a build system.
TOOLCHAINS = {
    "cpp": ("cl", "g++", "clang++", "c++", "gcc", "clang"), "rust": ("cargo",), "go": ("go",),
    "dotnet": ("dotnet", "msbuild"), "java": ("javac", "mvn", "gradle"), "node": ("npm", "node"),
}
TOOLCHAIN_LABELS = {"cpp": "a C or C++ compiler", "rust": "the Rust toolchain", "go": "the Go toolchain",
                    "dotnet": "the .NET build tools", "java": "a Java build tool", "node": "Node.js"}
# Claude Code tools by what they do. Reading and editing tools are the ones `agents.FILE_TOOLS` lists; the
# command tools are the two named beside them in `agents.py`.
READ_TOOLS = frozenset({"Read", "Glob", "Grep"})
WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
COMMAND_TOOLS = frozenset({"Bash", "PowerShell"})
POLICY = "PatchRondo's instructions to the developer forbid it"


def tool(name: str) -> str:
    """The capability name of a toolchain."""
    return f"tool.{name}"


def label(capability: str) -> str:
    if capability.startswith("tool."):
        return f"Have {TOOLCHAIN_LABELS.get(capability[5:], capability[5:])} installed"
    return LABELS.get(capability, capability)


def _cap(state: str, source: str, **more) -> dict:
    return {"state": state, "source": source, **{key: value for key, value in more.items() if value is not None}}


def _program(argv: list[str]) -> tuple[str, list[str], bool]:
    """Name of the program a command starts, the words after it, and whether it is a Python module."""
    name = re.split(r"[\\/]", argv[0])[-1].lower()
    for suffix in _SCRIPT:
        name = name.removesuffix(suffix)
    rest = list(argv[1:])
    if _PYTHON.fullmatch(name) and "-m" in rest[:-1]:
        module = rest[rest.index("-m") + 1]
        return module.lower(), [word.lower() for word in rest[rest.index("-m") + 2:] if not word.startswith("-")], True
    return name, [word.lower() for word in rest if not word.startswith("-")], False


def covers(argv: list[str]) -> list[str]:
    """What a configured command does besides being part of the project's test run: "build", "package"."""
    name, words, module = _program(argv)
    kinds = ["tests"]  # every configured command belongs to the test run; a failure of any of them fails it
    first = words[:1]
    scripted = name in {"npm", "yarn", "pnpm", "bun"} and words[:1] == ["run"]
    if module:
        build, package = name in BUILD_MODULES, name in PACKAGE_MODULES
    else:
        build = name in BUILD_PROGRAMS or (name in MULTI_PROGRAMS and bool(set(first) & BUILD_WORDS)) \
            or (scripted and bool(set(words[1:2]) & BUILD_WORDS))
        package = name in PACKAGE_PROGRAMS or (name in MULTI_PROGRAMS and bool(set(first) & PACKAGE_WORDS)) \
            or (scripted and bool(set(words[1:2]) & PACKAGE_WORDS))
    return kinds + (["build"] if build else []) + (["package"] if package else [])


def shown(argv: list[str]) -> str:
    """A command as people read it: credentials masked, bounded."""
    return clip(redact(" ".join(argv)), 200)


def test_commands(cfg: dict) -> list[list[str]]:
    commands = (cfg.get("tests") or {}).get("commands") or []
    return [cmd for cmd in commands if isinstance(cmd, list) and cmd and all(isinstance(part, str) and part for part in cmd)]


def _developer(provider, values) -> dict:
    """What the developer's own configuration lets it do. `values` are its resolved agent settings."""
    found = {}
    if values is None or provider not in agents.PROVIDERS:
        unknown = _cap(UNKNOWN, "The agent settings of the developer could not be read")
        found.update({FILES_READ: unknown, FILES_WRITE: unknown, SHELL: unknown})
    elif provider == "claude":
        tools = list(values.get("tools") or agents.DEFAULT_TOOLS["developer"])
        listed = f"Tools passed to Claude Code: {', '.join(tools)}"
        commands = [name for name in tools if name in COMMAND_TOOLS]
        found[FILES_READ] = _cap(DECLARED if READ_TOOLS & set(tools) else ABSENT, listed)
        if values.get("permission_mode") == "plan":
            found[FILES_WRITE] = _cap(ABSENT, "Permission mode “plan” proposes changes without making them")
        else:
            found[FILES_WRITE] = _cap(DECLARED if WRITE_TOOLS & set(tools) else ABSENT, listed)
        found[SHELL] = _cap(DECLARED, f"{', '.join(commands)} is among its tools: commands run on this computer, unsandboxed") \
            if commands else _cap(ABSENT, "No tool that runs commands is among its tools")
    else:
        sandbox = values.get("sandbox") or "workspace-write"
        mode = f"Codex sandbox: {sandbox}"
        found[FILES_READ] = _cap(DECLARED, mode)
        found[FILES_WRITE] = _cap(ABSENT if sandbox == "read-only" else DECLARED, mode)
        found[SHELL] = _cap(DECLARED, f"{mode}: its commands run inside that sandbox" if sandbox != "danger-full-access"
                            else f"{mode}: its commands run without a sandbox")
    # Whatever tools it has, the developer is never asked to do these: the prompt PatchRondo writes forbids them.
    found[NETWORK] = _cap(ABSENT, f"{POLICY}: it never contacts external services and never installs packages")
    found[GIT_COMMIT] = _cap(ABSENT, f"{POLICY}: it never commits")
    found[GIT_PUSH] = _cap(ABSENT, f"{POLICY}: it never pushes")
    return found


def _reviewer(provider, state: dict) -> dict:
    """The reviewer is read-only whatever is set; a passed sandbox check makes that an observation."""
    never = "The reviewer is read-only by design"
    found = {NETWORK: _cap(ABSENT, never), GIT_COMMIT: _cap(ABSENT, never), GIT_PUSH: _cap(ABSENT, never)}
    if provider == "claude":
        listed = f"Tools passed to Claude Code: {', '.join(agents.DEFAULT_TOOLS['reviewer'])}"
        found.update({FILES_READ: _cap(DECLARED, listed), FILES_WRITE: _cap(ABSENT, f"{never}: its tool list has no editing tool"),
                      SHELL: _cap(ABSENT, f"{never}: its tool list has no tool that runs commands")})
    elif provider == "codex":
        record = state.get("environment") if isinstance(state.get("environment"), dict) else {}
        passed = {check.get("id") for check in record.get("checks") or []
                  if isinstance(check, dict) and check.get("status") == "passed"}
        proved = record.get("status") == "ready" and record.get("live") is True
        read = proved and {"read.absolute", "read.relative"} & passed
        refused = proved and {"write.canary", "write.create"} & passed
        when = f"the sandbox check of {record.get('checked_at')}" if proved else ""
        found[FILES_READ] = _cap(VERIFIED, f"Read in {when}") if read else _cap(DECLARED, "Codex sandbox: read-only")
        found[FILES_WRITE] = _cap(ABSENT, f"{never}: writing was refused in {when}" if refused else f"{never}: Codex sandbox read-only")
        found[SHELL] = _cap(DECLARED, "Codex sandbox: read-only. Its commands can read and cannot change anything")
    else:
        unknown = _cap(UNKNOWN, "Unknown reviewer")
        found.update({FILES_READ: unknown, FILES_WRITE: _cap(ABSENT, never), SHELL: unknown})
    return found


def _runtime(cfg: dict, state: dict, toolchains, which) -> dict:
    """What PatchRondo can do itself: the project's authorized commands, and nothing that needs a new privilege."""
    tests = cfg.get("tests") or {}
    commands = test_commands(cfg)
    allowed = bool(tests.get("enabled") is True and tests.get("trust_acknowledged") is True and commands)
    ran = any(isinstance(item, dict) and item.get("status") in {"passed", "failed", "timeout"} for item in state.get("tests") or [])
    missing = {"tests": "No test command is configured for this project", "build": "No command of this project runs a build",
               "package": "No command of this project creates a package"}
    found = {}
    for kind, name in COMMAND_KINDS.items():
        matching = [shown(cmd) for cmd in commands if kind in covers(cmd)]
        if not matching:
            found[name] = _cap(ABSENT, missing[kind])
        elif not allowed:
            found[name] = _cap(ABSENT, "Configured, but the project's commands are not allowed to run: tests are disabled or "
                                       "the project is not trusted", consent="missing", commands=matching)
        else:
            found[name] = _cap(VERIFIED if ran else DECLARED, "Commands you allowed for this project" +
                               (", already run for this task" if ran else ""), consent="granted", commands=matching)
    worktree = state.get("worktree")
    there = isinstance(worktree, str) and Path(worktree).is_dir()
    found[FILES_READ] = _cap(VERIFIED if there else ABSENT, "The task's copy of the project is on this computer" if there
                             else "The task's copy of the project was not found")
    found[HASH] = _cap(VERIFIED, "Computed by PatchRondo itself, without starting anything")
    found[NETWORK] = _cap(ABSENT, "PatchRondo never downloads anything and never contacts a service")
    found[GIT_COMMIT] = _cap(ABSENT, "PatchRondo never commits for you")
    found[GIT_PUSH] = _cap(ABSENT, "PatchRondo never pushes, merges or opens a pull request")
    for name in sorted(set(toolchains) & set(TOOLCHAINS)):
        programs = TOOLCHAINS[name]
        located = next(((program, path) for program in programs if (path := which(program))), None)
        found[tool(name)] = _cap(VERIFIED, f"{located[0]} found at {located[1]}") if located \
            else _cap(ABSENT, f"None of {', '.join(programs)} was found on PATH")
    return found


def _user() -> dict:
    return {CONSENT: _cap(DECLARED, "Asked in the interface, one decision at a time"),
            MANUAL: _cap(DECLARED, "Recorded when you attach the evidence of a check"),
            EXTERNAL: _cap(UNKNOWN, "PatchRondo cannot see which programs, devices or accounts you have")}


def detect(cfg: dict, state: dict, *, settings: dict | None = None, toolchains=(), which=None) -> dict:
    """The capabilities of the four executors of one task.

    `settings` holds the resolved agent settings of the developer (None when
    they cannot be read); `toolchains` names the toolchains the task's
    criteria need, which are looked for on PATH with `which` and never started.
    """
    which = which or shutil.which
    developer, reviewer = state.get("developer"), state.get("reviewer")
    return {"version": VERSION, "detected_at": now(), "executors": {
        "developer": {"provider": developer, "capabilities": _developer(developer, (settings or {}).get("developer"))},
        "reviewer": {"provider": reviewer, "capabilities": _reviewer(reviewer, state)},
        "runtime": {"provider": None, "capabilities": _runtime(cfg, state, toolchains, which)},
        "user": {"provider": None, "capabilities": _user()},
    }}


def lookup(detected: dict | None, executor: str, capability: str) -> dict:
    """One capability of one executor; unknown when nothing was detected for it."""
    found = (((detected or {}).get("executors") or {}).get(executor) or {}).get("capabilities") or {}
    entry = found.get(capability)
    return entry if isinstance(entry, dict) and entry.get("state") in (VERIFIED, DECLARED, ABSENT, UNKNOWN) \
        else _cap(UNKNOWN, "Not detected yet")


def has(detected: dict | None, executor: str, capability: str) -> bool:
    return lookup(detected, executor, capability)["state"] in AVAILABLE
