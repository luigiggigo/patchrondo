# Phase 2.5 — Step 00: audit and baseline

Audit of November 8, 2026 for the proposal *PatchRondo — Fase 2.5: Adaptive
Agent Execution* (revision 1.0, November 8, 2026). It records what the code
does today, the baseline of checks, and where the new execution contracts
can be introduced without duplicating existing components.

This document implements nothing. Phase 2.5 is a proposal: no module, field,
setting or event it names exists in the source unless this audit says so
with a reference. The code is the authority on current behavior; the
proposal is the architectural reference.

## 1. Scope and sources

| Item | Value |
|---|---|
| Branch | `main` |
| Commit audited | `59c520577288978dca824c25899ed0a8efffdc3f` (`59c5205`, "feat: implement separate handling for test requests, preserving transcripts and folder structure") |
| Worktree at the start | clean (`git status --porcelain` empty) |
| Remote `main` | `59c5205` (`git ls-remote origin refs/heads/main`, November 8, 2026) |
| CI of that commit | run 42488098238, all 17 jobs `success` (created 14:05:49 UTC, finished 14:11:16 UTC); Ubuntu Python 3.11 and 3.13: `Ran 925 tests`, `OK (skipped=23)` |
| Package metadata | 0.2.1 (no tags, no Releases at the last recorded check) |
| Specification | `PatchRondo_Fase_2_5_Adaptive_Agent_Execution.md`, 821 lines, SHA-256 `1f864753eca8cec3ab1263370114ede0ec4c7986cf56354a232eec561754dc5f`; kept outside the repository |

The specification's own static audit (its section 2.2) cites line ranges of
`core.py`, `providers.py` and `agents.py`. Each range was compared with
`59c5205` and still points at the code it describes.

**Naming.** The proposal calls itself "Fase 2.5" and places itself after
"Fasi 1–2". In this repository, Phase 1 is the 0.3.0 Phase 1, "Phase 2
step 1–4" belongs to the separate 0.35 Evidence Engine proposal, and the
0.3.0 next phase, Progress & Stagnation Detection, is planned and not
implemented ([roadmap](ROADMAP.md)). Phase 2.5's PR 03 would deliver that
planned phase. How the three plans relate has to be decided explicitly
before the roadmap or the changelog mention Phase 2.5 (see section 7, D1).

## 2. Verified architecture

### 2.1 Entry points

| Entry point | Where | What it does today |
|---|---|---|
| `patchrondo run` / `resume` | `cli.py:48-60` → `recovery.supervise` → `core.run_task` | One run under the task lock until done, blocked or paused; `--unlock`, `--auto-resume`, `--draft-tests` |
| Dashboard Run/Resume | `app.start_run` (separate process group, `ui-run.log`) | Starts `patchrondo run` |
| Main loop | `core.run_task` (`core.py:1112-1333`) | `develop` → `test` → `review` → `validate`; checkpoints are `state.json` writes through `_save` |
| Test request | `core.draft_tests` (`core.py:1454`) | Developer call outside the loop, at the `tests_disabled` pause, in `runs/iteration-NNN/test-draft-KK/` (`core._draft_folder`, `core.py:1357`) |
| Agent call | `core._invoke` (`core.py:637-652`) → `OfficialCLI.invoke` → `OfficialCLI._step` (`providers.py:278-449`) | One journal attempt, one CLI process |
| Command construction | `agents.command` (`agents.py:540-600`) | The only builder of provider argument arrays |
| Process start | `process.execute` (`process.py:193`) | PID file written after spawn (`process.py:230-232`, `321-323`), removed when the child has exited |
| Quota recovery | `recovery.admit` / `on_failure` / `supervise` (`recovery.py:178`, `227`, `309`) | Opt-in, `quota_only` enforced (`recovery.py:48-49`) |
| Completion | `core._complete` (`core.py:954`) | Only writer of `done`, only with the gate's `PASS` |
| Stop | `app.stop_run` (`app.py:1906`) | POSIX only: refused on native Windows (`app.py:1913-1915`, `platform()["stop_supported"]`) |

### 2.2 One step today

1. `run_task` takes `TaskLock` (`core.py:1155`), checks the idempotent
   pauses, asks `recovery.admit`, writes `run_started` and `running`.
2. `run_dir = runs/iteration-NNN` (`core.py:1213`), the same folder for every
   call of that iteration.
3. `_agent_settings` (`core.py:584-607`) thaws or freezes the step's settings
   under `agent_runs["NNN:role"]` (`agents.step_key`, `agents.py:524`),
   `KEPT_RUNS = 60` (`agents.py:62`).
4. `development_started` / `review_started` is saved **before** the call
   (`core.py:1217-1218`, `1251-1252`).
5. `_step` writes `<role>.prompt.md`, builds the command, starts the CLI with
   `pid_file=<role>.active-process.json`, then writes `<role>.stdout.log`,
   `<role>.stderr.log` and, on success, `<role>.reply.md` with
   `atomic_text` (`providers.py:347-448`).
6. Any `AgentFailure`, `KeyboardInterrupt`, `OSError`, `RuntimeError` or
   `TimeoutExpired` pauses the task (`core.py:1322-1333`) with
   `last_error.kind` from `_failure` (`core.py:921`), then
   `recovery.on_failure` plans a retry for `quota` only.

### 2.3 Budgets that exist

| Limit | Source | Frozen with the step? |
|---|---|---|
| `workflow.max_iterations` (1-50, default 6) | `core.py:28`, `WORKFLOW_LIMITS` `core.py:37`; task override allowed | n/a — read per run |
| `workflow.agent_timeout_seconds` (default 900) | `core.py:28`; task override allowed; passed to `OfficialCLI(timeout=…)` in `_callers` (`core.py:1107`) | **No**: read from the configuration of each run |
| `workflow.claude_max_turns` (default 12) | `core.py:29`; **not** a task override (`TASK_OVERRIDES`, `core.py:40`); editable in Settings → Workflow (`app.py:52`, `static/js/views/settings.js:238`) | **No**: read from the configuration of each run |
| Agent setting `max_turns` (Claude) | `agents.py:189`; global/project/task levels | Yes, when set (sparse record, `agents.freeze`, `agents.py:507-514`) |
| Agent setting `timeout_seconds` | `agents.py:135` | Yes, when set |
| Agent setting `max_budget_usd` (Claude) | `agents.py:191` | Yes, when set |
| Phase budget, task budget (calls, wall time), profiles | — | **Absent** |

`--max-turns` is `values["max_turns"] or turns` (`agents.py:565`). The
frozen record only holds values that differ from PatchRondo's defaults, so
an unset `max_turns` resolves to `workflow.claude_max_turns` of the run that
resumes the step. The `max_turns` pause text tells the user to raise
`workflow.claude_max_turns` (`providers.py:395-397`); that has no effect
when an agent-settings `max_turns` was frozen for the step.

### 2.4 Typed signals that exist

- `AgentFailure.kind` (`providers.py:16-27`): free strings, among them
  `max_turns`, `timeout`, `quota`, `authentication`, `model_unavailable`,
  `unsupported_option`, `empty_output`, `invalid_output`, `configuration`,
  `invalid_review`, `agent_error`; plus engine kinds (`tests_disabled`,
  `verification_error`, `completion_refused`, …). There is no enum and no
  record of a successful call.
- Claude's turn limit is read from the CLI's own `result` subtype
  `error_max_turns` (`CLAUDE_TURN_LIMIT`, `providers.py:64`, decided at
  `390-397`), never from activity; a login or usage diagnostic on stderr
  decides first. `repair.py:57` classifies it as `permanent`;
  `test_turn_limit_with_quota_activity_does_not_schedule_recovery`
  (`tests/test_live_activity.py:487`) keeps it out of quota recovery.
- Observed usage reaches the activity journal only: Claude `num_turns`,
  `duration_ms` and `subtype` (`streams.py:213-215`), Codex `input_tokens` /
  `output_tokens` per `turn.completed` (`streams.py:319-323`). The engine
  never reads the journal.
- Session identifiers are not kept: Claude's `system/init` keeps model and
  tools only (`streams.py:224-230`), Codex's `thread.started` emits a bare
  `session` status (`streams.py:312-315`). Session continuation options are
  refused as custom arguments (`ARGS_BLOCKED`, `agents.py:104-116`:
  `--resume`, `--continue`, `--fork-session`, `--session-id`); Codex runs
  `--ephemeral` by default (`agents.py:233-235`, `586`); the settings text
  states that PatchRondo never resumes a CLI session (`agents.py:197-200`).

### 2.5 Observed with a simulated CLI: Resume after `max_turns`

A scratch script (not part of the repository) created a task in a temporary
home, replaced `patchrondo.providers.execute` with a function that streams a
Claude `error_max_turns` result, and ran `run_task` twice
(Python 3.13.3, native Windows, PATH without `claude`/`codex`, exit 0):

| Observation | Run 1 | Run 2 (Resume) |
|---|---|---|
| status / phase / iteration / kind | paused / develop / 1 / `max_turns` | paused / develop / 1 / `max_turns` |
| `agent_runs` keys | `001:developer` | `001:developer` (same frozen record) |
| `developer.stdout.log` contains | attempt 1 | **attempt 2 only** (attempt 1 overwritten) |
| files in `runs/iteration-001/` | `developer.prompt.md`, `.stdout.log`, `.stderr.log` | same three names |
| `--max-turns` passed | 12 | 12 |

The journal held two developer attempts with `turns: 12` and outcome
`error_max_turns` each; no session ID was in the state or the journal.

So a manual Resume already continues **in the same iteration and step, with
the same frozen settings**, as a new call with a fresh prompt (the list of
modified files, the previous iteration's handoff and feedback; nothing about
the interrupted call). What is missing is a record of each call, output
that is not overwritten, a budget, and an automatic decision.

### 2.6 Locks, PID files and readers of `runs/`

| Reader | Pattern it knows | Code |
|---|---|---|
| `TaskLock(force=True)` | `runs/iteration-*/*.active-process.json`, `runs/iteration-*/test-draft-*/*.active-process.json` | `storage.py:119-128` |
| Logs tab | flat files of `iteration-*` and `test-draft-*` folders | `app._logs`, `app.py:1159-1188` |
| Evidence bundle | `iteration-\d{3}/<name>` only (`LOG_NAME`) | `bundle.py:87`, `_part` `bundle.py:198` |
| Verification runner | `<check>.active-process.json` in the iteration folder | `verification.py:763`, `838` |

`storage._alive` answers `True` for any PID on native Windows
(`storage.py:156-158`), so `--unlock` fails closed there while a PID file
exists; `procinfo` can identify processes on Windows (`procinfo.py:20-30`)
but the lock does not use it. Agent CLIs run with `tree=False` (no job
object; `process.py:24-26`), by a standing decision of the project.

## 3. Matrix: PRESENT / PARTIAL / ABSENT / UNKNOWN

| # | Capability (spec section) | Status | Evidence |
|---|---|---|---|
| 1 | Per-call Claude turn limit (§5.2.1) | PRESENT | `agents.py:565`, `core.py:29`, `agents.py:189` |
| 2 | Per-call timeout (§5.2.1) | PRESENT | `core.py:28`, `agents.py:135`, `providers.py:344` |
| 3 | Phase budget (§5.2.2) | ABSENT | — |
| 4 | Task budget: calls, wall time (§5.2.3) | ABSENT | — |
| 5 | Iteration cap (§5.2.4) | PRESENT | `core.py:1312-1318` (`max_iterations_reached`) |
| 6 | Fast/Standard/Deep execution profiles (§5.1) | ABSENT | Agent presets `fast`/`balanced`/`quality` exist for model/effort only (`agents.py:241-250`) |
| 7 | Frozen step settings (§10.2) | PARTIAL | `agent_runs` (`core.py:584-607`); workflow-level turns and timeout are not frozen (2.3) |
| 8 | Settings digest (§7.3) | PRESENT | `agents.digest`, `agents.py:503`, saved in each frozen record |
| 9 | Typed attempt outcome (§7.4) | PARTIAL | `AgentFailure.kind` strings; no enum, no success record |
| 10 | Claude turn-limit detection from structured output | PRESENT | `providers.py:390-397`; tests in `test_live_activity.py` |
| 11 | Observed turns / tokens in persistent state (§5.3) | PARTIAL | Journal only (`streams.py:213`, `319-323`) |
| 12 | Continuation in the same step without a new iteration (§3.1.3) | PARTIAL | Manual Resume does it (2.5); automatic: absent |
| 13 | `checkpoint_resume` brief (§6.2) | PARTIAL | `_developer_prompt` (`core.py:470-500`) has modified files, previous handoff and feedback; nothing about the interrupted call |
| 14 | `native_resume` (§6.1) | ABSENT | Blocked by design (`agents.py:104-116`, `197-200`, `233-235`) |
| 15 | Session ID capture (§6.1.1) | ABSENT | `streams.py:224-230`, `312-315` |
| 16 | Per-attempt output folders (§7.1) | PARTIAL | Present for test requests (`core._draft_folder`); develop/review overwrite (2.5) |
| 17 | Attempt records in `state.json` (§7.3) | ABSENT | Journal attempts exist (`activity.attempt`, random `A-` IDs) and are observational only |
| 18 | Record before launch (§7.5) | PARTIAL | `development_started`/`review_started` saved before the call; PID file after spawn |
| 19 | TaskLock covers attempt folders (§7.6) | PARTIAL | Covers today's layout; would miss `develop/attempt-*` (2.6) |
| 20 | Fail-closed PID check on Windows (T10) | PRESENT | `storage.py:156-158` |
| 21 | Agent process tree on native Windows (§9.3) | ABSENT | `tree=False` for agents |
| 22 | Stop from the dashboard (T25) | PARTIAL | POSIX only (`app.py:1906-1928`) |
| 23 | Quota recovery as single owner (§4.1, T12) | PRESENT | `recovery.py:22-49`, `227-246` |
| 24 | Progress & Stagnation detector (§8) | ABSENT | Roadmap "planned, not implemented"; inputs exist (fingerprint, RF findings, WE events, ledger) |
| 25 | Evidence Engine: runner, provenance, gate, Verification Center, bundles | PRESENT | `verification.py`, `provenance.py`, `gate.py`, `bundle.py`; architecture sections 16-19 |
| 26 | Invalidation after the developer changed files (§11.2, T15) | PRESENT | `record.developed()` and `sync(current=fingerprint(...))`, `core.py:1228-1230`; `provenance.invalidate` |
| 27 | Gate as the only path to `done` (T28) | PRESENT | `core._complete`, `core.py:954-979` |
| 28 | Reviewer read-only at every call (§13.2, T06) | PRESENT | `agents.command` (`agents.py:553-588`), `clean_block` |
| 29 | Tests disabled → no reviewer (T14) | PRESENT | `tests_disabled` pause before the reviewer |
| 30 | Execution summary in UI and report (§12) | ABSENT | Settings → Workflow shows `claude_max_turns` only |
| 31 | Behavior of `error_max_turns` with the installed CLIs | UNKNOWN | Based on one observed failure (`providers.py:60-63`); streaming formats not confirmed by an authorized run |
| 32 | Native resume semantics of installed Claude Code / Codex | UNKNOWN | Not probed; would need help output at a named version or an authorized run |
| 33 | Whether installed CLIs print a session ID in the formats PatchRondo reads | UNKNOWN | Not probed |

## 4. Baseline of checks

All runs on November 8, 2026 on commit `59c5205` with a clean worktree,
`PYTHONPATH=src`, and `claude`/`codex` removed from PATH (asserted with
`shutil.which` before each run). No real provider call was made.

| Check | Platform | Command | Result |
|---|---|---|---|
| CI | GitHub-hosted, 17 jobs | run 42488098238 | VERIFIED: success; Ubuntu 3.11 and 3.13 `Ran 925 tests`, `OK (skipped=23)` |
| Full unittest suite | native Windows, Python 3.13.3, run alone | `python -m unittest discover -s tests` | VERIFIED: `Ran 925 tests in 847.957s`, `OK (skipped=23)` (process exit status not captured) |
| Full unittest suite, first attempt | native Windows, Python 3.13.3, beside the 3.11 run | same | FAILED: 925 tests, 1 failure, 23 skipped — `test_bundle.VerifyTests.test_unsafe_names_duplicates_and_links_are_refused_and_nothing_is_written` compares the whole `%TEMP%` listing, which the concurrent 3.11 run changed; `test_bundle.py` alone then gave 61 tests OK, exit 0. Cause: the two suites shared `%TEMP%`, not the code |
| Full unittest suite | native Windows, Python 3.11.3 | `python -m unittest discover -s tests` | VERIFIED: `Ran 925 tests in 1216.944s`, `OK (skipped=23)` (run beside the first 3.13 run) |
| Recovery protections | WSL2, Python 3.12.3, Git 2.43.0 | `python3 tools/recovery_checks.py protections --jobs 8` | VERIFIED: 364 of 364 removed protections detected, 0 missed; exit 0 after 1121 s |
| Recovery real clock | WSL2, Python 3.12.3 | `python3 tools/recovery_checks.py real-clock` | VERIFIED: 10/10 checks, exit 0 after 15 s |
| Reliability | native Windows, Python 3.13.3 | `python tools/reliability_e2e.py --repeat 3` | VERIFIED: 30 passed, 0 failed, 12 skipped (`interrupt`, `crash`: POSIX only); exit 0 (second run; the first gave the same counts, exit status not captured) |
| Reliability | WSL2, Python 3.12.3 | `python3 tools/reliability_e2e.py --repeat 3` | VERIFIED: 42 passed, 0 failed, 0 skipped; exit 0 after 77 s |
| Browser check | native Windows, Python 3.13.3, Chrome 156.0.8078.80 headless | `python tools/ui_e2e.py` | First full run FAILED, exit 1: 407 passed, 1 failed (`legacy`: "a configuration without a retrieval section shows retrieval as off"); `--only legacy` three times: 15/15, exit 0 each; second full run: 408 passed, 0 failed, exit 0. Intermittent, cause UNKNOWN (evidence kept in `%TEMP%\patchrondo-ui-nb8395ey`) |
| Publication scanner | native Windows | `python tools/check_publication.py` | Exit 1, 146 files, 6 findings, all reviewed: `.claude` and `debug.log` (ignored by `.gitignore`, not tracked) and four tracked images for manual review (`docs/assets/*.png`, `src/patchrondo/static/*.webp`); this document raised none |
| Turn-limit Resume probe | native Windows, Python 3.13.3 | scratch script (2.5) | VERIFIED: exit 0, observations in 2.5 |

Not run, and why:

- `PATCHRONDO_ALL_CHECKPOINTS=1 tests/test_coherence.py`: NOT_RUN (no checkpoint changed).
- `tests/test_sandbox_real.py` with `PATCHRONDO_REAL_SANDBOX=1`: NOT_RUN (no environment-check change); skipped in the suites above, which is not a pass.
- `tools/provider_e2e.py --authorize-provider-calls`: NOT_RUN (needs explicit authorization; uses quota).
- Package build and `twine check`: left to CI (passed in run 42488098238).

Suites the specification asks to preserve (§15.1), all inside the full
suite above: `test_workflow.py`, `test_recovery.py`, `test_agents.py`,
`test_coherence.py`, `test_activity.py`, `test_live_activity.py`,
`test_ui.py`, `test_verification.py`, `test_provenance.py`, `test_gate.py`,
`test_bundle.py`, `test_verification_center.py`, `test_findings.py`,
`test_history.py`, `test_draft_tests.py`, `test_overrides.py`.

## 5. Incompatibilities between the specification and the code

| ID | Specification | Code today | Consequence |
|---|---|---|---|
| I1 | Layout `runs/iteration-NNN/develop/attempt-NNN/` (§7.1) | Four readers know only the flat layout and `test-draft-*` (2.6) | Lock, Logs tab and bundle must learn the new folders in the same step that creates them, or a live agent goes unseen by `--unlock` and its logs leave the bundle |
| I2 | A continuation inherits identical policy (§10.2) | Workflow-level turns and timeout are read per run (2.3) | Decide whether the step record freezes them; changing it alters what a Resume does today |
| I3 | Task-level turn budget via `TASK_OVERRIDES` (spec §2.2) | Task-level `max_turns` already exists through the agent settings' task level | Do not add a third source; derive the call cap from `agents` + `workflow` |
| I4 | Profiles named Fast/Standard/Deep (§5.1) | `fast` is an agent preset; `standard` is the only gate policy (`gate.POLICY`) | Use distinct names (for example `execution.profile`), never reuse the preset or gate identifiers |
| I5 | Standard profile raises `max_iterations` to 12 and timeout to 45 min (§5.1) | Defaults 6 and 900 s; `DECIDED` in `tests/test_findings.py` fixes the loop's decisions | Changing defaults is a change to loop decisions: explicit request and regression tests, never for existing tasks |
| I6 | `EA-0001`, `CP-0001` IDs (§7.2) | IDs in use: `T-`, `AC-`, `A-`, `J-`, `U-`, `E-`/`E<n>-`, `RF-`, `WE-`, `GE-`, `EB-`, `H-` | `EA-` does not match `evidence.RECORD_ID` (`E\d*-\d{4,}`), but every reader of IDs must be checked; journal `A-` IDs are random and must not become attempt IDs |
| I7 | "Continuation" decisions | `gate.Action.CONTINUE` already means "the workflow goes on" | Name the new decision type differently in code and events |
| I8 | Attempt and continuation events (§11, P1) | `DECIDED` and coherence tests fix the events of the loop | With the feature off, no new event; with it on, new events only through `history.append` |
| I9 | Native resume through the command builder (§6.1) | `--resume`, `--session-id` etc. are in `ARGS_BLOCKED`; with no settings `agents.command` must stay argument-for-argument equal (`tests/test_agents.py`) | A resume form needs help output at a named CLI version or an authorized run, a separate builder path, and the reviewer guarantees re-applied |
| I10 | Session metadata from structured output (§6.1) | Parsers drop session IDs; stream formats unconfirmed by an authorized run | A new stream field needs a cited source; until then `native_resume` stays off |
| I11 | Stop and process tree before a second call (§9.3, T25) | Stop is POSIX-only; agents have no job object on Windows | On native Windows a continuation after a timeout cannot prove the previous tree stopped: pause, never continue |
| I12 | Quota during a continuation (T12, T13) | `recovery.on_failure` groups failures by phase and iteration; `note_progress` ends a streak only when phase or iteration change | Attempts of one step are one "point" for recovery; keep it, and decide whether a completed attempt counts as progress for the streak |
| I13 | `max_iterations_reached` vs `max_turns` (T21) | Already distinct (`core.py:1314`, `providers.py:397`) | Keep; budget pauses need their own kinds |
| I14 | Progress detector as PR 03 | Roadmap keeps it planned under 0.3.0, separate from 0.35 | D1 below |

## 6. Smallest integration points

**`execution.py`** — decides whether one more call of the same step is
allowed. Pure module: dataclasses and enums (`AttemptRecord`, outcome,
budget state), validation of an optional `execution` configuration section,
and `decide(state, record, budgets) -> decision`. It is called from one
place: a wrapper around the two `_invoke` calls in `run_task`
(`core.py:1220-1222` and `1255-1258`), inside the existing `try` so every
pause keeps going through `core.py:1322-1333`. Its record lives in
`state.json` under a new `execution` key, written with `_save`; `run_task`
stays the owner of phases and of `done`. With the feature off the wrapper
calls `_invoke` exactly once, as now.

**`continuation.py`** — prepares how to continue. Input: the task's
`task.md`, `gitops.changes`/`fingerprint`, `findings.brief`,
`provenance.brief`, the previous attempt's typed outcome. Output: an extra
prompt section appended by `_developer_prompt`/`_review_prompt` (the same
pattern as `brief`), never a command option. Native resume, if ever
validated, belongs to `agents.command` (the only builder) behind a flag that
`continuation.py` sets; `continuation.py` never builds argv.

**`progress.py`** — reads only: fingerprints before and after an attempt,
`Ledger` records that count (`provenance.recorded`), `findings` statuses,
history references. Returns a signal with its source and reliability. It
never writes to the ledger, never sets a finding's status, and is never read
by `gate.py`.

**Per-attempt output** — `run_dir` is computed once at `core.py:1213`;
passing `run_dir / <phase> / attempt-NNN` from the wrapper changes no
adapter code (`_step` already writes everything under the `run_dir` it is
given, as `test-draft-KK` shows). The four readers in 2.6 change in the same
step.

**Typed outcome** — mapped from `AgentFailure.kind` and the successful
return in the wrapper, not by changing the adapter's exceptions; the pause
kinds and their texts stay as they are.

Components to reuse, not duplicate: `agents.freeze`/`thaw`/`digest` (step
settings), `storage.save_json` (atomic checkpoint), `history.append`
(events), `activity.attempt` (observation), `recovery` (quota),
`gitops.fingerprint` (workspace before/after), `gate.evaluate` and
`core._complete` (completion).

## 7. Priority risks

| # | Risk | Priority |
|---|---|---|
| R1 | New attempt folders not seen by `TaskLock(force=True)` → `--unlock` while an agent still writes the worktree | P0 |
| R2 | Second call after a timeout on native Windows while the first tree may still run (no job object, no Stop) | P0 |
| R3 | Workflow-level turns/timeout not frozen: a continuation may run with a different policy than the step started with | P0 |
| R4 | Overwritten `developer.*`/`reviewer.*` logs: no attempt can be audited after a Resume (observed in 2.5) | P0 |
| R5 | Native resume enabled from unverified CLI behavior or by unblocking `--resume` as a custom argument | P0 |
| R6 | A budget or continuation path that reaches `done` or bypasses `parse_review` | P0 (guarded by `_complete`; keep tests) |
| R7 | Changing defaults (`max_iterations`, timeout) for existing tasks alters loop decisions (`DECIDED`) | P1 |
| R8 | New events or state keys break `test_coherence`/`test_history` invariants or `bundle.consistent` | P1 |
| R9 | Phase/version naming conflicts with the roadmap | P1 |
| R10 | Activity (changed files, agent text) counted as progress | P1 |
| R11 | `KEPT_RUNS = 60` trims old frozen records; many attempts must not multiply `agent_runs` entries | P2 |
| R12 | Baseline noise: the browser `legacy` check failed once in two full runs (section 4), and `test_bundle` needs `%TEMP%` to itself; a later failure of either is not evidence against a Phase 2.5 change until reproduced | P2 |

## 8. Adjustments to the plan

The specification defines five PRs (§14) and a PR 01 checklist (§17). No
"Step 01–15" list was found in the specification or the repository. The
decomposition below follows the PRs; it is a proposal to reconcile with the
step list in use.

| Step | Content | PR | Adjustment from this audit |
|---|---|---|---|
| 01 | `execution.py`: versioned records, outcome enum, budget state, validation; round-trip and corrupt/legacy tests; not wired | 01 | Names per I4/I6/I7; unknown or newer records refused, never defaulted |
| 02 | Optional `execution` configuration and precedence over `workflow.claude_max_turns` and agent `max_turns`; off by default | 01 | No new task override (I3); legacy configurations unchanged |
| 03 | Per-attempt folders for develop/review + `TaskLock`, `app._logs`, `bundle` readers | 01 | Must ship together (R1, I1); legacy flat files stay readable |
| 04 | Attempt record around `_invoke`: `STARTING` saved before launch, outcome after; feature flag | 01 | Decide D2 (freezing turns/timeout); no new event while off (I8) |
| 05 | Typed outcome mapping in the wrapper | 01/02 | Keep `AgentFailure.kind` and pause texts |
| 06 | `continuation.py` checkpoint brief, pure | 02 | Data only from task, fingerprint, ledger IDs, finding IDs |
| 07 | Automatic continuation of `develop` on Claude `error_max_turns` within budget | 02 | No iteration increment; quota stays with `recovery` (I12) |
| 08 | Reviewer continuation on truncated/invalid review, bounded | 02 | Never around `parse_review`/`settle_review` |
| 09 | Resume of an uncertain attempt (`STARTING`/`RUNNING` found): observe fingerprint and process, pause fail-closed | 02 | Native Windows: pause, never continue after a timeout (R2) |
| 10 | Native resume probe and session capture, off | 02 | Only from help at a named version or an authorized run (I9, I10) |
| 11 | `progress.py` signals, read-only | 03 | Resolve D1 first |
| 12 | Stagnation policy feeding `execution.decide` | 03 | Activity never verified progress (R10) |
| 13 | Report and dashboard API for attempts and budgets | 04 | Through `report.py`, `app.py`; nothing in the live snapshot that the engine reads |
| 14 | Execution Center UI | 04 | Actions only those the backend carries out |
| 15 | Hardening: `MUTATIONS`, coherence at all checkpoints, reliability scenarios, opt-in real-provider validation, `docs/REVIEW.md` | 05 | Real runs need explicit authorization |

Decisions needed before or at Step 01:

- **D1** — Relationship of "Phase 2.5" to the 0.3.0 Phase 1 / next phase and
  to the 0.35 proposal; whether PR 03 *is* Progress & Stagnation Detection.
- **D2** — Whether the step record freezes the workflow-level turns and
  timeout for steps started after the change (existing frozen records stay
  as they are).
- **D3** — The unit of the task budget for Codex (calls and wall time; tokens
  only as observed, `UNKNOWN` when absent).
- **D4** — Whether automatic continuation is allowed at all on native
  Windows before agent process trees are supervised there.

## 9. Handoff to Step 01

- **Goal:** `src/patchrondo/execution.py` with types and validation only,
  plus `tests/test_execution.py`. Not imported by `core.py`.
- **Contracts to define:** record schema version 1; attempt ID format
  distinct from every prefix in I6; outcome enum covering the spec's §7.4
  values and a mapping table from `AgentFailure.kind`; budget state with an
  explicit unit per value and `None`/`UNKNOWN` for unobserved usage;
  `load` that refuses unknown versions, wrong types (`bool` is not `int`)
  and newer records without replacing them.
- **Must not change:** `core.run_task`, `agents.command`, `providers`,
  `recovery`, `gate`, any pause kind, any event, `state.json` of existing
  tasks, the `DEFAULT_CONFIG`.
- **Checks for Step 01:** the new test module (including failure paths:
  corrupt, legacy, newer, out-of-range), `py_compile` with Python 3.11, the
  full suite as in section 4 only if a shared module changed;
  `tools/check_publication.py`. Protections and reliability are not affected
  by a module nothing imports; rerun them from Step 03/04.
- **Open inputs:** answers to D1-D4; the "Step 01–15" list in use, if it
  differs from section 8.
