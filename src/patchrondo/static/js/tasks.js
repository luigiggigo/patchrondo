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
  model_unavailable: { title: "The model is not available", help: "The provider refused the model this step was configured with: it may not exist, or your plan may not include it. Choose another model in Agents and press Test configuration; then use “Use the current settings” on this page and resume." },
  unsupported_option: { title: "The CLI did not accept an option", help: "The installed CLI stopped at one of the options of this step, before anything was sent to a model. Open Agents, press Test configuration to see which setting it is, change it or update the CLI; then use “Use the current settings” on this page and resume." },
  max_turns: { title: "Claude ran out of turns", help: "Claude used all the turns allowed for one step without finishing. This is not a usage limit, so nothing retries by itself. Raise “Claude turns per step” in Settings → Workflow or make the task smaller, then resume." },
  sandbox_access_denied: { title: "Codex cannot read the project yet", help: "A small fix is needed so that Codex can read the copy of the project made for this task." },
  sandbox_cwd_unavailable: { title: "Codex would start in the wrong folder", help: "A small fix is needed so that Codex starts inside the copy of the project made for this task." },
  sandbox_not_read_only: { title: "The review environment is not read-only", help: "The reviewer could change files here, so the review was not started and nothing was sent to Codex. Update Codex CLI or check the sandbox setting in its configuration, then resume." },
  sandbox_unverified: { title: "The review environment could not be confirmed", help: "PatchRondo could not prove that the reviewer is unable to change files, so the review was not started. Update Codex CLI, then resume." },
  sandbox_unavailable: { title: "Codex could not start its protected environment", help: "The review needs the protected environment of Codex and it did not start on this computer. Nothing was sent to Codex. Update Codex CLI, then resume." },
  sandbox_git_failed: { title: "Codex cannot use Git in its protected environment", help: "Nothing was sent to Codex. The technical details below show what Git answered." },
  git_dubious_ownership: { title: "Git does not trust this project folder", help: "The folder belongs to another user of this computer, so Git refuses to work in it. If the project is yours, you can tell Git to trust this folder, and only this one." },
  cli_missing: { title: "An agent is not installed", help: "Install it as shown in Settings → Agents, then resume. No agent was called." },
  system_error: { title: "PatchRondo hit a system error", help: "A file or Git operation failed. Check the message, then resume." },
  awaiting_validation: { title: "The code is approved; some criteria still need verification", help: "What is left cannot be done by the developer or checked by the reviewer, so the task was not sent back to them. “Criteria and checks” shows what each criterion needs." },
  awaiting_authorization: { title: "The code is approved; some criteria need your authorization", help: "An operation such as a download or a publication is never done without your decision. Decide in “Criteria and checks”; nothing is run either way." },
  acceptance_unreadable: { title: "The saved criteria cannot be read", help: "They were saved by another version of PatchRondo, or changed by hand. Nothing replaced them and no agent was called. Open the task with the version that saved them, or put back its state.json, then resume." },
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

export const PHASES = [["develop", "Develop"], ["test", "Test"], ["review", "Review"], ["validate", "Validate"], ["complete", "Complete"]];
export const PREPARE_STAGES = {
  agents: "Checking the agents", project: "Preparing the project", sandbox: "Checking the safe environment",
  repair: "Fixing an access problem",
};
const RUNNING = ["running", "starting", "running_unverified"];

// The environment check of a task, as the engine saved it: none | checking | ready | problem.
// "checking" needs a verified run; a check left unfinished by a run that died is not shown as running.
export function preparation(task) {
  const record = task.environment;
  const activity = task.runtime?.activity;
  if (!record) return { state: "none" };
  if (record.status === "checking") return RUNNING.includes(activity) ? { state: "checking", stage: record.stage } : { state: "none" };
  if (record.status === "ready") return { state: "ready", repaired: Boolean(record.repair_applied) };
  // A problem counts while it is what the task is paused on; a later pause for another reason replaces it.
  return task.status === "paused" && task.error_kind === record.error_kind ? { state: "problem", record } : { state: "none" };
}

// One state per step: done | current | waiting | paused | error | next | pending.
// The first step is the preparation PatchRondo does itself; the others are the phases of the engine.
export function workflow(task) {
  const index = PHASES.findIndex(([key]) => key === task.phase);
  const activity = task.runtime?.activity;
  const prepare = preparation(task);
  const held = prepare.state === "checking" || prepare.state === "problem";
  const first = { key: "prepare", label: "Prepare", step: "pending" };
  if (prepare.state === "checking") Object.assign(first, { step: "current", detail: PREPARE_STAGES[prepare.stage] });
  else if (prepare.state === "problem") first.step = "paused";
  else if (prepare.state === "ready" || task.status === "done") first.step = "done";
  else if (activity === "ready") first.step = "next";
  else if (!RUNNING.includes(activity)) first.detail = "not recorded"; // a task from before this check existed
  return [first, ...PHASES.map(([key, label], position) => {
    let step = "pending";
    if (task.status === "done" || (index >= 0 && position < index)) step = "done";
    else if (position === index && !held) {
      if (RUNNING.includes(activity)) step = "current";
      else if (["waiting_retry", "plan_only", "plan_unverified"].includes(activity)) step = "waiting";
      else if (["blocked", "stale_lock"].includes(activity)) step = "error";
      else if (activity === "ready") step = first.step === "next" ? "pending" : "next";
      else step = "paused";
    }
    // At Validate nobody is working: the task waits for evidence only a person or the project's commands can give.
    const detail = key === "validate" && step === "paused" && task.acceptance?.outcome !== "RELEASE_READY" ? "waiting for you" : undefined;
    return { key, label, step, detail };
  })];
}

export function phaseSentence(task) {
  const dev = agentName(task.developer), rev = agentName(task.reviewer);
  const prepare = preparation(task);
  if (prepare.state === "checking") return PREPARE_STAGES[prepare.stage] || "Preparing";
  if (task.phase === "develop") return `${dev} is developing`;
  if (task.phase === "test") return "Project tests are running";
  if (task.phase === "review") return `${rev} is reviewing`;
  return "Finishing";
}

const PAUSE_REASONS = {
  quota: "usage limit", authentication: "login required", configuration: "configuration problem",
  tests_disabled: "tests are disabled", stale_tests: "files changed after the tests", interrupted: "interrupted",
  timeout: "timeout", invalid_review: "unreadable review", max_turns: "Claude ran out of turns",
  model_unavailable: "the model is not available", unsupported_option: "the CLI did not accept an option",
  sandbox_access_denied: "Codex cannot read the project", sandbox_cwd_unavailable: "Codex would start in the wrong folder",
  sandbox_not_read_only: "the review environment is not read-only", sandbox_unverified: "the review environment could not be confirmed",
  sandbox_unavailable: "Codex could not start its protected environment", sandbox_git_failed: "Git does not work in the review environment",
  git_dubious_ownership: "Git does not trust the project folder", cli_missing: "an agent is not installed",
  awaiting_validation: "criteria wait for a verification", awaiting_authorization: "criteria wait for your authorization",
  acceptance_unreadable: "the saved criteria cannot be read",
};
const REPAIRS = { acl: "access to the project for the reviewer", git_trust: "Git trust for this project folder" };
const REPAIR_OUTCOMES = { applied: ["Fixed", "ok"], unchanged: ["Already in place", "neutral"], rolled_back: ["Could not fix; put back as it was", "warn"],
  failed: ["Could not fix", "warn"] };

// The model and effort a step was configured with, as the engine recorded them; empty for the CLI's own defaults.
export function configured(record) {
  return [record?.model, record?.effort ? `effort ${record.effort}` : null].filter(Boolean).join(" · ");
}

// A persisted history entry as a sentence. Nothing here is inferred beyond the entry itself.
export function describeEvent(event, task) {
  const dev = agentName(task?.developer), rev = agentName(task?.reviewer);
  const statuses = Array.isArray(event.statuses) ? event.statuses : [];
  switch (event.event) {
    case "run_started":
      return { text: event.previous_status === "ready" ? "Run started" : "Run resumed", tone: "accent", icon: "play" };
    case "development_started": return { text: `${dev} started development`, tone: "accent", icon: "code", detail: configured(event) || null };
    case "agent_settings_refreshed":
      return { text: `The ${event.role || "next"} step will start with the current agent settings`, tone: "neutral", icon: "user", detail: "Asked for by you; the step no longer keeps the settings it first started with" };
    case "development_completed": return { text: `${agentName(event.agent) || dev} finished development · handoff saved`, tone: "ok", icon: "code" };
    case "tests_started": return { text: `Tests started (${plural(event.commands ?? 0, "command")})`, tone: "accent", icon: "flask" };
    case "tests_completed":
      if (statuses.includes("timeout")) return { text: "Tests timed out", tone: "warn", icon: "flask" };
      if (statuses.includes("failed")) return { text: "Tests failed", tone: "danger", icon: "flask" };
      if (statuses.length && statuses.every((s) => s === "skipped")) return { text: "Tests skipped (disabled for this project)", tone: "neutral", icon: "flask" };
      // Passed only when the event says so for every command: an empty, unknown or shortened list proves nothing.
      if (statuses.length && statuses.every((s) => s === "passed") && !(event.truncated && typeof event.truncated === "object" && "statuses" in event.truncated))
        return { text: "Tests passed", tone: "ok", icon: "flask" };
      return { text: "Tests completed", tone: "neutral", icon: "flask", detail: "This event does not say how every command ended: the Tests tab has the results" };
    case "review_started": return { text: `${rev} started the review`, tone: "accent", icon: "eye", detail: configured(event) || null };
    case "review_completed": {
      const verdict = VERDICTS[event.verdict];
      const reported = VERDICTS[event.reported];
      return { text: `${rev}: ${verdict ? verdict.label.toLowerCase() : event.verdict}`, tone: verdict?.tone || "neutral", icon: "eye",
               detail: event.reported ? `It answered “${reported ? reported.label.toLowerCase() : event.reported}”; settled from what it said about each criterion` : null };
    }
    case "acceptance_planned":
      return { text: `Criteria recorded for this task (${event.criteria ?? 0})`, tone: "neutral", icon: "tasks",
               detail: event.source === "task.md" ? "Read back from the task description; nothing was taken as verified" : null };
    case "code_approved":
      return { text: "Code approved", tone: "ok", icon: "check", detail: event.open ? `${plural(event.open, "required criterion", "required criteria")} still to verify` : null };
    case "evidence_recorded": {
      const [word, tone] = { passed: ["it passed", "ok"], failed: ["it did not pass", "danger"] }[event.result] || ["its result is not in this event", "neutral"];
      return { text: `You recorded a check for ${event.criterion}: ${word}`, tone, icon: "user", detail: event.evidence || null };
    }
    case "authorization_decided":
      return { text: `${event.decision === "granted" ? "You authorized" : "You did not authorize"} the operation of ${event.criterion}`,
               tone: event.decision === "granted" ? "ok" : "neutral", icon: "shield", detail: "Nothing was run" };
    case "evidence_invalidated":
      return { text: `${plural(event.records ?? 0, "evidence record")} no longer ${event.records === 1 ? "counts" : "count"}`, tone: "warn", icon: "repeat",
               detail: "The files of the task, or a file the evidence named, changed after it was recorded" };
    case "evidence_ledger_restarted":
      return { text: "The evidence ledger failed its integrity check and was started again", tone: "danger", icon: "alert",
               detail: "The old file is kept beside it; what it verified has to be verified again" };
    case "validation_failed":
      return { text: `A check by you did not pass (${(event.criteria || []).join(", ")}): sent to ${dev} as a change request`, tone: "warn", icon: "repeat" };
    case "task_completed": return { text: "Task completed", tone: "ok", icon: "check" };
    case "task_paused": return { text: `Paused: ${PAUSE_REASONS[event.reason] || humanize(event.reason).toLowerCase()}`, tone: "warn", icon: "pause" };
    case "task_blocked": return { text: `Blocked: ${(ERRORS[event.reason]?.title || humanize(event.reason)).toLowerCase()}`, tone: "danger", icon: "x" };
    case "environment_repaired": {
      const [word, tone] = REPAIR_OUTCOMES[event.outcome] || [humanize(event.outcome), "neutral"];
      return { text: `${word}: ${REPAIRS[event.repair] || humanize(event.repair).toLowerCase()}`, tone, icon: "shield", detail: "No agent was called for this" };
    }
    case "environment_blocked":
      return { text: `Preparation stopped: ${PAUSE_REASONS[event.kind] || humanize(event.kind).toLowerCase()}`, tone: "warn", icon: "shield", detail: "Before any agent was called" };
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

// The lists an event was saved with only in part (`truncated`: path → whole length). Never shown as if they were whole.
export function shortened(event) {
  const cut = event.truncated && typeof event.truncated === "object" ? Object.entries(event.truncated) : [];
  return cut.length ? `Saved in part in this event: ${cut.map(([path, length]) => `${path} (${length} in all)`).join(", ")}. `
    + "The task's findings, evidence and test results keep all of them." : null;
}

export function eventTime(event) {
  return clock(event.at);
}

// --- Acceptance criteria -------------------------------------------------------------
// Statuses, reasons and available actions come from the backend; this is only how they are worded.

export const CRITERION_STATUS = {
  PENDING: { label: "Not verified yet", tone: "neutral", icon: "clock" },
  IN_PROGRESS: { label: "In progress", tone: "accent", icon: "code" },
  VERIFIED: { label: "Verified", tone: "ok", icon: "check" },
  FAILED: { label: "Failed", tone: "danger", icon: "x" },
  BLOCKED_CAPABILITY: { label: "Blocked · something is missing", tone: "warn", icon: "lock" },
  AWAITING_AUTHORIZATION: { label: "Needs your authorization", tone: "warn", icon: "shield" },
  AWAITING_MANUAL_VALIDATION: { label: "Needs a check by you", tone: "warn", icon: "user" },
  UNKNOWN: { label: "Unclear what it needs", tone: "neutral", icon: "info" },
};
export const CATEGORIES = { CODE: "Code", TEST: "Tests", BUILD: "Build", INTEGRATION: "Integration", EXTERNAL: "External", PUBLISHING: "Publishing", MANUAL: "Manual" };
export const FEASIBILITY = {
  automatable: { label: "Automatic", tone: "ok" }, needs_tools: { label: "A tool or a command is missing", tone: "warn" },
  needs_authorization: { label: "Needs your authorization", tone: "warn" }, manual: { label: "Needs a check by you", tone: "accent" },
  unknown: { label: "Unclear", tone: "neutral" },
};
export const CODE_STATUS = {
  PENDING: { label: "Not approved yet", tone: "neutral" }, CHANGES_REQUIRED: { label: "Changes requested", tone: "warn" },
  APPROVED: { label: "Approved", tone: "ok" },
};
export const OUTCOMES = {
  IN_PROGRESS: { label: "In progress", tone: "neutral" }, AWAITING_VALIDATION: { label: "Waiting for verification", tone: "warn" },
  AWAITING_AUTHORIZATION: { label: "Waiting for your authorization", tone: "warn" },
  RELEASE_READY: { label: "Every required criterion is verified", tone: "ok" },
};
export const EVIDENCE_KINDS = {
  command: "Command run by PatchRondo", review: "Review of the code", manual: "Check by you", authorization: "Your decision",
  invalidation: "Earlier evidence stopped counting", restart: "Ledger started again",
};
export const EVIDENCE_RESULTS = {
  passed: ["Passed", "ok"], failed: ["Failed", "danger"], timeout: ["Timed out", "warn"], approved: ["Approved", "ok"],
  changes_requested: ["Changes requested", "warn"], blocked: ["Blocked", "danger"], granted: ["Authorized", "ok"],
  denied: ["Not authorized", "neutral"], invalidated: ["Invalidated", "neutral"], restarted: ["Restarted", "warn"],
};
export const CAPABILITY_STATES = {
  verified: ["Observed", "ok"], declared: ["Configured", "accent"], absent: ["Not available", "neutral"], unknown: ["Unknown", "warn"],
};
export const REVIEW_OUTCOMES = {
  SATISFIED: "satisfied", CODE_CHANGES_REQUIRED: "the code has to change", VALIDATION_REQUIRED: "a check is missing",
  CAPABILITY_BLOCKED: "nobody here has the tools to check it", APPROVAL_REQUIRED: "an operation has to be authorized",
  MANUAL_ACTION_REQUIRED: "a person has to do something", NOT_ASSESSED: "not assessed",
};

export function executorName(name, task) {
  return { developer: `${agentName(task?.developer)} · developer`, reviewer: `${agentName(task?.reviewer)} · reviewer`,
           runtime: "PatchRondo", user: "You" }[name] || humanize(name);
}
