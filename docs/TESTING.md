# Testing and validation

Run these commands from the source checkout. For development setup and package
checks, see [CONTRIBUTING.md](../CONTRIBUTING.md).

## Project tests (no provider quota usage)

The [CI workflow](../.github/workflows/ci.yml) runs on pushes to `main`, pull
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

Dated results and platform coverage are recorded in the
[technical review](REVIEW.md).

Tests use temporary Git
repositories, simulated providers, state checks and mocked CLI arguments. They
do not call Claude or Codex, validate real model output, or replace an end-to-end
test with authenticated accounts. Full package checks are documented in
[CONTRIBUTING.md](../CONTRIBUTING.md).

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
`claude` or `codex` is reachable. Twelve scenarios compare the page with the
files on disk: the criteria of a task from the preflight of the form to their
evidence, with approved code that waits instead of going back to the
developer, an authorization declined and then granted, a statement refused as
evidence and the task completed by the user's checks without another agent
call; models and options chosen on the Agents page for both roles, at
the global and the project level, and a task that is then started with exactly
those arguments; a task started from the home page with one description and one
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

The check needs a local Chromium-based browser and reports a skip when none
is installed. It is separate from CI. Dated browser results and measurements
are recorded in the [technical review](REVIEW.md).

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
second automatic run while a process waits, the explicit consent for tests, or
the rule that a criterion is verified only by its evidence)
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
python tools/provider_e2e.py --authorize-provider-calls    # one developer call per pairing; the reviewer is not checked
python tools/provider_e2e.py --authorize-provider-calls --run-fixture-tests   # developer and reviewer
```

Nothing calls a model without `--authorize-provider-calls`. By default the
fixture's tests stay disabled, so each pairing ends paused at the missing test
consent after one developer call and before any review. That run checks the
developer side only: the reviewer's checks are printed as `not exercised`,
the result line counts them, and they are never reported as passed.
`--run-fixture-tests` is separate consent to run the fixture's unit
test on your machine against agent-written code; the reviewer is then called
and checked too (a parsed review, an unchanged worktree, its activity), and the
check requires a completed, approved task and may use up to four calls per
pairing. A quota,
login or interruption failure stops before the next pairing. The exit status is
0 only if no check fails; on failure the fixture and its private task
state, including provider transcripts, are kept in a temporary directory for
diagnosis. `--pairing` selects a single pairing and `--keep` preserves files
after a pass.

A pass covers only the platform, CLI versions and login reported in its output.
Record authorized runs in [docs/REVIEW.md](REVIEW.md), including their date,
platform, CLI versions and scope. Historical real-provider results are recorded
there; simulated checks do not establish compatibility with authenticated accounts.
