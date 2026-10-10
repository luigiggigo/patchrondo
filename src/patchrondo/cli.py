"""CLI: open the local interface, or initialize, create, run, resume, inspect and report tasks."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys

from . import __version__, doctor, rag, recovery, runinfo
from .core import config, create_task, initialize, task_config, task_dir
from .storage import read_json
from .workspace import REGISTRY, Project, Workspace


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="patchrondo",
        description="PatchRondo — Code. Review. Repeat. Claude ↔ Codex orchestrator (local). "
                    "Run it without a command to open the local interface in your browser.")
    p.add_argument("--version", action="version", version=f"patchrondo {__version__}")
    p.add_argument("--home", type=Path, default=Path(os.environ.get("PATCHRONDO_HOME", "~/.patchrondo")).expanduser(),
                   help="Private directory for settings, projects, tasks and worktrees")
    p.add_argument("--project", help="Project ID or name, when the home holds several projects (see: projects)")
    p.add_argument("--port", type=int, default=None,
                   help="Loopback port of the interface (default 8765, or a free port if it is taken; 0 picks one)")
    p.add_argument("--no-browser", action="store_true", help="Print the interface URL without opening a browser")
    sub = p.add_subparsers(dest="command", required=False)
    init = sub.add_parser("init", help="Register a Git repository")
    init.add_argument("--repo", required=True, type=Path)
    new = sub.add_parser("new", help="Create a task and a dedicated worktree")
    new.add_argument("--title", required=True)
    body = new.add_mutually_exclusive_group(required=True)
    body.add_argument("--description")
    body.add_argument("--task-file", type=Path)
    new.add_argument("--accept", action="append", default=[], help="Acceptance criterion (repeatable)")
    new.add_argument("--developer", choices=["claude", "codex"], default="claude")
    new.add_argument("--reviewer", choices=["claude", "codex"], default="codex")
    for action in ("run", "resume", "status", "report"):
        cmd = sub.add_parser(action)
        cmd.add_argument("task_id", help="Task ID such as T-0123456789ab")
        if action in {"run", "resume"}:
            cmd.add_argument("--unlock", action="store_true", help="Recover a stale lock only after checking process IDs")
            cmd.add_argument("--auto-resume", action=argparse.BooleanOptionalAction, default=None,
                             help="After a provider usage limit, keep this process waiting and retry at the reset "
                                  "or after a backoff, within the configured caps. --no-auto-resume runs once and "
                                  "cancels a pending retry plan. Default: recovery.enabled in config.json")
    sub.add_parser("list", help="List tasks")
    sub.add_parser("projects", help="List the projects registered in this home")
    index = sub.add_parser("index", help="Update the local retrieval index (no model calls)")
    search = sub.add_parser("search", help="Query the local retrieval index (no model calls)")
    search.add_argument("query")
    search.add_argument("-k", "--limit", type=int, default=8, help="Maximum number of excerpts")
    for cmd in (index, search):
        cmd.add_argument("--task", help="Use a task worktree instead of the configured repository")
    sub.add_parser("doctor", help="Check CLI availability and authentication without model calls")
    ui = sub.add_parser("ui", help="Open the local interface (the default when no command is given)")
    # SUPPRESS keeps values given before the command: a subparser default would overwrite them.
    ui.add_argument("--port", type=int, default=argparse.SUPPRESS, help="Loopback port (0 picks a free port)")
    ui.add_argument("--no-browser", action="store_true", default=argparse.SUPPRESS,
                    help="Print the URL without opening a browser")
    return p


def _project(workspace: Workspace, selector: str | None, task_id: str | None) -> Project:
    """The project a command works on; a task ID alone is enough when one registered project holds it."""
    if selector:
        return workspace.resolve(selector)
    registry = (workspace.root / REGISTRY).exists()
    try:
        project = workspace.resolve(None)
    except ValueError:
        found = workspace.find_task(task_id) if task_id and registry else None
        if found is None:
            raise
        return found
    if task_id and registry and not (project.home / "tasks" / task_id / "state.json").is_file():
        return workspace.find_task(task_id) or project
    return project


def _print_tasks(home: Path) -> None:
    folder = home / "tasks"
    for file in sorted(folder.glob("T-*/state.json")) if folder.exists() else []:
        s = read_json(file)
        print(f"{s['id']} · {s['status']:8} · {s['developer']} → {s['reviewer']} · {s['title']}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    opts = parser().parse_args(argv)
    root = opts.home.expanduser().resolve()
    workspace = Workspace(root)
    command = opts.command or "ui"
    try:
        if command == "ui":
            from .ui import serve
            serve(root, 8765 if opts.port is None else opts.port, open_browser=not opts.no_browser)
        elif command == "init":
            if (root / REGISTRY).exists():
                project = workspace.add(opts.repo)
                print(f"Project registered: {project.name} ({project.id})\nState directory: {project.home}")
            else:
                print(f"Configuration created: {initialize(root, opts.repo)}")
            print("Tests are disabled by default. Edit config.json to configure and authorize them.")
        elif command == "projects":
            projects = workspace.projects()
            for project in projects:
                print(f"{project.id} · {project.name} · {project.repository or '(configuration unreadable)'}"
                      f" · state {project.home}")
            if not projects:
                print("No projects yet. Run patchrondo to add one in the browser, or: patchrondo init --repo PATH")
        elif command == "doctor":
            for name in doctor.PROVIDERS:
                print("\n".join(doctor.report_lines(doctor.check(name))))
        elif command == "list" and not opts.project and not workspace.has_legacy_root() \
                and len(projects := workspace.projects()) > 1:
            for project in projects:
                print(f"# {project.name} ({project.id})")
                _print_tasks(project.home)
        else:
            home = _project(workspace, opts.project, getattr(opts, "task_id", None) or getattr(opts, "task", None)).home
            if command == "new":
                description = opts.task_file.read_text(encoding="utf-8") if opts.task_file else opts.description
                task_id, workspace_path = create_task(home, title=opts.title, description=description,
                     acceptance=opts.accept, developer=opts.developer, reviewer=opts.reviewer)
                print(f'Created {task_id}\nWorktree: {workspace_path}\nRun: patchrondo --home "{root}" run {task_id}')
            elif command in {"run", "resume"}:
                auto = recovery.active(task_config(home, opts.task_id), opts.auto_resume)
                if os.environ.get("PATCHRONDO_UI_CHILD") == "1" and signal.getsignal(signal.SIGINT) == signal.SIG_IGN:
                    # Started detached by the interface: its Stop button must still be able to interrupt.
                    signal.signal(signal.SIGINT, signal.default_int_handler)
                with runinfo.attached(task_dir(home, opts.task_id), auto):
                    state = recovery.supervise(home, opts.task_id, force_unlock=opts.unlock,
                                               auto_resume=opts.auto_resume,
                                               notify=lambda message: print(message, flush=True))
                print(f"{state['id']} · {state['status']} · iteration {state['iteration']} · phase {state['phase']}")
                print(f"Report: {home / 'tasks' / opts.task_id / 'report.md'}")
                if state.get("last_error"):
                    print(f"Details: {state['last_error']}")
                plan = recovery.describe(state.get("recovery"))
                if plan:
                    print(plan)
                return 0 if state["status"] == "done" else 2
            elif command == "status":
                print(json.dumps(read_json(task_dir(home, opts.task_id) / "state.json"), indent=2, ensure_ascii=False))
            elif command == "report":
                file = task_dir(home, opts.task_id) / "report.md"
                if not file.exists():
                    print("Report is not available yet; run the task first.")
                    return 1
                print(file.read_text(encoding="utf-8"))
            elif command == "list":
                _print_tasks(home)
            elif command in {"index", "search"}:
                cfg = config(home)
                target = (Path(read_json(task_dir(home, opts.task) / "state.json")["worktree"])
                          if opts.task else Path(cfg["repository"]))
                with rag.Index(rag.index_path(home)) as index:
                    stats = index.update(target)
                    if command == "index":
                        print(f"Indexed {stats['files']} files · {stats['updated']} updated · "
                              f"{stats['removed']} removed · {stats['chunks_added']} new chunks")
                    else:
                        context = rag.render(index.search(target, opts.query, opts.limit), 10**9)
                        print(context or "No matches.")
    except (OSError, ValueError, RuntimeError, KeyError, LookupError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0
