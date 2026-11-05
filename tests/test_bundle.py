"""Evidence bundles (`bundle.py`): what an export holds, the check of a bundle, and the Verification Center's read model.

Workflow cases run the real loop (`core.run_task`) with the scripted developer
and reviewer of `test_acceptance`: no CLI is started and no model is called.
Bundles are built in memory and checked without extracting anything.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import unittest.mock
from unittest.mock import patch
import warnings
import zipfile

from patchrondo import acceptance as criteria, bundle, evidence, gate, gitops
from patchrondo.app import App, Conflict
from patchrondo.cli import main
from patchrondo.core import attach_evidence, recheck_acceptance, run_task
from patchrondo.storage import read_json, save_json

from test_acceptance import GOOD, MO2, Agents, Case

CLOCK = lambda: "2026-11-05T12:00:00+00:00"  # noqa: E731 - one creation time, so that two exports can be compared byte for byte


def entries(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def rezip(files: dict[str, bytes], *, raw_names=(), attrs=None) -> bytes:
    """A ZIP of `files` in their order. Names in `raw_names` are written as given, even twice."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in [*files.items(), *raw_names]:
            info = zipfile.ZipInfo(name, date_time=bundle.FIXED_TIME)
            info.filename = name  # as given: on Windows ZipInfo would turn a backslash into a slash
            info.compress_type = zipfile.ZIP_DEFLATED
            if attrs and name in attrs:
                info.external_attr = attrs[name]
            archive.writestr(info, data)
    return buffer.getvalue()


def resealed(files: dict[str, bytes], changed: dict[str, bytes]) -> bytes:
    """`files` with `changed` replaced, the manifest's hashes, sizes, digest and ID recomputed: a careful forgery."""
    files = {**files, **changed}
    manifest = json.loads(files[bundle.MANIFEST])
    for entry in manifest["files"]:
        if entry["path"] in changed:
            entry["sha256"], entry["size"] = hashlib.sha256(changed[entry["path"]]).hexdigest(), len(changed[entry["path"]])
    manifest["content_sha256"] = bundle.content_digest(manifest)
    manifest["bundle_id"] = f"EB-{manifest['content_sha256'][:16]}"
    files[bundle.MANIFEST] = bundle.canonical(manifest)
    return rezip(files)


def codes(found: dict) -> set[str]:
    return {entry["code"] for entry in found["problems"]}


def reledgered(files: dict[str, bytes], ledger: bytes, *, declare_complete: bool = True) -> bytes:
    """`files` with another ledger and every other file made to agree with it, hashes recomputed: a careful forgery.

    The manifest names the ledger's count, head and problems as `verify`
    computes them, the validity rows follow from it, the gate file says
    truthfully whether it is about it, and (with `declare_complete`) the
    manifest declares nothing left out.
    """
    parsed = evidence.parse(ledger)
    contract = json.loads(files["task/contract.json"])
    anchored = parsed.for_state(criteria.Acceptance.load({"acceptance": contract["acceptance"]}).ledger)
    rows = bundle.validity_rows(anchored)
    manifest = json.loads(files[bundle.MANIFEST])
    manifest["ledger"].update(count=anchored.count, head=anchored.head, problems=len(anchored.problems), anchored=anchored is parsed)
    manifest["validity"] = bundle.counts(rows)
    changed = {"evidence/ledger.jsonl": ledger, "evidence/validity.json": bundle.canonical({"anchored": anchored is parsed, "records": rows})}
    if "gate/evaluation.json" in files:
        gate_file = json.loads(files["gate/evaluation.json"])
        seen = gate_file["saved"]["ledger"]
        about = {"count": seen["count"], "head": seen["head"]} == {"count": parsed.count, "head": parsed.head}
        gate_file.update(about_exported_ledger=about, exported_ledger={"count": parsed.count, "head": parsed.head})
        manifest["gate"]["about_exported_ledger"] = about
        changed["gate/evaluation.json"] = bundle.canonical(gate_file)
    if declare_complete:
        manifest["omissions"], manifest["complete"] = [], True
    return resealed({**files, bundle.MANIFEST: bundle.canonical(manifest)}, changed)


def edited(ledger: bytes, index: int, old: bytes, new: bytes) -> bytes:
    """The ledger with one field of record `index` (from 0) changed, its hash left as it was."""
    lines = ledger.split(b"\n")
    assert old in lines[index], (index, old)
    lines[index] = lines[index].replace(old, new, 1)
    return b"\n".join(lines)


class Done(Case):
    """One task run to `done`, shared by the checks of a bundle: an export is read-only."""

    def runTest(self):  # noqa: N802 - unittest's name
        pass


class VerifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        case = Done()
        case.setUp()
        cls.addClassCleanup(case.doCleanups)
        task_id, cls.path, _ = case.task(GOOD)
        state = run_task(case.home, task_id, adapter=Agents())
        assert state["status"] == "done", state.get("last_error")
        cls.data = bundle.build(cls.path, "summary", clock=CLOCK).data
        cls.files = entries(cls.data)

    def verify(self, data):
        return bundle.verify(data)

    def test_an_untouched_bundle_is_intact_and_says_it_is_not_signed(self):
        found = self.verify(self.data)
        self.assertEqual((found["status"], found["exit_code"], found["problems"]), ("INTACT", 0, []))
        self.assertEqual(found["authenticity"], "NOT_ESTABLISHED")
        self.assertIn("not signed", found["note"])
        manifest = json.loads(self.files[bundle.MANIFEST])
        self.assertIsNone(manifest["signature"])
        self.assertEqual((manifest["gate"]["decision"], manifest["gate"]["completion_allowed"]), ("PASS", True))
        # The ID the bundle recomputes for the saved gate is the gate's own (`gate.Gate.id`).
        saved = read_json(self.path / "state.json")["gate"]
        self.assertEqual(bundle.gate_id(saved), saved["id"])
        self.assertEqual(list(self.files)[0], bundle.MANIFEST)
        self.assertEqual(manifest["files"], sorted(manifest["files"], key=lambda entry: entry["path"]))

    def test_a_changed_file_is_found(self):
        events = self.files["history/events.json"].replace(b"task_completed", b"task_paused")
        found = self.verify(rezip({**self.files, "history/events.json": events}))
        self.assertEqual((found["status"], found["exit_code"]), ("MODIFIED", 2))
        self.assertIn("HASH_MISMATCH", codes(found))

    def test_a_forgery_that_recomputes_every_hash_still_contradicts_the_ledger(self):
        validity = json.loads(self.files["evidence/validity.json"])
        validity["records"][0]["validity"] = "INVALID"
        found = self.verify(resealed(self.files, {"evidence/validity.json": bundle.canonical(validity)}))
        self.assertEqual(found["status"], "MODIFIED")
        self.assertIn("VALIDITY_MISMATCH", codes(found))
        # A forgery that keeps the counts by validity: only the rows themselves contradict the ledger.
        validity = json.loads(self.files["evidence/validity.json"])
        validity["records"][0]["causes"] = ["WORKSPACE_CHANGED"]
        found = self.verify(resealed(self.files, {"evidence/validity.json": bundle.canonical(validity)}))
        self.assertEqual((found["status"], codes(found)), ("MODIFIED", {"VALIDITY_MISMATCH"}))
        saved = json.loads(self.files["gate/evaluation.json"])
        saved["saved"]["decision"] = "BLOCKED"
        found = self.verify(resealed(self.files, {"gate/evaluation.json": bundle.canonical(saved)}))
        self.assertTrue({"GATE_ID_MISMATCH", "GATE_MISMATCH"} <= codes(found), found["problems"])
        line = self.files["evidence/ledger.jsonl"].replace(b'"result":"passed"', b'"result":"failed"', 1)
        found = self.verify(resealed(self.files, {"evidence/ledger.jsonl": line}))
        self.assertIn("LEDGER_MISMATCH", codes(found))

    def test_the_forgery_helper_leaves_a_sound_ledger_intact(self):
        # The helper below recomputes what a careful forger would: on the exported ledger it changes nothing.
        found = self.verify(reledgered(self.files, self.files["evidence/ledger.jsonl"], declare_complete=False))
        self.assertEqual((found["status"], found["problems"]), ("INTACT", []))

    def test_a_ledger_whose_chain_is_broken_is_never_intact_even_when_the_manifest_agrees(self):
        ledger = self.files["evidence/ledger.jsonl"]
        for name, damaged in (("a field changed", edited(ledger, 1, b'"iteration":1', b'"iteration":7')),
                              ("a record removed", b"\n".join(line for number, line in enumerate(ledger.split(b"\n")) if number != 0)),
                              ("a record cut short", b"\n".join(line[:40] if number == 1 else line
                                                                for number, line in enumerate(ledger.split(b"\n")))),
                              ("no final newline", ledger.rstrip(b"\n"))):
            found = self.verify(reledgered(self.files, damaged))
            self.assertEqual((found["status"], found["exit_code"]), ("MODIFIED", 2), name)
            self.assertIn("LEDGER_DAMAGED", codes(found), name)
            self.assertNotIn("LEDGER_MISMATCH", codes(found), name)  # the manifest agrees: the ledger itself is what fails

    def test_a_ledger_that_lost_records_the_state_had_seen_is_not_attested(self):
        lines = self.files["evidence/ledger.jsonl"].split(b"\n")
        shorter = b"\n".join(lines[:-2]) + b"\n"  # the last record gone; what is left still chains
        self.assertEqual(evidence.parse(shorter).problems, [])
        found = self.verify(reledgered(self.files, shorter))
        self.assertEqual(found["status"], "MODIFIED")
        self.assertIn("LEDGER_UNANCHORED", codes(found))
        self.assertIn("GATE_NOT_ABOUT_LEDGER", codes(found))

    def test_a_record_of_a_later_schema_is_never_promoted_to_valid(self):
        ledger = evidence.parse(self.files["evidence/ledger.jsonl"])
        record = {"version": 2, "id": f"E-{ledger.count + 1:04d}", "seq": ledger.count + 1, "at": "2026-11-05T12:00:00+00:00",
                  "kind": "command", "executor": "runtime", "criteria": ["AC-01"], "result": "passed", "prev": ledger.head}
        record["hash"] = evidence._digest(record)
        longer = self.files["evidence/ledger.jsonl"] + json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        found = self.verify(reledgered(self.files, longer))
        self.assertNotIn("LEDGER_DAMAGED", codes(found))  # the chain holds it: a later version may have written it
        rows = {row["id"]: row for row in json.loads(entries(reledgered(self.files, longer))["evidence/validity.json"])["records"]}
        self.assertEqual((rows[record["id"]]["validity"], rows[record["id"]]["causes"]), ("INVALID", ["RECORD_UNKNOWN_SHAPE"]))
        self.assertIn("GATE_NOT_ABOUT_LEDGER", codes(found))
        self.assertNotEqual(found["status"], "INTACT")

    def test_a_bundle_without_a_gate_is_incomplete_whatever_its_manifest_declares(self):
        files = {name: data for name, data in self.files.items() if name != "gate/evaluation.json"}
        manifest = json.loads(files[bundle.MANIFEST])
        manifest["files"] = [entry for entry in manifest["files"] if entry["path"] != "gate/evaluation.json"]
        manifest.update(gate=None, omissions=[], complete=True)
        found = self.verify(resealed({**files, bundle.MANIFEST: bundle.canonical(manifest)}, {}))
        self.assertEqual((found["status"], codes(found)), ("INCOMPLETE", {"NO_GATE"}))

    def test_an_edited_manifest_is_found(self):
        manifest = json.loads(self.files[bundle.MANIFEST])
        manifest["gate"]["decision"] = "BLOCKED"
        found = self.verify(rezip({**self.files, bundle.MANIFEST: bundle.canonical(manifest)}))
        self.assertTrue({"MANIFEST_DIGEST", "GATE_MISMATCH"} <= codes(found))
        manifest = json.loads(self.files[bundle.MANIFEST])
        manifest["gate"]["completion_allowed"] = True
        manifest["gate"]["decision"] = "BLOCKED"
        manifest["content_sha256"] = bundle.content_digest(manifest)
        manifest["bundle_id"] = f"EB-{manifest['content_sha256'][:16]}"
        self.assertIn("GATE_MISMATCH", codes(self.verify(rezip({**self.files, bundle.MANIFEST: bundle.canonical(manifest)}))))

    def test_a_missing_file_makes_it_incomplete_and_an_extra_one_modified(self):
        missing = {name: data for name, data in self.files.items() if name != "verification/checks.json"}
        found = self.verify(rezip(missing))
        self.assertEqual((found["status"], found["exit_code"]), ("INCOMPLETE", 1))
        self.assertIn("FILE_MISSING", codes(found))
        found = self.verify(rezip({**self.files, "extra.txt": b"not listed"}))
        self.assertEqual(found["status"], "MODIFIED")
        self.assertIn("UNEXPECTED_FILE", codes(found))

    def test_a_manifest_that_cannot_be_read_or_is_newer_is_never_taken_as_valid(self):
        for manifest, status, code in ((b"{not json", "MALFORMED", "MANIFEST_UNREADABLE"), (b"[]", "MALFORMED", "MANIFEST_UNREADABLE")):
            found = self.verify(rezip({**self.files, bundle.MANIFEST: manifest}))
            self.assertEqual((found["status"], found["exit_code"]), (status, 3))
            self.assertIn(code, codes(found))
        found = self.verify(rezip({name: data for name, data in self.files.items() if name != bundle.MANIFEST}))
        self.assertIn("NO_MANIFEST", codes(found))
        for change, code in (({"schema_version": 2}, "UNKNOWN_SCHEMA"), ({"schema_version": True}, "UNKNOWN_SCHEMA"),
                             ({"format": "something-else"}, "UNKNOWN_FORMAT")):
            manifest = {**json.loads(self.files[bundle.MANIFEST]), **change}
            found = self.verify(rezip({**self.files, bundle.MANIFEST: bundle.canonical(manifest)}))
            self.assertEqual((found["status"], found["exit_code"], codes(found)), ("UNSUPPORTED", 4, {code}))
        manifest = {**json.loads(self.files[bundle.MANIFEST]), "files": [{"path": "summary.md"}]}
        self.assertIn("MANIFEST_FILES", codes(self.verify(rezip({**self.files, bundle.MANIFEST: bundle.canonical(manifest)}))))

    def test_unsafe_names_duplicates_and_links_are_refused_and_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as folder:
            before = set(Path(folder).parent.iterdir())
            for name in ("../evil.txt", "/etc/evil", "C:/evil.txt", "a\\b.txt", "logs/../../evil", "./x", "dir/", "", "a//b"):
                found = self.verify(rezip(self.files, raw_names=[(name, b"x")]))
                self.assertEqual((found["status"], found["exit_code"]), ("MALFORMED", 3), name)
                self.assertIn("UNSAFE_NAME", codes(found), name)
            self.assertEqual(set(Path(folder).parent.iterdir()), before)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)  # zipfile warns while it writes the duplicate on purpose
            found = self.verify(rezip(self.files, raw_names=[("summary.md", b"again")]))
        self.assertIn("DUPLICATE_NAME", codes(found))
        self.assertIn("DUPLICATE_NAME", codes(self.verify(rezip(self.files, raw_names=[("SUMMARY.md", b"x")]))))
        link = rezip({**self.files, "logs/x.log": b"/etc/passwd"}, attrs={"logs/x.log": (stat.S_IFLNK | 0o777) << 16})
        self.assertIn("LINK_ENTRY", codes(self.verify(link)))

    def test_a_zip_bomb_or_an_oversized_archive_is_not_read(self):
        bomb = rezip({**self.files, "logs/bomb.log": bytes(8 * 1024 * 1024)})
        found = self.verify(bomb)
        self.assertEqual((found["status"], found["exit_code"]), ("UNVERIFIABLE", 5))
        self.assertIn("COMPRESSION_RATIO", codes(found))
        with patch.object(bundle, "MAX_TOTAL_BYTES", 1000):
            self.assertIn("ARCHIVE_EXPANDS_TOO_MUCH", codes(self.verify(self.data)))
        with patch.object(bundle, "MAX_ARCHIVE_BYTES", 100):
            self.assertIn("ARCHIVE_TOO_LARGE", codes(self.verify(self.data)))
        with patch.object(bundle, "MAX_ENTRIES", 3):
            self.assertIn("TOO_MANY_ENTRIES", codes(self.verify(self.data)))
        self.assertEqual(self.verify(b"PK\x03\x04 not really")["status"], "MALFORMED")
        self.assertEqual(self.verify(Path(tempfile.gettempdir()) / "no-such-bundle-here.zip")["status"], "UNVERIFIABLE")

    def test_the_command_line_checks_a_file_and_returns_the_verdict(self):
        with tempfile.TemporaryDirectory() as folder:
            good, bad = Path(folder) / "good.zip", Path(folder) / "bad.zip"
            good.write_bytes(self.data)
            bad.write_bytes(rezip({**self.files, "extra.txt": b"x"}))
            with patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(main(["bundle", "verify", str(good)]), 0)
            self.assertIn("INTACT", out.getvalue())
            self.assertIn("Authenticity: Not established", out.getvalue())
            with patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(main(["bundle", "verify", str(bad), "--json"]), 2)
            self.assertEqual(json.loads(out.getvalue())["status"], "MODIFIED")


class ExportTests(Case):
    @staticmethod
    def log_name(path) -> str:
        """The log the first command record names: each run names its files with its own token."""
        return evidence.read(path).get("E-0001")["log"]

    def blocked(self):
        task_id, path, workspace = self.task(GOOD, MO2)
        state = run_task(self.home, task_id, adapter=Agents())
        self.assertEqual((state["status"], state["phase"], state["gate"]["decision"]), ("paused", "validate", "BLOCKED"))
        return task_id, path, workspace

    def test_a_blocked_task_is_exported_as_blocked_and_never_as_completed(self):
        _task_id, path, _workspace = self.blocked()
        made = bundle.build(path, "summary", clock=CLOCK)
        manifest, files = made.manifest, entries(made.data)
        self.assertEqual((manifest["gate"]["decision"], manifest["gate"]["completion_allowed"], manifest["gate"]["about_exported_ledger"]),
                         ("BLOCKED", False, True))
        self.assertEqual((manifest["complete"], manifest["omissions"], manifest["mode"]), (True, [], "summary"))
        self.assertEqual(set(files), {"manifest.json", "summary.md", "task/contract.json", "gate/evaluation.json", "evidence/ledger.jsonl",
                                      "evidence/validity.json", "reviews/references.json", "verification/checks.json", "history/events.json"})
        self.assertEqual(files["evidence/ledger.jsonl"], (path / evidence.LEDGER).read_bytes())  # byte for byte
        self.assertEqual(manifest["excluded_by_mode"][0]["reason"], "summary bundle")
        contract = json.loads(files["task/contract.json"])
        self.assertEqual({item["id"]: item["status"] for item in contract["judged"]["criteria"]},
                         {"AC-01": "VERIFIED", "AC-02": "AWAITING_MANUAL_VALIDATION"})
        summary = files["summary.md"].decode()
        self.assertIn("Decision: BLOCKED", summary)
        self.assertIn("Completion allowed: no", summary)
        self.assertNotIn("feature.txt contains good\n", files["evidence/ledger.jsonl"].decode())  # no file of the project
        self.assertEqual(bundle.verify(made.data)["status"], "INTACT")
        # A manifest that claims completion is allowed, everything else unchanged and its digest recomputed, is found.
        forged = json.loads(files[bundle.MANIFEST])
        forged["gate"]["completion_allowed"] = True
        forged["content_sha256"] = bundle.content_digest(forged)
        forged["bundle_id"] = f"EB-{forged['content_sha256'][:16]}"
        found = bundle.verify(rezip({**files, bundle.MANIFEST: bundle.canonical(forged)}))
        self.assertEqual((found["status"], codes(found)), ("MODIFIED", {"GATE_MISMATCH"}))

    def test_equal_state_gives_an_equal_bundle_and_only_the_creation_time_differs(self):
        _task_id, path, _workspace = self.blocked()
        first, second = bundle.build(path, "summary", clock=CLOCK), bundle.build(path, "summary", clock=CLOCK)
        self.assertEqual(first.data, second.data)
        later = bundle.build(path, "summary", clock=lambda: "2026-11-06T09:00:00+00:00")
        self.assertEqual(later.manifest["bundle_id"], first.manifest["bundle_id"])
        a, b = entries(first.data), entries(later.data)
        self.assertEqual({name: data for name, data in a.items() if name != bundle.MANIFEST},
                         {name: data for name, data in b.items() if name != bundle.MANIFEST})
        self.assertNotEqual(a[bundle.MANIFEST], b[bundle.MANIFEST])

    def test_a_detailed_bundle_holds_masked_logs_of_the_task_and_nothing_else(self):
        _task_id, path, workspace = self.blocked()
        name = self.log_name(path)
        log = path / "runs" / name
        secret = "sk-" + "a" * 32
        known = "very-private-value-42"
        with log.open("a", encoding="utf-8") as stream:
            stream.write(f"\nAPI key {secret} and {known}\n")
        made = bundle.build(path, "detailed", clock=CLOCK, secrets=[known])
        files = entries(made.data)
        copy = files[f"logs/{name}"].decode()
        self.assertNotIn(secret, copy)
        self.assertNotIn(known, copy)
        source = next(entry["source"] for entry in made.manifest["files"] if entry["path"] == f"logs/{name}")
        self.assertEqual((source["record"], source["sanitized"], source["matches_record"]), ("E-0001", True, False))  # it was changed
        self.assertFalse(any(name.endswith("feature.txt") or "worktree" in name for name in files))
        self.assertTrue(all(name.split("/")[0] in {"manifest.json", "summary.md", "task", "gate", "evidence", "reviews", "verification",
                                                    "history", "logs", "artifacts"} for name in files))
        # The reviewer's reply the review record names was not written by the scripted reviewer: said, never invented.
        self.assertIn({"item": "logs/iteration-001/reviewer.reply.md", "reason": "missing"}, made.manifest["omissions"])
        self.assertFalse(made.manifest["complete"])
        self.assertEqual(bundle.verify(made.data)["status"], "INCOMPLETE")

    def test_a_large_log_is_cut_to_its_end(self):
        _task_id, path, _workspace = self.blocked()
        name = self.log_name(path)
        log = path / "runs" / name
        log.write_bytes(b"x" * (bundle.LOG_BYTES * 3) + b"\nthe end\n")
        made = bundle.build(path, "detailed", clock=CLOCK)
        copy = entries(made.data)[f"logs/{name}"]
        self.assertLessEqual(len(copy), bundle.LOG_BYTES + 200)
        self.assertTrue(copy.endswith(b"the end\n"))
        self.assertIn(b"Only the end of this file is in the bundle", copy)
        self.assertTrue(any(entry["item"] == f"logs/{name}" and "last" in entry["reason"]
                            for entry in made.manifest["omissions"]))

    def test_a_log_name_or_a_link_never_leads_outside_the_task(self):
        _task_id, path, _workspace = self.blocked()
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("private file outside the task", encoding="utf-8")
        state = read_json(path / "state.json")
        record = evidence.read(path)
        with patch.object(bundle, "_sources", return_value=[("logs", "../../outside.txt", "E-0001", {}),
                                                            ("logs", "iteration-001/../../outside.txt", "E-0001", {})]):
            made = bundle.build(path, "detailed", clock=CLOCK)
        self.assertEqual([entry["reason"] for entry in made.manifest["omissions"] if entry["item"].startswith("logs/")],
                         ["unsafe_name", "unsafe_name"])
        self.assertFalse(any(b"private file" in data for data in entries(made.data).values()))
        name = self.log_name(path)
        log = path / "runs" / name
        log.unlink()
        try:
            os.symlink(outside, log)
        except OSError:
            self.skipTest("Symbolic links cannot be created here")
        made = bundle.build(path, "detailed", clock=CLOCK)
        self.assertIn({"item": f"logs/{name}", "reason": "link_refused"}, made.manifest["omissions"])
        self.assertFalse(any(b"private file" in data for data in entries(made.data).values()))
        self.assertEqual((read_json(path / "state.json"), evidence.read(path).head), (state, record.head))  # nothing written

    def test_a_task_that_keeps_changing_is_refused_instead_of_bundled(self):
        _task_id, path, _workspace = self.blocked()
        real, calls = bundle._bytes, []

        def moving(file):
            calls.append(file.name)
            data = real(file)
            return data + b"x" * len(calls) if file.name == evidence.LEDGER and data is not None else data

        with patch.object(bundle, "_bytes", side_effect=moving), patch.object(bundle.time, "sleep"):
            with self.assertRaises(bundle.Inconsistent):
                bundle.build(path, "summary")
        self.assertEqual(len(calls), 2 * len(bundle.TAKEN) * bundle.ATTEMPTS)  # each file of the view read twice per attempt

    def test_a_gate_saved_before_the_ledger_moved_on_is_named_and_makes_the_bundle_incomplete(self):
        _task_id, path, _workspace = self.blocked()
        state = read_json(path / "state.json")
        evidence.append(path, "manual", executor="user", criteria=["AC-02"], result="passed", summary="Tried it inside Mod Organizer 2",
                        reference="notes", iteration=1, fingerprint=state["gate"]["snapshot"], scope="code")
        made = bundle.build(path, "summary", clock=CLOCK)
        self.assertEqual((made.manifest["gate"]["about_exported_ledger"], made.manifest["complete"]), (False, False))
        self.assertIn("another state of the ledger", json.dumps(made.manifest["omissions"]))
        self.assertIn("does not describe the exported evidence", entries(made.data)["summary.md"].decode())
        found = bundle.verify(made.data)
        self.assertEqual(found["status"], "INCOMPLETE")
        self.assertNotIn("GATE_MISMATCH", codes(found))

    def test_a_record_cut_short_is_left_out_and_said(self):
        _task_id, path, _workspace = self.blocked()
        with (path / evidence.LEDGER).open("ab") as stream:
            stream.write(b'{"version":1,"id":"E-00')
        made = bundle.build(path, "summary", clock=CLOCK)
        self.assertTrue(made.manifest["ledger"]["incomplete_tail"])
        self.assertTrue(entries(made.data)["evidence/ledger.jsonl"].endswith(b"\n"))
        self.assertEqual(bundle.verify(made.data)["status"], "INCOMPLETE")

    def snapshot(self, path) -> dict[str, bytes | None]:
        return {name: bundle._bytes(path / name) for name in bundle.TAKEN}

    def test_a_damaged_ledger_is_exported_as_it_is_and_never_checked_as_intact(self):
        _task_id, path, _workspace = self.blocked()
        damaged = edited((path / evidence.LEDGER).read_bytes(), 1, b'"iteration":1', b'"iteration":7')
        (path / evidence.LEDGER).write_bytes(damaged)
        before = self.snapshot(path)
        made = bundle.build(path, "summary", clock=CLOCK)
        self.assertEqual(self.snapshot(path), before)  # nothing repaired, restarted or rewritten by an export
        files = entries(made.data)
        self.assertEqual(files["evidence/ledger.jsonl"], damaged)
        self.assertEqual((made.manifest["ledger"]["problems"], made.manifest["ledger"]["head"]), (1, ""))
        self.assertIn("fails its integrity check", files["summary.md"].decode())
        rows = json.loads(files["evidence/validity.json"])["records"]
        self.assertFalse(any(row["validity"] == "VALID" for row in rows[1:]))  # from the record that does not fit, nothing counts
        found = bundle.verify(made.data)
        self.assertEqual((found["status"], found["exit_code"]), ("MODIFIED", 2))
        self.assertTrue({"LEDGER_DAMAGED", "GATE_NOT_ABOUT_LEDGER"} <= codes(found), found["problems"])
        self.assertNotIn("LEDGER_MISMATCH", codes(found))

    def test_a_ledger_that_lost_its_last_records_is_exported_unanchored_and_not_attested(self):
        _task_id, path, _workspace = self.blocked()
        lines = (path / evidence.LEDGER).read_bytes().split(b"\n")
        (path / evidence.LEDGER).write_bytes(b"\n".join(lines[:-2]) + b"\n")
        made = bundle.build(path, "summary", clock=CLOCK)
        self.assertEqual((made.manifest["ledger"]["anchored"], made.manifest["ledger"]["problems"]), (False, 1))
        found = bundle.verify(made.data)
        self.assertEqual(found["status"], "MODIFIED")
        self.assertIn("LEDGER_UNANCHORED", codes(found))

    def test_a_tampered_invalidation_is_found_even_in_a_careful_forgery(self):
        task_id, path, workspace = self.blocked()
        (workspace / "feature.txt").write_text("good, and changed", encoding="utf-8")
        recheck_acceptance(self.home, task_id)
        files = entries(bundle.build(path, "summary", clock=CLOCK).data)
        lines = files["evidence/ledger.jsonl"].split(b"\n")
        index = next(number for number, line in enumerate(lines) if b'"kind":"invalidation"' in line)
        self.assertIn(b'"E-0001"', lines[index])
        # The invalidation no longer names E-0001: the passed check about the earlier files would count again.
        forged = reledgered(files, edited(files["evidence/ledger.jsonl"], index, b'"E-0001",', b""))
        found = bundle.verify(forged)
        self.assertEqual(found["status"], "MODIFIED")
        self.assertIn("LEDGER_DAMAGED", codes(found))
        # Removed altogether from the end of the ledger, it leaves a ledger the saved state no longer finds itself in.
        if index == len(lines) - 2:
            found = bundle.verify(reledgered(files, b"\n".join(lines[:index]) + b"\n"))
            self.assertIn("LEDGER_UNANCHORED", codes(found))

    def changing(self, path, change, times=1):
        """`bundle._build` that calls `change` after the first `times` bundles are made: as a run writing during an export."""
        real, made = bundle._build, []

        def build(*args, **kwargs):
            result = real(*args, **kwargs)
            made.append(result)
            if len(made) <= times:
                change(len(made))
            return result

        return patch.object(bundle, "_build", side_effect=build), made

    def test_a_task_md_changed_while_the_bundle_is_made_is_read_again_never_mixed(self):
        _task_id, path, _workspace = self.blocked()
        task = path / "task.md"
        written = lambda _n: task.write_bytes(task.read_bytes() + b"\nA line added during the export.\n")  # noqa: E731
        patched, made = self.changing(path, written)
        with patched, patch.object(bundle.time, "sleep"):
            result = bundle.build(path, "summary", clock=CLOCK)
        self.assertEqual(len(made), 2)  # the first bundle was made from a view that no longer held at its end
        contract = json.loads(entries(result.data)["task/contract.json"])
        self.assertIn("A line added during the export.", contract["description"])
        self.assertEqual(bundle.verify(result.data)["status"], "INTACT")
        patched, made = self.changing(path, written, times=bundle.ATTEMPTS)
        with patched, patch.object(bundle.time, "sleep"), self.assertRaises(bundle.Inconsistent):
            bundle.build(path, "summary", clock=CLOCK)
        self.assertEqual(len(made), bundle.ATTEMPTS)

    def test_a_state_or_ledger_written_while_the_bundle_is_made_is_bundled_from_the_later_view(self):
        _task_id, path, _workspace = self.blocked()
        state = read_json(path / "state.json")

        def saved(_n):
            save_json(path / "state.json", {**state, "updated_at": "2026-11-05T12:30:00+00:00"})

        patched, made = self.changing(path, saved)
        with patched, patch.object(bundle.time, "sleep"):
            result = bundle.build(path, "summary", clock=CLOCK)
        self.assertEqual(len(made), 2)
        self.assertEqual(json.loads(entries(result.data)["task/contract.json"])["task"]["updated_at"], "2026-11-05T12:30:00+00:00")

        def appended(_n):
            evidence.append(path, "authorization", executor="user", criteria=["AC-02"], result="denied", iteration=1)

        count = evidence.read(path).count
        patched, made = self.changing(path, appended)
        with patched, patch.object(bundle.time, "sleep"):
            result = bundle.build(path, "detailed", clock=CLOCK)
        self.assertEqual((len(made), result.manifest["ledger"]["count"]), (2, count + 1))
        self.assertFalse(result.manifest["gate"]["about_exported_ledger"])  # the gate saw the ledger before that record
        self.assertEqual(entries(result.data)["evidence/ledger.jsonl"], (path / evidence.LEDGER).read_bytes())
        self.assertNotIn(bundle.verify(result.data)["status"], {"MODIFIED", "MALFORMED"})

    def test_an_export_beside_a_run_that_writes_is_coherent_or_refused(self):
        _task_id, path, _workspace = self.blocked()
        state, stop, outcomes = read_json(path / "state.json"), threading.Event(), []
        count = evidence.read(path).count

        def run():
            number = 0
            while not stop.is_set() and number < 40:
                number += 1
                evidence.append(path, "authorization", executor="user", criteria=["AC-02"], result="denied", iteration=1)
                save_json(path / "state.json", {**state, "updated_at": f"2026-11-05T13:{number:02d}:00+00:00"})

        writer = threading.Thread(target=run)
        writer.start()
        try:
            for _ in range(6):
                try:
                    outcomes.append(bundle.verify(bundle.build(path, "detailed", clock=CLOCK).data)["status"])
                except bundle.Inconsistent:
                    outcomes.append("refused")
        finally:
            stop.set()
            writer.join(30)
        self.assertGreater(evidence.read(path).count, count)  # the writer did write beside the exports
        self.assertEqual(len(outcomes), 6)
        self.assertFalse({"MODIFIED", "MALFORMED", "UNVERIFIABLE"} & set(outcomes), outcomes)

    def test_a_state_being_replaced_is_read_again_and_a_lasting_refusal_is_raised(self):
        # Windows refuses to open a file for a moment while `save_json` replaces it: as a run does beside an export.
        # POSIX has no such refusal and asks nothing again (`storage.SHARING_RETRY_SECONDS` is 0 there): Windows' budget here.
        busy = SimpleNamespace(read_bytes=unittest.mock.Mock(side_effect=[PermissionError(13, "in use"), b"{}"]))
        with patch("patchrondo.storage.SHARING_RETRY_SECONDS", 2.0):
            self.assertEqual(bundle._bytes(busy), b"{}")
        gone = SimpleNamespace(read_bytes=unittest.mock.Mock(side_effect=FileNotFoundError))
        self.assertIsNone(bundle._bytes(gone))
        refused = SimpleNamespace(read_bytes=unittest.mock.Mock(side_effect=PermissionError(13, "denied")))
        with patch("patchrondo.storage.SHARING_RETRY_SECONDS", 0.05), self.assertRaises(PermissionError):
            bundle._bytes(refused)

    def test_a_log_replaced_or_grown_while_it_is_read_is_left_out(self):
        _task_id, path, _workspace = self.blocked()
        name = self.log_name(path)
        log = path / "runs" / name
        real = os.fdopen

        def opening(fd, *args, **kwargs):
            stream = real(fd, *args, **kwargs)
            if os.path.samestat(os.fstat(fd), os.stat(log)):
                replacement = log.with_name(log.name + ".new")
                replacement.write_bytes(b"another file put in its place\n")
                try:
                    os.replace(replacement, log)  # POSIX: the name now leads to another file
                except PermissionError:
                    replacement.unlink()          # Windows refuses to replace a file that is open: it grows instead
                    with open(log, "ab") as grown:
                        grown.write(b"written while it was read\n")
            return stream

        with patch("patchrondo.bundle.os.fdopen", side_effect=opening):
            made = bundle.build(path, "detailed", clock=CLOCK)
        self.assertIn({"item": f"logs/{name}", "reason": "changed_during_export"}, made.manifest["omissions"])
        self.assertNotIn(f"logs/{name}", entries(made.data))
        self.assertEqual(bundle.verify(made.data)["status"], "INCOMPLETE")

    def test_a_log_that_becomes_a_link_after_it_was_checked_is_never_followed(self):
        _task_id, path, _workspace = self.blocked()
        name = self.log_name(path)
        log = path / "runs" / name
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("private file outside the task", encoding="utf-8")
        real, swapped = bundle.winacl.is_reparse_point, []

        def checked(target):
            answer = real(target)
            if Path(target) == log and not swapped:
                swapped.append(True)
                log.unlink()
                os.symlink(outside, log)  # between the check of the file and its opening
            return answer

        try:
            os.symlink(outside, Path(self.tmp.name) / "probe-link")
        except OSError:
            self.skipTest("Symbolic links cannot be created here")
        with patch("patchrondo.bundle.winacl.is_reparse_point", side_effect=checked):
            made = bundle.build(path, "detailed", clock=CLOCK)
        reason = next(entry["reason"] for entry in made.manifest["omissions"] if entry["item"] == f"logs/{name}")
        self.assertIn(reason, {"outside_the_task", "unreadable", "changed_during_export"})
        self.assertFalse(any(b"private file" in data for data in entries(made.data).values()))

    def test_a_log_too_large_to_hash_is_cut_and_said_to_be_unknown(self):
        _task_id, path, _workspace = self.blocked()
        name = self.log_name(path)
        with patch.object(bundle, "HASH_BYTES", -1):  # every log is over it, even an empty one
            made = bundle.build(path, "detailed", clock=CLOCK)
        source = next(entry["source"] for entry in made.manifest["files"] if entry["path"] == f"logs/{name}")
        self.assertIsNone(source["matches_record"])  # not hashed: never said to match, never said to differ
        self.assertFalse(any(entry["item"] == f"logs/{name}" for entry in made.manifest["omissions"]))

    def test_a_log_that_is_no_longer_the_one_its_record_hashed_is_named_as_such(self):
        _task_id, path, _workspace = self.blocked()
        name = self.log_name(path)
        with (path / "runs" / name).open("a", encoding="utf-8") as stream:
            stream.write("\nwritten after the record\n")
        made = bundle.build(path, "detailed", clock=CLOCK)
        self.assertTrue(any(entry["item"] == f"logs/{name}" and "changed after its record hashed it" in entry["reason"]
                            for entry in made.manifest["omissions"]))
        self.assertEqual(bundle.verify(made.data)["status"], "INCOMPLETE")

    def test_stale_evidence_keeps_its_result_beside_its_validity(self):
        task_id, path, workspace = self.blocked()
        (workspace / "feature.txt").write_text("good, and changed", encoding="utf-8")
        recheck_acceptance(self.home, task_id)  # invalidates, under the lock, what was about the earlier files
        made = bundle.build(path, "summary", clock=CLOCK)
        rows = {row["id"]: row for row in json.loads(entries(made.data)["evidence/validity.json"])["records"]}
        self.assertEqual((rows["E-0001"]["result"], rows["E-0001"]["validity"], rows["E-0001"]["causes"]),
                         ("passed", "STALE", ["WORKSPACE_CHANGED"]))
        self.assertGreaterEqual(made.manifest["validity"]["STALE"], 2)
        self.assertNotEqual(made.manifest["gate"]["decision"], "PASS")
        self.assertEqual(bundle.verify(made.data)["status"], "INTACT")

    def test_a_task_from_before_the_gate_and_the_criteria_says_what_it_lacks(self):
        task_id, path, _workspace = self.task(GOOD)
        state = read_json(path / "state.json")
        del state["acceptance"]
        state.pop("gate", None)
        save_json(path / "state.json", state)
        made = bundle.build(path, "summary", clock=CLOCK)
        self.assertIsNone(made.manifest["gate"])
        self.assertEqual({entry["item"] for entry in made.manifest["omissions"]}, {"gate/evaluation.json", "task/contract.json#judged"})
        self.assertIn("Not evaluated", entries(made.data)["summary.md"].decode())
        self.assertNotIn("gate/evaluation.json", entries(made.data))
        self.assertEqual(bundle.verify(made.data)["status"], "INCOMPLETE")

    def test_concurrent_exports_are_identical_and_never_interfere(self):
        _task_id, path, _workspace = self.blocked()
        found, errors = [], []

        def export():
            try:
                found.append(bundle.build(path, "summary", clock=CLOCK).data)
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        threads = [threading.Thread(target=export) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual((errors, len(found), len(set(found))), ([], 4, 1))

    def test_the_command_line_exports_and_never_replaces_a_file(self):
        task_id, _path, _workspace = self.blocked()
        target = Path(self.tmp.name) / "out.zip"
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(main(["--home", str(self.home), "bundle", "export", task_id, "-o", str(target)]), 0)
        self.assertIn("Gate: BLOCKED", out.getvalue())
        self.assertEqual(bundle.verify(target)["status"], "INTACT")
        before = target.read_bytes()
        with patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(main(["--home", str(self.home), "bundle", "export", task_id, "-o", str(target), "--detailed"]), 1)
        self.assertEqual(target.read_bytes(), before)

    def test_a_mode_that_does_not_exist_is_refused(self):
        _task_id, path, _workspace = self.task(GOOD)
        with self.assertRaises(bundle.BundleError):
            bundle.build(path, "everything")


class CenterCase(Case):
    """An interface beside the project, to read the Verification Center as the page does. No tests of its own."""

    def setUp(self):
        super().setUp()
        self.app = App(Path(self.tmp.name) / "ui-home")
        self.project = SimpleNamespace(id="P-0000aaaa", home=self.home)

    def view(self, task_id) -> dict:
        return self.app.verification_view(self.project, task_id)

    def kinds(self, view) -> list[str]:
        return [action["kind"] for action in view["actions"]]


class CenterTests(CenterCase):
    """The Verification Center's read model, and the request's end-to-end scenario through the real loop."""

    def test_from_a_blocked_task_to_a_completed_one_backend_center_and_bundles_agree(self):
        task_id, path, workspace = self.task(GOOD, MO2)
        agents = Agents()
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual(state["gate"]["decision"], "BLOCKED")
        view = self.view(task_id)
        self.assertEqual((view["gate"]["decision"], view["gate_state"], view["code"]), ("BLOCKED", "current", "APPROVED"))
        self.assertEqual(view["counts"]["blocked"], ["AC-02"])
        self.assertEqual((view["summary"]["required"], view["summary"]["required_verified"]), (2, 1))
        self.assertEqual(self.kinds(view), ["attach_evidence", "check_again", "export"])
        self.assertEqual(view["actions"][0]["criterion"], "AC-02")
        self.assertNotIn("complete", self.kinds(view))
        first = bundle.build(path, "summary", clock=CLOCK)
        self.assertEqual((bundle.verify(first.data)["status"], first.manifest["gate"]["decision"]), ("INTACT", "BLOCKED"))
        # The check outside the project, recorded through the one path a person has: no agent is called.
        attached = attach_evidence(self.home, task_id, "AC-02", summary="Started the mod inside Mod Organizer 2",
                                   reference="https://example.invalid/log")
        self.assertEqual(attached["gate"]["decision"], "PASS")
        view = self.view(task_id)
        self.assertEqual((view["gate"]["decision"], view["gate_state"]), ("PASS", "current"))
        self.assertEqual(self.kinds(view)[0], "complete")
        self.assertIs(view["actions"][0]["calls_agents"], False)
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], agents.roles), ("done", ["developer", "reviewer"]))
        view = self.view(task_id)
        self.assertEqual((view["task"]["status"], self.kinds(view)), ("done", ["export"]))
        second = bundle.build(path, "summary", clock=CLOCK)
        found = bundle.verify(second.data)
        self.assertEqual((found["status"], second.manifest["gate"]["decision"], second.manifest["gate"]["completion_allowed"]),
                         ("INTACT", "PASS", True))
        self.assertNotEqual(second.manifest["bundle_id"], first.manifest["bundle_id"])
        judged = json.loads(entries(second.data)["task/contract.json"])["judged"]
        self.assertEqual({item["status"] for item in judged["criteria"]}, {"VERIFIED"})
        self.assertEqual(json.loads(entries(second.data)["task/contract.json"])["task"]["status"], "done")
        # The first bundle still says what was true when it was made.
        self.assertEqual((bundle.verify(first.data)["status"], json.loads(entries(first.data)["gate/evaluation.json"])["saved"]["decision"]),
                         ("INTACT", "BLOCKED"))

    def test_a_gate_older_than_the_ledger_is_never_shown_as_current(self):
        task_id, path, _workspace = self.task(GOOD, MO2)
        run_task(self.home, task_id, adapter=Agents())
        state = read_json(path / "state.json")
        evidence.append(path, "manual", executor="user", criteria=["AC-02"], result="passed", summary="Tried it inside Mod Organizer 2",
                        reference="notes", iteration=1, fingerprint=state["gate"]["snapshot"], scope="code")
        view = self.view(task_id)
        self.assertEqual(view["gate_state"], "behind")
        self.assertEqual((view["actions"][0]["kind"], view["actions"][0].get("primary")), ("check_again", True))

    def test_a_task_never_evaluated_or_from_before_criteria_gets_no_invented_gate(self):
        task_id, path, _workspace = self.task(GOOD)
        view = self.view(task_id)
        self.assertEqual((view["gate"], view["gate_state"], view["tracked"]), (None, "not_evaluated", True))
        self.assertEqual(self.kinds(view), ["resume", "check_again", "export"])
        self.assertEqual(view["actions"][0]["label"], "Run")
        state = read_json(path / "state.json")
        del state["acceptance"]
        save_json(path / "state.json", state)
        view = self.view(task_id)
        self.assertEqual((view["gate"], view["tracked"], view["criteria"], view["code"]), (None, False, [], None))
        self.assertEqual(self.kinds(view), ["resume", "export"])

    def test_while_a_run_works_only_the_export_is_offered(self):
        task_id, _path, _workspace = self.task(GOOD, MO2)
        run_task(self.home, task_id, adapter=Agents())
        with patch("patchrondo.app.runinfo.runtime", return_value={"activity": "running", "active": True, "can_start": False}):
            view = self.view(task_id)
        self.assertEqual(view["gate_state"], "running")
        self.assertEqual([(item["kind"], item["enabled"]) for item in view["actions"]], [("wait", False), ("export", True)])

    def test_evidence_pages_filter_on_the_server_and_a_record_says_what_invalidated_it(self):
        task_id, path, workspace = self.task(GOOD, MO2)
        run_task(self.home, task_id, adapter=Agents())
        (workspace / "feature.txt").write_text("good, and changed", encoding="utf-8")
        recheck_acceptance(self.home, task_id)
        page = self.app.verification_evidence(self.project, task_id, {"validity": ["STALE"]})
        self.assertEqual({row["validity"] for row in page["items"]}, {"STALE"})
        self.assertEqual(page["items"][-1]["id"], "E-0001")  # newest first
        passed = self.app.verification_evidence(self.project, task_id, {"result": ["passed"], "kind": ["command"]})
        self.assertEqual([row["id"] for row in passed["items"]], ["E-0001"])
        detail = self.app.verification_record(self.project, task_id, "E-0001")
        self.assertEqual((detail["record"]["result"], detail["assessment"]["validity"]), ("passed", "STALE"))
        self.assertEqual([entry["cause"] for entry in detail["invalidations"]], ["WORKSPACE_CHANGED"])
        self.assertEqual(detail["log"], {"id": evidence.read(path).get("E-0001")["log"], "available": True})
        for bad in ("E-9999", "../state.json", "E-1"):
            with self.assertRaises(FileNotFoundError):
                self.app.verification_record(self.project, task_id, bad)
        for query in ({"validity": ["GOOD"]}, {"limit": ["0"]}, {"offset": ["-1"]}, {"kind": ["restart"]}, {"q": ["x" * 81]}):
            with self.assertRaises(ValueError):
                self.app.verification_evidence(self.project, task_id, query)

    def test_exports_at_once_are_bounded(self):
        task_id, _path, _workspace = self.task(GOOD)
        for _ in range(2):
            self.assertTrue(self.app._exports.acquire(blocking=False))
        try:
            with self.assertRaises(Conflict):
                self.app.bundle_preview(self.project, task_id, "summary")
        finally:
            self.app._exports.release()
            self.app._exports.release()
        self.assertEqual(self.app.bundle_preview(self.project, task_id, "summary")["manifest"]["task_id"], task_id)
        with self.assertRaises(ValueError):
            self.app.bundle_preview(self.project, task_id, "../../etc")


class FreshnessTests(CenterCase):
    """A saved gate is shown as current only while its ledger, its files and its configuration are what it was evaluated on."""

    def passed(self):
        """A task whose gate is PASS, paused at validate: the run that would complete it calls no agent."""
        task_id, path, workspace = self.task(GOOD, MO2)
        run_task(self.home, task_id, adapter=Agents())
        attach_evidence(self.home, task_id, "AC-02", summary="Started the mod inside Mod Organizer 2",
                        reference="https://example.invalid/log")
        state = read_json(path / "state.json")
        self.assertEqual((state["status"], state["phase"], state["gate"]["decision"]), ("paused", "validate", "PASS"))
        return task_id, path, workspace

    def test_a_pass_on_unchanged_files_is_current_and_offers_completion(self):
        task_id, _path, _workspace = self.passed()
        view = self.view(task_id)
        self.assertEqual((view["gate_state"], view["freshness"]["causes"]), ("current", []))
        self.assertEqual({key: view["freshness"][key] for key in ("ledger", "files", "configuration", "evidence")},
                         {"ledger": "current", "files": "current", "configuration": "current", "evidence": "current"})
        self.assertIsNotNone(view["freshness"]["checked_at"])
        self.assertEqual(self.kinds(view)[0], "complete")

    def test_a_pass_on_files_that_changed_since_is_not_current_and_offers_no_completion(self):
        task_id, path, workspace = self.passed()
        before, ledger = read_json(path / "state.json"), (path / evidence.LEDGER).read_bytes()
        (workspace / "feature.txt").write_text("good, and changed after the gate", encoding="utf-8")
        view = self.view(task_id)
        self.assertEqual((view["gate"]["decision"], view["gate_state"], view["freshness"]["files"], view["freshness"]["causes"]),
                         ("PASS", "behind", "changed", ["WORKSPACE_CHANGED"]))
        self.assertNotIn("complete", self.kinds(view))
        self.assertEqual((view["actions"][0]["kind"], view["actions"][0]["label"], view["actions"][0].get("primary")),
                         ("check_again", "Evaluate again", True))
        self.assertIn("files changed", view["actions"][0]["detail"])
        # The view reads only: the saved gate and the ledger are as they were.
        self.assertEqual((read_json(path / "state.json"), (path / evidence.LEDGER).read_bytes()), (before, ledger))

    def test_a_change_that_keeps_the_size_and_the_time_is_still_seen(self):
        task_id, _path, workspace = self.passed()
        target = workspace / "feature.txt"
        info = target.stat()
        target.write_bytes(target.read_bytes().replace(b"good", b"gooD"))
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertEqual((target.stat().st_size, target.stat().st_mtime_ns), (info.st_size, info.st_mtime_ns))
        view = self.view(task_id)
        self.assertEqual((view["gate_state"], view["freshness"]["causes"]), ("behind", ["WORKSPACE_CHANGED"]))
        self.assertNotIn("complete", self.kinds(view))

    def test_a_pass_older_than_the_ledger_is_not_current(self):
        task_id, path, _workspace = self.passed()
        state = read_json(path / "state.json")
        evidence.append(path, "authorization", executor="user", criteria=["AC-02"], result="denied", iteration=state["iteration"])
        view = self.view(task_id)
        self.assertEqual((view["gate_state"], view["freshness"]["ledger"], view["freshness"]["causes"]),
                         ("behind", "changed", ["LEDGER_CHANGED"]))
        self.assertNotIn("complete", self.kinds(view))

    def test_a_pass_under_another_configuration_has_to_be_evaluated_again(self):
        task_id, _path, _workspace = self.passed()
        self.configure(commands=[self.check, [sys.executable, "-c", "pass"]])
        view = self.view(task_id)
        self.assertEqual((view["gate_state"], view["freshness"]["files"], view["freshness"]["configuration"],
                          view["freshness"]["causes"]), ("behind", "current", "changed", ["CONFIGURATION_CHANGED"]))
        self.assertNotIn("complete", self.kinds(view))
        self.assertEqual(view["actions"][0]["label"], "Evaluate again")
        # Put back, the configuration is again the one the gate was evaluated with.
        self.configure(commands=[self.check])
        self.assertEqual(self.view(task_id)["gate_state"], "current")

    def test_files_that_cannot_be_read_leave_the_gate_unknown_never_current(self):
        task_id, path, workspace = self.passed()
        with patch("patchrondo.app.fingerprint", side_effect=gitops.FingerprintError("feature.txt cannot be read")):
            view = self.view(task_id)
        self.assertEqual((view["gate"]["decision"], view["gate_state"], view["freshness"]["files"], view["freshness"]["causes"]),
                         ("PASS", "unknown", "unknown", ["FINGERPRINT_UNAVAILABLE"]))
        self.assertNotIn("complete", self.kinds(view))
        self.assertIn("check_again", self.kinds(view))
        with patch("patchrondo.app.fingerprint", side_effect=gitops.GitError("Git: not a repository")):
            self.assertEqual(self.view(task_id)["gate_state"], "unknown")
        state = read_json(path / "state.json")
        save_json(path / "state.json", {**state, "worktree": str(workspace.parent / "gone")})
        view = self.view(task_id)
        self.assertEqual((view["gate_state"], view["freshness"]["causes"]), ("unknown", ["WORKTREE_MISSING"]))
        save_json(path / "state.json", {**state, "gate": {**state["gate"], "snapshot": None}})
        self.assertEqual(self.view(task_id)["freshness"]["causes"], ["FILES_NOT_COMPARED"])
        with patch.object(self.app, "_config", return_value=(None, "config.json cannot be read")):
            save_json(path / "state.json", state)
            view = self.view(task_id)
        self.assertEqual((view["gate_state"], view["freshness"]["files"], view["freshness"]["causes"]),
                         ("unknown", "current", ["CONFIGURATION_UNREADABLE"]))

    def test_evaluating_again_replaces_what_is_shown_and_completes_nothing_by_itself(self):
        task_id, path, workspace = self.passed()
        (workspace / "feature.txt").write_text("good, and changed after the gate", encoding="utf-8")
        old = read_json(path / "state.json")["gate"]["id"]
        with patch.object(self.app, "start_run") as start:
            result = self.app.criteria_check(self.project, task_id, {"resume": True})  # what "Complete the task" posts
        start.assert_not_called()  # the backend decided again, on these files: no run, no `_complete`
        self.assertIs(result["completes"], False)
        state = read_json(path / "state.json")
        self.assertEqual(state["status"], "paused")
        self.assertNotEqual((state["gate"]["id"], state["gate"]["decision"]), (old, "PASS"))
        view = self.view(task_id)
        self.assertEqual((view["gate"]["id"], view["gate_state"]), (state["gate"]["id"], "current"))
        self.assertEqual(view["gate"]["decision"], "NEEDS_REVERIFICATION")
        self.assertNotIn("complete", self.kinds(view))

    def test_a_completed_task_keeps_its_gate_as_history_and_is_not_fingerprinted(self):
        task_id, path, workspace = self.passed()
        run_task(self.home, task_id, adapter=Agents())
        saved = read_json(path / "state.json")
        (workspace / "feature.txt").write_text("changed after completion", encoding="utf-8")
        with patch("patchrondo.app.fingerprint", side_effect=AssertionError("an ended task is not fingerprinted")):
            view = self.view(task_id)
        self.assertEqual((view["task"]["status"], view["gate"]["decision"], view["gate_state"], self.kinds(view)),
                         ("done", "PASS", "historical", ["export"]))
        self.assertEqual(read_json(path / "state.json"), saved)

    def test_a_task_from_before_the_gate_gets_no_invented_freshness(self):
        task_id, path, _workspace = self.task(GOOD)
        state = read_json(path / "state.json")
        del state["acceptance"]
        state.pop("gate", None)
        save_json(path / "state.json", state)
        with patch("patchrondo.app.fingerprint", side_effect=AssertionError("nothing to compare")):
            view = self.view(task_id)
        self.assertEqual((view["gate"], view["gate_state"], view["freshness"]["causes"]), (None, "not_evaluated", []))

    def test_the_dashboard_reads_the_files_without_a_lock_and_gets_the_engines_digest(self):
        _task_id, path, workspace = self.passed()
        (workspace / "feature.txt").write_text("good, and changed", encoding="utf-8")
        (workspace / "new.txt").write_text("untracked", encoding="utf-8")
        # A tracked file whose time changes and whose content does not: a plain `git status` refreshes the index then.
        tracked = workspace / gitops.git("ls-files", cwd=workspace).splitlines()[0]
        later = tracked.stat().st_mtime_ns + 5_000_000_000
        os.utime(tracked, ns=(later, later))
        index = Path(gitops.git("rev-parse", "--git-dir", cwd=workspace))
        index = (index if index.is_absolute() else workspace / index) / "index"
        before = index.read_bytes()
        found = gitops.fingerprint(workspace, reader=True)
        self.assertEqual(index.read_bytes(), before)  # `git status` refreshed nothing on disk
        self.assertEqual(found, gitops.fingerprint(workspace))
        self.assertNotEqual(found, read_json(path / "state.json")["gate"]["snapshot"])

    def test_evidence_pages_never_fingerprint_the_files(self):
        task_id, _path, _workspace = self.passed()
        with patch("patchrondo.app.fingerprint", side_effect=AssertionError("evidence pages read the ledger only")):
            self.app.verification_evidence(self.project, task_id, {})
            self.app.verification_record(self.project, task_id, "E-0001")

    def test_views_at_once_share_a_reading_only_when_it_started_after_they_asked(self):
        workspace = Path(self.tmp.name)
        started, release, calls = threading.Event(), threading.Event(), []

        def slow(_folder, **_options):
            calls.append(None)
            number = len(calls)
            if number == 1:
                started.set()
                release.wait(10)
            return f"reading-{number}"

        found = {}

        def ask(name):
            found[name] = self.app._fingerprint(workspace)

        with patch("patchrondo.app.fingerprint", side_effect=slow):
            first = threading.Thread(target=ask, args=("first",))
            first.start()
            self.assertTrue(started.wait(10))
            later = [threading.Thread(target=ask, args=(name,)) for name in ("second", "third")]
            for thread in later:
                thread.start()
            deadline = time.monotonic() + 10
            while self.app._print_ticket < 3 and time.monotonic() < deadline:  # both asked, and wait for the reading in progress
                time.sleep(0.01)
            self.assertEqual(self.app._print_ticket, 3)
            release.set()
            for thread in [first, *later]:
                thread.join(10)
            self.assertEqual(found, {"first": "reading-1", "second": "reading-2", "third": "reading-2"})
            self.assertEqual(len(calls), 2)
            self.assertEqual(self.app._fingerprint(workspace), "reading-3")  # a request after it, a reading of its own


if __name__ == "__main__":
    unittest.main()
