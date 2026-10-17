"""Acceptance criteria, capabilities and evidence: the rules of the engine, and the task loop that uses them.

Providers are scripted: no CLI is started. The engine scenarios are named
after what they establish; the letters are those of the feature's request.
"""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from patchrondo import acceptance, capabilities, evidence
from patchrondo.acceptance import Acceptance, Status
from patchrondo.cli import main
from patchrondo.core import (AcceptanceRefused, attach_evidence, create_task, decide_authorization, initialize, parse_review,
                             preflight, recheck_acceptance, run_task)
from patchrondo.providers import AgentFailure, AgentReply
from patchrondo.storage import read_json, save_json

from support import outcomes

MO2 ="Verified inside Mod Organizer 2"
MO2_IT = "Verificare la mod dentro Mod Organizer 2"  # as written in the task that was sent back to the developer
GOOD = "feature.txt contains good"


def review(verdict="APPROVED", issues=(), summary="Checked the worktree", **outcomes):
    """A structured review: outcomes are given as AC01="SATISFIED" or AC02=("MANUAL_ACTION_REQUIRED", "note")."""
    listed = []
    for name, outcome in outcomes.items():
        outcome, note = outcome if isinstance(outcome, tuple) else (outcome, "")
        listed.append({"id": f"{name[:2]}-{name[2:]}", "outcome": outcome, "note": note})
    return {"verdict": verdict, "summary": summary, "issues": list(issues), "criteria": listed}


def issue(description, kind="CODE_CHANGES_REQUIRED", severity="medium", path="feature.txt"):
    return {"severity": severity, "path": path, "description": description, "kind": kind}


class Agents:
    """A scripted developer and reviewer. The developer always claims that everything is done.

    Without a scripted answer the reviewer approves and, as the review schema asks, gives every criterion its
    prompt lists an outcome: SATISFIED.
    """

    def __init__(self, reviews=None, writes=None):
        self.roles, self.prompts = [], []
        self.reviews = list(reviews or [])
        self.writes = list(writes or [])

    def invoke(self, provider, role, prompt, workspace, run_dir):
        self.roles.append(role)
        self.prompts.append(prompt)
        if role == "developer":
            (workspace / "feature.txt").write_text(self.writes.pop(0) if self.writes else "good", encoding="utf-8")
            return AgentReply(text="## Changes\nEverything is done: built, packaged, tried in the game, released and all "
                                   "acceptance criteria are verified.", provider=provider)
        answer = self.reviews.pop(0) if self.reviews else {"verdict": "APPROVED", "summary": "Checked worktree", "issues": [],
                                                           "criteria": outcomes(prompt)}
        return AgentReply(text=json.dumps(answer), provider=provider)


class Case(unittest.TestCase):
    """A repository and a state directory whose project runs one test command: feature.txt must hold "good"."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.repo, self.home = root / "repo", root / "state"
        self.repo.mkdir()
        for args in (["init", "-q"], ["config", "user.name", "T"], ["config", "user.email", "t@example.invalid"],
                     ["config", "commit.gpgsign", "false"]):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        (self.repo / "README.md").write_text("Initial\n", encoding="utf-8")
        for args in (["add", "."], ["commit", "-qm", "initial"]):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        initialize(self.home, self.repo)
        self.check = [sys.executable, "-c", "from pathlib import Path; assert Path('feature.txt').read_text() == 'good'"]
        self.configure(commands=[self.check])
        # No toolchain of this computer takes part unless a test says so.
        which = patch("patchrondo.capabilities.shutil.which", return_value=None)
        self.which = which.start()
        self.addCleanup(which.stop)

    def configure(self, commands=None, enabled=True, **workflow):
        cfg = read_json(self.home / "config.json")
        if commands is not None:
            cfg["tests"] = {"enabled": enabled, "trust_acknowledged": enabled, "commands": commands}
        cfg["workflow"].update(workflow)
        save_json(self.home / "config.json", cfg)

    def task(self, *criteria, developer="claude", reviewer="codex"):
        task_id, workspace = create_task(self.home, title="Feature", description="Write feature.txt with good",
                                         acceptance=list(criteria), developer=developer, reviewer=reviewer)
        return task_id, self.home / "tasks" / task_id, workspace

    def state(self, path):
        return read_json(path / "state.json")

    def record(self, path) -> Acceptance:
        return Acceptance.load(self.state(path))

    def statuses(self, path) -> dict:
        return {criterion.id: criterion.status for criterion in self.record(path).criteria}

    def kinds(self, path) -> list[str]:
        return [entry["kind"] for entry in evidence.read(path).records]

    def events(self, path) -> list[str]:
        return [entry["event"] for entry in self.state(path)["history"]]


class ClassificationTests(unittest.TestCase):
    def test_the_criteria_of_the_task_that_looped_are_told_apart(self):
        expected = {
            "Scaricare modelli GGUF": (["EXTERNAL"], ["network"]),
            "Calcolare hash SHA256": (["BUILD"], ["hash"]),
            "Compilare componenti C++": (["BUILD"], ["build"]),
            "Eseguire inferenza reale con llama.cpp": (["INTEGRATION"], ["external"]),
            "Creare archivi ZIP": (["BUILD"], ["package"]),
            "Eseguire Git commit e creare una PR": (["PUBLISHING"], ["publish"]),
            "Verificare il funzionamento dentro Mod Organizer 2": (["INTEGRATION"], ["external"]),
            "Download the GGUF model": (["EXTERNAL"], ["network"]),
            "Compile the C++ component": (["BUILD"], ["build"]),
            "Create the release ZIP archive": (["BUILD"], ["package"]),
            "Open a pull request": (["PUBLISHING"], ["publish"]),
            "Publish the mod on Nexus": (["PUBLISHING"], ["publish"]),
            "The test suite passes": (["TEST"], ["tests"]),
            "pytest passes": (["TEST"], ["tests"]),
            "Add a regression test for the parser": (["CODE", "TEST"], ["edit", "tests"]),
            "Checked by hand on a phone": (["MANUAL"], ["manual"]),
        }
        for text, (categories, needs) in expected.items():
            with self.subTest(text=text):
                found = acceptance.classify(text)
                self.assertEqual((list(found.categories), list(found.needs), found.source), (categories, needs, "rules"))
        self.assertEqual(acceptance.classify("Compilare componenti C++").toolchains, ("cpp",))

    def test_a_criterion_about_the_code_stays_one(self):
        for text in ("File exists", "Expired tokens are rejected", "Build a cache layer", "Make the button blue", "Done", "Tested",
                     "Passwords are hashed with bcrypt", "The deploy script is updated", "The upload endpoint validates the size"):
            with self.subTest(text=text):
                found = acceptance.classify(text)
                self.assertEqual((found.categories, found.needs, found.source), (("CODE",), ("edit",), "default"))

    def test_what_cannot_be_placed_is_unclassified_with_its_reason(self):
        found = acceptance.classify("Verificare licenze e regole Nexus")
        self.assertEqual((found.categories, found.source), ((), "unclassified"))
        self.assertIn("a licence", found.note)
        record = Acceptance.create(acceptance.parse_items(["Verificare licenze e regole Nexus"]))
        record.refresh(evidence.Ledger())
        self.assertEqual((record.criteria[0].status, record.criteria[0].feasibility, record.criteria[0].verifier),
                         (Status.UNKNOWN, "unknown", "user"))
        self.assertIn("could not tell", record.criteria[0].reason)

    def test_the_author_decides_with_tags_or_fields(self):
        tagged = Acceptance.create(acceptance.parse_items(
            ["[manual] The colours look right", "[optional] Nicer docs", "[code] Deploy to production works from the script",
             "[WIP] not a tag", {"text": "Whatever it says", "categories": ["INTEGRATION"], "required": False},
             "Dark mode (optional)"])).criteria
        self.assertEqual([(c.categories, c.required, c.classification["source"]) for c in tagged], [
            (["MANUAL"], True, "user"), (["CODE"], False, "default"), (["CODE"], True, "user"), (["CODE"], True, "default"),
            (["INTEGRATION"], False, "user"), (["CODE"], False, "default")])
        self.assertEqual(tagged[3].text, "[WIP] not a tag")
        for bad in ("one", [3], [{"text": 3}], [{"text": "x", "categories": ["MAGIC"]}], [{"text": "x", "categories": [["CODE"]]}],
                    [{"text": "x", "required": "yes"}], [{"text": "x", "depends_on": ["first"]}], [{"text": "x", "extra": 1}]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                acceptance.parse_items(bad)
        self.assertEqual(acceptance.parse_items([" a ", "", {"text": "  "}]), [{"text": "a", "required": None, "categories": None,
                                                                              "depends_on": []}])

    def test_a_task_without_criteria_has_one_about_its_description(self):
        record = Acceptance.create([])
        self.assertEqual([(c.id, c.text, c.categories, c.required) for c in record.criteria],
                         [("AC-01", acceptance.IMPLICIT, ["CODE"], True)])

    def test_dependencies_are_checked_and_ordered(self):
        items = acceptance.parse_items([{"text": "Create the release ZIP archive", "depends_on": ["AC-02"]}, "Compile the project"])
        record = Acceptance.create(items)
        record.refresh(evidence.Ledger())
        self.assertEqual((record.get("AC-01").status, record.get("AC-01").reason), (Status.PENDING, "It waits for AC-02"))
        for bad in ([{"text": "a", "depends_on": ["AC-01"]}], [{"text": "a", "depends_on": ["AC-09"]}],
                    [{"text": "a", "depends_on": ["AC-02"]}, {"text": "b", "depends_on": ["AC-01"]}]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Acceptance.create(acceptance.parse_items(bad))

    def test_criteria_are_read_back_from_a_task_file(self):
        document = "# T\n\n## Description\n\n- not a criterion\n\n## Acceptance criteria\n\n- First\n- [manual] Second\n\n## Later\n- no\n"
        self.assertEqual([item["text"] for item in acceptance.from_task_text(document)], ["First", "[manual] Second"])
        self.assertEqual(acceptance.from_task_text(f"## Acceptance criteria\n\n- {acceptance.IMPLICIT}\n"), [])
        self.assertEqual(acceptance.from_task_text("no such section"), [])

    def test_a_saved_record_that_is_not_one_is_refused(self):
        good = Acceptance.create(acceptance.parse_items(["a", "b"]))
        good.refresh(evidence.Ledger())
        block = json.loads(json.dumps(good.dump()))
        self.assertEqual([c.id for c in Acceptance.load({"acceptance": block}).criteria], ["AC-01", "AC-02"])
        self.assertIsNone(Acceptance.load({}))
        with self.assertRaisesRegex(ValueError, "has schema version 2; this version of PatchRondo reads version 1"):
            Acceptance.load({"acceptance": {**block, "version": 2}})
        for change in (lambda b: b.update(version=2), lambda b: b.update(version=True), lambda b: b.update(version="1"),
                       lambda b: b.update(criteria=[]), lambda b: b["criteria"][0].update(status="FINE"),
                       lambda b: b["criteria"][0].update(id="first"), lambda b: b["criteria"][0].update(required="yes"),
                       lambda b: b["criteria"][0].update(categories=[["CODE"]]), lambda b: b["criteria"][1].update(id="AC-01"),
                       lambda b: b["criteria"][0].update(proof=["word"]), lambda b: b["criteria"][0].update(evidence="E-0001"),
                       lambda b: b["criteria"][0].update(depends_on=["AC-07"]),
                       lambda b: (b["criteria"][0].update(depends_on=["AC-02"]), b["criteria"][1].update(depends_on=["AC-01"])),
                       lambda b: b["criteria"][0].update(status="VERIFIED", evidence=[]),
                       lambda b: b["criteria"][0].update(evidence=["the tests passed"])):
            broken = json.loads(json.dumps(block))
            change(broken)
            with self.subTest(broken=broken["criteria"][:1]), self.assertRaises(ValueError):
                Acceptance.load({"acceptance": broken})

    def test_a_status_that_was_saved_verifies_nothing(self):
        """Only the ledger verifies: a record that says VERIFIED, with references of the right shape, is judged again."""
        record = Acceptance.create(acceptance.parse_items(["a", "Checked by hand on a phone"]))
        record.refresh(evidence.Ledger())
        block = json.loads(json.dumps(record.dump()))
        for criterion in block["criteria"]:
            criterion.update(status="VERIFIED", evidence=["E-0001"], missing=[])
        block.update(code={"status": "APPROVED", "evidence": "E-0001", "iteration": 1}, outcome="RELEASE_READY")
        claimed = Acceptance.load({"acceptance": block})
        self.assertEqual([criterion.status for criterion in claimed.criteria], ["VERIFIED", "VERIFIED"])  # read as it was saved
        claimed.refresh(evidence.Ledger())
        self.assertEqual([(criterion.status, criterion.evidence) for criterion in claimed.criteria],
                         [("PENDING", []), ("AWAITING_MANUAL_VALIDATION", [])])
        self.assertEqual((claimed.code["status"], claimed.outcome, claimed.results()), ("PENDING", "IN_PROGRESS", []))


class CapabilityTests(unittest.TestCase):
    CFG = {"tests": {"enabled": True, "trust_acknowledged": True, "commands": [["python", "-m", "pytest", "-q"]]}}

    def detect(self, cfg=None, state=None, settings=None, toolchains=(), found=()):
        state = {"developer": "claude", "reviewer": "codex", "tests": [], "worktree": str(Path.cwd()), **(state or {})}
        return capabilities.detect(cfg or self.CFG, state, settings={"developer": {} if settings is None else settings},
                                   toolchains=toolchains, which=lambda name: f"/bin/{name}" if name in found else None)["executors"]

    def states(self, executor: dict) -> dict:
        return {name: entry["state"] for name, entry in executor["capabilities"].items()}

    def test_capabilities_belong_to_each_executor(self):
        found = self.detect()
        developer, runtime = self.states(found["developer"]), self.states(found["runtime"])
        # The developer edits files and has no shell; running the tests is PatchRondo's own capability.
        self.assertEqual((developer["files.write"], developer["shell"], developer["network"], developer["git.push"]),
                         ("declared", "absent", "absent", "absent"))
        self.assertEqual((runtime["commands.tests"], runtime["commands.build"], runtime["network"], runtime["git.push"], runtime["hash"]),
                         ("declared", "absent", "absent", "absent", "verified"))
        self.assertEqual(self.states(found["user"]), {"consent": "declared", "manual": "declared", "external": "unknown"})

    def test_the_developer_is_read_from_its_own_settings(self):
        shell = self.states(self.detect(settings={"tools": ["Read", "Edit", "Bash"], "risk_acknowledged": True})["developer"])
        self.assertEqual((shell["shell"], shell["network"], shell["git.commit"]), ("declared", "absent", "absent"))
        plan = self.states(self.detect(settings={"permission_mode": "plan"})["developer"])
        self.assertEqual((plan["files.read"], plan["files.write"]), ("declared", "absent"))
        codex = self.detect(state={"developer": "codex"})["developer"]
        self.assertEqual((self.states(codex)["shell"], self.states(codex)["files.write"]), ("declared", "declared"))
        self.assertIn("workspace-write", codex["capabilities"]["shell"]["source"])
        sealed = self.states(self.detect(state={"developer": "codex"}, settings={"sandbox": "read-only"})["developer"])
        self.assertEqual(sealed["files.write"], "absent")
        unreadable = capabilities.detect(self.CFG, {"developer": "claude", "reviewer": "codex"}, settings={"developer": None})
        self.assertEqual(self.states(unreadable["executors"]["developer"])["files.write"], "unknown")

    def test_the_reviewer_never_writes_and_a_passed_sandbox_check_is_an_observation(self):
        declared = self.states(self.detect()["reviewer"])
        self.assertEqual((declared["files.read"], declared["files.write"]), ("declared", "absent"))
        checked = {"status": "ready", "live": True, "checked_at": "2026-10-13T10:00:00+00:00", "checks": [
            {"id": "read.absolute", "status": "passed"}, {"id": "write.canary", "status": "passed"}]}
        verified = self.detect(state={"environment": checked})["reviewer"]
        self.assertEqual(self.states(verified)["files.read"], "verified")
        self.assertIn("refused", verified["capabilities"]["files.write"]["source"])
        simulated = self.detect(state={"environment": {**checked, "live": False}})["reviewer"]
        self.assertEqual(self.states(simulated)["files.read"], "declared")  # checks that were skipped observed nothing
        claude = self.states(self.detect(state={"reviewer": "claude"})["reviewer"])
        self.assertEqual((claude["files.read"], claude["files.write"], claude["shell"]), ("declared", "absent", "absent"))

    def test_the_runtime_has_what_the_project_allowed_and_nothing_else(self):
        for tests, expected in (({"enabled": False, "trust_acknowledged": False, "commands": []}, ("absent", None)),
                                ({"enabled": False, "trust_acknowledged": False, "commands": [["pytest"]]}, ("absent", "missing")),
                                ({"enabled": True, "trust_acknowledged": False, "commands": [["pytest"]]}, ("absent", "missing")),
                                ({"enabled": True, "trust_acknowledged": True, "commands": [["pytest"]]}, ("declared", "granted"))):
            with self.subTest(tests=tests):
                entry = self.detect(cfg={"tests": tests})["runtime"]["capabilities"]["commands.tests"]
                self.assertEqual((entry["state"], entry.get("consent")), expected)
        ran = self.detect(state={"tests": [{"status": "passed"}]})["runtime"]["capabilities"]["commands.tests"]
        self.assertEqual(ran["state"], "verified")

    def test_configured_commands_are_classified_by_their_own_words(self):
        for argv, expected in ((["pytest", "-q"], ["tests"]), (["cmake", "--build", "."], ["tests", "build"]),
                               ([r"C:\Tools\cmake.exe", "--build", "."], ["tests", "build"]), (["npm", "run", "build"], ["tests", "build"]),
                               (["npm", "test"], ["tests"]), (["cargo", "build", "--release"], ["tests", "build"]),
                               (["python", "-m", "build"], ["tests", "package"]), (["python3.13", "-m", "compileall", "."], ["tests", "build"]),
                               (["./build.sh"], ["tests", "build"]), (["dotnet", "pack"], ["tests", "package"]),
                               (["7z", "a", "out.7z", "dist"], ["tests", "package"]), (["python", "-c", "print('build')"], ["tests"])):
            with self.subTest(argv=argv):
                self.assertEqual(capabilities.covers(argv), expected)

    def test_toolchains_are_looked_for_and_never_started(self):
        runtime = self.detect(toolchains=["cpp", "rust", "unknown"], found=("g++",))["runtime"]["capabilities"]
        self.assertEqual((runtime["tool.cpp"]["state"], runtime["tool.rust"]["state"]), ("verified", "absent"))
        self.assertIn("g++ found at /bin/g++", runtime["tool.cpp"]["source"])
        self.assertNotIn("tool.unknown", runtime)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.file = self.path / evidence.LEDGER

    def test_records_are_chained_bounded_and_redacted(self):
        first = evidence.append(self.path, "manual", criteria=["AC-01"], result="passed", summary=evidence.text("x" * 5000, 2000),
                                reference=evidence.text("token " + "sk-" + "a" * 30))
        second = evidence.append(self.path, "authorization", criteria=["AC-02"], result="granted")
        ledger = evidence.read(self.path)
        self.assertEqual(([r["id"] for r in ledger.records], ledger.problems, ledger.head), (["E-0001", "E-0002"], [], second["hash"]))
        self.assertEqual((first["prev"], second["prev"]), ("", first["hash"]))
        self.assertLess(len(first["summary"]), 2100)
        self.assertNotIn("sk-aaaa", first["reference"])
        self.assertNotIn(b"\r\n", self.file.read_bytes())
        self.assertTrue(all(ledger.counts(record) for record in ledger.records))
        with self.assertRaises(ValueError):
            evidence.append(self.path, "opinion", result="passed")

    def test_a_changed_removed_or_inserted_record_is_noticed(self):
        for number in range(3):
            evidence.append(self.path, "manual", criteria=[f"AC-0{number + 1}"], result="passed", summary="checked")
        original = self.file.read_bytes()
        lines = original.split(b"\n")
        edited = json.loads(lines[1])
        edited["result"] = "failed"
        cases = {"changed": lines[:1] + [json.dumps(edited).encode()] + lines[2:], "removed": lines[:1] + lines[2:],
                 "inserted": lines[:1] + [lines[0]] + lines[1:], "not json": lines[:1] + [b"{broken"] + lines[2:]}
        for name, changed in cases.items():
            with self.subTest(name=name):
                self.file.write_bytes(b"\n".join(changed))
                ledger = evidence.read(self.path)
                self.assertEqual(len(ledger.problems), 1)
                self.assertEqual([record["trusted"] for record in ledger.records][:1], [True])  # what precedes it still stands
                self.assertFalse(any(record["trusted"] for record in ledger.records[1:]))
                self.assertEqual(ledger.head, "")
                with self.assertRaisesRegex(RuntimeError, "integrity"):
                    evidence.append(self.path, "manual", result="passed")
        self.file.write_bytes(original)
        self.assertEqual(evidence.read(self.path).problems, [])

    def test_a_task_state_notices_records_it_saw_that_are_gone(self):
        evidence.append(self.path, "manual", result="passed")
        seen = evidence.read(self.path)
        evidence.append(self.path, "manual", result="passed")
        self.assertTrue(evidence.read(self.path).anchored(seen.count, seen.head))  # more records than it saw is the normal case
        self.file.write_bytes(b"")
        self.assertFalse(evidence.read(self.path).anchored(seen.count, seen.head))
        self.assertTrue(evidence.read(self.path).anchored(0, ""))

    def test_a_record_cut_short_by_a_crash_is_dropped_by_the_next_write(self):
        evidence.append(self.path, "manual", result="passed")
        with self.file.open("ab") as stream:
            stream.write(b'{"id": "E-0002", "kind": "man')
        ledger = evidence.read(self.path)
        self.assertEqual((ledger.count, ledger.problems, ledger.incomplete), (1, [], True))
        evidence.append(self.path, "manual", result="passed")
        ledger = evidence.read(self.path)
        self.assertEqual(([record["id"] for record in ledger.records], ledger.problems, ledger.incomplete), (["E-0001", "E-0002"], [], False))

    def test_a_damaged_ledger_is_kept_aside_and_the_new_one_numbers_apart(self):
        evidence.append(self.path, "manual", result="passed")
        self.file.write_bytes(self.file.read_bytes().replace(b"passed", b"failed"))
        started = evidence.restart(self.path, evidence.read(self.path).problems)
        self.assertEqual((started["id"], started["kind"], started["generation"]), ("E2-0001", "restart", 2))
        self.assertEqual(len(list(self.path.glob("evidence.damaged-1-*.jsonl"))), 1)
        self.assertEqual(evidence.append(self.path, "manual", result="passed")["id"], "E2-0002")
        self.assertEqual(evidence.read(self.path).problems, [])

    def test_invalidation_is_a_later_record_and_files_are_hashed_here(self):
        artifact = self.path / "release.zip"
        artifact.write_bytes(b"one")
        hashed = evidence.fingerprint_file(artifact)
        self.assertEqual((hashed["size"], len(hashed["sha256"])), (3, 64))
        code = evidence.append(self.path, "command", result="passed", fingerprint="F1", scope="code")
        apart = evidence.append(self.path, "manual", result="passed", fingerprint="F1", scope="independent", artifacts=[hashed])
        decision = evidence.append(self.path, "authorization", result="granted")
        self.assertEqual(evidence.outdated(evidence.read(self.path), "F1"), {})
        stale = evidence.outdated(evidence.read(self.path), "F2")
        self.assertEqual(list(stale.values()), [[code["id"]]])  # neither the download nor the decision depends on the files
        artifact.write_bytes(b"another")
        self.assertEqual(sorted(sum(evidence.outdated(evidence.read(self.path), "F2").values(), [])), [code["id"], apart["id"]])
        evidence.append(self.path, "invalidation", of=[code["id"], decision["id"], "E-9999"], reason="files changed")
        ledger = evidence.read(self.path)
        self.assertFalse(ledger.counts(ledger.get(code["id"])))
        self.assertEqual(ledger.get(code["id"])["invalidated"]["reason"], "files changed")
        self.assertEqual(evidence.outdated(ledger, "F2"), {"A file this evidence refers to changed or is no longer there": [apart["id"]]})
        for bad in (self.path, self.path / "missing.bin"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                evidence.fingerprint_file(bad)


class ReviewTests(unittest.TestCase):
    def record(self, *criteria) -> Acceptance:
        record = Acceptance.create(acceptance.parse_items(list(criteria)))
        record.refresh(evidence.Ledger())
        return record

    def test_the_answer_of_a_reviewer_is_checked_field_by_field(self):
        base = {"verdict": "APPROVED", "summary": "ok", "issues": []}
        self.assertEqual(parse_review(json.dumps(base)), base)  # an answer from before criteria were tracked is still a review
        full = review(issues=[issue("m", "MANUAL_ACTION_REQUIRED", "critical")], AC01="SATISFIED")
        self.assertEqual(parse_review(json.dumps(full))["verdict"], "APPROVED")  # not about the code: no downgrade
        self.assertEqual(parse_review(json.dumps(review(issues=[issue("bug", severity="high")])))["verdict"], "CHANGES_REQUESTED")
        for bad in ({**base, "criteria": "all"}, {**base, "criteria": ["AC-01"]}, {**base, "criteria": [{"id": "AC-01"}]},
                    {**base, "criteria": [{"id": "AC-01", "outcome": "FINE"}]}, {**base, "criteria": [{"id": 1, "outcome": "SATISFIED"}]},
                    {**base, "criteria": [{"id": "AC-01", "outcome": "SATISFIED", "note": 3}]},
                    {**base, "criteria": [{"id": "AC-01", "outcome": ["SATISFIED"]}]},
                    {**base, "issues": [{"severity": "low", "path": "", "description": "d", "kind": "OPINION"}]},
                    {**base, "issues": [{"severity": "low", "path": "", "description": "d", "kind": ["CODE_CHANGES_REQUIRED"]}]}):
            with self.subTest(bad=bad), self.assertRaises(AgentFailure) as caught:
                parse_review(json.dumps(bad))
            self.assertEqual(caught.exception.kind, "invalid_review")

    def test_the_schema_given_to_the_clis_names_every_outcome(self):
        schema = acceptance.REVIEW_SCHEMA
        self.assertEqual(schema["required"], ["verdict", "summary", "issues", "criteria"])
        self.assertEqual(schema["properties"]["criteria"]["items"]["properties"]["outcome"]["enum"], list(acceptance.ReviewOutcome))
        self.assertEqual(schema["properties"]["issues"]["items"]["properties"]["kind"]["enum"], [
            "CODE_CHANGES_REQUIRED", "VALIDATION_REQUIRED", "CAPABILITY_BLOCKED", "APPROVAL_REQUIRED", "MANUAL_ACTION_REQUIRED",
            "OBSERVATION"])
        json.dumps(schema)

    def test_an_approval_that_names_a_code_change_is_not_one(self):
        settled = self.record(GOOD, MO2).settle_review(review("APPROVED", AC01="CODE_CHANGES_REQUIRED", AC02="SATISFIED"), 1)
        self.assertEqual((settled["verdict"], settled["reported_verdict"], settled["normalized"]),
                         ("CHANGES_REQUESTED", "APPROVED", "code_change_named"))

    def test_a_request_for_changes_that_names_none_asks_nothing_of_the_developer(self):
        record = self.record(GOOD, MO2)
        settled = record.settle_review(review("CHANGES_REQUESTED", AC01="SATISFIED", AC02=("MANUAL_ACTION_REQUIRED", "needs the game")), 1)
        self.assertEqual((settled["verdict"], settled["reported_verdict"], settled["normalized"]),
                         ("APPROVED", "CHANGES_REQUESTED", "no_code_change_requested"))
        self.assertEqual(record.get("AC-02").review, {"outcome": "MANUAL_ACTION_REQUIRED", "note": "needs the game", "iteration": 1})
        # Anything that is the developer's keeps the request as it was given.
        for answer in (review("CHANGES_REQUESTED", [issue("bug")], AC01="SATISFIED", AC02="MANUAL_ACTION_REQUIRED"),
                       review("CHANGES_REQUESTED", AC01="CODE_CHANGES_REQUIRED", AC02="MANUAL_ACTION_REQUIRED"),
                       review("CHANGES_REQUESTED", AC02="MANUAL_ACTION_REQUIRED"),  # the code criterion was not judged
                       review("CHANGES_REQUESTED", AC01="SATISFIED", AC02="SATISFIED"),  # nothing was handed over
                       review("CHANGES_REQUESTED"),
                       review("BLOCKED", AC01="SATISFIED", AC02="MANUAL_ACTION_REQUIRED")):
            with self.subTest(answer=answer):
                settled = self.record(GOOD, MO2).settle_review(answer, 1)
                self.assertEqual((settled["verdict"], settled.get("normalized")), (answer["verdict"], None))
        only = review("CHANGES_REQUESTED", [issue("needs the game", "MANUAL_ACTION_REQUIRED", "high")], AC01="SATISFIED",
                      AC02="VALIDATION_REQUIRED")
        self.assertEqual(self.record(GOOD, MO2).settle_review(only, 1)["verdict"], "APPROVED")

    def test_outcomes_for_unknown_or_repeated_criteria_are_recorded_and_never_applied(self):
        record = self.record(GOOD)
        answer = review(AC01="SATISFIED", AC07="SATISFIED")
        answer["criteria"].append({"id": "AC-01", "outcome": "CODE_CHANGES_REQUIRED", "note": "again"})
        settled = record.settle_review(answer, 1)
        self.assertEqual(([entry["id"] for entry in settled["criteria"]], settled["ignored_criteria"], settled["verdict"]),
                         (["AC-01"], 2, "APPROVED"))
        self.assertEqual(record.get("AC-01").review["outcome"], "SATISFIED")

    def test_a_change_to_the_code_counts_only_for_a_criterion_a_change_to_the_files_can_satisfy(self):
        """A change asked for a check by a person or for the project's commands is recorded and sends nothing back."""
        for verdict, normalized in (("APPROVED", None), ("CHANGES_REQUESTED", "no_code_change_requested")):
            with self.subTest(verdict=verdict):
                record = self.record(GOOD, MO2_IT, "The test suite passes")
                answer = review(verdict, AC01="SATISFIED", AC02=("CODE_CHANGES_REQUIRED", "Va provata in Mod Organizer 2"),
                                AC03="CODE_CHANGES_REQUIRED")
                settled = record.settle_review(answer, 1)
                self.assertEqual((settled["verdict"], settled.get("normalized"), settled["refused_changes"]),
                                 ("APPROVED", normalized, ["AC-02", "AC-03"]))
                record.refresh(evidence.Ledger())
                external = record.get("AC-02")
                self.assertEqual((external.review["outcome"], external.executor, external.status),
                                 ("CODE_CHANGES_REQUIRED", "user", Status.AWAITING_MANUAL_VALIDATION))
                shown, apart = acceptance.feedback_parts(record, settled)
                self.assertEqual([entry["id"] for entry in shown["criteria"]], ["AC-01"])
                self.assertEqual([line.split()[1] for line in apart], ["AC-02", "AC-03"])
        self.assertNotIn("refused_changes", self.record(GOOD).settle_review(review(AC01="SATISFIED"), 1))
        # A change to the code still goes back for the criterion the code can satisfy, and only for it.
        record = self.record(GOOD, MO2_IT)
        settled = record.settle_review(review(AC01="CODE_CHANGES_REQUIRED", AC02="CODE_CHANGES_REQUIRED"), 1)
        self.assertEqual((settled["verdict"], settled["normalized"], settled["refused_changes"]),
                         ("CHANGES_REQUESTED", "code_change_named", ["AC-02"]))
        self.assertIn("for AC-01. ", settled["summary"])
        record.refresh(evidence.Ledger())
        shown, _ = acceptance.feedback_parts(record, settled)
        self.assertEqual([entry["id"] for entry in shown["criteria"]], ["AC-01"])
        # A criterion about the code that was handed over is still the developer's when the code has to change for it.
        record = self.record(GOOD, MO2_IT)
        record.settle_review(review(AC01="VALIDATION_REQUIRED", AC02="SATISFIED"), 1)
        record.refresh(evidence.Ledger())
        self.assertEqual(record.get("AC-01").executor, "user")
        settled = record.settle_review(review("CHANGES_REQUESTED", AC01="CODE_CHANGES_REQUIRED"), 2)
        self.assertEqual((settled["verdict"], "refused_changes" in settled), ("CHANGES_REQUESTED", False))
        record.refresh(evidence.Ledger())
        self.assertEqual((record.get("AC-01").executor, record.get("AC-01").status), ("developer", Status.FAILED))
        self.assertIn("Only for a criterion a change to the files can satisfy", record.reviewer_brief(evidence.Ledger()))

    def test_a_reviewer_can_only_make_a_criterion_ask_for_more(self):
        record = self.record(GOOD, MO2)
        record.settle_review(review(AC01=("MANUAL_ACTION_REQUIRED", "only visible on screen"), AC02="SATISFIED"), 1)
        record.refresh(evidence.Ledger())
        handed, external = record.get("AC-01"), record.get("AC-02")
        self.assertEqual((handed.proof, handed.verifier, handed.status), (["manual"], "user", Status.AWAITING_MANUAL_VALIDATION))
        self.assertIn("only visible on screen", handed.reason)
        # Its word about a criterion that is not its to verify is kept, and proves nothing.
        self.assertEqual((external.review["outcome"], external.status, external.evidence), ("SATISFIED", Status.AWAITING_MANUAL_VALIDATION, []))
        for outcome, status, required in (("CAPABILITY_BLOCKED", Status.BLOCKED_CAPABILITY, False),
                                          ("APPROVAL_REQUIRED", Status.AWAITING_AUTHORIZATION, True),
                                          ("NOT_ASSESSED", Status.AWAITING_MANUAL_VALIDATION, False),
                                          ("VALIDATION_REQUIRED", Status.AWAITING_MANUAL_VALIDATION, False)):
            with self.subTest(outcome=outcome):
                record = self.record(GOOD)
                record.settle_review(review(AC01=outcome), 1)
                record.refresh(evidence.Ledger())
                self.assertEqual((record.get("AC-01").status, record.get("AC-01").authorization_required), (status, required))
        # A later review that judges the code itself puts the criterion back where the rules had it.
        record.settle_review(review(AC01="SATISFIED"), 2)
        record.refresh(evidence.Ledger())
        self.assertEqual((record.get("AC-01").proof, record.get("AC-01").verifier), (["review"], "reviewer"))

    def test_the_kind_of_an_issue_decides_over_its_severity(self):
        """A change the code requires turns an approval around at any severity; an observation never does."""
        for issues, verdict in (([issue("rename the helper", severity="low")], "CHANGES_REQUESTED"),
                                ([issue("a docstring would help", "OBSERVATION", "critical")], "APPROVED"),
                                ([issue("needs the game", "MANUAL_ACTION_REQUIRED", "critical")], "APPROVED"),
                                ([{"severity": "low", "path": "", "description": "nit"}], "APPROVED"),  # no kind: severity decides
                                ([{"severity": "high", "path": "", "description": "bug"}], "CHANGES_REQUESTED")):
            with self.subTest(issues=issues):
                parsed = parse_review(json.dumps(review(issues=issues, AC01="SATISFIED")))
                self.assertEqual(parsed["verdict"], verdict)
                if verdict == "CHANGES_REQUESTED":
                    self.assertEqual((parsed["reported_verdict"], parsed["normalized"]), ("APPROVED", "code_change_named"))
        # An observation is nobody's work: the developer is not sent it, not even as something to leave alone.
        record = self.record(GOOD)
        answer = review("CHANGES_REQUESTED", [issue("wrong content"), issue("a clearer name would help", "OBSERVATION", "low")],
                        AC01="CODE_CHANGES_REQUIRED")
        record.settle_review(answer, 1)
        record.refresh(evidence.Ledger())
        shown, apart = acceptance.feedback_parts(record, answer)
        self.assertEqual(([entry["description"] for entry in shown["issues"]], apart), (["wrong content"], []))
        self.assertIn("Use OBSERVATION for a remark about the code that asks for no change", record.reviewer_brief(evidence.Ledger()))

    def test_an_approval_gives_each_criterion_of_the_reviewer_an_outcome_of_its_own(self):
        record = self.record(GOOD, "README.md mentions the feature", MO2, "The test suite passes")
        self.assertEqual((record.source, record.for_reviewer()), ("task", ["AC-01", "AC-02"]))
        for answer, missing in ((review(), "AC-01, AC-02"), (review(AC01="SATISFIED", AC03="SATISFIED", AC04="SATISFIED"), "AC-02")):
            with self.subTest(answer=answer):
                with self.assertRaises(acceptance.IncompleteReview) as caught:
                    record.settle_review(answer, 1)
                self.assertIn(f"gives no outcome to {missing}.", str(caught.exception))
                self.assertEqual([criterion.review for criterion in record.criteria], [None] * 4)  # nothing of it was kept
        self.assertIn("An APPROVED verdict that leaves out a criterion you verify is not accepted", record.reviewer_brief(evidence.Ledger()))
        # What the reviewer does not verify needs no outcome, and an answer that approves nothing is not held to it.
        self.assertEqual(record.settle_review(review(AC01="SATISFIED", AC02="SATISFIED"), 1)["verdict"], "APPROVED")
        for answer in (review("CHANGES_REQUESTED"), review("BLOCKED"), review(AC01="CODE_CHANGES_REQUIRED")):
            with self.subTest(answer=answer):
                settled = self.record(GOOD, "README.md mentions the feature").settle_review(answer, 1)
                self.assertNotEqual(settled["verdict"], "APPROVED")

    def test_only_a_task_read_back_from_its_task_md_lets_an_approval_cover_what_it_left_out(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence.append(Path(folder), "review", executor="reviewer", result="approved", verdict="APPROVED", criteria=[],
                            outcomes={}, scope="code")
            ledger = evidence.read(Path(folder))
        for source, status in (("task", Status.PENDING), ("task.md", Status.VERIFIED)):
            with self.subTest(source=source):
                record = Acceptance.create(acceptance.parse_items([GOOD]), source=source)
                record.refresh(ledger)
                self.assertEqual((record.explicit_outcomes, record.get("AC-01").status), (source == "task", status))
                record.approve_code(ledger.records[0]["id"], 1)
                record.refresh(ledger)  # an approval saved without the outcome stops approving the code only where one is needed
                self.assertEqual(record.code["status"], "PENDING" if source == "task" else "APPROVED")
        self.assertEqual(Acceptance.create([], source="task.md").settle_review(review(), 1)["verdict"], "APPROVED")


class EngineTests(Case):
    def test_a_python_task_is_done_when_every_criterion_has_its_evidence(self):
        """A: criteria implemented and verified, approval possible with valid evidence."""
        task_id, path, _ = self.task(GOOD, "The test suite passes")
        agents = Agents([review(AC01="SATISFIED", AC02="SATISFIED")])
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], state["phase"], agents.roles), ("done", "complete", ["developer", "reviewer"]))
        record, ledger = self.record(path), evidence.read(path)
        self.assertEqual((record.code["status"], record.outcome, record.results()), ("APPROVED", "RELEASE_READY", ["CODE_APPROVED", "RELEASE_READY"]))
        self.assertEqual(self.statuses(path), {"AC-01": "VERIFIED", "AC-02": "VERIFIED"})
        kinds = {criterion.id: [ledger.get(name)["kind"] for name in criterion.evidence] for criterion in record.criteria}
        self.assertEqual(kinds, {"AC-01": ["review"], "AC-02": ["command"]})  # each by who can verify it
        command = ledger.get(record.get("AC-02").evidence[0])
        self.assertEqual((command["executor"], command["result"], command["exit_code"], command["log"], command["of"]),
                         ("runtime", "passed", 0, "iteration-001/test-01.log", 1))
        self.assertEqual((command["fingerprint"], command["base"]), (state["test_context"]["fingerprint"], state["base_sha"]))
        self.assertEqual(len(command["log_sha256"]), 64)
        self.assertEqual(self.events(path), ["run_started", "development_started", "development_completed", "tests_started",
                                             "tests_completed", "review_started", "review_completed", "task_completed"])
        report = (path / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Acceptance criteria", report)
        self.assertIn("`AC-02` **VERIFIED**", report)

    def test_an_approval_that_leaves_a_criterion_without_its_outcome_is_asked_for_again(self):
        task_id, path, _ = self.task(GOOD, "README.md mentions the feature", "The test suite passes")
        agents = Agents([{"verdict": "APPROVED", "summary": "Checked the worktree", "issues": []},  # an answer from before outcomes
                         review(AC01="SATISFIED", AC03="SATISFIED")])
        for missing in ("AC-01, AC-02", "AC-02"):
            state = run_task(self.home, task_id, adapter=agents)
            self.assertEqual((state["status"], state["phase"], state["last_error"]["kind"]), ("paused", "review", "invalid_review"))
            self.assertIn(f"gives no outcome to {missing}.", state["last_error"]["message"])
            self.assertEqual(self.statuses(path), {"AC-01": "PENDING", "AC-02": "PENDING", "AC-03": "VERIFIED"})
            self.assertEqual((self.kinds(path), self.record(path).code["status"]), (["command"], "PENDING"))  # nothing of it counts
        state = run_task(self.home, task_id, adapter=agents)  # a Resume asks for the review again, and only for it
        self.assertEqual((state["status"], agents.roles), ("done", ["developer", "reviewer", "reviewer", "reviewer"]))
        self.assertEqual(self.statuses(path), {"AC-01": "VERIFIED", "AC-02": "VERIFIED", "AC-03": "VERIFIED"})

    def test_an_approval_saved_without_an_outcome_for_each_criterion_has_the_code_reviewed_again(self):
        task_id, path, _ = self.task(GOOD, MO2)
        with patch.object(Acceptance, "explicit_outcomes", False):  # as every task was before each criterion needed its own
            state = run_task(self.home, task_id, adapter=Agents([{"verdict": "APPROVED", "summary": "Checked", "issues": []}]))
        self.assertEqual((state["phase"], self.record(path).code["status"], self.statuses(path)["AC-01"]), ("validate", "APPROVED", "VERIFIED"))
        recheck_acceptance(self.home, task_id)
        record = self.record(path)
        self.assertEqual((record.code["status"], self.statuses(path)["AC-01"]), ("PENDING", "PENDING"))
        self.assertIn("gave no outcome of its own to AC-01", record.code["reason"])
        agents = Agents()
        state = run_task(self.home, task_id, adapter=agents)  # nothing is developed again: the code is reviewed again
        self.assertEqual((state["phase"], agents.roles, self.record(path).code["status"]), ("validate", ["reviewer"], "APPROVED"))
        self.assertEqual(self.statuses(path), {"AC-01": "VERIFIED", "AC-02": "AWAITING_MANUAL_VALIDATION"})

    def test_the_word_of_the_developer_verifies_nothing(self):
        self.configure(max_iterations=1)
        task_id, path, _ = self.task(GOOD, MO2, "Create the release ZIP archive")
        state = run_task(self.home, task_id, adapter=Agents([review("CHANGES_REQUESTED", [issue("wrong content")])], writes=["bad"]))
        self.assertIn("all acceptance criteria are verified", (path / "handoff.md").read_text(encoding="utf-8"))
        self.assertEqual(self.statuses(path), {"AC-01": "PENDING", "AC-02": "AWAITING_MANUAL_VALIDATION", "AC-03": "BLOCKED_CAPABILITY"})
        self.assertEqual((state["status"], self.record(path).results()), ("blocked", []))
        self.assertEqual([entry["kind"] for entry in evidence.read(path).records], ["command", "review"])

    def test_a_missing_compiler_blocks_the_build_criterion_and_nothing_is_approved_in_its_place(self):
        """B: the BUILD criterion is blocked by a missing capability; no false approval."""
        task_id, path, _ = self.task(GOOD, "Compile the C++ component")
        agents = Agents([review(AC01="SATISFIED", AC02="SATISFIED")])  # the reviewer says the build is fine: it cannot know
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], state["phase"], state["last_error"]["kind"], state["last_error"]["source"]),
                         ("paused", "validate", "awaiting_validation", "acceptance"))
        build = self.record(path).get("AC-02")
        self.assertEqual((build.status, build.feasibility, build.evidence, build.review["outcome"]),
                         ("BLOCKED_CAPABILITY", "needs_tools", [], "SATISFIED"))
        self.assertIn("a C or C++ compiler", build.reason)
        self.assertEqual([(item["capability"], item["state"]) for item in build.requires],
                         [("commands.build", "absent"), ("tool.cpp", "absent")])
        self.assertEqual(self.record(path).results(), ["CODE_APPROVED", "AWAITING_VALIDATION"])
        # With a compiler installed the criterion is still not verified: no command of the project runs a build.
        self.which.side_effect = lambda name: "/usr/bin/g++" if name == "g++" else None
        recheck_acceptance(self.home, task_id)
        build = self.record(path).get("AC-02")
        self.assertEqual((build.status, build.requires[1]["state"]), ("BLOCKED_CAPABILITY", "verified"))
        self.assertIn("No command you allowed for this project runs a build", build.reason)
        self.assertEqual(agents.roles, ["developer", "reviewer"])

    def test_a_build_command_the_project_allowed_verifies_the_build_without_a_new_review(self):
        task_id, path, _ = self.task(GOOD, "Compile the project")
        agents = Agents()
        self.assertEqual(run_task(self.home, task_id, adapter=agents)["phase"], "validate")
        self.configure(commands=[self.check, [sys.executable, "-m", "compileall", "-q", "."]])
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], agents.roles), ("done", ["developer", "reviewer"]))  # the review of these files stood
        build = self.record(path).get("AC-02")
        ledger = evidence.read(path)
        self.assertEqual((build.status, build.verifier, [ledger.get(name)["covers"] for name in build.evidence]),
                         ("VERIFIED", "runtime", [["tests", "build"]]))
        self.assertEqual(self.events(path).count("review_started"), 1)

    def test_a_build_command_that_fails_takes_the_approval_back(self):
        task_id, path, _ = self.task(GOOD, "Compile the project")
        agents = Agents()
        run_task(self.home, task_id, adapter=agents)
        broken = Path(self.tmp.name) / "broken.py"  # outside the task's files: they stay as the review saw them
        broken.write_text("def broken(:\n", encoding="utf-8")
        self.configure(commands=[self.check, [sys.executable, "-m", "compileall", "-q", str(broken)]], max_iterations=1)
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], state["last_error"], self.record(path).code["status"]),
                         ("blocked", "max_iterations_reached", "CHANGES_REQUIRED"))
        self.assertEqual((self.record(path).get("AC-02").status, agents.roles), ("FAILED", ["developer", "reviewer"]))
        self.assertIn("tests_missing_or_failed", (path / "feedback.md").read_text(encoding="utf-8"))

    def test_a_developer_without_a_shell_edits_and_patchrondo_runs_the_tests(self):
        """C: the developer has file tools only; PatchRondo runs the tests itself."""
        task_id, path, _ = self.task(GOOD, "pytest passes")
        agents = Agents()
        state = run_task(self.home, task_id, adapter=agents)
        found = self.record(path).capabilities["executors"]
        self.assertEqual((found["developer"]["capabilities"]["shell"]["state"], found["runtime"]["capabilities"]["commands.tests"]["state"]),
                         ("absent", "verified"))
        tests = self.record(path).get("AC-02")
        self.assertEqual((tests.executor, tests.verifier, tests.status, state["status"]), ("runtime", "runtime", "VERIFIED", "done"))
        self.assertEqual({evidence.read(path).get(name)["executor"] for name in tests.evidence}, {"runtime"})
        prompt = agents.prompts[0]
        mine, others = prompt.split("Not yours.")
        self.assertIn("AC-01 [required] feature.txt contains good", mine.split("Yours to satisfy")[1])
        self.assertIn("AC-02 [required] pytest passes — the orchestrator checks it with the project's own commands", others)

    def test_a_check_only_a_person_can_do_waits_for_it_instead_of_looping(self):
        """D: the criterion waits for validation, without repeated requests for changes about the same impediment."""
        task_id, path, _ = self.task(GOOD, MO2)
        asked = review("CHANGES_REQUESTED", AC01="SATISFIED", AC02=("MANUAL_ACTION_REQUIRED", "Needs Mod Organizer 2"))
        agents = Agents([asked] * 6)
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], state["phase"], state["iteration"], agents.roles), ("paused", "validate", 1, ["developer", "reviewer"]))
        self.assertEqual((state["review"]["verdict"], state["review"]["reported_verdict"]), ("APPROVED", "CHANGES_REQUESTED"))
        self.assertEqual(self.statuses(path), {"AC-01": "VERIFIED", "AC-02": "AWAITING_MANUAL_VALIDATION"})
        self.assertEqual(self.events(path)[-3:], ["review_completed", "code_approved", "task_paused"])
        self.assertIn("AC-02", state["last_error"]["message"])
        # Resuming changes nothing and calls nobody: not an event, not a provider.
        for _ in range(3):
            again = run_task(self.home, task_id, adapter=agents)
            self.assertEqual((again["history"], again["last_error"], agents.roles), (state["history"], state["last_error"], ["developer", "reviewer"]))
        self.assertFalse((path / "feedback.md").exists())

    def test_a_change_asked_for_a_check_only_a_person_can_do_does_not_go_back_to_the_developer(self):
        """The reviewer marks the user's criterion CODE_CHANGES_REQUIRED: no new iteration, the criterion waits for the user."""
        task_id, path, _ = self.task(GOOD, MO2_IT)
        asked = review("CHANGES_REQUESTED", AC01="SATISFIED", AC02=("CODE_CHANGES_REQUIRED", "Va provata in Mod Organizer 2"))
        agents = Agents([asked] * 6)
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], state["phase"], state["iteration"], agents.roles),
                         ("paused", "validate", 1, ["developer", "reviewer"]))
        self.assertEqual((state["review"]["verdict"], state["review"]["reported_verdict"], state["review"]["refused_changes"]),
                         ("APPROVED", "CHANGES_REQUESTED", ["AC-02"]))
        self.assertEqual(self.statuses(path), {"AC-01": "VERIFIED", "AC-02": "AWAITING_MANUAL_VALIDATION"})
        self.assertFalse((path / "feedback.md").exists())
        self.assertIn("A change to the code asked for AC-02, which no change to the files can satisfy, is recorded",
                      (path / "report.md").read_text(encoding="utf-8"))
        again = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((again["history"], agents.roles), (state["history"], ["developer", "reviewer"]))

    def test_the_user_attaches_the_evidence_and_the_task_completes_without_an_agent(self):
        task_id, path, _ = self.task(GOOD, MO2)
        agents = Agents()
        run_task(self.home, task_id, adapter=agents)
        for arguments, error in (({"summary": "ok"}, "Describe the check"), ({"summary": "Loaded the mod and played"}, "statement alone"),
                                 ({"summary": "Loaded the mod and played", "reference": "  "}, "statement alone"),
                                 ({"summary": "Loaded the mod and played", "reference": 3}, "must be text"),
                                 ({"summary": "Loaded the mod and played", "reference": "log", "passed": "yes"}, "boolean"),
                                 ({"summary": "Loaded the mod and played", "artifact": "no-such-file.zip"}, "not a regular file")):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, error):
                attach_evidence(self.home, task_id, "AC-02", **arguments)
        with self.assertRaisesRegex(AcceptanceRefused, "already verified"):
            attach_evidence(self.home, task_id, "AC-01", summary="I looked at it myself", reference="trust me")
        with self.assertRaises(FileNotFoundError):
            attach_evidence(self.home, task_id, "AC-09", summary="Loaded the mod and played", reference="log")
        self.assertEqual(self.kinds(path), ["command", "review"])
        done = attach_evidence(self.home, task_id, "AC-02", summary="Loaded the mod in MO2 2.5 and played ten minutes",
                               reference="https://example.invalid/session-log")
        self.assertEqual((done["record"]["kind"], done["record"]["executor"], done["record"]["result"], done["completes"]),
                         ("manual", "user", "passed", True))
        self.assertEqual(done["acceptance"]["outcome"], "RELEASE_READY")
        saved = self.state(path)
        self.assertEqual((saved["status"], saved["phase"]), ("paused", "validate"))  # only a run finishes a task
        self.assertIn("Resume to complete", saved["last_error"]["message"])
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], state["phase"], agents.roles), ("done", "complete", ["developer", "reviewer"]))
        self.assertEqual(self.events(path)[-3:], ["evidence_recorded", "run_started", "task_completed"])
        with self.assertRaisesRegex(AcceptanceRefused, "Task is done"):
            attach_evidence(self.home, task_id, "AC-02", summary="Loaded the mod and played", reference="log")

    def test_a_criterion_the_review_or_the_tests_verify_takes_no_statement(self):
        self.configure(commands=[self.check], enabled=False)
        task_id, path, _ = self.task(GOOD, "The test suite passes")
        run_task(self.home, task_id, adapter=Agents())
        for criterion, who in (("AC-01", "the review of the code"), ("AC-02", "the project's own commands")):
            with self.subTest(criterion=criterion), self.assertRaisesRegex(AcceptanceRefused, who):
                attach_evidence(self.home, task_id, criterion, summary="I am sure that it works", reference="my word")
        self.assertEqual(self.kinds(path), [])

    def test_approved_code_with_packaging_missing_is_never_release_ready(self):
        """E: CODE_APPROVED and AWAITING_VALIDATION; never RELEASE_READY."""
        task_id, path, _ = self.task(GOOD, "Create the release ZIP archive")
        state = run_task(self.home, task_id, adapter=Agents([review(AC01="SATISFIED", AC02="SATISFIED")]))
        record = self.record(path)
        self.assertEqual((record.results(), state["status"], state["phase"]), (["CODE_APPROVED", "AWAITING_VALIDATION"], "paused", "validate"))
        self.assertEqual((record.get("AC-02").status, record.get("AC-02").missing), ("BLOCKED_CAPABILITY", ["package"]))
        self.assertNotIn("task_completed", self.events(path))
        self.assertEqual(self.state(path)["acceptance"]["summary"]["missing"][0]["capability"], "commands.package")
        # The archive made elsewhere counts when it is named: PatchRondo hashes the file itself.
        archive = Path(self.tmp.name) / "release.zip"
        archive.write_bytes(b"PK\x03\x04 release")
        done = attach_evidence(self.home, task_id, "AC-02", summary="Built the archive with the release script", artifact=str(archive))
        self.assertEqual((done["record"]["artifacts"][0]["size"], done["completes"]), (len(b"PK\x03\x04 release"), True))
        self.assertEqual(self.record(path).results(), ["CODE_APPROVED", "RELEASE_READY"])

    def test_an_optional_criterion_does_not_hold_the_task(self):
        task_id, path, _ = self.task(GOOD, f"[optional] {MO2}")
        state = run_task(self.home, task_id, adapter=Agents())
        self.assertEqual((state["status"], self.statuses(path)), ("done", {"AC-01": "VERIFIED", "AC-02": "AWAITING_MANUAL_VALIDATION"}))

    def test_criteria_and_evidence_survive_a_restart(self):
        """F: state and evidence are kept after a restart."""
        task_id, path, _ = self.task(GOOD, MO2, "Checked by hand on a phone")
        run_task(self.home, task_id, adapter=Agents())
        attach_evidence(self.home, task_id, "AC-02", summary="Loaded the mod and played", reference="session.log")
        before, ledger = self.state(path), (path / evidence.LEDGER).read_bytes()
        self.assertEqual(self.statuses(path), {"AC-01": "VERIFIED", "AC-02": "VERIFIED", "AC-03": "AWAITING_MANUAL_VALIDATION"})
        # Another process, later: it has the files and nothing else.
        code = ("import sys, json; from pathlib import Path; from patchrondo.core import run_task; "
                "print(json.dumps(run_task(Path(sys.argv[1]), sys.argv[2], adapter=object())['acceptance']))")
        done = subprocess.run([sys.executable, "-c", code, str(self.home), task_id], capture_output=True, text=True,
                              env={**__import__("os").environ, "PYTHONPATH": str(Path(acceptance.__file__).parents[1])})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), before["acceptance"])
        self.assertEqual(((path / evidence.LEDGER).read_bytes(), self.state(path)), (ledger, before))
        record = self.record(path)
        self.assertEqual([len(criterion.evidence) for criterion in record.criteria], [1, 1, 0])
        self.assertEqual(evidence.read(path).problems, [])
        # Read back and written again, the record is the one saved, and each reference names a record of the ledger that counts.
        self.assertEqual(json.loads(json.dumps(record.dump())), before["acceptance"])
        records = evidence.read(path)
        self.assertTrue(all(records.counts(records.get(name)) for criterion in record.criteria for name in criterion.evidence))

    def test_evidence_about_files_that_changed_no_longer_counts(self):
        """G: a relevant file changes after a check passed, and the evidence is invalidated."""
        task_id, path, workspace = self.task(GOOD, MO2)
        agents = Agents()
        run_task(self.home, task_id, adapter=agents)
        attach_evidence(self.home, task_id, "AC-02", summary="Loaded the mod and played", reference="session.log")
        self.assertEqual(self.record(path).outcome, "RELEASE_READY")
        (workspace / "extra.txt").write_text("changed after the checks", encoding="utf-8")
        checked = recheck_acceptance(self.home, task_id)
        record, ledger = self.record(path), evidence.read(path)
        self.assertEqual((checked["completes"], record.code["status"], record.outcome), (False, "PENDING", "IN_PROGRESS"))
        self.assertEqual(self.statuses(path), {"AC-01": "PENDING", "AC-02": "AWAITING_MANUAL_VALIDATION"})
        self.assertEqual([record["kind"] for record in ledger.records], ["command", "review", "manual", "invalidation"])
        self.assertEqual((ledger.records[-1]["of"], [ledger.counts(record) for record in ledger.records[:3]]),
                         (["E-0001", "E-0002", "E-0003"], [False, False, False]))
        self.assertEqual(self.events(path)[-1], "evidence_invalidated")
        self.assertEqual(recheck_acceptance(self.home, task_id)["acceptance"]["verified"], 0)
        self.assertEqual(len(evidence.read(path).records), 4)  # said once, not at every look
        # The run goes back to what gives the missing evidence: the tests and a review of the files as they are.
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], state["phase"], agents.roles), ("paused", "validate", ["developer", "reviewer", "reviewer"]))
        self.assertEqual(self.statuses(path), {"AC-01": "VERIFIED", "AC-02": "AWAITING_MANUAL_VALIDATION"})

    def test_an_artifact_that_changed_invalidates_the_evidence_that_named_it(self):
        task_id, path, _ = self.task(GOOD, "Download the GGUF model")
        run_task(self.home, task_id, adapter=Agents())
        model = Path(self.tmp.name) / "model.gguf"
        model.write_bytes(b"GGUF first")
        decide_authorization(self.home, task_id, "AC-02", granted=True)
        entry = attach_evidence(self.home, task_id, "AC-02", summary="Downloaded from the model page", artifact=str(model))["record"]
        self.assertEqual((entry["scope"], self.statuses(path)["AC-02"]), ("independent", "VERIFIED"))
        model.write_bytes(b"GGUF another file")
        recheck_acceptance(self.home, task_id)
        self.assertEqual(self.statuses(path), {"AC-01": "VERIFIED", "AC-02": "AWAITING_MANUAL_VALIDATION"})  # the code is untouched
        self.assertEqual(evidence.read(path).records[-1]["of"], [entry["id"]])

    def test_a_denied_authorization_runs_nothing_and_stays_on_record(self):
        """H: no unauthorized execution, and a coherent state."""
        task_id, path, _ = self.task(GOOD, "Download the GGUF model", "Open a pull request")
        agents = Agents()
        with patch("patchrondo.core.execute", wraps=__import__("patchrondo.core", fromlist=["execute"]).execute) as commands:
            state = run_task(self.home, task_id, adapter=agents)
            self.assertEqual((state["status"], state["last_error"]["kind"], self.record(path).results()),
                             ("paused", "awaiting_authorization", ["CODE_APPROVED", "AWAITING_AUTHORIZATION"]))
            self.assertEqual(self.statuses(path), {"AC-01": "VERIFIED", "AC-02": "AWAITING_AUTHORIZATION", "AC-03": "AWAITING_AUTHORIZATION"})
            with self.assertRaisesRegex(AcceptanceRefused, "needs your authorization first"):
                attach_evidence(self.home, task_id, "AC-02", summary="Downloaded it anyway", reference="somewhere")
            denied = decide_authorization(self.home, task_id, "AC-02", granted=False)
            self.assertEqual((denied["record"]["kind"], denied["record"]["result"], denied["completes"]), ("authorization", "denied", False))
            download = self.record(path).get("AC-02")
            self.assertEqual((download.status, download.authorization["decision"], download.evidence), ("AWAITING_AUTHORIZATION", "denied", []))
            self.assertIn("You did not authorize", download.reason)
            with self.assertRaisesRegex(AcceptanceRefused, "needs your authorization first"):
                attach_evidence(self.home, task_id, "AC-02", summary="Downloaded it anyway", reference="somewhere")
            again = run_task(self.home, task_id, adapter=agents)
            # Only the project's one test command ever ran, once: nothing was downloaded, committed or pushed for anybody.
            self.assertEqual([call.args[0] for call in commands.call_args_list], [self.check])
        self.assertEqual((again["status"], again["phase"], agents.roles), ("paused", "validate", ["developer", "reviewer"]))
        self.assertEqual(again["history"][-1]["event"], "authorization_decided")
        self.assertEqual(self.record(path).outcome, "AWAITING_AUTHORIZATION")
        for bad in ("yes", 1, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                decide_authorization(self.home, task_id, "AC-03", granted=bad)
        with self.assertRaisesRegex(AcceptanceRefused, "not about an operation"):
            decide_authorization(self.home, task_id, "AC-01", granted=True)
        # A decision can change. Granting gives nobody a permission: the operation is the user's, and so is its proof.
        decide_authorization(self.home, task_id, "AC-02", granted=True)
        decide_authorization(self.home, task_id, "AC-03", granted=True)
        self.assertEqual(self.statuses(path), {"AC-01": "VERIFIED", "AC-02": "AWAITING_MANUAL_VALIDATION", "AC-03": "AWAITING_MANUAL_VALIDATION"})
        self.assertEqual(self.state(path)["last_error"]["kind"], "awaiting_validation")
        attach_evidence(self.home, task_id, "AC-02", summary="Downloaded from the model page", reference="sha256 in release notes")
        attach_evidence(self.home, task_id, "AC-03", summary="Opened the pull request", reference="https://example.invalid/pull/7")
        self.assertEqual(run_task(self.home, task_id, adapter=agents)["status"], "done")
        self.assertEqual(agents.roles, ["developer", "reviewer"])

    def test_a_decision_taken_back_takes_the_verification_back(self):
        task_id, path, _ = self.task(GOOD, "Open a pull request")
        run_task(self.home, task_id, adapter=Agents())
        decide_authorization(self.home, task_id, "AC-02", granted=True)
        attach_evidence(self.home, task_id, "AC-02", summary="Opened the pull request", reference="https://example.invalid/pull/7")
        self.assertEqual((self.statuses(path)["AC-02"], self.record(path).outcome), ("VERIFIED", "RELEASE_READY"))
        with self.assertRaisesRegex(AcceptanceRefused, "already verified"):
            decide_authorization(self.home, task_id, "AC-02", granted=False)
        # Evidence alone never stands for the decision: a check on record without it leaves the criterion waiting for it.
        other, second, _ = self.task(GOOD, "Open a pull request")
        state = run_task(self.home, other, adapter=Agents())
        evidence.append(second, "manual", executor="user", criteria=["AC-02"], result="passed", summary="Opened the pull request",
                        reference="link", fingerprint=state["test_context"]["fingerprint"], scope="code")
        checked = recheck_acceptance(self.home, other)
        self.assertEqual((self.statuses(second)["AC-02"], checked["acceptance"]["outcome"], checked["completes"]),
                         ("AWAITING_AUTHORIZATION", "AWAITING_AUTHORIZATION", False))

    def test_a_task_created_before_criteria_were_tracked_keeps_working(self):
        """I: no data lost, no crash, nothing approved in retrospect."""
        task_id, path, _ = self.task(GOOD, MO2)
        old = self.state(path)
        del old["acceptance"]
        save_json(path / "state.json", old)
        self.assertIsNone(Acceptance.load(self.state(path)))
        agents = Agents([{"verdict": "APPROVED", "summary": "Checked", "issues": []}])  # its reviewer answers as before outcomes
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], state["phase"], self.record(path).source), ("paused", "validate", "task.md"))
        self.assertEqual(self.statuses(path), {"AC-01": "VERIFIED", "AC-02": "AWAITING_MANUAL_VALIDATION"})
        planned = next(entry for entry in state["history"] if entry["event"] == "acceptance_planned")
        self.assertEqual((planned["source"], planned["criteria"]), ("task.md", 2))
        self.assertEqual({key: state[key] for key in old if key not in {"status", "phase", "updated_at", "history", "tests", "review",
                                                                        "last_error", "handoff"}},
                         {key: old[key] for key in old if key not in {"status", "phase", "updated_at", "history", "tests", "review",
                                                                      "last_error", "handoff"}})

    def test_an_old_task_that_ended_is_left_as_it_is(self):
        for status in ("done", "blocked"):
            with self.subTest(status=status):
                task_id, path, _ = self.task(GOOD, MO2)
                old = {**self.state(path), "status": status, "phase": "complete" if status == "done" else "review",
                       "review": {"verdict": "APPROVED", "summary": "fine", "issues": []},
                       "tests": [{"command": self.check, "status": "passed", "returncode": 0}]}
                del old["acceptance"]
                save_json(path / "state.json", old)
                agents = Agents()
                self.assertEqual(run_task(self.home, task_id, adapter=agents), old)
                self.assertEqual((self.state(path), agents.roles, (path / evidence.LEDGER).exists()), (old, [], False))
                shown = acceptance.view(None, evidence.read(path), pending=acceptance.from_task_text((path / "task.md").read_text()))
                self.assertEqual((shown["tracked"], shown["criteria"]), (False, [{"text": GOOD}, {"text": MO2}]))
                with self.assertRaises(AcceptanceRefused):
                    recheck_acceptance(self.home, task_id)

    def test_an_old_task_stopped_before_its_review_gets_fresh_evidence_first(self):
        task_id, path, _ = self.task(GOOD, "The test suite passes")

        class NoReviewer(Agents):
            def invoke(self, provider, role, prompt, workspace, run_dir):
                if role == "reviewer":
                    raise AgentFailure("rate limit", "quota")
                return super().invoke(provider, role, prompt, workspace, run_dir)

        run_task(self.home, task_id, adapter=NoReviewer())
        old = self.state(path)
        self.assertEqual((old["phase"], [item["status"] for item in old["tests"]]), ("review", ["passed"]))
        del old["acceptance"]
        save_json(path / "state.json", old)
        (path / evidence.LEDGER).unlink()
        agents = Agents()
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], agents.roles, self.events(path).count("tests_started")), ("done", ["reviewer"], 2))
        self.assertEqual(self.kinds(path), ["command", "review"])  # saved results were never turned into evidence
        self.assertEqual(self.statuses(path), {"AC-01": "VERIFIED", "AC-02": "VERIFIED"})

    def test_a_record_that_cannot_be_read_pauses_the_task_and_nothing_replaces_it(self):
        later = {"id": "AC-03", "text": "Signed off by the release manager", "required": True}  # only a later version knows it
        for change, reason in ((lambda block: block["criteria"][1].update(status="VERIFIED_BY_HAND"), "unknown category, need, proof or status"),
                               (lambda block: (block.update(version=2), block["criteria"].append(later)), "has schema version 2")):
            with self.subTest(reason=reason):
                task_id, path, _ = self.task(GOOD, MO2)
                run_task(self.home, task_id, adapter=Agents())
                state = self.state(path)
                saved = json.loads(json.dumps(state["acceptance"]))
                change(state["acceptance"])
                block = state["acceptance"]
                save_json(path / "state.json", state)
                ledger = (path / evidence.LEDGER).read_bytes()
                agents = Agents()
                paused = run_task(self.home, task_id, adapter=agents)
                error = paused["last_error"]
                self.assertEqual((paused["status"], paused["phase"], error["kind"], error["source"], agents.roles),
                                 ("paused", "validate", "acceptance_unreadable", "acceptance", []))
                self.assertIn(reason, error["message"])
                # The record the task runs by is left as it was, and so is the ledger: this version decides nothing with it.
                self.assertEqual((self.state(path)["acceptance"], (path / evidence.LEDGER).read_bytes()), (block, ledger))
                self.assertEqual(self.events(path)[-2:], ["run_started", "task_paused"])
                report = (path / "report.md").read_text(encoding="utf-8")
                self.assertIn("The saved criteria cannot be read", report)
                self.assertNotIn("**VERIFIED", report)
                # While it still cannot be read, a Resume returns the saved state (no event, no agent) and nothing judges it.
                self.assertEqual((run_task(self.home, task_id, adapter=agents), agents.roles), (paused, []))
                for action in (lambda: recheck_acceptance(self.home, task_id),
                               lambda: attach_evidence(self.home, task_id, "AC-02", summary="Loaded the mod and played",
                                                       reference="session.log")):
                    with self.assertRaisesRegex(AcceptanceRefused, "cannot be read by this version"):
                        action()
                self.assertEqual(self.state(path), paused)
                # Put back as it was, the record is read again and the task waits where it stopped.
                state = self.state(path)
                state["acceptance"] = saved
                save_json(path / "state.json", state)
                again = run_task(self.home, task_id, adapter=agents)
                self.assertEqual((again["status"], again["phase"], again["last_error"]["kind"], agents.roles),
                                 ("paused", "validate", "awaiting_validation", []))

    def test_what_shows_the_criteria_judges_them_against_the_ledger_as_it_is_now(self):
        """A VERIFIED that was saved and that the ledger no longer confirms is not shown as one; what was saved is kept apart."""
        task_id, path, _ = self.task(GOOD, MO2)
        run_task(self.home, task_id, adapter=Agents())
        manual = attach_evidence(self.home, task_id, "AC-02", summary="Loaded the mod and played", reference="session.log")["record"]
        shown = acceptance.view(self.record(path), evidence.read(path))
        self.assertEqual([(item["status"], item["saved_status"]) for item in shown["criteria"]], [("VERIFIED", "VERIFIED")] * 2)
        # The check by the user stops counting in the ledger, and nothing has judged the criteria again since.
        evidence.append(path, "invalidation", executor="runtime", result="invalidated", of=[manual["id"]], reason="Taken back")
        before = self.state(path)
        self.assertEqual((before["acceptance"]["outcome"], self.statuses(path)["AC-02"]), ("RELEASE_READY", "VERIFIED"))
        shown = acceptance.view(self.record(path), evidence.read(path))
        second = shown["criteria"][1]
        self.assertEqual((second["status"], second["saved_status"], second["evidence"]), ("AWAITING_MANUAL_VALIDATION", "VERIFIED", []))
        self.assertTrue(second["reason"].startswith("Saved as verified, but the evidence ledger does not confirm it now."))
        self.assertEqual((shown["outcome"], shown["summary"]["required_verified"], shown["saved"]["outcome"]),
                         ("AWAITING_VALIDATION", 1, "RELEASE_READY"))
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(main(["--home", str(self.home), "criteria", task_id]), 0)
        self.assertIn("Overall: AWAITING_VALIDATION · 1 of 2 required criteria verified", out.getvalue())
        self.assertIn("AC-02 · AWAITING_MANUAL_VALIDATION", out.getvalue())
        self.assertIn("(saved as VERIFIED)", out.getvalue())
        self.assertEqual(self.state(path), before)  # what shows them writes nothing

    def test_a_ledger_that_lost_what_the_state_saw_confirms_nothing_where_criteria_are_shown(self):
        """The record removed or changed may be the invalidation the last judgement counted: no record of that ledger counts."""
        for damage in ("removed", "edited"):
            with self.subTest(damage=damage):
                task_id, path, _ = self.task(GOOD, MO2)
                run_task(self.home, task_id, adapter=Agents())
                manual = attach_evidence(self.home, task_id, "AC-02", summary="Loaded the mod and played",
                                         reference="session.log")["record"]
                evidence.append(path, "invalidation", executor="runtime", result="invalidated", of=[manual["id"]],
                                reason="Taken back")
                recheck_acceptance(self.home, task_id)  # the invalidation is judged and saved with the state
                self.assertEqual((self.state(path)["acceptance"]["outcome"], self.statuses(path)),
                                 ("AWAITING_VALIDATION", {"AC-01": "VERIFIED", "AC-02": "AWAITING_MANUAL_VALIDATION"}))
                file = path / evidence.LEDGER
                data = file.read_bytes()
                # The invalidation is gone, or no longer fits the chain: either way E-0003 would count again.
                file.write_bytes(data[:data.rstrip(b"\n").rfind(b"\n") + 1] if damage == "removed"
                                 else data.replace(b'"reason":"Taken back"', b'"reason":"Kept"'))
                damaged, before = file.read_bytes(), self.state(path)
                self.assertEqual(evidence.read(path).problems == [], damage == "removed")  # what is left fits together
                shown = acceptance.view(self.record(path), evidence.read(path))
                self.assertEqual([(item["status"], item["saved_status"], item["evidence"]) for item in shown["criteria"]],
                                 [("PENDING", "VERIFIED", []), ("AWAITING_MANUAL_VALIDATION", "AWAITING_MANUAL_VALIDATION", [])])
                self.assertTrue(shown["criteria"][0]["reason"].startswith("Saved as verified, but the evidence ledger does not "
                                                                          "confirm it now."))
                self.assertEqual((shown["outcome"], shown["code"]["status"], shown["results"], shown["summary"]["verified"]),
                                 ("IN_PROGRESS", "PENDING", [], 0))
                self.assertEqual(len(shown["problems"]), 1)
                if damage == "removed":
                    self.assertEqual(shown["problems"], ["The evidence ledger no longer holds the records the task state had seen"])
                self.assertFalse(any(record["trusted"] or record["current"] for record in shown["evidence"]))
                with patch("sys.stdout", new_callable=io.StringIO) as out:
                    self.assertEqual(main(["--home", str(self.home), "criteria", task_id]), 0)
                self.assertIn("Code: PENDING · Overall: IN_PROGRESS · 0 of 2 required criteria verified", out.getvalue())
                self.assertIn(f"Warning: {shown['problems'][0]}", out.getvalue())
                self.assertEqual((file.read_bytes(), self.state(path)), (damaged, before))  # reading repairs nothing
                # The next judgement under the lock agrees: it puts the file aside, unchanged, and starts the ledger again.
                self.assertEqual(recheck_acceptance(self.home, task_id)["acceptance"]["outcome"], "IN_PROGRESS")
                self.assertEqual([kept.read_bytes() for kept in path.glob("evidence.damaged-*.jsonl")], [damaged])

    def test_a_real_bug_goes_to_the_developer_and_the_external_check_stays_apart(self):
        """J: the bug returns to the developer; the external verification stays distinct; neither is lost or mixed."""
        task_id, path, _ = self.task(GOOD, MO2)
        first = review("CHANGES_REQUESTED", [issue("feature.txt must contain good, not bad", severity="high"),
                                             issue("Nobody here can start Mod Organizer 2", "CAPABILITY_BLOCKED", "high", "")],
                       AC01=("CODE_CHANGES_REQUIRED", "wrong content"), AC02=("CAPABILITY_BLOCKED", "No Mod Organizer 2 here"))
        agents = Agents([first, review(AC01="SATISFIED", AC02=("CAPABILITY_BLOCKED", "No Mod Organizer 2 here"))], writes=["bad", "good"])
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual(agents.roles, ["developer", "reviewer", "developer", "reviewer"])
        feedback = (path / "feedback.md").read_text(encoding="utf-8")
        for_developer, apart = feedback.split("## Not work for the developer")
        self.assertIn("feature.txt must contain good, not bad", for_developer)
        self.assertNotIn("Mod Organizer", for_developer)
        self.assertIn("AC-02 Verified inside Mod Organizer 2 — BLOCKED_CAPABILITY", apart)
        self.assertIn("(CAPABILITY_BLOCKED) Nobody here can start Mod Organizer 2", apart)
        second = agents.prompts[2]
        self.assertIn("AC-01 [required] feature.txt contains good", second.split("Yours to satisfy")[1].split("Not yours.")[0])
        self.assertIn("AC-02 [required] Verified inside Mod Organizer 2 — it needs the user", second.split("Not yours.")[1])
        self.assertEqual((state["status"], state["phase"], state["iteration"]), ("paused", "validate", 2))
        external = self.record(path).get("AC-02")
        self.assertEqual((self.statuses(path)["AC-01"], external.status, external.required), ("VERIFIED", "BLOCKED_CAPABILITY", True))
        self.assertIn("No Mod Organizer 2 here", external.reason)
        kinds = [(entry["kind"], entry.get("result")) for entry in evidence.read(path).records]
        self.assertEqual(kinds, [("command", "failed"), ("review", "changes_requested"), ("invalidation", "invalidated"),
                                 ("command", "passed"), ("review", "approved")])

    def test_the_reviewer_is_given_the_criteria_the_capabilities_and_the_evidence(self):
        task_id, _, _ = self.task(GOOD, MO2, "The test suite passes")
        agents = Agents()
        run_task(self.home, task_id, adapter=agents)
        prompt = agents.prompts[1]
        listed = json.loads(prompt.split("## Acceptance criteria (tracked by the orchestrator)\n")[1].split("\n\n## Who can do what")[0])
        self.assertEqual([(row["id"], row["verified_by"], row["status"]) for row in listed],
                         [("AC-01", "reviewer", "PENDING"), ("AC-02", "user", "AWAITING_MANUAL_VALIDATION"), ("AC-03", "runtime", "VERIFIED")])
        self.assertIn("- developer: can files.read, files.write; cannot shell, network, git.commit, git.push", prompt)
        self.assertIn("command `", prompt.split("## Evidence recorded so far")[1].split("## How to judge")[0])
        self.assertIn('"criteria":[{"id":"AC-01", "outcome":"SATISFIED|CODE_CHANGES_REQUIRED|', prompt)
        self.assertIn("never a reason for CHANGES_REQUESTED", prompt)

    def test_a_check_by_the_user_that_fails_goes_back_to_the_developer_once(self):
        task_id, path, _ = self.task(GOOD, MO2)
        agents = Agents()
        run_task(self.home, task_id, adapter=agents)
        failed = attach_evidence(self.home, task_id, "AC-02", summary="The game crashes when the mod loads", passed=False)
        self.assertEqual((failed["record"]["result"], failed["completes"], self.statuses(path)["AC-02"]), ("failed", False, "FAILED"))
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual(agents.roles, ["developer", "reviewer", "developer", "reviewer"])
        self.assertIn("The game crashes when the mod loads", agents.prompts[2])
        self.assertEqual((state["phase"], state["iteration"], self.statuses(path)["AC-02"]), ("validate", 2, "AWAITING_MANUAL_VALIDATION"))
        events = self.events(path)
        self.assertEqual(events.count("validation_failed"), 1)
        self.assertEqual(run_task(self.home, task_id, adapter=agents)["history"], state["history"])  # not sent a second time

    def test_a_ledger_that_was_edited_proves_nothing_any_more(self):
        task_id, path, _ = self.task(GOOD, MO2)
        agents = Agents([review(AC01="SATISFIED", AC02=("MANUAL_ACTION_REQUIRED", "needs the game"))])
        run_task(self.home, task_id, adapter=agents)
        file = path / evidence.LEDGER
        forged = evidence.read(path).records[1] | {"outcomes": {"AC-01": "SATISFIED", "AC-02": "SATISFIED"}}
        lines = file.read_bytes().split(b"\n")
        lines[1] = json.dumps({key: value for key, value in forged.items() if key not in {"trusted", "invalidated"}}).encode()
        file.write_bytes(b"\n".join(lines))
        checked = recheck_acceptance(self.home, task_id)
        record, ledger = self.record(path), evidence.read(path)
        self.assertEqual((checked["acceptance"]["verified"], record.code["status"]), (0, "PENDING"))
        self.assertEqual(([entry["kind"] for entry in ledger.records], ledger.problems), (["restart"], []))
        self.assertEqual(len(list(path.glob("evidence.damaged-1-*.jsonl"))), 1)
        self.assertIn("evidence_ledger_restarted", self.events(path))
        state = run_task(self.home, task_id, adapter=agents)  # verified again from scratch
        self.assertEqual((state["phase"], agents.roles, self.statuses(path)["AC-01"]), ("validate", ["developer", "reviewer", "reviewer"], "VERIFIED"))
        self.assertEqual([entry["id"] for entry in evidence.read(path).records], ["E2-0001", "E2-0002", "E2-0003"])

    def test_actions_on_criteria_never_overlap_a_run(self):
        task_id, path, _ = self.task(GOOD, MO2)
        run_task(self.home, task_id, adapter=Agents())
        (path / ".run.lock").write_text("{}", encoding="utf-8")
        before = (self.state(path), (path / evidence.LEDGER).read_bytes())
        for action in (lambda: recheck_acceptance(self.home, task_id),
                       lambda: attach_evidence(self.home, task_id, "AC-02", summary="Loaded the mod and played", reference="log"),
                       lambda: decide_authorization(self.home, task_id, "AC-02", granted=True)):
            with self.assertRaisesRegex(RuntimeError, "already running"):
                action()
        self.assertEqual((self.state(path), (path / evidence.LEDGER).read_bytes()), before)

    def test_the_preflight_counts_what_a_task_would_need_without_creating_it(self):
        self.which.side_effect = lambda name: "/usr/bin/g++" if name == "g++" else None
        shown = preflight(self.home, developer="claude", reviewer="codex", acceptance=[
            GOOD, "The test suite passes", "Compile the C++ component", "Download the GGUF model", MO2, "Verificare licenze e regole Nexus"])
        self.assertEqual(shown["summary"]["feasibility"], {"automatable": 2, "needs_tools": 1, "needs_authorization": 1, "manual": 1, "unknown": 1})
        self.assertEqual([(item["executor"], item["capability"], item["criteria"]) for item in shown["summary"]["missing"]],
                         [("runtime", "commands.build", ["AC-03"])])
        self.assertEqual([criterion["status"] for criterion in shown["criteria"]], [
            "PENDING", "PENDING", "BLOCKED_CAPABILITY", "AWAITING_AUTHORIZATION", "AWAITING_MANUAL_VALIDATION", "UNKNOWN"])
        self.assertFalse(any((self.home / "tasks").iterdir()))
        self.configure(commands=[self.check], enabled=False)
        tests = preflight(self.home, developer="claude", reviewer="codex", acceptance=["The test suite passes"])["criteria"][0]
        self.assertEqual((tests["status"], tests["feasibility"]), ("AWAITING_AUTHORIZATION", "needs_authorization"))
        with self.assertRaises(ValueError):
            preflight(self.home, developer="gpt", reviewer="codex", acceptance=[])

    def test_the_task_starts_with_its_plan_and_a_task_that_cannot_finish_is_still_created(self):
        task_id, path, _ = self.task("Compile the C++ component", "Open a pull request")
        state = self.state(path)
        self.assertEqual((state["status"], state["history"]), ("ready", []))
        self.assertEqual(self.statuses(path), {"AC-01": "BLOCKED_CAPABILITY", "AC-02": "AWAITING_AUTHORIZATION"})
        self.assertIn("- Compile the C++ component\n- Open a pull request\n", (path / "task.md").read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            create_task(self.home, title="T", description="d", acceptance=[{"text": "a", "depends_on": ["AC-01"]}],
                        developer="claude", reviewer="codex")
        self.assertEqual(len(list((self.home / "tasks").iterdir())), 1)  # refused before anything was created

    def test_the_command_line_shows_and_records(self):
        task_id, path, _ = self.task(GOOD, "Download the GGUF model")
        run_task(self.home, task_id, adapter=Agents())

        def command(*arguments):
            with patch("sys.stdout", new_callable=io.StringIO) as out, patch("sys.stderr", new_callable=io.StringIO) as err:
                code = main(["--home", str(self.home), *arguments])
            return code, out.getvalue() + err.getvalue()

        code, shown = command("criteria", task_id)
        self.assertEqual(code, 0)
        self.assertIn("Code: APPROVED · Overall: AWAITING_AUTHORIZATION · 1 of 2 required criteria verified", shown)
        self.assertIn("AC-02 · AWAITING_AUTHORIZATION", shown)
        self.assertEqual(command("evidence", task_id, "AC-02", "--summary", "Downloaded from the page", "--reference", "x")[0], 1)
        code, shown = command("authorize", task_id, "AC-02", "--grant")
        self.assertIn("granted. Nothing was run.", shown)
        code, shown = command("evidence", task_id, "AC-02", "--summary", "Downloaded from the page", "--reference", "release notes")
        self.assertEqual(code, 0, shown)
        self.assertIn("Every required criterion is verified", shown)
        self.assertEqual(command("criteria", task_id, "--check")[0], 0)
        self.assertEqual(self.kinds(path), ["command", "review", "authorization", "manual"])


if __name__ == "__main__":
    unittest.main()
