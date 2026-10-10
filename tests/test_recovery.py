"""Automatic quota recovery with scripted providers and a simulated clock.

No test waits in real time or reaches a provider CLI: `providers.execute` is
replaced by a guard that fails the test if anything tries to start one.
"""
import contextlib
from datetime import datetime, timedelta, timezone
from functools import partial
from importlib import resources
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from patchrondo import recovery
from patchrondo.cli import main, parser
from patchrondo.core import config, create_task, initialize, run_task
from patchrondo.process import Result
from patchrondo.providers import AgentFailure, AgentReply, OfficialCLI, classified_failure, quota_hint
from patchrondo.storage import LockBusy, TaskLock, _alive, read_json, save_json
from patchrondo.app import App

T0 = datetime(2026, 10, 10, 14, 0, tzinfo=timezone.utc)
LIMITS = {**recovery.DEFAULTS, "enabled": True}
DEAD_PID = 2 ** 22 + 12345  # above the default Linux pid_max; not a running process


def at(**delta) -> datetime:
    return T0 + timedelta(**delta)


class FakeClock:
    """Injected clock. Sleeping advances it instantly and records the requested wait."""

    def __init__(self):
        self.now = T0
        self.sleeps = []
        self.on_sleep = None

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        if self.on_sleep:
            self.on_sleep(seconds)
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


class Script:
    """Provider whose calls follow a script: None succeeds, a callable returns the error to raise."""

    def __init__(self, clock, steps=(), always=None, dev_values=()):
        self.clock, self.steps, self.always = clock, list(steps), always
        self.dev_values = list(dev_values)
        self.calls = []
        self.lock = None

    def invoke(self, provider, role, prompt, workspace, run_dir):
        self.calls.append({"provider": provider, "role": role, "at": self.clock(),
                           "locked": bool(self.lock and self.lock.exists())})
        step = self.steps.pop(0) if self.steps else self.always
        if step is not None:
            raise step()
        if role == "developer":
            value = self.dev_values.pop(0) if self.dev_values else "good"
            (workspace / "feature.txt").write_text(value, encoding="utf-8")
            return AgentReply(text=f"Changed feature.txt to {value}", provider=provider)
        return AgentReply(text=json.dumps({"verdict": "APPROVED", "summary": "Checked", "issues": []}),
                          provider=provider)

    @property
    def roles(self):
        return [call["role"] for call in self.calls]


class RecoveryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.repo, self.home = root / "repo", root / "state"
        self.repo.mkdir()
        for args in (("init", "-q"), ("config", "user.name", "Test Runner"),
                     ("config", "user.email", "tester@example.com")):
            self._git(*args)
        (self.repo / "README.md").write_text("Initial repo\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-qm", "initial")
        initialize(self.home, self.repo)
        cfg = read_json(self.home / "config.json")
        cfg["tests"] = {"enabled": True, "trust_acknowledged": True,
                        "commands": [[sys.executable, "-c",
                                      "from pathlib import Path; assert Path('feature.txt').read_text() == 'good'"]]}
        cfg["workflow"]["max_iterations"] = 3
        save_json(self.home / "config.json", cfg)
        self.clock = FakeClock()
        # Any path that reaches a provider CLI fails the test instead of using quota.
        guard = patch("patchrondo.providers.execute", side_effect=AssertionError("real provider call attempted"))
        self.provider_execute = guard.start()
        self.addCleanup(guard.stop)

    def _git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def configure(self, **values):
        cfg = read_json(self.home / "config.json")
        cfg["recovery"] = {**cfg.get("recovery", {}), **values}
        save_json(self.home / "config.json", cfg)

    def task(self):
        task_id, workspace = create_task(self.home, title="Implement feature", description="Write feature.txt",
                                         acceptance=["File contains good"], developer="claude", reviewer="codex")
        return task_id, self.home / "tasks" / task_id, workspace

    def script(self, path, *steps, **options):
        adapter = Script(self.clock, steps, **options)
        adapter.lock = path / ".run.lock"
        return adapter

    def quota(self, text="Usage limit reached", provider=None):
        """A step raising what the adapters raise for this CLI error text, observed now."""
        return lambda: classified_failure(text, text, provider, self.clock()) if provider \
            else AgentFailure(text, "quota", **quota_hint(text, self.clock()))

    def supervise(self, task_id, adapter, **options):
        options.setdefault("auto_resume", True)
        return recovery.supervise(self.home, task_id, adapter=adapter, clock=self.clock,
                                  sleep=self.clock.sleep, **options)

    def events(self, path):
        return [entry["event"] for entry in read_json(path / "state.json")["history"]]


class ConfigurationTests(RecoveryCase):
    def test_recovery_is_disabled_by_default_and_legacy_behavior_is_unchanged(self):
        self.assertEqual(config(self.home)["recovery"], recovery.DEFAULTS)
        self.assertFalse(recovery.DEFAULTS["enabled"])
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota("Usage limit reached; resets at 2026-10-10T18:00:00Z"))
        paused = recovery.supervise(self.home, task_id, adapter=adapter, clock=self.clock, sleep=self.clock.sleep)
        self.assertEqual((paused["status"], paused["phase"], paused["last_error"]["kind"]),
                         ("paused", "develop", "quota"))
        self.assertIsNone(paused["recovery"])
        self.assertEqual(self.clock.sleeps, [])
        self.assertFalse([name for name in self.events(path) if name.startswith("recovery_")])
        # A manual resume runs at once, exactly as before, even though the reset is still ahead.
        done = run_task(self.home, task_id, adapter=adapter, clock=self.clock)
        self.assertEqual(done["status"], "done")
        self.assertEqual(adapter.roles, ["developer", "developer", "reviewer"])

    def test_legacy_configuration_and_state_without_recovery_sections(self):
        cfg = read_json(self.home / "config.json")
        del cfg["recovery"]
        save_json(self.home / "config.json", cfg)
        before = (self.home / "config.json").read_bytes()
        self.assertEqual(config(self.home)["recovery"], recovery.DEFAULTS)
        self.assertEqual((self.home / "config.json").read_bytes(), before)
        task_id, path, _ = self.task()
        state = read_json(path / "state.json")
        del state["recovery"]
        save_json(path / "state.json", state)
        adapter = self.script(path)
        self.assertEqual(run_task(self.home, task_id, adapter=adapter)["status"], "done")
        self.assertEqual(adapter.roles, ["developer", "reviewer"])

    def test_partial_section_uses_defaults_for_missing_fields(self):
        self.configure(enabled=True)
        cfg = read_json(self.home / "config.json")
        cfg["recovery"] = {"enabled": True, "max_consecutive_retries": 5}
        save_json(self.home / "config.json", cfg)
        self.assertEqual(config(self.home)["recovery"], {**recovery.DEFAULTS, "enabled": True,
                                                         "max_consecutive_retries": 5})

    def test_settings_are_validated_strictly(self):
        invalid = [{"enabled": "true"}, {"enabled": 1}, {"quota_only": False}, {"quota_only": 0},
                   {"max_consecutive_retries": True}, {"max_consecutive_retries": "3"},
                   {"max_consecutive_retries": 0}, {"max_consecutive_retries": 11},
                   {"max_consecutive_retries": 3.0}, {"initial_backoff_seconds": True},
                   {"initial_backoff_seconds": 0}, {"initial_backoff_seconds": 3601},
                   {"max_backoff_seconds": False}, {"max_backoff_seconds": 86401},
                   {"initial_backoff_seconds": 600, "max_backoff_seconds": 300},
                   {"max_total_wait_seconds": True}, {"max_total_wait_seconds": 59},
                   {"max_total_wait_seconds": 604801}, {"reset_safety_margin_seconds": True},
                   {"reset_safety_margin_seconds": -1}, {"reset_safety_margin_seconds": 3601},
                   {"max_retries": 3}]
        for section in invalid:
            with self.subTest(section=section), self.assertRaises(ValueError):
                recovery.settings(section)
        for section in ("on", [], 1, True):
            with self.subTest(section=section), self.assertRaises(ValueError):
                recovery.settings(section)
        self.configure(max_consecutive_retries=True)
        with self.assertRaisesRegex(ValueError, "recovery.max_consecutive_retries"):
            config(self.home)


class ParsingTests(unittest.TestCase):
    def test_reset_with_an_explicit_offset(self):
        cases = {
            "Usage limit reached; resets at 2026-10-10T18:00:00Z": at(hours=4),
            "limit reached, resets at 2026-10-10 20:00 +02:00": at(hours=4),
            "limit reached, resets at 2026-10-10T11:00:00-0700": at(hours=4),
            "Your limit will reset on Oct 10, 2026 at 6:00 PM UTC.": at(hours=4),
            "resets 10 October 2026, 20:30 GMT+2": at(hours=4, minutes=30),
            "resets at 2026-10-10 18:00:00.25 UTC": at(hours=4, seconds=1),  # rounded up, never early
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(quota_hint(text, T0), {"retry_at": expected})

    def test_relative_wait(self):
        cases = {"Rate limit exceeded. Retry after 120 seconds.": 120, "Retry-After: 45": 45,
                 "try again in 5 minutes": 300, "Try again in 3 days 4 hours 5 minutes.": 273900,
                 "retry in 2h30m": 9000, "resets in 1.5 hours": 5400}
        for text, seconds in cases.items():
            with self.subTest(text=text):
                self.assertEqual(quota_hint(text, T0),
                                 {"retry_after_seconds": seconds, "retry_at": at(seconds=seconds)})

    def test_ambiguous_or_impossible_resets_yield_nothing(self):
        for text in ("Usage limit reached; resets at 9pm", "resets at 3pm (America/Los_Angeles)",
                     "resets at 2026-10-10T18:00:00", "resets at 2026-10-10T18:00:00 PST",
                     "resets at 2026-02-30T18:00:00Z", "resets at 2026-13-01T18:00:00Z",
                     "resets at 2026-10-10T25:00:00Z", "resets at 2026-10-10T18:00:00+19:00",
                     "resets on Oct 10, 2026 at 13:00 PM UTC", "resets at 9999-12-31T23:59:59-14:00",
                     "retry after 3 failed attempts", "retry after 0 seconds", "try again in 5 to 10 minutes",
                     "retry in 99999999 days", "try again in a few minutes",
                     "2026-10-10T13:59:00Z ERROR rate limit exceeded", "quota exceeded"):
            with self.subTest(text=text):
                self.assertEqual(quota_hint(text, T0), {})

    @staticmethod
    def codex_message(reset, now):
        """The usage-limit text of codex-cli 0.160.1 (codex-rs/protocol/src/error.rs, format_retry_timestamp)."""
        local, today = reset.astimezone(), now.astimezone().date()
        clock = f"{local.hour % 12 or 12}:{local:%M} {'AM' if local.hour < 12 else 'PM'}"
        if local.date() != today:
            suffix = "th" if 11 <= local.day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(local.day % 10, "th")
            month = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")[local.month - 1]
            clock = f"{month} {local.day}{suffix}, {local.year} {clock}"
        return ("You’ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit "
                f"https://chatgpt.com/codex/settings/usage to purchase more credits or try again at {clock}.")

    def test_codex_reset_is_read_in_the_local_time_zone_of_the_process(self):
        # Codex prints local wall-clock time without a zone and without seconds.
        for delta in (timedelta(minutes=90, seconds=17), timedelta(hours=5), timedelta(hours=30, seconds=59),
                      timedelta(days=6, minutes=7), timedelta(days=23)):
            reset = T0 + delta
            text = self.codex_message(reset, T0)
            with self.subTest(text=text):
                self.assertEqual(classified_failure(text, text, "codex", T0).kind, "quota")
                # The printed minute is rounded up, so the result never precedes the real reset.
                expected = reset.replace(second=0) + timedelta(minutes=1)
                self.assertEqual(quota_hint(text, T0, "codex"), {"retry_at": expected})
                self.assertTrue(reset < expected <= reset + timedelta(minutes=1))
                # The same zoneless text from any other source stays ambiguous.
                self.assertEqual(quota_hint(text, T0), {})
                self.assertEqual(quota_hint(text, T0, "claude"), {})

    def test_codex_messages_without_a_usable_reset_yield_nothing(self):
        for text in ("You’ve hit your usage limit. Try again later.",
                     "You’ve hit your usage limit. Upgrade to Plus to continue using Codex "
                     "(https://chatgpt.com/explore/plus), or try again later.",
                     "try again at 13:45 PM.", "try again at 0:10 AM.", "try again at Feb 30th, 2027 3:45 PM.",
                     "try again at Foo 3rd, 2027 3:45 PM.", "try again at 3:45.", "try again at 3 PM."):
            with self.subTest(text=text):
                self.assertEqual(quota_hint(text, T0, "codex"), {})

    @unittest.skipUnless(hasattr(time, "tzset"), "changing the process time zone needs POSIX tzset")
    def test_codex_reset_in_a_repeated_local_hour_uses_the_later_instant(self):
        previous = os.environ.get("TZ")
        os.environ["TZ"] = "America/Los_Angeles"
        time.tzset()
        try:
            # 1:30 AM occurs twice on November 1, 2026 in this zone: 08:30 and 09:30 UTC.
            observed = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)
            hint = quota_hint("or try again at Nov 1st, 2026 1:30 AM.", observed, "codex")
            self.assertEqual(hint, {"retry_at": datetime(2026, 11, 1, 9, 31, tzinfo=timezone.utc)})
            # The short form is placed on the local day of the failure (still October 31 there).
            hint = quota_hint("or try again at 11:45 PM.", observed, "codex")
            self.assertEqual(hint, {"retry_at": datetime(2026, 11, 1, 6, 46, tzinfo=timezone.utc)})
        finally:
            if previous is None:
                del os.environ["TZ"]
            else:
                os.environ["TZ"] = previous
            time.tzset()

    def test_the_latest_stated_reset_wins(self):
        hint = quota_hint("resets at 2026-10-10T15:00:00Z; retry after 6 hours", T0)
        self.assertEqual(hint["retry_at"], at(hours=6))

    def test_failure_keeps_structured_metadata_and_stays_backward_compatible(self):
        plain = AgentFailure("rate limit", "quota")
        self.assertEqual((plain.kind, plain.provider, plain.retry_at, plain.retry_after_seconds, str(plain)),
                         ("quota", None, None, None, "rate limit"))
        self.assertEqual(AgentFailure("boom").kind, "agent_error")
        failure = classified_failure("codex/reviewer exit=1", "Rate limit exceeded. Retry after 120 seconds.",
                                     "codex", T0)
        self.assertEqual((failure.kind, failure.provider, failure.retry_at, failure.retry_after_seconds),
                         ("quota", "codex", at(seconds=120), 120))
        # A reset is read only for quota failures.
        other = classified_failure("m", "Not logged in. Retry after 120 seconds", "claude", T0)
        self.assertEqual((other.kind, other.retry_at), ("authentication", None))

    def test_adapter_attaches_provider_and_reset_from_cli_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            adapter = OfficialCLI(clock=lambda: T0)
            stderr = "Usage limit reached; resets at 2026-10-10T18:00:00Z"
            with patch("patchrondo.providers.execute", return_value=Result(1, "", stderr)):
                with self.assertRaises(AgentFailure) as caught:
                    adapter.invoke("codex", "reviewer", "review", path, path / "runs")
            self.assertEqual((caught.exception.kind, caught.exception.provider, caught.exception.retry_at),
                             ("quota", "codex", at(hours=4)))
            body = json.dumps({"is_error": True, "result": "Rate limit exceeded. Retry after 90 seconds."})
            with patch("patchrondo.providers.execute", return_value=Result(0, body, "")):
                with self.assertRaises(AgentFailure) as caught:
                    adapter.invoke("claude", "developer", "task", path, path / "runs")
            self.assertEqual((caught.exception.kind, caught.exception.provider, caught.exception.retry_at),
                             ("quota", "claude", at(seconds=90)))


class PolicyTests(unittest.TestCase):
    def plan(self, failures=1, retry_at=None, first=T0, failure_at=T0, now=None, **limits):
        return recovery.plan(failures=failures, first_failure_at=first, failure_at=failure_at,
                             retry_at=retry_at, cfg={**LIMITS, **limits}, now=now or failure_at)

    def test_exponential_backoff_is_capped(self):
        waits = [(self.plan(failures=n, max_consecutive_retries=10)["resume_at"] - T0).total_seconds()
                 for n in range(1, 8)]
        self.assertEqual(waits, [120, 240, 480, 960, 1800, 1800, 1800])
        self.assertEqual(self.plan()["source"], "backoff")

    def test_known_reset_is_respected_with_the_safety_margin(self):
        decision = self.plan(retry_at=at(hours=3))
        self.assertEqual(decision, {"action": "retry", "resume_at": at(hours=3, seconds=30),
                                    "source": "provider_reset"})
        self.assertEqual(self.plan(retry_at=at(hours=3), reset_safety_margin_seconds=0)["resume_at"], at(hours=3))

    def test_reset_never_shortens_the_backoff(self):
        decision = self.plan(retry_at=at(seconds=5))
        self.assertEqual((decision["resume_at"], decision["source"]), (at(seconds=120), "backoff"))

    def test_past_reset_falls_back_to_backoff(self):
        for stale in (at(hours=-1), at(seconds=-5), T0):
            with self.subTest(stale=stale):
                decision = self.plan(retry_at=stale)
                self.assertEqual((decision["resume_at"], decision["source"]), (at(seconds=120), "backoff"))
                # Not even a large safety margin turns an expired reset into a plan.
                decision = self.plan(retry_at=stale, reset_safety_margin_seconds=3600)
                self.assertEqual((decision["resume_at"], decision["source"]), (at(seconds=120), "backoff"))

    def test_reset_beyond_the_budget_stops_without_an_earlier_retry(self):
        self.assertEqual(self.plan(retry_at=at(days=3)), {"action": "stop", "reason": "reset_beyond_budget"})
        self.assertEqual(self.plan(retry_at=datetime.max.replace(tzinfo=timezone.utc)),
                         {"action": "stop", "reason": "reset_beyond_budget"})
        # Exactly at the budget is still allowed.
        edge = self.plan(retry_at=at(seconds=86400 - 30))
        self.assertEqual(edge["resume_at"], at(seconds=86400))

    def test_backoff_beyond_the_budget_stops(self):
        decision = self.plan(failures=2, first=T0, failure_at=at(seconds=200), max_total_wait_seconds=300)
        self.assertEqual(decision, {"action": "stop", "reason": "wait_budget_exceeded"})

    def test_retry_limit_stops(self):
        self.assertEqual(self.plan(failures=3)["action"], "retry")
        self.assertEqual(self.plan(failures=4), {"action": "stop", "reason": "max_consecutive_retries"})

    def test_fractional_times_round_up(self):
        decision = self.plan(failure_at=T0 + timedelta(milliseconds=1))
        self.assertEqual(decision["resume_at"], at(seconds=121))

    def test_stated_reset_reads_either_form_of_a_saved_failure(self):
        failed = "2026-10-10T14:00:00+00:00"
        self.assertIsNone(recovery.stated_reset({"kind": "quota", "at": failed}))
        self.assertEqual(recovery.stated_reset({"at": failed, "retry_at": "2026-10-10T17:00:00+00:00"}), at(hours=3))
        self.assertEqual(recovery.stated_reset({"at": failed, "retry_after_seconds": 600}), at(minutes=10))
        self.assertEqual(recovery.stated_reset({"at": failed, "retry_after_seconds": 600,
                                                "retry_at": "2026-10-10T14:05:00+00:00"}), at(minutes=10))
        # A wait cannot be placed without the failure time, and odd values are not guessed.
        self.assertIsNone(recovery.stated_reset({"retry_after_seconds": 600}))
        for value in (True, "600", -1, 0, float("nan"), float("inf"), None, [600]):
            self.assertIsNone(recovery.stated_reset({"at": failed, "retry_after_seconds": value}))
        self.assertEqual(recovery.stated_reset({"at": failed, "retry_after_seconds": 10 ** 15}).year, 9999)

    def test_persisted_timestamps_need_an_explicit_offset(self):
        self.assertEqual(recovery.parse_time("2026-10-10T17:00:30Z"), at(hours=3, seconds=30))
        self.assertEqual(recovery.parse_time("2026-10-10T19:00:30+02:00"), at(hours=3, seconds=30))
        for value in ("2026-10-10T17:00:30", "soon", "", None, 12, True):
            self.assertIsNone(recovery.parse_time(value))


class SupervisorTests(RecoveryCase):
    def test_auto_resume_waits_for_the_provider_reset_then_completes(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota("Usage limit reached; resets at 2026-10-10T17:00:00Z", "claude"))
        seen = []

        def during_wait(_seconds):
            state = read_json(path / "state.json")
            seen.append((state["status"], state["recovery"]["status"], state["recovery"]["resume_at"],
                         (path / ".run.lock").exists()))

        self.clock.on_sleep = during_wait
        messages = []
        state = self.supervise(task_id, adapter, notify=messages.append)
        self.assertEqual(state["status"], "done")
        self.assertIsNone(state["recovery"])
        self.assertIsNone(state["last_error"])
        self.assertEqual(adapter.roles, ["developer", "developer", "reviewer"])
        # The plan was persisted before the first sleep and no lock was held while waiting.
        self.assertTrue(seen)
        self.assertEqual(set(seen), {("paused", "scheduled", "2026-10-10T17:00:30+00:00", False)})
        self.assertGreaterEqual(adapter.calls[1]["at"], at(hours=3, seconds=30))
        self.assertEqual(sum(self.clock.sleeps), 3 * 3600 + 30)
        self.assertLessEqual(max(self.clock.sleeps), recovery.POLL_SECONDS)
        self.assertTrue(all(call["locked"] for call in adapter.calls))
        self.assertIn("automatic retry 1/3 at 2026-10-10T17:00:30+00:00 (provider_reset)", messages[0])
        recovery_events = [e for e in read_json(path / "state.json")["history"] if e["event"].startswith("recovery_")]
        self.assertEqual([e["event"] for e in recovery_events],
                         ["recovery_scheduled", "recovery_wait_started", "recovery_retry_started",
                          "recovery_completed"])
        self.assertEqual({key: recovery_events[0][key] for key in ("attempt", "resume_at", "source", "provider")},
                         {"attempt": 1, "resume_at": "2026-10-10T17:00:30+00:00", "source": "provider_reset",
                          "provider": "claude"})

    def test_flag_activates_recovery_when_the_configuration_disables_it(self):
        self.assertFalse(config(self.home)["recovery"]["enabled"])
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota())
        state = self.supervise(task_id, adapter, auto_resume=True)
        self.assertEqual(state["status"], "done")
        self.assertEqual(self.clock.sleeps, [30, 30, 30, 30])
        self.assertEqual(len(adapter.calls), 3)

    def test_configuration_activates_recovery_and_no_auto_resume_overrides_it(self):
        self.configure(enabled=True)
        first, path, _ = self.task()
        adapter = self.script(path, self.quota())
        self.assertEqual(self.supervise(first, adapter, auto_resume=None)["status"], "done")
        self.assertEqual(sum(self.clock.sleeps), 120)
        second, path, _ = self.task()
        adapter = self.script(path, self.quota())
        paused = self.supervise(second, adapter, auto_resume=False)
        self.assertEqual((paused["status"], paused["recovery"], len(adapter.calls)), ("paused", None, 1))
        self.assertEqual(sum(self.clock.sleeps), 120)

    def test_codex_usage_limit_message_plans_the_retry_at_its_local_reset(self):
        task_id, path, _ = self.task()
        reset = at(hours=5, seconds=20)
        text = ParsingTests.codex_message(reset, T0)
        adapter = self.script(path, None, self.quota(text, "codex"))
        state = self.supervise(task_id, adapter)
        self.assertEqual(state["status"], "done")
        planned = next(e for e in state["history"] if e["event"] == "recovery_scheduled")
        self.assertEqual((planned["source"], planned["provider"], recovery.parse_time(planned["resume_at"])),
                         ("provider_reset", "codex", at(hours=5, minutes=1, seconds=30)))
        self.assertGreaterEqual(adapter.calls[2]["at"], reset + timedelta(seconds=30))
        self.assertEqual(adapter.roles, ["developer", "reviewer", "reviewer"])

    def test_ambiguous_reset_uses_the_backoff(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota("Usage limit reached; resets at 9pm", "codex"), always=None)
        paused = run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock)
        self.assertNotIn("retry_at", paused["last_error"])
        self.assertEqual((paused["recovery"]["schedule_source"], paused["recovery"]["resume_at"]),
                         ("backoff", "2026-10-10T14:02:00+00:00"))

    def test_malformed_or_past_reset_does_not_cause_an_immediate_retry(self):
        for text, retry_at in (("Usage limit reached; resets at 2026-02-30T18:00:00Z", None),
                               ("Usage limit reached; resets at 2026-10-10T13:00:00Z", "2026-10-10T13:00:00+00:00")):
            with self.subTest(text=text):
                self.clock.now, self.clock.sleeps = T0, []
                task_id, path, _ = self.task()
                adapter = self.script(path, always=self.quota(text, "codex"))
                state = self.supervise(task_id, adapter)
                self.assertEqual(state["last_error"].get("retry_at"), retry_at)
                self.assertEqual((state["status"], state["recovery"]["status"]), ("paused", "stopped"))
                # Backoff only: 120 s, 240 s, 480 s between the four calls, then a stop.
                times = [(call["at"] - T0).total_seconds() for call in adapter.calls]
                self.assertEqual(times, [0, 120, 360, 840])
                self.assertEqual({e["source"] for e in read_json(path / "state.json")["history"]
                                  if e["event"] == "recovery_scheduled"}, {"backoff"})

    def test_sub_second_times_never_make_a_retry_early(self):
        self.configure(reset_safety_margin_seconds=0)
        observed = T0 + timedelta(milliseconds=900)
        # A stated reset: saved rounded up, so the plan is not before failure + 300 s.
        self.clock.now = observed
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota("Rate limit exceeded. Retry after 300 seconds.", "codex"))
        paused = run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock)
        self.assertEqual(paused["last_error"]["retry_at"], "2026-10-10T14:05:01+00:00")
        self.assertGreaterEqual(recovery.parse_time(paused["recovery"]["resume_at"]), observed + timedelta(seconds=300))
        # An absolute reset with a fractional second, as another adapter might provide it.
        task_id, path, _ = self.task()
        fractional = T0 + timedelta(seconds=300, milliseconds=500)
        adapter = self.script(path, lambda: AgentFailure("usage limit", "quota", retry_at=fractional))
        paused = run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock)
        self.assertEqual(paused["last_error"]["retry_at"], "2026-10-10T14:05:01+00:00")
        self.assertGreaterEqual(recovery.parse_time(paused["recovery"]["resume_at"]), fractional)
        # A backoff derived later from the saved failure time is not shorter than a fresh one.
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota())
        run_task(self.home, task_id, adapter=adapter, clock=self.clock)  # recovery not active
        self.clock.now = at(seconds=50)
        planned = run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock)
        self.assertEqual(len(adapter.calls), 1)
        self.assertGreaterEqual(recovery.parse_time(planned["recovery"]["resume_at"]), observed + timedelta(seconds=120))

    def relative_only(self, seconds, **extra):
        return lambda: AgentFailure("usage limit", "quota", provider="codex", retry_after_seconds=seconds, **extra)

    def test_relative_wait_without_an_absolute_reset_is_honored(self):
        # An adapter may state only a wait: it counts from the failure time and is rounded up.
        for seconds, reset, planned in ((3600, "2026-10-10T15:00:00+00:00", at(seconds=3630)),
                                        (90.5, "2026-10-10T14:01:31+00:00", at(seconds=121))):
            with self.subTest(seconds=seconds):
                self.clock.now, self.clock.sleeps = T0, []
                task_id, path, _ = self.task()
                adapter = self.script(path, self.relative_only(seconds))
                paused = run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock)
                self.assertEqual(paused["last_error"]["retry_at"], reset)
                self.assertEqual((paused["recovery"]["schedule_source"],
                                  recovery.parse_time(paused["recovery"]["resume_at"])), ("provider_reset", planned))
                self.assertEqual(self.supervise(task_id, adapter)["status"], "done")
                self.assertGreaterEqual(adapter.calls[1]["at"], planned)

    def test_later_of_an_absolute_reset_and_a_relative_wait_is_used(self):
        for extra, planned in (({"retry_at": at(minutes=1)}, at(seconds=3630)),
                               ({"retry_at": at(hours=3)}, at(hours=3, seconds=30))):
            with self.subTest(retry_at=extra["retry_at"]):
                task_id, path, _ = self.task()
                adapter = self.script(path, self.relative_only(3600, **extra))
                paused = run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock)
                self.assertEqual(recovery.parse_time(paused["recovery"]["resume_at"]), planned)

    def test_unusable_relative_waits_fall_back_or_stop(self):
        for seconds in (True, 0, -5, "120", float("nan"), float("inf")):
            with self.subTest(seconds=seconds):
                task_id, path, _ = self.task()
                adapter = self.script(path, self.relative_only(seconds))
                paused = run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock)
                self.assertNotIn("retry_at", paused["last_error"])
                self.assertNotIn("retry_after_seconds", paused["last_error"])
                self.assertEqual((paused["recovery"]["schedule_source"], paused["recovery"]["resume_at"]),
                                 ("backoff", "2026-10-10T14:02:00+00:00"))
        # A wait far beyond any date is not brought forward either.
        task_id, path, _ = self.task()
        adapter = self.script(path, always=self.relative_only(10 ** 15))
        state = self.supervise(task_id, adapter)
        self.assertEqual((state["recovery"]["status"], state["recovery"]["stop_reason"], len(adapter.calls)),
                         ("stopped", "reset_beyond_budget", 1))

    def test_saved_pause_with_only_a_relative_wait_is_honored_later(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, self.relative_only(3600))
        run_task(self.home, task_id, adapter=adapter, clock=self.clock)  # recovery not active
        state = read_json(path / "state.json")
        state["last_error"].pop("retry_at", None)  # as saved when only the wait was recorded
        self.assertEqual(state["last_error"]["retry_after_seconds"], 3600)
        save_json(path / "state.json", state)
        self.clock.now = at(minutes=10)
        self.assertEqual(self.supervise(task_id, adapter)["status"], "done")
        self.assertGreaterEqual(adapter.calls[1]["at"], at(seconds=3630))
        self.assertEqual(sum(self.clock.sleeps), 3630 - 600)

    def test_misclassified_error_is_bounded_by_the_retry_limit(self):
        task_id, path, _ = self.task()
        # Not a usage limit, but the wording matches the quota pattern.
        text = "error: could not read docs/rate-limit-policy.md"
        self.assertEqual(classified_failure(text, text, "claude", T0).kind, "quota")
        adapter = self.script(path, always=self.quota(text, "claude"))
        messages = []
        state = self.supervise(task_id, adapter, notify=messages.append)
        self.assertEqual(len(adapter.calls), 1 + LIMITS["max_consecutive_retries"])
        self.assertEqual((state["status"], state["phase"]), ("paused", "develop"))
        self.assertEqual((state["recovery"]["status"], state["recovery"]["stop_reason"],
                          state["recovery"]["consecutive_failures"]), ("stopped", "max_consecutive_retries", 4))
        self.assertIsNone(state["recovery"]["resume_at"])
        self.assertEqual(sum(self.clock.sleeps), 120 + 240 + 480)
        self.assertEqual(len(messages), 3)
        self.assertEqual(self.events(path).count("recovery_stopped"), 1)
        self.assertFalse((path / ".run.lock").exists())

    def test_configured_retry_limit_is_enforced(self):
        self.configure(max_consecutive_retries=1)
        task_id, path, _ = self.task()
        adapter = self.script(path, always=self.quota())
        state = self.supervise(task_id, adapter)
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(state["recovery"]["stop_reason"], "max_consecutive_retries")

    def test_total_wait_budget_is_enforced(self):
        self.configure(max_total_wait_seconds=300)
        task_id, path, _ = self.task()
        adapter = self.script(path, always=self.quota())
        state = self.supervise(task_id, adapter)
        # First wait 120 s; the second would end 360 s after the first failure.
        self.assertEqual([(call["at"] - T0).total_seconds() for call in adapter.calls], [0, 120])
        self.assertEqual((state["recovery"]["status"], state["recovery"]["stop_reason"]),
                         ("stopped", "wait_budget_exceeded"))
        self.assertEqual(sum(self.clock.sleeps), 120)

    def test_reset_beyond_the_budget_leaves_the_task_paused_without_a_retry(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, always=self.quota("Usage limit reached; resets at 2026-10-13T14:00:00Z", "codex"))
        state = self.supervise(task_id, adapter)
        self.assertEqual((len(adapter.calls), self.clock.sleeps), (1, []))
        self.assertEqual((state["status"], state["recovery"]["status"], state["recovery"]["stop_reason"]),
                         ("paused", "stopped", "reset_beyond_budget"))
        # Starting automatic resume again before the reset still makes no call.
        self.clock.now = at(days=2)
        again = self.supervise(task_id, adapter)
        self.assertEqual((len(adapter.calls), again["recovery"]["stop_reason"]), (1, "reset_beyond_budget"))
        # Once the reset has passed there is nothing left to wait for.
        self.clock.now = at(days=3, minutes=1)
        adapter.always = None
        self.assertEqual(self.supervise(task_id, adapter)["status"], "done")
        self.assertEqual(self.clock.sleeps, [])

    def test_plan_survives_a_restart_and_is_not_run_early(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota("Usage limit reached; resets at 2026-10-10T17:00:00Z", "claude"))
        paused = run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock)
        self.assertEqual(paused["recovery"]["resume_at"], "2026-10-10T17:00:30+00:00")
        saved = (path / "state.json").read_bytes()
        # A new process before the reset: neither a direct run nor a new supervisor may call.
        self.clock.now = at(hours=2)
        for _ in range(3):
            self.assertEqual(run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock),
                             paused)
        self.assertEqual((path / "state.json").read_bytes(), saved)
        self.assertEqual(len(adapter.calls), 1)
        restarted = FakeClock()
        restarted.now = at(hours=2)
        fresh = Script(restarted)
        state = recovery.supervise(self.home, task_id, adapter=fresh, auto_resume=True,
                                   clock=restarted, sleep=restarted.sleep)
        self.assertEqual(state["status"], "done")
        self.assertEqual(sum(restarted.sleeps), 3600 + 30)
        self.assertGreaterEqual(fresh.calls[0]["at"], at(hours=3, seconds=30))
        self.assertEqual(fresh.roles, ["developer", "reviewer"])
        self.assertEqual(self.events(path).count("recovery_wait_started"), 1)

    def test_no_lock_is_held_while_waiting(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota(), self.quota())
        locks = []

        def during_wait(_seconds):
            locks.append((path / ".run.lock").exists())
            with TaskLock(path):  # another process could take the lock at any point of the wait
                pass

        self.clock.on_sleep = during_wait
        self.assertEqual(self.supervise(task_id, adapter)["status"], "done")
        self.assertEqual(len(locks), 4 + 8)
        self.assertFalse(any(locks))
        self.assertTrue(all(call["locked"] for call in adapter.calls))

    def test_counter_resets_when_the_phase_advances(self):
        # With one retry allowed, two failures in a row would stop. Progress between them must reset the count.
        self.configure(max_consecutive_retries=1)
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota(), None, self.quota(), None)
        state = self.supervise(task_id, adapter)
        self.assertEqual(state["status"], "done")
        self.assertEqual(adapter.roles, ["developer", "developer", "reviewer", "reviewer"])
        scheduled = [e for e in read_json(path / "state.json")["history"] if e["event"] == "recovery_scheduled"]
        self.assertEqual([e["attempt"] for e in scheduled], [1, 1])
        self.assertEqual(self.events(path).count("recovery_completed"), 2)
        self.assertEqual(self.events(path).count("recovery_stopped"), 0)

    def test_consecutive_failures_at_the_same_point_accumulate(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota(), self.quota(), self.quota())
        state = self.supervise(task_id, adapter)
        self.assertEqual(state["status"], "done")
        scheduled = [e for e in read_json(path / "state.json")["history"] if e["event"] == "recovery_scheduled"]
        self.assertEqual([e["attempt"] for e in scheduled], [1, 2, 3])
        self.assertEqual(sum(self.clock.sleeps), 120 + 240 + 480)

    def test_review_is_resumed_without_repeating_development_or_tests(self):
        task_id, path, workspace = self.task()
        adapter = self.script(path, None, None, None,
                              self.quota("Rate limit exceeded. Retry after 600 seconds.", "codex"),
                              dev_values=["bad", "good"])
        paused = run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock)
        self.assertEqual((paused["status"], paused["phase"], paused["iteration"]), ("paused", "review", 2))
        self.assertEqual(paused["recovery"], {
            "status": "scheduled", "consecutive_failures": 1, "max_consecutive_retries": 3,
            "first_failure_at": "2026-10-10T14:00:00+00:00", "resume_at": "2026-10-10T14:10:30+00:00",
            "phase": "review", "iteration": 2, "schedule_source": "provider_reset", "provider": "codex",
            "stop_reason": None})
        self.assertEqual({key: paused["last_error"][key] for key in ("kind", "provider", "retry_at",
                                                                     "retry_after_seconds", "at")},
                         {"kind": "quota", "provider": "codex", "retry_at": "2026-10-10T14:10:00+00:00",
                          "retry_after_seconds": 600, "at": "2026-10-10T14:00:00+00:00"})
        kept = {name: (path / name).read_bytes() for name in ("task.md", "handoff.md", "feedback.md")}
        self.assertIn(b"tests_missing_or_failed", kept["feedback.md"])
        state = self.supervise(task_id, adapter)
        self.assertEqual(state["status"], "done")
        self.assertEqual(adapter.roles, ["developer", "reviewer", "developer", "reviewer", "reviewer"])
        self.assertGreaterEqual(adapter.calls[-1]["at"], at(seconds=630))
        for name, content in kept.items():
            self.assertEqual((path / name).read_bytes(), content, name)
        self.assertEqual(state["history"][:len(paused["history"])], paused["history"])
        self.assertEqual(state["tests"], paused["tests"])
        self.assertEqual(state["test_context"], paused["test_context"])
        self.assertEqual(state["review"]["verdict"], "APPROVED")
        events = self.events(path)
        self.assertEqual((events.count("development_completed"), events.count("tests_completed")), (2, 2))
        self.assertEqual((workspace / "feature.txt").read_text(encoding="utf-8"), "good")

    def test_interrupt_while_waiting_keeps_the_plan(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota("Usage limit reached; resets at 2026-10-10T17:00:00Z", "codex"))

        def interrupt(_seconds):
            if len(self.clock.sleeps) == 2:
                raise KeyboardInterrupt

        self.clock.on_sleep = interrupt
        messages = []
        state = self.supervise(task_id, adapter, notify=messages.append)
        self.assertEqual((state["status"], state["recovery"]["status"], state["recovery"]["resume_at"]),
                         ("paused", "scheduled", "2026-10-10T17:00:30+00:00"))
        self.assertEqual(state["last_error"]["kind"], "quota")
        self.assertEqual(len(adapter.calls), 1)
        self.assertFalse((path / ".run.lock").exists())
        self.assertIn("stays saved", messages[-1])
        self.assertNotIn("interrupted", self.events(path))
        # Waiting again later resumes at the same instant.
        self.clock.on_sleep = None
        self.assertEqual(self.supervise(task_id, adapter)["status"], "done")
        self.assertGreaterEqual(adapter.calls[1]["at"], at(hours=3, seconds=30))

    def test_orphaned_lock_stops_recovery_without_unlocking(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota())

        def runner_dies(_seconds):
            if not (path / ".run.lock").exists():
                save_json(path / ".run.lock", {"pid": DEAD_PID, "time": "2026-10-10T14:00:10+00:00"})

        self.clock.on_sleep = runner_dies
        # --unlock applies to the first, explicit run only: the automatic retry must not use it.
        with self.assertRaisesRegex(recovery.RecoveryAborted, "lock was not removed") as caught:
            self.supervise(task_id, adapter, force_unlock=True)
        self.assertIsInstance(caught.exception.__cause__, LockBusy)
        self.assertEqual(read_json(path / ".run.lock")["pid"], DEAD_PID)
        self.assertEqual(len(adapter.calls), 1)
        state = read_json(path / "state.json")
        self.assertEqual((state["status"], state["recovery"]["status"]), ("paused", "scheduled"))

    def test_native_windows_liveness_check_stays_conservative(self):
        task_id, path, _ = self.task()
        save_json(path / ".run.lock", {"pid": DEAD_PID})
        # Only the bare check runs under the patch: pathlib reads os.name too.
        with patch("patchrondo.storage.os.name", "nt"):
            alive = _alive(DEAD_PID)
        self.assertTrue(alive)
        with patch("patchrondo.storage._alive", return_value=alive):
            with self.assertRaisesRegex(RuntimeError, "still active"):
                with TaskLock(path, force=True):
                    pass
        self.assertTrue((path / ".run.lock").exists())

    def test_only_quota_failures_are_retried(self):
        kinds = ["authentication", "configuration", "timeout", "invalid_review", "agent_error",
                 "empty_output", "something_new"]
        failures = {kind: (lambda kind=kind: AgentFailure(f"{kind} failure", kind)) for kind in kinds}
        failures["system_error"] = lambda: RuntimeError("disk failure")
        failures["interrupted"] = KeyboardInterrupt
        for kind, step in failures.items():
            with self.subTest(kind=kind):
                task_id, path, _ = self.task()
                adapter = self.script(path, step)
                state = self.supervise(task_id, adapter)
                self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", kind))
                self.assertIsNone(state["recovery"])
                self.assertEqual((len(adapter.calls), self.clock.sleeps), (1, []))
                self.assertFalse([name for name in self.events(path) if name.startswith("recovery_")])

    def test_state_machine_pauses_are_not_retried(self):
        # stale_tests: the reviewer edits the worktree.
        task_id, path, workspace = self.task()
        adapter = self.script(path)
        real = adapter.invoke

        def mutating(provider, role, prompt, workspace, run_dir):
            if role == "reviewer":
                (workspace / "feature.txt").write_text("bad", encoding="utf-8")
            return real(provider, role, prompt, workspace, run_dir)

        adapter.invoke = mutating
        state = self.supervise(task_id, adapter)
        self.assertEqual((state["status"], state["last_error"]["kind"], state["recovery"]),
                         ("paused", "stale_tests", None))
        # tests_disabled: the independent test gate cannot be met.
        cfg = read_json(self.home / "config.json")
        cfg["tests"]["enabled"] = False
        save_json(self.home / "config.json", cfg)
        task_id, path, _ = self.task()
        adapter = self.script(path)
        state = self.supervise(task_id, adapter)
        self.assertEqual((state["status"], state["last_error"]["kind"], state["recovery"]),
                         ("paused", "tests_disabled", None))
        self.assertEqual((len(adapter.calls), self.clock.sleeps), (2, []))

    def test_other_failure_during_a_retry_ends_the_recovery_with_its_reason(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota(), lambda: AgentFailure("Not logged in", "authentication"))
        state = self.supervise(task_id, adapter)
        self.assertEqual((state["status"], state["last_error"]["kind"]), ("paused", "authentication"))
        self.assertEqual((state["recovery"]["status"], state["recovery"]["stop_reason"]),
                         ("stopped", "authentication"))
        self.assertEqual((len(adapter.calls), sum(self.clock.sleeps)), (2, 120))
        self.assertEqual(self.events(path)[-2:], ["task_paused", "recovery_stopped"])

    def test_manual_resume_runs_at_once_and_cancels_the_plan(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota("Usage limit reached; resets at 2026-10-10T17:00:00Z", "codex"),
                              self.quota())
        run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock)
        paused = run_task(self.home, task_id, adapter=adapter, auto_resume=False, clock=self.clock)
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual((paused["status"], paused["recovery"]), ("paused", None))
        self.assertIn("recovery_cancelled", self.events(path))
        # A supervisor that was waiting for the cancelled plan must not act on it.
        self.clock.now = at(hours=4)
        state = run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock,
                         retry_of="2026-10-10T17:00:30+00:00")
        self.assertEqual((state, len(adapter.calls)), (paused, 2))

    def test_explicit_auto_resume_on_an_earlier_quota_pause_waits_for_its_reset(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota("Usage limit reached; resets at 2026-10-10T17:00:00Z", "codex"))
        paused = run_task(self.home, task_id, adapter=adapter, clock=self.clock)  # recovery not active
        self.assertEqual((paused["recovery"], paused["last_error"]["retry_at"]), (None, "2026-10-10T17:00:00+00:00"))
        self.clock.now = at(minutes=30)
        state = self.supervise(task_id, adapter)
        self.assertEqual(state["status"], "done")
        self.assertGreaterEqual(adapter.calls[1]["at"], at(hours=3, seconds=30))
        self.assertEqual(sum(self.clock.sleeps), 2.5 * 3600 + 30)

    def test_unreadable_plan_is_never_guessed(self):
        for corrupt in ({"resume_at": "tomorrow"}, {"resume_at": "2026-10-10T14:02:00"},
                        {"consecutive_failures": "1"}):
            with self.subTest(corrupt=corrupt):
                task_id, path, _ = self.task()
                adapter = self.script(path, self.quota())
                run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock)
                state = read_json(path / "state.json")
                state["recovery"].update(corrupt)
                save_json(path / "state.json", state)
                self.clock.now = at(days=1)
                result = self.supervise(task_id, adapter)
                self.assertEqual((result["status"], result["recovery"]["status"],
                                  result["recovery"]["stop_reason"]), ("paused", "stopped", "invalid_schedule"))
                self.assertEqual(len(adapter.calls), 1)
                self.clock.now = T0

    def test_plan_is_dropped_when_the_pause_is_no_longer_a_quota_failure(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota())
        run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock)
        state = read_json(path / "state.json")
        state["last_error"]["kind"] = "authentication"
        save_json(path / "state.json", state)
        self.clock.now = at(hours=1)
        result = self.supervise(task_id, adapter)
        self.assertEqual((result["recovery"]["stop_reason"], len(adapter.calls)), ("invalid_schedule", 1))

    def test_supervisor_stops_when_recovery_is_disabled_during_the_wait(self):
        self.configure(enabled=True)
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota())
        self.clock.on_sleep = lambda _seconds: self.configure(enabled=False)
        messages = []
        state = self.supervise(task_id, adapter, auto_resume=None, notify=messages.append)
        self.assertEqual((state["status"], state["recovery"]["status"], len(adapter.calls)),
                         ("paused", "scheduled", 1))
        self.assertIn("disabled", messages[-1])

    def test_waiting_ends_early_when_the_task_is_completed_elsewhere(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota("Usage limit reached; resets at 2026-10-10T17:00:00Z", "codex"))

        def finish_manually(_seconds):
            if len(self.clock.sleeps) == 1:
                run_task(self.home, task_id, adapter=adapter, auto_resume=False, clock=self.clock)

        self.clock.on_sleep = finish_manually
        state = self.supervise(task_id, adapter)
        self.assertEqual(state["status"], "done")
        self.assertEqual(len(self.clock.sleeps), 2)
        self.assertEqual(adapter.roles, ["developer", "developer", "reviewer"])


class ConcurrencyTests(RecoveryCase):
    def test_retry_for_a_replaced_plan_makes_no_call(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota(), self.quota())
        first = run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock)
        plan_one = first["recovery"]["resume_at"]
        self.clock.now = at(seconds=120)
        # The first supervisor retries, hits the limit again and reschedules.
        second = run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock,
                          retry_of=plan_one)
        plan_two = second["recovery"]["resume_at"]
        self.assertEqual((plan_one, plan_two), ("2026-10-10T14:02:00+00:00", "2026-10-10T14:06:00+00:00"))
        self.assertEqual(len(adapter.calls), 2)
        saved = (path / "state.json").read_bytes()
        # The second supervisor wakes for the first plan: its retry is refused under the lock.
        for retry_of in (plan_one, plan_two):
            late = run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock,
                            retry_of=retry_of)
            self.assertEqual(late, second)
        self.assertEqual((path / "state.json").read_bytes(), saved)
        self.assertEqual(len(adapter.calls), 2)
        self.assertFalse((path / ".run.lock").exists())
        # It then follows the new plan instead of acting on the old one.
        state = self.supervise(task_id, adapter)
        self.assertEqual(state["status"], "done")
        self.assertGreaterEqual(adapter.calls[2]["at"], at(seconds=360))

    def test_two_supervisors_never_call_a_provider_twice_for_one_plan(self):
        task_id, path, _ = self.task()
        entered, release, waiting, wake = (threading.Event() for _ in range(4))
        adapter = self.script(path, self.quota())
        run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock)
        plan_one = read_json(path / "state.json")["recovery"]["resume_at"]
        real = adapter.invoke

        def slow(provider, role, prompt, workspace, run_dir):
            entered.set()
            self.assertTrue(release.wait(20))
            adapter.steps.append(self.quota())
            return real(provider, role, prompt, workspace, run_dir)

        adapter.invoke = slow
        outcome = {}

        def waiting_sleep(_seconds):
            waiting.set()
            self.assertTrue(wake.wait(20))

        def second_supervisor():
            try:
                outcome["state"] = recovery.supervise(self.home, task_id, adapter=adapter, auto_resume=True,
                                                      clock=self.clock, sleep=waiting_sleep)
            except BaseException as exc:  # reported to the main thread below
                outcome["error"] = exc

        def first_supervisor():
            try:
                outcome["first"] = run_task(self.home, task_id, adapter=adapter, auto_resume=True,
                                            clock=self.clock, retry_of=plan_one)
            except BaseException as exc:
                outcome["first_error"] = exc

        other = threading.Thread(target=second_supervisor, daemon=True)
        other.start()
        self.assertTrue(waiting.wait(20))  # the second supervisor is waiting for the plan, without a lock
        self.assertFalse((path / ".run.lock").exists())
        self.clock.now = at(seconds=120)
        runner = threading.Thread(target=first_supervisor, daemon=True)
        runner.start()
        self.assertTrue(entered.wait(20))  # the first supervisor is inside its provider call
        wake.set()
        other.join(20)
        self.assertFalse(other.is_alive())
        # The lock stopped the second supervisor: no second call, no unlock.
        self.assertIsInstance(outcome.get("error"), recovery.RecoveryAborted)
        self.assertEqual(len(adapter.calls), 1)
        self.assertTrue((path / ".run.lock").exists())
        release.set()
        runner.join(20)
        self.assertFalse(runner.is_alive())
        self.assertNotIn("first_error", outcome)
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(outcome["first"]["recovery"]["resume_at"], "2026-10-10T14:06:00+00:00")
        self.assertFalse((path / ".run.lock").exists())


class ReportingTests(RecoveryCase):
    def test_report_and_interface_data_show_a_pending_plan(self):
        self.configure(enabled=True)
        task_id, path, _ = self.task()
        adapter = self.script(path, None, self.quota("Usage limit reached; resets at 2026-10-10T17:00:00Z", "codex"))
        run_task(self.home, task_id, adapter=adapter, clock=self.clock)
        report = (path / "report.md").read_text(encoding="utf-8")
        for expected in ("## Quota recovery", "**Provider at its limit**: codex",
                         "**Planned retry**: 2026-10-10T17:00:30+00:00 (UTC)",
                         "**Automatic retries**: 1 of 3 used, 2 left",
                         "reset time stated by the provider", "phase `review`, iteration 1",
                         "only while a `patchrondo run` or `resume` process",
                         "`recovery_scheduled` (attempt 1, resume_at 2026-10-10T17:00:30+00:00, "
                         "source provider_reset, provider codex)"):
            self.assertIn(expected, report)
        app = App(self.home)
        project = app.ws.projects()[0]
        detail = app.task_detail(project, task_id)
        self.assertEqual(detail["state"]["recovery"]["resume_at"], "2026-10-10T17:00:30+00:00")
        runtime = detail["summary"]["runtime"]
        self.assertIsNone(runtime["lock"])
        # The plan is saved, but run_task has returned: nothing is waiting, and the interface must say so.
        self.assertEqual((runtime["activity"], runtime["active"], runtime["waiting_process"]), ("plan_only", False, False))
        self.assertTrue(app.project_info(project)["config"]["recovery_enabled"])
        self.assertIn("retry 1/3 for codex planned at 2026-10-10T17:00:30+00:00",
                      recovery.describe(detail["state"]["recovery"]))

    def test_report_states_why_recovery_stopped_and_clears_after_success(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota(), self.quota(), self.quota(), self.quota())
        state = self.supervise(task_id, adapter)
        report = (path / "report.md").read_text(encoding="utf-8")
        self.assertIn("**Automatic recovery ended**: the limit of consecutive retries was reached", report)
        self.assertIn("**Automatic retries**: 3 of 3 used, 0 left", report)
        self.assertNotIn("Planned retry", report)
        self.assertIn("max_consecutive_retries after 4 consecutive failure(s)", recovery.describe(state["recovery"]))
        # A new explicit run starts a fresh streak; the last failure's backoff has long passed.
        self.clock.now = at(hours=5)
        done = self.supervise(task_id, adapter)
        self.assertEqual(done["status"], "done")
        report = (path / "report.md").read_text(encoding="utf-8")
        self.assertNotIn("## Quota recovery", report)
        self.assertIn("`recovery_completed`", report)
        events = self.events(path)
        self.assertEqual(events.count("recovery_scheduled"), 4)
        self.assertEqual(events.count("recovery_stopped"), 1)
        # The streak ends at the first checkpoint past the failed phase, not at task completion.
        self.assertEqual(events.count("recovery_completed"), 1)
        self.assertEqual(events[events.index("recovery_completed") - 1], "development_completed")

    def test_interface_shows_the_plan_with_text_nodes_and_never_as_a_service(self):
        static = resources.files("patchrondo").joinpath("static")
        page = static.joinpath("js/views/task.js").read_text(encoding="utf-8")
        for expected in ("recoveryCard(", "Quota recovery", "Provider", "Next attempt", "Chosen from", "Retries",
                         "Stopped because", "no process is waiting", "A saved plan is not a running service"):
            self.assertIn(expected, page)
        for name in ("js/views/task.js", "js/tasks.js", "js/rondo.js", "js/components.js"):
            self.assertNotIn("innerHTML", static.joinpath(name).read_text(encoding="utf-8"))

    def test_recovery_adds_only_structured_fields_beside_the_diagnostic_message(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota("Usage limit reached; resets at 2026-10-10T17:00:00Z", "codex"))
        state = run_task(self.home, task_id, adapter=adapter, auto_resume=True, clock=self.clock)
        # `message` is the adapter's diagnostic, an excerpt of CLI output; nothing else holds output text.
        self.assertEqual(set(state["last_error"]), {"kind", "message", "at", "provider", "retry_at"})
        self.assertEqual(state["last_error"]["message"], "Usage limit reached; resets at 2026-10-10T17:00:00Z")
        self.assertEqual(set(state["recovery"]), {
            "status", "consecutive_failures", "max_consecutive_retries", "first_failure_at", "resume_at",
            "phase", "iteration", "schedule_source", "provider", "stop_reason"})
        self.assertTrue(all(value is None or type(value) in (str, int) for value in state["recovery"].values()))


class CommandLineTests(RecoveryCase):
    def test_flags_are_parsed_for_run_and_resume(self):
        for command in ("run", "resume"):
            self.assertIsNone(parser().parse_args([command, "T-0123456789ab"]).auto_resume)
            self.assertTrue(parser().parse_args([command, "T-0123456789ab", "--auto-resume"]).auto_resume)
            self.assertFalse(parser().parse_args([command, "T-0123456789ab", "--no-auto-resume"]).auto_resume)

    def run_cli(self, adapter, *args):
        simulated = partial(recovery.supervise, clock=self.clock, sleep=self.clock.sleep)
        output = io.StringIO()
        with patch("patchrondo.core.OfficialCLI", return_value=adapter), \
                patch("patchrondo.recovery.supervise", simulated), contextlib.redirect_stdout(output):
            code = main(["--home", str(self.home), *args])
        return code, output.getvalue()

    def test_auto_resume_flag_recovers_and_its_absence_keeps_the_manual_flow(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, self.quota("Rate limit exceeded. Retry after 300 seconds.", "claude"))
        code, output = self.run_cli(adapter, "run", task_id)
        self.assertEqual(code, 2)
        self.assertNotIn("automatic retry", output)
        self.assertEqual((len(adapter.calls), self.clock.sleeps), (1, []))
        self.assertIsNone(read_json(path / "state.json")["recovery"])
        code, output = self.run_cli(adapter, "resume", task_id, "--auto-resume")
        self.assertEqual(code, 0)
        self.assertIn("automatic retry 1/3 at 2026-10-10T14:05:30+00:00 (provider_reset)", output)
        self.assertIn("· done ·", output)
        self.assertEqual(adapter.roles, ["developer", "developer", "reviewer"])
        self.assertGreaterEqual(adapter.calls[1]["at"], at(seconds=330))

    def test_stopped_recovery_is_reported_and_lock_stop_is_an_error(self):
        task_id, path, _ = self.task()
        adapter = self.script(path, always=self.quota())
        code, output = self.run_cli(adapter, "run", task_id, "--auto-resume")
        self.assertEqual(code, 2)
        self.assertIn("Quota recovery stopped: max_consecutive_retries", output)
        self.assertEqual(len(adapter.calls), 4)
        other, path, _ = self.task()
        adapter = self.script(path, self.quota())
        self.clock.on_sleep = lambda _seconds: save_json(path / ".run.lock", {"pid": DEAD_PID})
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code, _ = self.run_cli(adapter, "run", other, "--auto-resume")
        self.assertEqual(code, 1)
        self.assertIn("Automatic recovery stopped: the task lock is present", errors.getvalue())
        self.assertEqual(len(adapter.calls), 1)

    def test_no_test_reached_a_provider_cli(self):
        # The guard installed in setUp replaces the only function that starts provider CLIs.
        task_id, path, _ = self.task()
        self.supervise(task_id, self.script(path, self.quota()))
        self.provider_execute.assert_not_called()
        with self.assertRaisesRegex(AssertionError, "real provider call attempted"):
            OfficialCLI().invoke("claude", "developer", "task", path, path / "runs")


if __name__ == "__main__":
    unittest.main()
