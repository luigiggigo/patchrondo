// Application shell: sidebar, top bar, routing, command palette and global shortcuts.

import { api } from "./api.js";
import { $, clear, h, icon, memo } from "./dom.js";
import { clock } from "./format.js";
import { href, navigate, parseRoute, replace } from "./router.js";
import { rondoFull } from "./rondo.js";
import { connect, notify, projectList, selectedProjectId, state, subscribe, taskList } from "./store.js";
import { activityOf, countGroups } from "./tasks.js";
import { button, iconButton, initTooltips, menuItem, popover, toastError } from "./ui.js";
import about from "./views/about.js";
import home from "./views/home.js";
import newTask from "./views/newtask.js";
import projects, { addProjectDialog } from "./views/projects.js";
import settings from "./views/settings.js";
import task from "./views/task.js";
import tasks from "./views/tasks.js";
import welcome from "./views/welcome.js";

const VIEWS = { home, tasks, task, new: newTask, projects, settings, welcome, about };
const NAV = [
  { name: "home", label: "Home", icon: "home", href: href() },
  { name: "tasks", label: "Tasks", icon: "tasks", href: href("tasks") },
  { name: "projects", label: "Projects", icon: "folder", href: href("projects") },
  { name: "settings", label: "Settings", icon: "settings", href: href("settings") },
];
const CONNECTION = {
  live: { label: "Live", tone: "ok", live: true },
  polling: { label: "Live · polling", tone: "ok", live: false },
  connecting: { label: "Connecting…", tone: "neutral", live: false },
  reconnecting: { label: "Reconnecting…", tone: "warn", live: true },
  offline: { label: "Offline", tone: "danger", live: false },
  unauthorized: { label: "No session", tone: "danger", live: false },
};

const root = $("#app");
let shell = null;
let view = null;
let viewKey = "";
let redirected = false;

function applyTheme() {
  const wanted = state.settings?.theme || localStorage.getItem("patchrondo.theme") || "dark";
  const theme = wanted === "system" ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : wanted;
  if (document.documentElement.dataset.theme !== theme) document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("patchrondo.theme", wanted); } catch { /* storage disabled */ }
}

export async function setTheme(theme) {
  state.settings = { ...state.settings, theme };
  applyTheme();
  notify();
  try { await api.patch("/api/settings", { theme }); } catch (error) { toastError(error, "Theme not saved: "); }
}

export async function selectProject(id) {
  state.settings = { ...state.settings, selected_project: id }; // shown at once; the server confirms through the stream
  notify();
  try { await api.patch("/api/settings", { selected_project: id }); } catch (error) { toastError(error, "Selection not saved: "); }
}

function fatal(title, body, extra = null) {
  shell = null;
  view?.destroy?.();
  view = null;
  viewKey = "";
  root.className = "fatal";
  root.removeAttribute("aria-busy");
  clear(root, h("div", { class: "empty" }, rondoFull("idle", 132), h("h2", {}, title), h("p", {}, body), extra));
}

// --- Shell ---------------------------------------------------------------------------

function buildShell() {
  const el = {};
  el.switcher = h("button", { type: "button", class: "switcher", "aria-haspopup": "menu", "aria-expanded": "false",
    "aria-label": "Switch project", onclick: () => projectMenu(el.switcher) });
  el.nav = h("nav", { class: "nav", "aria-label": "Main" });
  el.providers = h("div", { class: "stack" });
  el.connection = h("div", { class: "side-status", role: "status" });
  el.theme = iconButton("sun", "Switch theme", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
  el.title = h("div", { class: "topbar-title" });
  el.banners = h("div", {});
  el.main = h("main", { class: "view", id: "main", tabindex: "-1" });
  el.shell = h("div", { class: "shell" },
    h("aside", { class: "sidebar", "aria-label": "Sidebar" },
      h("a", { class: "brand", href: href(), "aria-label": "PatchRondo home" },
        h("span", { class: "brand-mark" }, h("img", { src: "/assets/rondo-head.webp", alt: "", width: 128, height: 128 })),
        h("span", { class: "brand-text" }, h("div", { class: "brand-name" }, "PatchRondo"), h("div", { class: "brand-tag" }, "Code. Review. Repeat."))),
      h("div", { class: "side-section" }, el.switcher),
      h("div", { class: "side-section" }, el.nav),
      h("div", { class: "side-foot" },
        h("div", { class: "side-label" }, "Agents"), el.providers, el.connection,
        h("div", { class: "side-tools" }, el.theme,
          iconButton("heart", "About PatchRondo", () => navigate("about")),
          iconButton("sidebar", "Collapse sidebar", () => toggleSidebar(), { class: "icon-btn collapse-btn" })))),
    h("div", { class: "main" },
      h("header", { class: "topbar" },
        iconButton("menu", "Open navigation", () => el.shell.classList.toggle("drawer"), { class: "icon-btn menu-btn" }),
        el.title,
        h("button", { type: "button", class: "btn ghost hide-sm", onclick: openPalette, "data-tip": "Search and commands" },
          icon("search"), "Search", h("span", { class: "kbd" }, navigator.platform.includes("Mac") ? "⌘K" : "Ctrl K")),
        button("New task", { variant: "primary", iconName: "plus", href: href("new"), tip: "Create a task (N)" })),
      el.banners, el.main));
  el.shell.addEventListener("click", (event) => {
    // Tapping outside the drawer, or choosing a destination in it, closes it.
    if (event.target === el.shell || event.target.closest(".sidebar a, .sidebar .nav-item")) el.shell.classList.remove("drawer");
  });
  if (localStorage.getItem("patchrondo.sidebar") === "collapsed") el.shell.classList.add("collapsed");
  return el;
}

function toggleSidebar() {
  const collapsed = shell.shell.classList.toggle("collapsed");
  try { localStorage.setItem("patchrondo.sidebar", collapsed ? "collapsed" : "open"); } catch { /* storage disabled */ }
}

function projectMenu(anchor) {
  const selected = selectedProjectId();
  popover(anchor, (close) => [
    h("div", { class: "menu-label" }, "Projects"),
    menuItem("All projects", { checked: !selected, lead: h("span", { class: "glyph all" }, icon("folder", "sm")),
      onClick: () => { close(); selectProject(null); } }),
    projectList().map((project) => menuItem(project.name, {
      checked: project.id === selected, lead: h("span", { class: "glyph" }, project.name.charAt(0)),
      onClick: () => { close(); selectProject(project.id); },
    })),
    h("div", { class: "menu-sep" }),
    menuItem("Add project…", { iconName: "plus", onClick: () => { close(); addProjectDialog(); } }),
    menuItem("Manage projects", { iconName: "settings", onClick: () => { close(); navigate("projects"); } }),
  ], { align: "stretch" });
}

function providerRow(provider) {
  let tone = "neutral", text = "checking…";
  if (provider) {
    if (provider.installed === false) [tone, text] = ["warn", "not installed"];
    else if (provider.authenticated === false) [tone, text] = ["warn", "login needed"];
    else if (provider.authenticated) [tone, text] = [provider.warning ? "warn" : "ok", provider.warning ? "check billing" : "ready"];
    else [tone, text] = ["neutral", "not confirmed"];
  }
  return h("a", { class: `side-status tone-${tone}`, href: href("settings", "global", "agents"),
    "data-tip": provider?.hint || provider?.warning || provider?.version || "CLI status" },
    h("span", { class: "dot" }), h("span", { class: "grow truncate" }, provider?.label || "Agent"), h("span", { class: "faint" }, text));
}

function updateShell(route) {
  const el = shell;
  const selected = selectedProjectId();
  const project = selected ? state.projects.get(selected) : null;
  memo(el.switcher, [project?.name, project?.repo?.branch, state.projects.size], () => [
    project ? h("span", { class: "glyph" }, project.name.charAt(0)) : h("span", { class: "glyph all" }, icon("folder", "sm")),
    h("span", { class: "switcher-text" }, h("span", { class: "truncate" }, project ? project.name : "All projects"),
      h("small", { class: "truncate" }, project ? (project.repo?.branch ? `on ${project.repo.branch}` : "no branch") : `${state.projects.size} registered`)),
    icon("chevronDown", "sm chev")]);
  const counts = countGroups(taskList(selected));
  const active = route.name === "task" || route.name === "new" ? "tasks" : route.name;
  const collapsed = el.shell.classList.contains("collapsed");
  memo(el.nav, [active, counts.attention, counts.all, collapsed], () => NAV.map((item) => h("a", { class: "nav-item", href: item.href, "aria-current": item.name === active ? "page" : null,
    "data-tip": collapsed ? item.label : null },
    icon(item.icon), h("span", {}, item.label),
    item.name === "tasks" && counts.attention ? h("span", { class: "count alert", "data-tip": `${counts.attention} need attention` }, counts.attention)
      : item.name === "tasks" && counts.all ? h("span", { class: "count" }, counts.all) : null)));
  const providers = state.providers?.providers;
  memo(el.providers, providers || "checking", () => ["claude", "codex"].map((name) => providerRow(providers?.[name])));
  const connection = CONNECTION[state.connection] || CONNECTION.connecting;
  el.connection.className = `side-status tone-${connection.tone}`;
  el.connection.setAttribute("data-tip", state.lastSyncAt ? `Last confirmed by the server at ${new Date(state.lastSyncAt).toLocaleTimeString()}` : "Not connected yet");
  memo(el.connection, state.connection, () => [h("span", { class: `dot${connection.live ? " live" : ""}` }), h("span", {}, connection.label)]);
  memo(el.theme, document.documentElement.dataset.theme, () => icon(document.documentElement.dataset.theme === "dark" ? "sun" : "moon"));

  const banners = [];
  if (["reconnecting", "offline"].includes(state.connection)) {
    banners.push(h("div", { class: `banner tone-${state.connection === "offline" ? "danger" : "warn"}`, role: "status" }, icon("alert"),
      h("span", {}, state.connection === "offline"
        ? `PatchRondo is not reachable. This is the last state it confirmed, at ${clock(state.lastSyncAt)}. Runs already started continue on their own; restart patchrondo to reconnect.`
        : `Connection lost. Showing the last confirmed state from ${clock(state.lastSyncAt)}; reconnecting…`)));
  }
  if (state.error) banners.push(h("div", { class: "banner tone-danger", role: "alert" }, icon("alert"), h("span", {}, `The project registry could not be read: ${state.error}`)));
  memo(el.banners, [state.connection, state.error, banners.length ? clock(state.lastSyncAt) : ""], () => banners);
}

function setTitle(crumbs) {
  if (!shell) return;
  const items = [];
  crumbs.forEach((crumb, index) => {
    if (index) items.push(icon("chevronRight", "sm crumb-sep"));
    items.push(index < crumbs.length - 1 && crumb.href
      ? h("a", { class: "crumb", href: crumb.href }, crumb.label)
      : h("span", { class: "crumb-current" }, crumb.label));
  });
  clear(shell.title, items);
  document.title = `${crumbs[crumbs.length - 1]?.label || "PatchRondo"} · PatchRondo`;
}

// --- Command palette ----------------------------------------------------------------

function openPalette() {
  if ($(".palette")) return;
  const previous = document.activeElement;
  const input = h("input", { class: "palette-input", type: "text", placeholder: "Search tasks, projects and commands…", "aria-label": "Search",
    autocomplete: "off", spellcheck: "false", role: "combobox", "aria-expanded": "true", "aria-controls": "palette-list" });
  const list = h("div", { class: "palette-list", id: "palette-list", role: "listbox" });
  const overlay = h("div", { class: "overlay" }, h("div", { class: "palette", role: "dialog", "aria-modal": "true", "aria-label": "Search and commands" }, input, list));
  let entries = [], cursor = 0;
  const close = () => { overlay.remove(); previous?.focus?.(); };
  const commands = () => [
    { label: "New task", hint: "Create", iconName: "plus", run: () => navigate("new") },
    { label: "Add project", hint: "Create", iconName: "folder", run: () => addProjectDialog() },
    ...NAV.map((item) => ({ label: `Go to ${item.label}`, hint: "Navigate", iconName: item.icon, run: () => navigate(item.href) })),
    { label: "About PatchRondo", hint: "Navigate", iconName: "heart", run: () => navigate("about") },
    { label: "Switch theme", hint: "Command", iconName: "sun", run: () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark") },
    { label: "Show all projects", hint: "Project", iconName: "folder", run: () => selectProject(null) },
    ...projectList().map((project) => ({ label: project.name, hint: "Switch project", iconName: "folder", run: () => selectProject(project.id) })),
    ...taskList().map((item) => ({ label: item.title || item.id, hint: `${activityOf(item).label} · ${state.projects.get(item.project)?.name || ""}`,
      iconName: activityOf(item).icon, run: () => navigate("tasks", item.project, item.id), text: `${item.title} ${item.id}` })),
  ];
  const render = () => {
    const query = input.value.trim().toLowerCase();
    entries = commands().filter((entry) => !query || `${entry.text || entry.label} ${entry.hint}`.toLowerCase().includes(query)).slice(0, 40);
    cursor = Math.min(cursor, Math.max(entries.length - 1, 0));
    clear(list, entries.length ? entries.map((entry, index) => h("button", { type: "button", class: "menu-item", role: "option",
      "aria-selected": String(index === cursor), tabindex: "-1", onclick: () => { close(); entry.run(); } },
      icon(entry.iconName), h("span", { class: "grow truncate" }, entry.label), h("small", {}, entry.hint)))
      : h("div", { class: "menu-label" }, "Nothing matches"));
    list.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  };
  input.addEventListener("input", () => { cursor = 0; render(); });
  overlay.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { event.preventDefault(); close(); }
    else if (event.key === "ArrowDown") { event.preventDefault(); cursor = Math.min(cursor + 1, entries.length - 1); render(); }
    else if (event.key === "ArrowUp") { event.preventDefault(); cursor = Math.max(cursor - 1, 0); render(); }
    else if (event.key === "Enter" && entries[cursor]) { event.preventDefault(); close(); entries[cursor].run(); }
    else if (event.key === "Tab") event.preventDefault();
  });
  overlay.addEventListener("mousedown", (event) => { if (event.target === overlay) close(); });
  document.body.append(overlay);
  render();
  input.focus();
}

// --- Routing and rendering ----------------------------------------------------------

const ctx = { setTitle, navigate, openPalette, selectProject, setTheme };

function mountView(route) {
  const key = route.name === "task" ? `task:${route.project}/${route.task}` : route.name === "settings" ? "settings" : route.name;
  const bare = route.name === "welcome";
  shell.shell.classList.toggle("bare", bare);
  if (view && key === viewKey && view.route) {
    view.route(route);
    return;
  }
  view?.destroy?.();
  viewKey = key;
  shell.main.scrollTop = 0;
  const factory = VIEWS[route.name];
  if (!factory) {
    clear(shell.main, h("div", { class: "page narrow" }, h("div", { class: "empty" }, rondoFull("idle", 120), h("h2", {}, "Page not found"),
      h("p", {}, "That address does not match anything here."), button("Back to Home", { variant: "primary", href: href() }))));
    setTitle([{ label: "Not found" }]);
    view = null;
    return;
  }
  clear(shell.main);
  view = factory(shell.main, route, ctx);
  view.update?.(state);
}

function render() {
  if (state.connection === "unauthorized") {
    fatal("This tab has no session", "For your safety every PatchRondo session uses a private link. Open the link that patchrondo printed in your terminal, or run patchrondo again.");
    return;
  }
  if (!state.ready) {
    if (state.connection === "offline") {
      fatal("PatchRondo is not reachable", "The local server did not answer. Check that patchrondo is still running in your terminal; this page reconnects by itself.");
    }
    return;
  }
  applyTheme();
  let route = parseRoute();
  if (!redirected && route.name === "home" && state.projects.size === 0 && !state.settings?.onboarding_completed) {
    redirected = true;
    replace("welcome");
    route = parseRoute();
  }
  if (!shell) {
    shell = buildShell();
    root.className = "";
    root.removeAttribute("aria-busy");
    clear(root, shell.shell);
    mountView(route);
  } else {
    view?.update?.(state);
  }
  updateShell(route);
}

window.addEventListener("hashchange", () => {
  if (!shell) return;
  const route = parseRoute();
  mountView(route);
  updateShell(route);
  shell.main.focus({ preventScroll: true });
});

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); if (shell) openPalette(); return; }
  if (event.metaKey || event.ctrlKey || event.altKey || !shell) return;
  if (event.target.closest?.("input, textarea, select, [contenteditable]") || $(".overlay") || $(".popover")) return;
  if (event.key === "/") { event.preventDefault(); openPalette(); }
  else if (event.key === "n" || event.key === "N") { event.preventDefault(); navigate("new"); }
  else if (event.key === "g") { keySequence = Date.now(); }
  else if (Date.now() - keySequence < 1200) {
    const target = { h: href(), t: href("tasks"), p: href("projects"), s: href("settings") }[event.key];
    keySequence = 0;
    if (target) { event.preventDefault(); navigate(target); }
  }
});
let keySequence = 0;

matchMedia("(prefers-color-scheme: light)").addEventListener("change", applyTheme);
applyTheme();
initTooltips();
subscribe(render);
render();
connect();
// Relative times ("2 min ago", "retry in 5 min") keep moving without any server traffic.
setInterval(() => { if (!document.hidden && shell) view?.tick?.(); }, 15000);
