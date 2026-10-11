"""Reviewer preflight with a scripted sandbox: what is asked, how answers are judged, what is left on disk.

No Codex CLI is started here. `tests/test_sandbox_real.py` runs the same probe
in the real sandbox when that is asked for.
"""
import base64
import os
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import patch

from patchrondo import preflight, providers

HELP = """Run commands within a Codex-provided sandbox

Usage: codex sandbox [OPTIONS] [COMMAND]...

Options:
  -P, --permission-profile <NAME>
  -C, --cd <DIR>
      --include-managed-config
"""
HEAD = "0123456789abcdef0123456789abcdef01234567"


def literal(text: str, name: str) -> str:
    """The value of a quoted PowerShell literal in the probe, as PowerShell would read it."""
    match = re.search(name + r"'((?:[^']|'')*)'", text)
    return match.group(1).replace("''", "'") if match else ""


class ScriptedSandbox:
    """Answers like the sandbox would. `leaks` makes it really write, as a sandbox that is not read-only."""

    def __init__(self, help_text=HELP, leaks=(), **answers):
        self.help_text, self.leaks, self.answers = help_text, set(leaks), answers
        self.calls: list[list[str]] = []

    def __call__(self, argv, cwd, timeout):
        self.calls.append(list(argv))
        if argv[1:] == ["sandbox", "--help"]:
            return (0 if self.help_text else 2), self.help_text.encode(), "", False
        command = argv[argv.index("--") + 1:]
        if command[0] == "cmd.exe":
            shown = self.answers.get("process_cwd", argv[argv.index("-C") + 1])
            return 0, (shown + "\r\n").encode("utf-16-le"), "", False
        text = base64.b64decode(command[-1]).decode("utf-16-le")
        token = re.search(r"'PR begin=(\w+)'", text).group(1)
        canary, fresh = literal(text, r"\$canary = "), literal(text, r"Set-Content -LiteralPath ")
        if "crash" in self.answers:
            return 1, b"", self.answers["crash"], False
        if "timeout" in self.answers:
            return 124, f"PR begin={token}\n".encode(), "", True
        if "overwrite" in self.leaks and canary:
            Path(canary).write_text("tampered\n", encoding="ascii")
        if "delete" in self.leaks and canary:
            Path(canary).unlink()
        if "create" in self.leaks:
            Path(fresh).write_text("created\n", encoding="ascii")
        said = {"begin": token, "language": "ConstrainedLanguage", "cwd_ok": "True", "cwd": "the worktree", "abs_ok": "True",
                "rel_ok": "True", "git_exit": "0", "git_head": HEAD, "status_exit": "0", "status": "",
                "canary_seen": "True", "overwrite": "refused", "delete": "refused", "create": "refused", "end": token}
        said.update({key: value for key, value in self.answers.items() if key != "process_cwd"})
        lines = [f"PR {key}={value}" for key, value in said.items() if value is not None]
        return 0, ("\r\n".join(lines) + "\r\n").encode("utf-8"), "#< CLIXML\n<Objs Version=\"1.1.0.1\"></Objs>", False


class ProbeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve() / "state"
        self.worktree = self.home / "worktrees" / "T-0123456789ab"
        self.worktree.mkdir(parents=True)
        (self.worktree / ".git").write_text("gitdir: /repo/.git/worktrees/T-0123456789ab\n", encoding="utf-8")

    def sandbox(self, **options) -> preflight.Sandbox:
        self.run = ScriptedSandbox(**options)
        return preflight.Sandbox(run=self.run, which=lambda name: "/synthetic/codex", system="nt")

    def judge(self, **options):
        checks, kind = preflight.evaluate(self.sandbox(**options).probe(self.home, self.worktree, HEAD))
        return {check["id"]: check["status"] for check in checks}, kind

    def leftovers(self) -> list[str]:
        return sorted(str(path.relative_to(self.home)) for path in self.home.rglob(f"{preflight.FIXTURE}*"))


class QuotingTests(unittest.TestCase):
    def test_a_path_is_passed_as_a_literal_whatever_it_contains(self):
        self.assertEqual(preflight.quote(r"C:\plain path"), r"'C:\plain path'")
        self.assertEqual(preflight.quote("it's $env:X `n \"q\" %T% & | ; (x)"), "'it''s $env:X `n \"q\" %T% & | ; (x)'")
        # PowerShell also ends a literal at the typographic single quotes: they are doubled too.
        self.assertEqual(preflight.quote("a\u2018b\u2019c\u201ad\u201be"), "'a\u2018\u2018b\u2019\u2019c\u201a\u201ad\u201b\u201be'")
        for text in ("line\nbreak", "tab\tstop", "nul\0"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                preflight.quote(text)

    def test_hostile_names_cannot_leave_their_literal(self):
        hostile = r"C:\wt'; Remove-Item C:\ -Recurse; '"
        text = preflight.script(root=hostile, marker=hostile + r"\.git", expected="gitdir: x'; exit; '", canary=hostile + r"\c.txt",
                                fresh=hostile + r"\f.tmp", token="abc123")
        self.assertNotIn("wt'; Remove-Item", text)
        self.assertIn("wt''; Remove-Item", text)
        # Outside quoted literals nothing of the hostile text remains.
        self.assertNotIn("Remove-Item C:", re.sub(r"'(?:[^']|'')*'", "''", text))
        self.assertEqual(base64.b64decode(preflight.encode(text)).decode("utf-16-le"), text)
        self.assertRegex(preflight.encode(text), r"^[A-Za-z0-9+/=]+$")

    def test_the_probe_uses_only_what_constrained_language_allows(self):
        text = preflight.script(root="r", marker="m", expected="e", canary="c", fresh="f", token="t")
        self.assertNotRegex(text, r"\[[A-Za-z.]+\]::")  # no .NET type is called
        self.assertNotRegex(text, r"\.\w+\(")           # no method is called


class EvaluationTests(ProbeCase):
    def test_a_working_sandbox_passes_every_check(self):
        states, kind = self.judge()
        self.assertIsNone(kind)
        self.assertEqual(list(states), list(preflight.CHECKS))
        self.assertEqual(set(states.values()), {"passed"})
        self.assertEqual(self.leftovers(), [])

    def test_the_sandbox_is_asked_through_arguments_never_through_a_shell_string(self):
        self.judge()
        shell, process = self.run.calls
        root = os.path.realpath(self.worktree)
        self.assertEqual(shell[:8], ["codex", "sandbox", "-P", ":read-only", "--include-managed-config", "-C", root, "--"])
        self.assertEqual(shell[8:12], ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand"])
        self.assertRegex(shell[12], r"^[A-Za-z0-9+/=]+$")
        self.assertEqual(process[8:], ["cmd.exe", "/d", "/u", "/c", "cd"])
        # Never a weaker profile, never a bypass.
        for call in self.run.calls:
            self.assertFalse({"danger-full-access", "workspace-write", "--dangerously-bypass-approvals-and-sandbox"} & set(call))

    def test_reading_denied_is_an_access_problem(self):
        states, kind = self.judge(abs_ok="False", rel_ok="False", cwd_ok="False", git_exit="128",
                                  git_head="fatal: cannot change to 'x': Permission denied", status_exit="128",
                                  canary_seen="False")
        self.assertEqual(kind, "sandbox_access_denied")
        self.assertEqual((states["read.absolute"], states["shell.cwd"], states["write.canary"]), ("failed", "failed", "skipped"))

    def test_a_wrong_working_directory_is_its_own_problem(self):
        for answers in ({"cwd_ok": "False", "cwd": "C:\\", "rel_ok": "False"}, {"rel_ok": "False"},
                        {"process_cwd": "C:\\Windows\\System32"}):
            with self.subTest(answers=answers):
                states, kind = self.judge(**answers)
                self.assertEqual((kind, states["read.absolute"]), ("sandbox_cwd_unavailable", "passed"))
        # A process directory that could not be read is not a failure by itself.
        states, kind = self.judge(process_cwd="")
        self.assertEqual((kind, states["process.cwd"]), (None, "skipped"))

    def test_a_write_that_happened_blocks_whatever_the_probe_said(self):
        for leak in ("overwrite", "delete", "create"):
            with self.subTest(leak=leak):
                states, kind = self.judge(leaks=[leak])  # the probe still prints "refused"
                self.assertEqual(kind, "sandbox_not_read_only")
                self.assertEqual(states["write.create" if leak == "create" else "write.canary"], "failed")
                self.assertEqual(self.leftovers(), [])
                self.assertEqual(sorted(path.name for path in self.worktree.iterdir()), [".git"])

    def test_a_command_that_failed_is_not_proof_that_writing_is_refused(self):
        # Nothing was attempted and the canary was not even visible: nothing proves the protection.
        states, kind = self.judge(canary_seen="False", overwrite=None, delete=None, create=None)
        self.assertEqual((kind, states["write.canary"], states["write.create"]), ("sandbox_unverified", "skipped", "skipped"))
        # One demonstrated refusal is enough, and it is the file on disk that demonstrates it.
        states, kind = self.judge(canary_seen="False")
        self.assertEqual((kind, states["write.canary"], states["write.create"]), (None, "skipped", "passed"))

    def test_git_failures_are_told_apart(self):
        dubious = "fatal: detected dubious ownership in repository at 'C:/x'"
        self.assertEqual(self.judge(git_exit="128", git_head=dubious, status_exit="128", status=dubious)[1],
                         "git_dubious_ownership")
        self.assertEqual(self.judge(git_exit="128", git_head="fatal: not a git repository")[1], "sandbox_git_failed")
        self.assertEqual(self.judge(status_exit="128", status="fatal: index file open failed")[1], "sandbox_git_failed")
        self.assertEqual(self.judge(git_exit="missing", git_head="git is not recognized")[1], "sandbox_git_failed")
        # Git answered, but for another commit than the one outside the sandbox.
        self.assertEqual(self.judge(git_head="f" * 40)[1], "sandbox_git_failed")

    def test_a_sandbox_that_does_not_run_the_probe_is_unavailable(self):
        states, kind = self.judge(crash="windows sandbox failed: CreateProcessAsUserW failed: 5")
        self.assertEqual((kind, states["sandbox.start"]), ("sandbox_unavailable", "failed"))
        self.assertEqual(set(list(states.values())[1:]), {"skipped"})
        self.assertEqual(len(self.run.calls), 1)  # no second command after a probe that did not complete
        self.assertEqual(self.judge(timeout=True)[1], "sandbox_unavailable")
        self.assertEqual(self.leftovers(), [])

    def test_a_worktree_outside_the_managed_folder_gets_no_fixture(self):
        elsewhere = Path(self.tmp.name).resolve() / "elsewhere" / "T-0123456789ab"
        elsewhere.mkdir(parents=True)
        (elsewhere / ".git").write_text("gitdir: /repo/.git/worktrees/x\n", encoding="utf-8")
        checks, kind = preflight.evaluate(self.sandbox().probe(self.home, elsewhere, HEAD))
        states = {check["id"]: check["status"] for check in checks}
        self.assertEqual((kind, states["write.canary"], states["write.create"]), (None, "skipped", "passed"))
        self.assertEqual(sorted(path.name for path in elsewhere.parent.iterdir()), ["T-0123456789ab"])
        self.assertEqual(self.leftovers(), [])

    def test_fixtures_of_a_probe_that_died_are_removed(self):
        stale = self.home / "worktrees" / f"{preflight.FIXTURE}dead"
        stale.mkdir()
        (stale / "canary.txt").write_text("x", encoding="ascii")
        old = time.time() - preflight.STALE_SECONDS - 60
        os.utime(stale, (old, old))
        recent = self.home / "worktrees" / f"{preflight.FIXTURE}busy"
        recent.mkdir()
        self.judge()
        self.assertEqual(self.leftovers(), [os.path.join("worktrees", f"{preflight.FIXTURE}busy")])


class AvailabilityTests(ProbeCase):
    def test_only_a_cli_whose_help_lists_the_options_is_probed(self):
        self.assertIsNone(self.sandbox().unavailable(self.worktree))
        old = self.sandbox(help_text="Usage: codex sandbox <COMMAND>\n\nCommands:\n  windows  Run under Windows sandbox\n")
        self.assertIn("does not list the options", old.unavailable(self.worktree))
        self.assertIn("does not list the options", old.unavailable(self.worktree))
        self.assertEqual(len(self.run.calls), 1)  # the help is read once per installed CLI
        self.assertIn("no sandbox self-check", self.sandbox(help_text="").unavailable(self.worktree))
        self.assertTrue(preflight.supported(HELP))
        self.assertFalse(preflight.supported(HELP.replace("--include-managed-config", "")))

    def test_other_systems_and_a_missing_cli_are_not_probed(self):
        run = ScriptedSandbox()
        self.assertIn("Windows only", preflight.Sandbox(run=run, which=lambda name: "/x/codex", system="posix").unavailable(self.worktree))
        self.assertIn("not found", preflight.Sandbox(run=run, which=lambda name: None, system="nt").unavailable(self.worktree))
        self.assertEqual(run.calls, [])
        self.assertIsNone(preflight.Sandbox(run=run, which=lambda name: None, system="nt").identity())

    def test_a_new_cli_is_asked_again(self):
        where = [str(self.worktree / ".git")]
        run = ScriptedSandbox()
        sandbox = preflight.Sandbox(run=run, which=lambda name: where[0], system="nt")
        sandbox.unavailable(self.worktree)
        sandbox.unavailable(self.worktree)
        (self.worktree / "codex2").write_text("newer", encoding="utf-8")
        where[0] = str(self.worktree / "codex2")
        sandbox.unavailable(self.worktree)
        self.assertEqual(len(run.calls), 2)


class SeamTests(unittest.TestCase):
    def test_no_real_cli_is_probed_while_providers_are_simulated(self):
        self.assertFalse(providers.simulated())
        with patch("patchrondo.providers.execute", side_effect=AssertionError("a provider command was started")):
            self.assertTrue(providers.simulated())
            with self.assertRaises(RuntimeError):
                providers.probe(["codex", "sandbox", "--help"], Path.cwd(), timeout=1)

    def test_only_the_sandbox_self_check_may_be_probed(self):
        # Refused before anything is started: a prompt can never travel this way.
        for argv in (["codex", "exec", "-"], ["claude", "-p"], ["codex"], ["powershell.exe", "-Command", "x"]):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                providers.probe(argv, Path.cwd(), timeout=1)


if __name__ == "__main__":
    unittest.main()
