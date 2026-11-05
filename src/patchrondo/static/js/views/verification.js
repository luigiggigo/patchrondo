// Verification Center: what was verified, what still counts, why the task is not complete, what unblocks it, and an
// export of the evidence. Everything comes from the backend: the gate as the engine saved it, the criteria and the
// validity of each record judged against the ledger as it is now, and actions the backend carries out. Nothing is
// decided here, codes are shown beside their words, and text written by agents or people is inserted as text.

import { api, download, saveFile } from "../api.js";
import { eventRow } from "../components.js";
import { clear, h, icon, memo, sync } from "../dom.js";
import { ago, bytes, fullDate, plural } from "../format.js";
import { href } from "../router.js";
import { rondoFace } from "../rondo.js";
import { CODE_STATUS, CRITERION_STATUS, EVIDENCE_KINDS, EVIDENCE_RESULTS, GATE_ACTIONS, GATE_DECISIONS, GATE_REASONS, OUTCOMES, VALIDITY,
  evidenceWords, executorName } from "../tasks.js";
import { badge, busy, button, callout, copyButton, dialog, loadingBlock, segmented, toast, toastError } from "../ui.js";
import { authorizeDialog, evidenceDialog, said } from "./criteria.js";

// Events of the history that are about verification. They describe what happened then, never the gate of now.
const TIMELINE = new Set(["run_started", "acceptance_planned", "tests_started", "tests_completed", "review_started", "review_completed",
  "code_approved", "evidence_recorded", "authorization_decided", "evidence_invalidated", "evidence_ledger_restarted", "validation_failed",
  "task_paused", "task_blocked", "task_completed"]);
const TIMELINE_SHOWN = 40;
const PAGE = 50;
const FILTERS = ["validity", "result", "kind", "criterion", "run"];

const base = (projectId, taskId) => `/api/projects/${projectId}/tasks/${taskId}/verification`;
const decisionOf = (code) => GATE_DECISIONS[code] || { label: code || "Unknown", tone: "neutral", icon: "info", mood: "idle", help: "" };
const reasonWords = (code) => GATE_REASONS[code] || code || "No reason recorded";
const validityBadge = (code) => {
  const meta = VALIDITY[code] || { label: code || "Not judged", tone: "neutral", help: "" };
  return badge(meta.label, meta.tone, { dot: true, tip: meta.help });
};
const resultBadge = (result) => {
  const [label, tone] = EVIDENCE_RESULTS[result] || [result || "Recorded", "neutral"];
  return badge(label, tone, { outline: true });
};

// --- What the gate says ------------------------------------------------------------------

function mood(view) {
  if (view.task.status === "done") return "done";
  if (view.runtime.active) return "working";
  if (!view.gate) return "idle";
  if (view.gate_state === "behind") return "waiting";
  return decisionOf(view.gate.decision).mood;
}

// A PASS is shown as one only while it describes the evidence as it is now.
function toneOf(view) {
  if (!view.gate) return "neutral";
  if (view.gate.decision === "PASS" && view.gate_state !== "current" && view.task.status !== "done") return "warn";
  return decisionOf(view.gate.decision).tone;
}

function headline(view) {
  const gate = view.gate;
  if (view.task.status === "done") return "This task is completed. The gate passed on the files and the evidence it was completed on.";
  if (view.runtime.active) return "A run is working on this task. What you see is its last checkpoint; this page follows it.";
  if (!gate) return view.tracked ? "The completion gate has not evaluated this task yet: it does at the next run, or when you check again."
    : "This task was created before criteria and the completion gate were tracked. Nothing is claimed about it in retrospect.";
  if (view.gate_state === "behind") return "Evidence was recorded after this evaluation was saved, so it may no longer describe the task. Evaluate it again: no agent is called.";
  if (gate.decision === "PASS") return "Every condition holds: the task can be completed, and completing it calls no agent.";
  const first = (gate.conditions || []).find((entry) => entry && entry.decision === gate.decision);
  const named = first?.criteria?.length ? ` (${first.criteria.join(", ")})` : "";
  return `Not complete yet. ${first ? `${reasonWords(first.reason)}${named}.` : decisionOf(gate.decision).help} Next: ${(GATE_ACTIONS[gate.next_action] || gate.next_action || "").toLowerCase()}.`;
}

function when(view) {
  const gate = view.gate;
  if (!gate) return null;
  const text = { current: `Evaluated ${ago(gate.at)}, on the evidence as it is now`, behind: `Saved ${ago(gate.at)}, before the latest evidence`,
    running: `Last checkpoint ${ago(gate.at)}` }[view.gate_state] || `Evaluated ${ago(gate.at)}`;
  return h("span", { class: "faint", "data-tip": fullDate(gate.at) }, text);
}

function stat(label, value, hint, tone, iconName) {
  return h("div", { class: `vc-stat tone-${tone}` },
    h("div", { class: "stat-label" }, icon(iconName, "sm"), label),
    h("div", { class: "summary-value" }, value),
    hint ? h("div", { class: "stat-hint" }, hint) : null);
}

function overviewCard(view) {
  const gate = view.gate;
  const decision = gate ? decisionOf(gate.decision) : null;
  const tone = toneOf(view);
  const counts = view.summary;
  const validity = view.counts.validity;
  const code = CODE_STATUS[view.code] || { label: view.code || "Not tracked", tone: "neutral" };
  const open = counts ? counts.required - counts.required_verified : 0;
  return h("section", { class: `card vc-overview tone-${tone}`, "aria-labelledby": "vc-title" },
    h("div", { class: "vc-head" }, rondoFace(mood(view), "lg"),
      h("div", { class: "grow" },
        h("h2", { id: "vc-title" }, "Verification Center"),
        h("p", { class: "vc-headline" }, headline(view)),
        h("div", { class: "row wrap" },
          decision ? badge(decision.label, tone, { iconName: decision.icon }) : badge("Not evaluated", "neutral", { iconName: "info" }),
          gate ? h("span", { class: "chip mono", "data-tip": "The gate's decision code" }, gate.decision) : null,
          gate && gate.decision === "PASS" && view.gate_state !== "current" && view.task.status !== "done" ? badge("Not current", "warn", { dot: true }) : null,
          when(view)))),
    h("div", { class: "grid c4 vc-stats" },
      stat("Code review", code.label, "The reviewer's verdict about the code. Approval alone does not complete a task.", code.tone, "eye"),
      stat("Required criteria", counts ? `${counts.required_verified} of ${counts.required} verified` : "Not tracked",
        counts ? (open ? `${plural(open, "still open", "still open")} · ${OUTCOMES[view.outcome]?.label || view.outcome}` : OUTCOMES[view.outcome]?.label || view.outcome)
          : "Created before criteria were tracked", counts && !open && counts.required ? "ok" : "neutral", "tasks"),
      stat("Evidence that counts", `${plural(validity.VALID, "record")}`,
        `${validity.STALE} stale · ${validity.INVALID} invalid · ${validity.UNKNOWN} unknown`, validity.INVALID ? "danger" : validity.STALE || validity.UNKNOWN ? "warn" : "neutral", "shield"),
      stat("Needs attention", `${view.counts.needs_reverification.length} to repeat · ${view.counts.blocked.length} for you`,
        view.reverify.length ? `${plural(view.reverify.length, "check")} without a usable result` : "Criteria whose verification must be repeated, and criteria waiting for you",
        view.counts.needs_reverification.length || view.counts.blocked.length ? "warn" : "neutral", "alert")),
    view.problems.length ? callout({ tone: "danger", iconName: "alert", title: "Integrity problem", body: [
      h("div", {}, "The evidence ledger, or what the task saved about it, cannot be fully trusted. Records after the problem do not count; there is no button to accept them."),
      view.problems.map((problem) => h("div", { class: "muted break" }, problem))] }) : null);
}

// --- Required actions --------------------------------------------------------------------

function actionsCard(view, task, ctx) {
  const rows = view.actions.map((action) => {
    const row = view.criteria.find((item) => item.id === action.criterion);
    let control = null;
    const disabled = !action.enabled;
    const label = action.kind === "export" ? "Export…" : action.kind === "authorize" ? "Decide…" : action.kind === "attach_evidence" ? "Attach…" : action.label;
    const primary = action.primary ? "primary" : "";
    const post = (body, text) => busy(control, async () => {
      try { said(await api.post(`/api/projects/${task.project}/tasks/${task.id}/criteria/check`, body), text); ctx.refresh(); }
      catch (error) { toastError(error); }
    });
    switch (action.kind) {
      case "complete": control = button(label, { variant: "primary", iconName: "flag", disabled, onClick: () => busy(control, async () => {
        try {
          const result = await api.post(`/api/projects/${task.project}/tasks/${task.id}/criteria/check`, { resume: true });
          if (result.run) toast("The task is completing.");
          else toast(result.run_error || "Something changed since the last evaluation: the gate has been evaluated again.", { tone: "warn" });
          ctx.refresh();
        } catch (error) { toastError(error); }
      }) }); break;
      case "resume": control = button(label, { variant: primary, iconName: "play", disabled, onClick: () => ctx.run() }); break;
      case "allow_tests": control = button(label, { variant: "primary", iconName: "flask", disabled, onClick: () => ctx.goTab("overview") }); break;
      case "check_again": control = button(label, { variant: primary, iconName: "refresh", disabled, onClick: () => post({ resume: false }, "Checked again.") }); break;
      case "authorize": control = button(label, { variant: "primary", iconName: "shield", disabled, onClick: () => row && authorizeDialog(task, row) }); break;
      case "attach_evidence": control = button(label, { variant: "primary", iconName: "plus", disabled, onClick: () => row && evidenceDialog(task, row) }); break;
      case "open_settings": control = button("Open settings", { iconName: "settings",
        href: action.section === "agents" ? href("agents", task.project, task.id) : href("settings", task.project, "tests") }); break;
      case "export": control = button(label, { iconName: "down", onClick: () => exportDialog(task.project, task.id) }); break;
      case "restore_switch": control = button(label, { disabled: true }); break;
      default: control = null;
    }
    return h("div", { class: `vc-action${action.primary ? " main" : ""}` },
      h("div", { class: "grow" },
        h("div", { class: "vc-action-label" }, action.label),
        h("div", { class: "muted" }, action.detail),
        h("div", { class: "row wrap vc-action-meta" },
          action.calls_agents === true ? badge("Calls the agents · uses plan quota", "warn", { iconName: "bolt" })
            : action.calls_agents === false ? badge("No agent is called", "neutral", { iconName: "check" }) : null,
          disabled && action.why ? h("span", { class: "faint" }, action.why) : null)),
      control);
  });
  return h("section", { class: "card", "aria-labelledby": "vc-actions" },
    h("div", { class: "card-head" }, h("h2", { id: "vc-actions" }, "What you can do now")),
    h("div", { class: "card-body stack" }, rows.length ? rows : h("p", { class: "muted" }, "Nothing to do here."),
      h("p", { class: "faint" }, "Only actions PatchRondo carries out are offered. None of them marks a criterion as verified: evidence does.")));
}

// --- Gate inspector -----------------------------------------------------------------------

function chips(names, ctx, kind) {
  return (names || []).filter((name) => typeof name === "string").map((name) => kind === "evidence"
    ? h("button", { type: "button", class: "chip mono vc-link", onclick: () => ctx.openRecord(name), "aria-label": `Open evidence ${name}` }, name)
    : h("span", { class: "chip mono" }, name));
}

function gateCard(view, ctx) {
  const gate = view.gate;
  if (!gate) {
    return callout({ tone: "accent", iconName: "info", title: "The completion gate has not evaluated this task",
      body: view.tracked ? "It is evaluated at every judgement of the criteria: by the next run, or by “Check again”. Until then nothing says whether the task may be completed."
        : "The task was created before the gate existed. It is not shown as passed or failed: nothing was recorded that could say." });
  }
  const conditions = (gate.conditions || []).filter((entry) => entry && typeof entry === "object");
  const findings = gate.findings && typeof gate.findings === "object" ? gate.findings : {};
  const row = (entry) => {
    const meta = decisionOf(entry.decision);
    return h("li", { class: "vc-condition" },
      badge(meta.label, meta.tone, { dot: true }),
      h("div", { class: "grow" },
        h("div", {}, reasonWords(entry.reason), entry.switch ? ` (${entry.switch})` : ""),
        h("div", { class: "row wrap vc-chips" }, h("span", { class: "chip mono", "data-tip": "Reason code" }, entry.reason),
          h("span", { class: "faint" }, `→ ${GATE_ACTIONS[entry.action] || entry.action}`), chips(entry.criteria, ctx), chips(entry.evidence, ctx, "evidence"),
          entry.causes?.length ? entry.causes.map((cause) => h("span", { class: "chip mono", "data-tip": view.words.causes[cause] || "" }, cause)) : null)));
  };
  const facts = [
    ["Evaluation", h("span", { class: "row wrap" }, h("span", { class: "mono break" }, gate.id), copyButton(gate.id, "Copy"))],
    ["Evaluated", fullDate(gate.at)],
    ["Decision", h("span", { class: "mono" }, `${gate.decision} · next ${gate.next_action}`)],
    ["Code review", h("span", { class: "mono" }, `${gate.code_review || "none"}${gate.review ? ` · ${gate.review}` : ""}`)],
    ["Files", gate.snapshot ? h("span", { class: "mono break", "data-tip": "Fingerprint of the task's files the gate was evaluated on" }, String(gate.snapshot).slice(0, 16)) : "Not compared"],
    ["Tests about these files", gate.fresh === true ? "Yes" : gate.fresh === false ? "No" : "Not compared"],
    ["Ledger", `${plural(gate.ledger?.count ?? 0, "record")} read${view.gate_state === "behind" ? ` · it holds ${view.ledger.count} now` : ""}`],
    ["Policy", h("span", { class: "mono" }, `${gate.policy || "?"} · version ${gate.version ?? "?"}`)],
  ];
  const settled = (findings.settled || []).length, waiting = (findings.pending_verification || []).length, openBlocking = (findings.open_blocking || []).length;
  return h("details", { class: "card vc-section", open: gate.decision !== "PASS" || view.gate_state !== "current" },
    h("summary", {}, h("h2", {}, gate.decision === "PASS" ? "Why this task can be completed" : "Why this task is not completed"), icon("chevronDown", "vc-chevron")),
    h("div", { class: "card-body stack" },
      conditions.length ? h("ol", { class: "vc-conditions", "aria-label": "What keeps the task from completion, in order of precedence" }, conditions.map(row))
        : h("p", {}, "Nothing keeps it from completion: the code is approved, every required criterion is verified by records that count, and the test results are about these files."),
      h("div", { class: "row wrap" },
        h("span", { class: "field-label" }, "Findings of the reviews"),
        badge(`${openBlocking} open and blocking`, openBlocking ? "warn" : "neutral", { outline: true }),
        badge(`${waiting} waiting for verification`, "neutral", { outline: true, tip: "A later approval left them out. That is not proof the problem is gone." }),
        badge(`${settled} settled by a criterion or an approval`, "neutral", { outline: true }),
        findings.unreadable ? badge("Unreadable", "danger") : null),
      h("dl", { class: "kv tight vc-facts" }, facts.flatMap(([label, value]) => [h("dt", {}, label), h("dd", {}, value)])),
      h("p", { class: "faint" }, "The gate decides from what PatchRondo recorded, by fixed rules: no model is asked. Only its PASS completes a task, ",
        "and the run that completes it fingerprints the files and reads the ledger again first. Earlier decisions in the timeline below describe their own moment.")));
}

// --- Criteria -----------------------------------------------------------------------------

function criteriaCard(view, task, ctx) {
  if (!view.tracked) return null;
  const open = view.criteria.filter((item) => item.status !== "VERIFIED");
  return h("section", { class: "card", "aria-labelledby": "vc-criteria" },
    h("div", { class: "card-head" }, h("h2", { id: "vc-criteria" }, "Acceptance criteria"),
      h("a", { class: "link", href: href("tasks", task.project, task.id, "criteria") }, "Criteria and checks")),
    h("div", { class: "card-body vc-criteria" }, view.criteria.map((item) => {
      const meta = CRITERION_STATUS[item.status] || { label: item.status, tone: "neutral", icon: "info" };
      const blocking = item.required && item.status !== "VERIFIED";
      return h("details", { class: `criterion tone-${meta.tone}`, open: blocking && open.length <= 4 },
        h("summary", {}, h("span", { class: "criterion-mark" }, icon(meta.icon)),
          h("span", { class: "grow" }, h("span", { class: "criterion-text break" }, item.text),
            h("span", { class: "row wrap criterion-meta" }, h("span", { class: "chip mono" }, item.id), badge(meta.label, meta.tone, { dot: true }),
              h("span", { class: "chip" }, item.required ? "Required" : "Optional · never blocks completion"),
              item.saved_status !== item.status ? h("span", { class: "chip", "data-tip": "What the engine saved last" }, `saved as ${item.saved_status}`) : null)),
          icon("chevronDown", "criterion-chevron")),
        h("div", { class: "criterion-body stack" },
          h("p", { class: "criterion-reason break" }, item.reason),
          h("dl", { class: "kv tight" },
            h("dt", {}, "Why"), h("dd", {}, reasonWords(item.basis), item.basis ? [" ", h("span", { class: "chip mono" }, item.basis)] : null),
            h("dt", {}, "Verified by"), h("dd", {}, executorName(item.verifier, task)),
            item.missing.length ? [h("dt", {}, "Still missing"), h("dd", { class: "mono" }, item.missing.join(", "))] : null,
            h("dt", {}, "Its evidence"), h("dd", {}, item.evidence.length ? h("span", { class: "row wrap" }, chips(item.evidence, ctx, "evidence")) : "None counts"),
            item.records.length ? [h("dt", {}, "Records about it"), h("dd", {}, h("span", { class: "row wrap" }, chips(item.records, ctx, "evidence")))] : null)));
    })));
}

// --- Evidence explorer --------------------------------------------------------------------

function recordRow(row, ctx) {
  return h("button", { type: "button", class: `vc-record${row.validity === "VALID" ? "" : " lapsed"}`, onclick: () => ctx.openRecord(row.id),
    "aria-label": `Evidence ${row.id}: ${EVIDENCE_KINDS[row.kind] || row.kind}, ${evidenceWords(row)}` },
  h("span", { class: "chip mono" }, row.id),
  h("span", { class: "vc-record-main" },
    h("span", { class: "row wrap" }, h("strong", {}, EVIDENCE_KINDS[row.kind] || row.kind), resultBadge(row.result), validityBadge(row.validity)),
    h("span", { class: "muted break" }, evidenceWords(row), row.check ? ` · check ${row.check}` : "", row.criteria.length ? ` · about ${row.criteria.join(", ")}` : ""),
    row.causes.length ? h("span", { class: "faint break" }, row.causes.join(", ")) : null),
  h("span", { class: "faint num hide-sm", "data-tip": fullDate(row.at) }, ago(row.at, true)));
}

function explorer(projectId, taskId, ctx) {
  const values = { q: "", validity: "", result: "", kind: "", criterion: "", run: "" };
  let turn = 0, items = [], matched = 0, total = 0, key = null, timer = null, facets = null;
  const search = h("input", { class: "input", type: "search", placeholder: "Search an ID or a check", "aria-label": "Search evidence by ID or check",
    maxlength: 80, oninput: () => { clearTimeout(timer); timer = setTimeout(() => { values.q = search.value.trim(); load(true); }, 220); } });
  const selects = Object.fromEntries(FILTERS.map((name) => [name, h("select", { class: "input", "aria-label": `Filter by ${name}`,
    onchange: (event) => { values[name] = event.target.value; load(true); } })]));
  const list = h("div", { class: "vc-records", role: "list" });
  const status = h("div", { class: "muted", role: "status", "aria-live": "polite" });
  const more = button("Show more", { size: "sm", onClick: () => load(false) });
  more.hidden = true;
  const el = h("section", { class: "card", "aria-labelledby": "vc-evidence" },
    h("div", { class: "card-head" }, h("h2", { id: "vc-evidence" }, "Evidence explorer")),
    h("div", { class: "card-body stack" },
      h("div", { class: "vc-filters" }, search, FILTERS.map((name) => selects[name])),
      status, list, more,
      h("p", { class: "faint" }, "Result is what was observed when a record was written; validity is whether it still counts now. A passed check that is stale ",
        "passed then, about something that changed since.")));

  const options = (name, all) => {
    const labels = { validity: (v) => VALIDITY[v]?.label || v, result: (v) => EVIDENCE_RESULTS[v]?.[0] || v, kind: (v) => EVIDENCE_KINDS[v] || v };
    const shown = labels[name] || ((v) => v);
    const word = { validity: "Any validity", result: "Any result", kind: "Any kind", criterion: "Any criterion", run: "Any run" }[name];
    memo(selects[name], [name, all, values[name]], () => [h("option", { value: "" }, word),
      all.map((value) => h("option", { value, selected: value === values[name] }, shown(value)))]);
    selects[name].value = values[name];
  };

  async function load(reset) {
    const mine = ++turn;
    const query = new URLSearchParams(Object.entries(values).filter(([, value]) => value));
    query.set("offset", reset ? "0" : String(items.length));
    query.set("limit", String(PAGE));
    if (reset && !items.length) clear(list, loadingBlock("Reading the evidence…"));
    try {
      const page = await api.get(`${base(projectId, taskId)}/evidence?${query}`);
      if (mine !== turn) return;
      items = reset ? page.items : [...items, ...page.items.filter((row) => !items.some((known) => known.id === row.id))];
      matched = page.matched;
      total = page.total;
      facets = page.facets;
      options("validity", Object.keys(VALIDITY));
      for (const name of ["result", "kind", "run"]) options(name, facets[`${name}s`] || []);
      options("criterion", facets.criteria || []);
      if (!items.length) clear(list, h("p", { class: "muted" }, total ? "No record matches these filters." : "No evidence has been recorded for this task yet."));
      else sync(list, items.map((row) => ({ key: row.id, sig: row, render: () => h("div", { role: "listitem" }, recordRow(row, ctx)) })));
      status.textContent = total ? `Showing ${items.length} of ${plural(matched, "matching record")} · ${plural(total, "record")} of evidence in the ledger` : "";
      more.hidden = items.length >= matched;
    } catch (error) {
      if (mine !== turn) return;
      status.textContent = `The evidence could not be read: ${error.message}`;
    }
  }

  return { el, refresh(ledger) {
    const next = JSON.stringify(ledger);
    if (next !== key) { key = next; load(true); }
  } };
}

async function recordDialog(projectId, taskId, id, ctx) {
  let data;
  try { data = await api.get(`${base(projectId, taskId)}/evidence/${encodeURIComponent(id)}`); }
  catch (error) { toastError(error, error.status === 404 ? "" : "The record could not be read: "); return; }
  const record = data.record, found = data.assessment;
  const check = record.check && typeof record.check === "object" ? record.check : {};
  const rows = [
    ["Kind", EVIDENCE_KINDS[record.kind] || record.kind], ["Written by", executorName(record.executor, ctx.task())], ["Recorded", fullDate(record.at)],
    record.run ? ["Run", h("span", { class: "mono" }, record.run)] : null,
    check.id ? ["Check", h("span", { class: "mono" }, `${check.id}${check.type ? ` · ${check.type}` : ""}`)] : null,
    record.command ? ["Command", h("span", { class: "mono break" }, record.command.join(" "))] : null,
    record.exit_code != null ? ["Exit code", h("span", { class: "mono" }, String(record.exit_code))] : null,
    record.iteration != null ? ["Iteration", String(record.iteration)] : null,
    record.fingerprint ? ["Files", h("span", { class: "mono break", "data-tip": "Fingerprint of the task's files it was recorded on" }, record.fingerprint)] : null,
    check.config_sha256 ? ["Configuration", h("span", { class: "mono break" }, check.config_sha256)] : null,
    check.environment?.id ? ["Environment", h("span", { class: "mono break" }, check.environment.id)] : null,
    check.report?.path ? ["Report copy", h("span", { class: "mono break" }, `${check.report.path} · sha256 ${check.report.sha256 || "?"}`)] : null,
    record.reference ? ["Reference", h("span", { class: "break" }, record.reference)] : null,
    record.summary ? ["Summary", h("span", { class: "break" }, record.summary)] : null,
    (record.artifacts || []).length ? ["Files it names", record.artifacts.map((file) => h("div", { class: "mono break" }, `${file.path} · ${bytes(file.size)} · sha256 ${file.sha256}`))] : null,
    ["Hash", h("span", { class: "mono break" }, record.hash || "")],
  ].filter(Boolean);
  const json = JSON.stringify(record, null, 2);
  dialog({ title: `Evidence ${record.id}`, description: EVIDENCE_KINDS[record.kind] || record.kind, wide: true, build: (close) => ({
    body: [
      h("div", { class: "row wrap" }, h("span", { class: "field-label" }, "Result when recorded"), resultBadge(record.result),
        h("span", { class: "field-label" }, "Validity now"), found ? validityBadge(found.validity) : badge("Not judged", "neutral")),
      found ? h("p", {}, found.validity === "VALID" ? "It counts for the task as it is now." : evidenceWords({ result: record.result, validity: found.validity })) : null,
      found?.causes?.length ? h("ul", { class: "vc-causes" }, found.causes.map((cause) => h("li", {}, data.words.causes[cause] || cause, " ", h("span", { class: "chip mono" }, cause)))) : null,
      record.trusted === false ? callout({ tone: "danger", iconName: "alert", title: "Not trusted", body: "The ledger's hash chain does not hold this record: it may have been changed, or a record before it was." }) : null,
      h("dl", { class: "kv tight" }, rows.flatMap(([label, value]) => [h("dt", {}, label), h("dd", {}, value)])),
      data.invalidations.length ? h("div", { class: "stack" }, h("div", { class: "field-label" }, "Later records that say it no longer counts"),
        data.invalidations.map((entry) => h("div", { class: "check-row" }, h("span", { class: "chip mono" }, entry.id),
          h("div", { class: "grow" }, h("div", { class: "break" }, entry.reason || ""), h("div", { class: "faint" }, `${entry.cause || ""} · ${fullDate(entry.at)}`))))) : null,
      data.criteria.length ? h("div", { class: "stack" }, h("div", { class: "field-label" }, "Criteria"),
        data.criteria.map((item) => h("div", { class: "check-row" }, h("span", { class: "chip mono" }, item.id),
          h("div", { class: "grow break" }, item.text, h("div", { class: "faint" }, item.counts ? "Counts as its evidence now" : "About it; does not count as its evidence now"))))) : null,
      h("details", { class: "technical" }, h("summary", {}, "The record as saved"), h("pre", { class: "log-chunk vc-json" }, json)),
    ],
    actions: [
      data.log?.available ? button("Open its log", { iconName: "terminal", onClick: () => { close(); ctx.openLog(data.log.id); } }) : null,
      copyButton(json, "Copy record"),
      button("Close", { variant: "primary", onClick: () => close() }),
    ],
  }) });
}

// --- Export -------------------------------------------------------------------------------

function previewBody(data) {
  const manifest = data.manifest;
  const gate = manifest.gate;
  const validity = manifest.validity || {};
  return [
    h("dl", { class: "kv tight" },
      h("dt", {}, "Gate"), h("dd", {}, gate ? [h("span", { class: "mono" }, gate.decision), gate.about_exported_ledger ? "" : " · saved before the latest evidence"] : "Not evaluated"),
      h("dt", {}, "Evidence"), h("dd", {}, `${plural(manifest.ledger.count, "record")} · ${Object.entries(validity).map(([name, count]) => `${count} ${name.toLowerCase()}`).join(" · ")}`),
      h("dt", {}, "Size"), h("dd", {}, bytes(data.size)),
      h("dt", {}, "Complete"), h("dd", {}, manifest.complete ? "Yes: nothing this mode includes was left out" : "No: see what was left out")),
    h("details", { class: "technical" }, h("summary", {}, plural(manifest.files.length + 1, "file")),
      h("div", { class: "check-list" }, [{ path: "manifest.json", size: null, role: "manifest" }, ...manifest.files].map((file) => h("div", { class: "check-row" },
        h("span", { class: "chip" }, file.role), h("span", { class: "mono grow break" }, file.path), file.size != null ? h("span", { class: "faint num" }, bytes(file.size)) : null)))),
    manifest.omissions.length ? h("div", { class: "stack" }, h("div", { class: "field-label" }, "Left out"),
      manifest.omissions.map((entry) => h("div", { class: "muted break" }, `${entry.item}: ${entry.reason}`))) : null,
    (manifest.excluded_by_mode || []).map((entry) => h("div", { class: "faint" }, `Not in a summary bundle: ${plural(entry.count, entry.item.replace(/s$/, ""), entry.item)}. Choose Detailed to add them.`)),
  ];
}

export function exportDialog(projectId, taskId) {
  let mode = "summary", turn = 0, go = null;
  const preview = h("div", { class: "stack", "aria-live": "polite" });
  const error = h("div", { class: "form-error", role: "alert" });
  const warning = h("div", {});
  const describe = () => clear(warning, mode === "detailed" ? callout({ tone: "warn", iconName: "alert", title: "Logs are included",
    body: "Copies of the logs and test reports the evidence names, from this task's folder only, cut to their last part. Values that look like secrets are masked, but masking recognizes common shapes only: share a detailed bundle only with people you would show these logs to." }) : null);
  async function load() {
    const mine = ++turn;
    if (go) go.disabled = true;
    clear(error);
    clear(preview, loadingBlock("Preparing the preview…"));
    try {
      const data = await api.get(`${base(projectId, taskId)}/bundle/preview?mode=${mode}`);
      if (mine !== turn) return;
      clear(preview, previewBody(data));
      if (go) go.disabled = false;
    } catch (failure) {
      if (mine !== turn) return;
      clear(preview, callout({ tone: "danger", iconName: "alert", title: "The preview could not be made", body: failure.message,
        actions: [button("Try again", { size: "sm", onClick: () => load() })] }));
    }
  }
  return dialog({ title: "Export evidence", description: "A ZIP of what PatchRondo recorded about this task, with a manifest of SHA-256 hashes.", wide: true,
    build: (close) => {
      go = button("Download", { variant: "primary", iconName: "down", disabled: true, onClick: () => busy(go, async () => {
        clear(error);
        try {
          const file = await download(`${base(projectId, taskId)}/bundle?mode=${mode}`);
          saveFile(file);
          close(true);
          toast(`Exported ${file.name}${file.id ? ` · ${file.id}` : ""}`);
        } catch (failure) { clear(error, icon("alert", "sm"), `Nothing was exported: ${failure.message}`); }
      }) });
      describe();
      load();
      return {
        body: [
          h("div", { class: "field" }, h("div", { class: "field-label" }, "What to include"),
            segmented({ label: "What to include", value: mode, onChange: (value) => { mode = value; describe(); load(); }, options: [
              { value: "summary", label: "Summary", icon: "file", tip: "Records, decisions, references and validity" },
              { value: "detailed", label: "Detailed", icon: "terminal", tip: "Also masked copies of the logs the evidence names" }] }),
            h("div", { class: "field-hint" }, "Summary is the default: it holds no logs. Neither mode includes the project's files.")),
          warning, preview,
          callout({ tone: "accent", iconName: "shield", title: "Integrity, not authenticity",
            body: "The hashes show whether a file of the bundle was changed after the export. Nothing is signed: they do not prove that PatchRondo produced it. Check a bundle with: patchrondo bundle verify FILE" }),
          h("p", { class: "faint" }, "The file is built again when you download it, from the task as it is then; its manifest describes exactly what it holds."),
          h("div", { class: "form-foot" }, error),
        ],
        actions: [button("Cancel", { variant: "ghost", onClick: () => close(false) }), go],
      };
    } });
}

// --- Timeline -----------------------------------------------------------------------------

function timeline(detail, task) {
  const saved = Array.isArray(detail.state.history) ? detail.state.history : [];
  const events = saved.filter((event) => event && typeof event === "object" && !Array.isArray(event) && TIMELINE.has(event.event));
  const shown = events.slice(-TIMELINE_SHOWN).reverse();
  return h("section", { class: "card", "aria-labelledby": "vc-timeline" },
    h("div", { class: "card-head" }, h("h2", { id: "vc-timeline" }, "Verification timeline"),
      h("span", { class: "muted" }, events.length > shown.length ? `latest ${shown.length} of ${events.length}` : plural(events.length, "event"))),
    h("div", { class: "card-body stack" },
      shown.length ? h("div", { class: "timeline vc-timeline" }, shown.map((event) => h("div", { class: "vc-event" }, eventRow(event, task),
        event.gate && typeof event.gate === "object" && event.gate.decision ? h("div", { class: "faint vc-then" },
          `The gate then: ${event.gate.decision}${event.gate.id ? ` · ${event.gate.id}` : ""}. This describes that moment, not the decision of now.`) : null)))
        : h("p", { class: "muted" }, "Nothing about verification has happened yet."),
      h("p", { class: "faint" }, "From the workflow history the engine saved with the task. The full history is in the Timeline tab.")));
}

// --- Panel --------------------------------------------------------------------------------

export function verificationPanel(projectId, taskId, { openLog, run, goTab }) {
  const el = h("div", { class: "stack verification" });
  const parts = { overview: h("div", {}), actions: h("div", {}), gate: h("div", {}), criteria: h("div", {}), timeline: h("div", {}), note: h("div", {}) };
  let view = null, failure = null, loaded = null, pending = false, queued = false, task = null, detail = null;
  const ctx = {
    openLog, run: () => run(), goTab, task: () => task, refresh: () => load(),
    openRecord: (id) => recordDialog(projectId, taskId, id, ctx),
  };
  const evidence = explorer(projectId, taskId, ctx);
  el.append(parts.note, parts.overview, parts.actions, parts.gate, parts.criteria, evidence.el, parts.timeline);
  clear(parts.overview, loadingBlock("Reading the verification state…"));

  function render() {
    if (!view) {
      if (failure) clear(parts.overview, callout({ tone: "danger", iconName: "alert", title: "The Verification Center could not be loaded", body: failure.message,
        actions: [button("Try again", { size: "sm", onClick: () => load() })] }));
      return;
    }
    memo(parts.note, [failure?.message], () => failure ? callout({ tone: "warn", iconName: "alert", title: "Not refreshed", body: `Showing the last state read. ${failure.message}` }) : null);
    // Ages are rebuilt with the view: every detail the page fetches follows a change it heard about.
    memo(parts.overview, [view, ago(view.gate?.at)], () => overviewCard(view));
    memo(parts.actions, [view.actions, view.criteria, task.runtime?.can_start], () => actionsCard(view, task, ctx));
    memo(parts.gate, [view.gate, view.gate_state, view.ledger, view.words], () => gateCard(view, ctx));
    memo(parts.criteria, [view.criteria, view.tracked, task.developer, task.reviewer], () => criteriaCard(view, task, ctx));
    evidence.refresh(view.ledger);
  }

  async function load() {
    if (pending) { queued = true; return; }
    pending = true;
    try {
      view = await api.get(base(projectId, taskId));
      failure = null;
    } catch (error) {
      failure = error;
    } finally {
      pending = false;
    }
    render();
    if (queued) { queued = false; load(); }
  }

  return { el, update(nextDetail, nextTask) {
    detail = nextDetail;
    task = nextTask;
    if (loaded !== detail.stamp) { loaded = detail.stamp; load(); }
    const history = Array.isArray(detail.state.history) ? detail.state.history : [];
    memo(parts.timeline, [history.length, history[history.length - 1], task.developer, task.reviewer], () => timeline(detail, task));
    render();
  } };
}
