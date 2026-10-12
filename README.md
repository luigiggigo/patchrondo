# PatchRondo

<p align="center">
  <img src="docs/assets/rondo-mascot-v1.png" alt="Rondo, the PatchRondo raccoon mascot, with a blue patch and a loop-shaped tail" width="240">
</p>

**Code. Review. Repeat.**

<p align="center">
  <a href="https://github.com/luigiggigo/patchrondo/actions/workflows/ci.yml">
    <img src="https://github.com/luigiggigo/patchrondo/actions/workflows/ci.yml/badge.svg?branch=main&amp;event=push" alt="CI status on main">
  </a>
  <a href="#requirements">
    <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.11 or later">
  </a>
  <a href="#project-tests-no-provider-quota-usage">
    <img src="https://img.shields.io/badge/CI%20matrix-Linux%20%7C%20Windows%20%7C%20macOS-475569" alt="CI matrix: Linux, Windows and macOS">
  </a>
</p>
<p align="center">
  <a href="CHANGELOG.md">
    <img src="https://img.shields.io/badge/Status-experimental%20alpha-F59E0B" alt="Status: experimental alpha">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-22C55E" alt="License: MIT">
  </a>
  <a href="pyproject.toml">
    <img src="https://img.shields.io/badge/Runtime%20dependencies-0-14B8A6" alt="Zero Python runtime dependencies">
  </a>
</p>

**Version 0.2.1.** The source has been public on
[GitHub](https://github.com/luigiggigo/patchrondo) since October 8, 2026
(0.1.0). The 0.2.0 interface and live activity are published as source on
`main`, and so is 0.2.1, the environment check with its repairs and the
one-press start, in commit `624f60e` (checked October 12, 2026). One of the six
CI jobs failed for that commit, on a timing fault in the reader of the
activity journal; its correction is a local change of October 12, 2026 that is
not published yet. The repository has no version tags or GitHub Releases; the
[changelog](CHANGELOG.md) says what each version contains and the
[technical review](docs/REVIEW.md) what was checked, where and when.

A **local**, **resumable** Python orchestrator that delegates development to
**Claude Code** or **OpenAI Codex CLI** and asks the other agent to review the
result until tests and review pass or a configured limit is reached. You use
it from a **local interface in your browser**: choose a project, describe what
should be done, press **Start task** and follow the loop live. PatchRondo
prepares the task's copy of the project, checks the agents and the review
sandbox, repairs what it safely can and tells you in one sentence when it
needs you. Every command-line command of 0.1 is still there.

It uses the **official CLIs** and the logins already configured on your machine.
It does not extract OAuth tokens, use unofficial endpoints, or promise unlimited
usage. Current plan and account documentation is linked below.

**Status: experimental alpha.** No Python runtime dependencies, and no build
step or external asset for the interface. Tests use simulated providers.
Real-provider checks passed on WSL2 with 0.1.0, including a complete workflow
through passing fixture tests and approval in both role pairings (see
[Project tests](#project-tests-no-provider-quota-usage)); they have not been
repeated for 0.2.0.
This is an independent project, not affiliated with or sponsored by Anthropic or OpenAI.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidance and
[SECURITY.md](SECURITY.md) for security limitations. The
[architecture notes](docs/ARCHITECTURE.md) describe the modules and the
[design notes](docs/DESIGN.md) the interface. The
[publishing guide](docs/PUBLISHING.md) covers future updates.

## Requirements

- Python **3.11+**, Git, macOS / Linux / **Windows through WSL2 recommended**.
- A **trusted** Git repository, initially **clean**, with at least one commit.
- For Claude: install **Claude Code** with `--restricted` support (version 2.1.248 or later), then authenticate with `claude auth login` or start `claude` and sign in with a supported plan.
- For Codex: install **Codex CLI** (`npm install -g @openai/codex`) and run `codex login`, choosing ChatGPT login if you intend to use your subscription.
- CLI usage limits, versions and flags may vary or require updates. The implementation is tested with simulated adapters; real-account checks cover WSL2 with the CLI versions listed under [Real provider check](#real-provider-check-uses-plan-quota).

> **Billing:** if `ANTHROPIC_API_KEY` is set, Claude Code may use API billing instead of your subscription. The runner removes common API environment variables from child processes, but cannot control your personal CLI settings. Run `patchrondo doctor` and check your account login and configuration.

## Quick start

```bash
git clone https://github.com/luigiggigo/patchrondo.git
cd patchrondo
./init.sh        # virtual environment, install, CLI check (no model calls)
./main.sh        # opens PatchRondo in your browser
```

Or, with the package installed in an environment of your own:

```bash
python -m pip install -e .
patchrondo       # starts the local server and opens the browser
```

On a new installation the first start shows a short setup: choose a local Git
repository, check that Claude Code and Codex are installed and logged in,
choose the default developer and reviewer, decide whether your project's tests
may run, and read how your plan's limits are used. Nothing is written until
the last step and no model is called. After that, describe a task on the home
page and press **Start task**: that one press creates the task and starts it.
Starting, running and resuming invoke the real CLIs and consume your account
quota; the project's tests never run until you allow them.

`init.sh` is safe to run again. Given a path (`./init.sh /path/to/repository`),
or run from inside a Git repository other than PatchRondo's own checkout, it
also registers that repository. Both scripts run on Linux, macOS, WSL2 and Git
Bash.

To look around with sample data first:

```bash
./main.sh --demo
```

## The interface

```bash
patchrondo                         # http://127.0.0.1:8765/#token=… opens in your browser
patchrondo --port 0 --no-browser   # pick a free port and only print the link
```

The link contains a private session token; keep it to yourself. If port 8765
is in use, a free port is chosen. The page keeps the token for that browser
tab and removes it from the address bar. Closing the terminal command stops
the interface, not the runs it started.

| Section | What you do there |
|---|---|
| **Home** | Start a task: the project (or *Choose folder…* for another one), what should be done, the developer and the reviewer with their defaults, a readiness indicator and one **Start task** button. Below it, the state of Claude Code and Codex, tasks that are active, need attention, are ready or done, and recent activity. |
| **Projects** | Add a local Git repository (with a folder picker and a check before anything is registered), switch project, rename, configure, or remove it from the list. Removing never deletes the repository, the tasks or the worktrees, and is refused while a task of the project is running, waiting, or has a recorded process that cannot be verified. |
| **Tasks** | All tasks of the selected project, or of every project, with filters and search. |
| **Task** | Header with status, roles and iteration; the workflow *Prepare → Develop → Test → Review → Repeat / Complete*; then Overview, Live Activity (what the agents are doing, while they do it), Timeline (the steps the engine recorded), Logs, Tests, Review, Files changed, Handoff, Report and Advanced (the raw files). Run, Resume and Stop are here. |
| **New task** | The longer form behind *More options*: project, title, description, acceptance criteria, developer and reviewer; under *Advanced*, limits for that task only. *Create and run* asks for confirmation before any agent is called. |
| **Settings** | Global preferences and, per project: default roles, workflow limits, tests, the Recovery Manager, local retrieval and the raw configuration. Every setting has a one-line explanation. |

Things worth knowing:

- **What you see is checked, not assumed.** A task is shown as *Running* only
  when its lock is held by a process the interface can verify (process ID and
  start time). A saved status of `running` with no process is shown as
  *Interrupted*; a lock whose process is gone as *Stopped unexpectedly*; and
  when the computer cannot verify a process the label says *unverified*.
- **Live.** The page loads one snapshot and then receives numbered changes. If
  the connection drops it says so, keeps showing the last confirmed state with
  its time, reconnects by itself and loads a fresh snapshot.
- **Live Activity.** While a step runs, the task page shows what the agent's
  CLI reports as it reports it. See [Live Activity](#live-activity).
- **Prepared for you, checked before any agent is called.** Every run first
  checks that the agents of the task are installed and logged in, that Git
  accepts the task's folder, and that the reviewer can read it and cannot
  write it. What can be fixed safely is fixed and checked again; anything else
  stops the task where it can resume, with one sentence and at most one
  button. See [Preparation and repairs](#preparation-and-repairs).
- **Tests stay off until you say so.** A task that needs the project's tests
  pauses and asks on its own page: it shows the commands, wants an explicit
  tick and resumes. Neither adding a project nor *Start task* is consent. In
  *Settings → Tests* you enter one
  command per line and tick *I trust this repository*; the tick is asked again
  whenever the commands change. On Windows, use double quotes around arguments
  containing spaces; single quotes are literal characters. On Linux and macOS,
  commands use POSIX quoting rules. Commands are saved as argument arrays and
  executed without a shell.
- **Stopping.** *Stop* interrupts a run, or a process waiting for a retry, like
  Ctrl+C in its terminal. It is available on Linux, macOS and WSL2. On native
  Windows it is shown as unavailable: use Ctrl+C in the terminal of a run
  started from the command line, or let the run reach its next pause.
- **After a crash.** If a run was killed and left its lock, the task page
  offers *Release lock and resume* once the lock's process is verified gone
  (Linux, macOS, WSL2). The engine repeats the check for every recorded agent
  and test process and refuses if one is alive. On native Windows the lock is
  removed by hand, as before; the page shows its path.
- **Keyboard.** Ctrl/Cmd+K or `/` opens search and commands, `N` a new task,
  `G` then `H`/`T`/`P`/`S` goes to Home, Tasks, Projects, Settings.

The server uses only the standard library, listens on `127.0.0.1` only, rejects
foreign `Host` and `Origin` headers, accepts writes only as JSON, requires the
session token on every API call and serves the page with a strict
Content-Security-Policy and no external assets. See [SECURITY.md](SECURITY.md).

### Preparation and repairs

Before the first agent call of every run, also a resumed or automatically
retried one, and again before each review, PatchRondo checks the environment
of the task. No model is called for it and no quota is used. The task page
shows it as the first step of the workflow, *Prepare*.

| Stage | What is checked |
|---|---|
| Agents | The CLI of the developer and of the reviewer is installed and logged in (its own status command; no prompt). |
| Project | The task worktree exists and Git accepts it. |
| Safe environment | With a Codex reviewer: the folders are readable by the review sandbox and, on native Windows, the sandbox itself is asked. |

On native Windows the last stage runs the sandbox self-check of the installed
Codex CLI in the task worktree and compares its answers with the disk: the
working directory of a process, the folder PowerShell starts in, a file read
by absolute and by relative path, Git and its metadata, and that a canary
file is intact and no file could be created. A write counts as refused only
when the disk proves it. The command was read from the help of codex-cli
0.161.0 and confirmed by running it there; a CLI that does not list the same
options is not probed, and those checks are then shown as skipped. On Linux,
macOS and WSL2 the sandbox is not probed.

What PatchRondo repairs by itself is one thing, on Windows: the access control
lists of its own folders, so that the review sandbox can read the task's copy
of the project. It adds read access for *your* user to the `worktrees` folder
of the project, and "read attributes" to a folder of its own above it when the
sandbox could not otherwise see that the folder exists. It gives nothing to a
group, removes nothing, never elevates, never follows a link or a junction,
and leaves `tasks/`, the configuration and everything outside its home alone.
It records what each folder had before, puts it back if the repair does not
verify, and tries once per check.

Everything else is a stop with an explanation, never a workaround:

| The page says | What happened | What you can do |
|---|---|---|
| Codex cannot read the project yet | The repair could not be applied or did not help | **Repair automatically** tries again and resumes |
| Git does not trust this project folder | Git reports "dubious ownership" | **Trust this folder and continue**, after a confirmation: that folder and the task's copy are recorded as trusted for your user, never a wildcard |
| An agent is not installed / Login required | The CLI is missing or logged out | Install it or run its login command, then resume |
| The review environment is not read-only | The sandbox could change files | Nothing is sent to the reviewer; update the CLI or its configuration |
| Claude ran out of turns | `error_max_turns` | Raise the limit or narrow the task; this is not a usage limit and is never retried by itself |

The sandbox is never weakened, approvals are never bypassed and tests are
never enabled to get a task through. `PATCHRONDO_PREFLIGHT=0` switches the
whole check off and a run then starts as it did in 0.2.0.

### Live Activity

PatchRondo asks both CLIs to print what they do while they work, and the
*Live Activity* tab of a task shows it, normally within a second:

- who is working (provider and role), the iteration, the time since the step
  started and the latest event;
- the messages the agent wrote, its tool calls and how they ended, the files
  it changed, each test command PatchRondo ran with its exit status, errors
  reported by the CLI, and the end of every attempt;
- *Follow* keeps the newest row in view and switches itself off when you
  scroll up; long messages and outputs open in place; a filter narrows the
  list; *Full logs* opens the saved transcripts.

What to know about it:

- **It observes and decides nothing.** The handoff, the review verdict, the
  tests and every pause are produced as before, from the CLI's final result.
  What a reviewer writes while it works is progress: text that looks like a
  verdict is shown as withheld, and a task is never approved before
  PatchRondo has validated the final answer and the tests have passed.
- **Internal reasoning is left out.** Claude's thinking blocks and Codex's
  reasoning summaries are not shown, not written to the activity journal and
  not written to the transcript saved for the step.
- **It is recorded.** Events are appended to `activity.jsonl` in the task's
  private directory and never rewritten: a retry or a resume adds a new
  attempt after the earlier ones. Closing the page or the interface loses
  nothing; reopening shows what was recorded and continues from there without
  repeating an event. Text is bounded in length, and values that look like
  credentials are masked on a best-effort basis. Treat the journal like the
  logs: private project data.
- **How the CLIs are started.** Claude Code with
  `--output-format stream-json --verbose` instead of `--output-format json`,
  Codex with `codex exec --json`. Tools, permissions, sandbox and login are
  unchanged, and so are `--json-schema`, `--output-schema` and
  `--output-last-message`. The options were read from the help of Claude Code
  2.1.291 and 2.1.295 and of Codex CLI 0.160.1, and the Codex event format
  from its source at `rust-v0.160.1`. No authorized real run has confirmed
  them yet; the [real provider check](#real-provider-check-uses-plan-quota)
  now tests exactly this.
- **When a CLI cannot stream.** If a CLI refuses these options before it
  starts, PatchRondo starts the step again with the previous options, and the
  view says that the step has no live activity. Nothing is shown that was not
  reported. Set `PATCHRONDO_LIVE_ACTIVITY=0` in the environment of
  `patchrondo` to use the previous options always.
- **Limits.** A CLI may buffer its own output, and Claude Code prints a
  message when it is complete, not word by word. One attempt records up to
  5000 events, with up to 4000 characters of a message; the end of the
  attempt says how many events were not recorded. The view was checked in
  Chrome on Windows with scripted CLIs only.

### Several projects, and homes created by 0.1

Private state lives in `~/.patchrondo/`; set `PATCHRONDO_HOME` or pass `--home`
to choose another location. A home can hold any number of projects, each with
its own configuration, tasks, worktrees and retrieval index.

A home created by 0.1 keeps working without any migration: its configuration
at the root of the home is listed as a project where it is, marked *0.1 home*,
with all its tasks, logs, handoffs and reports. Another 0.1 home (a different
`--home`) can be added with *Projects → ⋯ → Import a 0.1 state directory*; it
is used in place too. Nothing is moved or rewritten, because Git worktrees
record absolute paths.

### Try it with sample data

`./main.sh --demo` (or `python tools/demo_dashboard.py` from a source checkout)
creates two throwaway repositories with sample tasks and opens the interface
with tests disabled. The samples are saved states with no process behind them,
and the interface presents them that way. The demo deletes its files only
after a normal exit confirms there are no active runs, pending starts or run
locks. It keeps them after an uncertain or interrupted shutdown, or with
`--keep`, and prints their location. A second Ctrl+C during shutdown keeps the
files and exits with code 130.

Stopping the interface refuses new runs and waits up to 30 seconds for run
starts already in progress. Active runs continue in their separate processes;
their output is saved as `ui-run.log` in the task directory. Idle connections
do not delay shutdown.

## Command line

Everything can also be done from a terminal, and scripts written for 0.1 keep
working.

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

The command and Python import package are both `patchrondo`.
Task branches use the `patchrondo/<task-id>` prefix.

### 1. Register a repository

```bash
patchrondo init --repo /absolute/path/to/your/repository
```

On a new home this creates the configuration at the root of the home, exactly
as 0.1 did. On a home that already has a project registry (because you used the
interface) it adds the repository as another project. Private task and
worktree storage is always **outside the repository**; a `--home` inside the
repository is rejected to keep private state and logs out of project files.

```bash
patchrondo projects                       # list projects: ID, name, repository, state directory
patchrondo --project shop-api list        # choose a project by ID or name
```

When a home holds several projects, commands use the one selected in the
interface unless `--project` is given. Commands that take a task ID find the
project that holds it by themselves. This also applies to a home created by
0.1 once you have added a second project to it in the interface: the project
at the root of the home is then one project among the others, not the default.
With several projects and none selected, a command that needs one stops and
asks for `--project`.

### 2. Explicitly authorize tests

Use *Settings → Tests* in the interface, or edit the project's `config.json`
(shown by `patchrondo projects`; `~/.patchrondo/config.json` for a 0.1 home).
The `tests` section must contain commands **as argument arrays** (no shell),
chosen by you for your project:

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
`workflow.max_iterations`, timeouts, the maximum number of Claude turns and the
disabled-by-default `recovery` section. An optional `agents` section
(`{"developer": "claude", "reviewer": "codex"}`) sets the roles pre-selected
for new tasks of the project.

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

Claude's `error_max_turns` means the configured `workflow.claude_max_turns`
was reached (12 by default), not that the account quota is exhausted. The task
pauses with the failure kind `max_turns`. Increase that limit in the project's
workflow settings for a larger task, then resume. Automatic quota recovery
does not address this limit.

A run that stops while preparing (see
[Preparation and repairs](#preparation-and-repairs)) pauses with the kind of
the problem, for example `sandbox_access_denied`, `git_dubious_ownership` or
`cli_missing`, before any agent is called. `patchrondo status` shows the
record of the check under `environment`; `resume` checks again.

To let the same process wait for a usage limit to reset and retry by itself, see
[Automatic quota recovery](#automatic-quota-recovery-opt-in). It is disabled by
default.

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
       environment check (no model): agents, Git, review sandbox
           | repairs what it may, checks again; otherwise -> PAUSED
           v
       developer CLI
           | handoff.md
           v
       test runner (local; opt-in)
           | results in state.json + log files
           v
       environment check again (reused while nothing it depends on changed)
           v
       reviewer CLI (read-only)
           | structured review JSON
           +-- APPROVED + passing tests + changes -> DONE
           +-- CHANGES_REQUESTED / failing tests -> feedback.md -> developer (next iteration)
           +-- BLOCKED / too many iterations -> BLOCKED
           +-- quota / timeout / invalid output / Ctrl+C -> PAUSED -> resume
               (quota only, opt-in: wait for the reset or a backoff, then retry)
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

## Automatic quota recovery (opt-in)

By default a usage limit pauses the task until you resume it. With automatic
recovery, the `run` or `resume` process stays in the foreground, waits until
the limit should have reset and retries the phase that failed. It never works
around a limit: it only waits, and each retry is an ordinary CLI call that uses
your plan quota exactly like a manual resume.

```bash
patchrondo run T-a1b2c3d4e5f6 --auto-resume
patchrondo resume T-a1b2c3d4e5f6 --auto-resume
patchrondo resume T-a1b2c3d4e5f6 --no-auto-resume   # one run now; a saved retry plan is cancelled
```

Recovery is active when `--auto-resume` is given or `recovery.enabled` is `true`
in the project's `config.json`. `--no-auto-resume` turns it off for one
command. In the interface the same settings are in *Settings → Recovery*, the
Run dialog has a switch for that run, and a task can be created with its own
default.

```json
"recovery": {
  "enabled": false,
  "quota_only": true,
  "max_consecutive_retries": 3,
  "initial_backoff_seconds": 120,
  "max_backoff_seconds": 1800,
  "max_total_wait_seconds": 86400,
  "reset_safety_margin_seconds": 30
}
```

| Setting | Allowed values | Meaning |
|---|---|---|
| `enabled` | `true` / `false` | Default for commands without `--auto-resume` or `--no-auto-resume`. |
| `quota_only` | `true` | Fixed in this version: no other failure is ever retried automatically. |
| `max_consecutive_retries` | 1–10 | Retries allowed without progress before recovery stops. |
| `initial_backoff_seconds` | 10–3600 | Wait before the first retry when no reset time is usable; doubles each time. |
| `max_backoff_seconds` | `initial_backoff_seconds`–86400 | Upper bound for one backoff wait. |
| `max_total_wait_seconds` | 60–604800 | Longest time from the first failure of a streak to a planned retry. |
| `reset_safety_margin_seconds` | 0–3600 | Added to a reset time stated by the provider. |

Values must be JSON booleans and integers; `true` is not accepted as a number,
and unknown keys are rejected. A configuration without a `recovery` section, or
with only some of its keys, uses the defaults above for the rest.

### Three ways to continue a paused task

| | Command | Behavior |
|---|---|---|
| **Manual resume** | `resume` with recovery off, or `--no-auto-resume` | Runs once, immediately. A saved retry plan is cancelled and recorded as such. |
| **Automatic resume** | `run` / `resume` with recovery active, left running | After a usage limit the process saves a retry plan, waits and retries, within the limits above. |
| **Scheduled resume** | a plan saved by an automatic run that has since ended | Nothing runs by itself. Start `resume` with recovery active to wait for the saved time; it does not call a provider earlier. |

There is no daemon, system service or background process. If the waiting
process ends (Ctrl+C, closed terminal, reboot), the plan stays in `state.json`
and the task stays paused until you start a command again. Ctrl+C while waiting
keeps the plan.

### How the retry time is chosen

- Only a pause whose `last_error.kind` is `quota` is retried. `authentication`,
  `configuration`, `stale_tests`, `tests_disabled`, `interrupted`, `timeout`,
  `invalid_review`, `agent_error`, `system_error` and any unknown failure
  always wait for you.
- A reset time is used only when the CLI output states it unambiguously next to
  wording such as "resets" or "retry": a date and time with an explicit UTC
  offset (`resets at 2026-10-10T18:00:00Z`, `resets on Oct 10, 2026 at 6:00 PM
  UTC`), or a relative wait with units (`Retry after 120 seconds`, `try again in
  2 hours 30 minutes`). The retry is planned for that instant plus the safety
  margin, and never before it.
- One provider-specific form is also read. Codex CLI states its reset as local
  wall-clock time without a zone: `try again at 3:45 PM.` on the same day,
  otherwise `try again at Oct 12th, 2026 3:45 PM.`. For a Codex failure this is
  interpreted in the local time zone of the machine, which is the zone the
  Codex process itself used, and rounded up one minute because seconds are not
  printed. The same text from any other source is not used.
- A time without a date or offset (`resets at 9pm`), a time zone name or
  abbreviation, an impossible date or a reset that has already passed is not
  used. The wait is then an exponential backoff: 120 s, 240 s, 480 s … up to
  `max_backoff_seconds`.
- A stated reset can lengthen the wait but never shorten it below the backoff
  for that attempt.
- The count of consecutive failures returns to zero when the task reaches a new
  phase or iteration. After `max_consecutive_retries` retries without progress,
  recovery stops and the task stays paused.
- If the next wait would end later than `max_total_wait_seconds` after the first
  failure of the streak, recovery stops. A known reset beyond that budget is
  not brought forward: no call is made.

`patchrondo status` shows the plan in the `recovery` section of `state.json`
(`resume_at`, `consecutive_failures`, `schedule_source`, `provider`, and
`stop_reason` when recovery has ended). The report shows the same information.
All times are UTC.

### What the interface shows

The task page shows the failure kind, the provider, the reset time when one was
stated, the next attempt, retries used and allowed, where the plan came from
(provider reset or backoff) and why recovery stopped. It keeps five situations
apart, from the real state of the processes and not from the saved status:

| Shown as | Meaning |
|---|---|
| **Running** | A run holds the task lock and its process is verified. |
| **Waiting to retry** | A verified `run`/`resume` process with automatic resume is alive and waiting for the saved time. |
| **Retry planned · nothing waiting** | The plan is saved but no process is waiting. Nothing will retry until you resume. |
| **Recovery ended** | Automatic recovery stopped, with the reason. |
| **Paused** / **Blocked** | A person has to act: a failure that is never retried automatically, or recovery was off. |

A planned retry is not a promise: it happens only while the process that is
waiting for it stays alive. *Resume* lets you choose for that run whether to
use automatic recovery (the run then waits for the saved time before calling
the provider) or to run once now, which cancels the plan. A second automatic
run is refused while one is already waiting, and also while a waiting process
was recorded that the computer cannot verify (*Retry planned · unverified*):
it is not shown as waiting, and it is not assumed to be gone. Running once
now without automatic recovery stays possible in both cases.

### Limits of this version

- **Classification is textual.** A failure is treated as a usage limit when the
  CLI output matches patterns such as "rate limit" or "quota". Standard error
  is read first; standard output decides only when standard error names no
  specific cause, and a reset time stated on either is used. Wording of a login
  failure takes precedence over wording of a usage limit in the same text,
  because waiting cannot fix a login. When a streaming CLI reports an error,
  only that diagnostic and standard error are classified; activity messages
  and event names cannot override them. An unrelated diagnostic that mentions a limit
  is retried too. The retry limit bounds the cost: with the defaults, at most
  three extra calls.
- **Real limit messages have not been observed.** No test has hit a real usage
  limit. The Codex form above was taken from the public source of codex-cli
  0.160.1, not from a live failure, and may change between versions. For
  Claude Code no reset format could be established, so its usage limits are
  expected to fall back to the backoff. With the defaults that is three
  retries within about 14 minutes, after which recovery stops; a limit lasting
  several hours then still needs a manual resume or larger backoff settings.
- **The waiting process must stay alive.** Automatic resume happens only while
  it runs. The interface recognizes a waiting process started by 0.2 from the
  marker it writes; a process it cannot verify is shown as *unverified*.
  While that lasts, the project cannot be removed from the list and a second
  automatic run of the task is refused. The marker goes away by itself when
  the process is verified gone; if it never can be, the task page shows the
  file to delete after you have checked that the process is not a PatchRondo
  run.
- **Locks are never recovered automatically.** If the task lock exists when a
  retry is due, recovery stops with an error and leaves the lock and the plan
  untouched. `--unlock` applies only to the run you start by hand, never to an
  automatic retry. Native Windows cannot check whether a recorded PID is still
  alive, so stale-lock recovery there stays manual and conservative; use WSL2
  for unattended runs.
- **Starting it twice is safe but pointless.** Two waiting processes for one
  task never call a provider at the same time or twice for one plan: the task
  lock and a check of the saved plan under that lock decide. The process that
  meets the lock stops.
- Setting `recovery.enabled` to `false` stops a process that was activated by
  the configuration at its next wake-up, without a provider call.

## State layout

```text
~/.patchrondo/                  # the home (--home or PATCHRONDO_HOME)
  settings.json                 # global preferences: default roles, theme, selected project
  projects.json                 # project registry: ID, display name, state directory
  projects/
    P-1a2b3c4d/                 # state directory of one project
      config.json               # repository, workflow, tests, rag, recovery, optional agents
      empty-hooks/
      index/
        rag.sqlite3             # local retrieval index (rebuildable; safe to delete)
        last-index.json         # result of the last index update requested in the interface
      tasks/
        T-.../
          task.md
          state.json            # also the last environment check, under "environment"
          handoff.md
          feedback.md
          report.md
          ui-run.log            # output of runs started from the interface
          activity.jsonl        # live activity: one event per line, appended by every run
          attached/<pid>.json   # present while a run or resume process is alive
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
      worktrees/                # the one folder made readable for a review sandbox (Windows)
        T-.../                  # isolated Git worktree for each task
```

A home created by 0.1 has `config.json`, `tasks/`, `worktrees/` and `index/`
directly at its root. That layout is one project and stays where it is; the
registry then refers to it as `"home": "."`.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for state transitions, the
registry and the retrieval layer.

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

Configure it in *Settings → RAG* or in the `rag` section of the project's `config.json`:

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

The index lives in the project's state directory (`index/`) and can be deleted
at any time; it is rebuilt on the next run. Index files of removed worktrees
are cleaned up automatically. *Settings → RAG → Update index now* does what
`patchrondo index` does and shows the result.

## Security and practical limitations

1. **No shell for orchestrated commands.** Python uses `subprocess.Popen([...], shell=False)`; test commands are argument arrays, not shell-interpreted strings.
2. **Claude developer:** `--restricted --permission-mode dontAsk --tools Read,Glob,Grep,Edit,Write --allowedTools ... --disallowedTools mcp__*`. No Bash or MCP tools are authorized. **Claude reviewer:** only `Read,Glob,Grep`, with the same restricted mode. Output is requested with `--output-format stream-json --verbose` for [Live Activity](#live-activity): that changes what is printed, not what is allowed.
3. **Codex developer:** `codex --ask-for-approval never exec --json --sandbox workspace-write` (`--json` only makes Codex print its activity as events). **Codex reviewer:** `read-only` sandbox. The runner does not use `--yolo`, approval bypasses or `danger-full-access`. Codex can execute commands allowed by its sandbox: prompt instructions do not disable tools, and personal configuration can affect behavior.
4. **Separate worktree:** the main repository is not edited; no automatic commit, push or merge. Worktree creation disables Git hooks for the checkout command, but **does not create an OS sandbox** or prevent every possible effect of external Git configuration.
5. **Credentials:** prompts are sent through stdin, not shell arguments. State has private permissions on POSIX; the child environment removes several API key/secret/token variables. Local CLI credentials remain managed **by the CLIs**, not the orchestrator. Keep secrets out of the repository: file access and test scripts could expose them.
6. **Test execution:** unlike sandboxed agent tools, test commands run **on the host with your permissions**. A malicious repository can execute arbitrary code through its test suite. For untrusted code, run **the entire orchestrator inside an isolated VM/container**, without production credentials and with appropriate network/firewall settings.
7. **Billing and limits:** subscriptions have quotas and may support additional usage billing. The loop stops on rate limits, timeouts, errors, `BLOCKED` or the maximum iteration count. Nothing polls for quota availability. With the opt-in [automatic quota recovery](#automatic-quota-recovery-opt-in), a waiting process retries a usage-limit pause a bounded number of times; every retry uses plan quota.
8. **Fallible LLM review:** the JSON schema checks structure, not review accuracy. Require human review before integrating changes into main or production.
9. **Sensitive logs:** logs and handoffs may contain confidential project data. They are saved locally with private permissions; protect backups and disk storage, and do not share `~/.patchrondo`. The activity journal holds agent messages, command lines, file paths and excerpts of command output; masking of values that look like credentials is best effort, not a guarantee.
10. **Output and processes:** stdout/stderr are captured in temporary files and bounded in memory. Disk use remains proportional to output until the timeout. On POSIX, timeouts terminate the process group; native Windows terminates only the direct child, so WSL2 remains recommended.
11. **Windows access control lists:** POSIX private permissions are not translated into Windows lists. On Windows the privacy of the state directory is whatever Python gives a directory made with mode `0o700`: measured on October 11, 2026, owner-only with Python 3.13.3 and inherited from the parent folder with Python 3.11.3. PatchRondo changes lists in one place only, described under [Preparation and repairs](#preparation-and-repairs): read access for your own user on the `worktrees` folder, and "read attributes" on a folder of its own above it. A Codex reviewer's read-only sandbox reads what your user may read by name on the whole disk; that is the design of that sandbox, not something PatchRondo widens.
12. **The interface is local and single-user.** It is bound to the loopback interface and protected by a session token, but anyone who can read that token (your terminal output, your browser tab) can create tasks, change settings and start runs with your accounts. A repository does not become trusted by being added: its tests stay disabled until you enable them for it.

### Final integration

The result stays in the Git branch and worktree. Example, **after human review**:

```bash
cd ~/.patchrondo/projects/P-1a2b3c4d/worktrees/T-a1b2c3d4e5f6   # the task page shows and copies this path
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

The [CI workflow](.github/workflows/ci.yml) runs on pushes to `main`, pull
requests targeting `main` and manual dispatch. It tests Python 3.11 and 3.13
on `ubuntu-latest`, `windows-latest` and `macos-latest`, and checks source and
wheel packages on Linux with Python 3.13. Each job has a 15-minute timeout;
new runs cancel earlier runs of this workflow on the same branch or PR.

Only standard GitHub-hosted runners are used. GitHub documents these as
[free for public repositories](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#standard-github-hosted-runners-for-public-repositories).
The job is skipped before runner allocation when the repository is private.
There are no artifact uploads, Actions caches, deployments or paid services.
CI uses simulated providers without account credentials or real model calls.
The publication scanner remains a local manual check because binary assets
require human review. Run the suite locally with:

```bash
python -m unittest discover -s tests -v
# Or without installing the package:
PYTHONPATH=src python -m unittest discover -s tests -v
```

The latest recorded local validation, on October 12, 2026, of 0.2.1 with the
unpublished correction of the activity journal reader, ran **444 tests**: on
native Windows (Python 3.13 and Python 3.11) **433 passed and 11 were
skipped**; on WSL2 (Python 3.12) **426 passed and 18 were skipped**, all with
exit status 0. The skipped ones are tests that need
POSIX, tests of real Windows access lists, and five opt-in tests that run the
reviewer preflight in the real Codex sandbox; those five passed on native
Windows with both Pythons when asked for. These local results are separate from
GitHub CI; the [technical review](docs/REVIEW.md#current-source-and-ci-status-october-12-2026)
records the current published commit and its CI status, and
[what was checked for 0.2.1](docs/REVIEW.md#zero-configuration-and-self-healing-october-11-2026).
Tests use temporary Git
repositories, simulated providers, state checks and mocked CLI arguments. They
do not call Claude or Codex, validate real model output, or replace an end-to-end
test with authenticated accounts. Full package checks are documented in
[CONTRIBUTING.md](CONTRIBUTING.md).

The preflight can be run in the real Codex sandbox on native Windows. It
starts the installed CLI's sandbox self-check, sends no prompt and uses no
quota:

```powershell
$env:PATCHRONDO_REAL_SANDBOX = '1'
python -m unittest discover -s tests -p test_sandbox_real.py -v
```

### Browser checks of the interface (no provider quota usage)

```bash
python tools/ui_e2e.py --screenshots .test-tmp/shots --output .test-tmp/ui-e2e.json
```

The check starts the real server and drives a headless Chrome or Edge on your
computer through the DevTools protocol, with the Python standard library only.
Runs it starts are real `patchrondo run` processes whose provider commands
reach a synthetic executable only; the runner refuses to start while a real
`claude` or `codex` is reachable. Ten scenarios compare the page with the
files on disk: a task started from the home page with one description and one
press, the test consent and a guided repair of a preparation problem; first
start and setup wizard; a run followed live with
duplicate starts refused; the activity of a scripted agent shown while it
runs, across a disconnection, a reload and a CLI without streaming; the
interface closed and reopened during a run;
reconnection after the server restarts; the recovery states; two projects with
separate settings and tasks; a 0.1 home opened in place; and layout from 1440
to 480 pixels, keyboard use, accessible names, contrast, reduced motion, a
6 MB log and a list of more than 60 tasks. Select scenarios with repeatable
`--only` options.

On October 10, 2026, on native Windows 11 with Chrome 154 and Python 3.13, all
163 checks passed. Measured there, on a local server: a state change on disk
reached the page in about 0.36 to 0.46 seconds; a filter click was painted in
about 15 ms; a list of 68 tasks appeared about 50 ms after navigation; the end
of a 6 MB log was shown in about 0.15 seconds. These are measurements of one
machine, not guarantees. On October 11, 2026, with the `live` scenario added,
the same setup passed all 234 checks, and a message printed by the scripted
CLI reached the page in about 0.2 seconds. With the `start` scenario of 0.2.1
added, it passed all 268 checks. The check needs a local Chromium-based browser and
reports that it was skipped when there is none; it has not been run on Linux,
macOS, Firefox or Safari, and it is not part of CI.

### Local reliability checks (no provider quota usage)

Run the repeatable reliability driver from a source checkout:

```bash
python tools/reliability_e2e.py --repeat 3 --output .test-tmp/reliability.json
```

It uses synthetic Claude/Codex CLIs in real subprocesses for both role pairings.
Seven scenarios cover review feedback followed by a second iteration, developer
quota, reviewer quota, Ctrl+C during review, a killed runner with a stale lock,
a task spanning three Python files with six functional tests, and automatic
resume after a reviewer usage limit. Signal and crash scenarios require POSIX
and are skipped on native Windows; use WSL2 for full coverage. Select scenarios
with repeatable `--scenario` options.

In the quota, interruption and crash scenarios the driver invokes resume after
each injected failure and checks that completed development and tests are preserved
when resuming review. After a crash it first verifies that ordinary resume
refuses the stale lock, then uses `--unlock` after the runner has exited. That
is test-driver automation. The `auto-resume` scenario instead starts a single
runner with `--auto-resume`: the synthetic reviewer reports `Retry after 300
seconds`, the runner saves its plan, waits on a virtual clock without the task
lock and retries by itself. No scenario waits for or measures a real quota.

The optional JSON report records checks, iterations, attempted provider calls,
test runs, changed files, total elapsed time and provider-call timings. Quality
evidence consists of fixture acceptance tests and validation documentation;
synthetic results do not measure model solution quality, real quota recovery or
performance on complex projects. Successful fixtures are deleted; failed
fixtures and transcripts remain outside the repository for diagnosis.

Two further checks cover automatic quota recovery, also without model calls:

```bash
python tools/recovery_checks.py protections   # any platform
python tools/recovery_checks.py real-clock    # POSIX; use WSL2 on Windows
```

`protections` removes one safeguard at a time (for example the due check under
the lock, the retry limit, the explicit-offset requirement, the refusal of a
second automatic run while a process waits, or the explicit consent for tests)
from a temporary copy of the sources and expects the tests named for it to
fail. It
exits with status 1 if a removal goes unnoticed. `real-clock` lets a runner
really wait for a 12-second reset stated by a synthetic CLI, interrupts it with
Ctrl+C, restarts it and checks that the plan was kept and that the only further
call came after the planned time.

### Real provider check (uses plan quota)

`tools/provider_e2e.py` runs the loop with the real, authenticated CLIs on a
throwaway repository, once per pairing (Claude → Codex and Codex → Claude). It
verifies what simulated providers cannot: that the installed CLI versions accept
the adapter's arguments, that the developer can write inside the worktree, that
the reviewer leaves it unchanged and that both replies are extracted and parsed.

```bash
python tools/provider_e2e.py                               # versions and login only, no model calls
python tools/provider_e2e.py --authorize-provider-calls    # one developer and one reviewer call per pairing
python tools/provider_e2e.py --authorize-provider-calls --run-fixture-tests
```

Nothing calls a model without `--authorize-provider-calls`. By default the
fixture's tests stay disabled, so each pairing ends paused at the test gate after
two calls. `--run-fixture-tests` is separate consent to run the fixture's unit
test on your machine against agent-written code; the check then requires a
completed, approved task and may use up to four calls per pairing. A quota,
login or interruption failure stops before the next pairing. The exit status is
0 only if every check passes; on failure the fixture and its private task
state, including provider transcripts, are kept in a temporary directory for
diagnosis. `--pairing` selects a single pairing and `--keep` preserves files
after a pass.

A pass covers only the platform, CLI versions and login reported in its output.
Two local runs on October 10, 2026 passed both pairings on WSL2 with
Claude Code 2.1.291 and codex-cli 0.160.1. The first kept fixture tests disabled
and paused at the test gate. The second used `--run-fixture-tests`: both
pairings completed with passing tests, an `APPROVED` review and status `done`.
These checks cover a trivial greeting fixture; native Windows, macOS and
resumption after an interruption have not been checked with real accounts. Details are in
[docs/REVIEW.md](docs/REVIEW.md); record later runs there.

## Roadmap

- Optional embedding-based reranking and decision-log indexing, if lexical retrieval proves insufficient.
- Dedicated OS sandbox for tests (Docker/VM with minimal privileges).
- Record real-provider check results per platform and CLI version; extend the check to resumption after an interruption.
- Confirm quota classification and reset parsing against usage-limit failures observed with real CLI versions, including the reset format of Claude Code; consider named time zones and an optional supervisor service that keeps a retry plan attended without a foreground process (not part of 0.2: a saved plan still needs a live `run` or `resume`).
- Process supervision on native Windows: tracking of the whole process tree of an agent, which stale-lock recovery and Stop need before they can be offered there. (0.2 verifies single processes on Windows for display only.)
- Approval requests for risky tools and notifications.
- Stall detection based on Git/test changes.
- Confirm live activity with authorized real runs of both CLIs, and record the result per CLI version; consider word-by-word output for Claude Code, which today would also carry thinking deltas.
- A diff view per iteration.
- Dedicated illustrations of Rondo for each state (see [docs/DESIGN.md](docs/DESIGN.md)).
- Browser checks of the interface on Linux and macOS, and in Firefox and Safari.

## Official documentation

- Claude Code CLI: https://code.claude.com/docs/en/cli-reference
- Claude Code Pro/Max: https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan
- Codex CLI: https://learn.chatgpt.com/docs/developer-commands?surface=cli
- Codex login: https://learn.chatgpt.com/docs/auth

**License:** MIT. An MVP for local use with trusted projects.
