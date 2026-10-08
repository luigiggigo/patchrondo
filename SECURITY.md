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
