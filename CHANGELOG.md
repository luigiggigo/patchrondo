# Changelog

## Unreleased

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
