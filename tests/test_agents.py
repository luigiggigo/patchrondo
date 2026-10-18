"""Agent settings: validation, the three levels, the commands they become, and that runs really use them.

No test here reaches a real provider CLI. Commands are compared as argument
arrays; whole tasks run against the scripted CLI of `support`, which records
the arguments it was started with.
"""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from patchrondo import agents, doctor, providers, recovery
from patchrondo.cli import main
from patchrondo.commands import join_command, split_command
from patchrondo.core import create_task, refresh_agent_settings, run_task
from patchrondo.process import KILL_REFUSED, Result
from patchrondo.providers import AgentFailure, AgentReply, OfficialCLI
from patchrondo.storage import read_json, save_json
from support import ServerCase, outcomes
from test_live_activity import EngineCase

W = Path("/work/task copy")
FAKE_KEY = "sk-" + "a" * 32  # shaped like a provider key; assembled here so that no key-shaped text is in the sources
CODEX_HELP = "Options:\n  -m, --model <MODEL>\n  -c, --config <key=value>\n  -s, --sandbox <MODE>\n      --add-dir <DIR>\n" \
             "  -p, --profile <P>\n  -a, --ask-for-approval <P>\n      --ephemeral\n      --json\n      --ignore-user-config\n"
CATALOG = {"models": [
    {"slug": "gpt-test-large", "display_name": "Test Large", "description": "Big", "visibility": "list",
     "default_reasoning_level": "medium", "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}, {"effort": "high"}]},
    {"slug": "gpt-hidden", "display_name": "Hidden", "visibility": "hide", "supported_reasoning_levels": []},
    {"slug": "--not a model", "visibility": "list"}]}


def legacy(provider, role, live, workspace=W, out="o.txt", schema="S", turns=12):
    """The commands of the versions without agent settings, written out as they were."""
    if provider == "claude":
        tools = "Read,Glob,Grep,Edit,Write" if role == "developer" else "Read,Glob,Grep"
        argv = ["claude", "-p", "--output-format", *(("stream-json", "--verbose") if live else ("json",)), "--restricted",
                "--permission-mode", "dontAsk", "--tools", tools, "--allowedTools", tools, "--disallowedTools", "mcp__*",
                "--max-turns", str(turns)]
        return argv + (["--json-schema", schema] if role == "reviewer" else [])
    argv = ["codex", "--ask-for-approval", "never", "exec", *(("--json",) if live else ()), "--sandbox",
            "workspace-write" if role == "developer" else "read-only", "--cd", str(workspace), "--ephemeral",
            "--output-last-message", out]
    return argv + (["--output-schema", schema] if role == "reviewer" else []) + ["-"]


def build(provider, role, values=None, live=True, **options):
    return agents.command(provider, role, values or {}, workspace=options.pop("workspace", W), live=live, schema="S",
                          last_message="o.txt", **options)


class CommandTests(unittest.TestCase):
    def test_without_settings_every_command_is_the_one_patchrondo_always_ran(self):
        for provider in agents.PROVIDERS:
            for role in agents.ROLES:
                for live in (True, False):
                    with self.subTest(provider=provider, role=role, live=live):
                        self.assertEqual(build(provider, role, live=live), legacy(provider, role, live))
                        # The defaults a level resolves to are the same thing.
                        resolved = agents.resolve(provider, role, [])["values"]
                        self.assertEqual(build(provider, role, resolved, live=live), legacy(provider, role, live))

    def test_model_and_effort_reach_both_clis_for_both_roles(self):
        for role in agents.ROLES:
            argv = build("claude", role, agents.clean_block("claude", role, {"model": "opus", "effort": "high"}))
            self.assertEqual(argv[-4:], ["--model", "opus", "--effort", "high"])
            self.assertEqual(argv[:len(legacy("claude", role, True))], legacy("claude", role, True))
            argv = build("codex", role, agents.clean_block("codex", role, {"model": "gpt-test-large", "effort": "xhigh"}))
            self.assertEqual(argv[-5:], ["--model", "gpt-test-large", "-c", 'model_reasoning_effort="xhigh"', "-"])

    def test_one_provider_runs_both_roles_with_two_models(self):
        layer = agents.clean_layer({"developer": {"claude": {"model": "opus", "effort": "max"}},
                                    "reviewer": {"claude": {"model": "haiku"}}})
        developer = agents.resolve("claude", "developer", [("global", layer)])["values"]
        reviewer = agents.resolve("claude", "reviewer", [("global", layer)])["values"]
        self.assertEqual((developer["model"], developer["effort"], reviewer["model"], reviewer["effort"]), ("opus", "max", "haiku", None))
        self.assertIn("opus", build("claude", "developer", developer))
        self.assertNotIn("opus", build("claude", "reviewer", reviewer))

    def test_every_setting_becomes_its_documented_option(self):
        claude = agents.clean_block("claude", "developer", {
            "fallback_model": "sonnet,haiku", "permission_mode": "acceptEdits", "tools": ["Read", "Edit"], "max_turns": 30,
            "disallowed_tools": ["WebSearch", "Bash(rm *)"], "max_budget_usd": 2.5, "append_system_prompt": "Be brief.\nReally.",
            "session_persistence": False})
        argv = build("claude", "developer", claude)
        for expected in (["--fallback-model", "sonnet,haiku"], ["--permission-mode", "acceptEdits"], ["--tools", "Read,Edit"],
                         ["--allowedTools", "Read,Edit"], ["--disallowedTools", "mcp__*", "WebSearch", "Bash(rm *)"],
                         ["--max-turns", "30"], ["--max-budget-usd", "2.5"], ["--append-system-prompt", "Be brief.\nReally."]):
            with self.subTest(option=expected[0]):
                start = argv.index(expected[0])
                self.assertEqual(argv[start:start + len(expected)], expected)
        self.assertIn("--no-session-persistence", argv)
        self.assertIn("--restricted", argv)
        codex = agents.clean_block("codex", "developer", {
            "approval_policy": "on-request", "profile": "work", "web_search": "cached", "ephemeral": False, "ignore_user_config": True,
            "sandbox": "read-only", "config_overrides": [{"key": "model_verbosity", "value": "low"}, {"key": "hide_agent_reasoning", "value": True},
                                                          {"key": "tool_output_token_limit", "value": 9000}]})
        argv = build("codex", "developer", codex)
        self.assertEqual(argv[1:4], ["--ask-for-approval", "on-request", "exec"])
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertNotIn("--ephemeral", argv)
        self.assertEqual(argv[argv.index("--profile") + 1], "work")
        self.assertIn("--ignore-user-config", argv)
        self.assertEqual([argv[index + 1] for index, arg in enumerate(argv) if arg == "-c"],
                         ['web_search="cached"', 'model_verbosity="low"', "hide_agent_reasoning=true", "tool_output_token_limit=9000"])
        self.assertEqual(argv[-1], "-")

    def test_paths_with_spaces_stay_one_argument_on_every_platform(self):
        folder = "C:\\Users\\Ada Lovelace\\shared notes" if os.name == "nt" else "/home/ada lovelace/shared notes"
        workspace = Path("C:/State Dir/worktrees/T-1" if os.name == "nt" else "/state dir/worktrees/T-1")
        for provider in agents.PROVIDERS:
            values = agents.clean_block(provider, "developer", {"add_dirs": [folder], agents.ACK: True})
            argv = build(provider, "developer", values, workspace=workspace)
            self.assertEqual(argv[argv.index("--add-dir") + 1], str(Path(folder)))
        self.assertIn(str(workspace), build("codex", "developer", workspace=workspace))
        # What is shown for reading parses back to the same arguments, with the quoting of either system.
        argv = build("codex", "developer", agents.clean_block("codex", "developer", {"effort": "high"}), workspace=workspace)
        for windows in (True, False):
            shown = [str(arg).replace("\\", "/") for arg in argv]
            self.assertEqual(split_command(join_command(shown, windows), windows), shown)

    def test_toml_values_are_literals_whatever_they_contain(self):
        self.assertEqual([agents.toml(value) for value in (True, False, 3, 1.5, "plain", 'a "quoted" \\ value', "two\nlines")],
                         ["true", "false", "3", "1.5", '"plain"', '"a \\"quoted\\" \\\\ value"', '"two\\nlines"'])

    def test_the_reviewer_stays_read_only_whatever_reaches_the_command(self):
        # Values that validation refuses are forced here too: the command is the last place that decides.
        hostile = {"sandbox": "danger-full-access", "add_dirs": ["/etc"], "extra_args": ["--dangerously-bypass-approvals-and-sandbox"]}
        argv = build("codex", "reviewer", hostile)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertNotIn("--add-dir", argv)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", argv)
        argv = build("claude", "reviewer", {"tools": ["Bash", "Write"], "mcp_config": "/x.json", "mcp_tools": ["mcp__x"], "extra_args": ["--bare"]})
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Glob,Grep")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read,Glob,Grep")
        self.assertEqual(argv[argv.index("--disallowedTools") + 1], "mcp__*")
        self.assertNotIn("--mcp-config", argv)
        self.assertNotIn("--bare", argv)

    def test_mcp_servers_are_only_those_of_the_file_and_only_the_listed_tools(self):
        values = agents.clean_block("claude", "developer", {"mcp_config": str(Path("/cfg/mcp.json").resolve()), "mcp_tools": ["mcp__docs__search"],
                                                            agents.ACK: True})
        argv = build("claude", "developer", values)
        self.assertEqual(argv[argv.index("--mcp-config") + 2], "--strict-mcp-config")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read,Glob,Grep,Edit,Write,mcp__docs__search")
        self.assertNotIn("--disallowedTools", argv)

    def test_preview_masks_credentials_and_never_shows_the_schema(self):
        argv = build("claude", "reviewer", {"append_system_prompt": f"use API_KEY={FAKE_KEY} here"})
        shown = agents.preview(argv)
        self.assertNotIn(FAKE_KEY, shown["line"])
        self.assertIn("[redacted]", shown["line"])
        self.assertIn("<review schema>", shown["argv"])
        long = agents.preview(["codex", "-c", "developer_instructions=" + "x" * 900])
        self.assertLess(len(long["line"]), 400)

    def test_the_test_call_uses_no_tools_that_write_and_names_the_model(self):
        claude = agents.verification("claude", "opus", "low", folder=W, last_message=W / "o")
        self.assertEqual(claude[claude.index("--tools") + 1], "Read")
        self.assertEqual(claude[-4:], ["--model", "opus", "--effort", "low"])
        self.assertIn("--restricted", claude)
        codex = agents.verification("codex", "gpt-test-large", None, folder=W, last_message=W / "o")
        self.assertEqual(codex[codex.index("--sandbox") + 1], "read-only")
        self.assertEqual(codex[-3:], ["--model", "gpt-test-large", "-"])
        self.assertTrue(all(arg for arg in claude + codex))  # the runner refuses an empty argument


class ValidationTests(unittest.TestCase):
    def refused(self, provider, role, block, *words):
        with self.assertRaises(ValueError) as caught:
            agents.clean_block(provider, role, block)
        for word in words:
            self.assertIn(word, str(caught.exception))

    def test_values_are_typed(self):
        for provider, block in (("claude", {"nonsense": 1}), ("claude", {"effort": "extreme"}), ("claude", {"max_turns": True}),
                                ("claude", {"max_turns": 0}), ("claude", {"max_turns": "12"}), ("claude", {"max_budget_usd": 0}),
                                ("claude", {"max_budget_usd": float("inf")}), ("claude", {"session_persistence": 1}),
                                ("claude", {"tools": []}), ("claude", {"tools": ["Read;rm"]}), ("claude", {"disallowed_tools": ["Bash(a)(b)"]}),
                                ("claude", {"fallback_model": "a b"}), ("claude", {"instructions": "x" * 8001}),
                                ("codex", {"profile": "../x"}), ("codex", {"web_search": "yes"}), ("codex", {"approval_policy": "untrusted"}),
                                ("codex", {"ephemeral": "no"}), ("codex", {"timeout_seconds": 86401}), ("codex", [])):
            with self.subTest(block=block):
                self.refused(provider, "developer", block)

    def test_a_model_name_can_never_be_read_as_an_option(self):
        for name in ("--model", "-x", "a b", "", "x" * 129, "name;rm -rf", "$(id)", 'a"b', 7):
            with self.subTest(model=name):
                self.refused("codex", "developer", {"model": name}, "Model")
        for name in ("opus", "sonnet[1m]", "claude-opus-5-5", "gpt-6.1-sol", "us.anthropic.claude-sonnet-5-v1:0", "org/model@v2"):
            self.assertEqual(agents.clean_block("claude", "reviewer", {"model": name}), {"model": name})

    def test_what_widens_the_developer_is_saved_only_with_the_acknowledgement(self):
        folder = str(Path("/shared").resolve())
        for provider, block in (("codex", {"sandbox": "danger-full-access"}), ("codex", {"add_dirs": [folder]}),
                                ("codex", {"extra_args": ["--some-new-option"]}), ("codex", {"config_overrides": [{"key": "tui.theme", "value": "x"}]}),
                                ("claude", {"tools": ["Read", "Bash"]}), ("claude", {"add_dirs": [folder]}),
                                ("claude", {"mcp_config": str(Path("/m.json").resolve())}), ("claude", {"extra_args": ["--name", "x"]})):
            with self.subTest(block=block):
                self.refused(provider, "developer", block, agents.ACK)
                self.refused(provider, "developer", {**block, agents.ACK: "yes"}, agents.ACK)
                self.refused(provider, "developer", {**block, agents.ACK: 1}, agents.ACK)
                self.assertIs(agents.clean_block(provider, "developer", {**block, agents.ACK: True})[agents.ACK], True)
                self.assertTrue(agents.risks(provider, "developer", block))

    def test_an_acknowledgement_is_not_kept_beside_nothing_risky(self):
        self.assertEqual(agents.clean_block("codex", "developer", {"model": "m", agents.ACK: True}), {"model": "m"})
        self.assertEqual(agents.clean_block("claude", "developer", {"tools": ["Read", "Edit", "Write"], agents.ACK: True}),
                         {"tools": ["Read", "Edit", "Write"]})
        self.assertEqual(agents.risks("codex", "developer", agents.resolve("codex", "developer", [])["values"]), [])

    def test_the_reviewer_cannot_be_given_what_writes(self):
        for provider, block, word in (("codex", {"sandbox": "workspace-write"}, "read-only"), ("codex", {"sandbox": "danger-full-access", agents.ACK: True}, "read-only"),
                                      ("codex", {"add_dirs": [str(Path("/x").resolve())], agents.ACK: True}, "reviewer"),
                                      ("codex", {"extra_args": ["--x"], agents.ACK: True}, "reviewer"),
                                      ("codex", {"config_overrides": [{"key": "tui.theme", "value": "x"}], agents.ACK: True}, "reviewer"),
                                      ("claude", {"tools": ["Read"]}, "reviewer"), ("claude", {"mcp_config": str(Path("/m.json").resolve()), agents.ACK: True}, "reviewer"),
                                      ("claude", {"permission_mode": "acceptEdits"}, "never edits"), ("claude", {"extra_args": ["--x"], agents.ACK: True}, "reviewer")):
            with self.subTest(block=block):
                self.refused(provider, "reviewer", block, word)
        # Reading more, and answering differently, stay possible.
        self.assertEqual(agents.clean_block("codex", "reviewer", {"sandbox": "read-only", "config_overrides": [{"key": "model_verbosity", "value": "high"}]}),
                         {"sandbox": "read-only", "config_overrides": [{"key": "model_verbosity", "value": "high"}]})
        self.assertEqual(agents.clean_block("claude", "reviewer", {"permission_mode": "plan"}), {"permission_mode": "plan"})
        self.assertEqual(agents.risks("claude", "reviewer", {"add_dirs": ["/docs"]}), [])

    def test_modes_that_remove_a_protection_do_not_exist_with_or_without_acknowledgement(self):
        self.refused("claude", "developer", {"permission_mode": "bypassPermissions", agents.ACK: True}, "restricted")
        self.refused("claude", "developer", {"permission_mode": "manual"}, "prompt")
        for provider, arg in (("claude", "--dangerously-skip-permissions"), ("claude", "--bare"), ("claude", "--settings={}"),
                              ("claude", "--include-partial-messages"), ("claude", "--resume"), ("codex", "--dangerously-bypass-approvals-and-sandbox"),
                              ("codex", "--full-auto"), ("codex", "--ignore-rules"), ("codex", "--enable=x"), ("codex", "--oss")):
            with self.subTest(arg=arg):
                self.refused(provider, "developer", {"extra_args": [arg], agents.ACK: True}, "never passed")

    def test_custom_arguments_cannot_repeat_or_hide_an_option(self):
        for provider, args, word in (("claude", ["--model", "x"], "own setting"), ("claude", ["--tools=Bash"], "own setting"),
                                     ("codex", ["--sandbox", "danger-full-access"], "own setting"), ("codex", ["--config=sandbox_mode=x"], "own setting"),
                                     ("codex", ["-c", "sandbox_mode=danger-full-access"], "long form"), ("codex", ["-csandbox_mode=x"], "long form"),
                                     ("codex", ["-s"], "long form"), ("codex", ["stray"], "not an option"), ("codex", ["--x", "a", "b"], "not an option"),
                                     ("codex", [""], "empty"), ("codex", ["--x\n--y"], "control"), ("codex", "--x", "list")):
            with self.subTest(args=args):
                self.refused(provider, "developer", {"extra_args": args, agents.ACK: True}, word)
        self.assertEqual(agents.clean_block("codex", "developer", {"extra_args": ["--some-option", "value", "--other=1", "--limit", "-5"], agents.ACK: True})
                         ["extra_args"], ["--some-option", "value", "--other=1", "--limit", "-5"])

    def test_configuration_overrides_follow_their_policy(self):
        pair = lambda key, value="x": {"config_overrides": [{"key": key, "value": value}], agents.ACK: True}  # noqa: E731
        for key in ("sandbox_mode", "approval_policy", "model", "model_reasoning_effort", "web_search", "profile"):
            self.refused("codex", "developer", pair(key), "own setting")
        for key in ("notify", "sandbox_workspace_write.network_access", "shell_environment_policy.inherit", "model_provider",
                    "model_providers.x.base_url", "mcp_servers.x.command", "features.x", "show_raw_agent_reasoning", "projects.x.trust_level"):
            self.refused("codex", "developer", pair(key), "not passed")
        for bad in ({"key": "a b", "value": 1}, {"key": "x"}, {"key": "x", "value": None}, {"key": "x", "value": [1]},
                    {"key": "model_verbosity", "value": "loud"}, {"key": "hide_agent_reasoning", "value": "true"},
                    {"key": "tool_output_token_limit", "value": -1}, {"key": "x", "value": 1, "more": 2}):
            self.refused("codex", "developer", {"config_overrides": [bad], agents.ACK: True})
        self.refused("codex", "developer", {"config_overrides": [{"key": "a", "value": 1}, {"key": "a", "value": 2}], agents.ACK: True}, "twice")

    def test_a_value_that_looks_like_a_credential_is_not_stored(self):
        self.refused("codex", "developer", {"config_overrides": [{"key": "x.api_key", "value": FAKE_KEY}], agents.ACK: True}, "credential")
        self.refused("codex", "developer", {"extra_args": ["--header", "Bearer abcdefghijklmnopqrstuvwxyz"], agents.ACK: True}, "credential")

    def test_additional_folders_never_open_private_state_or_the_users_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            home, repo, other = root / "state", root / "repo", root / "other"
            for path in (home / "worktrees", repo / "src", other):
                path.mkdir(parents=True)
            check = lambda role, path: agents.check_folders("claude", role, {"add_dirs": [str(path)]}, private=(home,), repository=repo)  # noqa: E731
            for role, path in (("developer", home), ("reviewer", home / "worktrees"), ("reviewer", root), ("developer", repo),
                               ("developer", repo / "src")):
                with self.subTest(role=role, path=path), self.assertRaises(ValueError):
                    check(role, path)
            check("developer", other)
            check("reviewer", repo)  # the reviewer may read the user's checkout; it cannot change it
        for bad in ("relative/path", "\\\\server\\share", ""):
            self.refused("claude", "reviewer", {"add_dirs": [bad]}, "absolute")


class LevelTests(unittest.TestCase):
    def test_the_nearest_level_decides_and_every_value_names_its_source(self):
        layers = [("global", {"developer": {"claude": {"model": "sonnet", "effort": "low", "max_turns": 20}}}),
                  ("project", {"developer": {"claude": {"effort": "high", "max_turns": None}}}),
                  ("task", {"developer": {"claude": {"model": "opus"}}})]
        resolved = agents.resolve("claude", "developer", layers)
        self.assertEqual((resolved["values"]["model"], resolved["values"]["effort"], resolved["values"]["max_turns"]), ("opus", "high", None))
        self.assertEqual([resolved["sources"][key] for key in ("model", "effort", "max_turns", "permission_mode")],
                         ["task", "project", "project", "default"])
        self.assertEqual(resolved["values"]["permission_mode"], "dontAsk")
        # The other role and the other provider are untouched.
        self.assertIsNone(agents.resolve("claude", "reviewer", layers)["values"]["model"])
        self.assertIsNone(agents.resolve("codex", "developer", layers)["values"]["model"])
        self.assertEqual(agents.resolve("codex", "reviewer", layers)["values"]["sandbox"], "read-only")

    def test_a_layer_keeps_only_what_is_set_and_refuses_unknown_parts(self):
        self.assertEqual(agents.clean_layer({"developer": {"claude": {}, "codex": None}, "reviewer": None}), {})
        self.assertEqual(agents.clean_layer({"reviewer": {"codex": {"model": None}}}), {"reviewer": {"codex": {"model": None}}})
        for bad in ({"judge": {}}, {"developer": {"gemini": {}}}, {"developer": []}, [], "x"):
            with self.subTest(layer=bad), self.assertRaises(ValueError):
                agents.clean_layer(bad)

    def test_a_frozen_record_is_what_differs_from_the_defaults_and_validates_again(self):
        layers = [("global", {"developer": {"codex": {"model": "gpt-test-large", "sandbox": "workspace-write", "ephemeral": True}}}),
                  ("task", {"developer": {"codex": {"add_dirs": [str(Path("/shared").resolve())], agents.ACK: True}}})]
        record = agents.freeze("codex", "developer", layers)
        self.assertEqual(record["values"], {"model": "gpt-test-large", "add_dirs": [str(Path("/shared").resolve())], agents.ACK: True})
        self.assertEqual(record["sources"], {"model": "global", "add_dirs": "task"})
        self.assertEqual(agents.thaw(record, "codex", "developer"), record["values"])
        self.assertIsNone(agents.thaw(record, "claude", "developer"))  # another provider's record is not used
        self.assertIsNone(agents.thaw({"provider": "codex"}, "codex", "developer"))
        self.assertEqual(record["digest"], agents.freeze("codex", "developer", layers)["digest"])
        self.assertNotEqual(record["digest"], agents.freeze("codex", "developer", layers[:1])["digest"])
        tampered = {**record, "values": {"sandbox": "danger-full-access"}}
        with self.assertRaises(ValueError):
            agents.thaw(tampered, "codex", "developer")
        self.assertEqual(agents.freeze("claude", "reviewer", [])["values"], {})

    def test_step_keys_sort_in_the_order_of_the_steps(self):
        keys = [agents.step_key({"iteration": number}, role) for number in (1, 2, 10) for role in ("developer", "reviewer")]
        self.assertEqual(keys, sorted(keys))

    def test_presets_set_what_they_name_and_are_recognized_when_in_effect(self):
        available = agents.presets({"presets": [{"id": "U-00000001", "name": "Mine", "values": {"codex": {"model": "gpt-test-large", "effort": "high"}}}]})
        self.assertEqual([preset["id"] for preset in available], ["default", "fast", "balanced", "quality", "U-00000001"])
        self.assertEqual(agents.preset_of("claude", agents.resolve("claude", "developer", [])["values"], available), "default")
        self.assertEqual(agents.preset_of("claude", {"model": "sonnet", "effort": "medium"}, available), "balanced")
        self.assertEqual(agents.preset_of("claude", {"model": "sonnet", "effort": "max"}, available), "custom")
        self.assertEqual(agents.preset_of("codex", {"model": "gpt-test-large", "effort": "high"}, available), "quality")
        self.assertEqual(agents.preset_of("codex", {"model": "gpt-test-large", "effort": "ultra"}, available), "custom")
        clean = agents.clean_preset({"name": "  My   preset ", "values": {"claude": {"model": "opus", "max_turns": 40}, "codex": {}}})
        self.assertEqual((clean["name"], clean["values"]), ("My preset", {"claude": {"model": "opus", "max_turns": 40}}))
        self.assertRegex(clean["id"], r"^U-[a-f0-9]{8}$")
        for bad in ({"name": "", "values": {"claude": {"model": "x"}}}, {"name": "Fast", "values": {"claude": {"model": "x"}}},
                    {"name": "x", "values": {}}, {"name": "x", "values": {"claude": {}}}, {"name": "x", "values": {"claude": {"permission_mode": "plan"}}},
                    {"name": "x", "values": {"codex": {"sandbox": "read-only"}}}, {"name": "x", "values": {"claude": {"model": "y"}}, "id": "fast"},
                    {"name": "x", "values": {"claude": {"tools": ["Bash"], agents.ACK: True}}}):
            with self.subTest(preset=bad), self.assertRaises(ValueError):
                agents.clean_preset(bad)

    def test_the_store_is_validated_when_read_and_before_it_is_written(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(agents.read_store(root)["settings"], {})
            self.assertFalse((root / agents.STORE).exists())  # reading creates nothing
            agents.update_store(root, lambda store: store["settings"].update(reviewer={"codex": {"effort": "high"}}))
            self.assertEqual(read_json(root / agents.STORE)["settings"], {"reviewer": {"codex": {"effort": "high"}}})
            with self.assertRaises(ValueError):
                agents.update_store(root, lambda store: store["settings"].update(reviewer={"codex": {"sandbox": "workspace-write"}}))
            self.assertEqual(agents.read_store(root)["settings"], {"reviewer": {"codex": {"effort": "high"}}})
            for unusable in ('{"version": 2}', "{not json", '{"version": 1, "presets": {"a": 1}}'):
                (root / agents.STORE).write_text(unusable, encoding="utf-8")
                with self.subTest(file=unusable), self.assertRaises(ValueError) as caught:
                    agents.read_store(root)
                self.assertIn("delete it to go back to the defaults", str(caught.exception))
                with self.assertRaises(ValueError):
                    agents.update_store(root, lambda store: None)
                self.assertEqual((root / agents.STORE).read_text(encoding="utf-8"), unusable)  # never overwritten

    def test_the_global_settings_of_a_state_directory_are_found(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            managed, imported = root / "projects" / "P-00000001", root / "elsewhere"
            managed.mkdir(parents=True)
            imported.mkdir()
            with patch.dict(os.environ, {agents.WORKSPACE: ""}):
                self.assertEqual(agents.workspace_root(managed), managed)  # no registry: a 0.1 home is its own workspace
                save_json(root / "projects.json", {"version": 1, "projects": []})
                self.assertEqual(agents.workspace_root(managed), root)
                self.assertEqual(agents.workspace_root(imported), imported)
            with patch.dict(os.environ, {agents.WORKSPACE: str(root)}):
                self.assertEqual(agents.workspace_root(imported), root)  # a run started by the interface is told


class CapabilityTests(unittest.TestCase):
    def test_an_option_is_offered_only_when_the_cli_or_its_documentation_names_it(self):
        self.assertEqual(set(agents.compatibility("codex", None).values()), {"unknown", "builtin"})
        offered = {"flags": doctor.FLAG.findall(CODEX_HELP)}
        status = agents.compatibility("codex", offered)
        self.assertEqual((status["model"], status["effort"], status["sandbox"], status["web_search"]), ("listed",) * 4)
        without = {"flags": [flag for flag in offered["flags"] if flag != "--profile"]}
        self.assertEqual(agents.compatibility("codex", without)["profile"], "unlisted")
        # Claude Code's help does not print --max-turns; its CLI reference documents it.
        claude = agents.compatibility("claude", {"flags": ["--model", "--effort", "--output-format"]})
        self.assertEqual((claude["max_turns"], claude["model"], claude["fallback_model"], claude["instructions"]),
                         ("documented", "listed", "unlisted", "builtin"))  # instructions are PatchRondo's own, not an option
        field = next(item for item in agents.schema("codex", "reviewer", without) if item["key"] == "profile")
        self.assertEqual(field["status"], "unlisted")
        sandbox = next(item for item in agents.schema("codex", "reviewer", offered) if item["key"] == "sandbox")
        self.assertEqual([(choice["value"], bool(choice["disabled"])) for choice in sandbox["choices"]],
                         [("read-only", False), ("workspace-write", True), ("danger-full-access", True)])
        self.assertEqual(sandbox["default"], "read-only")
        tools = next(item for item in agents.schema("claude", "reviewer", None) if item["key"] == "tools")
        self.assertEqual((tools["available"], bool(tools["locked"])), (False, True))
        json.dumps(agents.schema("claude", "developer", None))  # what the page receives is plain data

    def test_a_catalog_never_says_that_an_account_can_use_a_model(self):
        store = agents.read_store(Path(tempfile.gettempdir()) / "patchrondo-no-such-home")
        store["models"]["custom"]["codex"] = ["my-fine-tune"]
        store["models"]["verified"]["codex"] = {"gpt-test-large": {"ok": True, "at": "2026-10-12T10:00:00+00:00"}}
        listed = agents.catalog("codex", {"models": doctor.catalog_models(CATALOG), "models_source": "bundled"}, store)
        self.assertEqual([(entry["id"], entry["source"], bool(entry["verified"])) for entry in listed["models"]],
                         [("gpt-test-large", "bundled", True), ("my-fine-tune", "custom", False)])
        self.assertEqual(listed["models"][0]["efforts"], ["low", "medium", "high"])
        self.assertEqual(agents.catalog("codex", None, agents.read_store(Path("/nowhere")))["models"], [])
        claude = agents.catalog("claude", None, agents.read_store(Path("/nowhere")))
        self.assertEqual((claude["source"], {entry["source"] for entry in claude["models"]}), ("documented", {"documented"}))
        self.assertIn("sonnet", [entry["id"] for entry in claude["models"]])

    def test_test_configuration_reports_what_would_stop_a_step_without_starting_anything(self):
        status = {"label": "Codex CLI", "installed": True, "authenticated": True, "version": "codex-cli 9", "path": "/x/codex", "login_command": "codex login"}
        offered = {"flags": doctor.FLAG.findall(CODEX_HELP), "models": doctor.catalog_models(CATALOG), "models_source": "cli"}
        models = agents.catalog("codex", offered, agents.read_store(Path("/nowhere")))
        run = lambda values, **more: {check["id"]: check for check in agents.diagnose(  # noqa: E731
            "codex", "developer", agents.resolve("codex", "developer", [("global", {"developer": {"codex": values}})])["values"],
            status=more.get("status", status), capabilities=more.get("offered", offered), models=models, folders=more.get("folders"))}
        with patch("patchrondo.providers.execute", side_effect=AssertionError("a provider command was started")):
            plain = run({})
            self.assertEqual({check["status"] for check in plain.values()}, {"passed"})
            self.assertEqual(run({"model": "gpt-test-large"})["model"]["status"], "warning")  # listed is not usable
            self.assertEqual(run({"model": "gpt-test-large", "effort": "ultra"}, )["effort"]["status"], "failed")
            self.assertEqual(run({"model": "gpt-unknown"})["model"]["status"], "warning")
            self.assertEqual(run({}, status={**status, "installed": False, "hint": "Install it"})["cli.installed"]["status"], "failed")
            self.assertEqual(run({}, status={**status, "authenticated": False})["cli.login"]["status"], "failed")
            self.assertEqual(run({}, status=None)["cli.installed"]["status"], "skipped")
            old = {"flags": [flag for flag in offered["flags"] if flag != "--profile"]}
            self.assertEqual(run({"profile": "work"}, offered=old)["options"]["status"], "failed")
            self.assertEqual(run({"profile": "work"}, offered=None)["options"]["status"], "skipped")
            self.assertEqual(run({}, folders={"project repository": "/no/such/folder"})["folder.project repository"]["status"], "failed")
            risky = run({"sandbox": "danger-full-access", agents.ACK: True})
            self.assertEqual(risky["risk.sandbox"]["status"], "warning")
            gone = run({"add_dirs": [str(Path("/no/such/shared").resolve())], agents.ACK: True})
            self.assertEqual(gone["folders"]["status"], "failed")


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.executable = Path(folder.name) / "codex"
        self.executable.write_text("", encoding="utf-8")
        self.codex_home = Path(folder.name) / "codex-home"
        self.codex_home.mkdir()
        self.status = {"name": "codex", "installed": True, "path": str(self.executable), "version": "codex-cli 9"}
        self.asked = []

    def probe(self, answers):
        def run(argv, cwd, *, timeout, on_stdout=None):
            self.asked.append(tuple(argv))
            code, text = answers.get(tuple(argv), (1, ""))
            for start in range(0, len(text), 7):  # in pieces, as a pipe delivers it
                on_stdout(text[start:start + 7].encode("utf-8"))
            return Result(code, "", "")
        return patch("patchrondo.doctor.providers.probe", side_effect=run)

    def test_options_models_and_profiles_come_from_the_cli_itself(self):
        for name in ("work.config.toml", "night-shift.config.toml", "config.toml", "bad name.config.toml", "notes.txt"):
            (self.codex_home / name).write_text("", encoding="utf-8")
        answers = {("codex", "--help"): (0, "  -a, --ask-for-approval <P>\n      --search\n"), ("codex", "exec", "--help"): (0, CODEX_HELP),
                   ("codex", "debug", "models"): (0, json.dumps(CATALOG))}
        with self.probe(answers), patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            found = doctor.discover(self.status)
        self.assertEqual(self.asked, [("codex", "--help"), ("codex", "exec", "--help"), ("codex", "debug", "models")])
        self.assertTrue({"--model", "--config", "--sandbox", "--search", "--ask-for-approval"} <= set(found["flags"]))
        self.assertEqual((found["models_source"], [model["id"] for model in found["models"]]), ("cli", ["gpt-test-large"]))
        self.assertEqual(found["models"][0]["default_effort"], "medium")
        self.assertEqual(found["profiles"], ["night-shift", "work"])
        self.assertEqual(found["notes"], [])

    def test_the_bundled_catalog_is_used_when_the_refreshed_one_does_not_answer(self):
        answers = {("codex", "debug", "models"): (0, "not json"), ("codex", "debug", "models", "--bundled"): (0, json.dumps(CATALOG))}
        with self.probe(answers), patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            found = doctor.discover(self.status)
        self.assertEqual((found["models_source"], len(found["models"]), found["flags"]), ("bundled", 1, []))
        self.assertEqual(len(found["notes"]), 3)  # two helps and the refreshed catalog: not known, never "not supported"
        self.assertEqual(set(agents.compatibility("codex", found).values()), {"unknown", "builtin"})

    def test_nothing_is_asked_without_a_real_executable_or_while_simulated(self):
        with self.probe({}):
            self.assertIsNone(doctor.discover({**self.status, "path": "/synthetic/codex"}))
            self.assertIsNone(doctor.discover({**self.status, "installed": False}))
            self.assertIsNone(doctor.discover({"name": "other", "installed": True, "path": str(self.executable)}))
            with patch("patchrondo.providers.execute", side_effect=AssertionError("a provider command was started")):
                self.assertIsNone(doctor.discover(self.status))
        self.assertEqual(self.asked, [])

    def test_claude_is_asked_for_its_help_only(self):
        with self.probe({("claude", "--help"): (0, "  --model <model>\n  --effort <level>\n")}):
            found = doctor.discover({**self.status, "name": "claude"})
        self.assertEqual((self.asked, found["flags"], found["models"]), ([("claude", "--help")], ["--effort", "--model"], []))

    def test_only_exact_model_free_questions_pass_the_probe(self):
        calls = []
        run = lambda argv, cwd, **kwargs: calls.append(argv) or Result(0, "", "")  # noqa: E731
        # Both names, so that the runner still counts as the real one and only the question is judged.
        with patch("patchrondo.providers._execute", run), patch("patchrondo.providers.execute", run):
            for argv in providers.PROBES:
                providers.probe(list(argv), Path.cwd(), timeout=1)
            for argv in (["codex", "exec", "--help", "-"], ["codex", "debug", "models", "--bundled", "x"], ["claude", "--help", "-p"],
                         ["codex", "debug", "prompt-input"], ["claude", "-p", "--help"], ["codex", "exec"]):
                with self.subTest(argv=argv), self.assertRaises(ValueError):
                    providers.probe(argv, Path.cwd(), timeout=1)
        self.assertEqual(calls, [list(argv) for argv in providers.PROBES])


class AdapterTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        self.runs = self.path / "runs"

    def invoke(self, result, provider="claude", role="developer", settings=None):
        with patch("patchrondo.providers.execute", return_value=result) as execute:
            try:
                reply = OfficialCLI(timeout=600, claude_turns=12).invoke(provider, role, "task", self.path, self.runs, settings=settings)
            except AgentFailure as failure:
                reply = failure
        return reply, execute.call_args

    def test_the_settings_of_a_step_decide_its_command_its_timeout_and_its_record(self):
        settings = agents.clean_block("claude", "developer", {"model": "opus", "effort": "high", "timeout_seconds": 77, "live_activity": False})
        reply, call = self.invoke(Result(0, json.dumps({"result": "done"}), ""), settings=settings)
        self.assertEqual(reply.text, "done")
        self.assertEqual(call.args[0], legacy("claude", "developer", False) + ["--model", "opus", "--effort", "high"])
        self.assertEqual(call.kwargs["timeout"], 77)
        self.assertNotIn("on_stdout", call.kwargs)  # live activity off for this role: the command of before streaming
        self.assertIn("--model opus --effort high", (self.runs / "developer.command.txt").read_text(encoding="utf-8"))

    def test_without_settings_nothing_new_is_written_or_passed(self):
        _, call = self.invoke(Result(0, json.dumps({"type": "result", "result": "done"}), ""))
        self.assertEqual(call.args[0], legacy("claude", "developer", True))
        self.assertEqual(call.kwargs["timeout"], 600)
        self.assertFalse((self.runs / "developer.command.txt").exists())

    def test_an_option_the_cli_refuses_is_named_and_never_retried_as_a_usage_limit(self):
        for provider, stderr in (("claude", "error: unknown option '--effort'\n"), ("claude", "error: option '--effort <level>' argument 'ultra' is invalid. Allowed choices are low, medium.\n"),
                                 ("codex", "error: unexpected argument '--profile' found\n\nUsage: codex exec [OPTIONS]\n"),
                                 ("codex", "error: invalid value 'x' for '--sandbox <SANDBOX_MODE>'\n")):
            with self.subTest(stderr=stderr):
                failure, _ = self.invoke(Result(2, "", stderr), provider=provider, settings={"effort": "high"})
                self.assertEqual((failure.kind, failure.provider), ("unsupported_option", provider))
                self.assertIn(stderr.strip().splitlines()[0][:40], str(failure))
        # A login problem named beside an option keeps its own kind: waiting or editing options cannot fix it.
        failure, _ = self.invoke(Result(1, "", "error: unknown option '--x'\nNot logged in. Please login.\n"))
        self.assertEqual(failure.kind, "authentication")

    def test_a_model_the_account_cannot_use_has_its_own_kind(self):
        for provider, stderr in (("claude", "There's an issue with the selected model (nope). It may not exist or you may not have access to it."),
                                 ("codex", "The model `nope` does not exist or you do not have access to it."),
                                 ("codex", "unexpected status 404: model_not_found")):
            with self.subTest(stderr=stderr):
                failure, _ = self.invoke(Result(1, "", stderr), provider=provider, settings={"model": "nope"})
                self.assertEqual(failure.kind, "model_unavailable")
        failure, _ = self.invoke(Result(1, "", "Usage limit reached for this model; it is not available until 9pm"), provider="codex")
        self.assertEqual(failure.kind, "quota")  # a usage limit named in the same message still decides
        failure, _ = self.invoke(Result(1, "", "Not logged in. The model is not available."), provider="codex")
        self.assertEqual(failure.kind, "authentication")

    def test_a_step_whose_group_refused_its_last_signal_says_so_in_its_message_and_log(self):
        """A timeout all the same; with `kill_refused` its message and its stderr log say what may still be running.
        Without the flag, or from an `execute` whose result has no such field, both are as before."""
        cases = {"refused": Result(124, "", "partial\n", timed_out=True, kill_refused=True),
                 "not refused": Result(124, "", "partial\n", timed_out=True),
                 "absent": SimpleNamespace(returncode=124, stdout="", stderr="partial\n", timed_out=True)}
        for provider in ("claude", "codex"):
            for label, result in cases.items():
                with self.subTest(provider=provider, flag=label):
                    failure, _ = self.invoke(result, provider=provider)
                    self.assertIsInstance(failure, AgentFailure)
                    self.assertEqual((failure.kind, failure.provider), ("timeout", provider))
                    shown = f"Timeout after 600s for {provider}/developer"
                    log = (self.runs / "developer.stderr.log").read_text(encoding="utf-8")
                    if label == "refused":
                        self.assertEqual(str(failure), f"{shown}. {KILL_REFUSED}")
                        self.assertEqual(log, f"partial\n[PatchRondo] {KILL_REFUSED}.\n")
                    else:
                        self.assertEqual((str(failure), log), (shown, "partial\n"))

    def test_a_folder_that_is_gone_stops_the_step_before_the_cli_is_started(self):
        missing = str((self.path / "gone").resolve())
        failure, call = self.invoke(Result(0, "", ""), settings={"add_dirs": [missing], agents.ACK: True})
        self.assertEqual((failure.kind, call), ("configuration", None))
        self.assertIn(missing, str(failure))

    def test_a_test_call_reports_what_the_cli_answered(self):
        adapter, folder = OfficialCLI(timeout=600), self.path / "verify"
        with patch("patchrondo.providers.execute", return_value=Result(0, json.dumps({"result": "OK"}), "")) as execute:
            self.assertEqual(adapter.verify("claude", "opus", "low", folder), {"ok": True, "kind": None, "message": "OK"})
        self.assertEqual(execute.call_args.args[0][-4:], ["--model", "opus", "--effort", "low"])
        self.assertEqual((execute.call_args.args[1], execute.call_args.kwargs["timeout"]), (folder, 180))

        def codex(argv, cwd, **kwargs):
            Path(argv[argv.index("--output-last-message") + 1]).write_text("OK\n", encoding="utf-8")
            return Result(0, "", "")
        with patch("patchrondo.providers.execute", side_effect=codex):
            self.assertTrue(adapter.verify("codex", "gpt-test-large", None, folder)["ok"])
        self.assertEqual(list(folder.iterdir()), [])  # nothing is left behind
        for result, kind in ((Result(1, "", "The model `x` does not exist or you do not have access to it."), "model_unavailable"),
                             (Result(1, "", "Not logged in"), "authentication"), (Result(124, "", "", timed_out=True), "timeout"),
                             (Result(2, "", "error: unknown option '--effort'"), "unsupported_option"), (Result(0, "", ""), "empty_output")):
            with self.subTest(kind=kind), patch("patchrondo.providers.execute", return_value=result):
                answer = adapter.verify("codex", "x", None, folder)
                self.assertEqual((answer["ok"], answer["kind"]), (False, kind))
        with patch("patchrondo.providers.execute", side_effect=FileNotFoundError()):
            self.assertEqual(adapter.verify("claude", "x", None, folder)["kind"], "configuration")


class StoppedStepTests(EngineCase):
    """A whole task whose developer step is stopped at its time limit and its group refused the last signal."""

    def test_the_saved_pause_and_the_logs_say_what_may_still_be_running(self):
        outcomes_of = {}
        for refused in (True, False):
            with self.subTest(refused=refused), patch("patchrondo.providers.execute",
                                                      return_value=Result(124, "", "", timed_out=True, kill_refused=refused)):
                task_id, path = self.task()
                state = run_task(self.home, task_id)
                saved = read_json(path / "state.json")
                self.assertEqual((saved["status"], saved["phase"], saved["iteration"], saved["last_error"]["kind"]),
                                 ("paused", "develop", 1, "timeout"))
                message = "Timeout after 60s for claude/developer" + (f". {KILL_REFUSED}" if refused else "")
                self.assertEqual((saved["last_error"]["message"], state["last_error"]["message"]), (message, message))
                log = (path / "runs" / "iteration-001" / "developer.stderr.log").read_text(encoding="utf-8")
                self.assertEqual(log, f"[PatchRondo] {KILL_REFUSED}.\n" if refused else "")
                errors = [event["text"] for event in self.events(path) if event["type"] == "error"]
                self.assertEqual(errors, [message])  # the attempt in the activity journal ends with it
                outcomes_of[refused] = [entry["event"] for entry in saved["history"]]
        self.assertEqual(outcomes_of[True], outcomes_of[False])  # the same events: the loop decided the same


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        self.sleeps = 0
        self.on_sleep = None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps += 1
        if self.on_sleep:
            self.on_sleep()
        self.now += timedelta(seconds=seconds)


class EngineTests(EngineCase):
    """Whole tasks against the scripted CLI: what the settings say is what the CLI is started with."""

    def set_global(self, **roles):
        agents.update_store(self.home, lambda store: store.__setitem__("settings", agents.clean_layer(roles)))

    def calls(self, role):
        return [call["args"] for call in self.cli.calls() if call["role"] == role]

    @staticmethod
    def after(args, flag):
        return args[args.index(flag) + 1] if flag in args else None

    def test_a_run_starts_each_role_with_its_own_model_and_effort(self):
        self.set_global(developer={"claude": {"model": "opus", "effort": "high"}},
                        reviewer={"codex": {"model": "gpt-test-large", "effort": "low", "web_search": "disabled"}})
        task_id, path = self.task()
        state = run_task(self.home, task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        developer, reviewer = self.calls("developer")[0], self.calls("reviewer")[0]
        self.assertEqual((self.after(developer, "--model"), self.after(developer, "--effort")), ("opus", "high"))
        self.assertEqual(self.after(reviewer, "--model"), "gpt-test-large")
        self.assertIn('model_reasoning_effort="low"', reviewer)
        self.assertEqual(self.after(reviewer, "--sandbox"), "read-only")
        # What was used is saved with the task, shown in its history and at the start of each attempt.
        self.assertEqual({key: record["values"] for key, record in state["agent_runs"].items()},
                         {"001:developer": {"model": "opus", "effort": "high"},
                          "001:reviewer": {"model": "gpt-test-large", "effort": "low", "web_search": "disabled"}})
        self.assertEqual(state["agent_runs"]["001:developer"]["sources"], {"model": "global", "effort": "global"})
        started = {event["event"]: event for event in state["history"] if event["event"].endswith("_started")}
        self.assertEqual((started["development_started"]["model"], started["review_started"]["effort"]), ("opus", "low"))
        begun = {event["role"]: event for event in self.events(path) if event["type"] == "status" and event.get("status") == "started"}
        self.assertEqual((begun["developer"]["model"], begun["developer"]["effort"], begun["reviewer"]["model"]), ("opus", "high", "gpt-test-large"))
        self.assertIn("--model opus", (path / "runs" / "iteration-001" / "developer.command.txt").read_text(encoding="utf-8"))

    def test_the_same_provider_plays_both_roles_with_two_models(self):
        self.set_global(developer={"claude": {"model": "opus"}}, reviewer={"claude": {"model": "haiku", "effort": "low"}})
        task_id, _ = self.task(developer="claude", reviewer="claude")
        self.assertEqual(run_task(self.home, task_id)["status"], "done")
        self.assertEqual([(self.after(args, "--model"), self.after(args, "--effort")) for args in self.calls("developer") + self.calls("reviewer")],
                         [("opus", None), ("haiku", "low")])
        self.assertEqual(self.after(self.calls("reviewer")[0], "--tools"), "Read,Glob,Grep")

    def test_task_settings_win_over_the_project_and_the_project_over_the_global_ones(self):
        self.set_global(developer={"claude": {"model": "sonnet", "effort": "low", "max_turns": 9}})
        cfg = read_json(self.home / "config.json")
        cfg["agent_settings"] = {"developer": {"claude": {"effort": "high"}}}
        save_json(self.home / "config.json", cfg)
        task_id, _ = create_task(self.home, title="Feature", description="Create feature.txt", acceptance=[], developer="claude",
                                 reviewer="codex", agent_settings={"developer": {"claude": {"model": "opus"}}})
        state = run_task(self.home, task_id)
        developer = self.calls("developer")[0]
        self.assertEqual((self.after(developer, "--model"), self.after(developer, "--effort"), self.after(developer, "--max-turns")),
                         ("opus", "high", "9"))
        self.assertEqual(state["agent_runs"]["001:developer"]["sources"], {"model": "task", "effort": "project", "max_turns": "global"})
        self.assertEqual(self.calls("reviewer")[0], [arg for arg in self.calls("reviewer")[0] if arg not in ("--model", "-c")])

    def test_without_settings_a_run_starts_the_clis_as_before_and_records_that(self):
        task_id, path = self.task()
        state = run_task(self.home, task_id)
        self.assertEqual(state["status"], "done")
        self.assertEqual(self.calls("developer")[0], legacy("claude", "developer", True)[1:])
        self.assertEqual({key: (record["provider"], record["values"]) for key, record in state["agent_runs"].items()},
                         {"001:developer": ("claude", {}), "001:reviewer": ("codex", {})})
        self.assertNotIn("model", next(event for event in state["history"] if event["event"] == "development_started"))
        self.assertEqual(list(path.glob("runs/iteration-*/*.command.txt")), [])

    def test_a_step_that_is_resumed_keeps_the_settings_it_started_with(self):
        self.set_global(developer={"claude": {"model": "sonnet", "effort": "low"}})
        self.cli.script(developer={"crash_once": True})
        task_id, path = self.task()
        self.assertEqual(run_task(self.home, task_id)["status"], "paused")
        frozen = read_json(path / "state.json")["agent_runs"]["001:developer"]
        self.set_global(developer={"claude": {"model": "opus", "effort": "max"}}, reviewer={"codex": {"effort": "high"}})
        state = run_task(self.home, task_id)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        self.assertEqual([(self.after(args, "--model"), self.after(args, "--effort")) for args in self.calls("developer")],
                         [("sonnet", "low"), ("sonnet", "low")])
        self.assertEqual(state["agent_runs"]["001:developer"], frozen)  # not rewritten: same values, same time
        # The review had not started when the settings changed: it takes the new ones.
        self.assertIn('model_reasoning_effort="high"', self.calls("reviewer")[0])

    def test_an_automatic_retry_after_a_usage_limit_runs_with_the_same_settings(self):
        self.set_global(developer={"claude": {"model": "sonnet"}})
        self.cli.script(developer={"quota_once": True})
        task_id, _ = self.task()
        clock = FakeClock()
        clock.on_sleep = lambda: self.set_global(developer={"claude": {"model": "opus"}})  # edited while the run waits
        state = recovery.supervise(self.home, task_id, auto_resume=True, clock=clock, sleep=clock.sleep)
        self.assertEqual(state["status"], "done", state.get("last_error"))
        self.assertGreater(clock.sleeps, 0)
        self.assertEqual([self.after(args, "--model") for args in self.calls("developer")], ["sonnet", "sonnet"])

    def test_the_user_can_ask_a_stopped_step_to_take_the_settings_of_now(self):
        self.set_global(developer={"claude": {"model": "retired-model"}})
        self.cli.script(developer={"crash_once": True})
        task_id, path = self.task()
        run_task(self.home, task_id)
        self.set_global(developer={"claude": {"model": "opus"}})
        state = read_json(path / "state.json")
        self.assertEqual(refresh_agent_settings(state), ["developer"])
        self.assertEqual(refresh_agent_settings(state), [])  # nothing left to refresh
        self.assertEqual(state["history"][-1]["event"], "agent_settings_refreshed")
        save_json(path / "state.json", state)
        self.assertEqual(run_task(self.home, task_id)["status"], "done")
        self.assertEqual([self.after(args, "--model") for args in self.calls("developer")], ["retired-model", "opus"])

    def test_the_next_iteration_takes_the_settings_of_its_own_start(self):
        cfg = read_json(self.home / "config.json")
        cfg["workflow"]["max_iterations"] = 2
        save_json(self.home / "config.json", cfg)
        self.set_global(developer={"claude": {"model": "sonnet"}})
        self.cli.script(developer={"writes": "bad"})  # the tests fail: a second iteration follows
        task_id, path = self.task()
        original = self.cli.execute

        def execute(argv, cwd, **kwargs):
            result = original(argv, cwd, **kwargs)
            if "--json-schema" not in argv and argv[0] == "claude":
                self.set_global(developer={"claude": {"model": "opus"}})  # edited while iteration 1 is under review
                self.cli.script(reviewer={"verdict": "CHANGES_REQUESTED"})
            return result
        with patch("patchrondo.providers.execute", side_effect=execute):
            state = run_task(self.home, task_id)
        self.assertEqual([self.after(args, "--model") for args in self.calls("developer")], ["sonnet", "opus"])
        self.assertEqual(sorted(state["agent_runs"]), ["001:developer", "001:reviewer", "002:developer", "002:reviewer"])

    def test_settings_that_cannot_be_used_pause_the_task_before_any_agent_is_called(self):
        save_json(self.home / agents.STORE, {"version": 1, "settings": {"developer": {"claude": {"tools": ["Bash"]}}}})
        task_id, _ = self.task()
        state = run_task(self.home, task_id)
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "configuration"))
        self.assertIn(agents.ACK, state["last_error"]["message"])
        self.assertEqual(self.cli.calls(), [])
        self.assertNotIn("agent_runs", state)
        # A task's own settings are checked like the others.
        (self.home / agents.STORE).unlink()
        other, path = self.task()
        saved = read_json(path / "state.json")
        saved["agent_settings"] = {"reviewer": {"codex": {"sandbox": "workspace-write"}}}
        save_json(path / "state.json", saved)
        state = run_task(self.home, other)
        self.assertEqual((state["phase"], state["last_error"]["kind"]), ("develop", "configuration"))
        self.assertIn("task's agent settings", state["last_error"]["message"])
        self.assertEqual(self.cli.calls(), [])  # a level that cannot be read stops the task at once, not at the review

    def test_a_private_folder_cannot_be_opened_to_an_agent_by_editing_the_files(self):
        self.set_global(developer={"claude": {"add_dirs": [str(self.home / "tasks")], agents.ACK: True}})
        task_id, _ = self.task()
        state = run_task(self.home, task_id)
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "configuration"))
        self.assertIn("private state", state["last_error"]["message"])
        self.assertEqual(self.cli.calls(), [])

    def test_extra_instructions_are_added_to_the_prompt_of_their_role_only(self):
        self.set_global(reviewer={"codex": {"instructions": "Look at naming first."}})
        seen = {}
        original = self.cli.execute

        def execute(argv, cwd, **kwargs):
            seen["reviewer" if "--output-schema" in argv else "developer"] = kwargs["stdin"]
            return original(argv, cwd, **kwargs)
        task_id, _ = self.task()
        with patch("patchrondo.providers.execute", side_effect=execute):
            self.assertEqual(run_task(self.home, task_id)["status"], "done")
        self.assertTrue(seen["reviewer"].rstrip().endswith("Look at naming first."))
        self.assertIn("# ROLE: INDEPENDENT CODE REVIEWER", seen["reviewer"])
        self.assertNotIn("Look at naming first.", seen["developer"])

    def test_an_adapter_that_takes_no_settings_is_called_as_before(self):
        self.set_global(developer={"claude": {"model": "opus"}})
        calls = []

        class Plain:
            def invoke(self, provider, role, prompt, workspace, run_dir):
                calls.append(role)
                if role == "developer":
                    (workspace / "feature.txt").write_text("good", encoding="utf-8")
                    return AgentReply(text="done", provider=provider)
                return AgentReply(text=json.dumps({"verdict": "APPROVED", "summary": "s", "issues": [],
                                                   "criteria": outcomes(prompt)}), provider=provider)
        task_id, _ = self.task()
        state = run_task(self.home, task_id, adapter=Plain())
        self.assertEqual((state["status"], calls), ("done", ["developer", "reviewer"]))
        self.assertEqual(state["agent_runs"]["001:developer"]["values"], {"model": "opus"})  # recorded all the same


class CommandLineTests(EngineCase):
    def new(self, *flags):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--home", str(self.home), "new", "--title", "Feature", "--description", "Create feature.txt", *flags])
        found = re.search(r"T-[a-f0-9]{12}", out.getvalue())
        return code, found.group(0) if found else None, err.getvalue()

    def test_new_takes_a_model_and_an_effort_for_each_role_of_that_task(self):
        code, task_id, _ = self.new("--developer-model", "opus", "--reviewer-effort", "high")
        self.assertEqual(code, 0)
        self.assertEqual(read_json(self.home / "tasks" / task_id / "state.json")["agent_settings"],
                         {"developer": {"claude": {"model": "opus"}}, "reviewer": {"codex": {"effort": "high"}}})
        code, plain, _ = self.new()
        self.assertEqual(code, 0)
        self.assertNotIn("agent_settings", read_json(self.home / "tasks" / plain / "state.json"))  # the shape of earlier versions
        code, refused, error = self.new("--reviewer-effort", "extreme")
        self.assertEqual((code, refused), (1, None))
        self.assertIn("Reasoning effort", error)
        self.assertEqual(len(list((self.home / "tasks").glob("T-*"))), 2)  # nothing was created for the refused one

    def test_run_from_a_terminal_uses_the_settings_and_the_report_lists_them(self):
        agents.update_store(self.home, lambda store: store.__setitem__("settings", agents.clean_layer(
            {"developer": {"claude": {"model": "sonnet", "instructions": f"token: {FAKE_KEY}"}}})))
        _, task_id, _ = self.new("--developer-effort", "low")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--home", str(self.home), "run", task_id]), 0)
        developer = next(call["args"] for call in self.cli.calls() if call["role"] == "developer")
        self.assertEqual(developer[-4:], ["--model", "sonnet", "--effort", "low"])
        report = (self.home / "tasks" / task_id / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Agent settings used", report)
        self.assertIn("iteration 1 · developer · claude · effort `low` (task)", report)
        self.assertIn("model `sonnet` (global)", report)
        self.assertNotIn(FAKE_KEY, report)  # what is shown is masked
        self.assertIn("iteration 1 · reviewer · codex · CLI defaults", report)
        # A task that used no settings has no such section.
        _, plain, _ = self.new()
        (self.home / agents.STORE).unlink()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--home", str(self.home), "run", plain]), 0)
        self.assertNotIn("Agent settings used", (self.home / "tasks" / plain / "report.md").read_text(encoding="utf-8"))


class ApiTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.project, self.repo = self.add_project()
        self.pid = self.project["id"]

    def save(self, expect=200, **body):
        response, data = self.patch_("/api/agents", body)
        self.assertEqual(response.status, expect, data)
        return data

    def view(self, query=""):
        response, data = self.get(f"/api/agents{query}")
        self.assertEqual(response.status, 200, data)
        return data

    def test_the_page_gets_both_roles_both_providers_and_where_every_value_comes_from(self):
        view = self.view()
        self.assertEqual((view["level"], sorted(view["roles"]), sorted(view["providers"])), ("global", ["developer", "reviewer"], ["claude", "codex"]))
        developer = view["roles"]["developer"]
        self.assertEqual((developer["provider"], developer["provider_source"], developer["blocks"]["claude"]["preset"]), ("claude", "global", "default"))
        self.assertEqual(set(developer["blocks"]["claude"]["sources"].values()), {"default"})
        self.assertIn("--permission-mode dontAsk", developer["blocks"]["claude"]["preview"]["line"])
        self.assertEqual([preset["id"] for preset in view["presets"]], ["default", "fast", "balanced", "quality"])
        self.assertEqual(view["providers"]["claude"]["catalog"]["source"], "documented")
        self.assertEqual(view["providers"]["codex"]["catalog"]["models"], [])  # nothing was discovered: no list is made up
        self.assertTrue(all(field["status"] in {"unknown", "builtin"} for field in view["providers"]["codex"]["schema"]["developer"]))

    def test_three_levels_are_saved_where_the_engine_reads_them(self):
        self.save(role="developer", blocks={"claude": {"model": "sonnet", "effort": "low"}})
        self.assertEqual(read_json(self.home / agents.STORE)["settings"], {"developer": {"claude": {"model": "sonnet", "effort": "low"}}})
        view = self.save(project=self.pid, role="developer", blocks={"claude": {"effort": "high"}})
        self.assertEqual(read_json(Path(self.project["home"]) / "config.json")["agent_settings"], {"developer": {"claude": {"effort": "high"}}})
        block = view["roles"]["developer"]["blocks"]["claude"]
        self.assertEqual((view["level"], block["values"]["model"], block["values"]["effort"], block["sources"]["model"], block["sources"]["effort"], block["own"]),
                         ("project", "sonnet", "high", "global", "project", {"effort": "high"}))
        task_id, path = self.add_task(self.project)
        view = self.save(project=self.pid, task=task_id, role="developer", blocks={"claude": {"model": "opus"}})
        self.assertEqual(read_json(path / "state.json")["agent_settings"], {"developer": {"claude": {"model": "opus"}}})
        block = view["roles"]["developer"]["blocks"]["claude"]
        self.assertEqual((view["level"], block["values"]["model"], block["sources"]["model"], block["sources"]["effort"], block["preset"]),
                         ("task", "opus", "task", "project", "quality"))
        self.assertIn("--model opus --effort high", block["preview"]["line"])
        # The engine resolves the same thing from the same files.
        layers = agents.layers(self.home, read_json(Path(self.project["home"]) / "config.json"), read_json(path / "state.json"))
        self.assertEqual(agents.freeze("claude", "developer", layers)["values"], {"model": "opus", "effort": "high"})
        # The reviewer and the other provider were never touched.
        self.assertEqual(self.view()["roles"]["reviewer"]["blocks"]["claude"]["own"], {})
        # An empty block clears a level; null in a block asks for the default against an outer value.
        view = self.save(project=self.pid, role="developer", blocks={"claude": {"model": None}})
        self.assertEqual((view["roles"]["developer"]["blocks"]["claude"]["values"]["model"], view["roles"]["developer"]["blocks"]["claude"]["sources"]["model"]),
                         (None, "project"))
        self.save(project=self.pid, role="developer", blocks={"claude": {}})
        self.assertNotIn("agent_settings", read_json(Path(self.project["home"]) / "config.json"))

    def test_who_plays_a_role_is_chosen_globally_or_per_project_and_fixed_in_a_task(self):
        self.save(role="reviewer", provider="claude")
        self.assertEqual(self.app.ws.settings()["defaults"], {"developer": "claude", "reviewer": "claude"})
        view = self.save(project=self.pid, role="reviewer", provider="codex")
        self.assertEqual((view["roles"]["reviewer"]["provider"], view["roles"]["reviewer"]["provider_source"]), ("codex", "project"))
        self.assertEqual(read_json(Path(self.project["home"]) / "config.json")["agents"], {"reviewer": "codex"})
        view = self.save(project=self.pid, role="reviewer", provider=None)
        self.assertEqual((view["roles"]["reviewer"]["provider"], view["roles"]["reviewer"]["provider_source"]), ("claude", "global"))
        task_id, _ = self.add_task(self.project)
        self.save(400, project=self.pid, task=task_id, role="reviewer", provider="claude")
        self.save(400, role="reviewer", provider=None)
        self.save(400, role="reviewer", provider="gemini")

    def test_invalid_or_risky_settings_are_refused_and_nothing_is_written(self):
        for body in ({"role": "judge", "blocks": {}}, {"role": "developer", "blocks": {"gemini": {}}}, {"role": "developer", "extra": 1},
                     {"role": "developer", "blocks": {"codex": {"sandbox": "danger-full-access"}}},
                     {"role": "reviewer", "blocks": {"codex": {"sandbox": "workspace-write"}}},
                     {"role": "developer", "blocks": {"claude": {"extra_args": ["--dangerously-skip-permissions"], agents.ACK: True}}},
                     {"role": "developer", "blocks": {"claude": {"add_dirs": [str(self.home)], agents.ACK: True}}},
                     {"role": "developer", "project": self.pid, "blocks": {"codex": {"add_dirs": [str(self.repo)], agents.ACK: True}}},
                     {"role": "developer", "task": "T-000000000000", "blocks": {}}):
            with self.subTest(body=body):
                self.save(400, **body)
        self.assertFalse((self.home / agents.STORE).exists())
        saved = self.save(role="developer", blocks={"codex": {"sandbox": "danger-full-access", agents.ACK: True}})
        block = saved["roles"]["developer"]["blocks"]["codex"]
        self.assertEqual(([risk["key"] for risk in block["risks"]], block["own"][agents.ACK]), (["sandbox"], True))
        self.assertIn("--sandbox danger-full-access", block["preview"]["line"])
        self.assertIn("--sandbox read-only", saved["roles"]["reviewer"]["blocks"]["codex"]["preview"]["line"])

    def test_a_running_or_finished_task_does_not_take_new_settings(self):
        task_id, path = self.add_task(self.project)
        (path / ".run.lock").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        self.save(409, project=self.pid, task=task_id, role="developer", blocks={"claude": {"model": "opus"}})
        self.assertTrue(self.view(f"?project={self.pid}&task={task_id}")["locked"])
        (path / ".run.lock").unlink()
        state = read_json(path / "state.json")
        save_json(path / "state.json", {**state, "status": "done"})
        self.save(409, project=self.pid, task=task_id, role="developer", blocks={"claude": {"model": "opus"}})
        self.assertNotIn("agent_settings", read_json(path / "state.json"))

    def test_test_configuration_checks_the_form_as_it_is_and_starts_nothing(self):
        with patch("patchrondo.providers.execute", side_effect=AssertionError("a provider command was started")):
            response, data = self.post("/api/agents/test", {"project": self.pid, "role": "developer", "provider": "claude",
                                                            "values": {"model": "opus", "effort": "high"}})
        self.assertEqual(response.status, 200, data)
        checks = {check["id"]: check["status"] for check in data["checks"]}
        self.assertEqual((data["ok"], checks["model"], checks["folder.project repository"], data["model"]), (True, "warning", "passed", "opus"))
        self.assertIn("--model opus --effort high", data["preview"]["line"])
        self.assertFalse((self.home / agents.STORE).exists())  # testing saves nothing
        response, data = self.post("/api/agents/test", {"role": "reviewer", "provider": "codex", "values": {"sandbox": "workspace-write"}})
        self.assertEqual(response.status, 400)
        self.assertIn("read-only", data["error"])
        for body in ({"role": "developer"}, {"role": "developer", "provider": "claude", "more": 1}):
            self.assertEqual(self.post("/api/agents/test", body)[0].status, 400)

    def test_a_test_call_needs_consent_and_its_answer_is_remembered(self):
        with patch("patchrondo.providers.execute", side_effect=AssertionError("a provider command was started")):
            for body in ({"provider": "claude", "model": "opus"}, {"provider": "claude", "model": "opus", "confirm": "yes"},
                         {"provider": "claude", "model": "opus", "confirm": 1}, {"provider": "claude", "confirm": True},
                         {"provider": "claude", "model": "--bad", "confirm": True}, {"provider": "x", "model": "opus", "confirm": True}):
                with self.subTest(body=body):
                    self.assertEqual(self.post("/api/agents/verify", body)[0].status, 400)
        with patch("patchrondo.providers.execute", return_value=Result(0, json.dumps({"result": "OK"}), "")) as execute:
            response, data = self.post("/api/agents/verify", {"provider": "claude", "model": "opus", "effort": "low", "confirm": True})
        self.assertEqual((response.status, data["ok"], execute.call_count), (200, True, 1))
        self.assertEqual(execute.call_args.args[1], self.home / "verify")
        models = {entry["id"]: entry for entry in self.view()["providers"]["claude"]["catalog"]["models"]}
        self.assertTrue(models["opus"]["verified"]["ok"])
        self.assertIsNone(models["sonnet"]["verified"])
        refusal = Result(1, "", "There's an issue with the selected model (opus). It may not exist or you may not have access to it.")
        with patch("patchrondo.providers.execute", return_value=refusal):
            data = self.post("/api/agents/verify", {"provider": "claude", "model": "opus", "confirm": True})[1]
        self.assertEqual((data["ok"], data["kind"]), (False, "model_unavailable"))
        data = self.post("/api/agents/test", {"role": "developer", "provider": "claude", "values": {"model": "opus"}})[1]
        self.assertEqual(({check["id"]: check["status"] for check in data["checks"]}["model"], data["ok"]), ("failed", False))

    def test_custom_presets_and_models_are_saved_duplicated_changed_and_removed(self):
        response, data = self.post("/api/agents/presets", {"name": "Deep review", "values": {"claude": {"model": "opus", "effort": "max"}}})
        self.assertEqual(response.status, 200, data)
        first = data["preset"]["id"]
        copy = self.post("/api/agents/presets", {"name": "Deep review copy", "values": data["preset"]["values"]})[1]["preset"]
        self.assertNotEqual(copy["id"], first)
        self.assertEqual(self.post("/api/agents/presets", {"name": "deep REVIEW", "values": {"claude": {"model": "x"}}})[0].status, 400)
        changed = self.post("/api/agents/presets", {"id": first, "name": "Deeper review", "values": {"claude": {"model": "fable"}}})[1]
        self.assertEqual([(preset["name"], preset["builtin"]) for preset in changed["presets"]][4:], [("Deeper review", False), ("Deep review copy", False)])
        self.assertEqual(self.post("/api/agents/presets", {"name": "x", "values": {"codex": {"sandbox": "read-only"}}})[0].status, 400)
        self.save(role="reviewer", blocks={"claude": {"model": "fable"}})
        self.assertEqual(self.view()["roles"]["reviewer"]["blocks"]["claude"]["preset"], first)
        self.assertEqual(self.delete(f"/api/agents/presets/{copy['id']}")[0].status, 200)
        self.assertEqual(self.delete(f"/api/agents/presets/{copy['id']}")[0].status, 404)
        self.assertEqual(self.delete("/api/agents/presets/fast")[0].status, 404)  # built-in presets cannot be removed
        self.assertEqual(len(self.view()["presets"]), 5)
        response, data = self.post("/api/agents/models", {"provider": "codex", "id": "my-fine-tune"})
        self.assertEqual([(entry["id"], entry["source"]) for entry in data["catalog"]["models"]], [("my-fine-tune", "custom")])
        self.assertEqual(self.post("/api/agents/models", {"provider": "codex", "id": "--x"})[0].status, 400)
        self.assertEqual(self.post("/api/agents/models", {"provider": "codex", "id": "my-fine-tune", "remove": True})[1]["catalog"]["models"], [])

    def test_what_a_cli_offers_reaches_the_page_and_incompatible_options_are_marked(self):
        offered = {"flags": [flag for flag in doctor.FLAG.findall(CODEX_HELP) if flag != "--profile"], "models": doctor.catalog_models(CATALOG),
                   "models_source": "cli", "profiles": ["work"]}
        self.check_all.return_value = {**self.check_all.return_value, "codex": {**self.check_all.return_value["codex"], "capabilities": offered}}
        self.post("/api/providers/refresh")
        deadline = datetime.now() + timedelta(seconds=10)
        while not self.app.providers.capabilities.get("codex") and datetime.now() < deadline:
            pass
        view = self.view()
        codex = view["providers"]["codex"]
        self.assertEqual(([entry["id"] for entry in codex["catalog"]["models"]], codex["catalog"]["source"], codex["profiles"], codex["discovered"]),
                         (["gpt-test-large"], "cli", ["work"], True))
        fields = {field["key"]: field["status"] for field in codex["schema"]["developer"]}
        self.assertEqual((fields["model"], fields["profile"]), ("listed", "unlisted"))
        self.assertNotIn("capabilities", self.get("/api/providers")[1]["providers"]["codex"])  # kept on the server
        data = self.post("/api/agents/test", {"role": "developer", "provider": "codex", "values": {"profile": "work"}})[1]
        self.assertEqual(({check["id"]: check["status"] for check in data["checks"]}["options"], data["ok"]), ("failed", False))

    def test_a_new_task_can_bring_its_own_settings_and_the_task_page_shows_what_is_used(self):
        self.save(role="developer", blocks={"claude": {"model": "sonnet"}})
        task_id, path = self.add_task(self.project, agent_settings={"developer": {"claude": {"effort": "max"}}})
        self.assertEqual(read_json(path / "state.json")["agent_settings"], {"developer": {"claude": {"effort": "max"}}})
        response, data = self.post(f"/api/projects/{self.pid}/tasks", {"title": "T", "description": "d", "developer": "claude", "reviewer": "codex",
                                                                         "agent_settings": {"reviewer": {"codex": {"sandbox": "workspace-write"}}}})
        self.assertEqual(response.status, 400, data)
        detail = self.get(f"/api/projects/{self.pid}/tasks/{task_id}")[1]
        developer = detail["agents"]["roles"]["developer"]
        self.assertEqual((detail["agents"]["pending"], developer["frozen"], developer["current"]["values"], developer["differs"]),
                         ("developer", None, {"model": "sonnet", "effort": "max"}, False))
        self.assertEqual(detail["summary"]["agents"]["developer"], {"provider": "claude", "model": None, "effort": None, "frozen": False})
        # A step that started keeps its record; an edit is shown as waiting, and applied only when asked.
        state = read_json(path / "state.json")
        state["agent_runs"] = {"001:developer": agents.freeze("claude", "developer", agents.layers(self.home, None, state))}
        save_json(path / "state.json", {**state, "status": "paused"})
        self.save(role="developer", blocks={"claude": {"model": "opus"}})
        detail = self.get(f"/api/projects/{self.pid}/tasks/{task_id}")[1]
        developer = detail["agents"]["roles"]["developer"]
        self.assertEqual((developer["frozen"]["values"]["model"], developer["current"]["values"]["model"], developer["differs"]), ("sonnet", "opus", True))
        self.assertEqual(detail["summary"]["agents"]["developer"]["model"], "sonnet")
        response, data = self.post(f"/api/projects/{self.pid}/tasks/{task_id}/agents/refresh")
        self.assertEqual((response.status, data), (200, {"refreshed": ["developer"]}))
        saved = read_json(path / "state.json")
        self.assertEqual((saved["agent_runs"], saved["history"][-1]["event"]), ({}, "agent_settings_refreshed"))
        self.assertEqual(self.post(f"/api/projects/{self.pid}/tasks/{task_id}/agents/refresh")[1], {"refreshed": []})
        (path / ".run.lock").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        self.assertEqual(self.post(f"/api/projects/{self.pid}/tasks/{task_id}/agents/refresh")[0].status, 409)

    def test_a_run_started_here_is_told_where_the_global_settings_are(self):
        task_id, _ = self.add_task(self.project)
        with patch("patchrondo.app.subprocess.Popen") as popen:
            popen.return_value.wait.return_value = 0
            popen.return_value.pid = 4242
            self.post(f"/api/projects/{self.pid}/tasks/{task_id}/run")
        self.assertEqual(popen.call_args.kwargs["env"][agents.WORKSPACE], str(self.home))
        with patch.dict(os.environ, {agents.WORKSPACE: str(self.home)}):
            self.assertEqual(agents.workspace_root(Path(self.project["home"])), self.home)


if __name__ == "__main__":
    unittest.main()
