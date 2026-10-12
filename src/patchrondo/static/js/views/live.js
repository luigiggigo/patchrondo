// Live Activity: what the agents and the test runner are doing, drawn as it is recorded.
// Rows are appended one by one and never rebuilt, so focus, scroll position and opened
// details survive. Everything shown comes from the task's activity journal or from the
// backend's process truth; text written by agents is inserted as text only.

import { clear, h, icon } from "../dom.js";
import { fullDate, humanize, plural } from "../format.js";
import { activityFeed } from "../live.js";
import { href } from "../router.js";
import { emptyState, rondoFace, taskMessage } from "../rondo.js";
import { agentName, configured, phaseSentence } from "../tasks.js";
import { badge, button, toastError, toggle } from "../ui.js";

const MAX_ROWS = 4000;
const ROLES = { developer: "developer", reviewer: "reviewer", tests: "tests", environment: "preparation" };
const RUNNING = new Set(["running", "starting", "running_unverified"]);
const STARTS = new Set(["development_started", "tests_started", "review_started"]);
const FILTERS = [["all", "Everything"], ["message", "Messages"], ["tool", "Tools and files"], ["test", "Tests"], ["error", "Errors"]];
const CONNECTION = {
  loading: ["Loading…", "neutral", false], live: ["Live", "ok", true], polling: ["Live · polling", "ok", false],
  reconnecting: ["Reconnecting…", "warn", true], offline: ["Offline", "danger", false],
  paused: ["Paused while this tab is hidden", "neutral", false], missing: ["Task not found", "danger", false],
  unauthorized: ["No session", "danger", false],
};
const TOOL_ICONS = { Read: "file", Write: "edit", Edit: "edit", MultiEdit: "edit", NotebookEdit: "edit", Glob: "search",
  Grep: "search", shell: "terminal", web_search: "search" };
const CHANGES = { add: "Added", delete: "Deleted", update: "Changed", written: "Wrote", edited: "Edited" };
const OUTCOMES = {
  completed: { developer: "Development step returned", reviewer: "Review step returned · PatchRondo validates the answer next", tests: "Test run finished",
    environment: "Preparation finished · no agent was called for it" },
  failed: "Step failed", timeout: "Step timed out", interrupted: "Step interrupted",
};

const seconds = (ms) => `${Math.round(ms / 100) / 10} s`;
const stamp = (iso) => { const time = Date.parse(iso); return Number.isFinite(time) ? new Date(time).toLocaleTimeString() : ""; };
const firstLine = (text) => String(text || "").trim().split("\n")[0];

function elapsed(since) {
  const total = Math.max(0, Math.floor((Date.now() - since) / 1000));
  const hours = Math.floor(total / 3600), minutes = Math.floor((total % 3600) / 60), rest = total % 60;
  return `${hours ? `${hours}:${String(minutes).padStart(2, "0")}` : minutes}:${String(rest).padStart(2, "0")}`;
}

function actor(event) {
  if (event.role === "tests") return "Project tests";
  if (event.role === "environment") return "PatchRondo · preparation";
  return `${agentName(event.provider)} · ${ROLES[event.role] || event.role}`;
}

// One journal event as { kind, icon, tone, title, detail, mono, state }. Nothing is inferred beyond the event.
export function describe(event) {
  const run = event.status === "started" ? "running" : null;
  switch (event.type) {
    case "message":
      return event.withheld ? { kind: "message", icon: "lock", tone: "wait", title: event.text }
        : { kind: "message", icon: "user", tone: "neutral", title: firstLine(event.text), detail: String(event.text || "").trim().includes("\n") || String(event.text || "").length > 220 ? event.text : null, long: true };
    case "tool": {
      const command = event.kind === "command";
      const name = command ? "$" : event.name || "Tool";
      const failed = event.status === "failed" || event.status === "declined";
      const facts = [event.exit_code != null ? `exit ${event.exit_code}` : null, event.status === "declined" ? "declined" : null].filter(Boolean).join(" · ");
      return { kind: "tool", icon: TOOL_ICONS[event.name] || "code", tone: failed ? "danger" : run ? "accent" : "ok", mono: command,
        title: `${name}${event.summary ? ` ${command ? "" : "· "}${event.summary}` : ""}`.trim(), state: run || (failed ? "failed" : null),
        note: facts, detail: event.output || null };
    }
    case "file":
      return { kind: "tool", icon: "file", tone: event.status === "failed" ? "danger" : "ok", mono: true,
        title: `${CHANGES[event.change] || humanize(event.change || "changed")} ${event.path || ""}`.trim(), note: event.status === "failed" ? "not applied" : "" };
    case "test": {
      const tone = { started: "accent", passed: "ok", failed: "danger", timeout: "warn" }[event.status] || "neutral";
      return { kind: "test", icon: "flask", tone, mono: true, title: event.command || `Test command ${event.index ?? ""}`,
        state: run, note: run ? "" : `${event.status}${event.exit_code != null ? ` · exit ${event.exit_code}` : ""}` };
    }
    case "error":
      return { kind: "error", icon: "alert", tone: "danger", title: event.kind ? `Error · ${humanize(event.kind).toLowerCase()}` : "Error reported by the CLI",
        detail: event.text || null, open: true };
    case "completion": {
      const done = event.outcome === "completed";
      const title = done ? OUTCOMES.completed[event.role] || "Step returned" : `${OUTCOMES[event.outcome] || "Step ended"}${event.kind ? ` · ${humanize(event.kind).toLowerCase()}` : ""}`;
      const note = [Number.isFinite(event.seconds) ? `${event.seconds} s` : null, event.unrecorded ? `${plural(event.unrecorded, "event")} not recorded (limit)` : null].filter(Boolean).join(" · ");
      return { kind: done ? "status" : "error", icon: done ? "check" : event.outcome === "interrupted" ? "pause" : "x",
        tone: done ? "ok" : event.outcome === "failed" ? "danger" : "warn", title, note };
    }
    case "status":
      switch (event.status) {
        // The model and effort are the configuration the step was started with; the session line below it is what the CLI reports.
        case "started": return { kind: "status", icon: "play", tone: "accent", title: event.role === "tests" ? "Project tests started"
          : event.role === "environment" ? "PatchRondo started preparing" : `${agentName(event.provider)} started as ${ROLES[event.role] || event.role}`,
          note: ["developer", "reviewer"].includes(event.role) ? configured(event) || "CLI default model" : "" };
        // Recorded by the environment check: its stages, what it repaired and what it found.
        case "stage": return { kind: "status", icon: "shield", tone: "neutral", title: event.text || humanize(event.stage || "stage") };
        case "repair": return { kind: "status", icon: "shield", tone: "accent", title: event.text || "Applying a fix" };
        case "repaired": return { kind: "status", icon: "shield", tone: event.outcome === "applied" ? "ok" : event.outcome === "unchanged" ? "neutral" : "warn", title: event.text || "Fix attempted" };
        case "check_failed": return { kind: "error", icon: "alert", tone: "warn", title: event.text || "A check failed", detail: event.detail || null };
        case "ready": return { kind: "status", icon: "check", tone: "ok", title: event.text || "Ready" };
        case "session": return { kind: "status", icon: "bolt", tone: "neutral", title: `Session opened${event.model ? ` · ${event.model}` : ""}` };
        case "retry": return { kind: "error", icon: "repeat", tone: "warn", title: `The CLI is retrying a request${event.reason ? ` · ${humanize(event.reason).toLowerCase()}` : ""}`,
          note: [event.attempt != null ? `attempt ${event.attempt}${event.max_retries != null ? ` of ${event.max_retries}` : ""}` : null,
            event.retry_delay_ms != null ? `next in ${seconds(event.retry_delay_ms)}` : null].filter(Boolean).join(" · ") };
        case "plan": return { kind: "message", icon: "tasks", tone: "neutral", title: `Plan · ${event.done ?? 0} of ${event.total ?? 0} steps done`,
          detail: (event.items || []).map((item) => `${item.completed ? "✓" : "○"} ${item.text}`).join("\n") || null };
        case "turn_completed": return { kind: "status", icon: "check", tone: "neutral", title: "Turn finished",
          note: [event.input_tokens != null ? `${event.input_tokens} input tokens` : null, event.output_tokens != null ? `${event.output_tokens} output tokens` : null].filter(Boolean).join(" · ") };
        case "result": return { kind: "status", icon: "flag", tone: "neutral", title: "The CLI returned its result",
          note: [event.turns != null ? plural(event.turns, "turn") : null, event.duration_ms != null ? seconds(event.duration_ms) : null].filter(Boolean).join(" · ") };
        default: return { kind: "status", icon: "info", tone: event.status === "notice" ? "warn" : "neutral", title: event.text || humanize(event.status || "status") };
      }
    default:
      return { kind: "status", icon: "info", tone: "neutral", title: humanize(event.type || "event") };
  }
}

// Started and finished events of one tool call or test command share a row.
function pairKey(event) {
  if (event.type === "tool" && event.id) return `${event.attempt}|tool|${event.id}`;
  if (event.type === "test" && event.index != null) return `${event.attempt}|test|${event.index}`;
  return null;
}

function fill(row, event) {
  const meta = describe(event);
  row.className = `act-row tone-${meta.tone}`;
  row.dataset.kind = meta.kind;
  const body = row.lastElementChild;
  // An opened detail stays open when its row is completed.
  const wasOpen = body.querySelector("details")?.open;
  clear(row.children[1], icon(meta.icon, "sm"));
  clear(body,
    h("div", { class: "act-title" },
      h("span", { class: `act-text${meta.mono ? " mono" : ""}${meta.long ? " clamp" : ""}` }, meta.title || ""),
      meta.state === "running" ? badge("running", "accent", { dot: true, live: true }) : null,
      meta.state === "failed" ? badge(event.status === "declined" ? "declined" : "failed", "danger") : null,
      meta.note ? h("span", { class: "act-note num" }, meta.note) : null),
    meta.detail ? h("details", { class: "act-more", open: Boolean(meta.open || wasOpen) },
      h("summary", {}, event.type === "message" ? "Full message" : event.type === "error" ? "Message" : "Output"),
      h("pre", { class: `act-detail${event.type === "message" ? " prose" : ""}` }, meta.detail)) : null);
}

function buildRow(event) {
  const row = h("div", { class: "act-row" },
    h("time", { class: "act-time num", datetime: event.at, "data-tip": fullDate(event.at) }, stamp(event.at)),
    h("span", { class: "event-mark", "aria-hidden": "true" }),
    h("div", { class: "act-body" }));
  fill(row, event);
  return row;
}

// A call whose end is not known (the run stopped, or the end is outside what is loaded) is not shown as running.
function settle(row) {
  row.querySelector(".act-title .badge.tone-accent")?.remove();
  if (row.classList.contains("tone-accent")) row.className = "act-row tone-neutral";
}

function attemptHead(event) {
  return h("div", { class: "timeline-iteration act-head" }, `Iteration ${event.iteration ?? "?"} · ${actor(event)}`);
}

export function livePanel(projectId, taskId) {
  let follow = true, lastAttempt = null, firstAttempt = null, shown = false, timer = null, context = null, connection = "loading";
  let trimmed = false;
  const pairs = new Map();      // pair key -> row still waiting for its end
  const attempts = new Map();   // attempt id -> { first, last, open }
  const now = { face: h("span", { class: "act-now-face" }), who: h("div", { class: "act-now-who" }), doing: h("div", { class: "act-now-doing", role: "status" }),
    time: h("div", { class: "act-now-time num" }) };
  const list = h("div", { class: "act-list" });
  const earlier = button("Load earlier activity", { size: "sm", onClick: () => loadEarlier() });
  const empty = h("div", {});
  const view = h("div", { class: "act-view", tabindex: "0", role: "log", "aria-label": "Agent activity", "aria-live": "off" }, earlier, list, empty);
  const state = h("span", {});
  const followBox = toggle({ checked: true, label: "Follow new activity", onChange: (value) => { follow = value; if (value) view.scrollTop = view.scrollHeight; } });
  const filter = h("select", { class: "input act-filter", "aria-label": "Show", onchange: () => { list.dataset.filter = filter.value; if (follow) view.scrollTop = view.scrollHeight; } },
    FILTERS.map(([value, label]) => h("option", { value }, label)));
  const el = h("div", { class: "stack" },
    h("section", { class: "card act-now" }, now.face, h("div", { class: "grow act-now-text" }, now.who, now.doing), now.time),
    h("div", { class: "log-toolbar" }, filter, state, h("span", { class: "grow" }),
      h("label", { class: "row log-option" }, followBox, h("span", {}, "Follow")),
      h("a", { class: "btn sm ghost", href: href("tasks", projectId, taskId, "logs"), "data-tip": "Run output and the transcript of each step" }, icon("terminal"), "Full logs")),
    view,
    h("p", { class: "faint" }, "What an agent writes while it works is progress, not a result. A review counts only after PatchRondo has validated the final answer and the tests have passed; the Review tab shows that."));
  earlier.hidden = true;
  list.dataset.filter = "all";

  // Scrolling away from the end pauses following, scrolling back resumes it.
  view.addEventListener("scroll", () => {
    const atEnd = view.scrollHeight - view.scrollTop - view.clientHeight < 24;
    if (follow !== atEnd && document.activeElement !== followBox) { follow = atEnd; followBox.checked = atEnd; }
  });

  function note(event) {
    const known = attempts.get(event.attempt) || { first: event, started: null, last: event, open: true };
    known.last = event;
    if (event.type === "status" && event.status === "started") known.started = event;
    if (event.type === "completion") known.open = false;
    attempts.delete(event.attempt); // keep the most recent attempt last
    attempts.set(event.attempt, known);
    while (attempts.size > 60) attempts.delete(attempts.keys().next().value);
  }

  function add(events, { before = false } = {}) {
    const fragment = document.createDocumentFragment();
    const open = before ? new Map() : pairs; // an earlier page pairs its calls among themselves
    let previous = before ? null : lastAttempt;
    for (const event of events) {
      if (!before) note(event);
      if (!before && event.type === "completion") {
        // The attempt ended: nothing of it is still running.
        for (const [name, row] of pairs) if (name.startsWith(`${event.attempt}|`)) { settle(row); pairs.delete(name); }
      }
      const key = pairKey(event);
      const waiting = key ? open.get(key) : null;
      if (waiting && event.status !== "started") {
        fill(waiting, event); // the call ended: its row changes in place
        open.delete(key);
        continue;
      }
      if (event.attempt !== previous) {
        previous = event.attempt;
        fragment.append(attemptHead(event));
      }
      const row = buildRow(event);
      if (key && event.status === "started") {
        open.set(key, row);
        if (open.size > 400) { settle(open.values().next().value); open.delete(open.keys().next().value); }
      }
      fragment.append(row);
    }
    if (before) {
      for (const row of open.values()) settle(row);
      // The page just loaded may end inside the attempt the list starts with: one heading is enough.
      if (events.length && events[events.length - 1].attempt === firstAttempt) list.querySelector(".act-head")?.remove();
      if (events.length) firstAttempt = events[0].attempt;
      list.prepend(fragment);
      return;
    }
    if (events.length && firstAttempt === null) firstAttempt = events[0].attempt;
    lastAttempt = previous;
    list.append(fragment);
    while (list.childElementCount > MAX_ROWS) { list.firstElementChild.remove(); trimmed = true; }
    if (trimmed) earlier.hidden = true;
  }

  const feed = activityFeed(projectId, taskId, {
    onReset(events, { moreBefore }) {
      pairs.clear();
      attempts.clear();
      lastAttempt = firstAttempt = null;
      trimmed = false;
      clear(list);
      add(events);
      earlier.hidden = !moreBefore;
      refresh();
      view.scrollTop = view.scrollHeight;
    },
    onEvents(events) {
      add(events);
      refresh();
      if (follow) view.scrollTop = view.scrollHeight;
    },
    onState(name) { connection = name; refresh(); },
  });

  async function loadEarlier() {
    try {
      const page = await feed.earlier();
      if (!page) return; // the journal changed underneath: the feed shows its end again
      const height = view.scrollHeight;
      add(page.events, { before: true });
      earlier.hidden = !page.moreBefore;
      view.scrollTop += view.scrollHeight - height; // keep what the reader was looking at in place
    } catch (error) { toastError(error); }
  }

  let drawn = {};
  const draw = (name, node, sig, build) => { if (drawn[name] !== sig) { drawn[name] = sig; clear(node, build()); } };

  function refresh() {
    if (!context) return;
    const [detail, task] = context;
    const message = taskMessage(task, detail.state);
    const running = RUNNING.has(task.runtime.activity);
    const current = [...attempts.values()].reverse().find((entry) => entry.open) || null;
    if (!running && pairs.size) {
      // No verified process: whatever was running when the run stopped is not running now.
      for (const row of pairs.values()) settle(row);
      pairs.clear();
    }
    // The mascot follows the verified state of the workflow, never the stream.
    draw("face", now.face, message.mood, () => rondoFace(message.mood, "lg"));
    let who, doing, since = null;
    if (running && current) {
      who = `${actor(current.last)} · iteration ${current.last.iteration}${task.max_iterations ? ` of ${task.max_iterations}` : ""}`;
      doing = describe(current.last).title;
      // The start of the attempt, or of its phase as the engine saved it when that event is not loaded.
      const phase = [...(detail.state.history || [])].reverse().find((entry) => STARTS.has(entry?.event));
      since = Date.parse(current.started?.at || phase?.at);
    } else if (running) {
      who = `${phaseSentence(task)} · iteration ${task.iteration}${task.max_iterations ? ` of ${task.max_iterations}` : ""}`;
      doing = task.runtime.activity === "starting" ? "The run is starting." : "Nothing has been recorded for this step yet.";
    } else {
      // Rondo's note above already says what the task is waiting for: this line is about steps only.
      who = "No step is running";
      doing = current ? `The last recorded step (${actor(current.last)}) has no recorded end: its run stopped before it finished.`
        : list.childElementCount ? "Below is what earlier runs recorded." : "";
    }
    draw("who", now.who, who, () => who);
    draw("doing", now.doing, doing, () => doing);
    const last = running && current ? Date.parse(current.last.at) : NaN;
    const clockText = Number.isFinite(since) ? `${elapsed(since)} elapsed` : "";
    const quiet = Number.isFinite(last) && Date.now() - last > 5000 ? `last activity ${elapsed(last)} ago` : "";
    draw("time", now.time, `${clockText}|${quiet}`, () => [clockText ? h("div", { class: "act-clock" }, clockText) : null, quiet ? h("div", { class: "faint" }, quiet) : null]);
    const [label, tone, live] = CONNECTION[connection] || CONNECTION.loading;
    draw("state", state, connection, () => badge(label, tone, { dot: true, live }));
    const none = !list.childElementCount && connection !== "loading";
    draw("empty", empty, none ? `none|${running}` : "", () => none ? emptyState({ compact: true, mood: message.mood, title: "No live activity recorded yet",
      body: running ? "The step that is running has not reported anything yet. Its output is also in the Logs tab."
        : "Messages, tool use, file changes and tests appear here while a run is working, as the CLIs report them." }) : null);
  }

  return {
    el,
    update(detail, task) { context = [detail, task]; refresh(); },
    show() {
      if (shown) return;
      shown = true;
      feed.start();
      timer = setInterval(() => { if (!document.hidden) refresh(); }, 1000);
      if (follow) requestAnimationFrame(() => { view.scrollTop = view.scrollHeight; });
    },
    hide() {
      if (!shown) return;
      shown = false;
      feed.stop();
      clearInterval(timer);
    },
    destroy() { this.hide(); },
  };
}
