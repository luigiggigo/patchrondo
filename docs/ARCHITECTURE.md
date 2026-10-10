# Architecture and handoff contract

## 1. Components

| Module | Responsibility |
|---|---|
| `cli.py` | CLI parsing, user input validation, `init/new/run/resume/status/report/list/index/search/ui/doctor` commands |
| `core.py` | Deterministic state machine and completion criteria |
| `providers.py` | Claude/Codex CLI adapters, structured JSON output, error classification and parsing of stated quota resets |
| `recovery.py` | Opt-in quota recovery: retry policy, the persisted plan and the foreground supervisor that waits without the task lock |
| `process.py` | Process stdin/stdout, timeouts, group cleanup and filtered environment |
| `gitops.py` | Separate worktrees and branches, Git status |
| `storage.py` | Atomic JSON state, private directories, locks and process PID checks |
| `report.py` | Auditable reports with files, tests, reviews and events |
| `rag.py` | Optional incremental SQLite FTS5 index and retrieval of repository excerpts |
| `ui.py`, `static/dashboard.html`, `static/*.webp` | Loopback dashboard; token-protected JSON API to read state, create tasks, start `patchrondo run` processes and edit test settings; mascot images inlined into the page at startup |

Models do not call each other directly. The orchestrator invokes each model and
stores state, feedback and evidence.

## 2. Invariants

- A task is `DONE` **if and only if** review is approved, all tests pass and changes exist (or `allow_no_changes=true`).
- File fingerprints and configured test commands must match the test checkpoint before and after review. Changes during a pause require fresh tests. Ignored files, external services and submodule contents are not fingerprinted.
- The developer cannot award itself an `APPROVED` verdict.
- Claude reviewer uses read-only file tools; Codex reviewer uses the `read-only` sandbox and can execute commands allowed by it. Change requests are passed to the developer as data.
- State moves between atomically saved checkpoints: `develop` -> `test` -> `review` -> `complete`, or `develop` in the next iteration.
- If an agent subprocess fails, the loop stops in `PAUSED`. A failed test proceeds to review so the reviewer can provide useful feedback; test timeouts pause at the test phase.
- Only a `quota` pause can be retried without a person, only when recovery is active, and never before its persisted `resume_at`. No task lock is held while waiting, and no lock is ever removed automatically (section 8).
- The orchestrator does not automatically merge, push, commit or install dependencies.
- The task branch is separate from main. The external test runner executes project commands on the host and requires explicit consent.

## 3. Review contract

The reviewer returns only an object:

```json
{
  "verdict": "CHANGES_REQUESTED",
  "summary": "JWT expiration is not checked correctly",
  "issues": [
    {
      "severity": "high",
      "path": "src/auth.py",
      "description": "Check exp against UTC and add expired-token tests"
    }
  ]
}
```

Allowed verdicts are `APPROVED`, `CHANGES_REQUESTED` and `BLOCKED`. An approval
containing `high` or `critical` issues is automatically downgraded. The orchestrator
always applies its own test gate, independently of the model's opinion.

The developer produces a Markdown handoff with *Changes*, *Decisions*, *Remaining
concerns* and *Suggested tests*. `task.md`, `handoff.md`, `feedback.md` and test
results are supplied through prompts and files; complete historical conversations
are not concatenated. Each iteration keeps prompts, logs and replies for auditing.

## 4. Resume, idempotency and limitations

A phase is complete only when `state.json` records the next `phase`. After an
abrupt termination, unfinished actions may be **repeated**: the model sees the
worktree with partial changes. Exactly-once execution is not guaranteed within a
phase, so work should be idempotent with respect to file contents.

If the runner dies with `SIGKILL`, child processes may continue working. `TaskLock`
and `*.active-process.json` prevent forced unlocking while recorded PIDs are
active. This does not replace OS supervision or prevent malicious processes from
creating additional children.

Configurable parameters include the maximum number of iterations, agent and test
timeouts, Claude turns, `allow_no_changes`, explicit trust in the configured tests
and the `recovery` limits described in section 8.

## 5. Permissions and threat model

The MVP is designed for **trusted local repositories** and credentials managed by
the official CLIs. It does not support hostile code running on sensitive hosts.
Git isolation avoids conflicting edits; it does not isolate filesystem access,
network access or host resources. Unfamiliar code requires a VM/container runner
with a dedicated identity, controlled network access, minimal filesystem exposure
and an external secret manager.

Claude uses `--restricted` with only the required file read/write tools. Codex
uses its native `workspace-write` or `read-only` sandbox. The reviewer has fewer
permissions than the developer. The test runner remains **a host process**, so
tests must not be enabled for untrusted code.

## 6. Retrieval layer

**Authoritative memory stays separate from retrieval:**

1. `state.json`, requirements and verified results remain authoritative. Retrieved chunks are labelled non-authoritative and untrusted, and cannot override them.
2. `rag.py` keeps one SQLite database in `<home>/index/rag.sqlite3`, outside the repository, so it never affects worktree fingerprints. Tables: `files(root, path, size, mtime_ns, sha)` maps each worktree file to a content hash; `blobs` and `chunk_map(path, sha, start_line, end_line)` hold chunks keyed by `(path, sha)`; `chunks_fts` is an FTS5 table with `path`, `symbol`, `body` and `parts` (identifier subwords) columns, tokenized with `porter unicode61 tokenchars '_'` and ranked by `bm25(3, 5, 1, 0.5)`.
3. Updates list files with `git ls-files --cached --others --exclude-standard`, skip ineligible files (secrets, binaries, generated files, more than 512 KB) and re-read only files whose size or `mtime_ns` changed. Files modified less than two seconds before an update are rehashed on the next one. Chunks no longer referenced by any worktree, including removed worktrees, are garbage-collected in the same transaction.
4. Queries OR together up to 48 distinct terms from the task, feedback or handoff and changed paths. Matches are restricted to the current worktree before the top 300 candidates are taken, so chunks of other worktrees in the shared index cannot crowd them out. Candidates are limited to two per file and rendered within `rag.max_chunks` and `rag.max_chars`.
5. The index is a cache: a schema version mismatch rebuilds it, and deleting it is always safe. Retrieval errors are logged as `retrieval_failed` events and never pause a task.

Vector embeddings are deliberately absent: they would add runtime dependencies
or network calls. Consider them as a reranking stage only after measuring that
lexical search is insufficient.

## 7. Dashboard and demo lifecycle

`ui.py` serves the bundled dashboard with `ThreadingHTTPServer` on `127.0.0.1`.
The API checks Host headers and a per-session token; writes additionally check
Origin when supplied and require JSON. Task creation delegates to `create_task`;
test settings retain the core's explicit boolean consent validation. The editor
round-trips argv arrays using Windows C runtime quoting on Windows and POSIX
quoting elsewhere. Neither form invokes a shell.

Each Run/Resume launches a separate `python -m patchrondo ... run` process with
absolute import paths, so source-only checkouts work after changing the child's
working directory to the state directory. Existing task locks and phase
checkpoints remain authoritative. The dashboard registers the process immediately
and reports startup failures detected during the first 1.5 seconds.

A condition variable guards `closing`, the count of pending starts and the
registered processes. Once closing begins, new Run requests return 409. Shutdown
waits at most 30 seconds for admitted starts, then returns `RunsAtExit(active,
pending)`. Request threads are daemon threads; silent connections have a
30-second timeout and do not delay shutdown. Started runs continue independently.

`tools/demo_dashboard.py` initializes a temporary repository with tests disabled.
It deletes the demo only after `serve()` returns normally and no active process,
pending start or `.run.lock` remains, unless `--keep` was requested. A timeout,
interruption or shutdown error preserves the files and prints their location.
A second Ctrl+C during shutdown returns exit code 130. Preparation failures can
be cleaned up because the dashboard has not yet admitted any runs.

`tools/provider_e2e.py` invokes authenticated provider CLIs only with
`--authorize-provider-calls`. It creates a temporary repository and state
directory, runs `run_task` once per pairing and inspects the saved state: the
developer's handoff and file, unchanged Git history, a parsed review and an
unchanged worktree fingerprint after review. Fixture tests stay disabled unless
`--run-fixture-tests` is given. Without that flag, agent-written code is checked
statically and never imported. Files are deleted only after every check passes.

`tools/reliability_e2e.py` exercises the same adapters with an exclusive route
to local synthetic Python executables. Each runner removes provider CLI
directories from PATH and confirms both CLI lookups fail before starting.
Each scenario uses a temporary repository and private state in sibling
directories. Separate runner processes invoke the CLI entry point. The driver
injects review feedback, developer/reviewer quota errors, SIGINT during review
and SIGKILL at the persisted review checkpoint before a reviewer child starts.
It verifies process cleanup, lock recovery and resume without repeated completed
stages. The crash case does not simulate a runner killed while an agent survives.
In those scenarios resume is initiated by the driver. The `auto-resume` scenario
instead starts one runner with `--auto-resume`: a synthetic reviewer reports
`Retry after 300 seconds`, and the runner's planned wait advances a virtual
clock, so the check covers parsing, the persisted plan, the lock-free wait and
the retry without real waiting.
Reports contain objective fixture checks and timing measurements; fixture state
and transcripts are deleted on success and retained outside the repo on failure.

## 8. Quota recovery

Recovery adds no task status or phase. A task that meets a usage limit is still
`paused` at the failed phase with `last_error.kind == "quota"`; what changes is
an optional `recovery` section in `state.json` and who calls `run_task` next.

**Responsibilities.** `providers.quota_hint` only parses: it returns a reset
instant when the CLI output states one unambiguously, relative waits being
converted with the instant the failure was observed. `AgentFailure` carries
`kind`, `provider`, `retry_at` and `retry_after_seconds`; `last_error` stores
those structured values and never more log text than before. `recovery.plan` is
a pure function that turns the failure count, the hint and the configured
limits into "retry at T" or "stop, because R". `run_task` applies it while it
holds `TaskLock`. `recovery.supervise` is the loop around `run_task` used by
`run` and `resume`: it sleeps between runs and owns no state of its own.

**Persisted section.**

```json
"recovery": {
  "status": "scheduled",
  "consecutive_failures": 1,
  "max_consecutive_retries": 3,
  "first_failure_at": "2026-10-10T14:00:00+00:00",
  "resume_at": "2026-10-10T17:00:30+00:00",
  "phase": "review",
  "iteration": 2,
  "schedule_source": "provider_reset",
  "provider": "codex",
  "stop_reason": null
}
```

`status` is `scheduled` (a retry is planned), `retrying` (a planned retry is
running under the lock) or `stopped` (automatic recovery ended; `stop_reason`
says why). The section is `null` when no recovery is in progress, and absent in
states written by earlier versions. Timestamps are UTC with an explicit offset;
a value without one is rejected rather than interpreted.

**Transitions, all under the lock and saved with the existing atomic write.**

| When | Effect on `recovery` | Event |
|---|---|---|
| Quota failure, recovery active, limits allow | `scheduled`, saved in the same write as the pause | `recovery_scheduled` |
| Quota failure, limits exhausted or reset beyond the budget | `stopped` with `max_consecutive_retries`, `wait_budget_exceeded` or `reset_beyond_budget` | `recovery_stopped` |
| A supervisor starts waiting | unchanged; the lock is taken only for this journal write | `recovery_wait_started` |
| Run starts, plan due and still a quota pause | `retrying` | `recovery_retry_started` |
| Run starts, plan not due | nothing is written and no provider is called | — |
| Checkpoint with a new phase or iteration, or `done`/`blocked` | `null`; the failure streak ends | `recovery_completed` |
| Same phase fails on quota again | `scheduled` with the count incremented, or `stopped` | as above |
| Any other failure during a retry | `stopped` with that failure kind | `recovery_stopped` |
| Run with recovery inactive (manual resume) | `null` | `recovery_cancelled` if a plan was pending |
| Plan unreadable or the pause is no longer a quota failure | `stopped` with `invalid_schedule`; no call | `recovery_stopped` |

`run_task` evaluates the plan before it clears `last_error`, so a retry cannot
start early because the evidence was already erased. A run that was requested
explicitly while recovery is active honors a pending plan in the same way; for
a quota pause without a plan it first derives one from `last_error`, so a known
reset still holds after a restart or after a pause recorded without recovery.
The retry time is the later of the backoff for that attempt and the stated
reset plus the safety margin, rounded up to a whole second.

**Locks and concurrency.** The supervisor never sleeps inside `TaskLock`. When
it wakes, it calls `run_task` with the `resume_at` it waited for. Under the
lock, `run_task` rereads the state and proceeds only if that exact plan is
still persisted, still a quota pause and due. Two supervisors for one task are
therefore safe: the lock prevents simultaneous provider calls, and the one that
arrives after the other has rescheduled finds a different `resume_at`, makes no
call and waits for the new plan. A supervisor that meets the lock when its retry
is due stops with an error: it cannot tell a live run from a dead runner, and
it never unlocks. `--unlock` is passed only to the first, explicitly requested
run. While waiting, the supervisor reads `state.json` without the lock only to
end a wait whose plan is gone; that read never authorizes a call.

**Bounds.** Consecutive failures at the same phase and iteration are limited by
`max_consecutive_retries`; the time from the first failure of a streak to a
planned retry by `max_total_wait_seconds`. Progress is limited by
`workflow.max_iterations`, so the number of runs one supervisor can start is
finite, and the loop also carries an explicit cycle limit derived from both.

**Limits.** There is no daemon: a plan without a waiting process is only data,
and reports and the dashboard word it that way. Classification of quota
failures is textual and can be wrong; the retry limit is the protection.
Liveness of a recorded PID cannot be checked on native Windows, so a stale lock
there always needs a person. Reset formats of the real CLIs have been exercised
with simulated output only.
