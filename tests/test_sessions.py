"""Native session resume, its parts (Phase 2.5, Step 10): capabilities, captured sessions, the choice, the command.

Capabilities are read from the help the installed CLIs printed, saved in
`tests/fixtures/cli` (see its README): Claude Code 2.1.295 and 2.1.302,
codex-cli 0.161.0 and 0.160.1. One Codex capability below is synthetic (a
`--sandbox` line added to the resume form's help) and says so: no observed
version lists it, and it exists only to test the command's construction.
Nothing here starts a CLI: `providers.probe` is reached only with its runner
replaced by a function that answers from the fixtures.
"""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from patchrondo import agents, execution, providers, sessions, streams
from patchrondo.execution import (AttemptOutcome, AttemptStatus, CallLimits, ContinuationMode, ExecutionProfile,
                                  ExecutionRecord, InvalidRecord, Phase, ResumeChoice, Source, Termination, Unit, Usage)
from patchrondo.process import Result
from patchrondo.sessions import Status, Support, Unavailable

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cli"
A = "0a1b2c3d-0000-4000-8000-00000000000a"
B = "0a1b2c3d-0000-4000-8000-00000000000b"


def text(folder: str, name: str) -> str:
    return (FIXTURES / folder / name).read_text(encoding="utf-8")


def texts(folder: str) -> dict:
    if folder.startswith("claude"):
        return {"version": text(folder, "version.txt"), "help": text(folder, "help.txt")}
    return {"version": text(folder, "version.txt"), "help": text(folder, "exec-help.txt"),
            "resume_help": text(folder, "exec-resume-help.txt")}


def capability(folder: str) -> sessions.Capability:
    return sessions.capability_of(folder.split("-")[0], texts(folder), evidence=sessions.Evidence.FIXTURE)


def synthetic_codex() -> sessions.Capability:
    """SYNTHETIC: codex-cli 0.161.0 with a `--sandbox` option added to its resume form. Not an observed binary."""
    found = texts("codex-0.161.0")
    line = "      --ephemeral\n"
    found["resume_help"] = found["resume_help"].replace(
        line, "  -s, --sandbox <SANDBOX_MODE>\n          Select the sandbox policy\n\n" + line, 1)
    return sessions.capability_of("codex", found, evidence=sessions.Evidence.FIXTURE)


class CapabilityTests(unittest.TestCase):
    def test_the_observed_versions_read_as_their_help_shows_them(self):
        expected = {
            "claude-2.1.295": ("2.1.295", Status.VERIFIED, Support.SUPPORTED),
            "claude-2.1.302": ("2.1.302", Status.VERIFIED, Support.SUPPORTED),
            # `codex exec resume --help` lists no --sandbox: nothing holds a resumed call to the step's sandbox.
            "codex-0.161.0": ("0.161.0", Status.UNVERIFIED, Support.UNKNOWN),
            "codex-0.160.1": ("0.160.1", Status.UNVERIFIED, Support.UNKNOWN),
        }
        for folder, (version, status, restrictions) in expected.items():
            with self.subTest(folder=folder):
                found = capability(folder)
                self.assertEqual((found.version, found.status, found.restrictions), (version, status, restrictions))
                self.assertEqual((found.noninteractive, found.resume_by_id, found.persistence, found.output_validation,
                                  found.stdin_prompt), (Support.SUPPORTED,) * 5)
                self.assertTrue(found.latest_selection)  # --continue / --last exist, and are never used
                self.assertEqual(found.evidence, sessions.Evidence.FIXTURE)
        codex = capability("codex-0.161.0")
        self.assertIn("--sandbox", codex.options)
        self.assertFalse({"--sandbox", "--cd", "--add-dir", "--profile"} & codex.resume_options)
        self.assertLessEqual({"--json", "--output-last-message", "--output-schema", "--ephemeral", "--last"},
                             codex.resume_options)

    def test_what_the_help_does_not_show_is_unknown_and_never_supported(self):
        claude = texts("claude-2.1.295")
        cases = {
            "no version": sessions.capability_of("claude", {**claude, "version": "Claude Code"}),
            "no help": sessions.capability_of("claude", {"version": claude["version"]}),
            "another CLI's help": sessions.capability_of("claude", {**claude, "help": "Usage: other [options]"}),
            "no resume form help": sessions.capability_of("codex", {**texts("codex-0.161.0"), "resume_help": None}),
        }
        for name, found in cases.items():
            with self.subTest(case=name):
                self.assertIn(found.status, (Status.UNKNOWN, Status.UNVERIFIED))
                self.assertNotEqual(found.resume_by_id, Support.SUPPORTED)
        removed = claude["help"].replace("  -r, --resume [value]", "  -r, --rezume [value]")
        self.assertEqual(sessions.capability_of("claude", {**claude, "help": removed}).status, Status.UNSUPPORTED)
        codex = texts("codex-0.161.0")
        without = codex["help"].replace("  resume  Resume a previous session", "  rezume  Resume a previous session")
        self.assertEqual(sessions.capability_of("codex", {**codex, "help": without}).resume_by_id, Support.UNSUPPORTED)
        self.assertEqual(sessions.unknown("codex").status, Status.UNKNOWN)

    def test_line_ends_and_description_lines_do_not_change_what_is_read(self):
        claude = texts("claude-2.1.295")
        crlf = {name: value.replace("\n", "\r\n") for name, value in claude.items()}
        self.assertEqual(sessions.capability_of("claude", crlf).matrix(), capability("claude-2.1.295").matrix()
                         | {"evidence": "help"})
        # An option named in a description (indented further) is not an option of the command.
        self.assertEqual(sessions.options_of("  --real <x>   text\n                --named-in-text\n"),
                         frozenset({"--real"}))

    def test_the_probes_are_model_free_exact_commands_refused_while_simulated(self):
        for provider, commands in sessions.PROBE_COMMANDS.items():
            for argv in commands.values():
                self.assertIn(argv, providers.PROBES)
                self.assertTrue(argv[-1] in ("--help", "--version"), argv)
        calls = []
        with patch("patchrondo.providers.execute", side_effect=lambda *a, **k: calls.append(a)):
            found = providers.OfficialCLI().resume_capability("claude")
        self.assertEqual((found.status, calls), (Status.UNKNOWN, []))

    def test_the_installed_cli_is_asked_once_and_read_from_its_own_help(self):
        answers = {argv: (texts("claude-2.1.295")[name] if argv[0] == "claude" else texts("codex-0.161.0")[name])
                   for provider, commands in sessions.PROBE_COMMANDS.items() for name, argv in commands.items()}
        asked = []

        def runner(argv, cwd, *, timeout, **_):
            asked.append(tuple(argv))
            if tuple(argv) not in answers:
                raise AssertionError(f"not a probe: {argv}")
            return Result(0, answers[tuple(argv)], "")

        # Both names replaced by the same function: the adapter is not simulated, and still no real CLI can start.
        with patch("patchrondo.providers.execute", runner), patch("patchrondo.providers._execute", runner), \
                patch("patchrondo.providers.shutil.which", return_value=None):
            adapter = providers.OfficialCLI()
            claude = adapter.resume_capability("claude")
            self.assertIs(adapter.resume_capability("claude"), claude)
            codex = adapter.resume_capability("codex")
        self.assertEqual((claude.status, claude.evidence, codex.status), (Status.VERIFIED, sessions.Evidence.HELP,
                                                                          Status.UNVERIFIED))
        self.assertEqual(len(asked), 5)  # two questions for Claude, three for Codex, each once

        def failing(argv, cwd, *, timeout, **_):
            return Result(1, "", "boom")

        with patch("patchrondo.providers.execute", failing), patch("patchrondo.providers._execute", failing):
            self.assertEqual(providers.OfficialCLI().resume_capability("claude").status, Status.UNKNOWN)


class CaptureTests(unittest.TestCase):
    def test_one_valid_session_or_none(self):
        self.assertEqual(sessions.captured([A, A]), sessions.Captured(A))
        self.assertEqual(sessions.captured([]).problem, "missing")
        for values in ([A, B], [A, A, B]):
            self.assertEqual(sessions.captured(values), sessions.Captured(None, "contradictory"))
        for values in ([A.upper()], ["synthetic"], [None], [42], [A + " "], [A, "x"], ["--last"]):
            self.assertEqual(sessions.captured(values), sessions.Captured(None, "malformed"), values)

    def feed(self, provider: str, role: str, *lines: str) -> streams.Stream:
        stream = streams.open_stream(provider, role, Path.cwd(), lambda *a, **k: None)
        stream.feed("".join(line + "\n" for line in lines).encode("utf-8"))
        stream.close()
        return stream

    def test_only_the_typed_fields_of_the_cli_report_a_session_and_logs_mask_them(self):
        prose = f'Note: "session_id": "{B}"'
        claude = self.feed(
            "claude", "developer",
            f'{{"type":"system","subtype":"init","session_id":"{A}","model":"m","tools":[]}}',
            f'{{"type":"assistant","message":{{"content":[{{"type":"text","text":{prose!r}}}]}}}}'.replace("'", '"'),
            f'{{"type":"user","message":{{"content":[{{"type":"tool_result","tool_use_id":"t","content":"{B}"}}]}}}}',
            f'{{"type":"system","subtype":"hook","session_id":"{B}"}}',
            f'{{"type":"result","subtype":"success","is_error":false,"result":"done","session_id":"{A}"}}',
            f'plain text "session_id": "{B}"')
        self.assertEqual(claude.sessions, [A, A])
        self.assertNotIn(A, claude.transcript.text())
        self.assertIn(sessions.MASK, claude.transcript.text())
        codex = self.feed("codex", "reviewer", f'{{"type":"thread.started","thread_id":"{A}"}}',
                          f'{{"type":"item.completed","item":{{"id":"m","type":"agent_message","text":"{B}"}}}}')
        self.assertEqual(codex.sessions, [A])
        self.assertNotIn(A, codex.transcript.text())
        self.assertEqual(sessions.from_output("claude", f'{{"type":"result","session_id":"{A}"}}'), [A])
        self.assertEqual(sessions.from_output("codex", f'{{"thread_id":"{A}"}}'), [])
        self.assertEqual(sessions.mask(f'{{"session_id": "{A}", "x": 1}}'), f'{{"session_id": "{sessions.MASK}", "x": 1}}')

    def test_a_reference_is_bound_to_its_call_and_names_nothing(self):
        ref = sessions.reference("T-0123456789ab", "CX-0001", "claude", "developer", A)
        self.assertRegex(ref, sessions.REFERENCE)
        self.assertNotIn(A[:8], ref)
        for other in (("T-0123456789ac", "CX-0001", "claude", "developer", A),
                      ("T-0123456789ab", "CX-0002", "claude", "developer", A),
                      ("T-0123456789ab", "CX-0001", "codex", "developer", A),
                      ("T-0123456789ab", "CX-0001", "claude", "reviewer", A),
                      ("T-0123456789ab", "CX-0001", "claude", "developer", B)):
            self.assertNotEqual(sessions.reference(*other), ref, other)

    def test_durable_only_as_the_user_set_it(self):
        self.assertTrue(sessions.durable("claude", {}))
        self.assertFalse(sessions.durable("claude", {"session_persistence": False}))
        self.assertFalse(sessions.durable("codex", {}))  # --ephemeral, PatchRondo's default, is never switched off
        self.assertTrue(sessions.durable("codex", {"ephemeral": False}))


TASK = "T-0123456789ab"


def entry(session=A, **changes) -> dict:
    found = sessions.stored(task_id=TASK, attempt_id="CX-0001", provider="claude", role="developer", session_id=session,
                            capability=capability("claude-2.1.295"))
    return {**found, **changes}


class SelectTests(unittest.TestCase):
    def options(self, **changes) -> dict:
        base = {"requested": sessions.REQUESTED, "provider": "claude", "role": "developer", "live": False,
                "resumable": True, "settings": {}, "live_output": True, "trigger_session": entry()["ref"],
                "trigger_digest": "0123456789abcdef", "current_digest": "0123456789abcdef", "entry": entry(),
                "capability": lambda: capability("claude-2.1.295"), "task_id": TASK, "trigger_attempt": "CX-0001"}
        return {**base, **changes}

    def reason(self, **changes):
        selection, resume = sessions.select(**self.options(**changes))
        self.assertIsNone(resume)
        return selection.reason

    def test_native_resume_only_when_every_condition_holds(self):
        selection, resume = sessions.select(**self.options())
        self.assertTrue(selection.native)
        self.assertEqual((selection.capability, selection.cli_version, selection.ref), (Status.VERIFIED, "2.1.295",
                                                                                        entry()["ref"]))
        self.assertEqual((resume.session_id, resume.provider, resume.role), (A, "claude", "developer"))
        self.assertNotIn(A, repr(resume))
        self.assertNotIn(A, str(selection.event()))

    def test_not_asked_for_changes_nothing(self):
        self.assertEqual(sessions.select(**self.options(requested="off")), (None, None))

    def test_each_reason_keeps_the_checkpoint_resume(self):
        later = lambda: replace(capability("claude-2.1.295"), version="2.1.302")  # noqa: E731
        cases = [
            (Unavailable.NOT_VALIDATED, {"live": True}),
            (Unavailable.ADAPTER_CANNOT_RESUME, {"resumable": False}),
            (Unavailable.NOT_DURABLE, {"settings": {"session_persistence": False}}),
            (Unavailable.NOT_DURABLE, {"provider": "codex", "settings": {}}),
            (Unavailable.SESSION_UNCONFIRMABLE, {"provider": "codex", "settings": {"ephemeral": False},
                                                 "live_output": False}),
            (Unavailable.NO_SESSION, {"trigger_session": None}),
            (Unavailable.SESSION_UNREADABLE, {"entry": None}),
            (Unavailable.SESSION_UNREADABLE, {"entry_problem": "launch record unreadable"}),
            (Unavailable.SESSION_UNREADABLE, {"entry": {**entry(), "id": "not-a-uuid"}}),
            (Unavailable.SESSION_UNREADABLE, {"entry": {**entry(), "extra": 1}}),
            (Unavailable.SESSION_MISMATCH, {"entry": {**entry(), "id": B}}),  # another ID under the same reference
            (Unavailable.SESSION_MISMATCH, {"trigger_attempt": "CX-0002"}),
            (Unavailable.SESSION_MISMATCH, {"role": "reviewer"}),
            (Unavailable.SESSION_MISMATCH, {"task_id": "T-0123456789ac"}),
            (Unavailable.CAPABILITY_UNKNOWN, {"capability": lambda: sessions.unknown("claude")}),
            (Unavailable.CAPABILITY_UNKNOWN, {"capability": lambda: 1 / 0}),
            (Unavailable.CAPABILITY_UNKNOWN, {"capability": lambda: capability("codex-0.161.0")}),  # another provider
            (Unavailable.RESUME_UNSUPPORTED, {"capability": lambda: replace(capability("claude-2.1.295"),
                                                                            resume_by_id=Support.UNSUPPORTED)}),
            (Unavailable.PERSISTENCE_UNKNOWN, {"capability": lambda: replace(capability("claude-2.1.295"),
                                                                             persistence=Support.UNKNOWN)}),
            (Unavailable.RESTRICTIONS_UNVERIFIED, {"capability": lambda: replace(capability("claude-2.1.295"),
                                                                                 restrictions=Support.UNKNOWN)}),
            (Unavailable.OUTPUT_UNVERIFIED, {"capability": lambda: replace(capability("claude-2.1.295"),
                                                                           output_validation=Support.UNKNOWN)}),
            (Unavailable.CLI_CHANGED, {"capability": later}),
            (Unavailable.CLI_CHANGED, {"entry": entry(cli_version=None)}),
            (Unavailable.SETTINGS_CHANGED, {"current_digest": "fedcba9876543210"}),
        ]
        for reason, changes in cases:
            with self.subTest(reason=reason, changes=sorted(changes)):
                self.assertEqual(self.reason(**changes), reason)

    def test_a_codex_session_on_the_observed_versions_is_never_resumed(self):
        stored = sessions.stored(task_id=TASK, attempt_id="CX-0001", provider="codex", role="reviewer", session_id=A,
                                 capability=capability("codex-0.161.0"))
        for folder in ("codex-0.161.0", "codex-0.160.1"):
            with self.subTest(folder=folder):
                self.assertEqual(self.reason(provider="codex", role="reviewer", settings={"ephemeral": False},
                                             trigger_session=stored["ref"], entry=stored,
                                             capability=lambda: capability(folder)),
                                 Unavailable.RESTRICTIONS_UNVERIFIED)

    def test_the_installed_cli_is_asked_only_when_everything_before_holds(self):
        asked = []

        def probe():
            asked.append(1)
            return capability("claude-2.1.295")

        for changes in ({"live": True}, {"settings": {"session_persistence": False}}, {"trigger_session": None},
                        {"entry": None}, {"requested": "off"}):
            sessions.select(**self.options(capability=probe, **changes))
        self.assertEqual(asked, [])
        sessions.select(**self.options(capability=probe))
        self.assertEqual(asked, [1])

    def test_no_real_cli_is_resumed_by_this_version(self):
        self.assertEqual(sessions.VALIDATED_PROVIDERS, frozenset())
        for provider in ("claude", "codex"):
            self.assertEqual(self.reason(live=True, provider=provider), Unavailable.NOT_VALIDATED)


class CommandTests(unittest.TestCase):
    def build(self, provider="claude", role="developer", values=None, session=A, found=None, live=True):
        return agents.resume_command(provider, role, values or {}, session_id=session,
                                     capability=found or capability("claude-2.1.295"), workspace=Path("/work"),
                                     live=live, turns=12, schema="SCHEMA", last_message="/run/last.txt")

    def test_claude_resumes_with_the_steps_own_command(self):
        # Settings as `agents.clean_block` saves them for each role: the reviewer's are narrowed there.
        settings = {"developer": agents.clean_block("claude", "developer", {
                        "model": "opus", "permission_mode": "acceptEdits", "tools": ["Read", "Edit", "Bash"],
                        "max_turns": 7, "risk_acknowledged": True}),
                    "reviewer": agents.clean_block("claude", "reviewer", {"model": "opus", "permission_mode": "plan"})}
        for role, values in settings.items():
            for live in (True, False):
                with self.subTest(role=role, live=live):
                    base = agents.command("claude", role, values, workspace=Path("/work"), live=live, turns=12,
                                          schema="SCHEMA", last_message="/run/last.txt")
                    argv = self.build(role=role, values=values, live=live)
                    self.assertEqual(argv, base[:2] + ["--resume", A] + base[2:])
                    self.assertFalse(agents.NEVER_ON_RESUME["claude"] & set(argv))
        developer = self.build(values=settings["developer"])
        self.assertEqual((developer[developer.index("--tools") + 1], developer[developer.index("--permission-mode") + 1],
                          developer[developer.index("--max-turns") + 1]), ("Read,Edit,Bash", "acceptEdits", "7"))
        # The reviewer keeps its reading tools whatever its settings say: `command` and the resume form both enforce it.
        reviewer = self.build(role="reviewer", values={**settings["reviewer"], "tools": ["Read", "Edit", "Bash"]})
        self.assertEqual(reviewer[reviewer.index("--tools") + 1], "Read,Glob,Grep")
        self.assertEqual(reviewer[reviewer.index("--permission-mode") + 1], "plan")
        self.assertIn("--json-schema", reviewer)
        self.assertIn("--restricted", reviewer)

    def test_no_session_but_the_exact_one(self):
        for session in ("", A.upper(), " " + A, A + "\n", "--last", "latest", f"{A} --fork-session", None, 7):
            with self.subTest(session=session), self.assertRaises(ValueError):
                self.build(session=session)

    def test_an_unverified_or_foreign_capability_builds_nothing(self):
        for found in (capability("codex-0.161.0"), sessions.unknown("claude"),
                      replace(capability("claude-2.1.295"), restrictions=Support.UNKNOWN)):
            with self.subTest(found=found.matrix()), self.assertRaises(ValueError):
                self.build(found=found)
        with self.assertRaises(ValueError):
            self.build(provider="codex", values={"ephemeral": False}, found=capability("codex-0.161.0"))

    def test_options_the_resume_form_does_not_list_are_never_passed(self):
        with self.assertRaises(ValueError):
            self.build(values={"extra_args": ["--frobnicate"], "risk_acknowledged": True})
        self.assertIn("--verbose", self.build(values={"extra_args": ["--verbose"], "risk_acknowledged": True}))
        # Options the binary lists that pick another session or make a new one: never in a resume, however given.
        for blocked in (["--continue"], ["--fork-session"], ["--session-id", B]):
            with self.subTest(blocked=blocked), self.assertRaises(ValueError):
                self.build(values={"extra_args": blocked, "risk_acknowledged": True})

    def test_codex_resume_form_places_options_after_resume_and_keeps_the_sandbox(self):
        found = synthetic_codex()
        self.assertEqual(found.status, Status.VERIFIED)
        argv = self.build("codex", "developer", {"ephemeral": False, "model": "gpt-x"}, found=found)
        self.assertEqual(argv[:5], ["codex", "--ask-for-approval", "never", "exec", "resume"])
        self.assertEqual(argv[-2:], [A, "-"])
        self.assertEqual(argv[argv.index("--sandbox") + 1], "workspace-write")
        self.assertNotIn("--cd", argv)  # the call runs in the worktree: the resume form takes no --cd
        self.assertLessEqual({"--json", "--output-last-message", "--model"}, set(argv))
        reviewer = self.build("codex", "reviewer", {"ephemeral": False, "sandbox": "danger-full-access",
                                                    "risk_acknowledged": True}, found=found)
        self.assertEqual(reviewer[reviewer.index("--sandbox") + 1], "read-only")
        self.assertIn("--output-schema", reviewer)
        for values in ({}, {"ephemeral": True}, {"ephemeral": False, "add_dirs": [str(Path.cwd())]},
                       {"ephemeral": False, "profile": "p"}, {"ephemeral": False, "extra_args": ["--x"],
                                                             "risk_acknowledged": True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.build("codex", "developer", values, found=found)
        self.assertFalse(agents.NEVER_ON_RESUME["codex"] & set(argv))

    def test_the_command_of_a_new_call_is_unchanged(self):
        # `agents.command` is not touched by a resume: the same arguments as before Step 10 (`test_agents` pins them).
        self.assertNotIn("--resume", agents.command("claude", "developer", {}, workspace=Path("/w"), live=True))
        self.assertNotIn("resume", agents.command("codex", "developer", {}, workspace=Path("/w"), live=True))
        self.assertIn("--resume", agents.ARGS_BLOCKED["claude"])
        self.assertLessEqual({"--continue", "--fork-session", "--session-id"}, agents.ARGS_BLOCKED["claude"])


class AdapterTests(unittest.TestCase):
    def invoke(self, role="developer", resume=None) -> tuple[list, BaseException | None]:
        calls = []

        def refuse(argv, cwd, **_):
            calls.append(list(argv))
            return Result(1, "", "error: unknown option '--verbose'\n")

        with patch("patchrondo.providers.execute", side_effect=refuse), tempfile.TemporaryDirectory() as folder:
            try:
                providers.OfficialCLI().invoke("claude", role, "prompt", Path(folder), Path(folder) / "run", resume=resume)
            except providers.AgentFailure as exc:
                return calls, exc
        return calls, None

    def test_a_resume_its_cli_refuses_is_never_started_again(self):
        resume = sessions.Resume("claude", "developer", A, capability("claude-2.1.295"))
        calls, failure = self.invoke(resume=resume)
        self.assertEqual((len(calls), failure.kind), (1, "unsupported_option"))
        self.assertEqual(calls[0][:4], ["claude", "-p", "--resume", A])
        # A new call keeps its one fallback without the streaming options, as before Step 10.
        calls, failure = self.invoke()
        self.assertEqual((len(calls), failure.kind), (2, "unsupported_option"))
        self.assertNotIn("stream-json", calls[1])

    def test_a_session_is_resumed_only_by_its_provider_and_role(self):
        with self.assertRaises(ValueError):
            self.invoke(role="reviewer", resume=sessions.Resume("claude", "developer", A, capability("claude-2.1.295")))
        with self.assertRaises(ValueError):
            self.invoke(resume=sessions.Resume("codex", "developer", A, capability("codex-0.161.0")))


def opened() -> ExecutionRecord:
    record = ExecutionRecord.open(TASK, {Unit.PROVIDER_CALLS: 10})
    return record.begin_step(1, Phase.DEVELOP, "claude", {Unit.PROVIDER_CALLS: 4},
                             CallLimits(40, Source.PROFILE, 600, Source.PROFILE), ExecutionProfile.STANDARD)


def ended(record: ExecutionRecord, outcome=AttemptOutcome.TURN_LIMIT_REACHED, kind="max_turns", session=None):
    item = record.step.last
    done = item.end(outcome, ended_at=item.started_at, elapsed_seconds=10, termination=Termination.EXITED,
                    failure_kind=kind, turns=Usage(Unit.CLAUDE_TURNS, execution.Availability.OBSERVED, 41),
                    session=session)
    return record.update(done)


NATIVE = ResumeChoice(ContinuationMode.NATIVE_RESUME, "verified", "2.1.295", True, None)
CHECKPOINT = ResumeChoice(ContinuationMode.CHECKPOINT_RESUME, "unknown", None, True, "capability_unknown")
REF = entry()["ref"]


class RecordTests(unittest.TestCase):
    def test_a_resume_choice_is_consistent(self):
        for bad in ((ContinuationMode.NONE, "verified", "2.1.295", True, None),
                    (ContinuationMode.NATIVE_RESUME, "verified", "2.1.295", True, "not_validated"),
                    (ContinuationMode.NATIVE_RESUME, "unverified", "2.1.295", True, None),
                    (ContinuationMode.NATIVE_RESUME, "verified", None, True, None),
                    (ContinuationMode.NATIVE_RESUME, "verified", "2.1.295", False, None),
                    (ContinuationMode.CHECKPOINT_RESUME, "verified", "2.1.295", True, None),
                    (ContinuationMode.CHECKPOINT_RESUME, "maybe", None, None, "x"),
                    (ContinuationMode.CHECKPOINT_RESUME, "unknown", None, 1, "x"),
                    (ContinuationMode.CHECKPOINT_RESUME, "unknown", "latest", None, "x")):
            with self.subTest(bad=bad), self.assertRaises(InvalidRecord):
                ResumeChoice(*bad)
        self.assertTrue(CHECKPOINT.fallback)
        self.assertFalse(NATIVE.fallback)
        self.assertEqual(ResumeChoice.from_dict(NATIVE.to_dict(), "choice"), NATIVE)
        with self.assertRaises(InvalidRecord):
            ResumeChoice.from_dict({**NATIVE.to_dict(), "requested": "checkpoint_resume"}, "choice")

    def test_a_native_resume_resumes_the_session_its_steps_last_call_kept(self):
        record, _ = opened().new_attempt(started_at="2026-11-10T10:00:00+00:00")
        record = ended(record, session=REF)
        self.assertEqual(record.step.last.session, REF)
        smaller = CallLimits(39, Source.PROFILE, 600, Source.PROFILE)
        for session in (None, "S-" + "0" * 24):
            with self.subTest(session=session), self.assertRaises(InvalidRecord):
                record.new_attempt(started_at="2026-11-10T10:01:00+00:00", continuation=ContinuationMode.NATIVE_RESUME,
                                   session=session, call=smaller, resume=NATIVE)
        with self.assertRaises(InvalidRecord):  # a native resume always records its choice
            record.new_attempt(started_at="2026-11-10T10:01:00+00:00", continuation=ContinuationMode.NATIVE_RESUME,
                               session=REF, call=smaller)
        native, item = record.new_attempt(started_at="2026-11-10T10:01:00+00:00",
                                          continuation=ContinuationMode.NATIVE_RESUME, session=REF, call=smaller,
                                          resume=NATIVE)
        self.assertEqual((item.continuation, item.session, item.resume, item.to_dict()["version"]),
                         (ContinuationMode.NATIVE_RESUME, REF, NATIVE, 3))
        self.assertEqual(execution.AttemptRecord.from_dict(item.to_dict()), item)
        # Charged as any continuation: one call, its limits.
        self.assertEqual(native.budget.line(Unit.PROVIDER_CALLS).used, 2)
        with self.assertRaises(InvalidRecord):
            native.update(replace(native.step.last, session="S-" + "1" * 24))
        with self.assertRaises(InvalidRecord):
            native.step.last.end(AttemptOutcome.COMPLETED, ended_at=item.started_at, elapsed_seconds=1,
                                 termination=Termination.EXITED, session="S-" + "1" * 24)
        # One continuation per step, native or not.
        finished = ended(native)
        self.assertEqual(execution.next_call(replace(finished, policy=None)).declined,
                         execution.Declined.POLICY_UNBOUND)
        policy = {"enabled": True, "profile": "standard", "adaptive": True,
                  "continuation": {"native_resume": "probe_then_use"}}
        self.assertEqual(execution.next_call(replace(finished, policy=policy)).declined,
                         execution.Declined.LIMIT_REACHED)

    def test_a_native_resume_that_never_started_leaves_the_call_it_follows_last(self):
        record, _ = opened().new_attempt(started_at="2026-11-10T10:00:00+00:00")
        record = ended(record, session=REF)
        record, item = record.new_attempt(started_at="2026-11-10T10:01:00+00:00",
                                          continuation=ContinuationMode.NATIVE_RESUME, session=REF,
                                          call=CallLimits(39, Source.PROFILE, 600, Source.PROFILE), resume=NATIVE)
        lost = item.end(AttemptOutcome.INTERRUPTED, ended_at=item.started_at, elapsed_seconds=0,
                        termination=Termination.NOT_STARTED,
                        turns=Usage(Unit.CLAUDE_TURNS, execution.Availability.OBSERVED, 0),
                        tokens=Usage(Unit.TOKENS, execution.Availability.OBSERVED, 0))
        record = record.update(lost)
        self.assertEqual(record.step.last_call.attempt_id, "CX-0001")

    def test_shapes_and_versions(self):
        record, item = opened().new_attempt(started_at="2026-11-10T10:00:00+00:00")
        self.assertEqual(item.to_dict()["version"], 1)  # a call that is not a recorded continuation keeps schema 1
        raw = item.to_dict()
        for bad in ({**raw, "session": REF},  # its own session is known once it ended
                    {**raw, "version": 3, "resume": None},
                    {**raw, "version": 3, "resume": NATIVE.to_dict()},  # the choice selects its continuation mode
                    {**raw, "version": 4, "resume": None}):
            with self.subTest(bad=sorted(bad.items())[:2]), self.assertRaises(InvalidRecord):
                execution.AttemptRecord.from_dict(bad)
        with self.assertRaises(InvalidRecord) as caught:
            execution.AttemptRecord.from_dict({**raw, "version": 4, "resume": None})
        self.assertTrue(caught.exception.newer)
        self.assertEqual(execution.OUTCOME_OF_KIND["resume_unconfirmed"], AttemptOutcome.UNSUPPORTED_RESUME)
        self.assertEqual(AttemptStatus.STARTING, record.step.last.status)


if __name__ == "__main__":
    unittest.main()
