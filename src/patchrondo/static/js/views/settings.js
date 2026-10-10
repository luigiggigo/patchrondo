// Settings: global preferences and everything in a project's config.json, validated by the backend.

import { api } from "../api.js";
import { clear, h, icon, memo } from "../dom.js";
import { ago, duration, fullDate, plural } from "../format.js";
import { href, navigate, replace } from "../router.js";
import { emptyState } from "../rondo.js";
import { projectList, refresh, selectedProjectId, state } from "../store.js";
import { AGENTS, agentName } from "../tasks.js";
import { badge, busy, button, callout, confirm, copyButton, loadingBlock, numberInput, segmented, selectInput, toast, toastError, toggle } from "../ui.js";
import { importDialog, removeProject, renameDialog } from "./projects.js";

const SECTIONS = [
  ["general", "General", "settings"], ["agents", "Agents", "user"], ["workflow", "Workflow", "repeat"], ["tests", "Tests", "flask"],
  ["recovery", "Recovery", "clock"], ["rag", "RAG", "database"], ["advanced", "Advanced", "code"],
];
const PROJECT_ONLY = new Set(["workflow", "tests", "recovery", "rag"]);

function row(label, hint, control, { block = false } = {}) {
  return h("div", { class: `setting${block ? " block" : ""}` },
    h("div", { class: "setting-text" }, h("div", { class: "field-label" }, label), hint ? h("div", { class: "field-hint" }, hint) : null),
    h("div", { class: "setting-control" }, control));
}

function readOnly(value, copy = true) {
  return h("div", { class: "row wrap readonly" }, h("span", { class: "mono break" }, value || "—"), copy && value ? copyButton(value, "Copy") : null);
}

// A card of settings with one Save button. `read()` returns the values to send, or throws
// after marking the offending field; `save(values)` performs the request.
function formCard({ title, intro, rows, read, save, onDirty }) {
  const error = h("div", { class: "form-error", role: "alert" });
  const saveButton = button("Save changes", { variant: "primary", disabled: true, onClick: () => submit() });
  const discard = button("Discard", { variant: "ghost", disabled: true, onClick: () => onDirty(false, true) });
  const card = h("section", { class: "card settings-card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, title), intro ? h("div", { class: "field-hint" }, intro) : null)),
    h("div", {}, rows),
    h("div", { class: "settings-foot" }, error, discard, saveButton));
  const markDirty = () => { saveButton.disabled = discard.disabled = false; onDirty(true); };
  card.addEventListener("input", markDirty);
  card.addEventListener("change", markDirty);
  async function submit() {
    error.textContent = "";
    for (const stale of card.querySelectorAll(".field-error")) stale.textContent = "";
    let values;
    try { values = read(); } catch (problem) {
      clear(error, icon("alert", "sm"), problem.message);
      problem.target?.focus?.();
      return;
    }
    await busy(saveButton, async () => {
      try {
        await save(values);
        saveButton.disabled = discard.disabled = true;
        onDirty(false);
        toast("Settings saved");
      } catch (failure) {
        clear(error, icon("alert", "sm"), failure.message);
      }
    });
  }
  return card;
}

// Number rows share reading and range errors; ranges come from the backend.
function numberRows(specs, values, limits) {
  const controls = {};
  const rows = specs.map(([key, label, hint, unit]) => {
    const [min, max] = limits[key] || [0, 1e9];
    controls[key] = numberInput({ value: values[key], min, max, unit, label });
    const problem = h("div", { class: "field-error", role: "alert" });
    controls[key].problem = problem;
    return row(label, `${hint} Allowed: ${min}–${max}${unit ? ` ${unit}` : ""}${unit === "seconds" ? ` (now ${duration(values[key])})` : ""}.`, [controls[key], problem]);
  });
  const read = () => {
    const out = {};
    let first = null;
    for (const [key, control] of Object.entries(controls)) {
      try { out[key] = control.read(); } catch (problem) { control.problem.textContent = problem.message; first = first || control.input; }
    }
    if (first) throw Object.assign(new Error("Some values are outside their allowed range."), { target: first });
    return out;
  };
  return { rows, read, controls };
}

export default function mount(root, route, ctx) {
  let scope = route.scope, section = route.section;
  let detail = null, detailFor = null, loading = false, failure = null, dirty = false, destroyed = false;
  const el = { scope: h("div", {}), nav: h("nav", { class: "settings-nav", "aria-label": "Settings sections" }), body: h("div", { class: "stack settings-body" }) };
  root.append(h("div", { class: "page" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Settings"), h("p", {}, "Global preferences apply everywhere. Each project keeps its own workflow, tests, recovery and retrieval settings.")), el.scope),
    h("div", { class: "settings-layout" }, el.nav, el.body)));
  // Registered only while a form has unsaved edits: the browser then asks before closing the tab.
  const guard = (event) => { event.preventDefault(); event.returnValue = ""; };

  const project = () => (scope !== "global" ? state.projects.get(scope) : null);
  const setDirty = (value, reset = false) => {
    dirty = value;
    window[value ? "addEventListener" : "removeEventListener"]("beforeunload", guard);
    if (reset) { body.sig = ""; renderBody(); }
  };
  const body = { sig: "" };

  async function load(force = false) {
    const current = project();
    if (!current) { detail = null; detailFor = null; return; }
    if (loading || (!force && detailFor === current.id)) return;
    loading = true;
    try {
      const data = await api.get(`/api/projects/${current.id}`);
      if (destroyed) return;
      detail = data; detailFor = current.id; failure = null;
    } catch (error) { failure = error; detailFor = current.id; detail = null; }
    loading = false;
    body.sig = "";
    renderBody();
  }

  const patchConfig = async (values) => {
    const data = await api.patch(`/api/projects/${scope}/config`, values);
    detail = { ...detail, settings: data };
    await refresh();
  };
  const patchSettings = async (values) => {
    try { await api.patch("/api/settings", values); await refresh(); toast("Saved"); } catch (error) { toastError(error); }
  };

  // --- Global sections ----------------------------------------------------------------

  function globalGeneral() {
    const settings = state.settings || {};
    return [h("section", { class: "card settings-card" }, h("div", { class: "card-head" }, h("h2", {}, "General")),
      row("Theme", "Dark is the main theme; light and system are available.", segmented({ label: "Theme", value: settings.theme || "dark",
        options: [{ value: "dark", label: "Dark", icon: "moon" }, { value: "light", label: "Light", icon: "sun" }, { value: "system", label: "System" }],
        onChange: (value) => ctx.setTheme(value) })),
      row("Selected project", "What the Home and Tasks pages show when PatchRondo opens.", selectInput(
        [{ value: "", label: "All projects" }, ...projectList().map((item) => ({ value: item.id, label: item.name }))], selectedProjectId() || "",
        { "aria-label": "Selected project", onchange: (event) => ctx.selectProject(event.target.value || null) })),
      row("State directory", "Where PatchRondo keeps settings, the project registry, tasks, worktrees and logs. Chosen with --home or PATCHRONDO_HOME; never inside a repository.",
        readOnly(state.about?.home), { block: true }))];
  }

  function providerCard(provider) {
    if (!provider) return h("div", { class: "card pad" }, loadingBlock("Checking the CLI…"));
    let tone = "neutral", label = "Login not confirmed";
    if (provider.installed === false) [tone, label] = ["warn", "Not installed"];
    else if (provider.authenticated === false) [tone, label] = ["warn", "Login needed"];
    else if (provider.authenticated) [tone, label] = provider.warning ? ["warn", "Check billing"] : ["ok", "Ready"];
    return h("div", { class: "card pad stack provider-detail" },
      h("div", { class: "row" }, h("span", { class: `agent-mark ${provider.name}`, "aria-hidden": "true" }, provider.label.charAt(0)),
        h("h3", { class: "grow" }, provider.label), badge(label, tone, { dot: true })),
      h("dl", { class: "kv tight" },
        h("dt", {}, "Executable"), h("dd", { class: "mono" }, provider.path || "Not found on PATH"),
        h("dt", {}, "Version"), h("dd", {}, provider.version || "—"),
        h("dt", {}, "Login"), h("dd", {}, provider.authenticated ? `Confirmed${provider.auth_method ? ` · ${provider.auth_method}` : ""}` : provider.authenticated === false ? "Not confirmed" : "Not checked")),
      provider.warning ? callout({ tone: "warn", iconName: "alert", title: provider.warning,
        body: "If an API key variable is set, the CLI may bill the API instead of your subscription. PatchRondo removes common key variables from the processes it starts, but cannot change your CLI settings." }) : null,
      provider.hint ? callout({ tone: "accent", iconName: "info", title: "How to fix it", body: provider.hint,
        actions: [provider.installed ? copyButton(provider.login_command, "Copy login command") : null,
          h("a", { class: "link", href: provider.docs, target: "_blank", rel: "noopener noreferrer" }, "Official documentation")] }) : null);
  }

  function globalAgents() {
    const view = state.providers || {};
    const defaults = state.settings?.defaults || {};
    const options = Object.entries(AGENTS).map(([value, label]) => ({ value, label }));
    const check = button("Check again", { iconName: "refresh", onClick: () => busy(check, async () => {
      try { await api.post("/api/providers/refresh", {}); toast("Checking the CLIs…"); } catch (error) { toastError(error); }
    }) });
    return [
      h("div", { class: "section-head" }, h("div", { class: "muted" }, view.checking ? "Checking the CLIs…" : view.checked_at ? `Checked ${ago(view.checked_at)}. These checks never call a model.` : ""), check),
      h("div", { class: "grid c2" }, ["claude", "codex"].map((name) => providerCard(view.providers?.[name]))),
      h("section", { class: "card settings-card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Default roles"),
        h("div", { class: "field-hint" }, "Pre-selected for new tasks. A project can set its own, and every task can still choose."))),
        row("Developer", "Writes the changes in the task worktree.", segmented({ label: "Default developer", value: defaults.developer, options,
          onChange: (value) => patchSettings({ defaults: { developer: value } }) })),
        row("Reviewer", "Reviews read-only and returns a verdict.", segmented({ label: "Default reviewer", value: defaults.reviewer, options,
          onChange: (value) => patchSettings({ defaults: { reviewer: value } }) }))),
      callout({ tone: "neutral", iconName: "shield", title: "PatchRondo uses the official CLIs and your own login",
        body: "It never reads or stores passwords, OAuth tokens or API keys, and it finds the CLIs through PATH: a custom executable path cannot be set from the browser. Every run uses the usage limits of your own plan." }),
    ];
  }

  function globalAdvanced() {
    return [h("section", { class: "card settings-card" }, h("div", { class: "card-head" }, h("h2", {}, "Advanced")),
      row("Import a 0.1 state directory", "Use a state directory created by PatchRondo 0.1 (another --home) as a project. Nothing is moved or rewritten.",
        button("Import…", { iconName: "database", onClick: () => importDialog() })),
      row("Guided setup", "Open the first-run setup again.", button("Open setup", { href: href("welcome") })),
      row("Command line", "Every command of 0.1 still works. Commands accept --project when the home holds several projects.",
        readOnly("patchrondo --help", true)))];
  }

  function needsProject() {
    const projects = projectList();
    if (!projects.length) return emptyState({ compact: true, mood: "welcome", title: "No projects yet", body: "These settings belong to a project. Add one first.",
      action: button("Go to Projects", { variant: "primary", href: href("projects") }) });
    return h("div", { class: "empty compact" }, icon("folder", "lg faint"), h("h2", {}, "Choose a project"),
      h("p", {}, `${SECTIONS.find(([key]) => key === section)[1]} settings are kept per project, in its own config.json.`),
      h("div", { class: "row wrap" }, projects.map((item) => button(item.name, { onClick: () => navigate("settings", item.id, section) }))));
  }

  // --- Project sections ---------------------------------------------------------------

  function projectGeneral(current) {
    return [h("section", { class: "card settings-card" }, h("div", { class: "card-head" }, h("h2", {}, "General")),
      row("Display name", "Shown in PatchRondo only; the repository folder keeps its name.", h("div", { class: "row" }, h("strong", {}, current.name), button("Rename…", { size: "sm", onClick: () => renameDialog(current) }))),
      row("Repository", "The Git repository tasks branch from. It cannot be changed: task worktrees belong to it.", readOnly(current.repository), { block: true }),
      row("Reference branch", "New tasks start from the commit checked out here when they are created.",
        h("div", { class: "row wrap" }, current.repo?.branch ? h("span", { class: "chip" }, icon("git", "sm"), current.repo.branch, h("span", { class: "faint mono" }, current.repo.head || "")) : h("span", { class: "muted" }, current.repo?.error || "Unknown"),
          current.repo?.dirty ? badge("Uncommitted changes", "warn") : null,
          button("Refresh", { size: "sm", variant: "ghost", iconName: "refresh", onClick: () => load(true).then(refresh) }))),
      row("State directory", current.origin === "managed" ? "This project's configuration, tasks, worktrees and index."
        : "A PatchRondo 0.1 state directory, used where it is.", readOnly(current.home), { block: true }))];
  }

  function projectAgents(current, settings) {
    const globalDefaults = state.settings?.defaults || {};
    const values = { developer: settings.agents.developer || "", reviewer: settings.agents.reviewer || "" };
    const make = (role) => segmented({ label: `Project ${role}`, value: values[role], onChange: (value) => { values[role] = value; },
      options: [{ value: "", label: `Global default (${agentName(globalDefaults[role])})` }, ...Object.entries(AGENTS).map(([value, label]) => ({ value, label }))] });
    return [formCard({ title: "Default roles for this project", intro: "Used to pre-select the agents of new tasks in this project.", onDirty: setDirty,
      rows: [row("Developer", "Writes the changes.", make("developer")), row("Reviewer", "Reviews read-only.", make("reviewer"))],
      read: () => ({ agents: { developer: values.developer || null, reviewer: values.reviewer || null } }),
      save: async (payload) => patchConfig({ agents: payload.agents.developer || payload.agents.reviewer ? payload.agents : null }) }),
      h("p", { class: "muted" }, "CLI status and login are the same for every project: ", h("a", { class: "link", href: href("settings", "global", "agents") }, "open global agent settings"), ".")];
  }

  function projectWorkflow(current, settings) {
    const numbers = numberRows([
      ["max_iterations", "Max iterations", "Develop–review rounds before a task is blocked.", ""],
      ["agent_timeout_seconds", "Agent timeout", "Longest single call to Claude Code or Codex.", "seconds"],
      ["test_timeout_seconds", "Test timeout", "Longest single test command.", "seconds"],
      ["claude_max_turns", "Claude max turns", "Passed to Claude Code as --max-turns for each call.", "turns"],
    ], settings.workflow, settings.limits.workflow);
    const noChanges = toggle({ checked: settings.workflow.allow_no_changes, label: "Allow completion without changes" });
    return [formCard({ title: "Workflow", intro: "How long and how many times the loop may run. Tasks can override the first three when they are created.", onDirty: setDirty,
      rows: [...numbers.rows, row("Allow completion without changes", "Off by default: a task whose worktree is unchanged is not marked done, even if approved.", noChanges)],
      read: () => ({ workflow: { ...numbers.read(), allow_no_changes: noChanges.checked } }), save: patchConfig })];
  }

  function projectTests(current, settings) {
    const tests = settings.tests;
    const enabled = toggle({ checked: tests.enabled, label: "Run project tests" });
    const commands = h("textarea", { class: "input mono", rows: 5, spellcheck: "false", "aria-label": "Test commands",
      placeholder: "python -m pytest -q\nruff check ." });
    commands.value = tests.command_lines.join("\n");
    const trust = h("input", { type: "checkbox", class: "check", checked: tests.enabled && tests.trust_acknowledged });
    // Consent covers the commands as shown: any edit asks for it again.
    const untick = () => { trust.checked = false; };
    commands.addEventListener("input", untick);
    enabled.addEventListener("change", untick);
    const quoting = settings.quoting === "windows"
      ? "Windows rules: put double quotes around an argument that contains spaces; single quotes are literal characters."
      : "POSIX quoting rules: quote arguments that contain spaces.";
    return [
      callout({ tone: "warn", iconName: "shield", title: "Tests run on your computer with your permissions",
        body: "They are not sandboxed. A malicious repository can run any code through its test suite. Enable tests only for repositories you trust; for unfamiliar code run PatchRondo inside a VM or container." }),
      formCard({ title: "Project tests", intro: "A task is done only when these pass and the review approves. Without tests a task is developed and reviewed once, then pauses.", onDirty: setDirty,
        rows: [
          row("Run project tests", "After each development step, in the task worktree.", enabled),
          row("Commands", `One command per line, run in order without a shell; the first failure stops the rest. ${quoting}`, commands, { block: true }),
          h("label", { class: "setting check-row" }, trust, h("span", { class: "setting-text" }, h("span", { class: "field-label" }, "I trust this repository"),
            h("span", { class: "field-hint" }, "I understand these commands run automatically on my computer, with my permissions, against code written by the agents. Required to enable tests, and asked again whenever the commands change."))),
        ],
        read: () => {
          if (enabled.checked && !commands.value.trim()) throw Object.assign(new Error("Enter at least one command, or turn tests off."), { target: commands });
          if (enabled.checked && !trust.checked) throw Object.assign(new Error("Tick “I trust this repository” to enable tests."), { target: trust });
          return { tests: { enabled: enabled.checked, trust_acknowledged: trust.checked, commands: commands.value.split("\n") } };
        },
        save: patchConfig }),
    ];
  }

  function projectRecovery(current, settings) {
    const recovery = settings.recovery;
    const enabled = toggle({ checked: recovery.enabled, label: "Automatic quota recovery" });
    const numbers = numberRows([
      ["max_consecutive_retries", "Max consecutive retries", "Retries without progress before recovery stops.", "retries"],
      ["initial_backoff_seconds", "Initial backoff", "Wait before the first retry when the provider states no usable reset time; doubles each time.", "seconds"],
      ["max_backoff_seconds", "Max backoff", "Longest single backoff wait. Not below the initial backoff.", "seconds"],
      ["max_total_wait_seconds", "Max total wait", "Longest time from the first failure of a streak to a planned retry. A later reset stops recovery instead of being brought forward.", "seconds"],
      ["reset_safety_margin_seconds", "Reset safety margin", "Added to a reset time stated by the provider.", "seconds"],
    ], recovery, settings.limits.recovery);
    return [
      formCard({ title: "Recovery Manager", intro: "What a run does after a provider usage limit. Off by default; when off, a limit pauses the task until you resume it.", onDirty: setDirty,
        rows: [
          row("Automatic quota recovery", "Default for runs of this project. A run can still be started with or without it.", enabled),
          row("Retry only usage limits", "Fixed: logins, timeouts, failed reviews and every other failure always wait for you.", toggle({ checked: true, disabled: true, label: "Retry only usage limits" })),
          ...numbers.rows,
        ],
        read: () => {
          const values = numbers.read();
          if (values.max_backoff_seconds < values.initial_backoff_seconds) {
            numbers.controls.max_backoff_seconds.problem.textContent = "Must not be below the initial backoff";
            throw Object.assign(new Error("Max backoff must not be below the initial backoff."), { target: numbers.controls.max_backoff_seconds.input });
          }
          return { recovery: { enabled: enabled.checked, ...values } };
        },
        save: patchConfig }),
      callout({ tone: "neutral", iconName: "info", title: "There is no background service",
        body: "Recovery works only while the run process that met the limit stays alive: it waits without holding the task lock and retries by itself. If that process ends, the plan stays saved and nothing retries until you resume. Each retry is an ordinary CLI call that uses your plan quota; PatchRondo only waits and never works around a limit." }),
    ];
  }

  function indexCard(current) {
    const index = detail.index || {};
    const last = index.last;
    const run = button(index.running ? "Indexing…" : "Update index now", { iconName: "refresh", disabled: index.running, onClick: () => busy(run, async () => {
      try { await api.post(`/api/projects/${current.id}/index`, {}); toast("Indexing started"); await load(true); } catch (error) { toastError(error); }
    }) });
    return h("section", { class: "card settings-card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Index"),
      h("div", { class: "field-hint" }, "The index is a rebuildable cache in the project's state directory. Runs update it by themselves; this updates it for the repository now, without any model call.")), run),
      h("div", { class: "card-body stack" },
        h("dl", { class: "kv" },
          h("dt", {}, "Status"), h("dd", {}, index.running ? badge("Indexing…", "accent", { dot: true, live: true }) : !last ? "Never updated from here" : last.status === "ok" ? badge("Up to date", "ok", { dot: true }) : badge("Failed", "danger", { dot: true })),
          last ? [h("dt", {}, "Last manual update"), h("dd", {}, `${fullDate(last.finished_at)} · ${ago(last.finished_at)}`)] : null,
          last?.stats ? [h("dt", {}, "Result"), h("dd", {}, `${plural(last.stats.files, "file")} indexed · ${last.stats.updated} updated · ${last.stats.removed} removed · ${last.stats.chunks_added} new chunks`)] : null,
          index.database?.files != null ? [h("dt", {}, "Files in the index"), h("dd", {}, `${index.database.files} for this repository`)] : null,
          h("dt", {}, "Database"), h("dd", { class: "mono" }, index.database?.exists ? index.database.path : "Not created yet")),
        last?.status === "error" ? callout({ tone: "danger", iconName: "alert", title: "The last update failed", body: last.error }) : null));
  }

  function projectRag(current, settings) {
    const enabled = toggle({ checked: settings.rag.enabled, label: "Local retrieval" });
    const numbers = numberRows([
      ["max_chunks", "Max excerpts", "Excerpts added to each developer and reviewer prompt.", "excerpts"],
      ["max_chars", "Max characters", "Upper bound of the added prompt text.", "characters"],
    ], settings.rag, settings.limits.rag);
    return [formCard({ title: "Local retrieval (RAG)", intro: "Before each prompt PatchRondo searches a local index of the task worktree and adds the best-matching excerpts. No model, network service or extra dependency is used.", onDirty: setDirty,
      rows: [row("Local retrieval", "Excerpts are hints; the task, its state and the test results stay authoritative.", enabled), ...numbers.rows],
      read: () => ({ rag: { enabled: enabled.checked, ...numbers.read() } }), save: patchConfig }), indexCard(current)];
  }

  function projectAdvanced(current, settings) {
    const raw = JSON.stringify({ repository: settings.repository, workflow: settings.workflow, tests: { enabled: settings.tests.enabled,
      trust_acknowledged: settings.tests.trust_acknowledged, commands: settings.tests.commands }, rag: settings.rag, recovery: settings.recovery,
      ...(Object.keys(settings.agents).length ? { agents: settings.agents } : {}) }, null, 2);
    return [
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Configuration in effect"),
        h("div", { class: "field-hint" }, ["Read from ", h("span", { class: "mono" }, `${current.home}/config.json`), " with defaults filled in. Edit it through the sections on the left."])), copyButton(raw, "Copy JSON")),
        h("pre", { class: "log-chunk raw" }, raw)),
      h("section", { class: "card settings-card" }, h("div", { class: "card-head" }, h("h2", {}, "Remove")),
        row("Remove this project from PatchRondo", "Only the entry in PatchRondo's list is removed. The repository, the tasks, the worktrees and the logs stay on disk.",
          button("Remove project…", { variant: "danger", iconName: "trash", onClick: async () => { await removeProject(current); if (!state.projects.has(current.id)) navigate("projects"); } }))),
    ];
  }

  // --- Rendering ----------------------------------------------------------------------

  function renderBody() {
    const current = project();
    const sig = JSON.stringify([scope, section, current, scope === "global" ? [state.settings, state.providers, state.projects.size, state.about?.home] : null,
      detailFor, detail?.settings, failure?.message, detail?.index]);
    if (sig === body.sig || dirty) return; // never rebuild a form that has unsaved edits
    const focused = el.body.contains(document.activeElement) ? document.activeElement : null;
    if (focused?.matches("input:not(.switch), textarea, select") && body.sig) return; // nor one that is being typed in
    const group = focused?.closest("[role=radiogroup]")?.getAttribute("aria-label");
    body.sig = sig;
    let content;
    if (scope !== "global" && !current) {
      content = emptyState({ compact: true, title: "This project is not registered", body: "It may have been removed.", action: button("Global settings", { href: href("settings") }) });
    } else if (scope === "global") {
      content = PROJECT_ONLY.has(section) ? needsProject() : { general: globalGeneral, agents: globalAgents, advanced: globalAdvanced }[section]?.() || globalGeneral();
    } else if (failure || current.config_error) {
      content = callout({ tone: "danger", iconName: "alert", title: "This project's configuration cannot be used",
        body: [failure?.message || current.config_error, h("div", {}, "Fix ", h("span", { class: "mono break" }, `${current.home}/config.json`), " in an editor; PatchRondo does not overwrite a file it cannot read.")] });
      if (section === "general") content = [content, ...projectGeneral(current)];
    } else if (!detail || detailFor !== current.id || !detail.settings) {
      content = loadingBlock("Loading the project settings…");
    } else {
      const builder = { general: projectGeneral, agents: projectAgents, workflow: projectWorkflow, tests: projectTests, recovery: projectRecovery,
        rag: projectRag, advanced: projectAdvanced }[section] || projectGeneral;
      content = builder(current, detail.settings);
    }
    clear(el.body, content);
    // Choosing an option saves and redraws at once: keep the keyboard where it was.
    if (group) [...el.body.querySelectorAll("[role=radiogroup]")].find((item) => item.getAttribute("aria-label") === group)?.querySelector('[aria-checked="true"]')?.focus();
  }

  function update() {
    const current = project();
    memo(el.scope, [scope, projectList().map((item) => [item.id, item.name])], () => h("label", { class: "row scope" }, h("span", { class: "muted" }, "Editing"),
      selectInput([{ value: "global", label: "Global preferences" }, ...projectList().map((item) => ({ value: item.id, label: `Project · ${item.name}` }))], scope,
        { "aria-label": "Settings scope", onchange: (event) => navigate("settings", event.target.value, section) })));
    memo(el.nav, [section, scope], () => SECTIONS.map(([key, label, iconName]) => h("a", { class: "nav-item", href: href("settings", scope, key),
      "aria-current": key === section ? "page" : null }, icon(iconName), h("span", {}, label),
      scope === "global" && PROJECT_ONLY.has(key) ? h("span", { class: "count", "data-tip": "Per project" }, "project") : null)));
    ctx.setTitle([{ label: "Settings", href: href("settings") }, { label: current ? current.name : "Global" }, { label: SECTIONS.find(([key]) => key === section)?.[1] || "General" }]);
    if (current && detailFor !== current.id) load();
    // The index status changes while this page is open.
    if (current && detail && section === "rag" && JSON.stringify(current.index) !== JSON.stringify({ running: detail.index?.running, last: detail.index?.last })) load(true);
    renderBody();
  }

  async function changeRoute(next) {
    const wantedScope = next.scope, wantedSection = SECTIONS.some(([key]) => key === next.section) ? next.section : "general";
    if (wantedScope === scope && wantedSection === section) return;
    if (dirty && !(await confirm({ title: "Discard unsaved changes?", body: h("p", {}, "The changes on this page have not been saved."), confirmLabel: "Discard", variant: "danger" }))) {
      replace("settings", scope, section);
      return;
    }
    setDirty(false);
    scope = wantedScope;
    section = wantedSection;
    body.sig = "";
    update();
  }

  if (!SECTIONS.some(([key]) => key === section)) section = "general";
  if (scope !== "global" && !state.projects.has(scope)) scope = "global";
  return { update, route: changeRoute, tick: () => { if (!dirty && !el.body.contains(document.activeElement) && ["agents", "rag"].includes(section)) { body.sig = ""; renderBody(); } }, destroy() {
    destroyed = true;
    window.removeEventListener("beforeunload", guard);
    if (dirty) toast("Unsaved settings were discarded", { tone: "warn" });
  } };
}
