# Roadmap

Areas for future development; these are not commitments to a release schedule.

- Optional embedding-based reranking and decision-log indexing, if lexical retrieval proves insufficient.
- Dedicated OS sandbox for tests (Docker/VM with minimal privileges).
- Record real-provider check results per platform and CLI version; extend the check to resumption after an interruption.
- Confirm quota classification and reset parsing against usage-limit failures observed with real CLI versions, including the reset format of Claude Code; consider named time zones and an optional supervisor service that keeps a retry plan attended without a foreground process (not part of 0.2: a saved plan still needs a live `run` or `resume`).
- Process supervision on native Windows: tracking of the whole process tree of an agent, which stale-lock recovery and Stop need before they can be offered there. (0.2 verifies single processes on Windows for display only.)
- Approval requests for risky tools and notifications.
- Stall detection based on Git/test changes.
- An executor of its own for builds, packaging and integration checks, with its own consent, so that criteria of those kinds can be verified without the user doing them by hand. Today the runtime runs only the project's configured commands, and an authorization is a recorded decision.
- Confirm the review answer with an outcome per criterion with authorized real runs of both CLIs.
- Changing the kind of a criterion after the task was created.
- Confirm live activity with authorized real runs of both CLIs, and record the result per CLI version; consider word-by-word output for Claude Code, which today would also carry thinking deltas.
- Confirm the agent settings with authorized real runs: each option against the CLI versions that accept it, and the wording of both providers when a model is not available to an account.
- Continuing a CLI session across the steps of a task (`--resume`, `codex exec resume`), which PatchRondo does not do: every step is a new call that starts from the task's files.
- A diff view per iteration.
- Dedicated illustrations of Rondo for each state (see [docs/DESIGN.md](DESIGN.md)).
- Browser checks of the interface on Linux and macOS, and in Firefox and Safari.
