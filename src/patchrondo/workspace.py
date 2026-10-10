"""Workspace: the project registry and global preferences of one PatchRondo home.

    <home>/settings.json            global preferences
    <home>/projects.json            registry: id, display name, state directory
    <home>/projects/<project-id>/   state directory of a project added in 0.2

A project's state directory has exactly the layout the whole home had in 0.1
(`config.json`, `tasks/`, `worktrees/`, `index/`, `empty-hooks/`), so the
engine works on it unchanged. A 0.1 home is therefore one project: it is
registered where it is, at the root of the home or at any other path, and
nothing in it is moved or rewritten. Git worktrees record absolute paths, so
moving them would break them.

Removing a project deletes its registry entry only. The repository and the
state directory are never deleted here.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import uuid

from .core import VALID_AGENTS, config, initialize
from .storage import FileMutex, now, read_json, save_json

REGISTRY = "projects.json"
SETTINGS = "settings.json"
PROJECT_ID = re.compile(r"P-[a-f0-9]{8}")
THEMES = ("dark", "light", "system")
DEFAULT_SETTINGS = {"version": 1, "defaults": {"developer": "claude", "reviewer": "codex"},
                    "theme": "dark", "selected_project": None, "onboarding_completed": False}
MAX_NAME = 80


class NotFound(LookupError):
    """No project with that identifier is registered."""


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    home: Path
    origin: str  # "managed" (created by 0.2), "legacy" (0.1 home at the root) or "imported" (0.1 home elsewhere)
    added_at: str | None = None

    @property
    def repository(self) -> str | None:
        """Repository path from the project's own configuration; None when it cannot be read."""
        try:
            repo = read_json(self.home / "config.json").get("repository")
        except (OSError, ValueError, AttributeError):
            return None
        return repo if isinstance(repo, str) and repo else None


def clean_name(name, fallback: str = "") -> str:
    if name is None:
        name = fallback
    if not isinstance(name, str):
        raise ValueError("The project name must be text")
    name = " ".join(name.split())
    if not name or len(name) > MAX_NAME:
        raise ValueError(f"The project name must have 1 to {MAX_NAME} characters")
    return name


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()

    # --- registry -------------------------------------------------------------------

    def _read(self) -> dict:
        file = self.root / REGISTRY
        if not file.exists():
            return {"version": 1, "legacy_root": None, "projects": []}
        data = read_json(file)
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("projects"), list):
            raise ValueError(f"Invalid project registry: {file}")
        return data

    def _project(self, entry) -> Project | None:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) \
                or not PROJECT_ID.fullmatch(entry["id"]) or not isinstance(entry.get("home"), str):
            return None  # an entry this version cannot interpret is left alone, not rewritten
        home = Path(entry["home"])
        home = home if home.is_absolute() else (self.root / home)
        origin = entry.get("origin") if entry.get("origin") in {"managed", "legacy", "imported"} else "imported"
        name = entry.get("name") if isinstance(entry.get("name"), str) and entry["name"].strip() else entry["id"]
        return Project(entry["id"], name, home.resolve(), origin, entry.get("added_at"))

    def _stored(self, home: Path) -> str:
        """Homes inside the workspace are saved relative to it, others as absolute paths."""
        home = home.resolve()
        return home.relative_to(self.root).as_posix() if home.is_relative_to(self.root) else str(home)

    def _update(self, change):
        """Read, change and atomically rewrite the registry under a cross-process mutex."""
        with FileMutex(self.root / REGISTRY):
            data = self._read()
            result = change(data)
            save_json(self.root / REGISTRY, data)
            return result

    def _new_id(self, data: dict) -> str:
        taken = {entry.get("id") for entry in data["projects"] if isinstance(entry, dict)}
        while True:
            candidate = "P-" + uuid.uuid4().hex[:8]
            if candidate not in taken:
                return candidate

    def has_legacy_root(self) -> bool:
        return (self.root / "config.json").is_file()

    def projects(self) -> list[Project]:
        """Registered projects. A 0.1 configuration at the root is registered in place on first use."""
        data = self._read()
        found = [project for entry in data["projects"] if (project := self._project(entry)) is not None]
        if self.has_legacy_root() and data.get("legacy_root") is None \
                and not any(project.home == self.root for project in found):
            def adopt(current: dict) -> None:
                if current.get("legacy_root") is None and not any(
                        (p := self._project(entry)) is not None and p.home == self.root
                        for entry in current["projects"]):
                    name = Path(Project("", "", self.root, "legacy").repository or "").name or "Default project"
                    current["projects"].append({"id": self._new_id(current), "name": name[:MAX_NAME], "home": ".",
                                                "origin": "legacy", "added_at": now()})
                    current["legacy_root"] = "registered"
            self._update(adopt)
            return self.projects()
        return found

    def get(self, project_id: str) -> Project:
        if isinstance(project_id, str) and PROJECT_ID.fullmatch(project_id):
            for project in self.projects():
                if project.id == project_id:
                    return project
        raise NotFound("Project not found")

    def _repositories(self, skip: str | None = None) -> dict[Path, Project]:
        known = {}
        for project in self.projects():
            repo = project.repository
            if repo and project.id != skip:
                known[Path(repo).resolve()] = project
        return known

    def add(self, repo: Path, name: str | None = None) -> Project:
        """Register a Git repository as a new project with its own state directory.

        Tests start disabled and untrusted, as after any initialization.
        """
        from .gitops import repo_root

        repo = repo_root(Path(repo).expanduser().resolve())
        if self.root == repo or self.root.is_relative_to(repo):
            raise ValueError("The PatchRondo state directory is inside that repository; choose another repository "
                             "or another state directory")
        duplicate = self._repositories().get(repo)
        if duplicate is not None:
            raise ValueError(f'That repository is already registered as "{duplicate.name}"')
        name = clean_name(name, repo.name or "Project")
        created: list[Path] = []

        def change(data: dict) -> Project:
            project_id = self._new_id(data)
            home = self.root / "projects" / project_id
            if home.exists():
                raise RuntimeError(f"State directory already exists: {home}")
            created.append(home)
            initialize(home, repo)
            entry = {"id": project_id, "name": name, "home": self._stored(home), "origin": "managed",
                     "added_at": now()}
            data["projects"].append(entry)
            return self._project(entry)

        try:
            return self._update(change)
        except BaseException:
            # Roll back only what this call created: a fresh directory with no tasks in it.
            for home in created:
                if home.is_dir() and not any((home / "tasks").glob("T-*")):
                    shutil.rmtree(home, ignore_errors=True)
            raise

    def import_home(self, home: Path, name: str | None = None) -> Project:
        """Register an existing 0.1 state directory where it is. Nothing in it is moved or changed."""
        home = Path(home).expanduser().resolve()
        if not (home / "config.json").is_file():
            raise ValueError(f"No PatchRondo configuration found in {home} (config.json is missing)")
        cfg = config(home)  # the same validation every command applies
        if any(project.home == home for project in self.projects()):
            raise ValueError("That state directory is already registered")
        duplicate = self._repositories().get(Path(cfg["repository"]).resolve())
        if duplicate is not None:
            raise ValueError(f'Its repository is already registered as "{duplicate.name}"')
        name = clean_name(name, Path(cfg["repository"]).name or "Imported project")

        def change(data: dict) -> Project:
            entry = {"id": self._new_id(data), "name": name, "home": self._stored(home),
                     "origin": "legacy" if home == self.root else "imported", "added_at": now()}
            data["projects"].append(entry)
            if home == self.root:
                data["legacy_root"] = "registered"
            return self._project(entry)

        return self._update(change)

    def rename(self, project_id: str, name) -> Project:
        name = clean_name(name)
        self.get(project_id)

        def change(data: dict) -> None:
            for entry in data["projects"]:
                if isinstance(entry, dict) and entry.get("id") == project_id:
                    entry["name"] = name

        self._update(change)
        return self.get(project_id)

    def remove(self, project_id: str) -> Project:
        """Forget a project. Its repository, worktrees and state directory stay on disk."""
        project = self.get(project_id)
        if any(project.home.glob("tasks/*/.run.lock")):
            raise RuntimeError("A task of this project holds a run lock; wait for it to finish before removing "
                               "the project")

        def change(data: dict) -> None:
            data["projects"] = [entry for entry in data["projects"]
                                if not (isinstance(entry, dict) and entry.get("id") == project_id)]
            if project.home == self.root:
                data["legacy_root"] = "dismissed"  # do not register the root configuration again by itself

        self._update(change)
        if self.settings().get("selected_project") == project_id:
            self.update_settings({"selected_project": None})
        return project

    # --- command-line resolution ----------------------------------------------------

    def resolve(self, selector: str | None = None) -> Project:
        """The project a command applies to.

        Without a selector, a configuration at the root of the home wins, exactly
        as in 0.1 and without touching the registry; then the selected project,
        then the only registered one.
        """
        if selector:
            projects = self.projects()
            matches = [p for p in projects if p.id == selector] or \
                      [p for p in projects if p.name.casefold() == selector.casefold()]
            if len(matches) != 1:
                raise ValueError(f"No single project matches {selector!r}; see: patchrondo projects")
            return matches[0]
        if self.has_legacy_root():
            return Project("", "", self.root, "legacy")
        projects = self.projects()
        selected = self.settings().get("selected_project")
        for project in projects:
            if project.id == selected:
                return project
        if len(projects) == 1:
            return projects[0]
        if not projects:
            raise RuntimeError(f"Run patchrondo init --repo PATH first (missing {self.root / 'config.json'}), "
                               "or run patchrondo to set up a project in the browser")
        raise ValueError("Several projects are registered: pass --project ID (see: patchrondo projects)")

    def find_task(self, task_id: str) -> Project | None:
        """The registered project that holds a task, for commands given only a task ID."""
        for project in self.projects():
            if (project.home / "tasks" / task_id / "state.json").is_file():
                return project
        return None

    # --- global preferences ---------------------------------------------------------

    def settings(self) -> dict:
        merged = json.loads(json.dumps(DEFAULT_SETTINGS))
        file = self.root / SETTINGS
        if file.exists():
            saved = read_json(file)
            if not isinstance(saved, dict) or saved.get("version") != 1:
                raise ValueError(f"Invalid settings file: {file}")
            _check_settings(saved)
            merged.update({key: value for key, value in saved.items() if key != "defaults"})
            merged["defaults"].update(saved.get("defaults") or {})
        return merged

    def update_settings(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise ValueError("Settings must be a JSON object")
        _check_settings(patch)
        if patch.get("selected_project") is not None:
            self.get(patch["selected_project"])
        with FileMutex(self.root / SETTINGS):
            current = self.settings()
            current.update({key: value for key, value in patch.items() if key != "defaults"})
            current["defaults"].update(patch.get("defaults") or {})
            save_json(self.root / SETTINGS, current)
        return current


def _check_settings(values: dict) -> None:
    unknown = sorted(set(values) - set(DEFAULT_SETTINGS))
    if unknown:
        raise ValueError(f"Unknown setting: {', '.join(unknown)}")
    defaults = values.get("defaults")
    if defaults is not None and (not isinstance(defaults, dict) or set(defaults) - {"developer", "reviewer"}
                                 or any(agent not in VALID_AGENTS for agent in defaults.values())):
        raise ValueError("defaults may only set developer and reviewer to claude or codex")
    if "theme" in values and values["theme"] not in THEMES:
        raise ValueError(f"theme must be one of: {', '.join(THEMES)}")
    if "onboarding_completed" in values and type(values["onboarding_completed"]) is not bool:
        raise ValueError("onboarding_completed must be a JSON boolean (true/false)")
    selected = values.get("selected_project")
    if selected is not None and not (isinstance(selected, str) and PROJECT_ID.fullmatch(selected)):
        raise ValueError("selected_project must be a project ID or null")
    if "version" in values and values["version"] != 1:
        raise ValueError("Unsupported settings version")
