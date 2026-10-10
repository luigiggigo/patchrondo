# Architecture and handoff contract

## 1. Components

| Module | Responsibility |
|---|---|
| `cli.py` | CLI parsing, user input validation, `init/new/run/resume/status/report/list/index/search/ui/doctor` commands |
| `core.py` | Deterministic state machine and completion criteria |
| `providers.py` | Claude/Codex CLI adapters and structured JSON output |
| `process.py` | Process stdin/stdout, timeouts, group cleanup and filtered environment |
| `gitops.py` | Separate worktrees and branches, Git status |
| `storage.py` | Atomic JSON state, private directories, locks and process PID checks |
| `report.py` | Auditable reports with files, tests, reviews and events |
| `rag.py` | Optional incremental SQLite FTS5 index and retrieval of repository excerpts |
| `ui.py`, `static/dashboard.html`, `static/*.webp` | Loopback dashboard; token-protected JSON API to read state, create tasks, start `patchrondo run` processes and edit test settings; mascot images inlined into the page at startup |

Models do not call each other directly. The orchestrator invokes each model and
stores state, feedback and evidence.

## 2. Invariants

- A task is `DONE` **if and only if** review is approved, all tests pass and changes exist (or `allow_no_changes=true`).
- File fingerprints and configured test commands must match the test checkpoint before and after review. Changes during a pause require fresh tests. Ignored files, external services and submodule contents are not fingerprinted.
- The developer cannot award itself an `APPROVED` verdict.
- Claude reviewer uses read-only file tools; Codex reviewer uses the `read-only` sandbox and can execute commands allowed by it. Change requests are passed to the developer as data.
- State moves between atomically saved checkpoints: `develop` -> `test` -> `review` -> `complete`, or `develop` in the next iteration.
- If an agent subprocess fails, the loop stops in `PAUSED`. A failed test proceeds to review so the reviewer can provide useful feedback; test timeouts pause at the test phase.
- The orchestrator does not automatically merge, push, commit or install dependencies.
- The task branch is separate from main. The external test runner executes project commands on the host and requires explicit consent.

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
timeouts, Claude turns, `allow_no_changes` and explicit trust in the configured tests.

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

## 7. Dashboard and demo lifecycle

`ui.py` serves the bundled dashboard with `ThreadingHTTPServer` on `127.0.0.1`.
The API checks Host headers and a per-session token; writes additionally check
Origin when supplied and require JSON. Task creation delegates to `create_task`;
test settings retain the core's explicit boolean consent validation. The editor
round-trips argv arrays using Windows C runtime quoting on Windows and POSIX
quoting elsewhere. Neither form invokes a shell.

Each Run/Resume launches a separate `python -m patchrondo ... run` process with
absolute import paths, so source-only checkouts work after changing the child's
working directory to the state directory. Existing task locks and phase
checkpoints remain authoritative. The dashboard registers the process immediately
and reports startup failures detected during the first 1.5 seconds.

A condition variable guards `closing`, the count of pending starts and the
registered processes. Once closing begins, new Run requests return 409. Shutdown
waits at most 30 seconds for admitted starts, then returns `RunsAtExit(active,
pending)`. Request threads are daemon threads; silent connections have a
30-second timeout and do not delay shutdown. Started runs continue independently.

`tools/demo_dashboard.py` initializes a temporary repository with tests disabled.
It deletes the demo only after `serve()` returns normally and no active process,
pending start or `.run.lock` remains, unless `--keep` was requested. A timeout,
interruption or shutdown error preserves the files and prints their location.
A second Ctrl+C during shutdown returns exit code 130. Preparation failures can
be cleaned up because the dashboard has not yet admitted any runs.

`tools/provider_e2e.py` is the only tool that calls the real adapter, and only
with `--authorize-provider-calls`. It creates a temporary repository and state
directory, runs `run_task` once per pairing and inspects the saved state: the
developer's handoff and file, unchanged Git history, a parsed review and an
unchanged worktree fingerprint after review. Fixture tests stay disabled unless
`--run-fixture-tests` is given. Agent-written code is checked statically and
never imported. Files are deleted only after every check passes.
