// Task workspace: header, workflow, live activity, logs, tests, review, changed files and documents.
// The header follows the live summary; the panels follow the detail fetched after each change.

import { api } from "../api.js";
import { agentRoles, eventRow, workflowBar } from "../components.js";
import { clear, h, icon, memo, sync } from "../dom.js";
import { ago, bytes, clock, duration, fullDate, humanize, markdown, plural } from "../format.js";
import { href, navigate, replace } from "../router.js";
import { emptyState, rondoNote, taskMessage } from "../rondo.js";
import { getTask, state } from "../store.js";
import { RECOVERY_SOURCES, SEVERITY, TEST_TONE, VERDICTS, activityOf, agentName, errorOf, stopReason } from "../tasks.js";
import { activityBadge, badge, busy, button, callout, confirm, copyButton, dialog, iconButton, loadingBlock, menuItem, popover, tabs,
  toast, toastError, toggle } from "../ui.js";

const TABS = [
  ["overview", "Overview", "home"], ["activity", "Activity", "activity"], ["logs", "Logs", "terminal"], ["tests", "Tests", "flask"],
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
  const dev = agentName(task.developer), rev = agentName(task.reviewer);
  const providers = state.providers?.providers || {};
  let recovery = waiting ? false : Boolean(effective?.recovery_enabled);
  let touched = waiting;
  const hint = h("div", { class: "field-hint" });
  const describe = () => {
    if (waiting) hint.textContent = "A process is already waiting for the planned retry. This runs once now instead and cancels that plan; the waiting process then stops by itself.";
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
          h("p", {}, `${dev} develops and ${rev} reviews in the task's own worktree. `, h("strong", {}, "This calls the real CLIs and uses your plan quota.")),
          effective && !effective.tests_enabled ? callout({ tone: "warn", iconName: "flask", title: "Project tests are disabled",
            body: "The task will pause after the first review: a task is done only when tests pass and the review approves." }) : null,
          missing.map((provider) => callout({ tone: "warn", iconName: "alert", title: `${provider.label} is ${provider.installed === false ? "not installed" : "not logged in"}`, body: provider.hint })),
          h("div", { class: "card" }, h("label", { class: "setting" },
            h("div", { class: "setting-text" }, h("div", { class: "field-label" }, "Automatic quota recovery"), hint),
            toggle({ checked: recovery, disabled: waiting, label: "Automatic quota recovery", onChange: (value) => { recovery = value; touched = true; describe(); } }))),
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
    const label = runtime.waiting_process ? "Run now" : task.status === "ready" ? "Run" : "Resume";
    items.push(button(label, { variant: runtime.waiting_process ? "" : "primary", iconName: "play", onClick: () => runDialog(task, detail) }));
  }
  const more = iconButton("dots", "More actions", () => popover(more, (close) => [
    menuItem("Copy task ID", { iconName: "copy", onClick: () => { close(); navigator.clipboard?.writeText(task.id).then(() => toast("Task ID copied")); } }),
    detail?.state?.worktree ? menuItem("Copy worktree path", { iconName: "folder", onClick: () => { close(); navigator.clipboard?.writeText(detail.state.worktree).then(() => toast("Worktree path copied")); } }) : null,
    menuItem("Project settings", { iconName: "settings", onClick: () => { close(); navigate("settings", task.project, "general"); } }),
  ], { align: "end" }), { "aria-haspopup": "menu" });
  items.push(more);
  return items;
}

// --- Panels --------------------------------------------------------------------------

function recoveryCard(task, plan) {
  if (!plan || typeof plan !== "object") return null;
  const activity = task.runtime.activity;
  const used = plan.consecutive_failures, limit = plan.max_consecutive_retries;
  const process = task.runtime.processes.find((entry) => entry.state === "alive");
  const stateLine = {
    waiting_retry: ["wait", `A process is waiting${process ? ` (PID ${process.pid})` : ""}. It retries at the planned time for as long as it keeps running.`],
    plan_only: ["warn", "Saved plan only: no process is waiting. Nothing retries until you resume."],
    plan_unverified: ["warn", "A waiting process was recorded, but this computer cannot verify that it is alive."],
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
    // `stamp` changes with every fetched detail: timestamps in the state only have one-second resolution.
    memo(el, [detail.stamp, task.runtime, detail.effective, detail.worktree_exists, ago(data.recovery?.resume_at)], () => [
      h("div", { class: "stack" },
        task.runtime.activity === "stale_lock" && !task.runtime.can_unlock ? callout({ tone: "danger", iconName: "lock", title: "The lock has to be removed by hand on native Windows",
          body: ["PatchRondo found no live process for this lock, but on native Windows the engine cannot confirm that for the agent's child processes. After checking that no claude, codex or test process is still running, delete ",
            h("span", { class: "mono break" }, `${state.projects.get(task.project)?.home}/tasks/${task.id}/.run.lock`), " and resume. WSL2 does this check for you."] }) : null,
        error && task.status !== "done" ? callout({ tone: task.status === "blocked" ? "danger" : "warn", iconName: "alert", title: error.title,
          body: [h("div", {}, error.help), error.message ? h("pre", { class: "error-message" }, error.message) : null] }) : null,
        !detail.worktree_exists ? callout({ tone: "danger", iconName: "folder", title: "The task worktree is missing", body: `Expected at ${data.worktree}. The task cannot run without it.` }) : null,
        detail.config_error ? callout({ tone: "danger", iconName: "alert", title: "The project configuration cannot be used", body: detail.config_error }) : null,
        recoveryCard({ ...task, last_error: data.last_error }, data.recovery),
        h("div", { class: "grid c2" },
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
            Object.entries(values).map(([key, value]) => `${humanize(key)}: ${value}`)).join(" · "))] : null),
        h("div", { class: "row wrap" }, copyButton(data.worktree, "Copy worktree path"))),
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
  let files = [], current = null, start = 0, next = 0, size = 0, follow = true, loading = false;
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

  async function open(id) {
    current = id;
    start = next = 0;
    clear(view, h("div", { class: "log-status" }, "Loading…"));
    try {
      const data = await api.get(url(-LOG_CHUNK, LOG_CHUNK));
      if (current !== id) return;
      ({ offset: start, next, size } = data);
      clear(view, earlier, data.text ? chunk(data.text) : h("div", { class: "log-status" }, "This file is empty."));
      describe();
      view.scrollTop = view.scrollHeight;
    } catch (error) {
      clear(view, h("div", { class: "log-status" }, `This log cannot be read: ${error.message}`));
    }
  }

  async function loadEarlier() {
    if (loading || start <= 0) return;
    loading = true;
    try {
      const from = Math.max(0, start - LOG_CHUNK);
      const data = await api.get(url(from, start - from));
      const before = view.scrollHeight;
      earlier.after(chunk(data.text));
      start = data.offset;
      describe();
      view.scrollTop += view.scrollHeight - before; // keep the text the reader was looking at in place
    } catch (error) { toastError(error); } finally { loading = false; }
  }

  async function append() {
    if (loading || !current) return;
    loading = true;
    try {
      const id = current;
      let data;
      do {
        data = await api.get(url(next, LOG_CHUNK));
        if (current !== id) return;
        if (data.size < next) { await open(id); return; } // the file was replaced: start over
        if (data.text) { view.querySelector(".log-status")?.remove(); view.append(chunk(data.text)); }
        ({ next, size } = data);
      } while (!data.eof);
      describe();
      if (follow) view.scrollTop = view.scrollHeight;
    } catch { /* the next update retries */ } finally { loading = false; }
  }

  return { el, update(detail) {
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
    if (!current || !files.some((file) => file.id === current)) { open(files[0].id); select.value = files[0].id; return; }
    select.value = current;
    const known = files.find((file) => file.id === current);
    if (known && known.size !== next) append();
  } };
}

function testsPanel() {
  const el = h("div", { class: "stack" });
  return { el, update(detail, task) {
    const tests = detail.state.tests || [];
    const effective = detail.effective;
    memo(el, [tests, effective, task.project], () => [
      effective && !effective.tests_enabled ? callout({ tone: "accent", iconName: "info", title: "Tests are disabled for this project",
        body: "Without tests a task is developed and reviewed once, then pauses: it is never marked done.",
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
          h("div", { class: "grow" }, h("div", { class: "break" }, issue.description), issue.path ? h("div", { class: "mono muted break" }, issue.path) : null))))
          : h("p", { class: "muted" }, "The reviewer reported no issues."),
        h("p", { class: "faint" }, "An AI review is one input. PatchRondo also requires passing tests, and you should review the changes before merging them."),
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
        overview: overviewPanel, activity: activityPanel, logs: () => logsPanel(projectId, taskId), tests: testsPanel, review: reviewPanel,
        files: () => filesPanel(projectId, taskId),
        handoff: () => documentPanel("handoff", "Developer handoff", ["No handoff yet", "The developer agent writes it at the end of each development step."]),
        report: () => documentPanel("report", "Report", ["No report yet", "PatchRondo writes it when the task completes, pauses or blocks."]),
        raw: rawPanel,
      }[key]());
    }
    return panels.get(key);
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
    memo(el.head, [task.title, task.runtime, task.status, task.developer, task.reviewer, project?.name, state.about?.platform, Boolean(detail)], () => [
      h("div", { class: "grow" },
        h("div", { class: "row wrap task-eyebrow" }, activityBadge(task),
          h("a", { class: "chip", href: href("projects"), "data-tip": "Project" }, icon("folder", "sm"), project?.name || "Unknown project"),
          h("span", { class: "chip mono", "data-tip": "Task ID" }, task.id)),
        h("h1", { class: "break" }, task.title || task.id),
        agentRoles(task)),
      h("div", { class: "page-actions" }, actions(task, detail)),
    ]);
    el.head.dataset.tone = meta.tone;
    memo(el.flow, [task.phase, task.status, task.runtime.activity, task.iteration, task.max_iterations], () => workflowBar(task));
    const message = taskMessage(task, detail?.state);
    memo(el.note, [message, state.lastEventAt ? clock(state.lastEventAt) : ""], () => rondoNote(message.text, message.mood,
      h("span", { class: "faint num hide-sm", "data-tip": "When this page last received a change from the server" }, state.lastEventAt ? `live · ${clock(state.lastEventAt)}` : "")));
    const counts = { activity: task.events || null, review: task.issues || null, logs: detail?.logs.length || null };
    tabList.update(TABS.map(([key, label, iconName]) => ({ key, label, icon: iconName, count: counts[key],
      alert: key === "review" && task.verdict && task.verdict !== "APPROVED" })), tab);
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
    destroy() { destroyed = true; },
  };
}
