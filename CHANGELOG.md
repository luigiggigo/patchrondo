# Changelog

## Unreleased

- Add `patchrondo ui`, a token-protected local dashboard (standard library only,
  loopback-bound) with live task status, reviews, tests, history and documents. It can
  create tasks, start or resume runs as separate CLI processes after confirmation,
  and edit test settings with explicit trust consent.
- Add `init.sh` (environment, install, configuration and CLI check) and `main.sh`
  (dashboard or `--demo` with sample data) for a two-command start.
- Keep demo tests disabled until explicit user consent. Preserve demo files when
  runs are active, starts are pending, locks exist or shutdown is interrupted.
- Track run starts during dashboard shutdown, reject new starts once closing,
  bound the startup wait and prevent idle connections from delaying exit.
- Support child runs from source-only checkouts and report immediate startup errors.
- Preserve command arguments through the test editor using Windows or POSIX
  quoting rules, including embedded quotes and trailing backslashes.
- Extend local simulated-provider validation to 65 tests; native Windows skips
  the POSIX process-group test. Authenticated provider workflows remain unvalidated.

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
