// Project Manager: register local Git repositories, switch between them, rename or forget them.

import { api, requestId } from "../api.js";
import { clear, h, icon, sync } from "../dom.js";
import { plural } from "../format.js";
import { href, navigate } from "../router.js";
import { emptyState } from "../rondo.js";
import { projectList, refresh, selectedProjectId, state, taskList } from "../store.js";
import { countGroups } from "../tasks.js";
import { badge, busy, button, callout, confirm, copyButton, dialog, field, iconButton, menuItem, popover, toast, toastError } from "../ui.js";

// A folder picker backed by the local server: it lists folder names on this computer only.
function folderBrowser(onPick) {
  const el = h("div", { class: "browser card" });
  async function open(path) {
    el.setAttribute("aria-busy", "true");
    try {
      const data = await api.post("/api/fs/list", { path });
      clear(el,
        h("div", { class: "browser-head" },
          iconButton("up", "Parent folder", () => open(data.parent), { disabled: !data.parent }),
          h("span", { class: "mono truncate grow", "data-tip": data.path }, data.path),
          button(data.repo ? "Use this repository" : "Use this folder", { size: "sm", variant: data.repo ? "primary" : "", onClick: () => onPick(data.path) })),
        h("div", { class: "browser-roots" }, data.roots.map((rootPath) => h("button", { type: "button", class: "chip", onclick: () => open(rootPath) }, rootPath))),
        h("div", { class: "browser-list", role: "list" },
          data.entries.length ? data.entries.map((entry) => h("button", { type: "button", class: "menu-item", role: "listitem", onclick: () => open(entry.path) },
            icon(entry.repo ? "git" : "folder"), h("span", { class: "grow truncate" }, entry.name),
            entry.repo ? h("small", {}, "Git repository") : null))
            : h("div", { class: "menu-label" }, "No sub-folders"),
          data.truncated ? h("div", { class: "menu-label" }, "Only the first 500 folders are listed") : null));
    } catch (error) {
      clear(el, callout({ tone: "danger", iconName: "alert", title: "That folder cannot be opened", body: error.message,
        actions: [button("Go to my home folder", { size: "sm", onClick: () => open(null) })] }));
    } finally {
      el.removeAttribute("aria-busy");
    }
  }
  el.open = open;
  return el;
}

// Path field with live validation by the backend (read-only Git checks, nothing is created).
export function repositoryField({ onChange } = {}) {
  const input = h("input", { class: "input mono", type: "text", placeholder: navigator.platform.startsWith("Win") ? "C:\\src\\my-project" : "/home/you/src/my-project",
    autocomplete: "off", spellcheck: "false", autofocus: true });
  const status = h("div", { class: "repo-status", "aria-live": "polite" });
  const browser = folderBrowser((path) => { input.value = path; browser.hidden = true; check(); });
  browser.hidden = true;
  const browse = button("Browse…", { iconName: "folder", onClick: () => { browser.hidden = !browser.hidden; if (!browser.hidden) browser.open(input.value.trim() || null); } });
  let timer = null, serial = 0, result = null;
  async function check() {
    const path = input.value.trim();
    const mine = ++serial;
    result = null;
    onChange?.(null);
    if (!path) { clear(status); return; }
    clear(status, h("span", { class: "faint" }, "Checking…"));
    try {
      const data = await api.post("/api/projects/inspect", { path });
      if (mine !== serial) return;
      result = data;
      clear(status,
        data.problems.map((problem) => h("div", { class: "field-error" }, icon("alert", "sm"), h("span", {}, problem,
          data.root && !data.is_root ? [" ", h("button", { type: "button", class: "link", onclick: () => { input.value = data.root; check(); } }, `Use ${data.root}`)] : null))),
        data.ok ? h("div", { class: "repo-ok" }, icon("check", "sm"), h("span", {}, `Git repository on ${data.branch || "a detached HEAD"}${data.head ? ` · ${data.head}` : ""}`)) : null,
        data.warnings.map((warning) => h("div", { class: "repo-warn" }, icon("alert", "sm"), h("span", {}, warning))));
      onChange?.(data);
    } catch (error) {
      if (mine !== serial) return;
      clear(status, h("div", { class: "field-error" }, icon("alert", "sm"), error.message));
    }
  }
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(check, 350); });
  const el = h("div", { class: "stack" },
    field({ label: "Repository folder", control: input, error: false,
      hint: "The root folder of a local Git repository with at least one commit. PatchRondo never commits, pushes or edits your checkout." }),
    h("div", { class: "row" }, browse), browser, status);
  el.input = input;
  el.check = check;
  Object.defineProperty(el, "result", { get: () => result });
  return el;
}

export function addProjectDialog({ onAdded } = {}) {
  const id = requestId();
  const name = h("input", { class: "input", type: "text", maxlength: 80, placeholder: "Defaults to the folder name" });
  const error = h("div", { class: "form-error", role: "alert" });
  let add;
  const repo = repositoryField({ onChange: (data) => {
    if (add) add.disabled = !data?.ok;
    if (data?.ok && !name.dataset.touched) name.placeholder = data.name;
  } });
  name.addEventListener("input", () => { name.dataset.touched = "1"; });
  return dialog({
    title: "Add a project", wide: true,
    description: "Register a local Git repository. Its tasks, worktrees and logs are kept in PatchRondo's private folder, outside the repository.",
    build: (close) => {
      add = button("Add project", { variant: "primary", disabled: true, onClick: () => busy(add, async () => {
        error.textContent = "";
        try {
          const project = await api.post("/api/projects", { path: repo.input.value.trim(), name: name.value.trim() || null, request_id: id });
          await api.patch("/api/settings", { selected_project: project.id, onboarding_completed: true });
          await refresh();
          close(project);
          toast(`“${project.name}” added. Tests stay disabled until you enable them.`);
          onAdded?.(project);
        } catch (failure) {
          clear(error, icon("alert", "sm"), failure.message);
        }
      }) });
      return { body: [repo, field({ label: "Display name", optional: true, control: name, error: false })],
               actions: [error, button("Cancel", { variant: "ghost", onClick: () => close() }), add] };
    },
  });
}

export function importDialog() {
  const home = h("input", { class: "input mono", type: "text", placeholder: "/home/you/.patchrondo-other", autocomplete: "off", spellcheck: "false" });
  const error = h("div", { class: "form-error", role: "alert" });
  return dialog({
    title: "Import a PatchRondo 0.1 state directory", wide: true,
    description: "Registers an existing state directory where it is. Its configuration, tasks, worktrees and logs are used in place: nothing is moved, copied or rewritten.",
    build: (close) => {
      const go = button("Import", { variant: "primary", onClick: () => busy(go, async () => {
        error.textContent = "";
        try {
          const project = await api.post("/api/projects/import", { home: home.value.trim() });
          await refresh();
          close();
          toast(`Imported “${project.name}” with ${plural(project.tasks, "task")}.${project.config?.tests_enabled ? " Its tests were already enabled and stay enabled." : ""}`);
        } catch (failure) {
          clear(error, icon("alert", "sm"), failure.message);
        }
      }) });
      return { body: [field({ label: "State directory", control: home, error: false,
        hint: "The folder that contains config.json and tasks/, for example the value you passed as --home or PATCHRONDO_HOME." })],
        actions: [error, button("Cancel", { variant: "ghost", onClick: () => close() }), go] };
    },
  });
}

export function renameDialog(project) {
  const name = h("input", { class: "input", type: "text", maxlength: 80, value: project.name });
  const error = h("div", { class: "form-error", role: "alert" });
  return dialog({
    title: "Rename project", description: "Only the name shown in PatchRondo changes. The repository folder is not renamed.",
    build: (close) => {
      const save = button("Save", { variant: "primary", type: "submit" });
      const form = h("form", { class: "stack", onsubmit: (event) => {
        event.preventDefault();
        busy(save, async () => {
          try {
            await api.patch(`/api/projects/${project.id}`, { name: name.value });
            await refresh();
            close();
            toast("Project renamed");
          } catch (failure) { clear(error, icon("alert", "sm"), failure.message); }
        });
      } }, field({ label: "Display name", control: name, error: false }),
        h("div", { class: "form-foot" }, error, button("Cancel", { variant: "ghost", onClick: () => close() }), save));
      return { body: form };
    },
  });
}

export async function removeProject(project) {
  const tasks = taskList(project.id).length;
  const ok = await confirm({
    title: `Remove “${project.name}” from PatchRondo?`, confirmLabel: "Remove project", variant: "danger", iconName: "trash",
    body: [
      h("p", {}, "The project disappears from this list. Nothing is deleted from your disk:"),
      h("ul", { class: "plain-list" },
        h("li", {}, "the repository ", h("span", { class: "mono break" }, project.repository || ""), " is not touched;"),
        h("li", {}, `its ${plural(tasks, "task")}, worktrees and logs stay in `, h("span", { class: "mono break" }, project.home), ".")),
      h("p", { class: "muted" }, "You can bring it back later with Import."),
    ],
  });
  if (!ok) return;
  try {
    await api.del(`/api/projects/${project.id}`);
    await refresh();
    toast(`“${project.name}” removed. Its files were kept.`);
  } catch (error) { toastError(error); }
}

function projectCard(project, ctx, selected) {
  const counts = countGroups(taskList(project.id));
  const config = project.config;
  const more = iconButton("dots", `More actions for ${project.name}`, () => popover(more, (close) => [
    menuItem("Rename…", { iconName: "edit", onClick: () => { close(); renameDialog(project); } }),
    menuItem("Project settings", { iconName: "settings", onClick: () => { close(); navigate("settings", project.id, "general"); } }),
    h("div", { class: "menu-sep" }),
    menuItem("Remove from PatchRondo…", { iconName: "trash", onClick: () => { close(); removeProject(project); } }),
  ], { align: "end" }), { "aria-haspopup": "menu" });
  return h("article", { class: `card project${project.id === selected ? " selected" : ""}`, "aria-label": project.name },
    h("div", { class: "project-head" },
      h("span", { class: "glyph lg", "aria-hidden": "true" }, project.name.charAt(0)),
      h("div", { class: "grow" },
        h("div", { class: "row wrap" }, h("h2", { class: "truncate" }, project.name),
          project.id === selected ? badge("Selected", "accent") : null,
          project.origin !== "managed" ? badge(project.origin === "legacy" ? "0.1 home" : "Imported", "neutral", { outline: true, tip: "Uses a state directory created by PatchRondo 0.1, in place" }) : null),
        h("div", { class: "mono muted break project-path" }, project.repository || "Repository unknown")),
      more),
    h("div", { class: "project-meta" },
      project.repo?.branch ? h("span", { class: "chip", "data-tip": "Branch checked out in the repository" }, icon("git", "sm"), project.repo.branch, project.repo.head ? h("span", { class: "faint mono" }, project.repo.head) : null) : null,
      project.repo?.dirty ? badge("Uncommitted changes", "warn", { tip: "Commit or stash them before creating a task" }) : null,
      project.repo?.error ? badge("Repository unreadable", "danger", { tip: project.repo.error }) : null,
      project.config_error ? badge("Configuration invalid", "danger", { tip: project.config_error }) : null,
      config ? badge(config.tests_enabled ? `Tests on · ${plural(config.test_commands, "command")}` : "Tests off", config.tests_enabled ? "ok" : "neutral", { outline: true }) : null,
      config?.recovery_enabled ? badge("Auto recovery", "wait", { outline: true }) : null,
      h("span", { class: "muted" }, `${plural(counts.all, "task")}${counts.active ? ` · ${counts.active} active` : ""}${counts.attention ? ` · ${counts.attention} need attention` : ""}`)),
    h("div", { class: "project-actions" },
      button(project.id === selected ? "View tasks" : "Open", { variant: project.id === selected ? "" : "primary",
        onClick: async () => { await ctx.selectProject(project.id); navigate("tasks"); } }),
      button("New task", { iconName: "plus", href: href("new", { project: project.id }) }),
      button("Configure", { variant: "ghost", iconName: "settings", href: href("settings", project.id, "general") }),
      copyButton(project.repository || "", "Copy path")));
}

export default function mount(root, route, ctx) {
  const list = h("div", { class: "stack projects" });
  const empty = h("div", {});
  const moreButton = iconButton("dots", "More project actions", () => popover(moreButton, (close) => [
    menuItem("Import a 0.1 state directory…", { iconName: "database", onClick: () => { close(); importDialog(); } }),
  ], { align: "end" }), { "aria-haspopup": "menu" });
  root.append(h("div", { class: "page" },
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Projects"), h("p", {}, "Each project is a local Git repository with its own settings, tasks and worktrees.")),
      h("div", { class: "page-actions" }, moreButton, button("Add project", { variant: "primary", iconName: "plus", onClick: () => addProjectDialog() }))),
    list, empty));
  ctx.setTitle([{ label: "Projects" }]);

  function update() {
    const selected = selectedProjectId();
    const projects = projectList();
    sync(list, projects.map((project) => ({ key: project.id,
      sig: [project, project.id === selected, countGroups(taskList(project.id))], render: () => projectCard(project, ctx, selected) })));
    if (!projects.length && !empty.firstChild) {
      empty.append(emptyState({ mood: "welcome", title: "No projects yet",
        body: "Add a local Git repository to start. PatchRondo works in separate worktrees and never commits or pushes for you.",
        action: h("div", { class: "row wrap" }, button("Add project", { variant: "primary", iconName: "plus", onClick: () => addProjectDialog() }),
          button("Guided setup", { href: href("welcome") })) }));
    } else if (projects.length) clear(empty);
  }
  return { update, destroy() {} };
}
