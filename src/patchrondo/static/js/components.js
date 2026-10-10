// Task-specific building blocks shared by the views.

import { h, icon } from "./dom.js";
import { ago, clock, fullDate } from "./format.js";
import { href } from "./router.js";
import { state } from "./store.js";
import { activityOf, agentName, describeEvent, workflow } from "./tasks.js";
import { agentChip } from "./ui.js";

const STEP_TONE = { done: "ok", current: "accent", waiting: "wait", paused: "warn", error: "danger", next: "neutral", pending: "neutral" };
const STEP_WORD = { done: "completed", current: "in progress", waiting: "waiting", paused: "paused here", error: "stopped here", next: "next", pending: "not started" };

// Four small segments summarizing the workflow in a list row.
export function miniSteps(task) {
  const steps = workflow(task);
  const label = steps.map((step) => `${step.label}: ${STEP_WORD[step.step]}`).join(", ");
  return h("span", { class: "steps-mini", role: "img", "aria-label": label, "data-tip": label },
    steps.map((step) => h("span", { class: `step-seg ${step.step} tone-${STEP_TONE[step.step]}` })));
}

// The full workflow: Develop -> Test -> Review -> Complete, with the repeat loop.
export function workflowBar(task) {
  const steps = workflow(task);
  const icons = { develop: "code", test: "flask", review: "eye", complete: "flag" };
  const max = task.max_iterations;
  return h("div", { class: "workflow card" },
    h("ol", { class: "workflow-steps", "aria-label": "Workflow" }, steps.map((step, index) => [
      index ? h("li", { class: `workflow-link ${steps[index - 1].step === "done" ? "done" : ""}`, "aria-hidden": "true" }) : null,
      h("li", { class: `workflow-step ${step.step} tone-${STEP_TONE[step.step]}`, "aria-current": ["current", "waiting", "paused", "error"].includes(step.step) ? "step" : null },
        h("span", { class: "workflow-node" }, step.step === "done" ? icon("check") : icon(icons[step.key])),
        h("span", { class: "workflow-text" }, h("span", { class: "workflow-label" }, step.label),
          h("span", { class: "workflow-state" }, STEP_WORD[step.step]))),
    ])),
    h("div", { class: "workflow-loop" }, icon("repeat", "sm"),
      h("span", {}, "Review feedback and failing tests send the task back to Develop."),
      h("span", { class: "workflow-iter num" }, `Iteration ${task.iteration ?? "?"}${max ? ` of ${max}` : ""}`)));
}

export function taskRow(task, { showProject = true } = {}) {
  const meta = activityOf(task);
  const project = state.projects.get(task.project);
  return h("a", { class: "list-row task-row", href: href("tasks", task.project, task.id) },
    h("span", { class: `dot tone-${meta.tone}${meta.live ? " live" : ""}`, "aria-hidden": "true" }),
    h("span", { class: "list-main" },
      h("span", { class: "list-title" }, task.title || task.id),
      h("span", { class: "list-sub" }, [
        h("span", { class: `tone-${meta.tone} task-activity` }, meta.label),
        showProject && project ? ` · ${project.name}` : "",
        ` · ${agentName(task.developer)} → ${agentName(task.reviewer)}`,
        ` · iteration ${task.iteration ?? "?"}${task.max_iterations ? `/${task.max_iterations}` : ""}`,
      ])),
    h("span", { class: "list-trail" }, miniSteps(task),
      h("span", { class: "num hide-sm", "data-tip": fullDate(task.updated_at) }, ago(task.updated_at, true))));
}

// Signature of everything a task row shows, for keyed updates.
export function taskSig(task, extra = "") {
  return [task.title, task.runtime?.activity, task.phase, task.status, task.iteration, task.max_iterations, task.developer,
    task.reviewer, ago(task.updated_at, true), state.projects.get(task.project)?.name, extra];
}

export function eventRow(event, task, { showTask = false } = {}) {
  const meta = describeEvent(event, task);
  return h("div", { class: `event tone-${meta.tone}` },
    h("time", { class: "event-time num", datetime: event.at, "data-tip": fullDate(event.at) }, clock(event.at)),
    h("span", { class: "event-mark", "aria-hidden": "true" }, icon(meta.icon, "sm")),
    h("div", { class: "event-body" },
      h("div", { class: "event-text" }, meta.text),
      meta.detail ? h("div", { class: "event-detail" }, meta.detail) : null,
      showTask ? h("a", { class: "event-task truncate", href: href("tasks", task.project, task.id) }, task.title || task.id) : null));
}

export function agentRoles(task) {
  return h("div", { class: "row wrap" },
    h("span", { class: "chip" }, h("span", { class: "faint" }, "Developer"), agentChip(task.developer)),
    icon("arrowRight", "sm faint"),
    h("span", { class: "chip" }, h("span", { class: "faint" }, "Reviewer"), agentChip(task.reviewer)));
}
