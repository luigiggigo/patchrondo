// New task: a short form with sensible defaults from the project, and optional per-task overrides.

import { api, requestId } from "../api.js";
import { clear, h, icon, memo } from "../dom.js";
import { duration } from "../format.js";
import { href, navigate } from "../router.js";
import { emptyState } from "../rondo.js";
import { defaultsFor, projectList, selectedProjectId, state } from "../store.js";
import { AGENTS, agentName } from "../tasks.js";
import { busy, button, callout, field, iconButton, numberInput, segmented, selectInput } from "../ui.js";

const DRAFT = "patchrondo.draft";

function readDraft() {
  try { return JSON.parse(sessionStorage.getItem(DRAFT) || "null") || {}; } catch { return {}; }
}

export default function mount(root, route, ctx) {
  ctx.setTitle([{ label: "Tasks", href: href("tasks") }, { label: "New task" }]);
  const page = h("div", { class: "page narrow" });
  root.append(page);
  if (!state.projects.size) {
    page.append(emptyState({ mood: "welcome", title: "Add a project first", body: "A task belongs to a project: a local Git repository PatchRondo may work on.",
      action: button("Go to Projects", { variant: "primary", href: href("projects") }) }));
    return { update() {}, destroy() {} };
  }

  const draft = readDraft();
  const id = requestId();
  let projectId = state.projects.has(route.params.project) ? route.params.project
    : state.projects.has(draft.project) ? draft.project : selectedProjectId() || projectList()[0].id;
  let roles = { ...defaultsFor(projectId), ...(draft.roles || {}) };
  let recovery = "default";

  const title = h("input", { class: "input", type: "text", maxlength: 200, placeholder: "Handle JWT expiration", value: draft.title || "", autofocus: true });
  const description = h("textarea", { class: "input", rows: 7, placeholder: "What should be built or changed, where, and any constraints the agents must respect." });
  description.value = draft.description || "";
  const criteria = h("div", { class: "stack criteria" });
  const projectSelect = selectInput(projectList().map((project) => ({ value: project.id, label: project.name })), projectId,
    { onchange: () => { projectId = projectSelect.value; roles = defaultsFor(projectId); roleControls(); update(); save(); } });
  const repoNote = h("div", {});
  const roleHost = h("div", { class: "grid c2" });
  const roleNote = h("div", {});
  const advancedHost = h("div", { class: "stack" });
  const formError = h("div", { class: "form-error", role: "alert" });
  const fields = {
    project: field({ label: "Project", control: projectSelect }),
    title: field({ label: "Title", control: title, hint: "A short name you will recognize in the list." }),
    description: field({ label: "Description", control: description, hint: "Sent to the developer agent as the authoritative requirements. Markdown is fine." }),
  };

  function criterion(text = "") {
    const input = h("input", { class: "input", type: "text", value: text, placeholder: "The test suite passes", "aria-label": "Acceptance criterion",
      oninput: save,
      onkeydown: (event) => {
        if (event.key === "Enter") { event.preventDefault(); const next = criterion(); row.after(next); next.querySelector("input").focus(); save(); }
        else if (event.key === "Backspace" && !input.value && criteria.children.length > 1) { event.preventDefault(); const previous = row.previousElementSibling; row.remove(); (previous || criteria.firstElementChild).querySelector("input").focus(); save(); }
      },
      onpaste: (event) => {
        const lines = (event.clipboardData?.getData("text") || "").split(/\r?\n/).map((line) => line.replace(/^\s*[-*]\s*/, "").trim()).filter(Boolean);
        if (lines.length < 2) return;
        event.preventDefault();
        input.value = lines[0];
        let last = row;
        for (const line of lines.slice(1)) { const next = criterion(line); last.after(next); last = next; }
        save();
      } });
    const row = h("div", { class: "input-row" }, input,
      iconButton("x", "Remove this criterion", () => { if (criteria.children.length > 1) row.remove(); else input.value = ""; save(); }));
    return row;
  }
  for (const text of draft.acceptance?.length ? draft.acceptance : [""]) criteria.append(criterion(text));

  function roleControls() {
    const providers = state.providers?.providers || {};
    const options = Object.entries(AGENTS).map(([value, label]) => ({ value, label,
      tip: providers[value]?.installed === false ? `${label} CLI is not installed` : providers[value]?.authenticated === false ? `${label} CLI is not logged in` : null }));
    clear(roleHost,
      h("div", { class: "field" }, h("div", { class: "field-label" }, "Developer"),
        segmented({ label: "Developer", value: roles.developer, options, onChange: (value) => { roles.developer = value; roleHints(); save(); } }),
        h("div", { class: "field-hint" }, "Writes the code in the task worktree.")),
      h("div", { class: "field" }, h("div", { class: "field-label" }, "Reviewer"),
        segmented({ label: "Reviewer", value: roles.reviewer, options, onChange: (value) => { roles.reviewer = value; roleHints(); save(); } }),
        h("div", { class: "field-hint" }, "Reads the result and answers with a verdict; it cannot edit files.")));
    roleHints();
  }
  function roleHints() {
    const providers = state.providers?.providers || {};
    const notes = [];
    for (const name of new Set([roles.developer, roles.reviewer])) {
      const provider = providers[name];
      if (provider?.installed === false) notes.push(callout({ tone: "warn", iconName: "alert", title: `${provider.label} is not installed`, body: `${provider.hint}. You can still create the task and run it later.` }));
      else if (provider?.authenticated === false) notes.push(callout({ tone: "warn", iconName: "alert", title: `${provider.label} is not logged in`, body: provider.hint }));
    }
    if (roles.developer === roles.reviewer) notes.push(callout({ tone: "accent", iconName: "info", title: "Same agent for both roles",
      body: `${agentName(roles.developer)} would review its own work. A different reviewer gives an independent check.` }));
    clear(roleNote, notes);
  }

  const numbers = {};
  function advanced() {
    const config = state.projects.get(projectId)?.config;
    const limits = state.about?.limits?.workflow || {};
    const make = (key, label, unit, hint) => {
      const [min, max] = limits[key] || [1, 86400];
      numbers[key] = numberInput({ value: numbers[key]?.input.value ?? draft.overrides?.[key] ?? "", min, max, unit, label, onInput: save });
      numbers[key].input.placeholder = config ? String(config[key]) : "";
      const item = field({ label, optional: true, control: numbers[key].input, hint: `${hint} Project value: ${config ? (unit === "seconds" ? `${config[key]} s (${duration(config[key])})` : config[key]) : "unknown"}.` });
      // field() took the input out of its unit wrapper: put the wrapper in its place and the input back inside.
      numbers[key].input.replaceWith(numbers[key]);
      numbers[key].prepend(numbers[key].input);
      return item;
    };
    clear(advancedHost,
      h("div", { class: "grid c3" },
        make("max_iterations", "Max iterations", "", "Develop–review rounds before the task is blocked."),
        make("agent_timeout_seconds", "Agent timeout", "seconds", "Longest single agent call."),
        make("test_timeout_seconds", "Test timeout", "seconds", "Longest single test command.")),
      h("div", { class: "field" }, h("div", { class: "field-label" }, "Automatic quota recovery"),
        segmented({ label: "Automatic quota recovery", value: recovery, onChange: (value) => { recovery = value; save(); }, options: [
          { value: "default", label: `Project default (${config?.recovery_enabled ? "on" : "off"})` }, { value: "on", label: "On" }, { value: "off", label: "Off" }] }),
        h("div", { class: "field-hint" }, "When on, a run of this task waits and retries after a provider usage limit, within the project's limits. It never works around a limit.")));
  }

  function save() {
    try {
      sessionStorage.setItem(DRAFT, JSON.stringify({ project: projectId, title: title.value, description: description.value, roles,
        acceptance: [...criteria.querySelectorAll("input")].map((input) => input.value),
        overrides: Object.fromEntries(Object.entries(numbers).map(([key, control]) => [key, control.input.value])) }));
    } catch { /* storage disabled */ }
  }

  function collect() {
    let first = null;
    const fail = (item, message, target) => { item.setError(message); first = first || target; };
    fields.title.setError(""); fields.description.setError("");
    if (!title.value.trim()) fail(fields.title, "Give the task a title", title);
    if (!description.value.trim()) fail(fields.description, "Describe what should be done", description);
    const workflow = {};
    for (const [key, control] of Object.entries(numbers)) {
      const wrap = control.closest(".field");
      const error = wrap.querySelector(".field-error");
      error.textContent = "";
      if (!control.input.value.trim()) continue;
      try { workflow[key] = control.read(); } catch (problem) { error.textContent = problem.message; first = first || control.input; page.querySelector("details.advanced").open = true; }
    }
    if (first) { first.focus(); return null; }
    const overrides = {};
    if (Object.keys(workflow).length) overrides.workflow = workflow;
    if (recovery !== "default") overrides.recovery = { enabled: recovery === "on" };
    return { title: title.value.trim(), description: description.value.trim(), ...roles, request_id: id,
      acceptance: [...criteria.querySelectorAll("input")].map((input) => input.value.trim()).filter(Boolean),
      overrides: Object.keys(overrides).length ? overrides : undefined };
  }

  async function submit(el, run) {
    const payload = collect();
    if (!payload) return;
    formError.textContent = "";
    await busy(el, async () => {
      try {
        const created = await api.post(`/api/projects/${projectId}/tasks`, payload);
        try { sessionStorage.removeItem(DRAFT); } catch { /* storage disabled */ }
        navigate("tasks", projectId, created.id, run ? { run: 1 } : {});
      } catch (error) {
        clear(formError, icon("alert", "sm"), error.message);
      }
    });
  }

  const create = button("Create task", { onClick: () => submit(create, false) });
  const createRun = button("Create and run…", { variant: "primary", iconName: "play", tip: "You confirm before any agent is called", onClick: () => submit(createRun, true) });
  page.append(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "New task"),
      h("p", {}, "Creating a task makes a dedicated Git worktree and branch. No agent is called until you run it."))),
    h("form", { class: "stack form", novalidate: true, onsubmit: (event) => { event.preventDefault(); submit(createRun, true); } },
      h("div", { class: "card pad stack" }, fields.project, repoNote, fields.title, fields.description,
        h("div", { class: "field" }, h("div", { class: "field-label" }, "Acceptance criteria", h("span", { class: "optional" }, "optional")), criteria,
          h("div", { class: "row" }, button("Add criterion", { size: "sm", variant: "ghost", iconName: "plus", onClick: () => { const row = criterion(); criteria.append(row); row.querySelector("input").focus(); } })),
          h("div", { class: "field-hint" }, "One per line; both agents see them. Press Enter to add the next one."))),
      h("div", { class: "card pad stack" }, h("h2", {}, "Agents"), roleHost, roleNote),
      h("details", { class: "card advanced" }, h("summary", {}, h("span", { class: "grow" }, h("span", { class: "field-label" }, "Advanced"),
        h("span", { class: "field-hint" }, "Limits for this task only. Leave empty to use the project's settings.")), icon("chevronDown")), h("div", { class: "card-body" }, advancedHost)),
      h("div", { class: "form-foot" }, formError, button("Cancel", { variant: "ghost", href: href("tasks") }), create, createRun)));
  for (const input of [title, description]) input.addEventListener("input", save);
  roleControls();
  advanced();

  function update() {
    const project = state.projects.get(projectId);
    fields.project.hidden = state.projects.size < 2;
    memo(repoNote, [project?.repo, project?.config_error, state.projects.size, project?.name], () => [
      state.projects.size < 2 && project ? h("div", { class: "row wrap muted" }, icon("folder", "sm"), h("span", {}, "In "), h("strong", {}, project.name),
        project.repo?.branch ? h("span", { class: "chip" }, icon("git", "sm"), project.repo.branch) : null) : null,
      project?.repo?.dirty ? callout({ tone: "warn", iconName: "alert", title: "The repository has uncommitted changes",
        body: "A task starts from a clean checkout so that its worktree matches a commit. Commit or stash your changes first; creating the task will be refused otherwise." }) : null,
      project?.config_error ? callout({ tone: "danger", iconName: "alert", title: "This project's configuration cannot be used", body: project.config_error }) : null,
    ]);
    if (memo(advancedHost, [project?.config, state.about?.limits], () => [])) advanced();
    if (memo(roleNote, [state.providers?.providers, roles], () => [])) roleHints();
  }

  return { update, destroy() {} };
}
