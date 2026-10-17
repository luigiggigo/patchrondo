"""Human-readable, reproducible reports from persisted state."""
from __future__ import annotations
from pathlib import Path
import subprocess
from .acceptance import Acceptance
from .activity import clip, redact
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


def _agent_rows(runs) -> list[str]:
    """The agent settings each step was started with, when any step had some; nothing for the CLIs' own defaults."""
    if not isinstance(runs, dict) or not any(isinstance(record, dict) and record.get("values") for record in runs.values()):
        return []
    rows = ["", "## Agent settings used", "", "Each step keeps the settings it first started with, also when it is resumed or retried.", ""]
    for key in sorted(runs):
        record = runs[key]
        if not isinstance(record, dict) or not isinstance(record.get("values"), dict):
            continue
        number, _, role = key.partition(":")
        sources = record.get("sources") if isinstance(record.get("sources"), dict) else {}
        shown = [f"{name} `{clip(redact(str(value)), 80)}`" + (f" ({sources[name]})" if sources.get(name) else "")
                 for name, value in sorted(record["values"].items())]
        rows.append(f"- iteration {int(number) if number.isdigit() else number} · {role} · {record.get('provider')} · "
                    f"{', '.join(shown) or 'CLI defaults'}")
    return rows


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
        if review.get("reported_verdict"):
            # The verdict is about the code; the answer was settled from its structured parts, never from its prose.
            rows += [f"The reviewer answered `{review['reported_verdict']}`; settled as `{review['verdict']}` "
                     f"(`{review.get('normalized')}`).", ""]
        if review.get("refused_changes"):
            rows += [f"A change to the code asked for {', '.join(review['refused_changes'])}, which no change to the files can "
                     "satisfy, is recorded and was not sent to the developer.", ""]
        for issue in review.get("issues", []):
            kind = f" ({issue['kind']})" if issue.get("kind") else ""
            rows.append(f"- **{issue['severity']}**{kind} `{issue.get('path', '')}`: {issue['description']}")
    else:
        rows.append("No review completed")
    if state.get("last_error"):
        rows += ["", "## Block/interruption", "", f"`{state['last_error']}`"]
    rows += _acceptance_rows(state.get("acceptance"))
    rows += _agent_rows(state.get("agent_runs"))
    rows += _environment_rows(state.get("environment"))
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


def _acceptance_rows(block) -> list[str]:
    """The criteria of the task as last judged: approval of the code, overall result, and each criterion with its evidence.

    A task created before criteria were tracked, and never run since, has no
    such record: nothing is said about its criteria.
    """
    if not isinstance(block, dict) or not isinstance(block.get("criteria"), list):
        return []
    try:
        Acceptance.load({"acceptance": block})
    except ValueError as exc:
        return ["", "## Acceptance criteria", "", "The saved criteria cannot be read by this version of PatchRondo, so nothing "
                f"is said about them here: {clip(redact(str(exc)), 400)}"]
    code = block.get("code") if isinstance(block.get("code"), dict) else {}
    counts = block.get("summary") if isinstance(block.get("summary"), dict) else {}
    rows = ["", "## Acceptance criteria", "",
            f"- **Code**: `{code.get('status')}` (the review approved it, the project's commands passed and there are changes)"
            if code.get("status") == "APPROVED" else f"- **Code**: `{code.get('status')}`",
            f"- **Overall**: `{block.get('outcome')}`",
            f"- **Required criteria verified**: {counts.get('required_verified')} of {counts.get('required')}",
            "", "A criterion is verified only by records of the evidence ledger (`evidence.jsonl`, next to this report).", ""]
    for criterion in block["criteria"]:
        if not isinstance(criterion, dict):
            continue
        kinds = ", ".join(criterion.get("categories") or []) or "unclassified"
        records = ", ".join(f"`{name}`" for name in criterion.get("evidence") or []) or "none"
        rows += [f"- `{criterion.get('id')}` **{criterion.get('status')}** · {'required' if criterion.get('required') else 'optional'}"
                 f" · {kinds} · done by {criterion.get('executor')}, verified by {criterion.get('verifier')} · evidence: {records}",
                 f"  {clip(redact(str(criterion.get('text'))), 300)}"]
        if criterion.get("reason"):
            rows.append(f"  {clip(redact(str(criterion['reason'])), 400)}")
    return rows


def _environment_rows(record) -> list[str]:
    """The last environment check, when it found a problem or repaired something."""
    if not isinstance(record, dict) or (record.get("status") in {"ready", "checking"} and not record.get("repairs")):
        return []
    rows = ["", "## Environment check", "", f"- **State**: `{record.get('status')}`"]
    if record.get("error_kind"):
        rows += [f"- **Problem**: `{record['error_kind']}` (cause: `{record.get('cause')}`)", f"- {record.get('message')}"]
        rows.append("- **Repair**: " + ("available; it needs your confirmation or another attempt" if record.get("repair_available")
                                        else "none that PatchRondo may apply"))
    for check in record.get("checks") or []:
        if isinstance(check, dict) and check.get("status") == "failed":
            rows.append(f"- Failed check `{check.get('id')}`: {check.get('detail') or check.get('label')}")
    for entry in record.get("repairs") or []:
        if isinstance(entry, dict):
            paths = ", ".join(f"`{change.get('path')}`" for change in entry.get("changed") or [] if isinstance(change, dict))
            rows.append(f"- {entry.get('at')} · repair `{entry.get('repair')}` → **{entry.get('outcome')}**"
                        + (f" on {paths}" if paths else "") + (f" ({entry.get('error')})" if entry.get("error") else ""))
    return rows


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
