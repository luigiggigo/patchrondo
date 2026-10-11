// Live activity of one task: the end of its journal, then a stream of what is appended.
// The position is a cursor into the journal file, so reconnecting continues exactly where
// the last message ended, also after the server was restarted. Nothing is ever shown that
// did not come from the journal.

import { api, stream } from "./api.js";

const PAGE = 200;
const HEARTBEAT_TIMEOUT = 25000;
const HIDDEN_GRACE = 30000;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// handlers: onReset(events, { moreBefore }) replaces what is shown, onEvents(events) appends,
// onState(name) reports loading | live | polling | reconnecting | offline | paused | missing | unauthorized.
export function activityFeed(projectId, taskId, { onReset, onEvents, onState }) {
  const base = `/api/projects/${projectId}/tasks/${taskId}/activity`;
  let cursor = null;      // where the next event starts
  let earliest = null;    // where the first loaded event starts
  let moreBefore = false;
  let lastSeq = 0;
  let loaded = false;
  let turn = 0;           // changes whenever the feed is stopped: late answers of an old turn are ignored
  let active = false;
  let parked = false;
  let controller = null;
  let hiddenTimer = null;

  // Events arrive in order; one already shown is never shown again.
  const fresh = (events) => {
    const kept = (events || []).filter((event) => Number.isInteger(event?.seq) && event.seq > lastSeq);
    if (kept.length) lastSeq = kept[kept.length - 1].seq;
    return kept;
  };

  async function loadEnd(mine) {
    const page = await api.get(`${base}?limit=${PAGE}`, controller.signal);
    if (mine !== turn) return;
    lastSeq = 0;
    ({ next: cursor, start: earliest, more_before: moreBefore } = page);
    loaded = true;
    onReset(fresh(page.events), { moreBefore });
  }

  async function poll(mine) {
    const page = await api.get(`${base}?limit=${PAGE}&cursor=${encodeURIComponent(cursor)}`, controller.signal);
    if (mine !== turn) return false;
    if (page.reset) { loaded = false; return false; }
    cursor = page.next;
    const events = fresh(page.events);
    if (events.length) onEvents(events);
    return page.more;
  }

  async function follow(mine) {
    let lost = false;
    const opened = Date.now();
    let watchdog = setTimeout(() => controller.abort(), HEARTBEAT_TIMEOUT);
    const alive = () => {
      clearTimeout(watchdog);
      watchdog = setTimeout(() => controller.abort(), HEARTBEAT_TIMEOUT);
    };
    try {
      await stream(`${base}/stream${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`, {
        signal: controller.signal,
        onEvent: (name, data) => {
          alive();
          if (mine !== turn || name === "ping") return;
          if (name === "reset") { lost = true; controller.abort(); return; }
          if (!data || typeof data.cursor !== "string") return;
          cursor = data.cursor;
          const events = fresh(data.events);
          if (events.length) onEvents(events);
        },
      });
    } finally {
      clearTimeout(watchdog);
    }
    return { lost, lived: Date.now() - opened };
  }

  async function run() {
    const mine = ++turn;
    let failures = 0;
    let shortStreams = 0;
    while (mine === turn) {
      if (parked) { await sleep(400); continue; }
      controller = new AbortController();
      try {
        if (!loaded) {
          onState("loading");
          await loadEnd(mine);
          if (mine !== turn) return;
        }
        failures = 0;
        if (shortStreams >= 3 && cursor) {
          // The stream keeps ending at once (something buffers it): ask for pages instead.
          onState("polling");
          const more = await poll(mine);
          if (++shortStreams > 60) shortStreams = 0; // try the stream again once a minute
          if (!more) await sleep(1000);
          continue;
        }
        onState("live");
        const result = await follow(mine);
        if (mine !== turn) return;
        if (result.lost) loaded = false; // the cursor no longer fits the journal: show its end again
        shortStreams = !result.lost && result.lived < 2000 ? shortStreams + 1 : 0;
        if (!parked) onState("reconnecting");
        await sleep(300);
      } catch (error) {
        if (mine !== turn) return;
        if (error.name === "AbortError") { if (!parked) onState("reconnecting"); continue; }
        if (error.status === 401) { onState("unauthorized"); return; }
        if (error.status === 404) { onState("missing"); return; }
        failures += 1;
        onState(failures > 2 ? "offline" : "reconnecting");
        await sleep(Math.min(400 * 2 ** failures, 5000));
      }
    }
  }

  // A hidden tab releases its connection after a while and catches up from its cursor when shown again.
  const onVisibility = () => {
    clearTimeout(hiddenTimer);
    if (document.hidden) {
      hiddenTimer = setTimeout(() => { if (active) { parked = true; controller?.abort(); onState("paused"); } }, HIDDEN_GRACE);
    } else {
      parked = false;
    }
  };

  return {
    start() {
      if (active) return;
      active = true;
      parked = false;
      document.addEventListener("visibilitychange", onVisibility);
      run();
    },
    stop() {
      if (!active) return;
      active = false;
      turn += 1;
      clearTimeout(hiddenTimer);
      document.removeEventListener("visibilitychange", onVisibility);
      controller?.abort();
    },
    // The page of events before the first one loaded; null when the journal changed underneath.
    async earlier() {
      if (!earliest || !moreBefore) return { events: [], moreBefore: false };
      const page = await api.get(`${base}?limit=${PAGE}&before=${encodeURIComponent(earliest)}`);
      if (page.reset) return null;
      ({ start: earliest, more_before: moreBefore } = page);
      return { events: page.events, moreBefore };
    },
  };
}
