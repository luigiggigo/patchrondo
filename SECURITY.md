# Security

## Scope

PatchRondo 0.2.1 is experimental and intended for trusted local repositories.
Git worktrees isolate changes, not host privileges. Test commands execute on the
host with the current user's permissions. Run the whole orchestrator in a VM or
container for unfamiliar code. Environment filtering does not prevent access to
credentials on disk or change personal provider configuration.

Claude uses restricted file tools. Codex uses its CLI sandbox, but can execute
commands permitted by that sandbox; a prompt is not an enforcement boundary.
Inherited provider configuration and managed settings can affect behavior.
AI approval requires passing configured tests and still needs human review before
integration. Logs may contain private project data.

`tools/provider_e2e.py` calls the real provider CLIs, and uses plan quota, only
with `--authorize-provider-calls`. `--run-fixture-tests` is separate consent to
run a unit test on the host against agent-written code. After a failed check,
or with `--keep`, its temporary directory holds prompts and provider
transcripts; delete it when no longer needed and do not publish it. Only a run
with `--run-fixture-tests` reaches the reviewer: without it the loop pauses at
the missing test consent and the reviewer's checks are reported as not
exercised. A passing check with that flag shows that the reviewer did not
change files in that run. It does not show that a provider sandbox would block
an attempt.

Automatic quota recovery is disabled by default. When enabled through
`recovery.enabled` or `--auto-resume`, a waiting `run`/`resume` process calls
the provider CLIs again without asking, at most `max_consecutive_retries` times
without progress; each call uses plan quota. It only waits for a limit to
reset. Usage limits are recognized from CLI output text and can be
misclassified, which is why the retry limit cannot be disabled. The task lock
is not held while waiting and is never removed automatically.

A failed CLI call is recorded in `state.json` as `last_error`. Its `message`
holds an excerpt of the CLI's own output, up to about 1,200 characters taken
from the error the CLI reported, standard error and standard output, and the
report and the interface display it. Recovery adds only structured fields derived from that output: the
failure kind, the provider name, a reset time and the stated wait in seconds.
The complete captured output is in the run logs of the task directory. None of
it is filtered for secrets, so treat task state as private project data.

The history of a task, in the same `state.json`, records what PatchRondo did:
names of events, their IDs, references to ledger records, findings and
criteria, counts and fixed words. It takes no text from an agent. What text
it holds, such as the message of a retrieval failure, is cut at 600
characters and passed through the best-effort filter for credentials of the
activity journal. Its IDs order the events; they do not protect them from
an edit of the file.

Captured process output is bounded in memory; temporary capture files can grow on
disk until the timeout. Apply disk quotas in isolated runners. On native Windows,
the project's checks run in a job object, so a time limit ends every process
they started; an agent CLI is still terminated alone, a process started before
the job object holds it escapes, a check no job object could hold is
terminated alone and its record says so, and stale-lock recovery is
conservative. Use
WSL2 for POSIX process-group cleanup.

## Environment check and repairs

Before a run calls an agent, and before each review or the pause for a missing
test consent that takes its place, PatchRondo checks that
the agents are installed and logged in, that Git accepts the task worktree,
and that a Codex reviewer's sandbox can read the worktree and cannot write it.
The check calls no model. It exists to stop early and explain; it is not
allowed to make a task pass by lowering a protection.

**What it never does.** It never selects a weaker sandbox than the reviewer's
`read-only`, never uses `danger-full-access` or an approval bypass, never
elevates or asks for administrator rights, never writes `safe.directory = *`
or any other wildcard, never enables or runs the tests of the target
repository, and never treats *Start task*, *Resume* or an automatic retry as
consent to any of these. When a problem cannot be solved inside these limits
the task pauses where it can resume, and says why.

**The sandbox self-check.** On native Windows the check starts
`codex sandbox -P :read-only …` with two small commands and compares their
answers with the disk. It runs the installed Codex CLI, with your Codex
configuration, as your user; nothing is sent to a model. The commands are
argument arrays, and the paths inside the PowerShell probe are single-quoted
literals in an encoded script, so a folder name cannot become code. The probe
tries to overwrite and delete a canary file PatchRondo created for it, and to
create one file in the worktree: if any of that succeeds, the review is not
started. A passing probe shows how that sandbox behaved for these operations
at that moment; it is not a proof about everything the sandbox permits.

**What it changes on Windows, by itself.** Only access control lists of
folders PatchRondo created, and only by adding allow entries for the SID of
the user it runs as:

- read and execute, inheritable, on `<state>/worktrees`, and on a worktree
  whose list does not inherit;
- "read attributes", on that folder only, on the state directory and, in a
  home with several projects, on `projects` and on the home, when the
  sandbox could not otherwise see that the folder exists.

It adds no entry for Everyone, Users or any other group, removes no entry,
leaves a deny entry in place (and then reports the problem instead of
repairing), does not touch folders above the home or inside your repository,
and does not follow symbolic links, junctions or other reparse points: a
worktree or a `worktrees` folder that is one is not repaired. Before each
change the previous list is saved in the task state; if the repair fails or
does not verify, the saved lists are put back. These entries let processes
that run as you, including a sandbox restricted to your user, read the
worktrees. They give nothing to other users of the computer. They do not open
`tasks/`, the activity journal, the retry plan or the configuration: those
keep the list Python gave them, which on Python 3.13 names only SYSTEM,
Administrators and the owner. On an older Python, and on other systems, the
Codex read-only sandbox can read whatever your user can, as it always could;
PatchRondo does not narrow that.

**What needs your decision.** If Git refuses the project folder for its
ownership ("dubious ownership"), the page offers to trust it. Only after you
confirm, PatchRondo runs `git config --global --add safe.directory <folder>`
for the project's repository and for the task's worktree, and records the
command that undoes each entry. That tells Git to run hooks and read
configuration from a folder another user owns: confirm it only for a project
you know. Authorizing the project's tests is likewise only ever your explicit
action, on the task page or in *Settings → Tests*.

**What is recorded.** The record of a check, in `state.json` and in the
report, holds check names, results, folder paths, the access lists that were
replaced (SDDL, which contains security identifiers), the version line of
each CLI and excerpts of what Git or the probe answered. It holds no
credentials: the login check reads only the exit status and, for Claude, the
login method.

`PATCHRONDO_PREFLIGHT=0` switches the check and its repairs off.

## Agent settings

The *Agents* page chooses the model and the options of each call. Settings
become arguments of one invocation; PatchRondo never writes the configuration
files of Claude Code or Codex, and never reads or stores their credentials.

**Fixed, whatever is set.**

- The reviewer is read-only. A Codex reviewer runs in the `read-only` sandbox
  and a Claude reviewer with `Read`, `Glob` and `Grep`; neither has custom
  arguments, additional writable folders or MCP servers. These limits are
  enforced when a setting is validated and again when the command is built.
  The environment check still verifies the Codex reviewer's sandbox before
  every review.
- Claude Code always runs `--restricted`: no command-running tool unless you
  name it, file tools confined to the working directories, `bypassPermissions`
  refused.
- Options that remove a protection, change how a step is started or redirect
  it are refused for every role, with or without an acknowledgement:
  `--dangerously-skip-permissions`, `--allow-dangerously-skip-permissions`,
  `--dangerously-bypass-approvals-and-sandbox`, `--dangerously-bypass-hook-trust`,
  `--bare`, `--settings`, `--setting-sources`, `--include-partial-messages`,
  `--forward-subagent-text`, session options such as `--resume`, and the
  Codex configuration keys for sandbox permissions, the shell environment,
  model providers, `notify`, MCP servers, hooks, project trust and features.
  The full lists are `ARGS_BLOCKED` and `CONFIG_BLOCKED` in `agents.py`.
- No sandbox is ever weakened because a step failed. A Windows access problem
  is still handled by the environment check, which never changes a setting.

**Applied only with your acknowledgement, developer only.** These widen what
the developer can touch, and the page says so before you tick the box, which
is asked again whenever the value changes:

- Codex `danger-full-access`: the agent's commands run without a sandbox, with
  your permissions.
- A Claude tool outside the file tools (for example `Bash`): it runs commands
  or reaches the network, unsandboxed.
- Additional folders: the agent can change files outside the task's own copy.
  Folders that hold PatchRondo's state, and your own checkout of the project,
  are refused in any case.
- An MCP configuration file: its servers are programs that run with your
  permissions. Only the servers of that file are started and only the MCP
  tools you list are allowed.
- A Codex configuration key PatchRondo has not reviewed, and any custom
  argument: they are passed as written and PatchRondo cannot check what they
  do.

The acknowledgement is stored beside the value it was given for, in the same
level. It is a record of an informed choice, not a protection: with these
options the worktree no longer bounds what a developer agent can change.

**Injection.** Every value is validated against a typed table before it is
saved and again before it is used. A model name cannot start with `-`. The
CLIs are started with an argument array and no shell, by name, which on
Windows never resolves to a `.cmd` launcher. Custom arguments accept long
options only, because a short option can carry another one.

**Secrets.** A short value that looks like a credential is refused. The
command preview, the saved command of a step and the report pass through the
same best-effort masking as the activity journal. Longer text you write
(instructions, an appended system prompt) is stored as written in the private
state directory and sent to the provider: do not put secrets in it.

**The one model call.** *Test with a real call* starts the CLI once and asks
the model for one word, with one reading tool in an empty folder (Claude) or
the read-only sandbox (Codex). It needs your confirmation every time and uses
a little of your plan. Refresh and *Test configuration* call no model.

**Web search.** Codex's `indexed` and `live` modes let the model read pages
written by others, which can carry instructions. PatchRondo offers the
setting and does not filter what the model reads.

## Acceptance criteria and evidence

PatchRondo tracks what each acceptance criterion of a task needs, who can do
it and who can verify it, and marks a criterion verified only by records of
the task's evidence ledger.

**What it never does.**

- It gives nobody a permission. Capabilities are read from the agent settings,
  the test consent, the environment check and PATH; nothing is started to
  find out, and a missing capability is never added to make a criterion pass.
- It runs no command of its own for a criterion. The only commands are the
  project's configured ones, under `tests.enabled` and
  `tests.trust_acknowledged`, exactly as before.
- It does not download, install, commit, push, open a pull request or
  publish, with or without an authorization. An authorization is the user's
  recorded decision to take an operation on themselves; granting one needs
  `confirm: true`, and it changes what the task waits for, not what any agent
  or PatchRondo may do.
- It does not take a model's word. The developer's handoff is never evidence.
  The reviewer's verdict and outcomes are validated by the backend, and count
  only for criteria the plan gives the reviewer to verify. An outcome can make
  a criterion ask for a check by the user; it cannot verify a criterion that
  needs a command, a decision or a person, and it cannot remove one.
- It does not accept a statement as proof. A check by the user needs where
  its proof can be found, or a file that PatchRondo hashes itself, and is
  accepted only for a criterion a person may verify, never in place of the
  review of the code or of the project's tests.

**The ledger.** `evidence.jsonl` is in the task's private directory. Every
record carries the hash of the one before it, so a record that was changed,
removed or inserted is noticed when the file is read, and so is a ledger
shorter than the one the task state saw. This makes silent edits visible. It
is not a signature and not access control: the files belong to the user, and
somebody who rewrites the ledger and the state together is not detected. Text
in it is bounded and passed through the same best-effort filter for
credentials as the activity journal; treat it as private project data all the
same. A ledger put aside after its check failed is kept unchanged and read
for one thing only, the IDs of review findings it names, so that none is
given to another problem; nothing in it counts as evidence.

**What a check by the user is.** The user's own statement, with a reference
that PatchRondo records and does not open, or the size and SHA-256 of a file
the user named. Naming a file makes PatchRondo read that file, any regular
file the user's account can read, to hash it; its content is not stored or
shown. Evidence is tied to the fingerprint of the task's files and stops
counting when they change.

**Untrusted text.** Criteria, notes of the reviewer and summaries typed by the
user are shown as text nodes, like every other text an agent or a repository
can write. The reviewer is told that repository content is data; a review
that was steered can at worst hand criteria over to the user or ask for
changes, and cannot mark a criterion verified. The descriptions of earlier
findings are shown to later reviewers as earlier reviewers' words, data and
not instructions, bounded and filtered like the notes. A finding approves,
verifies and authorizes nothing: its owner, flags and status come from fixed
rules, and a link the reviewer gives is checked before it is kept.

The actions on criteria go through the same server checks as every other
write (token, `Host`, `Origin`, JSON body) and hold the task lock, so they
never overlap a run.

## The project's checks

The verification runner (`verification.py`) runs the commands of
`tests.commands` and the checks of the `verification` section of a project's
configuration, after each development step, and records what each one showed
in the evidence ledger.

**What it never does.**

- It runs nothing the configuration does not name, and nothing without
  `tests.enabled` and `tests.trust_acknowledged`: a check that is not
  configured, or anything without the consent, is recorded as blocked before
  it starts. No agent and no text an agent wrote can name a command for it.
- It starts no shell. A configured check whose program runs its arguments as
  a script (`sh`, `bash`, `cmd`, `powershell`, `pwsh`, `wsl` and the like)
  needs `"shell": true` in the configuration; the entries of `tests.commands`
  run as they always did.
- It reads nothing outside the task's copy of the project and its own run
  folder: the folder of a check and a report location are relative, without
  `..` or a drive, and are refused when a link or a junction leads elsewhere.
- It changes no permission, sandbox or access list to let a check run: a
  check that cannot start is recorded as blocked, and the task pauses.
- It takes no agent's word: a record of a command can only be written as the
  runtime's (`evidence.EXECUTORS`), and a report counts only when this run
  wrote it where it was told to.

**Tests written by the developer on request.** At the test-consent pause,
*Ask … to write tests* calls the task's developer once, after a confirmation,
with the settings of its development step and the same environment check
first: it writes in the task's copy of the project only, as in a development
step, so its tests are reviewed with the rest of the changes. The commands it
proposes are text shown in the commands field, never a check: they run only
after you allow them with the trust box, as typed commands do. Read them as
you would read a command from someone else; a test file runs code of the
project with your rights once you allow it. The call uses your plan's quota
and is never retried by PatchRondo.

**Where a check runs.** On the host, with your account's rights: a check can
read and change what you can, use the network, and find credentials in files
or in the configuration of your tools. Removing secret-named variables from
its environment (`process.safe_env`) only keeps them from being handed to it.
`trust_acknowledged` records that you trust the repository; it isolates
nothing. Do not enable the checks of a repository you do not trust on your
own computer: run the whole orchestrator in a virtual machine or container,
without production credentials, first. A dedicated sandbox for tests is a
proposal in [the roadmap](docs/ROADMAP.md), not implemented.

**Stopping a check.** A time limit stops the process group on POSIX and the
job object on native Windows, with everything the check started. When
Windows refuses to put the check in a job object, it still runs but is
stopped alone: its record says `child_only`, and a timeout says that what
it started may still be running. On POSIX, when the system refuses the last
signal to the group for two seconds (macOS refuses it while what is left of
the group has exited and is not yet reaped), the check is still a timeout,
its record says `kill_refused`, and the timeout says that what it started
may still be running. An agent step, or a command of the test stage before
checks, stopped that way says the same in the message of the pause and at
the end of its log. A process that leaves the group is not stopped.

**What a result is worth.** Code under test can print anything and can write a report that says what it
likes. The runner refuses a report that was there before the run, a report
that contradicts the exit code or its own counts, and a run that changed the
task's files, so a forged pass takes deliberate code, which the review of the
change is there to find. A record is evidence of what PatchRondo observed,
not a signature.

**Reports.** JUnit XML reports are untrusted input. They are parsed with
`xml.parsers.expat`; a document type declaration is refused before any entity
is defined, so no entity is expanded and nothing is fetched; the size (32 MiB),
the depth and the number of test cases are bounded.

**Logs.** The output of a check is saved in the task's private run folder.
Before it is written, the values of PatchRondo's own environment variables
whose names say they hold a secret (`TOKEN`, `SECRET`, `PASSWORD`, `API_KEY`,
…) and the shapes the activity journal masks are replaced by `[redacted]`.
That is best effort: a secret printed in another shape is kept, so the logs
are private project data. The child process does not receive those variables
(`process.safe_env`), but it can read what your account can read.

`PATCHRONDO_VERIFICATION=0` runs `tests.commands` as the versions before the
runner did, without the masking of logs.

**Provenance and invalidation.** Whether a record can still be used is judged
apart from its result (`provenance.py`). Integrity, observed provenance,
authenticity, validity for the files as they are and correctness of the code
are different properties, and only the first two and the fourth are checked:
the hash chain shows an edit that does not rewrite it; a record names the
run, the check and the digest of its configuration, the files, the inputs it
declared and the environment; and evidence about anything that changed since
stops counting. Nothing is signed: a process with your account's rights can
rewrite the ledger with its chain, and no record proves that the code is
correct. A record of a schema version this one does not know, or written by
another executor than the one of its kind, never counts. Declared inputs are
read inside the project only, without following a link or a junction, and
only their digest is kept, never a file's content or its own hash; the
fingerprint of the task's files never reads behind a link or a junction.

**Acceptance gate.** A task is completed only by `core._complete`, and only
when the gate (`gate.py`) decides `PASS` from the records that count: the
code approved, every required criterion verified, the test results about
the files as they are. The files are fingerprinted and the ledger read again
right before `done` is written. A reviewer's answer cannot complete a task:
its verdict is about the code, and the evidence IDs it cites are checked in
the task's own ledger and recorded, never taken as proof. An authorization is
never the operation it authorizes. While `PATCHRONDO_VERIFICATION=0` or
`PATCHRONDO_PROVENANCE=0` turns off what the configured checks rely on, no
task is completed. The gate trusts the ledger as far as its chain does: a
process with your account's rights can rewrite the ledger and `state.json`
together.

## Evidence bundles and the Verification Center

An export takes files of the task's private directory and makes them a file
that leaves it, so it is a boundary of its own.

**What goes in.** A summary bundle (the default) holds the task's records:
the state's task fields and description, the criteria, the saved gate, the
evidence ledger byte for byte, the validity of each record, the last review's
verdict and references, the findings, the test results without their output
and the history. A detailed bundle also holds copies of the logs and test
report copies that records of the ledger name. Never included: the worktree
or the repository, agent transcripts the ledger does not name, the files a
check by the user names (only their size and SHA-256, already in the
ledger), configuration files, and anything a request names: the page and
the API choose a task and a mode, never a path.

**How files are read.** A log or report is read only when its name matches
`iteration-NNN/<name>` or `iteration-NNN/<phase>/attempt-NNN/<name>`
(`runfiles.LOG_NAME`) under the task's own `runs` folder; every folder of
the way, from `runs` on, and the file are checked not to be a link or a junction and the
file to be a regular file, the file opened is compared with the file
checked, and its size and time after reading with those before. Anything
else is left out and named in the manifest (`unsafe_name`, `link_refused`,
`not_a_regular_file`, `changed_during_export`, `missing`). Only the last
256 KiB of a log (1 MiB of a report) are copied, at most 60 files and 24 MiB.

**Masking.** Copies of logs and the text taken from the state pass through
`verification.scrub`: the values of PatchRondo's own environment variables
whose names say they hold a secret, then common credential shapes. This is
best effort; a secret in another shape stays. The ledger is copied as it is,
already filtered when it was written. Share a detailed bundle only with people
you would show the logs to.

**Integrity, not authenticity.** The manifest lists every file with its
SHA-256 and size and carries a digest of itself without its own identity.
`patchrondo bundle verify` recomputes them and checks that the ledger's hash
chain, the validity of each record and the saved gate's ID agree with what
the bundle says. It also checks the ledger on its own: one that fails its
hash chain, or no longer holds the records the task state had seen, is
`MODIFIED` even when the manifest names the damage. That shows changes made
after the export by somebody who did not recompute everything. Nothing is signed (`signature: null`): it does
not prove that PatchRondo produced the bundle, nor that the task's files
were what the records say.

**Checking an untrusted bundle.** `verify` reads the archive in memory and
extracts nothing. It refuses names that are absolute, use `..`, a drive, a
colon or a backslash (checked on the name as stored), directory entries,
links, encrypted entries and names given twice, also ignoring case; it reads
at most 128 MiB of archive, 400 entries, 64 MiB per entry and 192 MiB in all,
and refuses an entry that expands more than 250 times. A manifest of another
format or schema version is not checked at all (`UNSUPPORTED`).

**The page.** The routes of the Verification Center are reads behind the
same token and Host checks as every call; the download is fetched with the
token in its header and handed to the browser only once it arrived whole.
Records, criteria, titles and reviews are inserted as text nodes. An export
takes no lock and writes nothing; at most two run at a time per interface.
No action of the page marks a criterion as verified or accepts a record the
ledger does not count.

## Local interface

`patchrondo` starts an HTTP server for the browser interface. It is meant for
one person on their own computer.

**What protects it.** The server binds to `127.0.0.1` only. Every request must
carry a matching Host header, which defeats DNS rebinding. Every API request
must carry the per-session token in the `X-PatchRondo-Token` header; the token
is generated at start, printed once in the link, passed in the URL fragment
(never in a query string, so it does not reach server logs or Referer
headers), kept by the page for the browser tab and removed from the address
bar. Writes must also be JSON, at most 256 kB, and are refused with a foreign
Origin header. The body of a refused write is never processed: after the
answer it is read and discarded, at most 256 kB and for at most two seconds,
so that closing the connection does not reset it before the refusal is read.
The page is served with a nonce-based Content-Security-Policy
(`default-src 'none'`, scripts and styles only with the nonce, images and
requests only from the same origin, no framing) and loads nothing from
another origin. Keep the printed link private: whoever has the token can do
everything the interface can.

**What the interface can do.** Register a local Git repository, edit the
project settings described in the [user guide](docs/USAGE.md), create tasks, start
`patchrondo run` for a task with `--auto-resume`, `--no-auto-resume` or
`--unlock`, interrupt a run on POSIX, run the environment check of a task
with the repairs described above (the one that needs a decision only when the
request names it), and read task state, logs, diffs and documents. It cannot run a command chosen by the browser: test commands are
configuration, executed only by the engine, without a shell, and only when
`tests.enabled` and `tests.trust_acknowledged` are both true for that project.
The path of a provider executable cannot be set; the CLIs are found on `PATH`
by the engine.

**Consent.** A repository is not trusted because it was added, selected or
imported: a new project starts with tests disabled and untrusted. Enabling
tests through the interface requires the complete test settings with an
explicit `trust_acknowledged: true` in the same request; consent saved earlier
is not carried over to changed commands. A project imported from a 0.1 home
keeps the settings it already had, including tests its owner enabled there.
Starting or resuming a task invokes the real provider CLIs. *Start task* on the
home page does it with that one press, and says so beside the button; *Run*,
*Resume* and *Create and run* ask for confirmation first. Opening the
interface makes no model calls, and no way of starting a task enables or
trusts tests.

**Files.** Static files are served from a list of package files built at
startup. Logs and transcripts can be requested only by an identifier from the
task's own listing, in slices of at most 256 kB. The changed-files view runs
Git read-only in the task worktree, without external diff or text-conversion
programs, and shows the text of a new file only when it is a regular file
inside the worktree; symbolic links are not followed. The folder picker
returns the names of sub-folders of a local folder and whether each is a Git
repository; it returns no file names or contents. *Browse…* opens the
operating system's own folder dialog on the computer that runs PatchRondo
(Explorer on Windows, through `ctypes` in a separate Python process; Finder's
`choose folder` through `osascript` on macOS; `zenity` or `kdialog` on Linux,
only in a graphical session). Those programs are started with argument arrays,
without a shell; the folder a dialog opens in reaches AppleScript as an
argument, never inside the script. One dialog at a time, closed after 30
minutes or when the interface closes; it returns one path, answered only when
it is an absolute folder that exists, and checked like a typed one before
anything is registered. `PATCHRONDO_NATIVE_PICKER=0` turns it off; the page
then lists folders itself. The one write to a folder of yours from the
interface is a first commit you ask for there (*Create a Git repository
here…*): the files are listed first, read through a temporary repository
outside the folder, and `git init` and the commit run only after you confirm,
on exactly that list (a folder changed since is refused). Your Git settings
apply, hooks and signing included; a name and email typed because Git has
none are used for that commit only and written nowhere. A whole drive, the
home folder and PatchRondo's own state are refused. All of this is content of
your own disk shown to you, but remember that task state, logs and diffs may
contain private project data and are not filtered for secrets.

**Untrusted content.** Text written by agents or taken from repositories is
inserted into the page only as text. It is not interpreted as HTML, and links
inside it are not made clickable.

**Live activity.** While a step runs, PatchRondo reads what the provider CLI
prints and records it in `activity.jsonl` in the task's private directory.
The CLIs are started with options that change what they print
(`--output-format stream-json --verbose`, `codex exec --json`), not what they
may do: tools, permission mode, sandbox and login are the same. Recorded are
the agent's public messages, the names and arguments summary of its tool
calls, command lines and the end of their output, changed file paths, test
commands and results, and errors. Not recorded, and not kept in the step's
transcript either, is internal reasoning: Claude's thinking blocks and
Codex's reasoning summaries. The content of files an agent reads is not
recorded. Text is bounded, and values that look like credentials (assignments
to names such as `TOKEN` or `PASSWORD`, bearer tokens, common key formats,
private key blocks) are replaced by `[redacted]`. That filter is best effort:
a secret in another shape is recorded as written, so the journal is private
project data like the logs. The journal is served only through the
authenticated API, for the task it belongs to, read-only and in bounded
pages; it never travels in the snapshot. Nothing in it is acted upon: a
review counts only after the engine has validated the CLI's final answer, and
a reviewer's message that looks like a verdict is recorded as withheld.
`PATCHRONDO_LIVE_ACTIVITY=0` starts the CLIs as before this feature.

**Process information.** The interface shows whether a run or a waiting
process exists by checking the recorded process ID and start time. That check
is for display and for refusing duplicate starts. A process it cannot verify
is neither shown as alive nor treated as gone: a second automatic run of that
task and the removal of its project are refused until a person has settled
it. It never removes a lock:
releasing a stale lock is a confirmed action that starts
`patchrondo run --unlock`, and the engine applies its own check to every
recorded process. Neither that action nor Stop is available on native Windows.

**No secrets.** `settings.json`, `projects.json` and the project
configurations contain paths, names, limits and test commands. PatchRondo does
not store passwords, OAuth tokens or API keys, and does not read the CLIs'
credential files.

Runs continue independently of the interface. Closing it rejects new run starts
and waits up to 30 seconds for starts already in progress. The demo preserves
its repositories, worktrees and private state whenever runs may still be active,
including pending starts, existing run locks and interrupted or failed shutdown.
Demo tests are disabled by default. These controls preserve task state; the host
test execution and provider sandbox limitations above still apply.

## Reporting a vulnerability

Use [private vulnerability reporting](https://github.com/luigiggigo/patchrondo/security/advisories/new)
or **Security → Report a vulnerability** on GitHub. Do not include credentials or
private project logs in public issues. If that option is unavailable, open an
issue requesting a private contact without disclosing the vulnerability details.

Include the affected version, OS, CLI versions, impact and a minimal reproduction
using synthetic data. There is no guaranteed response time for this MVP.
