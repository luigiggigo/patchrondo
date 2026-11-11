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
export const TEST_TONE = { passed: "ok", failed: "danger", timeout: "warn", error: "warn", blocked: "warn", skipped: "neutral" };

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
  completion_refused: { title: "Every criterion is verified, and completion is still refused", help: "The completion gate found a reason not to finish the task: a switch that turns off what the configured checks rely on, or evidence that changed after it was judged. The message says which; no agent was called. Resume evaluates it again." },
  acceptance_unreadable: { title: "The saved criteria cannot be read", help: "They were saved by another version of PatchRondo, or changed by hand. Nothing replaced them and no agent was called. Open the task with the version that saved them, or put back its state.json, then resume." },
  attempt_uncertain: { title: "An agent call may still be running", help: "A run stopped before it could record how an agent call ended, or the call was stopped while what it started may still run. No other call starts meanwhile. The message says what PatchRondo could and could not show. Resume once its processes have ended: PatchRondo checks again. Otherwise check that no process of it is left, then run the command the message gives (patchrondo release-attempt … --confirm-stopped) and resume. Releasing says nothing about what the call did." },
  attempt_reconciled: { title: "An agent call was found ended after its run was lost", help: "PatchRondo showed that the call and everything it started have ended, and recorded it as ended. How it ended was not recorded, so its outcome stays unknown: its charges stay, its files are kept and not used as an answer, and no automatic continuation follows it. Check the task's files, then resume to start a new call of the step if its budgets admit one." },
  process_tree_unconfirmed: { title: "An agent left processes running", help: "The CLI exited while processes it started were still running, and stopping them could not be confirmed. Its answer was not used. Check that none of them is left; the next Resume checks again and holds the task while one may still run." },
  resume_unconfirmed: { title: "A resumed session was not confirmed", help: "The call was started to go on in the session of the call before it, and its CLI did not report that same session in its own output. Its answer was not used: it may have been a new conversation. Nothing else was started. Resume to start a new call of the step, if its budgets admit one." },
  budget_exhausted: { title: "A budget allows no further agent call", help: "The budget of this step or of the task, frozen when it started, cannot take the next call; the message names the limit and its unit. Nothing was started, nothing raised the budget, and the calls and evidence so far are kept. This version cannot amend a budget: create a follow-up task to go on." },
  execution_record_missing: { title: "The record of agent calls is missing", help: "This task made recorded agent calls, but its saved state no longer has their record, so it is not run as if it never had one: that would lift its budget. Nothing was started. Put back the task's state.json, or create a follow-up task." },
  execution_policy_missing: { title: "The execution policy of this task is not known", help: "This task is governed by Adaptive Execution until it ends, and it was recorded before tasks kept their own policy. The project's execution section is now off, so its next step has no limits to run with. Nothing was started. Turn the section back on for this project, or create a follow-up task." },
  attempt_folder_exists: { title: "The folder of an agent call already exists", help: "No record of this task names that folder, so nothing was written into it and no agent was called. Check what made it and move it away, then resume: the next call gets a folder of its own." },
  attempt_limit_reached: { title: "No more agent calls can be recorded", help: "This task reached the most agent calls PatchRondo records for one step or one task. Create a follow-up task." },
  execution_unreadable: { title: "The saved record of agent calls cannot be read", help: "It was saved by another version of PatchRondo, or changed by hand. Nothing replaced it and no agent was called. Open the task with the version that saved it, or put back its state.json, then resume." },
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
  budget_exhausted: "A budget of the step or the task could not take another call, so no retry was planned",
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
  acceptance_unreadable: "the saved criteria cannot be read", completion_refused: "the completion gate refused",
  verification_error: "a check gave no usable result", verification_blocked: "a check could not be started",
  attempt_uncertain: "an agent call may still be running", attempt_folder_exists: "the folder of an agent call already exists",
  attempt_reconciled: "an agent call was found ended after its run was lost", process_tree_unconfirmed: "an agent left processes running",
  attempt_limit_reached: "no more agent calls can be recorded", execution_unreadable: "the record of agent calls cannot be read",
  budget_exhausted: "a budget allows no further agent call", resume_unconfirmed: "a resumed session was not confirmed",
  execution_record_missing: "the record of agent calls is missing", execution_policy_missing: "the execution policy of the task is not known",
};
// How a recorded agent call ended (`execution.AttemptOutcome`), and whether what it started is known to have stopped.
const CALL_OUTCOMES = {
  COMPLETED: ["answered", "ok"], TURN_LIMIT_REACHED: ["ran out of turns", "warn"], QUOTA_EXHAUSTED: ["usage limit", "warn"],
  TIMEOUT: ["timed out", "warn"], INTERRUPTED: ["interrupted", "warn"], PERMISSION_DENIED: ["access refused", "warn"],
  ENVIRONMENT_BLOCKED: ["not started", "warn"], INVALID_FINAL_OUTPUT: ["no usable answer", "warn"],
  PROVIDER_ERROR: ["the CLI failed", "warn"], UNKNOWN: ["ended in an unknown way", "neutral"],
  UNSUPPORTED_RESUME: ["did not confirm the resumed session", "warn"],
};
const TERMINATIONS = {
  exited: null, stopped: "Stopped with everything it started", not_started: "No process was started",
  unverified: "Stopped; what it started may still be running", released: "You stated that its processes stopped",
  reconciled: "Found ended with everything it started, after its run was lost; what it did is unknown",
};
// Why no automatic continuation followed a call (`execution.Declined`), as the engine recorded it.
const NOT_CONTINUED = {
  not_the_developer: "only the developer's calls are continued", test_request: "a request for tests is not continued",
  not_settled: "the call did not end by itself", released_attempt: "a call of this step was released; what it did is unknown",
  limit_reached: "the step already made its automatic continuation", budget: "the budgets have too little left for another call",
  process_tree_unverified: "nothing showed that everything the call started has stopped",
  brief_unavailable: "no checkpoint brief could be prepared",
  workspace_changed: "the files changed during or after the review call, or could not be compared",
  reconciled_attempt: "a call of this step was found ended after its run was lost; what it did is unknown",
  descendants_left: "the CLI left processes running when it exited (they were stopped)",
  windows_not_validated: "on native Windows a real CLI is not continued until it is validated with real providers",
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
    case "test_draft_started": return { text: `${dev} started writing tests, as you asked`, tone: "accent", icon: "flask", detail: configured(event) || null };
    case "test_draft_completed":
      return { text: `${dev} wrote tests · ${plural(event.files ?? 0, "file")} changed, ${plural(event.commands ?? 0, "command")} proposed`, tone: "ok", icon: "flask",
        detail: "Proposed, not run: tests stay disabled until you allow them" };
    case "test_draft_failed": return { text: `${dev} did not write tests`, tone: "warn", icon: "flask", detail: humanize(event.kind || "") || null };
    case "tests_started": return { text: `Tests started (${plural(event.commands ?? 0, "command")})`, tone: "accent", icon: "flask" };
    case "tests_completed":
      if (statuses.includes("timeout")) return { text: "Tests timed out", tone: "warn", icon: "flask" };
      if (statuses.includes("blocked")) return { text: "A check could not be started", tone: "warn", icon: "flask", detail: "Nothing was learned about the code" };
      if (statuses.includes("error")) return { text: "A check gave no usable result", tone: "warn", icon: "flask", detail: "Nothing was learned about the code" };
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
    case "continuation_started":
      return { text: event.phase === "review"
                 ? `${rev} reviews again automatically: call ${event.attempt || ""} after ${event.after || "the previous call"}, whose answer could not be used`
                 : `${dev} continues the step automatically: call ${event.attempt || ""} after ${event.after || "the previous call"}`, tone: "accent", icon: "repeat",
               detail: [event.turn_limit ? `up to ${event.turn_limit} turns` : null, event.timeout_seconds ? `timeout ${event.timeout_seconds} s` : null,
                        event.mode === "native_resume" ? "in the same session of its CLI, with the step's settings"
                          : event.phase === "review" ? "a new, independent and read-only review" : "with a brief of the work so far",
                        event.resume && event.resume.selected === "checkpoint_resume"
                          ? `not resumed natively: ${humanize(event.resume.reason || "").toLowerCase()}` : null,
                        "not a new iteration"].filter(Boolean).join(" · ") };
    case "continuation_declined":
      return { text: `Not continued automatically after call ${event.after || ""}`, tone: "neutral", icon: "pause",
               detail: NOT_CONTINUED[event.reason] || humanize(event.reason || "") || null };
    case "attempt_started":
      return { text: `${event.phase === "review" ? rev : dev}: ${event.purpose === "test_draft" ? "request for tests" : event.continuation ? "continuation call" : "call"} ${event.attempt || ""} started`, tone: "accent", icon: "play",
               detail: [event.turn_limit ? `up to ${event.turn_limit} turns` : null, event.timeout_seconds ? `timeout ${event.timeout_seconds} s` : null].filter(Boolean).join(" · ") || null };
    case "attempt_ended": {
      const [word, tone] = CALL_OUTCOMES[event.outcome] || [humanize(event.outcome || "ended"), "neutral"];
      return { text: `Call ${event.attempt || ""} ${word}`, tone, icon: "info", detail: TERMINATIONS[event.termination] || null };
    }
    case "attempt_released":
      return { text: `You released call ${event.attempt || ""}`, tone: "neutral", icon: "user", detail: "You stated that its processes stopped; nothing was run, and what it did stays unknown" };
    case "attempt_reconciled":
      return { text: `Call ${event.attempt || ""} found ended after its run was lost`, tone: "neutral", icon: "info", detail: "Its processes were shown gone; nothing was run, and what it did stays unknown" };
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
  passed: ["Passed", "ok"], failed: ["Failed", "danger"], timeout: ["Timed out", "warn"], error: ["No usable result", "warn"],
  approved: ["Approved", "ok"],
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

// The acceptance gate's decision, its next action and its reasons, in words. The codes come from the backend
// (`gate.py`, `acceptance.Basis`) and are always shown beside these words; nothing is decided here.
export const GATE_DECISIONS = {
  PASS: { label: "Can be completed", tone: "ok", icon: "check", mood: "done", help: "Every condition for completing the task holds." },
  FAIL: { label: "Changes needed", tone: "danger", icon: "x", mood: "attention", help: "The review asked for a change to the code, or a required check failed." },
  NEEDS_REVERIFICATION: { label: "Verification to repeat", tone: "warn", icon: "repeat", mood: "waiting", help: "Evidence the task relied on no longer counts for the files and checks as they are now." },
  PENDING: { label: "Work in progress", tone: "accent", icon: "clock", mood: "working", help: "The workflow still has work its agents and checks can do." },
  BLOCKED: { label: "Needs you", tone: "warn", icon: "user", mood: "attention", help: "What is left needs you: a decision, something missing, or a check by a person." },
  ERROR: { label: "Cannot conclude", tone: "danger", icon: "alert", mood: "error", help: "The evidence recorded for this task does not hold together: nothing can be concluded from it until it is repaired." },
};
export const GATE_ACTIONS = {
  COMPLETE: "Complete the task", DEVELOP: "The developer changes the code", RUN_CHECKS: "PatchRondo runs the project's checks again",
  REVIEW: "The reviewer reads the code again", CONTINUE: "The workflow continues", WAIT_FOR_AUTHORIZATION: "Your decision about an operation",
  PROVIDE_CAPABILITY: "Something missing has to be provided", WAIT_FOR_USER_VALIDATION: "A check by you",
  WAIT_FOR_EXTERNAL_VERIFICATION: "A check outside the project, by you", RESTORE_CONFIGURATION: "A switched-off protection has to be put back",
  REPAIR_EVIDENCE: "The evidence ledger has to be repaired",
};
export const GATE_REASONS = {
  LEDGER_PROBLEM: "The evidence ledger failed its integrity check",
  EVIDENCE_INCONSISTENT: "A criterion counted as verified names a record that does not count",
  CODE_CHANGES_REQUIRED: "The review asked for a change to the code",
  CODE_NOT_APPROVED: "The code is not approved yet",
  APPROVAL_NO_LONGER_COUNTS: "The approval of the code is about other files than these",
  TESTS_NOT_CURRENT: "The test results are about other files, checks or inputs",
  FILES_NOT_CHECKED: "The files have not been compared with the test results yet",
  EVIDENCE_STALE: "Evidence it relied on became stale: what it was about changed",
  EVIDENCE_INVALID: "Evidence it relied on is invalid: its integrity cannot be confirmed",
  EVIDENCE_UNKNOWN: "Evidence it relied on cannot be judged: what is needed to judge it is missing",
  VERIFICATION_DISABLED: "PATCHRONDO_VERIFICATION=0 turns off the configured checks",
  PROVENANCE_DISABLED: "PATCHRONDO_PROVENANCE=0 turns off what the checks declare",
  VERIFIED: "Verified", WAITING_FOR_DEPENDENCY: "Waits for another criterion", IN_DEVELOPMENT: "Being developed",
  CODE_CHANGE_REQUIRED: "The reviewer asked for a change to the code", CHECK_FAILED: "A project check failed",
  USER_CHECK_FAILED: "Your check did not pass", CHECK_UNSETTLED: "A check gave no usable result",
  AUTHORIZATION_DENIED: "You did not authorize the operation", AUTHORIZATION_REQUIRED: "Needs your authorization",
  CHECKS_NOT_ALLOWED: "The project's checks are configured, without your consent", NO_TEST_COMMANDS: "No test command is configured",
  TOOL_MISSING: "A tool it needs is not installed", NO_COMMAND: "No allowed command does it",
  REVIEWER_CAPABILITY_BLOCKED: "The reviewer cannot check it with its tools", UNCLASSIFIED: "Unclear what it needs: you decide",
  HANDED_OVER_BY_REVIEW: "The reviewer could not settle it by reading the code", MANUAL_VALIDATION_REQUIRED: "Needs a check by you",
  EXTERNAL_VERIFICATION_REQUIRED: "Needs a check outside the project", AWAITING_REVIEW: "Waits for the review",
  AWAITING_CHECKS: "Waits for the project's checks",
};
export const VALIDITY = {
  VALID: { label: "Valid", tone: "ok", help: "It counts for the task as it is now." },
  STALE: { label: "Stale", tone: "warn", help: "True when it was recorded; what it was about changed since. It no longer counts." },
  INVALID: { label: "Invalid", tone: "danger", help: "Its integrity cannot be confirmed. It does not count." },
  UNKNOWN: { label: "Unknown", tone: "neutral", help: "What is needed to judge it is missing. It does not count." },
};

// What a record says it observed, and whether that still counts: never one without the other.
export function evidenceWords(row) {
  const [result] = EVIDENCE_RESULTS[row.result] || [row.result || "Recorded"];
  if (row.validity === "VALID") return `${result} · counts now`;
  if (["passed", "approved", "granted"].includes(row.result)) return `${result} earlier · no longer valid for the current state`;
  return `${result} · does not count now`;
}

export function executorName(name, task) {
  return { developer: `${agentName(task?.developer)} · developer`, reviewer: `${agentName(task?.reviewer)} · reviewer`,
           runtime: "PatchRondo", user: "You" }[name] || humanize(name);
}
