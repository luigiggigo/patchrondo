# Changelog

## 0.2.0 — interface first, several projects

Prepared in a local working tree on October 10, 2026. At that date it was not
committed, pushed, tagged or released; the technical review records what was
checked and on which platforms. The engine of 0.1.0 is unchanged except where
listed under *Engine*.

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
  task runs, with Follow, Wrap, jump and Copy.
- Changed files of a task with a bounded diff and the text of new files.

### Projects and settings

- Several projects in one home. `projects.json` holds the registry (stable
  `P-…` identifier, display name, state directory) and `settings.json` the
  global preferences. A project added in 0.2 keeps its state in
  `projects/<id>/`, with the layout the whole home had in 0.1.
- A 0.1 home is used where it is: a configuration at the root of the home is
  registered as a project in place, and another 0.1 home can be imported by
  reference. Nothing is moved or rewritten; worktrees keep their paths.
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
- *Stop* interrupts a verified run or a waiting process, like Ctrl+C in its
  terminal (POSIX). *Release lock and resume* is offered when the holder of a
  lock is verified gone (POSIX); the engine still makes its own check. Native
  Windows shows both as unavailable and explains what to do.
- Live updates: one snapshot, then numbered server-sent events read with
  `fetch`, so the session token stays in a header. A gap, a server restart or
  a lost connection leads to a new snapshot; while disconnected the page shows
  the last confirmed state with its time. A hidden tab releases its
  connection.

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

### Command line

- Every 0.1 command still works, and a home with `config.json` at its root
  behaves as before without a registry being written.
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
