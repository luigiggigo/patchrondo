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

## Reporting a vulnerability

Once this repository is hosted on GitHub, use **Security → Report a vulnerability**
when private vulnerability reporting is enabled. Do not include credentials or
private project logs in public issues. If that option is unavailable, open an
issue requesting a private contact without disclosing the vulnerability details.

Include the affected version, OS, CLI versions, impact and a minimal reproduction
using synthetic data. There is no guaranteed response time for this MVP.
