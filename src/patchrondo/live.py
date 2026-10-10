"""Live updates: an authoritative snapshot followed by numbered incremental events.

The hub periodically rebuilds the application's view from the files on disk and
publishes what differs from the previous view. Every event has a sequence
number in one `epoch` (one server lifetime). A client that reconnects asks for
events after the last number it applied; when the hub can no longer provide
them it answers "resync" and the client loads a fresh snapshot. Nothing is
inferred: an event always carries the complete new value of what changed.
"""
from __future__ import annotations

from collections import deque
import secrets
import threading
import time

from .app import App, dumps

SCAN_SECONDS = 0.5
IDLE_SECONDS = 15  # with no listener the hub stops scanning this long after the last request
KEEP_EVENTS = 2000


class Hub:
    def __init__(self, app: App, interval: float = SCAN_SECONDS):
        self.app, self.interval = app, interval
        self.epoch = secrets.token_hex(6)
        self.seq = 0
        self.events: deque[dict] = deque(maxlen=KEEP_EVENTS)
        self.cond = threading.Condition()
        self.scan_lock = threading.Lock()
        self.view: dict = {"projects": {}, "tasks": {}, "settings": None, "providers": None, "error": None}
        self.prints: dict[str, str] = {}
        self.listeners = 0
        self.last_use = 0.0
        self.closed = False
        self.thread: threading.Thread | None = None

    # --- scanning -------------------------------------------------------------------

    def scan(self) -> None:
        """Rebuild the view and publish one event per changed project, task or global section."""
        with self.scan_lock:
            fresh = self.app.collect()
            flat = {f"project:{key}": value for key, value in fresh["projects"].items()}
            flat.update({f"task:{key}": value for key, value in fresh["tasks"].items()})
            flat.update({name: fresh[name] for name in ("settings", "providers", "error")})
            prints = {key: dumps(value) for key, value in flat.items()}
            changes = []
            for key in prints.keys() - self.prints.keys() | {k for k in prints.keys() & self.prints.keys()
                                                              if prints[k] != self.prints[k]}:
                kind, _, name = key.partition(":")
                changes.append({"type": kind, "key": name or None, "data": flat[key]})
            for key in self.prints.keys() - prints.keys():
                kind, _, name = key.partition(":")
                changes.append({"type": f"{kind}_removed", "key": name, "data": None})
            with self.cond:
                self.view, self.prints = fresh, prints
                for change in changes:
                    self.seq += 1
                    self.events.append({"seq": self.seq, **change})
                if changes:
                    self.cond.notify_all()

    def _loop(self) -> None:
        while not self.closed:
            # A write made through the API wakes the scan immediately.
            self.app.changed.wait(self.interval)
            self.app.changed.clear()
            if self.closed:
                return
            with self.cond:
                idle = self.listeners == 0 and time.monotonic() - self.last_use > IDLE_SECONDS
            if idle:
                with self.cond:
                    self.thread = None
                return
            try:
                self.scan()
            except Exception:  # noqa: BLE001 - a failed scan is retried; listeners keep their last view
                time.sleep(self.interval)

    def _ensure_running(self) -> None:
        with self.cond:
            self.last_use = time.monotonic()
            if self.thread is None and not self.closed:
                self.thread = threading.Thread(target=self._loop, name="patchrondo-live", daemon=True)
                self.thread.start()

    # --- consumers ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """The complete current view with the sequence number later events continue from."""
        self.scan()
        self._ensure_running()
        with self.cond:
            return {"epoch": self.epoch, "seq": self.seq, **self.view,
                    "projects": list(self.view["projects"].values()),
                    "tasks": list(self.view["tasks"].values())}

    def subscribe(self) -> None:
        with self.cond:
            self.listeners += 1
        self._ensure_running()

    def unsubscribe(self) -> None:
        with self.cond:
            self.listeners -= 1
            self.last_use = time.monotonic()

    def wait(self, epoch: str, since: int, timeout: float) -> list[dict] | None:
        """Events after `since`, waiting up to `timeout`; None when the client has to resynchronize."""
        with self.cond:
            if epoch != self.epoch or since > self.seq:
                return None
            if since == self.seq and not self.closed:
                self.cond.wait(timeout)
            if since == self.seq:
                return []
            oldest = self.events[0]["seq"] if self.events else self.seq + 1
            if since + 1 < oldest:
                return None  # the events in between are no longer kept
            return [event for event in self.events if event["seq"] > since]

    def close(self) -> None:
        with self.cond:
            self.closed = True
            self.cond.notify_all()
        self.app.changed.set()
