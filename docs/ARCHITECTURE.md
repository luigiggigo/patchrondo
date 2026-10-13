# Architecture and handoff contract

## 1. Components

| Module | Responsibility |
|---|---|
| `cli.py` | CLI parsing, user input validation, project resolution, `init/new/run/resume/status/report/list/projects/index/search/ui/doctor` commands; no command opens the interface |
| `core.py` | Deterministic state machine and completion criteria; configuration and task-override validation |
| `providers.py` | Claude/Codex CLI adapters, structured JSON output, error classification and parsing of stated quota resets; asks the CLIs to stream and falls back when they refuse; starts each step with its agent settings |
| `agents.py` | Agent settings: the typed table of what each CLI can be started with, validation, the three levels and their resolution, the frozen record of a step, the command of a step, presets, model lists and the checks of *Test configuration* |
| `streams.py` | Parsers of the two CLIs' activity streams into provider-independent events; drop internal reasoning; keep a bounded transcript |
| `activity.py` | The activity journal of a task: bounded, redacted, append-only events, and paging through them by cursor |
| `recovery.py` | Opt-in quota recovery: retry policy, the persisted plan and the foreground supervisor that waits without the task lock |
| `process.py` | Process stdin/stdout, timeouts, group cleanup and filtered environment; optional observers that see output while the process runs |
| `gitops.py` | Separate worktrees and branches, Git status |
| `storage.py` | Atomic JSON state, private directories, locks and process PID checks |
| `report.py` | Auditable reports with files, tests, reviews and events |
| `rag.py` | Optional incremental SQLite FTS5 index and retrieval of repository excerpts |
| `workspace.py` | Project registry and global preferences of one home; in-place use of 0.1 homes |
| `procinfo.py` | Read-only check of a process by ID and start time: alive, dead or unknown |
| `runinfo.py` | Markers of attached `run`/`resume` processes and the process truth of one task |
| `doctor.py` | Availability and login of the provider CLIs without model calls; what an installed CLI offers (options of its help, Codex model catalog and profiles) |
| `environment.py` | The environment check before any provider call: agents, Git, review sandbox; one saved record per check |
| `preflight.py` | The reviewer preflight: the probe run in the Codex sandbox self-check and the judgment of its answers against the disk |
| `winacl.py` | Windows access control lists of the folders agents read: inspect, grant narrowly, restore (`ctypes`, Windows only) |
| `repair.py` | The catalogue of environment problems and the two repairs: one unattended, one that needs the user's decision |
| `commands.py` | Test command lines to and from argument arrays, with Windows or POSIX quoting |
| `app.py` | Application layer: every operation of the interface (projects, settings, tasks, bounded file views, run start and stop) |
| `live.py` | Snapshot and numbered change events |
| `ui.py` | HTTP only: loopback server, request checks, routing to `app.py`, static assets, the event stream and the activity stream of a task |
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
- Tests that are not enabled are a missing prerequisite, not work for the reviewer: the loop pauses at the `test` checkpoint with `tests_disabled` before any reviewer call, also from a saved `review` checkpoint whose tests were disabled afterwards (the saved results are replaced by a skipped entry). A run started while that pause and the missing consent both still hold returns the saved state unchanged: no event, no environment check, no provider call. With tests enabled, Resume runs them and then the review.
- Only a `quota` pause can be retried without a person, only when recovery is active, and never before its persisted `resume_at`. No task lock is held while waiting, and no lock is ever removed automatically (section 8).
- The orchestrator does not automatically merge, push, commit or install dependencies.
- The task branch is separate from main. The external test runner executes project commands on the host and requires explicit consent.
- What the interface says about a process comes from a check of that process, never from `status` alone, and a failed check is reported as unknown (section 10).
- Live activity is an observation. The engine never reads the journal, what an agent says while it works is never its answer, and failing to observe or to record never fails a step (section 11).
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

The state directory holds two kinds of data. Task state, transcripts, the
activity journal, locks, the retry plan and the configuration are private:
nothing in the program gives a sandbox access to them. Worktrees are what
agents read and write. On Windows the only access entries PatchRondo ever adds
are for the worktrees and for the way to them; [section 12](#12-environment-check-reviewer-preflight-and-repair)
has the policy.

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
| `GET …/tasks/{task}/activity?cursor=&before=&limit=` | A page of the task's activity journal: its end, or what follows or precedes a cursor |
| `GET …/tasks/{task}/activity/stream?cursor=` | Server-sent events with what is appended to that journal |
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
developer's handoff and file, unchanged Git history, the developer's activity
and, when the run reaches the review, a parsed review, an unchanged worktree
fingerprint after it and the reviewer's activity. Fixture tests stay disabled
unless `--run-fixture-tests` is given. Without that flag the loop pauses at the
missing test consent after one developer call: the reviewer's checks are
reported as not exercised (`None`, neither passed nor failed), a review seen in
that run fails the check, and agent-written code is checked statically and
never imported. Files are deleted only when no check failed.

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
what the page shows with the files on disk. Its synthetic executable prints an
activity stream line by line when it is started with the streaming options,
with reasoning lines that must never be shown, and refuses those options when
a scenario asks for a CLI without streaming.

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
cross-process file mutex and an atomic replace. `Workspace.add` and
`Workspace.import_home` refuse a repository or a state directory that is
already registered, and make that check twice: before the mutex, to answer
without waiting, and again on the registry read under it, because another
registration can complete between the first check and the mutex.

**Origins.** `managed`: created by `Workspace.add` under `projects/<id>/` with
the regular `initialize`, so tests start disabled and untrusted. `legacy`: a
0.1 configuration at the root of the home, registered with `"home": "."` the
first time the registry is read by the interface or by `patchrondo projects`.
`imported`: another 0.1 home registered by absolute path. Registration writes
`projects.json` and nothing else. Worktrees are never moved, because Git
records their absolute paths in the repository.

**Removal and rollback.** Removing a project deletes its registry entry. It is
refused while one of its tasks holds a lock, has a verified process attached,
or has a recorded process that cannot be verified (section 10). A
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

**Command line.** Without `--project`, a home with a configuration at its root
and no `projects.json` is used directly and no registry is written, so 0.1
scripts behave as before. Once a registry exists it decides, in a 0.1 home as
in any other: the selected project, or the only registered one; several
projects with none selected is an error that names `--project`, and an
unreadable registry is reported instead of being bypassed. The root
configuration is used without a registry entry only while no project is
registered. Commands that take a task ID look the task up in the registered
projects, then in a root configuration that was removed from the registry.
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
| `plan_unverified` | plan `scheduled`, an attached process with automatic resume that cannot be verified |
| `plan_only` | plan `scheduled`, no process |
| `recovery_stopped`, `paused`, `blocked`, `ready`, `done` | from the saved state, with no process |

It also says what is possible: `can_start`, `can_stop` (POSIX, a verified
process) and `can_unlock` (POSIX, a stale lock).

**A process that cannot be verified is not taken as gone.** It is not shown as
running or waiting (`active` stays false), and its marker is never deleted on
a guess. `runtime` lists such processes in `unverified` and sets
`waiting_unverified` for `plan_unverified`. Until a person settles it, two
actions that would assume the process is gone are refused with status 409: a
second run with automatic recovery for that task, and removing its project
from the registry. A run without automatic recovery stays possible, as beside
a verified waiting process: it cancels the saved plan, and a process that is
still waiting stops when it finds the plan changed or the lock taken. The marker disappears by
itself once the process is verified gone; when it never can be (a process ID
reused by a program this user may not inspect), the task page and the refusal
name the file to delete after checking, as for a stale lock on native Windows.

These answers are for display and for refusing duplicate starts. They never
remove a lock: *Release lock and resume* starts `patchrondo run --unlock`, and `TaskLock` then applies its own,
unchanged rule to the runner and to every recorded agent and test process. On
native Windows that rule cannot confirm that a process is gone, so the
interface does not offer the action there. *Stop* sends SIGINT to the verified
processes, which is what Ctrl+C does; a run started by the interface restores
the default interrupt handler if it inherited an ignored one.

**Reading beside a run.** The interface reads `state.json` and `.run.lock`
while a run replaces the first and deletes the second. A reader that finds a
file unreadable for a moment skips it until the next scan. The writer must not
fail instead: Windows refuses to replace or delete a file that another
process has open, so on Windows `storage.atomic_text` and the release of
`TaskLock` repeat a refused operation for up to two seconds
(`SHARING_RETRY_SECONDS`) before reporting the error. A refused save leaves
the previous file in place. Other systems allow both operations and never
wait.

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
another epoch, receives `resync` and loads a snapshot. With no listener and
no request for fifteen seconds the scanning thread ends. It decides that and
withdraws itself in one section under the hub's lock, the same one in which a
listener is counted and a missing thread is started, so a listener that
arrives at that moment starts a new thread. The page applies an
event only when its number is the next one, reconnects with backoff, falls
back to polling snapshots if the stream cannot be kept open, and marks the
state as last confirmed at a given time while disconnected. The stream is read
with `fetch`, not `EventSource`, so the token stays in a header.

The task page takes its header from the summary in the stream and fetches the
task's detail whenever that summary changes. Logs are read by byte offset in
slices of at most 256 kB, by an identifier taken from the task's own file
listing. The log viewer numbers each opening of a file and sends one request
at a time for it; an answer is used only if no file has been opened since its
request was sent, so a late answer cannot reach the view of another file.

## 11. Live activity

A step used to be visible when it ended. Now the same step is observed while
it runs. Nothing below changes how a step is started, bounded or judged.

**Reading output while the process runs.** `process.execute` takes two
optional observers, `on_stdout` and `on_stderr`. Without them it runs exactly
as before, with both streams redirected to temporary files. With them the
streams are pipes: one thread per stream copies each chunk into the same
temporary file and hands it to the observer, and a third thread writes the
prompt to standard input, so neither a full pipe nor a large prompt can block
the other side. The returned `Result` is built from the files as before, with
the same bounds. Timeout, group termination, the PID file and re-raising of an
interrupt are the same code paths. An observer that raises is not called
again. If a descendant keeps a pipe open after the process has exited, reading
stops two seconds after the exit. `LineReader` turns chunks of any shape into
lines: bytes wait for their newline, so a line or a UTF-8 sequence cut between
two chunks is decoded whole, and a line longer than 2 MB is dropped and
counted.

**Asking the CLIs to stream.** `OfficialCLI` starts Claude Code with
`--output-format stream-json --verbose` and Codex with `codex exec --json`;
every other argument is unchanged. `--include-partial-messages` is not used:
it adds a line per token, and those lines also carry thinking deltas. The
options were read from the CLIs' own help (Claude Code 2.1.291 and 2.1.295,
Codex CLI 0.160.1), the Claude line types from its documentation and the Codex
event types from `codex-rs/exec/src/exec_events.rs` at `rust-v0.160.1`; none
of it has been confirmed with an authorized real run. A CLI that exits with an
error naming one of these options, before it printed a single event, is
started once more with the previous arguments. Both CLIs check their arguments
before they open a session, so the refused start reached no model.
`PATCHRONDO_LIVE_ACTIVITY=0` selects the previous arguments from the start. In
both cases the journal says why the step has no activity.

**Normalizing.** `streams.py` has one parser per CLI. Each emits events of a
few types and nothing provider-specific:

| Type | Meaning | From Claude Code | From Codex |
|---|---|---|---|
| `message` | public text of the agent | `text` blocks of `assistant` | `agent_message` items |
| `tool` | a tool call: `started`, then `completed`, `failed` or `declined` | `tool_use` blocks and their `tool_result` | `command_execution`, `mcp_tool_call`, `collab_tool_call`, `web_search` items |
| `file` | a changed file | a successful `Write`, `Edit`, `MultiEdit` or `NotebookEdit` | `file_change` items |
| `test` | a test command of the project and its result | written by the engine | written by the engine |
| `error` | an error the CLI or the engine reported | an `is_error` result | `error`, `turn.failed`, `error` items |
| `status` | session opened, API retry, plan, end of a turn, the CLI's result line, notices | `system` lines, `result` | `thread.started`, `turn.completed`, `todo_list` |
| `completion` | how the attempt ended: `completed`, `failed`, `timeout`, `interrupted` | written by the engine | written by the engine |

Claude `thinking` and `redacted_thinking` blocks, Codex `reasoning` items and
partial deltas are dropped. Lines of an unknown type are named in the
transcript and not shown. The result of a successful Claude tool is recorded
by its size only, because it is file content more often than not; the output
of a Codex command by its last 1500 characters. Paths inside the worktree are
shown relative to it.

**What decides.** The parsers decide nothing. The answer of a step is still
Claude's `result` object (`structured_output` for a review) or the file Codex
writes with `--output-last-message`, and `core.parse_review` validates a
review as before. A stream that contained events but no result is an
`empty_output` failure, never an answer assembled from messages. A reviewer's
message that contains a verdict word or a `"verdict"` key is recorded as
withheld, and its `StructuredOutput` tool call without its content. Failure
classification uses what the CLI reported as its error together with standard
error. When that diagnostic is available, the transcript cannot change its
classification or supply a reset time: activity messages and event names are
not failure diagnostics. Without a reported error, the previous standard-error
and standard-output fallback still applies. The step's `stdout` log is the
transcript when a stream was read: bounded like the captured output, and
without reasoning.

**The journal.** `activity.Journal` appends to `<task>/activity.jsonl`, created
with mode 0600. The first line identifies the journal (`J-` and twelve
hexadecimal digits). Every other line is one event with `v`, `seq`, `at` (UTC,
milliseconds), `task`, `iteration`, `role`, `provider`, `attempt` and `type`,
then the fields of its type. `seq` grows by one within the task: a new attempt
reads the last number and continues, and a line left unfinished by a crash is
closed with a newline first, so readers skip it and nothing is numbered twice.
An attempt is one call of an agent, or one test stage; `core._invoke` and the
test stage wrap it in `activity.attempt`, which records its start, any
exception as an `error`, and its `completion`, and re-raises the exception
unchanged. Text is clipped (4000 characters for a message or an output, 400
for other fields), lists to 40 entries, a line to 24 kB, and an attempt to
5000 events, after which only its error and completion are recorded with the
number of events left out. `redact` masks values that look like credentials;
it recognizes common shapes only. Every operation on the journal catches its
own failures and switches the journal off: a step never fails because it
could not be recorded. The engine does not read the journal.

**Reading.** `activity.read` returns a page: the last events, the events
after a cursor, or the events before one. A cursor is the journal's identity
and a byte offset at the start of a line. Only complete lines are read, in
pages of at most 300 events and 1 MB. A cursor from another journal, beyond
the end of the file, or inside a line is answered with the end of the journal
and `reset`. Because the cursor describes the file and not the server, a
client continues exactly where it stopped after a reconnection and after a
restart of the interface.

**Transport.** `GET …/activity` serves pages and `GET …/activity/stream`
server-sent events: the stream looks at the journal five times a second and
sends each batch of new events with the cursor that follows them, a heartbeat
every ten seconds, and `reset` when its cursor no longer fits. Both go through
the checks of every API route. Activity never enters the snapshot or the
global event stream: a task's summary does not change when its journal grows.

**The page.** `js/live.js` loads the end of the journal, then follows the
stream from that cursor. It ignores an event whose `seq` it has already shown,
reconnects with backoff from its cursor, loads the end again after `reset`,
falls back to asking for pages when the stream cannot be kept open, and
releases its connection when the tab has been hidden for thirty seconds.
`js/views/live.js` appends one row per event and never rebuilds the list; the
started and finished events of one tool call or test command share a row.
The header of the view takes the mascot's mood and the words "is developing",
"is reviewing" from `task.runtime` and the saved phase, and only the latest
event and the elapsed time from the journal. An attempt without a recorded end
is shown as running only while the backend verifies a running process.

## 12. Environment check, reviewer preflight and repair

A run used to learn that its environment was wrong from a failed agent call.
Now it checks first. Nothing below changes how a step is started or judged,
and none of it calls a model.

**Where it runs.** `run_task` calls `_environment` under the task lock: once
after the run is marked `running` and before the loop, and again in the
review phase after the test context was checked and before `review_started`.
When the tests are not enabled there is no review: `_test_stage` then makes
that second call itself, after saving the skipped tests and before it raises
`tests_disabled`, so an access problem of the reviewer is reported first and
is not found only after the consent.
That holds for a first run, a resume, a run after a crash and an automatic
quota retry alike, because they are all `run_task`. The one run that checks
nothing is a resume of a `tests_disabled` pause while the tests are still not
enabled: it returns the saved state. The second call passes
`reviewer_only`: the agents stage is not repeated, and a ready record is
returned as it is while its `signature` still holds. The signature is a digest
of the platform, the reviewer, the worktree path, the identity of the Codex
executable (path, size, modification time) and the access lists of the folders
the policy covers. A record that is not ready, or was made with simulated
providers when the run is real, is never reused.

**What "real" means.** Two of the checks start a real CLI: the login status
(`doctor.check`) and the sandbox self-check. They belong to runs that start
real CLIs. `run_task` computes `live = adapter is None and not
providers.simulated()`: a run with a given adapter is not live, and neither is
one where `patchrondo.providers.execute` was replaced, which is how tests and
the synthetic runners simulate providers. `providers.probe` is the only other
function that starts a provider CLI; it accepts `codex sandbox …` and nothing
else, and refuses while providers are simulated. Checks that could not run are
recorded as `skipped` with the reason, never as `passed`.

**Stages** (`environment.Manager.check`), stopping at the first problem:

| Stage | Checks | Problem codes |
|---|---|---|
| `agents` | `cli.<provider>` for the developer and the reviewer: installed, logged in | `cli_missing`, `authentication` |
| `project` | `git.ownership`: `git rev-parse HEAD` in the worktree | `git_dubious_ownership` (any other Git failure is raised as before) |
| `sandbox` | `acl.worktree` (Windows), then the nine probe checks | `sandbox_access_denied`, `sandbox_cwd_unavailable`, `sandbox_not_read_only`, `sandbox_unverified`, `sandbox_unavailable`, `sandbox_git_failed` |

A login that the status command could not confirm either way does not stop the
run. The sandbox stage applies to a Codex reviewer; a Claude reviewer is
limited by its tool list and has no operating-system sandbox to probe.

**The record.** Saved by the engine in `state.json` under `environment`, with
the pause, whatever the outcome:

```text
status                ready | repairable | blocked   (checking while a check runs)
error_kind, cause     stable code of the problem and the exact finding behind it
message               one sentence for a person; no system error text
provider, repair      the CLI concerned; the repair that addresses the problem
repair_available      a repair exists that was not applied, or can be tried again
repair_applied        a repair was applied during this check
requires_user_action  nothing more happens until the user acts
checks                [{id, stage, label, status: passed|failed|skipped, detail}]
repairs               [{repair, at, outcome, changed: [{path, added, previous_sddl, …}], error}]
signature, live       what a reuse depends on
```

`last_error` gets the same `kind` and `message` and `"source": "environment"`,
through the pause path every other failure takes: `_failure`,
`recovery.on_failure` (a non-quota failure schedules nothing) and the report.
History gets `environment_repaired` and `environment_blocked`; a check that
finds nothing to do adds no event. The activity journal gets an attempt with
the role `environment` when the check is live, repairs something or finds a
problem. While a stage runs, the record is `{"status": "checking", "stage":
…}`; if the check cannot finish, the record saved before it is put back, so an
unfinished check is never read as a result.

**The preflight probe.** On native Windows, and only when the help of the
installed CLI lists the options, `preflight.Sandbox` runs

```text
codex sandbox -P :read-only --include-managed-config -C <worktree> -- powershell.exe -NoProfile -NonInteractive -EncodedCommand <base64>
codex sandbox -P :read-only --include-managed-config -C <worktree> -- cmd.exe /d /u /c cd
```

The syntax is the one printed by `codex sandbox --help` of codex-cli 0.161.0
on native Windows, read and run there on October 11, 2026. The first command
is the shell an agent's commands run in; the second is a plain process, whose
directory is the one the sandbox gave it. Both are argument arrays. Paths
reach PowerShell only inside an encoded script, as single-quoted literals with
every quote character PowerShell recognizes doubled; a path with a control
character is refused. That sandbox starts PowerShell in constrained language
mode, so the script calls cmdlets only, compares paths itself and answers in
ASCII lines `PR key=value`.

| Check | Passed when |
|---|---|
| `sandbox.start` | the script printed its begin and end marker with the token of this probe |
| `process.cwd` | the plain process reports the worktree (skipped when it could not be read) |
| `shell.cwd` | PowerShell's location equals the worktree |
| `read.absolute`, `read.relative` | the first line of the worktree's `.git` link file, read both ways, equals the one on disk |
| `git.run` | `git rev-parse HEAD` exits 0 with the commit Git names outside the sandbox |
| `git.metadata` | `git status --porcelain` exits 0 from the shell's own directory |
| `write.canary` | a file PatchRondo created beside the worktree was seen, overwriting and deleting were attempted, and afterwards it is byte for byte what was written |
| `write.create` | creating a file in the worktree was attempted and no such file exists |

The canary lives in `<state>/worktrees/.patchrondo-preflight-<token>/`, so the
worktree's Git status is not disturbed; it is removed afterwards, and one left
by a probe that died is removed by a later probe. What the script prints about
its writes is not evidence: `sandbox_not_read_only` is decided by the files,
and `sandbox_unverified` means that no refusal was demonstrated. The target
repository's tests are never run by a check.

**Access policy on Windows** (`winacl.py`). Measured with codex-cli 0.161.0
and `[windows] sandbox = "unelevated"`: the restricted token of the read-only
sandbox reads where the list names the user, Users, Authenticated Users or
Everyone, and not through OWNER RIGHTS; and for the shell to make a folder its
location, the attributes of every folder on the way must be readable, which
they are when the folder's own list says so or its parent can be listed.
Python 3.13.3 gives a `mkdir(mode=0o700)` directory a protected list of SYSTEM,
Administrators and OWNER RIGHTS; Python 3.11.3 does not restrict it.

| Folder | Entry added for the current user's SID, when missing |
|---|---|
| `<state>/worktrees` | read and execute, inherited by files and sub-folders |
| the worktree, when its list does not inherit | the same |
| the state directory; `projects` and the home of a multi-project home | read attributes, on that folder only, and only when its parent cannot be listed |

Nothing else is written: no entry for a group, no change to an existing entry,
no deny entry removed, no folder above the home, no elevation. "Read
attributes" opens neither content nor listing, so private files stay
unreadable from the sandbox. A folder is managed only if it is reached from
the state directory without a symbolic link, junction or other reparse point,
and the worktree is directly inside `worktrees`. `repair` reads each list,
records it (SDDL and whether it is protected) before changing it, verifies the
result, and puts every recorded list back when a step fails or the result does
not verify; running it again writes nothing. `initialize` and `Workspace.add`
apply the same to a new home or project, so a worktree created later inherits
its access.

**Repairs** (`repair.py`). `acl` is the only repair in `AUTOMATIC`. It is
applied when the lists say `missing`, once per check, and the stage is checked
again. If the sandbox still refuses although the lists grant reading, there is
nothing left that the policy allows: the record is `blocked`. `git_trust` runs
only when the caller passes it in `confirmed`, which only the interface does,
after the user confirmed it for that task: `git config --global --add
safe.directory <folder>` for the project's repository and the task's worktree,
skipping what is already listed and never writing a wildcard.

**The interface.** `POST …/tasks/<id>/repair` runs the same check in the
server process, under the task lock, with `confirm` limited to `git_trust`,
saves the record and its events, and starts the run when `resume` is true and
the record is ready. `POST /api/projects/<id>/start` is `new_task` followed by
`start_run` under one request ID. The page shows `task.environment` and
`task.error_category` from the summary; the *Prepare* step is `current` only
while the saved record is `checking` and the backend verifies a running
process.

**Limits.** The sandbox is probed on native Windows only; on Linux, macOS and
WSL2 the sandbox checks are skipped. Only the read-only profile is probed: a
Codex developer's `workspace-write` sandbox is not. The probe reflects the
Codex configuration at that moment. A CLI with another `codex sandbox` syntax
is not probed until its form is read from that CLI and added. Access lists
inside the user's repository, including its `.git` directory, are never
changed: a repository the sandbox cannot read is reported, not repaired.

## 13. Agent settings

`agents.py` holds everything that decides what a provider CLI is started
with. Nothing else builds a command: `providers.OfficialCLI._step` asks
`agents.command`, and with no settings the result is, argument for argument,
the command of the versions before this section existed.

### Data

One *block* is the settings of one role played by one provider:
`{setting: value}`, sparse. One *layer* is what a level keeps:
`{role: {provider: block}}`. There are three levels:

| Level | Where | Written by |
|---|---|---|
| global | `settings` in `<workspace>/agents.json` | the Agents page |
| project | `agent_settings` in the project's `config.json` | the Agents page |
| task | `agent_settings` in the task's `state.json` | task creation, the Agents page (under the task lock) |

`agents.json` also holds the custom presets and what is known about models
(IDs the user added, and the answer of each test call with its date). Older
versions ignore the file and both new keys, and a home without them behaves as
before.

Who plays a role is not part of a block: it stays where it was (`defaults` in
`settings.json`, `agents` in `config.json`, `developer`/`reviewer` in the
task), so a role has one block per provider and keeps both.

In a block a missing key inherits, `null` asks for PatchRondo's default
against an outer value, and anything else is the value. `resolve(provider,
role, layers)` applies the levels from the farthest to the nearest and returns
every value with its source (`default`, `global`, `project`, `task`). Lists
are replaced whole, never merged.

### The table of settings

`OPTIONS[provider]` lists the settings. Each entry has a kind (`model`,
`choice`, `text`, `int`, `number`, `bool`, `list`, `path`, `paths`, `pairs`,
`args`), the CLI option it becomes, its limits, the roles that may set it and
the reason the other may not, and whether a value is *risky*. `clean_block`
validates a block against the table; the interface draws its controls from
`schema`, so a setting cannot exist on the page without existing here.

Rules enforced by `clean_block`, whatever the caller:

- A model name is letters, digits and `. _ : / @ [ ] -`, and starts with a
  letter or a digit. It can never be read as an option.
- A setting of the other role is refused. The reviewer has no tools setting,
  no MCP servers, no additional writable folders, no custom arguments; its
  Codex sandbox accepts `read-only` only; its Claude permission mode
  `dontAsk` or `plan` only.
- A risky value needs `risk_acknowledged: true` **in the same block**; a level
  cannot ride on the acknowledgement of another. The reviewer has no risky
  value that an acknowledgement opens. An acknowledgement beside nothing
  risky is dropped.
- A short value that looks like a credential (`activity.redact` would change
  it) is refused: settings are not a place for secrets.

`command` repeats the reviewer's limits on its own (`read-only`, the three
reading tools, no custom arguments, no additional folders), so a value that
somehow passed validation still cannot reach a reviewer's command.
`check_folders` refuses an additional folder that is, contains or lies inside
PatchRondo's state (the workspace and the project's state directory), and for
the developer the user's own checkout of the project.

### Custom arguments and configuration overrides

Custom arguments (`extra_args`, developer only, always risky) follow three
lists per provider: `ARGS_MANAGED` (options PatchRondo passes or offers as a
setting: refused, "set it there"), `ARGS_BLOCKED` (options that remove a
protection, change how the step is started or where its output goes: refused
always) and everything else, which PatchRondo does not know and passes as it
is. Short options are refused, because `-cKEY=VALUE` would otherwise carry a
managed option past the lists; a value must follow a long option.

Codex configuration overrides (`-c key=value`) have the same shape:
`CONFIG_MANAGED` keys have their own setting, `CONFIG_BLOCKED` keys are never
passed (sandbox permissions, the shell environment policy, model providers,
`notify`, MCP servers, hooks, project trust, features, raw reasoning), keys in
`CONFIG_REVIEWED` are typed and need no acknowledgement, and any other key is
developer only and risky. Values are written as TOML literals by `toml`.

Arguments are never joined into a command line. The CLIs are started by name
with an argument array (`process.execute`, no shell); on Windows that finds an
executable and never a `.cmd` launcher, so no command interpreter reads them.

### What the CLIs offer

`doctor.discover(status)` asks an installed CLI, without a model call: the
long options its help names (`claude --help`; `codex --help` and `codex exec
--help`), and for Codex its model catalog (`codex debug models`, then
`--bundled`) and the names of its profile files. These commands go through
`providers.probe`, which accepts a fixed list of exact commands besides the
sandbox self-check and refuses while provider commands are simulated;
`discover` also refuses when the path the status check found is not a file.
The application caches the answer per executable path and version for an
hour, or until the user refreshes, and keeps it on the server.

`compatibility` gives every setting one of: `listed` (the help names its
option), `documented` (`DOCUMENTED`: the documentation names it and says the
help is incomplete), `unlisted` (the help was read and does not name it; the
page disables the setting and says so), `unknown` (the help was not read) or
`builtin` (not a CLI option). `catalog` returns the models to choose from,
each with its source (`cli`, `bundled`, `documented`, `custom`) and its test
mark. A source never means that the account can use the model.

### Freezing

A step is one role in one iteration, named `NNN:role` in the task's
`agent_runs`. `core._agent_settings` runs immediately before the event that
starts the step:

1. If `agent_runs` has a record for the step with the same provider, its
   values are validated again (`thaw`) and used.
2. Otherwise the three levels are read and validated, resolved, checked
   against the private folders, and the result (what differs from the
   defaults, the source of each value, a digest, the time) is stored. The
   record is saved in the same write as `development_started` or
   `review_started`.
3. Settings that cannot be used raise a `configuration` failure: the task
   pauses before any agent is called.

So a resume, a retry by `recovery.supervise` and a run after a crash repeat a
step with the settings it started with. The record changes in one way only:
`core.refresh_agent_settings`, called by the interface on the user's request
under the task lock, removes the record of the step the task is stopped at and
writes `agent_settings_refreshed` to the history. Records of the last sixty
steps are kept.

The frozen values travel to the adapter only if it declares `configurable`,
as the journal does with `journals`. `model` and `effort` are added to the
start event of the step, to the `started` event of each attempt in the
activity journal, and the report lists the records. The role's `instructions`
are appended to the prompt by the engine, for either provider.

A run started by the interface receives a project's state directory as its
home; `PATCHRONDO_WORKSPACE` tells it where `agents.json` is. A run started
from a terminal with a registry at its home passes that home; otherwise a
state directory under `<workspace>/projects/` finds its workspace, and any
other one is its own, as a 0.1 home.

### Failures

`unsupported_option`: the CLI exited before any event with a line naming a
refused option, and its error text names no login or usage problem.
`model_unavailable`: general wording of a refused model, checked after
authentication and quota. Neither is a `quota` failure, so recovery never
retries them. The wording of both has not been observed in an authorized run.

### Test configuration and the test call

`agents.diagnose` starts nothing: it reads the last status and the last
`discover` answer and reports installation, login, options the installed CLI
does not list, the model (a warning unless a test call confirmed it), an
effort the model does not advertise, missing folders and acknowledged risks.
`OfficialCLI.verify` is the only path of this feature that calls a model: one
call with one reading tool or the read-only sandbox, in an empty private
folder, started by `app.agents_verify` only with `confirm: true`.

### Limits

- No option was confirmed with an authenticated CLI. The table was read from
  the help of Claude Code 2.1.295 and codex-cli 0.161.0 on native Windows and
  from the documentation of both, on October 12, 2026.
- Help text shows that an option exists, not which values it accepts or that
  it works in a non-interactive call. `--max-budget-usd` is Claude Code's own
  estimate of API prices and does not measure a subscription's usage.
- Claude models are a documented list of aliases. A full model ID is typed.
- CLI sessions are never continued: each step is a new call.
- A profile, the user's configuration files and managed settings of the CLIs
  still apply, as before; `--ignore-user-config` (Codex) is offered, and
  Claude's `--restricted` already ignores user, project and local settings.
- The test call tells whether one call worked at that moment; it is not a
  guarantee for a later run.
