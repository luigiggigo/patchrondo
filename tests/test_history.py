"""Workflow history: versioned events with IDs of their own, references to the ledger and the findings, and nothing proved.

Providers are scripted: no CLI is started. The letters in the names of the
tests are those of the step's request: A the identity of events, B their
persistence, C older histories, D a review and its findings, E nothing
resolved by being left out, F an idempotent Resume, G pauses, crashes and
recovery, H what the history is not.
"""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from patchrondo import core, evidence, history, recovery
from patchrondo.core import attach_evidence, recheck_acceptance, run_task
from patchrondo.findings import Findings
from patchrondo.storage import read_json, save_json

from test_acceptance import GOOD, MO2, Agents, Case, review
from test_findings import NEWLINE, linked
from test_recovery import RecoveryCase

TASK = "T-0123456789ab"
SCHEMA = ("version", "id", "task_id", "event", "at", "iteration", "phase")
OLDER = [{"at": "2026-10-01T10:00:00+00:00", "event": "run_started", "iteration": 1, "previous_status": "ready"},
         {"at": "2026-10-01T10:00:05+00:00", "event": "development_started", "iteration": 1}]


def ids(events) -> list[str]:
    return [entry.get("id") for entry in events]


def numbered(events) -> bool:
    """Whether the events carry the IDs that follow their places in the history, one each."""
    return ids(events) == [f"WE-{number:04d}" for number in range(1, len(events) + 1)]


class EventTests(unittest.TestCase):
    def state(self, events=()):
        return {"id": TASK, "iteration": 2, "phase": "review", "history": list(events)}

    def test_each_event_carries_the_fields_of_the_schema_and_the_next_id(self):
        """A"""
        state = self.state()
        first = history.append(state, "review_started", model="sonnet")
        second = core._event(state, "review_completed", verdict="APPROVED")
        self.assertEqual({key: first[key] for key in SCHEMA},
                         {"version": 1, "id": "WE-0001", "task_id": TASK, "event": "review_started", "at": first["at"],
                          "iteration": 2, "phase": "review"})
        self.assertTrue(first["at"].endswith("+00:00"))  # UTC, from PatchRondo's clock
        self.assertEqual((second["id"], second["verdict"], first["model"]), ("WE-0002", "APPROVED", "sonnet"))
        self.assertEqual(state["history"], [first, second])

    def test_a_detail_never_sets_a_field_of_the_event(self):
        """A: a detail named like a field is a mistake of the caller, and nothing is appended."""
        state = self.state()
        for field in history.RESERVED:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, field):
                    history.append(state, "run_started", **{field: "forged"})
        with self.assertRaises(ValueError):
            core._event(state, "task_paused", at="2000-01-01T00:00:00+00:00")
        with self.assertRaises(ValueError):
            recovery._audit(state, "recovery_stopped", id="WE-9999")
        self.assertEqual(state["history"], [])

    def test_details_are_bounded_redacted_json(self):
        secret = "sk-" + "a" * 32
        state = self.state()
        entry = history.append(state, "retrieval_failed", message=f"key {secret} " + "x" * 5000, codes=list(range(500)),
                               nested={"a": {"b": {"c": {"d": 1}}}}, where=Path("feature.txt"), ratio=float("nan"))
        written = json.dumps(state["history"], allow_nan=False)
        self.assertNotIn(secret, written)
        self.assertIn("[redacted]", entry["message"])
        self.assertLess(len(entry["message"]), history.TEXT_LIMIT + 40)
        self.assertEqual(entry["codes"], list(range(history.LIST_LIMIT)))
        self.assertEqual((entry["nested"], entry["where"], entry["ratio"]), ({"a": {"b": {"c": None}}}, "feature.txt", None))
        # Nothing is cut without saying so: the path of each list or object cut, and how many entries it had.
        self.assertEqual(entry["truncated"], {"codes": 500, "nested.a.b.c": 1})
        wide = history.append(state, "x", rows=[list(range(150))], table={str(n): n for n in range(120)})
        self.assertEqual(wide["truncated"], {"rows.0": 150, "table": 120})
        self.assertNotIn("truncated", history.append(state, "x", codes=list(range(history.LIST_LIMIT))))

    def test_a_number_goes_past_every_id_and_the_length_of_the_history(self):
        """A, C: an older history is numbered past, an ID in the history is never given again, other values take no number."""
        state = self.state(copy.deepcopy(OLDER))
        self.assertEqual(history.append(state, "development_completed", agent="claude")["id"], "WE-0003")
        self.assertEqual(state["history"][:2], OLDER)
        state = self.state([{"version": 1, "id": "WE-0007", "event": "run_started", "at": OLDER[0]["at"], "iteration": 1}])
        self.assertEqual(history.append(state, "development_started")["id"], "WE-0008")
        state = self.state([{"id": "E-0009", "event": "x"}, {"id": "WE-12"}, "not an event", None])
        self.assertEqual(history.append(state, "development_started")["id"], "WE-0005")
        state = {"iteration": 1, "history": []}  # neither an ID nor a phase: they are recorded as unknown
        self.assertEqual({key: history.append(state, "x")[key] for key in ("id", "task_id", "phase")},
                         {"id": "WE-0001", "task_id": None, "phase": None})


class EngineTests(Case):
    def reviews(self, path) -> list[dict]:
        return [record for record in evidence.read(path).records if record["kind"] == "review"]

    def history(self, path) -> list[dict]:
        return self.state(path)["history"]

    def named(self, path, event) -> list[dict]:
        return [entry for entry in self.history(path) if entry["event"] == event]

    def test_every_event_of_a_run_is_identified_and_saved_with_the_state(self):
        """A, B"""
        task_id, path, _ = self.task(GOOD)
        state = run_task(self.home, task_id, adapter=Agents([review("CHANGES_REQUESTED", [linked(NEWLINE, criterion_id="AC-01")],
                                                                    AC01="CODE_CHANGES_REQUIRED")], writes=["bad", "good"]))
        self.assertEqual(state["status"], "done")
        saved = self.history(path)
        self.assertEqual(saved, state["history"])
        self.assertTrue(numbered(saved))
        self.assertEqual({(entry["version"], entry["task_id"]) for entry in saved}, {(1, task_id)})
        self.assertEqual([(entry["event"], entry["iteration"], entry["phase"]) for entry in saved], [
            ("run_started", 1, "develop"), ("development_started", 1, "develop"), ("development_completed", 1, "develop"),
            ("tests_started", 1, "test"), ("tests_completed", 1, "test"), ("review_started", 1, "review"),
            ("review_completed", 1, "review"), ("development_started", 2, "develop"), ("development_completed", 2, "develop"),
            ("evidence_invalidated", 2, "test"), ("tests_started", 2, "test"), ("tests_completed", 2, "test"),
            ("review_started", 2, "review"), ("review_completed", 2, "review"), ("task_completed", 2, "complete")])
        # Each invalidation names the ledger record that says so; the records it made stop counting are named there.
        invalidated = self.named(path, "evidence_invalidated")[0]
        record = evidence.read(path).get(invalidated["invalidations"][0])
        self.assertEqual((record["kind"], len(record["of"])), ("invalidation", invalidated["records"]))
        report = (path / "report.md").read_text(encoding="utf-8")
        self.assertEqual(report.count("· `"), len(saved))

    def test_an_older_history_stays_as_it_is_and_new_events_are_numbered_past_it(self):
        """C: a task paused by a version that wrote events without IDs runs on; its events are not rewritten."""
        self.configure(commands=[self.check], enabled=False)
        task_id, path, _ = self.task(GOOD)
        self.assertEqual(run_task(self.home, task_id, adapter=Agents())["last_error"]["kind"], "tests_disabled")
        state = self.state(path)
        older = [{"at": entry["at"], "event": entry["event"], "iteration": entry["iteration"],
                  **{key: value for key, value in entry.items() if key not in SCHEMA}} for entry in state["history"]]
        state["history"] = older
        save_json(path / "state.json", state)
        self.configure(commands=[self.check])
        state = run_task(self.home, task_id, adapter=Agents())
        self.assertEqual(state["status"], "done")
        saved = self.history(path)
        self.assertEqual(saved[:len(older)], older)
        self.assertEqual(ids(saved[len(older):]), [f"WE-{number:04d}" for number in range(len(older) + 1, len(saved) + 1)])
        self.assertIn(f"`{older[0]['event']}`", (path / "report.md").read_text(encoding="utf-8"))

    def test_a_review_names_its_ledger_record_and_the_findings_its_issues_were_recorded_under(self):
        """D, E: new, linked by ID and linked by the same words; the approval leaves every finding unresolved."""
        task_id, path, _ = self.task(GOOD)
        first = review("CHANGES_REQUESTED", [linked(NEWLINE, criterion_id="AC-01"), linked("README.md does not mention it",
                                                                                            path="README.md")],
                       AC01="CODE_CHANGES_REQUIRED")
        second = review("CHANGES_REQUESTED", [linked("README.md still says nothing", "RF-002", path="README.md"),
                                              linked("helpers.py repeats the parser", path="helpers.py")], AC01="CODE_CHANGES_REQUIRED")
        third = review("APPROVED", [linked(NEWLINE, criterion_id="AC-01")], AC01="SATISFIED")  # an approval that names a change
        state = run_task(self.home, task_id, adapter=Agents([first, second, third]))
        self.assertEqual((state["status"], state["iteration"]), ("done", 4))
        records, completed = self.reviews(path), self.named(path, "review_completed")
        fields = ("verdict", "reported", "normalized", "evidence", "issues", "finding_ids", "new_findings", "linked_findings",
                  "unrecorded_issues")
        self.assertEqual([{key: entry.get(key) for key in fields} for entry in completed], [
            {"verdict": "CHANGES_REQUESTED", "reported": None, "normalized": None, "evidence": records[0]["id"], "issues": 2,
             "finding_ids": ["RF-001", "RF-002"], "new_findings": ["RF-001", "RF-002"], "linked_findings": [], "unrecorded_issues": 0},
            {"verdict": "CHANGES_REQUESTED", "reported": None, "normalized": None, "evidence": records[1]["id"], "issues": 2,
             "finding_ids": ["RF-002", "RF-003"], "new_findings": ["RF-003"], "linked_findings": ["RF-002"], "unrecorded_issues": 0},
            {"verdict": "CHANGES_REQUESTED", "reported": "APPROVED", "normalized": "code_change_named", "evidence": records[2]["id"],
             "issues": 1, "finding_ids": ["RF-001"], "new_findings": [], "linked_findings": ["RF-001"], "unrecorded_issues": 0},
            {"verdict": "APPROVED", "reported": None, "normalized": None, "evidence": records[3]["id"], "issues": 0,
             "finding_ids": [], "new_findings": [], "linked_findings": [], "unrecorded_issues": 0}])
        # The event names the record of its review, and that record names the same findings. The reviewer's words stay out.
        self.assertEqual([records[index].get("findings") or [] for index in range(4)], [entry["finding_ids"] for entry in completed])
        self.assertNotIn("Checked the worktree", json.dumps(self.history(path)))
        # E: what the approval left out waits for a verification; nothing in the history says that a problem is gone.
        done = self.named(path, "task_completed")[0]
        self.assertEqual(done["pending_verification"], ["RF-001", "RF-002", "RF-003"])
        saved = Findings.load(self.state(path))
        self.assertEqual({item.status for item in saved.items}, {"PENDING_VERIFICATION"})
        self.assertNotIn("RESOLVED", json.dumps(self.history(path)))

    def test_findings_left_out_are_named_nowhere_and_an_approval_only_makes_them_pending(self):
        """E: the approving review lists none of them; `code_approved` names those waiting for a verification."""
        task_id, path, _ = self.task(GOOD, MO2)
        state = run_task(self.home, task_id, adapter=Agents([
            review("CHANGES_REQUESTED", [linked(NEWLINE, criterion_id="AC-01")], AC01="CODE_CHANGES_REQUIRED",
                   AC02="MANUAL_ACTION_REQUIRED"),
            review(AC01="SATISFIED", AC02="MANUAL_ACTION_REQUIRED")]))
        self.assertEqual((state["status"], state["phase"], state["last_error"]["kind"]), ("paused", "validate", "awaiting_validation"))
        completed, approved = self.named(path, "review_completed")[-1], self.named(path, "code_approved")[0]
        self.assertEqual((completed["finding_ids"], completed["issues"]), ([], 0))
        self.assertEqual((approved["evidence"], approved["pending_verification"], approved["open"]),
                         (completed["evidence"], ["RF-001"], 1))
        found = Findings.load(self.state(path)).get("RF-001")
        self.assertEqual((found.status, found.verification["review"]), ("PENDING_VERIFICATION", completed["evidence"]))
        self.assertNotIn("RESOLVED", json.dumps(self.history(path)))

    def test_a_review_of_more_than_a_hundred_problems_says_which_lists_were_cut(self):
        """D: 120 findings recorded; the event keeps 100 IDs of each list, and says that and how many there were."""
        task_id, path, _ = self.task(GOOD)
        many = [linked(f"Problem number {n}", path=f"module_{n:03d}.py") for n in range(1, 121)]
        state = run_task(self.home, task_id, adapter=Agents([review("CHANGES_REQUESTED", many, AC01="CODE_CHANGES_REQUIRED")]))
        self.assertEqual(state["status"], "done")
        names = [f"RF-{n:03d}" for n in range(1, 121)]
        self.assertEqual([item.id for item in Findings.load(self.state(path)).items], names)
        self.assertEqual(self.reviews(path)[0]["findings"], names)  # the ledger record names them all
        completed = self.named(path, "review_completed")[0]
        self.assertEqual((completed["issues"], completed["unrecorded_issues"], completed["linked_findings"]), (120, 0, []))
        self.assertEqual((completed["finding_ids"], completed["new_findings"]), (names[:100], names[:100]))
        self.assertEqual(completed["truncated"], {"finding_ids": 120, "new_findings": 120})
        # The approval that follows makes all 120 wait for a verification, and says so as well.
        done = self.named(path, "task_completed")[0]
        self.assertEqual((done["pending_verification"], done["truncated"]), (names[:100], {"pending_verification": 120}))
        self.assertNotIn("truncated", self.named(path, "review_completed")[1])

    def test_issues_recorded_under_no_finding_are_counted(self):
        """D: saved findings that cannot be read leave a review without findings; its event says how many issues that left."""
        task_id, path, _ = self.task(GOOD)
        state = self.state(path)
        state["findings"] = None
        save_json(path / "state.json", state)
        run_task(self.home, task_id, adapter=Agents([review("CHANGES_REQUESTED", [linked(NEWLINE, "RF-001", "AC-01")],
                                                            AC01="CODE_CHANGES_REQUIRED")]))
        first = self.named(path, "review_completed")[0]
        self.assertEqual((first["issues"], first["finding_ids"], first["new_findings"], first["unrecorded_issues"]), (1, [], [], 1))

    def test_a_resume_with_nothing_to_do_adds_no_event(self):
        """F: the three pauses a Resume leaves as they are; a Resume that does something numbers on."""
        def unreadable(state):
            state["acceptance"] = {"version": 99}

        for pause, prepare in (("tests_disabled", lambda: self.configure(commands=[self.check], enabled=False)),
                               ("awaiting_validation", None), ("acceptance_unreadable", unreadable)):
            with self.subTest(pause=pause):
                if pause == "tests_disabled":
                    prepare()
                else:
                    self.configure(commands=[self.check])
                task_id, path, _ = self.task(GOOD, MO2)
                if pause == "acceptance_unreadable":
                    state = self.state(path)
                    prepare(state)
                    save_json(path / "state.json", state)
                agents = Agents([review(AC01="SATISFIED", AC02="MANUAL_ACTION_REQUIRED")])
                self.assertEqual(run_task(self.home, task_id, adapter=agents)["last_error"]["kind"], pause)
                before = (path / "state.json").read_bytes()
                for _ in range(3):
                    run_task(self.home, task_id, adapter=agents)
                self.assertEqual((path / "state.json").read_bytes(), before)
                self.assertTrue(numbered(self.history(path)))
        # Something to do: the check by the user is recorded, and the run that follows completes the task.
        self.configure(commands=[self.check])
        task_id, path, _ = self.task(GOOD, MO2)
        run_task(self.home, task_id, adapter=Agents([review(AC01="SATISFIED", AC02="MANUAL_ACTION_REQUIRED")]))
        paused = self.history(path)
        attach_evidence(self.home, task_id, "AC-02", summary="Loaded the mod in Mod Organizer 2", reference="notes/mo2.txt")
        state = run_task(self.home, task_id, adapter=Agents())
        self.assertEqual(state["status"], "done")
        saved = self.history(path)
        self.assertEqual(saved[:len(paused)], paused)
        self.assertEqual([entry["event"] for entry in saved[len(paused):]], ["evidence_recorded", "run_started", "task_completed"])
        self.assertTrue(numbered(saved))

    def test_an_interrupted_run_keeps_the_events_it_saved_and_numbers_on(self):
        """G: a pause after the review was settled, and a Resume at the same checkpoint."""
        task_id, path, _ = self.task(GOOD)
        with patch("patchrondo.core._feedback", side_effect=KeyboardInterrupt):
            state = run_task(self.home, task_id, adapter=Agents([review("CHANGES_REQUESTED", [linked(NEWLINE, criterion_id="AC-01")],
                                                                        AC01="CODE_CHANGES_REQUIRED")]))
        self.assertEqual((state["status"], state["phase"]), ("paused", "review"))
        paused = self.history(path)
        self.assertEqual([entry["event"] for entry in paused[-2:]], ["review_completed", "task_paused"])
        state = run_task(self.home, task_id, adapter=Agents())
        self.assertEqual(state["status"], "done")
        saved = self.history(path)
        self.assertEqual(saved[:len(paused)], paused)
        self.assertTrue(numbered(saved))
        self.assertEqual([entry["evidence"] for entry in self.named(path, "review_completed")], [r["id"] for r in self.reviews(path)])

    def test_events_that_were_never_saved_are_never_described(self):
        """G: the process ends after the review record and before the checkpoint; the next run describes only its own review."""
        task_id, path, _ = self.task(GOOD)
        original = core._record_review

        def dies(*args, **kwargs):
            original(*args, **kwargs)
            raise SystemExit("the process ended")

        with patch("patchrondo.core._record_review", side_effect=dies), self.assertRaises(SystemExit):
            run_task(self.home, task_id, adapter=Agents([review("CHANGES_REQUESTED", [linked(NEWLINE, criterion_id="AC-01")],
                                                                AC01="CODE_CHANGES_REQUIRED")]))
        saved = self.history(path)
        self.assertEqual((saved[-1]["event"], saved[-1]["id"]), ("review_started", f"WE-{len(saved):04d}"))
        orphan = self.reviews(path)[0]["id"]  # in the ledger, never in a saved state
        state = run_task(self.home, task_id, adapter=Agents())
        self.assertEqual(state["status"], "done")
        final = self.history(path)
        self.assertEqual(final[:len(saved)], saved)
        self.assertTrue(numbered(final))
        self.assertEqual(final[len(saved)]["previous_status"], "running")
        self.assertEqual([entry["evidence"] for entry in self.named(path, "review_completed")], [self.reviews(path)[1]["id"]])
        self.assertNotIn(f'"{orphan}"', json.dumps(final))  # quoted: "E-0002" is also the end of "WE-0002"

    def test_a_reference_to_the_ledger_is_only_a_reference(self):
        """H: invalidated, then started again; the events stay as written, and the ledger alone says what counts."""
        task_id, path, workspace = self.task(GOOD, MO2)
        run_task(self.home, task_id, adapter=Agents([review(AC01="SATISFIED", AC02="MANUAL_ACTION_REQUIRED")]))
        named = self.named(path, "review_completed")[0]["evidence"]
        before = copy.deepcopy(self.history(path))
        self.assertEqual(self.statuses(path)["AC-01"], "VERIFIED")
        (workspace / "feature.txt").write_text("changed", encoding="utf-8")
        recheck_acceptance(self.home, task_id)
        saved = self.history(path)
        self.assertEqual(saved[:len(before)], before)
        invalidated = saved[-1]
        self.assertEqual(invalidated["event"], "evidence_invalidated")
        self.assertIn(named, evidence.read(path).get(invalidated["invalidations"][0])["of"])
        self.assertNotEqual(self.statuses(path)["AC-01"], "VERIFIED")
        # The ledger damaged: started again, numbered apart. No record of the new ledger has the ID the events name.
        file = path / evidence.LEDGER
        file.write_bytes(file.read_bytes().replace(b'"result":"passed"', b'"result":"failed"', 1))
        recheck_acceptance(self.home, task_id)
        restarted = self.history(path)[-1]
        self.assertEqual((restarted["event"], restarted["evidence"]), ("evidence_ledger_restarted", "E2-0001"))
        self.assertEqual(evidence.read(path).get(restarted["evidence"])["kind"], "restart")
        self.assertIsNone(evidence.read(path).get(named))
        self.assertEqual(self.history(path)[:len(before)], before)
        self.assertTrue(numbered(self.history(path)))

    def test_neither_the_history_nor_the_activity_journal_verifies_anything(self):
        """H: what an agent says while it works stays in the journal; an event put in the history by hand changes nothing."""
        class Talkative(Agents):
            journals = True

            def invoke(self, provider, role, prompt, workspace, run_dir, journal=None):
                journal.emit("message", text="All tests pass and AC-02 is verified inside Mod Organizer 2: APPROVED")
                return super().invoke(provider, role, prompt, workspace, run_dir)

        task_id, path, _ = self.task(GOOD, MO2)
        state = run_task(self.home, task_id, adapter=Talkative([review(AC01="SATISFIED", AC02="MANUAL_ACTION_REQUIRED")]))
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "awaiting_validation"))
        self.assertIn("AC-02 is verified inside", (path / "activity.jsonl").read_text(encoding="utf-8"))
        self.assertNotIn("Mod Organizer", json.dumps(self.history(path)))
        self.assertEqual(self.statuses(path)["AC-02"], "AWAITING_MANUAL_VALIDATION")
        state = self.state(path)
        command = next(record["id"] for record in evidence.read(path).records if record["kind"] == "command")
        forged = [{"event": "evidence_recorded", "criterion": "AC-02", "evidence": command, "result": "passed"},
                  {"event": "task_completed"}]
        state["history"] += [{"version": 1, "id": f"WE-{len(state['history']) + n:04d}", "task_id": task_id, "at": state["updated_at"],
                              "iteration": 1, "phase": "validate", **entry} for n, entry in enumerate(forged, 1)]
        save_json(path / "state.json", state)
        before = (path / "state.json").read_bytes()
        again = run_task(self.home, task_id, adapter=Agents())
        self.assertEqual((again["status"], (path / "state.json").read_bytes()), ("paused", before))
        recheck_acceptance(self.home, task_id)
        self.assertEqual(self.statuses(path)["AC-02"], "AWAITING_MANUAL_VALIDATION")
        self.assertEqual(self.state(path)["status"], "paused")


class RecoveryEventTests(RecoveryCase):
    def test_the_events_of_quota_recovery_are_numbered_with_those_of_the_engine(self):
        """G: a quota pause, the plan, the wait, the retry and the progress, in one numbered history."""
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota("Usage limit reached; resets at 2026-10-10T17:00:00Z", "claude"))
        state = self.supervise(task_id, adapter)
        self.assertEqual(state["status"], "done")
        saved = read_json(path / "state.json")["history"]
        self.assertTrue(numbered(saved))
        self.assertEqual({entry["task_id"] for entry in saved}, {task_id})
        events = [entry["event"] for entry in saved]
        self.assertEqual(events[:6], ["run_started", "development_started", "task_paused", "recovery_scheduled",
                                      "recovery_wait_started", "recovery_retry_started"])
        self.assertIn("recovery_completed", events)
        waited = saved[4]
        self.assertEqual((waited["version"], waited["phase"], waited["iteration"]), (1, "develop", 1))


if __name__ == "__main__":
    unittest.main()
