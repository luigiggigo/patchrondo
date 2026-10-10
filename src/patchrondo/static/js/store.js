// Client state: one authoritative snapshot, then numbered events. The interface never
// guesses: when events are lost or the server changes, it loads a new snapshot, and
// while disconnected it keeps showing the last known state marked as such.

import { api, stream, token } from "./api.js";

const HEARTBEAT_TIMEOUT = 25000;
const HIDDEN_GRACE = 30000;

export const state = {
  ready: false,
  connection: token ? "connecting" : "unauthorized", // connecting | live | polling | reconnecting | offline | unauthorized
  lastSyncAt: null,   // last time the server confirmed the state (snapshot, event or heartbeat)
  lastEventAt: null,  // last time something actually changed
  epoch: null,
  seq: 0,
  about: null,
  settings: null,
  error: null,
  providers: { checking: false, checked_at: null, providers: null },
  projects: new Map(),
  tasks: new Map(),
};

const listeners = new Set();
let scheduled = false;

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function notify() {
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(() => {
    scheduled = false;
    for (const listener of [...listeners]) {
      try { listener(state); } catch (error) { console.error(error); }
    }
  });
}

function applySnapshot(snapshot) {
  state.epoch = snapshot.epoch;
  state.seq = snapshot.seq;
  state.about = snapshot.about || state.about;
  state.settings = snapshot.settings;
  state.error = snapshot.error;
  state.providers = snapshot.providers || state.providers;
  state.projects = new Map(snapshot.projects.map((project) => [project.id, project]));
  state.tasks = new Map(snapshot.tasks.map((task) => [`${task.project}/${task.id}`, task]));
  state.ready = true;
  state.lastSyncAt = Date.now();
}

function applyEvent(event) {
  if (event.type === "task") state.tasks.set(event.key, event.data);
  else if (event.type === "task_removed") state.tasks.delete(event.key);
  else if (event.type === "project") state.projects.set(event.key, event.data);
  else if (event.type === "project_removed") state.projects.delete(event.key);
  else if (event.type === "settings") state.settings = event.data;
  else if (event.type === "providers") state.providers = event.data;
  else if (event.type === "error") state.error = event.data;
  state.seq = event.seq;
  state.lastSyncAt = state.lastEventAt = Date.now();
}

export async function refresh() {
  applySnapshot(await api.get("/api/snapshot"));
  notify();
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let controller = null;
let hiddenTimer = null;
let parked = false;

async function listen() {
  let lost = false;
  let opened = 0;
  controller = new AbortController();
  let watchdog = setTimeout(() => controller.abort(), HEARTBEAT_TIMEOUT);
  const alive = () => {
    clearTimeout(watchdog);
    watchdog = setTimeout(() => controller.abort(), HEARTBEAT_TIMEOUT);
  };
  try {
    await stream(`/api/events?epoch=${encodeURIComponent(state.epoch)}&since=${state.seq}`, {
      signal: controller.signal,
      onOpen: () => { opened = Date.now(); },
      onEvent: (name, event) => {
        alive();
        if (name === "ping") { state.lastSyncAt = Date.now(); return; }
        if (name === "resync" || !event || event.seq !== state.seq + 1) {
          lost = true; // an event is missing: do not apply anything after the gap
          controller.abort();
          return;
        }
        applyEvent(event);
        notify();
      },
    });
  } finally {
    clearTimeout(watchdog);
  }
  return { lost, lived: opened ? Date.now() - opened : 0 };
}

export async function connect() {
  if (!token) { notify(); return; }
  let failures = 0;
  let shortStreams = 0;
  for (;;) {
    if (parked) { await sleep(500); continue; }
    try {
      await refresh();
      failures = 0;
      if (shortStreams >= 3) {
        // The stream keeps ending at once (something buffers it): poll snapshots instead.
        state.connection = "polling";
        notify();
        await sleep(3000);
        if (++shortStreams > 23) shortStreams = 0; // try the stream again once a minute
        continue;
      }
      state.connection = "live";
      notify();
      const result = await listen().catch((error) => {
        if (error.name === "AbortError") return { lost: true, lived: 9999 };
        throw error;
      });
      shortStreams = !result.lost && result.lived < 2000 ? shortStreams + 1 : 0;
      if (parked) continue;
      state.connection = "reconnecting";
      notify();
    } catch (error) {
      if (error.status === 401) {
        state.connection = "unauthorized";
        notify();
        return;
      }
      failures += 1;
      state.connection = failures > 2 ? "offline" : "reconnecting";
      notify();
      await sleep(Math.min(400 * 2 ** failures, 5000));
    }
  }
}

// A hidden tab releases its connection after a while and catches up when shown again.
document.addEventListener("visibilitychange", () => {
  clearTimeout(hiddenTimer);
  if (document.hidden) {
    hiddenTimer = setTimeout(() => { parked = true; controller?.abort(); }, HIDDEN_GRACE);
  } else if (parked) {
    parked = false;
  }
});

// --- selectors ----------------------------------------------------------------------

export function projectList() {
  return [...state.projects.values()].sort((a, b) => a.name.localeCompare(b.name));
}

export function selectedProjectId() {
  const id = state.settings?.selected_project;
  return id && state.projects.has(id) ? id : null;
}

export function taskList(projectId = null) {
  const tasks = [...state.tasks.values()].filter((task) => !projectId || task.project === projectId);
  return tasks.sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
}

export function getTask(projectId, taskId) {
  return state.tasks.get(`${projectId}/${taskId}`) || null;
}

export function defaultsFor(projectId) {
  const globalDefaults = state.settings?.defaults || { developer: "claude", reviewer: "codex" };
  const own = state.projects.get(projectId)?.config?.agents || {};
  return { developer: own.developer || globalDefaults.developer, reviewer: own.reviewer || globalDefaults.reviewer };
}
