# PatchRondo

<p align="center">
  <img src="docs/assets/rondo-mascot-v1.png" alt="Rondo, the PatchRondo raccoon mascot" width="160">
</p>

**Code. Review. Repeat.**

[![CI](https://github.com/luigiggigo/patchrondo/actions/workflows/ci.yml/badge.svg?branch=main&event=push)](https://github.com/luigiggigo/patchrondo/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB)](pyproject.toml)
[![Experimental alpha](https://img.shields.io/badge/Status-experimental%20alpha-F59E0B)](CHANGELOG.md)
[![MIT license](https://img.shields.io/badge/License-MIT-22C55E)](LICENSE)

PatchRondo coordinates Claude Code and OpenAI Codex CLI in a local development
and review loop. One agent implements a task in a dedicated Git worktree;
the other reviews the changes. PatchRondo runs the tests you authorize,
returns actionable feedback to the developer and saves progress for resumption.

Manage projects, follow agent activity and inspect changes from a local browser
dashboard or the command line. The Python runtime has no external dependencies;
the dashboard needs no build step.

PatchRondo is experimental software for trusted repositories. It uses the
official CLIs and their existing logins; agent calls consume your account's
quota and follow its billing configuration. This independent project is not
affiliated with Anthropic or OpenAI.

## How it works

1. **Define a task.** Select a project, assign the developer and reviewer,
   and describe the change and its acceptance criteria.
2. **Develop and test.** PatchRondo creates a dedicated branch and worktree,
   checks the environment, invokes the developer and runs authorized tests.
   Without test consent, it pauses before calling the reviewer.
3. **Review and refine.** The reviewer reads the changes. Feedback returns
   to the developer until the code is approved or a configured limit is reached.
4. **Verify and integrate.** Completion requires passing tests, code approval
   and evidence for every required acceptance criterion. Checks that need you
   pause the task. You review and integrate the result yourself.

Tasks retain checkpoints, logs, test results and review feedback. Quota errors
pause the loop; optional automatic recovery retries within configured limits
while the run process remains active.

## Requirements

- Python **3.11+** and Git.
- Claude Code 2.1.248+ with `--restricted` support and Codex CLI, installed
  on `PATH` and authenticated before running tasks.
- A trusted Git repository with a clean working tree and at least one commit.

Linux and macOS are supported. On Windows, **WSL2 is recommended** for process
cleanup and interruption handling; native Windows has limitations described
in the [user guide](docs/USAGE.md#security-and-practical-limitations).

## Quick start

From Linux, macOS, WSL2 or Git Bash:

```bash
git clone https://github.com/luigiggigo/patchrondo.git
cd patchrondo
./init.sh
./main.sh
```

`init.sh` creates a virtual environment, installs the package and checks CLI
availability without calling a model. `main.sh` opens the dashboard.

The setup wizard lets you register a repository, check the agents, choose
roles and configure test consent. Then describe a task on the home page and
press **Start task**. Starting or resuming a task calls the real agents.

To explore the dashboard with sample data:

```bash
./main.sh --demo
```

The demo starts with tests disabled. Starting a task from the demo still
uses the real provider CLIs.

Alternatively, install from the cloned checkout in your own Python environment
(on native Windows, use PowerShell):

```bash
python -m pip install -e .
patchrondo
```

For terminal workflows, start with `patchrondo --help` and the
[command-line guide](docs/USAGE.md#command-line).

## Safety and limitations

- **Tests require explicit consent.** Configure commands and acknowledge that
  you trust the repository. Tests run on your computer with your permissions;
  a Git worktree does not sandbox them.
- **The reviewer is read-only.** Developer settings that broaden access
  require a separate acknowledgement.
- **Results need human review.** AI approval is not a guarantee of correctness.
  PatchRondo does not automatically commit, merge, push or publish changes.
- **Task data is private.** State, logs and transcripts live outside the target
  repository, under `~/.patchrondo` by default. Keep them and the dashboard's
  session link private. The server listens on `127.0.0.1` only.

Read [SECURITY.md](SECURITY.md) before running tasks, including the limitations
of provider sandboxes, credential filtering and billing settings.

## Documentation

| Guide | Contents |
|---|---|
| [User guide](docs/USAGE.md) | Dashboard, CLI, agent settings, acceptance criteria, recovery and integration |
| [Contributing](CONTRIBUTING.md) | Development setup and contribution guidelines |
| [Testing](docs/TESTING.md) | Simulated checks, browser tests and opt-in real-provider validation |
| [Architecture](docs/ARCHITECTURE.md) | Engine, state, permissions and module responsibilities |
| [Interface design](docs/DESIGN.md) | Dashboard layout, components and accessibility |
| [Changelog](CHANGELOG.md) | Version history and dated changes |
| [Validation record](docs/REVIEW.md) | Dated results, platforms and known verification gaps |
| [Roadmap](docs/ROADMAP.md) | Future development areas |

Licensed under the [MIT License](LICENSE).
