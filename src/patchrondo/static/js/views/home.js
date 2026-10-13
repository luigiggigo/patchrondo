// Dashboard: what is happening now, what needs a person, and the two main actions.

import { api, requestId } from "../api.js";
import { eventRow, taskRow, taskSig } from "../components.js";
import { clear, h, icon, memo, sync } from "../dom.js";
import { ago, plural } from "../format.js";
import { href, navigate } from "../router.js";
import { boardMessage, emptyState, rondoFull } from "../rondo.js";
import { defaultsFor, projectList, selectedProjectId, state, taskList } from "../store.js";
import { AGENTS, activityOf, agentName, countGroups } from "../tasks.js";
import { badge, busy, button, callout, selectInput, skeleton, toast, toastError } from "../ui.js";
import { addProjectDialog } from "./projects.js";

const QUICK = "patchrondo.quick";

// What stands between pressing Start and the agents working, from the checks the backend already made.
// Returns { tone, label, detail, blocking }: nothing here is assumed, an unknown stays "checking".
export function readiness(roles) {
  const providers = state.providers?.providers;
  const system = state.providers?.system;
  if (!providers) return { tone: "neutral", label: "Checking this computer…", detail: "PatchRondo is looking for Git, Claude Code and Codex.", blocking: false };
  if (system && system.git && !system.git.installed) {
    return { tone: "danger", label: "Git is not installed", detail: "PatchRondo keeps every task in its own Git copy of the project. Install Git, then press Check again.", blocking: true };
  }
  for (const name of new Set([roles.developer, roles.reviewer])) {
    const provider = providers[name];
    if (provider?.installed === false) return { tone: "warn", label: `${provider.label} is not installed`, detail: provider.hint, blocking: true };
    if (provider?.authenticated === false) return { tone: "warn", label: `${provider.label} needs a login`, detail: `Run this in a terminal, then press Check again: ${provider.login_command}`, blocking: true };
  }
  const unsure = [...new Set([roles.developer, roles.reviewer])].filter((name) => providers[name]?.authenticated !== true);
  if (unsure.length) return { tone: "neutral", label: "Agents found, login not confirmed", detail: "PatchRondo checks again when the task starts and stops before any agent is called if something is missing.", blocking: false };
  return { tone: "ok", label: "Ready", detail: "Git and both agents are installed and logged in. PatchRondo checks the rest when the task starts.", blocking: false };
}

// The one-step way to start: choose the project, say what to do, press Start.
function composer() {
  let draft = {};
  try { draft = JSON.parse(sessionStorage.getItem(QUICK) || "null") || {}; } catch { /* storage disabled */ }
  let id = requestId();
  let projectId = null, roles = null, touched = false;
  const text = h("textarea", { class: "input composer-text", rows: 4, "aria-label": "What should be done",
    placeholder: "Describe what should be built or changed. For example: “Add a --json option to the export command and cover it with a test.”" });
  text.value = draft.text || "";
  const projectHost = h("div", { class: "composer-project" });
  const roleHost = h("div", { class: "composer-roles" });
  const status = h("div", { class: "composer-status" });
  const notes = h("div", { class: "stack" });
  const error = h("div", { class: "form-error", role: "alert" });
  const start = button("Start task", { variant: "primary", size: "lg", iconName: "play", onClick: () => submit() });
  const el = h("section", { class: "card pad composer", "aria-label": "Start a task" },
    h("div", { class: "composer-head" }, h("h2", {}, "What should be done?"), projectHost),
    text,
    h("div", { class: "composer-foot" }, roleHost, status, h("span", { class: "grow" }),
      h("a", { class: "link", href: href("agents"), "data-tip": "Choose the model and the options of each agent" }, "Models"),
      h("a", { class: "link", href: href("new"), "data-tip": "Title, acceptance criteria and limits for this task" }, "More options"), start),
    notes, error,
    h("p", { class: "faint" }, "PatchRondo prepares a private copy of the project, checks the agents and starts them with your Claude and Codex plans. ",
      "Your own checkout is not touched, and the project's tests run only after you allow them."));

  const save = () => { try { sessionStorage.setItem(QUICK, JSON.stringify({ text: text.value, project: projectId, roles: touched ? roles : null })); } catch { /* storage disabled */ } };
  text.addEventListener("input", save);
  text.addEventListener("keydown", (event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); submit(); } });

  async function submit() {
    error.textContent = "";
    if (!text.value.trim()) { clear(error, icon("alert", "sm"), "Describe what should be done first."); text.focus(); return; }
    if (start.disabled) return;
    await busy(start, async () => {
      try {
        const result = await api.post(`/api/projects/${projectId}/start`, { description: text.value.trim(), ...roles, request_id: id });
        id = requestId();
        text.value = "";
        try { sessionStorage.removeItem(QUICK); } catch { /* storage disabled */ }
        if (result.started) toast("Started. PatchRondo is preparing the project.");
        else toastError(result.error, "The task was created but not started: ");
        navigate("tasks", result.project, result.id);
      } catch (failure) {
        clear(error, icon("alert", "sm"), failure.message);
      }
    });
  }

  function update(selected) {
    const projects = projectList();
    if (!projects.some((project) => project.id === projectId)) {
      projectId = projects.some((project) => project.id === draft.project) ? draft.project : selected || projects[0]?.id || null;
    } else if (selected && selected !== el.dataset.selected) projectId = selected; // the switcher in the sidebar leads
    el.dataset.selected = selected || "";
    if (!touched || !roles) roles = draft.roles && !touched && projectId === draft.project ? { ...draft.roles } : defaultsFor(projectId);
    const project = state.projects.get(projectId);
    memo(projectHost, [projects.map((item) => [item.id, item.name]), projectId, project?.repo?.branch], () => [
      h("span", { class: "faint" }, "in"),
      projects.length > 1
        ? (() => { const select = selectInput(projects.map((item) => ({ value: item.id, label: item.name })), projectId,
            { "aria-label": "Project", onchange: () => { projectId = select.value; touched = false; save(); update(selectedProjectId()); } }); return select; })()
        : h("strong", {}, project?.name || ""),
      project?.repo?.branch ? h("span", { class: "chip", "data-tip": "Branch the task starts from" }, icon("git", "sm"), project.repo.branch) : null,
      button("Choose folder…", { size: "sm", variant: "ghost", iconName: "folder", tip: "Use another local project",
        onClick: () => addProjectDialog({ onAdded: (added) => { projectId = added.id; touched = false; save(); } }) }),
    ]);
    const providers = state.providers?.providers || {};
    memo(roleHost, [roles, Object.keys(providers).map((name) => [providers[name]?.installed, providers[name]?.authenticated])], () =>
      [["developer", "writes the code"], ["reviewer", "checks it, read-only"]].map(([role, does], index) => {
        const select = selectInput(Object.entries(AGENTS).map(([value, label]) => ({ value, label })), roles[role],
          { class: "input role-select", "aria-label": `${role} agent`, onchange: () => { roles = { ...roles, [role]: select.value }; touched = true; save(); update(selectedProjectId()); } });
        return [index ? icon("arrowRight", "sm faint") : null,
          h("label", { class: "role-pick", "data-tip": `The ${role} ${does}` }, h("span", { class: `agent-mark ${roles[role]}`, "aria-hidden": "true" }, agentName(roles[role]).charAt(0)),
            h("span", { class: "faint" }, role === "developer" ? "Developer" : "Reviewer"), select)];
      }));
    const ready = readiness(roles);
    const checking = Boolean(state.providers?.checking);
    memo(status, [ready, checking], () => [
      badge(ready.label, ready.tone, { dot: true, live: checking, tip: ready.detail }),
      ready.blocking || ready.tone === "neutral" ? button(checking ? "Checking…" : "Check again", { size: "sm", variant: "ghost", iconName: "refresh", disabled: checking,
        onClick: () => api.post("/api/providers/refresh").catch(toastError) }) : null,
    ]);
    // Missing agents are explained once, in the alerts above the composer.
    const problems = [];
    if (project?.config_error) problems.push(callout({ tone: "danger", iconName: "alert", title: "This project's configuration cannot be used", body: project.config_error }));
    else if (project?.repo?.dirty) problems.push(callout({ tone: "warn", iconName: "alert", title: "The project has changes that are not committed",
      body: "A task starts from the last commit, so that its copy matches a known state. Commit or stash your changes, then start." }));
    else if (project?.repo?.error) problems.push(callout({ tone: "danger", iconName: "alert", title: "The project folder cannot be read", body: project.repo.error }));
    if (roles.developer === roles.reviewer) problems.push(callout({ tone: "accent", iconName: "info", title: "Same agent for both roles",
      body: `${agentName(roles.developer)} would review its own work. A different reviewer gives an independent check.` }));
    memo(notes, [problems.length, ready, project?.repo, project?.config_error, roles], () => problems);
    start.disabled = ready.blocking || !projectId || Boolean(project?.config_error || project?.repo?.dirty || project?.repo?.error);
  }

  return { el, update };
}

function providerCard(provider, role) {
  if (!provider) return h("div", { class: "card pad provider" }, skeleton(2));
  let tone = "neutral", label = "Not confirmed";
  if (provider.installed === false) [tone, label] = ["warn", "Not installed"];
  else if (provider.authenticated === false) [tone, label] = ["warn", "Login needed"];
  else if (provider.authenticated) [tone, label] = provider.warning ? ["warn", "Check billing"] : ["ok", "Ready"];
  return h("a", { class: "card pad provider", href: href("settings", "global", "agents") },
    h("div", { class: "row" },
      h("span", { class: `agent-mark ${provider.name}`, "aria-hidden": "true" }, provider.label.charAt(0)),
      h("div", { class: "grow" }, h("div", { class: "list-title" }, provider.label),
        h("div", { class: "list-sub" }, provider.version || provider.hint || "Version unknown")),
      badge(label, tone, { dot: true })),
    role ? h("div", { class: "provider-role faint" }, role) : null);
}

function statTile(key, label, count, tone, iconName, hint) {
  return h("a", { class: `card stat tone-${tone}`, href: href("tasks", { filter: key }), "aria-label": `${count} ${label}` },
    h("div", { class: "stat-top" }, h("span", { class: "stat-icon" }, icon(iconName)), h("span", { class: "stat-label" }, label)),
    h("div", { class: "stat-value num" }, String(count)), h("div", { class: "stat-hint" }, hint));
}

export default function mount(root, route, ctx) {
  const el = {
    hero: h("div", { class: "hero" }),
    alerts: h("div", { class: "stack" }),
    agents: h("div", { class: "grid c2" }),
    stats: h("div", { class: "grid c4" }),
    body: h("div", {}),
  };
  const quick = composer();
  root.append(h("div", { class: "page" }, el.hero, el.alerts, el.body));
  ctx.setTitle([{ label: "Home" }]);

  function update() {
    const selected = selectedProjectId();
    const project = selected ? state.projects.get(selected) : null;
    const tasks = taskList(selected);
    const counts = countGroups(tasks);
    const providers = state.providers?.providers;
    const message = boardMessage({ projects: state.projects.size, counts, providers, selected: project?.name });

    memo(el.hero, [message, project?.name, project?.repo, state.projects.size], () => [
      rondoFull(message.mood, 112),
      h("div", { class: "hero-text" },
        h("div", { class: "row wrap" }, h("h1", {}, project ? project.name : state.projects.size ? "All projects" : "Welcome to PatchRondo"),
          project?.repo?.branch ? h("span", { class: "chip", "data-tip": "Branch checked out in the repository" }, icon("git", "sm"), project.repo.branch) : null),
        h("div", { class: "bubble", role: "status" }, message.text)),
      h("div", { class: "hero-actions" },
        button("Add project", { size: "lg", iconName: "folder", onClick: () => addProjectDialog() })),
    ]);

    const alerts = [];
    if (providers) {
      for (const provider of Object.values(providers)) {
        if (provider.installed === false) alerts.push(["warn", `${provider.label} is not installed`, provider.hint, "settings/global/agents"]);
        else if (provider.authenticated === false) alerts.push(["warn", `${provider.label} is not logged in`, provider.hint, "settings/global/agents"]);
        else if (provider.warning) alerts.push(["warn", `${provider.label}: ${provider.warning}`, "If ANTHROPIC_API_KEY is set, Claude Code may bill the API instead of your subscription.", "settings/global/agents"]);
      }
    }
    for (const item of project ? [project] : projectList()) {
      if (item.config_error) alerts.push(["danger", `${item.name}: the configuration cannot be used`, item.config_error, `settings/${item.id}/advanced`]);
      else if (item.repo?.error) alerts.push(["danger", `${item.name}: the repository cannot be read`, item.repo.error, "projects"]);
    }
    if (project?.config && !project.config.tests_enabled && tasks.length) {
      alerts.push(["accent", "Project tests are disabled", "Tasks pause before the review until tests are enabled: a task is done only when tests pass and the review approves.", `settings/${project.id}/tests`]);
    }
    memo(el.alerts, alerts, () => alerts.map(([tone, title, body, target]) => callout({ tone, iconName: tone === "accent" ? "info" : "alert", title, body,
      actions: [h("a", { class: "link", href: `#/${target}` }, "Open settings")] })));

    if (!state.projects.size) {
      memo(el.body, "no-projects", () => h("div", { class: "section" }, emptyState({
        mood: "welcome", title: "Add your first project",
        body: "Point PatchRondo at a local Git repository. Each task gets its own worktree and branch, so your checkout is never touched.",
        action: h("div", { class: "row wrap" }, button("Add project", { variant: "primary", iconName: "plus", onClick: () => addProjectDialog() }),
          button("Guided setup", { href: href("welcome") })),
      })));
      return;
    }

    const defaults = defaultsFor(selected);
    const layout = () => [
      h("section", { class: "section" }, quick.el),
      h("section", { class: "section" }, el.agents),
      h("section", { class: "section" }, el.stats),
      h("div", { class: "home-columns section" },
        h("div", { class: "stack home-main" }),
        h("div", { class: "stack home-side" })),
    ];
    if (memo(el.body, "board", layout)) { el.agents.dataset.sig = ""; el.stats.dataset.sig = ""; }
    quick.update(selected);
    memo(el.agents, [providers, defaults], () => ["claude", "codex"].map((name) => providerCard(providers?.[name],
      [defaults.developer === name ? "Default developer" : null, defaults.reviewer === name ? "Default reviewer" : null].filter(Boolean).join(" · "))));
    memo(el.stats, counts, () => [
      statTile("active", "Active", counts.active, "accent", "bolt", "Running or waiting to retry"),
      statTile("attention", "Needs attention", counts.attention, counts.attention ? "warn" : "neutral", "alert", "Paused, blocked or interrupted"),
      statTile("ready", "Ready", counts.ready, "neutral", "play", "Created, not started yet"),
      statTile("done", "Done", counts.done, "ok", "check", "Approved with passing tests"),
    ]);

    const main = el.body.querySelector(".home-main"), side = el.body.querySelector(".home-side");
    const groups = [
      ["attention", "Needs attention", tasks.filter((task) => activityOf(task).group === "attention")],
      ["active", "In progress", tasks.filter((task) => activityOf(task).group === "active")],
      ["ready", "Ready to run", tasks.filter((task) => activityOf(task).group === "ready").slice(0, 5)],
    ].filter(([, , items]) => items.length);
    const blocks = groups.map(([key, label, items]) => ({
      key, sig: [label, items.map((task) => taskSig(task, selected || "all"))],
      render: () => h("div", {}, h("div", { class: "section-head" }, h("h2", {}, label),
        h("a", { class: "link", href: href("tasks", { filter: key }) }, `View all ${items.length}`)),
        h("div", { class: "card list" }, items.slice(0, 6).map((task) => taskRow(task, { showProject: !selected })))),
    }));
    if (!tasks.length) {
      blocks.push({ key: "empty", sig: project?.name || "all", render: () => emptyState({
        compact: true, title: "No tasks yet",
        body: `Describe what to build in the box above and press Start task. ${agentName(defaults.developer)} develops it and ${agentName(defaults.reviewer)} reviews it, until tests pass and the review approves.` }) });
    } else if (!groups.length) {
      blocks.push({ key: "calm", sig: counts.done, render: () => emptyState({ compact: true, mood: "done", title: "Nothing needs you right now",
        body: `${plural(counts.done, "task")} done. Start another when you are ready.`,
        action: button("View all tasks", { href: href("tasks") }) }) });
    }
    sync(main, blocks);

    const events = tasks.flatMap((task) => (task.recent || []).map((event) => ({ task, event })))
      .sort((a, b) => String(b.event.at).localeCompare(String(a.event.at))).slice(0, 12);
    sync(side, [{
      key: "activity", sig: [events.map(({ task, event }) => [task.id, event.at, event.event, task.title]), state.lastEventAt ? ago(state.lastEventAt) : ""],
      render: () => h("div", {}, h("div", { class: "section-head" }, h("h2", {}, "Recent activity"),
        h("span", { class: "muted", "data-tip": "Last change received from the server" }, state.lastEventAt ? `updated ${ago(state.lastEventAt)}` : "")),
        events.length ? h("div", { class: "card pad timeline compact" }, events.map(({ task, event }) => eventRow(event, task, { showTask: true })))
          : h("div", { class: "card pad muted" }, "Events appear here as tasks run.")),
    }]);
  }

  return { update, tick: update, destroy() {} };
}
