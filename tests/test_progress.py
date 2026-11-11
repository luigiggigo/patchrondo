"""Progress signals (Phase 2.5, Step 11): what a window of a task established, read from its records only.

Most tests give `progress.assess` observations built by hand, judged as
`provenance.assess` judges records (a record about other files is stale, a
record the ledger does not count is not valid). The adapter is checked on
tasks run with the emulated CLI of `test_attempts`, reached only through
`patchrondo.providers.execute`, and on a ledger written by `evidence.append`.
Nothing here starts a provider, a project command or a lock.
"""
import ast
import copy
import dataclasses
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import make_repo  # noqa: E402
from test_attempts import TEST, FakeCLI  # noqa: E402

from patchrondo import evidence, execution, gitops, progress  # noqa: E402
from patchrondo.core import create_task, initialize, run_task  # noqa: E402
from patchrondo.progress import (AttemptFact, Change, CriterionFact, FindingFact, Presence, RecordFact,  # noqa: E402
                                 Signal, Source, Trust, WindowMismatch)
from patchrondo.storage import read_json, save_json  # noqa: E402

A, B, C = "a" * 64, "b" * 64, "c" * 64
TASK, OTHER = "T-0123456789ab", "T-ba9876543210"
SOURCE = Path(progress.__file__)


# --- observations built by hand ------------------------------------------------------------------------------------

def rec(identifier: str, result: str = "passed", *, kind: str = "command", check: str | None = "unit", on: str | None = A,
        criteria=(), counts: bool = True, trusted: bool = True, scoped: bool = True) -> dict:
    """A ledger record as written: what an observation judges against its own files."""
    return {"id": identifier, "result": result, "kind": kind, "check": check if kind == "command" else None,
            "on": on, "criteria": tuple(criteria), "counts": counts, "trusted": trusted, "scoped": scoped}


def judged(item: dict, fingerprint: str | None) -> RecordFact:
    """As `provenance.assess` judges a record: untrusted is INVALID, an invalidated one STALE, other files STALE."""
    if isinstance(item, RecordFact):
        return item  # given as judged
    counts = item["counts"] and item["trusted"]
    if not item["trusted"]:
        validity, causes = "INVALID", ("RECORD_UNTRUSTED",)
    elif not item["counts"]:
        validity, causes = "STALE", ("WORKSPACE_CHANGED",)
    elif fingerprint is not None and item["scoped"] and item["on"] != fingerprint:
        validity, causes = "STALE", ("WORKSPACE_CHANGED",)
    else:
        validity, causes = "VALID", ()
    return RecordFact(item["id"], "h" + item["id"], item["kind"], item["result"], item["trusted"], counts, validity,
                      causes, item["check"], item["on"], item["scoped"], item["criteria"])


def crit(identifier: str, status: str = "PENDING", evidence_=(), required: bool = True) -> CriterionFact:
    return CriterionFact(identifier, required, status, tuple(evidence_))


def finding(identifier: str, status: str = "OPEN", reported=(), resting=(), owner: str = "developer") -> FindingFact:
    return FindingFact(identifier, status, owner, True, tuple(reported), tuple(resting))


def attempt(identifier: str, *, phase: str = "develop", outcome: str | None = "COMPLETED", kind: str | None = None,
            termination: str | None = "exited", before: str | None = A, after: str | None = A, ended: bool = True,
            uncertain: bool = False, draft: bool = False) -> AttemptFact:
    return AttemptFact(identifier, execution.attempt_number(identifier), phase, outcome if ended else None, kind,
                       termination if ended else None, ended, uncertain, draft, before, after if ended else None)


def obs(*, fingerprint: str | None = A, records=(), criteria=None, findings=(), attempts=None, events=(),
        iteration: int = 1, phase: str = "develop", task: str = TASK, made: int | None = None,
        unreadable=(), partial=(), absent=()) -> progress.Observation:
    sources = {source: Presence.PRESENT for source in Source}
    if fingerprint is None:
        sources[Source.WORKSPACE] = Presence.UNREADABLE
    if criteria is None:
        sources[Source.ACCEPTANCE] = Presence.ABSENT
    if attempts is None:
        sources[Source.EXECUTION] = Presence.ABSENT
    for names, presence in ((absent, Presence.ABSENT), (unreadable, Presence.UNREADABLE), (partial, Presence.PARTIAL)):
        sources.update({source: presence for source in names})
    facts = tuple(judged(item, fingerprint) for item in records)
    if made is None and attempts is not None:
        made = max((item.number for item in attempts), default=0)
    boundary = progress.Boundary(task, iteration, phase, fingerprint, made,
                                 attempts[-1].id if attempts else None, len(facts), facts[-1].digest if facts else "")
    return progress.Observation(boundary, tuple(progress.SourceState(source, presence) for source, presence in sources.items()),
                                facts, criteria, None if Source.FINDINGS in unreadable else tuple(findings),
                                attempts, tuple(progress.EventFact(name, "event") for name in events))


def changes(assessment: progress.Assessment) -> set[Change]:
    return {item.change for item in assessment.facts}


def signals(assessment: progress.Assessment) -> set[Signal]:
    return {item.signal for item in assessment.signals}


class Case(unittest.TestCase):
    def assertNoProgress(self, assessment: progress.Assessment) -> None:
        self.assertNotIn(Signal.VERIFIED_PROGRESS, signals(assessment))
        self.assertIn(Signal.NO_VERIFIED_PROGRESS, signals(assessment))
        self.assertFalse([item for item in assessment.facts if item.signal is Signal.VERIFIED_PROGRESS])

    def assertNoRegression(self, assessment: progress.Assessment) -> None:
        self.assertNotIn(Signal.REGRESSION, signals(assessment))


# --- the classification --------------------------------------------------------------------------------------------

class VerifiedProgressTests(Case):
    def test_a_previously_unmet_criterion_verified_on_the_files_now_is_verified_progress(self):
        start = obs(records=[rec("E-0001", "failed", criteria=["AC-01"])], criteria=(crit("AC-01", "FAILED"),))
        end = obs(fingerprint=B, records=[rec("E-0001", "failed", criteria=["AC-01"]),
                                          rec("E-0002", on=B, criteria=["AC-01"])],
                  criteria=(crit("AC-01", "VERIFIED", ["E-0002"]),))
        found = progress.assess(start, end)
        self.assertEqual(found.classification, Signal.VERIFIED_PROGRESS)
        self.assertEqual(found.indication(Signal.VERIFIED_PROGRESS).basis, Trust.VERIFIED)
        self.assertLessEqual({Change.CRITERION_VERIFIED, Change.CHECK_FIXED}, changes(found))
        self.assertIn("E-0002", found.indication(Signal.VERIFIED_PROGRESS).references)
        self.assertNotIn(Signal.NO_VERIFIED_PROGRESS, signals(found))
        self.assertEqual(found.applicable, ("E-0002",))
        self.assertEqual([(item.id, item.validity) for item in found.stale], [("E-0001", "STALE")])
        self.assertTrue(found.verification_ran)
        self.assertTrue(found.files_changed)

    def test_newly_applicable_evidence_a_first_pass_on_record_is_verified_progress(self):
        found = progress.assess(obs(), obs(records=[rec("E-0001")]))
        self.assertEqual(found.classification, Signal.VERIFIED_PROGRESS)
        self.assertEqual([item.change for item in found.facts if item.trust is Trust.VERIFIED], [Change.CHECK_ESTABLISHED])

    def test_a_failed_check_that_passes_is_verified_progress_not_stagnation(self):
        start = obs(records=[rec("E-0001", "failed")])
        end = obs(fingerprint=B, records=[rec("E-0001", "failed"), rec("E-0002", on=B)])
        found = progress.assess(start, end)
        self.assertEqual(found.of(Change.CHECK_FIXED)[0].references, ("E-0001", "E-0002"))
        self.assertEqual(found.classification, Signal.VERIFIED_PROGRESS)
        self.assertNoRegression(found)

    def test_a_finding_resolved_by_a_record_that_counts_on_the_files_now_is_verified_progress(self):
        start = obs(records=[rec("E-0001", "failed")], findings=[finding("RF-001", reported=["E-0002"])])
        end = obs(fingerprint=B, records=[rec("E-0001", "failed"), rec("E-0003", on=B)],
                  findings=[finding("RF-001", "RESOLVED", reported=["E-0002"], resting=["E-0003"])])
        found = progress.assess(start, end)
        self.assertEqual(found.of(Change.FINDING_RESOLVED)[0].references, ("E-0003",))
        self.assertEqual(found.of(Change.FINDING_RESOLVED)[0].trust, Trust.VERIFIED)


class ActivityTests(Case):
    def test_files_changed_without_verification_is_activity_and_no_verified_progress(self):
        found = progress.assess(obs(), obs(fingerprint=B))
        self.assertEqual(found.classification, Signal.OBSERVED_ACTIVITY)
        self.assertEqual(signals(found), {Signal.OBSERVED_ACTIVITY, Signal.NO_VERIFIED_PROGRESS})
        self.assertLessEqual({Change.FILES_CHANGED, Change.VERIFICATION_NOT_RUN}, changes(found))
        self.assertEqual(found.indication(Signal.OBSERVED_ACTIVITY).basis, Trust.OBSERVED)
        self.assertNoProgress(found)
        self.assertFalse(found.verification_ran)

    def test_an_answer_that_claims_completion_without_evidence_is_reported_only(self):
        start = obs(attempts=())
        end = obs(attempts=(attempt("CX-0001"),))
        found = progress.assess(start, end)
        self.assertEqual([(item.change, item.trust) for item in found.reported], [(Change.ANSWER_REPORTED, Trust.REPORTED)])
        self.assertNoProgress(found)
        self.assertEqual(found.classification, Signal.OBSERVED_ACTIVITY)

    def test_no_file_changes_and_no_verification_is_no_verified_progress_with_its_reasons(self):
        found = progress.assess(obs(), obs())
        self.assertEqual(found.classification, Signal.NO_VERIFIED_PROGRESS)
        indication = found.indication(Signal.NO_VERIFIED_PROGRESS)
        self.assertEqual(indication.basis, Trust.OBSERVED)
        self.assertLessEqual({Change.FILES_UNCHANGED, Change.VERIFICATION_NOT_RUN}, set(indication.reasons))
        self.assertIs(found.files_changed, False)

    def test_a_developer_at_its_turn_limit_before_verification_shows_activity_never_stagnation(self):
        start = obs(attempts=())
        end = obs(fingerprint=B, attempts=(attempt("CX-0001", outcome="TURN_LIMIT_REACHED", kind="max_turns", after=B),))
        found = progress.assess(start, end)
        self.assertEqual(signals(found), {Signal.OBSERVED_ACTIVITY, Signal.NO_VERIFIED_PROGRESS})
        self.assertIn(Change.VERIFICATION_NOT_RUN, found.indication(Signal.NO_VERIFIED_PROGRESS).reasons)
        self.assertEqual(found.of(Change.CALL_ENDED)[0].detail, "develop: TURN_LIMIT_REACHED")
        self.assertFalse(found.reported)  # no answer: nothing the agent said is even quoted
        codes = {str(item) for item in (*Signal, *Change)}
        self.assertFalse({code for code in codes if "STAGNA" in code or "STALL" in code})  # no such signal exists

    def test_a_call_that_changed_files_and_back_is_activity(self):
        found = progress.assess(obs(attempts=()), obs(attempts=(attempt("CX-0001", outcome="TURN_LIMIT_REACHED",
                                                                        before=A, after=B),)))
        self.assertEqual(found.of(Change.FILES_CHANGED)[0].references, ("CX-0001",))
        self.assertIn(Signal.OBSERVED_ACTIVITY, signals(found))

    def test_usage_elapsed_time_and_turns_are_not_inputs(self):
        names = {item.name for item in dataclasses.fields(AttemptFact)}
        self.assertFalse(names & {"turns", "tokens", "elapsed_seconds", "started_at", "ended_at", "session"})


class EvidenceTests(Case):
    def test_repeated_identical_passing_evidence_is_not_a_new_advancement(self):
        start = obs(records=[rec("E-0001", criteria=["AC-01"])], criteria=(crit("AC-01", "VERIFIED", ["E-0001"]),))
        end = obs(records=[rec("E-0001", criteria=["AC-01"]), rec("E-0002", criteria=["AC-01"])],
                  criteria=(crit("AC-01", "VERIFIED", ["E-0001", "E-0002"]),))
        found = progress.assess(start, end)
        self.assertLessEqual({Change.CHECK_REPEATED, Change.CRITERION_KEPT}, changes(found))
        self.assertNoProgress(found)
        self.assertTrue(found.verification_ran)

    def test_stale_passing_evidence_is_named_and_is_neither_progress_nor_regression(self):
        start = obs(records=[rec("E-0001", criteria=["AC-01"])], criteria=(crit("AC-01", "VERIFIED", ["E-0001"]),))
        end = obs(fingerprint=B, records=[rec("E-0001", criteria=["AC-01"], counts=False)],
                  criteria=(crit("AC-01", "PENDING"),))
        found = progress.assess(start, end)
        self.assertEqual(found.of(Change.EVIDENCE_INVALIDATED)[0].subject, "E-0001")
        self.assertEqual(found.of(Change.CRITERION_LAPSED)[0].subject, "AC-01")
        self.assertEqual([(item.id, item.validity, item.causes) for item in found.stale],
                         [("E-0001", "STALE", ("WORKSPACE_CHANGED",))])
        self.assertEqual(found.applicable, ())
        self.assertNoRegression(found)
        self.assertNoProgress(found)

    def test_a_pass_again_after_an_earlier_pass_went_stale_is_reverified_not_advanced(self):
        start = obs(fingerprint=B, records=[rec("E-0001", on=A)])
        end = obs(fingerprint=B, records=[rec("E-0001", on=A), rec("E-0002", on=B)])
        found = progress.assess(start, end)
        self.assertEqual(found.of(Change.CHECK_REVERIFIED)[0].references, ("E-0002",))
        self.assertNoProgress(found)

    def test_a_criterion_verified_only_by_records_from_before_the_window_did_not_advance(self):
        start = obs(fingerprint=None, records=[rec("E-0001", criteria=["AC-01"])],
                    criteria=(crit("AC-01", "VERIFIED", ["E-0001"]),))
        end = obs(records=[rec("E-0001", criteria=["AC-01"])], criteria=(crit("AC-01", "VERIFIED", ["E-0001"]),))
        found = progress.assess(start, end)
        self.assertEqual(found.of(Change.CRITERION_KEPT)[0].detail, "required: verified by records from before the window")
        self.assertNoProgress(found)

    def test_a_record_the_ledger_does_not_count_proves_nothing_even_when_judged_valid(self):
        uncounted = dataclasses.replace(judged(rec("E-0001"), A), counts=False)
        end = dataclasses.replace(obs(), records=(uncounted,))
        found = progress.assess(obs(), end)
        self.assertEqual(found.applicable, ())
        self.assertNoProgress(found)

    def test_evidence_of_other_files_never_applies(self):
        made_on_b = dataclasses.replace(judged(rec("E-0001", on=B), B))  # judged VALID where it was made
        end = dataclasses.replace(obs(), records=(made_on_b,))
        found = progress.assess(obs(), end)
        self.assertEqual(found.applicable, ())
        self.assertNoProgress(found)

    def test_a_missing_fingerprint_of_the_files_verifies_nothing(self):
        start = obs(records=[rec("E-0001", "failed", criteria=["AC-01"])], criteria=(crit("AC-01", "FAILED"),))
        end = obs(fingerprint=None, records=[rec("E-0001", "failed", criteria=["AC-01"]),
                                             rec("E-0002", criteria=["AC-01"])],
                  criteria=(crit("AC-01", "VERIFIED", ["E-0002"]),))
        found = progress.assess(start, end)
        self.assertEqual(found.classification, Signal.UNKNOWN)
        self.assertIn(Change.SNAPSHOT_UNKNOWN, changes(found))
        self.assertEqual(found.applicable, ())
        self.assertNoProgress(found)
        self.assertNoRegression(found)
        self.assertEqual(found.indication(Signal.NO_VERIFIED_PROGRESS).basis, Trust.UNKNOWN)
        self.assertIsNone(found.files_changed)
        # A record that names no files is not about "no files": without a fingerprint nothing applies.
        unnamed = progress.assess(obs(fingerprint=None), obs(fingerprint=None, records=[rec("E-0001", on=None)]))
        self.assertEqual(unnamed.applicable, ())
        self.assertNoProgress(unnamed)

    def test_contradictory_evidence_on_the_same_files_is_unknown(self):
        start = obs(records=[rec("E-0001")])
        end = obs(records=[rec("E-0001"), rec("E-0002"), rec("E-0003", "failed")])
        found = progress.assess(start, end)
        self.assertEqual(found.classification, Signal.UNKNOWN)
        self.assertEqual(found.of(Change.CHECK_CONTRADICTED)[0].references, ("E-0001", "E-0002", "E-0003"))
        self.assertNoRegression(found)
        self.assertNoProgress(found)

    def test_a_ledger_started_again_is_not_read_as_the_one_the_start_saw(self):
        start = obs(records=[rec("E-0001", "failed")])
        end = obs(records=[rec("E2-0001", "restarted", kind="restart", check=None), rec("E2-0002")])
        found = progress.assess(start, end)
        self.assertIn(Change.LEDGER_DISCONTINUOUS, changes(found))
        self.assertEqual(found.classification, Signal.UNKNOWN)
        self.assertNoProgress(found)
        changed = obs(records=[dataclasses.replace(judged(rec("E-0001", "failed"), A), digest="hX"), judged(rec("E-0002"), A)])
        self.assertIn(Change.LEDGER_DISCONTINUOUS, changes(progress.assess(start, changed)))
        untrusted = obs(records=[rec("E-0001", "failed", trusted=False), rec("E-0002")])
        self.assertIn(Change.LEDGER_DISCONTINUOUS, changes(progress.assess(start, untrusted)))


class RegressionTests(Case):
    def test_a_pass_on_the_files_at_the_start_then_a_failure_on_the_files_now_is_a_regression(self):
        start = obs(records=[rec("E-0001", criteria=["AC-01"])], criteria=(crit("AC-01", "VERIFIED", ["E-0001"]),))
        end = obs(fingerprint=B, records=[rec("E-0001", criteria=["AC-01"], counts=False),
                                          rec("E-0002", "failed", on=B, criteria=["AC-01"])],
                  criteria=(crit("AC-01", "FAILED"),))
        found = progress.assess(start, end)
        self.assertEqual(found.classification, Signal.REGRESSION)
        self.assertEqual(found.indication(Signal.REGRESSION).basis, Trust.VERIFIED)
        self.assertEqual(found.of(Change.CHECK_REGRESSED)[0].references, ("E-0001", "E-0002"))
        self.assertEqual(found.of(Change.CRITERION_REGRESSED)[0].references, ("E-0002",))
        self.assertIn(Signal.NO_VERIFIED_PROGRESS, signals(found))

    def test_invalidation_without_a_failure_is_not_a_regression(self):
        start = obs(records=[rec("E-0001", criteria=["AC-01"])], criteria=(crit("AC-01", "VERIFIED", ["E-0001"]),))
        end = obs(fingerprint=B, records=[rec("E-0001", criteria=["AC-01"], counts=False)], criteria=(crit("AC-01"),))
        found = progress.assess(start, end)
        self.assertNoRegression(found)
        self.assertLessEqual({Change.EVIDENCE_INVALIDATED, Change.CRITERION_LAPSED}, changes(found))

    def test_an_inconclusive_result_after_a_pass_is_unknown_not_a_regression(self):
        for result in ("error", "blocked", "timeout"):
            with self.subTest(result):
                start = obs(records=[rec("E-0001")])
                end = obs(fingerprint=B, records=[rec("E-0001", counts=False), rec("E-0002", result, on=B)])
                found = progress.assess(start, end)
                self.assertNoRegression(found)
                self.assertEqual(found.classification, Signal.UNKNOWN)
                self.assertEqual(found.of(Change.CHECK_NOT_CONCLUSIVE)[0].detail, result)

    def test_a_failure_after_a_pass_that_had_gone_stale_is_not_a_regression(self):
        start = obs(fingerprint=B, records=[rec("E-0001", on=A)])
        end = obs(fingerprint=C, records=[rec("E-0001", on=A), rec("E-0002", "failed", on=C)])
        found = progress.assess(start, end)
        self.assertNoRegression(found)
        self.assertEqual(found.of(Change.CHECK_FAILING)[0].detail, "no pass on the files at the start")

    def test_a_first_failure_is_not_a_regression(self):
        found = progress.assess(obs(), obs(records=[rec("E-0001", "failed")]))
        self.assertNoRegression(found)
        self.assertIn(Change.CHECK_FAILING, changes(found))


class ReviewerTests(Case):
    def test_a_valid_request_for_changes_is_a_workflow_decision_not_a_regression(self):
        start = obs(records=[rec("E-0001")])
        end = obs(records=[rec("E-0001"), rec("E-0002", "changes_requested", kind="review")],
                  findings=[finding("RF-001", reported=["E-0002"])])
        found = progress.assess(start, end)
        self.assertEqual(found.of(Change.REVIEW_RECORDED)[0].detail, "changes_requested")
        self.assertEqual(found.of(Change.FINDING_NEW)[0].subject, "RF-001")
        self.assertNoRegression(found)
        self.assertEqual(found.classification, Signal.OBSERVED_ACTIVITY)
        self.assertTrue(found.verification_ran)
        # Invalidated once the developer changed the files later in the window: it still took place.
        later = obs(fingerprint=B, records=[rec("E-0001"), rec("E-0002", "changes_requested", kind="review", counts=False)])
        self.assertEqual(progress.assess(start, later).of(Change.REVIEW_RECORDED)[0].subject, "E-0002")

    def test_new_defects_found_by_a_review_do_not_hide_verified_progress(self):
        start = obs(records=[rec("E-0001", "failed")])
        end = obs(fingerprint=B, records=[rec("E-0001", "failed"), rec("E-0002", on=B),
                                          rec("E-0003", "changes_requested", kind="review", on=B)],
                  findings=[finding("RF-001", reported=["E-0003"])])
        found = progress.assess(start, end)
        self.assertEqual(found.classification, Signal.VERIFIED_PROGRESS)
        self.assertIn(Change.FINDING_NEW, changes(found))

    def test_an_unusable_review_is_not_progress_nor_activity(self):
        start = obs(attempts=(attempt("CX-0001"),))
        end = obs(attempts=(attempt("CX-0002", phase="review", outcome="INVALID_FINAL_OUTPUT", kind="invalid_review"),),
                  made=2)
        found = progress.assess(start, end)
        self.assertEqual(found.of(Change.REVIEW_UNUSABLE)[0].detail, "invalid_review")
        self.assertNoProgress(found)
        self.assertNotIn(Signal.OBSERVED_ACTIVITY, signals(found))
        self.assertFalse(found.reported)  # a reviewer's answer is never taken as a claim of the work

    def test_findings_reported_again_are_repeated_observations(self):
        windows = []
        reported = ["E-0002"]
        for number in (4, 6):
            start = obs(findings=[finding("RF-001", reported=reported), finding("RF-002", reported=["E-0002"])])
            reported = reported + [f"E-{number:04d}"]
            end = obs(findings=[finding("RF-001", reported=reported), finding("RF-002", reported=["E-0002"])])
            windows.append(progress.assess(start, end))
            self.assertEqual([item.subject for item in windows[-1].of(Change.FINDING_REPEATED)], ["RF-001"])
            self.assertNoRegression(windows[-1])
        self.assertEqual(progress.Series(tuple(windows)).repeated_findings, ("RF-001",))

    def test_a_resolution_without_a_record_that_verifies_it_is_only_claimed(self):
        start = obs(records=[rec("E-0002", "approved", kind="review")], findings=[finding("RF-001", reported=["E-0001"])])
        cases = {"an approval": ["E-0002"], "a stale pass": ["E-0003"], "a failure": ["E-0004"], "nothing on record": ["E-0009"]}
        for name, resting in cases.items():
            with self.subTest(name):
                end = obs(fingerprint=B, records=[rec("E-0002", "approved", kind="review"), rec("E-0003", on=A),
                                                  rec("E-0004", "failed", on=B)],
                          findings=[finding("RF-001", "RESOLVED", reported=["E-0001"], resting=resting)])
                found = progress.assess(start, end)
                claimed = found.of(Change.FINDING_RESOLUTION_CLAIMED)
                self.assertEqual([(item.subject, item.trust) for item in claimed], [("RF-001", Trust.REPORTED)])
                self.assertFalse(found.of(Change.FINDING_RESOLVED))

    def test_a_finding_an_approval_left_out_waits_for_verification(self):
        start = obs(findings=[finding("RF-001", reported=["E-0001"])])
        end = obs(records=[rec("E-0002", "approved", kind="review")],
                  findings=[finding("RF-001", "PENDING_VERIFICATION", reported=["E-0001"], resting=["E-0002"])])
        found = progress.assess(start, end)
        self.assertEqual(found.of(Change.FINDING_AWAITING_VERIFICATION)[0].subject, "RF-001")
        self.assertNoProgress(found)
        self.assertIn(Change.REVIEW_RECORDED, changes(found))  # an approval is a workflow decision; the gate completes

    def test_signals_never_change_a_finding(self):
        items = [finding("RF-001", "RESOLVED", reported=["E-0001"], resting=["E-0002"])]
        end = obs(records=[rec("E-0002")], findings=items)
        progress.assess(obs(findings=[finding("RF-001", reported=["E-0001"])]), end)
        self.assertEqual(end.findings[0], finding("RF-001", "RESOLVED", reported=["E-0001"], resting=["E-0002"]))


class UncertaintyTests(Case):
    def test_an_unreadable_optional_source_is_unknown_and_does_not_stop_the_assessment(self):
        start = obs(records=[rec("E-0001", "failed")], findings=[finding("RF-001")])
        end = obs(fingerprint=B, records=[rec("E-0001", "failed"), rec("E-0002", on=B)], unreadable=(Source.FINDINGS,))
        found = progress.assess(start, end)
        self.assertEqual(found.classification, Signal.VERIFIED_PROGRESS)
        self.assertIn(Signal.UNKNOWN, signals(found))
        self.assertEqual(found.of(Change.SOURCE_UNREADABLE)[0].detail, "end: unreadable")

    def test_criteria_unreadable_at_the_start_are_never_compared(self):
        start = obs(records=[rec("E-0001", "failed", check="other")], unreadable=(Source.ACCEPTANCE,))
        end = obs(records=[rec("E-0001", "failed", check="other"), rec("E-0002", kind="manual", criteria=["AC-01"])],
                  criteria=(crit("AC-01", "VERIFIED", ["E-0002"]),))
        found = progress.assess(start, end)
        self.assertFalse(found.of(Change.CRITERION_VERIFIED))
        self.assertEqual(found.classification, Signal.UNKNOWN)

    def test_findings_unreadable_at_the_start_are_never_compared(self):
        start = obs(unreadable=(Source.FINDINGS,))
        end = obs(records=[rec("E-0002")], findings=[finding("RF-001", "RESOLVED", reported=["E-0001"], resting=["E-0002"])])
        found = progress.assess(start, end)
        self.assertFalse(found.of(Change.FINDING_RESOLVED) or found.of(Change.FINDING_NEW))

    def test_a_call_whose_effects_are_unknown_makes_the_window_uncertain(self):
        for item in (attempt("CX-0001", ended=False, uncertain=True), attempt("CX-0001", termination="released",
                                                                                outcome="UNKNOWN"),
                     attempt("CX-0001", termination="reconciled", outcome="UNKNOWN")):
            with self.subTest(item.termination):
                found = progress.assess(obs(attempts=()), obs(attempts=(item,)))
                self.assertIn(Change.ATTEMPT_EFFECTS_UNKNOWN, changes(found))
                self.assertEqual(found.classification, Signal.UNKNOWN)

    def test_calls_whose_records_left_with_their_step_are_counted_as_unknown(self):
        found = progress.assess(obs(attempts=(), made=1), obs(attempts=(attempt("CX-0004", phase="review"),), made=4))
        self.assertEqual(found.of(Change.ATTEMPTS_NOT_ON_RECORD)[0].detail, "2 calls")
        self.assertIn(found.of(Change.ATTEMPTS_NOT_ON_RECORD)[0], found.unknown)

    def test_a_partial_ledger_is_named(self):
        found = progress.assess(obs(), obs(records=[rec("E-0001", trusted=False)], partial=(Source.LEDGER,)))
        self.assertEqual(found.of(Change.SOURCE_UNREADABLE)[0].detail, "end: partial")
        self.assertNoProgress(found)


class WindowTests(Case):
    def test_observations_of_different_tasks_or_iterations_never_make_a_window(self):
        with self.assertRaises(WindowMismatch):
            progress.assess(obs(), obs(task=OTHER))
        with self.assertRaises(WindowMismatch):
            progress.assess(obs(), obs(iteration=2))
        with self.assertRaises(WindowMismatch):
            progress.assess(obs(attempts=(), made=3), obs(attempts=(), made=2))

    def test_a_series_holds_one_task_in_order(self):
        first = progress.assess(obs(), obs())
        with self.assertRaises(WindowMismatch):
            progress.Series((first, progress.assess(obs(task=OTHER), obs(task=OTHER))))
        later = progress.assess(obs(iteration=2), obs(iteration=2))
        with self.assertRaises(WindowMismatch):
            progress.Series((later, first))
        self.assertEqual(len(progress.Series((first, later)).windows), 2)

    def test_repeated_identical_fingerprints_are_counted_never_judged(self):
        windows = tuple(progress.assess(obs(), obs()) for _ in range(3))
        series = progress.Series(windows)
        self.assertEqual((series.unchanged_files, len(series.streak)), (3, 3))
        moved = progress.Series(windows + (progress.assess(obs(), obs(records=[rec("E-0001")])),))
        self.assertEqual((moved.unchanged_files, moved.streak), (4, ()))
        self.assertEqual(progress.Series(windows + (progress.assess(obs(), obs(fingerprint=B)),)).unchanged_files, 0)

    def test_an_assessment_is_deterministic_and_changes_neither_observation(self):
        start = obs(records=[rec("E-0001", "failed", criteria=["AC-01"])], criteria=(crit("AC-01", "FAILED"),),
                    findings=[finding("RF-001", reported=["E-0002"])], attempts=(), events=("WE-0001",))
        end = obs(fingerprint=B, records=[rec("E-0001", "failed", criteria=["AC-01"]),
                                          rec("E-0003", on=B, criteria=["AC-01"])],
                  criteria=(crit("AC-01", "VERIFIED", ["E-0003"]),), findings=[finding("RF-001", reported=["E-0002"])],
                  attempts=(attempt("CX-0001", after=B),), events=("WE-0001", "WE-0002"))
        kept = copy.deepcopy((start, end))
        first, second = progress.assess(start, end), progress.assess(start, end)
        self.assertEqual(first, second)
        self.assertEqual(json.dumps(first.dump(), sort_keys=True), json.dumps(second.dump(), sort_keys=True))
        self.assertEqual((start, end), kept)
        self.assertEqual(first.events, ("WE-0002",))

    def test_the_dump_holds_ids_codes_and_shortened_fingerprints_only(self):
        found = progress.assess(obs(attempts=()), obs(fingerprint=B, attempts=(attempt("CX-0001", after=B),)))
        text = json.dumps(found.dump())
        self.assertNotIn(A, text)
        self.assertNotIn(B, text)
        self.assertIn(B[:progress.SHORT], text)
        for signal in found.signals:
            self.assertEqual(found.dump()["signals"][found.signals.index(signal)]["reliability"],
                             progress.RELIABILITY[signal.signal])

    def test_every_fact_has_a_fixed_trust_and_only_verified_facts_give_progress_or_regression(self):
        self.assertEqual(set(progress.TRUST), set(Change))
        for change, signal in progress.SIGNAL_OF.items():
            if signal in (Signal.VERIFIED_PROGRESS, Signal.REGRESSION):
                self.assertIs(progress.TRUST[change], Trust.VERIFIED, change)
        for change in (Change.FILES_CHANGED, Change.ANSWER_REPORTED, Change.REVIEW_RECORDED, Change.CALL_ENDED,
                       Change.EVIDENCE_INVALIDATED, Change.CRITERION_LAPSED, Change.FINDING_RESOLUTION_CLAIMED,
                       Change.FINDING_AWAITING_VERIFICATION, Change.CHECK_REPEATED, Change.CHECK_REVERIFIED):
            self.assertNotIn(progress.SIGNAL_OF.get(change), (Signal.VERIFIED_PROGRESS, Signal.REGRESSION), change)


# --- the adapter, on tasks run with the emulated CLI ---------------------------------------------------------------

class RunCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(cls.folder.name).resolve()
        cls.repo, cls.home = root / "repo", root / "state"
        make_repo(cls.repo)
        initialize(cls.home, cls.repo)
        cls.cfg = read_json(cls.home / "config.json")
        cls.cfg["tests"].update(enabled=True, trust_acknowledged=True, commands=[TEST])
        cls.cfg["workflow"]["max_iterations"] = 2
        cls.cfg["rag"]["enabled"] = False
        save_json(cls.home / "config.json", cls.cfg)
        cls.cli = FakeCLI()
        cls.starts = {}
        with patch("patchrondo.providers.execute", side_effect=cls.cli):
            # A task without Adaptive Execution, from its developer's call to its approval.
            cls.done, _ = create_task(cls.home, title="Feature", description="Create feature.txt",
                                      acceptance=["Tests pass"], developer="claude", reviewer="codex")
            cls.cli.plan({"before_start": lambda: cls._observe_now("done")})
            state = run_task(cls.home, cls.done)
            assert state["status"] == "done", state.get("last_error")
            # A recorded task whose developer changed a file and reached its turn limit.
            cls.cfg["execution"] = {"enabled": True, "profile": "standard"}
            save_json(cls.home / "config.json", cls.cfg)
            cls.turns, _ = create_task(cls.home, title="Feature", description="Create feature.txt",
                                       acceptance=["Tests pass"], developer="claude", reviewer="claude")
            worktree = Path(read_json(cls.home / "tasks" / cls.turns / "state.json")["worktree"])
            cls.cli.plan({"max_turns": True, "turns": 12, "before_start": lambda: cls._observe_now("turns"),
                          "after_start": lambda: (worktree / "feature.txt").write_text("draft", encoding="utf-8")})
            state = run_task(cls.home, cls.turns)
            assert state["last_error"]["kind"] == "max_turns", state["last_error"]

    @classmethod
    def _observe_now(cls, name: str) -> None:
        """Inside the run, during the agent's call: what a reader of the saved files sees then."""
        task = {"done": getattr(cls, "done", None), "turns": getattr(cls, "turns", None)}[name]
        path = cls.home / "tasks" / task
        state = read_json(path / "state.json")
        cls.starts[name] = progress.observe(path, state, Path(state["worktree"]), cfg=cls.cfg)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def task(self, name: str) -> tuple[Path, dict, Path]:
        path = self.home / "tasks" / getattr(self, name)
        state = read_json(path / "state.json")
        return path, state, Path(state["worktree"])

    def observe(self, name: str, state: dict | None = None, **options) -> progress.Observation:
        path, saved, workspace = self.task(name)
        return progress.observe(path, saved if state is None else state, workspace, **{"cfg": self.cfg, **options})


class AdapterTests(RunCase):
    def test_a_task_run_to_its_approval_shows_verified_progress_from_its_own_records(self):
        found = progress.assess(self.starts["done"], self.observe("done"))
        self.assertEqual(found.classification, Signal.VERIFIED_PROGRESS)
        self.assertTrue(found.of(Change.CRITERION_VERIFIED))
        self.assertTrue(found.of(Change.CHECK_ESTABLISHED))
        self.assertEqual(found.of(Change.REVIEW_RECORDED)[0].detail, "approved")
        self.assertTrue(found.files_changed)
        self.assertTrue(found.verification_ran)
        self.assertEqual(dict(found.dump()["sources"]), {"workspace": "present", "ledger": "present", "acceptance": "present",
                                                         "findings": "absent", "execution": "absent",
                                                         "history": "present", "configuration": "present"})
        self.assertTrue(found.events)

    def test_a_developer_at_its_turn_limit_after_changing_a_file_is_activity_without_verification(self):
        start, end = self.starts["turns"], self.observe("turns")
        self.assertEqual([(item.id, item.ended) for item in start.attempts], [("CX-0001", False)])
        found = progress.assess(start, end)
        self.assertEqual(signals(found), {Signal.OBSERVED_ACTIVITY, Signal.NO_VERIFIED_PROGRESS})
        self.assertEqual(found.of(Change.CALL_ENDED)[0].detail, "develop: TURN_LIMIT_REACHED")
        self.assertEqual(found.of(Change.FILES_CHANGED)[0].references, ("CX-0001",))
        self.assertFalse(found.verification_ran)

    def test_unknown_provider_usage_gives_the_same_observation(self):
        path, state, workspace = self.task("turns")
        record = execution.load(state)
        old = record.step.last
        new = dataclasses.replace(old, turns=execution.Usage.unknown(execution.Unit.CLAUDE_TURNS))
        step = dataclasses.replace(record.step, budget=record.step.budget.charged(new.charges(), withdrawn=old.charges()),
                                   attempts=record.step.attempts[:-1] + (new,))
        record = dataclasses.replace(record, budget=record.budget.charged(new.charges(), withdrawn=old.charges()), step=step)
        unknown = {**copy.deepcopy(state), execution.KEY: record.to_dict()}
        fingerprint = gitops.fingerprint(workspace, reader=True)
        self.assertNotEqual(unknown[execution.KEY], state[execution.KEY])
        self.assertEqual(progress.observe(path, unknown, workspace, cfg=self.cfg, fingerprint=fingerprint),
                         progress.observe(path, state, workspace, cfg=self.cfg, fingerprint=fingerprint))

    def test_observing_twice_gives_the_same_observation(self):
        self.assertEqual(self.observe("done"), self.observe("done"))

    def test_corrupt_or_missing_optional_sources_are_named_and_never_stop_the_observation(self):
        _, state, _ = self.task("done")
        broken = {**copy.deepcopy(state), "findings": "x", execution.KEY: {"version": 99}, "acceptance": None,
                  "history": "x"}
        seen = self.observe("done", broken)
        self.assertEqual({item.source: item.presence for item in seen.sources if item.presence is Presence.UNREADABLE},
                         {Source.FINDINGS: Presence.UNREADABLE, Source.EXECUTION: Presence.UNREADABLE,
                          Source.ACCEPTANCE: Presence.UNREADABLE, Source.HISTORY: Presence.UNREADABLE})
        self.assertIsNone(seen.findings)
        self.assertIsNone(seen.criteria)
        self.assertIsNone(seen.attempts)
        found = progress.assess(self.observe("done"), seen)
        self.assertIn(Signal.UNKNOWN, signals(found))
        missing = self.observe("done", fingerprint=None)
        self.assertEqual(missing.presence(Source.WORKSPACE), Presence.UNREADABLE)
        self.assertEqual(progress.assess(missing, missing).applicable, ())

    def test_another_task_s_state_or_folder_is_refused(self):
        path, state, workspace = self.task("done")
        with self.assertRaises(ValueError):
            progress.observe(path, {**state, "id": OTHER}, workspace)
        with self.assertRaises(ValueError):
            progress.observe(path.parent / self.turns, state, workspace)
        with self.assertRaises(WindowMismatch):
            progress.assess(self.observe("done"), self.observe("turns"))

    def test_observing_writes_starts_and_charges_nothing(self):
        for name in ("done", "turns"):
            with self.subTest(name):
                path, state, workspace = self.task(name)
                kept = copy.deepcopy(state)
                before = snapshot(path, workspace)
                forbidden = AssertionError("observing must not start a provider or a command")
                with patch("patchrondo.providers.execute", side_effect=forbidden) as provider, \
                        patch("patchrondo.providers.probe", side_effect=forbidden) as probe, \
                        patch("patchrondo.process.execute", side_effect=forbidden) as command:
                    seen = progress.observe(path, state, workspace, cfg=self.cfg)
                    progress.assess(seen, seen)
                self.assertFalse(provider.called or probe.called or command.called)
                self.assertEqual(state, kept)  # no budget, attempt, finding, criterion, event or status changed
                if execution.KEY in kept:
                    self.assertEqual(execution.load(state).budget, execution.load(kept).budget)
                    self.assertEqual(execution.load(state).next_attempt, execution.load(kept).next_attempt)
                self.assertEqual(snapshot(path, workspace), before)
                self.assertEqual(read_json(path / "state.json"), kept)


def snapshot(*roots: Path) -> dict:
    files = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file() and ".git" not in path.relative_to(root).parts[:1]:
                files[str(path)] = (path.read_bytes(), os.stat(path).st_mtime_ns)
    return files


class LegacyTests(unittest.TestCase):
    """A task from before criteria, findings, history and execution records: a state and a ledger only."""

    def test_a_legacy_task_is_observed_from_its_ledger_and_files(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            root = Path(folder).resolve()
            workspace = make_repo(root / "repo")
            path = root / TASK
            path.mkdir()
            state = {"id": TASK, "iteration": 1, "phase": "test", "status": "running"}
            fingerprint = gitops.fingerprint(workspace, reader=True)
            start = progress.observe(path, state, workspace)
            self.assertEqual({item.source: item.presence for item in start.sources},
                             {Source.WORKSPACE: Presence.PRESENT, Source.EXECUTION: Presence.ABSENT,
                              Source.ACCEPTANCE: Presence.ABSENT, Source.LEDGER: Presence.ABSENT,
                              Source.CONFIGURATION: Presence.ABSENT, Source.FINDINGS: Presence.ABSENT,
                              Source.HISTORY: Presence.ABSENT})
            # As the test stage before checks wrote it (`core._record_commands`): no check, a run and a position.
            evidence.append(path, "command", executor="runtime", criteria=[], result="failed", exit_code=1,
                            command=["python", "-c", "x"], run="001-aaaaaaaa", of=1, iteration=1, fingerprint=fingerprint,
                            scope="code")
            middle = progress.observe(path, state, workspace)
            (workspace / "fixed.txt").write_text("fixed", encoding="utf-8")
            fingerprint = gitops.fingerprint(workspace, reader=True)
            evidence.append(path, "command", executor="runtime", criteria=[], result="passed", exit_code=0,
                            command=["python", "-c", "x"], run="001-bbbbbbbb", of=1, iteration=1, fingerprint=fingerprint,
                            scope="code")
            end = progress.observe(path, state, workspace)
            self.assertEqual(state, {"id": TASK, "iteration": 1, "phase": "test", "status": "running"})
            self.assertEqual([item.check for item in end.records], ["command-1", "command-1"])
            self.assertEqual(progress.assess(start, middle).of(Change.CHECK_FAILING)[0].subject, "command-1")
            found = progress.assess(middle, end)
            self.assertEqual(found.of(Change.CHECK_FIXED)[0].references, ("E-0001", "E-0002"))
            self.assertEqual(found.classification, Signal.VERIFIED_PROGRESS)


# --- the boundaries of the module -----------------------------------------------------------------------------------

class StructureTests(unittest.TestCase):
    def test_the_detector_imports_nothing_that_writes_starts_or_calls(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level == 1
                    for alias in node.names}
        self.assertEqual(imported, {"acceptance", "evidence", "execution", "findings", "gitops", "provenance", "Trust"})
        forbidden = {"new_attempt", "update", "release", "reconcile", "begin_step", "end", "running", "charged",
                     "append", "invalidate", "restart", "observe", "settle", "approved", "refresh", "save_json",
                     "atomic_text", "write_text", "write_bytes", "mkdir", "unlink", "rename", "replace", "allocate",
                     "reserve", "evaluate", "record_environment", "next_call", "sized_call", "admission", "open"}
        calls = {(node.func.value.id if isinstance(node.func.value, ast.Name) else "", node.func.attr)
                 for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        local = {"facts", "found", "kept", "indications", "records"}  # local lists: their `append` writes no record
        self.assertFalse({(owner, name) for owner, name in calls if name in forbidden and owner not in local})
        self.assertFalse([node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                          and node.func.id in ("open", "exec", "eval")])

    def test_nothing_reads_the_signals_yet(self):
        """Step 11 gives signals only: no decision, budget, recovery, continuation or completion reads them."""
        users = []
        for source in sorted(SOURCE.parent.glob("*.py")):
            if source.name == "progress.py":
                continue
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = [alias.name for alias in node.names] if isinstance(node, (ast.Import, ast.ImportFrom)) else []
                module = node.module or "" if isinstance(node, ast.ImportFrom) else ""
                if "progress" in names or module.endswith("progress"):
                    users.append(source.name)
        self.assertEqual(users, [])
        # The assessment has no field that could be read as a status of the task or a decision.
        fields = {item.name for item in dataclasses.fields(progress.Assessment)}
        self.assertFalse(fields & {"status", "done", "approved", "decision", "action", "stagnant", "stop", "retry",
                                   "continue_", "gate"})


if __name__ == "__main__":
    unittest.main()
