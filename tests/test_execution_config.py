"""Adaptive Execution settings (Phase 2.5, Step 02): the optional `execution` section and the limits it resolves to.

Nothing here reaches a real provider CLI. The section is validated by
`core.validate_config` and resolved by `execution.resolve`, which no run uses
yet: whole tasks run against the scripted CLI of `support` to show that a
configuration with execution enabled starts the same commands, writes the same
state and makes the same decisions as one without it.
"""
import copy
import itertools
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from patchrondo import agents, core, execution, gate
from patchrondo.core import create_task, run_task, validate_config, validate_overrides
from patchrondo.execution import (BudgetState, CallLimits, ExecutionLimits, ExecutionProfile, ExecutionRecord,
                                  InvalidSettings, Phase, Scope, Source, Termination, Unit, Usage, Availability,
                                  AttemptOutcome)
from patchrondo.process import Result
from patchrondo.providers import OfficialCLI
from patchrondo.storage import read_json, save_json
from test_live_activity import EngineCase, jsonl

TASK = "T-0123456789ab"
T0, T1 = "2026-11-08T10:00:00+00:00", "2026-11-08T10:05:00+00:00"
HOME = Path(tempfile.gettempdir()).resolve() / "patchrondo-execution-home"
REPO = Path(tempfile.gettempdir()).resolve() / "patchrondo-execution-repo"
STANDARD = {"enabled": True, "profile": "standard"}
CUSTOM_LIMITS = {
    "call": {"developer": {"claude_turns": 30, "seconds": 1200}, "reviewer": {"claude_turns": 10, "seconds": 600}},
    "phase": {"developer": {"claude_turns": 90, "provider_calls": 3, "seconds": 3600},
              "reviewer": {"claude_turns": 20, "provider_calls": 2, "seconds": 1200}},
    "task": {"provider_calls": 12, "seconds": 20000},
}


def configuration(section=None, **workflow) -> dict:
    """A valid project configuration, with an `execution` section when one is given."""
    cfg = copy.deepcopy(core.DEFAULT_CONFIG)
    cfg["repository"] = str(REPO)
    cfg["workflow"].update(workflow)
    if section is not None:
        cfg["execution"] = copy.deepcopy(section)
    return cfg


def validated(section) -> dict:
    return validate_config(configuration(section), HOME)


def resolve(section=None, provider="claude", role="developer", values=None, workflow=None, **options):
    return execution.resolve(configuration(section, **(workflow or {})), provider, role, values, **options)


class SectionTests(unittest.TestCase):
    """What `validate_config` accepts and refuses, and that it leaves the section as written."""

    def refused(self, section, *words):
        with self.assertRaises(ValueError) as caught:
            validated(section)
        for word in words:
            self.assertIn(word, str(caught.exception))
        self.assertIsInstance(caught.exception, InvalidSettings)
        return caught.exception

    def test_a_configuration_without_the_section_gains_nothing(self):
        cfg = validated(None)
        self.assertNotIn("execution", cfg)
        self.assertIsNone(execution.settings_of(cfg))

    def test_a_section_is_validated_and_kept_as_written(self):
        for section in ({"enabled": False}, {"enabled": False, "profile": "deep_autonomous"}, STANDARD,
                        {"enabled": True, "profile": "fast", "adaptive": True, "no_progress_windows": 3,
                         "continuation": {"checkpoint_resume": False, "native_resume": "off"},
                         "limits": {"task": {"provider_calls": 10}}},
                        {"enabled": True, "profile": "custom", "limits": CUSTOM_LIMITS}):
            with self.subTest(section=section):
                written = copy.deepcopy(section)
                cfg = validated(section)
                self.assertEqual(cfg["execution"], written)
                self.assertEqual(set(cfg), set(configuration(section)) | {"rag", "recovery"})

    def test_defaults_of_the_optional_fields(self):
        chosen = execution.settings_of(configuration(STANDARD))
        self.assertEqual((chosen.adaptive, chosen.checkpoint_resume, chosen.native_resume, chosen.no_progress_windows,
                          chosen.explicit), (False, True, "off", 2, frozenset()))
        off = execution.settings_of(configuration({"enabled": False}))
        self.assertEqual((off.enabled, off.profile, off.limits), (False, None, None))

    def test_each_profile_is_accepted_with_its_limits(self):
        for profile in ("fast", "standard", "deep_autonomous"):
            with self.subTest(profile=profile):
                chosen = execution.settings_of(validated({"enabled": True, "profile": profile}))
                self.assertIs(chosen.profile, ExecutionProfile(profile))
                self.assertEqual(chosen.limits, ExecutionLimits.of(execution.PROFILES[ExecutionProfile(profile)]))

    def test_a_custom_profile_takes_its_limits_as_given(self):
        chosen = execution.settings_of(validated({"enabled": True, "profile": "custom", "limits": CUSTOM_LIMITS}))
        self.assertEqual(chosen.limits.to_dict(), CUSTOM_LIMITS)
        self.assertEqual(len(chosen.explicit), 2 * 2 + 2 * 3 + 2)

    def test_a_custom_profile_must_set_every_limit(self):
        self.refused({"enabled": True, "profile": "custom"}, "every limit")
        for path in (("task",), ("phase", "reviewer"), ("call", "developer", "seconds"), ("phase", "developer", "provider_calls"),
                     ("task", "seconds")):
            limits = copy.deepcopy(CUSTOM_LIMITS)
            holder = limits
            for key in path[:-1]:
                holder = holder[key]
            del holder[path[-1]]
            with self.subTest(missing=path):
                self.refused({"enabled": True, "profile": "custom", "limits": limits}, "custom")

    def test_an_invalid_profile_is_refused(self):
        # Agent presets and gate policies are not execution profiles, and a profile is spelled as listed.
        for profile in ("turbo", "Standard", "balanced", "quality", "default", "strict", "", 1, True, None, ["fast"]):
            with self.subTest(profile=profile):
                self.refused({"enabled": True, "profile": profile}, "execution.profile")

    def test_enabling_needs_a_profile_and_limits_need_one(self):
        self.refused({"enabled": True}, "execution.profile is required")
        self.refused({"enabled": False, "limits": {"task": {"provider_calls": 3}}}, "need an execution.profile")
        self.refused({"profile": "standard"}, "execution.enabled is required")

    def test_the_section_and_its_flags_have_their_types(self):
        for section, word in ((None, None), ([], "JSON object"), ("on", "JSON object"), (True, "JSON object"),
                              ({"enabled": 1, "profile": "fast"}, "execution.enabled"),
                              ({"enabled": "true", "profile": "fast"}, "execution.enabled"),
                              ({"enabled": True, "profile": "fast", "adaptive": "yes"}, "execution.adaptive"),
                              ({"enabled": True, "profile": "fast", "adaptive": 0}, "execution.adaptive"),
                              ({"enabled": True, "profile": "fast", "continuation": {"checkpoint_resume": 1}},
                               "checkpoint_resume")):
            if section is None:
                cfg = configuration()
                cfg["execution"] = None  # a null is not a missing section
                with self.assertRaises(InvalidSettings):
                    validate_config(cfg, HOME)
                continue
            with self.subTest(section=section):
                self.refused(section, word)

    def test_unknown_nested_fields_are_refused(self):
        for section, word in (
                ({"enabled": False, "profiles": "fast"}, "unknown fields: profiles"),
                ({"enabled": True, "profile": "fast", "limits": {"step": {}}}, "unknown fields: step"),
                ({"enabled": True, "profile": "fast", "limits": {"call": {"tester": {"seconds": 5}}}}, "tester"),
                ({"enabled": True, "profile": "fast", "limits": {"call": {"developer": {"turns": 5}}}}, "turns"),
                ({"enabled": True, "profile": "fast", "limits": {"task": {"claude_turns": 5}}}, "claude_turns"),
                ({"enabled": True, "profile": "fast", "limits": {"call": {"developer": {"provider_calls": 1}}}},
                 "provider_calls"),
                ({"enabled": True, "profile": "fast", "limits": {"phase": []}}, "JSON object"),
                ({"enabled": True, "profile": "fast", "continuation": {"max_per_phase": 3}}, "max_per_phase"),
                ({"enabled": True, "profile": "fast", "continuation": "on"}, "JSON object"),
                # A profile sets limits, never a permission: such fields do not exist in the section.
                ({"enabled": True, "profile": "fast", "sandbox": "danger-full-access"}, "sandbox"),
                ({"enabled": True, "profile": "fast", "tools": ["Bash"]}, "tools"),
                ({"enabled": True, "profile": "fast", "limits": {"call": {"developer": {"tools": ["Bash"]}}}}, "tools")):
            with self.subTest(section=section):
                self.refused(section, word)

    def test_tokens_are_never_a_limit(self):
        for limits in ({"task": {"tokens": 1000}}, {"phase": {"developer": {"tokens": 10}}}):
            with self.subTest(limits=limits):
                self.refused({"enabled": True, "profile": "standard", "limits": limits}, "tokens are observed only")

    def test_native_resume_stays_off(self):
        for value in ("probe_then_use", "on", True, None, ["off"]):
            with self.subTest(value=value):
                self.refused({"enabled": True, "profile": "standard", "continuation": {"native_resume": value}},
                             "native_resume")

    def test_a_limit_is_a_whole_number_within_its_hard_limit(self):
        hard = execution.HARD_LIMITS
        for value in (True, False, 0, -1, 40.0, "40", None, [40], 10**30, hard["call", Unit.CLAUDE_TURNS] + 1):
            with self.subTest(value=value):
                self.refused({"enabled": True, "profile": "custom",
                              "limits": {**CUSTOM_LIMITS, "call": {**CUSTOM_LIMITS["call"],
                                                                   "developer": {"claude_turns": value, "seconds": 1200}}}},
                             "execution.limits.call.developer.claude_turns", "whole number")
        for scale, unit, high in (("phase", "provider_calls", execution.ORDINAL_LIMIT),
                                  ("phase", "seconds", execution.BUDGET_LIMIT)):
            limits = copy.deepcopy(CUSTOM_LIMITS)
            limits[scale]["developer"][unit] = high + 1
            with self.subTest(scale=scale, unit=unit):
                self.refused({"enabled": True, "profile": "custom", "limits": limits}, f"from 1 to {high}")
        limits = copy.deepcopy(CUSTOM_LIMITS)
        limits["task"]["provider_calls"] = execution.ATTEMPT_LIMIT + 1
        self.refused({"enabled": True, "profile": "custom", "limits": limits}, "task.provider_calls")
        for value in (0, 11, True, 2.0):
            with self.subTest(windows=value):
                self.refused({"enabled": True, "profile": "fast", "no_progress_windows": value}, "no_progress_windows")

    def test_inconsistent_budgets_are_refused_and_never_raised(self):
        cases = (
            (("call", "developer", "claude_turns", 91), "call.developer.claude_turns (91) is above execution.limits.phase.developer.claude_turns (90)"),
            (("call", "reviewer", "seconds", 1201), "call.reviewer.seconds (1201) is above execution.limits.phase.reviewer.seconds (1200)"),
            (("phase", "developer", "provider_calls", 13), "phase.developer.provider_calls (13) is above execution.limits.task.provider_calls (12)"),
            (("phase", "developer", "seconds", 20001), "phase.developer.seconds (20001) is above execution.limits.task.seconds (20000)"),
        )
        for (scale, role, unit, value), words in cases:
            limits = copy.deepcopy(CUSTOM_LIMITS)
            limits[scale][role][unit] = value
            with self.subTest(case=words):
                self.refused({"enabled": True, "profile": "custom", "limits": limits}, "inconsistent", words)
        # Narrowing a named profile's step below its call is a contradiction, not a reason to lower the call.
        self.refused({"enabled": True, "profile": "standard", "limits": {"phase": {"developer": {"claude_turns": 30}}}},
                     "call.developer.claude_turns (40) is above execution.limits.phase.developer.claude_turns (30)")
        self.refused({"enabled": True, "profile": "standard", "limits": {"task": {"provider_calls": 1}}},
                     "phase.developer.provider_calls (2) is above execution.limits.task.provider_calls (1)")

    def test_a_named_profile_can_only_be_narrowed(self):
        self.refused({"enabled": True, "profile": "standard", "limits": {"call": {"developer": {"claude_turns": 41}}}},
                     "(41) is above the standard profile's 40", "custom profile")
        self.refused({"enabled": True, "profile": "fast", "limits": {"task": {"seconds": 36001}}}, "fast profile")
        chosen = execution.settings_of(validated({"enabled": True, "profile": "standard",
                                                  "limits": {"call": {"developer": {"claude_turns": 20}},
                                                             "task": {"provider_calls": 10}}}))
        self.assertEqual((chosen.limits.call["developer"][Unit.CLAUDE_TURNS], chosen.limits.task[Unit.PROVIDER_CALLS],
                          chosen.limits.phase["developer"][Unit.CLAUDE_TURNS]), (20, 10, 80))

    def test_the_section_is_refused_even_while_disabled(self):
        self.refused({"enabled": False, "profile": "turbo"}, "execution.profile")
        self.refused({"enabled": False, "profile": "standard", "limits": {"task": {"provider_calls": 0}}}, "task.provider_calls")

    def test_a_task_cannot_override_execution_or_claude_turns(self):
        self.assertEqual(core.TASK_OVERRIDES, {"workflow": ("max_iterations", "agent_timeout_seconds", "test_timeout_seconds"),
                                               "recovery": ("enabled",)})
        for overrides in ({"execution": STANDARD}, {"workflow": {"claude_max_turns": 40}}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                validate_overrides(overrides)


class ProfileTests(unittest.TestCase):
    def test_the_profiles_hold_the_specifications_values(self):
        expected = {  # §5.1: turns per call and per step (developer, reviewer), timeout of a call, calls of a task
            "fast": ((25, 25), (12, 12), 1800, 20),
            "standard": ((40, 80), (15, 30), 2700, 60),
            "deep_autonomous": ((50, 150), (25, 50), 3600, 150),
        }
        for name, (developer, reviewer, seconds, calls) in expected.items():
            limits = ExecutionLimits.of(execution.PROFILES[ExecutionProfile(name)])
            with self.subTest(profile=name):
                for role, (per_call, per_step) in (("developer", developer), ("reviewer", reviewer)):
                    self.assertEqual((limits.call[role][Unit.CLAUDE_TURNS], limits.phase[role][Unit.CLAUDE_TURNS]),
                                     (per_call, per_step))
                    self.assertEqual(limits.call[role][Unit.SECONDS], seconds)
                    # Derived: enough calls for the step's turns and at least one continuation; seconds follow the calls.
                    step_calls = limits.phase[role][Unit.PROVIDER_CALLS]
                    self.assertEqual(step_calls, max(2, -(-per_step // per_call)))
                    self.assertEqual(limits.phase[role][Unit.SECONDS], step_calls * seconds)
                self.assertEqual(dict(limits.task), {Unit.PROVIDER_CALLS: calls, Unit.SECONDS: calls * seconds})
                limits.check()

    def test_profiles_stay_within_the_hard_limits_and_the_existing_settings(self):
        for profile, table in execution.PROFILES.items():
            limits = ExecutionLimits.of(table)
            for (scale, unit), high in execution.HARD_LIMITS.items():
                for role in (agents.ROLES if scale != "task" else (None,)):
                    with self.subTest(profile=profile, scale=scale, unit=unit, role=role):
                        self.assertTrue(1 <= limits.value(scale, role, unit) <= high)
            # A call's turns and timeout fit the bounds of the agent settings and of the workflow.
            for role in agents.ROLES:
                low, high = core.WORKFLOW_LIMITS["claude_max_turns"]
                option = agents.OPTIONS["claude"]["max_turns"]
                self.assertTrue(max(low, option["low"]) <= limits.call[role][Unit.CLAUDE_TURNS] <= min(high, option["high"]))
                low, high = core.WORKFLOW_LIMITS["agent_timeout_seconds"]
                self.assertTrue(low <= limits.call[role][Unit.SECONDS] <= high)
        # Claude turns are passed with `--max-turns`, an option PatchRondo already builds; Codex has none.
        self.assertIn("--max-turns", agents.DOCUMENTED["claude"])
        self.assertNotIn("max_turns", agents.OPTIONS["codex"])

    def test_profiles_are_not_agent_presets_or_gate_policies(self):
        self.assertEqual(gate.POLICY, "standard")
        self.assertNotIn("profile", gate.evaluate.__code__.co_varnames[:gate.evaluate.__code__.co_argcount
                                                                      + gate.evaluate.__code__.co_kwonlyargcount])
        for preset in agents.BUILTIN_PRESETS:  # presets choose a model and an effort and nothing about limits
            for values in preset["values"].values():
                self.assertLessEqual(set(values), {"model", "effort"})
        for table in execution.PROFILES.values():  # profiles choose limits and nothing else
            self.assertEqual(set(table), {"call", "phase", "task"})
            units = {unit for scale in ("call", "phase") for block in table[scale].values() for unit in block} | set(table["task"])
            self.assertLessEqual(units, execution.ENFORCEABLE)

    def test_the_resolved_limits_name_no_permission(self):
        limits = resolve({"enabled": True, "profile": "deep_autonomous"})
        self.assertEqual(set(limits.to_dict()), {"enabled", "profile", "provider", "role", "call_turns", "call_seconds",
                                                 "phase", "task", "notes"})

    def test_the_legacy_defaults_are_those_of_the_default_configuration(self):
        for key, value in execution.LEGACY_DEFAULTS.items():
            self.assertEqual(core.DEFAULT_CONFIG["workflow"][key], value)


class PrecedenceTests(unittest.TestCase):
    def test_off_resolves_what_the_adapter_passes_today(self):
        """Off (no section, or disabled): `max_turns or workflow turns`, `timeout_seconds or workflow timeout`, no budget."""
        for section, provider, role, turns, seconds, workflow_turns, workflow_seconds in itertools.product(
                (None, {"enabled": False}, {"enabled": False, "profile": "deep_autonomous"}), ("claude", "codex"),
                ("developer", "reviewer"), (None, 5, 100), (None, 60), (12, 30), (900, 1200)):
            if provider == "codex" and turns is not None:
                continue
            values = {key: value for key, value in (("max_turns", turns), ("timeout_seconds", seconds)) if value is not None}
            limits = resolve(section, provider, role, values,
                             workflow={"claude_max_turns": workflow_turns, "agent_timeout_seconds": workflow_seconds})
            with self.subTest(section=section, provider=provider, role=role, values=values):
                self.assertFalse(limits.enabled)
                self.assertIsNone(limits.profile)
                self.assertEqual(limits.call_turns.value, (values.get("max_turns") or workflow_turns) if provider == "claude" else None)
                self.assertEqual(limits.call_seconds.value, values.get("timeout_seconds") or workflow_seconds)
                self.assertEqual((limits.phase, limits.task, limits.notes), (None, None, ()))
                self.assertIsNone(limits.phase_limits())
                self.assertEqual(limits.call_turns.source, (Source.AGENT_SETTINGS if turns else Source.WORKFLOW)
                                 if provider == "claude" else Source.NOT_APPLICABLE)

    def test_off_agrees_with_the_adapter_and_the_command(self):
        """The resolved call limits are the `--max-turns` and the timeout the adapter really starts the CLI with."""
        class Started(Exception):
            pass

        seen = []

        def started(argv, cwd, **kwargs):
            seen.append((argv, kwargs["timeout"]))
            raise Started

        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        for provider, role, values in (("claude", "developer", {}), ("claude", "reviewer", {"max_turns": 7}),
                                       ("claude", "developer", {"timeout_seconds": 33, "max_turns": 90}),
                                       ("codex", "developer", {}), ("codex", "reviewer", {"timeout_seconds": 44})):
            seen.clear()
            adapter = OfficialCLI(timeout=321, claude_turns=17)
            with patch("patchrondo.providers.execute", side_effect=started), self.assertRaises(Started):
                adapter.invoke(provider, role, "prompt", root, root / "run", settings=values)
            argv, timeout = seen[0]
            limits = resolve(None, provider, role, values, workflow={"claude_max_turns": 17, "agent_timeout_seconds": 321})
            with self.subTest(provider=provider, role=role, values=values):
                self.assertEqual(timeout, limits.call_seconds.value)
                if provider == "claude":
                    self.assertEqual(argv[argv.index("--max-turns") + 1], str(limits.call_turns.value))
                else:
                    self.assertNotIn("--max-turns", argv)
                    self.assertIsNone(limits.call_turns.value)

    def test_on_a_profile_replaces_the_workflow_defaults_and_says_so(self):
        limits = resolve(STANDARD)
        self.assertEqual((limits.call_turns, limits.call_seconds), (execution.Limit(40, Source.PROFILE),
                                                                    execution.Limit(2700, Source.PROFILE)))
        self.assertEqual([(note.setting, note.value, note.effective, note.reason) for note in limits.notes],
                         [("workflow.claude_max_turns", 12, 40, "default_replaced"),
                          ("workflow.agent_timeout_seconds", 900, 2700, "default_replaced")])
        reviewer = resolve(STANDARD, role="reviewer")
        self.assertEqual((reviewer.call_turns.value, reviewer.phase_limits()),
                         (15, {Unit.CLAUDE_TURNS: 30, Unit.PROVIDER_CALLS: 2, Unit.SECONDS: 5400}))
        # A default equal to the profile's value is not reported as replaced.
        fast = resolve({"enabled": True, "profile": "fast"}, role="reviewer")
        self.assertEqual([note.setting for note in fast.notes], ["workflow.agent_timeout_seconds"])

    def test_on_a_configured_workflow_value_is_never_raised(self):
        lower = resolve(STANDARD, workflow={"claude_max_turns": 20, "agent_timeout_seconds": 600})
        self.assertEqual((lower.call_turns, lower.call_seconds), (execution.Limit(20, Source.WORKFLOW),
                                                                  execution.Limit(600, Source.WORKFLOW)))
        self.assertEqual(lower.notes, ())
        higher = resolve(STANDARD, workflow={"claude_max_turns": 70, "agent_timeout_seconds": 5000})
        self.assertEqual((higher.call_turns.value, higher.call_seconds.value), (40, 2700))
        self.assertEqual([(note.setting, note.value, note.reason) for note in higher.notes],
                         [("workflow.claude_max_turns", 70, "capped"), ("workflow.agent_timeout_seconds", 5000, "capped")])

    def test_on_a_timeout_the_task_overrides_is_configured_even_at_the_default_value(self):
        overrides = validate_overrides({"workflow": {"agent_timeout_seconds": 900}})
        limits = resolve(STANDARD, overrides=overrides)
        self.assertEqual(limits.call_seconds, execution.Limit(900, Source.TASK_OVERRIDE))
        self.assertEqual(resolve(None, overrides=overrides).call_seconds, execution.Limit(900, Source.TASK_OVERRIDE))

    def test_on_an_explicit_agent_limit_that_is_smaller_stays_effective(self):
        limits = resolve(STANDARD, values={"max_turns": 9, "timeout_seconds": 120},
                         agent_sources={"max_turns": "project", "timeout_seconds": "task"})
        self.assertEqual(limits.call_turns, execution.Limit(9, Source.AGENT_SETTINGS, "project"))
        self.assertEqual(limits.call_seconds, execution.Limit(120, Source.AGENT_SETTINGS, "task"))
        # The workflow defaults were not in effect for this call before either: nothing is reported as replaced.
        self.assertEqual(limits.notes, ())
        # Equal values are the user's.
        self.assertEqual(resolve(STANDARD, values={"max_turns": 40}).call_turns.source, Source.AGENT_SETTINGS)
        # A larger one is capped by the profile, and that is said; the budgets of the step are unchanged.
        capped = resolve(STANDARD, values={"max_turns": 500, "timeout_seconds": 86400})
        self.assertEqual((capped.call_turns.value, capped.call_seconds.value), (40, 2700))
        self.assertIn(("agent setting max_turns", 500, 40, "capped"),
                      [(note.setting, note.value, note.effective, note.reason) for note in capped.notes])
        self.assertEqual(capped.phase_limits(), resolve(STANDARD).phase_limits())

    def test_explicit_execution_limits_are_named_as_such(self):
        limits = resolve({"enabled": True, "profile": "standard", "limits": {"call": {"developer": {"claude_turns": 25}},
                                                                            "task": {"seconds": 100000}}})
        self.assertEqual(limits.call_turns, execution.Limit(25, Source.EXECUTION_LIMITS))
        self.assertEqual(limits.task[Unit.SECONDS], execution.Limit(100000, Source.EXECUTION_LIMITS))
        self.assertEqual(limits.task[Unit.PROVIDER_CALLS], execution.Limit(60, Source.PROFILE))
        custom = resolve({"enabled": True, "profile": "custom", "limits": CUSTOM_LIMITS}, role="reviewer")
        self.assertEqual({limit.source for limit in (custom.call_turns, custom.call_seconds, *custom.phase.values(),
                                                     *custom.task.values())}, {Source.EXECUTION_LIMITS})
        self.assertEqual((custom.call_turns.value, custom.phase_limits(), custom.task_limits()),
                         (10, {Unit.CLAUDE_TURNS: 20, Unit.PROVIDER_CALLS: 2, Unit.SECONDS: 1200},
                          {Unit.PROVIDER_CALLS: 12, Unit.SECONDS: 20000}))

    def test_the_nearest_level_of_the_agent_settings_still_decides_before_the_profile(self):
        layers = [("global", {"developer": {"claude": {"max_turns": 30}}}),
                  ("project", {"developer": {"claude": {"max_turns": 50}}}),
                  ("task", {"developer": {"claude": {"max_turns": 20}}})]
        for count, (level, expected_off, expected_on) in enumerate((("global", 30, 30), ("project", 50, 40),
                                                                     ("task", 20, 20)), 1):
            resolved = agents.resolve("claude", "developer", layers[:count])
            with self.subTest(level=level):
                self.assertEqual(resolved["sources"]["max_turns"], level)
                off = resolve(None, values=resolved["values"], agent_sources=resolved["sources"])
                on = resolve(STANDARD, values=resolved["values"], agent_sources=resolved["sources"])
                self.assertEqual((off.call_turns.value, off.call_turns.level), (expected_off, level))
                self.assertEqual(on.call_turns.value, expected_on)

    def test_a_frozen_step_record_is_read_and_left_as_it_is(self):
        layers = [("global", {}), ("project", {"developer": {"claude": {"max_turns": 25}}}), ("task", {})]
        record = agents.freeze("claude", "developer", layers)
        kept = copy.deepcopy(record)
        values = agents.thaw(record, "claude", "developer")
        limits = resolve(STANDARD, values=values, agent_sources=record["sources"])
        self.assertEqual(limits.call_turns, execution.Limit(25, Source.AGENT_SETTINGS, "project"))
        self.assertEqual(record, kept)
        self.assertEqual(record["digest"], agents.digest("claude", record["values"]))

    def test_without_an_agent_setting_the_workflow_value_is_the_fallback_only_while_off(self):
        self.assertEqual(resolve(None, workflow={"claude_max_turns": 12}).call_turns, execution.Limit(12, Source.WORKFLOW))
        self.assertEqual(resolve({"enabled": False, "profile": "standard"}).call_turns, execution.Limit(12, Source.WORKFLOW))
        self.assertEqual(resolve(STANDARD).call_turns, execution.Limit(40, Source.PROFILE))

    def test_step_and_task_budgets_do_not_depend_on_the_iterations(self):
        few = resolve(STANDARD, workflow={"max_iterations": 1})
        many = resolve(STANDARD, workflow={"max_iterations": 50})
        self.assertEqual((few.phase_limits(), few.task_limits()), (many.phase_limits(), many.task_limits()))
        self.assertEqual(few.task_limits(), {Unit.PROVIDER_CALLS: 60, Unit.SECONDS: 162000})

    def test_the_resolution_is_deterministic_and_changes_nothing_it_is_given(self):
        cfg = configuration({"enabled": True, "profile": "standard", "limits": {"task": {"provider_calls": 30}}},
                            claude_max_turns=50)
        values, sources, overrides = {"max_turns": 60}, {"max_turns": "global"}, {"workflow": {"agent_timeout_seconds": 100}}
        given = copy.deepcopy((cfg, values, sources, overrides))
        first = execution.resolve(cfg, "claude", "developer", values, agent_sources=sources, overrides=overrides)
        again = execution.resolve(cfg, "claude", "developer", values, agent_sources=sources, overrides=overrides)
        self.assertEqual(first, again)
        self.assertEqual(first.to_dict(), again.to_dict())
        self.assertEqual((cfg, values, sources, overrides), given)
        json.dumps(first.to_dict())  # plain JSON, for a later step to freeze with the step

    def test_unusable_inputs_are_refused(self):
        for provider, role, values, words in (("gemini", "developer", {}, "provider"), ("claude", "tester", {}, "role"),
                                              ("claude", "developer", {"max_turns": True}, "max_turns"),
                                              ("claude", "developer", {"max_turns": 0}, "max_turns"),
                                              ("claude", "developer", {"timeout_seconds": 86401}, "timeout_seconds")):
            with self.subTest(values=values), self.assertRaises(InvalidSettings) as caught:
                resolve(STANDARD, provider, role, values)
            self.assertIn(words, str(caught.exception))
        with self.assertRaises(InvalidSettings):
            execution.resolve({"repository": "x"}, "claude", "developer")
        with self.assertRaises(InvalidSettings):
            resolve({"enabled": True}, "claude", "developer")


class AccountingTests(unittest.TestCase):
    """Claude turns for Claude only; calls and seconds for both; unknown usage gives nothing back."""

    def test_claude_keeps_turns_calls_and_seconds_apart(self):
        limits = resolve(STANDARD, "claude", "developer")
        self.assertEqual(set(limits.phase), {Unit.CLAUDE_TURNS, Unit.PROVIDER_CALLS, Unit.SECONDS})
        self.assertEqual(set(limits.task), {Unit.PROVIDER_CALLS, Unit.SECONDS})
        self.assertEqual((limits.call_turns.value, limits.call_seconds.value), (40, 2700))

    def test_codex_is_limited_by_calls_and_seconds_and_never_by_claude_turns(self):
        for section in (None, STANDARD, {"enabled": True, "profile": "custom", "limits": CUSTOM_LIMITS}):
            for role in agents.ROLES:
                limits = resolve(section, "codex", role)
                with self.subTest(section=section, role=role):
                    self.assertEqual(limits.call_turns, execution.Limit(None, Source.NOT_APPLICABLE))
                    if limits.enabled:
                        self.assertEqual(set(limits.phase), {Unit.PROVIDER_CALLS, Unit.SECONDS})
                        self.assertNotIn(Unit.TOKENS, limits.task)
        with self.assertRaises(InvalidSettings) as caught:
            resolve(STANDARD, "codex", "developer", {"max_turns": 10})
        self.assertIn("Codex has no Claude turns", str(caught.exception))

    def test_the_resolved_limits_open_the_budgets_of_step_01(self):
        for provider in ("claude", "codex"):
            limits = resolve(STANDARD, provider, "developer")
            record = ExecutionRecord.open(TASK, limits.task_limits()).begin_step(
                1, Phase.DEVELOP, provider, limits.phase_limits(), CallLimits.of(limits), limits.profile)
            record, attempt = record.new_attempt(started_at=T0)
            with self.subTest(provider=provider):
                self.assertEqual(attempt.turn_limit, limits.call_turns.value)
                self.assertEqual(record.step.budget.scope, Scope.PHASE)
                self.assertEqual(record.budget, BudgetState.from_dict(record.budget.to_dict()))
                self.assertEqual(record.step.budget.line(Unit.CLAUDE_TURNS) is None, provider == "codex")

    def test_unknown_usage_is_charged_as_the_most_the_call_was_allowed(self):
        limits = resolve(STANDARD, "claude", "developer")
        opened = ExecutionRecord.open(TASK, limits.task_limits()).begin_step(
            1, Phase.DEVELOP, "claude", limits.phase_limits(), CallLimits.of(limits), limits.profile)

        def after(turns):
            record, attempt = opened.new_attempt(started_at=T0)
            ended = attempt.end(AttemptOutcome.COMPLETED, ended_at=T1, elapsed_seconds=300, turns=turns,
                                termination=Termination.EXITED)
            return record.update(ended)

        unknown = after(Usage.unknown(Unit.CLAUDE_TURNS))
        observed = after(Usage(Unit.CLAUDE_TURNS, Availability.OBSERVED, 10))
        self.assertIs(unknown.step.last.turns.availability, Availability.UNKNOWN)  # never recorded as 0
        self.assertEqual(unknown.step.budget.line(Unit.CLAUDE_TURNS).remaining, 80 - 40)
        self.assertEqual(observed.step.budget.line(Unit.CLAUDE_TURNS).remaining, 80 - 10)
        self.assertLessEqual(unknown.step.budget.line(Unit.CLAUDE_TURNS).remaining,
                             observed.step.budget.line(Unit.CLAUDE_TURNS).remaining)
        self.assertFalse(unknown.step.budget.line(Unit.CLAUDE_TURNS).allows(41))
        # Unknown tokens are charged to no limit: there is none to charge, and none can be configured.
        self.assertIsNone(unknown.budget.line(Unit.TOKENS))
        # A call that never ended is charged its whole timeout and one call.
        running, _ = opened.new_attempt(started_at=T0)
        self.assertEqual(running.step.budget.line(Unit.SECONDS).used, 2700)
        self.assertEqual(running.budget.line(Unit.PROVIDER_CALLS).used, 1)


class EngineCompatibilityTests(EngineCase):
    """Whole tasks: off, the run is the run without the section; on (Step 04), it decides exactly as without it and
    adds only the record of its calls, their folders and the profile's call limits."""

    def set_execution(self, section):
        cfg = read_json(self.home / "config.json")
        if section is None:
            cfg.pop("execution", None)
        else:
            cfg["execution"] = section
        save_json(self.home / "config.json", cfg)

    @staticmethod
    def shape(state):
        """What a run decided and saved, without times and the task's own ID."""
        return {"status": state["status"], "phase": state["phase"], "iteration": state["iteration"],
                "keys": sorted(state), "events": [event["event"] for event in state["history"]],
                "agent_runs": {key: record["values"] for key, record in state["agent_runs"].items()},
                "error": (state.get("last_error") or {}).get("kind")}

    @staticmethod
    def files(path, recursive=False):
        """The names written in the iteration's folder; the verification runner's own token taken out of its logs."""
        folder = path / "runs" / "iteration-001"
        return sorted(re.sub(r"^[0-9a-f]{8}-", "", item.name) for item in (folder.rglob("*") if recursive else folder.iterdir())
                      if item.is_file())

    @staticmethod
    def decided(state):
        """`shape` without what only a recorded run adds: its attempt events and its `execution` record."""
        shape = EngineCompatibilityTests.shape(state)
        shape["events"] = [name for name in shape["events"] if not name.startswith("attempt_")]
        shape["keys"] = [key for key in shape["keys"] if key != execution.KEY]
        return shape

    @staticmethod
    def without_limits(call):
        """A command without the values of `--max-turns`, which a profile sets, and without the attempt's folder in the
        paths of Codex's answer and schema, which a recorded call writes there."""
        return [re.sub(r"[\\/](?:develop|review)[\\/]attempt-\d{3}(?=[\\/])", "", arg)
                for index, arg in enumerate(call) if index == 0 or call[index - 1] != "--max-turns"]

    def run_with(self, section):
        self.set_execution(section)
        before = len(self.cli.calls())
        task_id, path = self.task()
        state = run_task(self.home, task_id)
        # The worktree in Codex's `--cd` is the task's own folder: compare it without the task's ID.
        return state, [[arg.replace(task_id, "T-") for arg in call["args"]] for call in self.cli.calls()[before:]], path

    def test_a_run_with_execution_off_is_the_run_without_it(self):
        baseline, calls, path = self.run_with(None)
        self.assertEqual(baseline["status"], "done", baseline.get("last_error"))
        state, disabled_calls, other = self.run_with({"enabled": False, "profile": "fast"})
        self.assertEqual(self.shape(state), self.shape(baseline))
        self.assertEqual(disabled_calls, calls)
        self.assertNotIn(execution.KEY, state)
        self.assertEqual(self.files(other), self.files(path))
        developer = calls[0]
        self.assertEqual(developer[developer.index("--max-turns") + 1], "12")  # the workflow's, not a profile's

    def test_a_run_with_execution_enabled_decides_as_the_run_without_it(self):
        baseline, calls, path = self.run_with(None)
        flat = [name for name in self.files(path, recursive=True) if not name.endswith(".command.txt")]
        for section, turns in ((STANDARD, "40"), ({"enabled": True, "profile": "deep_autonomous"}, "50"),
                               ({"enabled": True, "profile": "custom", "limits": CUSTOM_LIMITS},
                                str(CUSTOM_LIMITS["call"]["developer"]["claude_turns"]))):
            with self.subTest(section=section):
                state, enabled_calls, other = self.run_with(section)
                self.assertEqual(self.decided(state), self.decided(baseline))
                self.assertEqual([self.without_limits(call) for call in enabled_calls],
                                 [self.without_limits(call) for call in calls])
                developer = enabled_calls[0]
                self.assertEqual(developer[developer.index("--max-turns") + 1], turns)  # the profile's, frozen
                self.assertEqual([entry["attempt_id"] for entry in execution.load(state).step.to_dict()["attempts"]],
                                 ["CX-0002"])  # the review's; the developer's step left the record
                # The same files, each call in a folder of its own (with the command it was started with and, since
                # Step 09, its launch record: what a later run reads if this one is lost).
                recorded = [name for name in self.files(other, recursive=True) if not name.endswith(".command.txt")]
                self.assertEqual([name for name in recorded if name != "launch.json"], flat)
                self.assertEqual(len([name for name in recorded if name == "launch.json"]), 2)
                self.assertEqual(sorted(item.name for item in (other / "runs" / "iteration-001").iterdir() if item.is_dir()),
                                 ["develop", "review"])

    def test_a_turn_limit_with_execution_enabled_still_pauses_after_one_call(self):
        self.set_execution({"enabled": True, "profile": "deep_autonomous", "adaptive": True})
        task_id, path = self.task()
        stream = jsonl({"type": "result", "is_error": True, "subtype": "error_max_turns"})

        def stopped(argv, cwd, **kwargs):
            kwargs["on_stdout"](stream)
            return Result(1, "", "")

        with patch("patchrondo.providers.execute", side_effect=stopped) as started:
            state = run_task(self.home, task_id)
        self.assertEqual(started.call_count, 1)  # no automatic continuation
        self.assertEqual((state["status"], state["phase"], state["iteration"], state["last_error"]["kind"]),
                         ("paused", "develop", 1, "max_turns"))
        attempt = execution.load(state).step.last  # recorded (Step 04), and nothing more
        self.assertEqual((attempt.attempt_id, str(attempt.outcome)), ("CX-0001", "TURN_LIMIT_REACHED"))
        argv = started.call_args.args[0]
        self.assertEqual(argv[argv.index("--max-turns") + 1], "50")  # the profile's, replacing the workflow default
        self.assertEqual(started.call_args.kwargs["timeout"], 60)  # EngineCase's configured workflow timeout caps it

    def test_a_saved_task_is_not_changed_by_a_new_section(self):
        task_id, path = self.task()
        state_file = path / "state.json"
        saved = state_file.read_bytes()
        self.set_execution({"enabled": True, "profile": "standard"})
        cfg = core.task_config(self.home, task_id)
        state = read_json(state_file)
        self.assertIsNone(execution.load(state))
        execution.resolve(cfg, state["developer"], "developer",
                          overrides=validate_overrides(state.get("overrides")))
        self.assertEqual(state_file.read_bytes(), saved)
        self.assertEqual(read_json(self.home / "config.json")["execution"], {"enabled": True, "profile": "standard"})

    def test_a_configuration_with_an_unusable_section_is_refused_before_anything_runs(self):
        task_id, path = self.task()
        saved = (path / "state.json").read_bytes()
        self.set_execution({"enabled": True, "profile": "turbo"})
        with patch("patchrondo.providers.execute", side_effect=AssertionError("a provider command was started")):
            with self.assertRaises(ValueError):
                run_task(self.home, task_id)
        self.assertEqual((path / "state.json").read_bytes(), saved)
        with self.assertRaises(ValueError):
            create_task(self.home, title="Other", description="Other", acceptance=[], developer="claude", reviewer="codex")


if __name__ == "__main__":
    unittest.main()
