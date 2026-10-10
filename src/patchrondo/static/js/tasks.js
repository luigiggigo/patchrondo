// How task data is worded and toned. No operational logic lives here: what a task can
// do and what is attached to it comes from the backend (`task.runtime`).

import { clock, fullDate, humanize, plural } from "./format.js";

export const AGENTS = { claude: "Claude", codex: "Codex" };
export const agentName = (name) => AGENTS[name] || name || "Unknown";

export const ACTIVITY = {
  ready: { label: "Ready", tone: "neutral", icon: "play", group: "ready" },
  starting: { label: "Starting", tone: "accent", icon: "bolt", live: true, group: "active" },
  running: { label: "Running", tone: "accent", icon: "bolt", live: true, group: "active" },
  running_unverified: { label: "Running · unverified", tone: "accent", icon: "bolt", group: "active" },
  waiting_retry: { label: "Waiting to retry", tone: "wait", icon: "clock", live: true, group: "active" },
  plan_only: { label: "Retry planned · nothing waiting", tone: "warn", icon: "clock", group: "attention" },
  plan_unverified: { label: "Retry planned · unverified", tone: "warn", icon: "clock", group: "attention" },
  recovery_stopped: { label: "Recovery ended", tone: "warn", icon: "pause", group: "attention" },
  paused: { label: "Paused", tone: "warn", icon: "pause", group: "attention" },
  interrupted: { label: "Interrupted", tone: "warn", icon: "alert", group: "attention" },
  stale_lock: { label: "Stopped unexpectedly", tone: "danger", icon: "lock", group: "attention" },
  blocked: { label: "Blocked", tone: "danger", icon: "x", group: "attention" },
  done: { label: "Done", tone: "ok", icon: "check", group: "done" },
  unknown: { label: "Unknown", tone: "neutral", icon: "info", group: "attention" },
};

export function activityOf(task) {
  return ACTIVITY[task?.runtime?.activity] || ACTIVITY.unknown;
}

export const GROUPS = [
  ["all", "All"], ["active", "Active"], ["attention", "Needs attention"], ["ready", "Ready"], ["done", "Done"],
];

export function countGroups(tasks) {
  const counts = { all: tasks.length, active: 0, attention: 0, ready: 0, done: 0 };
  for (const task of tasks) counts[activityOf(task).group] += 1;
  return counts;
}

export const VERDICTS = {
  APPROVED: { label: "Approved", tone: "ok", icon: "check" },
  CHANGES_REQUESTED: { label: "Changes requested", tone: "warn", icon: "repeat" },
  BLOCKED: { label: "Blocked by reviewer", tone: "danger", icon: "x" },
};
export const SEVERITY = { low: "neutral", medium: "warn", high: "danger", critical: "danger" };
export const TEST_TONE = { passed: "ok", failed: "danger", timeout: "warn", skipped: "neutral" };

export const ERRORS = {
  quota: { title: "Usage limit reached", help: "The provider reported a usage limit for your plan. PatchRondo never works around a limit: wait for it to reset, then resume." },
  authentication: { title: "Login required", help: "The CLI is not logged in. Run its login command in a terminal (see Settings → Agents), then resume." },
  configuration: { title: "Something is not installed or configured", help: "A provider CLI or a test command was not found. Check Settings → Agents and Settings → Tests, then resume." },
  tests_disabled: { title: "Project tests are disabled", help: "A task is done only when the project tests pass and the review approves. Enable tests in Settings → Tests, then resume." },
  stale_tests: { title: "Files changed after the tests ran", help: "The worktree or the test commands changed since the last test run. Resume to run the tests again." },
  interrupted: { title: "The run was interrupted", help: "Resume continues from the last saved checkpoint; only the unfinished step is repeated." },
  timeout: { title: "A step timed out", help: "An agent or a test command ran longer than the configured timeout. Check the logs; raise the timeout in Settings → Workflow if it was legitimate, then resume." },
  invalid_review: { title: "The review could not be read", help: "The reviewer did not answer in the expected format. Resume to ask for the review again." },
  empty_output: { title: "The agent returned nothing", help: "The CLI finished without a final answer. Check the logs, then resume." },
  invalid_output: { title: "The agent output was not usable", help: "Check the logs, then resume." },
  agent_error: { title: "The agent CLI failed", help: "The CLI exited with an error. The message and the logs show what it printed; resume when the cause is resolved." },
  system_error: { title: "PatchRondo hit a system error", help: "A file or Git operation failed. Check the message, then resume." },
  max_iterations_reached: { title: "Iteration limit reached", help: "The task used all its iterations without an approved review and passing tests. Review the feedback and create a follow-up task." },
  review_blocked: { title: "The reviewer blocked the task", help: "The reviewer judged that the task cannot proceed as specified. Read the review and create a new task." },
  review_not_approved: { title: "The review was not approved", help: "Read the review for what is missing." },
  tests_missing_or_failed: { title: "Tests are missing or failed", help: "Check the Tests tab." },
  no_code_changes: { title: "No changes were made", help: "The developer agent left the worktree unchanged." },
};

export function errorOf(task) {
  const error = task?.last_error;
  if (!error) return null;
  const kind = typeof error === "object" ? error.kind : error;
  const known = ERRORS[kind] || { title: humanize(kind || "Stopped"), help: "Check the message and the logs, then resume." };
  return { kind, ...known, message: typeof error === "object" ? error.message : null,
           provider: typeof error === "object" ? error.provider : null, at: typeof error === "object" ? error.at : null,
           retry_at: typeof error === "object" ? error.retry_at : null };
}

export const RECOVERY_SOURCES = {
  provider_reset: "Reset time stated by the provider, plus the safety margin",
  backoff: "Exponential backoff (the provider stated no usable reset time)",
};
export const RECOVERY_STOPS = {
  max_consecutive_retries: "The limit of consecutive retries was reached",
  wait_budget_exceeded: "The next wait would exceed the total wait budget",
  reset_beyond_budget: "The provider reset is beyond the total wait budget; no earlier call was made",
  invalid_schedule: "The saved plan was unreadable or no longer matched a usage-limit pause",
};
export const stopReason = (reason) => RECOVERY_STOPS[reason] || `${humanize(reason)} is never retried automatically`;

export const PHASES = [["develop", "Develop"], ["test", "Test"], ["review", "Review"], ["complete", "Complete"]];

// One state per phase: done | current | waiting | paused | error | next | pending.
export function workflow(task) {
  const index = PHASES.findIndex(([key]) => key === task.phase);
  const activity = task.runtime?.activity;
  return PHASES.map(([key, label], position) => {
    let step = "pending";
    if (task.status === "done" || (index >= 0 && position < index)) step = "done";
    else if (position === index) {
      if (["running", "starting", "running_unverified"].includes(activity)) step = "current";
      else if (["waiting_retry", "plan_only", "plan_unverified"].includes(activity)) step = "waiting";
      else if (["blocked", "stale_lock"].includes(activity)) step = "error";
      else if (activity === "ready") step = "next";
      else step = "paused";
    }
    return { key, label, step };
  });
}

export function phaseSentence(task) {
  const dev = agentName(task.developer), rev = agentName(task.reviewer);
  if (task.phase === "develop") return `${dev} is developing`;
  if (task.phase === "test") return "Project tests are running";
  if (task.phase === "review") return `${rev} is reviewing`;
  return "Finishing";
}

const PAUSE_REASONS = {
  quota: "usage limit", authentication: "login required", configuration: "configuration problem",
  tests_disabled: "tests are disabled", stale_tests: "files changed after the tests", interrupted: "interrupted",
  timeout: "timeout", invalid_review: "unreadable review",
};

// A persisted history entry as a sentence. Nothing here is inferred beyond the entry itself.
export function describeEvent(event, task) {
  const dev = agentName(task?.developer), rev = agentName(task?.reviewer);
  const statuses = event.statuses || [];
  switch (event.event) {
    case "run_started":
      return { text: event.previous_status === "ready" ? "Run started" : "Run resumed", tone: "accent", icon: "play" };
    case "development_started": return { text: `${dev} started development`, tone: "accent", icon: "code" };
    case "development_completed": return { text: `${agentName(event.agent) || dev} finished development · handoff saved`, tone: "ok", icon: "code" };
    case "tests_started": return { text: `Tests started (${plural(event.commands ?? 0, "command")})`, tone: "accent", icon: "flask" };
    case "tests_completed":
      if (statuses.includes("timeout")) return { text: "Tests timed out", tone: "warn", icon: "flask" };
      if (statuses.includes("failed")) return { text: "Tests failed", tone: "danger", icon: "flask" };
      if (statuses.length && statuses.every((s) => s === "skipped")) return { text: "Tests skipped (disabled for this project)", tone: "neutral", icon: "flask" };
      return { text: "Tests passed", tone: "ok", icon: "flask" };
    case "review_started": return { text: `${rev} started the review`, tone: "accent", icon: "eye" };
    case "review_completed": {
      const verdict = VERDICTS[event.verdict];
      return { text: `${rev}: ${verdict ? verdict.label.toLowerCase() : event.verdict}`, tone: verdict?.tone || "neutral", icon: "eye" };
    }
    case "task_completed": return { text: "Task completed", tone: "ok", icon: "check" };
    case "task_paused": return { text: `Paused: ${PAUSE_REASONS[event.reason] || humanize(event.reason).toLowerCase()}`, tone: "warn", icon: "pause" };
    case "task_blocked": return { text: `Blocked: ${(ERRORS[event.reason]?.title || humanize(event.reason)).toLowerCase()}`, tone: "danger", icon: "x" };
    case "retrieval_failed": return { text: "Repository context could not be retrieved; the prompt was sent without it", tone: "warn", icon: "database", detail: event.message };
    case "recovery_scheduled":
      return { text: `Retry ${event.attempt ?? ""} planned for ${fullDate(event.resume_at)}`.replace("  ", " "), tone: "wait", icon: "clock",
               detail: event.source === "provider_reset" ? "from the provider's reset time" : "exponential backoff" };
    case "recovery_wait_started": return { text: "A process started waiting for the planned retry", tone: "wait", icon: "clock", detail: event.supervisor_pid ? `PID ${event.supervisor_pid}` : null };
    case "recovery_retry_started": return { text: `Automatic retry ${event.attempt ?? ""} started`.replace("  ", " "), tone: "accent", icon: "repeat" };
    case "recovery_completed": return { text: "The retry made progress; recovery finished", tone: "ok", icon: "check" };
    case "recovery_stopped": return { text: `Automatic recovery ended: ${stopReason(event.reason).toLowerCase()}`, tone: "warn", icon: "pause" };
    case "recovery_cancelled": return { text: "Planned retry cancelled by a manual run", tone: "neutral", icon: "x" };
    default: return { text: humanize(event.event), tone: "neutral", icon: "info" };
  }
}

export function eventTime(event) {
  return clock(event.at);
}
