# PatchRondo

[![CI](https://github.com/luigiggigo/patchrondo/actions/workflows/ci.yml/badge.svg)](https://github.com/luigiggigo/patchrondo/actions/workflows/ci.yml)

**Code. Review. Repeat.**

Experimental MVP 0.1.0.

A **local**, **resumable** Python orchestrator that delegates development to
**Claude Code** or **OpenAI Codex CLI** and asks the other agent to review the
result until tests and review pass or a configured limit is reached.

It uses the **official CLIs** and the logins already configured on your machine.
It does not extract OAuth tokens, use unofficial endpoints, or promise unlimited
usage. Current plan and account documentation is linked below.

**Status: experimental alpha.** No Python runtime dependencies. Tests use
simulated providers; a complete workflow with real accounts has yet to be validated.
This is an independent project, not affiliated with or sponsored by Anthropic or OpenAI.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidance and
[SECURITY.md](SECURITY.md) for security limitations. The
[technical review](docs/REVIEW.md) and [publishing guide](docs/PUBLISHING.md)
document the checks completed before publication.

## Requirements

- Python **3.11+**, Git, macOS / Linux / **Windows through WSL2 recommended**.
- A **trusted** Git repository, initially **clean**, with at least one commit.
- For Claude: install **Claude Code** with `--restricted` support (version 2.1.248 or later), then authenticate with `claude auth login` or start `claude` and sign in with a supported plan.
- For Codex: install **Codex CLI** (`npm install -g @openai/codex`) and run `codex login`, choosing ChatGPT login if you intend to use your subscription.
- CLI usage limits, versions and flags may vary or require updates. The implementation has been tested with simulated adapters, **not** with real accounts.

> **Billing:** if `ANTHROPIC_API_KEY` is set, Claude Code may use API billing instead of your subscription. The runner removes common API environment variables from child processes, but cannot control your personal CLI settings. Run `patchrondo doctor` and check your account login and configuration.

## Installation

```bash
git clone https://github.com/luigiggigo/patchrondo.git
cd patchrondo
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
patchrondo --help
patchrondo doctor
```

On Windows with WSL2, run the Linux commands in your WSL shell. Without installing
the package, run `PYTHONPATH=src python -m patchrondo --help` from the project root.

The command and Python import package are both `patchrondo`. Private state lives
in `~/.patchrondo/`; set `PATCHRONDO_HOME` or pass `--home` to choose another location.
Task branches use the `patchrondo/<task-id>` prefix.

### 1. Configure a repository

```bash
patchrondo init --repo /absolute/path/to/your/repository
```

This creates private task and worktree storage in `~/.patchrondo/`, **outside the
repository**. To use another project, run
`patchrondo --home /path/to/other-state init --repo /other/repository` and pass the
same `--home` to subsequent commands. You can also set `PATCHRONDO_HOME`.

A `--home` inside the repository is rejected to keep private state and logs out
of project files.

### 2. Explicitly authorize tests

Edit `~/.patchrondo/config.json` (or your `PATCHRONDO_HOME/config.json`). The `tests`
section must contain commands **as argument arrays** (no shell), chosen by you
for your project:

```json
{
  "enabled": true,
  "trust_acknowledged": true,
  "commands": [
    ["python", "-m", "pytest", "-q"],
    ["python", "-m", "compileall", "-q", "."]
  ]
}
```

**Do not replace the entire `config.json` with this snippet:** change only the
`tests` section. These commands execute automatically on your computer, so enable
them **only for repositories you trust**. `trust_acknowledged` is explicit consent;
it does not create an OS sandbox. Without enabled tests, the system can implement
and review once, but **pauses** before declaring the task `done`.

A complete reference configuration is in
[`examples/config.example.json`](examples/config.example.json). It also includes
`workflow.max_iterations`, timeouts and the maximum number of Claude turns.

### 3. Create tasks with configurable roles

```bash
patchrondo new \
  --title "Handle JWT expiration" \
  --description "Implement the exp check with tests for expired tokens." \
  --accept "Expired tokens are rejected" \
  --accept "The test suite passes" \
  --developer claude \
  --reviewer codex
```

You can reverse the roles with `--developer codex --reviewer claude`, or provide
a longer specification with `--task-file ./requirements.md` instead of `--description`.

The command returns an ID such as `T-a1b2c3d4e5f6` and creates a Git worktree on
the `patchrondo/<task-id>` branch.

### 4. Run and inspect

```bash
patchrondo run T-a1b2c3d4e5f6
patchrondo status T-a1b2c3d4e5f6
patchrondo report T-a1b2c3d4e5f6
patchrondo list
```

If an agent reaches a quota, times out or is interrupted, the task becomes
`paused`. Resume after addressing the cause:

```bash
patchrondo resume T-a1b2c3d4e5f6
```

If the Python process was killed abruptly and left a `.run.lock`, **first confirm
that no other run is active**, then use
`patchrondo resume T-a1b2c3d4e5f6 --unlock`. The lock is not removed if a recorded
runner, agent or test PID is still active. On native Windows, manual lock removal
is required after a reboot.

Statuses: `ready`, `running`, `paused`, `done`, `blocked`.
Phases: `develop`, `test`, `review`, `complete`.
`blocked` means the review returned `BLOCKED` or the iteration limit was reached:
human intervention and a new task, or a carefully controlled manual state edit,
are required.

## Loop behavior

```text
new -> [Git worktree + task.md + state.json]
           |
           v
       developer CLI
           | handoff.md
           v
       test runner (local; opt-in)
           | results in state.json + log files
           v
       reviewer CLI (read-only)
           | structured review JSON
           +-- APPROVED + passing tests + changes -> DONE
           +-- CHANGES_REQUESTED / failing tests -> feedback.md -> developer (next iteration)
           +-- BLOCKED / too many iterations -> BLOCKED
           +-- quota / timeout / invalid output / Ctrl+C -> PAUSED -> resume
```

AI review is one input: the orchestrator independently checks test results and
whether changes exist. Review comments are feedback, not permission to bypass
checks. State files are saved atomically after every phase. Following an abrupt
interruption, `resume` **repeats only the unfinished phase**, although partially
completed agent actions may require an additional check.

Before and after review, a fingerprint of tracked/untracked files (excluding
ignored files) and the configured test commands is checked. If they differ from
the last test checkpoint, the task pauses with `stale_tests`; `resume` starts at
the test phase. This check does not cover external dependencies, ignored files or
submodule contents. Do not modify these during a task.

## State layout

```text
~/.patchrondo/
  config.json
  empty-hooks/
  index/
    rag.sqlite3   # local retrieval index (rebuildable; safe to delete)
  tasks/
    T-.../
      task.md
      state.json
      handoff.md
      feedback.md
      report.md
      runs/
        iteration-001/
          developer.prompt.md
          developer.stdout.log
          developer.stderr.log
          developer.reply.md
          reviewer.prompt.md
          reviewer.last-message.txt  # Codex
          review.schema.json         # Codex
          reviewer.reply.md
          test-01.log
  worktrees/
    T-.../   # isolated Git worktree for each task
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for state transitions and the
retrieval layer.

Task memory consists of `task.md`, `state.json`, `handoff.md`, `feedback.md` and
previous runs. Retrieved context supplements it but never replaces it.

## Local retrieval (RAG)

Before each developer and reviewer prompt, the orchestrator searches a local
index of the task worktree and adds the best-matching excerpts, with
`path:line-range` references, under *Retrieved repository context*. The query is
built from the task, the latest feedback (developer) or handoff (reviewer) and
the changed file names. No model, network service or extra dependency is used.

- **Engine:** SQLite FTS5 with BM25 ranking from the Python standard library.
  Identifiers are indexed whole and split into subwords (`verifyToken`,
  `verify_token` → `verify token`); English words are stemmed. Matches in
  definition names weigh more than matches in paths, which weigh more than body text.
- **Chunks:** files are split at function/class definitions (methods are named
  `Class.method`) or Markdown headings, with at most 60 lines per chunk and at
  most two excerpts per file in the results.
- **Incremental:** each update checks file sizes and modification times and only
  re-reads changed files. Chunks are keyed by path and content hash, so task
  worktrees reuse what was already indexed for the repository. On the Python
  standard library (643 files, ~13,500 chunks) a cold index takes ~1.5 s, a no-op
  update ~50 ms and a query 1–10 ms.
- **Scope:** only Git-tracked and untracked, non-ignored text files up to 512 KB.
  `.env` files, keys, certificates, logs, lock files, minified assets and binaries
  are skipped. Retrieved text is still repository content: treat it as untrusted.
- **Non-authoritative:** excerpts are hints that may be incomplete; `task.md`,
  `state.json` and test results remain authoritative. A retrieval failure is
  recorded as a `retrieval_failed` event and the prompt is sent without context.

Configure it in the `rag` section of `config.json`:

```json
"rag": {"enabled": true, "max_chunks": 8, "max_chars": 12000}
```

New configurations enable retrieval. Configurations without a `rag` section keep
it disabled. `max_chars` bounds the added prompt size. Inspect the index without
any model call:

```bash
patchrondo index                       # update the index for the configured repository
patchrondo search "jwt expiration" -k 5
patchrondo search "jwt expiration" --task T-a1b2c3d4e5f6   # search a task worktree
```

The index lives in `~/.patchrondo/index/` and can be deleted at any time; it is
rebuilt on the next run. Index files of removed worktrees are cleaned up automatically.

## Security and practical limitations

1. **No shell for orchestrated commands.** Python uses `subprocess.Popen([...], shell=False)`; test commands are argument arrays, not shell-interpreted strings.
2. **Claude developer:** `--restricted --permission-mode dontAsk --tools Read,Glob,Grep,Edit,Write --allowedTools ... --disallowedTools mcp__*`. No Bash or MCP tools are authorized. **Claude reviewer:** only `Read,Glob,Grep`, with the same restricted mode.
3. **Codex developer:** `codex --ask-for-approval never exec --sandbox workspace-write`. **Codex reviewer:** `read-only` sandbox. The runner does not use `--yolo`, approval bypasses or `danger-full-access`. Codex can execute commands allowed by its sandbox: prompt instructions do not disable tools, and personal configuration can affect behavior.
4. **Separate worktree:** the main repository is not edited; no automatic commit, push or merge. Worktree creation disables Git hooks for the checkout command, but **does not create an OS sandbox** or prevent every possible effect of external Git configuration.
5. **Credentials:** prompts are sent through stdin, not shell arguments. State has private permissions on POSIX; the child environment removes several API key/secret/token variables. Local CLI credentials remain managed **by the CLIs**, not the orchestrator. Keep secrets out of the repository: file access and test scripts could expose them.
6. **Test execution:** unlike sandboxed agent tools, test commands run **on the host with your permissions**. A malicious repository can execute arbitrary code through its test suite. For untrusted code, run **the entire orchestrator inside an isolated VM/container**, without production credentials and with appropriate network/firewall settings.
7. **Billing and limits:** subscriptions have quotas and may support additional usage billing. The loop stops on rate limits, timeouts, errors, `BLOCKED` or the maximum iteration count. There is no automatic quota-availability polling.
8. **Fallible LLM review:** the JSON schema checks structure, not review accuracy. Require human review before integrating changes into main or production.
9. **Sensitive logs:** logs and handoffs may contain confidential project data. They are saved locally with private permissions; protect backups and disk storage, and do not share `~/.patchrondo`.
10. **Output and processes:** stdout/stderr are captured in temporary files and bounded in memory. Disk use remains proportional to output until the timeout. On POSIX, timeouts terminate the process group; native Windows terminates only the direct child, so WSL2 remains recommended. POSIX private permissions are not translated into Windows ACLs by the program.

### Final integration

The result stays in the Git branch and worktree. Example, **after human review**:

```bash
cd ~/.patchrondo/worktrees/T-a1b2c3d4e5f6
git status --short
git diff
# Review any untracked files, then:
git add -A
git commit -m "Implement feature"
# In the main repository, when ready:
cd /path/to/repository
git merge patchrondo/T-a1b2c3d4e5f6
```

## Project tests (no provider quota usage)

```bash
python -m unittest discover -s tests -v
# Or without installing the package:
PYTHONPATH=src python -m unittest discover -s tests -v
```

Tests use temporary Git repositories, simulated providers, state checks and mocked
CLI arguments. They do not call Claude or Codex, validate real model output, or
replace an end-to-end test with authenticated accounts.

## Roadmap

- Optional embedding-based reranking and decision-log indexing, if lexical retrieval proves insufficient.
- Dedicated OS sandbox for tests (Docker/VM with minimal privileges).
- End-to-end suite with authorized accounts and CLI version compatibility checks.
- Approval requests for risky tools, a dashboard and notifications.
- Stall detection based on Git/test changes and support for multiple projects in one state directory.

## Official documentation

- Claude Code CLI: https://code.claude.com/docs/en/cli-reference
- Claude Code Pro/Max: https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan
- Codex CLI: https://learn.chatgpt.com/docs/developer-commands?surface=cli
- Codex login: https://learn.chatgpt.com/docs/auth

**License:** MIT. An MVP for local use with trusted projects.
