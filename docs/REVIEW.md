# Technical review and validation record

Initial review: October 8, 2026. Local follow-up: October 9, 2026. Real-provider
checks: October 10, 2026.
The initial scope covered the Python sources, tests, examples, documentation
and metadata supplied before source publication. No `AGENTS.md` or existing Git
repository was present in those original directories. A local, ignored
`AGENTS.md` was added during publication preparation. The source is now public;
the dashboard and startup-script follow-up covers the current, unreleased
working tree.

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

## Remaining limitations

The remaining limitations are explicit:

1. Real Claude/Codex accounts were checked in two local runs on one platform and one pair of CLI versions, including passing fixture tests and approval in both pairings (see the runs above). The fixture is trivial; larger projects and reviewer-feedback iterations remain unvalidated with real accounts. The initial review environment did not have Claude installed. `doctor` checks availability/login and does not guarantee flag compatibility for every release; `tools/provider_e2e.py` does not cover resumption after an interruption.
2. Target-project tests execute on the host. Codex can run commands allowed by its sandbox and load personal configuration. Untrusted code requires a dedicated environment.
3. Capture bounds RAM use but not disk space until timeout. Use disk quotas in isolated runners.
4. Native Windows terminates only direct children; private ACLs and stale-lock recovery do not have POSIX guarantees. WSL2 remains recommended.
5. Fingerprints exclude ignored files, submodule contents and external dependencies/services. They do not eliminate every concurrent-edit race. Avoid other writers during a task and do not run multiple `--unlock` operations simultaneously.
6. There was no original Git history to inspect. The review covers the supplied source, not other copies or external repositories.

Source publication does not establish production readiness or authenticated-CLI
compatibility. [PUBLISHING.md](PUBLISHING.md) describes validation and publication
of future updates and versioned releases.
