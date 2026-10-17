"""Acceptance criteria, findings, workflow history and the evidence ledger, checked together on whole tasks.

Providers are scripted: no CLI is started. Each scenario runs a task through the loop and then rebuilds
what happened from the four records (`coherent`): every reference an event or a finding holds has to
resolve to the record it names, in the active ledger or in one `evidence.restart` put aside, and say
the same thing there. The letters are those of the request for this check: A a problem corrected
over iterations, B criteria nobody in the workflow can verify, C crashes before and after
checkpoints, a quota failure, a ledger invalidated and started again, the iteration limit, D a task
left by the sources from before findings and workflow events, E a review of more than a hundred
problems.

`PATCHRONDO_ALL_CHECKPOINTS=1` crashes the scenario of C before and after every checkpoint instead of
the ones listed in `CRASHES`.
"""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import unittest
from unittest.mock import patch

from patchrondo import core, evidence, findings, history
from patchrondo.acceptance import Acceptance
from patchrondo.core import attach_evidence, decide_authorization, recheck_acceptance, run_task
from patchrondo.findings import Findings
from patchrondo.providers import AgentFailure, AgentReply
from patchrondo.report import render_report
from patchrondo.storage import read_json, save_json

from support import outcomes
from test_acceptance import GOOD, MO2, Case, review
from test_findings import NEWLINE, linked

GAME = "Try the mod inside Mod Organizer 2"
README = "README.md does not mention feature.txt"
DOWNLOAD, PUBLISH = "Download the GGUF model", "Publish the package to PyPI"
# A row of the findings the reviewer is shown (`Findings.brief`): ID, kind, file, criterion.
SHOWN = re.compile(r"^- (RF-\d{3,6}) \[[A-Z_]+\] ([A-Z_]+|no kind) · (.*?) · (.*?) · iterations ", re.M)


def again(description, kind="CODE_CHANGES_REQUIRED", path="feature.txt", criterion_id="", **options):
    """An issue that reports a problem of an earlier review again: its ID is the one the reviewer is shown for it."""
    return {**linked(description, criterion_id=criterion_id, kind=kind, path=path, **options), "again": True}


# Scenario A: a developer issue reported, reported again in other words under its ID, a new one, and the approval;
# beside them a check only a person can do, reported in the first review and again in the approval.
REVIEWS = {
    1: review("CHANGES_REQUESTED", [linked(NEWLINE, criterion_id="AC-01"),
                                    linked(GAME, kind="MANUAL_ACTION_REQUIRED", path="", criterion_id="AC-02")],
              AC01="CODE_CHANGES_REQUIRED", AC02="MANUAL_ACTION_REQUIRED"),
    2: review("CHANGES_REQUESTED", [again("feature.txt still ends without a newline", criterion_id="AC-01"), linked(README, path="README.md")],
              AC01="CODE_CHANGES_REQUIRED", AC02="MANUAL_ACTION_REQUIRED"),
    3: review("APPROVED", [again(GAME, kind="MANUAL_ACTION_REQUIRED", path="", criterion_id="AC-02")], AC01="SATISFIED",
              AC02="MANUAL_ACTION_REQUIRED"),
}
WRITES = {1: "bad", 2: "good", 3: "good"}
# Its findings as the end of it leaves them: (kind, file, criterion, owner, status, first and last iteration, reports).
FOUND = [("CODE_CHANGES_REQUIRED", "README.md", "", "developer", "PENDING_VERIFICATION", 2, 2, 1),
         ("CODE_CHANGES_REQUIRED", "feature.txt", "AC-01", "developer", "PENDING_VERIFICATION", 1, 2, 2),
         ("MANUAL_ACTION_REQUIRED", "", "AC-02", "user", "OPEN", 1, 3, 2)]
# Where scenario C crashes, as (before or after `core._save` writes the checkpoint, its last event, its iteration).
CRASHES = (("before", "review_completed", 1), ("before", "evidence_invalidated", 2), ("after", "review_started", 2),
           ("before", "review_completed", 2), ("before", "code_approved", 3), ("after", "code_approved", 3),
           ("before", "evidence_recorded", 3), ("before", "task_completed", 3))
KEPT = (("after", "development_started", 2), ("after", "review_started", 2), ("after", "task_paused", 3))  # for B, C and D


class Scripted:
    """A developer and a reviewer that answer by iteration, so a step asked again after a crash gets the same answer.

    An issue made with `again` takes the ID the reviewer's prompt shows for its kind, file and criterion,
    as a reviewer that reports a problem again would. `fail` lists (role, iteration) that meet a usage
    limit once.
    """

    def __init__(self, reviews=None, writes=None, fail=()):
        self.reviews, self.writes, self.fail, self.calls = reviews or {}, writes or {}, set(fail), []

    def invoke(self, provider, role, prompt, workspace, run_dir):
        number = int(run_dir.name.rpartition("-")[2])
        self.calls.append((role, number))
        if (role, number) in self.fail:
            self.fail.discard((role, number))
            raise AgentFailure("Usage limit reached", "quota", provider=provider)
        if role == "developer":
            (workspace / "feature.txt").write_text(self.writes.get(number, "good"), encoding="utf-8")
            return AgentReply(text="## Changes\nEverything is done.", provider=provider)
        # Without a scripted answer it approves, with an outcome for each criterion its prompt lists.
        answer = copy.deepcopy(self.reviews.get(number)) or {"verdict": "APPROVED", "summary": "Checked the worktree", "issues": [],
                                                               "criteria": outcomes(prompt)}
        shown = {(kind, path, criterion): name for name, kind, path, criterion in SHOWN.findall(prompt)}
        for issue in answer["issues"]:
            if issue.pop("again", False):
                issue["finding_id"] = shown.get((issue["kind"], issue["path"] or "no file", issue["criterion_id"] or "no criterion"), "")
        return AgentReply(text=json.dumps(answer), provider=provider)


def records(path: Path) -> tuple[dict, list[str]]:
    """Every record of every ledger of a task, those `evidence.restart` put aside included, by ID; and an ID found twice."""
    found, twice = {}, []
    for file in (path / evidence.LEDGER, *sorted(path.glob(evidence.ARCHIVED))):
        for line in file.read_bytes().splitlines() if file.exists() else []:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and isinstance(record.get("id"), str):
                if record["id"] in found:
                    twice.append(record["id"])
                found[record["id"]] = record
    return found, twice


def coherent(path: Path) -> dict:
    """Rebuild a task from its four records and check every reference. Returns the problems, the orphans and the reviews.

    An orphan is a review record that no event names, as a crash between the ledger and the next
    checkpoint leaves it: then nothing else may name it either. A list an event saved only in part
    (`truncated`) is compared with the start of the whole one, never taken for all of it.
    """
    state = read_json(path / "state.json")
    every, twice = records(path)
    problems = [f"{name} is in the ledgers twice" for name in twice]
    ledger = evidence.read(path)
    problems += [f"active ledger: {problem}" for problem in ledger.problems]
    registry = Findings.load(state)

    def record(name, kind):
        found = every.get(name)
        if found is None or found.get("kind") != kind:
            problems.append(f"{name} is not a {kind} record of the task's ledgers")
            return None
        return found

    def same(event, key, whole) -> bool:
        cut = (event.get("truncated") or {}).get(key)
        return event.get(key) == (whole[:history.LIST_LIMIT] if cut is not None else whole) and cut in (None, len(whole))

    events = [entry for entry in state["history"] if isinstance(entry, dict) and "id" in entry]
    numbers = [int(entry["id"][3:]) for entry in events]
    if numbers != sorted(set(numbers)):
        problems.append(f"event IDs given twice or out of order: {numbers}")
    problems += [f"{entry['id']} belongs to another task or schema" for entry in events
                 if (entry.get("version"), entry.get("task_id")) != (history.VERSION, state["id"])]
    named = {}
    for entry in events:
        kind = entry["event"]
        if kind == "review_completed":
            found = record(entry["evidence"], "review")
            if found is None:
                continue
            named.setdefault(found["id"], []).append(entry["id"])
            listed = found.get("findings") or []
            if (found["verdict"], found["iteration"], found["issues"]) != (entry["verdict"], entry["iteration"], entry["issues"]):
                problems.append(f"{entry['id']} and {found['id']} tell another review")
            if not same(entry, "finding_ids", listed) or entry["unrecorded_issues"] != entry["issues"] - len(listed):
                problems.append(f"{entry['id']} does not name the findings of {found['id']}")
            if "truncated" not in entry and (set(entry["new_findings"]) & set(entry["linked_findings"])
                                             or set(entry["new_findings"]) | set(entry["linked_findings"]) != set(listed)):
                problems.append(f"{entry['id']}: new and linked findings do not add up")
            problems += [f"{name} does not name {found['id']}" for name in listed
                         if registry.get(name) is None or found["id"] not in registry.get(name).reported_in]
        elif kind in {"code_approved", "task_completed"}:
            approval = record(entry["evidence"], "review") if kind == "code_approved" else None
            if approval is not None and approval["verdict"] != "APPROVED":
                problems.append(f"{entry['id']} names a review that did not approve the code")
            for name in entry.get("pending_verification") or []:
                item = registry.get(name)
                waits = item.verification["review"] if item is not None and item.verification else None
                if waits is None or (approval is not None and waits != approval["id"]):
                    problems.append(f"{entry['id']}: {name} does not wait for the verification it names")
        elif kind == "evidence_recorded":
            found = record(entry["evidence"], "manual")
            if found is not None and entry["criterion"] not in found["criteria"]:
                problems.append(f"{entry['id']} names a check of another criterion")
        elif kind == "authorization_decided":
            record(entry["evidence"], "authorization")
        elif kind == "evidence_invalidated":
            for name in entry["invalidations"]:
                record(name, "invalidation")
        elif kind == "evidence_ledger_restarted":
            record(entry["evidence"], "restart")
    problems += [f"{name} is named by {by}" for name, by in named.items() if len(by) > 1]
    orphans = [name for name, found in every.items() if found.get("kind") == "review" and name not in named]
    for item in registry.items:
        if item.status == findings.Status.RESOLVED:
            problems.append(f"{item.id} is resolved")
        reports = [every[name] for name in item.reported_in if record(name, "review") is not None]
        problems += [f"{item.id} names {found['id']}, which does not name it" for found in reports if item.id not in (found.get("findings") or [])]
        problems += [f"{item.id} names {name}, which no event names" for name in item.reported_in if name in orphans]
        if reports and (reports[0]["iteration"], max(found["iteration"] for found in reports)) != (item.first_seen_iteration,
                                                                                                 item.last_seen_iteration):
            problems.append(f"{item.id}: its iterations are not those of its reports")
        if item.status == findings.Status.PENDING_VERIFICATION:
            approval = record(item.verification["review"], "review")
            if approval is not None and (approval["verdict"] != "APPROVED" or item.id in (approval.get("findings") or [])):
                problems.append(f"{item.id} waits for a review that did not leave it out of an approval")
    problems += [f"the findings recorded {name}, which no event names" for name in registry.reviews if name not in named]
    if any(int(name[3:]) >= registry.next for found in every.values() for name in found.get("findings") or []):
        problems.append("the next finding ID is not past every ID the ledgers name")
    if "acceptance" in state:
        saved = Acceptance.load(state)
        if not ledger.anchored(saved.ledger.get("count"), saved.ledger.get("head")):
            problems.append("the criteria were judged from records the ledger no longer holds")
        files = (state.get("test_context") or {}).get("fingerprint")
        for criterion in saved.criteria:
            for name in criterion.evidence:
                found = ledger.get(name)
                if not ledger.counts(found):
                    problems.append(f"{criterion.id} rests on {name}, which does not count")
                elif found.get("scope", "code") == "code" and found.get("kind") != "authorization" and found.get("fingerprint") != files:
                    problems.append(f"{criterion.id} rests on {name}, recorded on other files")
        if saved.code.get("status") == "APPROVED":
            approval = ledger.get(saved.code.get("evidence"))
            if not ledger.counts(approval) or approval.get("verdict") != "APPROVED":
                problems.append("the code is approved by a review that does not count")
    return {"problems": problems, "orphans": orphans, "reviews": len(named)}


def structure(path: Path) -> list[tuple]:
    """The findings of a task without their IDs: what a crash may number again, and must never change."""
    return sorted((item.kind or "", item.path, item.criterion_id or "", item.owner, item.status, item.first_seen_iteration,
                   item.last_seen_iteration, len(item.reported_in)) for item in Findings.load(read_json(path / "state.json")).items)


# What the sources from before findings and workflow events (d4c0ecd, October 16, 2026) did not write.
EVENT_FIELDS = ("version", "id", "task_id", "phase", "truncated")
ADDED = {"review_completed": ("evidence", "issues", "finding_ids", "new_findings", "linked_findings", "unrecorded_issues", "normalized"),
         "code_approved": ("pending_verification",), "task_completed": ("pending_verification",),
         "evidence_invalidated": ("invalidations",), "evidence_ledger_restarted": ("evidence",)}


def downgrade(path: Path) -> None:
    """Leave a task as the sources from before findings and workflow events would have: d4c0ecd, October 16, 2026.

    Events lose the fields of `history.py` and the references added with them, the state its
    `findings`, the issues of its review the backend's annotations, and the `review` records of the
    ledger their `findings`, the chain written again. Compared field by field, state and ledger, with
    tasks d4c0ecd made on October 17, 2026 at nine points of the same scripted scenarios (done,
    blocked, paused at develop, test, review and validate, interrupted, crashed after a review record):
    what differed was only what changes from one run to the next (times, task IDs, paths, hashes,
    commits of the test repository).
    """
    state = read_json(path / "state.json")
    state.pop("findings", None)
    state["history"] = [{key: value for key, value in entry.items() if key not in EVENT_FIELDS + ADDED.get(entry.get("event"), ())}
                        for entry in state["history"]]
    if isinstance(state.get("review"), dict):
        state["review"]["issues"] = [{key: value for key, value in issue.items() if key not in findings.ANNOTATIONS}
                                     for issue in state["review"]["issues"]]
    file, previous, hashes, lines = path / evidence.LEDGER, "", [], []
    for line in file.read_bytes().splitlines() if file.exists() else []:
        record = json.loads(line)
        record.pop("findings", None)
        record["prev"] = previous
        record["hash"] = previous = evidence._digest(record)
        hashes.append(previous)
        lines.append(json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
    if lines:
        file.write_bytes(b"".join(lines))
    seen = state["acceptance"]["ledger"]
    if seen.get("count"):
        seen["head"] = hashes[seen["count"] - 1]
    save_json(path / "state.json", state)


def finish(env: Case, task_id: str, adapter, *, attach=True) -> dict:
    """Run a task until it ends as a person would: resume after a usage limit, attach the check of AC-02 when it waits."""
    path = env.home / "tasks" / task_id
    for _ in range(6):
        state = run_task(env.home, task_id, adapter=adapter)
        error = state["last_error"] if isinstance(state["last_error"], dict) else {}
        if state["status"] != "paused":
            break
        if error.get("kind") == "quota":
            continue
        if not (attach and state["phase"] == "validate" and env.statuses(path).get("AC-02") != "VERIFIED"):
            break
        attach_evidence(env.home, task_id, "AC-02", summary="Loaded the mod in Mod Organizer 2 and played", reference="notes/mo2.txt")
    return state


class Snapshots:
    """The files of one task at chosen checkpoints, as a crash just before `core._save` writes one, or just after, leaves them.

    The task's folder is copied without its lock, as after a person checked that the process is gone
    and unlocked it, and so is the one file the developer writes in the worktree.
    """

    def __init__(self, path: Path, workspace: Path, folder: Path, wanted, calls: list):
        self.path, self.workspace, self.folder, self.wanted, self.calls = path, workspace, folder, wanted, calls
        self.points: dict[tuple, dict] = {}

    def wrap(self, original):
        def save(where, state):
            last = state["history"][-1] if state["history"] else {}
            key = (last.get("event"), last.get("iteration"))
            self.keep(("before", *key))
            original(where, state)
            self.keep(("after", *key))
        return save

    def keep(self, point: tuple) -> None:
        if point in self.points or (self.wanted is not None and point not in self.wanted):
            return
        target = self.folder / f"{len(self.points):02d}"
        shutil.copytree(self.path, target, ignore=shutil.ignore_patterns(".run.lock"))
        feature = self.workspace / "feature.txt"
        self.points[point] = {"folder": target, "calls": len(self.calls), "feature": feature.read_bytes() if feature.exists() else None,
                              "history": read_json(target / "state.json")["history"]}

    def restore(self, point: tuple) -> dict:
        kept = self.points[point]
        shutil.rmtree(self.path)
        shutil.copytree(kept["folder"], self.path)
        feature = self.workspace / "feature.txt"
        if kept["feature"] is None:
            feature.unlink(missing_ok=True)
        else:
            feature.write_bytes(kept["feature"])
        return kept


class CheckpointTests(unittest.TestCase):
    """One run of scenario A, with the task's files kept at checkpoints; each test starts again from one of them."""

    @classmethod
    def setUpClass(cls):
        cls.env = Case("setUp")  # the repository, state directory and test command of the other engine tests
        cls.env.setUp()
        cls.addClassCleanup(cls.env.doCleanups)
        cls.task_id, cls.path, cls.workspace = cls.env.task(GOOD, MO2)
        cls.adapter = Scripted(REVIEWS, WRITES)
        cls.everywhere = os.environ.get("PATCHRONDO_ALL_CHECKPOINTS") == "1"
        wanted = None if cls.everywhere else {*CRASHES, *KEPT}
        cls.snapshots = Snapshots(cls.path, cls.workspace, Path(cls.env.tmp.name) / "checkpoints", wanted, cls.adapter.calls)
        with patch.object(core, "_save", side_effect=cls.snapshots.wrap(core._save)):
            cls.final = finish(cls.env, cls.task_id, cls.adapter)
        cls.snapshots.wanted = {("end",)}
        cls.snapshots.keep(("end",))
        cls.snapshots.wanted = set()  # nothing more is kept
        cls.statuses = cls.env.statuses(cls.path)

    def resume(self, point, adapter=None, **options) -> tuple[dict, Scripted, dict]:
        kept = self.snapshots.restore(point)
        adapter = adapter or Scripted(REVIEWS, WRITES)
        return finish(self.env, self.task_id, adapter, **options), adapter, kept

    def test_a_problem_corrected_over_iterations_is_followed_through_every_record(self):
        """A: each review rebuilt from its event: the ledger record, the criteria it judged, its findings, the task at the end."""
        self.snapshots.restore(("end",))
        state, path = read_json(self.path / "state.json"), self.path
        self.assertEqual((state["status"], state["iteration"], self.statuses), ("done", 3, {"AC-01": "VERIFIED", "AC-02": "VERIFIED"}))
        traced = coherent(path)
        self.assertEqual((traced["problems"], traced["orphans"], traced["reviews"]), ([], [], 3))
        every, _ = records(path)
        completed = [entry for entry in state["history"] if entry["event"] == "review_completed"]
        reviewed = [every[entry["evidence"]] for entry in completed]
        self.assertEqual([(entry["iteration"], entry["verdict"], entry["finding_ids"], entry["new_findings"], entry["linked_findings"])
                          for entry in completed],
                         [(1, "CHANGES_REQUESTED", ["RF-001", "RF-002"], ["RF-001", "RF-002"], []),
                          (2, "CHANGES_REQUESTED", ["RF-001", "RF-003"], ["RF-003"], ["RF-001"]),
                          (3, "APPROVED", ["RF-002"], [], ["RF-002"])])
        # The record of each review: the same verdict, the outcome it gave to each criterion, the same findings.
        self.assertEqual([(found["verdict"], found["outcomes"], found["findings"]) for found in reviewed],
                         [("CHANGES_REQUESTED", {"AC-01": "CODE_CHANGES_REQUIRED", "AC-02": "MANUAL_ACTION_REQUIRED"}, ["RF-001", "RF-002"]),
                          ("CHANGES_REQUESTED", {"AC-01": "CODE_CHANGES_REQUIRED", "AC-02": "MANUAL_ACTION_REQUIRED"}, ["RF-001", "RF-003"]),
                          ("APPROVED", {"AC-01": "SATISFIED", "AC-02": "MANUAL_ACTION_REQUIRED"}, ["RF-002"])])
        # The problem reported again in other words kept its ID; each finding names the reviews that reported it.
        saved = Findings.load(state)
        self.assertEqual([(item.id, item.criterion_id, item.reported_in, item.status) for item in saved.items], [
            ("RF-001", "AC-01", [reviewed[0]["id"], reviewed[1]["id"]], "PENDING_VERIFICATION"),
            ("RF-002", "AC-02", [reviewed[0]["id"], reviewed[2]["id"]], "OPEN"),
            ("RF-003", None, [reviewed[1]["id"]], "PENDING_VERIFICATION")])
        self.assertEqual(saved.get("RF-001").description, "feature.txt still ends without a newline")  # as last reported
        self.assertEqual(structure(path), FOUND)
        # The approval named its record and what it made wait for a verification; nothing was resolved by it.
        approved = next(entry for entry in state["history"] if entry["event"] == "code_approved")
        self.assertEqual((approved["evidence"], approved["pending_verification"], approved["open"]), (reviewed[2]["id"], ["RF-001", "RF-003"], 1))
        self.assertEqual({item.verification["review"] for item in saved.items if item.verification}, {reviewed[2]["id"]})
        # The criteria at the end: the code by the approving review, the check of the person by its own record.
        record = Acceptance.load(state)
        manual = next(found["id"] for found in every.values() if found["kind"] == "manual")
        self.assertEqual([(item.id, item.evidence) for item in record.criteria], [("AC-01", [reviewed[2]["id"]]), ("AC-02", [manual])])
        self.assertEqual((record.code["status"], record.code["evidence"], record.results()), ("APPROVED", reviewed[2]["id"],
                                                                                              ["CODE_APPROVED", "RELEASE_READY"]))
        # The report shows the same chain: each event with its ID and what it names, each issue with its finding.
        report = (path / "report.md").read_text(encoding="utf-8")
        second = completed[1]
        self.assertIn(f"- {second['id']} · {second['at']} · iteration 2 · `review_completed` (verdict CHANGES_REQUESTED; "
                      f"evidence {reviewed[1]['id']}; finding_ids RF-001, RF-003)", report)
        self.assertIn(f"`code_approved` (evidence {reviewed[2]['id']}; pending_verification RF-001, RF-003)", report)
        self.assertIn(f": {GAME} [RF-002, reported by an earlier review too]", report)

    def test_a_crash_before_or_after_a_checkpoint_neither_duplicates_nor_reinterprets(self):
        """C: from each checkpoint kept, resumed until the task ends; what it ends with is what the run without a crash ended with.

        A step cut short is asked again, once; the record a crash left without its checkpoint is named
        by nothing and proves nothing for the files it was not made on; no record, event or finding ID
        is given twice; what was saved before the crash is kept as it was.
        """
        self.snapshots.restore(("end",))
        every, _ = records(self.path)
        kinds = [found["kind"] for found in every.values()]
        runs = len({found["run"] for found in every.values() if found["kind"] == "command"})
        crashes = [point for point in self.snapshots.points if point != ("end",)] if self.everywhere else list(CRASHES)
        self.assertEqual([point for point in crashes if point not in self.snapshots.points], [])  # each of them was reached
        for point in crashes:
            with self.subTest(crash=point):
                state, adapter, kept = self.resume(point)
                traced = coherent(self.path)
                self.assertEqual((traced["problems"], state["status"], self.env.statuses(self.path)), ([], "done", self.statuses))
                self.assertEqual(structure(self.path), FOUND)
                self.assertLessEqual(kept["calls"] + len(adapter.calls), len(self.adapter.calls) + 1)
                self.assertEqual(state["history"][:len(kept["history"])], kept["history"])
                found, _ = records(self.path)
                after = [record["kind"] for record in found.values()]
                self.assertLessEqual(len(traced["orphans"]), 1)
                self.assertLessEqual(after.count("review"), kinds.count("review") + 1)
                self.assertLessEqual(len({record["run"] for record in found.values() if record["kind"] == "command"}), runs + 1)
                self.assertEqual(after.count("manual"), 1)  # the check of the person is recorded once, whatever was lost

    def test_a_quota_failure_of_the_reviewer_resumes_into_the_same_records(self):
        """C: the reviewer meets its usage limit at the second review; the task is resumed and ends as without it."""
        state, adapter, _ = self.resume(("after", "review_started", 2), Scripted(REVIEWS, WRITES, fail=[("reviewer", 2)]))
        events = [entry["event"] for entry in state["history"]]
        self.assertEqual((state["status"], coherent(self.path)["problems"], structure(self.path)), ("done", [], FOUND))
        self.assertEqual(adapter.calls[:2], [("reviewer", 2), ("reviewer", 2)])
        paused = events.index("task_paused")
        self.assertEqual(events[paused - 1:paused + 4], ["review_started", "task_paused", "run_started", "review_started", "review_completed"])
        self.assertEqual(next(entry for entry in state["history"] if entry["event"] == "task_paused")["reason"], "quota")

    def test_references_stay_resolvable_when_evidence_is_invalidated_and_the_ledger_started_again(self):
        """C: the files change after the pause, then the ledger is edited: what the events and findings name stays where it was."""
        self.snapshots.restore(("after", "task_paused", 3))
        (self.workspace / "extra.txt").write_text("written after the approval", encoding="utf-8")
        recheck_acceptance(self.env.home, self.task_id)
        self.assertEqual(self.env.statuses(self.path), {"AC-01": "PENDING", "AC-02": "AWAITING_MANUAL_VALIDATION"})
        file = self.path / evidence.LEDGER
        file.write_bytes(file.read_bytes().replace(b'"result":"failed"', b'"result":"passed"', 1))  # a record edited by hand
        adapter = Scripted(REVIEWS, WRITES)
        try:
            state = finish(self.env, self.task_id, adapter)
        finally:
            (self.workspace / "extra.txt").unlink()
        self.assertEqual((state["status"], adapter.calls), ("done", [("reviewer", 3)]))  # the approval is asked for again, nothing more
        traced = coherent(self.path)
        self.assertEqual((traced["problems"], structure(self.path)), ([], [*FOUND[:2], (*FOUND[2][:7], 3)]))
        saved = Findings.load(read_json(self.path / "state.json"))
        self.assertTrue(saved.get("RF-002").reported_in[-1].startswith("E2-"))
        self.assertTrue(all(not name.startswith("E2-") for name in saved.get("RF-002").reported_in[:-1]))
        self.assertTrue(all(name.startswith("E2-") for criterion in self.env.record(self.path).criteria for name in criterion.evidence))
        events = {entry["event"]: entry for entry in state["history"]}
        self.assertEqual(records(self.path)[0][events["evidence_invalidated"]["invalidations"][0]]["kind"], "invalidation")
        self.assertEqual(events["evidence_ledger_restarted"]["evidence"], "E2-0001")

    def test_a_task_at_its_iteration_limit_is_left_coherent(self):
        """C: blocked at the second review; its findings stay open, and a Resume returns it as it is."""
        self.env.configure(max_iterations=2)
        try:
            state, adapter, _ = self.resume(("after", "review_started", 2))
        finally:
            self.env.configure(max_iterations=6)
        self.assertEqual((state["status"], state["last_error"], adapter.calls), ("blocked", "max_iterations_reached", [("reviewer", 2)]))
        self.assertEqual(coherent(self.path)["problems"], [])
        self.assertEqual({item.status for item in Findings.load(state).items}, {"OPEN"})
        self.assertEqual([entry["event"] for entry in state["history"][-2:]], ["review_completed", "task_blocked"])
        before = (self.path / "state.json").read_bytes()
        self.assertEqual(run_task(self.env.home, self.task_id, adapter=adapter)["status"], "blocked")
        self.assertEqual(((self.path / "state.json").read_bytes(), adapter.calls), (before, [("reviewer", 2)]))

    def test_a_task_left_by_the_sources_before_findings_and_workflow_events_runs_on(self):
        """D: written as d4c0ecd would have left it, at three points; it runs on and its older records stay as they were."""
        for point in KEPT:
            with self.subTest(point=point):
                self.snapshots.restore(point)
                downgrade(self.path)
                before = read_json(self.path / "state.json")
                self.assertNotIn("findings", before)
                self.assertTrue(all("id" not in entry for entry in before["history"]))
                older = before["history"]
                if point[1] == "task_paused":
                    raw, adapter = (self.path / "state.json").read_bytes(), Scripted(REVIEWS, WRITES)
                    for _ in range(2):
                        run_task(self.env.home, self.task_id, adapter=adapter)
                    self.assertEqual(((self.path / "state.json").read_bytes(), adapter.calls), (raw, []))  # nothing to do
                state = finish(self.env, self.task_id, Scripted(REVIEWS, WRITES))
                self.assertEqual((state["status"], self.env.statuses(self.path)), ("done", self.statuses))
                self.assertEqual(state["history"][:len(older)], older)
                self.assertEqual([entry["id"] for entry in state["history"][len(older):]],
                                 [f"WE-{number:04d}" for number in range(len(older) + 1, len(state["history"]) + 1)])
                traced = coherent(self.path)
                self.assertEqual(traced["problems"], [])
                # Only the reviews after the older sources are findings: one before them was never recorded as any.
                resumed = point[2]
                self.assertTrue(all(item.first_seen_iteration >= resumed for item in Findings.load(state).items))
                if point[1] == "task_paused":
                    self.assertEqual([entry["event"] for entry in state["history"][len(older):]],
                                     ["evidence_recorded", "run_started", "task_completed"])


class RecordTests(Case):
    def test_approved_code_waits_for_what_only_a_person_can_do_without_another_iteration(self):
        """B: a check in another program, a download and a publication; the code is approved once and nobody loops."""
        task_id, path, _ = self.task(GOOD, MO2, DOWNLOAD, PUBLISH)
        approval = review("APPROVED", [linked(GAME, kind="MANUAL_ACTION_REQUIRED", path="", criterion_id="AC-02"),
                                       linked("Publishing needs your decision", kind="APPROVAL_REQUIRED", path="", criterion_id="AC-04"),
                                       linked("Clear naming", kind="OBSERVATION")],
                          AC01="SATISFIED", AC02="MANUAL_ACTION_REQUIRED", AC03="APPROVAL_REQUIRED", AC04="APPROVAL_REQUIRED")
        adapter = Scripted({1: approval})
        state = run_task(self.home, task_id, adapter=adapter)
        self.assertEqual((state["status"], state["phase"], state["last_error"]["kind"], adapter.calls),
                         ("paused", "validate", "awaiting_authorization", [("developer", 1), ("reviewer", 1)]))
        self.assertEqual(self.record(path).results(), ["CODE_APPROVED", "AWAITING_AUTHORIZATION"])
        self.assertEqual(self.statuses(path), {"AC-01": "VERIFIED", "AC-02": "AWAITING_MANUAL_VALIDATION",
                                               "AC-03": "AWAITING_AUTHORIZATION", "AC-04": "AWAITING_AUTHORIZATION"})
        saved = Findings.load(state)
        self.assertEqual([(item.owner, item.blocking, item.status) for item in saved.items],
                         [("user", True, "OPEN"), ("user", True, "OPEN"), ("nobody", False, "OPEN")])
        approved = next(entry for entry in state["history"] if entry["event"] == "code_approved")
        self.assertEqual((approved["open"], approved["pending_verification"]), (3, []))
        self.assertFalse((path / "feedback.md").exists())  # nothing went back to the developer
        raw = (path / "state.json").read_bytes()
        for _ in range(3):
            run_task(self.home, task_id, adapter=adapter)
        self.assertEqual(((path / "state.json").read_bytes(), len(adapter.calls)), (raw, 2))
        report = (path / "report.md").read_text(encoding="utf-8")
        self.assertIn(f": {GAME} [RF-001, first reported here]", report)
        # The person decides and checks; the run that follows calls nobody and completes the task.
        for criterion in ("AC-03", "AC-04"):
            decide_authorization(self.home, task_id, criterion, granted=True)
        for criterion, what in (("AC-02", "Loaded the mod in Mod Organizer 2"), ("AC-03", "Downloaded it from the model page"),
                                ("AC-04", "Published version 1.0 to PyPI")):
            attach_evidence(self.home, task_id, criterion, summary=what, reference="https://example.invalid/proof")
        state = run_task(self.home, task_id, adapter=adapter)
        self.assertEqual((state["status"], len(adapter.calls), self.record(path).results()), ("done", 2, ["CODE_APPROVED", "RELEASE_READY"]))
        self.assertEqual([entry["event"] for entry in state["history"]][-7:],
                         ["authorization_decided", "authorization_decided", "evidence_recorded", "evidence_recorded", "evidence_recorded",
                          "run_started", "task_completed"])
        self.assertEqual(coherent(path)["problems"], [])
        self.assertEqual(Findings.load(state).dump(), saved.dump())  # a criterion verified resolves no finding

    def test_a_review_of_more_than_a_hundred_problems_is_kept_whole_where_it_is_authoritative(self):
        """E: the events keep 100 IDs of each list and say how many there were; the findings and the ledger record keep 120."""
        task_id, path, _ = self.task(GOOD)
        many = [linked(f"Problem number {number}", path=f"module_{number:03d}.py") for number in range(1, 121)]
        state = run_task(self.home, task_id, adapter=Scripted({1: review("CHANGES_REQUESTED", many, AC01="CODE_CHANGES_REQUIRED")}))
        self.assertEqual(state["status"], "done")
        names = [f"RF-{number:03d}" for number in range(1, 121)]
        traced = coherent(path)
        self.assertEqual((traced["problems"], traced["reviews"]), ([], 2))
        self.assertEqual([item.id for item in Findings.load(state).items], names)
        completed = next(entry for entry in state["history"] if entry["event"] == "review_completed")
        self.assertEqual((records(path)[0][completed["evidence"]]["findings"], completed["truncated"]),
                         (names, {"finding_ids": 120, "new_findings": 120}))
        report = (path / "report.md").read_text(encoding="utf-8")
        history_rows = report.partition("## History")[2]
        self.assertIn("finding_ids RF-001, RF-002, ", history_rows)
        self.assertIn("RF-100 (this event lists 100 of 120)", history_rows)
        self.assertIn("pending_verification RF-001, ", history_rows)
        self.assertNotIn("RF-101", history_rows)  # what the event left out is not invented: it says how many there were

    def test_anomalies_in_a_saved_history_are_shown_as_such_and_change_nothing(self):
        """Entries that are not events, or lack a field, stay as they are; runs, checks and reports go on and say so."""
        task_id, path, _ = self.task(GOOD, MO2)
        state = self.state(path)
        odd = [None, "not an event", {"event": "run_started"}, {"at": "2026-10-01T10:00:00+00:00", "iteration": 1}]
        state["history"] = copy.deepcopy(odd)
        save_json(path / "state.json", state)
        state = run_task(self.home, task_id, adapter=Scripted())
        self.assertEqual((state["status"], state["phase"]), ("paused", "validate"))
        attach_evidence(self.home, task_id, "AC-02", summary="Loaded the mod in Mod Organizer 2", reference="notes/mo2.txt")
        state = run_task(self.home, task_id, adapter=Scripted())
        self.assertEqual(state["status"], "done")
        saved = self.state(path)["history"]
        self.assertEqual((saved[:4], saved[4]["id"]), (odd, "WE-0005"))
        self.assertEqual(coherent(path)["problems"], [])
        report = (path / "report.md").read_text(encoding="utf-8")
        self.assertEqual(report.count("- An entry of the history that is not an event; it is kept in state.json as it is"), 2)
        self.assertIn("- unknown · iteration unknown · `run_started`", report)
        self.assertIn("- 2026-10-01T10:00:00+00:00 · iteration 1 · `unknown`", report)
        self.assertIn(f"- WE-0005 · {saved[4]['at']} · iteration 1 · `run_started`", report)
        state = self.state(path)
        state["history"] = {"not": "a list"}
        save_json(path / "state.json", state)
        self.assertIn("The history saved with this task cannot be read: it is not a list.", render_report(state))


if __name__ == "__main__":
    unittest.main()
