// Rondo, the PatchRondo raccoon. Only the two official images are used; a mood is shown
// with a small motion and a status mark, never with a different drawing.

import { h, icon } from "./dom.js";
import { ago, fullDate, plural } from "./format.js";
import { agentName, errorOf, phaseSentence, stopReason } from "./tasks.js";

const FULL = "/assets/rondo.webp";
const HEAD = "/assets/rondo-head.webp";

// mood -> motion class, tone and mark shown on the avatar
const MOODS = {
  idle: { motion: "", tone: null, mark: null },
  welcome: { motion: "mood-wave", tone: null, mark: null },
  working: { motion: "mood-bob", tone: "accent", mark: "bolt" },
  waiting: { motion: "mood-rest", tone: "wait", mark: "clock" },
  done: { motion: "mood-hop", tone: "ok", mark: "check" },
  attention: { motion: "", tone: "warn", mark: "pause" },
  error: { motion: "mood-tilt", tone: "danger", mark: "alert" },
};

export function rondoFull(mood = "idle", height = 150) {
  // Explicit dimensions (the image is 312x360) keep the layout stable while it loads.
  return h("img", { class: `rondo ${MOODS[mood]?.motion || ""}`.trim(), src: FULL, alt: "", width: Math.round(height * 312 / 360), height });
}

export function rondoFace(mood = "idle", size = "") {
  const meta = MOODS[mood] || MOODS.idle;
  return h("span", { class: `rondo-face ${size} ${meta.motion} ${meta.tone ? `tone-${meta.tone}` : ""}`.replace(/\s+/g, " ").trim(), "aria-hidden": "true" },
    h("img", { src: HEAD, alt: "", width: 128, height: 128 }),
    meta.mark ? h("span", { class: "mood" }, icon(meta.mark)) : null);
}

// Rondo saying one thing about the current situation. Announced politely to screen readers.
export function rondoNote(message, mood = "idle", action = null) {
  return h("div", { class: "card rondo-note", role: "status" }, rondoFace(mood),
    h("div", { class: "rondo-note-text grow" }, h("div", { class: "rondo-note-who" }, "Rondo"), h("div", {}, message)),
    action);
}

export function emptyState({ title, body, action, mood = "idle", compact = false, height }) {
  return h("div", { class: `empty${compact ? " compact" : ""}` },
    compact ? rondoFace(mood, "lg") : rondoFull(mood, height || 132),
    h("h2", {}, title), body ? h("p", {}, body) : null, action || null);
}

// What Rondo says on the dashboard. Counts come from the backend's process truth.
export function boardMessage({ projects, counts, providers, selected }) {
  if (!projects) return { mood: "welcome", text: "Welcome! Add a Git repository and I'll help Claude and Codex take turns on it." };
  const missing = providers ? Object.values(providers).filter((p) => p.installed === false).map((p) => p.label) : [];
  if (!counts.all) {
    return { mood: "idle", text: missing.length === 2
      ? "No tasks yet, and I can't find Claude Code or Codex on this computer. Check Settings → Agents first."
      : `No tasks yet${selected ? ` in ${selected}` : ""}. Describe what to build and I'll take it from there.` };
  }
  if (counts.attention) {
    return { mood: "attention", text: `${plural(counts.attention, "task")} ${counts.attention === 1 ? "needs" : "need"} your attention${counts.active ? `, ${counts.active} in progress` : ""}.` };
  }
  if (counts.active) return { mood: "working", text: `${plural(counts.active, "task")} in the loop. I'm keeping an eye on ${counts.active === 1 ? "it" : "them"}.` };
  if (counts.ready) return { mood: "idle", text: `${plural(counts.ready, "task")} ready. Press Run when you are.` };
  return { mood: "done", text: "Everything is patched up. Nice work!" };
}

// What Rondo says about one task, from its persisted state and the verified process state.
export function taskMessage(task, state) {
  const dev = agentName(task.developer), rev = agentName(task.reviewer);
  const plan = state?.recovery;
  const lock = task.runtime?.lock;
  switch (task.runtime?.activity) {
    case "done": return { mood: "done", text: `All patched up! ${rev} approved and the tests passed.` };
    case "blocked": return { mood: "error", text: "I'm stuck on this one. The review explains why; a new task is the way forward." };
    case "starting": return { mood: "working", text: "Starting the run…" };
    case "running": return { mood: "working", text: `${phaseSentence(task)} · iteration ${task.iteration}.` };
    case "running_unverified":
      return { mood: "attention", text: `The task is locked by a run${lock?.pid ? ` (PID ${lock.pid})` : ""}, but this computer cannot confirm that it is still alive.` };
    case "waiting_retry":
      return { mood: "waiting", text: `${agentName(plan?.provider)} is at its usage limit. A PatchRondo process is waiting and will retry ${ago(plan?.resume_at)} (${fullDate(plan?.resume_at)}), as long as it keeps running.` };
    case "plan_only":
      return { mood: "attention", text: `A retry was planned for ${fullDate(plan?.resume_at)}, but no process is waiting for it. Nothing happens until you resume.` };
    case "plan_unverified":
      return { mood: "attention", text: `A retry is planned for ${fullDate(plan?.resume_at)}. A waiting process was recorded, but it cannot be verified here.` };
    case "recovery_stopped":
      return { mood: "attention", text: `Automatic recovery ended: ${stopReason(plan?.stop_reason).toLowerCase()}. Resume by hand when you are ready.` };
    case "stale_lock":
      return { mood: "error", text: "The last run ended abruptly and left its lock behind. Check that nothing is still running, then release the lock and resume." };
    case "interrupted":
      return { mood: "attention", text: "The last run stopped between checkpoints. Resume continues from the last one." };
    case "paused": {
      const error = errorOf(state || task);
      if (error?.kind === "quota") return { mood: "waiting", text: `${agentName(error.provider)} reached a usage limit. I can only wait for it to reset; resume when it has.` };
      if (task.verdict === "CHANGES_REQUESTED" && error?.kind === "tests_disabled")
        return { mood: "attention", text: "I can't finish without project tests. Enable them in Settings → Tests, then resume." };
      return { mood: "attention", text: error ? `${error.title}. ${error.help}` : "Paused. Resume picks up from the last checkpoint." };
    }
    case "ready": return { mood: "idle", text: `Ready when you are. ${dev} develops, ${rev} reviews.` };
    default: return { mood: "idle", text: "I can't tell what this task is doing." };
  }
}
