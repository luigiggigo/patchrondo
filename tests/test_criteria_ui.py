"""The criteria of a task through the local API: what is shown, and the three actions a person has."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

from patchrondo import evidence
from patchrondo.commands import join_command
from patchrondo.core import attach_evidence, run_task
from patchrondo.storage import read_json, save_json
from support import ServerCase
from test_acceptance import GOOD, MO2, Agents

CHECK = [sys.executable, "-c", "from pathlib import Path; assert Path('feature.txt').read_text() == 'good'"]


class CriteriaApiTests(ServerCase):
    def setUp(self):
        super().setUp()
        which = patch("patchrondo.capabilities.shutil.which", return_value=None)
        which.start()
        self.addCleanup(which.stop)
        self.project, _ = self.add_project()
        self.base = f"/api/projects/{self.project['id']}"
        response, data = self.patch_(f"{self.base}/config", {"tests": {"enabled": True, "trust_acknowledged": True,
                                                                        "commands": [join_command(CHECK)]}})
        self.assertEqual(response.status, 200, data)
        # A run is a child that only leaves a mark: one word per start, no engine and no provider.
        self.marks = self.root / "starts.txt"
        self.app.run_command = lambda project, task_id, flags: [
            sys.executable, "-c", "import sys; open(sys.argv[1], 'a').write(sys.argv[2] + ' ')", str(self.marks), task_id]

    def starts(self):
        return self.marks.read_text().split() if self.marks.exists() else []

    def waiting(self, *criteria):
        """A task whose code is approved and that waits at `validate`, reached with scripted agents."""
        task_id, path = self.add_task(self.project, acceptance=list(criteria))
        state = run_task(Path(self.project["home"]), task_id, adapter=Agents())
        self.assertEqual((state["status"], state["phase"]), ("paused", "validate"))
        return task_id, path, f"{self.base}/tasks/{task_id}"

    def test_a_task_is_created_with_its_plan_and_shown_with_it(self):
        for acceptance in ("one", [1], [{"text": "x", "categories": ["MAGIC"]}], [{"text": "x", "required": "no"}],
                           [{"text": "a", "depends_on": ["AC-01"]}]):
            with self.subTest(acceptance=acceptance):
                response, _ = self.post(f"{self.base}/tasks", {"title": "T", "description": "d", "developer": "claude",
                                                               "reviewer": "codex", "acceptance": acceptance})
                self.assertEqual(response.status, 400)
        self.assertFalse(any((Path(self.project["home"]) / "tasks").glob("T-*")))
        task_id, path = self.add_task(self.project, acceptance=[GOOD, {"text": "Looks fine on a phone", "categories": ["MANUAL"],
                                                                       "required": False}])
        self.assertIn(f"- {GOOD}\n- Looks fine on a phone\n", (path / "task.md").read_text(encoding="utf-8"))
        detail = self.get(f"{self.base}/tasks/{task_id}")[1]
        shown = detail["acceptance"]
        self.assertEqual((shown["tracked"], shown["outcome"], shown["code"]["status"], shown["evidence"], shown["problems"]),
                         (True, "IN_PROGRESS", "PENDING", [], []))
        self.assertEqual([(item["id"], item["status"], item["required"], item["verifier"], item["actions"]["evidence"]) for item in shown["criteria"]],
                         [("AC-01", "PENDING", True, "reviewer", False), ("AC-02", "AWAITING_MANUAL_VALIDATION", False, "user", True)])
        self.assertIn("verified by the review of the code", shown["criteria"][0]["actions"]["evidence_refusal"])
        self.assertEqual([executor["name"] for executor in shown["capabilities"]["executors"]], ["developer", "reviewer", "runtime", "user"])
        self.assertTrue(all(item["label"] for executor in shown["capabilities"]["executors"] for item in executor["capabilities"]))
        summary = self.get("/api/snapshot")[1]["tasks"][0]["acceptance"]
        self.assertEqual((summary["outcome"], summary["code"], summary["total"], summary["required"], summary["verified"], summary["results"]),
                         ("IN_PROGRESS", "PENDING", 2, 1, 0, []))

    def test_what_is_shown_is_judged_against_the_ledger_as_it_is_now(self):
        task_id, path, url = self.waiting(GOOD, MO2)
        entry = attach_evidence(Path(self.project["home"]), task_id, "AC-02", summary="Loaded the mod and played",
                                reference="session.log")["record"]
        evidence.append(path, "invalidation", executor="runtime", result="invalidated", of=[entry["id"]], reason="Taken back")
        saved = read_json(path / "state.json")["acceptance"]
        self.assertEqual((saved["outcome"], [item["status"] for item in saved["criteria"]]), ("RELEASE_READY", ["VERIFIED", "VERIFIED"]))
        shown = self.get(url)[1]["acceptance"]
        self.assertEqual([(item["status"], item["saved_status"]) for item in shown["criteria"]],
                         [("VERIFIED", "VERIFIED"), ("AWAITING_MANUAL_VALIDATION", "VERIFIED")])
        self.assertEqual((shown["outcome"], shown["saved"]["outcome"]), ("AWAITING_VALIDATION", "RELEASE_READY"))
        summary = next(item for item in self.get("/api/snapshot")[1]["tasks"] if item["id"] == task_id)["acceptance"]
        self.assertEqual((summary["outcome"], summary["required_verified"], summary["results"]),
                         ("AWAITING_VALIDATION", 1, ["CODE_APPROVED", "AWAITING_VALIDATION"]))
        self.assertEqual(read_json(path / "state.json")["acceptance"], saved)  # nothing that was shown was written

    def test_a_ledger_cut_short_after_the_last_save_confirms_nothing_on_the_page_or_in_the_list(self):
        task_id, path, url = self.waiting(GOOD, MO2)
        entry = attach_evidence(Path(self.project["home"]), task_id, "AC-02", summary="Loaded the mod and played",
                                reference="session.log")["record"]
        evidence.append(path, "invalidation", executor="runtime", result="invalidated", of=[entry["id"]], reason="Taken back")
        self.assertEqual(self.post(f"{url}/criteria/check")[1]["acceptance"]["outcome"], "AWAITING_VALIDATION")
        file = path / evidence.LEDGER
        data = file.read_bytes()
        file.write_bytes(data[:data.rstrip(b"\n").rfind(b"\n") + 1])  # the invalidation the state saw is gone
        cut, saved = file.read_bytes(), read_json(path / "state.json")
        shown = self.get(url)[1]["acceptance"]
        self.assertEqual(shown["problems"], ["The evidence ledger no longer holds the records the task state had seen"])
        self.assertEqual([(item["status"], item["saved_status"]) for item in shown["criteria"]],
                         [("PENDING", "VERIFIED"), ("AWAITING_MANUAL_VALIDATION", "AWAITING_MANUAL_VALIDATION")])
        self.assertEqual((shown["outcome"], shown["code"]["status"], shown["saved"]["outcome"]),
                         ("IN_PROGRESS", "PENDING", "AWAITING_VALIDATION"))
        self.assertEqual([(record["id"], record["trusted"], record["current"]) for record in shown["evidence"]],
                         [("E-0001", False, False), ("E-0002", False, False), ("E-0003", False, False)])
        summary = next(item for item in self.get("/api/snapshot")[1]["tasks"] if item["id"] == task_id)["acceptance"]
        self.assertEqual((summary["outcome"], summary["verified"], summary["results"]), ("IN_PROGRESS", 0, []))
        self.assertEqual((file.read_bytes(), read_json(path / "state.json")), (cut, saved))  # reading repairs nothing

    def test_the_preflight_is_shown_before_a_task_exists(self):
        response, shown = self.post(f"{self.base}/criteria/preview", {"developer": "claude", "reviewer": "codex", "acceptance": [
            GOOD, "The test suite passes", "Compile the C++ component", "Open a pull request", MO2, "Verificare licenze e regole Nexus"]})
        self.assertEqual(response.status, 200, shown)
        self.assertEqual(shown["summary"]["feasibility"], {"automatable": 2, "needs_tools": 1, "needs_authorization": 1, "manual": 1, "unknown": 1})
        self.assertEqual([item["executor"] for item in shown["criteria"]], ["developer", "runtime", "user", "user", "user", "user"])
        self.assertFalse(any((Path(self.project["home"]) / "tasks").glob("T-*")))
        for body in ({"developer": "gpt", "reviewer": "codex", "acceptance": []}, {"developer": "claude", "reviewer": "codex", "acceptance": "x"},
                     {"developer": "claude", "reviewer": "codex", "acceptance": [], "title": "x"}):
            with self.subTest(body=body):
                self.assertEqual(self.post(f"{self.base}/criteria/preview", body)[0].status, 400)

    def test_a_check_by_the_user_is_recorded_and_completes_the_task_through_a_run(self):
        task_id, path, url = self.waiting(GOOD, MO2)
        good = {"summary": "Loaded the mod in MO2 and played ten minutes", "reference": "session.log"}
        for criterion, body, status in (
                ("AC-02", {"summary": "Loaded the mod in MO2 and played ten minutes"}, 400),  # a statement alone
                ("AC-02", {"summary": "ok", "reference": "log"}, 400), ("AC-02", {**good, "passed": "yes"}, 400),
                ("AC-02", {**good, "resume": "yes"}, 400), ("AC-02", {**good, "verified": True}, 400),
                ("AC-02", {**good, "artifact": "missing.zip"}, 400), ("AC-09", good, 404), ("AC-1", good, 404),
                ("AC-01", good, 409)):  # the review of the code is not replaced by a statement
            with self.subTest(criterion=criterion, body=body):
                self.assertEqual(self.post(f"{url}/criteria/{criterion}/evidence", body)[0].status, status)
        self.assertEqual([entry["kind"] for entry in evidence.read(path).records], ["command", "review"])
        self.assertEqual(self.starts(), [])
        response, data = self.post(f"{url}/criteria/AC-02/evidence", {**good, "resume": True})
        self.assertEqual((response.status, data["completes"], data["run"]["started"], data["acceptance"]["outcome"]),
                         (201, True, True, "RELEASE_READY"), data)
        self.assertEqual(self.starts(), [task_id])
        record = data["record"]
        self.assertEqual((record["kind"], record["executor"], record["criteria"], record["result"]), ("manual", "user", ["AC-02"], "passed"))
        shown = self.get(url)[1]["acceptance"]
        self.assertEqual([(item["id"], item["status"], item["evidence"]) for item in shown["criteria"]],
                         [("AC-01", "VERIFIED", ["E-0002"]), ("AC-02", "VERIFIED", ["E-0003"])])
        self.assertEqual([(entry["id"], entry["kind"], entry["current"]) for entry in shown["evidence"]],
                         [("E-0001", "command", True), ("E-0002", "review", True), ("E-0003", "manual", True)])
        self.assertNotIn("hash", shown["evidence"][0])
        self.assertEqual(read_json(path / "state.json")["status"], "paused")  # the request itself never ends a task

    def test_a_run_is_started_only_when_it_would_call_no_agent(self):
        task_id, path, url = self.waiting(GOOD, MO2, "Checked by hand on a phone")
        response, data = self.post(f"{url}/criteria/AC-02/evidence", {"summary": "Loaded the mod and played", "reference": "log",
                                                                     "resume": True})
        self.assertEqual((response.status, data["completes"], "run" in data), (201, False, False))
        response, data = self.post(f"{url}/criteria/check", {"resume": True})
        self.assertEqual((response.status, data["completes"], data["record"], "run" in data), (200, False, None, False))
        self.assertEqual(self.post(f"{url}/criteria/check", {"force": True})[0].status, 400)
        # Files that changed: the tests and a review would have to run again, which a person starts, knowing what it calls.
        (Path(read_json(path / "state.json")["worktree"]) / "later.txt").write_text("x", encoding="utf-8")
        self.post(f"{url}/criteria/AC-03/evidence", {"summary": "Looked at it on a phone", "reference": "photo", "resume": True})
        self.assertEqual(self.post(f"{url}/criteria/check", {"resume": True})[1]["completes"], False)
        self.assertEqual(self.starts(), [])

    def test_an_authorization_is_an_explicit_decision_and_runs_nothing(self):
        task_id, path, url = self.waiting(GOOD, "Download the GGUF model")
        decide = f"{url}/criteria/AC-02/authorization"
        for body in ({}, {"decision": "maybe"}, {"decision": "granted"}, {"decision": "granted", "confirm": "true"},
                     {"decision": "granted", "confirm": 1}, {"decision": "denied", "note": "x"}):
            with self.subTest(body=body):
                self.assertEqual(self.post(decide, body)[0].status, 400)
        self.assertEqual([entry["kind"] for entry in evidence.read(path).records], ["command", "review"])
        self.assertEqual(self.post(f"{url}/criteria/AC-01/authorization", {"decision": "denied"})[0].status, 409)
        response, data = self.post(decide, {"decision": "denied"})
        self.assertEqual((response.status, data["record"]["result"], data["acceptance"]["outcome"]), (200, "denied", "AWAITING_AUTHORIZATION"))
        self.assertEqual(self.post(f"{url}/criteria/AC-02/evidence", {"summary": "Downloaded it anyway", "reference": "x"})[0].status, 409)
        response, data = self.post(decide, {"decision": "granted", "confirm": True, "resume": True})
        self.assertEqual((response.status, data["record"]["result"], data["acceptance"]["outcome"], data["completes"]),
                         (200, "granted", "AWAITING_VALIDATION", False))
        self.assertEqual(self.starts(), [])
        criterion = self.get(url)[1]["acceptance"]["criteria"][1]
        self.assertEqual((criterion["status"], criterion["authorization"]["decision"], criterion["actions"]),
                         ("AWAITING_MANUAL_VALIDATION", "granted", {"evidence": True, "evidence_refusal": None, "authorize": True}))
        self.assertEqual([event["event"] for event in read_json(path / "state.json")["history"]][-2:],
                         ["authorization_decided", "authorization_decided"])

    def test_actions_are_refused_beside_a_run_and_on_a_task_that_ended(self):
        task_id, path, url = self.waiting(GOOD, "Open a pull request")
        requests = ((f"{url}/criteria/check", {}), (f"{url}/criteria/AC-02/authorization", {"decision": "denied"}),
                    (f"{url}/criteria/AC-02/evidence", {"summary": "Opened the pull request", "reference": "link"}))
        (path / ".run.lock").write_text("{}", encoding="utf-8")
        before = (read_json(path / "state.json"), (path / evidence.LEDGER).read_bytes())
        for target, body in requests:
            with self.subTest(target=target):
                response, data = self.post(target, body)
                self.assertEqual((response.status, data["code"]), (409, "conflict"))
        self.assertEqual((read_json(path / "state.json"), (path / evidence.LEDGER).read_bytes()), before)
        (path / ".run.lock").unlink()
        save_json(path / "state.json", {**before[0], "status": "done", "phase": "complete"})
        for target, body in requests:
            with self.subTest(target=target, ended=True):
                self.assertEqual(self.post(target, body)[0].status, 409)
        self.assertEqual((path / evidence.LEDGER).read_bytes(), before[1])

    def test_a_task_from_before_criteria_were_tracked_is_shown_as_it_is(self):
        task_id, path = self.add_task(self.project, acceptance=[GOOD, MO2])
        url = f"{self.base}/tasks/{task_id}"
        old = read_json(path / "state.json")
        del old["acceptance"]
        save_json(path / "state.json", {**old, "status": "done", "phase": "complete",
                                        "review": {"verdict": "APPROVED", "summary": "fine", "issues": []}})
        detail = self.get(url)[1]
        self.assertEqual(detail["acceptance"], {"tracked": False, "criteria": [{"text": GOOD}, {"text": MO2}], "problems": []})
        self.assertIsNone(detail["summary"]["acceptance"])
        self.assertEqual(self.post(f"{url}/criteria/check")[0].status, 409)  # it ended: nothing is said in retrospect
        self.assertNotIn("acceptance", read_json(path / "state.json"))
        save_json(path / "state.json", old)
        response, data = self.post(f"{url}/criteria/check")
        self.assertEqual((response.status, data["acceptance"]["total"], data["acceptance"]["verified"]), (200, 2, 0))
        shown = self.get(url)[1]["acceptance"]
        self.assertEqual((shown["tracked"], shown["source"], [item["status"] for item in shown["criteria"]]),
                         (True, "task.md", ["PENDING", "AWAITING_MANUAL_VALIDATION"]))

    def test_a_ledger_that_was_edited_is_reported_and_reading_it_changes_nothing(self):
        task_id, path, url = self.waiting(GOOD, MO2)
        file = path / evidence.LEDGER
        file.write_bytes(file.read_bytes().replace(b'"result":"approved"', b'"result":"APPROVED"'))
        damaged = file.read_bytes()
        shown = self.get(url)[1]["acceptance"]
        self.assertEqual(len(shown["problems"]), 1)
        self.assertIn("does not fit the records before it", shown["problems"][0])
        # The state saw E-0002, which no longer fits: no record of this ledger counts, as the next run will find.
        self.assertEqual([(entry["id"], entry["trusted"], entry["current"]) for entry in shown["evidence"]],
                         [("E-0001", False, False), ("E-0002", False, False)])
        self.assertEqual(file.read_bytes(), damaged)  # a reader repairs nothing
        # Judged against the ledger as it reads now, the review that verified AC-01 does not count; what was saved is kept apart.
        first = shown["criteria"][0]
        self.assertEqual((first["status"], first["saved_status"]), ("PENDING", "VERIFIED"))
        self.assertTrue(first["reason"].startswith("Saved as verified, but the evidence ledger does not confirm it now."))
        self.assertEqual((shown["code"]["status"], shown["saved"]["code"]), ("PENDING", "APPROVED"))
        self.assertEqual(self.post(f"{url}/criteria/check")[1]["acceptance"]["verified"], 0)
        self.assertEqual(self.get(url)[1]["acceptance"]["problems"], [])


if __name__ == "__main__":
    import unittest
    unittest.main()
