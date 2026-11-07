"""The system's folder dialog of the Add project form: commands per system, answers, one dialog at a time, the route.

No test here opens a real dialog except the opt-in one at the end
(`PATCHRONDO_REAL_PICKER=1`, native Windows): the others give scripted
child processes or no process at all.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from support import ServerCase

from patchrondo import picker


def child(code: str, *args: str) -> tuple[str, list[str]]:
    """A dialog of the kind `windows` played by a Python child that runs `code`."""
    return "windows", [sys.executable, "-c", code, *args]


def tools(*names: str):
    return lambda name: f"/usr/bin/{name}" if name in names else None


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve()

    def test_windows_opens_the_explorer_dialog_in_a_child_python(self):
        kind, command = picker.plan(str(self.folder), system="win32", env={})
        self.assertEqual(kind, "windows")
        self.assertEqual(command, [sys.executable, "-m", "patchrondo.picker", "--title", picker.TITLE, "--initial", str(self.folder)])

    def test_macos_passes_text_to_applescript_only_as_arguments(self):
        tricky = self.folder / ('a "quoted" folder' if os.name != "nt" else "it's a quoted folder")  # Windows refuses '"'
        tricky.mkdir()
        kind, command = picker.plan(str(tricky), system="darwin", env={}, which=tools("osascript"))
        self.assertEqual(kind, "osascript")
        script = [command[i + 1] for i, part in enumerate(command) if part == "-e"]
        self.assertEqual(script, ["on run argv", "activate",
                                  "set chosen to choose folder with prompt (item 1 of argv) default location (POSIX file (item 2 of argv))",
                                  "return POSIX path of chosen", "end run"])
        self.assertEqual(command[-2:], [picker.TITLE, str(tricky)])
        self.assertFalse(any("quoted" in line for line in script))
        _, without = picker.plan(None, system="darwin", env={}, which=tools("osascript"))
        self.assertEqual(without[-1], picker.TITLE)
        self.assertNotIn("default location", " ".join(without))
        with self.assertRaisesRegex(picker.Unavailable, "osascript"):
            picker.plan(None, system="darwin", env={}, which=tools())

    def test_linux_uses_zenity_or_kdialog_and_needs_a_graphical_session(self):
        display = {"DISPLAY": ":0"}
        kind, command = picker.plan(str(self.folder), system="linux", env=display, which=tools("zenity", "kdialog"))
        self.assertEqual((kind, command), ("zenity", ["/usr/bin/zenity", "--file-selection", "--directory", f"--title={picker.TITLE}",
                                                      f"--filename={str(self.folder).rstrip('/')}/"]))
        kind, command = picker.plan(str(self.folder), system="linux", env={**display, "XDG_CURRENT_DESKTOP": "KDE"},
                                    which=tools("zenity", "kdialog"))
        self.assertEqual((kind, command), ("kdialog", ["/usr/bin/kdialog", "--title", picker.TITLE, "--getexistingdirectory", str(self.folder)]))
        self.assertEqual(picker.plan(None, system="linux", env={"WAYLAND_DISPLAY": "wayland-0"}, which=tools("kdialog"))[0], "kdialog")
        self.assertEqual(picker.plan(None, system="freebsd14", env=display, which=tools("zenity"))[0], "zenity")
        self.assertNotIn("--filename", " ".join(picker.plan(None, system="linux", env=display, which=tools("zenity"))[1]))
        with self.assertRaisesRegex(picker.Unavailable, "graphical session"):
            picker.plan(None, system="linux", env={}, which=tools("zenity"))
        with self.assertRaisesRegex(picker.Unavailable, "zenity nor kdialog"):
            picker.plan(None, system="linux", env=display, which=tools())

    def test_the_switch_turns_it_off_everywhere(self):
        for system in ("win32", "darwin", "linux"):
            with self.subTest(system=system), self.assertRaisesRegex(picker.Unavailable, "PATCHRONDO_NATIVE_PICKER=0"):
                picker.plan(None, system=system, env={"PATCHRONDO_NATIVE_PICKER": "0", "DISPLAY": ":0"}, which=tools("osascript", "zenity"))

    def test_the_dialog_starts_in_the_typed_folder_or_the_one_that_holds_it(self):
        (self.folder / "file.txt").write_text("x", encoding="utf-8")
        start = lambda typed: picker.plan(typed, system="win32", env={})[1][-1]  # noqa: E731
        self.assertEqual(start(str(self.folder)), str(self.folder))
        self.assertEqual(start(str(self.folder / "file.txt")), str(self.folder))
        self.assertEqual(start(str(self.folder / "not-yet")), str(self.folder))
        for typed in (None, "", "   ", "relative/path", str(self.folder / "a" / "b" / "c"), 7):
            with self.subTest(typed=typed):
                self.assertEqual(start(typed), "")


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve()

    def test_a_chosen_folder_is_answered_only_when_it_is_an_absolute_folder(self):
        found = str(self.folder)
        self.assertEqual(picker.read("zenity", 0, found + "\n", ""), {"status": "picked", "path": found})
        self.assertEqual(picker.read("kdialog", 0, found + "\n", ""), {"status": "picked", "path": found})
        self.assertEqual(picker.read("osascript", 0, found + "/\n", ""), {"status": "picked", "path": found})
        self.assertEqual(picker.read("windows", 0, json.dumps({"status": "picked", "path": found}) + "\n", ""), {"status": "picked", "path": found})
        (self.folder / "file.txt").write_text("x", encoding="utf-8")
        for path in (str(self.folder / "missing"), str(self.folder / "file.txt"), "relative", ""):
            with self.subTest(path=path):
                self.assertEqual(picker.read("zenity", 0, path + "\n", "")["status"], "failed")
        root = Path(self.folder.anchor)
        self.assertEqual(picker.read("zenity", 0, str(root) + "\n", ""), {"status": "picked", "path": str(root)})

    def test_cancel_unavailable_and_failures_are_told_apart(self):
        self.assertEqual(picker.read("zenity", 1, "", ""), {"status": "cancelled"})
        self.assertEqual(picker.read("kdialog", 1, "", ""), {"status": "cancelled"})
        self.assertEqual(picker.read("zenity", 5, "", "Gtk-WARNING: cannot open display")["status"], "failed")
        self.assertEqual(picker.read("osascript", 1, "", "execution error: User canceled. (-128)"), {"status": "cancelled"})
        self.assertEqual(picker.read("osascript", 1, "", "execution error: No user interaction allowed. (-1713)")["status"], "unavailable")
        self.assertEqual(picker.read("osascript", 1, "", "execution error: something else (-50)")["status"], "failed")
        self.assertEqual(picker.read("windows", 0, '{"status": "cancelled"}\n', ""), {"status": "cancelled"})
        self.assertEqual(picker.read("windows", 0, '{"status": "unavailable", "message": "COM"}', ""), {"status": "unavailable", "message": "COM"})
        for out in ("", "not json", "[]", '{"status": "maybe"}'):
            with self.subTest(out=out):
                self.assertEqual(picker.read("windows", 1, out, "Traceback ...")["status"], "failed")
        self.assertLessEqual(len(picker.read("zenity", 9, "", "x" * 10_000)["message"]), picker.MESSAGE_LIMIT)
        with self.assertRaises(ValueError):
            picker.read("unknown", 0, "", "")

    @unittest.skipIf(sys.platform == "win32", "on Windows the module's main opens the real dialog")
    def test_the_child_entry_point_says_unavailable_outside_windows(self):
        done = subprocess.run([sys.executable, "-m", "patchrondo.picker"], capture_output=True, text=True, timeout=60,
                              env={**os.environ, "PYTHONPATH": picker.PACKAGE_ROOT})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["status"], "unavailable")


class PickerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve()
        self.planned = []

    def make(self, code: str, *args: str, **options) -> picker.Picker:
        def planner(initial):
            self.planned.append(initial)
            return child(code, *args)
        made = picker.Picker(planner=planner, **options)
        self.addCleanup(made.close)
        return made

    def test_the_page_asks_again_while_the_dialog_is_open_and_gets_the_answer_once(self):
        folders = self.make("import json, sys, time; time.sleep(1.0); print(json.dumps({'status': 'picked', 'path': sys.argv[1]}))",
                            str(self.folder), wait_seconds=0.2)
        first = folders.open(str(self.folder))
        self.assertEqual(first["status"], "open")
        answer = first
        while answer["status"] == "open":
            answer = folders.wait(first["id"])
        self.assertEqual(answer, {"status": "picked", "path": str(self.folder)})
        with self.assertRaises(LookupError):
            folders.wait(first["id"])
        with self.assertRaises(LookupError):
            folders.wait("another")
        with self.assertRaises(ValueError):
            folders.wait(7)
        self.assertEqual(self.planned, [str(self.folder)])

    def test_one_dialog_at_a_time_a_second_press_joins_it(self):
        folders = self.make("import time; time.sleep(30)", wait_seconds=0.1)
        first = folders.open(None)
        second = folders.open(str(self.folder))
        self.assertEqual((first["status"], second["status"], second["id"]), ("open", "open", first["id"]))
        self.assertEqual(len(self.planned), 1)

    def test_closing_the_dashboard_closes_the_dialog_and_refuses_new_ones(self):
        launched = []

        def launch(kind, command):
            process = picker._launch(kind, command)
            launched.append(process)
            return process
        folders = picker.Picker(planner=lambda initial: child("import time; time.sleep(60)"), launch=launch, wait_seconds=0.1)
        opened = folders.open(None)
        self.assertEqual(opened["status"], "open")
        folders.close()
        launched[0].wait(15)
        self.assertIsNotNone(launched[0].poll())
        folders.wait_seconds = 15  # the watcher may still be storing the answer
        self.assertEqual(folders.wait(opened["id"])["status"], "unavailable")
        self.assertEqual(folders.open(None)["status"], "unavailable")
        self.assertEqual(len(launched), 1)

    def test_a_dialog_left_open_too_long_is_closed(self):
        folders = self.make("import time; time.sleep(60)", wait_seconds=10, limit_seconds=0.5)
        started = time.monotonic()
        answer = folders.open(None)
        self.assertEqual(answer["status"], "failed")
        self.assertIn("too long", answer["message"])
        self.assertLess(time.monotonic() - started, 9)

    def test_no_dialog_is_unavailable_and_wrong_input_is_refused(self):
        def nothing(initial):
            raise picker.Unavailable("No graphical session was found")
        self.assertEqual(picker.Picker(planner=nothing).open(None), {"status": "unavailable", "message": "No graphical session was found"})

        def refused(kind, command):
            raise FileNotFoundError("zenity")
        answer = picker.Picker(planner=lambda initial: ("zenity", ["zenity"]), launch=refused).open(None)
        self.assertEqual(answer["status"], "unavailable")
        with self.assertRaises(ValueError):
            picker.Picker(planner=nothing).open(7)

    def test_a_child_that_dies_is_a_failure_not_an_answer(self):
        answer = self.make("import sys; sys.stderr.write('boom'); sys.exit(3)", wait_seconds=10).open(None)
        self.assertEqual(answer, {"status": "failed", "message": "boom"})


class RouteTests(ServerCase):
    def test_the_route_opens_asks_again_and_answers_once(self):
        self.app.picker = picker.Picker(planner=lambda initial: child(
            "import json, sys, time; time.sleep(0.8); print(json.dumps({'status': 'picked', 'path': sys.argv[1]}))", str(self.root)),
            wait_seconds=0.2)
        response, opened = self.post("/api/fs/pick", {"path": str(self.root)})
        self.assertEqual((response.status, opened["status"]), (200, "open"), opened)
        answer = opened
        while answer["status"] == "open":
            response, answer = self.post("/api/fs/pick", {"id": opened["id"]})
            self.assertEqual(response.status, 200, answer)
        self.assertEqual(answer, {"status": "picked", "path": str(self.root)})
        self.assertEqual(self.post("/api/fs/pick", {"id": opened["id"]})[0].status, 404)

    def test_bad_requests_are_refused_and_server_tests_open_no_real_dialog(self):
        for body in ({"path": 7}, {"id": 7}, {"path": None, "id": "x"}, {"other": 1}):
            with self.subTest(body=body):
                self.assertEqual(self.post("/api/fs/pick", body)[0].status, 400)
        self.assertEqual(self.post("/api/fs/pick", {"path": None}, token=False)[0].status, 401)
        response, answer = self.post("/api/fs/pick", {})
        self.assertEqual((response.status, answer["status"]), (200, "unavailable"))
        self.assertIn("PATCHRONDO_NATIVE_PICKER=0", answer["message"])

    def test_stopping_the_dashboard_closes_a_dialog_left_open(self):
        launched = []

        def launch(kind, command):
            launched.append(picker._launch(kind, command))
            return launched[-1]
        self.app.picker = picker.Picker(planner=lambda initial: child("import time; time.sleep(60)"), launch=launch, wait_seconds=0.1)
        self.assertEqual(self.post("/api/fs/pick", {})[1]["status"], "open")
        self.stop()
        launched[0].wait(15)
        self.assertIsNotNone(launched[0].poll())


@unittest.skipUnless(sys.platform == "win32" and os.environ.get("PATCHRONDO_REAL_PICKER") == "1",
                     "opt-in: opens the real Windows folder dialog (PATCHRONDO_REAL_PICKER=1, native Windows desktop)")
class RealWindowsDialogTests(unittest.TestCase):  # pragma: no cover - needs a Windows desktop
    """The Explorer dialog itself, answered by messages to its window: Select Folder, then Cancel."""

    def setUp(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        self.user32 = ctypes.WinDLL("user32")
        self.tmp = tempfile.TemporaryDirectory(prefix="patchrondo picker è ", ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve()

    def window_of(self, pid: int):
        ctypes, wintypes, user32 = self.ctypes, self.wintypes, self.user32
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            found = []

            def each(hwnd, _):
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(owner))
                name = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(wintypes.HWND(hwnd), name, 64)
                if owner.value == pid and name.value == "#32770" and user32.IsWindowVisible(wintypes.HWND(hwnd)):
                    found.append(hwnd)
                return True
            user32.EnumWindows(callback(each), 0)
            if found:
                return found[0]
            time.sleep(0.1)
        return None

    def answer(self, button: int) -> dict:
        kind, command = picker.plan(str(self.folder), system="win32", env={})
        process = picker._launch(kind, command)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        window = self.window_of(process.pid)
        self.assertIsNotNone(window, "the dialog did not appear")
        title = self.ctypes.create_unicode_buffer(256)
        self.user32.GetWindowTextW(self.wintypes.HWND(window), title, 256)
        self.assertEqual(title.value, picker.TITLE)
        self.assertTrue(self.user32.GetWindowLongW(self.wintypes.HWND(window), -20) & 0x8, "the dialog is not above other windows")
        time.sleep(0.5)
        self.user32.PostMessageW(self.wintypes.HWND(window), 0x0111, button, 0)  # WM_COMMAND
        out, err = process.communicate(timeout=30)
        return picker.read(kind, process.returncode, out, err)

    def test_select_folder_answers_the_folder_the_dialog_opened_in(self):
        self.assertEqual(self.answer(1), {"status": "picked", "path": str(self.folder)})

    def test_cancel_is_cancelled(self):
        self.assertEqual(self.answer(2), {"status": "cancelled"})


if __name__ == "__main__":
    unittest.main()
