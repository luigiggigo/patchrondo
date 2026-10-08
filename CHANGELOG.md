# Changelog

## 0.1.0 — initial public MVP (unreleased)

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

Real authenticated provider runs have not yet been validated for this release.
