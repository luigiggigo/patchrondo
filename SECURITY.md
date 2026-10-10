# Security

## Scope

PatchRondo 0.2 is experimental and intended for trusted local repositories.
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
transcripts; delete it when no longer needed and do not publish it. A passing
check shows that the reviewer did not change files in that run. It does not
show that a provider sandbox would block an attempt.

Automatic quota recovery is disabled by default. When enabled through
`recovery.enabled` or `--auto-resume`, a waiting `run`/`resume` process calls
the provider CLIs again without asking, at most `max_consecutive_retries` times
without progress; each call uses plan quota. It only waits for a limit to
reset. Usage limits are recognized from CLI output text and can be
misclassified, which is why the retry limit cannot be disabled. The task lock
is not held while waiting and is never removed automatically.

A failed CLI call is recorded in `state.json` as `last_error`. Its `message`
holds an excerpt of the CLI's own output, up to about 1,200 characters taken
from standard error and standard output, and the report and the interface
display it. Recovery adds only structured fields derived from that output: the
failure kind, the provider name, a reset time and the stated wait in seconds.
The complete captured output is in the run logs of the task directory. None of
it is filtered for secrets, so treat task state as private project data.

Captured process output is bounded in memory; temporary capture files can grow on
disk until the timeout. Apply disk quotas in isolated runners. On native Windows,
process cleanup terminates the direct child only, and stale-lock recovery is
conservative. Use WSL2 for POSIX process-group cleanup.

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
Origin header. The page is served with a nonce-based Content-Security-Policy
(`default-src 'none'`, scripts and styles only with the nonce, images and
requests only from the same origin, no framing) and loads nothing from
another origin. Keep the printed link private: whoever has the token can do
everything the interface can.

**What the interface can do.** Register a local Git repository, edit the
project settings described in the README, create tasks, start
`patchrondo run` for a task with `--auto-resume`, `--no-auto-resume` or
`--unlock`, interrupt a run on POSIX, and read task state, logs, diffs and
documents. It cannot run a command chosen by the browser: test commands are
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
Starting or resuming a task requires confirmation in the interface and invokes
the real provider CLIs; opening the interface makes no model calls.

**Files.** Static files are served from a list of package files built at
startup. Logs and transcripts can be requested only by an identifier from the
task's own listing, in slices of at most 256 kB. The changed-files view runs
Git read-only in the task worktree, without external diff or text-conversion
programs, and shows the text of a new file only when it is a regular file
inside the worktree; symbolic links are not followed. The folder picker
returns the names of sub-folders of a local folder and whether each is a Git
repository; it returns no file names or contents. All of this is content of
your own disk shown to you, but remember that task state, logs and diffs may
contain private project data and are not filtered for secrets.

**Untrusted content.** Text written by agents or taken from repositories is
inserted into the page only as text. It is not interpreted as HTML, and links
inside it are not made clickable.

**Process information.** The interface shows whether a run or a waiting
process exists by checking the recorded process ID and start time. That check
is for display and for refusing duplicate starts. It never removes a lock:
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
