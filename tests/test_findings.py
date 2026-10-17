"""Findings of the reviews of a task: identities that survive new words, iterations and restarts.

Providers are scripted: no CLI is started. The letters in the names of the
engine scenarios are those of the step's request: A a new problem, B the same
problem in other words, C another problem, D an ID that is wrong, E a problem
left out, F an interruption, G what was there before findings.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from patchrondo import acceptance, capabilities as caps, core, evidence, findings
from patchrondo.acceptance import Acceptance
from patchrondo.core import run_task
from patchrondo.findings import Findings
from patchrondo.storage import read_json, save_json

from test_acceptance import GOOD, MO2, Agents, Case, issue, review

TESTS_PASS = "The test suite passes"
NEWLINE = "feature.txt has no final newline"


def linked(description, finding_id="", criterion_id="", **options):
    """An issue as the extended schema has it: its link to an earlier finding and its criterion, empty when none."""
    return {**issue(description, **options), "finding_id": finding_id, "criterion_id": criterion_id}


def plan(*criteria, writes=True, ledger=None) -> Acceptance:
    """The criteria of a task, planned with a developer that can write files, or cannot, and judged against `ledger`."""
    record = Acceptance.create(acceptance.parse_items(list(criteria) or [GOOD]))
    record.refresh(ledger or evidence.Ledger(), capabilities={"executors": {"developer": {"capabilities": {
        caps.FILES_WRITE: {"state": caps.VERIFIED if writes else caps.ABSENT, "source": "scripted"}}}}})
    return record


def recorded(registry: Findings, issues, record: str, iteration: int, criteria=None) -> dict:
    """Settle and record one review as the engine does; returns the settled review."""
    criteria = criteria or plan()
    settled = registry.settle(review("CHANGES_REQUESTED", issues), criteria)
    registry.observe(settled, record, iteration, criteria)
    return settled


class FindingTests(unittest.TestCase):
    def test_a_new_problem_gets_the_next_id_and_what_it_is_about_from_fixed_rules(self):
        registry = Findings()
        settled = recorded(registry, [linked(NEWLINE, criterion_id="AC-01")], "E-0002", 1)
        self.assertEqual((settled["issues"][0]["finding_id"], settled["issues"][0]["finding_link"]), ("RF-001", "new"))
        found = registry.get("RF-001")
        self.assertEqual((found.kind, found.path, found.criterion_id, found.severity, found.description),
                         ("CODE_CHANGES_REQUIRED", "feature.txt", "AC-01", "medium", NEWLINE))
        self.assertEqual((found.owner, found.actionable_now, found.blocking, found.status), ("developer", True, True, "OPEN"))
        self.assertEqual((found.first_seen_iteration, found.last_seen_iteration, found.reported_in, found.verification),
                         (1, 1, ["E-0002"], None))
        self.assertEqual((registry.next, registry.reviews), (2, ["E-0002"]))

    def test_the_reviewer_sets_no_status_owner_flag_or_link(self):
        answer = {**linked(NEWLINE), "status": "RESOLVED", "owner": "user", "blocking": False, "actionable_now": False,
                  "finding_link": "explicit", "claimed_finding_id": "RF-009", "finding_refusal": "made up"}
        registry = Findings()
        settled = recorded(registry, [answer], "E-0002", 1)
        self.assertEqual((settled["issues"][0]["finding_link"], "claimed_finding_id" in settled["issues"][0]), ("new", False))
        found = registry.get("RF-001")
        self.assertEqual((found.status, found.owner, found.blocking, found.actionable_now, found.claimed),
                         ("OPEN", "developer", True, True, None))

    def test_owner_and_actionable_follow_the_kind_and_the_plan(self):
        criteria = plan(GOOD, TESTS_PASS, MO2)
        self.assertEqual([(c.verifier, c.feasibility) for c in criteria.criteria],
                         [("reviewer", "automatable"), ("runtime", "needs_authorization"), ("user", "manual")])
        for answer, expected, writes in (
                (linked("d", severity="low"), ("developer", True), True),
                (linked("d", severity="low"), ("developer", False), False),  # a developer without file tools
                ({"severity": "high", "path": "feature.txt", "description": "d"}, ("developer", True), True),  # no kind
                (linked("d", kind="OBSERVATION", severity="critical"), ("nobody", False), True),
                (linked("d", kind="MANUAL_ACTION_REQUIRED", severity="high"), ("user", True), True),
                (linked("d", kind="APPROVAL_REQUIRED"), ("user", True), True),
                (linked("d", kind="CAPABILITY_BLOCKED"), ("user", False), True),
                (linked("d", criterion_id="AC-02", kind="VALIDATION_REQUIRED"), ("runtime", False), True),
                (linked("d", criterion_id="AC-03", kind="VALIDATION_REQUIRED"), ("user", True), True),
                (linked("d", criterion_id="AC-01", kind="VALIDATION_REQUIRED"), ("unknown", False), True),
                (linked("d", kind="VALIDATION_REQUIRED"), ("unknown", False), True)):
            with self.subTest(answer=answer, writes=writes):
                registry = Findings()
                recorded(registry, [answer], "E-0002", 1, plan(GOOD, TESTS_PASS, MO2, writes=writes))
                found = registry.get("RF-001")
                self.assertEqual((found.owner, found.actionable_now), expected)

    def test_blocking_says_whether_a_correction_or_a_check_is_needed_before_the_task_can_be_done(self):
        """A change the code needs, or a check, a decision or an action for a required criterion that is not verified."""
        with tempfile.TemporaryDirectory() as folder:
            evidence.append(Path(folder), "review", executor="reviewer", result="approved", verdict="APPROVED", criteria=["AC-01"],
                            outcomes={"AC-01": "SATISFIED"}, scope="code")
            ledger = evidence.read(Path(folder))
        optional = "[optional] Checked by hand on a phone"
        for criteria, statuses in ((plan(GOOD, MO2, optional), ["PENDING", "AWAITING_MANUAL_VALIDATION", "AWAITING_MANUAL_VALIDATION"]),
                                   (plan(GOOD, MO2, optional, ledger=ledger), ["VERIFIED", "AWAITING_MANUAL_VALIDATION",
                                                                               "AWAITING_MANUAL_VALIDATION"])):
            self.assertEqual(([c.status for c in criteria.criteria], [c.required for c in criteria.criteria]), (statuses, [True, True, False]))
        legacy = {"severity": "high", "path": "feature.txt", "description": "d"}
        for answer, blocking, verified in (
                (linked("d", severity="low"), True, False),                                    # a change the code needs
                (linked("d", criterion_id="AC-03", severity="low"), True, False),              # ... whatever the criterion
                (legacy, True, False), ({**legacy, "severity": "low"}, False, False),          # no kind: the severity decides
                (linked("d", criterion_id="AC-02", kind="MANUAL_ACTION_REQUIRED"), True, False),  # a required criterion waits
                (linked("d", criterion_id="AC-02", kind="VALIDATION_REQUIRED"), True, False),
                (linked("d", criterion_id="AC-02", kind="APPROVAL_REQUIRED"), True, False),
                (linked("d", criterion_id="AC-02", kind="CAPABILITY_BLOCKED"), True, False),
                (linked("d", criterion_id="AC-01", kind="VALIDATION_REQUIRED"), True, False),
                (linked("d", criterion_id="AC-01", kind="VALIDATION_REQUIRED"), False, True),  # that criterion is verified
                (linked("d", criterion_id="AC-03", kind="MANUAL_ACTION_REQUIRED"), False, False),  # an optional criterion
                (linked("d", kind="MANUAL_ACTION_REQUIRED", severity="critical"), False, False),  # no criterion waits for it
                (linked("d", criterion_id="AC-02", kind="OBSERVATION", severity="critical"), False, False),
                ({**linked("d", criterion_id="AC-02", kind="OBSERVATION"), "blocking": True}, False, False)):  # not the reviewer's
            with self.subTest(answer=answer, verified=verified):
                registry = Findings()
                recorded(registry, [answer], "E-0002", 1, plan(GOOD, MO2, optional, ledger=ledger if verified else None))
                self.assertEqual(registry.get("RF-001").blocking, blocking)

    def test_identity_is_compared_in_full_and_not_on_the_shortened_text_that_is_kept(self):
        """Two descriptions or two files that differ only past what is kept to be shown are two problems."""
        stem = "The parser drops the last token " + "x" * 600
        registry = Findings()
        recorded(registry, [linked(stem + "a" * 80, criterion_id="AC-01")], "E-0002", 1)
        settled = recorded(registry, [linked(stem + "b" * 80, criterion_id="AC-01")], "E-0003", 2)
        self.assertEqual((settled["issues"][0]["finding_id"], settled["issues"][0]["finding_link"]), ("RF-002", "new"))
        settled = recorded(registry, [linked(f"  {stem}{'A' * 80} ", criterion_id="AC-01")], "E-0004", 3)  # the same words again
        self.assertEqual((settled["issues"][0]["finding_id"], settled["issues"][0]["finding_link"]), ("RF-001", "identical"))
        self.assertLess(len(registry.get("RF-001").description), len(stem))  # what is kept to be shown stays bounded
        folder = "src/" + "p" * 300
        registry = Findings()
        recorded(registry, [linked(NEWLINE, criterion_id="AC-01", path=folder + "/one.py")], "E-0002", 1)
        settled = recorded(registry, [linked(NEWLINE, "RF-001", "AC-01", path=folder + "/two.py")], "E-0003", 2)
        self.assertEqual([settled["issues"][0][key] for key in ("finding_id", "finding_link", "finding_refusal")],
                         ["RF-002", "new", "that finding has another file"])
        settled = recorded(registry, [linked("Other words", "RF-001", "AC-01", path=f"./{folder}/one.py:7")], "E-0004", 3)
        self.assertEqual((settled["issues"][0]["finding_id"], settled["issues"][0]["finding_link"]), ("RF-001", "explicit"))
        self.assertEqual(registry.get("RF-001").reported_in, ["E-0002", "E-0004"])

    def test_a_finding_saved_with_shortened_text_is_never_taken_for_a_match(self):
        """A record saved before identities were kept in full: what was cut off cannot be checked, and its history stays."""
        folder = "src/" + "p" * 300
        registry = Findings()
        recorded(registry, [linked("w" * 700, criterion_id="AC-01", path=folder + "/one.py"), linked(NEWLINE, criterion_id="AC-01")],
                 "E-0002", 1)
        saved = json.loads(json.dumps(registry.dump()))
        for item in saved["items"]:
            item.pop("path_key", None), item.pop("words_key", None)  # as saved before the correction
        before = copy.deepcopy(saved)
        old = Findings.load({"findings": saved})
        settled = recorded(old, [linked("w" * 700, "RF-001", "AC-01", path=folder + "/one.py"),
                                 linked("w" * 700, criterion_id="AC-01", path=folder + "/one.py"),
                                 linked("Still no newline", "RF-002", "AC-01")], "E-0003", 2)
        self.assertEqual([(item["finding_id"], item["finding_link"], item.get("finding_refusal")) for item in settled["issues"]],
                         [("RF-003", "new", "the file of that finding was saved shortened: it cannot be compared"),
                          ("RF-004", "new", None), ("RF-002", "explicit", None)])
        kept = old.dump()["items"][0]
        self.assertEqual({key: kept[key] for key in before["items"][0]}, before["items"][0])  # its history is untouched
        self.assertEqual((kept["path_key"], kept["words_key"]), (None, None))
        self.assertEqual(Findings.load({"findings": old.dump()}).dump(), old.dump())

    def test_an_explicit_link_keeps_the_identity_through_new_words_and_another_line(self):
        registry = Findings()
        recorded(registry, [linked(NEWLINE, criterion_id="AC-01")], "E-0002", 1)
        for number, place in enumerate(("./feature.txt:12", "feature.txt#L3-L9", "feature.txt:3:5", ".\\feature.txt"), 2):
            with self.subTest(place=place):
                words = f"Still no newline at the end of the file ({number})"
                settled = recorded(registry, [linked(words, " RF-001 ", "AC-01", path=place, severity="high")], f"E-000{number + 1}",
                                   number)
                self.assertEqual((settled["issues"][0]["finding_id"], settled["issues"][0]["finding_link"]), ("RF-001", "explicit"))
                self.assertEqual([item.id for item in registry.items], ["RF-001"])
                found = registry.get("RF-001")
                self.assertEqual((found.description, found.severity, found.last_seen_iteration, found.first_seen_iteration),
                                 (words, "high", number, 1))
        self.assertEqual(registry.get("RF-001").reported_in, ["E-0002", "E-0003", "E-0004", "E-0005", "E-0006"])

    def test_a_wrong_or_incompatible_id_never_changes_the_finding_it_names(self):
        """D: the issue becomes a new finding with the claim kept and refused; the finding named stays as it was."""
        for answer, reason in (
                (linked("helpers.py repeats it", "RF-001", "AC-01", path="helpers.py"), "that finding has another file"),
                (linked("feature.txt could say more", "RF-001", "AC-01", kind="OBSERVATION"), "that finding has another kind"),
                (linked(NEWLINE, "RF-001", ""), "that finding has another criterion"),
                (linked(NEWLINE, "RF-001", "AC-02"), "that finding has another criterion"),
                (linked("x", "RF-001", "", kind="OBSERVATION", path="x.py"), "that finding has another kind, file, criterion"),
                (linked(NEWLINE, "RF-077", "AC-01"), "no finding of this task has that ID"),
                (linked(NEWLINE, "rf-1", "AC-01"), "it is not a finding ID")):
            with self.subTest(answer=answer):
                registry, criteria = Findings(), plan(GOOD, MO2)
                recorded(registry, [linked(NEWLINE, criterion_id="AC-01")], "E-0002", 1, criteria)
                before = registry.dump()["items"][0]
                settled = recorded(registry, [answer], "E-0003", 2, criteria)
                self.assertEqual(registry.dump()["items"][0], before)
                shown = settled["issues"][0]
                self.assertEqual((shown["finding_id"], shown["finding_link"], shown["claimed_finding_id"], shown["finding_refusal"]),
                                 ("RF-002", "new", answer["finding_id"].strip(), reason))
                self.assertEqual(registry.get("RF-002").claimed, {"id": answer["finding_id"], "reason": reason})
        # An ID given twice in one review links one issue; an ID that is not text is no claim at all.
        registry = Findings()
        recorded(registry, [linked(NEWLINE, criterion_id="AC-01")], "E-0002", 1)
        settled = recorded(registry, [linked("one", "RF-001", "AC-01"), linked("two", "RF-001", "AC-01"), {**linked("three"), "finding_id": 7}],
                           "E-0003", 2)
        self.assertEqual([(item["finding_id"], item.get("finding_refusal")) for item in settled["issues"]],
                         [("RF-001", None), ("RF-002", "another issue of the same review was linked to it"), ("RF-003", None)])
        self.assertEqual((registry.get("RF-001").description, registry.get("RF-003").claimed), ("one", None))

    def test_an_issue_repeated_word_for_word_is_linked_only_when_one_finding_matches(self):
        registry = Findings()
        recorded(registry, [linked("Missing   final newline", criterion_id="AC-01")], "E-0002", 1)
        settled = recorded(registry, [linked("missing final NEWLINE", criterion_id="AC-01"),
                                      linked("missing final newline", criterion_id="AC-01", path="other.txt"),
                                      linked("Missing final newline, and more", criterion_id="AC-01")], "E-0003", 2)
        self.assertEqual([(item["finding_id"], item["finding_link"]) for item in settled["issues"]],
                         [("RF-001", "identical"), ("RF-002", "new"), ("RF-003", "new")])
        # Two findings with the same words: which one is meant cannot be told, so a third report is a new finding.
        registry = Findings()
        recorded(registry, [linked("Same words"), linked("Same words"), linked(""), linked("")], "E-0002", 1)
        settled = recorded(registry, [linked("Same words"), linked("")], "E-0003", 2)
        self.assertEqual([item["finding_id"] for item in settled["issues"]], ["RF-005", "RF-006"])

    def test_a_finding_left_out_is_left_as_it_was_and_an_approval_only_makes_it_pending(self):
        """E: a missing report is no evidence; an approval of the code is not a verification of one problem."""
        registry, criteria = Findings(), plan(GOOD, MO2)
        recorded(registry, [linked(NEWLINE, criterion_id="AC-01"), linked("Needs the game", kind="MANUAL_ACTION_REQUIRED"),
                            linked("Nice naming", kind="OBSERVATION")], "E-0002", 1, criteria)
        recorded(registry, [linked("README.md says nothing", path="README.md")], "E-0003", 2, criteria)
        before = registry.dump()
        recorded(registry, [], "E-0004", 3, criteria)  # a review that mentions none of them
        self.assertEqual(registry.dump()["items"], before["items"])
        settled = registry.settle(review("APPROVED", [linked("README.md says nothing", "RF-004", severity="low", path="README.md")]),
                                  criteria)
        registry.observe(settled, "E-0005", 4, criteria)
        self.assertEqual(registry.approved("E-0005", 4), ["RF-001"])
        self.assertEqual([(item.id, item.status) for item in registry.items],
                         [("RF-001", "PENDING_VERIFICATION"), ("RF-002", "OPEN"), ("RF-003", "OPEN"), ("RF-004", "OPEN")])
        self.assertEqual(registry.get("RF-001").verification, {"review": "E-0005", "iteration": 4})
        self.assertEqual(registry.approved("E-0006", 5), ["RF-004"])  # reported by the approval before: left out of this one
        self.assertNotIn("RESOLVED", {item.status for item in registry.items})
        # Reported again, a finding waiting for its verification is open again.
        recorded(registry, [linked(NEWLINE, "RF-001", "AC-01")], "E-0007", 6, criteria)
        self.assertEqual((registry.get("RF-001").status, registry.get("RF-001").last_seen_iteration), ("OPEN", 6))

    def test_an_id_is_never_given_twice(self):
        registry = Findings()
        recorded(registry, [linked("one")], "E-0002", 1)
        registry.reserve({"RF-005", "RF-x", "junk"})
        settled = recorded(registry, [linked("two")], "E-0003", 2)
        self.assertEqual((settled["issues"][0]["finding_id"], registry.next), ("RF-006", 7))
        with tempfile.TemporaryDirectory() as folder:
            task = Path(folder)
            evidence.append(task, "review", findings=["RF-004", 3])
            evidence.append(task, "manual", summary="Checked what RF-009 asked for")
            # A ledger put aside is searched as text, whatever state its lines are in.
            (task / "evidence.damaged-1-20261017T000000.jsonl").write_bytes(
                b'{"kind":"review","findings":["RF-012"\n{"note": "RF-1234567 XRF-013 RF-12"}\n')
            self.assertEqual(findings.named(task), {"RF-004", "RF-009", "RF-012"})
            (task / "evidence.damaged-2-20261017T000001.jsonl").mkdir()  # one that cannot be read
            with self.assertRaises(OSError):
                findings.named(task)
            state = {"history": [], "iteration": 1}
            self.assertIsNone(core._findings(state, task))  # which IDs are taken is not known: none is given
            self.assertEqual([event["event"] for event in state["history"]], ["findings_not_recorded"])
        with patch.object(findings, "MAX_FINDINGS", 2):
            settled = recorded(registry, [linked("three")], "E-0004", 3)
        self.assertEqual(("finding_id" in settled["issues"][0], len(registry.items)), (False, 2))
        registry = Findings()
        registry.reserve({"RF-999999"})  # no number is left
        settled = recorded(registry, [linked("one too many")], "E-0002", 1)
        self.assertEqual(("finding_id" in settled["issues"][0], registry.items), (False, []))

    def test_a_review_is_recorded_once_and_never_gives_a_finding_another_identity(self):
        """F: recording the same review again changes nothing."""
        registry, criteria = Findings(), plan()
        settled = registry.settle(review("CHANGES_REQUESTED", [linked(NEWLINE)]), criteria)
        self.assertEqual(registry.observe(settled, "E-0002", 1, criteria), ["RF-001"])
        before = registry.dump()
        self.assertEqual(registry.observe(settled, "E-0002", 1, criteria), [])
        self.assertEqual(registry.dump(), before)
        for issue_ in ({**linked("other words", path="other.py"), "finding_id": "RF-001", "finding_link": "explicit"},
                       {**linked(NEWLINE), "finding_id": "RF-001", "finding_link": "new"}):
            with self.subTest(issue=issue_):
                self.assertEqual(registry.observe(review("CHANGES_REQUESTED", [issue_]), "E-0009", 2, criteria), [])
                self.assertEqual(registry.dump()["items"], before["items"])
                registry.reviews.remove("E-0009")

    def test_the_record_is_read_back_as_saved_and_one_that_is_not_one_is_refused(self):
        registry = Findings()
        recorded(registry, [linked(NEWLINE, criterion_id="AC-01")], "E-0002", 1)
        registry.approved("E-0003", 2)
        saved = json.loads(json.dumps(registry.dump()))
        self.assertEqual(Findings.load({"findings": saved}).dump(), saved)
        self.assertEqual(Findings.load({}).dump(), {"version": 1, "next": 1, "reviews": [], "items": []})

        def changed(**fields):
            return {**saved, "items": [{**saved["items"][0], **fields}]}

        for bad in (None, [], {**saved, "version": 2}, {**saved, "version": True}, {**saved, "items": {}}, {**saved, "next": 1},
                    {**saved, "next": True}, {**saved, "reviews": ["x"]}, {**saved, "items": saved["items"] * 2},
                    changed(id="RF-1"), changed(kind="OPINION"), changed(kind=["OBSERVATION"]), changed(criterion_id="AC-1"),
                    changed(severity="urgent"), changed(owner="reviewer"), changed(actionable_now=1), changed(blocking=None),
                    changed(status="CLOSED"), changed(status="RESOLVED", verification=None), changed(status=[]),
                    changed(first_seen_iteration=0), changed(first_seen_iteration=3), changed(last_seen_iteration=True),
                    changed(reported_in=["x"]), changed(reported_in="E-0002"), changed(verification={"review": "E-1", "iteration": 2}),
                    changed(verification={"review": "E-0003", "iteration": "2"}), changed(claimed={"id": 3, "reason": ""}),
                    changed(path=None), changed(description=4), changed(path_key="feature.txt"), changed(words_key=5)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Findings.load({"findings": bad})

    def test_the_reviewer_is_shown_the_findings_and_how_to_name_one_again(self):
        registry = Findings()
        recorded(registry, [linked(NEWLINE, criterion_id="AC-01"), linked("Needs the game", kind="MANUAL_ACTION_REQUIRED", path="")],
                 "E-0002", 1)
        shown = findings.brief(registry)
        self.assertIn("- RF-002 [OPEN] MANUAL_ACTION_REQUIRED · no file · no criterion · iterations 1–1: Needs the game\n"
                      "- RF-001 [OPEN] CODE_CHANGES_REQUIRED · feature.txt · AC-01 · iterations 1–1: " + NEWLINE, shown)
        self.assertIn("give its ID in `finding_id`, with the same `kind`, the same file in `path` and the same `criterion_id`", shown)
        self.assertIn("leaving it out is not taken as proof that it is fixed", shown)
        self.assertIn("- (none so far)", findings.brief(Findings()))
        self.assertEqual(findings.brief(None), "")
        many = Findings()
        recorded(many, [linked(f"problem {number}") for number in range(45)], "E-0002", 1)
        self.assertIn("- … 5 earlier findings are not listed", many.brief())
        self.assertEqual(many.brief().count("\n- RF-"), findings.BRIEF_LIMIT)

    def test_the_schema_asks_for_the_links_and_an_answer_without_them_stays_a_review(self):
        item = acceptance.REVIEW_SCHEMA["properties"]["issues"]["items"]
        self.assertEqual(item["required"], ["severity", "path", "description", "kind", "finding_id", "criterion_id"])
        self.assertEqual((item["properties"]["finding_id"], item["properties"]["criterion_id"]), ({"type": "string"}, {"type": "string"}))
        for answer in ({"verdict": "CHANGES_REQUESTED", "summary": "s", "issues": [{"severity": "low", "path": "", "description": "d"}]},
                       {"verdict": "CHANGES_REQUESTED", "summary": "s", "issues": [{**linked("d"), "finding_id": 7, "criterion_id": []}]}):
            with self.subTest(answer=answer):
                parsed = core.parse_review(json.dumps(answer))
                self.assertEqual(parsed["verdict"], "CHANGES_REQUESTED")
                settled = Findings().settle(parsed, plan())
                self.assertEqual((settled["issues"][0]["finding_id"], settled["issues"][0]["finding_link"]), ("RF-001", "new"))

    def test_a_review_whose_findings_cannot_be_read_keeps_a_claim_and_links_nothing(self):
        answer = review("CHANGES_REQUESTED", [linked(NEWLINE, " RF-001 "), {**linked("two"), "finding_link": "explicit"}, linked("three")])
        kept = findings.unlinked(answer)
        self.assertEqual([{key: item.get(key) for key in findings.ANNOTATIONS} for item in kept["issues"]],
                         [{"finding_id": None, "finding_link": None, "claimed_finding_id": "RF-001", "finding_refusal": None},
                          dict.fromkeys(findings.ANNOTATIONS), dict.fromkeys(findings.ANNOTATIONS)])


class EngineTests(Case):
    def reviews(self, path) -> list[dict]:
        return [record for record in evidence.read(path).records if record["kind"] == "review"]

    def decisions(self, path) -> dict:
        """What the loop decided: events, where the task stands, the verdicts and the criteria. Nothing about findings."""
        state = self.state(path)
        error = {key: value for key, value in (state["last_error"] or {}).items() if key != "at"}
        return {"events": [entry["event"] for entry in state["history"]], "status": state["status"], "phase": state["phase"],
                "iteration": state["iteration"], "error": error, "verdicts": [record["verdict"] for record in self.reviews(path)],
                "code": state["acceptance"]["code"]["status"], "criteria": self.statuses(path)}

    def test_a_problem_keeps_its_id_through_new_words_and_iterations(self):
        """A, B, C, E: a new problem, the same in other words, another one, one left out, and the approval."""
        task_id, path, _ = self.task(GOOD)
        again = linked("The last line of feature.txt still does not end with a newline", "RF-001", "AC-01", severity="high",
                       path="./feature.txt:1")
        agents = Agents([review("CHANGES_REQUESTED", [linked(NEWLINE, criterion_id="AC-01")], AC01="CODE_CHANGES_REQUIRED"),
                         review("CHANGES_REQUESTED", [again, linked("README.md does not mention it", path="README.md")],
                                AC01="CODE_CHANGES_REQUIRED"),
                         review("CHANGES_REQUESTED", [linked("README.md still says nothing", "RF-002", path="README.md")], AC01="SATISFIED"),
                         review(AC01="SATISFIED")])
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], state["iteration"]), ("done", 4))
        records = self.reviews(path)
        self.assertEqual([record.get("findings") for record in records], [["RF-001"], ["RF-001", "RF-002"], ["RF-002"], None])
        saved = Findings.load(self.state(path))
        self.assertEqual([item.id for item in saved.items], ["RF-001", "RF-002"])
        first, second = saved.items
        self.assertEqual((first.kind, first.path, first.criterion_id, first.owner, first.blocking, first.actionable_now),
                         ("CODE_CHANGES_REQUIRED", "feature.txt", "AC-01", "developer", True, True))
        self.assertEqual((first.description, first.severity), (again["description"], "high"))  # as last reported
        self.assertEqual((first.first_seen_iteration, first.last_seen_iteration, first.reported_in), (1, 2, [r["id"] for r in records[:2]]))
        self.assertEqual((second.first_seen_iteration, second.last_seen_iteration, second.reported_in),
                         (2, 3, [r["id"] for r in records[1:3]]))
        # The reviewer was shown each finding before the reviews that followed it; left out of the third review, RF-001 stayed open.
        prompts = [prompt for role, prompt in zip(agents.roles, agents.prompts) if role == "reviewer"]
        self.assertIn("- (none so far)", prompts[0])
        self.assertIn(f"- RF-001 [OPEN] CODE_CHANGES_REQUIRED · feature.txt · AC-01 · iterations 1–1: {NEWLINE}", prompts[1])
        self.assertIn(f"- RF-001 [OPEN] CODE_CHANGES_REQUIRED · feature.txt · AC-01 · iterations 1–2: {again['description']}",
                      prompts[3])
        self.assertIn('"finding_id":"ID of an earlier problem it reports again, or empty"', prompts[0])
        # The approval left both out: they wait for a verification, naming it. Nothing verifies that either is gone.
        self.assertEqual([(item.status, item.verification) for item in saved.items],
                         [("PENDING_VERIFICATION", {"review": records[3]["id"], "iteration": 4})] * 2)
        self.assertEqual(saved.reviews, [record["id"] for record in records])
        developer = [prompt for role, prompt in zip(agents.roles, agents.prompts) if role == "developer"]
        self.assertIn('"finding_id": "RF-002"', developer[3])  # the feedback of the third review

    def test_a_wrong_id_never_overwrites_the_finding_it_names(self):
        """D: every issue of the second review claims RF-001 wrongly; RF-001 stays as the first review left it."""
        self.configure(max_iterations=2)
        task_id, path, _ = self.task(GOOD)
        wrong = [linked("helpers.py repeats the parser", "RF-001", "AC-01", path="helpers.py"),
                 linked("feature.txt could say more", "RF-001", "AC-01", kind="OBSERVATION"),
                 linked(NEWLINE, "RF-001", ""), linked("feature.txt is empty", "RF-077", "AC-01")]
        run_task(self.home, task_id, adapter=Agents([
            review("CHANGES_REQUESTED", [linked(NEWLINE, criterion_id="AC-01")], AC01="CODE_CHANGES_REQUIRED"),
            review("CHANGES_REQUESTED", wrong, AC01="CODE_CHANGES_REQUIRED")]))
        state = self.state(path)
        self.assertEqual((state["status"], state["last_error"]), ("blocked", "max_iterations_reached"))
        saved = Findings.load(state)
        first = saved.get("RF-001")
        self.assertEqual((first.description, first.last_seen_iteration, first.reported_in), (NEWLINE, 1, [self.reviews(path)[0]["id"]]))
        self.assertEqual([(item["finding_id"], item["claimed_finding_id"]) for item in state["review"]["issues"]],
                         [("RF-002", "RF-001"), ("RF-003", "RF-001"), ("RF-004", "RF-001"), ("RF-005", "RF-077")])
        self.assertEqual([item.claimed["reason"] for item in saved.items[1:]],
                         ["that finding has another file", "that finding has another kind", "that finding has another criterion",
                          "no finding of this task has that ID"])

    def test_an_interrupted_review_checkpoint_keeps_its_findings_and_each_review_is_recorded_once(self):
        """F: interrupted after the review was recorded, resumed at the same checkpoint."""
        task_id, path, _ = self.task(GOOD)
        first = review("CHANGES_REQUESTED", [linked(NEWLINE, criterion_id="AC-01")], AC01="CODE_CHANGES_REQUIRED")
        with patch("patchrondo.core._feedback", side_effect=KeyboardInterrupt):
            state = run_task(self.home, task_id, adapter=Agents([first]))
        self.assertEqual((state["status"], state["phase"], state["last_error"]["kind"]), ("paused", "review", "interrupted"))
        paused = Findings.load(self.state(path))
        self.assertEqual(([item.id for item in paused.items], paused.reviews), (["RF-001"], [self.reviews(path)[0]["id"]]))
        again = review("CHANGES_REQUESTED", [linked("Still no newline at the end", "RF-001", "AC-01"), linked("README.md is out of date",
                                                                                                             path="README.md")],
                       AC01="CODE_CHANGES_REQUIRED")
        state = run_task(self.home, task_id, adapter=Agents([again]))
        self.assertEqual(state["status"], "done")
        records = self.reviews(path)
        saved = Findings.load(self.state(path))
        self.assertEqual([item.id for item in saved.items], ["RF-001", "RF-002"])
        self.assertEqual((saved.get("RF-001").reported_in, saved.get("RF-001").last_seen_iteration), ([records[0]["id"], records[1]["id"]], 1))
        self.assertEqual(saved.reviews, [record["id"] for record in records])
        self.assertEqual(len(saved.reviews), len(set(saved.reviews)))

    def test_a_crash_between_the_ledger_and_the_state_never_gives_an_id_twice(self):
        """F: the process dies once the review record is written and before the state is saved."""
        task_id, path, _ = self.task(GOOD)
        original = core._record_review

        def dies(*args, **kwargs):
            original(*args, **kwargs)
            raise SystemExit("the process ended")

        with patch("patchrondo.core._record_review", side_effect=dies), self.assertRaises(SystemExit):
            run_task(self.home, task_id, adapter=Agents([review("CHANGES_REQUESTED", [linked(NEWLINE, criterion_id="AC-01")],
                                                                AC01="CODE_CHANGES_REQUIRED")]))
        state = self.state(path)
        self.assertEqual((state["status"], "findings" in state, self.reviews(path)[0]["findings"]), ("running", False, ["RF-001"]))
        state = run_task(self.home, task_id, adapter=Agents([review("CHANGES_REQUESTED", [
            linked("README.md is out of date", path="README.md"), linked(NEWLINE, "RF-001", "AC-01")], AC01="CODE_CHANGES_REQUIRED")]))
        self.assertEqual(state["status"], "done")
        saved = Findings.load(self.state(path))
        self.assertEqual([(item.id, item.description) for item in saved.items], [("RF-002", "README.md is out of date"), ("RF-003", NEWLINE)])
        self.assertEqual(saved.get("RF-003").claimed, {"id": "RF-001", "reason": "no finding of this task has that ID"})
        self.assertEqual([record.get("findings") for record in self.reviews(path)], [["RF-001"], ["RF-002", "RF-003"], None])

    def test_a_crash_and_a_ledger_started_again_never_give_an_id_twice(self):
        """F: RF-001 is only in a review record the state never saw, and that ledger is then found damaged and put aside."""
        task_id, path, _ = self.task(GOOD)
        original = core._record_review

        def dies(*args, **kwargs):
            original(*args, **kwargs)
            raise SystemExit("the process ended")

        with patch("patchrondo.core._record_review", side_effect=dies), self.assertRaises(SystemExit):
            run_task(self.home, task_id, adapter=Agents([review("CHANGES_REQUESTED", [linked(NEWLINE, criterion_id="AC-01")],
                                                                AC01="CODE_CHANGES_REQUIRED")]))
        file = path / evidence.LEDGER
        file.write_bytes(file.read_bytes().replace(b'"result":"passed"', b'"result":"failed"', 1))  # the record of the tests, edited
        damaged = file.read_bytes()
        self.assertTrue(evidence.read(path).problems)
        state = run_task(self.home, task_id, adapter=Agents([review("CHANGES_REQUESTED", [
            linked("README.md is out of date", path="README.md"), linked(NEWLINE, "RF-001", "AC-01")], AC01="CODE_CHANGES_REQUIRED")]))
        self.assertEqual(state["status"], "done")
        saved = Findings.load(self.state(path))
        self.assertEqual([(item.id, item.description) for item in saved.items], [("RF-002", "README.md is out of date"), ("RF-003", NEWLINE)])
        self.assertEqual(saved.get("RF-003").claimed, {"id": "RF-001", "reason": "no finding of this task has that ID"})
        # The ledger put aside was only read: it is unchanged, and nothing in it counts for a criterion.
        archived = list(path.glob("evidence.damaged-*.jsonl"))
        self.assertEqual((len(archived), archived[0].read_bytes() == damaged), (1, True))
        self.assertTrue(all(name.startswith("E2-") for criterion in self.record(path).criteria for name in criterion.evidence))
        self.assertTrue(self.record(path).get("AC-01").evidence)

    def test_a_check_that_a_required_criterion_waits_for_is_blocking(self):
        """3: a required criterion that waits for a person, and an issue about it; the loop pauses as it did before findings."""
        task_id, path, _ = self.task(GOOD, MO2)
        state = run_task(self.home, task_id, adapter=Agents([review(
            "APPROVED", [linked("Try the mod inside Mod Organizer 2", criterion_id="AC-02", kind="MANUAL_ACTION_REQUIRED")],
            AC01="SATISFIED", AC02="MANUAL_ACTION_REQUIRED")]))
        self.assertEqual((state["status"], state["phase"], state["last_error"]["kind"]), ("paused", "validate", "awaiting_validation"))
        self.assertEqual(self.statuses(path), {"AC-01": "VERIFIED", "AC-02": "AWAITING_MANUAL_VALIDATION"})
        found = Findings.load(self.state(path)).get("RF-001")
        self.assertEqual((found.owner, found.blocking, found.status, found.criterion_id), ("user", True, "OPEN", "AC-02"))

    def test_a_review_in_the_format_from_before_findings_still_works(self):
        """G: an answer without kinds, links or outcomes; an issue repeated word for word keeps its finding."""
        task_id, path, _ = self.task(GOOD)
        old = {"verdict": "CHANGES_REQUESTED", "summary": "Checked", "issues": [{"severity": "high", "path": "feature.txt",
                                                                                "description": "The content is wrong"}]}
        state = run_task(self.home, task_id, adapter=Agents([old, old]))
        self.assertEqual((state["status"], state["iteration"]), ("done", 3))
        saved = Findings.load(self.state(path))
        self.assertEqual([(item.id, item.kind, item.owner, item.blocking, item.first_seen_iteration, item.last_seen_iteration)
                          for item in saved.items], [("RF-001", None, "developer", True, 1, 2)])

    def test_the_loop_decides_as_before(self):
        """G: the same answers, with links right and wrong, lead to the same events, verdicts and criteria as before findings.

        DECIDED was recorded with the sources from before findings existed, on which this test passes too.
        """
        task_id, path, _ = self.task(GOOD, MO2)
        run_task(self.home, task_id, adapter=Agents([
            review("CHANGES_REQUESTED", [linked(NEWLINE, criterion_id="AC-01"), linked("Needs the game", kind="MANUAL_ACTION_REQUIRED")],
                   AC01="CODE_CHANGES_REQUIRED", AC02="MANUAL_ACTION_REQUIRED"),
            review("CHANGES_REQUESTED", [linked("Still no newline", "RF-001", "AC-01", severity="low"), linked("x", "RF-009", path="x.py")],
                   AC01="CODE_CHANGES_REQUIRED", AC02="MANUAL_ACTION_REQUIRED"),
            review("APPROVED", [linked("Nice naming", "RF-002", kind="OBSERVATION")], AC01="SATISFIED", AC02="MANUAL_ACTION_REQUIRED")]))
        self.assertEqual(self.decisions(path), DECIDED)

    def test_saved_findings_that_cannot_be_read_are_left_as_they_are(self):
        """G: nothing replaces them, nothing is linked, and the loop goes on as it does without findings."""
        for saved in (None, {"version": 2, "items": [], "next": 1}, {"version": 1, "next": 2, "reviews": [], "items": [{"id": "RF-001"}]}):
            with self.subTest(saved=saved):
                task_id, path, _ = self.task(GOOD)
                state = self.state(path)
                state["findings"] = saved
                save_json(path / "state.json", state)
                state = run_task(self.home, task_id, adapter=Agents([review("CHANGES_REQUESTED", [linked(NEWLINE, "RF-001", "AC-01")],
                                                                            AC01="CODE_CHANGES_REQUIRED")]))
                self.assertEqual((state["status"], state["iteration"]), ("done", 2))
                state = self.state(path)
                self.assertEqual(state["findings"], saved)
                self.assertEqual(self.events(path).count("findings_not_recorded"), 2)  # one for each review
                self.assertEqual([record.get("findings") for record in self.reviews(path)], [None, None])
                self.assertEqual(self.reviews(path)[0]["issues"], 1)
                feedback = (path / "feedback.md").read_text(encoding="utf-8")
                self.assertIn('"claimed_finding_id": "RF-001"', feedback)
                self.assertNotIn('"finding_id"', feedback)

    def test_a_task_from_before_findings_is_left_as_it_is(self):
        """G: a state without findings reads as none, a task that ended is not touched, and clean reviews add no record."""
        task_id, path, _ = self.task(GOOD)
        state = run_task(self.home, task_id, adapter=Agents())
        self.assertEqual(state["status"], "done")
        self.assertNotIn("findings", self.state(path))  # no review reported an issue
        self.assertEqual(Findings.load(self.state(path)).items, [])
        before = (path / "state.json").read_bytes()
        run_task(self.home, task_id, adapter=Agents([review("CHANGES_REQUESTED", [linked(NEWLINE)])]))
        self.assertEqual((path / "state.json").read_bytes(), before)


# Recorded on October 17, 2026 with the sources of d010809, before findings existed (without the time of the pause).
DECIDED = {
    "events": ["run_started", "development_started", "development_completed", "tests_started", "tests_completed", "review_started",
               "review_completed", "development_started", "development_completed", "tests_started", "tests_completed",
               "review_started", "review_completed", "development_started", "development_completed", "tests_started",
               "tests_completed", "review_started", "review_completed", "code_approved", "task_paused"],
    "status": "paused", "phase": "validate", "iteration": 3,
    "error": {"kind": "awaiting_validation", "source": "acceptance",
              "message": "The code is approved. 1 required criterion is not verified yet (AC-02): it needs a check or a decision "
                         "that the agents cannot give. Open “Criteria and checks” to see what each needs."},
    "verdicts": ["CHANGES_REQUESTED", "CHANGES_REQUESTED", "APPROVED"], "code": "APPROVED",
    "criteria": {"AC-01": "VERIFIED", "AC-02": "AWAITING_MANUAL_VALIDATION"},
}


if __name__ == "__main__":
    unittest.main()
