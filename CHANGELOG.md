# Changelog

## Unreleased

- Remove the GitHub Actions CI workflow, its README badge and its Dependabot
  action updates; retain local test, publication and package checks.
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
- Run the real-provider check once, on October 10, 2026: both pairings passed
  on WSL2 with Claude Code 2.1.291 and codex-cli 0.160.1, with fixture tests
  disabled. A real run through passing tests and approval remains unvalidated.

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
