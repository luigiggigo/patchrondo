// All tasks of the selected project, or of every project, with filters and search.

import { taskRow, taskSig } from "../components.js";
import { h, icon, memo, sync } from "../dom.js";
import { href, replace } from "../router.js";
import { emptyState } from "../rondo.js";
import { selectedProjectId, state, taskList } from "../store.js";
import { GROUPS, activityOf, countGroups } from "../tasks.js";
import { button, segmented } from "../ui.js";

export default function mount(root, route, ctx) {
  let filter = GROUPS.some(([key]) => key === route.params.filter) ? route.params.filter : "all";
  let query = "";
  const search = h("input", { class: "input", type: "search", placeholder: "Filter by title or ID", "aria-label": "Filter tasks",
    autocomplete: "off", spellcheck: "false", oninput: () => { query = search.value; update(); } });
  const el = {
    head: h("div", { class: "page-head" }),
    filters: h("div", { class: "task-filters" }),
    list: h("div", { class: "card list task-list" }),
    empty: h("div", {}),
  };
  root.append(h("div", { class: "page" }, el.head, h("div", { class: "task-toolbar" }, el.filters, h("div", { class: "task-search" }, search)), el.list, el.empty));
  ctx.setTitle([{ label: "Tasks" }]);

  // Arrow keys move between rows, as in a list.
  el.list.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const rows = [...el.list.querySelectorAll(".task-row")];
    const index = rows.indexOf(document.activeElement);
    const next = rows[index + (event.key === "ArrowDown" ? 1 : -1)];
    if (next) { event.preventDefault(); next.focus(); }
  });

  function update() {
    const selected = selectedProjectId();
    const project = selected ? state.projects.get(selected) : null;
    const tasks = taskList(selected);
    const counts = countGroups(tasks);
    memo(el.head, [project?.name, state.projects.size], () => [
      h("div", {}, h("h1", {}, "Tasks"),
        h("p", {}, project ? `In ${project.name}. ` : `Across ${state.projects.size === 1 ? "your project" : `all ${state.projects.size} projects`}. `,
          project ? h("button", { type: "button", class: "link", onclick: () => ctx.selectProject(null) }, "Show all projects") : null)),
      h("div", { class: "page-actions" }, button("New task", { variant: "primary", iconName: "plus", href: href("new") })),
    ]);
    const filtering = el.filters.contains(document.activeElement);
    const redrawn = memo(el.filters, [counts, filter], () => segmented({
      label: "Filter by state", value: filter,
      options: GROUPS.map(([key, label]) => ({ value: key, label: `${label} ${counts[key]}` })),
      onChange: (value) => { filter = value; replace("tasks", { filter: value === "all" ? null : value }); update(); },
    }));
    if (redrawn && filtering) el.filters.querySelector('[aria-checked="true"]')?.focus();
    const needle = query.trim().toLowerCase();
    const visible = tasks.filter((task) => (filter === "all" || activityOf(task).group === filter) &&
      (!needle || `${task.title} ${task.id}`.toLowerCase().includes(needle)));
    el.list.hidden = !visible.length;
    sync(el.list, visible.map((task) => ({ key: `${task.project}/${task.id}`, sig: taskSig(task, selected || "all"),
      render: () => taskRow(task, { showProject: !selected }) })));
    const reason = !state.projects.size ? "no-projects" : !tasks.length ? "no-tasks" : !visible.length ? "no-match" : "";
    memo(el.empty, [reason, filter, needle], () => {
      if (reason === "no-projects") return emptyState({ mood: "welcome", title: "No projects yet", body: "Add a Git repository first; tasks live inside a project.",
        action: button("Go to Projects", { variant: "primary", href: href("projects") }) });
      if (reason === "no-tasks") return emptyState({ title: "No tasks yet", body: "Describe what to build and let Claude and Codex take turns on it.",
        action: button("New task", { variant: "primary", iconName: "plus", href: href("new") }) });
      if (reason === "no-match") return h("div", { class: "empty compact" }, icon("search", "lg faint"), h("h2", {}, "Nothing matches"),
        h("p", {}, needle ? `No ${filter === "all" ? "" : `${GROUPS.find(([key]) => key === filter)[1].toLowerCase()} `}task matches “${query.trim()}”.` : "No task is in this state right now."),
        button("Clear filters", { onClick: () => { filter = "all"; query = search.value = ""; replace("tasks"); update(); } }));
      return null;
    });
  }

  return { update, tick: update, destroy() {} };
}
