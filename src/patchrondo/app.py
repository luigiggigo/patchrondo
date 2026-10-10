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

from . import __version__, doctor, rag, recovery, runinfo
from .commands import join_command, split_command
from .core import (TASK_OVERRIDES, VALID_AGENTS, WORKFLOW_LIMITS, config, create_task, effective_config,
                   validate_config, validate_overrides)
from .storage import FileMutex, now, read_json, save_json
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


class RunsAtExit(NamedTuple):
    active: list[subprocess.Popen]
    pending: int  # run starts still in progress when the wait timed out

    @property
    def any(self) -> bool:
        return bool(self.active or self.pending)


class Conflict(Exception):
    """The requested action does not fit the current state."""


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
        self.checked_at: str | None = None
        self.checked_monotonic = 0.0
        self.checking = False

    def view(self, refresh: bool = False) -> dict:
        with self.lock:
            stale = self.data is None or time.monotonic() - self.checked_monotonic > PROVIDER_TTL_SECONDS
            if (refresh or stale) and not self.checking:
                self.checking = True
                threading.Thread(target=self._check, name="patchrondo-providers", daemon=True).start()
            return {"checking": self.checking, "checked_at": self.checked_at, "providers": self.data}

    def _check(self) -> None:
        try:
            cwd = self.cwd if self.cwd.is_dir() else Path.home()
            data = doctor.check_all(cwd)
        except Exception as exc:  # noqa: BLE001 - shown in the interface instead of killing the thread
            data = {name: {"name": name, "label": spec["label"], "installed": None, "path": None, "version": None,
                           "authenticated": None, "auth_method": None, "warning": f"Check failed: {exc}",
                           "hint": spec["install"], "login_command": spec["login"], "docs": spec["docs"]}
                    for name, spec in doctor.PROVIDERS.items()}
        with self.lock:
            self.data, self.checked_at, self.checked_monotonic = data, now(), time.monotonic()
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
        self._cache_lock = threading.Lock()
        self._states: dict[Path, tuple] = {}
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
        """Forget a project. Refused while one of its tasks has a lock or an attached process."""
        for file in project.home.glob("tasks/T-*/state.json"):
            state = self._state(file) or {}
            task_id = file.parent.name
            if runinfo.runtime(file.parent, state, self._owned((project.id, task_id)))["active"]:
                raise Conflict(f"Task {task_id} is running or waiting; stop it or let it finish first")
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
        item["runtime"] = runinfo.runtime(path, state, self._owned((project.id, path.name)))
        return item

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
        return {"projects": projects, "tasks": tasks, "settings": settings, "error": error,
                "providers": self.providers.view()}

    def new_task(self, project: Project, payload: dict) -> dict:
        fields = {key: payload.get(key) for key in ("title", "description", "developer", "reviewer")}
        if not all(isinstance(value, str) for value in fields.values()):
            raise ValueError("title, description, developer and reviewer must be strings")
        if len(fields["title"]) > 200:
            raise ValueError("The title must have at most 200 characters")
        acceptance = payload.get("acceptance", [])
        if not isinstance(acceptance, list) or not all(isinstance(item, str) for item in acceptance):
            raise ValueError("acceptance must be a list of strings")
        overrides = validate_overrides(payload.get("overrides"))

        def create() -> dict:
            task_id, _ = create_task(project.home, acceptance=[item.strip() for item in acceptance if item.strip()],
                                     overrides=overrides or None, **fields)
            with self._cache_lock:
                self._repos.pop(project.id, None)
            self.changed.set()
            return {"id": task_id, "project": project.id}

        return self.once(payload.get("request_id"), create)

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
        return {"project": project.id, "state": state,
                "summary": self.task_summary(project, path, state, cfg),
                "effective": effective, "config_error": config_error,
                "worktree_exists": isinstance(worktree, str) and Path(worktree).is_dir(),
                "documents": {key: _text(path / name) for key, name in DOCUMENTS.items()},
                "logs": [{key: value for key, value in entry.items() if key != "file"} for entry in self._logs(path)]}

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
                env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PATCHRONDO_UI_CHILD": "1",
                       "PYTHONPATH": os.pathsep.join([PACKAGE_ROOT, *inherited])}
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
