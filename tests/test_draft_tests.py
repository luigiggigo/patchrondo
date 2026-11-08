"""Tests written by the developer on the user's request, while a task waits for the test consent.

Providers are scripted: no provider CLI is started. `TranscriptTests` reaches the scripted CLI of `support`
through `patchrondo.providers.execute`; the dashboard's start is replaced by a child that only leaves a mark.
"""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from patchrondo import activity, bundle, core
from patchrondo.cli import main
from patchrondo.core import DraftRefused, create_task, draft_tests, initialize, parse_draft, run_task
from patchrondo.providers import AgentFailure, AgentReply
from patchrondo.storage import read_json, save_json

from support import ScriptedCLI, ServerCase, make_repo
from test_acceptance import GOOD, Agents, Case

ANSWER = ("## Tests\n\nAdded `test_feature.py`.\n\n"
          '{"commands": ["python -m unittest -v"], "files": ["test_feature.py", "claimed_only.py"], '
          '"notes": "Covers the content of feature.txt; not the empty file."}')


class Writer:
    """A developer asked for tests: writes them, maybe touches another file, and answers `text`."""

    def __init__(self, text=ANSWER, touch=None, fail=None):
        self.text, self.touch, self.fail, self.prompts = text, touch, fail, []

    def invoke(self, provider, role, prompt, workspace, run_dir):
        self.prompts.append((role, prompt))
        if self.fail is not None:
            raise self.fail
        (workspace / "test_feature.py").write_text(
            "import unittest\nfrom pathlib import Path\n\n\nclass T(unittest.TestCase):\n"
            "    def test_good(self):\n        self.assertEqual(Path('feature.txt').read_text(), 'good')\n",
            encoding="utf-8", newline="\n")
        if self.touch:
            (workspace / self.touch).write_text("changed by the test author", encoding="utf-8")
        return AgentReply(text=self.text, provider=provider)


class EngineTests(Case):
    def waiting(self):
        """A task developed once and paused because tests are not allowed yet."""
        self.configure(commands=[self.check], enabled=False)
        task_id, path, workspace = self.task(GOOD)
        state = run_task(self.home, task_id, adapter=Agents())
        self.assertEqual((state["status"], state["phase"], state["last_error"]["kind"]), ("paused", "test", "tests_disabled"))
        return task_id, path, workspace, state

    def test_the_developer_writes_tests_and_the_task_still_waits_for_the_consent(self):
        task_id, path, workspace, before = self.waiting()
        records = self.kinds(path)
        writer = Writer()
        state = draft_tests(self.home, task_id, adapter=writer)
        draft = state["test_draft"]
        self.assertEqual((draft["status"], draft["iteration"], draft["agent"]), ("completed", 1, "claude"))
        self.assertEqual(draft["commands"], ["python -m unittest -v"])
        self.assertEqual(draft["files"], ["test_feature.py"])  # what the worktree shows, not what the answer claims
        self.assertEqual(draft["notes"], "Covers the content of feature.txt; not the empty file.")
        self.assertEqual([role for role, _ in writer.prompts], ["developer"])
        prompt = writer.prompts[0][1]
        self.assertIn("ROLE: TEST AUTHOR", prompt)
        self.assertIn("Write feature.txt with good", prompt)  # the task
        self.assertIn("Everything is done", prompt)  # the handoff of the development step
        self.assertIn("feature.txt", prompt.split("## Files changed so far")[1])
        # Still waiting for the same consent: nothing enabled, nothing run, nothing recorded as a check.
        self.assertEqual((state["status"], state["phase"], state["last_error"]), ("paused", "test", before["last_error"]))
        self.assertEqual(read_json(self.home / "config.json")["tests"]["enabled"], False)
        self.assertEqual(self.state(path), state)
        self.assertNotIn("command", self.kinds(path)[len(records):])
        self.assertEqual([name for name in self.events(path) if name.startswith("test_draft")], ["test_draft_started", "test_draft_completed"])
        completed = next(entry for entry in state["history"] if entry["event"] == "test_draft_completed")
        self.assertEqual({key: completed[key] for key in ("commands", "files")}, {"commands": 1, "files": 1})
        self.assertNotIn("feature.txt", json.dumps(completed))  # counts only: no agent text, no file names
        self.assertEqual(draft["transcripts"], "iteration-001/test-draft-01")
        self.assertIn("python -m unittest -v", (path / "runs" / "iteration-001" / "test-draft-01" / "test-draft.md").read_text(encoding="utf-8"))
        self.assertFalse((path / "runs" / "iteration-001" / "test-draft.md").exists())
        self.assertFalse((path / ".run.lock").exists())
        # A Resume without the consent still does nothing, and keeps the draft.
        self.assertEqual(run_task(self.home, task_id, adapter=Agents()), state)
        # Once allowed, the loop goes on with the tests in the task's files.
        # -B: a check that leaves files Git does not ignore (__pycache__) gives no usable result.
        self.configure(commands=[self.check, [sys.executable, "-B", "-m", "unittest", "-v"]])
        finished = run_task(self.home, task_id, adapter=Agents())
        self.assertEqual(finished["status"], "done", finished.get("last_error"))
        self.assertEqual([item["status"] for item in finished["tests"]], ["passed", "passed"])

    def test_files_are_read_from_the_worktree_also_when_one_already_changed_is_changed_again(self):
        task_id, path, workspace, _ = self.waiting()
        state = draft_tests(self.home, task_id, adapter=Writer(touch="feature.txt"))
        self.assertEqual(state["test_draft"]["files"], ["feature.txt", "test_feature.py"])

    def test_it_is_refused_unless_the_task_waits_for_the_test_consent(self):
        task_id, path, _ = self.task(GOOD)  # ready, never run
        saved = (path / "state.json").read_bytes()
        writer = Writer()
        with self.assertRaises(DraftRefused):
            draft_tests(self.home, task_id, adapter=writer)
        self.assertEqual(((path / "state.json").read_bytes(), writer.prompts), (saved, []))
        done = run_task(self.home, task_id, adapter=Agents())
        self.assertEqual(done["status"], "done")
        with self.assertRaises(DraftRefused):
            draft_tests(self.home, task_id, adapter=writer)
        self.assertEqual(writer.prompts, [])

    def test_a_failure_is_recorded_and_changes_nothing_else(self):
        task_id, path, workspace, before = self.waiting()
        for failure, kind in ((AgentFailure("Usage limit reached. Retry after 3600 seconds.", "quota", provider="claude"), "quota"),
                              (KeyboardInterrupt(), "interrupted")):
            with self.subTest(kind=kind):
                state = draft_tests(self.home, task_id, adapter=Writer(fail=failure))
                draft = state["test_draft"]
                self.assertEqual((draft["status"], draft["kind"]), ("failed", kind))
                self.assertEqual((state["status"], state["phase"], state["last_error"]), ("paused", "test", before["last_error"]))
                self.assertEqual(state.get("recovery"), before.get("recovery"))  # never a retry plan
                self.assertEqual(self.events(path)[-1], "test_draft_failed")
                self.assertFalse((workspace / "test_feature.py").exists())
        self.assertEqual(draft["message"], "Interrupted by the user")

    def test_the_environment_check_comes_before_the_agent(self):
        task_id, path, workspace, _ = self.waiting()
        order = []
        writer = Writer()
        original = writer.invoke
        writer.invoke = lambda *args: order.append("agent") or original(*args)
        with patch.object(core, "_environment", side_effect=lambda *args, **kwargs: order.append("environment")):
            draft_tests(self.home, task_id, adapter=writer)
        self.assertEqual(order, ["environment", "agent"])

    def test_the_command_line_refuses_it_outside_the_pause_and_with_a_retry_option(self):
        task_id, path, _ = self.task(GOOD)  # ready: refused before any agent is built a command
        saved = (path / "state.json").read_bytes()
        err = io.StringIO()

        def no_provider(*args, **kwargs):
            raise AssertionError("no provider CLI may start in this test")

        # Should the refusal ever be lost, the agent's command still reaches no real CLI.
        with redirect_stderr(err), redirect_stdout(io.StringIO()), patch("patchrondo.providers.execute", no_provider):
            self.assertEqual(main(["--home", str(self.home), "run", task_id, "--draft-tests"]), 1)
            self.assertEqual(main(["--home", str(self.home), "run", task_id, "--draft-tests", "--auto-resume"]), 1)
        self.assertIn("only while the task waits for the test consent", err.getvalue())
        self.assertIn("never retries", err.getvalue())
        self.assertEqual((path / "state.json").read_bytes(), saved)

    def test_an_answer_with_no_usable_proposal_keeps_the_files_and_no_command(self):
        task_id, path, workspace, _ = self.waiting()
        state = draft_tests(self.home, task_id, adapter=Writer(text="I wrote tests; run them as you like."))
        draft = state["test_draft"]
        self.assertEqual((draft["status"], draft["commands"], draft["files"], draft["answer_readable"]),
                         ("completed", [], ["test_feature.py"], False))


DRAFT_REPLY = ('## Tests\n\nNo new file was needed.\n\n'
               '{"commands": ["python -B -m unittest -v"], "files": [], "notes": "Written by the test author."}')


class TranscriptTests(unittest.TestCase):
    """The adapter writes real transcripts here: its CLI is the scripted one, started as a process."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        root = Path(folder.name).resolve()
        self.repo, self.home = root / "repo", root / "state"
        make_repo(self.repo)
        initialize(self.home, self.repo)
        cfg = read_json(self.home / "config.json")
        cfg["tests"]["commands"] = [[sys.executable, "-c", "from pathlib import Path; assert Path('feature.txt').read_text() == 'good'"]]
        cfg["workflow"].update(max_iterations=1, agent_timeout_seconds=60)
        cfg["rag"]["enabled"] = False
        save_json(self.home / "config.json", cfg)
        self.cli = ScriptedCLI(root / "fixture")
        guard = patch("patchrondo.providers.execute", side_effect=self.cli.execute)
        guard.start()
        self.addCleanup(guard.stop)

    def journal(self, path):
        return [json.loads(line) for line in (path / activity.JOURNAL).read_text(encoding="utf-8").splitlines()[1:]]

    @staticmethod
    def files(folder):
        return {file.name: file.read_bytes() for file in folder.iterdir() if file.is_file()}

    def test_the_transcripts_of_the_development_step_stay_and_each_request_keeps_its_own(self):
        task_id, _ = create_task(self.home, title="Feature", description="Create feature.txt", acceptance=["Tests pass"],
                                 developer="claude", reviewer="codex")
        path = self.home / "tasks" / task_id
        self.cli.script(developer={"says": ["Writing the feature"]})
        paused = run_task(self.home, task_id)
        self.assertEqual((paused["status"], paused["last_error"]["kind"]), ("paused", "tests_disabled"), paused.get("last_error"))
        iteration = path / "runs" / "iteration-001"
        developed = self.files(iteration)
        for name in ("developer.prompt.md", "developer.reply.md", "developer.stdout.log", "developer.stderr.log"):
            self.assertIn(name, developed)
        self.assertIn(b"Created `feature.txt`", developed["developer.reply.md"])

        self.cli.script(developer={"says": ["Writing the tests"], "reply": DRAFT_REPLY})
        first = draft_tests(self.home, task_id)
        self.assertEqual(first["test_draft"]["status"], "completed", first["test_draft"])
        self.assertEqual(first["test_draft"]["commands"], ["python -B -m unittest -v"])
        self.assertEqual(first["test_draft"]["transcripts"], "iteration-001/test-draft-01")
        # The development step's transcripts are byte for byte what they were, and nothing was added beside them.
        self.assertEqual(self.files(iteration), developed)
        drafted = self.files(iteration / "test-draft-01")
        self.assertIn(b"ROLE: TEST AUTHOR", drafted["developer.prompt.md"])
        self.assertIn(b"Written by the test author.", drafted["developer.reply.md"])
        self.assertIn(b"Writing the tests", drafted["developer.stdout.log"])
        self.assertIn("developer.stderr.log", drafted)
        self.assertIn(b"python -B -m unittest -v", drafted["test-draft.md"])
        self.assertNotIn("developer.active-process.json", drafted)  # removed when the CLI ended

        # A second request in the same iteration writes into a new folder: the first one stays too.
        self.cli.script(developer={"says": ["Writing more tests"], "reply": DRAFT_REPLY.replace("author.", "author again.")})
        second = draft_tests(self.home, task_id)
        self.assertEqual(second["test_draft"]["transcripts"], "iteration-001/test-draft-02")
        self.assertEqual((self.files(iteration), self.files(iteration / "test-draft-01")), (developed, drafted))
        self.assertIn(b"author again.", (iteration / "test-draft-02" / "developer.reply.md").read_bytes())

        # The journal tells the calls apart: the same role, the development step without `step`.
        attempts = {}
        for event in self.journal(path):
            attempts.setdefault(event["attempt"], []).append(event)
        agents = [events for events in attempts.values() if events[0]["role"] == "developer"]
        self.assertEqual(len(agents), 3)
        self.assertEqual([{event.get("step") for event in events} for events in agents],
                         [{None}, {core.DRAFT_STEP}, {core.DRAFT_STEP}])
        self.assertTrue(all(event["type"] != "completion" or event["outcome"] == "completed" for events in agents for event in events))
        self.assertIn("Writing the tests", [event.get("text") for event in agents[1]])

        # Allowed and finished: a detailed bundle holds only the logs its records name, from the folders they name.
        cfg = read_json(self.home / "config.json")
        cfg["tests"].update(enabled=True, trust_acknowledged=True)
        save_json(self.home / "config.json", cfg)
        done = run_task(self.home, task_id)
        self.assertEqual(done["status"], "done", done.get("last_error"))
        self.assertEqual((self.files(iteration / "test-draft-01"), developed["developer.reply.md"]),
                         (drafted, (iteration / "developer.reply.md").read_bytes()))
        made = bundle.build(path, "detailed")
        self.assertEqual(bundle.verify(made.data)["status"], "INTACT")
        logs = {entry["path"]: entry for entry in made.manifest["files"] if entry["role"] == "log"}
        self.assertIn("logs/iteration-001/reviewer.reply.md", logs)
        self.assertFalse([name for name in logs if "test-draft" in name or "developer" in name], logs)
        records = {record["id"]: record for record in map(json.loads, (path / "evidence.jsonl").read_text(encoding="utf-8").splitlines())}
        with zipfile.ZipFile(io.BytesIO(made.data)) as archive:
            for name, entry in logs.items():
                self.assertEqual("logs/" + records[entry["source"]["record"]]["log"], name)  # attributed to the record naming it
            self.assertEqual(archive.read("logs/iteration-001/reviewer.reply.md"), (iteration / "reviewer.reply.md").read_bytes())

    def test_a_folder_left_by_an_earlier_request_is_never_written_into(self):
        task_id, _ = create_task(self.home, title="Feature", description="Create feature.txt", acceptance=["Tests pass"],
                                 developer="codex", reviewer="claude")
        path = self.home / "tasks" / task_id
        run_task(self.home, task_id)
        left = path / "runs" / "iteration-001" / "test-draft-07"  # a crash after the folder, before the state named it
        left.mkdir()
        (left / "developer.reply.md").write_text("earlier", encoding="utf-8")
        self.cli.script(developer={"reply": DRAFT_REPLY})
        state = draft_tests(self.home, task_id)
        self.assertEqual(state["test_draft"]["transcripts"], "iteration-001/test-draft-08")
        self.assertEqual((left / "developer.reply.md").read_text(encoding="utf-8"), "earlier")
        self.assertIn("Written by the test author.", (path / "runs" / "iteration-001" / "test-draft-08" / "developer.last-message.txt").read_text(encoding="utf-8"))


class ParseTests(Case):
    def test_only_the_last_object_counts_and_only_usable_lines_are_kept(self):
        many = [f"python -m unittest tests.t{number}" for number in range(12)]
        cases = {
            "fenced": ("Done.\n```json\n" + json.dumps({"commands": ["npm test"], "notes": "n"}) + "\n```", ["npm test"], 0),
            "last wins": (json.dumps({"commands": ["first"]}) + "\nthen\n" + json.dumps({"commands": ["second"]}), ["second"], 0),
            "text after": (json.dumps({"commands": ["npm test"]}) + "\nAnything else?", [], 0),
            "bad lines": (json.dumps({"commands": ["ok one", "two\nlines", "x" * 301, '"unclosed', "", 5, "ok one"]}), ["ok one"], 6),
            "too many": (json.dumps({"commands": many}), many[:10], 2),
            "none": ("No JSON here", [], 0),
            "not a list": (json.dumps({"commands": "npm test"}), [], 0),
        }
        for label, (text, commands, dropped) in cases.items():
            with self.subTest(label):
                found = parse_draft(text)
                self.assertEqual((found["commands"], found["dropped"]), (commands, dropped))

    def test_notes_are_bounded_and_masked(self):
        key = "sk-" + "a" * 32
        found = parse_draft(json.dumps({"commands": [], "notes": f"uses {key} " + "y" * 5000}))
        self.assertNotIn(key, found["notes"])
        self.assertLess(len(found["notes"]), core.DRAFT_NOTES + 40)


class RouteTests(ServerCase):
    def setUp(self):
        super().setUp()
        which = patch("patchrondo.capabilities.shutil.which", return_value=None)
        which.start()
        self.addCleanup(which.stop)
        self.project, _ = self.add_project()
        self.base = f"/api/projects/{self.project['id']}"
        # A start is a child that only writes its flags: no engine and no provider.
        self.marks = self.root / "starts.txt"
        self.app.run_command = lambda project, task_id, flags: [
            sys.executable, "-c", "import sys; open(sys.argv[1], 'a').write(' '.join(sys.argv[2:]) + '\\n')", str(self.marks),
            task_id, *flags]

    def starts(self):
        return self.marks.read_text().splitlines() if self.marks.exists() else []

    def test_the_dashboard_starts_it_only_confirmed_and_only_at_the_consent_pause(self):
        task_id, path = self.add_task(self.project, acceptance=[GOOD])
        url = f"{self.base}/tasks/{task_id}/run"
        response, data = self.post(url, {"draft_tests": True, "confirm": True})
        self.assertEqual((response.status, data["code"]), (409, "conflict"))  # ready: not waiting for the consent
        state = run_task(Path(self.project["home"]), task_id, adapter=Agents())
        self.assertEqual(state["last_error"]["kind"], "tests_disabled")
        for payload in ({"draft_tests": True}, {"draft_tests": True, "confirm": "yes"}, {"draft_tests": "yes", "confirm": True},
                        {"draft_tests": True, "confirm": True, "auto_resume": True}, {"confirm": True}):
            with self.subTest(payload=payload):
                self.assertEqual(self.post(url, payload)[0].status, 400)
        self.assertEqual(self.starts(), [])
        response, data = self.post(url, {"draft_tests": True, "confirm": True})
        self.assertEqual(response.status, 202, data)
        self.assertEqual(self.starts(), [f"{task_id} --draft-tests"])
        self.assertIn("tests asked of the developer from the dashboard", (path / "ui-run.log").read_text(encoding="utf-8"))
