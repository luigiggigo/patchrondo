# Technical review and validation record

Initial review: October 8, 2026. Local follow-up: October 9, 2026. Real-provider
checks and the automatic quota recovery change: October 10, 2026. PatchRondo
0.2 (interface, several projects, process truth): October 10, 2026, as a local
working tree.
The initial scope covered the Python sources, tests, examples, documentation
and metadata supplied before source publication. No `AGENTS.md` or existing Git
repository was present in those original directories. A local, ignored
`AGENTS.md` was added during publication preparation. The source is now public.
Each section below says which code it covers: a published commit or a local
working tree at its date.

## Assessment

The initial **0.1.0 alpha/MVP source** is public on
[GitHub](https://github.com/luigiggigo/patchrondo) since October 8, 2026.
Responsibilities are clearly separated between the CLI, state machine,
adapters, process handling, Git and persistence. There are no Python runtime
dependencies. Explicit test
consent, atomic checkpoints and the absence of automatic merges are useful
foundations.

The initial preparation fixed the defects recorded below. A first check with
authenticated real CLIs passed on October 10, 2026 on one platform and one pair
of CLI versions, without reaching passing tests or approval; its scope is
recorded below. A second check on the same platform and CLI versions completed
both pairings with passing fixture tests and approved reviews. Broader validation
with authenticated real CLIs is still
required before promising complete compatibility or production readiness.
The MIT license already existed; this review cannot certify ownership or the
provenance of every contribution.

## Initial review findings and fixes

| Priority | Original problem and consequence | Fix / evidence |
|---|---|---|
| P1 | `providers.py`: `--ask-for-approval` after `exec` was not supported by the installed CLI subcommand, so Codex failed before invoking the model. | Move the global flag before `exec`; check the local parser without model calls and assert the argument order in tests. |
| P1 | `providers.py`: on resume, `*.last-message.txt` could contain an earlier attempt's reply and be accepted as current output. | Delete it before each invocation and require a new final reply; test an old approval with missing new output. |
| P1 | `core.py`: review after a pause could use tests that passed before file or test-command changes. | Check file fingerprints and commands before/after review; pause with `stale_tests` and resume from tests. Cover external edits, a mutating reviewer and changed/disabled test settings. |
| P1 | `core.py`: truthy values such as `"false"` could count as consent to host test execution. | Require JSON booleans; integer settings also reject `True`; regression tests included. |
| P1 | `core.py`: a `--home` inside the repository could include private state/logs in published code. | Resolve and reject such paths before state creation and when loading configuration. |
| P1 | `core.py`: a backslash inside an f-string expression was incompatible with the advertised Python 3.11 support. | Compute the join before the f-string; parse sources using Python 3.11 grammar. |
| P2 | `core.py`: test timeouts triggered further iterations and model calls instead of pausing. | Persist the timeout result and pause; resume reruns tests without repeating development. |
| P2 | `core.py` / `report.py`: Git errors could leave state marked `running` and also prevent report generation. | Pause on Git errors/timeouts and produce a report containing the available diagnostics. |
| P2 | `core.py`: an unknown phase could loop forever; non-hashable verdict/severity values in JSON could crash the runner. | Validate phases and review field types; handle invalid reviews and cover them with tests. |
| P2 | `process.py`: `communicate()` accumulated unbounded stdout/stderr in RAM; on POSIX a child ignoring TERM could outlive its leader. | Capture to temporary files with bounded reads and retained tails; KILL the process group after TERM. Test 2 MB output and POSIX cleanup. |
| P2 | Shebang-based integration scripts could not execute on native Windows. | Invoke the same synthetic scripts through Python on Windows, preserving real subprocess execution and I/O. |
| P2 | `cli.py`: `--help` and Unicode-arrow messages failed with redirected CP1252 output on Windows. | Explicit UTF-8 CLI output and a subprocess regression test starting with CP1252 encoding. |
| P3 | The command suggested by `new` omitted a custom `--home`. | Include the state directory in the printed command. |

P1 means a blocker or a serious correctness/consent/privacy issue; P2 means a
reliability or portability issue; P3 means a usability issue.

The Codex flag's global scope is documented in the
[official CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli).
Claude's `--restricted` requirement, version 2.1.248 or later, is confirmed by the
[official Claude reference](https://code.claude.com/docs/en/cli-reference).
The local Codex parser check used `codex-cli 0.162.0-alpha.2`; it does not certify
compatibility with every distributed CLI version.

## Repository preparation

- `.gitignore` excludes credentials, local agent state/instructions, logs, virtual environments and builds.
- `.gitattributes` normalizes text files; SPDX MIT metadata and the license are included in packages.
- Added `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, issue/PR templates and a publishing guide.
- Prepared CI for Python 3.11-3.14 on Linux, macOS and Windows, with `contents: read`, official actions pinned to commits, no provider login and no automatic publication.
- Built wheel and sdist packages, checked them with Twine and smoke-tested the wheel in a separate environment.
- Added a tested heuristic publication check; no recognized credentials were found in the examined public files. This is not an exhaustive guarantee that secrets are absent.
- Translated documentation, CLI messages, task templates, generated reports and examples into English. Local `AGENTS.md` is excluded from Git and public archives/distributions.

## Initial publication validation (October 8, 2026)

At initial publication the suite contained **45 tests**. Local execution on
Windows with Python 3.13.3 produced **44 passing tests and 1 skipped test**, which
requires POSIX process groups.
[GitHub CI](https://github.com/luigiggigo/patchrondo/actions/runs/37834267237)
passed all 12 combinations of Python 3.11-3.14 and Linux, macOS and Windows,
plus the package build, metadata and installed-wheel checks.

Initial sandbox restrictions on temporary directories and Git's ownership checks
on a filesystem without ownership were addressed by running local tests with
normal temporary directories. Global Git configuration was not changed.

## Local validation (October 9, 2026)

On that date the GitHub Actions CI workflow was disabled and removed from the
checkout, leaving local validation. The initial CI results remain a historical
record of the published snapshot. Restoration is recorded below.

On that date the development suite contained **66 tests**. Local execution on
Windows with Python 3.13 completed with **65 passing tests and 1 skipped POSIX
process-group test**. These tests use simulated providers and synthetic
repositories. No authenticated provider calls were made. This result applies
to the local working tree; the initial GitHub CI matrix above covers the
published snapshot, not these newer changes.

Dashboard review and regression coverage include:

- Mascot images inlined as `data:` URIs with no placeholder left in the page.
- Token-protected API access, Host checks, Origin checks on writes and JSON-only writes.
- Disabled demo tests by default and explicit boolean consent before enabling host tests.
- Source-only child imports and reporting immediate run-start failures.
- Windows and POSIX command round-trips, including embedded quotes, empty
  arguments and trailing backslashes, plus 3,000 deterministic random commands.
- Immediate process registration, refusal of new Run requests while closing,
  waiting for admitted starts and prompt shutdown with idle connections.
- Reporting pending starts after the shutdown timeout and retaining demo files
  for active runs, pending starts, existing locks, `--keep` and interrupted exit.
- A second Ctrl+C during shutdown returns 130 and preserves demo files; deletion
  is allowed only after a confirmed clean shutdown.

The heuristic publication check recorded on that date reported two items: an
ignored local `debug.log` and the binary mascot asset
`docs/assets/rondo-mascot-v1.png`. The October 10 result below supersedes it.
These require review; the scan does not report a clean result. The checked
documentation, changed Python files and startup scripts use LF line endings.

## Real-provider check preparation (October 10, 2026)

`tools/provider_e2e.py` was added to run the loop against the authenticated
CLIs with both pairings, behind explicit `--authorize-provider-calls` consent.
The preparation recorded in this section made no provider calls; the first
authorized run is recorded in the next section.

- The suite grew to **73 tests**. Windows with Python 3.13.3: 72 passed and 1
  POSIX process-group test skipped. WSL2 with Python 3.12.3: 73 passed.
  The seven new tests drive the tool with simulated CLIs and cover the
  authorization gate, the readiness gate, disabled fixture tests by default,
  consented fixture tests, a reviewer that edits the worktree, a rejected CLI
  argument and stopping on a quota failure. Removing the authorization gate,
  the fixture-test consent or the quota stop each made a test fail.
- Preflight only, without model calls: WSL2 reported Claude Code 2.1.291
  (login method `claude.ai`) and `codex-cli 0.160.1`, both logged in. Native
  Windows reported Claude Code 2.1.295 and no Codex on `PATH`, so both pairings
  can currently be checked on this machine only under WSL2.
- Parser help only: `codex-cli 0.160.1` lists every Codex argument the adapter
  uses, with `--ask-for-approval` as a global option before `exec`. Claude Code
  2.1.291 and 2.1.295 list every Claude argument the adapter uses except
  `--max-turns`, which is absent from `claude --help`. The authorized run
  below shows that 2.1.291 still accepts it.
- The heuristic publication check exited with status 1 and four findings, all
  predating this change: the ignored local `debug.log` and three binary images
  (`docs/assets/rondo-mascot-v1.png`, `src/patchrondo/static/rondo.webp`,
  `src/patchrondo/static/rondo-head.webp`). These require review; the scan
  does not report a clean result.

## First real-provider run (October 10, 2026)

The user authorized one run of
`python3 tools/provider_e2e.py --authorize-provider-calls`. It exited with
status 0 and reported `RESULT: PASS` for both pairings.

- Platform: WSL2 (Linux 6.6.87.2-microsoft-standard-WSL2, glibc 2.39), Python
  3.12.3, Claude Code 2.1.291 with login method `claude.ai`, codex-cli 0.160.1
  with its ChatGPT login.
- Claude → Codex (28 s) and Codex → Claude (17 s): the developer returned a
  handoff and created `greeting.py` in the worktree as the only change, made
  no commits and left the main checkout clean; the reviewer returned a review
  that parsed and left the worktree fingerprint unchanged; the loop paused
  with `tests_disabled`.
- Each CLI therefore accepted the adapter's arguments in both roles, including
  Claude's `--restricted`, `--max-turns` and `--json-schema` and Codex's
  `--sandbox`, `--ephemeral`, `--output-last-message` and `--output-schema`.
- The verdicts were `BLOCKED` (Codex) and `CHANGES_REQUESTED` (Claude). With
  the fixture's tests disabled the prompt reports them as skipped, so approval
  was not expected; the review texts were not examined, because a passing run
  deletes its files.

This run made four provider calls on a trivial task. It does not cover a run
through passing tests and final approval (`--run-fixture-tests`), a second
iteration with reviewer feedback, resumption after an interruption, timeouts,
quota handling with real error messages, native Windows, macOS or other CLI
versions. Whether a reviewer's sandbox would block an attempted edit was not
tested: the reviewers did not change files.

## Real-provider run with fixture tests (October 10, 2026)

The user explicitly authorized real provider calls and host execution of the
fixture tests through
`python3 tools/provider_e2e.py --authorize-provider-calls --run-fixture-tests`.
This local WSL2 run exited with status 0 and reported `RESULT: PASS` for both
pairings. It was not run in GitHub CI.

- Platform: WSL2 (Linux 6.6.87.2-microsoft-standard-WSL2, glibc 2.39), Python
  3.12.3, Claude Code 2.1.291 with login method `claude.ai`, codex-cli 0.160.1
  with login confirmed.
- Claude → Codex passed in 18 seconds; Codex → Claude passed in 16 seconds.
  Both reached status `done` with passing fixture tests and an `APPROVED`
  review. The fixture configuration enabled both `tests.enabled` and
  `tests.trust_acknowledged` as JSON booleans and ran
  `python3 -m unittest -v test_greeting` using the interpreter's absolute path.
- All six checks passed for each pairing: a nonempty developer handoff,
  `greeting.py` defining `greet` in the worktree, no developer commits and a
  clean main checkout, a parsed review, an unchanged worktree after review,
  and completion with passing tests and approval. The reported worktree
  change was only the untracked `greeting.py`.
- The tool removed the temporary fixture and private task state after success,
  as designed without `--keep`. Provider transcripts were not examined or
  copied into this repository.

This validates completion for the trivial greeting fixture on the reported
platform and CLI versions. It does not establish behavior on larger projects,
resumption after interruption, reviewer-feedback iterations, real quota or
timeout failures, or enforcement against an attempted reviewer edit. No
runtime code or CI configuration was changed for this check. README and
changelog validation summaries were updated; documentation checks covered
local links, UTF-8, LF line endings and diff whitespace.

## Retrieval candidate fix (October 10, 2026)

`Index.search` limited the full-text candidates to the top 300 of the shared
index and only then kept those of the searched worktree. It now restricts
matches to that worktree before the limit.

- A new regression test indexes more than 300 better-ranked chunks that exist
  only in the main checkout and searches a worktree whose single match ranks
  lower. Against the previous query it returned no excerpts and failed.
- The suite grew to **74 tests**. Windows with Python 3.13.3: 73 passed and 1
  POSIX process-group test skipped. WSL2 with Python 3.12.3: 74 passed. No
  provider calls were made.
- A synthetic index of 24,000 chunks across six roots took about 40 ms for a
  45-term query on Windows; larger indexes were not measured. The publication
  and package checks were not rerun for this change.

## Free CI restoration (October 10, 2026)

The current checkout restores `.github/workflows/ci.yml` for pushes to `main`,
pull requests targeting `main` and manual dispatch. The six matrix combinations
cover Python 3.11 and 3.13 on Linux, Windows and macOS. Package build and metadata
checks run on Linux with Python 3.13. Jobs have a 15-minute timeout and newer runs
cancel older runs on the same branch or PR.

- Verified in the [official GitHub runner documentation](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#standard-github-hosted-runners-for-public-repositories)
  that `ubuntu-latest`, `windows-latest` and `macos-latest` are standard runners
  free for public repositories. A job-level repository privacy condition skips
  the matrix before runner allocation for private repositories.
- Only official checkout and Python setup actions are used, pinned to release
  commits. Token permissions are limited to `contents: read`; checkout does not
  persist credentials. There are no artifact uploads, Actions caches, deployments,
  provider credentials or authenticated provider calls.
- Local validation on native Windows with Python 3.13.3: **74 tests**, 73 passed
  and 1 POSIX process-group test skipped, using `PYTHONPATH=src`. The initial run
  failed because Git rejected ownership of temporary repositories on the local
  drive. A process-scoped `safe.directory` setting limited to `.test-tmp/*`
  resolved this without changing global Git settings or project code.
- Editable installation and `python -m build` succeeded. Twine passed for the
  newly generated PatchRondo wheel and sdist. The initial `dist/*` check exited
  with status 1 because a pre-existing `dist/patchrondo` directory is not a
  distribution; checking the two generated files explicitly passed. CI builds
  in a fresh checkout and uploads neither file.
- Local YAML structure checks covered triggers, all six combinations, the
  job-level guard, runner labels, timeout, cancellation, permissions, install
  and test commands, and the absence of artifact/cache actions. Changed text
  files passed UTF-8, LF and diff whitespace checks. These are local checks;
  the restored workflow has not been pushed or run on GitHub, and this change
  has not been tested locally on Linux or macOS.
- The publication scanner exited with status 1 and four pre-existing findings:
  ignored `debug.log` and the three binary images
  `docs/assets/rondo-mascot-v1.png`, `src/patchrondo/static/rondo.webp` and
  `src/patchrondo/static/rondo-head.webp`. This is not a clean scan. Publication
  review remains local so binary findings require human review rather than
  being silently ignored by CI.

## Local reliability checks (October 10, 2026)

Added `tools/reliability_e2e.py` and eight driver tests. These use the real
adapters and CLI entry point in separate processes, with every provider command
routed exclusively to a local synthetic Python executable. Runners also remove
provider directories from PATH and verify neither CLI can be found. No authenticated
provider calls were made. Runtime code and CI configuration were unchanged.

The WSL2 driver run used Python 3.12.3 on Linux
6.6.87.2-microsoft-standard-WSL2. Three repetitions of six scenarios in both
role pairings produced **36 passed, 0 failed, 0 skipped**, exit status 0.
The local metrics report is `.test-tmp/reliability-wsl.json` (ignored).

| Scenario | Executions | Iterations | Attempted provider calls | Median elapsed time |
|---|---:|---:|---:|---:|
| Review feedback, correction, approval | 6 | 2 | 4 | 0.796 s |
| Developer quota, resume | 6 | 1 | 3 | 0.830 s |
| Reviewer quota, resume | 6 | 1 | 3 | 0.853 s |
| SIGINT during review, resume | 6 | 1 | 3 | 1.102 s |
| Killed runner, stale lock, unlock/resume | 6 | 1 | 2 | 1.008 s |
| Three-file task | 6 | 1 | 2 | 0.546 s |

Times include fixture setup and orchestration and are synthetic-provider
measurements, not expected real-model latency. Reports also include individual
provider-call durations, test runs, changed-file counts and named checks.

- The feedback scenario starts with six passing functional tests, receives
  `CHANGES_REQUESTED` for missing validation documentation, confirms delivery
  of that actionable feedback and approves the corrected second iteration.
- Quota is injected once for each provider in both developer and reviewer roles.
  The task pauses at the failed phase, with no additional calls before the
  driver's explicit resume. Review recovery does not repeat development or tests.
- SIGINT interrupts a runner waiting for a synthetic reviewer. Checks confirm
  provider termination, removed process markers and lock, preserved code and
  successful review-only resume in a new runner process.
- SIGKILL occurs at the saved review checkpoint before a reviewer child starts.
  Ordinary resume refuses the stale lock; `--unlock` succeeds after the runner
  exits. This does not cover a killed runner with a surviving agent process.
- The fixture spans name validation, batch greetings and JSON CLI I/O. Six
  independent functional cases cover names, whitespace, Unicode, empty input,
  blank-name rejection and CLI output. Final checks require documentation,
  exactly three changed files, preserved tests, unchanged main checkout and
  Git history, and an unchanged worktree after review.
- Negative controls remove review feedback at the synthetic CLI boundary and
  inject incorrect greeting behavior despite an approving reviewer. Both are
  detected as failures, so approval alone cannot satisfy the driver.
- Full local suite: **82 tests**. WSL2 with Python 3.12.3: 82 passed. Native
  Windows with Python 3.13.3: 79 passed, 3 POSIX tests skipped. After adding
  explicit provider PATH removal and refining the missing-feedback negative
  control, all eight driver tests were rerun: WSL2 passed all eight; Windows
  passed six with two POSIX skips. The earlier full-suite run precedes these
  driver-only refinements.
  Checked changed Python sources against Python 3.11 grammar and checked
  changed text for UTF-8, LF, local link targets and diff whitespace.
- Publication scanner: exit status **1**, with four pre-existing findings:
  ignored `debug.log`, `docs/assets/rondo-mascot-v1.png`,
  `src/patchrondo/static/rondo.webp` and `src/patchrondo/static/rondo-head.webp`.
  This is not a clean scan. Packages were not rebuilt for this tooling change.

Resume automation belongs to the check driver, not the runtime. PatchRondo
still requires an explicit resume after interruption or quota exhaustion.
Synthetic checks validate orchestration and objective fixture acceptance;
larger real-model tasks, solution quality, actual quota recovery and real-model
timings remain unvalidated. The earlier real-provider fixture results are
recorded separately above. No workflow was pushed or run remotely here.

## Automatic quota recovery (October 10, 2026)

Added opt-in recovery after provider usage limits: the `recovery` configuration
section, `--auto-resume` / `--no-auto-resume`, reset parsing in `providers.py`,
the policy and foreground supervisor in the new `recovery.py`, and the plan in
`state.json`, reports and the dashboard. It is disabled by default. This
supersedes the statement in the previous section that the runtime never retries
a quota failure: it still does not unless recovery is active. The validation
recorded first was local. The maintainer then committed and pushed that state
to `main` as `791566c`, and
[GitHub CI run 38065224479](https://github.com/luigiggigo/patchrondo/actions/runs/38065224479)
completed successfully for it: all six jobs, Python 3.11 and 3.13 on
`ubuntu-latest`, `windows-latest` and `macos-latest` (read with `gh` on October
10, 2026). No version was tagged or released. The follow-ups described further
down were not part of that run; each states its own status. **No provider calls
were made**:
every check below used scripted or synthetic providers.

Design points reviewed:

- The retry plan is saved in the same atomic write as the quota pause, while
  the task lock is held. `run_task` evaluates a pending plan before it clears
  `last_error`, and makes no provider call before `resume_at`.
- The supervisor waits without the lock. On waking it passes the `resume_at` it
  waited for; `run_task` rereads the state under the lock and proceeds only if
  that plan is still saved, still a quota pause and due.
- Only `quota` is retried. Retries are bounded by consecutive failures without
  progress and by a total wait budget; a known reset beyond the budget leaves
  the task paused. A stated reset cannot shorten the backoff.
- Locks are never removed automatically, and `--unlock` is not passed to
  automatic retries. A lock found at retry time ends recovery with an error.
- From provider output, recovery adds to task state the failure kind, the
  provider name and a parsed reset. `last_error.message` also holds an excerpt
  of that output, as it did before this change (corrected wording; see the
  second follow-up below).

Validation, all on October 10, 2026:

- The suite grew from 82 to **140 tests**: 56 in `tests/test_recovery.py` and
  two more driver tests. Native Windows with Python 3.13.3: 137 passed, 3 POSIX
  tests skipped. Native Windows with Python 3.11.3: 137 passed, 3 skipped. WSL2
  (Linux 6.6.87.2-microsoft-standard-WSL2) with Python 3.12.3: 140 passed. The
  recovery tests use an injected clock and replace `providers.execute` with a
  guard that fails the test if a provider CLI would be started.
- They cover the default-off and legacy configurations, strict setting types
  and ranges, reset parsing (explicit offset, relative waits, ambiguous,
  impossible and expired times), a misclassified error, the retry limit, the
  wait budget, a reset beyond the budget, restart before the reset, absence of
  the lock during every wait, two supervisors (threads, one inside its provider
  call), counter reset on progress, review resumed without repeating
  development or tests, Ctrl+C while waiting, an orphaned lock with `--unlock`
  given to the first run, the conservative native-Windows liveness check, each
  non-quota failure kind, reports, events and the CLI flags.
- Protection check: 26 single changes that each remove one safeguard (for
  example the due check under the lock, the plan comparison, the retry limit,
  the budget, the safety margin, lock-free waiting, strict integer types, the
  explicit-offset requirement) were applied one at a time to the sources and
  the recovery tests rerun. 25 made tests fail. The one that did not, accepting
  an expired reset, exposed a missing assertion; with that test added it fails
  too. This is a manual, local check, not part of the suite.
- `python tools/reliability_e2e.py --repeat 3`, now seven scenarios in both
  pairings. WSL2: **42 passed, 0 failed, 0 skipped**, exit status 0 (report in
  the ignored `.test-tmp/reliability-recovery-wsl.json`). Native Windows with
  Python 3.13.3: 30 passed, 0 failed, 12 skipped (the POSIX interruption and
  crash scenarios), exit status 0. In the new `auto-resume` scenario one runner
  started with `--auto-resume` meets `Retry after 300 seconds` from the
  synthetic reviewer, waits on a virtual clock and completes: three provider
  calls, one development and one test run, no lock during the wait, and the
  retry not before the planned time. A negative control with a reviewer that
  never leaves its limit ends after five calls with recovery stopped.
- That scenario found a defect during development: the stated reset was saved
  with its fractional second truncated, so a retry could start up to one second
  before "reset plus margin". Times a retry depends on are now rounded up. A
  regression test with a sub-second clock fails against the truncating code
  and passes with the fix.
- One real-clock check under WSL2, with synthetic CLIs and a 12-second stated
  reset: SIGINT during the wait ended the runner with status 2, plan and pause
  unchanged and no lock; a restarted runner made exactly one more call, 0.04 s
  after the planned time, and completed. 10 of 10 checks passed. This was an
  ad-hoc script and is not in the repository.
- The dashboard task page and overview were rendered in headless Chrome from
  the demo data, which now includes a paused task with a saved plan. The demo
  makes no provider calls; the throwaway directory was removed afterwards.
- Packages built from this tree passed `twine check`; the wheel contains
  `patchrondo/recovery.py`. Publication scanner: exit status **1** with the same
  four findings as before (ignored `debug.log` and the three binary images).
  This is not a clean scan. Changed text files were checked for UTF-8 and LF.

Follow-up on the same day, from a review of the change: `OfficialCLI.invoke`
classified `stderr or stdout`, so when both streams had content only standard
error was read. A usage limit printed on standard output next to a generic
error on standard error became `agent_error`, and a reset time on the other
stream was lost. Both streams are now read. Standard error decides the kind
when it names a specific cause, so agent text on standard output cannot turn
a login failure into a usage limit; otherwise standard output decides. A reset
stated on either stream is kept, also when Claude exits with status 0 and
reports the failure in its JSON result. The saved diagnostic shows a bounded
excerpt of each stream.

- Three adapter tests were added. Against the previous code five of their
  cases failed: a usage limit only on standard output for each provider, a
  reset only on standard output, a reset on standard error beside an
  `is_error` result, and a usage limit on standard output behind a long
  standard error. A further case checks that a login failure on standard
  error is not reclassified by standard output.
- Reading standard output widens what can match the usage-limit patterns when
  standard error is empty or generic. The retry limit remains the bound.
- The protection check and the real-clock check described above were ad-hoc
  scripts. They are now `tools/recovery_checks.py`, with two tests of their
  own. `protections` works on a temporary copy of the sources, so the working
  tree is not modified, and covers 32 safeguards: the original 26, the two
  rounding fixes and four for the stream handling.

Every check was then rerun on the final code of this follow-up, still on
October 10, 2026 and without provider calls:

- Suite: **145 tests**. Native Windows with Python 3.13.3: 142 passed, 3 POSIX
  tests skipped. Native Windows with Python 3.11.3: 142 passed, 3 skipped. WSL2
  (Linux 6.6.87.2-microsoft-standard-WSL2) with Python 3.12.3: 145 passed.
- `python tools/recovery_checks.py protections` on native Windows with Python
  3.13.3: 32 of 32 removed safeguards detected, exit status 0.
- `python3 tools/recovery_checks.py real-clock` under WSL2: 10 of 10 checks
  passed, the retry 0.04 s after the planned time, exit status 0. On native
  Windows it reports itself skipped.
- `python tools/reliability_e2e.py --repeat 3`: WSL2 42 passed, 0 failed, 0
  skipped; native Windows 30 passed, 0 failed, 12 skipped; exit status 0.
- Packages built outside the repository's `dist/` passed `twine check`. The
  wheel contains `patchrondo/recovery.py`; the source archive contains
  `tools/recovery_checks.py` and no `AGENTS.md`.
- Publication scanner: exit status **1**, 53 files checked, the same four
  findings (ignored `debug.log` and the three binary images). Not a clean scan.

Documentation was updated after these runs and checked for links, UTF-8 and LF.
The maintainer committed and pushed this follow-up as `f4f8ad0`;
[GitHub CI run 38068523374](https://github.com/luigiggigo/patchrondo/actions/runs/38068523374)
completed successfully for it, all six jobs (read with `gh` on October 10, 2026).

Second follow-up on the same day, from a further review. Two points were
checked against the code and both were present:

- A reset stated only as a wait was ignored. `AgentFailure` allows an adapter
  to set `retry_after_seconds` without `retry_at`; the value was saved but the
  retry decision read `retry_at` alone, and a non-integer wait was dropped
  silently. The official adapter always sets both, so its behavior was not
  affected. `recovery.stated_reset` now accepts either form: a wait counts
  from the failure time, the later instant is used when both are given, and
  `last_error` always saves the result as `retry_at`, rounded up. A saved pause
  that holds only the wait is honored as well. Zero, negative, non-numeric and
  non-finite waits are not used; a wait beyond any date stops recovery instead
  of being brought forward.
- Documentation said that only structured metadata is kept from provider
  output. `last_error.message` also holds the adapter's diagnostic, an excerpt
  of that output, as it did before recovery existed. `SECURITY.md`,
  `ARCHITECTURE.md`, the docstrings and the design point above now say so. On
  one path the excerpt was not bounded: a Claude `is_error` result was saved
  whole. It is now limited to about 1,200 characters like a failed exit.

Six tests were added; nine of their cases failed against the previous code.
The protection check gained three entries for the wait-only form. Every check
was rerun on this code on October 10, 2026, without provider calls:

- Suite: **151 tests**. Native Windows with Python 3.13.3: 148 passed, 3 POSIX
  tests skipped. Native Windows with Python 3.11.3: 148 passed, 3 skipped. WSL2
  (Linux 6.6.87.2-microsoft-standard-WSL2) with Python 3.12.3: 151 passed.
- `python tools/recovery_checks.py protections` on native Windows with Python
  3.13.3: 35 of 35 removed safeguards detected, exit status 0.
- `python3 tools/recovery_checks.py real-clock` under WSL2: 10 of 10 checks
  passed, the retry 0.05 s after the planned time, exit status 0; skipped on
  native Windows.
- `python tools/reliability_e2e.py --repeat 3`: WSL2 42 passed, 0 failed, 0
  skipped; native Windows 30 passed, 0 failed, 12 skipped; exit status 0.
- Packages built outside the repository's `dist/` passed `twine check`.
  Publication scanner: exit status **1**, 53 files checked, the same four
  findings. Not a clean scan.

Diagnostic excerpts and run logs are not filtered for secrets; that is
unchanged and documented, not fixed. The maintainer committed and pushed this
second follow-up as `375d0cd`;
[GitHub CI run 38071152945](https://github.com/luigiggigo/patchrondo/actions/runs/38071152945)
completed successfully for it, all six jobs (read with `gh` on October 10, 2026).

Third follow-up on the same day, from a further review with three points.

- *Classification precedence.* Confirmed: quota patterns were tested before
  authentication patterns, so one message naming both became `quota` and could
  be retried. The rules are now an ordered list with authentication first.
  Three mixed messages, each on either stream and as a Claude `is_error`
  result, failed against the previous code. The rule that a specific cause on
  standard error is not overridden by standard output is unchanged and now
  tested in both directions.
- *Real messages.* No usage limit was provoked and no provider call was made,
  so nothing was observed live. Two offline sources were read instead.
  The public Codex source at tag `rust-v0.160.1`, the installed version
  (`codex-rs/protocol/src/error.rs`, `format_retry_timestamp`), shows that a
  usage limit reads "You've hit your usage limit. … or try again at 3:45 PM."
  when the reset falls on the same local day and "… try again at Oct 12th,
  2026 3:45 PM." otherwise, in the local time zone of the Codex process, with
  no zone and no seconds; without a reset it ends "try again later." The
  parser recognized none of these, so every real Codex limit would have used
  the backoff only. For Codex failures this form is now read in the local
  zone of the host, rounded up one minute, taking the later instant in an
  hour repeated by a clock change; the same text from another source is still
  not used. The strings of the installed Claude Code 2.1.295 binary were
  searched as well. They contain fragments such as "You've hit your" and
  "resets at" and an internal reset in epoch seconds, but the text printed in
  non-interactive mode could not be established, so nothing was added for
  Claude Code: its limits are expected to fall back to the backoff.
- *Native Windows locks.* Not changed. A liveness check alone would not make
  unlocking safe there, because a timeout terminates only the direct child and
  surviving descendants of an agent are not recorded. It is listed in the
  README roadmap together with process-tree tracking.

Five tests were added (one skipped without POSIX `tzset`), and four entries in
the protection check. Every check was rerun on this code on October 10, 2026,
without provider calls:

- Suite: **156 tests**. Native Windows with Python 3.13.3: 152 passed, 4
  skipped (3 POSIX process tests and the time-zone test). Native Windows with
  Python 3.11.3: 152 passed, 4 skipped. WSL2 (Linux
  6.6.87.2-microsoft-standard-WSL2) with Python 3.12.3: 156 passed. The
  recovery tests also passed under WSL2 with `TZ` set to `Asia/Tokyo`,
  `America/Los_Angeles` and `Pacific/Kiritimati`; the Windows runs used the
  host's own zone.
- `python tools/recovery_checks.py protections` on native Windows with Python
  3.13.3: 39 of 39 removed safeguards detected, exit status 0.
- `python3 tools/recovery_checks.py real-clock` under WSL2: 10 of 10 checks
  passed, the retry 0.04 s after the planned time, exit status 0; skipped on
  native Windows.
- `python tools/reliability_e2e.py --repeat 3`: WSL2 42 passed, 0 failed, 0
  skipped; native Windows 30 passed, 0 failed, 12 skipped; exit status 0.
- Packages built outside the repository's `dist/` passed `twine check`.
  Publication scanner: exit status **1**, 53 files checked, the same four
  findings. Not a clean scan.

This third follow-up is local: it has not been pushed and no CI run covers it.

Not verified:

- Recovery with authenticated CLIs. No real usage-limit failure was observed.
  The Codex wording comes from its source, not from the output of `codex exec`
  at a limit: which stream carries it and what surrounds it are unconfirmed.
  The Claude Code wording is unknown. An unrelated error may still be
  classified as a usage limit (the retry limit applies).
- Waits of realistic length, system suspend during a wait and clock changes
  during a wait. Named time zones in messages are not read.
- A supervisor started from the dashboard, beyond the unchanged run-start path;
  dashboard Run/Resume was not exercised because it invokes real provider CLIs.
- The third follow-up on macOS or on Linux outside WSL2. The CI runs named
  above cover those platforms for commits `791566c`, `f4f8ad0` and `375d0cd`
  only.
- Two supervisors as separate operating-system processes; concurrency was
  tested with threads sharing the same lock file and state.

## PatchRondo 0.2: interface, projects and process truth (October 10, 2026)

Scope: the 0.2.0 working tree on one development machine. At the time of
writing nothing of it was committed or pushed, so the GitHub repository and
its CI runs do not contain or cover it. No provider call was made and no plan
quota was used: every run in these checks used scripted or synthetic
providers. CLI status commands (`--version`, login status) were replaced by
fixed answers in the tests and, in the installed-package check, the provider
CLIs were removed from `PATH`.

### What changed

`patchrondo` without a command opens a local interface that works on a new
installation; a home holds several projects; settings are edited in the
interface; and the interface reports the real state of processes. The engine
(state machine, adapters, recovery policy, locks) is unchanged except for
per-task overrides, one new event and a start token in the lock file. The
changelog lists the changes; `docs/ARCHITECTURE.md` sections 7, 9 and 10 and
`docs/DESIGN.md` describe them.

Decisions that shaped the result:

- **A project is what the home was.** The engine keeps working on one
  directory; the registry maps a project to its directory. 0.1 homes are used
  in place because Git worktrees record absolute paths. Nothing is migrated,
  so nothing can be lost by a migration, and deleting `projects.json` and
  `settings.json` restores a 0.1 home exactly.
- **No framework and no build step.** About twenty ES modules and three style
  sheets served from the package. A compiled front end would have added a
  toolchain and generated files to review, and made a strict nonce-based
  policy harder to keep, for an interface of this size.
- **Status is not a process.** The page shows what the backend verified by
  process ID and start time, and "unverified" otherwise. A retry plan is shown
  as attended only while a verified process with automatic resume exists.
- **No daemon.** Recovery still needs a live `run` or `resume`. The interface
  starts those processes and reads what they persist; it has no retry logic.
- **No executable path setting.** The CLIs are still found on `PATH`; letting
  the browser name an executable would let it run one.

### Defects found by these checks and fixed

| Found by | Defect | Fix |
|---|---|---|
| Browser check, recovery scenario | The task page kept an old detail when two saves fell in the same second: its panels were refreshed by a signature built on `updated_at`, which has one-second resolution, so a paused task could keep showing "Running" below a correct header. | Panels are refreshed whenever a new detail is fetched. |
| Suite on WSL2 (`/mnt/g`) | The server cached a state file by time and size. Two saves within one timestamp tick with the same size were not seen until a later save. | The inode is part of the signature, and a file modified in the last two seconds is always read again. Regression test with a forced identical signature. |
| Suite on Python 3.11 | `tools/ui_e2e.py` had a backslash inside an f-string expression: a syntax error before Python 3.12. | Moved out of the expression; every source, test and tool parses with the 3.11 grammar. |
| Browser check | Project settings always opened as *Global*: an inner function named `route` shadowed the route argument. | Renamed. |
| Browser check | *Save changes* became active again after a successful save. | The busy state no longer restores the button's previous disabled state. |
| Browser check | Selecting the first tab raised an exception when the route had no tab part. | The route builder accepts a missing part. |
| Browser check | *Copy* in the log viewer left out text scrolled out of view. | Copies the text content of every loaded part. |
| Installed-package check | With output redirected, the link was not printed until exit (block buffering), so a script or a pipe never saw it. | The start messages are flushed; regression test with a buffered stream, which fails without the flush. |
| Unit test | An unexpected exception in a request handler closed the connection without an answer. | Answered with status 500 in the common error shape; the exception type goes to the terminal. |
| Review of the server on Windows | A second `patchrondo` could bind a port already in use, because address reuse is enabled by default and means port sharing on Windows. | Address reuse is disabled on Windows; a taken port falls back to a free one. |

### Checks, all rerun on the final code on October 10, 2026

- **Suite: 248 tests.** Native Windows 11 (10.0.26200) with Python 3.13.3: 243
  passed, 5 skipped, exit status 0. Native Windows with Python 3.11.3: 243
  passed, 5 skipped, exit status 0. WSL2 (Linux
  6.6.87.2-microsoft-standard-WSL2, repository on `/mnt/g`) with Python 3.12.3:
  247 passed, 1 skipped, exit status 0. The skips are the four POSIX-only
  tests that existed before plus the POSIX interrupt test of the interface on
  Windows, and the native Windows refusal test on WSL2.
- **Browser check** (`python tools/ui_e2e.py`), native Windows, Chrome
  154.0.8037.99 headless, Python 3.13.3: 163 checks passed, 0 failed, exit
  status 0, in eight scenarios. Timings of that run: final state on disk to
  page 0.46 s; a later change on disk to page 0.36 s; navigation to a list of
  68 tasks 48 ms; filter click to paint 15 ms; end of a 6 MB log shown in
  0.15 s; longest frame while loading earlier log output 20 ms. The same
  scenarios passed in an earlier complete run on the same day with 0.36 s for
  the first figure.
- **Protections** (`python tools/recovery_checks.py protections`), native
  Windows, Python 3.13.3: 52 of 52 removed safeguards detected, exit
  status 0. Thirteen entries are new: what the interface may claim about a
  process, what it may start, explicit consent for tests, validation before a
  configuration is written, and task overrides.
- **Real clock** (`python3 tools/recovery_checks.py real-clock`), WSL2: 10 of
  10 checks passed, the retry 0.05 s after the planned time, exit status 0.
  Skipped on native Windows.
- **Reliability driver** (`python tools/reliability_e2e.py --repeat 3`): WSL2
  42 passed, 0 failed, 0 skipped; native Windows 30 passed, 0 failed, 12
  skipped; exit status 0 on both.
- **Packages.** `python -m build` into a directory outside the repository
  produced `patchrondo-0.2.0.tar.gz` and `patchrondo-0.2.0-py3-none-any.whl`;
  `twine check` passed both. The wheel contains the 24 files of the interface
  (`index.html`, 3 style sheets, 18 modules, 2 images) and no `AGENTS.md`.
- **Installed package.** The wheel was installed with `--no-index` in a new
  Python 3.13 environment. `patchrondo --version` printed 0.2.0; `patchrondo
  --home <new directory> --port 0 --no-browser` printed its link at once,
  served the page with a nonce policy, the scripts, style sheets and images,
  answered the snapshot with the token and 401 without it, reported both CLIs
  as not installed (none was reachable), and wrote nothing to the home.
- **Publication scanner:** exit status **1**, 91 files checked, four findings:
  `debug.log` (an ignored local log), `docs/assets/rondo-mascot-v1.png`,
  `src/patchrondo/static/rondo.webp` and `rondo-head.webp` (binary assets that
  need manual review). The same four as before this change. Not a clean scan.

One run of the suite on native Windows with Python 3.13 during development
ended with one error whose output was not kept. It was not seen again in five
later complete runs with that interpreter, six runs of the new test modules
alone, or the runs on Python 3.11 and WSL2. Its cause is not known.

### The stated acceptance criteria

| Criterion | Status | Evidence |
|---|---|---|
| `patchrondo` opens the interface with no manual initialization | Met | CLI tests; installed-package check; browser scenario *wizard* |
| A new user can configure everything from the interface | Met for project, roles, workflow, tests, recovery and retrieval. The path of a CLI executable is shown, not editable, by design | Browser scenarios *wizard*, *projects*, *recovery*; settings tests |
| Several projects can be registered and used | Met | Registry tests; browser scenario *projects* |
| Global and per-project settings work | Met | Settings tests; browser scenarios |
| Tasks can be created, started, monitored and resumed | Met with scripted providers | Browser scenarios *run*, *restart*, *recovery* |
| Information updates without reloading the page | Met; measured 0.36 to 0.46 s | Event stream test; browser scenarios *run*, *reconnect* |
| Process state is told apart from persisted state | Met on Linux (WSL2) and Windows; `ps`-based path for macOS not run | `test_runinfo`; restart and stale-lock tests with real child processes |
| Recovery Manager and retrieval are configurable from the interface | Met | Settings tests; browser scenario *recovery* |
| Rondo is in branding, onboarding and empty states | Met with the two official images | Browser screenshots; `docs/DESIGN.md` |
| Coherent, careful, responsive design | Layout checked from 1440 to 480 px in Chrome; visual quality is a judgment, reviewed from screenshots by the author of the change only | Browser scenario *quality* |
| Interactions are fluid and non-blocking | Measured in Chrome on one machine | Timings above |
| Earlier configurations and tasks stay usable | Met | 0.1 home tests with file hashes; browser scenario *legacy* |
| Existing security constraints are preserved | Met for the constraints listed in `SECURITY.md` | Protection tests for every route; static checks of the page |
| Automated tests pass | Met locally on three interpreters and two platforms | Above |
| Documentation reflects the new usage | Updated: README, architecture, design, security, contributing, publishing, changelog | This change |

### Not verified

- **Real providers.** Nothing in 0.2 was run with authenticated CLIs. Run and
  Resume from the interface start the same `patchrondo run` that the 0.1
  real-provider checks exercised, but that path was not repeated, and the
  provider status shown by the interface was only tested with fixed answers.
- **Browsers and platforms.** Chrome on native Windows only. Not Firefox,
  Safari or Edge; not Linux or macOS desktops. The server ran under WSL2 only
  in the unit tests; no browser was pointed at it there. The `ps`-based
  process check for systems without `/proc` (macOS, BSD) was not run locally,
  and CI had not run on this code at the time of writing; the follow-up below
  records its first run.
- **POSIX-only actions in a browser.** *Stop* and *Release lock and resume*
  were tested through the API under WSL2 (a real interrupt of a synthetic
  child; the `--unlock` flag reaching a mocked start), not by clicking them.
  *Release lock and resume* with the real engine was not exercised from the
  interface; the engine's own unlock rule is covered by its existing tests and
  the `crash` scenario of the reliability driver.
- **The polling fallback** of the page (used when the event stream cannot be
  kept open) and the release of the connection by a hidden tab were not
  exercised.
- **Long sessions and scale.** Pages left open for hours, hundreds of tasks,
  many projects, and logs larger than 6 MB were not tried.
- **Assistive technology.** Names, roles, focus order, contrast and reduced
  motion were checked by script. No screen reader was used.
- **Concurrent writers to the registry** from separate operating-system
  processes; the mutex was exercised with threads.

### Limits of 0.2

- Agent output is not streamed: the CLIs' output is captured and saved when a
  step ends, so the live view is the timeline of persisted events and the run
  log, not the agent's words as they are produced.
- Stop and stale-lock release are not available on native Windows.
- There is still no service that keeps a retry plan attended.
- A lock or a waiting process created by 0.1, or by a `run` on a system where
  the process check is not available, is shown as unverified.
- Rondo's states use two images, motion and a mark; dedicated illustrations
  are listed in `docs/DESIGN.md` as work to do.
- The interface is in English only.

### Follow-up: the first CI run and two test defects on macOS (October 10, 2026)

The maintainer committed and pushed the 0.2.0 tree as `c82a345`.
[GitHub CI run 38084131923](https://github.com/luigiggigo/patchrondo/actions/runs/38084131923)
(read with `gh` on October 10, 2026) failed: the four Linux and Windows jobs
passed, and both `macos-latest` jobs (Python 3.11 and 3.13) ended with 245
passed, 2 failed and 1 skipped of 248. The tests that start real child
processes passed there, so that run did exercise the `ps`-based process check.
Both failures were defects of tests added with 0.2, not of the runtime:

| Test | Cause | Fix |
|---|---|---|
| `test_a_waiting_process_blocks_a_second_automatic_run_only` | `patch("patchrondo.app.subprocess.Popen")` replaces `Popen` for the whole `subprocess` module. Without `/proc` the process check starts `ps` through it, so under the patch it received a mock and answered "unknown"; the waiting process counted as unverified and the second run was accepted (202 instead of 409). | The test hands `ps` to the real `Popen` and mocks only the start of the run. |
| `test_a_rewrite_within_the_timestamp_granularity_is_still_seen` | The background scan of the event hub reads through the same cache while the test pins the file signature. A scan between the pin and the next save stored the previous text as settled, and the test then read it back (`'Other' != 'Settled'`). The scan runs every 0.5 s; on the macOS runners the test took about 1.3 s with its setup. | The test holds the scan lock while the signature is pinned. |

The second defect needs a signature that does not follow the file, which only
the test creates: the runtime reads the signature before the file, so a scan
can pair an old signature with newer text (read again on the next scan) but
not a new signature with older text.

Both failures were reproduced under WSL2 before the fix, with the messages of
the CI log: the first with `procinfo._procfs` replaced so that the `ps` path
is taken, the second with `os.fsync` delayed by 0.2 s (three runs of three).
With the fix the same commands passed (one run, and three of three).

Checks rerun after the fix, which changed `tests/test_ui.py` only, on October
10, 2026, with scripted or synthetic providers and no provider call:

- **Suite: 248 tests.** Native Windows with Python 3.13.3 and with Python
  3.11: 243 passed, 5 skipped, exit status 0 on both. WSL2 with Python 3.12.3:
  247 passed, 1 skipped, exit status 0.
- **Protections**, native Windows, Python 3.13.3: 52 of 52 removed
  safeguards detected, exit status 0.
- **Real clock**, WSL2: 10 of 10 checks passed, the retry 0.04 s after the
  planned time, exit status 0.
- **Reliability driver** (`--repeat 3`): WSL2 42 passed, 0 failed, 0 skipped;
  native Windows 30 passed, 0 failed, 12 skipped; exit status 0 on both.
- **Packages.** `python -m build` into a directory outside the repository
  produced `patchrondo-0.2.0.tar.gz` and `patchrondo-0.2.0-py3-none-any.whl`;
  `twine check` passed both.
- **Publication scanner:** exit status **1**, 91 files checked, the same four
  findings as above (`debug.log` and the three binary assets). Not a clean
  scan.

Not verified: the fix on macOS. Its conditions were imitated under WSL2, and
no CI run covers it until it is pushed. The whole suite under the imitation of
a system without `/proc` ended with 246 passed, 1 failed and 1 skipped; the
failure, `test_interface_restart_during_a_run_recovers_the_real_state`, is a
limit of the imitation, which does not reach the real child that test starts,
so parent and child disagree on the process token. That test passed in both
macOS jobs of the run above.

## Remaining limitations

The remaining limitations are explicit:

1. Real Claude/Codex accounts were checked in two local runs on one platform and one pair of CLI versions, including passing fixture tests and approval in both pairings (see the runs above). The fixture is trivial; larger projects and reviewer-feedback iterations remain unvalidated with real accounts. The initial review environment did not have Claude installed. `doctor` checks availability/login and does not guarantee flag compatibility for every release; `tools/provider_e2e.py` does not cover resumption after an interruption.
2. Target-project tests execute on the host. Codex can run commands allowed by its sandbox and load personal configuration. Untrusted code requires a dedicated environment.
3. Capture bounds RAM use but not disk space until timeout. Use disk quotas in isolated runners.
4. Native Windows terminates only direct children; private ACLs and stale-lock recovery do not have POSIX guarantees. WSL2 remains recommended.
5. Fingerprints exclude ignored files, submodule contents and external dependencies/services. They do not eliminate every concurrent-edit race. Avoid other writers during a task and do not run multiple `--unlock` operations simultaneously.
6. There was no original Git history to inspect. The review covers the supplied source, not other copies or external repositories.
7. The 0.2 interface was checked in one browser on one platform with scripted providers; see the section above for what was not verified. It is a single-user local tool: whoever holds its session token can start runs with the user's accounts.
8. Automatic quota recovery is opt-in and needs its foreground process to stay alive; there is no service that resumes a saved plan. Usage limits are recognized from CLI text and may be misclassified, with the retry limit as the bound. Reset times are used only in explicit forms, validated with simulated output. Locks are not recovered automatically, and native Windows cannot check recorded PIDs.

Source publication does not establish production readiness or authenticated-CLI
compatibility. [PUBLISHING.md](PUBLISHING.md) describes validation and publication
of future updates and versioned releases.
