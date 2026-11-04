"""Provenance and invalidation: whether a record of the evidence ledger can still be used, and what is put on record when not."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from patchrondo import acceptance, evidence, gitops, provenance, verification
from patchrondo.core import create_task, recheck_acceptance, run_task
from patchrondo.provenance import Cause, Current, Validity
from patchrondo.storage import private_dir, read_json, save_json

from support import copied, one_commit
from test_verification import GREEN, Agents, Case

RUN = "001-0000aaaa"


def previous_fingerprint(worktree: Path) -> str:
    """`gitops.fingerprint` as it was before this change, kept to show that ordinary copies keep their digest."""
    digest = hashlib.sha256()
    digest.update(gitops.git("rev-parse", "HEAD", cwd=worktree).encode("utf-8"))
    digest.update(gitops.git("status", "--porcelain", cwd=worktree).encode("utf-8"))
    names = gitops.git("ls-files", "-z", "--cached", "--others", "--exclude-standard", cwd=worktree)
    for name in sorted(set(names.split("\0")) - {""}):
        digest.update(name.encode("utf-8") + b"\0")
        path = worktree / name
        if path.is_symlink():
            digest.update(b"symlink\0" + os.readlink(path).encode("utf-8"))
        elif path.is_file():
            digest.update(b"file\0" + hashlib.sha256(path.read_bytes()).digest())
        else:
            digest.update(b"missing-or-directory\0")
    return digest.hexdigest()


def git(*args, cwd):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def link_folder(link: Path, target: Path, case: unittest.TestCase) -> None:
    """A junction on Windows, a symbolic link elsewhere; the test is skipped where none can be made."""
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        case.skipTest(f"no link could be made here: {exc}")
    case.addCleanup(lambda: os.rmdir(link) if os.name == "nt" else link.unlink())  # the link only, never what it points to


def forge(task_path: Path, **fields) -> dict:
    """Append a record the way a hand that rewrites the chain would: `evidence.append` checks nothing of it."""
    ledger = evidence.read(task_path)
    record = {"version": 1, "id": f"E-{ledger.count + 1:04d}", "seq": ledger.count + 1, "at": "2026-11-04T00:00:00Z",
              **fields, "prev": ledger.head}
    record["hash"] = evidence._digest(record)
    with (task_path / evidence.LEDGER).open("ab") as stream:
        stream.write((json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    return record


def rechained(task_path: Path, edit) -> None:
    """Rewrite the ledger as a version that wrote other fields would have: `edit` changes the records, the chain is rebuilt.

    The task state is pointed at the new chain, so the ledger still holds what it saw.
    """
    file = task_path / evidence.LEDGER
    records = [json.loads(line) for line in file.read_bytes().decode("utf-8").splitlines()]
    edit(records)
    previous, lines = "", []
    for number, record in enumerate(records, 1):
        record.update(seq=number, prev=previous)
        record.pop("hash", None)
        record["hash"] = previous = evidence._digest(record)
        lines.append(json.dumps(record, sort_keys=True, separators=(",", ":")))
    file.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    state = read_json(task_path / "state.json")
    seen = state["acceptance"]["ledger"]
    if seen["count"]:
        seen["head"] = records[seen["count"] - 1]["hash"]
    save_json(task_path / "state.json", state)


class FingerprintTests(unittest.TestCase):
    """`gitops.fingerprint`: what it covers, what it never reads, and what it refuses to give."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = copied(one_commit, self.root / "repo with space é")
        (self.repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
        (self.repo / "src dir").mkdir()
        (self.repo / "src dir" / "módulo.py").write_text("print('a')\n", encoding="utf-8")
        git("add", ".", cwd=self.repo)
        git("commit", "-qm", "files", cwd=self.repo)

    def digest(self) -> str:
        return gitops.fingerprint(self.repo)

    def test_an_ordinary_copy_keeps_the_digest_earlier_versions_gave(self):
        """Saved evidence stays bound to the files it was made on: the hardening changes no ordinary digest."""
        self.assertEqual(self.digest(), previous_fingerprint(self.repo))
        (self.repo / "src dir" / "módulo.py").write_text("print('b')\n", encoding="utf-8")
        (self.repo / "new file.txt").write_text("new", encoding="utf-8")
        self.assertEqual(self.digest(), previous_fingerprint(self.repo))

    def test_it_is_the_same_for_the_same_files_and_moves_with_every_relevant_change(self):
        base = self.digest()
        self.assertEqual(self.digest(), base)
        module = self.repo / "src dir" / "módulo.py"
        os.utime(module, ns=(1_000_000_000, 1_000_000_000))
        self.assertEqual(self.digest(), base, "a time is not content")
        seen = {base}
        module.write_text("print('changed')\n", encoding="utf-8")  # modified, not committed
        seen.add(self.digest())
        (self.repo / "untracked.py").write_text("x = 1\n", encoding="utf-8")  # new and not tracked
        seen.add(self.digest())
        (self.repo / "untracked.py").unlink()
        module.write_text("print('a')\n", encoding="utf-8")
        self.assertEqual(self.digest(), base, "the same files again")
        module.unlink()  # deleted
        seen.add(self.digest())
        git("checkout", "--", ".", cwd=self.repo)
        git("mv", "README.md", "READ ME.md", cwd=self.repo)  # renamed
        seen.add(self.digest())
        self.assertEqual(len(seen), 5)

    def test_ignored_files_are_not_part_of_it(self):
        base = self.digest()
        (self.repo / "ignored").mkdir()
        (self.repo / "ignored" / "local.cfg").write_text("secret=1", encoding="utf-8")
        self.assertEqual(self.digest(), base)  # a check names such files in `inputs`

    def test_nothing_behind_a_link_or_a_junction_is_read(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "data.txt").write_text("outside", encoding="utf-8")
        link_folder(self.repo / "linked", outside, self)
        opened = []
        real = gitops.content_digest
        with patch("patchrondo.gitops.content_digest", side_effect=lambda path, name=None: opened.append(name) or real(path, name)):
            first = self.digest()
            (outside / "data.txt").write_text("changed outside", encoding="utf-8")
            self.assertEqual(self.digest(), first)
        self.assertFalse(any("data.txt" in str(name) for name in opened), opened)
        self.assertTrue(gitops._through_link(self.repo, "linked/data.txt", {}))

    def test_a_cloud_file_or_another_reparse_point_is_read_as_a_file(self):
        """Only links and junctions are links: a OneDrive file holds its own data."""
        info = os.lstat(self.repo / "README.md")
        # Stand-ins with the fields `_linked` reads: `os.stat_result` refuses Windows-only fields elsewhere (Python 3.13 on
        # Linux and macOS raises TypeError, as CI run 41901150009 showed).
        cloud = SimpleNamespace(st_mode=info.st_mode, st_reparse_tag=0x9000001A, st_file_attributes=0x400)
        with patch("patchrondo.gitops.os.lstat", return_value=cloud):
            self.assertFalse(gitops._linked(self.repo / "README.md"))
        junction = SimpleNamespace(st_mode=info.st_mode, st_reparse_tag=0xA0000003, st_file_attributes=0x410)
        with patch("patchrondo.gitops.os.lstat", return_value=junction):
            self.assertTrue(gitops._linked(self.repo / "README.md"))

    def test_a_repository_inside_the_copy_is_hashed_too(self):
        nested = self.repo / "vendor lib"
        one_commit(nested)
        base = self.digest()
        (nested / "README.md").write_text("changed inside the nested repository\n", encoding="utf-8")
        changed = self.digest()
        self.assertNotEqual(changed, base)
        (nested / "README.md").write_text("changed again, same status line\n", encoding="utf-8")
        self.assertNotEqual(self.digest(), changed, "the parent's `git status` line is the same: only the content differs")
        empty = self.repo / "started"
        empty.mkdir()
        git("init", "-q", cwd=empty)
        (empty / "a.txt").write_text("a", encoding="utf-8")
        self.assertRegex(self.digest(), r"\A[0-9a-f]{64}\Z")  # a repository without a commit is hashed, not refused

    def test_a_file_that_cannot_be_read_gives_no_digest(self):
        target = self.repo / "src dir" / "módulo.py"
        real = os.open

        def refuse(path, *args, **kwargs):
            if Path(path) == target:
                raise PermissionError(13, "Permission denied")
            return real(path, *args, **kwargs)

        with patch("patchrondo.gitops.os.open", side_effect=refuse):
            with self.assertRaises(gitops.FingerprintError) as raised:
                self.digest()
        self.assertIn("módulo.py cannot be read", str(raised.exception))
        self.assertEqual(raised.exception.kind, "fingerprint_unavailable")
        self.assertIsInstance(raised.exception, OSError)  # what caught an OSError before still catches it

    def test_a_file_that_keeps_changing_while_it_is_read_gives_no_digest(self):
        real = os.fstat
        calls = {"n": 0}

        def moving(fd):
            info = real(fd)
            calls["n"] += 1
            values = list(info)
            values[8] += calls["n"]  # st_mtime: another time at every look
            return os.stat_result(values, {"st_mtime_ns": info.st_mtime_ns + calls["n"]})

        with patch("patchrondo.gitops.os.fstat", side_effect=moving):
            with self.assertRaisesRegex(gitops.FingerprintError, "kept changing while it was read"):
                self.digest()

    def test_a_change_during_one_read_is_read_again(self):
        real, flips = os.fstat, {"left": 1}

        def once(fd):
            info = real(fd)
            if flips["left"]:
                flips["left"] -= 1
                return os.stat_result(list(info), {"st_mtime_ns": info.st_mtime_ns + 7})
            return info

        base = self.digest()
        with patch("patchrondo.gitops.os.fstat", side_effect=once):
            self.assertEqual(self.digest(), base)

    @unittest.skipIf(sys.platform in {"win32", "darwin"}, "file names are not arbitrary bytes there")
    def test_a_name_that_is_not_utf8_is_still_its_file(self):
        name = os.fsdecode(b"raw\xff name.txt")
        (self.repo / name).write_text("one", encoding="utf-8")
        before = self.digest()
        (self.repo / name).write_text("two", encoding="utf-8")
        self.assertNotEqual(self.digest(), before)  # before, it was hashed as missing whatever it held

    def test_without_git_there_is_no_digest(self):
        plain = self.root / "plain folder"
        plain.mkdir()
        with patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(self.root)}):
            with self.assertRaises(gitops.GitError):
                gitops.fingerprint(plain)


class InputsTests(unittest.TestCase):
    """Files a check declares it reads, ignored by Git or not."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "project ü"
        (self.root / "conf" / "deep").mkdir(parents=True)
        (self.root / "conf" / "a.yaml").write_text("a", encoding="utf-8")
        (self.root / "conf" / "deep" / "b.yaml").write_text("b", encoding="utf-8")
        (self.root / ".env").write_text("TOKEN=" + "x" * 20, encoding="utf-8")

    def check(self, *inputs, **fields):
        return verification.parse_check({"id": "suite", "type": "test", "command": ["x"], "inputs": list(inputs), **fields},
                                        default_timeout=5)

    def test_patterns_match_like_glob_and_the_digest_follows_the_files(self):
        self.assertEqual(verification._matches(self.root, "conf/**/*.yaml")[0], ["conf/a.yaml", "conf/deep/b.yaml"])
        self.assertEqual(verification._matches(self.root, "**/b.yaml")[0], ["conf/deep/b.yaml"])
        check = self.check(".env", "conf/*.yaml", "missing/*.txt")
        first, problem = verification.inputs_digest(check, self.root)
        self.assertIsNone(problem)
        self.assertEqual((first["files"], first["patterns"]), (2, [".env", "conf/*.yaml", "missing/*.txt"]))
        self.assertNotIn("TOKEN", json.dumps(first))  # only a digest: no name of a value, no content
        self.assertEqual(verification.inputs_digest(check, self.root)[0], first)
        (self.root / "conf" / "a.yaml").write_text("A", encoding="utf-8")
        second = verification.inputs_digest(check, self.root)[0]
        (self.root / "missing").mkdir()
        (self.root / "missing" / "late.txt").write_text("late", encoding="utf-8")
        third = verification.inputs_digest(check, self.root)[0]
        self.assertEqual(len({first["sha256"], second["sha256"], third["sha256"]}), 3)

    def test_locations_outside_the_project_are_refused_in_the_configuration(self):
        for bad in ("../secrets.txt", "/etc/passwd", "C:/Windows/win.ini", "a/../../b", ".", "c:x"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.check(bad)
        with self.assertRaises(ValueError):
            self.check(".env", same_environment="yes")

    def test_a_link_or_a_cycle_of_links_is_refused_and_the_walk_ends(self):
        link_folder(self.root / "conf" / "loop", self.root / "conf", self)
        names, problem = verification._matches(self.root, "conf/**/*.yaml")
        self.assertEqual(names, [])
        self.assertIn("is a link or a junction", problem)
        found, problem = verification.inputs_digest(self.check("conf/**/*.yaml"), self.root)
        self.assertIsNone(found)
        self.assertIn("conf/loop", problem)

    def test_files_too_large_or_too_many_are_not_hashed(self):
        with patch.object(verification, "MAX_INPUT_BYTES", 0):
            self.assertIn("larger than", verification.inputs_digest(self.check(".env"), self.root)[1])
        with patch.object(verification, "MAX_INPUT_FILES", 1):
            self.assertIn("more than 1 files", verification.inputs_digest(self.check("conf/**/*.yaml"), self.root)[1])

    def test_identity_and_digest_stay_as_they_were_without_the_new_settings(self):
        plain = verification.parse_check({"id": "suite", "type": "test", "command": ["x"]}, default_timeout=5)
        self.assertEqual(set(plain.identity()), {"id", "type", "command", "cwd", "report", "report_path", "min_tests",
                                                 "required_tests", "artifacts", "shell"})
        self.assertNotEqual(self.check(".env").digest(), plain.digest())
        self.assertNotEqual(self.check(".env", same_environment=True).digest(), self.check(".env").digest())
        self.assertEqual(plain.digest(), hashlib.sha256(json.dumps(plain.identity(), sort_keys=True,
                                                                   separators=(",", ":")).encode()).hexdigest())


class LedgerCase(unittest.TestCase):
    """A private task folder with a ledger written by `evidence.append`, and what is current for it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.path = private_dir(Path(self.tmp.name).resolve() / "task")
        self.cfg = {"tests": {"commands": [["python", "-m", "unittest"]]}, "workflow": {"test_timeout_seconds": 60},
                    "verification": {"checks": [{"id": "suite", "type": "test", "command": ["pytest"]}]}}
        checks = verification.checks(self.cfg)
        self.digests = {check.id: check.digest() for check in checks}
        self.now = Current(fingerprint="F1", checks=dict(self.digests), legacy=(provenance.kept(checks[0].command),),
                           environment="H-here")

    def command(self, **fields):
        check = {"id": "suite", "config_sha256": self.digests["suite"], "environment": {"id": "H-here"}}
        check.update(fields.pop("check", {}))
        values = {"result": "passed", "fingerprint": "F1", "scope": "code", "run": RUN, "of": 1, "command": ["pytest"],
                  "check": check, **fields}
        return evidence.append(self.path, "command", **{key: value for key, value in values.items() if value is not None})

    def judge(self, record, now=None, artifacts=lambda artifact: evidence.INTACT):
        ledger = evidence.read(self.path)
        return provenance.assess(ledger, ledger.get(record["id"]), now or self.now, artifacts=artifacts)


class AssessmentTests(LedgerCase):
    """`provenance.assess` from synthetic ledgers: one rule at a time, the result of each record left as it was."""

    def test_fresh_evidence_is_valid(self):
        result = self.judge(self.command())
        self.assertEqual((result.validity, result.causes, result.check, result.result), ("VALID", (), "suite", "passed"))

    def test_evidence_about_other_files_is_stale_and_keeps_its_result(self):
        record = self.command()
        result = self.judge(record, Current(**{**self.now.__dict__, "fingerprint": "F2"}))
        self.assertEqual((result.validity, result.causes, result.result), ("STALE", ("WORKSPACE_CHANGED",), "passed"))
        self.assertTrue(result.dump()["reverification_required"])
        self.assertEqual(evidence.read(self.path).get(record["id"])["result"], "passed")  # nothing rewritten

    def test_without_the_state_of_its_files_it_is_unknown_never_valid(self):
        result = self.judge(self.command(fingerprint=None))
        self.assertEqual((result.validity, result.causes), ("UNKNOWN", ("SNAPSHOT_MISSING",)))
        # Files not looked at: nothing about them is judged.
        self.assertEqual(self.judge(self.command(fingerprint=None), Current(checks=self.now.checks)).validity, "VALID")

    def test_another_configuration_of_its_check(self):
        self.assertEqual(self.judge(self.command(check={"config_sha256": "0" * 64})).causes, ("CHECK_CHANGED",))
        self.assertEqual(self.judge(self.command(check={"id": "gone"})).causes, ("CHECK_REMOVED",))
        unnamed = self.command(check={"config_sha256": None})
        self.assertEqual((self.judge(unnamed).validity, self.judge(unnamed).causes), ("UNKNOWN", ("CHECK_UNIDENTIFIED",)))

    def test_a_command_of_tests_commands_written_before_records_named_their_configuration(self):
        same = self.command(check={"id": "command-1", "config_sha256": None}, command=["python", "-m", "unittest"])
        other = self.command(check={"id": "command-1", "config_sha256": None}, command=["python", "-m", "pytest"], run="001-b")
        self.assertEqual((self.judge(same).validity, self.judge(other).causes), ("VALID", ("CHECK_CHANGED",)))
        # Before checks: no `check` at all, known by its place in its run.
        first = evidence.append(self.path, "command", result="passed", fingerprint="F1", scope="code", run="001-old", of=2,
                                command=["python", "-m", "unittest"])
        second = evidence.append(self.path, "command", result="passed", fingerprint="F1", scope="code", run="001-old", of=2,
                                 command=["python", "-m", "unittest"])
        self.assertEqual((self.judge(first).validity, self.judge(first).check), ("VALID", "command-1"))
        self.assertEqual((self.judge(second).causes, self.judge(second).check), (("CHECK_REMOVED",), "command-2"))

    def test_inputs_and_the_environment_a_check_depends_on(self):
        now = Current(**{**self.now.__dict__, "inputs": {"suite": "I1"}, "same_environment": frozenset({"suite"})})
        record = self.command(check={"inputs": {"sha256": "I1"}})
        self.assertEqual(self.judge(record, now).validity, "VALID")
        self.assertEqual(self.judge(record, Current(**{**now.__dict__, "inputs": {"suite": "I2"}})).causes, ("INPUTS_CHANGED",))
        self.assertEqual(self.judge(record, Current(**{**now.__dict__, "inputs": {"suite": None}})).validity, "UNKNOWN")
        self.assertEqual(self.judge(record, Current(**{**now.__dict__, "environment": "H-there"})).causes, ("ENVIRONMENT_CHANGED",))
        # A check that does not ask for its environment counts wherever the same files are.
        self.assertEqual(self.judge(record, Current(**{**self.now.__dict__, "environment": "H-there"})).validity, "VALID")

    def test_artifacts_missing_changed_and_the_report_copy(self):
        report = {"path": "r.xml", "sha256": "R", "size": 1}
        build = {"path": "dist/x.whl", "sha256": "W", "size": 1}
        record = self.command(artifacts=[report, build], check={"report": {"sha256": "R"}})
        missing = lambda artifact: evidence.MISSING if artifact["sha256"] == "W" else evidence.INTACT  # noqa: E731
        self.assertEqual(self.judge(record, artifacts=missing).causes, ("ARTIFACT_MISSING",))
        changed = lambda artifact: evidence.CHANGED  # noqa: E731
        result = self.judge(record, artifacts=changed)
        self.assertEqual((result.validity, result.causes), ("INVALID", ("REPORT_CHANGED", "ARTIFACT_CHANGED")))

    def test_an_artifact_replaced_by_a_link_is_changed(self):
        folder = Path(self.tmp.name).resolve()
        real, moved = folder / "real.bin", folder / "moved.bin"
        # As long as the link itself where it can be, so that only its type tells them apart.
        real.write_bytes(b"x" * (0 if os.name == "nt" else len(os.fsencode(str(moved)))))
        artifact = evidence.fingerprint_file(real)
        real.rename(moved)
        try:
            real.symlink_to(moved)
        except OSError as exc:
            self.skipTest(f"no link could be made here: {exc}")
        if os.utime in os.supports_follow_symlinks:
            os.utime(real, ns=(artifact["mtime_ns"], artifact["mtime_ns"]), follow_symlinks=False)
        self.assertEqual(Path(artifact["path"]).stat().st_size, artifact["size"])  # what the link leads to looks the same
        self.assertEqual(evidence.artifact_state(artifact), evidence.CHANGED)
        real.unlink()
        self.assertEqual(evidence.artifact_state(artifact), evidence.MISSING)

    def test_records_this_version_does_not_write_are_never_proof(self):
        future = forge(self.path, version=2, kind="command", executor="runtime", result="passed", fingerprint="F1", scope="code",
                       run=RUN, of=1, command=["pytest"])
        forged = forge(self.path, version=1, kind="command", executor="developer", result="passed", fingerprint="F1",
                       scope="code", run=RUN, of=1, command=["pytest"])
        odd = forge(self.path, version=True, kind="command", executor="runtime", result="passed")
        ledger = evidence.read(self.path)
        self.assertEqual(ledger.problems, [])  # the chain holds: only their shape is wrong
        for record in (future, forged, odd):
            with self.subTest(record=record["id"]):
                self.assertFalse(ledger.counts(ledger.get(record["id"])))
                result = self.judge(record)
                self.assertEqual((result.validity, result.causes), ("INVALID", ("RECORD_UNKNOWN_SHAPE",)))

    def test_a_record_the_chain_does_not_hold_is_invalid(self):
        record = self.command()
        file = self.path / evidence.LEDGER
        file.write_bytes(file.read_bytes().replace(b'"result":"passed"', b'"result":"failed"'))
        result = self.judge(record)
        self.assertEqual((result.validity, result.causes), ("INVALID", ("RECORD_UNTRUSTED",)))

    def test_decisions_of_the_user_and_independent_checks_do_not_depend_on_the_files(self):
        granted = evidence.append(self.path, "authorization", criteria=["AC-02"], result="granted")
        download = evidence.append(self.path, "manual", criteria=["AC-03"], result="passed", scope="independent",
                                   fingerprint="F1", reference="https://example.invalid/x")
        by_hand = evidence.append(self.path, "manual", criteria=["AC-04"], result="passed", scope="code", fingerprint="F1",
                                  reference="session.log")
        elsewhere = Current(fingerprint="F2", checks=self.now.checks)
        self.assertEqual([self.judge(record, elsewhere).validity for record in (granted, download, by_hand)],
                         ["VALID", "VALID", "STALE"])
        # A forged decision is no decision.
        forge(self.path, version=1, kind="authorization", executor="developer", criteria=["AC-05"], result="granted")
        self.assertIsNone(acceptance._Proofs(evidence.read(self.path)).decision("AC-05"))


class InvalidationTests(LedgerCase):
    """`provenance.invalidate`: one record per cause, append-only, never twice, whole again after a crash."""

    def test_one_record_per_cause_naming_what_a_rerun_needs(self):
        files = self.command()
        config = self.command(check={"config_sha256": "0" * 64}, run="001-b")
        decision = evidence.append(self.path, "authorization", criteria=["AC-02"], result="granted")
        now = Current(**{**self.now.__dict__, "fingerprint": "F2"})
        written = provenance.invalidate(self.path, evidence.read(self.path), now, iteration=2)
        self.assertEqual([entry["cause"] for entry in written], ["WORKSPACE_CHANGED"])  # each record under its first cause
        [entry] = written
        self.assertEqual((entry["of"], entry["validity"], entry["detector"], entry["reverify"], entry["executor"]),
                         ([files["id"], config["id"]], "STALE", "provenance", ["suite"], "runtime"))
        self.assertEqual(entry["snapshot"], {"previous": ["F1"], "current": "F2"})
        self.assertEqual(entry["reason"], "The files of the task changed after this was recorded")
        ledger = evidence.read(self.path)
        self.assertTrue(ledger.counts(ledger.get(decision["id"])))
        self.assertEqual(provenance.invalidate(self.path, ledger, now, iteration=2), [])  # a second look writes nothing
        result = provenance.recorded(ledger, ledger.get(files["id"]))
        self.assertEqual((result.validity, result.causes, result.invalidated_by), ("STALE", ("WORKSPACE_CHANGED",), entry["id"]))

    def test_a_crash_between_two_invalidations_is_completed_by_the_next_look(self):
        moved = self.command()
        changed = self.command(check={"config_sha256": "0" * 64}, run="001-b", fingerprint="F2")
        now = Current(**{**self.now.__dict__, "fingerprint": "F2"})
        real, calls = evidence.append, []

        def crash(path, kind, **fields):
            calls.append(kind)
            if len(calls) == 2:
                raise KeyboardInterrupt
            return real(path, kind, **fields)

        with patch("patchrondo.provenance.evidence.append", side_effect=crash), self.assertRaises(KeyboardInterrupt):
            provenance.invalidate(self.path, evidence.read(self.path), now, iteration=2)
        provenance.invalidate(self.path, evidence.read(self.path), now, iteration=2)
        ledger = evidence.read(self.path)
        named = [name for record in ledger.records if record["kind"] == "invalidation" for name in record["of"]]
        self.assertEqual(sorted(named), sorted([moved["id"], changed["id"]]))  # each once
        self.assertEqual(ledger.problems, [])

    def test_invalidations_written_before_causes_keep_their_meaning(self):
        record = self.command()
        evidence.append(self.path, "invalidation", result="invalidated", of=[record["id"]],
                        reason="A file this evidence refers to changed or is no longer there")
        other = self.command(run="001-c")
        evidence.append(self.path, "invalidation", result="invalidated", of=[other["id"]], reason="somebody's words")
        ledger = evidence.read(self.path)
        self.assertEqual(provenance.recorded(ledger, ledger.get(record["id"])).causes, ("ARTIFACT_CHANGED",))
        self.assertEqual(provenance.recorded(ledger, ledger.get(other["id"])).causes, ("INVALIDATED",))

    def test_the_brief_lists_what_no_longer_counts_by_id(self):
        old = self.command()
        provenance.invalidate(self.path, evidence.read(self.path), Current(**{**self.now.__dict__, "fingerprint": "F2"}),
                              iteration=2)
        lines = "\n".join(provenance.brief(evidence.read(self.path)))
        self.assertIn(f"- {old['id']} command of check suite: passed when recorded; STALE (WORKSPACE_CHANGED)", lines)
        self.assertIn("- suite: WORKSPACE_CHANGED", lines)
        self.assertNotIn("F1", lines)  # identifiers and causes, not fingerprints
        self.command(run="001-d", fingerprint="F2")
        self.assertNotIn("- suite:", "\n".join(provenance.brief(evidence.read(self.path))))  # a usable result exists again


class Prompts(Agents):
    """The agents of the runner's engine tests, keeping each reviewer prompt."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prompts = []

    def invoke(self, provider, role, prompt, workspace, run_dir):
        if role == "reviewer":
            self.prompts.append(prompt)
        return super().invoke(provider, role, prompt, workspace, run_dir)


class EngineTests(Case):
    """The rules inside `run_task` and the user's operations, with the real runner and a synthetic test tool."""

    def setUp(self):
        super().setUp()
        self.home = self.root / "state home"
        from patchrondo.core import initialize
        initialize(self.home, self.repo)
        self.configure([self.suite()])
        self.behave(report=GREEN)

    def suite(self, **fields):
        return {"id": "suite", "type": "test", "report": "junit",
                "command": [sys.executable, str(self.tool), str(self.plan), "{report}"], **fields}

    def configure(self, checks):
        cfg = read_json(self.home / "config.json")
        cfg["tests"] = {"enabled": True, "trust_acknowledged": True, "commands": []}
        cfg["verification"] = {"checks": checks}
        save_json(self.home / "config.json", cfg)

    def task(self, manual=True):
        criteria = ["feature.txt says good", "The test suite passes"] + (["Verified inside Mod Organizer 2"] if manual else [])
        task_id, _ = create_task(self.home, title="Feature", description="Write feature.txt", developer="claude",
                                 reviewer="codex", acceptance=criteria)
        return task_id, self.home / "tasks" / task_id

    def ledger(self, path):
        return evidence.read(path)

    def invalidations(self, path):
        return [record for record in self.ledger(path).records if record["kind"] == "invalidation"]

    def statuses(self, path):
        return {item["id"]: item["status"] for item in read_json(path / "state.json")["acceptance"]["criteria"]}

    def paused(self):
        """A task paused at `validate`: code approved, tests verified, the check by hand still missing."""
        task_id, path = self.task()
        state = run_task(self.home, task_id, adapter=Prompts())
        self.assertEqual((state["status"], state["phase"], self.statuses(path)["AC-02"]), ("paused", "validate", "VERIFIED"))
        return task_id, path, Path(state["worktree"])

    def test_a_test_passed_on_other_files_never_completes_the_task(self):
        """Gate C: the files change after the check passed; the record is invalidated and the check runs again."""
        task_id, path, workspace = self.paused()
        [first] = [record for record in self.ledger(path).records if record["kind"] == "command"]
        (workspace / "feature.txt").write_text("changed after the check", encoding="utf-8")
        checked = recheck_acceptance(self.home, task_id)
        self.assertFalse(checked["completes"])
        [entry] = self.invalidations(path)
        self.assertEqual((entry["cause"], first["id"] in entry["of"], entry["reverify"]), ("WORKSPACE_CHANGED", True, ["suite"]))
        self.assertNotEqual(self.statuses(path)["AC-02"], "VERIFIED")
        self.assertEqual(self.ledger(path).get(first["id"])["result"], "passed")  # what was observed stays as it was
        history = read_json(path / "state.json")["history"]
        self.assertEqual(history[-1]["causes"], ["WORKSPACE_CHANGED"])
        attached = Path(self.tmp.name) / "proof.log"
        attached.write_text("played", encoding="utf-8")
        from patchrondo.core import attach_evidence
        attach_evidence(self.home, task_id, "AC-03", summary="Loaded the mod and played it", artifact=str(attached))
        agents = Prompts()
        state = run_task(self.home, task_id, adapter=agents)
        # Back to the check and a review of the files as they are; the reviewer is told what no longer counts.
        self.assertEqual((state["status"], agents.roles), ("done", ["reviewer"]))
        commands = [record for record in self.ledger(path).records if record["kind"] == "command"]
        self.assertEqual(len(commands), 2)
        self.assertEqual(read_json(path / "state.json")["acceptance"]["criteria"][1]["evidence"], [commands[-1]["id"]])
        self.assertIn(f"- {first['id']} command of check suite: passed when recorded; STALE (WORKSPACE_CHANGED)", agents.prompts[0])

    def test_a_reviewer_citing_stale_evidence_approves_nothing(self):
        task_id, path = self.task(manual=False)
        run_task(self.home, task_id, adapter=Prompts(quota_once=True))  # tests passed, the review paused for quota
        [first] = [record for record in self.ledger(path).records if record["kind"] == "command"]
        workspace = Path(read_json(path / "state.json")["worktree"])
        (workspace / "feature.txt").write_text("not what was tested", encoding="utf-8")
        citing = {"verdict": "APPROVED", "summary": f"{first['id']} shows the tests pass", "issues": [],
                  "criteria": [{"id": "AC-01", "outcome": "SATISFIED"}, {"id": "AC-02", "outcome": "SATISFIED", "note": first["id"]}]}
        agents = Prompts(reviews=[citing])
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], state["last_error"]["kind"], agents.roles), ("paused", "stale_tests", []))
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual(state["status"], "done")
        commands = [record["id"] for record in self.ledger(path).records if record["kind"] == "command"]
        criteria = {item["id"]: item for item in state["acceptance"]["criteria"]}
        self.assertEqual(criteria["AC-02"]["evidence"], [commands[-1]])  # the new run's record, never the one cited
        self.assertIn(first["id"], self.invalidations(path)[0]["of"])

    def test_a_changed_check_reruns_and_says_so_once(self):
        task_id, path, _ = self.paused()
        [first] = [record for record in self.ledger(path).records if record["kind"] == "command"]
        self.assertEqual(first["check"]["config_sha256"], verification.parse_check(self.suite(), default_timeout=180).digest())
        self.configure([self.suite(min_tests=2)])
        for _ in range(2):
            recheck_acceptance(self.home, task_id)
        [entry] = self.invalidations(path)
        self.assertEqual((entry["cause"], entry["of"], entry["reverify"]), ("CHECK_CHANGED", [first["id"]], ["suite"]))
        state = run_task(self.home, task_id, adapter=Prompts())
        self.assertEqual((state["status"], state["phase"]), ("paused", "validate"))
        last = [record for record in self.ledger(path).records if record["kind"] == "command"][-1]
        self.assertNotEqual(last["id"], first["id"])
        self.assertEqual(self.statuses(path)["AC-02"], "VERIFIED")

    def test_a_removed_check_no_longer_proves_anything(self):
        task_id, path, _ = self.paused()
        self.configure([{"id": "other", "type": "test", "command": [sys.executable, "-c", "pass"]}])
        recheck_acceptance(self.home, task_id)
        self.assertEqual([entry["cause"] for entry in self.invalidations(path)], ["CHECK_REMOVED"])
        self.assertNotEqual(self.statuses(path)["AC-02"], "VERIFIED")

    def test_a_changed_declared_input_makes_the_result_stale(self):
        (self.repo / ".gitignore").write_text("__pycache__/\n*.pyc\nreports/\ndist/\nlocal/\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qam", "ignore local"], check=True, capture_output=True)
        self.configure([self.suite(inputs=["local/*.cfg"])])
        task_id, path = self.task()
        workspace = Path(read_json(path / "state.json")["worktree"])
        (workspace / "local").mkdir()
        (workspace / "local" / "settings.cfg").write_text("mode=a", encoding="utf-8")
        state = run_task(self.home, task_id, adapter=Prompts())
        self.assertEqual(state["phase"], "validate")
        [first] = [record for record in self.ledger(path).records if record["kind"] == "command"]
        self.assertEqual((first["check"]["inputs"]["files"], first["check"]["inputs"]["patterns"]), (1, ["local/*.cfg"]))
        fingerprint = gitops.fingerprint(workspace)
        (workspace / "local" / "settings.cfg").write_text("mode=b", encoding="utf-8")
        self.assertEqual(gitops.fingerprint(workspace), fingerprint)  # ignored by Git: the files' digest cannot see it
        recheck_acceptance(self.home, task_id)
        self.assertEqual([entry["cause"] for entry in self.invalidations(path)], ["INPUTS_CHANGED"])
        state = run_task(self.home, task_id, adapter=Prompts())
        self.assertEqual((state["status"], state["phase"], self.statuses(path)["AC-02"]), ("paused", "validate", "VERIFIED"))

    def test_an_input_changed_while_the_check_ran_gives_no_result(self):
        (self.repo / ".gitignore").write_text("__pycache__/\n*.pyc\nreports/\ndist/\nlocal/\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qam", "ignore local"], check=True, capture_output=True)
        self.configure([self.suite(inputs=["local/*.cfg"])])
        task_id, path = self.task()
        workspace = Path(read_json(path / "state.json")["worktree"])
        (workspace / "local").mkdir()
        self.behave(report=GREEN, write={str(workspace / "local" / "made.cfg"): "written by the check"})
        agents = Prompts()
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["status"], state["last_error"]["kind"], agents.roles), ("paused", "verification_error", ["developer"]))
        [record] = [record for record in self.ledger(path).records if record["kind"] == "command"]
        self.assertEqual((record["result"], record["check"]["error"]["kind"]), ("error", "inputs_modified"))

    def quota_paused_with_an_input(self, **fields):
        """A task whose check declares an ignored input, paused for quota at the review after its tests passed."""
        (self.repo / ".gitignore").write_text("__pycache__/\n*.pyc\nreports/\ndist/\nlocal/\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qam", "ignore local"], check=True, capture_output=True)
        self.configure([self.suite(inputs=["local/*.cfg"], **fields)])
        task_id, path = self.task(manual=False)
        workspace = Path(read_json(path / "state.json")["worktree"])
        (workspace / "local").mkdir()
        (workspace / "local" / "settings.cfg").write_text("mode=a", encoding="utf-8")
        state = run_task(self.home, task_id, adapter=Prompts(quota_once=True))
        self.assertEqual((state["phase"], state["last_error"]["kind"]), ("review", "quota"))
        return task_id, path, workspace

    def test_a_declared_input_changed_before_the_review_stops_the_review(self):
        task_id, _, workspace = self.quota_paused_with_an_input()
        (workspace / "local" / "settings.cfg").write_text("mode=b", encoding="utf-8")
        agents = Prompts()
        state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["last_error"]["kind"], state["phase"], agents.roles), ("stale_tests", "test", []))

    def test_another_environment_before_the_review_stops_the_review(self):
        task_id, _, _ = self.quota_paused_with_an_input(same_environment=True)
        agents = Prompts()
        here = verification.environment()
        with patch("patchrondo.verification.environment", return_value={**here, "id": "H-elsewhere000000"}):
            state = run_task(self.home, task_id, adapter=agents)
        self.assertEqual((state["last_error"]["kind"], agents.roles), ("stale_tests", []))

    def test_an_input_that_cannot_be_hashed_blocks_the_check(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "a.cfg").write_text("outside", encoding="utf-8")
        link_folder(self.repo / "linked", outside, self)
        check = self.check(inputs=["linked/*.cfg"])
        with patch("patchrondo.verification.process.execute", side_effect=AssertionError("started")) as start:
            result = self.run_one(check)
        start.assert_not_called()
        self.assertEqual((result.status, result.error["kind"]), ("blocked", "refused"))
        self.assertIn("link or a junction", result.reason)

    def test_a_check_bound_to_its_environment(self):
        self.configure([self.suite(same_environment=True)])
        task_id, path, _ = self.paused()
        here = verification.environment()
        with patch("patchrondo.verification.environment", return_value={**here, "id": "H-elsewhere000000"}):
            recheck_acceptance(self.home, task_id)
        self.assertEqual([entry["cause"] for entry in self.invalidations(path)], ["ENVIRONMENT_CHANGED"])

    def test_a_record_that_does_not_name_its_configuration_is_run_again(self):
        """As a check written by the runner of October 17, 2026 would be: never promoted to valid for compatibility."""
        task_id, path, _ = self.paused()

        def older(records):
            for record in records:
                if record["kind"] == "command":
                    record["check"].pop("config_sha256")

        rechained(path, older)
        self.assertEqual(self.ledger(path).problems, [])
        state = run_task(self.home, task_id, adapter=Prompts())
        self.assertEqual([entry["cause"] for entry in self.invalidations(path)], ["CHECK_UNIDENTIFIED"])
        self.assertEqual(self.invalidations(path)[0]["validity"], "UNKNOWN")
        self.assertEqual((state["status"], state["phase"], self.statuses(path)["AC-02"]), ("paused", "validate", "VERIFIED"))
        self.assertEqual(len([record for record in self.ledger(path).records if record["kind"] == "command"]), 2)

    def test_resume_at_validate_with_nothing_changed_writes_nothing(self):
        task_id, path, _ = self.paused()
        before = ((path / "state.json").read_bytes(), (path / evidence.LEDGER).read_bytes())
        self.assertEqual(run_task(self.home, task_id, adapter=Prompts())["status"], "paused")
        self.assertEqual(((path / "state.json").read_bytes(), (path / evidence.LEDGER).read_bytes()), before)

    def test_a_crash_after_the_invalidation_and_before_the_checkpoint(self):
        task_id, path, workspace = self.paused()
        (workspace / "feature.txt").write_text("changed", encoding="utf-8")
        with patch("patchrondo.core._save", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            recheck_acceptance(self.home, task_id)
        self.assertEqual(len(self.invalidations(path)), 1)  # in the ledger; the checkpoint with its event was never saved
        self.assertEqual(self.statuses(path)["AC-02"], "VERIFIED")  # the saved status, which nothing shows as current
        record = acceptance.Acceptance.load(read_json(path / "state.json"))
        shown, _ = acceptance.judged_now(record, self.ledger(path))
        self.assertNotEqual(shown.get("AC-02").status, "VERIFIED")
        recheck_acceptance(self.home, task_id)
        self.assertEqual(len(self.invalidations(path)), 1)  # not written twice
        self.assertNotEqual(self.statuses(path)["AC-02"], "VERIFIED")

    def test_a_checkpoint_from_before_the_invalidation_proves_nothing_again(self):
        task_id, path, workspace = self.paused()
        old = (path / "state.json").read_bytes()
        (workspace / "feature.txt").write_text("changed", encoding="utf-8")
        recheck_acceptance(self.home, task_id)
        (path / "state.json").write_bytes(old)  # a state saved before the invalidation, restored
        record = acceptance.Acceptance.load(read_json(path / "state.json"))
        shown, saved = acceptance.judged_now(record, self.ledger(path))
        self.assertEqual((saved["AC-02"], shown.get("AC-02").status != "VERIFIED"), ("VERIFIED", True))
        view = acceptance.view(record, self.ledger(path))
        command = next(entry for entry in view["evidence"] if entry["kind"] == "command")
        self.assertEqual((command["current"], command["validity"], command["causes"]), (False, "STALE", ["WORKSPACE_CHANGED"]))

    def test_two_tasks_on_one_repository_keep_their_evidence_apart(self):
        first, first_path, first_work = self.paused()
        second, second_path, _ = self.paused()
        (first_work / "feature.txt").write_text("changed in the first task only", encoding="utf-8")
        recheck_acceptance(self.home, first)
        recheck_acceptance(self.home, second)
        self.assertEqual((len(self.invalidations(first_path)), len(self.invalidations(second_path))), (1, 0))
        self.assertEqual(self.statuses(second_path)["AC-02"], "VERIFIED")

    def test_an_unreadable_file_pauses_and_nothing_is_taken_as_verified(self):
        task_id, path, _ = self.paused()
        with patch("patchrondo.core.fingerprint", side_effect=gitops.FingerprintError("feature.txt cannot be read")):
            with self.assertRaises(OSError):
                recheck_acceptance(self.home, task_id)
            state = run_task(self.home, task_id, adapter=Prompts())
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "fingerprint_unavailable"))
        self.assertEqual(self.invalidations(path), [])

    def test_the_switch_keeps_the_invalidation_of_earlier_versions(self):
        with patch.dict(os.environ, {provenance.SWITCH: "0"}):
            task_id, path, workspace = self.paused()
            self.configure([self.suite(min_tests=2)])
            recheck_acceptance(self.home, task_id)
            self.assertEqual(self.invalidations(path), [])  # a check's configuration was no reason before
            (workspace / "feature.txt").write_text("changed", encoding="utf-8")
            recheck_acceptance(self.home, task_id)
            [entry] = self.invalidations(path)
            self.assertNotIn("cause", entry)
            self.assertEqual(entry["reason"], "The files of the task changed after this was recorded")
            history = read_json(path / "state.json")["history"]
            self.assertNotIn("causes", history[-1])


if __name__ == "__main__":
    unittest.main()
