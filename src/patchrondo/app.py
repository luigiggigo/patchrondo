"""Application layer of the local interface: every operation the HTTP API exposes.

No HTTP here and no orchestration logic either: tasks are created by
`core.create_task`, runs are separate `patchrondo run` processes, and the
configuration is validated by `core.validate_config` before it is written. This
module adds what several projects and a browser need on top of the engine:
summaries, bounded file views, duplicate-start protection and process truth.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import platform
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from typing import NamedTuple

from . import __version__, acceptance as criteria, activity, agents, doctor, environment, evidence, providers, rag, recovery, repair
from . import runinfo, streams
from .commands import join_command, split_command
from .core import (TASK_OVERRIDES, VALID_AGENTS, WORKFLOW_LIMITS, AcceptanceRefused, attach_evidence, config, create_task,
                   decide_authorization, effective_config, preflight, recheck_acceptance, record_environment,
                   refresh_agent_settings, validate_config, validate_overrides)
from .storage import FileMutex, LockBusy, TaskLock, now, private_dir, read_json, save_json
from .workspace import NotFound, Project, Workspace

TASK_ID = re.compile(r"T-[a-f0-9]{12}")
RUN_LOG = "ui-run.log"
STARTUP_GRACE_SECONDS = 1.5
TEXT_LIMIT = 200_000
CHUNK_LIMIT = 256_000
DIFF_LIMIT = 400_000
UNTRACKED_FILES = 60
UNTRACKED_BYTES = 64_000
# Directory containing the imported package, so child runs work without installation.
PACKAGE_ROOT = str(Path(__file__).resolve().parents[1])
SUMMARY_FIELDS = ("id", "title", "status", "phase", "iteration", "developer", "reviewer",
                  "created_at", "updated_at")
DOCUMENTS = {"task": "task.md", "handoff": "handoff.md", "feedback": "feedback.md", "report": "report.md"}
LOG_SUFFIXES = (".log", ".md", ".txt")
CONFIG_SECTIONS = ("workflow", "tests", "rag", "recovery", "agents")
WORKFLOW_FIELDS = ("max_iterations", "agent_timeout_seconds", "test_timeout_seconds", "claude_max_turns",
                   "allow_no_changes")
RAG_LIMITS = {"max_chunks": (1, 50), "max_chars": (1000, 100000)}
REPO_TTL_SECONDS = 30
PROVIDER_TTL_SECONDS = 300
CAPABILITY_TTL_SECONDS = 3600  # what a CLI offers is asked again after this long, or when the user refreshes
VERIFY_TIMEOUT_SECONDS = 180
QUICK_TITLE = 80
ENVIRONMENT_FIELDS = ("status", "stage", "error_kind", "cause", "message", "provider", "repair", "repair_available",
                      "repair_applied", "requires_user_action", "checked_at")
CONFIRMABLE_REPAIRS = {"git_trust"}  # repairs that are a decision of the user and never run unattended


class RunsAtExit(NamedTuple):
    active: list[subprocess.Popen]
    pending: int  # run starts still in progress when the wait timed out

    @property
    def any(self) -> bool:
        return bool(self.active or self.pending)


class Conflict(Exception):
    """The requested action does not fit the current state."""


def _unverified(path: Path, runtime: dict) -> str:
    """What a person has to check before an unverifiable process stops counting as attached."""
    files = ", ".join(str(path / runinfo.MARKERS / f"{pid}.json") for pid in runtime["unverified"])
    return (f"PID {', '.join(str(pid) for pid in runtime['unverified'])}: stop it if it is a PatchRondo run; "
            f"if it is not, or it is gone, delete {files}")


def _text(path: Path, tail: bool = False) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-TEXT_LIMIT:] if tail else text[:TEXT_LIMIT]


RACY_NANOSECONDS = 2_000_000_000


def _signature(path: Path) -> tuple[int, int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size, info.st_ino


def _settled(signature: tuple[int, int, int]) -> bool:
    """Whether a cached read of this file can be trusted while its signature stays the same.

    A file written twice within the timestamp granularity of its filesystem can
    keep its time and size, so a file modified in the last two seconds is read
    again instead.
    """
    return signature[0] < time.time_ns() - RACY_NANOSECONDS


def _git(root: Path, *args: str, limit: int = 0, timeout: int = 20) -> tuple[str, bool]:
    """Read-only Git output for display: (text, truncated).

    Never takes optional locks and never starts a file-system monitor program
    named by the repository's configuration.
    """
    command = ["git", "--no-optional-locks", "-c", "core.quotepath=false", "-c", "core.fsmonitor=false",
               "-C", str(root), *args]
    if not limit:
        try:
            done = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Git did not answer in time") from exc
        if done.returncode:
            message = done.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Git: {message or 'command failed'}")
        return done.stdout.decode("utf-8", errors="replace").rstrip("\r\n"), False
    # A diff can be arbitrarily large: read what is shown, then stop the command.
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        timer = threading.Timer(timeout, process.kill)
        timer.start()
        try:
            data = process.stdout.read(limit + 1)
        finally:
            timer.cancel()
            process.kill()
    return data[:limit].decode("utf-8", errors="replace").rstrip("\r\n"), len(data) > limit


def repository_state(root: Path) -> dict:
    """Branch, head and cleanliness of a repository, for display and for the new-task check."""
    info = {"branch": None, "head": None, "dirty": None, "error": None}
    try:
        info["branch"] = _git(root, "rev-parse", "--abbrev-ref", "HEAD")[0] or None
        info["head"] = _git(root, "rev-parse", "--short", "HEAD")[0] or None
        info["dirty"] = bool(_git(root, "status", "--porcelain")[0])
    except (OSError, RuntimeError) as exc:
        info["error"] = str(exc)[:300]
    return info


def inspect_repository(workspace: Workspace, raw) -> dict:
    """Validate a path chosen in the interface before it is registered. Runs Git read-only."""
    result = {"path": None, "root": None, "is_root": False, "name": None, "branch": None, "head": None,
              "dirty": None, "registered": None, "ok": False, "problems": [], "warnings": []}
    if not isinstance(raw, str) or not raw.strip():
        result["problems"].append("Enter the path of a local Git repository")
        return result
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        result["problems"].append("Use an absolute path")
        return result
    try:
        path = path.resolve()
    except OSError as exc:
        result["problems"].append(f"That path cannot be read: {exc}")
        return result
    result["path"] = str(path)
    if not path.is_dir():
        result["problems"].append("That folder does not exist")
        return result
    try:
        root = Path(_git(path, "rev-parse", "--show-toplevel")[0]).resolve()
    except (OSError, RuntimeError):
        result["problems"].append("That folder is not inside a Git repository. Run git init and make a first "
                                  "commit, or choose another folder")
        return result
    result.update(root=str(root), is_root=root == path, name=root.name or "Project")
    if root != path:
        result["problems"].append("That folder is inside a repository. Use the repository root instead")
        return result
    state = repository_state(root)
    result.update(branch=state["branch"], head=state["head"], dirty=state["dirty"])
    if state["head"] is None:
        result["problems"].append("The repository has no commits yet. Make a first commit: tasks start from it")
    if workspace.root == root or workspace.root.is_relative_to(root):
        result["problems"].append("PatchRondo keeps its private state inside this repository; choose another "
                                  "repository or start PatchRondo with another --home")
    for project in workspace.projects():
        repo = project.repository
        if repo and Path(repo).resolve() == root:
            result["registered"] = project.name
            result["problems"].append(f'This repository is already registered as "{project.name}"')
    if state["dirty"]:
        result["warnings"].append("There are uncommitted changes. Commit or stash them before creating a task")
    result["ok"] = not result["problems"]
    return result


def browse(raw) -> dict:
    """List the sub-folders of one local folder for the folder picker. Names only, never file contents."""
    if raw is None or raw == "":
        base = Path.home()
    elif isinstance(raw, str):
        base = Path(raw).expanduser()
    else:
        raise ValueError("path must be text")
    if not base.is_absolute():
        raise ValueError("Use an absolute path")
    base = base.resolve()
    if not base.is_dir():
        raise FileNotFoundError("That folder does not exist")
    entries, truncated = [], False
    try:
        with os.scandir(base) as scan:
            for entry in sorted(scan, key=lambda item: item.name.casefold()):
                try:
                    if entry.name.startswith(".") or not entry.is_dir():
                        continue
                except OSError:
                    continue
                if len(entries) >= 500:
                    truncated = True
                    break
                entries.append({"name": entry.name, "path": str(base / entry.name),
                                "repo": os.path.exists(os.path.join(entry.path, ".git"))})
    except PermissionError as exc:
        raise ValueError("That folder cannot be read") from exc
    roots = [str(Path.home())]
    if os.name == "nt":
        roots += [f"{letter}:\\" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{letter}:\\")]
    else:
        roots.append("/")
    return {"path": str(base), "parent": str(base.parent) if base.parent != base else None,
            "repo": (base / ".git").exists(), "entries": entries, "truncated": truncated, "roots": roots}


class Providers:
    """Cached CLI status. Checks run in a background thread and never call a model."""

    def __init__(self, cwd: Path, on_change=None):
        self.cwd, self.on_change = cwd, on_change
        self.lock = threading.Lock()
        self.data: dict | None = None
        self.system: dict | None = None
        self.checked_at: str | None = None
        self.checked_monotonic = 0.0
        self.checking = False
        # What each CLI says it offers (`doctor.discover`): kept here for the agent settings, not sent with the status.
        self.capabilities: dict[str, dict | None] = {}
        self._asked: dict[str, tuple] = {}
        self.force = False

    def view(self, refresh: bool = False) -> dict:
        with self.lock:
            stale = self.data is None or time.monotonic() - self.checked_monotonic > PROVIDER_TTL_SECONDS
            if (refresh or stale) and not self.checking:
                self.checking = True
                self.force = self.force or refresh
                threading.Thread(target=self._check, name="patchrondo-providers", daemon=True).start()
            return {"checking": self.checking, "checked_at": self.checked_at, "providers": self.data,
                    "system": self.system}

    def _discover(self, name: str, status: dict, cwd: Path, force: bool) -> dict | None:
        key = (status.get("path"), status.get("version"))
        known = self._asked.get(name)
        if known and known[0] == key and not force and time.monotonic() - known[1] < CAPABILITY_TTL_SECONDS:
            return known[2]
        try:
            found = doctor.discover(status, cwd)
        except Exception:  # noqa: BLE001 - not knowing what a CLI offers never breaks its status
            found = None
        self._asked[name] = (key, time.monotonic(), found)
        return found

    def _check(self) -> None:
        cwd = self.cwd if self.cwd.is_dir() else Path.home()
        with self.lock:
            force, self.force = self.force, False
        try:
            data = {name: dict(status) for name, status in doctor.check_all(cwd).items()}
        except Exception as exc:  # noqa: BLE001 - shown in the interface instead of killing the thread
            data = {name: {"name": name, "label": spec["label"], "installed": None, "path": None, "version": None,
                           "authenticated": None, "auth_method": None, "warning": f"Check failed: {exc}",
                           "hint": spec["install"], "login_command": spec["login"], "docs": spec["docs"]}
                    for name, spec in doctor.PROVIDERS.items()}
        capabilities = {}
        for name, status in data.items():
            given = status.pop("capabilities", None)  # a scripted status may say what its CLI offers
            capabilities[name] = given if isinstance(given, dict) else self._discover(name, status, cwd, force)
        try:
            system = environment.system()
        except Exception:  # noqa: BLE001 - the indicator shows "unknown" instead of killing the thread
            system = None
        with self.lock:
            self.data, self.system, self.capabilities = data, system, capabilities
            self.checked_at, self.checked_monotonic = now(), time.monotonic()
            self.checking = False
        if self.on_change:
            self.on_change()


class App:
    def __init__(self, root: Path, run_command=None):
        self.ws = Workspace(root)
        self.run_command = run_command or self._run_command
        self.children: dict[tuple[str, str], list[subprocess.Popen]] = {}
        self.closing = False
        self.starting = 0
        self.starting_tasks: set[tuple[str, str]] = set()
        self.runs = threading.Condition()  # guards children, closing, starting and starting_tasks
        self.changed = threading.Event()  # set by writes here so the live stream scans at once
        self.providers = Providers(self.ws.root, on_change=self.changed.set)
        self.environment = environment.Manager()  # checks and repairs asked for from the interface
        self._cache_lock = threading.Lock()
        self._states: dict[Path, tuple] = {}
        self._judged: dict[Path, tuple] = {}  # task folder: (signatures of state.json and the ledger, counts, settled)
        self._configs: dict[str, tuple] = {}
        self._repos: dict[str, tuple[float, dict]] = {}
        self._index_jobs: dict[str, dict] = {}
        self._requests: OrderedDict[str, dict] = OrderedDict()
        self._request_lock = threading.Lock()

    # --- helpers --------------------------------------------------------------------

    def once(self, request_id, action):
        """Run `action` once per client-supplied request ID, so a repeated request cannot create twice."""
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 64:
            return action()
        with self._request_lock:
            entry = self._requests.get(request_id)
            if entry is None:
                entry = self._requests[request_id] = {"lock": threading.Lock()}
                while len(self._requests) > 256:
                    self._requests.popitem(last=False)
        with entry["lock"]:
            if "result" not in entry:
                entry["result"] = action()  # a failure stores nothing, so the request can be retried
            return entry["result"]

    def platform(self) -> dict:
        release = platform.release().lower()
        return {"system": platform.system(), "posix": os.name == "posix",
                "wsl": "microsoft" in release or "wsl" in release,
                "stop_supported": os.name == "posix", "unlock_supported": os.name == "posix",
                "python": platform.python_version()}

    def _state(self, file: Path) -> dict | None:
        signature = _signature(file)
        if signature is None:
            return None
        with self._cache_lock:
            cached = self._states.get(file)
        if cached and cached[0] == signature and cached[2]:
            return cached[1]
        try:
            state = read_json(file)
        except (OSError, ValueError):
            return None  # being replaced atomically: the next scan reads it
        if not isinstance(state, dict):
            return None
        with self._cache_lock:
            self._states[file] = (signature, state, _settled(signature))
        return state

    def _owned(self, key: tuple[str, str]) -> list[subprocess.Popen]:
        with self.runs:
            return list(self.children.get(key, ()))

    def task_path(self, project: Project, task_id) -> Path:
        if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
            raise FileNotFoundError("Task not found")
        path = project.home / "tasks" / task_id
        if not (path / "state.json").is_file():
            raise FileNotFoundError("Task not found")
        return path

    # --- configuration --------------------------------------------------------------

    def _config(self, project: Project) -> tuple[dict | None, str | None]:
        """Validated project configuration, cached until the file changes."""
        signature = _signature(project.home / "config.json")
        with self._cache_lock:
            cached = self._configs.get(project.id)
        if cached and cached[0] == signature and cached[3]:
            return cached[1], cached[2]
        try:
            cfg, error = config(project.home), None
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            cfg, error = None, str(exc)
        with self._cache_lock:
            self._configs[project.id] = (signature, cfg, error, signature is not None and _settled(signature))
        return cfg, error

    @staticmethod
    def _compact(cfg: dict) -> dict:
        tests, workflow = cfg["tests"], cfg["workflow"]
        return {"tests_enabled": tests["enabled"], "trust_acknowledged": tests["trust_acknowledged"],
                "test_commands": len(tests.get("commands") or []),
                "max_iterations": workflow["max_iterations"],
                "agent_timeout_seconds": workflow["agent_timeout_seconds"],
                "test_timeout_seconds": workflow["test_timeout_seconds"],
                "rag_enabled": cfg["rag"]["enabled"], "recovery_enabled": cfg["recovery"]["enabled"],
                "agents": dict(cfg.get("agents") or {})}

    def config_view(self, project: Project) -> dict:
        """Everything the settings pages edit, with the limits the engine enforces."""
        cfg = config(project.home)
        tests = cfg["tests"]
        commands = [cmd for cmd in tests.get("commands") or []
                    if isinstance(cmd, list) and all(isinstance(part, str) for part in cmd)]
        return {
            "repository": cfg["repository"],
            "workflow": {field: cfg["workflow"][field] for field in WORKFLOW_FIELDS},
            "tests": {"enabled": tests["enabled"], "trust_acknowledged": tests["trust_acknowledged"],
                      "commands": commands, "command_lines": [join_command(cmd) for cmd in commands]},
            "rag": dict(cfg["rag"]),
            "recovery": dict(cfg["recovery"]),
            "agents": dict(cfg.get("agents") or {}),
            "limits": {"workflow": {key: list(value) for key, value in WORKFLOW_LIMITS.items()},
                       "rag": {key: list(value) for key, value in RAG_LIMITS.items()},
                       "recovery": {key: list(value) for key, value in recovery.LIMITS.items()}},
            "quoting": "windows" if os.name == "nt" else "posix",
        }

    def update_config(self, project: Project, patch: dict) -> dict:
        """Apply edited sections to a project's config.json: validate the candidate, then write it atomically."""
        unknown = sorted(set(patch) - set(CONFIG_SECTIONS))
        if unknown:
            raise ValueError(f"Unknown configuration section: {', '.join(unknown)}")
        file = project.home / "config.json"
        with FileMutex(file):
            raw = read_json(file)
            if not isinstance(raw, dict):
                raise ValueError("The saved configuration is not a JSON object")
            current = validate_config(copy.deepcopy(raw), project.home)
            candidate = copy.deepcopy(raw)
            for section in ("workflow", "rag", "recovery"):
                values = patch.get(section)
                if values is None:
                    continue
                if not isinstance(values, dict):
                    raise ValueError(f"{section} must be a JSON object")
                allowed = set(WORKFLOW_FIELDS) if section == "workflow" else set(current[section])
                extra = sorted(set(values) - allowed)
                if extra:
                    raise ValueError(f"Unknown {section} setting: {', '.join(extra)}")
                # Start from the values in effect, so a section missing in an older file is written whole.
                candidate[section] = {**current[section], **values}
            if patch.get("tests") is not None:
                candidate["tests"] = self._tests_section(patch["tests"])
            if "agents" in patch:
                agents = patch["agents"]
                if agents is None or agents == {}:
                    candidate.pop("agents", None)  # inherit the global defaults again
                elif not isinstance(agents, dict):
                    raise ValueError("agents must be a JSON object")
                else:
                    candidate["agents"] = {key: value for key, value in agents.items() if value is not None}
            validate_config(copy.deepcopy(candidate), project.home)  # nothing invalid reaches the disk
            save_json(file, candidate)
        self.changed.set()
        return self.config_view(project)

    @staticmethod
    def _tests_section(values) -> dict:
        """Test settings always arrive whole: consent is never inherited from a previous save."""
        if not isinstance(values, dict) or set(values) != {"enabled", "trust_acknowledged", "commands"}:
            raise ValueError("tests must contain exactly enabled, trust_acknowledged and commands")
        enabled, trust, lines = values["enabled"], values["trust_acknowledged"], values["commands"]
        if type(enabled) is not bool or type(trust) is not bool:
            raise ValueError("enabled and trust_acknowledged must be booleans")
        if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
            raise ValueError("commands must be a list of strings")
        return {"enabled": enabled, "trust_acknowledged": trust,
                "commands": [split_command(line) for line in lines if line.strip()]}

    # --- projects -------------------------------------------------------------------

    def _repo(self, project: Project, repository: str | None, refresh: bool = False) -> dict:
        if not repository:
            return {"branch": None, "head": None, "dirty": None, "error": "No repository configured"}
        with self._cache_lock:
            cached = self._repos.get(project.id)
        if cached and not refresh and time.monotonic() - cached[0] < REPO_TTL_SECONDS:
            return cached[1]
        root = Path(repository)
        info = repository_state(root) if root.is_dir() else {
            "branch": None, "head": None, "dirty": None, "error": "The repository folder was not found"}
        with self._cache_lock:
            self._repos[project.id] = (time.monotonic(), info)
        return info

    def project_info(self, project: Project, refresh: bool = False) -> dict:
        cfg, error = self._config(project)
        repository = cfg["repository"] if cfg else project.repository
        return {"id": project.id, "name": project.name, "home": str(project.home), "origin": project.origin,
                "added_at": project.added_at, "repository": repository,
                "config": self._compact(cfg) if cfg else None, "config_error": error,
                "repo": self._repo(project, repository, refresh), "index": self.index_status(project, light=True)}

    def project_detail(self, project: Project) -> dict:
        info = self.project_info(project, refresh=True)
        try:
            info["settings"] = self.config_view(project)
        except (OSError, ValueError, RuntimeError, KeyError):
            info["settings"] = None
        info["index"] = self.index_status(project)
        return info

    def add_project(self, payload: dict) -> dict:
        path, name = payload.get("path"), payload.get("name")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path must be the absolute path of a Git repository")
        if not Path(path.strip()).expanduser().is_absolute():
            raise ValueError("Use an absolute path")

        def create() -> dict:
            project = self.ws.add(Path(path.strip()), name if name else None)
            self.changed.set()
            return self.project_info(project, refresh=True)

        return self.once(payload.get("request_id"), create)

    def import_project(self, payload: dict) -> dict:
        home, name = payload.get("home"), payload.get("name")
        if not isinstance(home, str) or not home.strip() or not Path(home.strip()).expanduser().is_absolute():
            raise ValueError("home must be the absolute path of an existing PatchRondo state directory")
        project = self.ws.import_home(Path(home.strip()), name if name else None)
        self.changed.set()
        info = self.project_info(project, refresh=True)
        info["tasks"] = len(list(project.home.glob("tasks/T-*/state.json")))
        return info

    def rename_project(self, project: Project, payload: dict) -> dict:
        if set(payload) - {"name"}:
            raise ValueError("Only the display name of a project can be changed here")
        renamed = self.ws.rename(project.id, payload.get("name"))
        self.changed.set()
        return self.project_info(renamed)

    def remove_project(self, project: Project) -> dict:
        """Forget a project. Refused while one of its tasks has a lock or an attached process.

        A recorded process that cannot be verified counts as attached: the
        project would disappear from the interface while it may still retry.
        """
        for file in project.home.glob("tasks/T-*/state.json"):
            state = self._state(file) or {}
            task_id = file.parent.name
            runtime = runinfo.runtime(file.parent, state, self._owned((project.id, task_id)))
            if runtime["active"]:
                raise Conflict(f"Task {task_id} is running or waiting; stop it or let it finish first")
            if runtime["unverified"]:
                raise Conflict(f"Task {task_id} has a recorded process that this computer cannot verify "
                               f"({_unverified(file.parent, runtime)}); the project stays until that is settled")
        try:
            self.ws.remove(project.id)
        except RuntimeError as exc:
            raise Conflict(str(exc)) from exc
        self.changed.set()
        return {"removed": project.id, "kept": {"repository": project.repository, "state": str(project.home)}}

    # --- settings -------------------------------------------------------------------

    def update_settings(self, patch: dict) -> dict:
        settings = self.ws.update_settings(patch)
        self.changed.set()
        return settings

    # --- agent settings -------------------------------------------------------------

    def _agent_scope(self, project_id, task_id):
        """What a request about agent settings is about: (level, project, task path, configuration, state)."""
        if task_id and not project_id:
            raise ValueError("A task is named together with its project")
        project = self.ws.get(project_id) if project_id else None
        path = self.task_path(project, task_id) if task_id else None
        cfg = config(project.home) if project else None
        state = read_json(path / "state.json") if path else None
        return ("task" if path else "project" if project else "global"), project, path, cfg, state

    @staticmethod
    def _agent_layers(level: str, store: dict, cfg: dict | None, state: dict | None) -> tuple[list, list[str]]:
        """The levels up to `level`, and what is wrong with those that cannot be read (they count as empty)."""
        found, problems = [("global", store["settings"])], []
        for name, holder in (("project", cfg), ("task", state)):
            if holder is None:
                break
            try:
                found.append((name, agents.clean_layer(holder.get("agent_settings"))))
            except ValueError as exc:
                found.append((name, {}))
                problems.append(f"The agent settings saved with the {name} cannot be used: {exc}")
        return found[:agents.LEVELS.index(level) + 1], problems

    @staticmethod
    def _command_preview(provider: str, role: str, values: dict, cfg: dict | None, state: dict | None) -> dict:
        """The command a step would be started with, for reading only: placeholders stand for what a run fills in."""
        argv = agents.command(provider, role, values, live=streams.enabled() and values.get("live_activity") is not False,
                              workspace=state["worktree"] if state else "<task copy>",
                              turns=cfg["workflow"]["claude_max_turns"] if cfg else 12,
                              schema="<review schema>", last_message=f"<run>/{role}.last-message.txt")
        return agents.preview(argv)

    def _known(self) -> tuple[dict, dict]:
        with self.providers.lock:
            return dict(self.providers.data or {}), dict(self.providers.capabilities)

    def agents_view(self, project_id=None, task_id=None) -> dict:
        """Everything the Agents page shows for one level: both roles, both providers, values and where they come from."""
        level, project, path, cfg, state = self._agent_scope(project_id, task_id)
        store = agents.read_store(self.ws.root)
        layers, problems = self._agent_layers(level, store, cfg, state)
        available = agents.presets(store)
        statuses, capabilities = self._known()
        defaults = self.ws.settings()["defaults"]
        roles = {}
        for role in agents.ROLES:
            chosen = (cfg or {}).get("agents") or {}
            if state is not None:
                provider, source = state.get(role), "task"
            elif chosen.get(role):
                provider, source = chosen[role], "project"
            else:
                provider, source = defaults[role], "global"
            blocks = {}
            for name in agents.PROVIDERS:
                resolved, outer = agents.resolve(name, role, layers), agents.resolve(name, role, layers[:-1])
                blocks[name] = {"own": ((layers[-1][1].get(role) or {}).get(name)) or {}, "values": resolved["values"],
                                # What this level would have without its own block: the values a reset goes back to.
                                "inherited": {"values": outer["values"], "sources": outer["sources"]},
                                "sources": resolved["sources"], "preset": agents.preset_of(name, resolved["values"], available),
                                "risks": agents.risks(name, role, resolved["values"]),
                                "preview": self._command_preview(name, role, resolved["values"], cfg, state)}
            roles[role] = {"provider": provider, "provider_source": source, "blocks": blocks,
                           "provider_own": chosen.get(role) if level == "project" else provider if level == "global" else None}
        described = {}
        for name in agents.PROVIDERS:
            offered = capabilities.get(name) or {}
            described[name] = {"status": statuses.get(name), "catalog": agents.catalog(name, offered, store),
                               "profiles": offered.get("profiles") or [], "discovered": bool(offered.get("flags")),
                               "notes": offered.get("notes") or [],
                               "schema": {role: agents.schema(name, role, offered) for role in agents.ROLES}}
        running = None
        if path is not None:
            running = runinfo.runtime(path, state, self._owned((project.id, task_id)))["lock"] is not None
        return {"level": level, "project": project.id if project else None, "task": task_id,
                "task_title": state.get("title") if state else None, "task_status": state.get("status") if state else None,
                "locked": bool(running) or (state or {}).get("status") in {"done", "blocked"},
                "categories": [list(pair) for pair in agents.CATEGORIES], "providers": described, "presets": available,
                "roles": roles, "problems": problems, "checking": self.providers.checking,
                "checked_at": self.providers.checked_at}

    def agents_save(self, payload: dict) -> dict:
        """Replace the settings one level keeps for a role, and optionally who plays the role at that level.

        `blocks` maps a provider to its whole block at this level: a key left
        out inherits, `null` asks for the default. Everything is validated
        before anything is written; an option that widens what the developer
        can touch needs `risk_acknowledged: true` in the same block.
        """
        unknown = sorted(set(payload) - {"project", "task", "role", "provider", "blocks"})
        if unknown:
            raise ValueError(f"Unknown field: {', '.join(unknown)}")
        role, blocks = payload.get("role"), payload.get("blocks", {})
        if role not in agents.ROLES:
            raise ValueError("role must be developer or reviewer")
        if not isinstance(blocks, dict) or set(blocks) - set(agents.PROVIDERS):
            raise ValueError("blocks may only contain claude and codex")
        level, project, path, cfg, _ = self._agent_scope(payload.get("project"), payload.get("task"))
        clean = {name: agents.clean_block(name, role, block) for name, block in blocks.items()}
        for name, block in clean.items():
            agents.check_folders(name, role, block, private=(self.ws.root, *([project.home] if project else [])),
                                 repository=Path(cfg["repository"]) if cfg else None)
        provider = payload.get("provider")
        if "provider" in payload and (level == "task" or not (provider in VALID_AGENTS or (provider is None and level == "project"))):
            raise ValueError("The agents of a task are chosen when it is created" if level == "task"
                             else "provider must be claude or codex" + (", or null to use the global default" if level == "project" else ""))

        def merged(layer) -> dict:
            layer = copy.deepcopy(layer) if isinstance(layer, dict) else {}
            for name, block in clean.items():
                layer.setdefault(role, {})[name] = block
            return agents.clean_layer(layer)

        if level == "global":
            agents.update_store(self.ws.root, lambda store: store.__setitem__("settings", merged(store["settings"])))
            if "provider" in payload:
                self.ws.update_settings({"defaults": {role: provider}})
        elif level == "project":
            file = project.home / "config.json"
            with FileMutex(file):
                candidate = read_json(file)
                if not isinstance(candidate, dict):
                    raise ValueError("The saved configuration is not a JSON object")
                layer = merged(candidate.get("agent_settings"))
                candidate.pop("agent_settings", None)
                if layer:
                    candidate["agent_settings"] = layer
                if "provider" in payload:
                    chosen = {key: value for key, value in (candidate.get("agents") or {}).items() if key != role}
                    if provider:
                        chosen[role] = provider
                    candidate.pop("agents", None)
                    if chosen:
                        candidate["agents"] = chosen
                validate_config(copy.deepcopy(candidate), project.home)  # nothing invalid reaches the disk
                save_json(file, candidate)
        else:
            with self.run_slot((project.id, payload["task"])):  # no run of this task starts while its settings change
                held = TaskLock(path)  # and none is running: a step never sees its task's settings change under it
                try:
                    with held:
                        state = read_json(path / "state.json")
                        if state.get("status") in {"done", "blocked"}:
                            raise Conflict(f"Task is {state['status']}: its settings are no longer used")
                        layer = merged(state.get("agent_settings"))
                        state.pop("agent_settings", None)
                        if layer:
                            state["agent_settings"] = layer
                        state["updated_at"] = now()
                        save_json(path / "state.json", state)
                except LockBusy as exc:
                    raise Conflict("The task is running or its lock is present: change its settings when it has stopped") from exc
        self.changed.set()
        return self.agents_view(payload.get("project"), payload.get("task"))

    def agents_test(self, payload: dict) -> dict:
        """Test configuration: check one role's settings without starting anything.

        `values`, when given, is the block being edited at this level and not
        saved yet. The checks use the last answers of the CLIs; a model the
        account may not have is a warning here, and `agents_verify` settles it.
        """
        unknown = sorted(set(payload) - {"project", "task", "role", "provider", "values"})
        if unknown:
            raise ValueError(f"Unknown field: {', '.join(unknown)}")
        role, provider = payload.get("role"), payload.get("provider")
        if role not in agents.ROLES or provider not in agents.PROVIDERS:
            raise ValueError("role must be developer or reviewer, and provider claude or codex")
        level, project, _, cfg, state = self._agent_scope(payload.get("project"), payload.get("task"))
        store = agents.read_store(self.ws.root)
        layers, problems = self._agent_layers(level, store, cfg, state)
        if "values" in payload:
            block = agents.clean_block(provider, role, payload["values"])
            agents.check_folders(provider, role, block, private=(self.ws.root, *([project.home] if project else [])),
                                 repository=Path(cfg["repository"]) if cfg else None)
            layer = copy.deepcopy(layers[-1][1])
            layer.setdefault(role, {})[provider] = block
            layers[-1] = (level, layer)
        values = agents.resolve(provider, role, layers)["values"]
        statuses, capabilities = self._known()
        folders = {}
        if cfg:
            folders["project repository"] = cfg["repository"]
        if state:
            folders["task's copy of the project"] = state.get("worktree")
        checks = agents.diagnose(provider, role, values, status=statuses.get(provider), capabilities=capabilities.get(provider),
                                 models=agents.catalog(provider, capabilities.get(provider), store), folders=folders)
        checks += [{"id": "saved", "label": "Saved settings can be read", "status": "failed", "detail": text, "fix": ""}
                   for text in problems]
        return {"ok": not any(check["status"] == "failed" for check in checks), "checks": checks, "role": role,
                "provider": provider, "model": values.get("model"), "effort": values.get("effort"),
                "preview": self._command_preview(provider, role, values, cfg, state), "tested_at": now(),
                "cli_checked_at": self.providers.checked_at}

    def agents_verify(self, payload: dict) -> dict:
        """Ask a model for one word with the real CLI, to learn whether this login can use it.

        The only action of the agent settings that calls a model. It uses a
        little of the plan, so it needs `confirm: true`, and its answer is
        remembered with its date so that it is not asked again without need.
        """
        if set(payload) - {"provider", "model", "effort", "confirm"}:
            raise ValueError("Unknown field")
        provider = payload.get("provider")
        if provider not in agents.PROVIDERS:
            raise ValueError("provider must be claude or codex")
        if payload.get("confirm") is not True:
            raise ValueError("A test call is a real call that uses a little of your plan: confirm must be true")
        block = agents.clean_block(provider, "reviewer", {"model": payload.get("model"), "effort": payload.get("effort")})
        if not block.get("model"):
            raise ValueError("Choose the model to test")
        with self.runs:
            if self.closing:
                raise Conflict("PatchRondo is shutting down")
        result = providers.OfficialCLI(timeout=VERIFY_TIMEOUT_SECONDS).verify(
            provider, block["model"], block.get("effort"), private_dir(self.ws.root / "verify"))
        version = (self._known()[0].get(provider) or {}).get("version")
        mark = {"ok": result["ok"], "kind": result["kind"], "message": None if result["ok"] else result["message"], "at": now(),
                "effort": block.get("effort"), "cli": version}
        agents.update_store(self.ws.root, lambda store: store["models"]["verified"][provider].__setitem__(block["model"], mark))
        self.changed.set()
        return {**mark, "provider": provider, "model": block["model"]}

    def agents_preset(self, payload: dict) -> dict:
        """Save a custom preset; with the `id` of an existing one, replace it."""
        preset = agents.clean_preset(payload)

        def change(store: dict) -> None:
            kept = [item for item in store["presets"] if item["id"] != preset["id"]]
            if any(item["name"].casefold() == preset["name"].casefold() for item in kept):
                raise ValueError(f"A preset named “{preset['name']}” already exists")
            if len(kept) >= agents.MAX_ITEMS:
                raise ValueError(f"At most {agents.MAX_ITEMS} presets can be saved")
            position = next((index for index, item in enumerate(store["presets"]) if item["id"] == preset["id"]), len(kept))
            store["presets"] = kept[:position] + [preset] + kept[position:]

        store = agents.update_store(self.ws.root, change)
        self.changed.set()
        return {"preset": preset, "presets": agents.presets(store)}

    def agents_preset_remove(self, preset_id: str) -> dict:
        def change(store: dict) -> None:
            if not any(item["id"] == preset_id for item in store["presets"]):
                raise FileNotFoundError("Preset not found")
            store["presets"] = [item for item in store["presets"] if item["id"] != preset_id]

        return {"presets": agents.presets(agents.update_store(self.ws.root, change))}

    def agents_model(self, payload: dict) -> dict:
        """Add a model ID to the list of a provider, or remove one that was added. Nothing is checked about it."""
        provider, model = payload.get("provider"), payload.get("id")
        if set(payload) - {"provider", "id", "remove"} or provider not in agents.PROVIDERS or type(payload.get("remove", False)) is not bool:
            raise ValueError("provider must be claude or codex, id a model name, remove true or false")
        model = agents.clean_block(provider, "reviewer", {"model": model}).get("model")
        if not model:
            raise ValueError("Enter a model name")

        def change(store: dict) -> None:
            names = [name for name in store["models"]["custom"][provider] if name != model]
            store["models"]["custom"][provider] = names if payload.get("remove") else names + [model]

        store = agents.update_store(self.ws.root, change)
        return {"catalog": agents.catalog(provider, self._known()[1].get(provider), store)}

    def agents_refresh_step(self, project: Project, task_id) -> dict:
        """Let the step a stopped task is at start again with the settings of now, not those it was frozen with."""
        path = self.task_path(project, task_id)
        with self.run_slot((project.id, task_id)):
            held = TaskLock(path)
            try:
                with held:
                    state = read_json(path / "state.json")
                    if state.get("status") in {"done", "blocked"}:
                        raise Conflict(f"Task is {state['status']}: no step will start again")
                    roles = refresh_agent_settings(state)
                    if roles:
                        state["updated_at"] = now()
                        save_json(path / "state.json", state)
            except LockBusy as exc:
                raise Conflict("The task is running or its lock is present: its step keeps the settings it started with") from exc
            finally:
                self.changed.set()
        return {"refreshed": roles}

    @staticmethod
    def _used(state: dict) -> dict:
        """Provider, model and effort of each role's most recent step, from the records the engine froze."""
        runs = state.get("agent_runs") if isinstance(state.get("agent_runs"), dict) else {}
        used = {}
        for role in agents.ROLES:
            record = next((runs[key] for key in sorted(runs, reverse=True) if key.endswith(f":{role}") and isinstance(runs[key], dict)), None)
            values = record.get("values") if record and isinstance(record.get("values"), dict) else {}
            used[role] = {"provider": state.get(role), "model": values.get("model"), "effort": values.get("effort"),
                          "frozen": record is not None}
        return used

    def _task_agents(self, state: dict, cfg: dict | None) -> dict:
        """For the task page: what the step in progress is frozen with, and what the settings of now would give."""
        runs = state.get("agent_runs") if isinstance(state.get("agent_runs"), dict) else {}
        # At `validate` no agent is called: nothing is pending for either role.
        pending = None if state.get("status") in {"done", "blocked"} or state.get("phase") == "validate" \
            else "developer" if state.get("phase") == "develop" else "reviewer"
        try:
            layers, error = agents.layers(self.ws.root, cfg, state), None
        except (ValueError, OSError) as exc:
            layers, error = None, str(exc)
        result = {"error": error, "pending": pending, "roles": {}}
        for role in agents.ROLES:
            provider = state.get(role)
            frozen = runs.get(agents.step_key(state, role)) if isinstance(state.get("iteration"), int) else None
            frozen = frozen if isinstance(frozen, dict) and frozen.get("provider") == provider else None
            current = agents.freeze(provider, role, layers) if layers is not None and provider in agents.PROVIDERS else None
            result["roles"][role] = {"provider": provider, "frozen": frozen, "current": current,
                                     "differs": bool(frozen and current and role == pending and frozen.get("digest") != current["digest"])}
        return result

    # --- retrieval index ------------------------------------------------------------

    def index_status(self, project: Project, light: bool = False) -> dict:
        with self._cache_lock:
            job = self._index_jobs.get(project.id)
        status = {"running": bool(job and job.get("running")), "last": None}
        try:
            last = read_json(project.home / "index" / "last-index.json")
            status["last"] = last if isinstance(last, dict) else None
        except (OSError, ValueError):
            pass
        if light:
            return status
        database = rag.index_path(project.home)
        status["database"] = {"path": str(database), "exists": database.is_file(), "files": None}
        cfg, _ = self._config(project)
        if database.is_file() and cfg:
            try:
                connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=2)
                try:
                    root = str(Path(cfg["repository"]).resolve())
                    status["database"]["files"] = connection.execute(
                        "SELECT COUNT(*) FROM files WHERE root=?", (root,)).fetchone()[0]
                finally:
                    connection.close()
            except sqlite3.Error:
                pass  # an index being rebuilt or from another schema: the count is simply not shown
        return status

    def start_index(self, project: Project) -> dict:
        """Update the retrieval index of the project's repository in the background. No model calls."""
        cfg = config(project.home)
        with self._cache_lock:
            if self._index_jobs.get(project.id, {}).get("running"):
                raise Conflict("The index is already being updated")
            self._index_jobs[project.id] = {"running": True}

        def work() -> None:
            record = {"started_at": now(), "root": cfg["repository"], "status": "ok", "stats": None, "error": None}
            try:
                with rag.Index(rag.index_path(project.home)) as index:
                    record["stats"] = index.update(Path(cfg["repository"]))
            except (sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
                record.update(status="error", error=str(exc)[:500])
            record["finished_at"] = now()
            try:
                save_json(project.home / "index" / "last-index.json", record)
            except OSError:
                pass
            with self._cache_lock:
                self._index_jobs[project.id] = {"running": False}
            self.changed.set()

        threading.Thread(target=work, name="patchrondo-index", daemon=True).start()
        self.changed.set()
        return {"started": True}

    # --- tasks ----------------------------------------------------------------------

    def task_summary(self, project: Project, path: Path, state: dict, cfg: dict | None) -> dict:
        item = {key: state.get(key) for key in SUMMARY_FIELDS}
        item["id"] = path.name
        item["project"] = project.id
        review, error, plan = state.get("review"), state.get("last_error"), state.get("recovery")
        item["verdict"] = review.get("verdict") if isinstance(review, dict) else None
        item["issues"] = len(review.get("issues") or []) if isinstance(review, dict) else 0
        item["error_kind"] = error.get("kind") if isinstance(error, dict) else error if isinstance(error, str) else None
        item["error_category"] = repair.category(item["error_kind"])
        checked = state.get("environment")
        item["environment"] = {key: checked.get(key) for key in ENVIRONMENT_FIELDS} if isinstance(checked, dict) else None
        item["recovery"] = {key: plan.get(key) for key in (
            "status", "resume_at", "provider", "schedule_source", "stop_reason", "consecutive_failures",
            "max_consecutive_retries")} if isinstance(plan, dict) else None
        tests = state.get("tests")
        item["tests"] = [test.get("status") for test in tests if isinstance(test, dict)] if isinstance(tests, list) else []
        history = state.get("history")
        item["events"] = len(history) if isinstance(history, list) else 0
        item["recent"] = [event for event in history[-3:] if isinstance(event, dict)] if isinstance(history, list) else []
        item["max_iterations"] = None
        if cfg:
            try:
                item["max_iterations"] = effective_config(cfg, state)["workflow"]["max_iterations"]
            except ValueError:
                pass
        signature = _signature(path / RUN_LOG)
        item["log_size"] = signature[1] if signature else 0
        item["acceptance"] = self._acceptance(path, state)
        item["agents"] = self._used(state)
        item["runtime"] = runinfo.runtime(path, state, self._owned((project.id, path.name)))
        return item

    def _acceptance(self, path: Path, state: dict) -> dict | None:
        """Results and counts of a task's criteria, judged against its evidence ledger as it is now; None without a record.

        A record this version cannot read gives None as well: the task page says
        why. Nothing is written. The result is kept while state.json and the
        ledger keep their signatures.
        """
        if not isinstance(state.get("acceptance"), dict):
            return None
        key = (_signature(path / "state.json"), _signature(path / evidence.LEDGER))
        with self._cache_lock:
            cached = self._judged.get(path)
        if cached and cached[0] == key and cached[2]:
            return cached[1]
        try:
            record = criteria.Acceptance.load(state)
        except ValueError:
            record = None
        result = None
        if record is not None:
            developing = state.get("status") == "running" and state.get("phase") == "develop"
            current, _ = criteria.judged_now(record, evidence.read(path), developing=developing)
            result = current.compact()
        with self._cache_lock:
            self._judged[path] = (key, result, all(signature is None or _settled(signature) for signature in key))
        return result

    def collect(self) -> dict:
        """Authoritative view of everything the interface lists: projects, task summaries, settings."""
        projects, tasks, error = {}, {}, None
        try:
            registered = self.ws.projects()
            settings = self.ws.settings()
        except (OSError, ValueError, RuntimeError) as exc:
            registered, settings, error = [], None, str(exc)
        seen: set[Path] = set()
        for project in registered:
            projects[project.id] = self.project_info(project)
            cfg, _ = self._config(project)
            folder = project.home / "tasks"
            for file in folder.glob("T-*/state.json") if folder.is_dir() else []:
                if not TASK_ID.fullmatch(file.parent.name):
                    continue
                seen.add(file)
                state = self._state(file)
                if state is not None:
                    tasks[f"{project.id}/{file.parent.name}"] = self.task_summary(project, file.parent, state, cfg)
        with self._cache_lock:
            for file in set(self._states) - seen:
                del self._states[file]
            for folder in set(self._judged) - {file.parent for file in seen}:
                del self._judged[folder]
        return {"projects": projects, "tasks": tasks, "settings": settings, "error": error,
                "providers": self.providers.view()}

    def new_task(self, project: Project, payload: dict) -> dict:
        fields = {key: payload.get(key) for key in ("title", "description", "developer", "reviewer")}
        if not all(isinstance(value, str) for value in fields.values()):
            raise ValueError("title, description, developer and reviewer must be strings")
        if len(fields["title"]) > 200:
            raise ValueError("The title must have at most 200 characters")
        # Each criterion is text, or text with what its author says about it: required, categories, depends_on.
        acceptance = criteria.parse_items(payload.get("acceptance", []))
        overrides = validate_overrides(payload.get("overrides"))
        # The task's own level of the agent settings, checked like any other level before anything is created.
        chosen = agents.clean_layer(payload.get("agent_settings"))
        for role, blocks in chosen.items():
            for name, block in blocks.items():
                agents.check_folders(name, role, block, private=(self.ws.root, project.home),
                                     repository=project.repository and Path(project.repository))

        def create() -> dict:
            task_id, _ = create_task(project.home, acceptance=acceptance, overrides=overrides or None,
                                     agent_settings=chosen or None, **fields)
            with self._cache_lock:
                self._repos.pop(project.id, None)
            self.changed.set()
            return {"id": task_id, "project": project.id}

        return self.once(payload.get("request_id"), create)

    def quick_start(self, project: Project, payload: dict) -> dict:
        """Create a task from a description and start it at once: the one-step path of the home page.

        Roles default to the project's, then to the global ones; a missing
        title is the first line of the description. Starting is exactly
        `start_run`: the environment is checked by the run before any agent is
        called, and the test consent of the project is neither read as given
        nor changed. A task that was created but could not be started is
        returned with the reason, so nothing is created twice.
        """
        unknown = sorted(set(payload) - {"description", "title", "developer", "reviewer", "acceptance", "request_id",
                                         "agent_settings"})
        if unknown:
            raise ValueError(f"Unknown field: {', '.join(unknown)}")
        description, title = payload.get("description"), payload.get("title")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Describe what should be done")
        if title is not None and not isinstance(title, str):
            raise ValueError("title must be a string")
        if not title or not title.strip():
            first = next((line.strip().lstrip("#-* ").strip() for line in description.splitlines() if line.strip()), "")
            title = first if len(first) <= QUICK_TITLE else first[:QUICK_TITLE - 1].rstrip() + "…"
        cfg, _ = self._config(project)
        defaults = {**self.ws.settings()["defaults"], **((cfg or {}).get("agents") or {})}
        roles = {role: payload.get(role) or defaults[role] for role in ("developer", "reviewer")}

        def create_and_start() -> dict:
            created = self.new_task(project, {"title": title or "Task", "description": description, **roles,
                                              "acceptance": payload.get("acceptance", []),
                                              "agent_settings": payload.get("agent_settings")})
            try:
                return {**created, "started": True, "run": self.start_run(project, created["id"], {})}
            except (Conflict, RuntimeError, OSError, ValueError) as exc:
                return {**created, "started": False, "error": str(exc)}

        return self.once(payload.get("request_id"), create_and_start)

    def _logs(self, path: Path) -> list[dict]:
        """Readable log and transcript files of a task. The viewer can only ask for an ID listed here."""
        files = []
        signature = _signature(path / RUN_LOG)
        if signature:
            files.append({"id": "run", "label": "Run output", "group": "Run", "size": signature[1],
                          "file": path / RUN_LOG})
        runs = path / "runs"
        for folder in sorted(runs.glob("iteration-*")) if runs.is_dir() else []:
            if not folder.is_dir() or folder.is_symlink():
                continue
            for file in sorted(folder.iterdir()):
                if file.suffix not in LOG_SUFFIXES or file.is_symlink() or not file.is_file():
                    continue
                number = folder.name.partition("-")[2]
                files.append({"id": f"{folder.name}/{file.name}", "label": file.name,
                              "group": f"Iteration {int(number)}" if number.isdigit() else folder.name,
                              "size": file.stat().st_size, "file": file})
        return files

    def task_detail(self, project: Project, task_id) -> dict:
        path = self.task_path(project, task_id)
        state = self._state(path / "state.json")
        if state is None:
            raise FileNotFoundError("Task not found")
        cfg, config_error = self._config(project)
        effective = None
        if cfg:
            try:
                merged = effective_config(cfg, state)
                effective = {"max_iterations": merged["workflow"]["max_iterations"],
                             "agent_timeout_seconds": merged["workflow"]["agent_timeout_seconds"],
                             "test_timeout_seconds": merged["workflow"]["test_timeout_seconds"],
                             "recovery_enabled": merged["recovery"]["enabled"],
                             "tests_enabled": merged["tests"]["enabled"],
                             "test_commands": [join_command(cmd) for cmd in merged["tests"].get("commands") or []]}
            except ValueError as exc:
                config_error = str(exc)
        worktree = state.get("worktree")
        return {"project": project.id, "state": state, "agents": self._task_agents(state, cfg),
                "acceptance": self._criteria(path, state),
                "summary": self.task_summary(project, path, state, cfg),
                "effective": effective, "config_error": config_error,
                "worktree_exists": isinstance(worktree, str) and Path(worktree).is_dir(),
                "documents": {key: _text(path / name) for key, name in DOCUMENTS.items()},
                "logs": [{key: value for key, value in entry.items() if key != "file"} for entry in self._logs(path)]}

    @staticmethod
    def _criteria(path: Path, state: dict) -> dict:
        """The criteria of a task with their evidence, judged in memory against its ledger as it is now; nothing is written."""
        problems = []
        try:
            record = criteria.Acceptance.load(state)
        except ValueError as exc:
            record, problems = None, [str(exc)]
        written = criteria.from_task_text(_text(path / DOCUMENTS["task"]) or "") if record is None else None
        shown = criteria.view(record, evidence.read(path), pending=written,
                              developing=state.get("status") == "running" and state.get("phase") == "develop")
        shown["problems"] += problems
        return shown

    def criteria_preview(self, project: Project, payload: dict) -> dict:
        """The preflight of a task that does not exist yet: how its criteria would be classified and planned."""
        unknown = sorted(set(payload) - {"acceptance", "developer", "reviewer", "agent_settings"})
        if unknown:
            raise ValueError(f"Unknown field: {', '.join(unknown)}")
        return preflight(project.home, acceptance=payload.get("acceptance", []), developer=payload.get("developer"),
                         reviewer=payload.get("reviewer"), agent_settings=payload.get("agent_settings"), agents_root=self.ws.root)

    def _on_criteria(self, project: Project, task_id, payload: dict, fields: set, action) -> dict:
        """One action of the user on a task's criteria: under the run slot and the task lock, never beside a run.

        With `resume`, a task that would now finish without calling an agent
        is started, so that it completes. Anything else is left to Resume,
        which says what it will call.
        """
        unknown = sorted(set(payload) - fields - {"resume"})
        if unknown:
            raise ValueError(f"Unknown field: {', '.join(unknown)}")
        resume = payload.get("resume", False)
        if type(resume) is not bool:
            raise ValueError("resume must be a JSON boolean (true/false)")
        self.task_path(project, task_id)
        with self.run_slot((project.id, task_id)):  # no run of this task starts while its criteria are being judged
            try:
                result = action()
            except LockBusy as exc:
                raise Conflict("The task is running or its lock is present: its criteria are judged by that run") from exc
            except AcceptanceRefused as exc:
                raise Conflict(str(exc)) from exc
            finally:
                self.changed.set()
        if resume and result["completes"]:
            try:
                result["run"] = self.start_run(project, task_id, {})
            except (Conflict, RuntimeError, OSError) as exc:
                result["run_error"] = str(exc)  # what was recorded is saved either way
        return result

    def criteria_check(self, project: Project, task_id, payload: dict) -> dict:
        """Detect capabilities again and judge the criteria of a task from its evidence. No model, no command."""
        return self._on_criteria(project, task_id, payload, set(),
                                 lambda: recheck_acceptance(project.home, task_id, agents_root=self.ws.root))

    def criteria_evidence(self, project: Project, task_id, criterion: str, payload: dict) -> dict:
        """Record a check the user did by hand for one criterion, with where its proof can be found."""
        return self._on_criteria(project, task_id, payload, {"summary", "reference", "artifact", "passed"}, lambda: attach_evidence(
            project.home, task_id, criterion, summary=payload.get("summary"), reference=payload.get("reference"),
            artifact=payload.get("artifact"), passed=payload.get("passed", True), agents_root=self.ws.root))

    def criteria_authorize(self, project: Project, task_id, criterion: str, payload: dict) -> dict:
        """Record the user's decision about the operation a criterion needs. Granting needs `confirm: true`; nothing is run."""
        decision = payload.get("decision")
        if decision not in ("granted", "denied"):
            raise ValueError("decision must be granted or denied")
        if decision == "granted" and payload.get("confirm") is not True:
            raise ValueError("Authorizing an operation is your explicit decision: confirm must be true")
        return self._on_criteria(project, task_id, payload, {"decision", "confirm"}, lambda: decide_authorization(
            project.home, task_id, criterion, granted=decision == "granted", agents_root=self.ws.root))

    def log_chunk(self, project: Project, task_id, file_id, offset, limit) -> dict:
        """A bounded slice of one listed log file, by byte offset; a negative offset reads from the end."""
        path = self.task_path(project, task_id)
        entry = next((item for item in self._logs(path) if item["id"] == file_id), None)
        if entry is None:
            raise FileNotFoundError("Log file not found")
        limit = min(max(int(limit), 1), CHUNK_LIMIT)
        size = entry["file"].stat().st_size
        offset = int(offset)
        start = max(0, size + offset) if offset < 0 else min(offset, size)
        with entry["file"].open("rb") as stream:
            stream.seek(start)
            data = stream.read(limit + 4)
        end = min(len(data), limit)
        if start + end < size:
            while 0 < end < len(data) and (data[end] & 0xC0) == 0x80:  # do not cut a UTF-8 sequence in half
                end -= 1
        skip = 0
        if start > 0:
            while skip < end and (data[skip] & 0xC0) == 0x80:
                skip += 1
        return {"id": file_id, "offset": start + skip, "next": start + end, "size": size,
                "eof": start + end >= size, "text": data[skip:end].decode("utf-8", errors="replace")}

    def activity_page(self, project: Project, task_id, cursor=None, before=None, limit=activity.PAGE_EVENTS) -> dict:
        """One page of the task's activity journal: its end, or what follows or precedes a cursor."""
        return activity.read(self.task_path(project, task_id), cursor, before=before, limit=int(limit))

    def task_files(self, project: Project, task_id) -> dict:
        """Changed files of a task worktree with a bounded diff. Read-only Git, no external diff tools."""
        path = self.task_path(project, task_id)
        state = self._state(path / "state.json") or {}
        result = {"available": False, "reason": None, "worktree": state.get("worktree"),
                  "base_sha": state.get("base_sha"), "branch": f"patchrondo/{task_id}",
                  "files": [], "stat": "", "diff": "", "truncated": False, "untracked": []}
        worktree = Path(state["worktree"]) if isinstance(state.get("worktree"), str) else None
        if worktree is None or not worktree.is_dir():
            result["reason"] = "The task worktree was not found"
            return result
        try:
            status, _ = _git(worktree, "status", "--short", "--untracked-files=all")
            result["stat"], _ = _git(worktree, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--stat", "HEAD")
            result["diff"], result["truncated"] = _git(
                worktree, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "HEAD", limit=DIFF_LIMIT)
        except (OSError, RuntimeError) as exc:
            result["reason"] = str(exc)[:300]
            return result
        root = worktree.resolve()
        for line in status.splitlines():
            code, name = line[:2], line[3:]
            result["files"].append({"code": code.strip() or "?", "path": name})
            if code != "??" or len(result["untracked"]) >= UNTRACKED_FILES:
                continue
            # New files are not part of `git diff HEAD`: show their text, never following links out.
            target = root / name.strip('"')
            entry = {"path": name, "size": None, "text": None, "note": None}
            try:
                if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(root):
                    entry["note"] = "Not a regular file inside the worktree"
                else:
                    entry["size"] = target.stat().st_size
                    with target.open("rb") as stream:
                        data = stream.read(UNTRACKED_BYTES + 1)
                    if b"\0" in data[:8192]:
                        entry["note"] = "Binary file"
                    else:
                        entry["text"] = data[:UNTRACKED_BYTES].decode("utf-8", errors="replace")
                        if len(data) > UNTRACKED_BYTES:
                            entry["note"] = "Truncated"
            except OSError as exc:
                entry["note"] = str(exc)[:200]
            result["untracked"].append(entry)
        result["available"] = True
        return result

    # --- runs -----------------------------------------------------------------------

    @staticmethod
    def _run_command(project: Project, task_id: str, flags: list[str]) -> list[str]:
        return [sys.executable, "-m", "patchrondo", "--home", str(project.home), "run", task_id, *flags]

    def track(self, key: tuple[str, str], process: subprocess.Popen) -> None:
        with self.runs:
            # Reap finished runs so they do not linger as zombies on POSIX.
            for other, processes in list(self.children.items()):
                alive = [child for child in processes if child.poll() is None]
                if alive:
                    self.children[other] = alive
                else:
                    del self.children[other]
            self.children.setdefault(key, []).append(process)

    @contextmanager
    def run_slot(self, key: tuple[str, str]):
        """Mark a run start in progress; refused once closing or while the same task is being started."""
        with self.runs:
            if self.closing:
                raise Conflict("PatchRondo is shutting down")
            if key in self.starting_tasks:
                raise Conflict("This task is already being started")
            self.starting_tasks.add(key)
            self.starting += 1
        try:
            yield
        finally:
            with self.runs:
                self.starting_tasks.discard(key)
                self.starting -= 1
                self.runs.notify_all()

    def close_runs(self, timeout: float = 30) -> RunsAtExit:
        """Refuse new run starts, wait for those in progress and report what may still run."""
        with self.runs:
            self.closing = True
            self.runs.wait_for(lambda: self.starting == 0, timeout)
            # After a timeout, `pending` starts may still launch a run: callers must keep its files.
            active = [child for processes in self.children.values() for child in processes if child.poll() is None]
            return RunsAtExit(active, self.starting)

    def start_run(self, project: Project, task_id, payload: dict) -> dict:
        """Start `patchrondo run` in its own process group; its lock prevents concurrent runs.

        `auto_resume` true/false is passed as --auto-resume/--no-auto-resume; null
        leaves the configured default. `unlock` passes --unlock, which the engine
        honors only after its own check of every recorded process. A run that
        exits with an error during the first moments is reported, not shown as
        started.
        """
        auto_resume, unlock = payload.get("auto_resume"), payload.get("unlock", False)
        if auto_resume is not None and type(auto_resume) is not bool:
            raise ValueError("auto_resume must be true, false or null")
        if type(unlock) is not bool:
            raise ValueError("unlock must be a JSON boolean (true/false)")
        if set(payload) - {"auto_resume", "unlock"}:
            raise ValueError("Unknown run option")
        path = self.task_path(project, task_id)
        key = (project.id, task_id)
        with self.run_slot(key):
            state = read_json(path / "state.json")
            status = state.get("status")
            if status in {"done", "blocked"}:
                raise Conflict(f"Task is {status} and cannot be run again")
            runtime = runinfo.runtime(path, state, self._owned(key))
            if runtime["lock"] is not None:
                if not unlock:
                    raise Conflict("The task lock is present: a run is active, or it ended abruptly"
                                   if runtime["activity"] != "running" else "Task is already running")
                if not runtime["can_unlock"]:
                    raise Conflict("The lock cannot be released here: its process may still be running, or this "
                                   "platform cannot verify it")
            elif unlock:
                raise Conflict("There is no lock to release")
            if runtime["activity"] == "starting":
                raise Conflict("A run for this task is already starting")
            if runtime["waiting_process"] and auto_resume is not False:
                raise Conflict("A process is already waiting to retry this task. Stop it, or run once now "
                               "without automatic recovery")
            if runtime["waiting_unverified"] and auto_resume is not False:
                raise Conflict("A process was recorded as waiting to retry this task and this computer cannot verify "
                               f"it ({_unverified(path, runtime)}). Until then, run once now without automatic recovery")
            cfg = config(project.home)
            if not Path(str(state.get("worktree"))).is_dir():
                raise Conflict("The task worktree was not found")
            flags = (["--unlock"] if unlock else []) + \
                    ([] if auto_resume is None else ["--auto-resume" if auto_resume else "--no-auto-resume"])
            auto = recovery.active(effective_config(cfg, state), auto_resume)
            fd = os.open(path / RUN_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "ab") as log:
                log.write(f"\n=== {now()} · run started from the dashboard ===\n".encode())
                log.flush()
                extra = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                         else {"start_new_session": True})
                # The child runs from the state directory: make inherited entries absolute.
                inherited = [os.path.abspath(entry) for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry]
                # The run is given the project's own state directory: tell it where the global agent settings are.
                env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PATCHRONDO_UI_CHILD": "1",
                       agents.WORKSPACE: str(self.ws.root), "PYTHONPATH": os.pathsep.join([PACKAGE_ROOT, *inherited])}
                process = subprocess.Popen(self.run_command(project, task_id, flags), cwd=project.home,
                                           stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                           env=env, **extra)
            self.track(key, process)
            self.changed.set()
            try:
                code = process.wait(timeout=STARTUP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                return {"started": True, "pid": process.pid, "auto_resume": auto}
            finally:
                self.changed.set()
            if code not in (0, 2):  # 0 = done, 2 = paused/blocked; anything else is a failure to run
                lines = (_text(path / RUN_LOG, tail=True) or "").strip().splitlines()
                raise RuntimeError(f"Run exited immediately (code {code}): {lines[-1] if lines else 'see the Run log'}")
            return {"started": True, "pid": process.pid, "auto_resume": auto, "finished": True, "code": code}

    def repair_task(self, project: Project, task_id, payload: dict) -> dict:
        """Check the environment of a task again and apply the repairs that are allowed. No model is called.

        Unattended repairs are tried again; a repair that is the user's
        decision is applied only when `confirm` names it. The check holds the
        task lock, so it never overlaps a run, and its record and events are
        saved with the task like those of a run. With `resume`, a task that
        is ready afterwards is started.
        """
        confirm, resume = payload.get("confirm", []), payload.get("resume", False)
        if set(payload) - {"confirm", "resume"}:
            raise ValueError("Unknown repair option")
        if not isinstance(confirm, list) or set(confirm) - CONFIRMABLE_REPAIRS:
            raise ValueError(f"confirm may only list: {', '.join(sorted(CONFIRMABLE_REPAIRS))}")
        if type(resume) is not bool:
            raise ValueError("resume must be a JSON boolean (true/false)")
        path = self.task_path(project, task_id)
        key = (project.id, task_id)
        with self.run_slot(key):  # no run of this task is started while it is being checked
            try:
                with TaskLock(path):
                    state = read_json(path / "state.json")
                    if state.get("status") in {"done", "blocked"}:
                        raise Conflict(f"Task is {state['status']}: there is nothing to repair")
                    worktree = state.get("worktree")
                    if not isinstance(worktree, str) or not Path(worktree).is_dir():
                        raise Conflict("The task worktree was not found")
                    cfg = config(project.home)
                    previous = state.get("environment") if isinstance(state.get("environment"), dict) else None
                    record = self.environment.check(project.home, state, live=not providers.simulated(),
                                                    confirmed=tuple(confirm), previous=previous,
                                                    repository=Path(cfg["repository"]))
                    record_environment(state, record)
                    state["updated_at"] = now()
                    save_json(path / "state.json", state)
            except LockBusy as exc:
                raise Conflict("The task is running or its lock is present: it cannot be checked now") from exc
            finally:
                self.changed.set()
        result = {"ready": record["status"] == "ready",
                  "environment": {name: record.get(name) for name in ENVIRONMENT_FIELDS}}
        if resume and result["ready"]:
            try:
                result["run"] = self.start_run(project, task_id, {})
            except (Conflict, RuntimeError, OSError) as exc:
                result["run_error"] = str(exc)  # the check itself succeeded and is saved
        return result

    def stop_run(self, project: Project, task_id) -> dict:
        """Interrupt the verified processes of a task, as Ctrl+C in their terminal would.

        The runner pauses the task at its last checkpoint, or stops waiting and
        keeps the retry plan. Not available on native Windows, where an
        interrupt cannot be delivered to a detached process safely.
        """
        if os.name != "posix":
            raise Conflict("Stopping a run from here is not available on native Windows. Press Ctrl+C in the "
                           "terminal of that run, or let it reach its next pause")
        path = self.task_path(project, task_id)
        state = read_json(path / "state.json")
        runtime = runinfo.runtime(path, state, self._owned((project.id, task_id)))
        targets = [entry["pid"] for entry in runtime["processes"] if entry["state"] == "alive"]
        if not targets:
            raise Conflict("No verified process is attached to this task")
        for pid in targets:
            try:
                os.kill(pid, signal.SIGINT)
            except ProcessLookupError:
                pass
        self.changed.set()
        return {"signalled": targets}

    # --- snapshot -------------------------------------------------------------------

    def about(self) -> dict:
        return {"version": __version__, "home": str(self.ws.root), "platform": self.platform(),
                "agents": sorted(VALID_AGENTS), "task_overrides": {key: list(value) for key, value in TASK_OVERRIDES.items()},
                "limits": {"workflow": {key: list(value) for key, value in WORKFLOW_LIMITS.items()}}}


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
