"""Human-readable, reproducible reports from persisted state."""
from __future__ import annotations
from pathlib import Path
import subprocess
from .gitops import changes, stat

SOURCES = {"provider_reset": "reset time stated by the provider, plus the safety margin",
           "backoff": "exponential backoff (no usable reset time)"}
STOP_REASONS = {
    "max_consecutive_retries": "the limit of consecutive retries was reached",
    "wait_budget_exceeded": "the next backoff would exceed the total wait budget",
    "reset_beyond_budget": "the provider reset is beyond the total wait budget; no earlier call was made",
    "invalid_schedule": "the saved plan was unreadable or no longer matched a quota pause",
}
RECOVERY_DETAILS = ("attempt", "resume_at", "scheduled_for", "source", "provider", "reason", "failures", "retries")


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
    rows += _recovery_rows(state.get("recovery"))
    rows += ["", "## How to apply the changes", "",
             "The main repository is not changed automatically. Review the worktree,",
             "then commit on the `patchrondo/<task-id>` branch and integrate it manually.",
             "", "## History", ""]
    for evt in state.get("history", []):
        row = f"- {evt['at']} · iteration {evt['iteration']} · `{evt['event']}`"
        if str(evt["event"]).startswith("recovery_"):
            notes = [f"{key} {evt[key]}" for key in RECOVERY_DETAILS if evt.get(key) is not None]
            row += f" ({', '.join(notes)})" if notes else ""
        rows.append(row)
    return "\n".join(rows) + "\n"


def _recovery_rows(recovery) -> list[str]:
    """Automatic quota recovery as persisted. A saved plan is not a running service."""
    if not isinstance(recovery, dict):
        return []
    used, limit = recovery.get("consecutive_failures"), recovery.get("max_consecutive_retries")
    rows = ["", "## Quota recovery", "", f"- **State**: `{recovery.get('status')}`",
            f"- **Provider at its limit**: {recovery.get('provider') or 'unknown'}",
            f"- **Failed at**: phase `{recovery.get('phase')}`, iteration {recovery.get('iteration')}",
            f"- **Consecutive quota failures**: {used} since {recovery.get('first_failure_at')}"]
    if type(used) is int and type(limit) is int:
        rows.append(f"- **Automatic retries**: {min(used, limit)} of {limit} used, {max(limit - used, 0)} left")
    if recovery.get("status") == "scheduled":
        source = recovery.get("schedule_source")
        rows += [f"- **Planned retry**: {recovery.get('resume_at')} (UTC)",
                 f"- **Schedule source**: {SOURCES.get(source, source)}",
                 "", "The retry happens only while a `patchrondo run` or `resume` process with automatic",
                 "resume is waiting. Otherwise the plan stays saved and the task stays paused."]
    elif recovery.get("status") == "stopped":
        reason = recovery.get("stop_reason")
        rows += [f"- **Automatic recovery ended**: {STOP_REASONS.get(reason, f'`{reason}` is not retried automatically')}",
                 "", "Resume manually when the cause is resolved."]
    return rows
