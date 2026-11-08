"""Execution records (Phase 2.5, Step 01): typed, versioned, strictly read, and not wired into the workflow.

Nothing here starts a process or calls a provider: the module under test is
pure data. Classes follow the step's list: round trips, malformed and corrupt
records, schema versions, legacy states, the mapping of failure kinds, units
and unknown usage, identifiers, no mutation of input, and the separation of
a call's outcome from the completion of a phase or a task.
"""
import ast
import copy
import dataclasses
import json
from pathlib import Path
import unittest

from patchrondo import acceptance, activity, agents, core, evidence, execution, findings, history, providers, repair, \
    workspace
from patchrondo.execution import (AttemptOutcome, AttemptRecord, AttemptStatus, Availability, BudgetLine, BudgetState,
                                  ContinuationMode, ExecutionRecord, InvalidRecord, Phase, Scope, StepExecution,
                                  StepStatus, Unit, Usage)

SOURCES = Path(execution.__file__).resolve().parent
TASK = "T-0123456789ab"
OTHER_TASK = "T-ba9876543210"
DIGEST = "0123456789abcdef"
BEFORE, AFTER = "a" * 64, "b" * 64
T0, T1, T2 = "2026-11-08T10:00:00+00:00", "2026-11-08T10:05:00+00:00", "2026-11-08T10:09:00+00:00"
LIMITS = {Unit.CLAUDE_TURNS: 80, Unit.PROVIDER_CALLS: 3, Unit.SECONDS: 3600, Unit.TOKENS: None}


def opened(provider="claude", phase=Phase.DEVELOP, iteration=1, limits=None) -> ExecutionRecord:
    task_limits = {Unit.CLAUDE_TURNS: 200, Unit.PROVIDER_CALLS: 20, Unit.SECONDS: 36000, Unit.TOKENS: None}
    record = ExecutionRecord.open(TASK, task_limits)
    if limits is None:
        limits = dict(LIMITS) if provider == "claude" else {key: value for key, value in LIMITS.items()
                                                             if key is not Unit.CLAUDE_TURNS}
    return record.begin_step(iteration, phase, provider, limits)


def started(record: ExecutionRecord, **options) -> tuple[ExecutionRecord, AttemptRecord]:
    claude = record.step.provider == "claude"
    values = {"started_at": T0, "turn_limit": 40 if claude else None, "timeout_seconds": 900,
              "settings_digest": DIGEST, "workspace_before": BEFORE, **options}
    return record.new_attempt(**values)


def finished(record: ExecutionRecord, outcome=AttemptOutcome.COMPLETED, *, kind=None, turns=None, tokens=None,
             elapsed=300) -> ExecutionRecord:
    """The record with its current attempt run and ended."""
    record, item = started(record)
    item = item.running()
    claude = item.provider == "claude"
    if turns is None and claude:
        turns = Usage(Unit.CLAUDE_TURNS, Availability.OBSERVED, 31)
    if tokens is None:
        tokens = Usage(Unit.TOKENS, Availability.OBSERVED, 12000)
    return record.update(item.end(outcome, ended_at=T1, elapsed_seconds=elapsed, failure_kind=kind,
                                  workspace_after=AFTER, turns=turns, tokens=tokens))


def ended_attempt(provider="claude") -> AttemptRecord:
    return finished(opened(provider)).step.last


def walk(value):
    """Every key and value of a JSON structure."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)
    else:
        yield value


class RoundTripTests(unittest.TestCase):
    def test_a_starting_attempt_survives_a_round_trip(self):
        _, item = started(opened())
        self.assertIs(item.status, AttemptStatus.STARTING)
        self.assertEqual(AttemptRecord.from_dict(item.to_dict()), item)
        self.assertEqual(AttemptRecord.from_dict(execution.parse(execution.dumps(item))), item)

    def test_an_ended_claude_attempt_keeps_its_observations(self):
        item = ended_attempt("claude")
        again = AttemptRecord.from_dict(json.loads(json.dumps(item.to_dict())))
        self.assertEqual(again, item)
        self.assertEqual((again.turns.value, again.tokens.value, again.outcome), (31, 12000, AttemptOutcome.COMPLETED))
        self.assertEqual(again.to_dict()["role"], "developer")
        self.assertEqual(again.step_key, agents.step_key({"iteration": 1}, "developer"))

    def test_a_codex_attempt_has_no_claude_turns(self):
        record = finished(opened("codex", Phase.REVIEW), tokens=Usage.unknown(Unit.TOKENS))
        item = record.step.last
        self.assertEqual(item.to_dict()["turns"], {"unit": "claude_turns", "availability": "not_applicable", "value": None})
        self.assertIsNone(item.turn_limit)
        self.assertEqual(AttemptRecord.from_dict(item.to_dict()), item)

    def test_a_whole_record_survives_the_state_and_its_json_text(self):
        record = finished(opened(), AttemptOutcome.TURN_LIMIT_REACHED, kind="max_turns",
                          turns=Usage(Unit.CLAUDE_TURNS, Availability.OBSERVED, 40))
        record, item = started(record, continuation=ContinuationMode.CHECKPOINT_RESUME)
        state = {"id": TASK, "status": "running", execution.KEY: record.to_dict()}
        self.assertEqual(execution.load(state), record)
        text = execution.dumps(record)
        self.assertEqual(ExecutionRecord.from_dict(execution.parse(text)), record)
        self.assertEqual(execution.dumps(ExecutionRecord.from_dict(execution.parse(text))), text)
        self.assertEqual([entry.attempt_id for entry in record.step.attempts], ["CX-0001", "CX-0002"])

    def test_a_record_without_a_step_survives_a_round_trip(self):
        record = ExecutionRecord.open(TASK, {Unit.PROVIDER_CALLS: 10})
        self.assertIsNone(record.step)
        self.assertEqual(ExecutionRecord.from_dict(record.to_dict()), record)
        self.assertEqual([line.unit for line in record.budget.lines], [Unit.PROVIDER_CALLS, Unit.SECONDS])

    def test_a_budget_survives_a_round_trip(self):
        budget = BudgetState.open(Scope.PHASE, LIMITS).charged({Unit.CLAUDE_TURNS: 12, Unit.TOKENS: None})
        self.assertEqual(BudgetState.from_dict(budget.to_dict()), budget)


class MalformedTests(unittest.TestCase):
    def setUp(self):
        self.item = ended_attempt().to_dict()
        self.record = finished(opened()).to_dict()

    def refused(self, raw, loader=AttemptRecord.from_dict):
        with self.assertRaises(InvalidRecord) as caught:
            loader(raw)
        return caught.exception

    def changed(self, raw, path, value):
        raw = copy.deepcopy(raw)
        target = raw
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        return raw

    def test_every_missing_field_is_refused(self):
        for name in ("version", *AttemptRecord.FIELDS):
            with self.subTest(name=name):
                raw = copy.deepcopy(self.item)
                del raw[name]
                self.refused(raw)
        for name in ("unit", "availability", "value"):
            with self.subTest(usage=name):
                raw = copy.deepcopy(self.item)
                del raw["turns"][name]
                self.refused(raw)
        for name in ("version", *ExecutionRecord.FIELDS):
            with self.subTest(record=name):
                raw = copy.deepcopy(self.record)
                del raw[name]
                self.refused(raw, ExecutionRecord.from_dict)

    def test_an_unexpected_field_is_refused(self):
        self.refused({**self.item, "task_status": "done"})
        self.refused(self.changed(self.record, ("budget", "lines", 0, "spare"), 1), ExecutionRecord.from_dict)
        self.refused({**self.record, "status": "done"}, ExecutionRecord.from_dict)

    def test_values_of_another_type_are_refused(self):
        for name, value in (("iteration", "1"), ("iteration", 1.0), ("iteration", None), ("ordinal", [1]),
                            ("timeout_seconds", 900.5), ("provider", "gemini"), ("provider", None),
                            ("attempt_id", 1), ("task_id", "T-XYZ"), ("settings_digest", "short"),
                            ("workspace_before", "not-a-digest"), ("started_at", 1731060000),
                            ("started_at", "2026-11-08T10:00:00"), ("started_at", "yesterday"),
                            ("ended_at", None), ("elapsed_seconds", None), ("session", ""), ("session", "a b"),
                            ("turns", None), ("turns", 31), ("failure_kind", "Max Turns"),
                            ("attempt_id", "CX-0000"), ("attempt_id", "CX-1"), ("attempt_id", "cx-0001")):
            with self.subTest(name=name, value=value):
                self.refused(self.changed(self.item, (name,), value))
        self.refused([self.item])
        self.refused(None)
        self.refused(self.changed(self.record, ("step", "attempts"), {}), ExecutionRecord.from_dict)
        self.refused(self.changed(self.record, ("budget", "lines"), "calls"), ExecutionRecord.from_dict)

    def test_booleans_are_not_numbers(self):
        for path in (("iteration",), ("ordinal",), ("timeout_seconds",), ("turn_limit",), ("elapsed_seconds",),
                     ("turns", "value"), ("tokens", "value"), ("version",)):
            for value in (True, False):
                with self.subTest(path=path, value=value):
                    self.refused(self.changed(self.item, path, value))
        for path in (("next_attempt",), ("version",), ("budget", "version"), ("budget", "lines", 0, "limit"),
                     ("budget", "lines", 0, "used"), ("budget", "lines", 0, "unknown"), ("step", "iteration")):
            with self.subTest(path=path):
                self.refused(self.changed(self.record, path, True), ExecutionRecord.from_dict)
        with self.assertRaises(InvalidRecord):
            BudgetLine(Unit.PROVIDER_CALLS, limit=True)
        with self.assertRaises(InvalidRecord):
            BudgetState.open(Scope.TASK, {}).line(Unit.SECONDS).allows(True)

    def test_unknown_enum_values_are_refused(self):
        for path, value in ((("phase",), "test"), (("phase",), "validate"), (("outcome",), "DONE"),
                            (("outcome",), "completed"), (("status",), "finished"), (("continuation",), "resume"),
                            (("turns", "unit"), "codex_turns"), (("turns", "availability"), "zero"),
                            (("role",), "tester"), (("role",), "reviewer")):
            with self.subTest(path=path, value=value):
                self.refused(self.changed(self.item, path, value))
        for path, value in ((("budget", "scope"), "global"), (("budget", "scope"), "phase"),
                            (("budget", "lines", 0, "unit"), "dollars"), (("step", "phase"), "test"),
                            (("step", "role"), "reviewer")):
            with self.subTest(path=path, value=value):
                self.refused(self.changed(self.record, path, value), ExecutionRecord.from_dict)

    def test_negative_and_out_of_range_values_are_refused(self):
        for path, value in ((("iteration",), 0), (("iteration",), 1000), (("ordinal",), 0), (("timeout_seconds",), 0),
                            (("timeout_seconds",), 86401), (("turn_limit",), 0), (("turn_limit",), -40),
                            (("elapsed_seconds",), -1), (("turns", "value"), -1), (("tokens", "value"), 10**15 + 1)):
            with self.subTest(path=path, value=value):
                self.refused(self.changed(self.item, path, value))
        for path, value in ((("next_attempt",), 0), (("next_attempt",), -1),
                            (("budget", "lines", 0, "limit"), 0), (("budget", "lines", 0, "limit"), -5),
                            (("budget", "lines", 0, "limit"), execution.BUDGET_LIMIT + 1),
                            (("budget", "lines", 0, "used"), -1), (("budget", "lines", 0, "unknown"), -1)):
            with self.subTest(path=path, value=value):
                self.refused(self.changed(self.record, path, value), ExecutionRecord.from_dict)
        for unit, limit in ((Unit.CLAUDE_TURNS, 0), (Unit.SECONDS, -1), (Unit.PROVIDER_CALLS, 10**8)):
            with self.subTest(unit=unit, limit=limit), self.assertRaises(InvalidRecord):
                BudgetLine(unit, limit)

    def test_tokens_are_never_a_limit_and_every_budget_keeps_calls_and_seconds(self):
        with self.assertRaises(InvalidRecord):
            BudgetLine(Unit.TOKENS, limit=100000)
        with self.assertRaises(InvalidRecord):
            BudgetState(Scope.TASK, (BudgetLine(Unit.PROVIDER_CALLS, 5),))
        with self.assertRaises(InvalidRecord):
            BudgetState(Scope.TASK, (BudgetLine(Unit.PROVIDER_CALLS), BudgetLine(Unit.SECONDS), BudgetLine(Unit.SECONDS)))
        with self.assertRaises(InvalidRecord):
            BudgetState(Scope.TASK, [{"unit": "seconds"}])

    def test_a_record_that_contradicts_itself_is_refused(self):
        refused = self.refused
        refused(self.changed(self.item, ("ended_at",), "2026-11-08T09:00:00+00:00"))
        refused(self.changed(self.item, ("failure_kind",), "quota"))                 # COMPLETED with a failure
        refused({**self.item, "outcome": "TIMEOUT", "failure_kind": "quota"})        # kind of another outcome
        refused({**self.item, "outcome": "TURN_LIMIT_REACHED", "failure_kind": None})  # a turn limit is never assumed
        refused({**self.item, "outcome": "QUOTA_EXHAUSTED", "failure_kind": None})
        refused({**self.item, "outcome": "UNKNOWN", "failure_kind": "tests_disabled"})  # an engine pause
        refused({**self.item, "continuation": "native_resume", "session": None})
        refused({**self.item, "turn_limit": None})                                    # Claude always has one
        refused(self.changed(self.item, ("turns", "availability"), "not_applicable"))
        refused({**self.item, "status": "running"})                                   # open, with an outcome
        refused({**self.item, "status": "starting", "outcome": None, "failure_kind": None, "ended_at": None,
                 "elapsed_seconds": None, "workspace_after": None})                   # open, with observations
        refused({**self.item, "status": "ended", "outcome": None})
        codex = ended_attempt("codex").to_dict()
        refused({**codex, "turn_limit": 40})
        refused(self.changed(codex, ("turns",), {"unit": "claude_turns", "availability": "observed", "value": 3}))
        refused(self.changed(codex, ("tokens",), {"unit": "tokens", "availability": "not_applicable", "value": None}))
        refused(self.changed(codex, ("tokens",), {"unit": "seconds", "availability": "observed", "value": 3}))

    def test_a_step_that_contradicts_its_attempts_is_refused(self):
        load = ExecutionRecord.from_dict
        two = finished(finished(opened())).to_dict()
        self.refused(self.changed(self.record, ("step", "budget", "lines", 0, "used"), 0), load)  # charges differ
        self.refused(self.changed(self.record, ("step", "attempts", 0, "ordinal"), 2), load)
        self.refused(self.changed(self.record, ("step", "attempts", 0, "iteration"), 2), load)
        self.refused(self.changed(self.record, ("step", "attempts", 0, "task_id"), OTHER_TASK), load)
        self.refused(self.changed(self.record, ("next_attempt",), 1), load)  # would give CX-0001 again
        self.refused(self.changed(two, ("step", "attempts"), list(reversed(two["step"]["attempts"]))), load)
        self.refused(self.changed(self.record, ("budget", "lines", 1, "used"), 0), load)  # task charged less than its step
        phase = self.record["step"]["budget"]
        self.refused(self.changed(self.record, ("step", "provider"), "codex"), load)
        self.assertEqual([line["unit"] for line in phase["lines"]], ["claude_turns", "provider_calls", "seconds", "tokens"])
        first = self.changed(two, ("step", "attempts", 0, "status"), "running")
        for key in ("outcome", "failure_kind", "ended_at", "elapsed_seconds", "workspace_after"):
            first["step"]["attempts"][0][key] = None
        first["step"]["attempts"][0]["turns"] = {"unit": "claude_turns", "availability": "unknown", "value": None}
        first["step"]["attempts"][0]["tokens"] = {"unit": "tokens", "availability": "unknown", "value": None}
        self.refused(first, load)  # only the last attempt may still be open
        with self.assertRaises(InvalidRecord):
            opened("codex", limits=LIMITS)  # Claude turns in the budget of a Codex step


class VersionTests(unittest.TestCase):
    def test_a_newer_schema_is_refused_as_newer(self):
        for raw, loader in ((ended_attempt().to_dict(), AttemptRecord.from_dict),
                            (finished(opened()).to_dict(), ExecutionRecord.from_dict),
                            (BudgetState.open(Scope.TASK, {}).to_dict(), BudgetState.from_dict)):
            with self.subTest(loader=loader):
                with self.assertRaises(InvalidRecord) as caught:
                    loader({**raw, "version": 2, "something_new": [1]})
                self.assertTrue(caught.exception.newer)
                self.assertIn("newer", str(caught.exception))

    def test_older_missing_and_odd_versions_are_refused(self):
        raw = ended_attempt().to_dict()
        for version in (0, -1, "1", 1.0, None, True):
            with self.subTest(version=version):
                with self.assertRaises(InvalidRecord) as caught:
                    AttemptRecord.from_dict({**raw, "version": version})
                self.assertFalse(caught.exception.newer)
        with self.assertRaises(InvalidRecord):
            AttemptRecord.from_dict({key: value for key, value in raw.items() if key != "version"})

    def test_a_newer_nested_record_is_refused_too(self):
        raw = finished(opened()).to_dict()
        raw["step"]["attempts"][0]["version"] = 2
        with self.assertRaises(InvalidRecord) as caught:
            ExecutionRecord.from_dict(raw)
        self.assertTrue(caught.exception.newer)


class CorruptTests(unittest.TestCase):
    def test_text_that_is_not_a_record_is_refused(self):
        text = execution.dumps(finished(opened()))
        for bad in (text[: len(text) // 2], "", "null", "[]", "42", '"record"', text.replace("300", "NaN", 1),
                    text.replace("300", "Infinity", 1), text.replace("300", "-Infinity", 1),
                    '{"version": 1, "version": 1}', "[" * 100000 + "]" * 100000, text.encode("utf-8"), None):
            with self.subTest(bad=repr(bad)[:40]), self.assertRaises(InvalidRecord):
                ExecutionRecord.from_dict(execution.parse(bad))

    def test_the_reader_refuses_what_json_would_otherwise_accept(self):
        text = execution.dumps(finished(opened()))
        self.assertEqual(execution.parse(text), json.loads(text))
        # A repeated key would let the last copy win silently: here the earlier copy names another task.
        doubled = '{"task_id": "' + OTHER_TASK + '", ' + text[1:]
        self.assertEqual(ExecutionRecord.from_dict(json.loads(doubled)).task_id, TASK)  # what plain json would read
        for bad in (doubled, '{"value": NaN}', '{"value": Infinity}', '{"value": -Infinity}'):
            with self.subTest(bad=bad[:30]), self.assertRaises(InvalidRecord):
                execution.parse(bad)

    def test_a_corrupt_saved_record_is_refused_and_left_as_it_is(self):
        good = finished(opened()).to_dict()
        for saved in (None, [], "execution", 7, {}, {"version": 1}, {**good, "next_attempt": "2"},
                      {**good, "version": 9}):
            state = {"id": TASK, "status": "paused", "history": [], execution.KEY: copy.deepcopy(saved)}
            kept = copy.deepcopy(state)
            with self.subTest(saved=repr(saved)[:40]):
                with self.assertRaises(InvalidRecord):
                    execution.dumps(execution.load(state))
                self.assertEqual(state, kept)

    def test_a_state_that_is_not_an_object_is_refused(self):
        for state in (None, [], "state"):
            with self.subTest(state=state), self.assertRaises(InvalidRecord):
                execution.load(state)


class LegacyTests(unittest.TestCase):
    LEGACY = {"version": 1, "id": TASK, "title": "x", "status": "paused", "phase": "develop", "iteration": 1,
              "developer": "claude", "reviewer": "codex",
              "agent_runs": {"001:developer": {"provider": "claude", "values": {}, "digest": DIGEST}},
              "last_error": {"kind": "max_turns", "message": "used all 12 turns"}, "history": []}

    def test_a_task_without_execution_records_has_none_and_is_not_changed(self):
        state = copy.deepcopy(self.LEGACY)
        self.assertIsNone(execution.load(state))
        self.assertEqual(state, self.LEGACY)
        self.assertNotIn(execution.KEY, state)
        self.assertIsNone(execution.load({}))

    def test_the_default_configuration_is_unchanged(self):
        self.assertNotIn("execution", core.DEFAULT_CONFIG)
        self.assertEqual(core.DEFAULT_CONFIG["workflow"], {"max_iterations": 6, "agent_timeout_seconds": 900,
                                                           "test_timeout_seconds": 180, "claude_max_turns": 12,
                                                           "allow_no_changes": False})

    def test_no_module_of_patchrondo_uses_the_records_yet(self):
        """Since Step 02 only `core.validate_config` reads the module, to check the configuration; no run uses it."""
        users = {}
        for source in sorted(SOURCES.glob("*.py")):
            if source.name == "execution.py":
                continue
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = [alias.name for alias in node.names] if isinstance(node, (ast.Import, ast.ImportFrom)) else []
                module = node.module or "" if isinstance(node, ast.ImportFrom) else ""
                if "execution" in names or module.endswith("execution") or "patchrondo.execution" in names:
                    users[source.name] = sorted(
                        (function.name, node.attr) for function in ast.walk(tree) if isinstance(function, ast.FunctionDef)
                        for node in ast.walk(function) if isinstance(node, ast.Attribute)
                        and isinstance(node.value, ast.Name) and node.value.id == "execution")
        self.assertEqual(users, {"core.py": [("validate_config", "settings_of")]})


def failure_kinds() -> set[str]:
    """Every kind of AgentFailure the sources name, and the kinds they compute from tables."""
    raisers = {"AgentFailure", "ValidationPending", "AcceptanceUnreadable", "CompletionRefused", "EnvironmentBlocked"}
    kinds = set()
    for source in sorted(SOURCES.glob("*.py")):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else \
                node.func.attr if isinstance(node.func, ast.Attribute) else None
            if name not in raisers:
                continue
            values = node.args[1:2] + [word.value for word in node.keywords if word.arg == "kind"]
            kinds.update(value.value for value in values if isinstance(value, ast.Constant) and isinstance(value.value, str))
    kinds.update(kind for kind, _ in providers._ERROR_KINDS)        # `classified_failure`
    kinds.update(repair.PROBLEMS)                                  # `EnvironmentBlocked(record["error_kind"])`
    kinds.update({"agent_error", "interrupted", "timeout", "system_error"})  # defaults of AgentFailure and core._failure
    return kinds


class OutcomeMappingTests(unittest.TestCase):
    def test_every_failure_kind_of_the_sources_is_mapped_or_an_engine_pause(self):
        kinds = failure_kinds()
        self.assertGreater(len(kinds), 25)
        self.assertEqual(sorted(kind for kind in kinds if kind not in execution.OUTCOME_OF_KIND
                                and kind not in execution.ENGINE_KINDS), [])
        self.assertEqual(set(execution.OUTCOME_OF_KIND) & execution.ENGINE_KINDS, set())

    def test_the_mapping_of_the_kinds_that_decide_continuation(self):
        expected = {"max_turns": "TURN_LIMIT_REACHED", "quota": "QUOTA_EXHAUSTED", "timeout": "TIMEOUT",
                    "interrupted": "INTERRUPTED", "empty_output": "INVALID_FINAL_OUTPUT",
                    "invalid_output": "INVALID_FINAL_OUTPUT", "invalid_review": "INVALID_FINAL_OUTPUT",
                    "authentication": "ENVIRONMENT_BLOCKED", "configuration": "ENVIRONMENT_BLOCKED",
                    "unsupported_option": "ENVIRONMENT_BLOCKED", "sandbox_access_denied": "PERMISSION_DENIED",
                    "model_unavailable": "PROVIDER_ERROR", "agent_error": "PROVIDER_ERROR", "system_error": "UNKNOWN"}
        self.assertEqual({kind: str(execution.outcome_for_failure(kind)) for kind in expected}, expected)

    def test_no_failure_is_ever_a_completed_call(self):
        self.assertNotIn(AttemptOutcome.COMPLETED, execution.OUTCOME_OF_KIND.values())
        for kind in (*failure_kinds(), "a_kind_added_later", "", "COMPLETED", "completed"):
            self.assertIsNot(execution.outcome_for_failure(kind), AttemptOutcome.COMPLETED)

    def test_engine_pauses_and_unknown_kinds(self):
        for kind in execution.ENGINE_KINDS:
            self.assertIsNone(execution.outcome_for_failure(kind))
        for kind in ("a_kind_added_later", None, 3, ["quota"]):
            self.assertIs(execution.outcome_for_failure(kind), AttemptOutcome.UNKNOWN)

    def test_the_failure_class_and_its_kinds_are_unchanged(self):
        failure = providers.AgentFailure("x")
        self.assertEqual((failure.kind, failure.provider, failure.retry_at, failure.retry_after_seconds),
                         ("agent_error", None, None, None))
        self.assertIs(core.AgentFailure, providers.AgentFailure)


class UsageTests(unittest.TestCase):
    def test_usage_has_a_value_only_when_observed(self):
        self.assertEqual(Usage.unknown(Unit.TOKENS).to_dict(), {"unit": "tokens", "availability": "unknown", "value": None})
        for availability, value in ((Availability.UNKNOWN, 0), (Availability.NOT_APPLICABLE, 0),
                                    (Availability.OBSERVED, None), (Availability.OBSERVED, -1),
                                    (Availability.OBSERVED, 2.5), (Availability.OBSERVED, True)):
            with self.subTest(availability=availability, value=value), self.assertRaises(InvalidRecord):
                Usage(Unit.TOKENS, availability, value)

    def test_unknown_claude_turns_are_charged_the_call_limit(self):
        record = finished(opened(), AttemptOutcome.TIMEOUT, kind="timeout", turns=Usage.unknown(Unit.CLAUDE_TURNS),
                          tokens=Usage.unknown(Unit.TOKENS), elapsed=900)
        turns, tokens = record.step.budget.line(Unit.CLAUDE_TURNS), record.step.budget.line(Unit.TOKENS)
        self.assertEqual((turns.used, turns.unknown), (40, 0))      # the 40 the call was allowed, never 0
        self.assertEqual((tokens.used, tokens.unknown), (0, 1))     # not reported: counted as unknown, never 0
        self.assertEqual(record.budget.line(Unit.CLAUDE_TURNS).used, 40)

    def test_observed_usage_is_charged_as_observed(self):
        record = finished(opened())
        budget = record.step.budget
        self.assertEqual([(line.unit, line.used, line.unknown) for line in budget.lines],
                         [(Unit.CLAUDE_TURNS, 31, 0), (Unit.PROVIDER_CALLS, 1, 0), (Unit.SECONDS, 300, 0),
                          (Unit.TOKENS, 12000, 0)])
        self.assertEqual(budget.line(Unit.CLAUDE_TURNS).remaining, 49)

    def test_codex_activity_is_never_counted_as_claude_turns(self):
        record = ExecutionRecord.open(TASK, LIMITS)
        record = finished(record.begin_step(1, Phase.REVIEW, "codex", {Unit.PROVIDER_CALLS: 2}))
        self.assertNotIn(Unit.CLAUDE_TURNS, record.step.last.charges())
        self.assertEqual(record.budget.line(Unit.CLAUDE_TURNS).used, 0)
        self.assertEqual(record.budget.line(Unit.PROVIDER_CALLS).used, 1)
        self.assertEqual(record.budget.line(Unit.TOKENS).used, 12000)

    def test_a_call_not_ended_is_charged_what_it_may_still_use(self):
        record, item = started(opened())
        self.assertEqual(item.charges(), {Unit.PROVIDER_CALLS: 1, Unit.SECONDS: 900, Unit.TOKENS: None,
                                          Unit.CLAUDE_TURNS: 40})
        ended = record.update(item.running().end(AttemptOutcome.COMPLETED, ended_at=T1, elapsed_seconds=120,
                                                 turns=Usage(Unit.CLAUDE_TURNS, Availability.OBSERVED, 9)))
        self.assertEqual(ended.step.budget.line(Unit.SECONDS).used, 120)
        self.assertEqual(ended.step.budget.line(Unit.TOKENS).unknown, 1)
        self.assertEqual(ended.budget.line(Unit.CLAUDE_TURNS).used, 9)

    def test_a_limited_budget_with_an_unknown_charge_allows_nothing_more(self):
        line = BudgetLine(Unit.SECONDS, limit=3600, used=10, unknown=1)
        self.assertEqual(line.remaining, 0)
        self.assertFalse(line.allows(0))
        self.assertTrue(BudgetLine(Unit.SECONDS, used=10, unknown=1).allows(10**9))  # not limited
        self.assertIsNone(BudgetLine(Unit.SECONDS).remaining)
        self.assertTrue(BudgetLine(Unit.PROVIDER_CALLS, 2, used=1).allows(1))
        self.assertFalse(BudgetLine(Unit.PROVIDER_CALLS, 2, used=2).allows(1))
        with self.assertRaises(InvalidRecord):
            line.allows(-1)


class IdentifierTests(unittest.TestCase):
    OTHERS = ("T-0123456789ab", "AC-01", "A-0123456789ab", "J-0123456789ab", "U-0123abcd", "E-0001", "E2-0001",
              "RF-001", "WE-0001", "GE-0123456789abcdef", "EB-0123456789abcdef", "H-0123456789abcdef", "P-0123abcd",
              "001:developer", "iteration-001")

    def test_ids_are_numbered_in_the_task_and_never_given_twice(self):
        record = opened()
        given = []
        for iteration in range(1, 6):
            for phase, provider in ((Phase.DEVELOP, "claude"), (Phase.REVIEW, "codex")):
                limits = {Unit.PROVIDER_CALLS: 3} if provider == "codex" else {Unit.CLAUDE_TURNS: 80}
                record = record.begin_step(iteration, phase, provider, limits)
                for ordinal in (1, 2):
                    record = finished(record)
                    given.append(record.step.last.attempt_id)
                    self.assertEqual(record.step.last.ordinal, ordinal)
                record = ExecutionRecord.from_dict(record.to_dict())  # the counter survives a reload
        self.assertEqual(len(given), 20)
        self.assertEqual(len(set(given)), 20)
        self.assertEqual(given, [f"CX-{number:04d}" for number in range(1, 21)])
        self.assertEqual(record.next_attempt, 21)

    def test_attempt_ids_and_the_other_ids_never_match_each_other(self):
        patterns = (evidence.RECORD_ID, findings.FINDING_ID, history.EVENT_ID, activity.JOURNAL_ID,
                    acceptance.CRITERION_ID, agents.PRESET_ID, workspace.PROJECT_ID, execution.TASK_ID)
        for name in ("CX-0001", "CX-0042", "CX-999999"):
            for pattern in patterns:
                self.assertIsNone(pattern.fullmatch(name), (name, pattern.pattern))
                self.assertIsNone(pattern.search(name), (name, pattern.pattern))
        for name in self.OTHERS:
            self.assertIsNone(execution.ATTEMPT_ID.fullmatch(name), name)
            with self.assertRaises(InvalidRecord):
                execution.attempt_number(name)

    def test_the_last_id_is_never_followed_by_a_reused_one(self):
        record = dataclasses.replace(opened(), next_attempt=execution.ATTEMPT_LIMIT)
        record = finished(record)
        self.assertEqual(record.step.last.attempt_id, "CX-999999")
        with self.assertRaises(InvalidRecord):
            started(record)
        with self.assertRaises(InvalidRecord):
            execution.attempt_id(0)

    def test_attempts_follow_one_another(self):
        record, item = started(opened())
        with self.assertRaises(InvalidRecord):
            started(record)  # the previous attempt has not ended
        with self.assertRaises(InvalidRecord):
            record.begin_step(1, Phase.REVIEW, "codex", {})
        ended = item.running().end(AttemptOutcome.COMPLETED, ended_at=T1, elapsed_seconds=1)
        with self.assertRaises(InvalidRecord):
            ended.end(AttemptOutcome.COMPLETED, ended_at=T2, elapsed_seconds=2)
        with self.assertRaises(InvalidRecord):
            ended.running()
        record = record.update(ended)
        with self.assertRaises(InvalidRecord):
            record.update(ended)  # an ended attempt is not changed again
        with self.assertRaises(InvalidRecord):
            ExecutionRecord.open(TASK, {}).update(ended)
        with self.assertRaises(InvalidRecord):
            ExecutionRecord.open(TASK, {}).new_attempt(started_at=T0, turn_limit=40, timeout_seconds=900)
        _, other = started(opened())
        with self.assertRaises(InvalidRecord):
            started(record)[0].update(dataclasses.replace(other.running(), started_at=T2))

    def test_a_call_keeps_the_limits_and_settings_it_started_with(self):
        record, item = started(opened())
        for name, value in (("turn_limit", 80), ("timeout_seconds", 3600), ("settings_digest", "f" * 16),
                            ("settings_digest", None), ("continuation", ContinuationMode.CHECKPOINT_RESUME),
                            ("workspace_before", AFTER), ("started_at", T1)):
            with self.subTest(name=name), self.assertRaises(InvalidRecord):
                record.update(dataclasses.replace(item.running(), **{name: value}))
        self.assertEqual(record.update(item.running()).step.last.turn_limit, 40)


class NoMutationTests(unittest.TestCase):
    def test_reading_never_changes_its_input(self):
        raw = finished(finished(opened())).to_dict()
        kept = copy.deepcopy(raw)
        ExecutionRecord.from_dict(raw)
        AttemptRecord.from_dict(raw["step"]["attempts"][0])
        BudgetState.from_dict(raw["budget"])
        state = {"id": TASK, execution.KEY: raw}
        execution.load(state)
        self.assertEqual(raw, kept)
        self.assertIs(state[execution.KEY], raw)
        bad = {**kept, "version": 2}
        before = copy.deepcopy(bad)
        with self.assertRaises(InvalidRecord):
            ExecutionRecord.from_dict(bad)
        self.assertEqual(bad, before)

    def test_records_are_frozen_and_their_dicts_are_copies(self):
        record = finished(opened())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            record.next_attempt = 1
        with self.assertRaises(dataclasses.FrozenInstanceError):
            record.step.last.outcome = AttemptOutcome.UNKNOWN
        dumped = record.to_dict()
        dumped["step"]["attempts"][0]["outcome"] = "UNKNOWN"
        dumped["budget"]["lines"].clear()
        self.assertEqual(record.to_dict()["step"]["attempts"][0]["outcome"], "COMPLETED")
        self.assertEqual(ExecutionRecord.from_dict(record.to_dict()), record)

    def test_changes_return_new_records(self):
        record = opened()
        kept = record.to_dict()
        after, item = started(record)
        after.update(item.running())
        record.begin_step(2, Phase.DEVELOP, "claude", {})
        self.assertEqual(record.to_dict(), kept)
        limits = dict(LIMITS)
        opened(limits=limits)
        self.assertEqual(limits, LIMITS)


class SeparationTests(unittest.TestCase):
    def test_a_completed_call_is_an_answer_for_the_workflow_to_judge(self):
        record = finished(opened())
        self.assertIs(record.step.last.outcome, AttemptOutcome.COMPLETED)
        self.assertIs(record.step.status, StepStatus.ANSWER_RECEIVED)
        self.assertTrue(AttemptOutcome.COMPLETED.answered)
        self.assertEqual([outcome for outcome in AttemptOutcome if outcome.answered], [AttemptOutcome.COMPLETED])

    def test_no_record_says_that_a_phase_or_a_task_is_done(self):
        record = finished(opened())
        dumped = record.to_dict()
        self.assertEqual(set(dumped), {"version", "task_id", "next_attempt", "budget", "step"})
        self.assertNotIn("status", dumped["step"])  # derived from the attempts, never saved
        words = {str(value).lower() for value in walk(dumped) if isinstance(value, str)}
        self.assertEqual(words & {"done", "passed", "approved", "verified", "pass", "complete"}, set())
        self.assertEqual({status.value for status in StepStatus} & {"done", "completed", "passed", "approved"}, set())
        for name in ("done", "completed", "phase_completed", "task_status", "verdict", "gate"):
            self.assertFalse(hasattr(record, name), name)
            self.assertFalse(hasattr(record.step, name), name)

    def test_a_call_whose_answer_was_unusable_did_not_answer(self):
        record = finished(opened(provider="codex", phase=Phase.REVIEW), AttemptOutcome.INVALID_FINAL_OUTPUT,
                          kind="invalid_review")
        self.assertIs(record.step.status, StepStatus.STOPPED)
        self.assertFalse(record.step.last.outcome.answered)

    def test_a_step_whose_last_call_has_not_ended_is_running_not_finished(self):
        record, _ = started(opened())
        self.assertIs(record.step.status, StepStatus.RUNNING)
        self.assertIs(opened().step.status, StepStatus.NOT_STARTED)

    def test_reading_a_record_leaves_the_task_status_alone(self):
        state = {"id": TASK, "status": "paused", "phase": "review", "iteration": 2,
                 execution.KEY: finished(opened()).to_dict()}
        kept = copy.deepcopy(state)
        execution.load(state)
        self.assertEqual(state, kept)


if __name__ == "__main__":
    unittest.main()
