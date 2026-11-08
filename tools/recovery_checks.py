"""Local checks for automatic quota recovery. Scripted or synthetic providers only.

protections  Remove one safeguard at a time from a temporary copy of the sources
             and confirm that the tests fail: the recovery and adapter tests,
             or the interface tests named by the entry. The working tree is
             never modified. A safeguard whose removal goes unnoticed, or whose
             source pattern no longer matches, fails the check.
real-clock   Run the supervisor with synthetic CLIs on the real clock: interrupt
             a 12-second wait with SIGINT, restart, and confirm that the plan
             was kept and no call preceded it. Requires POSIX signals; skipped
             on native Windows (use WSL2).

Without a command both run. Neither reaches a provider CLI or uses plan quota.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "patchrondo"
TESTS = ("test_recovery", "test_adapters")


class Mutation(NamedTuple):
    name: str
    file: str
    old: str
    new: str
    tests: tuple[str, ...] = ()  # test modules or classes that must notice; TESTS when empty


# Each entry removes exactly one protection. `old` must occur once in `file`.
# The rule of `blocking` for what a criterion waits for, as `findings._about` has it.
WAITS = "    waits = kind in ASKS and criterion is not None and criterion.required and criterion.status != CriterionStatus.VERIFIED\n"

MUTATIONS = [
    Mutation("no due check under the lock", "recovery.py",
             "        if now < resume_at:\n            return False, False\n", ""),
    Mutation("retry for a replaced plan is accepted", "recovery.py",
             "    if retry_of is not None and not (scheduled and section.get(\"resume_at\") == retry_of):\n"
             "        return False, False\n", ""),
    Mutation("no retry limit", "recovery.py",
             "    if failures > cfg[\"max_consecutive_retries\"]:\n"
             "        return {\"action\": \"stop\", \"reason\": \"max_consecutive_retries\"}\n", ""),
    Mutation("no wait budget", "recovery.py",
             "    if resume_at > now and (resume_at - first_failure_at).total_seconds() > cfg[\"max_total_wait_seconds\"]:",
             "    if False:"),
    Mutation("expired reset accepted", "recovery.py",
             "    if retry_at is not None and retry_at > failure_at:", "    if retry_at is not None:"),
    Mutation("reset may shorten the backoff", "recovery.py",
             "        if reset >= resume_at:", "        if True:"),
    Mutation("no safety margin", "recovery.py",
             "            reset = retry_at + timedelta(seconds=cfg[\"reset_safety_margin_seconds\"])",
             "            reset = retry_at"),
    Mutation("non-quota failures retried", "recovery.py",
             "    if kind != \"quota\":\n", "    if False:\n"),
    Mutation("counter never resets on progress", "recovery.py",
             "        _audit(state, \"recovery_completed\", retries=section.get(\"consecutive_failures\"),\n"
             "               provider=section.get(\"provider\"))\n        state[\"recovery\"] = None\n",
             "        pass\n"),
    Mutation("counter never accumulates", "recovery.py",
             "failures=section[\"consecutive_failures\"] + 1 if same_point else 1", "failures=1"),
    Mutation("automatic retries reuse --unlock", "recovery.py",
             "force_unlock=force_unlock and retry_of is None", "force_unlock=True"),
    Mutation("manual run keeps the plan", "recovery.py",
             "        if section is not None and section.get(\"status\") in {\"scheduled\", \"retrying\"}:\n"
             "            _audit(state, \"recovery_cancelled\", reason=\"manual_run\")\n"
             "        state[\"recovery\"] = None\n        return True, False\n",
             "        return True, False\n"),
    Mutation("explicit automatic run ignores an earlier quota pause", "recovery.py",
             "    if not quota:\n        return True, False\n", "    return True, False\n"),
    Mutation("unreadable plan runs anyway", "recovery.py",
             "            _stop(state, section, \"invalid_schedule\")  # never guess a retry time\n"
             "            return False, True\n",
             "            section[\"status\"] = \"retrying\"\n            return True, True\n"),
    Mutation("lock held while waiting", "recovery.py",
             "            _record_wait(path, section[\"resume_at\"])\n"
             "            _wait(path, resume_at, (state[\"status\"], section), clock, sleep)\n",
             "            with TaskLock(path):\n"
             "                _wait(path, resume_at, (state[\"status\"], section), clock, sleep)\n"),
    Mutation("interrupt clears the plan", "recovery.py",
             "            return _reread(path, state)\n        if not active(task_config(home, task_id), auto_resume):",
             "            state[\"recovery\"] = None\n            save_json(path / \"state.json\", state)\n"
             "            return state\n        if not active(task_config(home, task_id), auto_resume):"),
    Mutation("recovery active by default", "recovery.py",
             "    return cfg[\"recovery\"][\"enabled\"] if flag is None else bool(flag)", "    return flag is not False"),
    Mutation("booleans accepted as integers", "recovery.py",
             "        if type(merged[field]) is not int or not low <= merged[field] <= high:",
             "        if not isinstance(merged[field], int) or not low <= merged[field] <= high:"),
    Mutation("quota_only may be false", "recovery.py",
             "    if not merged[\"quota_only\"]:", "    if False:"),
    Mutation("plan not saved with the pause", "core.py",
             "            recovery.on_failure(state, cfg, active=auto, now=failed_at)\n", ""),
    Mutation("plan not checked before the run starts", "core.py",
             "        proceed, changed = recovery.admit(state, cfg, active=auto, retry_of=retry_of, now=clock())\n",
             "        proceed, changed = True, False\n"),
    Mutation("stated reset saved truncated", "core.py",
             "error[\"retry_at\"] = recovery.stamp_up(retry_at)", "error[\"retry_at\"] = recovery.stamp(retry_at)"),
    Mutation("failure time saved truncated", "core.py",
             "\"at\": recovery.stamp_up(at)}", "\"at\": recovery.stamp(at)}"),
    Mutation("wait-only reset not saved as an instant", "core.py",
             "        if reset is not None:\n            error[\"retry_at\"] = recovery.stamp_up(reset)\n", ""),
    Mutation("wait-only reset ignored", "recovery.py",
             "            moments.append(failed_at + timedelta(seconds=seconds))", "            pass"),
    Mutation("plan reads only the absolute reset", "recovery.py",
             "retry_at=stated_reset(error), cfg=limits", "retry_at=parse_time(error.get(\"retry_at\")), cfg=limits"),
    Mutation("ambiguous time parsed", "providers.py",
             "    if moments:\n        hint[\"retry_at\"] = max(moments)",
             "    if \"9pm\" in text:\n        moments.append(observed_at + timedelta(hours=1))\n"
             "    if moments:\n        hint[\"retry_at\"] = max(moments)"),
    Mutation("UTC offset not required", "providers.py",
             "[re.compile(_CUE + date + _ZONE, re.I) for date in (",
             "[re.compile(_CUE + date + \"(?:\" + _ZONE + \")?\", re.I) for date in ("),
    Mutation("earliest stated reset wins", "providers.py", "max(moments)", "min(moments)"),
    Mutation("usage-limit wording wins over a login failure", "providers.py",
             "    return next((kind for kind, pattern in _ERROR_KINDS if pattern.search(lowered)), \"agent_error\")",
             "    return next((kind for kind, pattern in reversed(_ERROR_KINDS) if pattern.search(lowered)), "
             "\"agent_error\")"),
    Mutation("standard output ignored beside standard error", "providers.py",
             "outputs[0], provider, self.clock(), \"\\n\".join(outputs[1:]))",
             "outputs[0], provider, self.clock())"),
    Mutation("standard output overrides a specific standard error", "providers.py",
             "    kind = _classify_error(detail)\n    if kind == \"agent_error\" and secondary:\n"
             "        kind = _classify_error(secondary)\n",
             "    kind = _classify_error(f\"{detail}\\n{secondary}\")\n"),
    Mutation("reset on the other stream dropped", "providers.py",
             "quota_hint(f\"{detail}\\n{secondary}\", observed_at, provider)",
             "quota_hint(detail, observed_at, provider)"),
    Mutation("Codex local reset time ignored", "providers.py",
             "    if provider == \"codex\":\n", "    if False:\n"),
    Mutation("zoneless local time accepted from any source", "providers.py",
             "    if provider == \"codex\":\n", "    if True:\n"),
    Mutation("Codex printed minute not rounded up", "providers.py",
             "        return local.astimezone(timezone.utc) + timedelta(minutes=1)",
             "        return local.astimezone(timezone.utc)"),
    Mutation("standard error ignored beside an is_error result", "providers.py",
             "\"claude\", observed_at, stderr.strip())", "\"claude\", observed_at)"),
    Mutation("lock error is a plain RuntimeError", "storage.py",
             "raise LockBusy(\"Task is already running", "raise RuntimeError(\"Task is already running"),
    Mutation("native Windows liveness assumed dead", "storage.py",
             "        # Cannot reliably determine liveness without optional dependencies on Windows.\n        return True",
             "        return False"),
    # What the interface may claim or start. These are checked by the interface tests.
    Mutation("saved plan shown as waiting without a live process", "runinfo.py",
             "        if any(entry[\"auto_resume\"] is not False for entry in alive):\n            activity = \"waiting_retry\"",
             "        if True:\n            activity = \"waiting_retry\"", ("test_runinfo",)),
    Mutation("unverifiable process counted as waiting", "runinfo.py",
             "    alive = [entry for entry in attached_now if entry[\"state\"] == procinfo.ALIVE]",
             "    alive = list(attached_now)", ("test_runinfo",)),
    Mutation("saved running status shown as a running process", "runinfo.py",
             "        activity = \"interrupted\"  # a checkpoint says running, but nothing holds the lock",
             "        activity = \"running\"", ("test_runinfo",)),
    Mutation("recycled PID accepted as the recorded process", "procinfo.py",
             "    return ALIVE if current == token else DEAD", "    return ALIVE", ("test_runinfo",)),
    Mutation("live PID without identity called alive", "procinfo.py",
             "    if not token or current is None:\n        return UNKNOWN\n", "", ("test_runinfo",)),
    Mutation("second automatic run started while a process waits", "app.py",
             "            if runtime[\"waiting_process\"] and auto_resume is not False:", "            if False:",
             ("test_ui.RunTests",)),
    # A recorded process that cannot be verified is not shown as waiting, and is not taken as gone either.
    Mutation("second automatic run started beside an unverifiable waiting process", "app.py",
             "            if runtime[\"waiting_unverified\"] and auto_resume is not False:", "            if False:",
             ("test_ui.RunTests",)),
    Mutation("unverifiable waiting process not reported", "runinfo.py",
             "        \"waiting_unverified\": activity == \"plan_unverified\",", "        \"waiting_unverified\": False,",
             ("test_runinfo",)),
    Mutation("project removed while a recorded process cannot be verified", "app.py",
             "            if runtime[\"unverified\"]:", "            if False:", ("test_ui.ProjectTests",)),
    Mutation("interface start ignores the task lock", "app.py",
             "            if runtime[\"lock\"] is not None:\n                if not unlock:",
             "            if runtime[\"lock\"] is not None and unlock:\n                if not unlock:", ("test_ui.RunTests",)),
    Mutation("interface unlock without a verified dead holder", "app.py",
             "                if not runtime[\"can_unlock\"]:", "                if False:", ("test_ui.RunTests",)),
    Mutation("simultaneous starts of one task admitted", "app.py",
             "            if key in self.starting_tasks:\n                raise Conflict(\"This task is already being started\")\n", "",
             ("test_ui.RunTests",)),
    Mutation("test consent inherited from a previous save", "app.py",
             "        if not isinstance(values, dict) or set(values) != {\"enabled\", \"trust_acknowledged\", \"commands\"}:",
             "        values = {\"trust_acknowledged\": True, **values} if isinstance(values, dict) else values\n"
             "        if not isinstance(values, dict) or set(values) != {\"enabled\", \"trust_acknowledged\", \"commands\"}:",
             ("test_ui.SettingsTests",)),
    Mutation("configuration written before it is validated", "app.py",
             "            validate_config(copy.deepcopy(candidate), project.home)  # nothing invalid reaches the disk\n"
             "            save_json(file, candidate)",
             "            save_json(file, candidate)\n"
             "            validate_config(copy.deepcopy(candidate), project.home)", ("test_ui.SettingsTests",)),
    Mutation("task override outside the listed settings", "core.py",
             "    if not isinstance(overrides, dict) or set(overrides) - set(TASK_OVERRIDES):", "    if not isinstance(overrides, dict):",
             ("test_overrides",)),
    Mutation("task recovery override ignored", "core.py",
             "    cfg = effective_config(cfg, saved)  # overrides are written once, when the task is created\n", "",
             ("test_overrides",)),
    # A run must not end, or leave its lock behind, because the interface is reading the file (Windows).
    Mutation("state save fails while a reader has the file open", "storage.py",
             "        _unshared(lambda: os.replace(name, path))", "        os.replace(name, path)", ("test_storage",)),
    Mutation("lock left behind while a reader has it open", "storage.py",
             "            _unshared(lambda: self.path.unlink(missing_ok=True))",
             "            self.path.unlink(missing_ok=True)", ("test_storage",)),
    Mutation("refused save repeated without a bound", "storage.py",
             "            if time.monotonic() >= deadline:\n                raise\n", "", ("test_storage",)),
    # Two requests for one repository: the check made before the registry mutex is not enough.
    Mutation("repository check not repeated under the registry mutex", "workspace.py",
             "            self._refuse_registered(self._listed(data), repo)\n", "",
             ("test_workspace.OvertakenRegistrationTests",)),
    Mutation("import check not repeated under the registry mutex", "workspace.py",
             "            self._refuse_registered(self._listed(data), repo, home)\n", "",
             ("test_workspace.OvertakenRegistrationTests",)),
    # A listener that subscribes while the scanning thread ends for inactivity must not be left without one.
    Mutation("idle scanning thread withdrawn after the check, not with it", "live.py",
             "                if self.listeners == 0 and time.monotonic() - self.last_use > IDLE_SECONDS:\n"
             "                    self.thread = None\n                    return\n",
             "                idle = self.listeners == 0 and time.monotonic() - self.last_use > IDLE_SECONDS\n"
             "            if idle:\n                with self.cond:\n                    self.thread = None\n                return\n",
             ("test_ui.LiveIdleTests",)),
    # A write refused on its headers is still answered when its body arrives afterwards (Windows dropped the answer).
    Mutation("refused write answered with a reset when its body arrives late", "ui.py",
             "            deadline, left = time.monotonic() + LINGER_SECONDS, MAX_BODY\n",
             "            deadline, left = time.monotonic(), MAX_BODY\n", ("test_ui.ProtectionTests",)),
    # Live activity only observes: it decides nothing, hides internal reasoning and cannot fail a step.
    Mutation("activity reclassifies a reported CLI error", "providers.py",
             "                outputs = [\"\\n\".join(part for part in (reported, result.stderr.strip()) if part)]\n",
             "                outputs = [\"\\n\".join(part for part in (reported, result.stderr.strip()) if part), stdout.strip()]\n",
             ("test_live_activity",)),
    Mutation("reviewer progress recorded as a verdict", "streams.py",
             "        if self.role == \"reviewer\" and _VERDICT.search(text):", "        if False:", ("test_activity",)),
    Mutation("Codex reasoning kept in the transcript", "streams.py",
             "            return None  # the reasoning summary is not public output", "            return True",
             ("test_activity",)),
    Mutation("Claude thinking kept in the transcript", "streams.py",
             "        return {\"type\": \"assistant\", \"content\": kept, \"parent_tool_use_id\": obj.get(\"parent_tool_use_id\")}",
             "        return True", ("test_activity",)),
    Mutation("stream taken as the answer when the final result is missing", "providers.py",
             "            if text is None and stream is not None and stream.recognized:\n"
             "                raise AgentFailure(f\"Missing final result from {provider}/{role}\", \"empty_output\", provider=provider)\n",
             "", ("test_live_activity",)),
    Mutation("step started again after the CLI had begun to work", "providers.py",
             "            if stream is not None and not stream.recognized and streams.refused(",
             "            if stream is not None and streams.refused(", ("test_live_activity",)),
    Mutation("a journal that cannot be written fails the step", "activity.py",
             "        except Exception:  # noqa: BLE001\n            self._off()\n\n    def close",
             "        except Exception:  # noqa: BLE001\n            raise\n\n    def close", ("test_activity",)),
    Mutation("a failing observer disturbs the process it observes", "process.py",
             "                except Exception:  # noqa: BLE001 - observing must never disturb the process observed\n"
             "                    observer = None",
             "                except Exception:  # noqa: BLE001 - observing must never disturb the process observed\n"
             "                    raise", ("test_process",)),
    Mutation("cursor inside a line accepted", "activity.py",
             "    return offset if stream.read(1) == b\"\\n\" else None  # a cursor always sits at the start of a line",
             "    return offset", ("test_activity",)),
    Mutation("a journal met while its first line is written breaks its reader", "activity.py",
             "        size = max(os.fstat(stream.fileno()).st_size, first)\n",
             "        size = os.fstat(stream.fileno()).st_size\n", ("test_activity",)),
    # --- environment check, reviewer preflight and repair ---------------------------------------
    Mutation("turn limit reported as a plain CLI failure", "providers.py",
             "            if turn_limit is not None:\n                raise turn_limit\n", "", ("test_live_activity",)),
    Mutation("environment not checked before the first provider call", "core.py",
             "            if manager is not None:\n                _environment(manager, home, path, state, cfg, live=live)\n",
             "", ("test_environment",)),
    Mutation("environment not checked before a review", "core.py",
             "                        _environment(manager, home, path, state, cfg, live=live, reviewer_only=True)\n",
             "                        pass\n", ("test_environment",)),
    Mutation("a blocked environment does not stop the run", "core.py",
             "            raise EnvironmentBlocked(record[\"message\"], record[\"error_kind\"], provider=record[\"provider\"])\n",
             "", ("test_environment",)),
    Mutation("real CLI checks run while providers are simulated", "core.py",
             "    live = adapter is None and not providers.simulated()\n", "    live = adapter is None\n",
             ("test_environment",)),
    Mutation("real CLI checks run beside a given adapter", "core.py",
             "    live = adapter is None and not providers.simulated()\n", "    live = not providers.simulated()\n",
             ("test_environment",)),
    Mutation("the reviewer is called without the test consent", "core.py",
             "        raise AgentFailure(\"Configure tests and set trust_acknowledged=true, then run resume\", \"tests_disabled\")\n",
             "", ("test_workflow",)),
    Mutation("a resume without the test consent starts a run again", "core.py",
             "            if state[\"phase\"] == \"test\":\n                return state\n", "", ("test_workflow",)),
    Mutation("an access problem is hidden behind the missing test consent", "core.py",
             "        if reviewer_check is not None:\n            reviewer_check()\n", "", ("test_environment",)),
    Mutation("a repair is tried again in the same check", "environment.py",
             "            if wanted != \"acl\" or record[\"attempts\"] >= repair.MAX_REPAIRS or \"acl\" in applied:",
             "            if wanted != \"acl\" or record[\"attempts\"] >= repair.MAX_REPAIRS:", ("test_environment",)),
    Mutation("a repair that is the user's decision runs unattended", "environment.py",
             "            if problem is None or \"git_trust\" not in confirmed or \"git_trust\" in applied:",
             "            if problem is None or \"git_trust\" in applied:", ("test_environment",)),
    Mutation("a ready result reused after its environment changed", "environment.py",
             "                and previous.get(\"signature\") == self.signature(home, worktree, reviewer, live):",
             "                and previous.get(\"signature\"):", ("test_environment",)),
    Mutation("a check beside a running task", "app.py",
             "                with TaskLock(path):\n", "                if True:\n", ("test_environment",)),
    Mutation("owner rights taken as access for the reviewer", "winacl.py",
             "    readers = {sid, *BROAD_READERS}\n", "    readers = {sid, *BROAD_READERS, \"S-1-3-4\"}\n", ("test_winacl",)),
    Mutation("a deny entry ignored", "winacl.py",
             "            if held & refused:\n                return \"denied\"\n            continue\n", "            continue\n",
             ("test_winacl",)),
    Mutation("a link is taken for a managed folder", "winacl.py",
             "            if is_reparse_point(folder):\n"
             "                return f\"{folder.name} is a link or a junction, not a real folder\"\n", "", ("test_winacl",)),
    Mutation("a failed repair leaves the lists changed", "winacl.py",
             "        result[\"restored\"] = rollback(result[\"changed\"])\n", "        result[\"restored\"] = False\n",
             ("test_winacl",)),
    Mutation("more than reading can be granted", "winacl.py",
             "    if rights not in (READ_EXECUTE, READ_ATTRIBUTES):\n"
             "        raise ValueError(\"Only reading, or reading attributes, can be granted\")\n", "", ("test_winacl",)),
    Mutation("a write that happened in the sandbox is accepted", "preflight.py",
             "    written = facts.get(\"created\") or facts.get(\"canary_intact\") is False\n", "    written = False\n",
             ("test_preflight",)),
    Mutation("ready without proof that writing is refused", "preflight.py",
             "    elif \"passed\" not in (state[\"write.canary\"], state[\"write.create\"]):\n"
             "        kind = \"sandbox_unverified\"  # nothing proved that writing is refused\n", "", ("test_preflight",)),
    Mutation("a path can leave its literal in the probe", "preflight.py",
             "    for mark in _QUOTES:\n        text = text.replace(mark, mark * 2)\n", "", ("test_preflight",)),
    Mutation("the probe asks for a sandbox that can write", "preflight.py",
             "PROFILE = \":read-only\"", "PROFILE = \":workspace-write\"", ("test_preflight",)),
    # --- agent settings -------------------------------------------------------------------------
    Mutation("the reviewer's sandbox follows its settings", "agents.py",
             "    sandbox = \"read-only\" if reviewer else get(\"sandbox\") or \"workspace-write\"\n",
             "    sandbox = get(\"sandbox\") or (\"read-only\" if reviewer else \"workspace-write\")\n", ("test_agents",)),
    Mutation("the reviewer's tools follow its settings", "agents.py",
             "        tools = list(DEFAULT_TOOLS[role] if reviewer or not get(\"tools\") else get(\"tools\"))\n",
             "        tools = list(get(\"tools\") or DEFAULT_TOOLS[role])\n", ("test_agents",)),
    Mutation("the reviewer gets custom arguments", "agents.py",
             "    custom = [] if reviewer else list(get(\"extra_args\") or [])\n",
             "    custom = list(get(\"extra_args\") or [])\n", ("test_agents",)),
    Mutation("a risky option is saved without the acknowledgement", "agents.py",
             "        if raw.get(ACK) is not True:\n", "        if False:\n", ("test_agents",)),
    Mutation("the reviewer can be widened with an acknowledgement", "agents.py",
             "        if role == \"reviewer\":\n            # No acknowledgement opens this",
             "        if False:\n            # No acknowledgement opens this", ("test_agents",)),
    Mutation("a setting of the other role is accepted", "agents.py",
             "        if role not in option.get(\"roles\", ROLES):\n", "        if False:\n", ("test_agents",)),
    Mutation("a custom argument can remove a protection", "agents.py",
             "            if name in ARGS_BLOCKED[provider]:\n", "            if False:\n", ("test_agents",)),
    Mutation("a custom argument can repeat a managed option", "agents.py",
             "            if name in ARGS_MANAGED[provider]:\n", "            if False:\n", ("test_agents",)),
    Mutation("a short option hides another one", "agents.py",
             "        elif arg.startswith(\"-\") and not re.fullmatch(r\"-\\d+(?:\\.\\d+)?\", arg):\n",
             "        elif False:\n", ("test_agents",)),
    Mutation("a configuration override can weaken the sandbox", "agents.py",
             "        if head in CONFIG_BLOCKED or key in CONFIG_BLOCKED:\n", "        if False:\n", ("test_agents",)),
    Mutation("a model name can be read as an option", "agents.py",
             "        if not isinstance(value, str) or not MODEL.fullmatch(value):\n",
             "        if not isinstance(value, str):\n", ("test_agents",)),
    Mutation("a credential is stored with the settings", "agents.py",
             "    if not multiline and redact(value) != value:\n", "    if False:\n", ("test_agents",)),
    Mutation("private state can be opened to an agent", "agents.py",
             "            if folder == other or folder.is_relative_to(other) or other.is_relative_to(folder):\n"
             "                raise ValueError(f\"Additional folders: {text} holds PatchRondo's private state",
             "            if False:\n"
             "                raise ValueError(f\"Additional folders: {text} holds PatchRondo's private state", ("test_agents",)),
    Mutation("a resumed step takes the settings of now", "core.py",
             "        values = agents.thaw(runs.get(key), provider, role)\n", "        values = None\n", ("test_agents",)),
    Mutation("a frozen record is used without being validated again", "agents.py",
             "    return clean_block(provider, role, record[\"values\"])\n", "    return record[\"values\"]\n", ("test_agents",)),
    Mutation("settings that cannot be used do not stop the run", "core.py",
             "        raise AgentFailure(f\"The agent settings of the {role} cannot be used: {exc}\", \"configuration\", "
             "provider=provider) from exc\n", "        values = {}\n", ("test_agents",)),
    Mutation("a test call is made without consent", "app.py",
             "        if payload.get(\"confirm\") is not True:\n", "        if False:\n", ("test_agents",)),
    Mutation("the settings of a running task change under it", "app.py",
             "                held = TaskLock(path)  # and none is running: a step never sees its task's settings change under it\n"
             "                try:\n                    with held:\n",
             "                try:\n                    if True:\n", ("test_agents",)),
    Mutation("any command passes the probe", "providers.py",
             "    if tuple(argv) not in PROBES and (argv[:1] != [\"codex\"] or argv[1:2] != [\"sandbox\"]):\n",
             "    if False:\n", ("test_agents",)),  # the test that judges the question with the runner replaced
    Mutation("a CLI is asked what it offers while providers are simulated", "doctor.py",
             "            or not Path(status[\"path\"]).is_file() or providers.simulated():\n",
             "            or not Path(status[\"path\"]).is_file():\n", ("test_agents",)),
    Mutation("a refused option is reported as a plain CLI failure", "providers.py",
             "            if refused and _classify_error(result.stderr) == \"agent_error\":\n",
             "            if False:\n", ("test_agents",)),
    Mutation("a login problem beside a refused option is hidden", "providers.py",
             "            if refused and _classify_error(result.stderr) == \"agent_error\":\n",
             "            if refused:\n", ("test_agents",)),
    # --- acceptance criteria, capabilities and evidence -------------------------------------------
    Mutation("approved code completes a task whose criteria are not verified", "core.py",
             "                        if decided.completion_allowed:\n"
             "                            return _complete(path, state, cfg, workspace, decided,\n",
             "                        if True:\n"
             "                            return _complete(path, state, cfg, workspace, decided,\n",
             ("test_acceptance",)),
    Mutation("a task that waits for a person is run again while nothing changed", "core.py",
             "            # Idempotent Resume: while nothing was attached, decided or changed, a run has nothing to do.\n"
             "            return state\n",
             "            # Idempotent Resume: while nothing was attached, decided or changed, a run has nothing to do.\n"
             "            pass\n", ("test_acceptance",)),
    Mutation("the reviewer's answer is used without being settled against the plan", "core.py",
             "                            review = record.settle_review(parse_review(reply.text), iteration)\n",
             "                            review = parse_review(reply.text)\n", ("test_acceptance",)),
    Mutation("an outcome that is not one is accepted from the reviewer", "core.py",
             "        raise AgentFailure(\"Invalid review: malformed criterion outcome\", \"invalid_review\")\n",
             "        pass\n", ("test_acceptance",)),
    Mutation("a statement alone is accepted as evidence", "core.py",
             "    if passed and not reference and not artifact:\n", "    if False:\n", ("test_acceptance",)),
    Mutation("evidence is recorded beside a running task", "core.py",
             "    path = task_dir(home, task_id)\n    with TaskLock(path):\n        state = read_json(path / \"state.json\")\n"
             "        if state.get(\"status\") in {\"done\", \"blocked\"}:\n            raise AcceptanceRefused",
             "    path = task_dir(home, task_id)\n    if True:\n        state = read_json(path / \"state.json\")\n"
             "        if state.get(\"status\") in {\"done\", \"blocked\"}:\n            raise AcceptanceRefused", ("test_acceptance",)),
    Mutation("the saved results of an older task are taken as verified", "core.py",
             "        if state.get(\"phase\") == \"review\":\n            state[\"phase\"] = \"test\"\n    return record\n",
             "    return record\n", ("test_acceptance",)),
    Mutation("a ledger that failed its check keeps being used", "core.py",
             "    if problems:\n        # Its records prove nothing any more", "    if False:\n        # Its records prove nothing any more",
             ("test_acceptance",)),
    Mutation("a request for changes that names none goes back to the developer", "acceptance.py",
             "        elif review[\"verdict\"] == \"CHANGES_REQUESTED\" and not code_issues and not changes and (deferred or refused) \\\n",
             "        elif False and (deferred or refused) \\\n", ("test_acceptance",)),
    Mutation("a change to the code counts for a criterion no change to the files can satisfy", "acceptance.py",
             "        changes = [name for name in named if name in mine]\n", "        changes = named\n", ("test_acceptance",)),
    Mutation("a change asked only for a criterion no change to the files can satisfy goes back to the developer", "acceptance.py",
             " and not changes and (deferred or refused) \\\n", " and not changes and deferred \\\n", ("test_acceptance",)),
    Mutation("a change asked for a criterion that is not the developer's is sent to it as its work", "acceptance.py",
             "                          or (entry[\"outcome\"] == ReviewOutcome.CODE_CHANGES_REQUIRED and entry[\"id\"] in changeable)]}\n",
             "                          or entry[\"outcome\"] == ReviewOutcome.CODE_CHANGES_REQUIRED]}\n", ("test_acceptance",)),
    Mutation("an approval that names a change to the code stays an approval", "acceptance.py",
             "        if review[\"verdict\"] == \"APPROVED\" and changes:\n", "        if False:\n", ("test_acceptance",)),
    Mutation("the severity of an issue decides over its kind", "acceptance.py",
             "        return issue[\"kind\"] == ReviewOutcome.CODE_CHANGES_REQUIRED\n",
             "        return issue[\"kind\"] == ReviewOutcome.CODE_CHANGES_REQUIRED and issue[\"severity\"] in {\"high\", \"critical\"}\n",
             ("test_acceptance",)),
    Mutation("an observation turns an approval into a request for changes", "acceptance.py",
             "        return issue[\"kind\"] == ReviewOutcome.CODE_CHANGES_REQUIRED\n",
             "        return issue[\"kind\"] in {ReviewOutcome.CODE_CHANGES_REQUIRED, OBSERVATION}\n", ("test_acceptance",)),
    Mutation("an observation is handed to the developer as somebody else's work", "acceptance.py",
             "              if not code(issue) and issue[\"kind\"] != OBSERVATION]", "              if not code(issue)]",
             ("test_acceptance",)),
    Mutation("an approval that leaves a criterion of the reviewer without its outcome is accepted", "acceptance.py",
             "        if unjudged:\n            raise IncompleteReview(", "        if False:\n            raise IncompleteReview(",
             ("test_acceptance",)),
    Mutation("an approval covers the criteria it did not list in every task", "acceptance.py",
             "            covered = self.implicit and review.get(\"verdict\") == \"APPROVED\"\n",
             "            covered = review.get(\"verdict\") == \"APPROVED\"\n", ("test_acceptance",)),
    Mutation("an approval saved without an outcome for each criterion keeps approving the code", "acceptance.py",
             "        elif approval is not None and (unjudged := self.unjudged(approval.get(\"outcomes\") or {})):\n",
             "        elif False:\n", ("test_acceptance",)),
    Mutation("no task needs an outcome for each criterion", "acceptance.py",
             "        return self.source != \"task.md\"\n", "        return False\n", ("test_acceptance",)),
    Mutation("a task read back from its task.md needs an outcome for each criterion", "acceptance.py",
             "        return self.source != \"task.md\"\n", "        return True\n", ("test_acceptance",)),
    Mutation("every criterion is verified by the review of the code", "acceptance.py",
             "        proof = list(dict.fromkeys(PROOF[need] for need in needs))\n", "        proof = [\"review\"]\n",
             ("test_acceptance",)),
    Mutation("a review counts for a criterion it did not find satisfied", "acceptance.py",
             "            return [review[\"id\"]] if judged == ReviewOutcome.SATISFIED else []\n",
             "            return [review[\"id\"]]\n", ("test_acceptance",)),
    Mutation("a criterion is verified without its evidence", "acceptance.py",
             "        if not held and not waiting and (granted or not criterion.authorization_required):\n",
             "        if not waiting and (granted or not criterion.authorization_required):\n", ("test_acceptance",)),
    Mutation("an operation that needs consent counts without the user's decision", "acceptance.py",
             "        if not held and not waiting and (granted or not criterion.authorization_required):\n",
             "        if not held and not waiting:\n", ("test_acceptance",)),
    Mutation("evidence is attached before the operation is authorized", "acceptance.py",
             "        if criterion.authorization_required and (criterion.authorization or {}).get(\"decision\") != \"granted\":\n"
             "            return f\"{criterion.id} is about an operation that needs your authorization first\"\n", "",
             ("test_acceptance",)),
    Mutation("a statement takes the place of the review or of the tests", "acceptance.py",
             "        if \"manual\" not in criterion.proof and not (set(criterion.proof) & {\"build\", \"package\"}):\n",
             "        if False:\n", ("test_acceptance",)),
    Mutation("the approval of the code outlives the review it came from", "acceptance.py",
             "        if self.code.get(\"status\") == Code.APPROVED and not ledger.counts(ledger.get(self.code.get(\"evidence\"))):\n",
             "        if False:\n", ("test_acceptance",)),
    Mutation("what cannot be classified is taken for a criterion about the code", "acceptance.py",
             "    if unclear:\n", "    if False:\n", ("test_acceptance",)),
    Mutation("a saved criterion with an unknown status is loaded", "acceptance.py",
             "            raise ValueError(f\"Criterion {raw['id']}: unknown category, need, proof or status\")\n",
             "            pass\n", ("test_acceptance",)),
    Mutation("a saved criterion is loaded as verified without the records that verify it", "acceptance.py",
             "        if criterion.status == Status.VERIFIED and not criterion.evidence:\n", "        if False:\n", ("test_acceptance",)),
    Mutation("a saved criterion names evidence that is not a record of the ledger", "acceptance.py",
             "        if not all(evidence.RECORD_ID.fullmatch(name) for name in criterion.evidence):\n", "        if False:\n",
             ("test_acceptance",)),
    Mutation("a null criteria record is read as a task from before criteria were tracked", "acceptance.py",
             "            raise ValueError(\"The acceptance record of the task is null\")\n", "            return None\n",
             ("test_acceptance",)),
    Mutation("a saved review outcome is planned without being checked", "acceptance.py",
             "            (\"review\", review is None or (_within([review.get(\"outcome\")], ReviewOutcome) and _typed(review, note=str, iteration=int))),\n",
             "            (\"review\", True),\n", ("test_acceptance",)),
    Mutation("a saved toolchain this version does not know is planned", "acceptance.py",
             "            (\"toolchains\", _within(criterion.toolchains, caps.TOOLCHAIN_LABELS))) if not readable]\n",
             "            (\"toolchains\", True)) if not readable]\n", ("test_acceptance",)),
    Mutation("the nested fields of a saved criterion are read whatever their shape", "acceptance.py",
             "        if unread:\n            raise ValueError(f\"Criterion {raw['id']}: its {', '.join(unread)} cannot be read\")\n", "",
             ("test_acceptance",)),
    Mutation("saved capabilities of any shape are read", "acceptance.py",
             "            (\"capabilities\", _detected(raw.get(\"capabilities\"))),\n", "            (\"capabilities\", True),\n",
             ("test_acceptance",)),
    Mutation("the fields of a saved criteria record are read whatever their shape", "acceptance.py",
             "        if unread:\n            raise ValueError(f\"The acceptance record of the task: its {', '.join(unread)} cannot be read\")\n",
             "", ("test_acceptance",)),
    Mutation("the report says nothing about a null criteria record", "report.py",
             "    if \"acceptance\" not in state:\n        return []\n    block = state[\"acceptance\"]\n",
             "    block = state.get(\"acceptance\")\n    if not isinstance(block, dict):\n        return []\n", ("test_acceptance",)),
    Mutation("a saved record of another schema version is loaded", "acceptance.py",
             "        if isinstance(raw, dict) and (type(raw.get(\"version\")) is not int or raw[\"version\"] != VERSION):\n",
             "        if False:\n", ("test_acceptance",)),
    Mutation("a saved record that cannot be read is replaced by one read from task.md", "core.py",
             "        raise AcceptanceUnreadable(UNREADABLE.format(exc), \"acceptance_unreadable\") from exc\n", "        record = None\n",
             ("test_acceptance",)),
    Mutation("a task whose saved criteria cannot be read is run again while nothing changed", "core.py",
             "            # Idempotent Resume: while the saved criteria still cannot be read, a run may do nothing.\n"
             "            return state\n",
             "            # Idempotent Resume: while the saved criteria still cannot be read, a run may do nothing.\n"
             "            pass\n", ("test_acceptance",)),
    Mutation("what shows the criteria shows the statuses as they were saved", "acceptance.py",
             "    current.refresh(ledger, developing=developing)\n", "    pass\n", ("test_acceptance",)),
    Mutation("the list of tasks shows the counts as they were saved", "app.py",
             "            result = current.compact()\n", "            result = record.compact()\n", ("test_criteria_ui",)),
    Mutation("a ledger that lost what the task state saw still counts where criteria are shown", "evidence.py",
             "        if self.anchored(seen.get(\"count\"), seen.get(\"head\")):\n", "        if True:\n", ("test_acceptance",)),
    Mutation("the list of tasks counts a ledger that lost what the task state saw", "acceptance.py",
             "    ledger = ledger.for_state(acceptance.ledger)  # a removed or changed record may have been an invalidation\n",
             "", ("test_criteria_ui",)),
    Mutation("the records of a ledger that lost what the task state saw are shown as counting", "acceptance.py",
             "    ledger = ledger.for_state(acceptance.ledger)  # the records and problems shown are those `judged_now` judges from\n",
             "", ("test_acceptance",)),
    Mutation("the command line does not say that the ledger lost what the task state saw", "cli.py",
             "    ledger = evidence.read(path).for_state(record.ledger)  # with the problem of a ledger that lost what was saved\n",
             "    ledger = evidence.read(path)\n", ("test_acceptance",)),
    Mutation("the report shows the statuses of criteria this version cannot read", "report.py",
             "        Acceptance.load({\"acceptance\": block})\n", "        pass\n", ("test_acceptance",)),
    Mutation("evidence about other files still counts", "evidence.py",
             "        if fingerprint is not None and record.get(\"scope\", \"code\") == \"code\" and record.get(\"fingerprint\") != fingerprint:\n",
             "        if False:\n", ("test_acceptance",)),
    Mutation("evidence about a file that changed still counts", "evidence.py",
             "        elif any(artifact_changed(artifact) for artifact in listed(record, \"artifacts\") if isinstance(artifact, dict)):\n",
             "        elif False:\n", ("test_acceptance",)),
    Mutation("an edited ledger is trusted", "evidence.py",
             "        if trusted and (record.get(\"prev\") != previous or record[\"hash\"] != digest or record.get(\"seq\") != number):\n",
             "        if False:\n", ("test_acceptance",)),
    Mutation("an invalidated record still counts", "evidence.py",
             "        return self.sound(record) and record[\"invalidated\"] is None\n",
             "        return self.sound(record)\n", ("test_acceptance",)),
    Mutation("commands count as a capability of the runtime without the consent", "capabilities.py",
             "    allowed = bool(tests.get(\"enabled\") is True and tests.get(\"trust_acknowledged\") is True and commands)\n",
             "    allowed = bool(commands)\n", ("test_acceptance",)),
    Mutation("an authorization is granted without the explicit confirmation", "app.py",
             "        if decision == \"granted\" and payload.get(\"confirm\") is not True:\n", "        if False:\n",
             ("test_criteria_ui",)),
    Mutation("an action on criteria starts a run that would call an agent", "app.py",
             "        if resume and result[\"completes\"]:\n", "        if resume:\n", ("test_criteria_ui",)),
    # --- findings of the reviews ------------------------------------------------------------------
    Mutation("a finding ID links an issue of another kind, file or criterion", "findings.py",
             "        if differs:\n            return None, f\"that finding has another {', '.join(differs)}\"\n", "",
             ("test_findings",)),
    Mutation("a finding ID that a ledger named is given to a new problem", "core.py",
             "        registry.reserve(findings.named(path))  # the ledgers put aside included: they are only read\n",
             "        pass\n", ("test_findings",)),
    Mutation("a ledger put aside is not searched for finding IDs", "findings.py",
             "    for file in (task_path / evidence.LEDGER, *sorted(task_path.glob(evidence.ARCHIVED))):\n",
             "    for file in (task_path / evidence.LEDGER,):\n", ("test_findings",)),
    Mutation("a ledger that cannot be read is passed over when IDs are kept", "findings.py",
             "        except FileNotFoundError:\n            continue\n", "        except OSError:\n            continue\n",
             ("test_findings",)),
    Mutation("an ID quoted in free text of a ledger takes a number", "findings.py",
             "for match in _NAMED.finditer(field.group(1)))\n", "for match in _NAMED.finditer(field.string))\n",
             ("test_findings",)),
    Mutation("a review record cut short by a crash is passed over when IDs are kept", "findings.py",
             r"""_FIELD = re.compile(r'[{,]"findings":\[([^\]\n]*)')""", r"""_FIELD = re.compile(r'[{,]"findings":\[([^\]\n]*)\]')""",
             ("test_findings",)),
    Mutation("a criterion the task does not have is taken for none", "findings.py",
             "    return (value, None) if value in known else (None, clip(redact(value), 40))\n",
             "    return (value, None) if value in known else (None, None)\n", ("test_findings",)),
    Mutation("an issue naming a criterion the task does not have is linked by its ID", "findings.py",
             "        if unknown is not None:\n            return None, \"the issue names a criterion this task does not have\"\n", "",
             ("test_findings",)),
    Mutation("an issue naming a criterion the task does not have is linked by its words", "findings.py",
             "        if unknown is not None:\n            return None\n", "", ("test_findings",)),
    Mutation("a finding that keeps a criterion the task does not have is linked by its ID", "findings.py",
             "        if found.unknown_criterion is not None:\n            return None, (", "        if False:\n            return None, (",
             ("test_findings",)),
    Mutation("a finding that keeps a criterion the task does not have is linked by its words", "findings.py",
             "if item.id not in used and item.unknown_criterion is None\n", "if item.id not in used\n", ("test_findings",)),
    Mutation("a finding saved without a criterion before unknown ones were kept is taken to name none", "findings.py",
             "            unknown = None if criterion is not None else \"\"\n", "            unknown = None\n", ("test_findings",)),
    Mutation("an issue is recorded under a finding when either names a criterion the task does not have", "findings.py",
             "            elif unknown is not None or found.unknown_criterion is not None:\n", "            elif False:\n",
             ("test_findings",)),
    Mutation("files are compared on the shortened copy that is shown", "findings.py",
             "else None, _key(_file(issue.get(\"path\"))), criterion",
             "else None, _key(clip(_file(issue.get(\"path\")), PATH_LIMIT)), criterion", ("test_findings",)),
    Mutation("descriptions are compared on the shortened copy that is shown", "findings.py",
             "    words = _words(text)\n", "    words = _words(clip(text, TEXT_LIMIT))\n", ("test_findings",)),
    Mutation("a file saved shortened is taken as the whole file", "findings.py",
             "_key(raw[\"path\"]) if len(raw[\"path\"]) <= PATH_LIMIT else None\n", "_key(raw[\"path\"])\n", ("test_findings",)),
    Mutation("a description saved shortened is taken as the whole description", "findings.py",
             "_words_key(raw[\"description\"]) if len(raw[\"description\"]) <= TEXT_LIMIT else None\n",
             "_words_key(raw[\"description\"])\n", ("test_findings",)),
    Mutation("blocking says only what keeps the code from being approved", "findings.py",
             "    return owner, actionable, requires_change(issue) or waits\n", "    return owner, actionable, requires_change(issue)\n",
             ("test_findings",)),
    Mutation("a check for an optional criterion is blocking", "findings.py", WAITS,
             "    waits = kind in ASKS and criterion is not None and criterion.status != CriterionStatus.VERIFIED\n", ("test_findings",)),
    Mutation("a check for a criterion already verified is blocking", "findings.py", WAITS,
             "    waits = kind in ASKS and criterion is not None and criterion.required\n", ("test_findings",)),
    Mutation("an observation about a required criterion is blocking", "findings.py", WAITS,
             "    waits = kind in (*ASKS, OBSERVATION) and criterion is not None and criterion.required"
             " and criterion.status != CriterionStatus.VERIFIED\n", ("test_findings",)),
    Mutation("a check for no criterion is blocking", "findings.py", WAITS,
             "    waits = kind in ASKS and (criterion is None or criterion.required and criterion.status != CriterionStatus.VERIFIED)\n",
             ("test_findings",)),
    Mutation("two issues of one review are linked to the same finding", "findings.py",
             "        if claim in used:\n            return None, \"another issue of the same review was linked to it\"\n", "",
             ("test_findings",)),
    Mutation("words shared by several findings link an issue to one of them", "findings.py",
             "        return same[0] if len(same) == 1 else None\n", "        return same[0] if same else None\n", ("test_findings",)),
    Mutation("the same review is recorded twice", "findings.py",
             "        if record in self.reviews:\n            return []\n", "", ("test_findings",)),
    Mutation("a finding takes the identity of another problem", "findings.py",
             "            elif found.identity != identity or issue.get(\"finding_link\") == \"new\":\n                continue\n",
             "            elif False:\n                continue\n", ("test_findings",)),
    Mutation("an approval of the code resolves the findings it left out", "findings.py",
             "item.status, item.verification = str(Status.PENDING_VERIFICATION), {\"review\": record, \"iteration\": iteration}",
             "item.status, item.verification = str(Status.RESOLVED), {\"review\": record, \"iteration\": iteration}",
             ("test_findings",)),
    Mutation("a finding that a review leaves out is taken as resolved", "findings.py",
             "        self.reviews = [*self.reviews, record][-MAX_REVIEWS:]\n        return recorded\n",
             "        for item in self.items:\n            if item.id not in recorded:\n                item.status = str(Status.RESOLVED)\n"
             "        self.reviews = [*self.reviews, record][-MAX_REVIEWS:]\n        return recorded\n", ("test_findings",)),
    Mutation("the reviewer's answer sets what the backend adds to an issue", "findings.py",
             "            issue = {key: value for key, value in raw.items() if key not in ANNOTATIONS}\n",
             "            issue = dict(raw)\n", ("test_findings",)),
    Mutation("a status beyond OPEN is read back without the record it rests on", "findings.py",
             "        if raw[\"status\"] != Status.OPEN and verified is None:\n", "        if False:\n", ("test_findings",)),
    Mutation("a saved record that would give an ID in use again is read", "findings.py",
             " or any(_number(name) >= upcoming for name in names)", "", ("test_findings",)),
    Mutation("findings that cannot be read are replaced", "core.py",
             "        _event(state, \"findings_not_recorded\", reason=evidence.text(exc, 300))\n        return None\n",
             "        return findings.Findings()\n", ("test_findings",)),
    Mutation("an ID the reviewer gave is kept as a link when the findings cannot be read", "core.py",
             " if registry is not None else findings.unlinked(review)\n", " if registry is not None else review\n",
             ("test_findings",)),
    Mutation("the review record does not name the findings of its issues", "core.py",
             "findings=named or None,", "findings=None,", ("test_findings",)),
    # --- workflow history ---------------------------------------------------------------------------
    Mutation("a detail sets a field of an event", "history.py",
             "    if taken:\n        raise ValueError(f\"Details cannot set the fields of an event: {', '.join(taken)}\")\n", "",
             ("test_history",)),
    Mutation("a new event takes the number of an event in the history", "history.py",
             "    number = max([len(events), *(_number(entry) for entry in events)]) + 1\n",
             "    number = len(events) + 1\n", ("test_history",)),
    Mutation("the first new event of an older history is numbered as if the history were empty", "history.py",
             "    number = max([len(events), *(_number(entry) for entry in events)]) + 1\n",
             "    number = max([0, *(_number(entry) for entry in events)]) + 1\n", ("test_history",)),
    Mutation("the text of an event is not redacted", "history.py",
             "        return clip(redact(value), TEXT_LIMIT)\n", "        return clip(value, TEXT_LIMIT)\n", ("test_history",)),
    Mutation("the lists of an event are not bounded", "history.py",
             "for index, item in enumerate(value[:LIST_LIMIT])]\n", "for index, item in enumerate(value)]\n", ("test_history",)),
    Mutation("a list of an event is cut without saying so", "history.py",
             "        if len(value) > LIST_LIMIT:\n            _cut(cut, path, len(value))\n        return [",
             "        return [", ("test_history",)),
    Mutation("an object of an event is cut without saying so", "history.py",
             "        if len(value) > LIST_LIMIT:\n            _cut(cut, path, len(value))\n        kept = {}\n",
             "        kept = {}\n", ("test_history",)),
    Mutation("what was cut from an event is not saved with it", "history.py",
             "    if cut:\n        entry[\"truncated\"] = cut", "    if False:\n        entry[\"truncated\"] = cut", ("test_history",)),
    Mutation("the events of quota recovery are written without the fields of the schema", "recovery.py",
             "    history.append(state, name, **details)  # numbered with the events of the engine, in the same history\n",
             "    state[\"history\"].append({\"at\": now(), \"event\": name, \"iteration\": state[\"iteration\"], **details})\n",
             ("test_history",)),
    Mutation("the reviewer's words go into the history", "core.py",
             "            **({\"normalized\": review[\"normalized\"]} if review.get(\"normalized\") else {})}\n",
             "            **({\"normalized\": review[\"normalized\"]} if review.get(\"normalized\") else {}), \"summary\": review[\"summary\"]}\n",
             ("test_history",)),
    # --- the four records read together -----------------------------------------------------------
    Mutation("the report fails on an entry of the history that is not an event", "report.py",
             "    if not isinstance(evt, dict):\n        return \"- An entry of the history that is not an event; it is kept in state.json as it is\"\n",
             "", ("test_coherence.RecordTests",)),
    Mutation("the report presumes the fields an event lacks", "report.py",
             "    known = lambda value: \"unknown\" if value is None else value  # noqa: E731\n",
             "    known = lambda value: value  # noqa: E731\n", ("test_coherence.RecordTests",)),
    Mutation("the report takes a list an event saved in part for the whole of it", "report.py",
             "if isinstance(value, list) and whole is not None else \"\"))\n", "if False else \"\"))\n", ("test_coherence.RecordTests",)),
    # --- verification runner ---------------------------------------------------------------
    Mutation("a check that is not configured runs", "verification.py",
             "        if check not in self.allowed:\n",
             "        if False:\n", ("test_verification",)),
    Mutation("a check runs without the consent", "verification.py",
             "        if not self.consent:\n",
             "        if False:\n", ("test_verification",)),
    Mutation("a shell runs although the check does not allow one", "verification.py",
             "        if not check.legacy and program(check.command) in SHELLS and not check.shell:\n",
             "        if False:\n", ("test_verification",)),
    Mutation("a shell is configured without being allowed", "verification.py",
             "    if program(command) in SHELLS and not shell:\n",
             "    if False:\n", ("test_verification",)),
    Mutation("the exit code decides a check that reads a report", "verification.py",
             "        if check.report:\n            self._report(result, target, earlier)\n",
             "        if False:\n            self._report(result, target, earlier)\n", ("test_verification",)),
    Mutation("failing tests in a report are ignored", "verification.py",
             "    if failing:\n",
             "    if False:\n", ("test_verification",)),
    Mutation("too few tests pass a check", "verification.py",
             "    if counts[\"tests\"] < check.min_tests:\n",
             "    if False:\n", ("test_verification",)),
    Mutation("required tests are not looked for", "verification.py",
             "    missing, unmet = requirements(check.required_tests, report)\n",
             "    missing, unmet = [], []\n", ("test_verification",)),
    Mutation("a report that hides failures is believed", "verification.py",
             "    if report.hidden:\n",
             "    if False:\n", ("test_verification",)),
    Mutation("a clean report from a failed command passes", "verification.py",
             "    if exit_code != 0:\n        return Status.ERROR",
             "    if False:\n        return Status.ERROR", ("test_verification",)),
    Mutation("a report with a document type is read", "verification.py",
             "    parser.StartDoctypeDeclHandler = refuse\n",
             "    pass\n", ("test_verification",)),
    Mutation("a check that changed the task's files counts", "verification.py",
             "        if result.changed:\n",
             "        if False:\n", ("test_verification",)),
    Mutation("output is kept with its secrets", "verification.py",
             "        output = scrub(ran.stdout + \"\\n\" + ran.stderr, self.secrets)\n",
             "        output = ran.stdout + \"\\n\" + ran.stderr\n", ("test_verification",)),
    Mutation("a report location is not checked before the run", "verification.py",
             "                target = _inside(self.workspace, check.report_path)\n",
             "                target = self.workspace / check.report_path\n", ("test_verification",)),
    Mutation("a report left from before the run is read as this run's", "verification.py",
             "        if earlier is not None and earlier == version:\n",
             "        if False:\n", ("test_verification",)),
    Mutation("a build without its artifacts passes", "verification.py",
             "        if empty:\n",
             "        if False:\n", ("test_verification",)),
    Mutation("a timeout hides that what the check started may still run", "verification.py",
             "            if result.process_tree == process.CHILD:\n",
             "            if False:\n", ("test_verification",)),
    Mutation("a record leaves out how the check's processes were held", "verification.py",
             "                   \"process_tree\": result.process_tree,\n",
             "", ("test_verification",)),
    Mutation("a refused last signal to a stopped group is raised as a refused start", "process.py",
             "        except PermissionError:\n            if time.monotonic() >= deadline:\n",
             "        except ():\n            if time.monotonic() >= deadline:\n", ("test_process", "test_verification")),
    Mutation("a group still refusing its last signal is taken for a stopped one", "process.py",
             "            if time.monotonic() >= deadline:\n                return True\n",
             "            return False\n", ("test_process", "test_verification")),
    Mutation("a group's refusal of its last signal is not asked again", "process.py",
             "    deadline = time.monotonic() + GROUP_SECONDS\n",
             "    deadline = time.monotonic()\n", ("test_process", "test_verification")),
    Mutation("a timeout hides that the system refused to stop what the check started", "verification.py",
             "            if result.kill_refused:\n",
             "            if False:\n", ("test_verification",)),
    Mutation("a record leaves out that the system refused to stop the check's processes", "verification.py",
             "                   \"kill_refused\": result.kill_refused,\n",
             "", ("test_verification",)),
    Mutation("a stopped command is said to be refused its last signal when nothing says so", "process.py",
             "    return getattr(result, \"timed_out\", False) is True and getattr(result, \"kill_refused\", False) is True\n",
             "    return getattr(result, \"timed_out\", False) is True\n", ("test_agents", "test_verification")),
    Mutation("an agent's timeout hides that the system refused to stop what it started", "providers.py",
             "f\"Timeout after {timeout}s for {provider}/{role}\" + (f\". {KILL_REFUSED}\" if refused else \"\")",
             "f\"Timeout after {timeout}s for {provider}/{role}\"", ("test_agents",)),
    Mutation("an agent's log leaves out that the system refused to stop what it started", "providers.py",
             "with_refusal(errors) if refused else errors",
             "errors", ("test_agents",)),
    Mutation("the test stage's log leaves out that the system refused to stop a command", "core.py",
             "with_refusal(output) if refused else output",
             "output", ("test_verification",)),
    Mutation("the test stage's journal leaves out that the system refused to stop a command", "core.py",
             "                     text=KILL_REFUSED if refused else None)\n",
             "                     text=None)\n", ("test_verification",)),
    Mutation("the test stage's result leaves out that the system refused to stop a command", "core.py",
             "            entry.update(message=KILL_REFUSED, kill_refused=True)\n",
             "            pass\n", ("test_verification",)),
    Mutation("a check's result leaves out that the system refused to stop it", "verification.py",
             "            found[\"kill_refused\"] = True",
             "            pass", ("test_verification",)),
    Mutation("a test timeout's pause hides that the system refused to stop what it started", "core.py",
             "\"Tests timed out; check the logs before resuming\" + (f\". {KILL_REFUSED}\" if refused else \"\")",
             "\"Tests timed out; check the logs before resuming\"", ("test_verification",)),
    Mutation("a record of a command may be written by somebody else", "evidence.py",
             "    if executor != EXECUTORS[kind]:\n",
             "    if False:\n", ("test_verification",)),
    Mutation("a record of a command may say anything", "evidence.py",
             "    if kind == \"command\" and fields.get(\"result\") not in COMMAND_RESULTS:\n",
             "    if False:\n", ("test_verification",)),
    Mutation("writers of one ledger do not take turns", "evidence.py",
             "    with FileMutex(file):\n",
             "    with open(os.devnull):\n", ("test_verification",)),
    Mutation("a check without a result fails its criterion", "acceptance.py",
             "            if failed and failed.get(\"result\") not in verification.UNSETTLED:\n",
             "            if failed:\n", ("test_verification",)),
    Mutation("a check without a result goes on to the review", "core.py",
             "    if unsettled is not None:\n        _save(path, state)\n        raise _unsettled(unsettled)\n",
             "", ("test_verification",)),
    Mutation("the checks are left out of the test context", "core.py",
             "    checks = verification.context(cfg) if enabled else []\n",
             "    checks = []\n", ("test_verification",)),
    # Provenance and invalidation (November 4, 2026).
    Mutation("evidence about other files is still valid", "provenance.py",
             "        elif fingerprint != now.fingerprint:\n            causes.append(Cause.WORKSPACE_CHANGED)\n", "",
             ("test_provenance", "test_acceptance")),
    Mutation("evidence that names no state of its files is valid", "provenance.py",
             "        if not isinstance(fingerprint, str) or not fingerprint:\n            causes.append(Cause.SNAPSHOT_MISSING)\n",
             "        if False:\n            pass\n", ("test_provenance",)),
    Mutation("a result of another configuration of its check is valid", "provenance.py",
             "        if digest != now.checks[name]:\n", "        if False:\n", ("test_provenance",)),
    Mutation("a result of a check no longer configured is valid", "provenance.py",
             "    if name not in now.checks:\n        return [Cause.CHECK_REMOVED]\n", "    if name not in now.checks:\n        return []\n",
             ("test_provenance",)),
    Mutation("a result that does not name its configuration is valid", "provenance.py",
             "    else:\n        return [Cause.CHECK_UNIDENTIFIED]\n", "    else:\n        return []\n", ("test_provenance",)),
    Mutation("an older command of tests.commands is valid whatever ran", "provenance.py",
             "        if number > len(now.legacy) or now.legacy[number - 1] != tuple(record.get(\"command\") or ()):\n",
             "        if False:\n", ("test_provenance",)),
    Mutation("a result on other declared inputs is valid", "provenance.py",
             "        elif before[\"sha256\"] != now.inputs[name]:\n", "        elif False:\n", ("test_provenance",)),
    Mutation("declared inputs that cannot be compared are taken as unchanged", "provenance.py",
             "        if now.inputs[name] is None or not isinstance(before.get(\"sha256\"), str):\n", "        if False:\n",
             ("test_provenance",)),
    Mutation("a result bound to its environment is valid elsewhere", "provenance.py",
             "        if environment.get(\"id\") != now.environment:\n", "        if False:\n", ("test_provenance",)),
    Mutation("evidence whose artifacts changed is valid", "provenance.py",
             "        if state != evidence.INTACT:\n", "        if False:\n", ("test_provenance", "test_acceptance")),
    Mutation("a changed copy of a report is only stale", "provenance.py",
             "            causes.append(Cause.REPORT_CHANGED if _report_copy(record, artifact)",
             "            causes.append(Cause.ARTIFACT_CHANGED if _report_copy(record, artifact)", ("test_provenance",)),
    Mutation("a decision of the user is judged on the files", "provenance.py",
             "    if not found.usable or record.get(\"kind\") not in JUDGED or record[\"kind\"] == \"authorization\":\n",
             "    if not found.usable or record.get(\"kind\") not in JUDGED:\n", ("test_provenance",)),
    Mutation("an invalidated record is invalidated again", "provenance.py",
             "        if record.get(\"kind\") in JUDGED and ledger.counts(record):\n",
             "        if record.get(\"kind\") in JUDGED:\n", ("test_provenance",)),
    Mutation("a run invalidates nothing it judged unusable", "core.py",
             "        entries = provenance.invalidate(path, ledger, now, iteration=state[\"iteration\"])\n",
             "        entries = []\n", ("test_provenance",)),
    Mutation("a Resume at validate does not see evidence that would be invalidated", "core.py",
             "        return bool(provenance.stale(ledger, provenance.current(cfg, workspace, current)))\n",
             "        return False\n", ("test_provenance",)),
    Mutation("declared inputs are left out of the test context", "core.py",
             "        if declared:\n            context[\"inputs\"]", "        if False:\n            context[\"inputs\"]",
             ("test_provenance",)),
    Mutation("the environment is left out of the test context", "core.py",
             "            context[\"environment\"] = verification.environment()[\"id\"]\n", "            pass\n", ("test_provenance",)),
    Mutation("the reviewer is not told what no longer counts", "acceptance.py",
             "        apart = provenance.brief(ledger) if provenance.enabled() else []\n", "        apart = []\n",
             ("test_provenance",)),
    Mutation("a forged decision of the user counts", "acceptance.py",
             "        if not self.ledger.sound(record):\n", "        if not record or not record[\"trusted\"]:\n", ("test_provenance",)),
    Mutation("a check runs although its inputs cannot be hashed", "verification.py",
             "            if problem:\n                refusal = f\"An input the check declares cannot be hashed",
             "            if False:\n                refusal = f\"An input the check declares cannot be hashed", ("test_provenance",)),
    Mutation("a check that changed its declared inputs counts", "verification.py",
             "            if problem or after[\"sha256\"] != result.inputs[\"sha256\"]:\n", "            if False:\n",
             ("test_provenance",)),
    Mutation("a record does not name the configuration of its check", "verification.py",
             "                   \"config_sha256\": check.digest(), **extra,", "                   **extra,", ("test_provenance",)),
    Mutation("a record of a schema version this one does not know is proof", "evidence.py",
             "        return type(record.get(\"version\")) is int and record[\"version\"] == VERSION and kind in KINDS \\\n",
             "        return kind in KINDS \\\n", ("test_provenance",)),
    Mutation("a record written by another executor than its kind's is proof", "evidence.py",
             "            and record.get(\"executor\") == EXECUTORS[kind] and (kind", "            and (kind", ("test_provenance",)),
    Mutation("an artifact replaced by a link is intact", "evidence.py",
             "        if not stat.S_ISREG(info.st_mode) or info.st_size != artifact[\"size\"]:\n",
             "        if info.st_size != artifact[\"size\"]:\n", ("test_provenance",)),
    Mutation("files behind a link or a junction are read into the fingerprint", "gitops.py",
             "        if seen[folder]:\n            return True\n", "", ("test_provenance",)),
    Mutation("a file that changes while it is read is hashed anyway", "gitops.py",
             "            if _same(before, opened) and _same(opened, ended) and size == ended.st_size and _same(ended, os.lstat(path)):\n",
             "            if True:\n", ("test_provenance",)),
    Mutation("the files of a repository inside the copy are not hashed", "gitops.py",
             "            elif _depth < NESTED and path.is_dir() and not _linked(path) and (path / \".git\").exists():\n",
             "            elif False:\n", ("test_provenance",)),
    # The acceptance gate (November 4, 2026).
    Mutation("a task is completed without the gate's pass", "core.py",
             "    if not decided.completion_allowed:\n        raise CompletionRefused(decided.message(), \"completion_refused\")\n",
             "", ("test_gate",)),
    Mutation("a task is completed on files that changed after the gate decided", "core.py",
             "    if current != decided.snapshot or state.get(\"test_context\") != _test_context(cfg, current, workspace):\n",
             "    if False:\n", ("test_gate",)),
    Mutation("a task is completed on a ledger that moved after the gate decided", "core.py",
             "    if ledger.problems or {\"count\": ledger.count, \"head\": ledger.head} != decided.ledger:\n",
             "    if False:\n", ("test_gate",)),
    Mutation("the validate checkpoint completes without the gate", "core.py",
             "    if decided.completion_allowed:\n        _complete(path, state, cfg, workspace, decided)\n",
             "    if True:\n        _complete(path, state, cfg, workspace, decided)\n", ("test_acceptance", "test_gate")),
    Mutation("a task that waits for a switch to be put back is run again while it is off", "core.py",
             "            # Idempotent Resume: while a switch still turns off what the configured checks rely on, nothing can complete.\n"
             "            return state\n",
             "            # Idempotent Resume: while a switch still turns off what the configured checks rely on, nothing can complete.\n"
             "            pass\n", ("test_gate",)),
    Mutation("a reviewer's evidence references are read whatever their shape", "core.py",
             "        raise AgentFailure(\"Invalid review: malformed evidence references\", \"invalid_review\")\n",
             "        pass\n", ("test_gate",)),
    Mutation("test results about other files let the gate pass", "gate.py",
             "    if fresh is False:\n", "    if False:\n", ("test_gate",)),
    Mutation("files nobody compared are taken as current by the gate", "gate.py",
             "    elif fresh is None or snapshot is None:\n", "    elif False:\n", ("test_gate",)),
    Mutation("evidence that lapsed is not asked for again", "gate.py",
             "                if not ledger.counts(record) and proofs.positive(record, criterion.id):\n",
             "                if False:\n", ("test_gate",)),
    Mutation("a verified criterion naming a record that does not count passes the gate", "gate.py",
             "            if broken:\n", "            if False:\n", ("test_gate",)),
    Mutation("a record that was not judged passes the gate", "gate.py",
             "    if not conditions and acceptance.outcome != Outcome.RELEASE_READY:\n", "    if False:\n", ("test_gate",)),
    Mutation("the runner turned off lets a task complete without its configured checks", "gate.py",
             "    if section and not verification.enabled():\n", "    if False:\n", ("test_gate",)),
    Mutation("provenance turned off lets a task complete without the inputs its checks declare", "gate.py",
             "    if tied and not provenance.enabled():\n", "    if False:\n", ("test_gate",)),
    Mutation("a reviewer's reference to a record of another criterion is accepted", "gate.py",
             "            elif name not in pertinent:\n", "            elif False:\n", ("test_gate",)),
    Mutation("a reviewer's reference to a record that no longer counts is accepted", "gate.py",
             "            if not found.usable:\n", "            if False:\n", ("test_gate",)),
    # The gate binds what it relies on to its own snapshot (November 4, 2026, Step 3 hardening).
    Mutation("an approval about other files lets the gate pass", "gate.py",
             "        elif (found := _elsewhere(ledger, approval, snapshot)) is not None:\n", "        elif False:\n",
             ("test_gate",)),
    Mutation("evidence about other files verifies a required criterion at the gate", "gate.py",
             "            if elsewhere:\n", "            if False:\n", ("test_gate",)),
    Mutation("a record that is not an approving review approves the code at the gate", "gate.py",
             "        if approval is None or approval.get(\"kind\") != \"review\" or approval.get(\"verdict\") != \"APPROVED\":\n",
             "        if False:\n", ("test_gate",)),
    # The evidence bundle and the Verification Center (November 5, 2026, the user's "0.35 Phase 2 / Step 4").
    Mutation("an export assembles a state and a ledger that were never on disk together", "bundle.py",
             "        if first == second:\n", "        if True:\n", ("test_bundle",)),
    Mutation("an export follows a log name out of the task's runs", "bundle.py",
             "    if not LOG_NAME.fullmatch(name):\n", "    if False:\n", ("test_bundle",)),
    Mutation("an export reads a log through a link", "bundle.py",
             "        if stat.S_ISLNK(checked.st_mode) or winacl.is_reparse_point(target):\n", "        if False:\n", ("test_bundle",)),
    Mutation("an export copies logs without masking them", "bundle.py",
             "    return verification.scrub(text, secrets).encode(\"utf-8\")\n", "    return text.encode(\"utf-8\")\n", ("test_bundle",)),
    Mutation("an export keeps a ledger record cut short", "bundle.py",
             "    whole = ledger_bytes[:ledger_bytes.rfind(b\"\\n\") + 1]\n", "    whole = ledger_bytes\n", ("test_bundle",)),
    Mutation("an export calls a gate about another ledger complete", "bundle.py",
             "        if not about:\n", "        if False:\n", ("test_bundle",)),
    Mutation("a bundle entry with an unsafe name is read", "bundle.py",
             "        if not safe_name(info.orig_filename) or info.orig_filename != name or name.endswith(\"/\") or info.is_dir():\n",
             "        if False:\n", ("test_bundle",)),
    Mutation("a bundle with two entries of one name is read", "bundle.py",
             "        if name in names or name.casefold() in folded:\n", "        if False:\n", ("test_bundle",)),
    Mutation("a bundle entry that expands far beyond its size is read", "bundle.py",
             "        elif info.file_size > RATIO_FLOOR and info.file_size > MAX_RATIO * max(info.compress_size, 1):\n",
             "        elif False:\n", ("test_bundle",)),
    Mutation("a changed bundle file passes the check", "bundle.py",
             "        if len(found) != entry[\"size\"] or hashlib.sha256(found).hexdigest() != entry[\"sha256\"]:\n",
             "        if False:\n", ("test_bundle",)),
    Mutation("a file the manifest does not list passes the check", "bundle.py",
             "    for name in sorted(set(names) - set(paths) - {MANIFEST}):\n", "    for name in []:\n", ("test_bundle",)),
    Mutation("a bundle of an unknown schema is checked as if it were known", "bundle.py",
             "    if type(version) is not int or version != VERSION:\n", "    if False:\n", ("test_bundle",)),
    Mutation("a bundle whose validity contradicts its ledger passes the check", "bundle.py",
             "            if validity[\"records\"] != validity_rows(anchored):\n", "            if False:\n", ("test_bundle",)),
    Mutation("a bundle whose saved gate does not carry its own ID passes the check", "bundle.py",
             "        if gate_id(saved_gate) != saved_gate.get(\"id\"):\n", "        if False:\n", ("test_bundle",)),
    Mutation("a manifest that names another gate decision or permission passes the check", "bundle.py",
             "        if not isinstance(shown, dict) or (shown.get(\"id\"), shown.get(\"decision\"), shown.get(\"about_exported_ledger\"),\n",
             "        if False and (shown.get(\"id\"), shown.get(\"decision\"), shown.get(\"about_exported_ledger\"),\n", ("test_bundle",)),
    Mutation("the Verification Center offers to complete a task the gate does not let complete", "app.py",
             "                if action == \"COMPLETE\":\n", "                if True:\n", ("test_bundle",)),
    Mutation("the Verification Center shows a gate older than the ledger as current", "app.py",
             "        ledger_current = (seen.get(\"count\"), seen.get(\"head\")) == (raw.count, raw.head)\n",
             "        ledger_current = True\n", ("test_bundle.CenterTests", "test_bundle.FreshnessTests")),
    Mutation("the Verification Center shows a gate evaluated on other files as current", "core.py",
             "    files = \"current\" if current == snapshot else \"changed\"\n", "    files = \"current\"\n",
             ("test_bundle.FreshnessTests",)),
    Mutation("the Verification Center shows a gate evaluated with other checks as current", "core.py",
             "        configuration = \"current\" if fresh == saved.get(\"fresh\") and gate.protections(cfg) == (saved.get(\"protections\") or []) \\\n",
             "        configuration = \"current\" if True or gate.protections(cfg) == (saved.get(\"protections\") or []) \\\n",
             ("test_bundle.FreshnessTests",)),
    Mutation("the Verification Center offers to complete from an evaluation that is not current", "app.py",
             "                    if gate_state != \"current\":\n", "                    if False:\n", ("test_bundle.FreshnessTests",)),
    Mutation("the Verification Center tells a view the files are as an earlier reading found them", "app.py",
             "            if entry[\"covers\"] < ticket:\n", "            if entry[\"value\"] is None and entry[\"error\"] is None:\n",
             ("test_bundle.FreshnessTests",)),
    Mutation("the Verification Center's reading of the files writes into the copy's Git directory", "gitops.py",
             "READER = (\"--no-optional-locks\", \"-c\", \"core.fsmonitor=false\")\n", "READER = ()\n",
             ("test_bundle.FreshnessTests",)),
    Mutation("a bundle whose ledger fails its own hash chain passes the check", "bundle.py",
             "        if ledger.problems or ledger.incomplete:\n", "        if False:\n", ("test_bundle.VerifyTests", "test_bundle.ExportTests")),
    Mutation("a bundle whose ledger lost records the state had seen passes the check", "bundle.py",
             "        elif anchored.problems:\n", "        elif False:\n", ("test_bundle.VerifyTests", "test_bundle.ExportTests")),
    Mutation("a bundle with no evaluation of its own ledger passes as complete", "bundle.py",
             "        if ledger is not None and not about:\n", "        if False:\n", ("test_bundle.VerifyTests",)),
    Mutation("a bundle with no gate passes as complete when its manifest declares nothing missing", "bundle.py",
             "        problem(Verdict.INCOMPLETE, \"NO_GATE\",", "        (lambda *_: None)(Verdict.INCOMPLETE, \"NO_GATE\",",
             ("test_bundle.VerifyTests",)),
    Mutation("a bundle whose manifest no longer lists a file every export writes passes the check", "bundle.py",
             "        if name not in paths:\n", "        if False:\n", ("test_bundle.VerifyTests",)),
    Mutation("a bundle record of another shape is read as nothing to compare", "bundle.py",
             "        elif name in content and not _shaped(name, value):\n", "        elif False:\n", ("test_bundle.VerifyTests",)),
    Mutation("a bundle whose contract holds no criteria passes as complete", "bundle.py",
             "        elif contract is not None:\n", "        elif False:\n", ("test_bundle.VerifyTests",)),
    Mutation("a bundle file nested deeper than any export nests is read", "bundle.py",
             "    if not evidence.shallow(value):\n", "    if False:\n", ("test_bundle.VerifyTests",)),
    Mutation("a ledger record that takes the ID of an earlier one is trusted", "evidence.py",
             "        if trusted and record[\"id\"] in earlier:\n", "        if False:\n", ("test_acceptance.LedgerTests",)),
    Mutation("an invalidation that does not name a list of record IDs is trusted", "evidence.py",
             "        if not formed:\n", "        if False:\n", ("test_acceptance.LedgerTests",)),
    Mutation("an invalidation that does not name a list of record IDs may be written", "evidence.py",
             "    if kind == \"invalidation\" and not _named(fields.get(\"of\"), {})[1]:\n", "    if False:\n",
             ("test_acceptance.LedgerTests",)),
    Mutation("a ledger line nested deeper than any writer nests is read", "evidence.py",
             "            record = record if shallow(record) else None\n", "            record = record\n",
             ("test_acceptance.LedgerTests",)),
    Mutation("an export fails on a state a run is replacing instead of reading it again", "bundle.py",
             "        _unshared(lambda: found.append(file.read_bytes()))\n", "        found.append(file.read_bytes())\n",
             ("test_bundle.ExportTests",)),
    Mutation("an export keeps a bundle made while the task's files changed", "bundle.py",
             "        if _reading(task_path) == taken:\n", "        if True:\n", ("test_bundle.ExportTests",)),
    Mutation("an export calls a log that is no longer the one its record hashed complete", "bundle.py",
             "            if matches is False:\n", "            if False:\n", ("test_bundle.ExportTests",)),
    Mutation("exports are not bounded in number", "app.py",
             "        if not self._exports.acquire(blocking=False):\n", "        if False:\n", ("test_bundle",)),
    # The system's folder dialog of the Add project form (November 7, 2026).
    Mutation("the folder dialog cannot be turned off", "picker.py",
             "    if env.get(SWITCH) == \"0\":\n", "    if False:\n", ("test_picker.PlanTests",)),  # route tests would open a real one
    Mutation("a path reaches AppleScript inside the script instead of as an argument", "picker.py",
             "(\" default location (POSIX file (item 2 of argv))\" if start else \"\")",
             "(f\" default location (POSIX file \\\"{start}\\\")\" if start else \"\")", ("test_picker.PlanTests",)),
    Mutation("a dialog without a graphical session is started", "picker.py",
             "    if not (env.get(\"DISPLAY\") or env.get(\"WAYLAND_DISPLAY\")):\n", "    if False:\n", ("test_picker.PlanTests",)),
    Mutation("a chosen path that is not a folder is answered", "picker.py",
             "        if path.is_absolute() and path.is_dir():\n", "        if True:\n", ("test_picker.ReadTests",)),
    Mutation("a second press opens a second folder dialog", "picker.py",
             "            if session is None or session[\"done\"].is_set():\n", "            if True:\n", ("test_picker.PickerTests",)),
    Mutation("a folder dialog left open is never closed", "picker.py",
             "            process.kill()\n            process.communicate()\n",
             "            process.communicate()\n", ("test_picker.PickerTests",)),
    Mutation("closing the dashboard leaves the folder dialog open", "ui.py",
             "        self.app.picker.close()\n", "", ("test_picker.RouteTests",)),
    # A first commit made on request from the Add project form (November 7, 2026).
    Mutation("a folder that changed after the review is committed", "firstcommit.py",
             "        if survey[\"digest\"] != digest:\n", "        if False:\n", ("test_firstcommit.MakeTests",)),
    Mutation("the home folder is made a repository", "firstcommit.py",
             "    if path == Path.home().resolve():\n", "    if False:\n", ("test_firstcommit.PlanTests",)),
    Mutation("PatchRondo's own state is made a repository", "firstcommit.py",
             "    if path == home or home.is_relative_to(path) or path.is_relative_to(home):\n", "    if False:\n",
             ("test_firstcommit.PlanTests",)),
    Mutation("a repository Git does not open gets a new one inside it", "firstcommit.py",
             "            if os.path.lexists(folder / \".git\"):\n", "            if False:\n", ("test_firstcommit.PlanTests",)),
    Mutation("more files than the limit are committed", "firstcommit.py",
             "    if len(given) > LIMIT:\n", "    if False:\n", ("test_firstcommit.PlanTests",)),
    Mutation("a missing Git identity is found only after git init", "firstcommit.py",
             "        who = [] if _identity(path) else _typed_identity(name, email)\n", "        who = []\n",
             ("test_firstcommit.MakeTests",)),
    # Tests written by the developer on request while a task waits for the test consent (November 7, 2026).
    Mutation("tests are written by an agent outside the consent pause", "core.py",
             "        if not (state.get(\"status\") == \"paused\" and state.get(\"phase\") == \"test\" and isinstance(error, dict)\n",
             "        if False and not (state.get(\"status\") == \"paused\" and state.get(\"phase\") == \"test\" and isinstance(error, dict)\n",
             ("test_draft_tests.EngineTests",)),
    Mutation("the developer writes tests before the environment check", "core.py",
             "            if manager is not None:  # before the agent, as before any provider call\n"
             "                _environment(manager, home, path, state, cfg, live=live)\n",
             "", ("test_draft_tests.EngineTests",)),
    Mutation("a proposed command spanning lines is kept", "core.py",
             "and not any(char in line for char in \"\\r\\n\\0\")", "and True", ("test_draft_tests.ParseTests",)),
    Mutation("the notes of a test author are not masked", "core.py",
             "\"notes\": activity.clip(activity.redact(notes.strip()), DRAFT_NOTES)}",
             "\"notes\": activity.clip(notes.strip(), DRAFT_NOTES)}", ("test_draft_tests.ParseTests",)),
    Mutation("the dashboard asks for tests without the user's confirmation", "app.py",
             "        if draft and payload.get(\"confirm\") is not True:\n", "        if False:\n", ("test_draft_tests.RouteTests",)),
    Mutation("the dashboard asks for tests outside the consent pause", "app.py",
             "            if draft and not (status == \"paused\"", "            if False and not (status == \"paused\"",
             ("test_draft_tests.RouteTests",)),
    # Each request for tests keeps its own transcripts (November 8, 2026).
    Mutation("a request for tests writes over the development step's transcripts", "core.py",
             "            run_dir = _draft_folder(path, state[\"iteration\"])\n",
             "            run_dir = private_dir(path / \"runs\" / f\"iteration-{state['iteration']:03d}\")\n",
             ("test_draft_tests.TranscriptTests",)),
    Mutation("a request for tests writes into an earlier request's folder", "core.py",
             "f\"test-draft-{max(used, default=0) + 1:02d}\"", "f\"test-draft-{1:02d}\"",
             ("test_draft_tests.TranscriptTests",)),
    Mutation("a request for tests looks like a development step in the journal", "core.py",
             "workspace, run_dir, settings, step=DRAFT_STEP)", "workspace, run_dir, settings)",
             ("test_draft_tests.TranscriptTests",)),
    Mutation("forced unlock ignores the process of a request for tests", "runfiles.py",
             "PROCESS_DEPTH = 3", "PROCESS_DEPTH = 1", ("test_workflow.WorkflowTests",)),
    Mutation("the transcripts of a request for tests are not listed", "app.py",
             "                    drafts.append(file)  # a request for tests", "                    pass  # a request for tests",
             ("test_ui.TaskTests",)),
    # Adaptive Execution settings and their limits (Phase 2.5, Step 02, November 8, 2026). Nothing applies them yet.
    Mutation("an execution section is not checked", "core.py", "    execution.settings_of(obj)\n", "",
             ("test_execution_config.SectionTests",)),
    Mutation("a disabled execution section applies its profile", "execution.py",
             "    if chosen is None or not chosen.enabled:", "    if chosen is None:",
             ("test_execution_config.PrecedenceTests",)),
    Mutation("a profile raises an explicit agent or workflow limit", "execution.py",
             "        effective = min([*candidates, base], key=lambda limit: limit.value)", "        effective = base",
             ("test_execution_config.PrecedenceTests",)),
    Mutation("a named profile is widened by explicit limits", "execution.py",
             "                if value > allowed:", "                if False:", ("test_execution_config.SectionTests",)),
    Mutation("inconsistent execution budgets are accepted", "execution.py",
             "                if self.value(*small) > self.value(*large):", "                if False:",
             ("test_execution_config.SectionTests",)),
    Mutation("native resume can be switched on", "execution.py",
             "        if native not in NATIVE_RESUME or type(native) is not str:", "        if False:",
             ("test_execution_config.SectionTests",)),
    Mutation("a Codex step is given Claude turns", "execution.py",
             "    if agent_turns is not None and not claude:", "    if False:", ("test_execution_config.AccountingTests",)),
    Mutation("a Codex step budget counts Claude turns", "execution.py",
             "if claude or unit is not Unit.CLAUDE_TURNS", "if True", ("test_execution_config.AccountingTests",)),
    # Attempt folders and their readers (Phase 2.5, Step 03, November 8, 2026). No run writes them yet.
    Mutation("forced unlock ignores the process of an attempt", "runfiles.py",
             "PROCESS_DEPTH = 3", "PROCESS_DEPTH = 2", ("test_runfiles.LockTests",)),
    Mutation("forced unlock reads no process file", "storage.py",
             "            for entry in runfiles.process_files(runs):\n", "            for entry in []:\n",
             ("test_workflow.WorkflowTests", "test_runfiles.LockTests")),
    Mutation("native Windows liveness assumed dead for an attempt", "storage.py",
             "        # Cannot reliably determine liveness without optional dependencies on Windows.\n        return True",
             "        return False", ("test_runfiles.LockTests",)),
    Mutation("a new attempt writes into an existing folder", "runfiles.py",
             "    target.mkdir(mode=0o700)  # never an existing folder\n", "    target.mkdir(mode=0o700, exist_ok=True)\n",
             ("test_runfiles.AllocationTests",)),
    Mutation("a new attempt takes the number of an earlier one", "runfiles.py",
             "        ordinal = max(used, default=0) + 1\n", "        ordinal = 1\n", ("test_runfiles.AllocationTests",)),
    Mutation("an attempt is written through a link", "runfiles.py",
             "        if os.path.lexists(folder) and not plain_folder(folder):\n", "        if False:\n",
             ("test_runfiles.AllocationTests",)),
    Mutation("attempt folder names are not checked", "runfiles.py",
             "attempt-(?!000)\\d{{3}}", "attempt-[^/]+", ("test_runfiles.NameTests",)),
    Mutation("a record may name a log no reader opens", "runfiles.py",
             "    if not LOG_NAME.fullmatch(name):\n        raise ValueError", "    if False:\n        raise ValueError",
             ("test_runfiles.NameTests",)),
    Mutation("a review record names its log after the last folder only", "core.py",
             "log=runfiles.log_name(path, run_dir / \"reviewer.reply.md\"),", "log=f\"{run_dir.name}/reviewer.reply.md\",",
             ("test_runfiles.NameTests",)),
    Mutation("the Logs tab leaves out attempt folders", "app.py",
             "            for phase, ordinal, attempt in runfiles.attempts(folder):\n",
             "            for phase, ordinal, attempt in []:\n", ("test_runfiles.LogsTests",)),
    Mutation("the Logs tab reads an attempt folder through a link", "runfiles.py",
             "            if ordinal is not None and plain_folder(entry):\n", "            if ordinal is not None:\n",
             ("test_runfiles.LogsTests",)),
    Mutation("the Logs tab reads a phase folder through a link", "runfiles.py",
             "        if not plain_folder(phase_dir):\n", "        if not phase_dir.is_dir():\n", ("test_runfiles.LogsTests",)),
    Mutation("the Logs tab lists a file of an attempt through a link", "app.py",
             "f\"{folder.name}/{phase}/{attempt.name}/{file.name}\") or not runfiles.plain_file(file):",
             "f\"{folder.name}/{phase}/{attempt.name}/{file.name}\") or not file.is_file():", ("test_runfiles.LogsTests",)),
    Mutation("the Logs tab lists any name in an attempt folder", "app.py",
             "                    if file.suffix not in LOG_SUFFIXES or not runfiles.LOG_NAME.fullmatch(\n",
             "                    if file.suffix not in LOG_SUFFIXES or not (\n", ("test_runfiles.LogsTests",)),
    Mutation("an export reads a nested log through a linked folder", "bundle.py",
             "        for part in folders:\n", "        for part in folders[:2]:\n", ("test_runfiles.BundleTests",)),
]


def run_mutation(mutation: Mutation | None, tests: tuple[str, ...] = TESTS) -> tuple[bool, str]:
    """Run `tests` against a temporary copy of the sources; return (tests failed, last output line).

    With `mutation` the copy is changed first. None checks the unchanged copy.
    """
    work = Path(tempfile.mkdtemp(prefix="patchrondo-protection-"))
    try:
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(PACKAGE, work / "src" / "patchrondo", ignore=ignore)
        shutil.copytree(ROOT / "tests", work / "tests", ignore=ignore)
        shutil.copytree(ROOT / "tools", work / "tools", ignore=ignore)  # some interface tests load the demo tool
        if mutation is not None:
            target = work / "src" / "patchrondo" / mutation.file
            text = target.read_bytes().decode("utf-8")
            if text.count(mutation.old) != 1:
                return False, f"pattern occurs {text.count(mutation.old)} times in {mutation.file}"
            target.write_bytes(text.replace(mutation.old, mutation.new).encode("utf-8"))
        env = {**os.environ, "PYTHONPATH": str(work / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            run = subprocess.run([sys.executable, "-m", "unittest", *tests], cwd=work / "tests", env=env,
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        except subprocess.TimeoutExpired:
            return True, "tests timed out"
        lines = run.stderr.strip().splitlines()
        return run.returncode != 0, lines[-1] if lines else "no output"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def protections(jobs: int, only: str | None = None) -> int:
    chosen = [m for m in MUTATIONS if only is None or only in m.name]
    if not chosen:
        print(f"No protection matches {only!r}")
        return 1
    # Every group of tests used below must pass on the unchanged copy first.
    for tests in sorted({m.tests or TESTS for m in chosen}):
        failed, summary = run_mutation(None, tests)
        if failed:
            print(f"The unchanged sources do not pass {' '.join(tests)} ({summary}); fix that first.")
            return 1
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(lambda mutation: run_mutation(mutation, mutation.tests or TESTS), chosen))
    missed = 0
    for mutation, (detected, summary) in zip(chosen, results):
        print(f"{'detected' if detected else 'NOT DETECTED'}: {mutation.name} ({summary})")
        missed += not detected
    print(f"RESULT: {len(chosen) - missed} of {len(chosen)} removed protections detected, {missed} missed "
          "(scripted providers)")
    return 1 if missed else 0


# Runs the real CLI entry point; provider commands can only reach the synthetic executable.
RUNNER = r'''
import json, os, shutil, sys, time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[3])
from patchrondo.cli import main
from patchrondo.process import execute
root = Path(sys.argv[1])
def synthetic(argv, cwd, **kwargs):
    if argv[0] not in {"claude", "codex"}:
        raise ValueError("Unexpected provider command")
    reviewer = "--json-schema" in argv or "--output-schema" in argv
    with (root / "real-calls.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": time.time(), "role": "reviewer" if reviewer else "developer"}) + "\n")
    return execute([sys.executable, str(root / "synthetic.py"), *argv], cwd, **kwargs)
free = os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep)
                       if d and not any(shutil.which(n, path=d) for n in ("claude", "codex")))
with patch.dict(os.environ, {"PATH": free}), patch("patchrondo.providers.execute", side_effect=synthetic):
    if any(shutil.which(n) for n in ("claude", "codex")):
        raise RuntimeError("A real provider CLI is still on PATH; refusing the synthetic run")
    raise SystemExit(main(["--home", str(root / "state"), "resume", sys.argv[2], "--auto-resume"]))
'''


def real_clock() -> int:
    if os.name != "posix":
        print("real-clock: SKIPPED (requires POSIX signals; run it under WSL2)")
        return 0
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "tools"))
    import reliability_e2e as fixture
    from patchrondo.storage import read_json, save_json

    checks = []

    def check(name, ok):
        checks.append(bool(ok))
        print(f"{'ok      ' if ok else 'FAILED  '}{name}", flush=True)

    root = Path(tempfile.mkdtemp(prefix="patchrondo-reliability-")).resolve()
    task_id, _ = fixture.setup(root, "auto-resume", "claude", "codex")
    cli = (root / "synthetic.py").read_text(encoding="utf-8")
    (root / "synthetic.py").write_text(cli.replace("Retry after 300 seconds", "Retry after 12 seconds"),
                                       encoding="utf-8", newline="\n")
    cfg = read_json(root / "state" / "config.json")
    cfg["recovery"] = {"initial_backoff_seconds": 10, "reset_safety_margin_seconds": 0}
    save_json(root / "state" / "config.json", cfg)
    path = root / "state" / "tasks" / task_id

    def start():
        return subprocess.Popen([sys.executable, "-c", RUNNER, str(root), task_id, str(ROOT / "src")],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                start_new_session=True)

    def calls():
        file = root / "real-calls.jsonl"
        return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()] if file.exists() else []

    started = time.time()
    first = start()
    output = ""
    try:
        state = {}
        deadline = time.time() + 30
        while time.time() < deadline and first.poll() is None:
            try:
                state = read_json(path / "state.json")
            except (OSError, ValueError):
                state = {}
            waiting = any(entry["event"] == "recovery_wait_started" for entry in state.get("history", []))
            if waiting and not (path / ".run.lock").exists():
                break
            time.sleep(0.05)
        plan = state.get("recovery") or {}
        resume_at = datetime.fromisoformat(plan["resume_at"]).timestamp() if plan.get("resume_at") else 0.0
        check("runner is waiting with a saved plan and no lock", first.poll() is None
              and plan.get("status") == "scheduled" and plan.get("schedule_source") == "provider_reset"
              and not (path / ".run.lock").exists())
        time.sleep(1.0)
        first.send_signal(signal.SIGINT)
        output, _ = first.communicate(timeout=30)
        interrupted_at = time.time()
        after = read_json(path / "state.json")
        check("Ctrl+C ended the wait before the planned time with exit status 2",
              first.returncode == 2 and interrupted_at < resume_at)
        check("plan and quota pause kept unchanged", after["status"] == "paused" and after["recovery"] == plan
              and (after.get("last_error") or {}).get("kind") == "quota")
        check("message says the plan stays saved", "stays saved" in output)
        check("no lock and no call beyond developer and reviewer", not (path / ".run.lock").exists()
              and [call["role"] for call in calls()] == ["developer", "reviewer"])
        second = start()
        output, _ = second.communicate(timeout=90)
        done = read_json(path / "state.json")
        log = calls()
        exact = [call["role"] for call in log] == ["developer", "reviewer", "reviewer"]
        check("restarted runner completed the task", second.returncode == 0 and done["status"] == "done")
        check("exactly one more call, not before the planned time", exact and log[2]["at"] >= resume_at)
        check("the retry came 12 s or more after the failed call", exact and log[2]["at"] - log[1]["at"] >= 12)
        events = [entry["event"] for entry in done["history"]]
        check("journal: one plan, two waits, one retry, completed", (
            events.count("recovery_scheduled"), events.count("recovery_wait_started"),
            events.count("recovery_retry_started"), events.count("recovery_completed")) == (1, 2, 1, 1))
        check("development and tests ran once",
              (events.count("development_completed"), events.count("tests_completed")) == (1, 1))
        if exact:
            print(f"real elapsed: {time.time() - started:.1f}s; retry {log[2]['at'] - resume_at:.2f}s after the planned time")
    finally:
        if first.poll() is None:
            first.kill()
            first.communicate()
    passed = bool(checks) and all(checks)
    if passed:
        fixture.cleanup(root)
    else:
        print(f"Evidence retained: {root}\n{output}")
    print(f"RESULT: real-clock {'PASS' if passed else 'FAIL'} ({sum(checks)}/{len(checks)} checks, synthetic providers)")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", choices=("protections", "real-clock"), help="Default: both")
    parser.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1),
                        help="Parallel test runs for protections (1-8)")
    parser.add_argument("--only", help="Run only protections whose name contains this text")
    args = parser.parse_args(argv)
    if not 1 <= args.jobs <= 8:
        parser.error("--jobs must be between 1 and 8")
    code = 0
    if args.command in (None, "protections"):
        code |= protections(args.jobs, args.only)
    if args.command in (None, "real-clock"):
        code |= real_clock()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
