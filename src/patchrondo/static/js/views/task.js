// Task workspace: header, workflow, criteria, live activity, timeline, logs, tests, review, changed files and documents.
// The header follows the live summary; the panels follow the detail fetched after each change.

import { api } from "../api.js";
import { agentRoles, eventRow, workflowBar } from "../components.js";
import { clear, h, icon, memo, sync } from "../dom.js";
import { ago, bytes, clock, duration, fullDate, humanize, markdown, plural } from "../format.js";
import { href, navigate, replace } from "../router.js";
import { emptyState, rondoNote, taskMessage } from "../rondo.js";
import { getTask, state } from "../store.js";
import { CRITERION_STATUS, OUTCOMES, RECOVERY_SOURCES, SEVERITY, TEST_TONE, VERDICTS, activityOf, agentName, configured, errorOf, preparation,
  stopReason } from "../tasks.js";
import { activityBadge, badge, busy, button, callout, confirm, copyButton, dialog, iconButton, loadingBlock, menuItem, popover, tabs,
  toast, toastError, toggle } from "../ui.js";
import { criteriaPanel } from "./criteria.js";
import { livePanel } from "./live.js";

const TABS = [
  ["overview", "Overview", "home"], ["criteria", "Criteria and checks", "tasks"], ["live", "Live Activity", "bolt"], ["activity", "Timeline", "activity"], ["logs", "Logs", "terminal"],
  ["tests", "Tests", "flask"],
  ["review", "Review", "eye"], ["files", "Files changed", "diff"], ["handoff", "Handoff", "file"], ["report", "Report", "book"],
  ["raw", "Advanced", "code"],
];
const LOG_CHUNK = 200000;
const ANSI = /\u001b\[[0-9;?]*[ -/]*[@-~]/g;

// --- Run, stop and unlock ------------------------------------------------------------

function runDialog(task, detail) {
  const plan = detail?.state?.recovery;
  const effective = detail?.effective;
  const resume = task.status !== "ready";
  const waiting = task.runtime.waiting_process;
  // A recorded waiting process that cannot be verified may still be there: the backend allows one manual run only.
  const once = waiting || task.runtime.waiting_unverified;
  const dev = agentName(task.developer), rev = agentName(task.reviewer);
  const providers = state.providers?.providers || {};
  let recovery = once ? false : Boolean(effective?.recovery_enabled);
  let touched = once;
  const hint = h("div", { class: "field-hint" });
  const describe = () => {
    if (waiting) hint.textContent = "A process is already waiting for the planned retry. This runs once now instead and cancels that plan; the waiting process then stops by itself.";
    else if (once) hint.textContent = "A waiting process was recorded for this task and cannot be verified here, so a second automatic run is refused. This runs once now and cancels the saved plan; if that process is still waiting, it then stops by itself.";
    else if (recovery && plan?.status === "scheduled") hint.textContent = `On: this run waits until ${fullDate(plan.resume_at)}, the saved retry time, before it calls ${agentName(plan.provider)}.`;
    else if (recovery) hint.textContent = "On: after a usage limit the run process stays alive, waits for the reset or a backoff and retries, within the limits in Settings → Recovery. It only waits; it never works around a limit.";
    else if (plan?.status === "scheduled") hint.textContent = "Off: runs once now. The saved retry plan is cancelled.";
    else hint.textContent = "Off: a usage limit pauses the task until you resume it.";
  };
  describe();
  const missing = [...new Set([task.developer, task.reviewer])].map((name) => providers[name]).filter((p) => p && (p.installed === false || p.authenticated === false));
  return dialog({
    title: `${resume ? "Resume" : "Run"} “${task.title}”?`,
    build: (close) => {
      const go = button(resume ? "Resume task" : "Run task", { variant: "primary", iconName: "play", onClick: () => busy(go, async () => {
        try {
          const result = await api.post(`/api/projects/${task.project}/tasks/${task.id}/run`, { auto_resume: touched ? recovery : null });
          close(true);
          toast(result.finished ? "The run finished at once; see the result below." : `${resume ? "Resumed" : "Started"}. Progress appears here as it happens.`);
        } catch (error) {
          close(false);
          toastError(error, "Not started: ");
        }
      }) });
      return {
        body: [
          // At Validate a run finishes the task or leaves it as it is; an agent is called only for files that changed.
          task.phase === "validate"
            ? h("p", {}, "The code of this task is approved and it waits for its criteria. Resuming completes it when every required criterion is verified, and otherwise changes nothing. ",
              h("strong", {}, `If the files or the project's commands changed since the approval, the tests run again and ${rev} reviews again, which uses your plan quota.`))
            : h("p", {}, `${dev} develops and ${rev} reviews in the task's own worktree. `, h("strong", {}, "This calls the real CLIs and uses your plan quota.")),
          effective && !effective.tests_enabled ? callout({ tone: "warn", iconName: "flask", title: "Project tests are disabled",
            body: "The task will pause after development, before the review: a task is done only when tests pass and the review approves." }) : null,
          missing.map((provider) => callout({ tone: "warn", iconName: "alert", title: `${provider.label} is ${provider.installed === false ? "not installed" : "not logged in"}`, body: provider.hint })),
          h("div", { class: "card" }, h("label", { class: "setting" },
            h("div", { class: "setting-text" }, h("div", { class: "field-label" }, "Automatic quota recovery"), hint),
            toggle({ checked: recovery, disabled: once, label: "Automatic quota recovery", onChange: (value) => { recovery = value; touched = true; describe(); } }))),
        ],
        actions: [button("Cancel", { variant: "ghost", onClick: () => close(false) }), go],
      };
    },
  });
}

async function stopRun(task) {
  const waiting = task.runtime.activity === "waiting_retry";
  const ok = await confirm({
    title: waiting ? "Stop waiting for the retry?" : "Stop this run?", confirmLabel: waiting ? "Stop waiting" : "Stop run", variant: "danger", iconName: "stop",
    body: h("p", {}, waiting
      ? "The waiting process is interrupted. The retry plan stays saved, but nothing will retry by itself until you resume."
      : "The run is interrupted like Ctrl+C in its terminal: the current agent or test command is stopped and the task pauses at its last checkpoint. Work the agent already wrote stays in the worktree."),
  });
  if (!ok) return;
  try {
    await api.post(`/api/projects/${task.project}/tasks/${task.id}/stop`, {});
    toast("Stop requested. The task pauses as soon as its process reacts.");
  } catch (error) { toastError(error); }
}

async function unlockRun(task) {
  const ok = await confirm({
    title: "Release the lock and resume?", confirmLabel: "Release lock and resume", variant: "danger", iconName: "lock",
    body: [
      h("p", {}, "The last run ended abruptly and left its lock. PatchRondo found no live process for it."),
      h("p", {}, "Resuming re-checks every recorded process first and refuses if an agent or test command is still running. ",
        h("strong", {}, "This calls the real CLIs and uses your plan quota.")),
    ],
  });
  if (!ok) return;
  try {
    await api.post(`/api/projects/${task.project}/tasks/${task.id}/run`, { unlock: true });
    toast("Lock released; the task resumed.");
  } catch (error) { toastError(error, "Not resumed: "); }
}

function actions(task, detail) {
  const runtime = task.runtime;
  const items = [];
  if (runtime.can_stop) items.push(button(runtime.activity === "waiting_retry" ? "Stop waiting" : "Stop", { iconName: "stop", onClick: () => stopRun(task) }));
  else if (runtime.active && !state.about?.platform?.stop_supported) {
    items.push(button("Stop", { iconName: "stop", disabled: true, tip: "Not available on native Windows: press Ctrl+C in the run's terminal, or let it reach its next pause" }));
  }
  if (runtime.can_unlock) items.push(button("Release lock and resume", { variant: "primary", iconName: "lock", onClick: () => unlockRun(task) }));
  else if (runtime.can_start) {
    const once = runtime.waiting_process || runtime.waiting_unverified;
    const label = once ? "Run now" : task.status === "ready" ? "Run" : "Resume";
    items.push(button(label, { variant: once ? "" : "primary", iconName: "play", onClick: () => runDialog(task, detail) }));
  }
  const more = iconButton("dots", "More actions", () => popover(more, (close) => [
    menuItem("Copy task ID", { iconName: "copy", onClick: () => { close(); navigator.clipboard?.writeText(task.id).then(() => toast("Task ID copied")); } }),
    detail?.state?.worktree ? menuItem("Copy worktree path", { iconName: "folder", onClick: () => { close(); navigator.clipboard?.writeText(detail.state.worktree).then(() => toast("Worktree path copied")); } }) : null,
    menuItem("Project settings", { iconName: "settings", onClick: () => { close(); navigate("settings", task.project, "general"); } }),
  ], { align: "end" }), { "aria-haspopup": "menu" });
  items.push(more);
  return items;
}

// --- Guided fixes --------------------------------------------------------------------

const CHECK_TONE = { passed: "ok", failed: "danger", skipped: "neutral" };

// Everything the environment check recorded, for people who want to see it. Closed by default.
function technicalDetails(record) {
  const checks = (record.checks || []).filter((check) => check && typeof check === "object");
  const repairs = (record.repairs || []).filter((entry) => entry && typeof entry === "object");
  return h("details", { class: "technical" },
    h("summary", {}, "Technical details"),
    h("dl", { class: "kv tight" },
      record.error_kind ? [h("dt", {}, "Problem code"), h("dd", { class: "mono" }, record.error_kind)] : null,
      record.cause ? [h("dt", {}, "Cause"), h("dd", { class: "mono" }, record.cause)] : null,
      h("dt", {}, "Checked"), h("dd", {}, `${fullDate(record.checked_at)}${record.live ? "" : " · provider commands simulated"}`)),
    checks.length ? h("div", { class: "check-list" }, checks.map((check) => h("div", { class: "check-row" },
      badge(check.status, CHECK_TONE[check.status] || "neutral", { dot: true }),
      h("div", { class: "grow" }, h("div", {}, check.label || check.id), check.detail ? h("div", { class: "mono muted break" }, check.detail) : null),
      h("span", { class: "chip mono" }, check.id)))) : null,
    repairs.length ? [h("div", { class: "field-label" }, "Changes PatchRondo made"), h("div", { class: "check-list" }, repairs.map((entry) => h("div", { class: "check-row" },
      badge(humanize(entry.outcome), entry.outcome === "applied" ? "ok" : entry.outcome === "unchanged" ? "neutral" : "warn"),
      h("div", { class: "grow" }, h("div", {}, `${humanize(entry.repair)} · ${fullDate(entry.at)}`),
        (entry.changed || []).map((change) => h("div", { class: "mono muted break" }, `${change.path}: ${change.added}`)),
        entry.error ? h("div", { class: "mono muted break" }, entry.error) : null))))] : null);
}

async function repair(el, task, record) {
  const trust = record.repair === "git_trust";
  if (trust) {
    const ok = await confirm({
      title: "Trust this project folder?", confirmLabel: "Trust this folder and continue", iconName: "shield",
      body: [
        h("p", {}, "Git refuses to work in a folder that belongs to another user of this computer, to protect you from projects you did not create."),
        h("p", {}, "If this project is yours, PatchRondo can add this project folder and the task's copy of it to the folders Git trusts for your user. ",
          h("strong", {}, "No other folder is added, and nothing is run as administrator.")),
      ],
    });
    if (!ok) return;
  }
  await busy(el, async () => {
    try {
      const result = await api.post(`/api/projects/${task.project}/tasks/${task.id}/repair`, { confirm: trust ? ["git_trust"] : [], resume: true });
      if (result.ready && result.run) toast("Fixed and checked again. The task is running.");
      else if (result.ready) toast(`Fixed and checked again. ${result.run_error || "Resume when you are ready."}`, { tone: result.run_error ? "warn" : "ok" });
      else toast("The fix did not work. The technical details show what was tried.", { tone: "warn" });
    } catch (error) { toastError(error, "Not repaired: "); }
  });
}

// A problem found while preparing: what it means, and the one action that addresses it.
function environmentCard(task, record, error) {
  const guided = record.repair_available && task.runtime.can_start;
  const go = guided ? button(record.repair === "git_trust" ? "Trust this folder and continue" : "Repair automatically",
    { variant: "primary", iconName: "shield", onClick: () => repair(go, task, record) }) : null;
  const provider = state.providers?.providers?.[record.provider];
  const command = record.error_kind === "authentication" ? provider?.login_command : null;
  return callout({ tone: "warn", iconName: "shield", title: error.title,
    body: [h("div", {}, error.help),
      record.error_kind === "cli_missing" && provider?.hint ? h("div", {}, provider.hint) : null,
      command ? h("div", { class: "row wrap" }, "Run this in a terminal, then resume:", h("span", { class: "chip mono" }, command), copyButton(command, "Copy")) : null,
      record.repair_applied && !guided ? h("div", { class: "muted" }, "PatchRondo already tried its own fix and checked again.") : null,
      h("div", { class: "muted" }, "No agent was called and no quota was used for this check."),
      technicalDetails(record)],
    actions: go ? [go] : null });
}

const SETTING_LEVELS = { global: "global preferences", project: "project", task: "this task" };

// A step keeps the settings it started with. When the settings of now differ, the choice to switch is the user's.
function agentNotes(task, detail) {
  const info = detail.agents;
  if (!info) return null;
  if (info.error) {
    return callout({ tone: "danger", iconName: "alert", title: "The agent settings cannot be used", body: info.error,
      actions: [button("Open Agents", { size: "sm", href: href("agents", task.project, task.id) })] });
  }
  const [role, item] = Object.entries(info.roles).find(([, entry]) => entry.differs) || [];
  if (!role) return null;
  const use = button("Use the current settings", { size: "sm", iconName: "refresh", disabled: !task.runtime.can_start,
    tip: task.runtime.can_start ? "The step will start again with the settings of now" : "Available when the task is not running",
    onClick: () => busy(use, async () => {
      try { await api.post(`/api/projects/${task.project}/tasks/${task.id}/agents/refresh`, {}); toast("The step will start with the current settings"); }
      catch (error) { toastError(error); }
    }) });
  return callout({ tone: "accent", iconName: "user", title: `The ${role} step keeps the settings it started with`,
    body: [h("div", {}, `It started with ${configured(item.frozen.values) || "the CLI's default model"}; the settings of now give ${configured(item.current.values) || "the CLI's default model"}. `,
      "Resuming repeats the step as it was started, so that a retry is the same call. Switch only if that is what you want."),
      h("div", { class: "muted" }, `Frozen ${ago(item.frozen.frozen_at)}. Later steps take the settings of their own start.`)],
    actions: [use, h("a", { class: "link", href: href("agents", task.project, task.id) }, "Agent settings of this task")] });
}

function agentFacts(task, detail) {
  const info = detail.agents;
  if (!info || info.error) return null;
  return Object.entries(info.roles).flatMap(([role, item]) => {
    const record = item.frozen || item.current;
    const from = [...new Set(Object.values(record?.sources || {}))].map((level) => SETTING_LEVELS[level] || level).join(", ");
    return [h("dt", {}, role === "developer" ? "Developer" : "Reviewer"),
      h("dd", {}, `${agentName(item.provider)} · ${configured(record?.values) || "CLI default model"}`,
        h("div", { class: "faint" }, item.frozen ? `Kept by the step in progress${from ? ` · from ${from}` : ""}` : `For its next step${from ? ` · from ${from}` : ""}`))];
  });
}

// Tests run code of the project on this computer: they start only after an explicit decision.
function consentCard(task, detail) {
  const project = state.projects.get(task.project);
  const known = detail.effective?.test_commands || [];
  const commands = h("textarea", { class: "input mono", rows: Math.max(2, known.length), spellcheck: "false", "aria-label": "Test commands",
    placeholder: "python -m unittest\nnpm test" });
  commands.value = known.join("\n");
  const trust = h("input", { type: "checkbox", id: `trust-${task.id}` });
  const error = h("div", { class: "form-error", role: "alert" });
  const go = button("Allow tests and resume", { variant: "primary", iconName: "flask", onClick: () => busy(go, async () => {
    const lines = commands.value.split("\n").map((line) => line.trim()).filter(Boolean);
    error.textContent = "";
    if (!lines.length) { clear(error, icon("alert", "sm"), "Enter at least one test command."); commands.focus(); return; }
    if (!trust.checked) { clear(error, icon("alert", "sm"), "Tick the box to confirm that you trust this project."); trust.focus(); return; }
    try {
      // The whole section is sent: consent is this decision, never something inherited.
      await api.patch(`/api/projects/${task.project}/config`, { tests: { enabled: true, trust_acknowledged: true, commands: lines } });
      await api.post(`/api/projects/${task.project}/tasks/${task.id}/run`, {});
      toast("Tests are allowed for this project. The task resumed.");
    } catch (failure) { clear(error, icon("alert", "sm"), failure.message); }
  }) });
  return h("section", { class: "card consent" },
    h("div", { class: "card-head" }, h("h2", {}, "Allow this project's tests?"), badge("Your decision", "warn", { iconName: "flask" })),
    h("div", { class: "card-body stack" },
      h("p", {}, "A task is done only when the project's tests pass and the review approves. Tests are commands of the project that run on this computer with your permissions, so PatchRondo never starts them by itself."),
      h("div", { class: "field" }, h("label", { class: "field-label", for: commands.id = `commands-${task.id}` }, known.length ? "These commands will run after each development step" : "Commands to run after each development step"),
        commands, h("div", { class: "field-hint" }, "One per line, run in the task's copy of the project. You can change them later in Settings → Tests.")),
      h("label", { class: "row consent-check", for: trust.id }, trust,
        h("span", {}, "I trust ", h("strong", {}, project?.name || "this project"), " and allow these commands to run on this computer.")),
      h("div", { class: "form-foot" }, error,
        h("a", { class: "link", href: href("settings", task.project, "tests") }, "Open test settings"), go),
      h("p", { class: "faint" }, "This applies to the whole project, not only to this task. Nothing runs until you press the button.")));
}

// The code is approved and the task waits for something that is not development: what, and where to act on it.
function validationCard(task, detail, error) {
  const view = detail.acceptance;
  const open = (view?.criteria || []).filter((criterion) => criterion.required && criterion.status !== "VERIFIED");
  const ready = view?.outcome === "RELEASE_READY";
  return callout({ tone: ready ? "ok" : "warn", iconName: ready ? "check" : "tasks",
    title: ready ? "Every required criterion is verified" : error.title,
    body: [h("div", {}, ready ? "Complete the task from “Criteria and checks”: no agent is called for that." : error.help),
      open.length ? h("div", { class: "check-list" }, open.slice(0, 6).map((criterion) => {
        const meta = CRITERION_STATUS[criterion.status] || { label: criterion.status, tone: "neutral" };
        return h("div", { class: "check-row" }, badge(meta.label, meta.tone, { dot: true }),
          h("div", { class: "grow" }, h("div", { class: "break" }, `${criterion.id} · ${criterion.text}`), h("div", { class: "muted break" }, criterion.reason)));
      })) : null,
      open.length > 6 ? h("div", { class: "muted" }, `and ${open.length - 6} more`) : null,
      h("div", { class: "muted" }, "The work done so far is kept as it is. No agent is called while the task waits here.")],
    actions: [button("Open criteria and checks", { size: "sm", variant: "primary", iconName: "tasks", href: href("tasks", task.project, task.id, "criteria") })] });
}

function recoveryCard(task, plan) {
  if (!plan || typeof plan !== "object") return null;
  const activity = task.runtime.activity;
  const used = plan.consecutive_failures, limit = plan.max_consecutive_retries;
  const process = task.runtime.processes.find((entry) => entry.state === "alive");
  const stateLine = {
    waiting_retry: ["wait", `A process is waiting${process ? ` (PID ${process.pid})` : ""}. It retries at the planned time for as long as it keeps running.`],
    plan_only: ["warn", "Saved plan only: no process is waiting. Nothing retries until you resume."],
    plan_unverified: ["warn", "A waiting process was recorded, but this computer cannot verify that it is alive. Until that is settled a second automatic run is refused; a run without automatic recovery stays possible."],
    recovery_stopped: ["warn", "Automatic recovery has ended. Resume by hand when the cause is resolved."],
    running: ["accent", "A planned retry is running now."],
  }[activity] || ["neutral", plan.status === "stopped" ? "Automatic recovery has ended." : "Recovery information saved with the task."];
  return h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "Quota recovery"),
      badge(activity === "waiting_retry" ? "Waiting to retry" : plan.status === "scheduled" ? "Planned, not attended" : humanize(plan.status), stateLine[0], { dot: true, live: activity === "waiting_retry" })),
    h("div", { class: "card-body stack" },
      h("p", { class: "muted" }, stateLine[1]),
      h("dl", { class: "kv" },
        h("dt", {}, "Failure"), h("dd", {}, "Usage limit (quota)"),
        h("dt", {}, "Provider"), h("dd", {}, agentName(plan.provider)),
        plan.status === "scheduled" ? [h("dt", {}, "Next attempt"), h("dd", {}, `${fullDate(plan.resume_at)} · ${ago(plan.resume_at)}`)] : null,
        plan.status === "scheduled" ? [h("dt", {}, "Chosen from"), h("dd", {}, RECOVERY_SOURCES[plan.schedule_source] || humanize(plan.schedule_source))] : null,
        task.last_error?.retry_at ? [h("dt", {}, "Provider reset"), h("dd", {}, fullDate(task.last_error.retry_at))] : null,
        Number.isInteger(used) && Number.isInteger(limit) ? [h("dt", {}, "Retries"), h("dd", {}, `${Math.min(used, limit)} of ${limit} used · ${Math.max(limit - used, 0)} left`)] : null,
        plan.first_failure_at ? [h("dt", {}, "First failure"), h("dd", {}, fullDate(plan.first_failure_at))] : null,
        plan.status === "stopped" ? [h("dt", {}, "Stopped because"), h("dd", {}, stopReason(plan.stop_reason))] : null),
      h("p", { class: "faint" }, "A saved plan is not a running service: retries happen only while a PatchRondo run process is alive.")));
}

function overviewPanel() {
  const el = h("div", { class: "task-overview" });
  return { el, update(detail, task) {
    const data = detail.state;
    const error = errorOf(data);
    const review = data.review, tests = data.tests || [];
    const verdict = review ? VERDICTS[review.verdict] : null;
    const passed = tests.filter((test) => test.status === "passed").length;
    const overrides = data.overrides;
    const prepare = preparation(task);
    const record = prepare.state === "problem" && data.environment?.error_kind === error?.kind ? data.environment : null;
    const consent = error?.kind === "tests_disabled" && task.status === "paused" && task.runtime.can_start;
    const validation = ["awaiting_validation", "awaiting_authorization"].includes(error?.kind) && task.status === "paused";
    const counts = detail.acceptance?.tracked ? detail.acceptance.summary : null;
    const outcome = counts ? OUTCOMES[detail.acceptance.outcome] : null;
    // The consent form keeps what was typed in it: it is rebuilt only when the task or its commands change.
    let form = null;
    if (consent) {
      const wanted = JSON.stringify([task.id, detail.effective?.test_commands]);
      form = el.querySelector(".consent");
      if (form?.dataset.for !== wanted) {
        form = consentCard(task, detail);
        form.dataset.for = wanted;
      }
    }
    // `stamp` changes with every fetched detail: timestamps in the state only have one-second resolution.
    const frozen = Object.values(detail.agents?.roles || {}).map((item) => (item.frozen ? ago(item.frozen.frozen_at) : ""));
    memo(el, [detail.stamp, task.runtime, detail.effective, detail.worktree_exists, ago(data.recovery?.resume_at), state.providers?.providers, frozen,
      detail.acceptance?.summary, detail.acceptance?.outcome], () => [
      h("div", { class: "stack" },
        task.runtime.activity === "stale_lock" && !task.runtime.can_unlock ? callout({ tone: "danger", iconName: "lock", title: "The lock has to be removed by hand on native Windows",
          body: ["PatchRondo found no live process for this lock, but on native Windows the engine cannot confirm that for the agent's child processes. After checking that no claude, codex or test process is still running, delete ",
            h("span", { class: "mono break" }, `${state.projects.get(task.project)?.home}/tasks/${task.id}/.run.lock`), " and resume. WSL2 does this check for you."] }) : null,
        task.runtime.unverified.length ? callout({ tone: "warn", iconName: "alert", title: "A recorded process cannot be verified",
          body: [`PID ${task.runtime.unverified.join(", ")} was recorded as attached to this task, and this computer cannot tell whether that process still exists. `,
            "PatchRondo does not assume that it is gone: while this is open the project cannot be removed, and a planned retry cannot be started a second time with automatic recovery. ",
            "If it is a PatchRondo run, stop it in its terminal. If it is another program, or it is gone, delete ",
            task.runtime.unverified.map((pid, index) => [index ? ", " : "", h("span", { class: "mono break" }, `${state.projects.get(task.project)?.home}/tasks/${task.id}/attached/${pid}.json`)]), "."] }) : null,
        record ? environmentCard(task, record, error)
          : consent ? form
          : validation ? validationCard(task, detail, error)
          : error && task.status !== "done" ? callout({ tone: task.status === "blocked" ? "danger" : "warn", iconName: "alert", title: error.title,
            body: [h("div", {}, error.help), error.message ? h("pre", { class: "error-message" }, error.message) : null] }) : null,
        !detail.worktree_exists ? callout({ tone: "danger", iconName: "folder", title: "The task worktree is missing", body: `Expected at ${data.worktree}. The task cannot run without it.` }) : null,
        detail.config_error ? callout({ tone: "danger", iconName: "alert", title: "The project configuration cannot be used", body: detail.config_error }) : null,
        task.status !== "done" && task.status !== "blocked" ? agentNotes(task, detail) : null,
        recoveryCard({ ...task, last_error: data.last_error }, data.recovery),
        h("div", { class: "grid c3" },
          h("a", { class: `card pad summary-card tone-${outcome?.tone || "neutral"}`, href: href("tasks", task.project, task.id, "criteria") },
            h("div", { class: "stat-top" }, h("span", { class: "stat-icon" }, icon("tasks")), h("span", { class: "stat-label" }, "Criteria")),
            h("div", { class: "summary-value" }, counts ? `${counts.required_verified} of ${counts.required} verified` : "Not tracked"),
            h("div", { class: "stat-hint" }, counts ? outcome?.label || "" : "Created before criteria were tracked")),
          h("a", { class: `card pad summary-card tone-${verdict?.tone || "neutral"}`, href: href("tasks", task.project, task.id, "review") },
            h("div", { class: "stat-top" }, h("span", { class: "stat-icon" }, icon("eye")), h("span", { class: "stat-label" }, "Review")),
            h("div", { class: "summary-value" }, verdict ? verdict.label : "Not reviewed yet"),
            h("div", { class: "stat-hint" }, review ? plural((review.issues || []).length, "issue") : `${agentName(task.reviewer)} reviews after the tests`)),
          h("a", { class: `card pad summary-card tone-${!tests.length ? "neutral" : tests.every((t) => t.status === "skipped") ? "neutral" : passed === tests.length ? "ok" : "danger"}`, href: href("tasks", task.project, task.id, "tests") },
            h("div", { class: "stat-top" }, h("span", { class: "stat-icon" }, icon("flask")), h("span", { class: "stat-label" }, "Tests")),
            h("div", { class: "summary-value" }, !tests.length ? "Not run yet" : tests.every((t) => t.status === "skipped") ? "Disabled" : `${passed} of ${tests.length} passed`),
            h("div", { class: "stat-hint" }, detail.effective?.tests_enabled ? plural(detail.effective.test_commands.length, "command") + " configured" : "Disabled for this project"))),
        h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Task")),
          h("div", { class: "card-body" }, detail.documents.task ? markdown(detail.documents.task) : h("p", { class: "muted" }, "The task description is missing.")))),
      h("aside", { class: "card pad facts" }, h("h2", {}, "Details"),
        h("dl", { class: "kv tight" },
          h("dt", {}, "Saved status"), h("dd", {}, `${humanize(data.status)} · ${humanize(data.phase)} phase`),
          h("dt", {}, "Process"), h("dd", {}, processLine(task)),
          h("dt", {}, "Iteration"), h("dd", {}, `${data.iteration}${detail.effective ? ` of ${detail.effective.max_iterations}` : ""}`),
          h("dt", {}, "Created"), h("dd", {}, fullDate(data.created_at)),
          h("dt", {}, "Updated"), h("dd", {}, `${fullDate(data.updated_at)}`),
          h("dt", {}, "Branch"), h("dd", { class: "mono" }, `patchrondo/${task.id}`),
          h("dt", {}, "Base commit"), h("dd", { class: "mono" }, String(data.base_sha || "").slice(0, 12)),
          h("dt", {}, "Worktree"), h("dd", { class: "mono" }, data.worktree),
          detail.effective ? [h("dt", {}, "Agent timeout"), h("dd", {}, duration(detail.effective.agent_timeout_seconds))] : null,
          detail.effective ? [h("dt", {}, "Auto recovery"), h("dd", {}, detail.effective.recovery_enabled ? "On by default" : "Off by default")] : null,
          overrides ? [h("dt", {}, "Task overrides"), h("dd", {}, Object.entries(overrides).flatMap(([section, values]) =>
            Object.entries(values).map(([key, value]) => `${humanize(key)}: ${value}`)).join(" · "))] : null,
          agentFacts(task, detail)),
        h("div", { class: "row wrap" }, copyButton(data.worktree, "Copy worktree path"),
          task.status !== "done" && task.status !== "blocked" ? button("Agent settings", { size: "sm", variant: "ghost", iconName: "user", href: href("agents", task.project, task.id),
            tip: "Model and options of this task only" }) : null)),
    ]);
  } };
}

function processLine(task) {
  const runtime = task.runtime;
  const lock = runtime.lock;
  const alive = runtime.processes.filter((entry) => entry.state === "alive");
  if (lock && lock.holder === "alive") return `Running, verified (PID ${lock.pid})`;
  if (lock && lock.holder === "dead") return `Lock left by a process that is gone${lock.pid ? ` (PID ${lock.pid})` : ""}`;
  if (lock) return alive.length ? `Running (PID ${alive[0].pid})` : `Locked; process not verifiable${lock.pid ? ` (PID ${lock.pid})` : ""}`;
  if (alive.length) return `${runtime.activity === "waiting_retry" ? "Waiting" : "Attached"}, verified (PID ${alive[0].pid})`;
  if (runtime.processes.length) return "A recorded process cannot be verified";
  return "No process attached";
}

function activityPanel() {
  let order = localStorage.getItem("patchrondo.timeline") === "newest" ? "newest" : "oldest";
  const list = h("div", { class: "timeline" });
  const head = h("div", { class: "section-head" });
  const el = h("div", {}, head, list);
  let last = null;
  const api_ = { el, update(detail, task) {
    last = [detail, task];
    const history = (detail.state.history || []).filter((event) => event && typeof event === "object");
    const latest = history[history.length - 1];
    memo(head, [history.length, order, latest?.at, ago(latest?.at)], () => [
      h("div", { class: "muted" }, history.length ? [`${plural(history.length, "event")} saved by the engine · last one `, h("span", { "data-tip": fullDate(latest.at) }, ago(latest.at))] : "No events yet"),
      history.length ? button(order === "oldest" ? "Oldest first" : "Newest first", { size: "sm", variant: "ghost", iconName: order === "oldest" ? "down" : "up",
        tip: "Change the order", onClick: () => { order = order === "oldest" ? "newest" : "oldest"; localStorage.setItem("patchrondo.timeline", order); api_.update(...last); } }) : null,
    ]);
    if (!history.length) {
      sync(list, [{ key: "empty", sig: task.runtime.activity, render: () => emptyState({ compact: true, title: "Nothing has happened yet",
        body: "Events appear here as the task runs: each one is saved by the engine with the task state." }) }]);
      return;
    }
    const items = [];
    let iteration = null;
    const ordered = order === "oldest" ? history : [...history].reverse();
    ordered.forEach((event, index) => {
      if (event.iteration !== iteration) {
        iteration = event.iteration;
        items.push({ key: `i${iteration}-${index}`, sig: iteration, render: () => h("div", { class: "timeline-iteration" }, `Iteration ${iteration}`) });
      }
      const position = order === "oldest" ? index : history.length - 1 - index;
      items.push({ key: `e${position}`, sig: [event, task.developer, task.reviewer], render: () => eventRow(event, task) });
    });
    sync(list, items);
  } };
  return api_;
}

function logsPanel(projectId, taskId) {
  let files = [], current = null, start = 0, next = 0, size = 0, follow = true, loading = false, opened = 0;
  let wanted = null; // a file another panel asked for, shown as soon as the list of files has it
  const select = h("select", { class: "input log-select", "aria-label": "Log file", onchange: () => open(select.value) });
  const info = h("span", { class: "muted num log-info" });
  const view = h("div", { class: "log-view", tabindex: "0", role: "log", "aria-label": "Log output" });
  const earlier = button("Load earlier output", { size: "sm", onClick: () => loadEarlier() });
  const followBox = toggle({ checked: true, label: "Follow new output", onChange: (value) => { follow = value; if (value) view.scrollTop = view.scrollHeight; } });
  const wrapBox = toggle({ checked: false, label: "Wrap long lines", onChange: (value) => view.classList.toggle("wrap", value) });
  const el = h("div", { class: "stack" },
    h("div", { class: "log-toolbar" }, select, info, h("span", { class: "grow" }),
      h("label", { class: "row log-option" }, followBox, h("span", {}, "Follow")),
      h("label", { class: "row log-option" }, wrapBox, h("span", {}, "Wrap")),
      iconButton("up", "Jump to the start", () => { view.scrollTop = 0; }),
      iconButton("down", "Jump to the end", () => { view.scrollTop = view.scrollHeight; }),
      // textContent, not innerText: chunks scrolled out of view are skipped by layout but must still be copied.
      copyButton(() => [...view.querySelectorAll(".log-chunk")].map((part) => part.textContent).join(""), "Copy")),
    view);
  const empty = h("div", {});
  el.append(empty);

  // Scrolling away from the end pauses following, scrolling back resumes it.
  view.addEventListener("scroll", () => {
    const atEnd = view.scrollHeight - view.scrollTop - view.clientHeight < 24;
    if (follow !== atEnd && document.activeElement !== followBox) { follow = atEnd; followBox.checked = atEnd; }
  });

  const url = (offset, limit) => `/api/projects/${projectId}/tasks/${taskId}/log?file=${encodeURIComponent(current)}&offset=${offset}&limit=${limit}`;
  const chunk = (text) => h("pre", { class: "log-chunk" }, text.replace(ANSI, ""));
  const describe = () => {
    info.textContent = `${bytes(size)}${start > 0 ? ` · showing the last ${bytes(next - start)}` : ""}`;
    earlier.hidden = start <= 0;
  };
  const behind = () => { const known = files.find((file) => file.id === current); return Boolean(known && known.size !== next); };

  // Every open() starts a new turn and one request at a time reads for it. An answer is used only while its turn
  // is still the current one: by then another file may be shown, or the same file may have been opened again.
  async function open(id) {
    const turn = ++opened;
    current = id;
    start = next = 0;
    loading = true;
    let shown = false;
    clear(view, h("div", { class: "log-status" }, "Loading…"));
    try {
      const data = await api.get(url(-LOG_CHUNK, LOG_CHUNK));
      if (turn !== opened) return;
      ({ offset: start, next, size } = data);
      clear(view, earlier, data.text ? chunk(data.text) : h("div", { class: "log-status" }, "This file is empty."));
      describe();
      view.scrollTop = view.scrollHeight;
      shown = true;
    } catch (error) {
      if (turn === opened) clear(view, h("div", { class: "log-status" }, `This log cannot be read: ${error.message}`));
    } finally { if (turn === opened) loading = false; }
    if (shown && behind()) append(); // the file grew while its end was being read
  }

  async function loadEarlier() {
    if (loading || start <= 0) return;
    loading = true;
    const turn = opened;
    try {
      const from = Math.max(0, start - LOG_CHUNK);
      const data = await api.get(url(from, start - from));
      if (turn !== opened) return;
      const before = view.scrollHeight;
      earlier.after(chunk(data.text));
      start = data.offset;
      describe();
      view.scrollTop += view.scrollHeight - before; // keep the text the reader was looking at in place
    } catch (error) { if (turn === opened) toastError(error); } finally { if (turn === opened) loading = false; }
  }

  async function append() {
    if (loading || !current) return;
    loading = true;
    const turn = opened;
    try {
      let data;
      do {
        data = await api.get(url(next, LOG_CHUNK));
        if (turn !== opened) return;
        if (data.size < next) { await open(current); return; } // the file was replaced: start over
        if (data.text) { view.querySelector(".log-status")?.remove(); view.append(chunk(data.text)); }
        ({ next, size } = data);
      } while (!data.eof);
      describe();
      if (follow) view.scrollTop = view.scrollHeight;
    } catch { /* the next update retries */ } finally { if (turn === opened) loading = false; }
  }

  return { el, choose(id) { wanted = id; }, update(detail) {
    files = detail.logs;
    el.querySelector(".log-toolbar").hidden = view.hidden = !files.length;
    memo(empty, files.length ? "files" : "none", () => files.length ? null : emptyState({ compact: true, title: "No logs yet",
      body: "The run output and each agent's transcript appear here once the task has been started from PatchRondo." }));
    if (!files.length) return;
    memo(select, files.map((file) => [file.id, file.size]), () => {
      const groups = new Map();
      for (const file of files) groups.set(file.group, [...(groups.get(file.group) || []), file]);
      return [...groups].map(([group, items]) => h("optgroup", { label: group }, items.map((file) =>
        h("option", { value: file.id, selected: file.id === current }, `${file.label} · ${bytes(file.size)}`))));
    });
    if (wanted && files.some((file) => file.id === wanted)) {
      const id = wanted;
      wanted = null;
      if (id !== current) { open(id); select.value = id; return; }
    }
    if (!current || !files.some((file) => file.id === current)) { open(files[0].id); select.value = files[0].id; return; }
    select.value = current;
    if (behind()) append();
  } };
}

function testsPanel() {
  const el = h("div", { class: "stack" });
  return { el, update(detail, task) {
    const tests = detail.state.tests || [];
    const effective = detail.effective;
    memo(el, [tests, effective, task.project], () => [
      effective && !effective.tests_enabled ? callout({ tone: "accent", iconName: "info", title: "Tests are disabled for this project",
        body: "Without tests a task is developed, then pauses before the review: it is never marked done.",
        actions: [h("a", { class: "link", href: href("settings", task.project, "tests") }, "Open test settings")] }) : null,
      tests.length ? h("div", { class: "card list" }, tests.map((test, index) => h("div", { class: "test-row" },
        h("div", { class: "row" }, badge(test.status || "unknown", TEST_TONE[test.status] || "neutral", { dot: true }),
          h("span", { class: "mono grow break" }, test.command?.length ? test.command.join(" ") : "(no command)"),
          test.returncode != null ? h("span", { class: "chip mono" }, `exit ${test.returncode}`) : null),
        test.message ? h("div", { class: "muted" }, test.message) : null,
        test.output_tail?.trim() ? h("details", { class: "output", open: test.status !== "passed" },
          h("summary", {}, `Output (last lines) of command ${index + 1}`), h("pre", { class: "log-chunk" }, test.output_tail.replace(ANSI, ""))) : null)))
        : emptyState({ compact: true, title: "Tests have not run yet", body: effective?.tests_enabled
          ? `They run after each development step: ${effective.test_commands.join(" · ")}` : "Nothing to show until tests are enabled and the task runs." }),
      tests.length ? h("p", { class: "faint" }, "Results of the latest test run, executed by PatchRondo on this computer. Full output is in the Logs tab.") : null,
    ]);
  } };
}

function reviewPanel() {
  const el = h("div", { class: "stack" });
  return { el, update(detail, task) {
    const review = detail.state.review;
    memo(el, [review, task.reviewer], () => {
      if (!review) return emptyState({ compact: true, title: "No review yet", body: `${agentName(task.reviewer)} reviews the changes after development and tests.` });
      const verdict = VERDICTS[review.verdict] || { label: review.verdict, tone: "neutral", icon: "eye" };
      const issues = review.issues || [];
      return [
        h("div", { class: `card pad verdict tone-${verdict.tone}` }, h("span", { class: "verdict-icon" }, icon(verdict.icon, "lg")),
          h("div", { class: "grow" }, h("div", { class: "verdict-title" }, verdict.label),
            h("div", { class: "muted" }, `by ${agentName(task.reviewer)} · ${plural(issues.length, "issue")}`))),
        review.summary ? h("div", { class: "card pad break" }, review.summary) : null,
        issues.length ? h("div", { class: "card list" }, issues.map((issue) => h("div", { class: "issue-row" },
          badge(issue.severity, SEVERITY[issue.severity] || "neutral"),
          h("div", { class: "grow" }, h("div", { class: "break" }, issue.description), issue.path ? h("div", { class: "mono muted break" }, issue.path) : null,
            issue.kind === "OBSERVATION" ? h("div", { class: "faint" }, "An observation: it asks for no change and is not sent to the developer.")
              : issue.kind && issue.kind !== "CODE_CHANGES_REQUIRED" ? h("div", { class: "faint" }, "Not about the code: it is not sent to the developer.") : null))))
          : h("p", { class: "muted" }, "The reviewer reported no issues."),
        review.reported_verdict ? callout({ tone: "accent", iconName: "info", title: `The reviewer answered “${(VERDICTS[review.reported_verdict]?.label || review.reported_verdict).toLowerCase()}”`,
          body: review.normalized === "no_code_change_requested"
            ? "It named no change to the code that the developer can make: what it found open is recorded with the criteria. So nothing was sent back to the developer."
            : "It also named a change the code requires, in an issue or for a criterion, so the code is not approved.",
          actions: [h("a", { class: "link", href: href("tasks", task.project, task.id, "criteria") }, "Criteria and checks")] }) : null,
        review.refused_changes?.length ? h("p", { class: "muted break" }, `It asked for a change to the code for ${review.refused_changes.join(", ")}, which no change to the files can satisfy. `
          + "That is recorded with the criteria and was not sent to the developer.") : null,
        h("p", { class: "faint" }, "An AI review is one input. PatchRondo also requires passing tests and recorded evidence for every required criterion, and you should review the changes before merging them."),
      ];
    });
  } };
}

function diffBlocks(diff) {
  const parts = diff.split(/^(?=diff --git )/m).filter((part) => part.trim());
  return parts.map((part, index) => {
    const lines = part.split("\n");
    const name = (lines[0].match(/ b\/(.+)$/) || [])[1] || lines[0];
    const added = lines.filter((line) => line.startsWith("+") && !line.startsWith("+++")).length;
    const removed = lines.filter((line) => line.startsWith("-") && !line.startsWith("---")).length;
    const body = h("div", { class: "diff-body" });
    const details = h("details", { class: "diff-file", open: index < 3 && lines.length < 400 },
      h("summary", {}, h("span", { class: "mono grow break" }, name), h("span", { class: "diff-add num" }, `+${added}`), h("span", { class: "diff-del num" }, `−${removed}`)), body);
    const fill = () => {
      if (body.firstChild) return;
      for (const line of lines.slice(1)) {
        const kind = line.startsWith("@@") ? "hunk" : line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "del" : "ctx";
        body.append(h("div", { class: `diff-line ${kind}` }, line || " "));
      }
    };
    details.addEventListener("toggle", () => { if (details.open) fill(); });
    if (details.open) fill();
    return details;
  });
}

function filesPanel(projectId, taskId) {
  const el = h("div", { class: "stack" });
  let loaded = "", loading = false;
  async function load(force, stamp) {
    if (loading || (!force && stamp === loaded)) return;
    loading = true;
    if (!loaded) clear(el, loadingBlock("Reading the worktree…"));
    try {
      const data = await api.get(`/api/projects/${projectId}/tasks/${taskId}/files`);
      loaded = stamp;
      if (!data.available) {
        clear(el, callout({ tone: "warn", iconName: "alert", title: "The changes cannot be shown", body: data.reason }));
        return;
      }
      clear(el,
        h("div", { class: "section-head" },
          h("div", { class: "muted" }, data.files.length ? `${plural(data.files.length, "file")} changed against the task's base commit, on branch ${data.branch}` : "No changes in the worktree yet"),
          button("Refresh", { size: "sm", variant: "ghost", iconName: "refresh", onClick: () => load(true, loaded) })),
        data.files.length ? h("div", { class: "card list" }, data.files.map((file) => h("div", { class: "file-row" },
          h("span", { class: `file-code mono ${file.code.includes("?") || file.code.includes("A") ? "add" : file.code.includes("D") ? "del" : ""}` }, file.code), h("span", { class: "mono break" }, file.path))))
          : emptyState({ compact: true, title: "Nothing changed yet", body: "Files written by the developer agent appear here. Nothing is committed for you." }),
        data.diff ? [h("h2", {}, "Diff of tracked files"), diffBlocks(data.diff)] : null,
        data.truncated ? callout({ tone: "warn", iconName: "info", title: "The diff is longer than shown", body: `Run git diff in ${data.worktree} to see all of it.` }) : null,
        data.untracked.length ? [h("h2", {}, "New files"), data.untracked.map((file, index) => h("details", { class: "diff-file", open: index < 3 && file.text != null },
          h("summary", {}, h("span", { class: "mono grow break" }, file.path), file.size != null ? h("span", { class: "muted num" }, bytes(file.size)) : null,
            file.note ? h("span", { class: "chip" }, file.note) : null),
          file.text != null ? h("pre", { class: "log-chunk diff-new" }, file.text) : h("div", { class: "diff-body muted" }, file.note || "Not shown")))] : null,
        h("p", { class: "faint" }, "The main repository is not changed. Review the worktree, then commit on the task branch and merge it yourself."));
    } catch (error) {
      clear(el, callout({ tone: "danger", iconName: "alert", title: "The changes could not be loaded", body: error.message,
        actions: [button("Try again", { size: "sm", onClick: () => load(true, stamp) })] }));
    } finally { loading = false; }
  }
  return { el, update(detail) {
    const data = detail.state;
    load(false, `${data.updated_at}|${data.phase}|${data.status}|${data.iteration}|${(data.history || []).length}`);
  } };
}

function documentPanel(key, title, missing) {
  const el = h("div", {});
  return { el, update(detail) {
    const text = detail.documents[key];
    memo(el, text || "", () => text ? h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, title), copyButton(text, "Copy Markdown")),
      h("div", { class: "card-body" }, markdown(text))) : emptyState({ compact: true, title: missing[0], body: missing[1] }));
  } };
}

function rawPanel() {
  const el = h("div", { class: "stack" });
  return { el, update(detail) {
    memo(el, [detail.state, detail.documents.feedback, detail.documents.task], () => {
      const blocks = [["state.json", JSON.stringify(detail.state, null, 2)], ["task.md", detail.documents.task], ["feedback.md", detail.documents.feedback]];
      return [
        h("p", { class: "muted" }, "The files PatchRondo keeps for this task, exactly as saved. They are authoritative; everything else on this page is derived from them."),
        blocks.filter(([, text]) => text).map(([name, text], index) => h("details", { class: "diff-file", open: index === 0 },
          h("summary", {}, h("span", { class: "mono grow" }, name), copyButton(text, "Copy")), h("pre", { class: "log-chunk raw" }, text))),
      ];
    });
  } };
}

// --- View ----------------------------------------------------------------------------

export default function mount(root, route, ctx) {
  const projectId = route.project, taskId = route.task;
  let tab = TABS.some(([key]) => key === route.tab) ? route.tab : "overview";
  let detail = null, failure = null, destroyed = false, print = "", pending = false, queued = false;
  let autoRun = route.params.run === "1";
  let stamps = 0;
  let visible = null; // the panel on screen; one that streams is told when it is shown and hidden
  const panels = new Map();
  const el = {
    head: h("div", { class: "task-head" }),
    flow: h("div", {}),
    note: h("div", {}),
    panel: h("div", { class: "tab-panel", id: "tabpanel", role: "tabpanel", tabindex: "0" }),
  };
  const tabList = tabs({ label: "Task sections", selected: tab, items: TABS.map(([key, label, iconName]) => ({ key, label, icon: iconName })),
    onSelect: (key) => { tab = key; replace("tasks", projectId, taskId, key === "overview" ? null : key); showPanel(); } });
  const page = h("div", { class: "page" }, el.head, el.flow, el.note, tabList, el.panel);
  root.append(page);

  function panelFor(key) {
    if (!panels.has(key)) {
      panels.set(key, {
        overview: overviewPanel, criteria: () => criteriaPanel(projectId, taskId, { openLog }), live: () => livePanel(projectId, taskId),
        activity: activityPanel, logs: () => logsPanel(projectId, taskId), tests: testsPanel, review: reviewPanel,
        files: () => filesPanel(projectId, taskId),
        handoff: () => documentPanel("handoff", "Developer handoff", ["No handoff yet", "The developer agent writes it at the end of each development step."]),
        report: () => documentPanel("report", "Report", ["No report yet", "PatchRondo writes it when the task completes, pauses or blocks."]),
        raw: rawPanel,
      }[key]());
    }
    return panels.get(key);
  }

  // Evidence refers to a log by the name the Logs tab lists it under.
  function openLog(id) {
    panelFor("logs").choose(id);
    tab = "logs";
    replace("tasks", projectId, taskId, tab);
    tabList.update(undefined, tab);
    showPanel();
  }

  function showPanel() {
    el.panel.setAttribute("aria-labelledby", `tab-${tab}`);
    if (!detail) {
      clear(el.panel, failure ? callout({ tone: "danger", iconName: "alert", title: "The task details could not be loaded", body: failure.message,
        actions: [button("Try again", { size: "sm", onClick: () => load() })] }) : loadingBlock("Loading the task…"));
      return;
    }
    const panel = panelFor(tab);
    if (el.panel.firstChild !== panel.el) clear(el.panel, panel.el);
    const task = getTask(projectId, taskId) || detail.summary;
    panel.update(detail, task);
    if (visible !== panel) {
      visible?.hide?.();
      visible = panel;
      panel.show?.();
    }
  }

  async function load() {
    if (pending) { queued = true; return; }
    pending = true;
    try {
      const data = await api.get(`/api/projects/${projectId}/tasks/${taskId}`);
      if (destroyed) return;
      detail = data;
      detail.stamp = ++stamps;
      failure = null;
      if (autoRun) {
        // Arrived from "Create and run": ask for the same confirmation as the Run button.
        autoRun = false;
        replace("tasks", projectId, taskId);
        if (data.summary.runtime.can_start) runDialog(data.summary, data);
      }
    } catch (error) {
      if (destroyed) return;
      failure = error;
      if (error.status === 404) detail = null;
    } finally {
      pending = false;
    }
    render();
    if (queued) { queued = false; load(); }
  }

  function render() {
    const task = getTask(projectId, taskId) || detail?.summary;
    const project = state.projects.get(projectId);
    if (!task) {
      if (state.ready && failure?.status === 404 || (state.ready && !project)) {
        clear(page, emptyState({ title: "This task does not exist", body: "It may belong to a project that was removed, or the link is from another PatchRondo home.",
          action: button("Back to tasks", { variant: "primary", href: href("tasks") }) }));
        ctx.setTitle([{ label: "Tasks", href: href("tasks") }, { label: "Not found" }]);
      } else if (!el.head.firstChild) clear(el.head, loadingBlock("Loading the task…"));
      return;
    }
    ctx.setTitle([{ label: "Tasks", href: href("tasks") }, { label: project?.name || "Project", href: href("projects") }, { label: task.title || task.id }]);
    const meta = activityOf(task);
    memo(el.head, [task.title, task.runtime, task.status, task.developer, task.reviewer, task.agents, project?.name, state.about?.platform, Boolean(detail)], () => [
      h("div", { class: "grow" },
        h("div", { class: "row wrap task-eyebrow" }, activityBadge(task),
          h("a", { class: "chip", href: href("projects"), "data-tip": "Project" }, icon("folder", "sm"), project?.name || "Unknown project"),
          h("span", { class: "chip mono", "data-tip": "Task ID" }, task.id)),
        h("h1", { class: "break" }, task.title || task.id),
        agentRoles(task)),
      h("div", { class: "page-actions" }, actions(task, detail)),
    ]);
    el.head.dataset.tone = meta.tone;
    memo(el.flow, [task.phase, task.status, task.runtime.activity, task.iteration, task.max_iterations, task.environment, task.error_kind,
      task.acceptance?.outcome], () => workflowBar(task));
    const message = taskMessage(task, detail?.state);
    const watch = task.runtime.active && tab !== "live";
    memo(el.note, [message, state.lastEventAt ? clock(state.lastEventAt) : "", watch], () => rondoNote(message.text, message.mood, [
      watch ? h("a", { class: "btn sm", href: href("tasks", projectId, taskId, "live") }, icon("bolt"), "Watch live") : null,
      h("span", { class: "faint num hide-sm", "data-tip": "When this page last received a change from the server" }, state.lastEventAt ? `live · ${clock(state.lastEventAt)}` : "")]));
    const waiting = task.acceptance ? task.acceptance.required - task.acceptance.required_verified : 0;
    const counts = { activity: task.events || null, review: task.issues || null, logs: detail?.logs.length || null, criteria: waiting || null };
    tabList.update(TABS.map(([key, label, iconName]) => ({ key, label, icon: iconName, count: counts[key],
      alert: (key === "review" && task.verdict && task.verdict !== "APPROVED") || (key === "criteria" && task.acceptance?.code === "APPROVED") })), tab);
    showPanel();
  }

  function update() {
    const task = getTask(projectId, taskId);
    const next = JSON.stringify(task);
    if (next !== print) { print = next; load(); } // the summary changed: fetch the detail it summarizes
    render();
  }

  showPanel();
  return {
    update, tick: render,
    route(next) { const wanted = TABS.some(([key]) => key === next.tab) ? next.tab : "overview"; if (wanted !== tab) { tab = wanted; tabList.update(undefined, tab); showPanel(); } },
    destroy() { destroyed = true; for (const panel of panels.values()) panel.destroy?.(); },
  };
}
