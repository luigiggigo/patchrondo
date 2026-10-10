# Security

## Scope

The 0.1.x MVP is experimental and intended for trusted local repositories.
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
misclassified, which is why the retry limit cannot be disabled. From that
output only the failure kind, the provider name and a parsed reset time are
added to task state. The task lock is not held while waiting and is never
removed automatically.

Captured process output is bounded in memory; temporary capture files can grow on
disk until the timeout. Apply disk quotas in isolated runners. On native Windows,
process cleanup terminates the direct child only, and stale-lock recovery is
conservative. Use WSL2 for POSIX process-group cleanup.

## Local dashboard

The dashboard in the current development checkout binds to `127.0.0.1`, checks
Host headers, checks Origin headers on writes and requires a per-session token
for its API. Keep the printed URL private; it contains that token. API writes
require JSON. Starting or resuming a task requires confirmation in the interface
and invokes the real provider CLIs; opening the dashboard makes no model calls.
Enabling target-project tests requires explicit boolean trust consent.

Runs continue independently of the dashboard. Closing it rejects new run starts
and waits up to 30 seconds for starts already in progress. The demo preserves
its repository, worktrees and private state whenever runs may still be active,
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
