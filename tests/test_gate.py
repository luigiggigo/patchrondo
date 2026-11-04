"""The acceptance gate (`gate.py`): whether a task may be completed, and the workflow that obeys it.

Rule cases build an evidence ledger and an acceptance record directly. Workflow
cases run the real loop (`core.run_task`) with the scripted developer and
reviewer of `test_acceptance`: no CLI is started and no model is called.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from patchrondo import acceptance, capabilities as caps, core, evidence, gate, provenance, verification
from patchrondo.acceptance import Acceptance, Basis
from patchrondo.core import attach_evidence, decide_authorization, parse_review, run_task
from patchrondo.gate import Action, Decision
from patchrondo.gitops import fingerprint
from patchrondo.providers import AgentFailure, AgentReply
from patchrondo.storage import LockBusy, TaskLock, read_json, save_json

from support import outcomes
from test_acceptance import GOOD, MO2, Agents, Case, issue, review

TESTS_PASS = "The test suite passes"
PULL = "Open a pull request"
OPTIONAL = "Update the changelog (optional)"


def detected(tests=True) -> dict:
    """Capabilities as `capabilities.detect` reports them, for the rule cases."""
    have = lambda *names: {"capabilities": {name: {"state": caps.VERIFIED} for name in names}}  # noqa: E731
    return {"executors": {"developer": have(caps.FILES_WRITE), "reviewer": have(caps.FILES_READ),
                          "runtime": have(caps.TESTS) if tests else have(), "user": have(caps.MANUAL, caps.CONSENT, caps.EXTERNAL)}}


class RuleTests(unittest.TestCase):
    """The gate as a function: decisions, reasons and next actions from a record judged against a ledger."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)

    def plan(self, *texts) -> Acceptance:
        return Acceptance.create(acceptance.parse_items(list(texts)))

    def review(self, verdict="APPROVED", fingerprint="F1", **outcome) -> dict:
        listed = {f"{name[:2]}-{name[2:]}": value for name, value in outcome.items()}
        return evidence.append(self.path, "review", verdict=verdict, result=verdict.lower(), criteria=list(listed),
                               outcomes=listed, fingerprint=fingerprint, scope="code")

    def command(self, result="passed", run="r1", fingerprint="F1") -> dict:
        return evidence.append(self.path, "command", result=result, exit_code=0 if result == "passed" else 1, command=["t"],
                               covers=["tests"], run=run, of=1, fingerprint=fingerprint, scope="code")

    def lapse(self, record: dict, cause: str) -> None:
        evidence.append(self.path, "invalidation", result="invalidated", of=[record["id"]], cause=cause,
                        reason=provenance.REASONS[provenance.Cause(cause)])

    def judge(self, record: Acceptance, *, fresh=True, snapshot="F1", missing=None, tests=True) -> gate.Gate:
        ledger = evidence.read(self.path)
        record.refresh(ledger, capabilities=detected(tests))
        return gate.evaluate(record, ledger, task_id="T-1", fresh=fresh, snapshot=snapshot, missing=missing)

    def approved(self, *texts, **outcome) -> tuple[Acceptance, dict]:
        """A record whose code was approved by a review giving these outcomes, after a run whose one command passed."""
        record = self.plan(*texts)
        self.command()
        entry = self.review(**outcome)
        record.approve_code(entry["id"], 1)
        return record, entry

    def test_completion_needs_the_approval_every_required_criterion_and_current_files(self):
        record, entry = self.approved(GOOD, TESTS_PASS, AC01="SATISFIED")
        result = self.judge(record)
        self.assertEqual((result.decision, result.next_action, result.completion_allowed), (Decision.PASS, Action.COMPLETE, True))
        self.assertEqual((result.review, result.blocking(), result.conditions), (entry["id"], [], []))
        self.assertEqual([row["evidence"] for row in result.criteria], [[entry["id"]], ["E-0001"]])
        # Unknown files are never taken as current; files the results are not about need the checks again.
        self.assertEqual(self.judge(record, fresh=None).decision, Decision.PENDING)
        self.assertEqual(self.judge(record, snapshot=None).decision, Decision.PENDING)
        stale = self.judge(record, fresh=False)
        self.assertEqual((stale.decision, stale.next_action, stale.completion_allowed),
                         (Decision.NEEDS_REVERIFICATION, Action.RUN_CHECKS, False))
        # Equal inputs give the same ID; the time of the evaluation is not part of it.
        self.assertEqual(self.judge(record).id, result.id)
        self.assertRegex(result.id, r"\AGE-[0-9a-f]{16}\Z")

    def test_an_optional_criterion_never_blocks_and_a_required_one_always_does(self):
        record, _ = self.approved(GOOD, OPTIONAL, AC01="SATISFIED", AC02="NOT_ASSESSED")
        result = self.judge(record)
        self.assertEqual((record.get("AC-02").required, record.get("AC-02").status), (False, "PENDING"))
        self.assertEqual(result.decision, Decision.PASS)
        record, _ = self.approved(GOOD, MO2, AC01="SATISFIED", AC02="SATISFIED")  # a reviewer's word is no external check
        result = self.judge(record)
        self.assertEqual((result.decision, result.next_action, result.blocking()),
                         (Decision.BLOCKED, Action.WAIT_FOR_EXTERNAL_VERIFICATION, ["AC-02"]))
        self.assertEqual(record.basis["AC-02"], Basis.EXTERNAL_VERIFICATION_REQUIRED)

    def test_evidence_that_no_longer_counts_never_satisfies_a_requirement(self):
        for cause, validity, reason in (("WORKSPACE_CHANGED", "STALE", "EVIDENCE_STALE"),
                                        ("REPORT_CHANGED", "INVALID", "EVIDENCE_INVALID"),
                                        ("SNAPSHOT_MISSING", "UNKNOWN", "EVIDENCE_UNKNOWN")):
            with self.subTest(cause=cause):
                self.setUp()
                record, _ = self.approved(GOOD, TESTS_PASS, AC01="SATISFIED")
                self.lapse(evidence.read(self.path).get("E-0001"), cause)
                result = self.judge(record)
                self.assertEqual((result.decision, result.next_action), (Decision.NEEDS_REVERIFICATION, Action.RUN_CHECKS))
                self.assertEqual(result.conditions[0]["reason"], reason)
                self.assertEqual(result.criteria[1]["lapsed"], [{"evidence": "E-0001", "validity": validity, "causes": [cause],
                                                                 "proof": "test"}])
                self.assertFalse(result.completion_allowed)

    def test_an_approval_that_no_longer_counts_asks_for_a_review_again(self):
        record, entry = self.approved(GOOD, TESTS_PASS, AC01="SATISFIED")
        self.lapse(entry, "WORKSPACE_CHANGED")
        result = self.judge(record)
        self.assertEqual((result.code_review, result.decision, result.next_action),
                         ("PENDING", Decision.NEEDS_REVERIFICATION, Action.REVIEW))
        self.assertIn("APPROVAL_NO_LONGER_COUNTS", [entry["reason"] for entry in result.conditions])

    def test_a_newer_failed_run_is_never_outweighed_by_an_older_pass(self):
        record, _ = self.approved(GOOD, TESTS_PASS, AC01="SATISFIED")
        self.command("failed", run="r2")
        result = self.judge(record)
        self.assertEqual((record.basis["AC-02"], result.decision, result.next_action),
                         (Basis.CHECK_FAILED, Decision.FAIL, Action.DEVELOP))
        self.assertEqual(result.criteria[1]["evidence"], [])

    def test_a_request_for_changes_is_a_failure_whatever_the_tests_say(self):
        record = self.plan(GOOD, TESTS_PASS)
        self.command()
        self.review("CHANGES_REQUESTED", AC01="CODE_CHANGES_REQUIRED")
        record.request_changes(1, "review_not_approved")
        result = self.judge(record)
        self.assertEqual((result.decision, result.next_action), (Decision.FAIL, Action.DEVELOP))
        self.assertEqual(record.get("AC-02").status, "VERIFIED")

    def test_an_authorization_is_not_the_operation_it_authorizes(self):
        record, _ = self.approved(GOOD, PULL, AC01="SATISFIED", AC02="SATISFIED")
        self.assertEqual(self.judge(record).next_action, Action.WAIT_FOR_AUTHORIZATION)
        self.assertEqual(record.basis["AC-02"], Basis.AUTHORIZATION_REQUIRED)
        evidence.append(self.path, "authorization", criteria=["AC-02"], result="granted")
        result = self.judge(record)
        self.assertEqual((record.get("AC-02").status, record.basis["AC-02"]),
                         ("AWAITING_MANUAL_VALIDATION", Basis.EXTERNAL_VERIFICATION_REQUIRED))
        self.assertEqual((result.decision, result.next_action), (Decision.BLOCKED, Action.WAIT_FOR_EXTERNAL_VERIFICATION))

    def test_a_check_that_cannot_run_here_is_a_blocker_not_a_defect(self):
        record, _ = self.approved(GOOD, "Create the release ZIP archive", AC01="SATISFIED", AC02="SATISFIED")
        result = self.judge(record)
        self.assertEqual((record.basis["AC-02"], result.decision, result.next_action),
                         (Basis.NO_COMMAND, Decision.BLOCKED, Action.PROVIDE_CAPABILITY))
        self.setUp()
        record = self.plan(GOOD, TESTS_PASS)
        record.approve_code(self.review(AC01="SATISFIED")["id"], 1)
        result = self.judge(record, tests=False)  # no command ran, and none is configured
        self.assertEqual(record.basis["AC-02"], Basis.NO_TEST_COMMANDS)
        self.assertEqual((result.decision, result.next_action), (Decision.BLOCKED, Action.PROVIDE_CAPABILITY))

    def test_a_verified_criterion_naming_a_record_that_does_not_count_stops_everything(self):
        record, _ = self.approved(GOOD, TESTS_PASS, AC01="SATISFIED")
        ledger = evidence.read(self.path)
        record.refresh(ledger, capabilities=detected())
        record.get("AC-02").evidence = ["E-0099"]  # as a record edited by hand, or a defect, would hold
        result = gate.evaluate(record, ledger, fresh=True, snapshot="F1")
        self.assertEqual((result.decision, result.next_action), (Decision.ERROR, Action.REPAIR_EVIDENCE))
        self.assertEqual(result.criteria[1]["broken"], ["E-0099"])
        # A record that was never judged is not taken for a ready one.
        fresh = self.plan(GOOD)
        self.assertEqual(gate.evaluate(fresh, ledger, fresh=True, snapshot="F1").decision, Decision.ERROR)
        # Statuses that say ready under an overall outcome that does not: the two never pass the gate apart.
        self.setUp()
        record, _ = self.approved(GOOD, TESTS_PASS, AC01="SATISFIED")
        record.refresh(evidence.read(self.path), capabilities=detected())
        record.outcome = acceptance.Outcome.IN_PROGRESS
        result = gate.evaluate(record, evidence.read(self.path), fresh=True, snapshot="F1")
        self.assertEqual((result.decision, result.conditions[0]["reason"]), (Decision.ERROR, "EVIDENCE_INCONSISTENT"))

    def test_a_tampered_ledger_completes_nothing(self):
        record, _ = self.approved(GOOD, TESTS_PASS, AC01="SATISFIED")
        file = self.path / evidence.LEDGER
        file.write_bytes(file.read_bytes().replace(b'"result":"passed"', b'"result":"failed"', 1))
        result = self.judge(record)
        self.assertEqual((result.decision, result.next_action), (Decision.ERROR, Action.REPAIR_EVIDENCE))
        self.assertFalse(result.completion_allowed)

    def test_a_switch_that_turns_off_what_the_checks_rely_on_blocks_completion(self):
        cfg = {"tests": {"enabled": True, "trust_acknowledged": True, "commands": [["python", "-c", "pass"]]},
               "workflow": {"test_timeout_seconds": 60}}
        self.assertEqual(gate.protections(cfg), [])
        cfg["verification"] = {"checks": [{"id": "unit", "type": "test", "command": ["python", "-c", "pass"],
                                           "inputs": ["data/*.json"]}]}
        self.assertEqual(gate.protections(cfg), [])
        with patch.dict(os.environ, {verification.SWITCH: "0"}):
            self.assertEqual([entry["reason"] for entry in gate.protections(cfg)], ["VERIFICATION_DISABLED"])
        with patch.dict(os.environ, {provenance.SWITCH: "0"}):
            [entry] = gate.protections(cfg)
            self.assertEqual((entry["reason"], entry["switch"], entry["checks"]), ("PROVENANCE_DISABLED", provenance.SWITCH, ["unit"]))
        del cfg["verification"]
        with patch.dict(os.environ, {verification.SWITCH: "0", provenance.SWITCH: "0"}):
            self.assertEqual(gate.protections(cfg), [])  # nothing configured relies on them: the documented behavior stays
        record, _ = self.approved(GOOD, TESTS_PASS, AC01="SATISFIED")
        missing = [{"reason": "PROVENANCE_DISABLED", "switch": provenance.SWITCH, "checks": ["unit"]}]
        result = self.judge(record, missing=missing)
        self.assertEqual((result.decision, result.next_action), (Decision.BLOCKED, Action.RESTORE_CONFIGURATION))
        self.assertIn(provenance.SWITCH, result.message())

    def test_findings_are_reported_against_the_criteria_as_they_are_now(self):
        record, _ = self.approved(GOOD, MO2, AC01="SATISFIED", AC02="MANUAL_ACTION_REQUIRED")
        ledger = evidence.read(self.path)
        record.refresh(ledger, capabilities=detected())
        saved = {"version": 1, "next": 3, "reviews": ["E-0002"], "items": [
            {"id": "RF-001", "kind": "MANUAL_ACTION_REQUIRED", "path": "", "criterion_id": "AC-02", "severity": "medium",
             "description": "Try it in the game", "owner": "user", "actionable_now": True, "blocking": True,
             "status": "OPEN", "first_seen_iteration": 1, "last_seen_iteration": 1, "reported_in": ["E-0002"]},
            {"id": "RF-002", "kind": "CODE_CHANGES_REQUIRED", "path": "feature.txt", "criterion_id": None, "severity": "high",
             "description": "Wrong", "owner": "developer", "actionable_now": True, "blocking": True,
             "status": "PENDING_VERIFICATION", "first_seen_iteration": 1, "last_seen_iteration": 1, "reported_in": ["E-0002"], "verification": {"review": "E-0002", "iteration": 1}}]}
        result = gate.evaluate(record, ledger, fresh=True, snapshot="F1", findings=saved)
        self.assertEqual(result.findings, {"open_blocking": ["RF-001"], "settled": [], "pending_verification": ["RF-002"]})
        self.assertEqual(result.decision, Decision.BLOCKED)  # from AC-02, which the finding is about
        evidence.append(self.path, "manual", criteria=["AC-02"], result="passed", summary="Tried it in the game",
                        reference="video.mp4", fingerprint="F1", scope="code")
        result = self.judge(record)
        settled = gate.evaluate(record, evidence.read(self.path), fresh=True, snapshot="F1", findings=saved)
        self.assertEqual((settled.decision, settled.findings["settled"]), (Decision.PASS, ["RF-001"]))
        self.assertEqual(gate.evaluate(record, evidence.read(self.path), fresh=True, snapshot="F1",
                                       findings={"version": 9}).findings, {"unreadable": True})

    def test_completion_is_refused_without_a_pass_wherever_it_is_asked_for(self):
        record = self.plan(GOOD)
        result = self.judge(record)
        with self.assertRaises(core.CompletionRefused):
            core._complete(self.path, {"status": "running", "phase": "validate", "history": [], "iteration": 1}, {}, self.path,
                           result)


class ReportTests(unittest.TestCase):
    def test_the_report_shows_the_saved_decision_as_it_was_saved(self):
        from patchrondo.report import _gate_rows
        self.assertEqual(_gate_rows(None), [])  # a task from before the gate
        self.assertIn("cannot be read", "\n".join(_gate_rows("PASS")))
        partial = "\n".join(_gate_rows({"criteria": "x", "conditions": [1, {"reason": "TESTS_NOT_CURRENT"}]}))
        self.assertIn("**Decision**: unknown", partial)
        self.assertIn("not compared", partial)
        self.assertIn("TESTS_NOT_CURRENT", partial)
        saved = {"id": "GE-0123456789abcdef", "decision": "BLOCKED", "next_action": "WAIT_FOR_EXTERNAL_VERIFICATION", "fresh": True,
                 "code_review": "APPROVED", "criteria": [{"id": "AC-02", "required": True, "status": "AWAITING_MANUAL_VALIDATION",
                                                          "basis": "EXTERNAL_VERIFICATION_REQUIRED",
                                                          "lapsed": [{"evidence": "E-0003", "validity": "STALE"}]}],
                 "conditions": [{"reason": "EXTERNAL_VERIFICATION_REQUIRED"}]}
        shown = "\n".join(_gate_rows(saved))
        self.assertIn("`AC-02` waits: `EXTERNAL_VERIFICATION_REQUIRED` (no longer counts: E-0003 STALE)", shown)


class ReferenceTests(unittest.TestCase):
    """What a reviewer cites is checked against this task's ledger and recorded; it never becomes proof."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)

    def test_each_reference_is_accepted_or_refused_with_a_fixed_reason(self):
        record = Acceptance.create(acceptance.parse_items([GOOD, TESTS_PASS, MO2]))
        passed = evidence.append(self.path, "command", result="passed", exit_code=0, command=["t"], covers=["tests"], run="r1",
                                 of=2, fingerprint="F1", scope="code")
        failed = evidence.append(self.path, "command", result="failed", exit_code=1, command=["u"], covers=["tests"], run="r1",
                                 of=2, fingerprint="F1", scope="code")
        manual = evidence.append(self.path, "manual", criteria=["AC-03"], result="passed", summary="Tried in the game", reference="x",
                                 fingerprint="F1", scope="code")
        evidence.append(self.path, "invalidation", result="invalidated", of=[manual["id"]], cause="WORKSPACE_CHANGED",
                        reason=provenance.REASONS[provenance.Cause.WORKSPACE_CHANGED])
        ledger = evidence.read(self.path)
        record.refresh(ledger, capabilities=detected())
        answer = {"verdict": "APPROVED", "summary": "All tests passed and it works in the game", "issues": [], "criteria": [
            {"id": "AC-01", "outcome": "SATISFIED", "note": "", "evidence_refs": [passed["id"]]},
            {"id": "AC-02", "outcome": "SATISFIED", "note": "", "evidence_refs": [passed["id"], failed["id"], "E-0099", "T-2/E-0001"]},
            {"id": "AC-03", "outcome": "SATISFIED", "note": "", "evidence_refs": [manual["id"]]},
            {"id": "AC-09", "outcome": "SATISFIED", "note": "", "evidence_refs": [passed["id"]]}]}
        checked = gate.references(answer, record, ledger)
        self.assertEqual(checked["AC-01"], {"accepted": [], "refused": [{"id": passed["id"], "reason": "NOT_FOR_CRITERION"}]})
        self.assertEqual(checked["AC-02"], {"accepted": [passed["id"]], "refused": [
            {"id": failed["id"], "reason": "NOT_POSITIVE"}, {"id": "E-0099", "reason": "UNKNOWN_RECORD"},
            {"id": "T-2/E-0001", "reason": "UNKNOWN_RECORD"}]})
        self.assertEqual(checked["AC-03"], {"accepted": [], "refused": [
            {"id": manual["id"], "reason": "STALE", "causes": ["WORKSPACE_CHANGED"]}]})
        self.assertNotIn("AC-09", checked)  # not a criterion of the task: nothing is linked to it
        before = ledger.count
        record.refresh(evidence.read(self.path), capabilities=detected())
        self.assertEqual((record.get("AC-03").status, evidence.read(self.path).count), ("AWAITING_MANUAL_VALIDATION", before))

    def test_an_id_of_another_task_is_looked_up_in_this_task_only(self):
        other = self.path / "other"
        other.mkdir()
        for _ in range(3):
            evidence.append(other, "command", result="passed", exit_code=0, command=["t"], covers=["tests"], run="r1", of=1,
                            fingerprint="F1", scope="code")
        mine = self.path / "mine"
        mine.mkdir()
        record = Acceptance.create(acceptance.parse_items([TESTS_PASS]))
        ledger = evidence.read(mine)
        record.refresh(ledger, capabilities=detected())
        answer = {"criteria": [{"id": "AC-01", "outcome": "SATISFIED", "evidence_refs": ["E-0003"]}]}
        self.assertEqual(gate.references(answer, record, ledger)["AC-01"]["refused"], [{"id": "E-0003", "reason": "UNKNOWN_RECORD"}])

    def test_the_reviewer_answer_is_checked_before_anything_reads_it(self):
        base = {"verdict": "APPROVED", "summary": "ok", "issues": []}
        good = parse_review(json.dumps({**base, "criteria": [{"id": "AC-01", "outcome": "SATISFIED", "note": "", "evidence_refs": []}]}))
        self.assertEqual(good["criteria"][0]["evidence_refs"], [])
        self.assertEqual(parse_review(json.dumps({**base, "criteria": [{"id": "AC-01", "outcome": "SATISFIED"}]}))["verdict"],
                         "APPROVED")  # an answer from before the field existed
        for refs in ("E-0001", [1], ["E-0001"] * (gate.MAX_REFS + 1), {"E-0001": True}):
            with self.subTest(refs=refs), self.assertRaises(AgentFailure) as raised:
                parse_review(json.dumps({**base, "criteria": [{"id": "AC-01", "outcome": "SATISFIED", "evidence_refs": refs}]}))
            self.assertEqual(raised.exception.kind, "invalid_review")
        with self.assertRaises(AgentFailure):
            parse_review('{"verdict": "APPROVED", "summary": "ok", "issues": [], "criteria": [')
        item = acceptance.REVIEW_SCHEMA["properties"]["criteria"]["items"]
        self.assertEqual(sorted(item["required"]), sorted(item["properties"]))  # strict structured output needs every field


class WorkflowTests(Case):
    """The real loop with scripted agents: the gate is the one way to `done`, and what it waits for calls no agent."""

    def gate_of(self, path) -> dict:
        return self.state(path)["gate"]

    def event(self, path, name) -> dict:
        return [entry for entry in self.state(path)["history"] if entry["event"] == name][-1]

    def test_an_external_check_pauses_the_task_once_and_its_evidence_completes_it_without_an_agent(self):
        """The cycle of the request: develop, checks pass, approval, external check missing, pause, evidence, resume, done."""
        task_id, path, workspace = self.task(GOOD, MO2)
        agents = Agents()
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], state["phase"], state["last_error"]["kind"]), ("paused", "validate", "awaiting_validation"))
        saved = self.gate_of(path)
        self.assertEqual((saved["decision"], saved["next_action"], saved["code_review"]),
                         ("BLOCKED", "WAIT_FOR_EXTERNAL_VERIFICATION", "APPROVED"))
        self.assertEqual(self.event(path, "code_approved")["gate"]["criteria"], ["AC-02"])
        self.assertEqual(self.event(path, "task_paused")["gate"]["id"], saved["id"])
        self.assertEqual(agents.roles, ["developer", "reviewer"])
        # Nothing changed: a Resume does nothing, and calls nobody.
        before = (path / "state.json").read_bytes()
        run_task(self.home, task_id, adapter=agents)
        self.assertEqual(((path / "state.json").read_bytes(), agents.roles), (before, ["developer", "reviewer"]))
        attached = attach_evidence(self.home, task_id, "AC-02", summary="Started the mod inside Mod Organizer 2",
                                   reference="https://example.invalid/log")
        self.assertEqual((attached["completes"], attached["gate"]["decision"]), (True, "PASS"))
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], state["iteration"], agents.roles), ("done", 1, ["developer", "reviewer"]))
        completed = self.event(path, "task_completed")["gate"]
        self.assertEqual((completed["decision"], completed["snapshot"]), ("PASS", fingerprint(workspace)))
        self.assertEqual(self.gate_of(path)["completion_allowed"], True)

    def test_evidence_about_earlier_files_is_never_carried_to_the_files_of_now(self):
        task_id, path, workspace = self.task(GOOD, MO2)
        agents = Agents()
        run_task(self.home, task_id, adapter=agents)
        attach_evidence(self.home, task_id, "AC-02", summary="Started the mod inside Mod Organizer 2", reference="log.txt")
        (workspace / "notes.txt").write_text("changed after the check\n", encoding="utf-8")
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "awaiting_validation"))
        self.assertEqual(agents.roles, ["developer", "reviewer", "reviewer"])  # the code is read again; no developer step
        saved = self.gate_of(path)
        self.assertEqual((saved["decision"], saved["next_action"]), ("NEEDS_REVERIFICATION", "WAIT_FOR_USER_VALIDATION"))
        [lapsed] = saved["criteria"][1]["lapsed"]
        self.assertEqual((lapsed["validity"], lapsed["causes"]), ("STALE", ["WORKSPACE_CHANGED"]))

    def test_files_that_change_after_the_gate_is_evaluated_are_not_completed(self):
        task_id, path, workspace = self.task(GOOD)
        real = core._gate

        def then_changed(*args, **kwargs):
            result = real(*args, **kwargs)
            if result.completion_allowed:
                (workspace / "late.txt").write_text("written between the decision and the completion\n", encoding="utf-8")
            return result

        with patch("patchrondo.core._gate", side_effect=then_changed):
            state = run_task(self.home, task_id, adapter=Agents())
        self.assertEqual((state["status"], state["phase"], state["last_error"]["kind"]), ("paused", "test", "stale_tests"))
        self.assertNotIn("task_completed", self.events(path))
        agents = Agents()
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], agents.roles), ("done", ["reviewer"]))  # checks again, the new files reviewed
        self.assertEqual(self.event(path, "task_completed")["gate"]["snapshot"], fingerprint(workspace))

    def test_a_ledger_that_moves_after_the_gate_is_evaluated_is_evaluated_again(self):
        task_id, path, _ = self.task(GOOD)
        real = core._gate

        def then_recorded(*args, **kwargs):
            result = real(*args, **kwargs)
            if result.completion_allowed and evidence.read(path).count == result.ledger["count"]:
                evidence.append(path, "manual", criteria=["AC-01"], result="failed", summary="A late check that did not pass")
            return result

        with patch("patchrondo.core._gate", side_effect=then_recorded):
            state = run_task(self.home, task_id, adapter=Agents())
        self.assertEqual((state["status"], state["phase"], state["last_error"]["kind"]), ("paused", "review", "completion_refused"))
        self.assertNotIn("task_completed", self.events(path))

    def test_a_task_that_needs_what_nobody_here_can_do_stops_after_one_iteration(self):
        """The task that looped six times: archives, hashes, a pull request and a test in another application."""
        task_id, path, _ = self.task(GOOD, "Create the release ZIP archive", "Calcolare hash SHA256",
                                     "Eseguire Git commit e creare una PR", "Verificare il funzionamento dentro Mod Organizer 2")
        agents = Agents([{"verdict": "APPROVED", "summary": "All tests passed, the ZIP was built and the PR opened.", "issues": [],
                          "criteria": [{"id": f"AC-0{n}", "outcome": "SATISFIED", "note": "done"} for n in range(1, 6)]}])
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], state["iteration"], agents.roles), ("paused", 1, ["developer", "reviewer"]))
        self.assertEqual(state["last_error"]["kind"], "awaiting_authorization")
        saved = self.gate_of(path)
        self.assertEqual((saved["decision"], saved["next_action"]), ("BLOCKED", "WAIT_FOR_AUTHORIZATION"))
        reasons = {row["id"]: row["basis"] for row in saved["criteria"]}
        self.assertEqual(reasons, {"AC-01": "VERIFIED", "AC-02": "NO_COMMAND", "AC-03": "MANUAL_VALIDATION_REQUIRED",
                                   "AC-04": "AUTHORIZATION_REQUIRED", "AC-05": "EXTERNAL_VERIFICATION_REQUIRED"})
        # What the reviewer said was done created no evidence: one command record (the project's test), one review.
        self.assertEqual(self.kinds(path), ["command", "review"])
        decide_authorization(self.home, task_id, "AC-04", granted=True)
        run_task(self.home, task_id, adapter=agents)
        self.assertEqual(agents.roles, ["developer", "reviewer"])  # an authorization runs nothing and starts nobody
        self.assertEqual(self.record(path).get("AC-04").status, "AWAITING_MANUAL_VALIDATION")

    def test_a_turned_off_runner_never_completes_a_task_whose_checks_it_skips(self):
        cfg = read_json(self.home / "config.json")
        cfg["verification"] = {"checks": [{"id": "suite", "type": "test", "command": [sys.executable, "-c", "pass"]}]}
        save_json(self.home / "config.json", cfg)
        task_id, path, _ = self.task(GOOD)
        agents = Agents()
        with patch.dict(os.environ, {verification.SWITCH: "0"}):
            state = run_task(self.home, task_id, adapter=agents)
            self.assertEqual((state["status"], state["phase"], state["last_error"]["kind"], state["last_error"]["source"]),
                             ("paused", "validate", "completion_refused", "gate"))
            self.assertIn(verification.SWITCH, state["last_error"]["message"])
            before = (path / "state.json").read_bytes()
            run_task(self.home, task_id, adapter=agents)
            self.assertEqual((path / "state.json").read_bytes(), before)  # nothing can complete while it is off
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], agents.roles), ("done", ["developer", "reviewer"]))
        ran = [record.get("check", {}).get("id") for record in evidence.read(path).records if record.get("kind") == "command"]
        self.assertEqual(ran, [None, "command-1", "suite"])  # the skipped check ran before the task was completed

    def test_reviewer_references_are_recorded_and_prove_nothing(self):
        task_id, path, _ = self.task(GOOD, TESTS_PASS)

        def answer(prompt):
            listed = outcomes(prompt)
            for entry in listed:
                entry["evidence_refs"] = ["E-0001", "E-0042"]
            return {"verdict": "APPROVED", "summary": "Tests pass", "issues": [], "criteria": listed}

        class Citing(Agents):
            def invoke(self, provider, role, prompt, workspace, run_dir):
                if role == "reviewer":
                    self.roles.append(role)
                    return AgentReply(text=json.dumps(answer(prompt)), provider=provider)
                return super().invoke(provider, role, prompt, workspace, run_dir)

        state = run_task(self.home, task_id, adapter=Citing())
        self.assertEqual(state["status"], "done")
        [reviewed] = [record for record in evidence.read(path).records if record["kind"] == "review"]
        self.assertEqual(reviewed["references"]["AC-02"], {"accepted": ["E-0001"], "refused": [{"id": "E-0042",
                                                                                              "reason": "UNKNOWN_RECORD"}]})
        self.assertEqual(reviewed["references"]["AC-01"]["accepted"], [])
        self.assertEqual(state["review"]["references"], reviewed["references"])

    def test_a_crash_after_the_approval_is_decided_again_at_the_resume(self):
        task_id, path, _ = self.task(GOOD, MO2)
        agents = Agents()
        with patch("patchrondo.core._held", side_effect=SystemExit("the process died")), self.assertRaises(SystemExit):
            run_task(self.home, task_id, adapter=agents)
        saved = self.state(path)
        self.assertEqual((saved["status"], saved["phase"]), ("running", "validate"))  # as the checkpoint before the death left it
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], state["last_error"]["kind"], agents.roles),
                         ("paused", "awaiting_validation", ["developer", "reviewer"]))
        self.assertEqual(self.gate_of(path)["decision"], "BLOCKED")

    def test_a_crash_before_the_completion_is_saved_completes_only_through_the_gate(self):
        task_id, path, _ = self.task(GOOD)
        real = core._event

        def dies(state, name, **details):
            if name == "task_completed":
                raise SystemExit("the process died")
            return real(state, name, **details)

        with patch("patchrondo.core._event", side_effect=dies), self.assertRaises(SystemExit):
            run_task(self.home, task_id, adapter=Agents())
        self.assertNotEqual(self.state(path)["status"], "done")
        agents = Agents()
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], agents.roles), ("done", ["reviewer"]))  # the lost approval is asked for again
        self.assertEqual(self.event(path, "task_completed")["gate"]["decision"], "PASS")

    def test_two_runs_never_complete_the_same_task_together(self):
        task_id, path, _ = self.task(GOOD, MO2)
        run_task(self.home, task_id, adapter=Agents())
        attach_evidence(self.home, task_id, "AC-02", summary="Started the mod inside Mod Organizer 2", reference="log.txt")
        before = (path / "state.json").read_bytes()
        with TaskLock(path):
            with self.assertRaises(LockBusy):
                run_task(self.home, task_id, adapter=Agents())
            with self.assertRaises(LockBusy):
                attach_evidence(self.home, task_id, "AC-02", summary="Another check of the same thing", reference="log2.txt")
        self.assertEqual((path / "state.json").read_bytes(), before)
        self.assertEqual(run_task(self.home, task_id, adapter=Agents())["status"], "done")


if __name__ == "__main__":
    unittest.main()
