# User guide

Detailed reference for the dashboard, command line and task lifecycle.
For installation and requirements, see the [README](../README.md#quick-start).

## Contents

- [Dashboard](#the-interface)
- [Preparation and repairs](#preparation-and-repairs)
- [Criteria and checks](#criteria-and-checks)
- [Agent settings](#agents-models-and-options)
- [Live activity](#live-activity)
- [Command line](#command-line)
- [Loop behavior](#loop-behavior)
- [Automatic quota recovery](#automatic-quota-recovery-opt-in)
- [State layout](#state-layout)
- [Local retrieval](#local-retrieval-rag)
- [Security and integration](#security-and-practical-limitations)

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
| **Agents** | One card for the developer and one for the reviewer: who plays the role, a preset, the model and the effort; under *Advanced*, every other option of that CLI. Global preferences, a project's own values or a single task's, with the origin of each value in effect. *Test configuration* checks a card without starting anything. See [Agents: models and options](#agents-models-and-options). |
| **Tasks** | All tasks of the selected project, or of every project, with filters and search. |
| **Task** | Header with status, roles and iteration; the workflow *Prepare → Develop → Test → Review → Validate → Repeat / Complete*; then Overview, Criteria and checks (each criterion with who does it, who verifies it, why it is open and its evidence), Live Activity (what the agents are doing, while they do it), Timeline (the steps the engine recorded), Logs, Tests, Review, Files changed, Handoff, Report and Advanced (the raw files). Run, Resume and Stop are here. |
| **New task** | The longer form behind *More options*: project, title, description, acceptance criteria with a preflight of what each one will take, developer and reviewer; under *Advanced*, limits for that task only. *Create and run* asks for confirmation before any agent is called. |
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
- **Approved code is not a finished task.** A task is done when its code is
  approved and every required criterion has evidence on record. A criterion
  that needs a compiler nobody has, a download, a pull request or a check in
  another program does not go back to the developer: the task waits at
  *Validate* and says what each one needs. See
  [Criteria and checks](#criteria-and-checks).
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
Content-Security-Policy and no external assets. See [SECURITY.md](../SECURITY.md).

### Preparation and repairs

Before the first agent call of every run, also a resumed or automatically
retried one, and again before each review (or before the pause that takes its
place while the tests are not authorized), PatchRondo checks the environment
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

### Criteria and checks

Every acceptance criterion of a task is tracked on its own. PatchRondo works
out what each one needs, who in the task can do it and who can verify it, and
keeps the proof.

**Capabilities belong to whoever has them.** There are four executors: the
developer agent, the reviewer agent, PatchRondo itself and you. The developer
may have file tools and no shell; PatchRondo can still run the project's
tests, because that is its own capability. *Who can do what in this task*, on
the task page, lists them, each marked *Observed* (seen on this computer),
*Configured* (follows from a setting PatchRondo passes or enforces), *Not
available* or *Unknown*. Nothing is started to find out, and no model is
asked.

| A criterion about | Done by | Verified by |
|---|---|---|
| The code: logic, refactoring, documentation | The developer | The reviewer, reading the code |
| Tests that pass | PatchRondo, with the commands you allowed | The result of those commands |
| A build or a package | PatchRondo, when one of your commands does it; otherwise you | That command's result, or your check |
| Another program, a real device, something only a person can judge | You | Your check, with its evidence |
| A download, a commit, a pull request, a publication | You, after your explicit authorization | Your check, with its evidence |

The kind of each criterion is read from its text by fixed rules, in English
and Italian. When the rules cannot place one (a licence, the rules of a
platform), it is shown as *Unclear what it needs* with the reason, never as
done. You decide over the rules by starting a criterion with a tag:
`[manual]`, `[build]`, `[external]`, `[publishing]`, `[integration]`,
`[test]`, `[code]`, and `[optional]` for one the task may finish without. The
*New task* form shows this preflight while you type; it never stops a task
from being created.

**What a criterion can be:**

| Shown as | Meaning |
|---|---|
| *Not verified yet*, *In progress* | Waiting for the developer, the tests or the review |
| *Verified* | Evidence is on record and still counts |
| *Failed* | The reviewer asked for a change to the code, a command failed, or your check did not pass |
| *Blocked · something is missing* | No program or command in this workflow can do it; the row says which |
| *Needs your authorization* | An operation that is never done without your decision |
| *Needs a check by you* | Only a person can verify it |
| *Unclear what it needs* | It could not be classified; your evidence settles it |

**Code and task are judged apart.** *Code: Approved* means the review approved
it, the project's commands passed and there are changes. *Task as a whole*
says whether every required criterion is verified. With the code approved and
criteria open, the task pauses at *Validate*: it is not done, and it is not
sent back to a developer who can do nothing about it. Resuming it calls no
agent while nothing changed.

**The reviewer answers per criterion.** Besides its verdict, which is about
the code, it gives each criterion one outcome: satisfied, the code has to
change, a check is missing, nobody here has the tools, an operation has to be
authorized, a person has to act. PatchRondo validates that answer. A request
for changes that names no change to the code asks nothing of the developer,
so nothing goes back to the developer. Neither does a change to the code that
the reviewer asks for a criterion no change to the files can satisfy, such as
a check inside another program: that criterion waits for you. What the
reviewer says about a criterion it cannot verify is shown as its opinion and
is not evidence. A real defect in the code still goes back, with the outside
checks listed apart.
An issue the reviewer marks as a change the code requires keeps the code from
being approved, whatever its severity; an *observation* never does. For a task
created with its criteria, an approval has to give each criterion it verifies
an outcome of its own: one that leaves a criterion out is not accepted, the
task pauses with *unreadable review*, and Resume asks for the review again.

**Evidence, not statements.** A criterion is verified only by records of the
task's evidence ledger: a command PatchRondo ran (with its exit code, its log
and the fingerprint of the files it ran on), a review PatchRondo validated,
or a check you attached. What the developer writes in its handoff is never
evidence. Records are never edited; when the files change after a check, the
records about the earlier files stop counting and the criterion is open
again.

**What you can do on the page**, each without any agent being called:

- **Attach evidence** to a criterion a person may verify: what you did and
  saw, and where the proof is, or a file that PatchRondo fingerprints
  (SHA-256) itself. A statement alone is refused. A check that did not pass
  is sent to the developer at the next run.
- **Authorize or decline** the operation of a criterion. PatchRondo and the
  agents never download, commit or publish, with or without it: authorizing
  means that you take the operation on yourself, and the criterion then waits
  for its evidence.
- **Check again** after installing a program or changing the project's
  commands, and **Complete the task** when everything required is verified.

To let PatchRondo verify a build or a package, add the command to the
project's commands in *Settings → Tests*: it runs with the consent you already
gave there, and its result is the evidence.

A task created before criteria were tracked keeps working. If it ended, it is
shown as it was, with nothing said about each criterion; if it runs again,
its criteria are read from its description with nothing taken as verified.
A saved record of the criteria that this version cannot read, such as one
written by a later version, is never replaced: the task pauses with *the
saved criteria cannot be read* and calls no agent. Open it with the version
that saved it, or put back its `state.json`, and resume. The criteria shown
on the task page, in the list and by `patchrondo criteria` are judged against
the evidence ledger as it is when you look; a criterion saved as verified
whose evidence no longer counts says so. A ledger that no longer holds what
was saved, for example after a line was removed from it, counts for nothing
there until the next run or *Check again* starts it again.

### Agents: models and options

*Agents* is where you choose what each role runs with, without editing a JSON
or TOML file and without touching the CLIs' own configuration: PatchRondo
passes your choices as arguments of each call.

**Each role has its own settings.** The developer and the reviewer are
configured separately, and separately for Claude Code and for Codex, so the
same agent can play both roles with two models, and switching the agent of a
role keeps what you chose for each.

**Basic**, on the card:

- *Agent*: Claude or Codex (a project can also follow the global default; a
  task keeps the agents it was created with);
- *Preset*: *CLI default* (nothing is passed, as before), *Fast*, *Balanced*,
  *Max quality*, or *Custom* when your values match none. A preset fills in
  the model and the effort; it is a starting point, not a link, so changing a
  preset later never changes a task;
- *Model* and *Effort*, with the state of the CLI under them.

**Advanced** unfolds the rest by category, each setting with a line that says
what it does and the option it becomes:

| | Claude Code | Codex |
|---|---|---|
| Model | `--model` (alias or full name), `--effort`, `--fallback-model` | `--model`, reasoning effort as `-c model_reasoning_effort=…` |
| Execution and permissions | `--permission-mode` (`dontAsk`, `acceptEdits`, `plan`, `auto`) | `--sandbox`, `--ask-for-approval`, `--profile`, `--ignore-user-config` |
| Limits | `--max-turns`, `--max-budget-usd`, step timeout | step timeout |
| Folders and tools | `--tools`, deny rules, `--add-dir`, `--mcp-config` with `--strict-mcp-config` and the MCP tools allowed | web search (`-c web_search=…`), `--add-dir` |
| Prompt | `--append-system-prompt`, extra instructions | extra instructions |
| Session and streaming | keep or drop the CLI's session transcript, live activity | ephemeral session, live activity |
| Configuration | | `-c key=value` overrides |
| Custom arguments | developer only | developer only |

**Where the lists come from.** *Refresh* asks the installed CLIs, without any
model call, for their version, login and help, and Codex for its model
catalog (`codex debug models`, or the catalog shipped with the binary when
that does not answer) and its profile files. A setting whose option the
installed CLI does not list is shown disabled with that reason, except where
the CLI's documentation names it (`--max-turns` is not printed by `claude
--help`). Claude Code has no command that lists models: its list is the
aliases its documentation names. You can always type another model ID.

**Listed is not usable.** No list says what your account may use. A model
that nothing has confirmed is marked *Not verified for your account*, and
*Test configuration* offers *Test with a real call*: after you confirm, the
CLI is started once and the model is asked for one word, with no writing tool.
That is the only action on this page that calls a model and uses a little of
your plan; its answer is remembered with its date.

**Three levels.** A value is looked up in the task, then in the project, then
in the global preferences, then in PatchRondo's default. The nearest level
that sets it wins, and each value on the page shows where it comes from, with
a button to go back to the inherited one. *New task → Advanced* sets the
model and effort of one task; its other settings are on *Agents* once the
task exists (from *Agent settings* on its page).

**A step keeps the settings it started with.** The settings in effect are
frozen when a step (one role in one iteration) first starts and saved with
the task. A resume, a retry and an automatic recovery of that step repeat the
same call, whatever was edited meanwhile; an edit applies from the next step.
When a stopped step's settings differ from those of now, the task page says so
and offers *Use the current settings*: nothing is switched silently. The task
header, its details, the report and the start of every attempt in *Live
Activity* name the model and effort that were used; the *Logs* tab has the
command of each configured step, with credentials masked.

**What does not change.** The reviewer is read-only: a Codex reviewer always
runs in the `read-only` sandbox and a Claude reviewer with its three reading
tools, and neither can be given custom arguments, additional writable folders
or MCP servers. Claude Code always runs `--restricted`, which refuses
`bypassPermissions`. An option that widens what the developer can touch
(`danger-full-access`, a tool that runs commands, a folder outside the task's
copy, MCP servers, an unreviewed configuration key, any custom argument) is
saved only after you tick an acknowledgement that says what it allows, asked
again whenever the value changes. Options that remove a protection or change
how a step is started (`--dangerously-skip-permissions`,
`--dangerously-bypass-approvals-and-sandbox`, `--bare`, `--settings`,
`--resume` and others) are refused with or without it. The policy is in
[SECURITY.md](../SECURITY.md#agent-settings).

**When a choice does not work.** A model the provider refuses pauses the task
as *The model is not available*; an option the installed CLI refuses, as *The
CLI did not accept an option*, before anything was sent to a model. Neither is
retried by automatic recovery. Fix the setting on *Agents*, press *Use the
current settings* on the task page, and resume.

None of these options had been confirmed by a call to an authenticated CLI
when this was written: the options and their meaning come from the CLIs' help
and documentation read on October 12, 2026 (Claude Code 2.1.295, codex-cli
0.161.0), and from scripted providers.

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
  them yet; the [real provider check](TESTING.md#real-provider-check-uses-plan-quota)
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

The command line supports task creation, execution, inspection and acceptance
checks. Scripts written for 0.1 keep working.

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
it does not create an OS sandbox. Without enabled tests, the system implements
the change and then **pauses** before the review (`tests_disabled`): the
missing consent is a prerequisite, so no reviewer call is made for work that
cannot be approved. Resuming while tests are still disabled changes nothing
and calls no agent; after you enable them, Resume runs the tests and then the
review.

A complete reference configuration is in
[`examples/config.example.json`](../examples/config.example.json). It also includes
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
An `--accept` criterion may start with a tag that says what it is, as in
`--accept "[manual] Tried in the game"` or `--accept "[optional] ..."`; see
[Criteria and checks](#criteria-and-checks).
`--developer-model`, `--developer-effort`, `--reviewer-model` and
`--reviewer-effort` set the model and effort of this task only; everything
else comes from the [agent settings](#agents-models-and-options) of the
project and of the home.

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

A task whose code is approved while required criteria are open pauses at the
`validate` phase with `awaiting_validation` or `awaiting_authorization`. No
agent is called there:

```bash
patchrondo criteria T-a1b2c3d4e5f6                       # each criterion, who verifies it, why it is open, its evidence
patchrondo criteria T-a1b2c3d4e5f6 --check               # look at this computer and at the evidence again first
patchrondo authorize T-a1b2c3d4e5f6 AC-04 --grant        # or --deny; nothing is run either way
patchrondo evidence T-a1b2c3d4e5f6 AC-04 --summary "Opened the pull request" --reference https://example.com/pull/7
patchrondo evidence T-a1b2c3d4e5f6 AC-03 --summary "Built the archive" --artifact dist/release.zip
patchrondo resume T-a1b2c3d4e5f6                         # completes the task once every required criterion is verified
```

To let the same process wait for a usage limit to reset and retry by itself, see
[Automatic quota recovery](#automatic-quota-recovery-opt-in). It is disabled by
default.

If the Python process was killed abruptly and left a `.run.lock`, **first confirm
that no other run is active**, then use
`patchrondo resume T-a1b2c3d4e5f6 --unlock`. The lock is not removed if a recorded
runner, agent or test PID is still active. On native Windows, manual lock removal
is required after a reboot.

Statuses: `ready`, `running`, `paused`, `done`, `blocked`.
Phases: `develop`, `test`, `review`, `validate`, `complete`.
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
           | structured review JSON: a verdict about the code, one outcome per criterion
           +-- APPROVED + passing tests + changes = code approved
           |       +-- every required criterion verified by evidence -> DONE
           |       +-- otherwise -> PAUSED at validate (no agent is called there)
           |               evidence attached, operation authorized -> resume -> DONE
           |               a check of yours failed -> feedback.md -> developer (next iteration)
           +-- a change to the code is asked / failing tests -> feedback.md -> developer (next iteration)
           +-- BLOCKED / too many iterations -> BLOCKED
           +-- quota / timeout / invalid output / Ctrl+C -> PAUSED -> resume
               (quota only, opt-in: wait for the reset or a backoff, then retry)
```

AI review is one input: the orchestrator independently checks test results,
whether changes exist and the evidence of every required criterion. Review
comments are feedback, not permission to bypass checks. A request for changes
that names no change to the code does not start another development step. State files are saved atomically after every phase. Following an abrupt
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
  agents.json                   # global agent settings, your presets, models you added or tested
  projects.json                 # project registry: ID, display name, state directory
  projects/
    P-1a2b3c4d/                 # state directory of one project
      config.json               # repository, workflow, tests, rag, recovery, optional agents and agent_settings
      empty-hooks/
      index/
        rag.sqlite3             # local retrieval index (rebuildable; safe to delete)
        last-index.json         # result of the last index update requested in the interface
      tasks/
        T-.../
          task.md
          state.json            # also the last environment check ("environment"), the task's own agent
                                # settings ("agent_settings"), what each step was started with ("agent_runs")
                                # and the criteria with their plan, status and results ("acceptance")
          evidence.jsonl        # evidence ledger: append-only, each record chained to the one before
          handoff.md
          feedback.md
          report.md
          ui-run.log            # output of runs started from the interface
          activity.jsonl        # live activity: one event per line, appended by every run
          attached/<pid>.json   # present while a run or resume process is alive
          runs/
            iteration-001/
              developer.prompt.md
              developer.command.txt      # the command of a step that had agent settings, credentials masked
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

See [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) for state transitions, the
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

   These are the commands without [agent settings](#agents-models-and-options). Settings add a model and options to them; the reviewer's read-only limits and Claude's `--restricted` never change, and what widens the developer (`danger-full-access` included) is applied only after your explicit acknowledgement for that role.
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
