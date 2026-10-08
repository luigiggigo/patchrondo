"""CLI: initialize, create, run, resume, inspect and report tasks."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

from .core import initialize, config, create_task, run_task, task_dir
from . import rag
from .storage import read_json
from .process import execute


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="patchrondo", description="PatchRondo — Code. Review. Repeat. Claude ↔ Codex orchestrator (local)")
    p.add_argument("--home", type=Path, default=Path(os.environ.get("PATCHRONDO_HOME", "~/.patchrondo")).expanduser(),
                   help="Private directory for configuration, tasks and worktrees")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Initialize a Git repository")
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
    sub.add_parser("list", help="List tasks")
    index = sub.add_parser("index", help="Update the local retrieval index (no model calls)")
    search = sub.add_parser("search", help="Query the local retrieval index (no model calls)")
    search.add_argument("query")
    search.add_argument("-k", "--limit", type=int, default=8, help="Maximum number of excerpts")
    for cmd in (index, search):
        cmd.add_argument("--task", help="Use a task worktree instead of the configured repository")
    sub.add_parser("doctor", help="Check CLI availability and authentication without model calls")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    opts = parser().parse_args(argv)
    home = opts.home.expanduser().resolve()
    try:
        if opts.command == "init":
            print(f"Configuration created: {initialize(home, opts.repo)}")
            print("Tests are disabled by default. Edit config.json to configure and authorize them.")
        elif opts.command == "new":
            description = opts.task_file.read_text(encoding="utf-8") if opts.task_file else opts.description
            task_id, workspace = create_task(home, title=opts.title, description=description,
                 acceptance=opts.accept, developer=opts.developer, reviewer=opts.reviewer)
            print(f'Created {task_id}\nWorktree: {workspace}\nRun: patchrondo --home "{home}" run {task_id}')
        elif opts.command in {"run", "resume"}:
            state = run_task(home, opts.task_id, force_unlock=opts.unlock)
            print(f"{state['id']} · {state['status']} · iteration {state['iteration']} · phase {state['phase']}")
            print(f"Report: {home / 'tasks' / opts.task_id / 'report.md'}")
            if state.get("last_error"):
                print(f"Details: {state['last_error']}")
            return 0 if state["status"] == "done" else 2
        elif opts.command == "status":
            print(json.dumps(read_json(task_dir(home, opts.task_id) / "state.json"), indent=2, ensure_ascii=False))
        elif opts.command == "report":
            file = task_dir(home, opts.task_id) / "report.md"
            if not file.exists():
                print("Report is not available yet; run the task first.")
                return 1
            print(file.read_text(encoding="utf-8"))
        elif opts.command == "list":
            folder = home / "tasks"
            for file in sorted(folder.glob("T-*/state.json")) if folder.exists() else []:
                s = read_json(file)
                print(f"{s['id']} · {s['status']:8} · {s['developer']} → {s['reviewer']} · {s['title']}")
        elif opts.command in {"index", "search"}:
            cfg = config(home)
            root = (Path(read_json(task_dir(home, opts.task) / "state.json")["worktree"])
                    if opts.task else Path(cfg["repository"]))
            with rag.Index(rag.index_path(home)) as index:
                stats = index.update(root)
                if opts.command == "index":
                    print(f"Indexed {stats['files']} files · {stats['updated']} updated · "
                          f"{stats['removed']} removed · {stats['chunks_added']} new chunks")
                else:
                    context = rag.render(index.search(root, opts.query, opts.limit), 10**9)
                    print(context or "No matches.")
        elif opts.command == "doctor":
            for name, auth in (("claude", ["claude", "auth", "status"]),
                               ("codex", ["codex", "login", "status"])):
                path = shutil.which(name)
                if not path:
                    print(f"{name}: NOT INSTALLED")
                    continue
                check = execute(auth, Path.cwd(), timeout=20)
                if name == "claude" and check.returncode == 0:
                    try:
                        method = json.loads(check.stdout).get("authMethod", "unknown")
                    except json.JSONDecodeError:
                        method = "unknown"
                    print(f"claude: installed · authenticated · method {method}")
                    if method not in {"claude.ai", "oauth_token"}:
                        print("  Warning: check whether API billing is being used")
                else:
                    print(f"{name}: installed · auth {'OK' if check.returncode == 0 else 'NOT CONFIRMED'}")
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0
