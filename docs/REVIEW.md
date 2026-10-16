# Technical review and validation record

Initial review: October 8, 2026. Local follow-up: October 9, 2026. Real-provider
checks and the automatic quota recovery change: October 10, 2026. PatchRondo
0.2 (interface, several projects, process truth): October 10, 2026. Live
activity and publication-status update: October 11, 2026. Environment check,
repairs and one-press start (0.2.1): October 11, 2026. Activity journal reader
correction and agent settings: October 12, 2026. Test consent before the
review, and the capability and acceptance engine: October 13, 2026. Refused
writes answered on Windows and publication-status update: October 14, 2026.
The review settled by the kind of an issue, with an outcome for each
criterion: October 14, 2026, validated on October 14 and 15, 2026. A change
to the code only for what the code can satisfy, and publication-status
update: October 16, 2026.
The initial scope covered the Python sources, tests, examples, documentation
and metadata supplied before source publication. No `AGENTS.md` or existing Git
repository was present in those original directories. A local, ignored
`AGENTS.md` was added during publication preparation. The source is now public.
Each section below says which code it covers: a published commit or a local
working tree at its date.

## Current source and CI status (October 16, 2026)

The source on `main` is 0.2.1, at
[`b9e5283`](https://github.com/luigiggigo/patchrondo/commit/b9e528324f7e38f2e5776ad8d76c54d419e12a85),
pushed on October 16, 2026 (22:33 +02:00): 0.2.0 with the interface, live
activity and the streaming failure correction, the environment check, repairs
and one-press start of 0.2.1 (commit
[`624f60e`](https://github.com/luigiggigo/patchrondo/commit/624f60e77ce4122473af472931371d2faf15167b)),
the correction of the activity journal reader (`a529273`), the agent settings
(`77cdc14`), the test consent before the review (`b7347fe`), the capability
and acceptance engine (`303a8d3`), refused writes answered on Windows
(`8fe1f47`), the documentation structure (`b376702`) and the review settled
by kind (`b9e5283`). The remote commit (`git ls-remote`), the empty tag list
and the empty GitHub Releases list were checked on October 16, 2026 (exit
status 0). There is no tagged release.

Checked with `gh run list` and `gh run view` on October 16, 2026:

- [CI run 38963145556](https://github.com/luigiggigo/patchrondo/actions/runs/38963145556)
  for `b9e5283` completed **successfully** in all six jobs (Python 3.11 and
  3.13 on Ubuntu, Windows and macOS).
- [CI run 38635937887](https://github.com/luigiggigo/patchrondo/actions/runs/38635937887)
  for `b376702` completed **successfully** in all six jobs.
- [CI run 38635373547](https://github.com/luigiggigo/patchrondo/actions/runs/38635373547)
  for `8fe1f47` was **cancelled**: its two Ubuntu jobs passed, and its four
  Windows and macOS jobs were cancelled when the push of `b376702`, four
  minutes later, superseded the run. `b376702` changes documentation only, so
  its run covers the code of `8fe1f47` on all three systems.

Checked on October 14, 2026:

- [CI run 38630375033](https://github.com/luigiggigo/patchrondo/actions/runs/38630375033)
  for `303a8d3` **failed**. Five of its six jobs passed. The job for Python
  3.13 on `windows-latest` ran 577 tests with one error and 11 skipped, and
  its package check did not run. The error and its correction are described
  under [Refused writes answered on Windows](#refused-writes-answered-on-windows-october-14-2026).
- [CI run 38454139603](https://github.com/luigiggigo/patchrondo/actions/runs/38454139603)
  for `b7347fe` **failed** without a failing test: the four Ubuntu and
  Windows jobs passed, and both macOS jobs ended after 15 minutes because no
  hosted runner picked them up ("The job was not acquired by Runner of type
  hosted even after multiple attempts"). That commit was not tested on macOS
  in CI.
- [CI run 38311781412](https://github.com/luigiggigo/patchrondo/actions/runs/38311781412)
  for `77cdc14` completed **successfully** in all six jobs.

[CI run 38292863463](https://github.com/luigiggigo/patchrondo/actions/runs/38292863463)
for `a529273` completed **successfully** in all six jobs (Python 3.11 and
3.13 on Ubuntu, Windows and macOS).
[CI run 38180731756](https://github.com/luigiggigo/patchrondo/actions/runs/38180731756)
for `624f60e` had **failed**. Five of its six jobs passed (Python 3.11 and
3.13 on Windows and macOS, Python 3.11 on Ubuntu). The job for Python 3.13 on
`ubuntu-latest` ran 443 tests with one error and 18 skipped, and its package
check did not run. The error and its correction are described under
[Activity journal reader](#activity-journal-reader-october-12-2026).
[Run 38170281475](https://github.com/luigiggigo/patchrondo/actions/runs/38170281475)
for the preceding commit `6937d9f` completed successfully, and so did
[run 38164218154](https://github.com/luigiggigo/patchrondo/actions/runs/38164218154)
for `c9f6e1e`, which an earlier version of this section recorded as still in
progress. CI does not run the browser checks, the real-sandbox tests or
authenticated providers.

The dated sections below retain the scope and results of the checks made at
each stage. Earlier references to local validation describe those checks,
not the current publication status. The live-activity addition recorded 344
tests; later corrections have their own validation records below.

The documentation-status refresh on October 11, 2026 changed five tracked
Markdown files and local, ignored `AGENTS.md`. On native Windows, six files
passed UTF-8, LF, no-BOM and final-newline checks; all 36 local links and
anchors resolved; the changelog has one 0.2.0 entry matching package metadata
and no pending-release section. The documentation checks and `git diff
--check` exited with status 0. Runtime, tests and tools were unchanged; the
validation scope for this refresh was documentation only.

## A change to the code only for what the code can satisfy (October 16, 2026)

This section covers changes to `acceptance.py`, `report.py` and
`static/js/views/task.js`, with their tests, the synthetic reviewer of
`tools/ui_e2e.py` and three new mutation protections, developed on top of
`b9e5283`. The package version is unchanged, 0.2.1.

**The defect.** A review of `b9e5283` found that the reviewer could still send
the developer criteria it cannot satisfy. `settle_review` counted a
`CODE_CHANGES_REQUIRED` outcome for any criterion, also one the plan gives to
the user, such as "Verificare la mod dentro Mod Organizer 2" (classified as
integration with an external application, verified by the user). An approval
with that outcome became a request for changes, and a request for changes
whose only reason it was stayed one: only an outcome that hands a criterion
over allowed the settlement. `feedback_parts` then showed the entry to the
developer as its work, while the same criterion was listed under "Not work for
the developer". Each iteration asked again for the same thing, until
`max_iterations_reached`.

**Changes.** `CODE_CHANGES_REQUIRED` now counts only for a criterion a change
to the files can satisfy (`Acceptance.for_reviewer`, a criterion that needs
`edit`, also after a review handed it over). For any other it is kept with
the criterion as the reviewer's opinion, listed in the settled answer's
`refused_changes`, and changes neither the verdict nor the plan. A request for
changes with nothing else in it is settled as an approval
(`no_code_change_requested`); `feedback_parts` sends the developer such an
outcome only for a criterion of the reviewer. The reviewer's instructions say
which criteria take that outcome, and the task page and `report.md` name the
refused changes. An issue of kind `CODE_CHANGES_REQUIRED` names no criterion
and still sends the code back: that is unchanged.

**Regression tests.** Two were added to `tests/test_acceptance.py` (584 tests
in all):
`test_a_change_to_the_code_counts_only_for_a_criterion_a_change_to_the_files_can_satisfy`
and
`test_a_change_asked_for_a_check_only_a_person_can_do_does_not_go_back_to_the_developer`.
Run against the previous `acceptance.py` and `report.py` on native Windows
with Python 3.13.3, both failed: the engine test found the task `blocked` at
the review after six iterations instead of paused at `validate` after one
developer and one reviewer call (1 failure), and the unit test failed in its
two subtests and in the rest of its body, where the settled answer had no
`refused_changes` (3 errors). In `tools/ui_e2e.py` the synthetic reviewer
can now be given a verdict and outcomes, and the `criteria` scenario has it
answer `CHANGES_REQUESTED` with `CODE_CHANGES_REQUIRED` for its Mod Organizer
2 criterion. Its existing checks (one developer call, one reviewer call, the
pause at Validate) and three new ones cover the defect through the
interface. Run from a copy of the repository with the previous
`acceptance.py` and `report.py`, the task ended blocked and the scenario
failed (10 passed, 1 failed, exit status 1). Each of the three new
protections, and the one whose pattern changed, was also run alone before the
full set and detected.

Validation on October 16, 2026, on the final code. In WSL2, `~/.local/bin`
and `/usr/local/bin` were removed from `PATH`, and `shutil.which` found
neither `claude` nor `codex`:

| Check | Platform and result | Exit status |
|---|---|---|
| Full unittest suite | Native Windows, Python 3.13.3: 584 tests, 573 passed, 11 skipped, 303.700 s | 0 |
| Full unittest suite | Native Windows, Python 3.11.3: 584 tests, 573 passed, 11 skipped, 276.934 s | 0 |
| Full unittest suite | WSL2 kernel 6.6.87.2, Python 3.12.3: 584 tests, 566 passed, 18 skipped, 152.175 s | 0 |
| Protections | WSL2, Python 3.12.3: 153 of 153 removed safeguards detected, 0 missed (three of them new) | 0 |
| Reliability, `--repeat 3` | Native Windows: 30 passed, 0 failed, 12 skipped (interruption and stale-lock scenarios need POSIX) | 0 |
| Reliability, `--repeat 3` | WSL2: 42 passed, 0 failed, 0 skipped | 0 |
| Real-clock recovery | WSL2: 10/10 checks passed; retry 0.12 s after the planned time | 0 |
| Browser, all twelve scenarios | Native Windows, Python 3.13.3, Chrome 155.0.8059.82 headless: 353 passed, 0 failed | 0 |
| Build and `twine check` | Native Windows: sdist and wheel of 0.2.1 built outside the repository; both passed | 0 / 0 |
| Publication scanner | 118 files checked; four findings: `debug.log` and the three existing binary mascot assets | 1 |

The scanner is not a clean pass. Every provider was scripted: no model was
called. The opt-in tests in the real Codex sandbox were not run:
`environment.py`, `preflight.py`, `winacl.py`, `repair.py` and
`core._environment` did not change. Whether a real reviewer still marks such
a criterion `CODE_CHANGES_REQUIRED` after the new instruction has not been
checked with an authorized call; when it does, the outcome is recorded and
refused, and nothing goes back to the developer.

## The review settled by kind, with an outcome for each criterion (October 14, 2026)

This section covers changes to `acceptance.py`, `core.py`, `report.py` and
`static/js/views/task.js`, with their tests, the scripted reviewers of the
tests and tools, and eight mutation protections, developed on top of
`b376702`. The package version is unchanged, 0.2.1.

**Two gaps in the acceptance engine.** First, an approval was turned into a
request for changes only by an issue of severity `high` or `critical`: a
`low` or `medium` issue that the reviewer itself had marked
`CODE_CHANGES_REQUIRED` left the code approved, and the schema offered no
kind for a remark that asks for nothing. Second, a criterion the reviewer did
not list was verified by any approval of the code as a whole
(`_Proofs.proves`), for every task: an approval with an empty `criteria`
list, which the schema allows, verified every criterion of the reviewer
without a word about any of them.

**Changes.** `acceptance.requires_change` decides from the kind of an issue
when it has one, whatever its severity, and from `high` or `critical` only
for an issue without a kind. A new kind, `OBSERVATION`, asks for no change,
never blocks an approval and is sent to nobody. For a task whose criteria
were given when it was created (`Acceptance.explicit_outcomes`), an approval
that gives no outcome to a criterion of the reviewer raises
`IncompleteReview` before anything of the answer is applied; the loop turns
it into an `invalid_review` pause at the review checkpoint, and a Resume asks
for the review again. Such a criterion is no longer proved by the approval,
and `refresh` withdraws an approval of the code whose record leaves one out,
so a task that was approved that way before the change has its code reviewed
again, without a new development step. A task read back from its `task.md`
keeps the earlier rule.

**Effect on the existing tests.** With the runtime change alone, the Windows
suite with Python 3.13.3 had 74 failures and 1 error out of 578 tests: the
scripted reviewers approved without `criteria`, which the schema given to the
CLIs requires, and those approvals now pause. The scripted reviewers in
`tests/support.py`, nine test modules, `tools/reliability_e2e.py` and
`tools/ui_e2e.py` now give an outcome to each criterion their prompt lists
when they approve, as a reviewer bound to the schema does. In
`tests/test_acceptance.py`, `test_a_review_without_outcomes_still_covers_the_code_as_a_whole`
was replaced by
`test_an_approval_that_leaves_a_criterion_without_its_outcome_is_asked_for_again`;
the test of a task created before criteria were tracked now has its reviewer
answer without outcomes; the test of an edited ledger now forges a record
that differs from the real one (the real approval already lists both
outcomes); the schema test lists `OBSERVATION`. The pattern of one existing
protection follows the new indentation of `settle_review`'s call.

**Regression tests.** Four were added (582 tests in all):
`test_the_kind_of_an_issue_decides_over_its_severity`,
`test_an_approval_gives_each_criterion_of_the_reviewer_an_outcome_of_its_own`,
`test_only_a_task_read_back_from_its_task_md_lets_an_approval_cover_what_it_left_out`
and
`test_an_approval_saved_without_an_outcome_for_each_criterion_has_the_code_reviewed_again`.
Run with the new tests against the previous `acceptance.py`, `core.py` and
`report.py` on native Windows with Python 3.13.3, the five new and replaced
tests failed: a `low` `CODE_CHANGES_REQUIRED` issue stayed `APPROVED`, an
observation was listed to the developer as somebody else's work, the replaced
engine test found the task done where it expects the pause, and the rest
stopped on the API that did not exist (2 failures and 7 errors over their
subtests). Each of the eight new protections was also run alone before the
full set and detected.

Validation on October 14 and 15, 2026:

| Check | Platform and result | Exit status |
|---|---|---|
| Full unittest suite | Native Windows, Python 3.13.3: 582 tests, 571 passed, 11 skipped, 292.827 s | 0 |
| Full unittest suite | Native Windows, Python 3.11.3: 582 tests, 571 passed, 11 skipped, 263.404 s | 0 |
| Full unittest suite | WSL2 kernel 6.6.87.2, Python 3.12.3: 582 tests, 564 passed, 18 skipped, 161.942 s | 0 |
| Protections | WSL2, Python 3.12.3: 150 of 150 removed safeguards detected, 0 missed (eight of them new) | 0 |
| Reliability, `--repeat 3` | Native Windows: 30 passed, 0 failed, 12 skipped (interruption and stale-lock scenarios need POSIX) | 0 |
| Reliability, `--repeat 3` | WSL2: 42 passed, 0 failed, 0 skipped | 0 |
| Real-clock recovery | WSL2: 10/10 checks passed; retry 0.06 s after the planned time | 0 |
| Browser, all twelve scenarios | Native Windows, Python 3.13.3, Chrome 155.0.8059.79 headless: 350 passed, 0 failed | 0 |
| Build and `twine check` | Native Windows: sdist and wheel of 0.2.1 built outside the repository; both passed | 0 / 0 |
| Publication scanner | 118 files checked; four findings: `debug.log` and the three existing binary mascot assets | 1 |

The scanner is not a clean pass. Every provider was scripted: no model was
called. The opt-in tests in the real Codex sandbox were not run:
`environment.py`, `preflight.py`, `winacl.py`, `repair.py` and
`core._environment` did not change. Whether a real reviewer gives every
criterion an outcome, and how often an approval would now be asked for again,
has not been checked with an authorized call: an incomplete approval costs a
Resume and a new review, never a verified criterion.

## Refused writes answered on Windows (October 14, 2026)

This section covers a correction to `ui.py`, its regression test in
`tests/test_ui.py` and its mutation protection, developed on top of
`303a8d3`. The package version is unchanged, 0.2.1.

**What CI found.** In run 38630375033 for `303a8d3`, the job for Python
3.13.15 on `windows-latest` failed with one error out of 577 tests:
`test_ui.ProtectionTests.test_every_api_route_requires_the_session_token`,
for `POST /api/projects/P-00000000/tasks/T-000000000000/run` with the token
`wrong`. Reading the status line of the answer raised `ConnectionAbortedError:
[WinError 10053]`. The other five jobs ran the same test and passed, as did
the native Windows runs recorded for the acceptance engine below.

**Cause.** `http.client` writes the headers of a request and its body with
two separate calls. The test server runs in a thread of the same process, so
it can read the headers, refuse them with 401 and close the connection before
the client writes the body. That body then meets a closed socket, the server's
system answers with a reset, and Windows discards the answer the client has
received but not read. Every write refused on its headers (token, Host,
Origin, content type, size) was exposed; a browser would have shown a network
error instead of the refusal. The server is the one of 0.2.0; the route that
failed is not new.

**Correction.** `DashboardServer.shutdown_request` closes only the sending
side after the answer, then reads and discards what the client still sends,
at most `MAX_BODY` (256 kB) and for at most `LINGER_SECONDS` (two seconds) in
all, until the client closes; then the connection is closed. No check moved
and no refused body is parsed. The handler threads are daemon threads, so the
wait never delays the closing of the dashboard.

**Regression test.**
`test_a_refused_write_is_answered_when_its_body_arrives_after_the_answer`
sends the headers with a wrong token, waits until the answer has arrived and
the server has finished with the request, then sends the body. Against the
previous `ui.py` it failed 5 times out of 5 on native Windows with Python
3.13.3 and the same `ConnectionAbortedError` (WinError 10053). On WSL2 with
Python 3.12.3 the previous server let the answer through but the reset stayed
recorded on the socket: the test's check of `SO_ERROR` failed 5 times out of
5 with 32 (`EPIPE`). With the correction it passed 5 times out of 5 on both.
The first version of the test, which held the body in the server's buffer
while the headers were checked, passed against the previous code and was
replaced. The protection check removes the wait and expects
`test_ui.ProtectionTests` to fail.

Validation after the correction, on October 14, 2026:

| Check | Platform and result | Exit status |
|---|---|---|
| Full unittest suite | Native Windows, Python 3.13.3: 578 tests, 567 passed, 11 skipped, 361.402 s | 0 |
| Full unittest suite | Native Windows, Python 3.11.3: 578 tests, 567 passed, 11 skipped, 329.756 s | 0 |
| Full unittest suite | WSL2 kernel 6.6.87.2, Python 3.12.3: 578 tests, 560 passed, 18 skipped, 159.303 s | 0 |
| Protections | WSL2, Python 3.12.3: 142 of 142 removed safeguards detected, 0 missed (one of them new) | 0 |
| Reliability, `--repeat 3` | Native Windows: 30 passed, 0 failed, 12 skipped (interruption and stale-lock scenarios need POSIX) | 0 |
| Reliability, `--repeat 3` | WSL2: 42 passed, 0 failed, 0 skipped | 0 |
| Real-clock recovery | WSL2: 10/10 checks passed; retry 0.06 s after the planned time | 0 |
| Browser, all twelve scenarios | Native Windows, Python 3.13.3, Chrome 155.0.8059.79 headless: 350 passed, 0 failed | 0 |
| Build and `twine check` | Native Windows: sdist and wheel of 0.2.1 built outside the repository; both passed | 0 / 0 |
| Publication scanner | 115 files checked; four findings: `debug.log` and the three existing binary mascot assets | 1 |

The scanner is not a clean pass. Every provider was scripted: no model was
called. The opt-in tests in the real Codex sandbox were not run:
`environment.py`, `preflight.py`, `winacl.py`, `repair.py` and
`core._environment` did not change. Windows Server on GitHub's runners was
not reproduced locally; the failure is a race, so a passing run does not
show its absence. What shows that this cause is removed is the regression
test, which forces the order in which the body arrives.

## Capability and acceptance engine (October 13, 2026)

This section covers three new modules (`acceptance.py`, `capabilities.py`,
`evidence.py`), their use in `core.py`, the criteria routes of the interface
and its *Criteria and checks* section, developed on top of `b7347fe` and
validated on the computers named in the table below. The package version is
unchanged, 0.2.1. [Architecture, section 14](ARCHITECTURE.md#14-acceptance-criteria-capabilities-and-evidence)
describes the design.

**What was wrong.** A task's criteria were lines of text, the reviewer had one
verdict for everything, and every `CHANGES_REQUESTED` went back to the
developer. A release task of another project had used six iterations that
way: its developer had file tools only and was asked each time for downloads,
a C++ build, real inference, archives, a pull request and a check inside
another program, while its code was correct and its tests passed. Measured
with scripted providers on `b7347fe`: a task with the criteria "feature.txt
contains good" and "Verified inside Mod Organizer 2", passing tests and a
reviewer that answers `CHANGES_REQUESTED` with no issue, saying only that
nobody verified it in that program, made **six developer and six reviewer
calls**, wrote 38 events and ended `blocked` with `max_iterations_reached`.

**What changed.** The same task now makes **one developer and one reviewer
call**: the backend settles the answer as an approval of the code (it named no
change to the code), the first criterion is verified by the review, the second
waits for a check by the user, and the task pauses at `validate` with
`awaiting_validation` after 9 events. Two further resumes return the saved
state. Attaching the evidence of that check and resuming completes the task
with no further call.

**What was kept.** For a task whose criteria are about the code and its tests,
the provider calls, the checkpoints and the history are those of `b7347fe`:
the 514 tests that existed before pass with no expectation changed. Two were
added to, both in `tests/test_ui.py`: the new routes in the token test and the
sample task with criteria in the demo test. In the browser tool two
expectations follow the page: six workflow steps, and *Criteria and checks* as
the tab after *Overview*. The reviewer's schema gained two required fields;
`parse_review` still accepts an answer without them, which every scripted
provider of the existing tests and tools gives. The test consent, the
environment check, the frozen agent settings and quota recovery are untouched:
the 115 earlier removed protections are all still detected.

**Decisions that are not obvious.**

- A request for changes with no issue about the code and no criterion marked
  as needing a change to the code, while every criterion of the developer is
  satisfied or handed over, is settled as an approval of the code. It is read
  from the structured answer only. `BLOCKED` is never settled into anything
  else.
- An outcome of the reviewer may move a criterion from "the reviewer reads the
  code" to "the user checks it", and back when a later review judges the code
  itself. It never verifies a criterion that needs a command, a decision or a
  person.
- A task that returns from `validate` to `test` with unchanged files is not
  reviewed again. If the commands then fail, the approval is withdrawn.
- A saved state without criteria gets them from `task.md` with nothing
  verified, and a review that was about to start waits for the tests to run
  again: saved test results were never evidence.
- An authorization runs nothing. It records that the user takes an operation
  on themselves.

**Regression tests.** `tests/test_acceptance.py` (55 tests) and
`tests/test_criteria_ui.py` (8 tests) cover classification, capability
detection, the ledger, the settling of a review and ten engine scenarios: a
Python task verified by review and tests; a missing compiler; a developer
without a shell while PatchRondo runs the tests; a check only a person can
do, with three resumes that change nothing; approved code with packaging
missing; a restart in another process; evidence invalidated by a changed file
and by a changed artifact; a denied authorization; tasks created before
criteria were tracked; and a review that names a real defect together with an
outside check. Twenty-six removed protections were added, 141 in all, and each
is detected: among them a criterion verified without its evidence, a
statement accepted alone, the reviewer verifying what is not its to verify,
evidence about other files still counting, an edited ledger trusted, approved
code completing a task with open criteria, and a run started by an action when
it would call an agent.

**Defects found by these checks and fixed before the final round.**

- The workflow bar, with a sixth step, overflowed its card at 1024 pixels: the
  browser's layout scenario failed. Below 1080 pixels each label now sits
  under its node.
- A first version of the API tests replaced `subprocess.Popen` to observe a
  run start, which also broke the Git calls of the action under test. The
  tests now use a child process that leaves a mark, as the environment tests
  do.
- The message of the pause said that every open criterion needed an
  authorization when only one did.

Validation on October 13, 2026, after the last change to runtime code, tests
and tools:

| Check | Platform and result | Exit status |
|---|---|---|
| Full unittest suite | Native Windows, Python 3.13.3: 577 tests, 566 passed, 11 skipped, 377.708 s | 0 |
| Full unittest suite | Native Windows, Python 3.11.3: 577 tests, 566 passed, 11 skipped, 355.767 s | 0 |
| Full unittest suite | WSL2 kernel 6.6.87.2, Python 3.12.3: 577 tests, 559 passed, 18 skipped, 154.664 s | 0 |
| Protections | WSL2, Python 3.12.3: 141 of 141 removed safeguards detected, 0 missed (26 of them new) | 0 |
| Reliability, `--repeat 3` | Native Windows: 30 passed, 0 failed, 12 skipped (interruption and stale-lock scenarios need POSIX) | 0 |
| Reliability, `--repeat 3` | WSL2: 42 passed, 0 failed, 0 skipped | 0 |
| Real-clock recovery | WSL2: 10/10 checks passed; retry 0.05 s after the planned time | 0 |
| Browser, all twelve scenarios | Native Windows, Python 3.13.3, Chrome 154.0.8037.99 headless: 350 passed, 0 failed (33 in the new `criteria` scenario) | 0 |
| Build and `twine check` | Native Windows: sdist and wheel of 0.2.1 built outside the repository; both passed; the three new modules and `criteria.js` are in both | 0 / 0 |
| Publication scanner | 115 files checked; four findings: `debug.log` and the three existing binary mascot assets | 1 |

The scanner is not a clean pass. Every provider was scripted: **no model was
called** and no plan quota was used. No real CLI was started in this round.
The opt-in tests of the reviewer preflight in the real Codex sandbox were not
run: `environment.py`, `preflight.py`, `winacl.py`, `repair.py` and
`core._environment` did not change.

**Not verified.**

- The review answer with `issues[].kind` and `criteria` has been produced by
  scripted providers only. Whether Claude Code 2.1.295 and codex-cli 0.161.0
  accept the larger schema with `--json-schema` and `--output-schema`, and how
  well a real reviewer follows the outcomes, has not been checked with an
  authorized call. `tools/provider_e2e.py` was not run with
  `--authorize-provider-calls`.
- The task that showed the problem was not run again: its criteria were used
  as classification examples, and the loop was reproduced with scripted
  providers.
- macOS and Linux outside WSL2 were not used. The browser checks ran in Chrome
  on native Windows only.

**Limits.**

- PatchRondo runs only the commands a project configured and allowed. A
  criterion about a build or a package is verified by PatchRondo only when
  such a command is among them; otherwise the user does it and attaches the
  evidence. Downloads and publication are always the user's.
- Classification is a list of patterns for English and Italian and can be
  wrong in both directions. A tag corrects it when the task is created; the
  kind of a criterion cannot be changed afterwards.
- A check by the user is a statement with a reference that PatchRondo does not
  open, or a file it hashed. The ledger makes edits visible and does not
  prevent them.
- A task created before this, and paused at a review, runs its tests again
  before that review.

## Test consent before the review (October 13, 2026)

This section covers a change to the state machine in `core.py`, developed on
top of `77cdc14` and validated on the computers named in the table below. The
package version is unchanged, 0.2.1.

**What was wrong.** With tests not enabled, a task was developed, reviewed
once and then paused with `tests_disabled` at the test checkpoint. Each Resume
without the consent repeated the skipped test stage and called the reviewer
again. Measured with scripted providers on `77cdc14`: four runs of one task
made one developer and four reviewer calls and grew the history from 7 to 22
events.

**What changed.** Missing consent is a prerequisite and stops the loop before
the review: `_test_stage` saves the skipped entry, keeps the phase `test` and
raises `tests_disabled`. A run that finds that pause while tests are still not
enabled returns the saved state under the lock, before `run_started`. A saved
`review` checkpoint whose tests were disabled afterwards goes back to `test`
the same way. The same four runs now make one developer call and leave five
events.

**What was kept, and how.** A first version of the change paused before the
reviewer's environment check, so a task whose worktree the reviewer could not
read reported `tests_disabled`: the access problem would have been found only
after the consent. That contradicted
`test_an_access_problem_is_not_hidden_behind_disabled_tests`, and it was
corrected in the engine, not in the test: `_test_stage` runs the reviewer's
check before it raises. The test keeps its first assertions and now also
asserts that the review never starts and that a further Resume inspects
nothing. The other expectations that changed are intended:

- `test_workflow`: `tests_disabled` in place of `stale_tests` when tests are
  disabled at a saved review checkpoint. The test now also asserts that the
  passed results are replaced by a skipped entry and that, with the settings
  restored, the tests run again before the one reviewer call. A changed
  command still gives `stale_tests`.
- `test_recovery`: one provider call in place of two for a `tests_disabled`
  pause, still with no wait and no retry plan.
- `tools/provider_e2e.py` and its tests: without `--run-fixture-tests` the run
  makes one developer call per pairing; the three reviewer checks are `not
  exercised` and counted in the result line, and a review seen in such a run
  fails the check. With the flag the developer and the reviewer are checked as
  before; the tests of a silent CLI and of a reviewer that edits the worktree
  use it.
- Browser `start` scenario: one developer call before the consent, and the
  reviewer called once, after it.

**Regression tests.** The three workflow tests of the change fail against
`core.py` of `77cdc14` (23 tests, 3 failures, exit status 1). Three removed
protections were added, 115 in all: the reviewer called without the consent,
a Resume that starts a run again, and the access problem hidden behind the
consent.

Validation on October 13, 2026, after the last change to runtime code, tests
and tools:

| Check | Platform and result | Exit status |
|---|---|---|
| Full unittest suite | Native Windows, Python 3.13.3: 514 tests, 503 passed, 11 skipped, 307.041 s | 0 |
| Full unittest suite | Native Windows, Python 3.11.3: 514 tests, 503 passed, 11 skipped, 305.422 s | 0 |
| Full unittest suite | WSL2 kernel 6.6.87.2, Python 3.12.3: 514 tests, 496 passed, 18 skipped, 157.370 s | 0 |
| Protections | WSL2, Python 3.12.3: 115 of 115 removed safeguards detected, 0 missed (3 of them new) | 0 |
| Reliability, `--repeat 3` | Native Windows: 30 passed, 0 failed, 12 skipped (interruption and stale-lock scenarios need POSIX) | 0 |
| Reliability, `--repeat 3` | WSL2: 42 passed, 0 failed, 0 skipped | 0 |
| Real-clock recovery | WSL2: 10/10 checks passed; retry 0.05 s after the planned time | 0 |
| Browser, all eleven scenarios | Native Windows, Python 3.13.3, Chrome 154.0.8037.99 headless: 317 passed, 0 failed | 0 |
| Preflight in the real Codex sandbox (opt-in) | Native Windows, codex-cli 0.161.0, Python 3.13.3 and 3.11.3: 5 tests passed on each | 0 / 0 |
| Build and `twine check` | Native Windows: sdist and wheel of 0.2.1 built outside the repository; both passed | 0 / 0 |
| Publication scanner | 109 files checked; four findings: `debug.log` and the three existing binary mascot assets | 1 |

The scanner is not a clean pass. Every provider was scripted: **no model was
called** and no plan quota was used. The only real CLI started was Codex's
sandbox self-check in the opt-in tests, which takes no prompt.

**Not verified.** `tools/provider_e2e.py` was not run with
`--authorize-provider-calls` after this change, so its new output has been
seen with simulated CLIs only, and the two runs of October 10, 2026 recorded
below describe the earlier behavior (one review with the fixture's tests
disabled). macOS and Linux outside WSL2 were not used.

**Limit.** A project with no test commands now gets no review at all until
tests are enabled; before this change it got one review that could not
approve.

## Agent settings (October 12, 2026)

This section covers the agent settings (models and CLI options per role,
three levels, the Agents page), developed on top of `a529273` and validated
on the computers named in the table below. The package version is unchanged,
0.2.1.

### What changed

- `agents.py` (new): the typed table of settings of each CLI, validation, the
  three levels and their resolution, the frozen record of a step, the command
  of a step, presets, model lists and the checks of *Test configuration*.
- `providers.py`: a step is started with the command `agents.command` builds
  from its settings; two failure kinds (`model_unavailable`,
  `unsupported_option`); the probe accepts five more exact model-free
  commands; `OfficialCLI.verify` (the test call).
- `doctor.py`: `discover` (options of the help, Codex model catalog and
  profile files).
- `core.py`: the settings of a step are frozen when it first starts and kept
  across resume, retry and recovery; `refresh_agent_settings`; tasks can be
  created with their own settings.
- `app.py`, `ui.py`: the `/api/agents…` routes; what each role used in the
  task summaries and details.
- `static/`: the Agents page and its styles; model and effort on the task
  page, in Live Activity, in the new-task form; two new error explanations.
- `report.py`, `cli.py`, `recovery.py`, `activity.py`: the settings used in
  the report; `--developer-model` and the three sibling options of `new`;
  where the global settings are for a run; details on an attempt's start.
- Tests: `tests/test_agents.py` (66 tests); the `agents` scenario of
  `tools/ui_e2e.py` (48 checks); 22 protections in `tools/recovery_checks.py`.

### Where the options come from

Read on October 12, 2026, on native Windows 11:

- `claude --help` of Claude Code 2.1.295, and the CLI reference
  (https://code.claude.com/docs/en/cli-reference), which states that `--help`
  does not list every flag. `--max-turns` is in the reference and not in that
  help. Model aliases and effort levels:
  https://code.claude.com/docs/en/model-config.
- `codex --help`, `codex exec --help`, `codex debug --help` and `codex debug
  models --help` of codex-cli 0.161.0, and the configuration reference
  (https://learn.chatgpt.com/docs/config-file/config-reference) for
  `model_reasoning_effort`, `web_search` and the reviewed override keys.

Commands started on the real CLIs during this work, none of which takes a
prompt: `claude --version`, `claude auth status`, `claude --help`, `codex
--version`, `codex login status`, `codex --help`, `codex exec --help`, `codex
debug --help`, `codex features --help`, `codex debug models --help`, `codex
debug models --bundled` and, once through `doctor.discover`, `codex debug
models`. With them `discover` found 71 long options for Claude Code, where
every setting's option is listed except `--max-turns` (reported as
documented), and 35 for Codex, with all settings listed and seven models in
the catalog (the bundled catalog has eleven entries, four of them hidden).

**No model was called.** No provider CLI was started with a prompt, by the
checks or by hand, and no plan quota was used. In particular the test call
(*Test with a real call*) was exercised with scripted answers only.

### Validation on the final code

All of these ran on October 12, 2026 after the last change to runtime code,
tests and tools.

| Check | Platform and result | Exit status |
|---|---|---|
| Full unittest suite | Native Windows, Python 3.13.3: 510 tests, 499 passed, 11 skipped, 289.752 s | 0 |
| Full unittest suite | Native Windows, Python 3.11.3: 510 tests, 499 passed, 11 skipped, 281.826 s | 0 |
| Full unittest suite | WSL2 kernel 6.6.87.2, Python 3.12.3: 510 tests, 492 passed, 18 skipped, 140.114 s | 0 |
| Protections | WSL2, Python 3.12.3: 112 of 112 removed safeguards detected, 0 missed (22 of them new) | 0 |
| Reliability, `--repeat 3` | Native Windows: 30 passed, 0 failed, 12 skipped (interruption and stale-lock scenarios need POSIX) | 0 |
| Reliability, `--repeat 3` | WSL2: 42 passed, 0 failed, 0 skipped | 0 |
| Real-clock recovery | WSL2: 10/10 checks passed; retry 0.06 s after the planned time | 0 |
| Browser, all eleven scenarios | Native Windows, Python 3.13.3, Chrome 154.0.8037.99 headless: 316 passed, 0 failed (48 in the new `agents` scenario) | 0 |
| Preflight in the real Codex sandbox (opt-in) | Native Windows, codex-cli 0.161.0, Python 3.13.3 and 3.11.3: 5 tests passed on each | 0 / 0 |
| Build and `twine check` | Native Windows: sdist and wheel of 0.2.1 built outside the repository; both passed; both contain `agents.py` and `views/agents.js` | 0 / 0 |
| Publication scanner | 109 files checked; four findings: `debug.log` and the three existing binary mascot assets | 1 |

The scanner is not a clean pass: its four findings are the ones recorded for
the previous change, and none is in a new file. The real-sandbox tests were
run because `providers.probe`, which the preflight uses, changed; the
environment check itself did not.

### What the checks establish

- **Compatibility with existing setups.** With no settings, every command is
  compared, argument for argument, with the commands of the previous version
  written out in the test, for both providers, both roles, with and without
  streaming. A task without settings records empty settings and writes no new
  file in its run folder; the 444 tests that existed before pass unchanged
  apart from one renamed test of the probe.
- **The settings are really applied.** Whole tasks run against a scripted CLI
  that records its arguments: each role receives its own model and effort,
  one provider plays both roles with two models, and the task level wins over
  the project and the project over the global one. The browser scenario does
  the same from the page: it chooses a preset and a model on the Agents page,
  overrides the effort for the project, starts a task from the home page and
  finds `--model opus --effort low` in the developer's arguments and the
  reviewer's own model, effort and `read-only` sandbox in the reviewer's.
- **Freezing.** A step that crashed is resumed with the settings it started
  with after the global settings were changed; an automatic retry after a
  scripted usage limit does the same when the settings are edited while the
  run waits; the next iteration takes the settings of its own start; the
  stopped step takes the settings of now only after
  `refresh_agent_settings`, which leaves a history event.
- **Limits.** The reviewer's read-only command is checked against values that
  validation would refuse; the acknowledgement, the blocked options, the
  managed options, short options, blocked configuration keys, model names
  that look like options, credentials and private folders each have a test
  and a protection that removes the check and expects that test to fail.
- **Regression tests fail against the previous behavior.** For every new
  safeguard the protection run does exactly that, on a copy of the sources.

### Defects found by these checks and fixed

- A reviewer could be given an unreviewed Codex configuration key by sending
  the acknowledgement with it. Found by `test_the_reviewer_cannot_be_given_what_writes`
  on its first run; the reviewer now has nothing an acknowledgement opens.
- On the Agents page, a request made for the previous level could arrive
  after the level was changed and be shown under the new one. Found by the
  browser scenario; only the answer to the latest request is used.
- `tools/ui_e2e.py` used a backslash inside an f-string expression, which
  Python 3.11 does not parse. Found by the unit suite on Python 3.11.3, where
  two tests that load the tool failed; corrected, and all sources parse on
  3.11.
- In its first complete run the new scenario failed one of its own checks,
  which counted the provider calls of the scenarios before it. Corrected in
  the scenario.
- The publication scanner reported five lines of the new test file, where a
  key-shaped text was written out to test masking. The text is now assembled
  in the test, as the existing tests do.

One complete browser run, made while the protection run was using eight
processes on the same computer, failed one check of the `legacy` scenario
(the retrieval settings of a 0.1 home did not appear within ten seconds).
That scenario and the code it exercises were not changed; the two later
complete runs made with nothing else running passed all 316 checks. It is
recorded here as an unexplained failure under load, not as fixed.

### Not verified

- Any option against an authenticated CLI. That a CLI's help names an option
  does not show that the option works in a non-interactive call, with which
  values, or with which models. This includes `--effort`, `--fallback-model`,
  `--max-budget-usd`, `--mcp-config`, `-c web_search=…` and `--profile`.
- The wording of both providers when a model is not available to an account,
  and when an option is refused. `model_unavailable` matches general wording;
  `unsupported_option` matches the shapes of the two argument parsers. A
  message that matches neither pauses the task as a plain CLI failure.
- The test call with a real CLI, including whether a Codex test call is
  affected by the access-list problem the environment check handles for task
  worktrees (its folder is a private folder of the home).
- Claude Code and Codex versions other than 2.1.295 and 0.161.0; `discover`
  on Linux, macOS and WSL2 was exercised with scripted answers only.
- The Agents page in a browser other than Chrome on Windows, and with a
  screen reader. The scenario checks accessible names and a 420-pixel
  viewport.

### Limits

- Presets are starting points. *Fast*, *Balanced* and *Max quality* name the
  Claude aliases `haiku`, `sonnet` and `opus` with an effort; for Codex they
  set the effort only, because no Codex model name is written into the
  sources.
- A list of models never says what an account can use; only a test call
  does, for one moment.
- CLI sessions are not continued between steps.
- A profile and the CLIs' own configuration files still apply, as before.
- The acknowledgement records a choice. With `danger-full-access`, a tool
  that runs commands, additional folders or MCP servers, the worktree no
  longer bounds what a developer agent can change.

## Activity journal reader (October 12, 2026)

This section covers a correction to `activity.py`, its regression test and
its mutation protection, made after 0.2.1 was published. It is published in
commit `a529273`, and CI run 38292863463 for that commit passed in all six
jobs.

**What CI found.** In run 38180731756 for `624f60e`, the job for Python
3.13.16 on `ubuntu-latest` failed with one error out of 443 tests:
`test_ui.ActivityTests.test_events_reach_an_open_stream_within_a_second_and_in_order`.
The server thread of the activity stream had ended with `ValueError: read
length must be non-negative or -1` in `activity._forward`, so the test received
no message. The other five jobs ran the same test and passed, as did every
local run recorded above.

**Cause.** That test opens the stream before the journal exists. `activity.read`
measured the file, then read its first line. When the writer created the file
and wrote that line between the two, the reader held a size of zero and a
first line that ends later, and asked for a negative number of bytes. The
reader is part of live activity as published in 0.2.0; the 0.2.1 changes do not
touch it. The window is a few instructions wide, which is why it had not been
met before.

**Correction.** The size is measured after the first line was read and is
never taken as smaller than that line. A journal met in that instant is read
as empty, with a cursor from which the next read continues.

**Regression test.** `test_a_journal_met_while_its_first_line_is_being_written_is_read_as_empty`
gives the reader a stale size of zero for a journal whose first line is
there, with no dependence on timing. Against `activity.py` as published in
`624f60e` it fails with the same `ValueError` as the CI job (34 tests, 1 error,
exit status 1); with the correction the module passes (34 tests, exit status
0). The protection check removes the correction and expects that test to fail.

Validation after the correction, on October 12, 2026:

| Check | Platform and result | Exit status |
|---|---|---|
| Full unittest suite | Native Windows, Python 3.13.3: 444 tests, 433 passed, 11 skipped, 297.848 s | 0 |
| Full unittest suite | Native Windows, Python 3.11.3: 444 tests, 433 passed, 11 skipped, 296.262 s | 0 |
| Full unittest suite | WSL2 kernel 6.6.87.2, Python 3.12.3: 444 tests, 426 passed, 18 skipped, 120.990 s | 0 |
| Protections | WSL2, Python 3.12.3: 90 of 90 removed safeguards detected, 0 missed | 0 |
| Reliability, `--repeat 3` | Native Windows: 30 passed, 0 failed, 12 skipped | 0 |
| Reliability, `--repeat 3` | WSL2: 42 passed, 0 failed, 0 skipped | 0 |
| Real-clock recovery | WSL2: 10/10 checks passed; retry 0.04 s after the planned time | 0 |
| Browser, all ten scenarios | Native Windows, Python 3.13.3, Chrome 154.0.8037.99 headless: 268 passed, 0 failed | 0 |
| Preflight in the real Codex sandbox (opt-in) | Native Windows, codex-cli 0.161.0, Python 3.13.3 and 3.11.3: 5 tests passed on each | 0 / 0 |
| Build and `twine check` | Native Windows: sdist and wheel of 0.2.1 built outside the repository; both passed | 0 / 0 |
| Publication scanner | 106 files checked; four findings: `debug.log` and the three existing binary mascot assets | 1 |

The scanner is not a clean pass. Ubuntu with Python 3.13 was not reproduced
locally: the nearest local platform is WSL2 with Python 3.12.3. The failure is
a race, so a passing run does not show its absence; what shows that this cause
is removed is the regression test, which forces the condition. On GitHub, CI
run 38292863463 for `a529273` passed in all six jobs, the job for Python 3.13
on `ubuntu-latest` included (checked on October 12, 2026).

## Zero configuration and self-healing (October 11, 2026)

This section covers PatchRondo 0.2.1 as validated in the local working tree
before it was published in commit `624f60e`: the
environment check (`environment.py`), the reviewer preflight (`preflight.py`),
the Windows access policy (`winacl.py`), the repairs (`repair.py`), their place
in `core.run_task`, the `/start` and `/repair` routes, the home-page composer,
the *Prepare* step, the guided fix and the test consent on the task page, the
two failure kinds `max_turns` and `git_dubious_ownership`, 97 new tests, 19
new protections and one new browser scenario. When these checks were made
nothing of it was committed or pushed. The work was done on October 11, 2026;
the last validation runs below ended after midnight, on October 12, local
time.

### What was observed before anything was changed

On native Windows 11 Pro (10.0.26200) with codex-cli 0.161.0 and
`[windows] sandbox = "unelevated"`, in throwaway homes outside the repository,
with no model call:

| Observation | Result |
|---|---|
| `mkdir(mode=0o700)` on Python 3.13.3 | protected list of SYSTEM, Administrators and OWNER RIGHTS; no entry for the user |
| the same on Python 3.11.3 | the list inherited from the parent folder |
| Probe in `codex sandbox -P :read-only` on a worktree of the 3.13 home | absolute read denied, PowerShell started in `C:\WINDOWS\System32\WindowsPowerShell\v1.0`, `git -C` "cannot change to …: Permission denied" |
| Read entry for the user on the worktree only | absolute read and `git -C` work; PowerShell still starts in `C:\`, relative read and Git discovery fail |
| Read and execute for the user on `worktrees`, inherited | everything works; write, create and delete refused |
| Less than read and execute on `worktrees` (list and traverse only) | PowerShell starts in `C:\` again |
| Read entry for Users, Authenticated Users or Everyone instead of the user | works: the restricted token is checked against these, not against OWNER RIGHTS |
| Several levels above `worktrees` that nobody but the owner can see | PowerShell starts in `C:\` until each level grants "read attributes" or its parent can be listed |
| A junction inside a repaired worktree | the inherited entry stops at the junction; its target is unchanged |
| The same probe in a plain folder of `%TEMP%` | the canary could be deleted: that folder inherits write entries of Codex sandbox identities |
| PowerShell in that sandbox | constrained language mode: no .NET call is allowed |

The last two rows shaped the tests: sandbox fixtures are built in a folder
whose list is set explicitly, and the probe uses cmdlets only. The existing
home of this computer had been repaired by hand earlier; read without writing,
the policy judges it `granted` and would change nothing there.

### Validation after the final change

Every row was run after the last change to runtime code, tests or tools that
it depends on. The last such change was to the test fixture
(`tests/support.py`, see the regression below); the suites, the protections,
the real-sandbox tests, the scanner and the build were run again after it. The
reliability, real-clock and browser checks do not use the files under `tests/`
and are recorded from the run on the final runtime and tool code.

| Check | Platform and result | Exit status |
|---|---|---|
| Full unittest suite | Native Windows, Python 3.13.3: 443 tests, 432 passed, 11 skipped, 298.799 s | 0 |
| Full unittest suite | Native Windows, Python 3.11.3: 443 tests, 432 passed, 11 skipped, 292.856 s | 0 |
| Full unittest suite | WSL2 kernel 6.6.87.2, Python 3.12.3, Git 2.43.0: 443 tests, 425 passed, 18 skipped, 119.051 s | 0 |
| Preflight in the real Codex sandbox (opt-in) | Native Windows, codex-cli 0.161.0, Python 3.13.3: 5 tests passed, 17.033 s | 0 |
| Preflight in the real Codex sandbox (opt-in) | Native Windows, codex-cli 0.161.0, Python 3.11.3: 5 tests passed, 17.186 s | 0 |
| Protections | WSL2, Python 3.12.3: 89 of 89 removed safeguards detected, 0 missed (70 earlier, 19 new) | 0 |
| Reliability, `--repeat 3` | Native Windows: 30 passed, 0 failed, 12 skipped (interruption and stale-lock scenarios need POSIX) | 0 |
| Reliability, `--repeat 3` | WSL2: 42 passed, 0 failed, 0 skipped | 0 |
| Real-clock recovery | WSL2: 10/10 checks passed; retry 0.04 s after the planned time | 0 |
| Browser, all ten scenarios | Native Windows, Python 3.13.3, Chrome 154.0.8037.99 headless: 268 passed, 0 failed (34 in the new `start` scenario) | 0 |
| Build and `twine check` | Native Windows: `patchrondo-0.2.1.tar.gz` and the wheel built outside the repository; both passed; the wheel contains the four new modules and the interface files | 0 / 0 |
| Publication scanner | 106 files checked; four findings: `debug.log` and the three existing binary mascot assets | 1 |

Documentation, after the last edit: nine Markdown files (eight tracked and the
local, ignored `AGENTS.md`) passed UTF-8, LF, no-BOM and final-newline checks;
all 39 local links and anchors resolved; `git diff --check` exited with
status 0.

The scanner is not a clean pass: its findings are the same four as before and
still require local review before publication. The skipped tests are, on
Windows, the five real-sandbox tests (opt-in) and the six POSIX-only tests
skipped before; on WSL2, the same five, the twelve tests of real Windows
access lists and the one skipped before. The protections were not run on
native Windows in this round.

### What each kind of check establishes

- **Simulated** (the suites, reliability, real-clock, protections, browser):
  providers are scripted; the sandbox, the access lists and the CLI status are
  replaced where a test needs them. They establish the orchestration: a failed
  preflight stops a run before any provider call; a repair that works lets the
  workflow continue and one that fails leaves a resumable pause with one
  guided action; nothing is retried in a loop; an access problem is not
  replaced by `tests_disabled`; a run resumed after a crash is checked before
  its next call; the turn limit and Git's ownership refusal get their own
  kinds; no way of starting a task enables tests.
- **Real access lists, no CLI** (`WindowsAclTests`, on both Windows Pythons):
  the policy on the real lists of temporary folders: one entry added, private
  folders unchanged, idempotence, exact rollback, a customized list kept, a
  deny entry left alone, names with spaces, Unicode and special characters,
  junctions neither repaired nor crossed.
- **Real sandbox, no model** (`tests/test_sandbox_real.py`): the installed
  codex-cli 0.161.0 ran its sandbox self-check. A home as restrictive as the
  observed one is found unreadable, repaired, and then passes all nine probe
  checks, with a second check changing nothing; the same in the layout of a
  multi-project home and with unusual folder names; the task state stays
  unreadable from the sandbox, and a state directory made visible by "read
  attributes" still cannot be listed from it.
- **Not run:** no authenticated provider was called. Whether a real Codex
  review now reads a repaired worktree on this computer follows from the
  self-check of the same CLI and sandbox, not from an observed review.

### A regression found by these runs, and its fix

In the first final round the suite failed once on native Windows with Python
3.11.3 (442 of 443, exit status 1):
`test_a_waiting_process_blocks_a_second_automatic_run_only` found one
unexpected `Popen` call, `git --version`. The new report about the computer
runs that command in the background thread of the agent status, at a moment of
its own, and that test replaces `Popen` for the whole process. The same suite
had passed on Python 3.13.3 minutes earlier: a race, introduced by this
change. The shared fixture of the interface tests now replaces the report as
it already replaced the CLI status, and the test that reads the report through
the server compares it with the fixture; the real report is checked directly.
All suites and the protections were run again afterwards, with the results in
the table.

Two earlier rounds on the same day found and removed: a browser check that
counted a network error logged by the page of the previous scenario's stopped
server, and a first form of the access policy that repaired only `worktrees`.
The real-sandbox tests showed that form to be insufficient for the layout of a
multi-project home, which is where "read attributes" on PatchRondo's own
folders comes from.

### Limits that remain

- The sandbox is probed on native Windows only, with the one `codex sandbox`
  form read from codex-cli 0.161.0. Another CLI version with other options is
  not probed until its form is added; its checks are then shown as skipped.
- Only the reviewer's read-only profile is probed. A Codex developer's
  `workspace-write` sandbox is not.
- The static check of the access lists models what was measured: the user,
  Users, Authenticated Users and Everyone, allow and deny entries. Other entry
  types and group memberships are not interpreted; when the sandbox can be
  probed, its answer decides.
- Lists inside the user's repository are never changed. A repository whose
  `.git` directory the sandbox cannot read is reported as
  `sandbox_git_failed`, not repaired.
- Granting to an existing folder propagates to what is inside it; on a very
  large worktree that takes time and has no timeout.
- The login check trusts the exit status of each CLI's own status command. A
  login it cannot confirm does not stop the run; one it reports as missing
  does.
- Stopping a run from the interface is still unavailable on native Windows.
- Measurements are of one computer, one Windows build and one CLI version.

## Local streaming failure correction (October 11, 2026)

This section covers the correction to `providers.py`, its two regression tests
and its mutation protection, as validated in the local working tree before it
was published in commit `6937d9f`. Existing task logs showed
Claude ending at the configured 12-turn limit with `error_max_turns`. The
transcript also named an unknown `rate_limit_event`, which caused the adapter
to misclassify the failure as quota and schedule unnecessary recovery attempts.
The correction classifies a reported CLI error only from that diagnostic and
stderr, without using activity text as a fallback. No new provider message
form or stream event type is parsed, and no workflow limit is changed.
Existing task state and history are not rewritten by this correction.

Both regression tests failed against the previous implementation (exit status
1 each): three adapter subcases classified activity as quota/authentication,
and one engine assertion detected recovery for the turn-limit failure. With
the correction, the adapter/live-activity/protection-pattern tests passed:
37 tests in 19.510 s, exit status 0. Tests use simulated
providers; inspecting existing logs did not start an authenticated provider.

Validation after the final runtime, test and tool edits:

| Check | Platform and result | Exit status |
|---|---|---|
| Full unittest suite | Native Windows, Python 3.13.3: 346 tests, 340 passed, 6 skipped, 305.783 s | 0 |
| Full unittest suite | Native Windows, Python 3.11.3: 346 tests, 340 passed, 6 skipped, 306.889 s | 0 |
| Full unittest suite | WSL2 kernel 6.6.87.2, Python 3.12.3: 346 tests, 345 passed, 1 skipped, 114.194 s | 0 |
| Reliability, `--repeat 3` | Native Windows: 30 passed, 0 failed, 12 skipped | 0 |
| Reliability, `--repeat 3` | WSL2: 42 passed, 0 failed, 0 skipped | 0 |
| Protections, first attempt | Native Windows: intentionally terminated because of slow execution; no complete result claimed | -1 |
| Protections, complete run | WSL2, Python 3.12.3: 70/70 removed safeguards detected, 0 missed | 0 |
| Real-clock recovery | WSL2: 10/10 checks passed; retry 0.04 s after the planned time | 0 |
| Browser, all nine scenarios | Native Windows, Python 3.13.3, Chrome 154.0.8037.99 headless: 234 passed, 0 failed | 0 |
| Build and `twine check` | Native Windows: sdist and wheel built outside the repository; both packages passed | 0 / 0 |
| Publication scanner | 98 files checked; four findings: `debug.log` and the three existing binary mascot assets | 1 |

The scanner is not a clean pass; its findings still require local review before
publication. No push, publication or real-provider validation was performed.

## Assessment

PatchRondo's source is public on
[GitHub](https://github.com/luigiggigo/patchrondo) since October 8, 2026,
now including 0.2.0 and live activity on `main`.
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
  files passed UTF-8, LF and diff whitespace checks. At that check the
  restored workflow had not yet been pushed or run on GitHub, and the change
  had not been tested locally on Linux or macOS. Subsequent CI results are
  recorded below and in the current status above.
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

The checks above were recorded locally before this follow-up was pushed.
It is now included in the published source; see the current status above.

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

Scope: local checks of the 0.2.0 working tree on one development machine,
before its source publication. Subsequent commits and CI results are recorded
below and in the current status above. No provider call was made and no plan
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

At this local check, the fix had not been verified on macOS. Its conditions
were imitated under WSL2; subsequent CI status is recorded above. The whole
suite under the imitation of a system without `/proc` ended with 246 passed, 1 failed and 1 skipped; the
failure, `test_interface_restart_during_a_run_recovers_the_real_state`, is a
limit of the imitation, which does not reach the real child that test starts,
so parent and child disagree on the process token. That test passed in both
macOS jobs of the run above.

### Follow-up: the command line ignored the selected project in a 0.1 home (October 11, 2026)

Found by review of `Workspace.resolve`: without `--project`, a configuration
at the root of the home was returned before the registry or the selection was
read. In a home created by 0.1 and then opened in the interface, adding a
second project selects it, yet `patchrondo new` kept creating the task, its
branch and its worktree in the 0.1 repository. The existing tests covered a
0.1 home with no registry and a 0.2 home with a selection, not the two
together.

| Case, without `--project` | Before | Now |
|---|---|---|
| Root configuration, no `projects.json` | Root, no registry written | Unchanged |
| Registry with the root project only | Root | Unchanged (the only project) |
| Registry with several projects, one selected | Root | The selected project |
| Registry with several projects, none selected | Root | Error that names `--project`; `list` shows every project, labelled |
| Root project removed from the registry, one other project | Root | The registered project |
| Root project removed, no project registered | Root | Unchanged |
| Root configuration, unreadable `projects.json` | Root, registry not read | The registry error is reported |

A task ID still finds its project in every case, including a root project
that was removed from the registry (`Workspace.find_task`). The change is in
`workspace.py` and one condition of `cli.py`; the interface does not call
`resolve`. Three tests were added to `tests/test_workspace.py`, and all three
failed against the previous code before the change (the task named
"Selected" was created in the 0.1 home).

The fourth row is a deliberate change of behavior for 0.1 scripts: a home
with two projects and no selection no longer falls back to the root. A clear
error was preferred to a task created in a repository the user did not name.

The checks of this change are listed with those of the next one, which was
made on the same day: they were all rerun on the final code.

### Follow-up: a run could end on native Windows while the interface read its files (October 11, 2026)

Found by the browser check while the change above was verified. Its first run
that day ended with exit status **1**, 161 passed and 2 failed, both in the
*run* scenario: "a start while running is refused" was answered 202, and the
timeline had a second start in it. The run log of that task shows why: the
first run ended with `[WinError 5]` (access denied) when
`storage.atomic_text` replaced `state.json` after development, so no run was
active and the later start was accepted and resumed the task. The previous
`state.json` stayed in place, with status `running` and no process behind it.

Windows refuses to replace or delete a file while another process has it open
without delete sharing, which is how Python opens a file to read it. The
interface reads `state.json` of a task again whenever it changes, and
`.run.lock` when it works out what is attached to a task. Measured on native
Windows before the fix:

- One process reading the file in a loop, another saving it: 1595 of 2000
  saves failed with `PermissionError` (Python 3.13.3). Under WSL2: 0 of 2000.
- With a file open for reading in the same process, `os.replace` onto it
  failed with WinError 5 and `unlink` with WinError 32 (Python 3.13.3 and
  3.11.3). The second is what `TaskLock` does on exit, so a run could also
  leave its lock behind, on the platform where a stale lock cannot be
  released from the interface. That case was not observed in a run.

`storage.py`, `app.py` and `live.py` were not part of the change above, so
the defect came with 0.2.0, when the interface started to read these files
during a run. It may also be the unexplained error recorded above for one
Windows run on October 10; that cannot be confirmed, because its output was
not kept.

**Fix.** On Windows, `atomic_text` and the release of `TaskLock` repeat a
refused operation for up to two seconds (`SHARING_RETRY_SECONDS`), then report
the error; a refused save still leaves the previous file in place. Other
systems do not wait. Readers were already written to skip a file that is
being replaced, and were not changed. `tests/test_storage.py` has seven tests:
two hold a real file handle open and failed against the previous code on
native Windows with the two errors above; five use a stand-in for the refused
call and cover the repetition, its bound and the unchanged file on every
platform. Three entries were added to `MUTATIONS`. After the fix the
reproduction gave 0 of 2000 failed saves on native Windows with Python 3.13.3
and with 3.11.3.

**A defect of the browser check, also fixed.** A later run of
`tools/ui_e2e.py` failed one check, "the timeline lists the persisted events
in order", although that run had completed normally: the check read the
timeline as soon as the header showed *Done*, and the panels follow a detail
that is fetched after the header changes. The tool now waits up to five
seconds for the timeline to have every persisted event before reading it.
This failure appeared once in five complete runs that day and was not
reproduced on purpose; with the wait, the *run* scenario passed eight times of
eight, and the three complete runs below passed.

Checks rerun on the final code of both changes on October 11, 2026, with
scripted or synthetic providers and no provider call:

- **Suite: 258 tests.** Native Windows 11 (10.0.26200) with Python 3.13.3 and
  with Python 3.11.3: 253 passed, 5 skipped, exit status 0 on both. WSL2 with
  Python 3.12.3: 257 passed, 1 skipped, exit status 0.
- **Reliability driver** (`--repeat 3`): WSL2 42 passed, 0 failed, 0 skipped;
  native Windows 30 passed, 0 failed, 12 skipped; exit status 0 on both.
- **Protections**, native Windows, Python 3.13.3: 55 of 55 removed
  safeguards detected, exit status 0. The three new entries were also run
  under WSL2, where only the stand-in tests can notice them: 3 of 3 detected.
- **Real clock**, WSL2: 10 of 10 checks passed, the retry 0.03 s after the
  planned time, exit status 0.
- **Browser check** (`python tools/ui_e2e.py`), native Windows, Chrome
  154.0.8037.99 headless, Python 3.13.3, three complete runs: 163 passed, 0
  failed, exit status 0 each time. Final state on disk to page: 0.36, 0.41 and
  0.41 s.
- **Packages.** `python -m build` into a directory outside the repository
  produced `patchrondo-0.2.0.tar.gz` and `patchrondo-0.2.0-py3-none-any.whl`;
  `twine check` passed both.
- **Publication scanner:** exit status **1**, 92 files checked (one more: the
  new test module), the same four findings as above (`debug.log` and the
  three binary assets). Not a clean scan.

Complete runs of the browser check on October 11, in order: 161 of 163 (the
defect above, before its fix); 163 of 163; after the fix, 163, 163 and 162 of
163 (the defect of the check); after the wait was added, the three runs
listed.

Not verified:

- At this local check, neither change had been checked on macOS or in CI;
  both are now in the published source (see the current status above).
  [run 38086345062](https://github.com/luigiggigo/patchrondo/actions/runs/38086345062)
  (read with `gh` on October 11, 2026, concluded successfully) covers
  `4e9cc6c`, the commit before them.
- A reader that keeps a file open for longer than two seconds, such as a
  scanner or a backup tool: the save then fails as before, after the wait.
- The same refusal for other files a run deletes while the interface may read
  them. The attached marker is removed on a best-effort basis by design; the
  agent process files were not examined.

### Follow-up: two simultaneous requests could register one repository twice (October 11, 2026)

Found by review of `Workspace.add` and `Workspace.import_home`: both looked
for a repository or a state directory that was already registered before
taking the registry mutex, and the function that then ran under the mutex
appended its entry without looking again. A second request that made its
check before the first one saved passed it too. Each `add` also created its
own state directory, so one repository ended up as several projects with
separate tasks and worktrees.

**Fix.** The check is one function, `Workspace._refuse_registered`, called
twice: before the mutex, as before, and again as the first step under it, on
the registry that was just read. The second call does not go through
`Workspace.projects`, which can take the same mutex to register a 0.1 root
configuration; the first call has already done that. A request refused under
the mutex has created nothing, so there is nothing to roll back. Messages and
their order are unchanged. `_repositories`, which nothing else used, was
removed.

`tests/test_workspace.py` has five new tests. Four slip a competing
registration in between the check and the mutex (add and add, import and
import, and the two mixed orders through the shared repository); the fifth
registers one repository from six threads. All five failed against the
previous code on native Windows with Python 3.13.3: "ValueError not raised"
four times, and six projects for six threads. With the fix they passed 25
runs of 25 on native Windows and 25 of 25 under WSL2. Two entries were added
to `MUTATIONS`, one for each repeated check.

Separate operating-system processes, which the 0.2 section lists as not
verified, were tried with a script that is not part of the repository: six
processes started on a shared instant, each calling `Workspace.add` for the
same repository.

| Code | Platform | Rounds | Rounds with more than one entry | Per round |
|---|---|---|---|---|
| Previous (`8d79f7d`) | Native Windows, Python 3.13.3 | 5 | 5 | 6 entries, 6 state directories |
| Previous (`8d79f7d`) | WSL2, Python 3.12.3 | 5 | 5 | 6 entries, 6 state directories |
| Fixed | Native Windows, Python 3.13.3 | 10 | 0 | 1 accepted, 5 refused, 1 state directory |
| Fixed | WSL2, Python 3.12.3 | 10 | 0 | 1 accepted, 5 refused, 1 state directory |

Checks rerun on the final code on October 11, 2026, with scripted or
synthetic providers and no provider call:

- **Suite: 263 tests.** Native Windows 11 (10.0.26200) with Python 3.13.3 and
  with Python 3.11.3: 258 passed, 5 skipped, exit status 0 on both. WSL2 with
  Python 3.12.3: 262 passed, 1 skipped, exit status 0.
- **Reliability driver** (`--repeat 3`): WSL2 42 passed, 0 failed, 0 skipped;
  native Windows 30 passed, 0 failed, 12 skipped; exit status 0 on both.
- **Protections**, native Windows, Python 3.13.3: 57 of 57 removed
  safeguards detected, exit status 0.
- **Real clock**, WSL2: 10 of 10 checks passed, the retry 0.03 s after the
  planned time, exit status 0.
- **Browser check** (`python tools/ui_e2e.py`), native Windows, Chrome
  154.0.8037.99 headless, Python 3.13.3, one run: 163 passed, 0 failed, exit
  status 0. `workspace.py` is not one of the interface modules; the check was
  run because it registers one repository twice through the interface.
- **Packages.** `python -m build` into a directory outside the repository
  produced `patchrondo-0.2.0.tar.gz` and `patchrondo-0.2.0-py3-none-any.whl`;
  `twine check` passed both.
- **Publication scanner:** exit status **1**, 92 files checked, the same four
  findings as above (`debug.log` and the three binary assets). Not a clean
  scan.

Not verified:

- At this local check, the change had not been checked on macOS or in CI;
  it is now in the published source (see the current status above).
  [run 38132089056](https://github.com/luigiggigo/patchrondo/actions/runs/38132089056)
  (read with `gh` on October 11, 2026, all six jobs successful) covers
  `8d79f7d`, the commit before it.
- Simultaneous requests through the HTTP interface. The interface calls the
  same two functions, and the tests call them directly.
- Two registrations of one repository under different paths that
  `Path.resolve` does not make equal, such as a second mount of the same
  folder. The comparison is by resolved path, as before.

### Follow-up: a process that could not be verified was treated as gone (October 11, 2026)

Found by review of `runinfo.runtime` and `App.remove_project`. A recorded
process whose existence the host cannot confirm (no start token, access
denied, a failed probe) was correctly not shown as running or waiting, but two
actions then went on as if it were gone:

- With a `scheduled` plan and such a process (`plan_unverified`),
  `waiting_process` was false, so a second run with automatic recovery was
  accepted beside a process that might still be waiting for the same plan.
- `remove_project` refused only `active` tasks. A project could be removed
  from the registry while a recovery process that could not be verified was
  still recorded for one of its tasks, and would then retry out of sight.

The engine was not at risk: the task lock and the check of the saved plan
under it still decide every provider call. The defect was in what the
interface allowed.

**Fix.** `runtime` now returns `unverified`, the process IDs of the recorded
processes it cannot verify, and `waiting_unverified` for `plan_unverified`.
`active` and `waiting_process` are unchanged, so nothing unverified is shown
as running, waiting or attended. `start_run` refuses a run with automatic
recovery when `waiting_unverified` is set, on the same condition as for a
verified waiting process; a run with `auto_resume: false` stays possible.
`remove_project` refuses while any task has an entry in `unverified`. Both
refusals are status 409 and name the process ID and the marker file. A marker
is still deleted only when its process is verified gone; nothing new removes
one. The task page shows *Run now* with the recovery switch off, and a note
with the file to delete once a person has checked, as it already does for a
stale lock on native Windows.

`tests/test_runinfo.py` has two new tests and one extended; `tests/test_ui.py`
has one new test for the run and one for the removal. Against the previous
code on native Windows with Python 3.13.3 the removal test received 200 and
the project was gone; the other new tests stopped at the missing fields.
Three entries were added to `MUTATIONS`, one for each refusal and one for the
field; each removal is detected (below), which is the evidence that the run
test fails without its refusal. `tools/ui_e2e.py` has six new checks in the
*recovery* scenario.

Checks rerun on the final code on October 11, 2026, with scripted or
synthetic providers and no provider call:

- **Suite: 267 tests.** Native Windows 11 (10.0.26200) with Python 3.13.3 and
  with Python 3.11.3: 262 passed, 5 skipped, exit status 0 on both. WSL2 with
  Python 3.12.3: 266 passed, 1 skipped, exit status 0.
- **Reliability driver** (`--repeat 3`): WSL2 42 passed, 0 failed, 0 skipped;
  native Windows 30 passed, 0 failed, 12 skipped; exit status 0 on both.
- **Protections**, native Windows, Python 3.13.3: 60 of 60 removed
  safeguards detected, exit status 0.
- **Real clock**, WSL2: 10 of 10 checks passed, the retry 0.04 s after the
  planned time, exit status 0.
- **Browser check** (`python tools/ui_e2e.py`), native Windows, Chrome
  154.0.8037.99 headless, Python 3.13.3, one run: 169 passed, 0 failed, exit
  status 0.
- **Packages.** `python -m build` into a directory outside the repository
  produced `patchrondo-0.2.0.tar.gz` and `patchrondo-0.2.0-py3-none-any.whl`;
  `twine check` passed both.
- **Publication scanner:** exit status **1**, 92 files checked, the same four
  findings as above (`debug.log` and the three binary assets). Not a clean
  scan.

Not verified:

- At this local check, the change had not been checked on macOS or in CI;
  it is now in the published source (see the current status above).
  [run 38134941472](https://github.com/luigiggigo/patchrondo/actions/runs/38134941472)
  (read with `gh` on October 11, 2026, completed successfully) covers
  `115d151`, the commit before it.
- A process that is unverifiable for a real reason. Every test uses a marker
  without a start token for a live process ID. Access denied by the operating
  system and a failing `ps` were not produced.
- The run dialog for this state in a browser. The browser check confirms the
  label, the note, the *Run now* button and both refusals through the API; it
  does not open the dialog.

Limits that remain:

- A marker that can never be verified (a process ID reused by a program this
  user may not inspect) keeps both refusals until a person deletes the file.
  There is no button for it: the interface does not delete a marker on a
  person's word alone.
- An unverifiable process recorded for a task with no retry plan does not
  refuse a start; the task lock decides, as before. It does refuse the removal
  of the project.
- A home used from both native Windows and WSL2 was not examined. Process IDs
  of one are checked against the process table of the other, so a marker
  written by one side can be judged gone by the other.

### Follow-up: a page could connect to a hub whose scan had just stopped (October 11, 2026)

Found by review of `live.Hub._loop`. With no listener and no request for
fifteen seconds the scanning thread ends. It made that decision in one section
under the hub's lock and cleared `Hub.thread` in a second one. A `subscribe`
that ran between the two was counted too late to keep the thread, and still
found `Hub.thread` set, so it started no other. The thread then ended. The
stream of that page stayed open and received heartbeats, so the page neither
reconnected nor marked itself as disconnected, and showed no change until
something asked for a snapshot, which scans and starts the thread again.

**Fix.** The decision and the withdrawal are one section. A listener is either
counted before the thread decides, and the thread goes on, or arrives after
the thread has withdrawn, and `_ensure_running` starts a new one. Nothing else
in the hub changed.

`tests/test_ui.py` has a new class, `LiveIdleTests`, with two tests on a hub
over a stub application. The first subscribes at the exact point of the
defect, from the scanning thread as it leaves the section in which it found
the hub idle, and then expects a live thread and events without a snapshot
request. Against the previous `live.py` (`45259fd`) on native Windows with
Python 3.13.3 it failed with "a listener was left without a scanning thread".
The second covers the ordinary case, which had no test: the thread ends when
nobody listens and the next listener starts one. One entry was added to
`MUTATIONS`; it restores the two separate sections.

Checks rerun on the final code on October 11, 2026, with scripted or
synthetic providers and no provider call:

- **Suite: 269 tests.** Native Windows 11 (10.0.26200) with Python 3.13.3 and
  with Python 3.11.3: 264 passed, 5 skipped, exit status 0 on both. WSL2 with
  Python 3.12.3: 268 passed, 1 skipped, exit status 0.
- **Reliability driver** (`--repeat 3`): WSL2 42 passed, 0 failed, 0 skipped;
  native Windows 30 passed, 0 failed, 12 skipped; exit status 0 on both.
- **Protections**, native Windows, Python 3.13.3: 61 of 61 removed
  safeguards detected, exit status 0.
- **Real clock**, WSL2: 10 of 10 checks passed, the retry 0.04 s after the
  planned time, exit status 0.
- **Browser check** (`python tools/ui_e2e.py`), native Windows, Chrome
  154.0.8037.99 headless, Python 3.13.3, one run: 169 passed, 0 failed, exit
  status 0.
- **Packages.** `python -m build` into a directory outside the repository
  produced `patchrondo-0.2.0.tar.gz` and `patchrondo-0.2.0-py3-none-any.whl`;
  `twine check` passed both.
- **Publication scanner:** exit status **1**, 92 files checked, the same four
  findings as above (`debug.log` and the three binary assets). Not a clean
  scan.

Not verified:

- The defect in a running server. It needs a subscription in the instant
  between two acquisitions of one lock, after fifteen idle seconds. The page
  asks for a snapshot just before each stream, which renews the time of last
  use, so with the page the thread would also have to be suspended between
  the two sections for that whole exchange. It was produced only by placing
  the subscription there in a test, and was not observed in use.
- At this local check, the change had not been checked on macOS or in CI;
  it is now in the published source (see the current status above).
  [run 38137546425](https://github.com/luigiggigo/patchrondo/actions/runs/38137546425)
  (read with `gh` on October 11, 2026, completed successfully) covers
  `45259fd`, the commit before it.
- The page's side of it in a browser. The browser check opens streams while
  the scan is running and does not wait for it to stop.

### Follow-up: a late answer could be drawn into another log file (October 11, 2026)

Found by review of `logsPanel` in `static/js/views/task.js`. "Load earlier
output" sent a request and, when the answer came, inserted its text into the
view and took its offset as the start of what was shown. It did not check
which file the view showed by then. If another file was chosen while the
request was unanswered, the view of that file received text of the previous
one, and the size line and the "Load earlier output" button described the
wrong file.

The new check for this showed a second defect of the same kind. While the
request that opens a file was unanswered, the viewer's position in that file
was zero. A change to the task noticed in that time started the request for
new output from that position: the whole file was read from its first byte in
200 kB slices and drawn. In the check this drew 6,020,090 characters of a
6 MB log whose last 200 kB should have been shown.

**Fix.** Each opening of a file has a number. Opening, earlier output and new
output share one request at a time, and an answer is used only if its number
is still the current one; an error of a superseded request is not shown
either. A file that grew while its end was being read is brought up to date
when the opening completes. The comparison by file identifier that the
opening and the new output used before is replaced by the number, because it
could not tell a file from the same file opened again.

`tools/ui_e2e.py` has eight new checks in the `quality` scenario. They hold
back one answer in the page, choose another file or change the log on disk,
and then release it. Against the previous `task.js` (`ee34619`) on native
Windows two of them failed: "a late answer for the previous file is not drawn
into the file now shown" and "nothing else is read into the view while a log
is still opening". No entry was added to `MUTATIONS`: the unit tests do not
run the page, so they cannot notice this safeguard being removed.

Checks rerun on the final code on October 11, 2026, with scripted or
synthetic providers and no provider call:

- **Suite: 269 tests.** Native Windows 11 (10.0.26200) with Python 3.13.3 and
  with Python 3.11.3: 264 passed, 5 skipped, exit status 0 on both. WSL2 with
  Python 3.12.3: 268 passed, 1 skipped, exit status 0.
- **Reliability driver** (`--repeat 3`): WSL2 42 passed, 0 failed, 0 skipped;
  native Windows 30 passed, 0 failed, 12 skipped; exit status 0 on both.
- **Protections**, native Windows, Python 3.13.3: 61 of 61 removed
  safeguards detected, exit status 0.
- **Real clock**, WSL2: 10 of 10 checks passed, the retry 0.03 s after the
  planned time, exit status 0.
- **Browser check** (`python tools/ui_e2e.py`), native Windows, Chrome
  154.0.8037.99 headless, Python 3.13.3, one run: 177 passed, 0 failed, exit
  status 0.
- **Packages.** `python -m build` into a directory outside the repository
  produced `patchrondo-0.2.0.tar.gz` and `patchrondo-0.2.0-py3-none-any.whl`;
  `twine check` passed both.
- **Publication scanner:** exit status **1**, 92 files checked, the same four
  findings as above (`debug.log` and the three binary assets). Not a clean
  scan.

Not verified, and one defect left as it was:

- When the request that opens a file fails, the view shows the error and the
  next change to the task reads the file from its first byte, as before this
  change. For a small file that is a retry; for a large one it loads all of
  it. Seen by reading the code, not reproduced, and not changed here.
- The change in Edge. At this local check it had not been checked on macOS
  or in CI; it is now in the published source (see the current status above).
  [run 38143812643](https://github.com/luigiggigo/patchrondo/actions/runs/38143812643)
  (read with `gh` on October 11, 2026, completed successfully) covers
  `ee34619`, the commit before it, and CI does not run the browser check.
- The defect with a slow answer from a real server. Requests to the local
  server are answered in milliseconds, so both defects were produced only by
  holding an answer back in the page.

### Addition: live activity of the agents (October 11, 2026)

Until this change a step was visible when it ended: the adapter waited for the
CLI and saved its output. The task page now shows what the agent's CLI reports
while it works. `docs/ARCHITECTURE.md` section 11 describes the design; this
entry records the decisions that were not obvious, what was checked and what
was not.

**Decisions.**

- *Where the outcome comes from.* Unchanged: Claude's final `result` object,
  or the file Codex writes with `--output-last-message`, then `parse_review`
  and the test gate. The stream is recorded and never read back. A stream with
  events and no result is an `empty_output` failure. A reviewer's message that
  contains a verdict word is recorded as withheld, so the page cannot show
  "APPROVED" before the engine has validated anything.
- *`--include-partial-messages` is not used.* It gives text word by word, and
  with it one line per token, including thinking deltas. Complete messages and
  tool calls arrive as each is finished, which is what the view shows.
- *No probe of the CLI.* Asking a CLI for its help before each step would have
  been a second place that starts a provider CLI: the unit tests would have
  started the real `claude` installed on this computer, and the synthetic
  runners would have counted the probe as a provider call. Instead the step is
  started with the streaming options, and a CLI that exits at its own
  arguments naming one of them, before printing an event, is started once more
  with the previous arguments. Both CLIs validate arguments before opening a
  session, so the refused start reaches no model. `PATCHRONDO_LIVE_ACTIVITY=0`
  selects the previous arguments from the start.
- *One journal file per task, attempts as a field.* The cursor is then one byte
  offset, valid across reconnections and server restarts. Appending under the
  task lock keeps a single writer.
- *What is not recorded.* Internal reasoning, in the journal and in the step's
  transcript. The text a successful Claude tool returned (file content more
  often than not): its size only. Activity is not put into the snapshot.

**Evidence for the formats.** The options were read on October 11, 2026 from
`claude --help` of Claude Code 2.1.295 (native Windows) and 2.1.291 (WSL2) and
from `codex exec --help` of codex-cli 0.160.1 (WSL2); these commands make no
model call. The Claude line types are those of
https://code.claude.com/docs/en/headless, read the same day. The Codex event
and item types are those of `codex-rs/exec/src/exec_events.rs` at
`rust-v0.160.1`. No real provider call was made: the user had not authorized
one.

**Defects found by the checks while this was written, and fixed.**

| Found by | Defect | Fix |
|---|---|---|
| The existing suite (111 errors on the first run) | `Journal.emit` named its first parameter `kind`, and the end of a failed attempt passed a field of the same name: every failing step raised `TypeError` from the code that must never raise, and left the journal file open. | The event type is positional-only, and the attempt records its end and closes the journal in a `finally`. |
| A new journal test | An event longer than a journal line was reduced to its identity at once. | Collections are dropped first; the text only if the line is still too long. |
| The publication scanner | A made-up key block in the redaction test was reported as a possible private key. | The test puts the marker together from two parts; the scanner is back to its four earlier findings. |
| Screenshots of the new scenario | The mascot in the header of the view was drawn at the natural size of the image, and expandable blocks showed two disclosure marks. | Styles corrected. |
| Runs of the new scenario | Two of its checks compared a count taken before an event with one taken after it. | The counts are taken once the page is known to be disconnected; the list is read with `textContent`, because rows scrolled out of view are skipped by layout. |

**Checks, all rerun on the final code on October 11, 2026**, with scripted or
synthetic providers and no provider call:

- **Suite: 344 tests** (269 before; new: `test_activity`, `test_live_activity`,
  eleven process tests, seven HTTP tests, one test of the provider check).
  Native Windows 11 (10.0.26200) with Python 3.13.3 and with Python 3.11.3:
  338 passed, 6 skipped, exit status 0 on both. WSL2 (kernel 6.6.87.2) with
  Python 3.12.3: 343 passed, 1 skipped, exit status 0.
- **Reliability driver** (`--repeat 3`): WSL2 42 passed, 0 failed, 0 skipped;
  native Windows 30 passed, 0 failed, 12 skipped; exit status 0 on both. Its
  synthetic CLIs ignore the streaming options and print one result, so these
  runs also exercise a CLI that streams nothing.
- **Protections**, native Windows, Python 3.13.3: 69 of 69 removed safeguards
  detected, exit status 0. Eight are new: a reviewer's progress recorded as a
  verdict; Codex reasoning and Claude thinking kept in the transcript; the
  stream taken as the answer when the final result is missing; a step started
  again after the CLI had begun to work; a journal that cannot be written
  failing the step; a failing observer disturbing its process; a cursor inside
  a line accepted.
- **Real clock**, WSL2: 10 of 10 checks passed, the retry 0.04 s after the
  planned time, exit status 0.
- **Browser check** (`python tools/ui_e2e.py`), native Windows, Chrome
  154.0.8037.99 headless, Python 3.13.3, one run of the nine scenarios:
  234 passed, 0 failed, exit status 0. The new `live` scenario has 56
  checks. In that run a message printed by the scripted CLI reached the page
  in at most 0.21 s (0.205 s measured), on one machine and a local server.
- **Packages.** `python -m build` into a directory outside the repository
  produced `patchrondo-0.2.0.tar.gz` and `patchrondo-0.2.0-py3-none-any.whl`;
  `twine check` passed both.
- **Publication scanner:** exit status **1**, 98 files checked,
  the same four findings as above (`debug.log` and the three binary assets). Not a clean scan.

**The stated acceptance criteria.**

| Criterion | Evidence | Result |
|---|---|---|
| Activity is visible while the process is still running | `test_activity_is_visible_while_the_agent_process_is_still_running` holds a scripted CLI alive and reads its messages from the journal while its PID file exists and the state says `running`; `test_output_is_seen_before_the_process_ends` does the same one level down; the browser scenario reads the first row while the developer process runs | Met with scripted CLIs |
| Events appear within about a second | Process: under 1 s from the child's own timestamp. HTTP stream: under 1 s per event. Page: at most 0.21 s in the run above | Met on this machine |
| The task completes exactly as before | `test_the_task_ends_exactly_as_without_live_activity` compares state, history, tests, handoff, report and saved files with and without live activity; the recovery plan is the same in `test_quota_recovery_plans_the_same_retry_with_live_activity` | Met with scripted CLIs |
| After a restart earlier events are there and new ones arrive once | `test_a_client_that_reconnects_misses_nothing_and_sees_nothing_twice` (two connections, then another server on the same journal); the browser scenario stops the server during a run, restarts it, and later reloads the page | Met |
| A CLI without streaming is reported and a safe fallback is used | `test_a_cli_that_refuses_the_streaming_options_runs_as_before_and_says_so`, `test_a_cli_without_streaming_runs_and_the_journal_says_why`, the last part of the browser scenario | Met for a CLI that refuses the options |
| A reviewer is not APPROVED before final validation | `test_a_reviewer_is_not_approved_before_its_answer_is_validated`, `test_a_reviewer_without_a_final_answer_is_not_approved`, the reviewer part of the browser scenario | Met |

**Not verified.**

- **The real CLIs.** Everything above ran against scripted processes that
  print what the documentation and the source say the CLIs print. Not
  confirmed by a real run: that Claude Code's `result` line carries
  `structured_output` when `--json-schema` is combined with `stream-json` (the
  documentation shows it for `--output-format json`); that Codex in `--json`
  mode reports a usage limit as `error` and `turn.failed` events with the
  wording and exit status the classification expects; how much either CLI
  buffers; how `claude.exe` on native Windows behaves writing to a pipe
  instead of a file. `tools/provider_e2e.py --authorize-provider-calls` now
  fails when a CLI reports no activity, and is the way to check this; until
  then `PATCHRONDO_LIVE_ACTIVITY=0` restores the previous command lines.
- **Releasing the connection of a hidden tab** (after thirty seconds) and
  **the fallback to asking for pages** when a stream cannot be kept open: both
  are in `js/live.js` and neither was exercised in a browser.
- **Other browsers and systems.** Chrome on native Windows only. WSL2 has no
  browser. Not Edge, Firefox, Safari, Linux or macOS.
- **CI.** The change is published in `c9f6e1e`; the current status above
  records its CI run. CI does not run the browser check.

**Limits of this addition.**

- Claude Code's text appears per complete message, not per word.
- `redact` recognizes common shapes of credentials and nothing else. The
  journal holds command lines, file paths and the end of command output:
  private project data.
- One attempt records at most 5000 events; the journal of a task is not
  rotated or trimmed across attempts.
- With a stream, `<role>.stdout.log` is the transcript without reasoning,
  limited to its first and last lines; the complete raw output of the CLI is
  not kept anywhere.
- On native Windows a timeout still ends only the direct child. If a
  descendant keeps the output pipe open, reading stops two seconds after the
  exit and the reader thread stays blocked until that descendant ends.

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
9. Live activity was checked with scripted CLIs only. The streaming options were read from the help of the installed CLIs and the event formats from documentation and source; no authorized real run has confirmed them, including structured output of a Claude reviewer in a stream and the form of Codex's usage-limit errors in `--json` mode. The journal masks only common shapes of credentials and is not trimmed across attempts.

Source publication does not establish production readiness or authenticated-CLI
compatibility. [PUBLISHING.md](PUBLISHING.md) describes validation and publication
of future updates and versioned releases.
