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
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import warnings
import zipfile

from patchrondo import bundle, evidence, gate
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
        saved = json.loads(self.files["gate/evaluation.json"])
        saved["saved"]["decision"] = "BLOCKED"
        found = self.verify(resealed(self.files, {"gate/evaluation.json": bundle.canonical(saved)}))
        self.assertTrue({"GATE_ID_MISMATCH", "GATE_MISMATCH"} <= codes(found), found["problems"])
        line = self.files["evidence/ledger.jsonl"].replace(b'"result":"passed"', b'"result":"failed"', 1)
        found = self.verify(resealed(self.files, {"evidence/ledger.jsonl": line}))
        self.assertIn("LEDGER_MISMATCH", codes(found))

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
        self.assertEqual(len(calls), 4 * bundle.ATTEMPTS)

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


class CenterTests(Case):
    """The Verification Center's read model, and the request's end-to-end scenario through the real loop."""

    def setUp(self):
        super().setUp()
        self.app = App(Path(self.tmp.name) / "ui-home")
        self.project = SimpleNamespace(id="P-0000aaaa", home=self.home)

    def view(self, task_id) -> dict:
        return self.app.verification_view(self.project, task_id)

    def kinds(self, view) -> list[str]:
        return [action["kind"] for action in view["actions"]]

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


if __name__ == "__main__":
    unittest.main()
