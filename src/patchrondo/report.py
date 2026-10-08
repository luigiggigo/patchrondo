"""Human-readable, reproducible reports from persisted state."""
from __future__ import annotations
from pathlib import Path
import subprocess
from .gitops import changes, stat


def render_report(state: dict) -> str:
    workspace = Path(state["worktree"])
    try:
        files = changes(workspace) if workspace.exists() else ["(missing worktree)"]
        diffstat = stat(workspace) if workspace.exists() else ""
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        files = ["(Git status unavailable)"]
        diffstat = f"Unable to read the Git diff: {exc}"
    statuses = [x.get("status", "unknown") for x in state.get("tests", [])]
    rows = [f"# Report: {state['title']}", "", f"- **Task**: `{state['id']}`",
            f"- **Status**: `{state['status']}`", f"- **Iterations**: {state['iteration']}",
            f"- **Developer**: {state['developer']}", f"- **Reviewer**: {state['reviewer']}",
            f"- **Worktree**: `{workspace}`", f"- **Git base**: `{state['base_sha']}`",
            "", "## Changed files", ""]
    rows += [f"- `{f}`" for f in files] if files else ["- No changes"]
    rows += ["", "## Diff stat (tracked files)", "", "```", diffstat or "(none)", "```",
             "", "## Tests", "", f"Results: {', '.join(statuses) if statuses else 'not run'}", ""]
    for t in state.get("tests", []):
        cmd = " ".join(t.get("command", [])) or "(disabled)"
        rows.append(f"- `{cmd}` → **{t.get('status', '?')}**")
        if t.get("log_file"):
            rows.append(f"  Log: `{t['log_file']}`")
    review = state.get("review")
    rows += ["", "## Review", ""]
    if review:
        rows += [f"Verdict: **{review['verdict']}**", "", review.get("summary", ""), ""]
        for issue in review.get("issues", []):
            rows.append(f"- **{issue['severity']}** `{issue.get('path', '')}`: {issue['description']}")
    else:
        rows.append("No review completed")
    if state.get("last_error"):
        rows += ["", "## Block/interruption", "", f"`{state['last_error']}`"]
    rows += ["", "## How to apply the changes", "",
             "The main repository is not changed automatically. Review the worktree,",
             "then commit on the `patchrondo/<task-id>` branch and integrate it manually.",
             "", "## History", ""]
    for evt in state.get("history", []):
        rows.append(f"- {evt['at']} · iteration {evt['iteration']} · `{evt['event']}`")
    return "\n".join(rows) + "\n"
