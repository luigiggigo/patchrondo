"""The operating system's own folder dialog, for the path of a repository in the Add project form.

A page cannot learn a local path from the browser's dialog, so the dashboard,
which runs on the same computer and listens on 127.0.0.1 only, opens the
system's dialog in a separate process and reads back the one path chosen:

- Windows: the Explorer dialog for choosing a folder (`IFileOpenDialog` with
  `FOS_PICKFOLDERS`), through `ctypes` in a child Python process
  (`python -m patchrondo.picker`), so that a failure of the dialog never
  touches the dashboard.
- macOS: the Finder panel of AppleScript's `choose folder`, through `osascript`.
- Linux and other desktops: `kdialog --getexistingdirectory` on KDE, otherwise
  `zenity --file-selection --directory` (GNOME and most GTK desktops), then the
  other one; nothing without a graphical session.

Nothing else is read or written. The path chosen is checked like a typed one
(`app.inspect_repository`); only an absolute folder that exists is answered.
When no dialog can be opened the answer is `unavailable` and the page shows its
own folder list (`app.browse`). One dialog at a time; the page asks for its
result with short requests, so no request waits for a person. Closing the
dashboard closes a dialog left open. `PATCHRONDO_NATIVE_PICKER=0` turns it off.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading

TITLE = "Choose the folder of a Git repository"
WAIT_SECONDS = 20.0  # what one request of the page waits for the dialog before it asks again
LIMIT_SECONDS = 30 * 60  # a dialog left open longer is closed
MESSAGE_LIMIT = 300
SWITCH = "PATCHRONDO_NATIVE_PICKER"
PACKAGE_ROOT = str(Path(__file__).resolve().parents[1])


class Unavailable(Exception):
    """No folder dialog of the system can be opened here; the reason is for people."""


def _start_folder(initial) -> str | None:
    """The folder the dialog opens in: the typed path, or the folder that holds it, when it exists."""
    if not isinstance(initial, str) or not initial.strip():
        return None
    try:
        path = Path(initial.strip()).expanduser()
        if not path.is_absolute():
            return None
        for candidate in (path, path.parent):
            if candidate.is_dir():
                return str(candidate.resolve())
    except (OSError, ValueError):
        return None
    return None


def plan(initial=None, *, system: str | None = None, env=None, which=shutil.which) -> tuple[str, list[str]]:
    """The kind of dialog and the command that opens it on this system; raises `Unavailable` with the reason."""
    env = os.environ if env is None else env
    system = system or sys.platform
    if env.get(SWITCH) == "0":
        raise Unavailable(f"The system's folder dialog is turned off ({SWITCH}=0)")
    start = _start_folder(initial)
    if system == "win32":
        return "windows", [sys.executable, "-m", "patchrondo.picker", "--title", TITLE, "--initial", start or ""]
    if system == "darwin":
        osascript = which("osascript")
        if not osascript:
            raise Unavailable("osascript was not found")
        # Text reaches AppleScript only as arguments (`argv`), never inside the script.
        script = ["on run argv", "activate"]
        script.append("set chosen to choose folder with prompt (item 1 of argv)"
                      + (" default location (POSIX file (item 2 of argv))" if start else ""))
        script += ["return POSIX path of chosen", "end run"]
        command = [osascript]
        for line in script:
            command += ["-e", line]
        return "osascript", [*command, TITLE, *([start] if start else [])]
    if not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        raise Unavailable("No graphical session was found (neither DISPLAY nor WAYLAND_DISPLAY is set)")
    order = ("kdialog", "zenity") if "KDE" in env.get("XDG_CURRENT_DESKTOP", "").upper() else ("zenity", "kdialog")
    for name in order:
        found = which(name)
        if not found:
            continue
        if name == "zenity":
            return "zenity", [found, "--file-selection", "--directory", f"--title={TITLE}",
                              *([f"--filename={start.rstrip('/')}/"] if start else [])]
        return "kdialog", [found, "--title", TITLE, "--getexistingdirectory", start or str(Path.home())]
    raise Unavailable("Neither zenity nor kdialog is installed")


def _folder(text: str) -> dict:
    """The answer for a path a dialog printed: only an absolute folder that exists."""
    if not text:
        return {"status": "failed", "message": "The folder dialog returned no path"}
    path = Path(text)
    if len(text) > 1 and text.endswith(("/", "\\")) and path.anchor != text:
        path = Path(text.rstrip("/\\"))
    try:
        if path.is_absolute() and path.is_dir():
            return {"status": "picked", "path": str(path)}
    except (OSError, ValueError):
        pass
    return {"status": "failed", "message": "The folder dialog returned a path that is not a folder"}


def _short(text: str) -> str:
    text = " ".join((text or "").split())
    return text[:MESSAGE_LIMIT]


def read(kind: str, returncode: int, stdout: str, stderr: str) -> dict:
    """What a dialog's process said: picked (with its path), cancelled, unavailable or failed (with a message)."""
    line = (stdout or "").splitlines()[0] if (stdout or "").strip() else ""
    if kind == "windows":
        try:
            found = json.loads(line)
        except ValueError:
            found = None
        if not isinstance(found, dict) or found.get("status") not in {"picked", "cancelled", "unavailable", "failed"}:
            return {"status": "failed", "message": _short(stderr) or f"The folder dialog ended with exit status {returncode}"}
        if found["status"] == "picked":
            return _folder(found.get("path") if isinstance(found.get("path"), str) else "")
        if found["status"] == "cancelled":
            return {"status": "cancelled"}
        return {"status": found["status"], "message": _short(str(found.get("message") or "")) or "The folder dialog could not be opened"}
    if kind == "osascript":
        if returncode == 0:
            return _folder(line)
        if "(-128)" in (stderr or ""):
            return {"status": "cancelled"}
        if "(-1713)" in (stderr or "") or "(-1712)" in (stderr or ""):
            return {"status": "unavailable", "message": "macOS did not allow a dialog from this session"}
        return {"status": "failed", "message": _short(stderr) or f"osascript ended with exit status {returncode}"}
    if kind in {"zenity", "kdialog"}:
        if returncode == 0:
            return _folder(line)
        if returncode == 1 and not line:
            return {"status": "cancelled"}
        return {"status": "failed", "message": _short(stderr) or f"{kind} ended with exit status {returncode}"}
    raise ValueError(f"Unknown kind of dialog: {kind}")


def _launch(kind: str, command: list[str]) -> subprocess.Popen:
    env = os.environ.copy()
    extra = {}
    if kind == "windows":
        inherited = [os.path.abspath(entry) for entry in env.get("PYTHONPATH", "").split(os.pathsep) if entry]
        env["PYTHONPATH"] = os.pathsep.join([PACKAGE_ROOT, *inherited])  # also without installation
        extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", env=env, **extra)


class Picker:
    """At most one folder dialog at a time, and its answer for the page that opened it.

    `open` starts a dialog (or joins the one already open) and `wait` returns
    its answer, or `{"status": "open", "id": ...}` while the person still
    chooses. `launch(kind, command)` returns a `subprocess.Popen`; tests and
    the browser check give their own, so they never open a real dialog.
    """

    def __init__(self, launch=_launch, planner=plan, wait_seconds: float = WAIT_SECONDS, limit_seconds: float = LIMIT_SECONDS):
        self.launch, self.planner = launch, planner
        self.wait_seconds, self.limit_seconds = wait_seconds, limit_seconds
        self._lock = threading.Lock()
        self._session: dict | None = None
        self.closed = False

    def open(self, initial=None) -> dict:
        if initial is not None and not isinstance(initial, str):
            raise ValueError("path must be text or null")
        with self._lock:
            if self.closed:
                return {"status": "unavailable", "message": "PatchRondo is shutting down"}
            session = self._session
            if session is None or session["done"].is_set():
                try:
                    kind, command = self.planner(initial)
                except Unavailable as exc:
                    return {"status": "unavailable", "message": str(exc)}
                session = {"id": secrets.token_urlsafe(9), "kind": kind, "done": threading.Event(), "result": None, "process": None}
                try:
                    session["process"] = self.launch(kind, command)
                except OSError as exc:
                    return {"status": "unavailable", "message": _short(f"The folder dialog could not be started: {exc}")}
                self._session = session
                threading.Thread(target=self._watch, args=(session,), name="patchrondo-picker", daemon=True).start()
        return self.wait(session["id"])

    def _watch(self, session: dict) -> None:
        process = session["process"]
        try:
            stdout, stderr = process.communicate(timeout=self.limit_seconds)
            result = read(session["kind"], process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            result = {"status": "failed", "message": "The folder dialog stayed open too long and was closed"}
        except Exception as exc:  # noqa: BLE001 - a dialog's failure is an answer, never an error of the dashboard
            result = {"status": "failed", "message": _short(str(exc))}
        if self.closed:
            result = {"status": "unavailable", "message": "PatchRondo is shutting down"}
        session["result"] = result
        session["done"].set()

    def wait(self, session_id) -> dict:
        """The answer of the dialog `session_id`, waiting up to `wait_seconds`; an answer is given once."""
        if not isinstance(session_id, str):
            raise ValueError("id must be text")
        with self._lock:
            session = self._session
        if session is None or session["id"] != session_id:
            raise LookupError("That folder dialog is no longer open")
        if not session["done"].wait(self.wait_seconds):
            return {"status": "open", "id": session_id}
        with self._lock:
            if self._session is not session:
                raise LookupError("That folder dialog is no longer open")
            self._session = None
        return dict(session["result"])

    def close(self) -> None:
        """Refuse new dialogs and close one left open."""
        with self._lock:
            self.closed = True
            session = self._session
        process = session.get("process") if session else None
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass


# --- Windows: the Explorer dialog, in its own process --------------------------------------------------------

FOS_NOCHANGEDIR = 0x8
FOS_PICKFOLDERS = 0x20
FOS_FORCEFILESYSTEM = 0x40
FOS_PATHMUSTEXIST = 0x800
SIGDN_FILESYSPATH = 0x80058000
ERROR_CANCELLED = 0x800704C7 - (1 << 32)  # HRESULT_FROM_WIN32(ERROR_CANCELLED) as a signed value
CLSID_FILE_OPEN_DIALOG = "{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}"
IID_FILE_OPEN_DIALOG = "{D57C7288-D4AD-4768-BE02-9D969532D960}"
IID_SHELL_ITEM = "{43826D1E-E718-42EE-BC55-A1E261C37BFE}"
# Positions in the vtables (shobjidl_core.h): IUnknown 0-2, IModalWindow::Show 3, then IFileDialog.
SHOW, SET_OPTIONS, GET_OPTIONS, SET_FOLDER, SET_TITLE, GET_RESULT = 3, 9, 10, 12, 17, 20
RELEASE, GET_DISPLAY_NAME = 2, 5


def windows_dialog(initial: str | None, title: str) -> dict:  # pragma: no cover - needs a Windows desktop
    """Show the Explorer folder dialog and return the answer `read` expects for the kind `windows`."""
    import ctypes
    from ctypes import wintypes

    ole32, shell32, user32 = ctypes.WinDLL("ole32"), ctypes.WinDLL("shell32"), ctypes.WinDLL("user32")

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    HRESULT = ctypes.c_long
    ole32.CLSIDFromString.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(GUID))
    ole32.CLSIDFromString.restype = HRESULT
    ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    ole32.CoInitializeEx.restype = HRESULT
    ole32.CoCreateInstance.argtypes = (ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
    ole32.CoCreateInstance.restype = HRESULT
    ole32.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
    ole32.CoTaskMemFree.restype = None
    shell32.SHCreateItemFromParsingName.argtypes = (wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
    shell32.SHCreateItemFromParsingName.restype = HRESULT
    user32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p)
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = (wintypes.HWND,)
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.GetSystemMetrics.argtypes = (ctypes.c_int,)

    def guid(text: str) -> GUID:
        value = GUID()
        if ole32.CLSIDFromString(text, ctypes.byref(value)) < 0:
            raise OSError(f"Unreadable GUID {text}")
        return value

    def call(obj, index: int, *args, argtypes=()):
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        return ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *argtypes)(vtable[index])(obj, *args)

    def release(obj) -> None:
        if obj:
            call(obj, RELEASE)

    try:  # a sharp dialog on screens scaled above 100 %
        user32.SetProcessDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        pass
    started = ole32.CoInitializeEx(None, 0x2 | 0x4)  # apartment threaded, no OLE1 DDE
    if started < 0:
        return {"status": "unavailable", "message": f"COM could not start (HRESULT {started & 0xFFFFFFFF:#010x})"}
    dialog, item, chosen, owner = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p(), None
    try:
        made = ole32.CoCreateInstance(ctypes.byref(guid(CLSID_FILE_OPEN_DIALOG)), None, 1, ctypes.byref(guid(IID_FILE_OPEN_DIALOG)),
                                      ctypes.byref(dialog))
        if made < 0:
            return {"status": "unavailable", "message": f"The folder dialog is not available (HRESULT {made & 0xFFFFFFFF:#010x})"}
        options = wintypes.DWORD()
        call(dialog, GET_OPTIONS, ctypes.byref(options), argtypes=(ctypes.POINTER(wintypes.DWORD),))
        call(dialog, SET_OPTIONS, options.value | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | FOS_NOCHANGEDIR,
             argtypes=(wintypes.DWORD,))
        call(dialog, SET_TITLE, title, argtypes=(wintypes.LPCWSTR,))
        if initial and shell32.SHCreateItemFromParsingName(initial, None, ctypes.byref(guid(IID_SHELL_ITEM)), ctypes.byref(item)) >= 0:
            call(dialog, SET_FOLDER, item, argtypes=(ctypes.c_void_p,))
        # An invisible topmost owner in the middle of the screen: the dialog opens there, above the browser,
        # even when Windows keeps the focus where it was.
        owner = user32.CreateWindowExW(0x8 | 0x80, "STATIC", title, 0x80000000, user32.GetSystemMetrics(0) // 2,
                                       user32.GetSystemMetrics(1) // 3, 0, 0, None, None, None, None)
        if owner:
            user32.SetForegroundWindow(owner)
        shown = call(dialog, SHOW, owner, argtypes=(wintypes.HWND,))
        if shown == ERROR_CANCELLED:
            return {"status": "cancelled"}
        if shown < 0:
            return {"status": "failed", "message": f"The folder dialog failed (HRESULT {shown & 0xFFFFFFFF:#010x})"}
        if call(dialog, GET_RESULT, ctypes.byref(chosen), argtypes=(ctypes.POINTER(ctypes.c_void_p),)) < 0 or not chosen:
            return {"status": "failed", "message": "The folder dialog gave no result"}
        name = ctypes.c_void_p()
        if call(chosen, GET_DISPLAY_NAME, SIGDN_FILESYSPATH, ctypes.byref(name),
                argtypes=(wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))) < 0 or not name:
            return {"status": "failed", "message": "The chosen folder has no path on disk"}
        try:
            return {"status": "picked", "path": ctypes.wstring_at(name)}
        finally:
            ole32.CoTaskMemFree(name)
    finally:
        for obj in (chosen, item, dialog):
            release(obj)
        if owner:
            user32.DestroyWindow(owner)
        ole32.CoUninitialize()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - the child process of `plan` on Windows
    import argparse

    parser = argparse.ArgumentParser(prog="python -m patchrondo.picker", description="Open the Windows folder dialog; print JSON.")
    parser.add_argument("--title", default=TITLE)
    parser.add_argument("--initial", default="")
    args = parser.parse_args(argv)
    try:
        result = windows_dialog(args.initial or None, args.title) if sys.platform == "win32" else \
            {"status": "unavailable", "message": "This dialog exists on Windows only"}
    except Exception as exc:  # noqa: BLE001 - reported to the dashboard as the dialog's failure
        result = {"status": "failed", "message": _short(f"{type(exc).__name__}: {exc}")}
    sys.stdout.write(json.dumps(result) + "\n")  # ASCII only: paths are escaped
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
