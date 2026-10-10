# Architecture and handoff contract

## 1. Components

| Module | Responsibility |
|---|---|
| `cli.py` | CLI parsing, user input validation, project resolution, `init/new/run/resume/status/report/list/projects/index/search/ui/doctor` commands; no command opens the interface |
| `core.py` | Deterministic state machine and completion criteria; configuration and task-override validation |
| `providers.py` | Claude/Codex CLI adapters, structured JSON output, error classification and parsing of stated quota resets |
| `recovery.py` | Opt-in quota recovery: retry policy, the persisted plan and the foreground supervisor that waits without the task lock |
| `process.py` | Process stdin/stdout, timeouts, group cleanup and filtered environment |
| `gitops.py` | Separate worktrees and branches, Git status |
| `storage.py` | Atomic JSON state, private directories, locks and process PID checks |
| `report.py` | Auditable reports with files, tests, reviews and events |
| `rag.py` | Optional incremental SQLite FTS5 index and retrieval of repository excerpts |
| `workspace.py` | Project registry and global preferences of one home; in-place use of 0.1 homes |
| `procinfo.py` | Read-only check of a process by ID and start time: alive, dead or unknown |
| `runinfo.py` | Markers of attached `run`/`resume` processes and the process truth of one task |
| `doctor.py` | Availability and login of the provider CLIs without model calls |
| `commands.py` | Test command lines to and from argument arrays, with Windows or POSIX quoting |
| `app.py` | Application layer: every operation of the interface (projects, settings, tasks, bounded file views, run start and stop) |
| `live.py` | Snapshot and numbered change events |
| `ui.py` | HTTP only: loopback server, request checks, routing to `app.py`, static assets, event stream |
| `static/` | The interface: `index.html`, style sheets, ES modules and the two mascot images |

Models do not call each other directly. The orchestrator invokes each model and
stores state, feedback and evidence.

The layers depend in one direction: `static/` talks to `ui.py` over HTTP;
`ui.py` calls `app.py`; `app.py` calls `workspace.py`, `runinfo.py` and the
engine (`core.py`, `rag.py`); the engine knows nothing about projects or the
interface. A run is always a separate `patchrondo run` process, so the
interface cannot bypass the lock, the checkpoints or the consent rules.

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
- What the interface says about a process comes from a check of that process, never from `status` alone, and a failed check is reported as unknown (section 10).
- A project is trusted for tests only by explicit consent given for that project; registering or importing one never enables tests.

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

A task may carry `overrides` in its `state.json`, written once when it is
created: `workflow.max_iterations`, `workflow.agent_timeout_seconds`,
`workflow.test_timeout_seconds` and `recovery.enabled`, nothing else.
`core.validate_overrides` applies the configuration's own ranges, and
`core.effective_config` merges them over the project configuration in
`run_task` and in the recovery supervisor. A state without `overrides` (every
state written by 0.1) uses the project configuration unchanged. Tests, consent,
retry limits and `quota_only` cannot be overridden per task.

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

## 7. Interface and demo lifecycle

`ui.py` serves the interface with `ThreadingHTTPServer` on `127.0.0.1`. Every
request is checked for its Host header; every API request for the per-session
token; writes (POST, PATCH, DELETE) additionally for Origin when supplied, a
JSON content type and a body of at most 256 kB. The page is `static/index.html`
with a fresh nonce per response; scripts and style sheets carry that nonce and
are served from a dictionary of package files built at startup, so a request
can only name a file that was collected. Nothing is loaded from another origin.
Errors have one shape, `{"error", "code"}`, with 400, 401, 403, 404, 409, 413,
415 or 500.

| Route | Purpose |
|---|---|
| `GET /api/snapshot` | Everything the interface lists, with the sequence number events continue from |
| `GET /api/events?epoch=&since=` | Server-sent change events |
| `GET/PATCH /api/settings` | Global preferences |
| `POST /api/projects`, `/api/projects/import`, `/api/projects/inspect` | Register a repository, register a 0.1 home in place, check a path without registering it |
| `GET/PATCH/DELETE /api/projects/{id}`, `PATCH …/config`, `POST …/index` | Detail with the full configuration, rename, forget; edit configuration sections; update the retrieval index |
| `POST /api/projects/{id}/tasks`, `GET …/tasks/{task}`, `…/files`, `…/log` | Create a task; state and documents; changed files; a slice of one log |
| `POST …/tasks/{task}/run`, `…/stop` | Start `patchrondo run`; interrupt it |
| `POST /api/fs/list`, `GET /api/providers`, `POST /api/providers/refresh` | Folder names for the picker; cached CLI status |

A task is always addressed through its project, so one project cannot reach
another's tasks. Task creation delegates to `create_task`. Configuration edits
are merged into the saved file, checked as a whole by `core.validate_config`
and only then written atomically under a short file mutex; a section that an
older file lacks stays absent until it is edited. Test settings must arrive
complete (`enabled`, `trust_acknowledged`, `commands`), so consent is never
inherited from an earlier save. The editor round-trips argv arrays using
Windows C runtime quoting on Windows and POSIX quoting elsewhere. Neither form
invokes a shell. A client may send a `request_id` with a creation request; a
repeat with the same identifier returns the first result.

Each Run/Resume launches a separate `python -m patchrondo --home <project
state directory> run <task>` process with absolute import paths, so source-only
checkouts work after changing the child's working directory. The flags
`--auto-resume`, `--no-auto-resume` and `--unlock` are the only options the
interface can add. Existing task locks and phase checkpoints remain
authoritative. Before starting, `app.start_run` refuses a task that is done or
blocked, locked, already being started, or (for an automatic run) already
attended by a waiting process. It registers the process immediately and
reports startup failures detected during the first 1.5 seconds.

A condition variable guards `closing`, the count and set of pending starts and
the registered processes. Once closing begins, new Run requests return 409.
Shutdown ends the event streams, waits at most 30 seconds for admitted starts,
then returns `RunsAtExit(active, pending)`. Request threads are daemon threads;
silent connections have a 30-second timeout and do not delay shutdown. Started
runs continue independently.

The page is a set of ES modules without a build step: `dom.js` (element
creation from text only), `api.js`, `store.js` (snapshot, events,
reconnection), `router.js`, `ui.js` (components), `tasks.js` and `rondo.js`
(wording), and one module per view. A compiled framework was considered and
not adopted: the interface is about twenty modules, the package must stay
free of build tooling and runtime dependencies, the shipped files are the
reviewed files, and a strict nonce-based policy is simplest to keep with
nothing generated. `docs/DESIGN.md` describes the design system.

`tools/demo_dashboard.py` registers two temporary repositories with tests
disabled. It deletes the demo only after `serve()` returns normally and no
active process, pending start or `.run.lock` remains, unless `--keep` was
requested. A timeout, interruption or shutdown error preserves the files and
prints their location. A second Ctrl+C during shutdown returns exit code 130.
Preparation failures can be cleaned up because the interface has not yet
admitted any runs.

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

`tools/ui_e2e.py` starts the real server in its own process and drives a
headless Chrome or Edge through the DevTools protocol (`tools/browser.py`,
standard library only). Runs it starts are real `patchrondo run` processes
whose provider commands are routed to a synthetic executable; the runner
refuses to start while a real `claude` or `codex` is reachable. It compares
what the page shows with the files on disk.

`tools/recovery_checks.py` holds two further local checks. `protections` copies
`src/patchrondo`, `tests` and `tools` to a temporary directory, removes one
safeguard there, and runs the tests named for it against the copy: the
recovery and adapter tests, or interface tests for what the interface may
claim or start. An undetected removal, or a pattern that no longer matches the
sources, fails the check. `real-clock` reuses the reliability fixture with a
12-second stated reset and no virtual clock: it sends SIGINT to a waiting
runner, restarts it and compares the call times with the saved plan.

## 8. Quota recovery

Recovery adds no task status or phase. A task that meets a usage limit is still
`paused` at the failed phase with `last_error.kind == "quota"`; what changes is
an optional `recovery` section in `state.json` and who calls `run_task` next.

**Responsibilities.** `providers.quota_hint` only parses: it returns a reset
instant when the CLI output states one unambiguously, relative waits being
converted with the instant the failure was observed. Classification rules are
an ordered list in which authentication precedes quota, so text that names both
is never retried automatically. Both output streams of a failed CLI are read:
stderr decides the failure kind, stdout only when stderr is inconclusive (so
agent text cannot turn a login failure into a usage limit), and a reset stated
on either stream is kept. One form is provider-specific: Codex prints its reset
as local wall-clock time without a zone, so for Codex failures only that form
is read in the local zone of the host and rounded up a minute; in an hour
repeated by a clock change the later instant is taken. `AgentFailure` carries
`kind`, `provider`, `retry_at` and `retry_after_seconds`. An adapter may set
either reset form: `recovery.stated_reset` counts a wait from the failure time,
uses the later instant when both are present, and `last_error` always saves
the result as `retry_at`. Besides these structured fields, `last_error.message`
keeps the adapter's diagnostic, which for CLI failures is an unfiltered excerpt
of provider output of about 1,200 characters at most. `recovery.plan` is
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

**In the interface.** `run` and `resume` are the only supervisors, as before.
The interface starts them and reads what they persist; it adds no retry logic.
`app.start_run` passes `--auto-resume` or `--no-auto-resume` when the Run
dialog's switch was changed and nothing otherwise, so the configured default
(or the task's override) applies. Whether a process is waiting is answered by
`runinfo` (section 10), which is why a plan is shown as attended only while a
verified process with automatic resume is attached.

**Limits.** There is no daemon: a plan without a waiting process is only data,
and reports and the interface word it that way. A service that keeps plans
attended would be a separate, opt-in process started by the user that runs
`recovery.supervise` for tasks with a due plan, taking the same lock and
obeying the same limits; it is not part of this version, and the interface
must not suggest that one exists. Classification of quota
failures is textual and can be wrong; the retry limit is the protection.
Liveness of a recorded PID cannot be checked on native Windows, so a stale lock
there always needs a person. Reset formats of the real CLIs have been exercised
with simulated output only.

## 9. Home, projects and settings

```text
<home>/settings.json            global preferences
<home>/projects.json            registry
<home>/projects/<project-id>/   state directory of a project added in 0.2
```

The engine has always worked on one directory holding `config.json`, `tasks/`,
`worktrees/`, `index/` and `empty-hooks/`. In 0.1 that directory was the whole
home. In 0.2 it is the **state directory of a project**, and the engine is
given that directory wherever it used to be given the home. Nothing in
`core.py`, `recovery.py`, `rag.py`, `gitops.py` or `storage.py` knows about
projects, which is what keeps tasks, worktrees, indexes and locks of different
projects apart: they are in different directories.

**Registry.** `projects.json` is `{"version": 1, "legacy_root": …, "projects":
[{"id", "name", "home", "origin", "added_at"}]}`. `id` is `P-` plus eight
random hexadecimal digits and never changes. `home` is relative to the home
when the state directory is inside it and absolute otherwise. The repository
path is not copied into the registry: the project's own `config.json` remains
its only source. Writes go through a read-modify-write under a short
cross-process file mutex and an atomic replace.

**Origins.** `managed`: created by `Workspace.add` under `projects/<id>/` with
the regular `initialize`, so tests start disabled and untrusted. `legacy`: a
0.1 configuration at the root of the home, registered with `"home": "."` the
first time the registry is read by the interface or by `patchrondo projects`.
`imported`: another 0.1 home registered by absolute path. Registration writes
`projects.json` and nothing else. Worktrees are never moved, because Git
records their absolute paths in the repository.

**Removal and rollback.** Removing a project deletes its registry entry. A
removed root project is marked so that it is not registered again by itself.
The way back from any registration is to remove the entry; the way back from
all of 0.2 is to delete `projects.json` and `settings.json`, after which a 0.1
home is exactly what it was. A registration that fails after creating a state
directory removes that new, empty directory.

**Settings.** `settings.json` holds the default developer and reviewer, the
theme, the selected project and whether setup was completed; unknown keys are
rejected. Precedence for a new task's roles: the task form, then the project's
optional `agents` section, then the global defaults. Precedence for limits: a
task's `overrides`, then the project's `config.json`. No password, token or
API key is stored anywhere by PatchRondo.

**Command line.** Without `--project`, a configuration at the root of the home
is used directly and the registry is not read or written, so 0.1 scripts
behave as before. Otherwise the selected project is used, or the only one.
Commands that take a task ID look the task up in the registered projects.
`init --repo` creates the 0.1 layout on a home with no registry and adds a
project on a home that has one.

## 10. Process truth and live updates

`status` in `state.json` is a checkpoint, not a process. `running` stays
written after a crash, and a `scheduled` retry plan stays written after its
supervisor is gone. The interface therefore asks two further questions.

**Who holds the lock?** `TaskLock` writes its PID and, since 0.2, the start
token of its process (`procinfo.identity`): the start time from
`/proc/<pid>/stat` on Linux, the creation time from the Windows process API,
or the start time printed by `ps` elsewhere. `procinfo.state(pid, token)`
answers `alive` only when the PID exists and its token matches, `dead` when
the PID does not exist, is a zombie, or belongs to a later process, and
`unknown` when there is no token (a lock written by 0.1), access is denied or
the probe fails.

**What is attached?** `cli.py` wraps `run` and `resume` in `runinfo.attached`,
which writes `<task>/attached/<pid>.json` with the PID, the start token and
whether automatic resume is in effect, and removes it on exit. The marker
exists while the process waits for a retry without the lock, which is the case
the lock cannot show. A marker whose process is verified gone is deleted.
Children started by this interface are also known directly.

`runinfo.runtime` combines both with the saved state into one `activity`:

| Activity | Condition |
|---|---|
| `running` | lock present, holder or an attached process verified alive |
| `running_unverified` | lock present, holder cannot be verified |
| `stale_lock` | lock present, holder verified gone |
| `interrupted` | status `running`, no lock |
| `starting` | a process is attached and has not taken the lock yet |
| `waiting_retry` | plan `scheduled`, a verified process with automatic resume attached |
| `plan_unverified` | plan `scheduled`, an attached process that cannot be verified |
| `plan_only` | plan `scheduled`, no process |
| `recovery_stopped`, `paused`, `blocked`, `ready`, `done` | from the saved state, with no process |

It also says what is possible: `can_start`, `can_stop` (POSIX, a verified
process) and `can_unlock` (POSIX, a stale lock). These answers are for display
and for refusing duplicate starts. They never remove a lock: *Release lock and
resume* starts `patchrondo run --unlock`, and `TaskLock` then applies its own,
unchanged rule to the runner and to every recorded agent and test process. On
native Windows that rule cannot confirm that a process is gone, so the
interface does not offer the action there. *Stop* sends SIGINT to the verified
processes, which is what Ctrl+C does; a run started by the interface restores
the default interrupt handler if it inherited an ignored one.

**Live updates.** `live.Hub` rebuilds the view (`app.collect`) every half
second while a page is listening, at once after a write made through the API,
and on every snapshot request. State files are re-read when their time, size
or inode changed, and always while they are less than two seconds old, because
two saves can fall within one timestamp tick. Each difference from the
previous view becomes an event with the complete new value of one project,
task or global section, numbered within an `epoch` that changes when the
server restarts. `GET /api/snapshot` returns the view with its number;
`GET /api/events` then delivers later events and a heartbeat every ten
seconds. A client that asks for events the hub no longer holds, or with
another epoch, receives `resync` and loads a snapshot. The page applies an
event only when its number is the next one, reconnects with backoff, falls
back to polling snapshots if the stream cannot be kept open, and marks the
state as last confirmed at a given time while disconnected. The stream is read
with `fetch`, not `EventSource`, so the token stays in a header.

The task page takes its header from the summary in the stream and fetches the
task's detail whenever that summary changes. Logs are read by byte offset in
slices of at most 256 kB, by an identifier taken from the task's own file
listing.
