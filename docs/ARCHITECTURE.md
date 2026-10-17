# Architecture and handoff contract

## 1. Components

| Module | Responsibility |
|---|---|
| `cli.py` | CLI parsing, user input validation, project resolution, `init/new/run/resume/status/report/list/projects/index/search/ui/doctor` commands; no command opens the interface |
| `core.py` | Deterministic state machine and completion criteria; configuration and task-override validation; the operations a person has on a task's criteria |
| `acceptance.py` | The acceptance criteria of a task as typed records: classification by fixed rules, planning against the detected capabilities, verification against the evidence ledger, the settling of a review, the results of the task |
| `capabilities.py` | What each executor of a task can do (developer, reviewer, PatchRondo itself, the user), read from settings and from the computer; nothing is started |
| `evidence.py` | The evidence ledger of a task: append-only, hash-chained records of commands, reviews, checks by the user and decisions; invalidation and integrity; the one executor allowed to write each kind |
| `verification.py` | The verification runner: the project's configured checks (tests, build, static analysis, repository checks), run by PatchRondo, judged from their exit code or JUnit XML report, each result recorded in the ledger as it ends |
| `findings.py` | The findings of a task's reviews: an ID for each problem that it keeps across iterations, links by fixed rules only, and its history; it decides nothing |
| `history.py` | The workflow history of a task: versioned events with an ID each, saved with the checkpoints, naming ledger records, findings and criteria; it proves and decides nothing |
| `providers.py` | Claude/Codex CLI adapters, structured JSON output, error classification and parsing of stated quota resets; asks the CLIs to stream and falls back when they refuse; starts each step with its agent settings |
| `agents.py` | Agent settings: the typed table of what each CLI can be started with, validation, the three levels and their resolution, the frozen record of a step, the command of a step, presets, model lists and the checks of *Test configuration* |
| `streams.py` | Parsers of the two CLIs' activity streams into provider-independent events; drop internal reasoning; keep a bounded transcript |
| `activity.py` | The activity journal of a task: bounded, redacted, append-only events, and paging through them by cursor |
| `recovery.py` | Opt-in quota recovery: retry policy, the persisted plan and the foreground supervisor that waits without the task lock |
| `process.py` | Process stdin/stdout, timeouts, group cleanup (a job object on native Windows when asked) and filtered environment; optional observers that see output while the process runs |
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

- The code of a task is approved **if and only if** review is approved, all tests pass and changes exist (or `allow_no_changes=true`).
- A task is `DONE` **if and only if** its code is approved and every required acceptance criterion is verified by records of the evidence ledger that still count ([section 14](#14-acceptance-criteria-capabilities-and-evidence)). Approved code with a required criterion open pauses the task at the `validate` checkpoint; it is never completed and never sent back to a developer who cannot change the outcome.
- A criterion is verified only by evidence: a command PatchRondo ran, a review the backend settled, a check the user attached. The developer's handoff is never evidence, and the reviewer's word counts only for criteria the plan gives it to verify.
- File fingerprints and configured test commands must match the test checkpoint before and after review. Changes during a pause require fresh tests. Ignored files, external services and submodule contents are not fingerprinted.
- The developer cannot award itself an `APPROVED` verdict.
- Claude reviewer uses read-only file tools; Codex reviewer uses the `read-only` sandbox and can execute commands allowed by it. Change requests are passed to the developer as data.
- State moves between atomically saved checkpoints: `develop` -> `test` -> `review` -> `complete`, or `develop` in the next iteration. `review` -> `validate` -> `complete` is the path of a task whose code is approved while criteria are open; `validate` goes back to `test` when evidence that only the project's commands and a review can give is missing, and to `develop` when a check by the user failed. No agent is called at `validate`.
- If an agent subprocess fails, the loop stops in `PAUSED`. A failed test proceeds to review so the reviewer can provide useful feedback; a check that gave no result (a timeout, an error, or blocked before it started) pauses at the test phase, since it says nothing about the code (section 16).
- A result of a check is what PatchRondo observed and put in the ledger before telling anybody: never what an agent said, never a report the run did not write, never a run that changed the task's files (section 16).
- Tests that are not enabled are a missing prerequisite, not work for the reviewer: the loop pauses at the `test` checkpoint with `tests_disabled` before any reviewer call, also from a saved `review` checkpoint whose tests were disabled afterwards (the saved results are replaced by a skipped entry). A run started while that pause and the missing consent both still hold returns the saved state unchanged: no event, no environment check, no provider call. With tests enabled, Resume runs them and then the review.
- Only a `quota` pause can be retried without a person, only when recovery is active, and never before its persisted `resume_at`. No task lock is held while waiting, and no lock is ever removed automatically (section 8).
- The orchestrator does not automatically merge, push, commit or install dependencies.
- The task branch is separate from main. The external test runner executes project commands on the host and requires explicit consent.
- What the interface says about a process comes from a check of that process, never from `status` alone, and a failed check is reported as unknown (section 10).
- Live activity is an observation. The engine never reads the journal, what an agent says while it works is never its answer, and failing to observe or to record never fails a step (section 11).
- The findings of the reviews are a record. They decide nothing, an ID is never given to a second problem, a link is checked and never guessed, and a finding a review leaves out is not resolved (section 14, "Findings of the reviews").
- The history of a task records what the orchestration did. Its events are written by the backend, numbered once, saved with the checkpoints and never rewritten; they prove nothing, and the engine never reads them to decide (section 15).
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
      "description": "Check exp against UTC and add expired-token tests",
      "kind": "CODE_CHANGES_REQUIRED",
      "finding_id": "RF-001",
      "criterion_id": "AC-01"
    }
  ],
  "criteria": [
    {"id": "AC-01", "outcome": "CODE_CHANGES_REQUIRED", "note": "Expired tokens are accepted"},
    {"id": "AC-03", "outcome": "MANUAL_ACTION_REQUIRED", "note": "Needs a browser"}
  ]
}
```

Allowed verdicts are `APPROVED`, `CHANGES_REQUESTED` and `BLOCKED`. The
verdict is about the **code**. An approval that names a change the code
requires is automatically downgraded (`acceptance.requires_change`): an issue
of kind `CODE_CHANGES_REQUIRED`, whatever its severity, or an issue without a
kind whose severity is `high` or `critical`. `reported_verdict` keeps the
answer and `normalized` is `code_change_named`. The orchestrator always
applies its own test gate, independently of the model's opinion.

`issues[].kind` says what an issue is about: `CODE_CHANGES_REQUIRED` (a
change the code requires; the default, and the only kind that goes back to
the developer), `OBSERVATION` (a remark about the code that asks for no
change: it never blocks an approval and is sent to nobody),
`VALIDATION_REQUIRED`, `CAPABILITY_BLOCKED`, `APPROVAL_REQUIRED` or
`MANUAL_ACTION_REQUIRED`. `criteria` gives each acceptance criterion one
outcome: `SATISFIED`, `CODE_CHANGES_REQUIRED`, one of the four that hand a
criterion over, or `NOT_ASSESSED`. The schema passed to the CLIs
(`acceptance.REVIEW_SCHEMA`) requires both. `parse_review` also accepts an
answer without them, but for a task whose criteria were given when it was
created such an answer cannot approve the code: an approval needs an outcome
for each criterion the reviewer verifies. Only a task read back from its
`task.md` takes an approval without outcomes as a review of the code as a
whole, as before criteria were tracked. The backend validates the answer and
settles it against the plan of the task; section 14 has the rules.

`issues[].finding_id` names a problem an earlier review reported, by the ID
PatchRondo gave it, and `issues[].criterion_id` the criterion an issue is
about; each is empty when there is none. They are links the backend checks
before it keeps them: a value it cannot use links nothing and never makes an
answer invalid, and an answer without them is read as before (section 14,
"Findings of the reviews").

The developer produces a Markdown handoff with *Changes*, *Decisions*, *Remaining
concerns* and *Suggested tests*. `task.md`, `handoff.md`, `feedback.md` and test
results are supplied through prompts and files; complete historical conversations
are not concatenated. Each iteration keeps prompts, logs and replies for auditing.

## 4. Resume, idempotency and limitations

A phase is complete only when `state.json` records the next `phase`. After an
abrupt termination, unfinished actions may be **repeated**: the model sees the
worktree with partial changes. Exactly-once execution is not guaranteed within a
phase, so work should be idempotent with respect to file contents. Events of
the history recorded since the last saved checkpoint are lost with it and are
not written afterwards (section 15).

If the runner dies with `SIGKILL`, child processes may continue working. `TaskLock`
and `*.active-process.json` prevent forced unlocking while recorded PIDs are
active. This does not replace OS supervision or prevent malicious processes from
creating additional children.

Configurable parameters include the maximum number of iterations, agent and test
timeouts, Claude turns, `allow_no_changes`, explicit trust in the configured tests
and the `recovery` limits described in section 8.

A task paused at `validate` with `awaiting_validation` or
`awaiting_authorization` is resumed like the pause for a missing test consent:
while nothing was attached, decided or changed, a run returns the saved state
with no event, no environment check and no provider call.

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
415 or 500. After an answer the server closes only its sending side, then
reads and discards what the client still sends, at most 256 kB and for at
most two seconds (`LINGER_SECONDS`), before it closes the connection. A write
refused on its headers leaves its body unread or still on its way; closing
over it would reset the connection, and Windows drops an answer the client
has not read when that happens.

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

## 14. Acceptance criteria, capabilities and evidence

Before this, a task's criteria were lines of `task.md`, the reviewer had one
verdict for everything, and every `CHANGES_REQUESTED` went back to the
developer. A release task whose developer had only file tools was asked six
times for downloads, a C++ build, real inference, archives, a pull request and
a check inside another program, while its code was correct and its tests
passed. The engine described here tells those things apart: what the code
needs, what a command can prove, what needs a decision and what only a person
can do. It lowers no standard: a criterion nobody in the workflow can satisfy
stays required and stays open.

### Data

`state.json` gains `acceptance`, written in the same atomic save as every
checkpoint:

```json
{
  "version": 1,
  "source": "task",
  "criteria": [{
    "id": "AC-02", "text": "Compile the C++ component", "required": true,
    "categories": ["BUILD"], "needs": ["build"], "toolchains": ["cpp"], "depends_on": [],
    "classification": {"source": "rules", "signals": ["compiling"], "note": "It mentions compiling"},
    "executor": "user", "verifier": "user", "proof": ["build"], "feasibility": "needs_tools",
    "requires": [{"executor": "runtime", "capability": "commands.build", "state": "absent", "source": "No command of this project runs a build"}],
    "authorization_required": false, "authorization": null,
    "status": "BLOCKED_CAPABILITY", "reason": "It needs a C or C++ compiler, and none is installed on this computer …",
    "evidence": [], "missing": ["build"], "review": {"outcome": "SATISFIED", "note": "", "iteration": 1}
  }],
  "capabilities": {"detected_at": "…", "executors": {"developer": {}, "reviewer": {}, "runtime": {}, "user": {}}},
  "code": {"status": "APPROVED", "evidence": "E-0002", "iteration": 1},
  "outcome": "AWAITING_VALIDATION", "results": ["CODE_APPROVED", "AWAITING_VALIDATION"],
  "ledger": {"count": 2, "head": "…"}, "summary": {}
}
```

Criteria are `acceptance.Criterion` dataclasses and the block an
`acceptance.Acceptance`; `load` validates every field and refuses a block it
does not know: another schema `version` (an integer, `acceptance.VERSION`),
an unknown status or category, a repeated ID, or a criterion whose `evidence`
names anything but IDs of ledger records (`E-0001`, `E2-0001`) or that is
`VERIFIED` without naming any. It also refuses a null, and any field whose
content does not have the shape this version saves (October 17, 2026): a
criterion's `review` (an outcome of `ReviewOutcome`), `authorization` (a
decision and the ledger record that holds it), `classification`,
`requires`, `toolchains` (names of `capabilities.TOOLCHAIN_LABELS`),
executor and verifier, and the block's `code`, `ledger`, `capabilities`,
`outcome`, `source` and dates. A field the block lacks takes its default; a
field it holds is never replaced by one. `refresh` never carries a saved status over,
and what shows criteria judges them against the ledger first (see "Shown as
the ledger has them"). IDs are
`AC-01`, `AC-02`, … in the order the criteria were given, and never change.
A task without criteria has one, about its description.

Three questions are kept apart for each criterion:

| Question | Fields | Decided by |
|---|---|---|
| What kind of work or check is it? | `categories`, `needs`, `toolchains`, `classification` | Fixed rules over its text, or its author |
| Who could do it and verify it here? | `executor`, `verifier`, `proof`, `requires`, `feasibility` | The capabilities detected for the task |
| Is it verified? | `status`, `reason`, `evidence`, `missing` | The evidence ledger |

Categories: `CODE`, `TEST`, `BUILD`, `INTEGRATION`, `EXTERNAL`, `PUBLISHING`,
`MANUAL`; a criterion may have several, and none when it could not be
classified. Statuses: `PENDING`, `IN_PROGRESS`, `VERIFIED`, `FAILED`,
`BLOCKED_CAPABILITY`, `AWAITING_AUTHORIZATION`, `AWAITING_MANUAL_VALIDATION`,
`UNKNOWN`. Feasibility (`automatable`, `needs_tools`, `needs_authorization`,
`manual`, `unknown`) is not a status: a criterion that can be automated is
verified only by its evidence.

### Classification

`acceptance.classify` applies a fixed list of patterns to the text, in English
and in Italian, the two languages criteria had been written in on October 13,
2026. A rule only says that a criterion asks for more than a change to the
code: a download or a dependency (`EXTERNAL`), a commit, a pull request or a
publication (`PUBLISHING`), compiling, packaging or a checksum (`BUILD`),
passing tests (`TEST`), another program or a real environment
(`INTEGRATION`), a check by a person (`MANUAL`). A text that matches nothing
is a criterion about the code. Wording about licences, rules of a platform or
compliance, with nothing that says who can check it, is left unclassified:
status `UNKNOWN`, with the reason.

The author decides over the rules: a criterion may start with tags
(`[manual]`, `[build]`, `[external]`, `[publishing]`, `[integration]`,
`[test]`, `[code]`, `[optional]`), and the API accepts
`{"text", "required", "categories", "depends_on"}` instead of text. No model
classifies anything. The reviewer can only make a criterion ask for more than
the rules did (below).

### Capabilities

`capabilities.detect` reports what each of four executors can do. A capability
belongs to one executor, never to the task: a developer without a shell does
not stop a criterion that asks for passing tests, because running them is a
capability of the runtime.

| Executor | Read from | Examples |
|---|---|---|
| `developer` | Its agent settings, frozen for the step or resolved now | Claude: tools of `--tools` (file tools by default, so no shell); Codex: its sandbox mode. Network, commit and push are absent whatever is set: the developer's prompt forbids them |
| `reviewer` | Its fixed read-only configuration and the last environment check | Reads; never writes. A sandbox check that passed on a live run makes that `verified` |
| `runtime` | `tests.*` of the project, PATH | `commands.tests`, `commands.build`, `commands.package` from the configured commands, with the consent; SHA-256 of a file; toolchains found with `shutil.which`. Never the network, never Git writes |
| `user` | Nothing | Consent and manual checks on request; what the person has outside the project is `unknown` |

States: `verified` (observed on this computer), `declared` (follows from a
configuration PatchRondo passes or enforces), `absent`, `unknown`. Nothing is
started to find out: a toolchain is looked for on PATH and never run. What a
configured command does is read from its own words (`capabilities.covers`:
`cmake`, `make`, `cargo build`, `npm run build`, `python -m build`, …), so a
project that adds its build to `tests.commands` gives the runtime the
capability to prove a build, under the consent the project already gave.

Detection grants nothing. Agents get tools only through their agent settings
with the acknowledgement (section 13), and commands run only with
`tests.enabled` and `tests.trust_acknowledged`.

### Planning

`Acceptance._plan` gives each criterion its kinds of `proof` from what it
needs:

| Need | Proof | Done by | Verified by |
|---|---|---|---|
| a change to files | `review` | developer | reviewer |
| passing tests | `test` | PatchRondo | PatchRondo: the whole run of the project's commands passed |
| a build, a package | `build`, `package` | PatchRondo when a configured command covers it; otherwise the user | that command's result, or a check by the user |
| a checksum, another program, a real environment, a person | `manual` | the user | a check by the user |
| a download, a commit, a publication | `manual`, after an authorization | the user | a check by the user |

The preflight is this plan with an empty ledger. `create_task` computes and
saves it; `core.preflight` and `POST …/criteria/preview` compute it for a task
that does not exist yet. It never stops a task from being created.

### Evidence

`<task>/evidence.jsonl` is append-only. Each line is one record:

```json
{"id": "E-0001", "seq": 1, "at": "…", "kind": "command", "executor": "runtime", "result": "passed", "exit_code": 0,
 "command": ["python", "-m", "pytest", "-q"], "covers": ["tests"], "run": "001-3f9c2a1b", "of": 1, "criteria": [],
 "log": "iteration-001/3f9c2a1b-test-01.log", "log_sha256": "…", "iteration": 1, "fingerprint": "…", "base": "…",
 "scope": "code", "check": {"version": 1, "id": "command-1", "type": "test", "adapter": "exit_code", "…": "…"},
 "prev": "", "hash": "…"}
```

A `command` record written by the verification runner carries `check`, what
the runner observed (section 16); one written before it, or with
`PATCHRONDO_VERIFICATION=0`, has none and names `iteration-001/test-01.log`.

| Kind | Written when | By |
|---|---|---|
| `command` | A configured check of the project ran in the test stage, or was refused before it started | `verification.Runner`, as each check ends (`_record_commands` with the runner switched off) |
| `review` | A review was parsed and settled | `_record_review` |
| `manual` | The user attached a check: what was done, a reference or a file hashed by PatchRondo, passed or failed | `attach_evidence` |
| `authorization` | The user granted or denied an operation | `decide_authorization` |
| `invalidation` | Earlier records stopped counting | `_sync`, `_validate_stage` |
| `restart` | The ledger was started again after failing its check | `evidence.restart` |

Each kind has one executor (`evidence.EXECUTORS`): `append` fills it in
when it is left out and refuses any other, and a `command` record says one
of `passed`, `failed`, `error`, `blocked` or `timeout`. So a record of a
command is the runtime's whatever a caller passes; what an agent says about
a command never reaches the ledger at all. Writers of one ledger take turns
(`storage.FileMutex` on `evidence.jsonl.lock`, held for one append), so two
writers never give two records the same number; the task lock still
serializes everything a run does.

`fingerprint` is the digest of the task's files the record was made on
(`gitops.fingerprint`), `base` the commit the task started from. `prev` and
`hash` chain the records: a record that was changed, removed or inserted is
found when the file is read, and from that record on nothing is trusted. This
makes silent edits visible; it is not a signature, and the files belong to the
user. The task state keeps the count and the last hash it saw, so a shortened
ledger is noticed too. A ledger that fails is put aside unchanged
(`evidence.damaged-<n>-<time>.jsonl`), a new one starts with a `restart`
record and numbers its records apart (`E2-0001`), and what the old one
verified has to be verified again.

Records are never edited. When the fingerprint of the files changes, records
with `scope: "code"` made on other files are named by an `invalidation`
record; a record with `artifacts` is invalidated when one of the files it
named is gone or no longer the file that was hashed. A check of a download
has `scope: "independent"`; a decision of the user does not depend on files.
The developer's step invalidates what was verified on the files as they were,
which is how a criterion goes back from `VERIFIED` to open.

### Verification

`Acceptance.refresh` judges every criterion again from the ledger; nothing is
carried over from the previous judgement. A criterion is `VERIFIED` when each
kind of proof it needs has records that count, the criteria it depends on are
verified and, for an operation that needs consent, the user's last decision
is a grant. Otherwise its status says what is missing, in this order: waiting
for another criterion; being developed; a change to the code asked by the
reviewer, or a command or a check that failed (`FAILED`); a command of the
project that gave no result, an error, a timeout or a check that could not
start (`PENDING`, with that reason: nothing was learned about the code); a decision
(`AWAITING_AUTHORIZATION`, also when the project's commands are configured
and not allowed); a missing program or command (`BLOCKED_CAPABILITY`); a
check by the user (`AWAITING_MANUAL_VALIDATION`, or `UNKNOWN` for an
unclassified criterion); the review or the commands that have not run yet
(`PENDING`).

### The review, settled

`Acceptance.settle_review` validates a parsed review against the plan:

- An outcome for an unknown or repeated criterion is counted in
  `ignored_criteria` and not applied.
- `CODE_CHANGES_REQUIRED` counts only for a criterion that a change to the
  files can satisfy (`Acceptance.for_reviewer`: one that needs `edit`, also
  after a review handed it over). For a criterion the plan gives to the user
  or to the project's commands it is kept with the criterion as the
  reviewer's opinion, listed in `refused_changes`, and changes neither the
  verdict nor the plan.
- `APPROVED` with a criterion of the reviewer marked `CODE_CHANGES_REQUIRED`
  becomes `CHANGES_REQUESTED` (`normalized: "code_change_named"`).
- `CHANGES_REQUESTED` with no issue about the code and no
  `CODE_CHANGES_REQUIRED` outcome that counts, while at least one criterion
  was handed over or had its change refused, and every criterion of the
  developer is `SATISFIED` or handed over, becomes `APPROVED` (`normalized:
  "no_code_change_requested"`, `reported_verdict` keeps the answer). It asked
  nothing the developer can do, so nothing is sent to the developer. `BLOCKED`
  is never changed.
- An outcome that hands a criterion over (`VALIDATION_REQUIRED`,
  `CAPABILITY_BLOCKED`, `APPROVAL_REQUIRED`, `MANUAL_ACTION_REQUIRED`,
  `NOT_ASSESSED`) replaces the review of the code by a check of the user as
  its proof, until a later review judges the code itself. This is the only
  way a model changes a plan, and it only asks for more.
- `SATISFIED` for a criterion that the reviewer does not verify is kept with
  the criterion and proves nothing.
- An approval, as answered or as settled, that gives no outcome to a
  criterion the reviewer verifies (one that needs a reading of the code,
  `Acceptance.for_reviewer`) raises `IncompleteReview` when the task needs an
  outcome for each (`explicit_outcomes`: every task whose criteria were given
  when it was created). Nothing of the answer is applied or recorded; the
  loop pauses at the `review` checkpoint with `invalid_review`, and a Resume
  asks for the review again. A request for changes and `BLOCKED` approve
  nothing and are not held to it.

A `review` record proves a criterion only through the outcome it gives that
criterion. An approval covers a criterion it did not list only in a task read
back from its `task.md`, and in every other task `refresh` withdraws the
approval of the code whose record leaves a criterion of the reviewer without
an outcome (an approval saved before each one needed its own).

These decisions come from the structured answer, never from its prose.
`feedback.md` then holds the issues about the code and the outcomes of the
developer's criteria; what is open outside the code is listed apart, under
"Not work for the developer", and the developer's prompt names the criteria
that are its work and those that are not. A criterion whose change was
refused is listed apart; an observation is in neither list.

### Findings of the reviews

Since October 17, 2026 each issue of a validated review is recorded as a
finding (`findings.py`) with an ID of its own, `RF-001`, `RF-002`, …, or
linked to a finding an earlier review reported, so that a problem reported
again in other words is not taken for a new one. Findings are the ground a
later detection of progress can stand on; they decide nothing now. The
verdict, the test gate and the criteria decide as before:
`tests/test_findings.py` keeps the events, verdicts and criteria of a
scenario as recorded with the sources from before findings, and checks that
the same answers lead to them.

`state.json` gains `findings`, saved in the same atomic write as the
checkpoint of the review, once a review has reported an issue:

```json
{
  "version": 1, "next": 3, "reviews": ["E-0002", "E-0004"],
  "items": [{
    "id": "RF-001", "kind": "CODE_CHANGES_REQUIRED", "path": "feature.txt", "criterion_id": "AC-01",
    "severity": "high", "description": "Still no newline at the end",
    "owner": "developer", "actionable_now": true, "blocking": true, "status": "OPEN",
    "first_seen_iteration": 1, "last_seen_iteration": 2, "reported_in": ["E-0002", "E-0004"],
    "verification": null, "claimed": null, "path_key": "5f2c…", "words_key": "a91e…", "unknown_criterion": null
  }]
}
```

| Field | What it holds | From |
|---|---|---|
| `id` | `RF-` and a number: unique in the task, never given to another problem | PatchRondo |
| `kind`, `path_key`, `criterion_id` | The identity: the kind of the issue (null for an answer without kinds), the SHA-256 of the whole file it named without a line after it, the criterion of the task it named | The issue, checked |
| `unknown_criterion` | What the issue gave as its criterion when the task has no such criterion, shortened; `""` for a finding saved without a criterion before this field existed. Either way the finding is never linked | The issue, checked |
| `path`, `severity`, `description` | As last reported; `path` and `description` shortened to be shown (300 and 600 characters) and never compared | The issue |
| `words_key` | The SHA-256 of the whole last description, spaces and case aside; null for one without words | PatchRondo |
| `owner` | `developer` for `CODE_CHANGES_REQUIRED` or no kind (what goes back to the developer), `nobody` for `OBSERVATION`, `user` for `MANUAL_ACTION_REQUIRED`, `APPROVAL_REQUIRED` and `CAPABILITY_BLOCKED`, the criterion's verifier for `VALIDATION_REQUIRED` (`runtime` or `user`), otherwise `unknown` | A fixed rule |
| `actionable_now` | Whether that owner could act when it was last reported: the developer can write files, the runtime's criterion is `automatable`, a person can, except for missing tools | A fixed rule |
| `blocking` | Whether, as last reported, the task needed a correction or a check for it before it could be done: a change the code needs (`acceptance.requires_change`), or a `VALIDATION_REQUIRED`, `CAPABILITY_BLOCKED`, `APPROVAL_REQUIRED` or `MANUAL_ACTION_REQUIRED` issue about a required criterion that was not verified. Never an observation, nor an issue about an optional criterion or about none | A fixed rule |
| `status` | `OPEN`, `PENDING_VERIFICATION` or `RESOLVED` | PatchRondo |
| `first_seen_iteration`, `last_seen_iteration` | The iterations of its first and of its last report | PatchRondo |
| `reported_in` | The `review` records of the ledger that reported it | PatchRondo |
| `verification` | The record a status beyond `OPEN` rests on: `{"review", "iteration"}`. A finding reported again is `OPEN` and keeps it: an approval had left it out before | PatchRondo |
| `claimed` | An ID the reviewer gave the issue that was refused, and why | PatchRondo |

`reported_in` is not called evidence: those records prove that a review
reported the problem, not that it exists or that it is gone. `nobody` is an
owner of its own because an observation asks for nothing, of anybody. The
issues of the saved review carry `finding_id`, the finding they were
recorded under, and `finding_link` (`new`, `explicit` or `identical`); a
refused claim stays as `claimed_finding_id` with `finding_refusal`. Values
the reviewer gave these fields are dropped first. The `review` record of the
ledger names the findings of its issues (`findings`).

- The reviewer is shown the findings with their IDs, at most 40, those
  reported last first, as earlier reviewers' words: data, not instructions.
  It is asked to give an ID back in `finding_id` when an issue reports one of
  them again, with the same kind, file and criterion.
- An issue is linked to an earlier finding only when its `finding_id` names a
  finding of the task with the same kind, file and criterion, which no other
  issue of the review took (`./a.py:12`, `a.py#L3-L9` and `.\a.py` all name
  `a.py`); or, without a `finding_id`, when exactly one finding has the same
  kind, file, criterion and description, spaces and case aside. Nothing is
  matched by similarity. Any other issue is a new finding, which keeps a claim
  it made, refused, with the reason: not an ID, an ID the task does not have,
  another kind, file or criterion, or an ID another issue took.
- A criterion is compared only when it is known. A missing, null or empty
  `criterion_id` names no criterion; any other value that is not a criterion
  of the task (`AC-999` in a task with `AC-01` only, a number) is not taken
  for none. Such an issue is linked to nothing, by its ID or by its words (a
  claim is refused: "the issue names a criterion this task does not have"),
  and the finding it becomes keeps the value in `unknown_criterion` and is
  never linked either. A finding saved without a criterion before the field
  existed may have come from such an issue, since both were saved as none:
  it is read with `unknown_criterion` `""`, never linked, and its history
  stays as it is.
- The file and the words are compared whole, through the SHA-256 of their
  normalized text (`path_key`, `words_key`). `path` and `description` are
  shortened copies kept to be shown, and two values that differ only past
  what they keep are two problems. A finding saved before the keys existed
  gets them from those copies only where `clip` cut nothing off; otherwise
  the key is null, the finding is never linked through it (an ID claimed for
  it is refused: its file "was saved shortened"), and its history stays as it
  is.
- A new finding is numbered past every ID of the saved findings and every
  finding ID in the `findings` field of the records of the task's ledger and
  of each ledger `evidence.restart` put aside (`evidence.ARCHIVED`). The
  field is read in the text of each line, whatever state the line is in: a
  record cut short by a crash, or damaged, is read up to where its list or
  the line ends. Other text, a summary the user typed or a reason, is not
  searched: inside a JSON string a quote is always written `\"`, so not even
  the key of the field written out in a summary matches, and an ID quoted
  there (`RF-999999`) takes no number. The files are only read: nothing read
  there counts as evidence. An ID written by a review whose state was never
  saved, because the process ended in between, is therefore not given again,
  also after that ledger was found damaged and started again. A ledger that
  cannot be read leaves the review without findings (`findings_not_recorded`):
  which IDs are taken would not be known.
- A review is recorded once (`reviews`), and recording never changes the
  identity of a finding.
- A finding a review does not mention stays as it was. When a review approves
  the code, the developer's open findings it did not report become
  `PENDING_VERIFICATION`, naming that review; a later report makes one `OPEN`
  again. No finding becomes `RESOLVED`: an approval is the reviewer's
  judgement about the code as a whole, and no record yet verifies that one
  problem is gone. `load` refuses a status beyond `OPEN` that names no
  record.
- `load` refuses a null, another `version`, a repeated ID, a `next` at or
  below an ID in use and any field of another shape. Such a record is never
  replaced: the review goes on as it would without findings, its issues
  linked to nothing (an ID the reviewer gave stays as `claimed_finding_id`),
  its ledger record naming none, and the history records
  `findings_not_recorded`. A state without the key has no findings and is
  read and run as before.
- At most 1000 findings are kept for a task, and no number past 999999 is
  given; an issue past either stays in its review without an ID.

### In the loop

```text
create_task          criteria classified, capabilities detected, plan saved (the preflight)
run start            _sync: a task without a record gets one from its task.md, nothing verified
                     a record this version cannot read -> paused: acceptance_unreadable, nothing replaced
develop              developer told which criteria are its work; afterwards evidence on the old files is invalidated
test                 one `command` record per command; criteria that commands prove are judged
review               reviewer given criteria, capabilities, evidence and findings; answer settled; issues recorded as findings; `review` record
  code approved      every required criterion verified -> done
                     otherwise                          -> phase `validate`, paused: awaiting_validation | awaiting_authorization
  not approved       feedback -> develop (next iteration), or blocked at the iteration limit, as before
validate (a run)     nothing changed                    -> the saved state is returned: no event, no check, no call
                     every required criterion verified  -> done
                     a check by the user failed         -> feedback, develop (next iteration)
                     evidence of commands or review missing (files or commands changed, ledger restarted) -> test
```

Results kept apart from the five statuses of the workflow: `code`
(`PENDING`, `CHANGES_REQUIRED`, `APPROVED`) and `outcome` (`IN_PROGRESS`,
`AWAITING_VALIDATION`, `AWAITING_AUTHORIZATION`, `RELEASE_READY`); `results`
lists what holds, such as `["CODE_APPROVED", "AWAITING_VALIDATION"]`. A task
that went back to `test` from `validate` with its files unchanged is not
reviewed again: the review that approved these files still counts, and only
the commands run. If they fail, the approval is withdrawn. An approval
withdrawn because its review left a criterion without an outcome sends the
run back to `test` too, and there the code is reviewed again, without a
development step.

### What a person does

Three operations, in `core.py`, each under the task lock, none calling an
agent or a command and none changing the status of the task:

| Operation | Route | Rule |
|---|---|---|
| `attach_evidence` | `POST …/criteria/AC-nn/evidence` | Only for a criterion a person may verify, never in place of the review or of the tests. A statement alone is refused: a passed check names a reference or a file. A failed check goes to the developer at the next run, once |
| `decide_authorization` | `POST …/criteria/AC-nn/authorization` | Only for a criterion about an operation that needs consent. A grant needs `confirm: true`. Nothing is run either way: the operation stays the user's, and its evidence too |
| `recheck_acceptance` | `POST …/criteria/check` | Detects capabilities again and judges the criteria from the ledger |

With `resume: true` the interface starts a run only when that run would
complete the task without calling an agent (`completes`). The command line
has the same three: `patchrondo criteria`, `evidence`, `authorize`.

### Shown as the ledger has them

The task page, the list of tasks and `patchrondo criteria` run no step, yet
they show statuses. They judge a copy of the saved record in memory against
the ledger as it is (`acceptance.judged_now`, `refresh` on a copy) and write
nothing. A `VERIFIED` whose records were invalidated, or are no longer read
intact, since the last save is therefore not shown as verified: its reason
says it was saved as verified and the ledger does not confirm it now. What
was saved is kept apart: `saved_status` for each criterion and `saved`
(`outcome`, `code`, `updated_at`) in the view, "(saved as …)" on the command
line. A ledger that no longer holds, unchanged, the records the saved record
was judged from (`Ledger.anchored` against its `ledger` count and head: fewer
records, or one of them changed) confirms nothing there. The record that is
gone may be the invalidation the last judgement counted, so
`evidence.Ledger.for_state` gives a copy in which no record is trusted, with
the problem named; the page lists every record as not trusted, and the
counts and results are those of a ledger with nothing in it. The next run or
*Check again* starts the ledger again (see "Evidence"); reading it touches
nothing. The list keeps its counts while `state.json` and the ledger keep their
signatures. Evidence about files that changed is not invalidated there: that
needs the fingerprint of the files, which a run or *Check again* takes and
puts on record in the ledger. `report.md` shows the criteria as the engine
judged them when it wrote the report.

### Older tasks

A state without `acceptance` is a task created before criteria were tracked.
A state with `"acceptance": null` is not: `load` refuses the null like any
record it cannot read.
A task that ended is returned as it is by `run_task`; the interface lists its
criteria as written and says nothing about them. A task that runs again gets
its criteria from `task.md` (`acceptance_planned`, `source: "task.md"`), with
nothing verified: saved test results and a saved review were never evidence,
so a review that was about to start waits for the tests to run again. Such a
task keeps the answers from before outcomes (`explicit_outcomes` is false):
an approval covers a criterion it did not list. The
schema is versioned (`acceptance.VERSION`, `evidence.VERSION`). Only a task
with no record at all is planned from its `task.md`. A record that `load`
refuses, one written with a later schema version included, is never replaced
(October 17, 2026): a later version may have criteria `task.md` does not
list, and `task.md` never holds what an author set beyond the text. The run
pauses with `acceptance_unreadable` before any agent, environment check or
project command; the record and the ledger stay as they were, `report.md`
says nothing about the criteria, and the three operations of the user are
refused. A Resume while the record still cannot be read returns the saved
state: no event, no check, no call. Opened with the version that saved it, or
with the `state.json` that held it put back, the task goes on where it
stopped.

### Limits

- The runtime runs only the commands a project configured and allowed. There
  is no separate executor for builds, packaging, downloads or publication: a
  criterion that needs one is done by the user, or by a command the user adds
  to the project's commands.
- An authorization is a recorded decision. It gives nobody a permission.
- Classification is a list of patterns. It can be wrong in both directions; a
  wrong guess is corrected with a tag when the task is created, not afterwards.
- A check by the user is the user's statement with a reference, or a file
  PatchRondo hashed. PatchRondo does not open the reference.
- The ledger shows edits; it does not prevent them.
- The extended review schema had not been confirmed with a call to an
  authenticated CLI on October 13, 2026.
- Findings are linked by an ID the reviewer gives back or by the same words.
  A reviewer that describes a problem again in other words without its ID
  makes a new finding: nothing guesses. No finding becomes `RESOLVED` yet.
  The fields `finding_id` and `criterion_id` of the review schema had not
  been confirmed with a call to an authenticated CLI on October 17, 2026.
- `blocking` describes the task as it was when the finding was last
  reported, and nothing reads it to decide. A finding saved before its
  definition was corrected on October 17, 2026 keeps the value it was saved
  with until it is reported again.

## 15. Workflow history

Since October 17, 2026 every event the engine writes to `state["history"]`
is versioned and identified (`history.py`). Four records are kept apart:

| Record | What it holds |
|---|---|
| The history, in `state.json` | What the orchestration decided or did: a run started, a step started or ended, a review was settled, the code was approved, the task paused |
| The activity journal, `activity.jsonl` | What the agents and the test runner showed while they worked (section 11): an observation, never an answer and never evidence |
| The evidence ledger, `evidence.jsonl` | What was verified, by whom and on which files, and what stopped counting (section 14) |
| The findings, `state["findings"]` | The identity and the history of each problem the reviews reported (section 14) |

The history proves nothing, and the engine never reads it to decide. A later
detection of progress can read it beside the other three.

```json
{
  "version": 1, "id": "WE-0014", "task_id": "T-bcf343d027e6", "event": "review_completed",
  "at": "2026-10-17T13:59:14+00:00", "iteration": 2, "phase": "review",
  "verdict": "CHANGES_REQUESTED", "evidence": "E-0005", "issues": 2,
  "finding_ids": ["RF-002", "RF-003"], "new_findings": ["RF-003"], "linked_findings": ["RF-002"],
  "unrecorded_issues": 0
}
```

| Field | What it holds |
|---|---|
| `version` | The schema of the event: 1 |
| `id` | `WE-` and a number of at least four digits: unique in the task, in the order of the list, never given twice once saved |
| `task_id` | The task the event belongs to (`state["id"]`) |
| `event` | What happened, as before |
| `at` | When it was recorded: UTC, PatchRondo's clock, to the second, as before |
| `iteration` | The iteration of the task, as before |
| `phase` | The phase the task was at when the event was recorded (`complete` for `task_completed`) |
| `truncated` | Only in an event a detail of which was cut: the path of each list or object cut (`finding_ids`, or `table.rows` inside a detail) and how many entries it had |

`history.append` sets these from the state and the clock. A detail named
like one of them raises `ValueError` and appends nothing: a mistake of the
code that calls it. The details of each kind of event are those it had, with
the references below. Text is cut at 600 characters and passed through
`redact`, and a text that was cut says so itself (`… [+N characters]`). A
list or an object keeps its first 100 entries, over three levels, and is
never cut without saying so: `truncated` names it with its whole length,
and one past the third level, which keeps nothing, with the number of
entries it had (since October 17, 2026; until then a list was cut without a
mark). `truncated` names at most 100 paths. A value JSON cannot hold is
written as text, and a number that is not finite as null. Both writers of
the history call it, `core._event` and
`recovery._audit`, so the events of quota recovery, `recovery_wait_started`
of a waiting supervisor included, are numbered with those of the engine.

### References

| Event | What it names since October 17, 2026 |
|---|---|
| `review_completed` | `evidence`: the `review` record of the ledger. `issues`. `finding_ids`: the findings its issues were recorded under (`Findings.observe`), split by the `finding_link` of the issues into `new_findings` (`new`) and `linked_findings` (`explicit` or `identical`). `unrecorded_issues`: issues recorded under no finding, because the findings could not be read or no ID was left. `normalized` when the backend settled the verdict (`code_change_named`, `no_code_change_requested`), beside `reported`, the verdict as answered |
| `code_approved` | `pending_verification`: the developer's findings this approval made `PENDING_VERIFICATION`, an empty list when none |
| `task_completed` | `pending_verification`, when the run that completed the task approved the code and that approval made findings wait for a verification: `code_approved` is not written then |
| `evidence_invalidated` | `invalidations`: the `invalidation` records written, which name what stopped counting |
| `evidence_ledger_restarted` | `evidence`: the `restart` record of the new ledger |

`code_approved`, `evidence_recorded` and `authorization_decided` already
named their record in `evidence`; `task_paused`, `task_blocked` and the
other events are unchanged. No event takes words from an agent: not the
summary of a review, not the description of an issue, not a handoff. A
verdict is the one `parse_review` and `settle_review` validated, finding IDs
are those PatchRondo gave and `observe` recorded, criterion IDs come from
the task's record. Lists hold IDs, never copies of findings, criteria or
records.

A reference is only a reference. `review_completed` says that a review was
settled with that verdict, not that the code is right; `development_completed`
says that a step ended, not that a problem is gone; a finding a review does
not list is not resolved, and no event says `RESOLVED`. Whether the record
an event names still counts is the ledger's to say: after an invalidation
the event names it as before, and after a restart the new ledger numbers its
records apart (`E2-0001`), so an ID an event names never comes to mean
another record.

### IDs, checkpoints and Resume

An event is appended to the state in memory and saved with the checkpoint
that follows it (`_save`, or `save_json` in the interface's operations and
in a waiting supervisor), in the same atomic write as the rest of
`state.json`. Only `state.json` holds event IDs: the ledger, the journal and
the findings do not. The number of a new event is past every ID of the
history and past its length, so an ID that was saved is never given again;
an event lost with a checkpoint that was never saved leaves its number free,
since nobody saw it.

- A Resume with nothing to do (sections 2 and 4: `tests_disabled`,
  `awaiting_validation` or `awaiting_authorization`, `acceptance_unreadable`)
  returns the saved state before any event, and uses no ID.
- A run that pauses saves the events it had, and the next run numbers on
  after them.
- A process that ends between two checkpoints loses the events since the
  last one. The next run does not write them afterwards: an event describes
  what was recorded when it was written, and nothing is reconstructed.
- `state.json` and `evidence.jsonl` are two files written one after the
  other, with no transaction across them. A ledger record whose checkpoint
  was never saved, such as a review recorded just before the process ended,
  is named by no event; an `invalidation` record can exist without its
  `evidence_invalidated`. The ledger stays the authority for evidence, and
  the next run judges from it.

### Older histories

Events written before have `at`, `event`, `iteration` and their details.
They are read as they are and never rewritten or given IDs; the report, the
interface and the tools read the same fields from both. The first new event
of such a task is numbered after the length of its history: `WE-0011` in a
task with ten older events.

### The four records read together

Since October 17, 2026 one review can be followed through all four, and
each link has its way back:

```text
review_completed WE-…  --evidence-->      review record E-… (verdict, iteration, outcomes per AC, findings)
                       --finding_ids-->   findings RF-… (reported_in names E-…; Findings.reviews names E-… once)
code_approved WE-…     --evidence-->      the approving E-…; pending_verification -> findings whose verification is that E-…
criteria AC-…          --evidence-->      ledger records that count now; acceptance.code.evidence -> the approving E-…
```

A record an event names may live in a ledger `evidence.restart` put aside:
IDs are numbered apart for each ledger (`E-`, `E2-`, …), so the ID alone
finds it. A `review` record named by no event is one whose checkpoint was
never saved: no finding names it and `Findings.reviews` does not hold it.
`tests/test_coherence.py` rebuilds every review of a task this way after
whole runs, crashes before and after each checkpoint, a quota pause, a ledger
invalidated and started again, the iteration limit, and a task as the sources
from before findings and workflow events left it, and checks each link in
both directions.

What people read shows these links as saved, and nothing beyond them. The
history of `report.md` gives each event its ID and the verdict, records and
findings it names (`verdict`, `evidence`, `finding_ids`,
`pending_verification`, `invalidations`); a list the event saved in part says
so, "(this event lists 100 of 120)". The issues of the last review, in the
report and on the task page, show the finding each was recorded under and
whether an earlier review reported it. On the page, an event saved in part
says which lists were cut and how long each was, and tests are shown as
passed only when the event holds its whole list of statuses and each one is
`passed`. An entry of the history that is not an event, or an event without
`at`, `iteration` or `event`, is shown as one, with "unknown" for what it
lacks; nothing in `state.json` is changed for it, and runs, reports and the
operations of the user go on.

### Limits

- The IDs order the events of one task; they are not a signature. Somebody
  who edits `state.json` can change or remove events, as any other part of
  the state, and nothing notices it.
- `at` has a resolution of one second; the order of the list is the order of
  the events.
- A list in an event stops at 100 entries. A review of 120 problems lists
  100 of them in `finding_ids` and `new_findings`, with `"truncated":
  {"finding_ids": 120, "new_findings": 120}`; the ledger's `review` record
  and the findings keep all 120. The whole length of a list is its entry in
  `truncated` when there is one, otherwise its length. The counts beside the
  lists (`issues`, `unrecorded_issues`, `records`) are never cut.

## 16. Verification runner

Before this, the test stage ran the entries of `tests.commands` one after the
other, read each from its exit code, and put the results in the ledger after
the last one ended. A report was never read, an infrastructure error was a
failure of the code, a command that rewrote the task's files was recorded as
having passed on the files it left, the output was kept unmasked, and a run
again in the same iteration replaced the log an earlier record named.
`verification.py` keeps that stage and its consent and changes how a check
is run, read and recorded. No model runs or reads anything here: the runner
is plain Python, and an agent can neither start a check nor write a result.

### Data

A check is a `verification.Check`, built only from the validated project
configuration. The entries of `tests.commands` are checks named
`command-1`, `command-2`, … of type `test`, read from their exit code, as
before. More checks go in a top-level section of `config.json`, next to
`tests`, which the interface keeps when it saves the test settings:

```json
"verification": {"checks": [
  {"id": "pytest", "type": "test", "report": "junit",
   "command": ["python", "-m", "pytest", "-q", "--junitxml={report}"],
   "min_tests": 1, "required_tests": ["tests/test_api.py::test_expired_token"], "criteria": ["AC-01"]},
  {"id": "ruff", "type": "static_analysis", "command": ["ruff", "check", "."]},
  {"id": "wheel", "type": "build", "command": ["python", "-m", "build", "--wheel"], "artifacts": ["dist/*.whl"]}
]}
```

| Field | Meaning |
|---|---|
| `id` | 1–40 letters, digits, `.`, `_`, `-`; unique; `command-<n>` is reserved for `tests.commands` |
| `type` | `test`, `build`, `static_analysis`, `repository_check`; a `build` check proves a build (`capabilities.check_covers`) |
| `command` | An argument array, started without a shell. A program that runs its arguments as a script (`sh`, `bash`, `cmd`, `powershell`, `pwsh`, `wsl`, …) needs `"shell": true` |
| `cwd` | A folder inside the task's copy of the project, `.` by default |
| `timeout_seconds` | 1–86400; `workflow.test_timeout_seconds` by default, so a task's override applies |
| `report` | `junit`: the result is read from a JUnit XML report |
| `{report}` / `report_path` | Where the report is written: `{report}` in an argument is replaced with a new path in the task's private run folder; `report_path` is a file inside the project, for a tool that cannot be told where to write |
| `min_tests` | With a report: fewer test cases fail the check; 1 by default |
| `required_tests` | With a report: tests, classes, modules or suites that must be in it with a pass and no failure (`tests/test_api.py::test_x`, `tests.test_api.TestA`, a suite's name) |
| `artifacts` | Patterns inside the project; a passing check hashes the files they match, and fails when one matches none |
| `criteria` | Criteria this check may give evidence for. Naming one proves nothing: it lists the record under the criterion on the page, and is kept for the acceptance gate of a later step |

`validate_config` refuses an unknown field, a location that is absolute, has
a drive, `..` or a `:`, a report nobody is told to write, `min_tests` or
`required_tests` without a report, and a check named twice, so an invalid
check never reaches the disk from the interface and never runs. With tests
enabled, `tests.commands` may be empty when `verification.checks` has a check.

### Running

`core._test_stage` runs `verification.checks(cfg)`, `tests.commands` first,
in one run named `<iteration>-<8 hex>`, until the first check that does not
pass, as before. For each check the runner:

1. refuses it before anything starts (`blocked`) when it is not one of the
   checks of the configuration, when `tests.enabled` and
   `tests.trust_acknowledged` are not both true, when it starts a shell it
   does not allow (`tests.commands` entries keep the behavior they always
   had), or when its folder or report location is not inside the project or
   goes through a link or a junction (`winacl.is_reparse_point`, then
   `resolve`);
2. takes a snapshot of the task's files (`verification.snapshot`): the
   commit, the branch, the Git directory of the worktree, whether it is
   dirty and how many entries `git status` lists, and `gitops.fingerprint`.
   The commit alone never says that two copies are the same;
3. starts the command with `process.execute(..., tree=True)` through the
   engine's own `execute`, so the tests that replace it still do. On POSIX
   the process group, and on native Windows a job object (`process._Job`),
   stops everything the command started when it times out or the run is
   interrupted;
4. writes its output to `runs/iteration-NNN/<run>-test-NN.log` after
   masking the values of this process's variables named like secrets
   (`known_secrets`) and what `activity.redact` recognizes; the task state
   keeps the last 6000 characters;
5. takes the snapshot again, and judges.

| Observed | Result | `check.error.kind` |
|---|---|---|
| The time limit | `timeout` (an error, written as before) | `timeout` |
| A signal ended it (POSIX) | `error` | `killed` |
| The task's files changed between the two snapshots | `error`, naming up to 20 entries `git status` lists | `workspace_modified` |
| Git answered before and not after | `error` | `snapshot_unavailable` |
| No report; a link; larger than 32 MiB; not XML; a document type; not JUnit | `error` | `report_missing`, `report_invalid`, `report_unsafe` |
| A `report_path` file that is the one from before the run (size, time and SHA-256 equal) | `error` | `report_stale` |
| Suites that declare more failures or errors than their test cases show; another number of tests while none fails; exit 0 with failing cases; a nonzero exit with none | `error` | `report_inconsistent` |
| Failing test cases and a nonzero exit; fewer than `min_tests`; a required test absent or without a pass; an artifact pattern that matches nothing; a nonzero exit without a report | `failed` | |
| Everything else | `passed` | |
| The program is not there, or the system refused to start it | `blocked` | `not_found`, `permission_denied` |
| A start that failed otherwise | `error` | `start_failed` |

`parse_junit` reads the report with `xml.parsers.expat` and refuses a
document type declaration before an entity can be defined, so nothing is
expanded and nothing outside the file is fetched; it bounds the size, the
depth (64) and the test cases (200,000). Counts come from the `testcase`
elements, a case with a `failure` and an `error` counting once for each, as
pytest writes a test that fails and then errors in its teardown. What the
suites declare is only compared with them. `judge_report` and `judge_exit`
read nothing but their arguments.

A report written to `{report}` is read where it is; one read from
`report_path` is copied beside the log, to a name of this run only. The copy
and every artifact are hashed into `artifacts`, so `evidence.outdated`
invalidates the record when one of them changes or disappears. A report
inside the project must be ignored by Git, or the run changed the task's
files.

The result is appended to the ledger as soon as it is known, before the
next check starts and before the stage returns. An interruption records
nothing about the check it stopped, and a ledger that cannot be written
stops the run with the error: no result exists that the ledger does not
hold.

### The record

```json
{"kind": "command", "executor": "runtime", "result": "passed", "exit_code": 0, "criteria": ["AC-01"],
 "command": ["python", "-m", "pytest", "-q", "--junitxml=…/runs/iteration-001/3f9c2a1b-test-01.junit.xml"],
 "covers": ["tests"], "run": "001-3f9c2a1b", "of": 2, "fingerprint": "<before the check>", "base": "…", "scope": "code",
 "log": "iteration-001/3f9c2a1b-test-01.log", "log_sha256": "…",
 "artifacts": [{"path": "…/3f9c2a1b-test-01.junit.xml", "sha256": "…", "size": 1234, "mtime_ns": 0}],
 "check": {"version": 1, "id": "pytest", "type": "test", "adapter": "junit", "index": 1, "cwd": ".", "timeout_seconds": 180,
           "started_at": "…", "finished_at": "…", "duration_ms": 2140,
           "environment": {"id": "H-…", "system": "Windows", "release": "11", "machine": "AMD64", "python": "3.13.3", "wsl": false},
           "snapshot": {"before": {"available": true, "head": "…", "branch": "patchrondo/T-…", "git_dir": "…", "dirty": true,
                                   "changes": 1, "fingerprint": "…", "worktree": "…"}, "after": {"…": "…"}, "changed": false},
           "report": {"format": "junit", "path": "iteration-001/3f9c2a1b-test-01.junit.xml", "sha256": "…", "source": "{report}",
                      "suites": 1, "counts": {"tests": 12, "passed": 11, "failed": 0, "errors": 0, "skipped": 1}},
           "reason": "12 tests: 11 passed, 1 skipped", "error": null}}
```

| Field asked for | Where it is |
|---|---|
| `evidence_id` | `id` (`E-0001`, `E2-0001` after a restart), numbered by the ledger |
| `schema_version` | `version` of the record, and `check.version` of what the runner observed |
| `task_id`, `criterion_ids` | The ledger is the task's own file; `criteria`, as candidates |
| `verification_id`, `run_id`, `type` | `check.id`, `run`, `check.type` |
| `source` | `kind` `command` with `executor` `runtime`: only the runtime writes it (`evidence.EXECUTORS`) |
| `status` | `result`; `verification.status_of` gives the typed `Status` (`timeout` is `ERROR`) |
| `repository_revision`, `workspace_snapshot` | `check.snapshot.before.head`, `check.snapshot`; `fingerprint` binds the record to the files before the check |
| `command`, `exit_code` | `command` as it ran, `exit_code` (none when it never started) |
| `started_at`, `finished_at`, `duration_ms` | In `check`, to the millisecond, UTC |
| `environment_id` | `check.environment.id`: a digest of the system, the interpreter and the computer's name, which is not kept |
| `artifacts`, `error` | `log`, `log_sha256`, `artifacts`, `check.report`; `check.error.kind` and `check.reason` |

`stale` is reserved in `verification.STALE`: no version writes it, and
evidence that stopped counting is named by an `invalidation` record.

### In the loop

The `tests_started` event names the run (`run`) and is saved before the
first check starts; `tests_completed` names it again with the records it
wrote (`evidence`). A run with a start and no completion in the history is
one whose outcome is not known, and none of its checks counts unless its
record is in the ledger. No new event exists, so the loop's sequence of
events does not change. The journal's `test` events name the check and,
at the end, its record.

`state["tests"]` keeps, for each check, what it kept before (`command`,
`status`, `returncode`, `log_file`, `output_tail`) and `check`, `type`,
`evidence`, `duration_ms`, the reason as `message` when it did not pass and
the report's `counts`. The reviewer reads that list under its heading, told
that the developer's word about tests is a claim; the evidence it is shown
is built from the ledger each time (`verification.describe`), and the
developer reads the list in `feedback.md`.

| After the run | The task |
|---|---|
| Every check passed | Goes to the review, as before |
| A check failed | Goes to the review, which cannot approve it, then to the developer, as before |
| `timeout` | Pauses with `timeout` at `test`, as before |
| `error` | Pauses with `verification_error` at `test`: no agent is told |
| `blocked` | Pauses with `verification_blocked` at `test`: no agent is told |

A Resume runs the checks again: a check is meant to be repeatable, and its
earlier records stay. A criterion whose proof is a run that gave no result
is `PENDING` with the reason, not `FAILED`: nothing was learned about the
code. The test context of a task (`_test_context`) gains `checks`, the
identity of each configured check, only when there is one, so the context
saved by an earlier version stays equal and a changed check makes the saved
results stale. A quota pause after the tests keeps them: the review resumes
on the same records.

`PATCHRONDO_VERIFICATION=0` runs `tests.commands` as the versions before
this one did, and records them after the last one ends; the
`verification` section is then not run, and a task that has nothing else
pauses with `configuration`.

### Limits

- The command under test runs with the user's rights. It can print what it
  likes and write any report where it is told to. The runner reads a report
  only from where this run was told to write it, refuses one left from
  before, compares it with the exit code and refuses a run that changed the
  task's files; a test suite written to forge a passing report can still
  do it, and the review of its changes is what finds that. Nothing here is a
  signature.
- Masking is best effort: the values of this process's variables named like
  secrets, and the shapes `activity.redact` knows. A secret printed in
  another shape stays in the log, which is private to the user.
- The snapshot needs Git. Without it the record says which facts are
  missing, binds itself to no files and so never counts for the task's
  files. Ignored files and submodule contents are outside the fingerprint.
- `report_path` names one file. A tool that writes one report per module
  (Maven Surefire) needs a single merged file.
- Required tests are matched by name, never by what they test.
- On native Windows a child that starts before it is put in the job object,
  or a process that leaves the job, is not stopped with it. Agent CLIs are
  still stopped alone, as before.
- Nothing marks a check as unrepeatable: a Resume runs every check again.
