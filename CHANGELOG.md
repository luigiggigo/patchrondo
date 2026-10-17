# Changelog

## 0.2.1 — zero configuration and self-healing

Source published on `main` in commit `624f60e` on October 12, 2026 (00:30
+02:00). No version tags or GitHub Releases exist as checked that day. The
[technical review](docs/REVIEW.md#zero-configuration-and-self-healing-october-11-2026)
records what was checked, where, and what was not. The engine keeps every
state transition of 0.2.0; what is new runs before the first agent call and
before each review.

### Saved criteria that cannot be read pause the task, and statuses are shown as the ledger has them — October 17, 2026

- **A saved record of the criteria that this version cannot read is no longer
  replaced.** Since "Criteria records kept when they cannot be read" below,
  such a record, one written with a later schema version included, was kept
  aside in `acceptance.unread-<digest>.json` and then rebuilt from `task.md`,
  and the run went on with the rebuilt record: the reviewer was called again,
  criteria only a later version knew were left out, and what an author set
  beyond the text (`required`, kinds, dependencies), which `task.md` does not
  hold, was lost. The task now pauses with `acceptance_unreadable` before any
  agent, environment check or project command. The record and the ledger stay
  as they were, so nothing is kept aside any more; `report.md` says nothing
  about criteria it cannot read; a Resume while the record still cannot be
  read returns the saved state; and checking, attaching evidence and deciding
  about a criterion are refused for such a task. A task created before
  criteria were tracked, with no record at all, still gets its criteria from
  `task.md`.
- **A null record is not a task from before criteria were tracked.**
  `Acceptance.load` returned nothing for `"acceptance": null`, as for a
  state without the key, so a run rebuilt the criteria from `task.md` and
  lost what only the record held. Only a state without the key is such a
  task now. A null is refused like any record this version cannot read: the
  task pauses with `acceptance_unreadable`, and `report.md` says that the
  saved criteria cannot be read instead of saying nothing about them.
- **Every field of a saved record is checked.** `load` checked the type of a
  criterion's `review`, `authorization`, `classification`, `requires` and
  `toolchains`, not what they held, and fell back to defaults for an
  approval of the code, an outcome or capabilities it could not use. A
  review saved as `{"outcome": []}` was loaded and stopped the run with a
  `TypeError` when the criterion was planned. These fields, and the
  executor, the verifier, `ledger`, `source` and the dates, now need the
  shape this version saves, or the record is refused with the field named
  and nothing replaces it. A field the record lacks still takes its default.
- **What shows criteria shows them as the ledger has them now.** The task
  page, the list of tasks and `patchrondo criteria` showed the statuses as
  last saved, so a `VERIFIED` whose evidence the ledger had since invalidated,
  or no longer held intact, was still shown as verified, and a task as
  release-ready. They now judge a copy of the record in memory against the
  ledger as it is (`acceptance.judged_now`) and write nothing; what was saved
  is kept apart, as `saved_status` for each criterion and `saved` for the task
  in the interface, and "(saved as …)" on the command line. A ledger that no
  longer holds, unchanged, the records the saved record was judged from (one
  removed from its end, or one changed) confirms nothing there
  (`evidence.Ledger.for_state`): the record that is gone may be the
  invalidation the last judgement counted, so none of its records counts, as
  the next run, which starts the ledger again, will find. The file is not
  touched.
- In `tools/ui_e2e.py`, the `criteria` scenario waits until the page knows the
  project it registered before it opens the form, and a press waits for its
  button to be enabled: two races that made the scenario fail now and then.
- Sixteen protections were added to `tools/recovery_checks.py`, and the one
  for the copy kept aside was removed.

### Validation responsibilities — October 17, 2026

- The full unittest matrix and package build/metadata checks belong to GitHub
  CI, without requiring duplicate runs during development or before a push.
  Local validation keeps the checks CI does not perform: browser, reliability,
  recovery protections and real-clock checks, opt-in sandbox checks and
  publication scanning, as applicable. Focused regression tests remain
  available locally; manual package inspection belongs to versioned releases.

### Criteria records kept when they cannot be read — October 16, 2026

- **A saved record of the criteria that cannot be read is kept, not
  overwritten.** A task's `acceptance` record that this version cannot read,
  such as one written with a later schema version, was replaced at the next
  run or check by one read back from `task.md`, and only a note in the event
  history said so. Before it is replaced, it is now kept aside unchanged, with
  the task, the time and the reason, in `acceptance.unread-<digest>.json` in
  the task folder, and the `acceptance_planned` event names the file. What
  the engine does with the task is unchanged: its criteria are read back from
  `task.md` with nothing taken as verified, as before.
- **The evidence of a saved criterion names records of the ledger.** `load`
  refuses a criterion whose `evidence` holds anything but ledger record IDs,
  a criterion saved as `VERIFIED` that names no record, and a schema
  `version` that is not the integer 1 (`true` was read as 1). The engine never
  writes any of these; a record that has one is handled as unreadable. The
  error names the schema version it found.
- Four protections were added to `tools/recovery_checks.py`.

### A change to the code only for what the code can satisfy — October 16, 2026

- **A change to the code asked for a criterion that no change to the files
  can satisfy no longer goes back to the developer.** The reviewer could mark
  `CODE_CHANGES_REQUIRED` a criterion the plan gives to the user or to the
  project's commands, such as "Verificare la mod dentro Mod Organizer 2".
  `settle_review` took it as a change to the code: an approval became a
  request for changes, a request for changes was not settled, and
  `feedback.md` listed the criterion as the developer's work, so every
  iteration asked the developer again for what it could not do, until the
  iteration limit blocked the task. That outcome now counts only for a
  criterion a change to the files can satisfy (`Acceptance.for_reviewer`, a
  criterion about the code, also after a review handed it over). For any
  other it is kept with the criterion as the reviewer's opinion, listed in
  `refused_changes`, and decides nothing. A request for changes whose only
  change is of this sort is settled as an approval (`normalized:
  "no_code_change_requested"`), and the criterion waits for the user. An
  issue of kind `CODE_CHANGES_REQUIRED` names no criterion and still keeps the
  code from being approved.
- The reviewer's instructions say which criteria take
  `CODE_CHANGES_REQUIRED`. The task page and `report.md` say when a change
  was asked for a criterion no change to the files can satisfy. Three removed
  protections were added to `tools/recovery_checks.py`, and the pattern of
  one follows the new condition. The review answer had still not been
  confirmed with a call to an authenticated CLI on October 16, 2026.

### The review settled by kind, and an outcome for each criterion — October 14, 2026

Published on `main` in commit `b9e5283` on October 16, 2026 (22:33 +02:00).

- **The kind of an issue decides, not its severity.** An approval with an
  issue of kind `CODE_CHANGES_REQUIRED` is a request for changes whatever the
  severity of that issue. Before, only a `high` or `critical` one turned an
  approval around, so the code could be approved with a change the reviewer
  itself had marked as required. A new kind, `OBSERVATION`, is a remark about
  the code that asks for no change: it never blocks an approval and is not
  sent to the developer. An issue without a kind, from an answer in the
  earlier shape, keeps the earlier rule. The settled answer keeps the
  reviewer's verdict in `reported_verdict`, with `normalized:
  "code_change_named"`.
- **An outcome for each criterion of the reviewer.** For a task whose
  criteria were given when it was created, an approval that gives no outcome
  to a criterion the reviewer verifies is not a complete review: the task
  pauses at the review with `invalid_review`, nothing of the answer is
  recorded, and Resume asks for the review again. An approval no longer
  verifies a criterion it did not list. An approval saved before this that
  left such a criterion out stops approving the code when the criteria are
  judged again, and the next run asks for a new review without a new
  development step. Tasks created before criteria were tracked, whose
  criteria are read back from their `task.md`, keep the earlier rule: there an
  approval covers what it did not list.
- The task page says when an issue is an observation, and `report.md` names
  the kind of each issue. The scripted reviewers of the tests and tools now
  answer as the schema given to the CLIs requires, with an outcome for each
  criterion. Eight removed protections were added to
  `tools/recovery_checks.py`. The review answer had still not been confirmed
  with a call to an authenticated CLI on October 14, 2026.

### Documentation structure — October 14, 2026

Published on `main` in commit `b376702` on October 14, 2026 (22:54 +02:00).

- Shortened the README to the project overview, workflow, requirements, quick
  start and essential safety limits. Moved the operational reference to
  `docs/USAGE.md`, validation instructions to `docs/TESTING.md` and future
  development areas to `docs/ROADMAP.md`. Dated validation results remain in
  `docs/REVIEW.md`.

### Refused writes answered on Windows — October 14, 2026

Published on `main` in commit `8fe1f47` on October 14, 2026 (22:49 +02:00).

- The interface no longer loses its answer to a write it refuses on the
  headers: a missing or wrong session token, a foreign Host or Origin, a
  content type that is not JSON or a size out of bounds. A client may send the
  headers and the body separately, as Python's `http.client` does. When the
  server had answered and closed the connection before the body arrived, the
  body met a closed socket and the connection was reset, and on Windows the
  client lost the answer it had not read yet: it saw a network error instead
  of the refusal. After an answer the server now closes only its sending
  side, then reads and discards what the client still sends, at most 256 kB
  and for at most two seconds, until the client closes. A refused body is
  still never processed, and the checks keep their order.
- The fault dates from the interface of 0.2.0. It was found by the CI run for
  `303a8d3`: one of six jobs (Python 3.13 on `windows-latest`) failed in
  `test_every_api_route_requires_the_session_token` with
  `ConnectionAbortedError` (WinError 10053); the other five passed. A
  regression test sends the body after the answer. Against the previous
  server it fails on native Windows with the same error and on WSL2 with the
  reset recorded on the socket. A mutation check protects the correction.

### Capability and acceptance engine — October 13, 2026

Published on `main` in commit `303a8d3` on October 14, 2026 (22:14 +02:00).

A release task had reached its sixth iteration with `CHANGES_REQUESTED` every
time. Its developer had file tools only and was asked, again and again, for
downloads, a C++ build, real inference, archives, a pull request and a check
inside another program, while its code was correct and its tests passed. The
loop had one verdict for everything and one place to send it.

- **Criteria are records.** Every acceptance criterion of a task has an ID
  (`AC-01`, …), a kind, what it needs, who does it, who verifies it, a status
  with its reason, and the evidence that counts for it. They are saved with
  the task state under `acceptance`. Kinds: `CODE`, `TEST`, `BUILD`,
  `INTEGRATION`, `EXTERNAL`, `PUBLISHING`, `MANUAL`. Statuses: `PENDING`,
  `IN_PROGRESS`, `VERIFIED`, `FAILED`, `BLOCKED_CAPABILITY`,
  `AWAITING_AUTHORIZATION`, `AWAITING_MANUAL_VALIDATION`, `UNKNOWN`.
- **Classification by fixed rules, corrected by the author.** The kind is read
  from the text of a criterion, in English and Italian; what cannot be placed
  is `UNKNOWN` with its reason. A leading tag (`[manual]`, `[build]`,
  `[external]`, `[publishing]`, `[integration]`, `[test]`, `[code]`,
  `[optional]`) or the fields of the API decide over the rules. No model
  classifies anything.
- **Capabilities per executor.** `capabilities.py` reports what the developer,
  the reviewer, PatchRondo itself and the user can do, each as observed,
  configured, absent or unknown, from the agent settings, the test consent,
  the environment check and PATH. Nothing is started for it. A developer
  without a shell no longer stops a criterion that PatchRondo can verify with
  the project's own commands.
- **A preflight when a task is created**, and in the *New task* form while the
  criteria are typed: how many are automatic, need a tool, need an
  authorization, need a check by the user or are unclear, and which
  capabilities are missing. It never stops a task from being created.
- **The review is about the code, with an outcome per criterion.** The
  reviewer is given the criteria, the capabilities and the recorded evidence,
  and answers `SATISFIED`, `CODE_CHANGES_REQUIRED`, `VALIDATION_REQUIRED`,
  `CAPABILITY_BLOCKED`, `APPROVAL_REQUIRED`, `MANUAL_ACTION_REQUIRED` or
  `NOT_ASSESSED` for each; an issue has a `kind`. The backend validates the
  answer against the plan. A request for changes that names no change to the
  code asks nothing of the developer and starts no development step; an
  approval that names one is not an approval. What the reviewer says about a
  criterion it cannot verify is recorded and is not evidence. An answer in the
  earlier shape is still a review of the code as a whole.
- **Approved code is not a finished task.** New results beside the five
  statuses: `CODE_APPROVED`, and `AWAITING_VALIDATION`,
  `AWAITING_AUTHORIZATION` or `RELEASE_READY`. A task is `done` only with the
  code approved and every required criterion verified. Otherwise it pauses at
  a new `validate` checkpoint with `awaiting_validation` or
  `awaiting_authorization`. No agent is called there, and resuming while
  nothing changed returns the saved state.
- **Evidence ledger.** `<task>/evidence.jsonl` records each command PatchRondo
  ran (exit code, log and its hash, what it covers), each validated review,
  each check and each decision of the user, with the fingerprint of the files
  it was made on. Records are chained by hash and never edited; evidence about
  files that changed, or about an artifact that changed, is invalidated by a
  later record, and the criterion is open again. A ledger that fails its check
  is put aside and started again.
- **Three actions of the user**, in the interface and on the command line
  (`patchrondo criteria`, `evidence`, `authorize`), none of which calls an
  agent or a command: attach the evidence of a check (a statement alone is
  refused; a file is hashed by PatchRondo), authorize or decline an operation
  (nothing is run either way), and check again. A check that did not pass goes
  to the developer at the next run.
- **Criteria and checks** on the task page: approval of the code and the task
  as a whole shown apart, progress, one row per criterion with who does it,
  who verifies it, why it is open, what it takes and its evidence; *Who can do
  what in this task*; a *Validate* step in the workflow.
- **Tasks created before this** keep working. One that ended is left as it
  is, with nothing said about each criterion. One that runs again gets its
  criteria from its `task.md`, with nothing taken as verified.
- **Limits.** PatchRondo runs only the commands a project configured and
  allowed: it does not build, package, download or publish by itself, and an
  authorization is a recorded decision that gives nobody a permission. The
  review answer with an outcome per criterion had not been confirmed with a
  call to an authenticated CLI on October 13, 2026.
- Twenty-six removed protections were added to `tools/recovery_checks.py`, a
  `criteria` scenario to the browser checks, and a sample task with criteria
  to the demo.

### Test consent before the review — October 13, 2026

Published on `main` in commit `b7347fe` on October 13, 2026 (21:20 +02:00).

- **No review without the test consent.** With tests not enabled, a task
  pauses at the test checkpoint with `tests_disabled` after development and
  before the reviewer is called. Until this change it was reviewed once and
  then paused, and every Resume without the consent called the reviewer
  again.
- **Resume is idempotent.** Resuming such a pause while tests are still not
  enabled returns the saved state: no provider call, no environment check, no
  history event.
- **A saved review checkpoint.** When tests are disabled after a task reached
  the review, the next run goes back to the test checkpoint with
  `tests_disabled` and replaces the saved results with a skipped entry.
  Enabling tests runs them again before the review. `CHANGES_REQUESTED` still
  sends the feedback to the next developer iteration, within
  `max_iterations`.
- **An access problem is still reported first.** The reviewer's environment
  check runs before the pause for the missing consent, so a problem the review
  would meet is not hidden behind `tests_disabled`.
- **Real-provider check.** `tools/provider_e2e.py` without
  `--run-fixture-tests` makes one developer call per pairing and checks the
  developer side only; the reviewer's checks are printed as `not exercised`
  and counted in the result line, never as passed. With `--run-fixture-tests`
  the developer and the reviewer are checked as before.
- The pages and the documents that said "developed and reviewed once" say
  what now happens. Three removed protections were added to
  `tools/recovery_checks.py`.

### Agent settings — October 12, 2026

Published on `main` in commit `77cdc14` on October 12, 2026 (23:37 +02:00).

- **Agents page.** A card for the developer and one for the reviewer: who
  plays the role, a preset (*CLI default*, *Fast*, *Balanced*, *Max quality*,
  *Custom*), the model and the effort; *Advanced* unfolds every other setting
  of that CLI by category, each with a description, the option it becomes and,
  when it cannot be used, the reason. Custom presets can be saved, renamed,
  duplicated, replaced and deleted.
- **Independent settings per role and provider.** Model, effort, permission
  mode or sandbox, approval policy, profile, tools and deny rules, additional
  folders, limits (turns, budget, timeout), fallback model, MCP configuration,
  prompt additions, session and streaming choices, Codex configuration
  overrides and custom arguments. One provider can play both roles with two
  models.
- **Three levels with visible origin.** Global preferences
  (`<home>/agents.json`), a project (`agent_settings` in its `config.json`)
  and a task (`agent_settings` in its `state.json`); the nearest level that
  sets a value wins and the page shows where each value comes from. Homes and
  tasks without these keys behave as before, and with no settings every
  command is the one PatchRondo already ran.
- **What the CLIs offer.** Refresh asks the installed CLIs for the options of
  their help and Codex for its model catalog and profiles, without a model
  call. A setting whose option the installed CLI does not list is disabled
  with that reason. A model that nothing confirmed is marked as not verified,
  and a test call, made only after an explicit confirmation, settles it.
- **Really applied, and frozen per step.** Every call receives the model,
  effort, permissions and overrides of its role. The settings in effect are
  frozen when a step first starts and saved with the task (`agent_runs`); a
  resume, a retry and an automatic recovery repeat the step with them, and an
  edit applies from the next step or, on request, to the stopped one. The
  task page, the report and Live Activity name the model and effort used.
- **Test configuration.** Installation, login, options the installed CLI
  does not list, the model, missing folders and acknowledged risks, with a
  preview of the command (credentials masked). Nothing is started.
- **Two new failure kinds.** `model_unavailable` and `unsupported_option`
  pause the task with an explanation and the way to fix it; neither is
  retried by automatic recovery.
- **Safety.** The reviewer stays read-only and Claude Code stays
  `--restricted`, whatever is set. What widens the developer needs an
  explicit acknowledgement per role and level; options that remove a
  protection are refused. Values are typed and validated, model names cannot
  be read as options, and values that look like credentials are not stored.
- **Command line.** `patchrondo new` accepts `--developer-model`,
  `--developer-effort`, `--reviewer-model` and `--reviewer-effort`.
- Not confirmed with an authenticated CLI: the options come from the help of
  Claude Code 2.1.295 and codex-cli 0.161.0 and from the documentation of
  both, read on October 12, 2026. See the
  [technical review](docs/REVIEW.md#agent-settings-october-12-2026).

### Activity journal reader — October 12, 2026

Published on `main` in commit `a529273` on October 12, 2026 (20:43 +02:00).

- Reading a task's activity journal no longer fails when the reader meets the
  file in the instant between its creation and the writing of its first line.
  The reader measured the file before reading that line; when the line arrived
  in between, the measured size was smaller than what had already been read and
  the read raised an error, which ended an open activity stream. The size is
  now measured afterwards and never taken as smaller than the first line. Such
  a journal is read as empty, and what is written next is read from the cursor
  that answer gave.
- The fault is in the reader added with live activity in 0.2.0, not in the
  0.2.1 changes. It was found by the CI run for `624f60e`: one of six jobs
  (Python 3.13 on `ubuntu-latest`) failed in
  `test_events_reach_an_open_stream_within_a_second_and_in_order`; the other
  five passed. A regression test reproduces the same error against the
  published reader without depending on timing, and a mutation check protects
  the correction.

### Why

On native Windows 11 a Codex reviewer could not read the task worktree.
Python 3.13 creates PatchRondo's private folders with an access control list
of SYSTEM, Administrators and OWNER RIGHTS only, and the restricted token of
the Codex read-only sandbox (codex-cli 0.161.0, `[windows] sandbox =
"unelevated"`) is not checked against OWNER RIGHTS. The files were there, the
user could read them, the sandbox got "Permission denied", its shell started
in another folder and Git failed inside it. Both findings were reproduced in a
throwaway home before anything was changed.

### Added

- **Environment check** before any agent is called and again before each
  review (`environment.py`): the CLIs of the task's roles are installed and
  logged in, Git accepts the worktree, and the reviewer can read it and cannot
  write it. The record is saved in the task state under `environment`, with
  every check and its technical detail. Checks that start a real CLI run only
  in runs that start real CLIs; with simulated providers they are reported as
  skipped, never as passed. `PATCHRONDO_PREFLIGHT=0` switches the check off.
- **Reviewer preflight** (`preflight.py`), without a model and without quota:
  on native Windows the sandbox self-check of the installed Codex CLI
  (`codex sandbox -P :read-only --include-managed-config -C <worktree> -- …`,
  read from the help of codex-cli 0.161.0 and confirmed by running it) runs a
  probe that reports, each on its own: the working directory of a process, the
  location PowerShell starts in, a file read by absolute and by relative path,
  Git and its metadata, and that a canary file is intact and no file could be
  created. A write counts as refused only when the disk proves it. A CLI whose
  help does not list the options is not probed.
- **Windows access control policy** (`winacl.py`, `ctypes` only): the
  worktrees folder of a project gets one inheritable allow entry for the
  current user's SID with read and execute; a folder PatchRondo created above
  it gets "read attributes" on that folder only, when the sandbox could not
  otherwise see that it exists. Nothing is granted to a group, no deny entry
  is removed, nothing is elevated, links and junctions are never followed, and
  `tasks/`, `index/` and the configuration files keep their lists. Each change
  is recorded with the list it replaced and rolled back when a later step
  fails or the result does not verify. New homes and new projects are
  prepared when they are created.
- **Repairs** (`repair.py`): the access repair runs unattended, once per
  check, followed by a new check. Trusting a folder Git refuses for its
  ownership is offered only as a decision of the user, for the exact folders
  of that task, never as a wildcard. Every problem has a stable code, a
  category (`repairable`, `consent`, `action`, `temporary`, `permanent`) and a
  sentence without system jargon.
- **One description, one press.** The home page has a box for what should be
  done, the project (with *Choose folder…*), the two roles with their
  defaults, a readiness indicator and one *Start task* button. It creates the
  task and starts it; a repeated press creates nothing twice. The longer form
  stays under *More options*.
- **Preparation on the page.** The workflow starts with a *Prepare* step that
  follows the saved check. A problem found there is one sentence and one
  button (*Repair automatically*, or *Trust this folder and continue* after a
  confirmation); the checks, the paths and what was changed are under
  *Technical details*.
- **Test consent on the task page.** A task paused because the project's
  tests are not authorized shows the commands, asks for an explicit tick and
  resumes. Nothing else enables tests: not adding a project, not *Start task*.
- New API routes: `POST /api/projects/<id>/start` and
  `POST /api/projects/<id>/tasks/<task>/repair`. Task summaries carry
  `environment` and `error_category`; the agent status carries `system`.

### Changed

- **Claude's turn limit has its own failure kind**, `max_turns`, decided from
  the CLI's own `result` line (`error_max_turns`). It was `agent_error` since
  the correction below and `quota` before it. A login or usage diagnostic on
  standard error still decides first. It is never retried automatically. The
  subtype is known from an observed failure, the logs of a task that stopped
  at its 12-turn limit; it was not taken from the provider's source.
- **Git's "dubious ownership" has its own failure kind**,
  `git_dubious_ownership`, instead of `system_error`, with or without the
  environment check.
- The Windows note about access control lists in the README no longer says
  that the program leaves them alone: see above for exactly what it changes.

### Migration notes

- Nothing to do. States and configurations of 0.2.0 and 0.1 are read as they
  are; `environment` is added to a task state at its next run.
- An existing home created by Python 3.13 on Windows is repaired at the first
  run of a task whose reviewer is Codex: one entry on `worktrees`, and
  `environment_repaired` in the task's history. To undo it, the previous list
  of each changed folder is in the task state (`environment.repairs`).
- A manual repair made earlier (read and execute for your user on the home and
  on `worktrees`) is recognized as sufficient and left as it is.

## 0.2.0 — interface first, several projects, live activity

Source published on `main` on October 10 and 11, 2026, including live activity
in commit `c9f6e1e` and the failure classification correction in commit
`6937d9f`. No version tags or GitHub Releases exist as checked on
October 11, 2026. The [technical review](docs/REVIEW.md) records validation
by revision and platform. The engine of 0.1.0 is unchanged except as listed
below.

### Failure classification — October 11, 2026

Published on `main` in commit `6937d9f`.

- A reported streaming CLI error is classified only from its diagnostic and
  standard error. Activity messages and event names no longer turn an ordinary
  failure into a quota or authentication failure. In particular, Claude's
  `error_max_turns` alongside a `rate_limit_event` no longer schedules quota
  recovery. No new provider message form or stream event type is parsed.
- Regression coverage checks both adapters, genuine stderr diagnostics, and
  the task's pause without a recovery plan. A mutation check protects the
  classification boundary. The [technical review](docs/REVIEW.md) records the
  local validation of this correction separately from the published source.

### Live activity of the agents

Added on October 11, 2026. Checked with scripted CLIs only: no authorized real
provider call has confirmed the streaming options or the event formats.

#### Added

- **Live Activity** tab in the task workspace: which agent is working, its
  latest event, the time since the step started and the iteration; the
  agent's public messages, tool calls and their outcome, changed files, the
  project's test commands and results, errors and the end of every attempt.
  Rows are appended one at a time; *Follow* can be switched off; long messages
  and outputs open in place; a filter narrows the list; the full logs are one
  click away. The previous *Activity* tab, the steps recorded by the engine,
  is now called *Timeline*; its address is unchanged.
- `process.execute` accepts `on_stdout` and `on_stderr` observers that receive
  output while the process runs. Calls without observers run as before.
  `LineReader` reassembles lines and UTF-8 characters cut between chunks and
  drops lines longer than 2 MB.
- Claude Code is started with `--output-format stream-json --verbose` and
  Codex with `codex exec --json`. Tools, permissions, sandbox, login,
  `--json-schema`, `--output-schema` and `--output-last-message` are
  unchanged. `streams.py` turns both streams into the same events.
- `<task>/activity.jsonl`: a private, append-only journal with one event per
  line. Each event carries its time, type, provider, role, iteration, attempt
  and a sequence number that grows within the task. Retries and resumes add
  attempts and never rewrite earlier ones; a line cut by a crash is skipped.
- `GET /api/projects/{id}/tasks/{task}/activity` (pages by cursor) and
  `…/activity/stream` (server-sent events), with the checks of every API
  route. A cursor names a position in the journal, so a page that reconnects,
  or an interface that was restarted, continues without losing or repeating
  an event.
- `PATCHRONDO_LIVE_ACTIVITY=0` starts the CLIs with the previous options.
- `tools/provider_e2e.py --authorize-provider-calls` also checks that both
  CLIs reported activity while they worked.

#### Unchanged on purpose

- The outcome of a step comes from the CLI's final result only. A stream with
  events and no result is an `empty_output` failure. A reviewer's message that
  looks like a verdict is recorded as withheld, and a task is not approved
  before the final answer is validated and the tests have passed.
- Classification of quota, authentication and timeout failures, reset parsing,
  the Recovery Manager, the task lock, checkpoints, handoff and report.
- Internal reasoning (Claude thinking blocks, Codex reasoning items) is not
  shown and not saved: not in the journal, not in the step's transcript.
- If a CLI refuses the streaming options before it starts, the step is started
  again with the previous options and the journal says so. No activity is
  shown that a CLI did not report.
- A journal that cannot be written, or an observer that fails, does not fail
  the step.

#### Changed

- With a stream, `<role>.stdout.log` of a step holds the stream's transcript
  without reasoning, bounded to its first and last lines, instead of the
  single result object. The failure message saved in `state.json` prefers the
  error the CLI reported in its stream over raw output.
- The test stage and every agent call record their start and end in the
  journal, also with adapters that do not stream.

#### Tests and tools

- Tests of the observers with real processes (output seen before the process
  ends, both streams at once with megabytes of output, timeout, interrupt, a
  descendant that keeps a pipe open), of the journal (appending, crash damage,
  bounds, cursors), of both parsers with fragmented input, of the adapter and
  of whole tasks with a scripted CLI started as a real process.
- `tools/ui_e2e.py`: a ninth scenario, `live`.
- `tools/recovery_checks.py protections`: eight more safeguards.

### Interface

- `patchrondo` without a command starts the local server and opens the
  interface. It works on a new installation with no `config.json`: the first
  start shows a setup wizard (project, CLI status, default roles, tests, how
  plan limits are used) and writes nothing until its last step. If port 8765
  is taken a free port is used.
- New interface, replacing the single-page dashboard: Home, Tasks, a workspace
  per task (Overview, Activity, Logs, Tests, Review, Files changed, Handoff,
  Report, Advanced), New task, Projects, Settings and About. No page reloads;
  a sidebar that collapses to a rail and becomes a drawer on narrow windows; a
  command palette (Ctrl/Cmd+K); dark theme by default, light and system
  optional.
- A design system with tokens for color, type, space, radius and motion. The
  palette is sampled from the mascot image. Rondo appears in the brand, the
  wizard, empty states, the task note and the About page, using only the two
  official images; moods are a small motion and a status mark, switched off
  under reduced motion. See `docs/DESIGN.md`.
- The page is plain ES modules and style sheets served from the package, with
  no build step and no runtime dependency. Content written by agents is still
  inserted only as text nodes.
- New task as a page: project, title, description, acceptance criteria, roles,
  and optional limits for that task only. *Create and run* asks for the same
  confirmation as *Run* before any agent is called.
- Settings edit everything in a project's `config.json`: workflow limits,
  tests, the Recovery Manager, local retrieval (with *Update index now* and
  the result of the last manual update) and default roles, plus global
  preferences. Each form is validated by the backend before it is written.
  Enabling tests needs the trust checkbox each time the commands change.
- Timeline of the persisted events in words ("Claude started development",
  "Tests passed", "Codex: changes requested"), grouped by iteration.
- Log viewer for the run output and every saved transcript: only the end of a
  file is loaded, earlier output on request, new output is appended while a
  task runs, with Follow, Wrap, jump and Copy. (Corrected on October 11, 2026:
  an answer that arrived after another file had been chosen was drawn into
  that file's view, and a change noticed while a file was opening made the
  page read that file again from its first byte.)
- Changed files of a task with a bounded diff and the text of new files.

### Projects and settings

- Several projects in one home. `projects.json` holds the registry (stable
  `P-…` identifier, display name, state directory) and `settings.json` the
  global preferences. A project added in 0.2 keeps its state in
  `projects/<id>/`, with the layout the whole home had in 0.1.
- A 0.1 home is used where it is: a configuration at the root of the home is
  registered as a project in place, and another 0.1 home can be imported by
  reference. Nothing is moved or rewritten; worktrees keep their paths.
- A repository or a state directory is registered once. The check is repeated
  on the registry read under its mutex, so of two requests made at the same
  time one is refused.
- Removing a project deletes its registry entry only. The repository, tasks,
  worktrees and logs stay on disk and can be imported again.
- A folder picker that lists folder names on this computer, and a read-only
  repository check before anything is registered.
- A project may set default roles (`agents` in `config.json`); global defaults
  are in `settings.json`.

### Process truth and live updates

- The interface no longer shows a task as running because its saved status
  says so. `run` and `resume` record themselves in `<task>/attached/<pid>.json`
  while they live, and the lock records which process instance holds it. The
  backend verifies process ID and start time (`/proc` on Linux, the Windows
  process API, `ps` elsewhere) and answers *unverified* when it cannot.
- Five recovery situations are told apart: running; a process waiting to
  retry; a saved plan with no process; recovery ended; manual intervention.
  A saved plan is never described as a retry that will happen.
- A second automatic run is refused while a process waits for the same task; a
  manual run now stays possible. Simultaneous or repeated starts of one task
  create one process.
- A recorded process that cannot be verified is not taken as gone. A second
  automatic run is refused beside a waiting process of that kind, and a
  project is not removed from the list while one of its tasks has one; the
  task page names the file to delete once a person has checked. (Corrected on
  October 11, 2026: both actions used to be accepted, because only verified
  processes counted.)
- *Stop* interrupts a verified run or a waiting process, like Ctrl+C in its
  terminal (POSIX). *Release lock and resume* is offered when the holder of a
  lock is verified gone (POSIX); the engine still makes its own check. Native
  Windows shows both as unavailable and explains what to do.
- Live updates: one snapshot, then numbered server-sent events read with
  `fetch`, so the session token stays in a header. A gap, a server restart or
  a lost connection leads to a new snapshot; while disconnected the page shows
  the last confirmed state with its time. A hidden tab releases its
  connection. With no page listening the periodic scan stops after fifteen
  seconds and starts again with the next page. (Corrected on October 11, 2026:
  a page that connected at the instant the scan stopped could be left without
  one, and then received heartbeats but no changes until it reconnected.)

### Engine

- A task can override `max_iterations`, `agent_timeout_seconds`,
  `test_timeout_seconds` and `recovery.enabled`. The values are validated with
  the configuration's ranges, saved in `state.json` as `overrides` when the
  task is created and applied by `run_task` and the recovery supervisor. Tasks
  without overrides keep the previous state shape.
- New `tests_started` event, recorded when enabled tests begin.
- `core.validate_config` checks a configuration without reading or writing a
  file; editors use it before saving.
- The task lock also stores the start token of its process. Unlocking does not
  use it.
- On Windows, saving a state file and releasing the task lock wait up to two
  seconds for another process that has the file open, instead of failing with
  "access denied". The interface reads both files while a run writes them, so
  a run could end in the middle of a task, or leave its lock behind.
  (Corrected on October 11, 2026.)

### Command line

- Every 0.1 command still works, and a home with `config.json` at its root
  and no registry behaves as before without a registry being written. Once
  the interface has written a registry in such a home, commands without
  `--project` use the selected project, or the only registered one, as in any
  other home; with several projects and none selected they stop and ask for
  `--project`. (Corrected on October 11, 2026: the root configuration used to
  win over the selection, so `patchrondo new` could create a task in the 0.1
  repository while another project was selected.)
- New `--project ID|NAME` and `patchrondo projects`. Commands that take a task
  ID find the project that holds it. `init --repo` on a home that already has a
  registry adds a project; on a new home it creates the 0.1 layout as before.
- `ui` no longer requires `init`. `--port` and `--no-browser` are also accepted
  before the command.
- `main.sh` opens the interface without requiring a configuration; `init.sh`
  registers a repository only when one is given or found.

### Security

- Unchanged: loopback only, per-session token on every API call, Host and
  Origin checks, JSON-only bounded writes, nonce-based CSP, no external assets.
- The token is taken from the URL fragment, kept for the tab and removed from
  the address bar.
- Static files are served from a list built at startup; log and transcript
  files can only be requested by an identifier from the task's own listing.
- An unexpected server fault is answered in the common error shape with status
  500 instead of dropping the connection.

### Tests and tools

- Tests for the registry, isolation between projects, 0.1 homes, settings,
  input validation, every endpoint's protection, task creation, runs,
  duplicate starts, process verification, an interface restart during a run,
  the event stream and static checks of the shipped page.
- `tools/ui_e2e.py`: browser checks in headless Chrome or Edge through the
  DevTools protocol, with the standard library only (`tools/browser.py`).
- `tools/recovery_checks.py protections` also removes interface safeguards and
  expects the interface tests to fail.
- `tools/demo_dashboard.py` now creates two projects.

### Removed

- `static/dashboard.html` and the API routes of the previous dashboard
  (`/api/overview`, `/api/tasks/…`, `/api/settings/tests`).

### Published as source on `main` after 0.1.0 and included here

These entries describe the development snapshots as they were pushed on
October 9 and 10, 2026. Where 0.2.0 replaced something they mention (the first
dashboard, its routes, mascot images inlined as `data:` URIs, test counts), the
sections above are current.

- Add opt-in automatic quota recovery (`recovery` configuration section,
  `--auto-resume` / `--no-auto-resume` on `run` and `resume`). After a usage
  limit the foreground process saves a retry plan in `state.json`, waits
  without holding the task lock and retries the failed phase at the reset time
  stated by the provider plus a safety margin, or after an exponential backoff.
  Retries are limited by consecutive failures without progress and by a total
  wait budget; a reset beyond the budget is never brought forward. Only `quota`
  failures are retried. Disabled by default: existing configurations and
  commands behave as before.
- Keep the provider and an explicitly stated reset time with quota failures.
  Times without a date or UTC offset (apart from the Codex form below),
  impossible dates and expired resets are not used. Plans are checked again under the task lock, so concurrent waiting
  processes cannot call a provider twice for one plan or before it is due.
  Locks are never removed automatically; a lock found at retry time stops
  recovery with an error.
- Classify a failed CLI run from both output streams. Previously only standard
  error was read when both had content, so a usage limit or reset time printed
  on standard output was missed. Standard error still decides when it names a
  specific cause; the saved diagnostic shows a bounded excerpt of each stream.
- Honor a reset that an adapter states only as a wait in seconds: it is counted
  from the failure time and saved as an instant, and the later one is used when
  an absolute time is also given. Previously only the absolute form reached
  the retry decision. Bound the saved diagnostic of a Claude `is_error` result
  to an excerpt, as for a failed exit.
- Give login failures precedence over usage-limit wording when one message
  contains both; such a failure was previously classified as `quota`.
- Read the reset time that Codex CLI prints in local time without a zone
  (`try again at 3:45 PM.`, `try again at Oct 12th, 2026 3:45 PM.`), for Codex
  failures only. The form comes from the public source of codex-cli 0.160.1
  and has not been observed in a live usage-limit failure.
- Show the saved plan, provider, schedule source, retries used and the reason
  recovery ended in reports, `status`, and the dashboard task page. Record
  `recovery_scheduled`, `recovery_wait_started`, `recovery_retry_started`,
  `recovery_completed`, `recovery_stopped` and `recovery_cancelled` events.
- Add `tools/recovery_checks.py`: `protections` removes one recovery safeguard
  at a time from a temporary copy of the sources and expects tests to fail;
  `real-clock` interrupts and restarts a real wait with synthetic CLIs (POSIX).
- Add an `auto-resume` scenario to the synthetic reliability driver, and
  recovery tests with a simulated clock, bringing the local suite to 156 tests.
  Reset parsing and recovery have been exercised with simulated providers only,
  not with real usage limits.
- Add a local reliability driver with synthetic CLI subprocesses for both role
  pairings: review feedback and correction, developer/reviewer quota recovery,
  POSIX interruption and stale-lock recovery, and a three-file task. Record
  objective acceptance checks, iterations, attempted calls and elapsed times
  in an optional JSON report, without real provider usage.
- Restore GitHub Actions CI for Python 3.11 and 3.13 on Linux, Windows and
  macOS using only free standard runners for public repositories. Skip jobs
  for private repositories, cancel superseded runs and limit jobs to 15 minutes.
  Check packages on Linux without artifact uploads, Actions caches, provider
  calls or automatic publication; retain local publication review.
- Add `patchrondo ui`, a token-protected local dashboard (standard library only,
  loopback-bound) with live task status, reviews, tests, history and documents. It can
  create tasks, start or resume runs as separate CLI processes after confirmation,
  and edit test settings with explicit trust consent.
- Add `init.sh` (environment, install, configuration and CLI check) and `main.sh`
  (dashboard or `--demo` with sample data) for a two-command start. Without a
  path, `init.sh` uses the Git repository of the current directory; relative
  paths resolve from where it is run.
- Keep demo tests disabled until explicit user consent. Preserve demo files when
  runs are active, starts are pending, locks exist or shutdown is interrupted.
- Track run starts during dashboard shutdown, reject new starts once closing,
  bound the startup wait and prevent idle connections from delaying exit.
- Feature Rondo, the mascot, across the dashboard: favicon, sidebar brand, an
  overview greeting summarizing the board, empty and loading states, and a
  per-task status note. Two small WebP images ship in the package and are inlined
  as `data:` URIs at startup, so the page makes no extra requests.
- Support child runs from source-only checkouts and report immediate startup errors.
- Preserve command arguments through the test editor using Windows or POSIX
  quoting rules, including embedded quotes and trailing backslashes.
- Add `tools/provider_e2e.py`, an opt-in check of the real Claude Code and Codex
  CLIs on a throwaway repository with both pairings. It makes no model calls
  without `--authorize-provider-calls`, keeps the fixture's tests disabled unless
  `--run-fixture-tests` is also given, and stops on quota or login failures.
- Fix retrieval in a shared index: restrict matches to the searched worktree
  before taking the top candidates. Previously, more than 300 better-ranked
  chunks from other worktrees could hide relevant excerpts or return none.
- Extend local simulated-provider validation to 74 tests; native Windows skips
  the POSIX process-group test.
- Run two local real-provider checks on October 10, 2026: both pairings passed
  on WSL2 with Claude Code 2.1.291 and codex-cli 0.160.1. The first kept fixture
  tests disabled; the second completed both tasks with passing fixture tests
  and approved reviews.

## 0.1.0 — initial public MVP (source published October 8, 2026)

- Adopt the PatchRondo name for the package, CLI, state directory, environment variable and task branches.
- Local Claude Code / Codex developer and reviewer loop in a dedicated Git worktree.
- Atomic checkpoints, opt-in host tests, pause/resume and local reports.
- Correct Codex global approval argument placement and reject stale final replies.
- Restore Python 3.11 syntax compatibility and validate explicit boolean consent.
- Reject state directories inside the source repository and invalid task phases.
- Invalidate saved test results when worktree files or configured commands change.
- Pause on test timeouts and Git failures; keep diagnostic reports available.
- Bound captured output in memory and clean up POSIX process groups on timeout.
- Add simulated integration tests, publication checks, packaging and GitHub CI.
- Emit CLI output as UTF-8, including when redirected on Windows.
- Use English throughout documentation, CLI messages, task templates and reports.
- Keep local `AGENTS.md` instructions ignored by Git and excluded from public archives.
- Add optional local retrieval (SQLite FTS5/BM25, incremental, shared across worktrees)
  that adds referenced repository excerpts to developer and reviewer prompts, plus
  `patchrondo index` and `patchrondo search` commands.
- Publish the source repository on GitHub, validate the initial CI matrix and
  package checks, and add the Rondo mascot to the README.

Real authenticated provider runs have not yet been validated for this release.
