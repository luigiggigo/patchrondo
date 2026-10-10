// Dashboard: what is happening now, what needs a person, and the two main actions.

import { eventRow, taskRow, taskSig } from "../components.js";
import { h, icon, memo, sync } from "../dom.js";
import { ago, plural } from "../format.js";
import { href } from "../router.js";
import { boardMessage, emptyState, rondoFull } from "../rondo.js";
import { defaultsFor, projectList, selectedProjectId, state, taskList } from "../store.js";
import { activityOf, agentName, countGroups } from "../tasks.js";
import { badge, button, callout, skeleton } from "../ui.js";
import { addProjectDialog } from "./projects.js";

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
        state.projects.size ? button("New task", { variant: "primary", size: "lg", iconName: "plus", href: href("new") }) : null,
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
      alerts.push(["accent", "Project tests are disabled", "Tasks pause after the first review until tests are enabled: a task is done only when tests pass and the review approves.", `settings/${project.id}/tests`]);
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
      h("section", { class: "section" }, el.agents),
      h("section", { class: "section" }, el.stats),
      h("div", { class: "home-columns section" },
        h("div", { class: "stack home-main" }),
        h("div", { class: "stack home-side" })),
    ];
    if (memo(el.body, "board", layout)) { el.agents.dataset.sig = ""; el.stats.dataset.sig = ""; }
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
        body: `Describe what to build. ${agentName(defaults.developer)} develops it and ${agentName(defaults.reviewer)} reviews it, until tests pass and the review approves.`,
        action: button("New task", { variant: "primary", iconName: "plus", href: href("new") }) }) });
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
