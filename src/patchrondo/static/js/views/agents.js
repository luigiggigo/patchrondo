// Agents: the model and the CLI options of each role, at three levels (global, project, task).
// The page draws what the backend describes: the settings a CLI offers, their limits, which
// are unavailable and why, and where each value in effect comes from. Nothing is decided
// here: the backend validates every save, and a run reads the same files.

import { api } from "../api.js";
import { clear, h, icon } from "../dom.js";
import { ago, fullDate } from "../format.js";
import { href, navigate, replace } from "../router.js";
import { emptyState, rondoNote } from "../rondo.js";
import { projectList, refresh, state } from "../store.js";
import { AGENTS, agentName } from "../tasks.js";
import { badge, busy, button, callout, confirm, copyButton, dialog, iconButton, loadingBlock, popover, menuItem, segmented, selectInput, toast, toastError, toggle } from "../ui.js";

const ROLES = { developer: ["Developer", "Writes the changes in the task's own copy of the project."],
  reviewer: ["Reviewer", "Reads the result and answers with a verdict. Always read-only."] };
const SOURCES = { default: "PatchRondo default", global: "Global", project: "Project", task: "This task" };
const LEVELS = { global: "global preferences", project: "this project", task: "this task" };
const CHECKS = { passed: ["check", "ok"], warning: ["alert", "warn"], failed: ["x", "danger"], skipped: ["info", "neutral"] };
const CATALOG_SOURCES = { cli: "from the installed CLI's catalog", bundled: "from the catalog shipped with the installed CLI",
  documented: "named by the documentation", none: "no list is available; enter a model ID" };
const OPEN = "patchrondo.agents.advanced";
const has = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
const copy = (value) => JSON.parse(JSON.stringify(value));
const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

// Whether a value of a setting widens what the agent can touch, from the facts the backend sends with the setting.
function risky(field, value) {
  if (!field.risk || value == null || value === "" || value === false || (Array.isArray(value) && !value.length)) return false;
  if (field.risky_values) return field.risky_values.includes(value);
  if (field.safe_items) return value.some((item) => !field.safe_items.includes(item));
  if (field.safe_keys) return value.some((pair) => !field.safe_keys.includes(pair.key));
  return true;
}

function cliState(provider) {
  if (!provider) return ["neutral", "Checking…"];
  if (provider.installed === false) return ["warn", "Not installed"];
  if (provider.authenticated === false) return ["warn", "Login needed"];
  if (provider.authenticated) return provider.warning ? ["warn", "Check billing"] : ["ok", "Ready"];
  return ["neutral", "Login not confirmed"];
}

function summarize(values) {
  return Object.entries(values).map(([name, block]) => `${agentName(name)}: ${Object.entries(block).map(([key, value]) =>
    `${key.replace(/_/g, " ")} ${value == null ? "CLI default" : value}`).join(", ")}`).join(" · ");
}

// "key = value" lines of the configuration overrides; values are text, numbers or true/false.
function parsePairs(text) {
  return text.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
    const cut = line.indexOf("=");
    if (cut < 1) throw new Error(`“${line}” is not key = value`);
    const raw = line.slice(cut + 1).trim();
    let value = raw;
    if (raw === "true" || raw === "false") value = raw === "true";
    else if (/^-?\d+(\.\d+)?$/.test(raw)) value = Number(raw);
    else if (raw.length > 1 && raw.startsWith('"') && raw.endsWith('"')) value = raw.slice(1, -1);
    return { key: line.slice(0, cut).trim(), value };
  });
}
const showPairs = (pairs) => (pairs || []).map((pair) => `${pair.key} = ${typeof pair.value === "string" ? `"${pair.value}"` : pair.value}`).join("\n");

export default function mount(root, route, ctx) {
  let scope = { project: route.project, task: route.task };
  let view = null, failure = null, ticket = 0, destroyed = false, seen = "";
  const drafts = {};
  const el = { scope: h("div", { class: "row wrap" }), note: h("div", {}), problems: h("div", { class: "stack" }),
    cards: { developer: h("div", { class: "agent-slot" }), reviewer: h("div", { class: "agent-slot" }) }, presets: h("div", {}) };
  const body = h("div", { class: "stack" });
  root.append(h("div", { class: "page" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Agents"),
      h("p", {}, "Choose the model and the options of each role. Global preferences apply everywhere; a project or a single task can override them.")), el.scope),
    body));
  const guard = (event) => { event.preventDefault(); event.returnValue = ""; };
  const dirty = () => Object.values(drafts).some((draft) => draft.dirty);
  const watch = () => window[dirty() ? "addEventListener" : "removeEventListener"]("beforeunload", guard);
  const query = () => { const parts = new URLSearchParams(); if (scope.project) parts.set("project", scope.project); if (scope.task) parts.set("task", scope.task); return parts.toString(); };
  const where = () => ({ ...(scope.project ? { project: scope.project } : {}), ...(scope.task ? { task: scope.task } : {}) });

  function makeDraft(role) {
    const info = view.roles[role];
    const own = { claude: copy(info.blocks.claude.own), codex: copy(info.blocks.codex.own) };
    const ack = {};
    for (const name of Object.keys(own)) { ack[name] = own[name].risk_acknowledged === true; delete own[name].risk_acknowledged; }
    return { own, ack, provider: info.provider, chosen: info.provider_own || "", touched: new Set(), agent: false, dirty: false, tests: null,
      error: "", custom: false, parse: {} };
  }

  // Only the answer to the latest request is used: one asked for another level, or overtaken, is dropped.
  async function load(force = false) {
    const mine = ++ticket;
    try {
      const next = await api.get(`/api/agents${query() ? `?${query()}` : ""}`);
      if (destroyed || mine !== ticket) return;
      view = next; failure = null;
      for (const role of Object.keys(ROLES)) if (force || !drafts[role] || !drafts[role].dirty) drafts[role] = makeDraft(role);
    } catch (error) {
      if (destroyed || mine !== ticket) return;
      failure = error; view = null;
    }
    draw();
  }

  // --- one role ---------------------------------------------------------------------

  function roleCard(role) {
    const draft = drafts[role], info = view.roles[role];
    const provider = draft.provider, offered = view.providers[provider];
    const fields = offered.schema[role], saved = info.blocks[provider], own = draft.own[provider];
    const inherited = saved.inherited, catalog = offered.catalog;
    const locked = view.locked ? "This task is running or finished: its settings cannot be changed now." : null;
    const value = (field) => (has(own, field.key) ? own[field.key] ?? field.default : inherited.values[field.key]);
    const source = (field) => (has(own, field.key) ? view.level : inherited.sources[field.key]);
    const byKey = Object.fromEntries(fields.map((field) => [field.key, field]));
    const error = h("div", { class: "form-error", role: "alert" }, draft.error ? [icon("alert", "sm"), draft.error] : null);
    const applyButton = button("Apply", { variant: "primary", disabled: !draft.dirty || Boolean(locked), onClick: () => apply(role, applyButton), attrs: { "data-key": "apply" } });
    const discard = button("Discard", { variant: "ghost", disabled: !draft.dirty, onClick: () => { drafts[role] = makeDraft(role); watch(); draw(); } });
    const touch = (redraw = true) => {
      draft.touched.add(provider); draft.dirty = true; draft.tests = null; draft.error = "";
      applyButton.disabled = Boolean(locked); discard.disabled = false; error.textContent = "";
      watch();
      if (redraw) draw(); else note();
    };
    const set = (field, next, redraw = true) => {
      if (field.risk && !same(own[field.key], next)) draft.ack[provider] = false; // consent covers the values as shown
      own[field.key] = next;
      touch(redraw);
    };
    const reset = (field) => { delete own[field.key]; delete draft.parse[field.key]; if (field.risk) draft.ack[provider] = false; touch(); };
    const off = (field) => (locked || (!field.available ? field.locked : field.status === "unlisted"
      ? `${offered.status?.version || "The installed CLI"} does not list ${field.flag}. Update the CLI to use this setting.` : null));

    // The small line under a control: where the value comes from, and the way back to the inherited one.
    const origin = (field) => h("span", { class: "agent-origin" },
      h("span", { class: `chip src-${source(field)}`, "data-tip": has(own, field.key) ? `Set in ${LEVELS[view.level]}` : `Not set in ${LEVELS[view.level]}` },
        has(own, field.key) ? (view.level === "global" ? "Global" : SOURCES[view.level]) : SOURCES[inherited.sources[field.key]]),
      has(own, field.key) && !locked ? iconButton("refresh", view.level === "global" ? `Reset ${field.label} to the default` : `Use the inherited ${field.label}`, () => reset(field),
        { class: "icon-btn sm", "data-key": `reset-${field.key}` }) : null);

    function modelControl(field) {
      const current = value(field);
      const listed = catalog.models.some((entry) => entry.id === current);
      const inherit = view.level !== "global";
      const options = [
        inherit ? { value: "@inherit", label: `Inherit · ${inherited.values.model || "CLI default"} (${SOURCES[inherited.sources.model]})` } : null,
        { value: "@default", label: "CLI default · no model is passed" },
        ...catalog.models.map((entry) => ({ value: entry.id, label: `${entry.label}${entry.label.toLowerCase() === entry.id ? "" : ` · ${entry.id}`}${entry.verified?.ok ? " ✓" : ""}` })),
        current && !listed ? { value: current, label: `${current} · typed by you` } : null,
        { value: "@custom", label: "Other model ID…" },
      ].filter(Boolean);
      const selected = draft.custom ? "@custom" : has(own, "model") ? own.model ?? "@default" : inherit ? "@inherit" : "@default";
      const select = selectInput(options, selected, { "aria-label": `${ROLES[role][0]} model`, "data-key": "model", disabled: Boolean(off(field)),
        onchange: () => {
          draft.custom = select.value === "@custom";
          if (draft.custom) { draw(); host(role).querySelector("[data-key=model-id]")?.focus(); return; }
          if (select.value === "@inherit") reset(field);
          else if (select.value === "@default") { if (inherit) set(field, null); else reset(field); }
          else set(field, select.value);
        } });
      const typed = draft.custom ? h("input", { class: "input mono", type: "text", "data-key": "model-id", "aria-label": "Model ID", placeholder: "Model ID, for example the full name of a model",
        value: current && !listed ? current : "", spellcheck: "false", autocomplete: "off",
        onchange: (event) => { const text = event.target.value.trim(); if (text) { draft.custom = false; set(field, text); } } }) : null;
      const entry = catalog.models.find((item) => item.id === current);
      const mark = entry?.verified || (current ? catalog.verified[current] : null);
      const known = !current ? null : mark?.ok ? badge("Answered a test call", "ok", { iconName: "check", tip: `${fullDate(mark.at)}${mark.cli ? ` · ${mark.cli}` : ""}` })
        : mark ? badge("Failed a test call", "danger", { iconName: "x", tip: mark.message || mark.kind })
        : badge("Not verified for your account", "warn", { iconName: "alert", tip: "Being listed does not mean your plan can use it. Test configuration offers a test call." });
      return [select, typed, h("div", { class: "row wrap agent-under" }, origin(field), known,
        h("span", { class: "faint" }, entry?.description || (current && !listed ? "Not in the list" : `Models ${CATALOG_SOURCES[catalog.source] || ""}`)))];
    }

    function effortControl(field) {
      const model = value(byKey.model);
      const levels = catalog.models.find((entry) => entry.id === model)?.efforts;
      const inherit = view.level !== "global";
      const options = [
        inherit ? { value: "@inherit", label: `Inherit · ${inherited.values.effort || "CLI default"}` } : null,
        { value: "@default", label: "CLI default" },
        ...field.choices.map((choice) => ({ value: choice.value, label: choice.value, disabled: Boolean(levels && !levels.includes(choice.value)) })),
      ].filter(Boolean);
      const selected = has(own, "effort") ? own.effort ?? "@default" : inherit ? "@inherit" : "@default";
      const select = h("select", { class: "input", "aria-label": `${ROLES[role][0]} ${field.label.toLowerCase()}`, "data-key": "effort", disabled: Boolean(off(field)),
        onchange: () => { if (select.value === "@inherit") reset(field); else if (select.value === "@default") { if (inherit) set(field, null); else reset(field); } else set(field, select.value); } },
        options.map((option) => h("option", { value: option.value, selected: option.value === selected, disabled: option.disabled }, option.disabled ? `${option.label} — not offered by ${model}` : option.label)));
      return [select, h("div", { class: "row wrap agent-under" }, origin(field),
        levels ? h("span", { class: "faint" }, `${model} offers ${levels.join(", ")}`) : null)];
    }

    function control(field) {
      const current = value(field), disabled = Boolean(off(field));
      const attrs = { "aria-label": field.label, "data-key": field.key, disabled };
      const lines = (parse, show, placeholder) => {
        const area = h("textarea", { class: "input mono", rows: 3, spellcheck: "false", placeholder, ...attrs,
          oninput: () => {
            try { const next = parse(area.value); delete draft.parse[field.key]; problem.textContent = ""; set(field, next, false); }
            catch (failed) { draft.parse[field.key] = failed.message; problem.textContent = failed.message; touch(false); }
          } });
        area.value = show(current);
        const problem = h("div", { class: "field-error", role: "alert" }, draft.parse[field.key] || "");
        area.addEventListener("change", () => draw()); // counts, origin and the consent box follow once typing ends
        return [area, problem];
      };
      const list = (text) => text.split("\n").map((line) => line.trim()).filter(Boolean);
      switch (field.kind) {
        case "choice": {
          const select = h("select", { class: "input", ...attrs, onchange: () => set(field, select.value) },
            field.choices.map((choice) => h("option", { value: choice.value, selected: choice.value === current, disabled: Boolean(choice.disabled) }, choice.value)),
            current == null ? h("option", { value: "", selected: true, disabled: true }, "Not set · the CLI decides") : null);
          const refused = field.choices.filter((choice) => choice.disabled);
          return [select, refused.length ? h("ul", { class: "agent-refused" }, refused.map((choice) => h("li", {}, h("span", { class: "mono" }, choice.value), ` — ${choice.disabled}`))) : null];
        }
        case "bool": return toggle({ checked: Boolean(current), label: field.label, disabled, onChange: (checked) => set(field, checked) });
        case "int": case "number": {
          const input = h("input", { class: "input num", type: "number", min: field.low, max: field.high, step: field.kind === "int" ? 1 : "any", value: current ?? "", placeholder: "Not set", ...attrs,
            oninput: () => {
              const text = input.value.trim();
              if (!text) { if (has(own, field.key)) { delete own[field.key]; touch(false); } return; }
              set(field, Number(text), false);
            }, onchange: () => draw() });
          return h("div", { class: "input-unit" }, input, field.unit ? h("span", { class: "faint" }, field.unit) : null);
        }
        case "text": case "path": {
          const input = field.multiline ? h("textarea", { class: "input", rows: 3, ...attrs }) : h("input", { class: `input${field.kind === "path" ? " mono" : ""}`, type: "text", spellcheck: "false", ...attrs });
          input.value = current || "";
          input.placeholder = "Not set";
          input.addEventListener("input", () => { const text = input.value.trim(); if (text) set(field, text, false); else if (has(own, field.key)) { delete own[field.key]; touch(false); } });
          input.addEventListener("change", () => draw());
          return input;
        }
        case "pairs": return lines(parsePairs, showPairs, "model_verbosity = \"low\"");
        default: return lines(list, (items) => (items || []).join("\n"), field.kind === "paths" ? "One absolute folder per line" : field.kind === "args" ? "--option\nvalue" : "One per line");
      }
    }

    const line = (field, content) => {
      const why = off(field);
      return h("div", { class: `setting block agent-field${why ? " is-off" : ""}`, "data-field": field.key },
        h("div", { class: "setting-text" },
          h("div", { class: "field-label" }, field.label, field.flag && field.status !== "builtin" ? h("span", { class: "mono faint agent-flag", "data-tip": field.status === "documented"
            ? "Documented by the CLI's reference, which says its help does not list every option" : field.status === "unknown" ? "The CLI's help has not been read yet" : "The option this setting becomes" }, field.flag) : null,
            field.risk && risky(field, value(field)) ? badge("Needs your OK", "warn", { iconName: "shield" }) : null),
          h("div", { class: "field-hint" }, field.help),
          why ? h("div", { class: "agent-why" }, icon("lock", "sm"), why) : null),
        h("div", { class: "setting-control" }, content, field.basic ? null : h("div", { class: "row wrap agent-under" }, origin(field))));
    };

    // Agent, preset, model and effort: the part most people need.
    const options = Object.entries(AGENTS).map(([name, label]) => ({ value: name, label }));
    const globalAgent = state.settings?.defaults?.[role];
    const agentRow = view.level === "task"
      ? h("div", { class: "row wrap" }, h("span", { class: "agent" }, h("span", { class: `agent-mark ${provider}`, "aria-hidden": "true" }, agentName(provider).charAt(0)), agentName(provider)),
        h("span", { class: "faint" }, "chosen when the task was created"))
      : segmented({ label: `${ROLES[role][0]} agent`, value: draft.chosen || (view.level === "project" ? "" : provider),
        options: [view.level === "project" ? { value: "", label: `Global default (${agentName(globalAgent)})` } : null, ...options].filter(Boolean),
        onChange: (next) => { draft.chosen = next; draft.provider = next || globalAgent; draft.agent = true; draft.dirty = true; draft.tests = null; draft.custom = false; watch(); draw(); } });
    const active = view.presets.find((preset) => { const block = preset.values[provider]; return block && Object.entries(block).every(([key, wanted]) => same(value(byKey[key]) ?? null, wanted)); });
    const custom = view.presets.filter((preset) => !preset.builtin);
    const usePreset = (preset) => { for (const [key, wanted] of Object.entries(preset.values[provider] || {})) { if (byKey[key] && !off(byKey[key])) own[key] = wanted; } draft.custom = false; touch(); };
    const presetRow = h("div", { class: "row wrap" },
      segmented({ label: `${ROLES[role][0]} preset`, value: active?.builtin ? active.id : "custom",
        options: [...view.presets.filter((preset) => preset.builtin).map((preset) => ({ value: preset.id, label: preset.name, tip: `${preset.description} ${summarize({ [provider]: preset.values[provider] || {} })}` })),
          { value: "custom", label: active && !active.builtin ? active.name : "Custom", tip: "Your own choice of model and options" }],
        onChange: (id) => { const preset = view.presets.find((item) => item.id === id); if (preset) usePreset(preset); else draw(); } }),
      custom.length ? selectInput([{ value: "", label: "Your presets…" }, ...custom.map((preset) => ({ value: preset.id, label: preset.values[provider] ? preset.name : `${preset.name} (nothing for ${agentName(provider)})` }))], "",
        { "aria-label": "Apply one of your presets", class: "input preset-pick", onchange: (event) => { const preset = custom.find((item) => item.id === event.target.value); if (preset?.values[provider]) usePreset(preset); else draw(); } }) : null);

    const [tone, label] = cliState(offered.status);
    const riskNow = fields.filter((field) => has(own, field.key) && risky(field, own[field.key]));
    const consent = riskNow.length ? h("label", { class: "callout tone-warn agent-consent" }, h("input", { type: "checkbox", class: "check", checked: draft.ack[provider], "data-key": "consent",
      onchange: (event) => { draft.ack[provider] = event.target.checked; touch(false); } }),
      h("div", { class: "grow" }, h("div", { class: "callout-title" }, `I understand what this allows the ${role} to do`),
        h("ul", { class: "callout-body agent-risks" }, riskNow.map((field) => h("li", {}, h("strong", {}, `${field.label}: `), field.risk))),
        h("div", { class: "callout-body" }, "Required to apply, and asked again whenever one of these values changes."))) : null;
    const inheritedRisks = saved.risks.filter((risk) => !has(own, risk.key));
    const advanced = view.categories.map(([key, name]) => {
      const group = fields.filter((field) => field.category === key && !field.basic);
      if (!group.length) return null;
      const setHere = group.filter((field) => has(own, field.key)).length;
      return h("details", { class: "agent-section", open: draft.open?.has(key), ontoggle: (event) => { draft.open = draft.open || new Set(); if (event.target.open) draft.open.add(key); else draft.open.delete(key); } },
        h("summary", {}, icon("chevronRight", "sm"), h("span", { class: "grow" }, name), setHere ? h("span", { class: "count" }, `${setHere} set here`) : null),
        h("div", { class: "agent-section-body" }, group.map((field) => line(field, control(field)))));
    });
    const wide = localStorage.getItem(OPEN) === "1";
    const tests = draft.tests;
    const shown = tests?.preview || saved.preview;
    const unverified = tests?.checks.find((check) => check.id === "model" && check.status === "warning") && tests.model;
    const card = h("section", { class: "card agent-card", "aria-label": `${ROLES[role][0]} settings` },
      h("div", { class: "card-head" }, h("div", { class: "row" }, h("span", { class: `agent-mark lg ${provider}`, "aria-hidden": "true" }, agentName(provider).charAt(0)),
        h("div", {}, h("h2", {}, ROLES[role][0]), h("div", { class: "field-hint" }, ROLES[role][1]))),
        badge(label, tone, { dot: true, tip: offered.status?.hint || offered.status?.warning || offered.status?.version || "CLI status" })),
      h("div", { class: "agent-basic" },
        h("div", { class: "agent-row" }, h("div", { class: "field-label" }, "Agent"), agentRow),
        h("div", { class: "agent-row" }, h("div", { class: "field-label" }, "Preset"), presetRow),
        h("div", { class: "agent-row" }, h("div", { class: "field-label" }, byKey.model.label), h("div", { class: "stack tight" }, modelControl(byKey.model))),
        h("div", { class: "agent-row" }, h("div", { class: "field-label" }, byKey.effort.label), h("div", { class: "stack tight" }, effortControl(byKey.effort))),
        h("div", { class: "agent-cli faint" }, icon("terminal", "sm"), offered.status?.installed === false ? offered.status.hint
          : [offered.status?.version || "Version unknown", offered.status?.authenticated ? ` · logged in${offered.status.auth_method ? ` (${offered.status.auth_method})` : ""}` : "",
            offered.discovered ? " · options read from its help" : " · its options have not been read yet"].join(""))),
      locked ? callout({ tone: "neutral", iconName: "lock", title: locked }) : null,
      inheritedRisks.length ? callout({ tone: "warn", iconName: "shield", title: "A wider permission is in effect from another level",
        body: inheritedRisks.map((risk) => `${byKey[risk.key]?.label || risk.key} (${SOURCES[saved.sources[risk.key]]}): ${risk.text}`).join(" · ") }) : null,
      consent,
      h("details", { class: "agent-advanced", open: wide, ontoggle: (event) => { try { localStorage.setItem(OPEN, event.target.open ? "1" : "0"); } catch { /* storage disabled */ } } },
        h("summary", {}, icon("chevronRight", "sm"), h("span", { class: "grow" }, h("span", { class: "field-label" }, "Advanced"),
          h("span", { class: "field-hint" }, `Permissions, limits, folders, prompt and every other option of ${agentName(provider)}.`))),
        h("div", { class: "agent-advanced-body" }, advanced,
          h("div", { class: "row wrap agent-tools" },
            button("Save as preset…", { size: "sm", iconName: "plus", tip: "Keep the model, effort and limits of this card under a name", onClick: () => savePreset(role, fields, value) }),
            button(view.level === "global" ? "Restore defaults" : "Clear what is set here", { size: "sm", variant: "ghost", iconName: "refresh", disabled: Boolean(locked) || !Object.keys(own).length,
              tip: view.level === "global" ? `Back to PatchRondo's defaults for ${agentName(provider)}` : `Inherit everything again for ${agentName(provider)}`,
              onClick: () => { for (const key of Object.keys(own)) delete own[key]; draft.ack[provider] = false; draft.parse = {}; draft.custom = false; touch(); } })))),
      h("div", { class: "agent-preview" }, h("div", { class: "row" }, h("span", { class: "field-label grow" }, "Command"),
        h("span", { class: "faint" }, tests ? "as tested, not saved yet" : draft.dirty ? "of the saved settings" : "of the next step"), copyButton(shown.line, "Copy")),
        h("pre", { class: "agent-command", tabindex: "0", "aria-label": "Command preview" }, shown.line),
        h("div", { class: "field-hint" }, "Shown for reading: credentials are masked, and the prompt travels on standard input, never on the command line.")),
      tests ? h("div", { class: "agent-tests", role: "status" },
        h("div", { class: "row" }, h("span", { class: "field-label grow" }, tests.ok ? "Nothing would stop this configuration" : "This configuration would not start"),
          badge(tests.ok ? "Checked" : "Problems found", tests.ok ? "ok" : "danger", { dot: true })),
        h("ul", { class: "agent-checks" }, tests.checks.map((check) => h("li", { class: `tone-${CHECKS[check.status][1]}` }, icon(CHECKS[check.status][0], "sm"),
          h("div", { class: "grow" }, h("div", {}, check.label, h("span", { class: "faint" }, ` · ${check.status}`)),
            check.detail ? h("div", { class: "field-hint break" }, check.detail) : null, check.fix ? h("div", { class: "agent-fix" }, check.fix) : null)))),
        unverified ? h("div", { class: "row wrap" }, button("Test with a real call…", { size: "sm", iconName: "bolt", attrs: { "data-key": "verify" }, onClick: (event) => verify(role, tests, event.currentTarget) }),
          h("span", { class: "faint" }, "Asks the model for one word. It uses a little of your plan.")) : null,
        h("div", { class: "faint" }, `No model was called. CLI status from ${tests.cli_checked_at ? ago(tests.cli_checked_at) : "the last check"}.`)) : null,
      h("div", { class: "settings-foot" }, error,
        button("Test configuration", { iconName: "flask", attrs: { "data-key": "test" }, onClick: (event) => test(role, event.currentTarget) }), discard, applyButton));
    return card;
  }

  const host = (role) => el.cards[role];

  function block(role, name) {
    const draft = drafts[role];
    const fields = view.providers[name].schema[role];
    const values = { ...draft.own[name] };
    if (fields.some((field) => has(values, field.key) && risky(field, values[field.key]))) values.risk_acknowledged = draft.ack[name];
    return values;
  }

  function ready(role) {
    const draft = drafts[role];
    const broken = Object.values(draft.parse)[0];
    if (broken) return broken;
    for (const name of draft.touched) if (block(role, name).risk_acknowledged === false) return `Tick “I understand what this allows the ${role} to do” first.`;
    return "";
  }

  async function apply(role, el_) {
    const draft = drafts[role];
    draft.error = ready(role);
    if (draft.error) { draw(); return; }
    await busy(el_, async () => {
      try {
        const body = { ...where(), role, blocks: Object.fromEntries([...draft.touched].map((name) => [name, block(role, name)])) };
        if (draft.agent && view.level !== "task") body.provider = draft.chosen || null;
        view = await api.patch("/api/agents", body);
        drafts[role] = makeDraft(role);
        for (const other of Object.keys(ROLES)) if (!drafts[other].dirty) drafts[other] = makeDraft(other);
        toast(`${ROLES[role][0]} settings applied${view.level === "global" ? "" : ` to ${LEVELS[view.level]}`}`);
        watch();
        refresh().catch(() => {});
      } catch (failed) { draft.error = failed.message; }
    });
    draw();
  }

  async function test(role, el_) {
    const draft = drafts[role];
    draft.error = Object.values(draft.parse)[0] || "";
    if (draft.error) { draw(); return; }
    await busy(el_, async () => {
      try {
        const values = block(role, draft.provider);
        if (values.risk_acknowledged === false) values.risk_acknowledged = true; // a test saves nothing: the consent is asked on Apply
        draft.tests = await api.post("/api/agents/test", { ...where(), role, provider: draft.provider, values });
      } catch (failed) { draft.tests = null; draft.error = failed.message; }
    });
    draw();
    host(role).querySelector(".agent-tests")?.scrollIntoView({ block: "nearest" });
  }

  async function verify(role, tests, el_) {
    const sure = await confirm({ title: `Test ${tests.model} with a real call?`, confirmLabel: "Run the test call", iconName: "bolt",
      body: [h("p", {}, `PatchRondo will start ${agentName(tests.provider)} once and ask ${tests.model} for a single word, with no tools and nothing to write.`),
        h("p", {}, "This is a real call to the model: it uses a little of your plan. Nothing else on this page calls a model.")] });
    if (!sure) return;
    await busy(el_, async () => {
      try {
        const result = await api.post("/api/agents/verify", { provider: tests.provider, model: tests.model, effort: tests.effort || null, confirm: true });
        if (result.ok) toast(`${tests.model} answered: your login can use it`);
        else toast(`${tests.model} did not answer: ${result.message || result.kind}`, { tone: "danger" });
        view = await api.get(`/api/agents${query() ? `?${query()}` : ""}`);
        await test(role, el_);
      } catch (failed) { toastError(failed); }
    });
  }

  // --- presets ----------------------------------------------------------------------

  function nameDialog(title, initial, action) {
    return dialog({ title, build: (close) => {
      const input = h("input", { class: "input", type: "text", maxlength: 60, value: initial, "aria-label": "Preset name" });
      const error = h("div", { class: "form-error", role: "alert" });
      const save = button("Save", { variant: "primary", onClick: () => busy(save, async () => {
        try { await action(input.value.trim()); close(true); } catch (failed) { clear(error, icon("alert", "sm"), failed.message); }
      }) });
      input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); save.click(); } });
      return { body: h("div", { class: "stack" }, input, error), actions: [button("Cancel", { variant: "ghost", onClick: () => close(false) }), save] };
    } }).done;
  }

  async function storePreset(preset) {
    const result = await api.post("/api/agents/presets", preset);
    view.presets = result.presets;
    draw();
  }

  function savePreset(role, fields, value) {
    const provider = drafts[role].provider;
    const values = Object.fromEntries(fields.filter((field) => field.preset).map((field) => [field.key, value(field) ?? null]).filter(([, item]) => item != null));
    if (!Object.keys(values).length) { toast("There is nothing to save yet: choose a model or an effort first", { tone: "warn" }); return; }
    nameDialog(`Save the ${role}'s ${agentName(provider)} choices as a preset`, "", async (name) => { await storePreset({ name, values: { [provider]: values } }); toast(`Preset “${name}” saved`); });
  }

  function presetList() {
    const actions = (preset, anchor) => popover(anchor, (close) => [
      menuItem("Duplicate…", { iconName: "copy", onClick: () => { close(); nameDialog("Duplicate preset", `${preset.name} copy`, (name) => storePreset({ name, values: Object.fromEntries(
        Object.entries(preset.values).map(([name_, block_]) => [name_, Object.fromEntries(Object.entries(block_).filter(([, item]) => item != null))]).filter(([, block_]) => Object.keys(block_).length)) })); } }),
      preset.builtin ? null : menuItem("Rename…", { iconName: "edit", onClick: () => { close(); nameDialog("Rename preset", preset.name, (name) => storePreset({ id: preset.id, name, values: preset.values })); } }),
      ...(preset.builtin ? [] : Object.keys(ROLES).map((role) => menuItem(`Replace with the ${role}'s current choices`, { iconName: "refresh", onClick: async () => {
        close();
        const provider = drafts[role].provider, fields = view.providers[provider].schema[role], saved = view.roles[role].blocks[provider], own = drafts[role].own[provider];
        const values = Object.fromEntries(fields.filter((field) => field.preset).map((field) => [field.key, has(own, field.key) ? own[field.key] ?? field.default : saved.inherited.values[field.key]]).filter(([, item]) => item != null));
        if (!Object.keys(values).length) { toast("That card has no model, effort or limit to save", { tone: "warn" }); return; }
        try { await storePreset({ id: preset.id, name: preset.name, values: { ...preset.values, [provider]: values } }); toast(`Preset “${preset.name}” updated`); } catch (failed) { toastError(failed); }
      } }))),
      preset.builtin ? null : menuItem("Delete…", { iconName: "trash", onClick: async () => {
        close();
        if (!(await confirm({ title: `Delete the preset “${preset.name}”?`, confirmLabel: "Delete", variant: "danger",
          body: h("p", {}, "Settings that were filled in from it stay as they are: a preset is a starting point, not a link.") }))) return;
        try { view.presets = (await api.del(`/api/agents/presets/${preset.id}`)).presets; draw(); toast("Preset deleted"); } catch (failed) { toastError(failed); }
      } }),
    ], { align: "end" });
    return h("section", { class: "section" }, h("div", { class: "section-head" }, h("h2", {}, "Presets"),
      h("span", { class: "muted" }, "A preset fills in the model, effort and limits of a card. Applying one changes nothing until you press Apply.")),
      h("div", { class: "card list" }, view.presets.map((preset) => h("div", { class: "list-row preset-row" },
        h("span", { class: "list-main" }, h("span", { class: "list-title" }, preset.name, preset.builtin ? h("span", { class: "chip" }, "Built-in") : null),
          h("span", { class: "list-sub" }, preset.description ? `${preset.description} ` : "", summarize(preset.values))),
        iconButton("dots", `Actions for the preset ${preset.name}`, (event) => actions(preset, event.currentTarget), { "aria-haspopup": "menu", "aria-expanded": "false" })))));
  }

  // --- page -------------------------------------------------------------------------

  function note() {
    if (!view) return;
    const used = [...new Set(Object.keys(ROLES).map((role) => drafts[role].provider))].map((name) => view.providers[name].status).filter(Boolean);
    const missing = used.find((status) => status.installed === false), login = used.find((status) => status.installed && status.authenticated === false);
    const check = () => api.post("/api/providers/refresh").then(() => toast("Checking the CLIs…")).catch(toastError);
    let message, mood = "idle", action = null;
    if (view.checking || !used.length) [message, mood] = ["I'm asking the CLIs what they offer…", "working"];
    else if (missing) [message, mood, action] = [`I can't find ${missing.label} on this computer. ${missing.hint || ""}`, "attention", button("Check again", { size: "sm", iconName: "refresh", onClick: check })];
    else if (login) [message, mood, action] = [`${login.label} is not logged in. Run “${login.login_command}” in a terminal, then check again.`, "attention", button("Check again", { size: "sm", iconName: "refresh", onClick: check })];
    else if (dirty()) [message, mood] = ["You have changes that are not applied yet. Test them if you like, then press Apply.", "attention"];
    else if (view.level === "task") message = "These settings belong to this task only. A step that already started keeps what it started with; the next one takes what you set here.";
    else message = "Each role keeps its own model, even when one agent plays both. Changes apply from the next step of a task: a step that already started keeps what it started with.";
    const sig = JSON.stringify([message, mood]);
    if (el.note.dataset.sig === sig) return;
    el.note.dataset.sig = sig;
    clear(el.note, rondoNote(message, mood, action));
  }

  function draw() {
    if (destroyed) return;
    const projects = projectList();
    const current = scope.task ? `${scope.project}/${scope.task}` : scope.project || "global";
    const check = button(view?.checking ? "Checking…" : "Refresh", { iconName: "refresh", disabled: Boolean(view?.checking), tip: "Ask the CLIs again for their version, login, options and models. No model is called.",
      onClick: () => busy(check, async () => { try { await api.post("/api/providers/refresh", {}); toast("Checking the CLIs…"); } catch (failed) { toastError(failed); } }) });
    clear(el.scope, h("label", { class: "row scope" }, h("span", { class: "muted" }, "Editing"),
      selectInput([{ value: "global", label: "Global preferences" }, ...projects.map((item) => ({ value: item.id, label: `Project · ${item.name}` })),
        scope.task ? { value: current, label: `Task · ${view?.task_title || scope.task}` } : null].filter(Boolean), current,
      { "aria-label": "Settings level", onchange: (event) => { const [project, task] = event.target.value === "global" ? [] : event.target.value.split("/"); navigate("agents", project, task); } })), check);
    ctx.setTitle([{ label: "Agents", href: href("agents") }, { label: scope.task ? view?.task_title || scope.task : scope.project ? state.projects.get(scope.project)?.name || "Project" : "Global" }]);
    if (failure) {
      clear(body, callout({ tone: "danger", iconName: "alert", title: "The agent settings cannot be shown", body: failure.message,
        actions: [button("Global preferences", { size: "sm", href: href("agents") })] }));
      return;
    }
    if (!view) { clear(body, loadingBlock("Loading the agent settings…")); return; }
    if (!body.contains(el.note)) {
      clear(body, el.note, el.problems, h("div", { class: "agents-grid" }, el.cards.developer, el.cards.reviewer), el.presets,
        h("p", { class: "faint" }, "PatchRondo passes these choices as arguments of each call and never edits the configuration files of Claude Code or Codex. ",
          "It never reads or stores passwords, tokens or API keys: the CLIs use their own login."));
    }
    note();
    clear(el.problems, view.problems.map((text) => callout({ tone: "danger", iconName: "alert", title: "Saved settings cannot be used", body: text })),
      view.level === "task" ? callout({ tone: "accent", iconName: "info", title: `Settings of the task “${view.task_title || view.task}”`,
        body: "They win over the project and the global preferences, for this task only.", actions: [h("a", { class: "link", href: href("tasks", view.project, view.task) }, "Back to the task")] }) : null);
    for (const role of Object.keys(ROLES)) {
      const focused = host(role).contains(document.activeElement) ? document.activeElement.dataset.key : null;
      const top = host(role).querySelector(".agent-advanced-body")?.scrollTop;
      clear(host(role), roleCard(role));
      if (focused) host(role).querySelector(`[data-key="${focused}"]`)?.focus({ preventScroll: true });
      if (top) host(role).querySelector(".agent-advanced-body").scrollTop = top;
    }
    clear(el.presets, presetList());
  }

  function update() {
    // The CLIs were checked again, or a project was renamed: load what changed, keeping unsaved edits.
    const now = JSON.stringify([state.providers?.checked_at, state.providers?.checking, projectList().map((item) => [item.id, item.name]), state.settings?.defaults]);
    if (now !== seen) { seen = now; load(); }
  }

  async function changeRoute(next) {
    if ((next.project || null) === (scope.project || null) && (next.task || null) === (scope.task || null)) return;
    if (dirty() && !(await confirm({ title: "Discard the changes that are not applied?", body: h("p", {}, "The changes on this page have not been applied."), confirmLabel: "Discard", variant: "danger" }))) {
      replace("agents", scope.project, scope.task);
      return;
    }
    scope = { project: next.project, task: next.task };
    for (const role of Object.keys(drafts)) delete drafts[role];
    view = null;
    watch();
    draw();
    load(true);
  }

  if (scope.project && !state.projects.has(scope.project)) scope = { project: null, task: null };
  if (!state.projects.size && scope.project) body.append(emptyState({ compact: true, title: "No such project", body: "It may have been removed." }));
  draw();
  return { update, route: changeRoute, destroy() {
    destroyed = true;
    window.removeEventListener("beforeunload", guard);
    if (dirty()) toast("Agent settings that were not applied were discarded", { tone: "warn" });
  } };
}
