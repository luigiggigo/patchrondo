"""Drive a local headless Chrome or Edge through the DevTools protocol. Standard library only.

Used by `tools/ui_e2e.py`. It starts the browser with a throwaway profile and a
debugging port bound to loopback, and talks to one page over a WebSocket. No
extension, driver binary or Python package is needed.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request

CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "chrome", "msedge")
# Helpers available to every evaluated expression.
HELPERS = r"""
const q = (selector, root = document) => root.querySelector(selector);
const qa = (selector, root = document) => [...root.querySelectorAll(selector)];
const visible = (el) => Boolean(el && el.getClientRects().length && getComputedStyle(el).visibility !== "hidden");
const byText = (text, selector = "button, a, [role=tab], [role=radio], [role=option], .menu-item, summary, label, option") =>
  qa(selector).filter(visible).find((el) => el.textContent.replace(/\s+/g, " ").trim() === text)
  || qa(selector).filter(visible).find((el) => el.textContent.replace(/\s+/g, " ").trim().includes(text));
"""
KEYS = {"Tab": 9, "Enter": 13, "Escape": 27, "ArrowDown": 40, "ArrowUp": 38, "ArrowRight": 39, "ArrowLeft": 37, "Backspace": 8}


def find_browser() -> str | None:
    """Path of a Chromium-based browser, or None. PATCHRONDO_BROWSER overrides the search."""
    override = os.environ.get("PATCHRONDO_BROWSER")
    if override:
        return override if Path(override).is_file() else None
    for candidate in CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    for name in NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def encode_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    """One masked client frame, as RFC 6455 requires of clients."""
    head = bytes([0x80 | opcode])
    size = len(payload)
    if size < 126:
        head += bytes([0x80 | size])
    elif size < 65536:
        head += bytes([0x80 | 126]) + struct.pack(">H", size)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", size)
    mask = secrets.token_bytes(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return head + mask + masked


class WebSocket:
    def __init__(self, url: str, timeout: float = 30):
        assert url.startswith("ws://")
        host, _, path = url[5:].partition("/")
        name, _, port = host.partition(":")
        self.sock = socket.create_connection((name, int(port or 80)), timeout=timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        # No Origin header: the browser then accepts the connection as a local tool.
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        self.buffer = b""
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket handshake failed")
            self.buffer += chunk
        head, _, self.buffer = self.buffer.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"WebSocket handshake refused: {head[:120]!r}")

    def _read(self, count: int) -> bytes:
        while len(self.buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("WebSocket closed")
            self.buffer += chunk
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def send(self, text: str) -> None:
        self.sock.sendall(encode_frame(text.encode("utf-8")))

    def receive(self) -> str:
        """Next complete text message; answers pings and reassembles fragments."""
        message = b""
        while True:
            first, second = self._read(2)
            opcode, size = first & 0x0F, second & 0x7F
            if size == 126:
                size = struct.unpack(">H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if second & 0x80 else b""
            payload = self._read(size)
            if mask:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 0x9:
                self.sock.sendall(encode_frame(payload, 0xA))
                continue
            if opcode == 0x8:
                raise ConnectionError("WebSocket closed by the browser")
            if opcode in (0x0, 0x1, 0x2):
                message += payload
                if first & 0x80:
                    return message.decode("utf-8", errors="replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class BrowserError(RuntimeError):
    pass


class Page:
    """One browser page. `problems` collects console errors, uncaught exceptions and CSP reports."""

    def __init__(self, executable: str, width: int = 1440, height: int = 900):
        self.profile = Path(tempfile.mkdtemp(prefix="patchrondo-browser-"))
        self.process = subprocess.Popen(
            [executable, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
             "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--disable-extensions",
             "--disable-background-networking", "--disable-sync", "--mute-audio", f"--window-size={width},{height}",
             "about:blank"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = self.profile / "DevToolsActivePort"
        deadline = time.monotonic() + 30
        while not port_file.exists():
            if time.monotonic() > deadline or self.process.poll() is not None:
                self.close()
                raise BrowserError("The browser did not open its debugging port")
            time.sleep(0.05)
        time.sleep(0.1)
        port = port_file.read_text(encoding="utf-8").split()[0]
        target = None
        while target is None:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as response:
                target = next((item for item in json.load(response) if item.get("type") == "page"), None)
            if target is None:
                if time.monotonic() > deadline:
                    self.close()
                    raise BrowserError("The browser has no page to control")
                time.sleep(0.1)
        self.socket = WebSocket(target["webSocketDebuggerUrl"])
        self.serial = 0
        self.problems: list[str] = []
        for method in ("Runtime.enable", "Page.enable", "Log.enable"):
            self.call(method)
        self.viewport(width, height)

    # --- protocol -------------------------------------------------------------------

    def _event(self, message: dict) -> None:
        method, params = message.get("method"), message.get("params", {})
        if method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails", {})
            text = (details.get("exception") or {}).get("description") or details.get("text", "")
            self.problems.append(f"exception: {text[:900]}")
        elif method == "Runtime.consoleAPICalled" and params.get("type") in {"error", "assert"}:
            text = " ".join(str(arg.get("value", arg.get("description", ""))) for arg in params.get("args", []))
            self.problems.append(f"console.error: {text[:400]}")
        elif method == "Page.javascriptDialogOpening":
            # A "leave this page?" prompt would block navigation: accept it. The reply is not awaited.
            self.serial += 1
            self.socket.send(json.dumps({"id": self.serial, "method": "Page.handleJavaScriptDialog", "params": {"accept": True}}))
        elif method == "Log.entryAdded" and params.get("entry", {}).get("level") == "error":
            entry = params["entry"]
            self.problems.append(f"{entry.get('source')}: {entry.get('text', '')[:300]} {entry.get('url', '')}")

    def call(self, method: str, params: dict | None = None) -> dict:
        self.serial += 1
        serial = self.serial
        self.socket.send(json.dumps({"id": serial, "method": method, "params": params or {}}))
        while True:
            try:
                message = json.loads(self.socket.receive())
            except TimeoutError as exc:
                detail = str((params or {}).get("expression") or (params or {}).get("url") or "")[-300:]
                raise BrowserError(f"The browser did not answer {method} {detail}") from exc
            if message.get("id") == serial:
                if "error" in message:
                    raise BrowserError(f"{method}: {message['error'].get('message')}")
                return message.get("result", {})
            self._event(message)

    # --- actions --------------------------------------------------------------------

    def js(self, expression: str):
        """Evaluate a JavaScript expression (awaiting a promise) and return its JSON value."""
        result = self.call("Runtime.evaluate", {
            "expression": f"(async () => {{ {HELPERS}\n return ({expression}); }})()",
            "awaitPromise": True, "returnByValue": True, "userGesture": True})
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            raise BrowserError((details.get("exception") or {}).get("description") or details.get("text", "evaluation failed"))
        return result.get("result", {}).get("value")

    def wait(self, expression: str, timeout: float = 10, what: str = ""):
        """Poll until the expression is truthy; raise with `what` after the timeout."""
        deadline = time.monotonic() + timeout
        while True:
            value = self.js(expression)
            if value:
                return value
            if time.monotonic() > deadline:
                raise BrowserError(f"Timed out waiting for {what or expression}")
            time.sleep(0.05)

    def goto(self, url: str) -> None:
        """Navigate and wait until the new document is in place, so a following call cannot race it."""
        self.call("Page.navigate", {"url": url})
        base = url.split("#")[0]
        deadline = time.monotonic() + 20
        while True:
            try:
                if self.js("document.readyState !== 'loading' && location.href.split('#')[0]") == base:
                    return
            except BrowserError:
                pass  # the previous document is being replaced
            if time.monotonic() > deadline:
                raise BrowserError(f"Navigation to {base} did not finish")
            time.sleep(0.05)

    def click(self, selector: str | None = None, text: str | None = None) -> None:
        target = f"byText({json.dumps(text)}{', ' + json.dumps(selector) if selector else ''})" if text else f"q({json.dumps(selector)})"
        found = self.js(f"(() => {{ const el = {target}; if (!el) return false; el.scrollIntoView({{block: 'center'}}); el.focus?.(); el.click(); return true; }})()")
        if not found:
            raise BrowserError(f"Nothing to click: {text or selector}")

    def fill(self, selector: str, text: str) -> None:
        if not self.js(f"(() => {{ const el = q({json.dumps(selector)}); if (!el) return false; el.focus(); el.select?.(); return true; }})()"):
            raise BrowserError(f"No field: {selector}")
        self.call("Input.insertText", {"text": text}) if text else self.press("Backspace")

    def press(self, key: str, modifiers: int = 0) -> None:
        code = KEYS.get(key, ord(key.upper()) if len(key) == 1 else 0)
        base = {"key": key, "code": key if len(key) > 1 else f"Key{key.upper()}", "windowsVirtualKeyCode": code,
                "modifiers": modifiers}
        self.call("Input.dispatchKeyEvent", {"type": "rawKeyDown" if len(key) > 1 or modifiers else "keyDown", **base,
                                             **({"text": key} if len(key) == 1 and not modifiers else {})})
        self.call("Input.dispatchKeyEvent", {"type": "keyUp", **base})

    def viewport(self, width: int, height: int) -> None:
        self.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})

    def reduced_motion(self, enabled: bool) -> None:
        self.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "reduce" if enabled else ""}]})

    def screenshot(self, path: Path) -> None:
        data = self.call("Page.captureScreenshot", {"format": "png"})["data"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))

    def take_problems(self) -> list[str]:
        self.js("1")  # drain pending protocol events
        found, self.problems = self.problems, []
        return found

    def close(self) -> None:
        try:
            self.socket.close()
        except AttributeError:
            pass
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        for _ in range(20):  # the profile stays locked for a moment after exit on Windows
            shutil.rmtree(self.profile, ignore_errors=True)
            if not self.profile.exists():
                break
            time.sleep(0.2)
