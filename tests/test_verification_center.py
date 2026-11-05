"""The Verification Center's HTTP routes: the same token, Host and answer rules as every call, bounded pages, and downloads.

The server is the real one (`ui.DashboardServer`) on the loopback interface;
no provider CLI is started. Ledgers are written record by record with their
hash chain, as `evidence.append` writes them.
"""
import json
from pathlib import Path
import re
import time

from patchrondo import bundle, evidence

from support import ServerCase


def write_ledger(path: Path, count: int, every: int = 10) -> None:
    """`count` command records, one in `every` named by a later invalidation, chained as the engine chains them."""
    lines, previous, seq = [], "", 0

    def add(fields: dict) -> str:
        nonlocal previous, seq
        seq += 1
        record = {"version": 1, "id": f"E-{seq:04d}", "seq": seq, "at": "2026-11-05T10:00:00+00:00", **fields, "prev": previous}
        record["hash"] = evidence._digest(record)
        previous = record["hash"]
        lines.append(json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
        return record["id"]

    named = []
    for number in range(count):
        identifier = add({"kind": "command", "executor": "runtime", "criteria": ["AC-01"], "result": "passed" if number % 3 else "failed",
                          "exit_code": 0, "command": ["python", "-m", "unittest"], "run": f"{number // 50:03d}-aaaaaaaa",
                          "iteration": 1, "fingerprint": "f" * 64, "scope": "code",
                          "check": {"id": f"check-{number % 4}", "type": "command", "config_sha256": "c" * 64}})
        if number % every == 0:
            named.append(identifier)
    add({"kind": "invalidation", "executor": "runtime", "result": "invalidated", "of": named, "cause": "WORKSPACE_CHANGED",
         "reason": "The files of the task changed after this was recorded", "validity": "STALE", "iteration": 1})
    (path / evidence.LEDGER).write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


class RouteTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.project, _repo = self.add_project()
        self.task_id, self.path = self.add_task(self.project, title="Release <script>alert(1)</script>")
        self.base = f"/api/projects/{self.project['id']}/tasks/{self.task_id}/verification"

    def test_every_route_needs_the_token_and_a_local_host(self):
        for route in ("", "/evidence", "/evidence/E-0001", "/bundle/preview", "/bundle?mode=summary"):
            response, data = self.get(self.base + route, token=False)
            self.assertEqual((response.status, data["code"]), (401, "unauthorized"), route)
            self.assertNotEqual(response.getheader("Content-Type"), "application/zip")
            response, data = self.get(self.base + route, host="attacker.example:80")
            self.assertEqual(response.status, 403, route)

    def test_a_task_never_evaluated_answers_without_a_gate_and_its_text_as_data(self):
        response, view = self.get(self.base)
        self.assertEqual(response.status, 200, view)
        self.assertEqual((view["gate"], view["gate_state"], view["tracked"]), (None, "not_evaluated", True))
        self.assertEqual(view["task"]["title"], "Release <script>alert(1)</script>")  # data, never markup: the page inserts text
        self.assertEqual(response.getheader("Content-Type"), "application/json; charset=utf-8")

    def test_unknown_tasks_records_and_modes_are_answered_as_errors(self):
        other = f"/api/projects/{self.project['id']}/tasks/T-000000000000/verification"
        for route, status in ((other, 404), (other + "/bundle?mode=summary", 404), ("/api/projects/P-00000000/tasks/T-000000000000/verification", 404),
                              (self.base + "/evidence/E-0001", 404), (self.base + "/evidence/..%2Fstate.json", 404),
                              (self.base + "/bundle?mode=everything", 400), (self.base + "/bundle/preview?mode=..", 400),
                              (self.base + "/evidence?validity=GOOD", 400), (self.base + "/evidence?limit=100000", 400)):
            response, data = self.get(route)
            self.assertEqual(response.status, status, route)
            self.assertIn("error", data, route)
            self.assertNotIn(str(self.home), data["error"], route)

    def test_a_download_is_the_bundle_the_manifest_describes(self):
        response, data = self.get(self.base + "/bundle?mode=summary")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/zip")
        disposition = response.getheader("Content-Disposition")
        self.assertRegex(disposition, r'^attachment; filename="patchrondo-evidence-T-[0-9a-f]{12}-summary\.zip"$')
        self.assertEqual((response.getheader("X-Content-Type-Options"), response.getheader("Cache-Control")), ("nosniff", "no-store"))
        self.assertIn("default-src 'none'", response.getheader("Content-Security-Policy"))
        found = bundle.verify(data)
        self.assertEqual(found["manifest"]["bundle_id"], response.getheader("X-PatchRondo-Bundle"))
        self.assertEqual(found["manifest"]["task_id"], self.task_id)
        self.assertEqual(found["status"], "INCOMPLETE")  # never evaluated: the bundle says what it lacks
        response, preview = self.get(self.base + "/bundle/preview?mode=detailed")
        self.assertEqual((response.status, preview["manifest"]["mode"]), (200, "detailed"))
        self.assertFalse(list(self.path.glob("*.zip")))  # nothing written beside the task

    def test_a_large_ledger_is_read_in_small_pages(self):
        write_ledger(self.path, 4990)
        started = time.monotonic()
        response, view = self.get(self.base)
        first = time.monotonic() - started
        self.assertEqual(response.status, 200, view)
        self.assertEqual(view["counts"]["validity"]["STALE"], 499)
        self.assertEqual(view["ledger"]["count"], 4991)
        response, page = self.get(self.base + "/evidence?validity=STALE&limit=50")
        self.assertEqual((page["total"], page["matched"], len(page["items"])), (4990, 499, 50))
        self.assertEqual(page["items"][0]["id"], "E-4981")  # newest first
        self.assertLess(len(json.dumps(page)), 60_000)  # a page, never the ledger
        response, again = self.get(self.base + "/evidence?validity=STALE&limit=50&offset=50&q=check-1")
        self.assertTrue(all(row["check"] == "check-1" for row in again["items"]))
        response, record = self.get(self.base + "/evidence/E-0011")
        self.assertEqual((record["assessment"]["validity"], [entry["id"] for entry in record["invalidations"]]), ("STALE", ["E-4991"]))
        started = time.monotonic()
        self.get(self.base + "/evidence?result=failed&limit=200")
        cached = time.monotonic() - started
        self.assertLess(first, 20)  # generous: this is a functional bound, measured separately for the record
        self.assertLess(cached, 10)

    def test_the_file_name_of_a_download_holds_no_path(self):
        response, _data = self.get(self.base + "/bundle?mode=detailed")
        name = re.search(r'filename="([^"]+)"', response.getheader("Content-Disposition")).group(1)
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        self.assertNotIn("..", name)
