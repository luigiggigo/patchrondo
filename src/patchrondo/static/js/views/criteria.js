// Criteria and checks: what the task has to satisfy, who does and who verifies each criterion, and with what evidence.
// Statuses, reasons, capabilities and the actions that are possible all come from the backend. Nothing is worked
// out here, and every action shown is one the backend carries out: recording a check, recording a decision,
// judging the criteria again. None of them calls an agent.

import { api } from "../api.js";
import { clear, h, icon, memo } from "../dom.js";
import { bytes, fullDate, plural } from "../format.js";
import { emptyState } from "../rondo.js";
import { CAPABILITY_STATES, CATEGORIES, CODE_STATUS, CRITERION_STATUS, EVIDENCE_KINDS, EVIDENCE_RESULTS, FEASIBILITY, OUTCOMES,
  REVIEW_OUTCOMES, executorName } from "../tasks.js";
import { badge, busy, button, callout, dialog, field, segmented, toast, toastError } from "../ui.js";

const ATTENTION = ["FAILED", "BLOCKED_CAPABILITY", "AWAITING_AUTHORIZATION", "AWAITING_MANUAL_VALIDATION", "UNKNOWN"];

function post(task, path, body) {
  return api.post(`/api/projects/${task.project}/tasks/${task.id}/criteria/${path}`, body);
}

// What happened after something was recorded. A task is completed only by a run, which the backend
// starts by itself only when that run would call no agent.
function said(result, text) {
  if (result.run) toast(`${text} Every required criterion is verified: the task is completing.`);
  else if (result.completes) toast(`${text} ${result.run_error || "Press Complete to close the task."}`, { tone: "warn" });
  else toast(text);
}

function evidenceDialog(task, criterion) {
  let passed = true;
  const summary = h("textarea", { class: "input", rows: 3, placeholder: "I installed the build in a clean profile and played for ten minutes without errors." });
  const reference = h("input", { class: "input", type: "text", placeholder: "A link, a commit, the path of a log or of a screenshot" });
  const artifact = h("input", { class: "input mono", type: "text", spellcheck: "false", autocomplete: "off", placeholder: "dist/release.zip" });
  const error = h("div", { class: "form-error", role: "alert" });
  const proof = h("div", { class: "stack" },
    field({ label: "Where the proof can be found", optional: true, error: false, control: reference,
      hint: "Something another person could look at to see the same thing." }),
    field({ label: "A file to fingerprint", optional: true, error: false, control: artifact,
      hint: "A path on this computer, or one inside the task's copy of the project. PatchRondo computes its SHA-256 itself and records it." }),
    h("p", { class: "faint" }, "A statement alone is not accepted as evidence: give a reference, a file, or both."));
  return dialog({ title: `Attach evidence for ${criterion.id}`, description: criterion.text, wide: true,
    build: (close) => {
      const go = button("Attach evidence", { variant: "primary", iconName: "check", onClick: () => busy(go, async () => {
        clear(error);
        try {
          const result = await post(task, `${criterion.id}/evidence`, { summary: summary.value, reference: reference.value.trim() || null,
            artifact: artifact.value.trim() || null, passed, resume: true });
          close(true);
          said(result, passed ? "Evidence recorded." : "Recorded: the check did not pass.");
        } catch (failure) { clear(error, icon("alert", "sm"), failure.message); }
      }) });
      return {
        body: [
          h("div", { class: "field" }, h("div", { class: "field-label" }, "Result of your check"),
            segmented({ label: "Result of your check", value: "passed", onChange: (value) => { passed = value === "passed"; proof.hidden = !passed; },
              options: [{ value: "passed", label: "It passed", icon: "check" }, { value: "failed", label: "It did not pass", icon: "x" }] }),
            h("div", { class: "field-hint" }, "A check that did not pass is sent to the developer as a change request at the next run.")),
          field({ label: "What you did and what you saw", error: false, control: summary }),
          proof, h("div", { class: "form-foot" }, error),
        ],
        actions: [button("Cancel", { variant: "ghost", onClick: () => close(false) }), go],
      };
    } });
}

function authorizeDialog(task, criterion) {
  return dialog({ title: `Authorize the operation of ${criterion.id}?`, description: criterion.text,
    build: (close) => {
      const decide = (el, decision) => busy(el, async () => {
        try {
          // `confirm` is the explicit consent the backend requires for a grant; a refusal needs none.
          const result = await post(task, `${criterion.id}/authorization`, { decision, confirm: decision === "granted" ? true : undefined, resume: true });
          close(true);
          said(result, decision === "granted" ? "Authorized. Nothing was run: do it, then attach its evidence." : "Recorded: not authorized. Nothing was run.");
        } catch (error) { close(false); toastError(error); }
      });
      const grant = button("I authorize it", { variant: "primary", iconName: "shield", onClick: () => decide(grant, "granted") });
      const deny = button("Do not authorize", { onClick: () => decide(deny, "denied") });
      return {
        body: [
          h("p", {}, "Operations like a download, a commit or a publication are never done by the agents or by PatchRondo, with or without this authorization."),
          h("p", {}, h("strong", {}, "Authorizing means that you take the operation on yourself."),
            " The criterion then waits for the evidence that it was done. If you do not authorize it, nothing happens and the criterion stays open."),
          criterion.authorization ? h("p", { class: "muted" }, `Your last decision: ${criterion.authorization.decision === "granted" ? "authorized" : "not authorized"} on ${fullDate(criterion.authorization.at)}.`) : null,
        ],
        actions: [button("Cancel", { variant: "ghost", onClick: () => close(false) }), deny, grant],
      };
    } });
}

function resultBadge(record) {
  const [label, tone] = EVIDENCE_RESULTS[record.result] || [record.result || "Recorded", "neutral"];
  return badge(label, tone, { dot: true });
}

// One record of the ledger as it concerns one criterion. Text written by agents or people is inserted as text.
function evidenceRow(record, criterion, task, context) {
  const state = !record.trusted ? badge("Not trusted", "danger", { tip: "The ledger failed its integrity check from this record on" })
    : record.invalidated ? badge("No longer counts", "neutral", { tip: record.invalidated.reason || "" }) : null;
  const facts = [];
  if (record.kind === "command") {
    facts.push(h("div", { class: "mono break" }, (record.command || []).join(" ")),
      h("div", { class: "row wrap muted" }, `exit ${record.exit_code}`, record.covers?.length ? `· covers ${record.covers.join(", ")}` : null,
        record.log && context.logs.has(record.log) ? button("Open log", { size: "sm", variant: "ghost", iconName: "terminal", onClick: () => context.openLog(record.log) }) : null));
  } else if (record.kind === "review") {
    const outcome = record.outcomes?.[criterion.id];
    facts.push(h("div", { class: "break" }, outcome ? `It judged this criterion: ${REVIEW_OUTCOMES[outcome] || outcome}.` : "It approved the code as a whole and did not list this criterion separately."),
      record.reported_verdict ? h("div", { class: "muted" }, "Its verdict was settled from what it said about each criterion.") : null);
  } else if (record.kind === "manual") {
    facts.push(h("div", { class: "break" }, record.summary || ""),
      record.reference ? h("div", { class: "muted break" }, "Proof: ", record.reference) : null,
      (record.artifacts || []).map((file) => h("div", { class: "mono muted break" }, `${file.path} · ${bytes(file.size)} · sha256 ${file.sha256}`)));
  } else if (record.kind === "authorization") {
    facts.push(h("div", {}, record.result === "granted" ? "You took this operation on yourself." : "You decided that this operation is not done."));
  }
  const who = executorName(record.executor, task);
  return h("div", { class: `evidence-row${state ? " stale" : ""}` },
    h("div", { class: "row wrap" }, h("span", { class: "chip mono" }, record.id), h("strong", {}, EVIDENCE_KINDS[record.kind] || record.kind), resultBadge(record), state,
      h("span", { class: "grow" }), h("span", { class: "faint num" }, `${who} · ${fullDate(record.at)}`)),
    facts,
    record.fingerprint ? h("div", { class: "faint mono break", "data-tip": "Fingerprint of the task's files this was recorded on" }, `files ${String(record.fingerprint).slice(0, 12)} · iteration ${record.iteration ?? "?"}`) : null);
}

function requirementRow(item, task) {
  const [label, tone] = CAPABILITY_STATES[item.state] || [item.state, "neutral"];
  return h("div", { class: "check-row" }, badge(label, tone, { dot: true }),
    h("div", { class: "grow" }, h("div", {}, `${item.label} — ${executorName(item.executor, task)}`), item.source ? h("div", { class: "muted break" }, item.source) : null));
}

function criterionCard(criterion, view, task, context) {
  const meta = CRITERION_STATUS[criterion.status] || { label: criterion.status, tone: "neutral", icon: "info" };
  const records = criterion.records.map((id) => context.records.get(id)).filter(Boolean).reverse();
  const counting = new Set(criterion.evidence);
  const usable = task.runtime.can_start && task.status !== "done" && task.status !== "blocked";
  const why = usable ? null : "Available when the task is not running";
  const actions = [];
  if (criterion.status !== "VERIFIED") {
    if (criterion.actions.authorize) {
      actions.push(button(criterion.authorization ? "Change your decision…" : "Authorize or decline…", { size: "sm", iconName: "shield", disabled: !usable, tip: why,
        variant: criterion.authorization?.decision === "granted" ? "" : "primary", onClick: () => authorizeDialog(task, criterion) }));
    }
    if (criterion.actions.evidence) {
      actions.push(button("Attach evidence…", { size: "sm", variant: "primary", iconName: "plus", disabled: !usable, tip: why, onClick: () => evidenceDialog(task, criterion) }));
    }
  }
  const opinion = criterion.review;
  const details = h("details", { class: `criterion tone-${meta.tone}`, open: context.open.has(criterion.id) ? context.open.get(criterion.id) : ATTENTION.includes(criterion.status) },
    h("summary", {},
      h("span", { class: "criterion-mark" }, icon(meta.icon)),
      h("span", { class: "grow" },
        h("span", { class: "criterion-text break" }, criterion.text),
        h("span", { class: "row wrap criterion-meta" }, h("span", { class: "chip mono" }, criterion.id),
          badge(meta.label, meta.tone, { dot: true }),
          criterion.required ? null : h("span", { class: "chip" }, "Optional"),
          criterion.categories.length ? criterion.categories.map((name) => h("span", { class: "chip" }, CATEGORIES[name] || name)) : h("span", { class: "chip" }, "Unclassified"))),
      icon("chevronDown", "criterion-chevron")),
    h("div", { class: "criterion-body stack" },
      h("p", { class: "criterion-reason break" }, criterion.reason),
      h("dl", { class: "kv tight" },
        h("dt", {}, "Done by"), h("dd", {}, executorName(criterion.executor, task)),
        h("dt", {}, "Verified by"), h("dd", {}, executorName(criterion.verifier, task)),
        h("dt", {}, "Feasibility"), h("dd", {}, (FEASIBILITY[criterion.feasibility] || { label: criterion.feasibility }).label),
        criterion.depends_on.length ? [h("dt", {}, "Waits for"), h("dd", {}, criterion.depends_on.join(", "))] : null,
        h("dt", {}, "Why this kind"), h("dd", {}, criterion.classification?.note || "—")),
      criterion.requires.length ? h("div", {}, h("div", { class: "field-label" }, "What it takes, and who has it"),
        h("div", { class: "check-list" }, criterion.requires.map((item) => requirementRow(item, task)))) : null,
      opinion ? h("p", { class: "muted break" }, `The reviewer's opinion: ${REVIEW_OUTCOMES[opinion.outcome] || opinion.outcome}${opinion.note ? ` — ${opinion.note}` : ""}.`,
        criterion.verifier === "reviewer" ? "" : " It is recorded and is not evidence for this criterion.") : null,
      h("div", {}, h("div", { class: "field-label" }, "Evidence"),
        records.length ? h("div", { class: "evidence-list" }, records.map((record) => {
          const row = evidenceRow(record, criterion, task, context);
          if (counting.has(record.id)) row.classList.add("counts");
          return row;
        })) : h("p", { class: "muted" }, "No evidence has been recorded for this criterion.")),
      actions.length || criterion.actions.evidence_refusal ? h("div", { class: "row wrap" }, actions,
        criterion.actions.evidence_refusal && !criterion.actions.evidence ? h("span", { class: "faint" }, criterion.actions.evidence_refusal) : null) : null));
  details.addEventListener("toggle", () => context.open.set(criterion.id, details.open));
  return details;
}

function capabilitiesCard(view, task) {
  const found = view.capabilities;
  if (!found) return null;
  return h("details", { class: "card advanced" },
    h("summary", {}, h("span", { class: "grow" }, h("span", { class: "field-label" }, "Who can do what in this task"),
      h("span", { class: "field-hint" }, `Read from the settings and from this computer on ${fullDate(found.detected_at)}. Nothing was run, and no model was asked.`)), icon("chevronDown")),
    h("div", { class: "card-body capabilities" }, found.executors.map((executor) => h("div", { class: "stack" },
      h("div", { class: "field-label" }, executorName(executor.name, task)),
      h("div", { class: "check-list" }, executor.capabilities.map((item) => {
        const [label, tone] = CAPABILITY_STATES[item.state] || [item.state, "neutral"];
        return h("div", { class: "check-row" }, badge(label, tone, { dot: true }),
          h("div", { class: "grow" }, h("div", {}, item.label), h("div", { class: "muted break" }, item.source),
            (item.commands || []).map((line) => h("div", { class: "mono muted break" }, line))));
      }))))));
}

function summaryChips(counts) {
  return h("div", { class: "row wrap" }, Object.entries(FEASIBILITY).map(([key, meta]) => counts.feasibility[key]
    ? badge(`${counts.feasibility[key]} · ${meta.label}`, meta.tone, { outline: true }) : null));
}

export function criteriaPanel(projectId, taskId, { openLog }) {
  const el = h("div", { class: "stack criteria-panel" });
  const open = new Map(); // which criteria the reader opened or closed, kept across refreshes
  return { el, update(detail, task) {
    const view = detail.acceptance;
    memo(el, [view, task.runtime.can_start, task.status, task.phase, detail.logs.map((file) => file.id), task.developer, task.reviewer], () => {
      const ended = task.status === "done" || task.status === "blocked";
      const check = button("Check again", { size: "sm", iconName: "refresh", disabled: !task.runtime.can_start || ended,
        tip: ended ? "The task has ended" : task.runtime.can_start ? "Looks at this computer and at the evidence again. No agent is called and nothing is run" : "Available when the task is not running",
        onClick: () => busy(check, async () => {
          try { said(await post(task, "check", { resume: false }), "Checked again."); } catch (error) { toastError(error); }
        }) });
      if (!view?.tracked) {
        return [
          callout({ tone: "accent", iconName: "info", title: "This task was created before criteria were tracked",
            body: ended ? "It ended under the earlier rule: an approved review, passing tests and changes. Nothing was recorded about each criterion, so nothing is said about them here."
              : "Its criteria are recorded at its next run, with nothing taken as verified. You can also record them now.",
            actions: ended ? null : [check] }),
          view?.criteria.length ? h("div", { class: "card list" }, view.criteria.map((item) => h("div", { class: "issue-row" },
            h("span", { class: "chip" }, "As written"), h("div", { class: "grow break" }, item.text)))) : null,
          (view?.problems || []).map((problem) => callout({ tone: "danger", iconName: "alert", title: "The saved criteria cannot be used", body: problem })),
        ];
      }
      const counts = view.summary;
      const code = CODE_STATUS[view.code.status] || { label: view.code.status, tone: "neutral" };
      const outcome = OUTCOMES[view.outcome] || { label: view.outcome, tone: "neutral" };
      const ready = view.outcome === "RELEASE_READY" && task.status === "paused" && task.phase === "validate";
      const complete = ready ? button("Complete the task", { variant: "primary", iconName: "flag", disabled: !task.runtime.can_start,
        tip: "Closes the task. No agent is called", onClick: () => busy(complete, async () => {
          try {
            const result = await post(task, "check", { resume: true });
            if (result.run) toast("The task is completing.");
            else toast(result.run_error || "Something changed since the last check: look at the criteria again.", { tone: "warn" });
          } catch (error) { toastError(error); }
        }) }) : null;
      const context = { open, openLog, logs: new Set(detail.logs.map((file) => file.id)), records: new Map(view.evidence.map((record) => [record.id, record])) };
      const fill = h("span", { class: "criteria-fill" });
      fill.style.width = `${counts.required ? Math.round(100 * counts.required_verified / counts.required) : 100}%`;
      return [
        view.problems.map((problem) => callout({ tone: "danger", iconName: "alert", title: "The evidence ledger cannot be trusted",
          body: [problem, " The next run or “Check again” starts it again and keeps the old file; what it verified then has to be verified again."] })),
        h("section", { class: "card pad stack" },
          h("div", { class: "grid c2" },
            h("div", { class: `criteria-stat tone-${code.tone}` }, h("div", { class: "stat-label" }, "Code"), h("div", { class: "summary-value" }, code.label),
              h("div", { class: "stat-hint" }, view.code.status === "APPROVED" ? "The review approved it, the project's commands passed and there are changes."
                : view.code.reason && view.code.status === "PENDING" ? view.code.reason : "Approved by a review, with passing tests and changes.")),
            h("div", { class: `criteria-stat tone-${outcome.tone}` }, h("div", { class: "stat-label" }, "Task as a whole"), h("div", { class: "summary-value" }, outcome.label),
              h("div", { class: "stat-hint" }, "Finished when the code is approved and every required criterion has recorded evidence."))),
          h("div", { class: "criteria-progress", role: "img", "aria-label": `${counts.required_verified} of ${counts.required} required criteria verified` },
            h("div", { class: "criteria-bar" }, fill),
            h("span", { class: "num" }, `${counts.required_verified} of ${plural(counts.required, "required criterion", "required criteria")} verified`,
              counts.total > counts.required ? ` · ${plural(counts.total - counts.required, "optional one")}` : "")),
          h("div", { class: "row wrap" }, summaryChips(counts), h("span", { class: "grow" }), check, complete),
          counts.missing.length ? h("div", {}, h("div", { class: "field-label" }, "Missing in this workflow"),
            h("div", { class: "check-list" }, counts.missing.map((item) => h("div", { class: "check-row" }, badge(item.criteria.join(", "), "warn"),
              h("div", { class: "grow" }, h("div", {}, `${item.label} — ${executorName(item.executor, task)}`), h("div", { class: "muted break" }, item.source)))))) : null),
        view.criteria.length ? h("div", { class: "criteria-list" }, view.criteria.map((criterion) => criterionCard(criterion, view, task, context)))
          : emptyState({ compact: true, title: "No criteria", body: "This task names no acceptance criterion." }),
        capabilitiesCard(view, task),
        h("p", { class: "faint" }, `${plural(view.evidence.length, "record")} in the evidence ledger (evidence.jsonl in the task's folder). Records are never edited: `,
          "one that stops counting is named by a later record. What an agent says about its own work is never evidence."),
      ];
    });
  } };
}
