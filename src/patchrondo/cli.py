"""CLI: open the local interface, or initialize, create, run, resume, inspect and report tasks."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys

from . import __version__, acceptance, agents, bundle, doctor, evidence, rag, recovery, runinfo
from .core import (attach_evidence, config, create_task, decide_authorization, draft_tests, initialize, recheck_acceptance,
                   release_attempt, task_config, task_dir)
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
    new.add_argument("--accept", action="append", default=[],
                     help="Acceptance criterion (repeatable). Start it with a tag to say what it is, as in \"[manual] Tried in "
                          "the game\" or \"[optional] ...\"; kinds: code, test, build, integration, external, publishing, manual")
    new.add_argument("--developer", choices=["claude", "codex"], default="claude")
    new.add_argument("--reviewer", choices=["claude", "codex"], default="codex")
    for role in agents.ROLES:
        new.add_argument(f"--{role}-model", metavar="MODEL", help=f"Model of the {role} for this task (an alias or a model ID); "
                         "default: the project's and the global agent settings")
        new.add_argument(f"--{role}-effort", metavar="LEVEL", help=f"Effort of the {role} for this task")
    for action in ("run", "resume", "status", "report"):
        cmd = sub.add_parser(action)
        cmd.add_argument("task_id", help="Task ID such as T-0123456789ab")
        if action in {"run", "resume"}:
            cmd.add_argument("--unlock", action="store_true", help="Recover a stale lock only after checking process IDs")
            cmd.add_argument("--auto-resume", action=argparse.BooleanOptionalAction, default=None,
                             help="After a provider usage limit, keep this process waiting and retry at the reset "
                                  "or after a backoff, within the configured caps. --no-auto-resume runs once and "
                                  "cancels a pending retry plan. Default: recovery.enabled in config.json")
            cmd.add_argument("--draft-tests", action="store_true",
                             help="Instead of resuming: while the task waits for the test consent, ask its developer to "
                                  "write tests for the work done and propose commands (a real agent call). Nothing runs "
                                  "and tests stay disabled")
    shown = sub.add_parser("criteria", help="Show the acceptance criteria of a task: who verifies each one, and with what evidence")
    shown.add_argument("task_id", help="Task ID such as T-0123456789ab")
    shown.add_argument("--check", action="store_true",
                       help="Detect capabilities and judge the criteria from the evidence again first (no model calls)")
    attest = sub.add_parser("evidence", help="Record a check you did by hand for one criterion (no model calls)")
    attest.add_argument("task_id", help="Task ID such as T-0123456789ab")
    attest.add_argument("criterion", help="Criterion ID such as AC-03")
    attest.add_argument("--summary", required=True, help="What you did and what you saw")
    attest.add_argument("--reference", help="Where the proof can be found: a link, a commit, a log")
    attest.add_argument("--artifact", help="A file PatchRondo hashes (SHA-256) as part of the evidence")
    attest.add_argument("--failed", action="store_true", help="The check did not pass: the developer gets it as a change request")
    decide = sub.add_parser("authorize", help="Decide about the operation a criterion needs. PatchRondo runs nothing either way")
    decide.add_argument("task_id", help="Task ID such as T-0123456789ab")
    decide.add_argument("criterion", help="Criterion ID such as AC-05")
    choice = decide.add_mutually_exclusive_group(required=True)
    choice.add_argument("--grant", action="store_true", help="You take the operation on yourself, and its evidence")
    choice.add_argument("--deny", action="store_true", help="The operation is not done; the criterion stays open")
    released = sub.add_parser("release-attempt",
                              help="After checking that no process of an agent call is left, let the task call again "
                                   "(no model calls)")
    released.add_argument("task_id", help="Task ID such as T-0123456789ab")
    released.add_argument("attempt", help="The attempt the pause names, such as CX-0003")
    released.add_argument("--confirm-stopped", action="store_true",
                          help="Required: you checked that no process of that call is left. PatchRondo cannot verify "
                               "it, and the release says nothing about what the call did")
    bundled =sub.add_parser("bundle", help="Export the evidence of a task as a ZIP, or check such a file (no model calls)")
    steps = bundled.add_subparsers(dest="bundle_command", required=True)
    exported = steps.add_parser("export", help="Write an evidence bundle of a task: its records, decisions and a manifest of hashes")
    exported.add_argument("task_id", help="Task ID such as T-0123456789ab")
    exported.add_argument("--detailed", action="store_true",
                          help="Also sanitized copies of the logs and report copies the ledger names (masking is best effort)")
    exported.add_argument("-o", "--output", type=Path, help="The ZIP to write (default: its own name in the current folder); "
                                                           "an existing file is never replaced")
    checked = steps.add_parser("verify", help="Check an evidence bundle: hashes, names, manifest and the records it holds")
    checked.add_argument("file", type=Path, help="The bundle (.zip)")
    checked.add_argument("--json", action="store_true", help="Print the whole result as JSON")
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


def _print_criteria(path: Path) -> None:
    """The criteria of one task judged against its evidence ledger as it is now, naming what was saved where it differs.

    Nothing is written and nothing is run; `--check` judges them again with the files as they are and saves that.
    """
    state = read_json(path / "state.json")
    record = acceptance.Acceptance.load(state)
    if record is None:
        print("This task was created before criteria were tracked. They are recorded, with nothing taken as verified, "
              "at its next run or with: patchrondo criteria TASK --check")
        return
    ledger = evidence.read(path).for_state(record.ledger)  # with the problem of a ledger that lost what was saved
    record, saved = acceptance.judged_now(record, ledger,
                                          developing=state.get("status") == "running" and state.get("phase") == "develop")
    counts = record.summary()
    print(f"Code: {record.code.get('status')} · Overall: {record.outcome} · "
          f"{counts['required_verified']} of {counts['required']} required criteria verified")
    for criterion in record.criteria:
        kinds = ", ".join(criterion.categories) or "unclassified"
        was = f" (saved as {saved[criterion.id]})" if saved[criterion.id] != criterion.status else ""
        print(f"{criterion.id} · {criterion.status:26} · {'required' if criterion.required else 'optional'} · {kinds} · "
              f"done by {criterion.executor}, verified by {criterion.verifier}{was}")
        print(f"    {criterion.text}")
        print(f"    {criterion.reason}" + (f" [{', '.join(criterion.evidence)}]" if criterion.evidence else ""))
    for problem in ledger.problems:
        print(f"Warning: {problem}")


def _verify_bundle(file: Path, as_json: bool) -> int:
    """Check an evidence bundle and print the verdict. The exit code is the verdict's (`bundle.EXIT_CODES`)."""
    found = bundle.verify(file)
    if as_json:
        print(json.dumps(found, indent=2, ensure_ascii=False))
        return found["exit_code"]
    shown = found["manifest"] or {}
    print(f"{found['status']} · {shown.get('bundle_id') or 'no bundle ID'} · task {shown.get('task_id') or '?'} · "
          f"{found['files_checked']} file(s) checked")
    gate = shown.get("gate")
    if isinstance(gate, dict):
        print(f"Gate as exported: {gate.get('decision')}" + ("" if gate.get("about_exported_ledger") else
                                                              " (saved before the ledger moved on)"))
    for entry in found["problems"]:
        print(f"- {entry['code']}: {entry['detail']}" + (f" ({entry['path']})" if entry.get("path") else ""))
    print(f"Authenticity: {found['note']}")
    return found["exit_code"]


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
        elif command == "bundle" and opts.bundle_command == "verify":
            return _verify_bundle(opts.file, opts.json)
        elif command == "list" and not opts.project and (root / REGISTRY).exists() \
                and len(projects := workspace.projects()) > 1:
            for project in projects:
                print(f"# {project.name} ({project.id})")
                _print_tasks(project.home)
        else:
            home = _project(workspace, opts.project, getattr(opts, "task_id", None) or getattr(opts, "task", None)).home
            if command == "new":
                description = opts.task_file.read_text(encoding="utf-8") if opts.task_file else opts.description
                chosen = {role: {getattr(opts, role): {key: value for key in ("model", "effort")
                                                       if (value := getattr(opts, f"{role}_{key}")) is not None}}
                          for role in agents.ROLES}
                task_id, workspace_path = create_task(home, title=opts.title, description=description,
                     acceptance=opts.accept, developer=opts.developer, reviewer=opts.reviewer, agent_settings=chosen)
                print(f'Created {task_id}\nWorktree: {workspace_path}\nRun: patchrondo --home "{root}" run {task_id}')
            elif command in {"run", "resume"}:
                auto = recovery.active(task_config(home, opts.task_id), opts.auto_resume)
                if os.environ.get("PATCHRONDO_UI_CHILD") == "1" and signal.getsignal(signal.SIGINT) == signal.SIG_IGN:
                    # Started detached by the interface: its Stop button must still be able to interrupt.
                    signal.signal(signal.SIGINT, signal.default_int_handler)
                # The global agent settings live beside the project registry. A run started by the interface
                # is given a project's own state directory as its home and is told where they are.
                shared = root if (root / REGISTRY).exists() or (root / agents.STORE).exists() else None
                if opts.draft_tests:
                    if opts.auto_resume is not None:
                        raise ValueError("--draft-tests takes no --auto-resume option: it never retries")
                    with runinfo.attached(task_dir(home, opts.task_id), False):
                        state = draft_tests(home, opts.task_id, force_unlock=opts.unlock, agents_root=shared)
                    draft = state["test_draft"]
                    if draft["status"] == "completed":
                        print(f"{state['id']} · tests written: {len(draft['files'])} file(s) changed, "
                              f"{len(draft['commands'])} command(s) proposed. Tests stay disabled until you allow them.")
                        for line in draft["commands"]:
                            print(f"  {line}")
                        return 0
                    print(f"{state['id']} · the developer did not write tests: {draft['message']}")
                    return 2
                with runinfo.attached(task_dir(home, opts.task_id), auto):
                    state = recovery.supervise(home, opts.task_id, force_unlock=opts.unlock,
                                               auto_resume=opts.auto_resume,
                                               notify=lambda message: print(message, flush=True), agents_root=shared)
                print(f"{state['id']} · {state['status']} · iteration {state['iteration']} · phase {state['phase']}")
                print(f"Report: {home / 'tasks' / opts.task_id / 'report.md'}")
                if state.get("last_error"):
                    print(f"Details: {state['last_error']}")
                plan = recovery.describe(state.get("recovery"))
                if plan:
                    print(plan)
                return 0 if state["status"] == "done" else 2
            elif command in {"criteria", "evidence", "authorize"}:
                shared = root if (root / REGISTRY).exists() or (root / agents.STORE).exists() else None
                if command == "evidence":
                    done = attach_evidence(home, opts.task_id, opts.criterion, summary=opts.summary, reference=opts.reference,
                                           artifact=opts.artifact, passed=not opts.failed, agents_root=shared)
                    print(f"Recorded {done['record']['id']} for {opts.criterion}: {done['record']['result']}")
                elif command == "authorize":
                    done = decide_authorization(home, opts.task_id, opts.criterion, granted=opts.grant, agents_root=shared)
                    print(f"Recorded {done['record']['id']} for {opts.criterion}: {done['record']['result']}. Nothing was run.")
                elif opts.check:
                    done = recheck_acceptance(home, opts.task_id, agents_root=shared)
                else:
                    done = None
                _print_criteria(task_dir(home, opts.task_id))
                if done and done["completes"]:
                    print(f"Every required criterion is verified. To complete the task: patchrondo resume {opts.task_id}")
            elif command == "bundle":
                made = bundle.build(task_dir(home, opts.task_id), "detailed" if opts.detailed else "summary")
                target = opts.output or Path(made.name)
                with open(target, "xb") as stream:  # never replaces a file
                    stream.write(made.data)
                manifest = made.manifest
                gate = manifest["gate"]
                print(f"Wrote {target} ({len(made.data)} bytes) · {manifest['bundle_id']} · {manifest['mode']}")
                print(f"Gate: {gate['decision'] if gate else 'not evaluated'}" + ("" if not gate or gate["about_exported_ledger"]
                                                                                   else " (saved before the ledger moved on)"))
                for entry in manifest["omissions"]:
                    print(f"Left out: {entry['item']}: {entry['reason']}")
                print("Hashes show changes after the export; nothing is signed. Check it with: patchrondo bundle verify FILE")
            elif command == "release-attempt":
                state = release_attempt(home, opts.task_id, opts.attempt, confirm=opts.confirm_stopped)
                print(f"{state['id']} · {opts.attempt} released: you stated that its processes stopped. Nothing was run, "
                      "and its outcome stays as recorded (what it did is not known). "
                      f"To continue: patchrondo resume {opts.task_id}")
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
