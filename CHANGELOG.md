# Changelog

## 0.3.0 — Unreleased

Development series for the Task Contract and its related records. Phase 1 is
functionally implemented in the source; this is not a release-readiness claim.
Package metadata remains **0.2.1**. See the [0.3 roadmap](docs/ROADMAP.md)
for planned work, the separate 0.35 proposal and release requirements.

The entries below retain their original dates, technical details, commit
references and validation descriptions. They were previously collected under
0.2.1 because the package version had not changed. Grouping them here describes
the development plan; it does not rename published commits or packages.

### Step 1.1 — Acceptance Task Contract

Persistent, versioned acceptance criteria separate code approval from task
completion. Criteria are judged against intact evidence, with compatibility
for tasks created before criteria were tracked. Source: `303a8d3`, `b9e5283`,
`d4c0ecd`, `d04ec84`, `e7c8243`, `d010809`; see the dated entries below.

#### Saved criteria that cannot be read pause the task, and statuses are shown as the ledger has them — October 17, 2026

- **A saved record of the criteria that this version cannot read is no longer
  replaced.** Since "Criteria records kept when they cannot be read" below,
  such a record, one written with a later schema version included, was kept
  aside in `acceptance.unread-<digest>.json` and then rebuilt from `task.md`,
  and the run went on with the rebuilt record: the reviewer was called again,
  criteria only a later version knew were left out, and what an author set
  beyond the text (`required`, kinds, dependencies), which `task.md` does not
  hold, was lost. The task now pauses with `acceptance_unreadable` before any
  agent, environment check or project command. The record and the ledger stay
  as they were, so nothing is kept aside any more; `report.md` says nothing
  about criteria it cannot read; a Resume while the record still cannot be
  read returns the saved state; and checking, attaching evidence and deciding
  about a criterion are refused for such a task. A task created before
  criteria were tracked, with no record at all, still gets its criteria from
  `task.md`.
- **A null record is not a task from before criteria were tracked.**
  `Acceptance.load` returned nothing for `"acceptance": null`, as for a
  state without the key, so a run rebuilt the criteria from `task.md` and
  lost what only the record held. Only a state without the key is such a
  task now. A null is refused like any record this version cannot read: the
  task pauses with `acceptance_unreadable`, and `report.md` says that the
  saved criteria cannot be read instead of saying nothing about them.
- **Every field of a saved record is checked.** `load` checked the type of a
  criterion's `review`, `authorization`, `classification`, `requires` and
  `toolchains`, not what they held, and fell back to defaults for an
  approval of the code, an outcome or capabilities it could not use. A
  review saved as `{"outcome": []}` was loaded and stopped the run with a
  `TypeError` when the criterion was planned. These fields, and the
  executor, the verifier, `ledger`, `source` and the dates, now need the
  shape this version saves, or the record is refused with the field named
  and nothing replaces it. A field the record lacks still takes its default.
- **What shows criteria shows them as the ledger has them now.** The task
  page, the list of tasks and `patchrondo criteria` showed the statuses as
  last saved, so a `VERIFIED` whose evidence the ledger had since invalidated,
  or no longer held intact, was still shown as verified, and a task as
  release-ready. They now judge a copy of the record in memory against the
  ledger as it is (`acceptance.judged_now`) and write nothing; what was saved
  is kept apart, as `saved_status` for each criterion and `saved` for the task
  in the interface, and "(saved as …)" on the command line. A ledger that no
  longer holds, unchanged, the records the saved record was judged from (one
  removed from its end, or one changed) confirms nothing there
  (`evidence.Ledger.for_state`): the record that is gone may be the
  invalidation the last judgement counted, so none of its records counts, as
  the next run, which starts the ledger again, will find. The file is not
  touched.
- In `tools/ui_e2e.py`, the `criteria` scenario waits until the page knows the
  project it registered before it opens the form, and a press waits for its
  button to be enabled: two races that made the scenario fail now and then.
- Sixteen protections were added to `tools/recovery_checks.py`, and the one
  for the copy kept aside was removed.

#### Criteria records kept when they cannot be read — October 16, 2026

- **A saved record of the criteria that cannot be read is kept, not
  overwritten.** A task's `acceptance` record that this version cannot read,
  such as one written with a later schema version, was replaced at the next
  run or check by one read back from `task.md`, and only a note in the event
  history said so. Before it is replaced, it is now kept aside unchanged, with
  the task, the time and the reason, in `acceptance.unread-<digest>.json` in
  the task folder, and the `acceptance_planned` event names the file. What
  the engine does with the task is unchanged: its criteria are read back from
  `task.md` with nothing taken as verified, as before.
- **The evidence of a saved criterion names records of the ledger.** `load`
  refuses a criterion whose `evidence` holds anything but ledger record IDs,
  a criterion saved as `VERIFIED` that names no record, and a schema
  `version` that is not the integer 1 (`true` was read as 1). The engine never
  writes any of these; a record that has one is handled as unreadable. The
  error names the schema version it found.
- Four protections were added to `tools/recovery_checks.py`.

#### A change to the code only for what the code can satisfy — October 16, 2026

- **A change to the code asked for a criterion that no change to the files
  can satisfy no longer goes back to the developer.** The reviewer could mark
  `CODE_CHANGES_REQUIRED` a criterion the plan gives to the user or to the
  project's commands, such as "Verificare la mod dentro Mod Organizer 2".
  `settle_review` took it as a change to the code: an approval became a
  request for changes, a request for changes was not settled, and
  `feedback.md` listed the criterion as the developer's work, so every
  iteration asked the developer again for what it could not do, until the
  iteration limit blocked the task. That outcome now counts only for a
  criterion a change to the files can satisfy (`Acceptance.for_reviewer`, a
  criterion about the code, also after a review handed it over). For any
  other it is kept with the criterion as the reviewer's opinion, listed in
  `refused_changes`, and decides nothing. A request for changes whose only
  change is of this sort is settled as an approval (`normalized:
  "no_code_change_requested"`), and the criterion waits for the user. An
  issue of kind `CODE_CHANGES_REQUIRED` names no criterion and still keeps the
  code from being approved.
- The reviewer's instructions say which criteria take
  `CODE_CHANGES_REQUIRED`. The task page and `report.md` say when a change
  was asked for a criterion no change to the files can satisfy. Three removed
  protections were added to `tools/recovery_checks.py`, and the pattern of
  one follows the new condition. The review answer had still not been
  confirmed with a call to an authenticated CLI on October 16, 2026.

#### The review settled by kind, and an outcome for each criterion — October 14, 2026

Published on `main` in commit `b9e5283` on October 16, 2026 (22:33 +02:00).

- **The kind of an issue decides, not its severity.** An approval with an
  issue of kind `CODE_CHANGES_REQUIRED` is a request for changes whatever the
  severity of that issue. Before, only a `high` or `critical` one turned an
  approval around, so the code could be approved with a change the reviewer
  itself had marked as required. A new kind, `OBSERVATION`, is a remark about
  the code that asks for no change: it never blocks an approval and is not
  sent to the developer. An issue without a kind, from an answer in the
  earlier shape, keeps the earlier rule. The settled answer keeps the
  reviewer's verdict in `reported_verdict`, with `normalized:
  "code_change_named"`.
- **An outcome for each criterion of the reviewer.** For a task whose
  criteria were given when it was created, an approval that gives no outcome
  to a criterion the reviewer verifies is not a complete review: the task
  pauses at the review with `invalid_review`, nothing of the answer is
  recorded, and Resume asks for the review again. An approval no longer
  verifies a criterion it did not list. An approval saved before this that
  left such a criterion out stops approving the code when the criteria are
  judged again, and the next run asks for a new review without a new
  development step. Tasks created before criteria were tracked, whose
  criteria are read back from their `task.md`, keep the earlier rule: there an
  approval covers what it did not list.
- The task page says when an issue is an observation, and `report.md` names
  the kind of each issue. The scripted reviewers of the tests and tools now
  answer as the schema given to the CLIs requires, with an outcome for each
  criterion. Eight removed protections were added to
  `tools/recovery_checks.py`. The review answer had still not been confirmed
  with a call to an authenticated CLI on October 14, 2026.

#### Capability and acceptance engine — October 13, 2026

Published on `main` in commit `303a8d3` on October 14, 2026 (22:14 +02:00).

A release task had reached its sixth iteration with `CHANGES_REQUESTED` every
time. Its developer had file tools only and was asked, again and again, for
downloads, a C++ build, real inference, archives, a pull request and a check
inside another program, while its code was correct and its tests passed. The
loop had one verdict for everything and one place to send it.

- **Criteria are records.** Every acceptance criterion of a task has an ID
  (`AC-01`, …), a kind, what it needs, who does it, who verifies it, a status
  with its reason, and the evidence that counts for it. They are saved with
  the task state under `acceptance`. Kinds: `CODE`, `TEST`, `BUILD`,
  `INTEGRATION`, `EXTERNAL`, `PUBLISHING`, `MANUAL`. Statuses: `PENDING`,
  `IN_PROGRESS`, `VERIFIED`, `FAILED`, `BLOCKED_CAPABILITY`,
  `AWAITING_AUTHORIZATION`, `AWAITING_MANUAL_VALIDATION`, `UNKNOWN`.
- **Classification by fixed rules, corrected by the author.** The kind is read
  from the text of a criterion, in English and Italian; what cannot be placed
  is `UNKNOWN` with its reason. A leading tag (`[manual]`, `[build]`,
  `[external]`, `[publishing]`, `[integration]`, `[test]`, `[code]`,
  `[optional]`) or the fields of the API decide over the rules. No model
  classifies anything.
- **Capabilities per executor.** `capabilities.py` reports what the developer,
  the reviewer, PatchRondo itself and the user can do, each as observed,
  configured, absent or unknown, from the agent settings, the test consent,
  the environment check and PATH. Nothing is started for it. A developer
  without a shell no longer stops a criterion that PatchRondo can verify with
  the project's own commands.
- **A preflight when a task is created**, and in the *New task* form while the
  criteria are typed: how many are automatic, need a tool, need an
  authorization, need a check by the user or are unclear, and which
  capabilities are missing. It never stops a task from being created.
- **The review is about the code, with an outcome per criterion.** The
  reviewer is given the criteria, the capabilities and the recorded evidence,
  and answers `SATISFIED`, `CODE_CHANGES_REQUIRED`, `VALIDATION_REQUIRED`,
  `CAPABILITY_BLOCKED`, `APPROVAL_REQUIRED`, `MANUAL_ACTION_REQUIRED` or
  `NOT_ASSESSED` for each; an issue has a `kind`. The backend validates the
  answer against the plan. A request for changes that names no change to the
  code asks nothing of the developer and starts no development step; an
  approval that names one is not an approval. What the reviewer says about a
  criterion it cannot verify is recorded and is not evidence. An answer in the
  earlier shape is still a review of the code as a whole.
- **Approved code is not a finished task.** New results beside the five
  statuses: `CODE_APPROVED`, and `AWAITING_VALIDATION`,
  `AWAITING_AUTHORIZATION` or `RELEASE_READY`. A task is `done` only with the
  code approved and every required criterion verified. Otherwise it pauses at
  a new `validate` checkpoint with `awaiting_validation` or
  `awaiting_authorization`. No agent is called there, and resuming while
  nothing changed returns the saved state.
- **Evidence ledger.** `<task>/evidence.jsonl` records each command PatchRondo
  ran (exit code, log and its hash, what it covers), each validated review,
  each check and each decision of the user, with the fingerprint of the files
  it was made on. Records are chained by hash and never edited; evidence about
  files that changed, or about an artifact that changed, is invalidated by a
  later record, and the criterion is open again. A ledger that fails its check
  is put aside and started again.
- **Three actions of the user**, in the interface and on the command line
  (`patchrondo criteria`, `evidence`, `authorize`), none of which calls an
  agent or a command: attach the evidence of a check (a statement alone is
  refused; a file is hashed by PatchRondo), authorize or decline an operation
  (nothing is run either way), and check again. A check that did not pass goes
  to the developer at the next run.
- **Criteria and checks** on the task page: approval of the code and the task
  as a whole shown apart, progress, one row per criterion with who does it,
  who verifies it, why it is open, what it takes and its evidence; *Who can do
  what in this task*; a *Validate* step in the workflow.
- **Tasks created before this** keep working. One that ended is left as it
  is, with nothing said about each criterion. One that runs again gets its
  criteria from its `task.md`, with nothing taken as verified.
- **Limits.** PatchRondo runs only the commands a project configured and
  allowed: it does not build, package, download or publish by itself, and an
  authorization is a recorded decision that gives nobody a permission. The
  review answer with an outcome per criterion had not been confirmed with a
  call to an authenticated CLI on October 13, 2026.
- Twenty-six removed protections were added to `tools/recovery_checks.py`, a
  `criteria` scenario to the browser checks, and a sample task with criteria
  to the demo.

### Step 1.2 — Structured Review Findings

Persistent RF identities link problems across reviews by explicit, checked
references or exact identity. OPEN and PENDING_VERIFICATION do not imply
resolution; no finding becomes RESOLVED without proof. Source: `40c5e5c`,
`bd6536b`.

#### Findings: a criterion the task does not have links nothing, and free text takes no ID — October 17, 2026

- **A `criterion_id` the task does not have is no longer taken for none.**
  An issue naming one (`AC-999` in a task with `AC-01` only) was compared as
  an issue about no criterion, so a review could link it, by its ID or by
  its words, to a finding about none. Such an issue is now linked to
  nothing; a claim is refused ("the issue names a criterion this task does
  not have"), and the finding it becomes keeps the value in
  `unknown_criterion` and is never linked either. A missing, null or empty
  value still names no criterion. A finding saved without a criterion before
  this change may have come from such an issue, since both were saved the
  same way: it is read with `unknown_criterion` `""`, is never linked again,
  and its history stays as it is.
- **Only the `findings` field of the ledgers' records keeps an ID from new
  problems.** Every ID-shaped text of a ledger did, a summary the user typed
  included, so a note quoting `RF-999999` used up the numbers and no new
  finding got an ID afterwards. `findings.named` now reads the `findings`
  field of each record, in the text of each line so that a record cut short
  by a crash or damaged is read up to where its list or the line ends, in
  the active ledger and in each ledger put aside. Inside a JSON string a
  quote is always written `\"`, so text cannot pass for that field.
- `tests/test_findings.py` has two new tests and two extended ones, and nine
  new mutation protections in `tools/recovery_checks.py` remove one
  safeguard each. Findings still decide nothing: the loop decides as before.

#### Findings of a review keep their identity — October 17, 2026

- **Each problem a reviewer reports gets an ID that it keeps across
  iterations.** An issue lived only in the review that reported it, so a
  problem reported again in other words could not be told from a new one.
  Each issue of a validated review is now recorded as a finding
  (`findings.py`), `RF-001`, `RF-002`, …, saved with the task under
  `findings` in `state.json` with the checkpoint of the review, and the
  `review` record of the evidence ledger names the findings of its issues. A
  finding keeps its kind, file and criterion, its last severity and
  description, who it is for, whether they could act on it, whether the task
  needed a correction or a check for it to be done (a change the code needs,
  or a check, a decision or an action for a required criterion that is not
  verified), the iterations of its first and last report and the review
  records that reported it. What it is about follows from the kind of the
  issue and the plan of the task by fixed rules; the reviewer sets none of it.
- **Links are explicit or word for word, and checked.** The reviewer is shown
  the findings with their IDs, and the review schema gives each issue
  `finding_id` and `criterion_id`, empty when there is none. An ID links an
  issue to its finding only with the same kind, file (a line after it aside)
  and criterion, once in a review; an issue without one is linked only when
  exactly one finding has the same identity and the same words. Any other
  issue is a new finding, which keeps the refused claim and its reason.
  Nothing is matched by similarity, and the file and the words are compared
  whole, through the SHA-256 of their normalized text: the copies kept to be
  shown are shortened and never compared, and a finding whose copy may have
  been shortened is never linked through it. A new finding is numbered past
  every ID the task's findings or its ledgers ever named, a ledger put aside
  as damaged included, so no ID is given twice, not even after a crash
  between the ledger and the state.
- **A finding a review leaves out is not resolved.** It stays as it was. A
  review that approves the code makes the developer's findings it did not
  report wait for a verification (`PENDING_VERIFICATION`, naming that
  review); no finding becomes `RESOLVED`, because nothing yet verifies that
  one problem is gone.
- **The loop decides as before.** Findings decide nothing: the verdict, the
  test gate and the criteria do. An answer without the new fields, or with
  values the backend cannot use, is still a review. A saved record of
  findings this version cannot read is never replaced: the review goes on
  without findings, and the history records `findings_not_recorded`. A task
  whose reviews reported no issue gets no record.
- `tests/test_findings.py` covers the rules and the engine scenarios, among
  them an interrupted review checkpoint, a crash after the review record,
  the same crash followed by a ledger found damaged and started again, and a
  scenario whose decisions were recorded with the sources from before
  findings. Twenty-five mutation protections in `tools/recovery_checks.py`
  remove one safeguard each.

### Step 1.3 — Structured Workflow Events

Versioned WE events retain references at checkpoints and across Resume, with
explicit truncation notices. History describes orchestration; activity only
observes output; the evidence ledger determines what counts as proof. Source:
`c27e61f`, `14d7b86`.

#### Workflow events say what they cut — October 17, 2026

- **A list cut short in an event is no longer taken for the whole of it.**
  An event keeps the first 100 entries of a list, and said nothing when it
  cut one: the `review_completed` of a review of 120 problems listed 100
  findings in `finding_ids` with `unrecorded_issues` 0, as if the review had
  recorded only those. The limit stays; an event a detail of which was cut
  now carries `truncated`, the path of each list or object cut with how many
  entries it had (`{"finding_ids": 120, "new_findings": 120}`), and so does a
  `pending_verification` of more than 100 findings. The findings and the
  ledger's `review` record kept all of them before and still do. Events
  saved before stay as they were.
- `tests/test_history.py` has a review of 120 problems and checks the paths
  and lengths of lists and objects cut inside a detail. Three new mutation
  protections remove the notice one way each, and the pattern of one
  existing protection follows the changed line.

#### Workflow events are versioned and identified — October 17, 2026

- **Every event written to a task's history has an identity.** Besides
  `at`, `event`, `iteration` and its details, an event now carries `version`
  (1), `id` (`WE-0001`, `WE-0002`, …), `task_id` and `phase`, set by
  `history.py` from the state and PatchRondo's clock. A new number is past
  every ID of the history and past its length, so an ID that was saved is
  never given again. A detail can no longer set one of these fields: a call
  that tries raises an error and records nothing. Text in an event is cut and
  filtered for credentials, and lists and objects are bounded. The events of
  quota recovery are numbered with those of the engine.
- **A review is tied to its ledger record and to its findings.**
  `review_completed` names the `review` record of the ledger (`evidence`),
  counts the issues, lists the findings they were recorded under, new
  (`new_findings`) or linked to an earlier one (`linked_findings`), counts
  the issues recorded under none, and says how the backend settled the
  verdict when it did (`normalized`). `code_approved`, or `task_completed`
  when the same run completes the task, names the findings the approval made
  wait for a verification (`pending_verification`); none is resolved by it.
  `evidence_invalidated` names its `invalidation` records and
  `evidence_ledger_restarted` the `restart` record of the new ledger.
- **References, not proof.** No event takes words from an agent. An event
  says what PatchRondo did; whether a record it names counts is the ledger's
  to say, and nothing reads the history to decide. Events are saved with the
  checkpoints, as before: those of a checkpoint that was never saved are not
  written afterwards.
- **Older histories are read as they are.** Events without an ID are never
  rewritten; the first new event of such a task is numbered past them.
- **The loop decides as before.** The same events are written in the same
  order, the decisions recorded before findings (`DECIDED` in
  `tests/test_findings.py`) are unchanged, and a Resume with nothing to do
  still adds no event.
- `tests/test_history.py` has 15 tests: the schema, the numbering, the
  bounds, an older history, a review with new and linked findings, findings
  left out, the three idempotent pauses, an interruption, a process that
  ends before its checkpoint, a reference to a record invalidated and then a
  ledger started again, what the activity journal and an event added by hand
  do not verify, and quota recovery. Seven new mutation protections in
  `tools/recovery_checks.py` remove one safeguard each, and the pattern of
  one existing protection follows the changed line.

### Step 1.4 — Phase 1 Integration

Integration coverage checks Acceptance, Findings, History and Evidence Ledger
together, including legacy tasks, crashes and Resume. Reports and the UI show
record links and incomplete events explicitly. Source: `15750e6`; its history
test import error is corrected in `753d957`. See the dated validation record
for the distinction between executed checks and CI failures.

#### The four records checked together — October 17, 2026

- **An entry of the history that is not an event no longer breaks the
  report.** `history.append` numbers past such entries, but `report.md` read
  `at`, `iteration` and `event` of each one without checking: with a `null`
  or a text in `state["history"]` the report was never written, a run ended
  with an error right after saving its checkpoint, and recording a check by
  the user raised after its record was written. The state stayed as saved.
  The report now lists such an entry as one, and a field an event lacks as
  "unknown"; nothing in `state.json` is changed for it.
- **The task page no longer shows what an event does not say.** Tests were
  shown as passed for a `tests_completed` with no statuses, unknown ones, or
  a list of statuses cut at 100 entries; a check by the user whose event had
  no result was shown as passed. Each now says that the event does not tell.
  An event saved with a list cut short says which list and how long it was,
  the timeline says how many saved entries are not events instead of leaving
  them out, and a history that is not a list no longer stops the page.
- **The links between the records can be read.** The history in `report.md`
  gives each event its ID and the verdict, ledger records and findings it
  names, and says when the event lists only part of one. The issues of the
  last review, in the report and on the Review tab, show the finding each was
  recorded under and whether an earlier review reported it.
- **Nothing the loop decides changed.** No engine module was edited. Tasks
  made with the sources from before findings and workflow events (`d4c0ecd`)
  and from before criteria (`624f60e`), at fifteen points between created and
  ended, were resumed with these sources on October 17, 2026: each ended or
  stayed as it was, with its older events unchanged and every review
  traceable.
- `tests/test_coherence.py` has nine tests that rebuild every review of a
  task from its event, ledger record, findings and criteria and check each
  link both ways: a problem corrected over three iterations; approved code
  that waits for a person, a download and a publication without another
  iteration; crashes before and after eight checkpoints (every checkpoint
  with `PATCHRONDO_ALL_CHECKPOINTS=1`); a quota pause; evidence invalidated
  and a ledger started again; the iteration limit; a task as `d4c0ecd` would
  have left it, at three points; a review of 120 problems; and entries that
  are not events. Three new mutation protections remove the new safeguards
  of the report one at a time, and the `criteria` scenario of
  `tools/ui_e2e.py` checks the finding of an issue and an event saved in
  part on the page.

### Separate development — Evidence Engine proposal (0.35)

`753d957` implements the verification runner requested as the proposal's
"Phase 2, step 1"; the entries of November 4, 2026 below implement its
"Phase 2, step 2", provenance and invalidation, and "step 3", the acceptance
gate; the entry of November 5, 2026 implements "step 4", the Verification
Center and evidence bundles. This is separate from the
planned 0.3 Progress & Stagnation Detection phase; it does not mean that
phase or the whole 0.35 proposal is implemented. The existing ledger is
extended, not replaced.

#### Checks PatchRondo runs and records itself — October 17, 2026

- **A result of a check is what PatchRondo observed.** The new verification
  runner (`verification.py`) runs the project's commands after each
  development step, as the test stage did, under the same consent. Each
  result goes into the evidence ledger as soon as its check ends, before the
  next one starts and before any agent is told: `passed`, `failed`, `error`
  (no result could be established) or `blocked` (not started), with the
  exit code, times to the millisecond, the computer it ran on, the state of
  the task's files before and after, the log and the report. Nothing an
  agent writes reaches it, and `evidence.append` now gives a `command` record
  to the runtime only, whatever a caller passes.
- **Checks with a JUnit XML report.** A `verification` section beside `tests`
  configures typed checks (`test`, `build`, `static_analysis`,
  `repository_check`). A check with `"report": "junit"` is read from the
  report its run wrote: exit code 0 without the report, a report that is not
  XML, declares a document type, contradicts its exit code or its own counts,
  or was there before the run is an error, never a pass; zero tests, fewer
  than `min_tests`, or a `required_tests` entry without a pass fail the
  check. A build can name the `artifacts` it must produce. The reports are
  read with `expat`, with no entity expanded and size, depth and test cases
  bounded.
- **A check that gave no result no longer goes to the agents.** A missing
  program, a refused start, an unreadable report or a check that changed the
  task's files pause the task with `verification_blocked` or
  `verification_error` at the test stage, where a Resume runs the checks
  again. Before, a missing program paused with `configuration` and the rest
  reached the reviewer and the developer as failing tests. The criterion it
  was to prove stays `PENDING` with the reason, not `FAILED`; so does one
  whose tests timed out.
- **A check that changes the task's files proves nothing about them.** It was
  recorded as passed on the files it left; it is now an error naming the
  files Git saw change. Keep what tests generate ignored by Git.
- **Logs are masked and kept.** The output of each check is saved with the
  values of PatchRondo's secret-named environment variables and
  credential-shaped text replaced by `[redacted]`. Each run names its files
  (`<run>-test-01.log`), so a run again in the same iteration no longer
  replaces the log an earlier record names.
- **On native Windows a time limit stops what a check started.** Checks run
  in a job object, so a test runner's child processes no longer outlive its
  timeout. Agent CLIs are still stopped alone.
- **Compatibility.** The entries of `tests.commands` run as before, read from
  their exit code, under the names `command-1`, `command-2`. A task saved
  before resumes where it was: its test context stays equal while no
  `verification` section is configured. No event was added and the events of
  the loop are those of before. `PATCHRONDO_VERIFICATION=0` runs the tests as
  the versions before this one did.
- **`tests/test_history.py` imports again.** A stray character committed in
  `15750e6` broke its line 78, and every job of CI run 39051863842 failed on
  it; the line is as it was in `14d7b86`.
- `tests/test_verification.py` has 47 tests: reports (valid, failing,
  zero tests, a partial suite, declared counts, malformed, a document type),
  the configuration, real processes in a repository whose path has spaces
  (timeouts with child processes, a missing program, a refused start, an
  unconfigured or unallowed check, an interruption, a ledger that cannot be
  written, two runs at once, two writers of one ledger, a restart, a changed
  record, no Git, a check that changes the files, a known token, a report
  location through a link or a junction, a report from before the run, build
  artifacts) and the loop (a claim of the developer, a reviewer that names
  evidence, a pause for an error and for a blocked check, an interrupted
  check, a quota pause, changed checks, the switch, a task saved before the
  runner). 22 new mutation protections remove the new safeguards one at a
  time.

#### A check no job object holds says so — October 17, 2026

From a review of the runner: on native Windows, a check that Windows refused
to put in a job object ran without one and nothing said so, so a timeout
could leave what it started running unseen.

- **The result says how the processes were held.** `process.execute` now
  returns `process_tree`: `process_group` (POSIX), `job_object` (native
  Windows, for the project's checks) or `child_only` (native Windows without
  a job object: agent CLIs, or a check that no job object could hold). The
  record of a check keeps it as `check.process_tree`; records written before
  have no such field.
- **A timeout says what may still run.** A check that no job object held
  still runs and is judged as before: no result and no pause changes. Its
  timeout's reason, which reaches the pause, the task page and the record,
  now says that what it started was not stopped and may still be running.
- **The limit of running checks on the host is stated.** SECURITY.md and
  section 16 of the architecture say that a check runs with the user's
  rights, that filtering the environment is not a sandbox, that the test
  consent isolates nothing, and that an untrusted repository needs an
  isolated environment before its checks are allowed to run.
- Four new tests: the value on each platform with and without `tree`, a job
  object refused with and without observers (native Windows), a check whose
  processes nothing held, judged from a scripted result on every platform,
  and the same with a real command on native Windows. The test of a timeout
  with a child process also checks the recorded value. Two new mutation
  protections remove the reason and the recorded value. The checks that
  were run are recorded in
  [the technical review](docs/REVIEW.md#a-check-no-job-object-holds-says-so-october-17-2026).

#### A timeout is not a refused start — October 18, 2026

From the failure of Python 3.11 on `macos-latest` in CI runs 39064594210
(`fb6ecb1`) and 39135998928 (`eb295cc`): a check stopped at its time limit
was recorded as `blocked`, `permission_denied`, as if its program had been
refused at the start. Published in `b962214` (October 18, 2026, 19:24
+02:00); its CI run 39149471702 passed in every job, and on `macos-latest`
(macOS 26.6.2, Python 3.11 and 3.13) the test that asks the real system
passed, which there requires EPERM for a group left with a zombie while the
zombie is held, and ESRCH once it is reaped.

- **Cause.** After a timeout, `process._terminate` ended with one
  `os.killpg(..., SIGKILL)` to the group and caught only
  `ProcessLookupError`. macOS answers that call with EPERM while the only
  members left in the group are zombies not yet reaped (XNU's `killpg1`
  skips zombies and answers EPERM when it finds no other member), which is
  what a check leaves when the child it started dies with it and launchd has
  not reaped it yet. The `PermissionError` left `process.execute`, and
  `verification.Runner` reads a `PermissionError` of `execute` as a refusal
  to start the program. With that answer modelled on Linux and the orphan
  reaped 200 ms after it died, the CI test failed 5 times out of 5 with the
  message of the CI runs, each time from that call; with the orphan reaped
  by Linux's init, it passed 10 times out of 10. Nothing was run on macOS.
  Agent steps and the test stage before checks could get the same
  exception after a timeout.
- **The last signal is settled by the system only.** The final SIGKILL
  (`process._kill_group`) ends when it reaches the group or when the system
  answers ESRCH (no such group is left). EPERM settles nothing: the call is
  repeated for `process.GROUP_SECONDS` (2 s). It is never taken for a
  stopped group, and no other error of the call is caught.
- **A refusal to the end is said, not raised.** A group still refused after
  that is a timeout all the same. `process.Result.kill_refused` says so, the
  record of the check keeps it as `check.kill_refused` (records written
  before have no such field), and the timeout's reason says that what the
  check started may still be running. A timeout is still an error that
  pauses the task at `test`: no result, pause or decision of the loop
  changes. An interruption is raised again, never replaced by the refusal.
- Six new tests, five of them on POSIX: the macOS answer scripted
  (`support.refusing`) for a refusal that ends, with a child that ignores
  TERM and is still stopped, for one that lasts, and for an interruption,
  each with and without observers; a check whose group refuses its last
  signal; the real answer of the system for a group left with a zombie
  (macOS refuses it, Linux signals it); and a program the system refuses to
  start (a folder), on every system. Five new mutation protections. The
  checks that were run are recorded in
  [the technical review](docs/REVIEW.md#a-timeout-is-not-a-refused-start-october-18-2026).

#### A refused stop is said by agent steps and the earlier test stage — October 18, 2026

The entry above made the runner say when the system refused the last signal
to a stopped command's group (`kill_refused`). Agent steps and the test stage
before checks (`PATCHRONDO_VERIFICATION=0`) received the flag and said
nothing, and the pause of a timed-out check said only "Tests timed out".

- **Agent steps.** A step stopped at its time limit whose group the system
  refused adds `process.KILL_REFUSED` to its message, `Timeout after Ns for
  provider/role`, which is saved as `last_error.message` and ends the attempt
  in the activity journal, and to a last line of `<role>.stderr.log`, marked
  `[PatchRondo]` so that it is not read as the CLI's.
- **The test stage before checks.** The stopped command's log ends with the
  same marked line; its journal event has the sentence as text; its saved
  result has it as `message`, with `kill_refused: true`; and the message of
  the pause, `Tests timed out; check the logs before resuming`, adds it.
- **The pause of a timed-out check** adds it too: the runner's result in the
  task state now carries `kill_refused` when it is true.
- Unchanged: the kind of the failure (`timeout`), the checkpoint, the
  events, every decision of the loop, the ledger records of the earlier test
  stage, and everything when the flag is false or a result has no such field
  (`process.stop_refused`). The test call of a model and the probes of
  `doctor` and of the environment check do not report it.
- Three new tests with scripted results, on every system: an agent step
  (Claude and Codex) and a whole task paused by its developer step, and both
  test stages; each with the flag, without it and, except for the whole
  task, with a result that has no such field. Eight new mutation
  protections. The checks that were run are recorded in
  [the technical review](docs/REVIEW.md#a-refused-stop-is-said-by-agent-steps-and-the-earlier-test-stage-october-18-2026).

#### Provenance and invalidation of evidence — November 4, 2026

- **Whether evidence can still be used is judged apart from its result.**
  `provenance.py` gives each record of the ledger a validity: `VALID`,
  `STALE` (true when written, about something that changed since),
  `INVALID` (the record or what it rests on cannot be trusted) or `UNKNOWN`
  (what is needed to judge it is missing), with typed causes. Only `VALID`
  evidence counts. A check that `passed` stays `passed`: no record is
  rewritten, and what stops counting is named by an `invalidation` record
  that now carries its `cause`, its validity, the state of the files before
  and now and the checks to run again (`reverify`). The
  `evidence_invalidated` event names the causes. Nothing is run because of
  it: the next test stage runs the project's checks, under the same consent.
- **A changed or removed check no longer leaves a counting record.** Each
  record of the runner names the digest of its check's configuration
  (`check.config_sha256`). A record of another configuration, or of a check
  no longer configured, is invalidated (`CHECK_CHANGED`, `CHECK_REMOVED`).
  An entry of `tests.commands` recorded before is compared by its command;
  a record of the runner written between October 17 and November 3, 2026,
  whose configuration cannot be read back, is `UNKNOWN` and run again once,
  never taken as valid for compatibility.
- **Checks can declare the ignored files they read and the environment they
  need.** `inputs` lists up to 20 patterns inside the project (a local
  configuration, a model file); they are hashed before and after the check
  without following a link or a junction, only their digest is kept, a
  change makes the result stale, and a check that changes them while it runs
  gives no result (`inputs_modified`). `"same_environment": true` makes a
  result count only where it ran. Both are part of the test context only
  when set, so a change before the review pauses with `stale_tests` before
  the reviewer is called.
- **The fingerprint of a task's files reads only what it may and refuses to
  guess.** Names that are not UTF-8 are hashed as the files they are (they
  were hashed as missing); nothing behind a folder that is a link or a
  junction is read; a repository inside the copy (an initialized submodule,
  a clone) is hashed with its files; a file that keeps changing while it is
  read, or cannot be read, pauses the task with `fingerprint_unavailable`
  instead of giving a digest. For a copy without those, the digest is the
  one earlier versions computed, so saved evidence stays bound to its files.
- **Records this version does not write never count.** A record of another
  schema version, a kind this version does not know, or an executor other
  than the one of its kind is never proof, a decision of the user included,
  even when the hash chain holds it. An artifact replaced by a link is
  changed; one that is gone is told apart from one that changed, and a
  changed copy of a report that PatchRondo kept makes the record `INVALID`.
- **The reviewer and the developer are told what no longer counts.** The
  reviewer's brief and `feedback.md` list, from the ledger, the evidence
  that no longer counts by ID with its causes, and the checks without a
  usable result; the reviewer is told that such evidence is never a reason
  for `SATISFIED`. Each record the interface lists carries `validity` and
  `causes`.
- **Compatibility.** Saved ledgers and tasks are read as they are; no record
  is rewritten or migrated. The identity of a check, its test context and
  the decisions of the loop are unchanged for a configuration without the
  new settings (`DECIDED` in `tests/test_findings.py` unchanged). A Resume
  at `validate` with nothing changed still writes nothing.
  `PATCHRONDO_PROVENANCE=0` keeps the invalidation of earlier versions
  (`evidence.outdated`), the test context and the briefs; the stricter
  reading of records, the fingerprint and the new fields of records stay.
- `tests/test_provenance.py` has 48 tests: the fingerprint (the earlier
  digest kept, uncommitted, new, deleted and renamed files, ignored files, a
  link or a junction, a cloud file, a nested repository, an unreadable file,
  a file that changes while it is read, a name that is not UTF-8, no Git),
  declared inputs (patterns, locations outside the project, a cycle of
  links, size and count limits), each rule of the judgement, the
  invalidation records (one per cause, never twice, a crash between two),
  and the loop with the real runner (a test passed on other files, a
  reviewer citing stale evidence, a changed and a removed check, a changed
  input before and during a check, another environment, a record without
  its configuration, a Resume with nothing changed, a crash before the
  checkpoint, a checkpoint restored from before an invalidation, two tasks
  on one repository, an unreadable file, the switch). 28 new mutation
  protections remove the new safeguards one at a time. The checks that were
  run are recorded in
  [the technical review](docs/REVIEW.md#provenance-and-invalidation-of-evidence-november-4-2026).

#### The acceptance gate decides completion — November 4, 2026

- **A task is completed only on the gate's `PASS`.** `gate.py` decides from
  what the backend recorded whether a task may be completed now: `PASS`,
  `FAIL`, `PENDING`, `BLOCKED`, `NEEDS_REVERIFICATION` or `ERROR`, with a
  next action, one row per criterion and the conditions that keep it from
  passing. `PASS` needs the code approved, every required criterion verified
  by records that still count, and the test results about the files as they
  are. Evidence that proved a required criterion and no longer counts is
  named with its validity and causes (`lapsed`). It calls no model and runs
  nothing. Statuses stay the acceptance record's: each criterion's reason is
  now also a fixed code (`acceptance.Basis`), set where its status is
  decided.
- **The decision is bound to the files and the ledger it was made on.**
  `core._complete`, the one place that writes `done`, refuses without `PASS`,
  fingerprints the files and compares the test context again (changed: back
  to the tests, `stale_tests`), and reads the ledger again (moved:
  `completion_refused`). Before, the files were not compared again between
  the last judgement and `done`.
- **Switches no longer skip what the configured checks need.** With
  `PATCHRONDO_VERIFICATION=0` the checks of the `verification` section did
  not run and `tests.commands` alone could prove the tests; with
  `PATCHRONDO_PROVENANCE=0` the `inputs` and `same_environment` a check
  declares were not compared. Either way a task could be completed. It now
  pauses at `validate` with `completion_refused` (source `gate`), which a
  Resume returns as saved while the switch is still off; put back, the
  checks run before the task completes. A project that configured none of
  these keeps the behavior the switches document.
- **The reviewer cites evidence; the backend checks it, and it proves
  nothing.** Each criterion of a review answer has `evidence_refs` (in the
  schema; an answer without them is read as before). They are looked up in
  the task's own ledger and recorded in the review's record (`references`):
  accepted, or refused as `UNKNOWN_RECORD`, `STALE`, `INVALID`, `UNKNOWN`,
  `NOT_FOR_CRITERION` or `NOT_POSITIVE`. The reviewer's brief lists the
  records that count for each criterion and says that completion is the
  orchestrator's decision.
- **Every decision can be reconstructed.** The last evaluation is saved under
  `gate` in `state.json` (ID `GE-` plus a digest of its inputs), and
  `code_approved`, `task_completed` and a pause at `validate` name it with
  its decision, next action, the criteria it waits for, the snapshot and the
  ledger count. `report.md` has a "Completion gate" section; the user's
  criteria operations return the gate's decision.
- **Nothing else in the loop changes.** Pauses that wait for a person keep
  their kinds and words, no event was added, and `DECIDED` in
  `tests/test_findings.py` holds. Findings are reported by the gate and
  decide nothing. Gate profiles are not implemented; the one policy is
  `standard`.
- **A test of the fingerprint no longer fails on Linux and macOS with Python
  3.13.** `test_a_cloud_file_or_another_reparse_point_is_read_as_a_file`
  built an `os.stat_result` with Windows-only fields, which Python 3.13
  refuses elsewhere (CI run 41901150009 for `abfb476`, `ubuntu-latest` and
  `macos-latest` part 2/2). It uses stand-ins with the fields read, and now
  checks the junction case on every system.
- `tests/test_gate.py` has 27 tests; 15 new mutation protections and one
  whose pattern moved. See
  [architecture section 18](docs/ARCHITECTURE.md#18-acceptance-gate) and
  [the technical review](docs/REVIEW.md#acceptance-gate-november-4-2026).

#### The gate checks its own snapshot — November 4, 2026

- **An approval or a result about other files never passes the gate, whatever
  its caller did.** `gate.evaluate` took the approval of the code and the
  records of verified criteria as they counted in the ledger, and relied on
  `core._sync` to have invalidated those about other files. Given a ledger
  that was not brought up to date, it passed on an approval and test results
  of snapshot A when asked about snapshot B. It now requires every record it
  relies on to name its `snapshot` (the rule of `provenance.assess`):
  otherwise `NEEDS_REVERIFICATION`, `APPROVAL_NO_LONGER_COUNTS` with action
  `REVIEW` for the approval, `EVIDENCE_STALE` (or `EVIDENCE_UNKNOWN` for a
  record that names no fingerprint) under the criterion's `elsewhere`. An
  approval naming a record that is not an approving review is `ERROR`. In
  the loop `core._sync` invalidates such records first, so no decision of a
  run changes; the loop already asked for a new review whenever the files
  changed after an approval, including untracked files, edits under the same
  `HEAD`, a new commit and a crash before the pause.
- **Blocking findings keep a task open without deciding anything.** Audited
  rather than changed: a blocking finding is a change the code needs (the
  approval that names it becomes a request for changes) or a check for a
  required criterion that is not verified, so neither the reviewer's later
  approval nor the developer's claim, nor a status the reviewer writes into
  an issue, completes the task while one is open. A finding left out by an
  approval of the current files is `PENDING_VERIFICATION`, never `RESOLVED`.
  The workflow tests follow these cases through the loop.
- **A blocking finding waiting for a verification completes nothing on an
  approval of other files.** A change the code needs, about no criterion,
  that an approval left out stays `PENDING_VERIFICATION`, its `blocking`
  flag as reported. When the files change, the approval it waits on stops
  counting and nothing completes until a reviewer, shown the finding with
  its status, approves the files as they are: leaving it out again
  completes the task with the finding still waiting; reporting it again
  reopens it and sends the code back. A test follows both ways through the
  loop. The flag itself decides nothing, by policy: nothing sets `RESOLVED`,
  so deciding on it would keep such tasks open forever.
- `tests/test_gate.py` has 34 tests (two rule tests and five workflow tests
  added); `tests/test_coherence.py` also checks that a task that is done, or
  whose saved gate passes, has no unsettled open blocking finding and is
  approved by a review of the files of its test results. Three new mutation
  protections.

#### The Verification Center and evidence bundles — November 5, 2026

The request's "Phase 2, step 4". The Verification Center and the evidence
bundle are placed in this proposal by that request; nothing in the engine's
decisions changed: no acceptance rule, validity rule, event or path to
`done` was added, and the gate stays the one authority for completion.

- **A Verification tab on the task page** (`static/js/views/verification.js`,
  `App.verification_view`, `GET …/tasks/<id>/verification`). Rondo and one
  sentence say where the task stands, beside the saved gate's decision in
  words and its code. The figures keep apart the code review, the required
  criteria verified, the evidence records that count and what needs
  attention. *Why this task is not completed* lists the gate's conditions in
  order of precedence with their codes, the criteria and evidence they name,
  the findings and the evaluation it was decided on. A saved gate older than
  the ledger is shown as *before the latest evidence* with **Evaluate again**
  (no agent), never as a current `PASS`; a task with no saved gate shows *Not
  evaluated*. The timeline marks each saved gate decision as *the gate then*.
- **Only actions the backend carries out**, chosen from the saved gate:
  attach evidence, authorize or decline, complete (only for `COMPLETE` at
  `validate`; a run that calls no agent), resume (through the usual
  confirmation), check again, open settings, export. A disabled action says
  why; none marks a criterion as verified.
- **An evidence explorer** paged and filtered on the server
  (`…/verification/evidence`, 50 rows, at most 200), newest first, showing
  what each record observed beside whether it counts now; a record opens with
  its provenance, the invalidations that name it, its criteria and its log
  (`…/verification/evidence/<record>`).
- **Evidence bundles** (`bundle.py`): a summary bundle (records, decisions,
  references, validity, no logs) or a detailed one (also masked copies of
  the logs and report copies the ledger names, from the task's own `runs`
  folder, no link followed, cut to their end), with a versioned
  `manifest.json` of every file's SHA-256 and size, omissions, limits, a
  content digest and `signature: null`. Built in memory from one coherent
  view of `state.json` and the ledger, read twice: a task that keeps changing
  is refused (HTTP 409) rather than bundled; a gate saved before the ledger
  moved on is named and makes the bundle incomplete. The same state gives the
  same files and bundle ID; only `created_at` differs. Downloaded from the
  page after a preview (`…/verification/bundle?mode=`), or written with
  `patchrondo bundle export TASK [--detailed] [-o FILE]`, which never
  replaces a file.
- **`patchrondo bundle verify FILE [--json]`** checks a bundle without
  extracting it: safe raw names (on Windows `zipfile` turns a backslash into a
  slash, so the stored name is checked), no links, duplicates or encrypted
  entries, size, entry and compression-ratio limits, the format and schema
  version, every hash and size, no unlisted file, the content digest, and that
  the ledger's chain, the validity of each record, the saved gate's own ID and
  the manifest's gate decision and permission to complete agree. Verdicts
  `INTACT` 0, `INCOMPLETE` 1, `MODIFIED` 2, `MALFORMED` 3, `UNSUPPORTED` 4,
  `UNVERIFIABLE` 5. Integrity is not authenticity: nothing is signed.
- `evidence.parse` checks the bytes of a ledger as `evidence.read` does
  (`read` now calls it), so that an export and a check read exactly the bytes
  they hold.
- Tests: `tests/test_bundle.py` (29: the checks of a bundle, the exporter's
  limits and refusals, the read model, and the request's end-to-end scenario
  through the real loop with scripted agents, from a gate that waits for an
  external check to a completed task, bundled before and after) and
  `tests/test_verification_center.py` (6: the routes behind the token and
  Host checks, errors, downloads, a ledger of about 5,000 records read in
  pages). The browser check gains a `verification` scenario, and `quality`
  checks the new tab at every width; its keyboard check now lands on the
  *Verification* tab, which follows *Overview*. Eighteen new mutation
  protections.

#### Step 4 hardening: gate freshness, ledger integrity, consistent exports — November 5, 2026

The user's "0.35 Step 4 Hardening", from a review of `8e548b0`. Three
observations were reproduced on that commit with scripted agents before any
change; no gate rule, acceptance rule, event, pause or path to `done`
changed.

- **A saved gate is current only for the task it was evaluated on.** The
  Verification Center compared the saved gate with the ledger only: after a
  file of the worktree was edited, a `PASS` was still shown as current with
  *Complete the task* (pressing it was safe: the backend evaluates again
  under the lock and `_complete` fingerprints again). `verification_view`
  now fingerprints the worktree and compares it, the configured checks and
  the protections with what the gate saw (`core.gate_currency`, the
  comparisons of `_complete`), and gives `freshness` (what was compared,
  the causes, `checked_at`). `gate_state` gains `unknown` (files or
  configuration that cannot be read, a gate evaluated without the files:
  never shown as current) and `historical` (a task that ended: its gate is
  not compared again); `behind` now also covers changed files and checks.
  *Complete the task* is offered only while the gate is `current`; the page
  says *Not current* or *Not confirmed* with the cause and offers *Evaluate
  again*, and compares again each time the tab is shown. A fingerprint is
  never reused across requests: views at once share one reading only if it
  started after each of them arrived. Evidence pages and live events do not
  fingerprint. The view reads without the task lock, so it fingerprints
  with `gitops.fingerprint(…, reader=True)`: `--no-optional-locks` and
  `core.fsmonitor=false`, as the dashboard's other Git reads, so `git
  status` writes nothing into the copy; the digest is the engine's.
- **A damaged ledger is never an intact bundle.** `verify` compared the
  ledger's problems with the count the manifest declared, so a bundle whose
  manifest named a broken chain consistently was `INTACT`; an honest export
  of a damaged ledger was `INCOMPLETE` only through its gate omission, with
  no reason about the ledger. The ledger is now checked on its own:
  `LEDGER_DAMAGED` (broken chain, unreadable line, cut-short last record)
  and `LEDGER_UNANCHORED` (records the state saw are gone) are `MODIFIED`;
  `NO_GATE` and `GATE_NOT_ABOUT_LEDGER` are `INCOMPLETE` whatever the
  manifest declares. Exit codes are unchanged; `summary.md` says when the
  ledger fails its check and what the gate's snapshot does not cover. An
  export still copies the ledger byte for byte and repairs nothing.
- **An export holds files of one moment.** `task.md` was read once, after
  the coherent reading of the state and the ledger, and included unchecked.
  `consistent` now reads all three twice, and once the bundle is made the
  three are read again: a bundle made while one of them changed is made
  again from a new view, and refused (`Inconsistent`) after four attempts.
  A log is opened without following a link where the system can refuse it,
  and its name must still lead to the same unchanged file after the reading
  (`changed_during_export`); a log that is no longer the one its record
  hashed is named among the omissions.
- **A state being replaced is read again on Windows.** The new test of an
  export beside a writing run found that reading `state.json` at the instant
  `save_json` replaces it raises `PermissionError` on native Windows, which
  failed the export instead of retrying (also before this change). The
  export now asks again for a bounded time (`storage._unshared`); a refusal
  that lasts is still raised.
- Tests: `tests/test_bundle.py` gains 28 tests (`FreshnessTests`, careful
  forgeries of the ledger, honest exports of damaged and unanchored
  ledgers, changes during an export, a writer beside exports, a state being
  replaced, logs replaced, linked or too large to hash, the dashboard's
  reading without a lock); the count of readings in the test of a task
  that keeps changing now follows the three files read. The browser
  `verification` scenario edits a file of the worktree, sees *Not current*
  without *Complete*, restores it and completes through the button. Twelve
  new mutation protections, and the one of a gate older than the ledger
  follows the new line.

#### Bundle structure and untrusted ledger records — November 7, 2026

From an independent review of `1eb8056` (P1, P2). Both findings, and the
cases next to them below, were reproduced on that commit with scripted
agents before any change. No gate rule, acceptance rule, event, pause, exit
code or path to `done` changed, and a bundle exported unchanged keeps its
verdict.

- **A bundle without a file every export writes is never intact (P1).** A
  bundle whose ledger, contract, validity or any other file an export always
  writes was removed from both the archive and the manifest, with every hash
  and the bundle ID recomputed, was `INTACT`; so was one whose gate,
  contract or validity file was replaced by `null` or `[]` (a contract that
  was not an object also skipped the check that the ledger still holds the
  records the state had seen). `verify` now requires the files of
  `bundle.REQUIRED` in the manifest (`REQUIRED_FILE_UNLISTED`, `MODIFIED`;
  one listed and absent stays `FILE_MISSING`, `INCOMPLETE`), checks the
  type and fields of each JSON file (`bundle.SHAPES`, `RECORD_SHAPE`,
  `MODIFIED`), and calls a bundle whose contract holds no criteria it can
  read `INCOMPLETE` (`NO_CRITERIA`) whatever the manifest declares. The
  gate file stays optional (`NO_GATE`).
- **A malformed invalidation is a problem of the ledger, never an exception
  (P2).** An `invalidation` whose `of` was an integer, or a list holding an
  object or a list, raised `TypeError` in `evidence.parse`: in the engine,
  the dashboard and `patchrondo bundle verify` alike. One whose `of` was a
  string, null or missing was read as naming nothing, so the records it
  meant went on counting with no problem reported. Now an `of` that is not a
  non-empty list of text stops the ledger's trust there with a problem: the
  gate is `ERROR`, a run starts the ledger again, and a state that saw the
  record is no longer anchored; the IDs of earlier records it does name
  still stop counting. `append` refuses to write such a record. A well-formed
  `of` keeps its meaning (an unknown ID names nothing, a repeated one counts
  once).
- **One ID, one record.** A later record that took an invalidated record's
  ID made it count again (the invalidation was looked up under the later
  record). A repeated ID is now a problem of the ledger.
- **Other shapes are data.** Fields writers fill with lists (`criteria`,
  `covers`, `artifacts`, `of`) are read through `evidence.listed`; an
  invalidation's `cause` or `reason` that is not text gives the plain cause.
  These raised `TypeError` in `bundle.validity_rows`, `provenance.recorded`
  and `assess`, `evidence.outdated`, `Ledger.last`, the criteria view and the
  evidence page. A JSON value nested more than 64 levels
  (`evidence.MAX_DEPTH`; the writers and an export nest fewer than 10) is
  unreadable in the ledger and in a bundle, where it raised `RecursionError`.
  A log naming its record through a list no longer raises in `verify`.
- Tests: `tests/test_bundle.py` gains 4 (required files removed from the
  archive or from both, records of other shapes and nested too deeply,
  contracts without criteria with a shortened ledger, ledgers of odd records),
  `tests/test_acceptance.py` 5 (`of` of every shape, IDs an invalidation
  names, repeated IDs, records of other shapes, nesting) and
  `tests/test_gate.py` 1 (a malformed invalidation or a repeated ID never
  lets the gate pass); each failed on `1eb8056`. Eight new mutation
  protections; two follow lines that changed.

#### The browser check waits for the run to end — November 7, 2026

The `verification` scenario of `tools/ui_e2e.py` failed in about half of
its runs, at one of four places, with the sources of `fa3e615` and of
`1eb8056` alike. Cause, measured with the run's lock, its process marker,
the hub's events and the page's store side by side: a run saves `paused` or
`done` and leaves about 0.2 s later; until the next scan of the dashboard
(every 0.5 s) sees it gone, the backend rightly reports the run as working
and the Verification tab offers *The run is working* instead of the next
action. The scenario read the actions once, right after the status or the
gate's overview changed, so it could see the run still working (and, when
the check after the pause was too early, find no *Attach…* button and
stop). The timeline comes from the task's detail, a fetch apart from the
gate's, and could be read before it arrived. The page was always behind by
one scan at most and then showed the right state: events are applied in
order with gaps refused, and each of the two fetches is serialized. No
runtime file changed.

- The three checks wait, up to 15 s, for what they check: the first action
  after the pause, the only action after completion, and the timeline with
  *Task completed*; a timeout prints what the page showed. `Env.expect`
  takes that optional expression. Two deliberate regressions (the backend
  offering *The run is working* for a completed task, a timeline that no
  longer follows the history) each fail their check.

### The system's folder dialog for Browse — November 7, 2026

- **Browse… opens the folder dialog of the operating system** (user request
  of November 7, 2026), wherever the repository field is: *Add project*,
  *Choose folder…* on Home and the first-run wizard. Before, it opened a
  list of folders drawn in the page, which a browser's own dialog cannot
  replace: it never gives a page the path of a folder. The dashboard, on the
  same computer and on `127.0.0.1` only, opens the dialog in a separate
  process (`picker.py`): on Windows the Explorer dialog (`IFileOpenDialog`
  with `FOS_PICKFOLDERS`, through `ctypes` in a child Python process, above
  the other windows); on macOS Finder's `choose folder` through `osascript`,
  the starting folder passed as an argument, never inside the script; on
  Linux `kdialog` on KDE, otherwise `zenity`, then the other one, only with
  `DISPLAY` or `WAYLAND_DISPLAY`. The dialog starts in the folder typed in
  the field, or the one that holds it. Only an absolute folder that exists
  is answered, and it is checked like a typed path. *Cancel* changes
  nothing.
- No request waits for a person: `POST /api/fs/pick` with `path` opens the
  dialog and with `id` asks for its answer, each waiting at most 20 s; the
  page asks again and says, meanwhile, that the dialog is open and may be
  behind the browser. One dialog at a time (a second press joins it), closed
  after 30 minutes or when the dashboard closes.
- Where there is no such dialog (no graphical session; neither `zenity` nor
  `kdialog`, as in a plain WSL2 Ubuntu; `PATCHRONDO_NATIVE_PICKER=0`) the
  page says why and shows its folder list, which stays as it was.
- Tests: `tests/test_picker.py` (commands per system, answers, one dialog at
  a time, the time limit, the route, closing with the dashboard; the
  Explorer dialog itself opt-in with `PATCHRONDO_REAL_PICKER=1`). `ServerCase`
  and `tools/ui_e2e.py` turn the dialog off, and the `projects` scenario
  plays it with a child process: the page waits for it, takes the folder,
  validates it, and falls back to the list without a dialog. Seven new
  mutation protections.

### A first commit from the Add project form — November 7, 2026

- **A folder that is not a Git repository yet can become one on request**
  (user request of November 7, 2026). Under *That folder is not inside a Git
  repository*, the repository field offers *Create a Git repository here…*;
  under *The repository has no commits yet*, *Make the first commit…*.
  Nothing happens on the press itself: the page first lists the files the
  commit would hold, with their sizes and total (`firstcommit.plan`, read
  through a temporary repository outside the folder, so `.gitignore` applies
  and nothing is written in the folder), and asks to check that no password,
  key or large file is listed. *Create repository and commit* then runs
  `git init` and commits exactly those files as `Initial commit`
  (`firstcommit.make`, `--literal-pathspecs`, the list given on standard
  input). A folder whose files changed since the review is refused with 409
  and listed again; nothing is created.
- The user's Git settings decide the author, the branch name, hooks and
  signing. When Git does not know the user's name and email (`git var`
  fails, as on a computer where they were never set), the page asks for them
  and they are passed with `git -c` for this one commit: no Git
  configuration file is written, global or local. That check comes before
  `git init`, so a missing name creates nothing.
- Refused: a whole drive, the home folder, PatchRondo's own state (the
  folder, inside or above it), a folder inside Git's data, a folder inside a
  repository, a folder under a `.git` Git does not open (a broken gitfile, a
  repository of another owner), more than 10,000 files or 1 GiB. A commit
  Git refuses (a hook, signing) leaves what was done in place, says so, and
  offers *Make the first commit…* again; nothing is deleted.
- Routes `POST /api/projects/first-commit/plan` and
  `POST /api/projects/first-commit`; `first_commit` (`init`, `commit` or
  null) in the answer of `/api/projects/inspect`. The hint of the field says
  PatchRondo commits only this first commit, on request.
- Tests: `tests/test_firstcommit.py` (12 tests: the plan writes nothing and
  follows `.gitignore`, literal names, refused folders, a `.git` above that
  Git does not open, limits, a changed folder, an empty folder, a repository
  with an index and no commit, a missing identity, a hook that refuses the
  commit, the routes), each with Git settings of its own; the routes in the
  token test of `tests/test_ui.py`; the `projects` scenario of
  `tools/ui_e2e.py` creates a repository from the page, with a changed
  folder and a missing identity on the way. Six new mutation protections.

### Tests written by the developer at the test consent — November 7, 2026

- **The test-consent card can ask the task's developer to write the tests**
  (user request of November 7, 2026). Under the commands field, *Ask Claude
  to write tests* (or *Ask Codex…*, after the task's developer) opens a
  confirmation that says it is a real call using the plan's quota, then
  starts `patchrondo run --draft-tests` like a Run: its own process, the
  task lock, `ui-run.log`, the page following it.
- `core.draft_tests`, outside the loop, only while the task is paused for
  `tests_disabled`: the environment check, then one call of the developer
  with the settings frozen for its step of the iteration and a test-author
  prompt (the task, the handoff, the files changed; test files only, the
  project's own framework, commands that leave no file Git does not ignore,
  a JSON object at the end with the commands). `test_draft` in the state
  keeps the proposed commands (`parse_draft`: the last JSON object, one-line
  commands `split_command` reads, at most ten, the rest counted), the files
  the worktree shows changed by content hash, notes masked and bounded, or
  the failure with its kind. The task keeps its pause and `last_error`;
  nothing is enabled or run, no retry plan is made, and `_sync` invalidates
  evidence bound to the files as they were. Events `test_draft_started`,
  `test_draft_completed`, `test_draft_failed` (counts and fixed words only).
  The loop and its events are unchanged.
- The card shows what was written (files, notes, a link to *Files changed*)
  and puts the proposed commands in the field when none is configured; the
  user still ticks the trust box and presses *Allow tests and resume*. A
  failure is shown there, and the button stays available.
- `--draft-tests` on `patchrondo run`/`resume`; `draft_tests` and `confirm`
  in the run route, refused without `confirm: true`, with `auto_resume`, or
  outside that pause (409).
- Tests: `tests/test_draft_tests.py` (10 tests: the draft and the pause it
  keeps, then a completed task running the written tests; files read from
  the worktree; refusals outside the pause, from the engine and the command
  line, with provider commands routed to a function that fails; a usage limit
  and an interruption recorded and nothing else changed; the environment
  check first; an answer without a proposal; parsing; the route). The `start`
  scenario of `tools/ui_e2e.py` asks for tests from the page, checks the
  confirmation, the files, the field filled with the proposal, and completes
  the task with the written tests. Six new mutation protections.

### Tests written on request keep their own transcripts — November 8, 2026

- **Correction** (user report from a real run, November 8, 2026). The
  adapter names the files of a step after its role, and `draft_tests` calls
  the developer: it wrote into the iteration's folder, so
  `developer.prompt.md`, `developer.reply.md`, `developer.stdout.log`,
  `developer.stderr.log`, `developer.command.txt` and
  `developer.last-message.txt` of that iteration's development step were
  replaced by the test author's, and `test-draft.md` by the next request's.
- Each request now writes in a folder of its own,
  `runs/iteration-NNN/test-draft-KK/` (`core._draft_folder`): its number is
  past every `test-draft-*` folder of the iteration, also one a crash left
  that no state names, and the folder must not exist. `test-draft.md` goes
  there too. `test_draft.transcripts` names the folder
  (`iteration-001/test-draft-01`). The call is still the developer's:
  `agents.command` builds the same command with the same frozen settings,
  and the adapter is unchanged.
- The journal tells the calls apart: every event of a request's attempt
  carries `step: "test_draft"` (`activity.attempt(step=…)`, through
  `core._invoke`). Events of the loop's steps carry no `step`, as before.
  Live Activity says "writing tests" for them.
- The Logs tab lists a request's files under "Iteration N · tests written on
  request K", with IDs `iteration-NNN/test-draft-KK/<file>`; the viewer reads
  only listed IDs, as before. Forced unlocking (`TaskLock(force=True)`) also
  looks for `*.active-process.json` in those folders: a test author still
  running blocks it like a development step.
- Evidence bundles are unchanged: a detailed bundle copies only the logs a
  ledger record names (`reviewer.reply.md`, check logs), and no record names
  a developer transcript or a request's file. The test checks that a bundle
  after requests holds no such file and attributes each log to the record
  that names it.
- Compatibility: nothing already written is moved, renamed or rewritten. A
  task whose `iteration-NNN/test-draft.md` was written before keeps it, and
  the Logs tab still lists it in the iteration's group; a saved `test_draft`
  without `transcripts` reads as before. Transcripts a request already
  replaced are not restored: PatchRondo kept no other copy (the journal keeps
  that step's bounded, redacted activity, and `handoff.md` its answer).
- Tests: `tests/test_draft_tests.py` `TranscriptTests` (2 tests, with the
  scripted CLI of `tests/support.py` behind `providers.execute`, so the
  adapter writes real transcripts: the development step's files byte for
  byte after two requests, each request's files in its folder, the journal,
  a detailed bundle after the task completed; a folder left by a crash never
  written into); a forced-unlock test in `tests/test_workflow.py`; a Logs
  test in `tests/test_ui.py`. Five new mutation protections.

### Adaptive Execution: records and settings — November 8, 2026

Phase 2.5, Adaptive Agent Execution (the user's Steps 01 and 02; audit in
`docs/PHASE_2_5_AUDIT.md`). Nothing here is applied by a run yet.

- **Step 01.** `src/patchrondo/execution.py`: versioned records of execution
  (attempts `CX-0001`, steps, the task's record), budgets with an explicit
  unit, typed outcomes mapped from `AgentFailure.kind`, strict loading that
  refuses unknown, newer and contradictory records without replacing them.
  `tests/test_execution.py` (49 tests).
- **Step 02.** An optional `execution` section of `config.json`, validated by
  `core.validate_config` (`execution.settings_of`) and left as written:
  `enabled`, `profile` (`fast`, `standard`, `deep_autonomous`, `custom`),
  `adaptive`, `limits` of a call, a step and the task, `continuation`
  (`native_resume` only `"off"`) and `no_progress_windows`. Profiles hold
  the specification's starting values; a named profile can only be
  narrowed, `custom` gives every limit, and contradictory limits are refused.
  Execution profiles are distinct from agent presets and from the gate's
  policy and change no model, tool, permission or sandbox.
- `execution.resolve` gives the effective limits of a step with their
  sources. Off (no section or `enabled: false`) it is exactly the adapter's
  `--max-turns` and timeout, with no budget. On, a profile caps but never
  raises an agent setting that is set or a configured workflow value, and
  replaces `workflow.claude_max_turns` and `workflow.agent_timeout_seconds`
  only when they hold PatchRondo's defaults; what it replaced or capped is
  listed. Codex has no Claude turns. See "Adaptive Execution settings" in
  `docs/USAGE.md` and section 20 of `docs/ARCHITECTURE.md`.
- Compatibility: configurations without the section, saved tasks, frozen
  agent settings, commands, events, checkpoints and pauses are unchanged; a
  turn limit still pauses after one call. A configuration that already held
  a hand-written `execution` key, which earlier versions ignored, is now
  validated and refused if this version cannot use it. No task override or
  global level was added.
- Tests: `tests/test_execution_config.py` (43 tests: the section, each
  profile, custom limits, types and bounds, inconsistent budgets, precedence
  over agent settings at each level and over workflow values, Claude and
  Codex accounting, unknown usage, whole runs with execution enabled
  compared with runs without it). Eight new mutation protections.

### Adaptive Execution: attempt folders and their readers — November 8, 2026

Phase 2.5, Adaptive Agent Execution (the user's Step 03). Readers know the
folders; no run writes them yet.

- `src/patchrondo/runfiles.py`: the layout of a task's `runs/` folder. Beside
  the flat `iteration-NNN/` files and the `test-draft-KK/` folders, one
  folder per attempt of a step, `iteration-NNN/develop/attempt-NNN/` or
  `iteration-NNN/review/attempt-NNN/`, created exclusively by `allocate`
  (never an existing folder, numbered past every attempt folder of the
  step, 1-999, never through a link, a junction or a file).
- Readers: the forced unlock reads every process file at the depths writers
  use, whatever its folders are called, and treats a file whose content is
  not an object with a PID as unreadable (it kept the lock before too, with
  another error); the Logs tab lists attempt folders as their own groups
  with IDs from `runs`; the evidence bundle exports a log a record names
  inside an attempt folder, checking every folder from `runs`; a review
  record names its reply's path from `runs` (unchanged for the flat folder);
  `tools/reliability_e2e.py` checks for leftover process files at every
  depth. The verification runner keeps its flat files.
- Compatibility: `run_task`, its commands, events, checkpoints, pauses and
  the gate are unchanged; it still writes flat. Nothing is migrated or
  moved; flat logs, requests for tests and earlier bundles read as before,
  and mixed layouts are listed side by side. See section 20 of
  `docs/ARCHITECTURE.md`.
- Tests: `tests/test_runfiles.py` (30 tests: allocation and its refusals,
  names, the lock with live, exited, unreadable and Windows-unverifiable
  process files, the real adapter writing two attempts and a review into
  their own folders and a live agent in one keeping the lock, the Logs API,
  bundles and the Verification Center with nested logs, links and junctions,
  the real loop with attempt folders beside it). One mutation protection
  moved to `runfiles.py`, fifteen new.

### Adaptive Execution: the lifecycle of each agent call — November 8, 2026

Phase 2.5, Adaptive Agent Execution (the user's Step 04). With
`execution.enabled`, each call of a step that starts afterwards is recorded;
without it nothing changes.

- Each call is an attempt (`CX-0001`) saved `STARTING` in `state["execution"]`
  before its folder is made and its CLI started, `RUNNING` once the process
  exists and its process file is written, then ended with its outcome, its
  termination (`exited`, `stopped`, `unverified`, `not_started`,
  `released`), the seconds measured and the usage its CLI reported (Claude's
  `num_turns`, Codex's tokens; unknown otherwise). History events
  `attempt_started`, `attempt_ended`, `attempt_released`. One folder per
  call, `runs/iteration-NNN/<develop|review>/attempt-NNN/`, made
  exclusively; a review record names the answer of its attempt.
- The Claude turn limit and the timeout of a call are resolved from the
  profile when the step first starts and frozen with it (audit decision D2):
  a Resume, a quota retry and an edited configuration do not change them for
  that step. Step and task budgets are charged once per call and never
  reset; they refuse no call yet. Nothing continues a step by itself.
- A call that may still be running holds the task: one found without an end
  after a lost run, or stopped while what it started may still run (a
  timeout or an interruption on native Windows; a refused group signal),
  pauses with `attempt_uncertain` at every Resume until the user checks and
  runs `patchrondo release-attempt TASK CX-NNNN` (refused while a process
  file of a live process is there; on native Windows, any). A call saved
  but whose folder was never made is closed as never started. An existing
  attempt folder is never reused (`attempt_folder_exists`); an unreadable
  record pauses with `execution_unreadable` and is kept.
- `process.Launch` (the PID, what holds the process, whether it was
  stopped; a start the caller cannot record stops the process) and
  `providers.Observation` (`OfficialCLI.metered`) pass this to the record;
  without them the CLI is started exactly as before.
- Compatibility: without the section or with it off, commands, flat files,
  events, checkpoints, pauses and saved states are unchanged; a step started
  before execution was enabled finishes as it started. Quota recovery, the
  reviewer's restrictions, `parse_review`, the test consent and the gate are
  unchanged. Requests for tests are not recorded as attempts. The task page
  words the new pauses and events; `tools/ui_e2e.py` and
  `tools/provider_e2e.py` read either layout. See "Adaptive Execution:
  limits and recorded calls" in `docs/USAGE.md` and section 20 of
  `docs/ARCHITECTURE.md`.
- Tests: `tests/test_attempts.py` (35 tests: the lifecycle and its saves,
  IDs and folders over manual resumes, roles, frozen limits, legacy steps
  and configurations, each outcome, Claude and Codex usage, unknown usage,
  quota, timeouts and interruptions, crashes at six boundaries, release,
  existing and malformed folders, loop and gate invariance, bundles and
  journal, real processes for `Launch`); `tests/test_execution.py` updated
  for the schema (termination, frozen call limits) with five new tests.
  In `tests/test_execution_config.py`, `EngineCompatibilityTests` keeps a
  run with execution off identical to a run without the section, and now
  checks that a run with it on decides identically (same events apart from
  the attempt events, same calls apart from the profile's `--max-turns` and
  the attempt's folder, same files, each call in its own folder).
  Twenty-one new mutation protections.

### Adaptive Execution: typed outcomes, enforced budgets and releases — November 8, 2026

Phase 2.5, Adaptive Agent Execution (the user's Step 05). With
`execution.enabled`, budgets now refuse calls; without it nothing changes.

- One mapping of outcomes: `execution.outcome_of` and `termination_of` end
  every attempt (they replace `core._outcome` and the rule inside
  `core._termination`). `AgentFailure.kind`, `last_error.kind` and every
  pause text are unchanged; a `PermissionError` (saved as `system_error`) is
  recorded as `PERMISSION_DENIED`; a malformed kind keeps no kind and is
  `UNKNOWN`. The reviewer's answer is read inside its attempt, so an
  unreadable review ends it `INVALID_FINAL_OUTPUT` (`invalid_review`, the CLI
  `exited`) instead of `COMPLETED`; records saved before are kept as they
  are. A call's end, a usable answer and the end of a phase stay three
  different things.
- Hard budgets: `ExecutionRecord.new_attempt` refuses an attempt that its
  step's budget or the task's cannot take (`admission`: one provider call,
  the call's timeout and, for Claude, its turn limit, asked of every limited
  line in a fixed order). A refusal pauses with the new kind
  `budget_exhausted` before anything starts; `last_error.budget` and the
  `task_paused` event name the scope, the unit, the limit, what was used and
  what was asked. Budgets stay frozen with the step and the task: a Resume,
  a retry, an edited configuration, turning execution off and a refreshed
  agent setting never raise them for a recorded step, and a Resume returns
  the saved state while the refusal holds. The refusal is read before the
  environment check, so no CLI self-check starts for a call that would be
  refused. A budget cannot be amended yet. An adapter that cannot be given
  a step's frozen limits makes no recorded call.
- Quota recovery stays the only retrier and is held to the budgets: a quota
  failure that no budget would let be retried stops the recovery with
  `budget_exhausted`, and a saved plan a budget now refuses is stopped, never
  started. The quota pause keeps its kind, counters keep counting, and the
  streak still ends only when the task leaves the step (an answer in the
  same step is not counted as progress).
- Releasing an uncertain call needs `--confirm-stopped` (`confirm=True`),
  a task paused by a run, a valid attempt ID of that task and a readable
  plain folder, besides the existing check of live process files.
  `attempt_released` records the acknowledgement and what was checked in
  fixed words, with `effects: "unknown"`.
- A request for tests of a task whose developer step is recorded runs with
  that step's turn limit and timeout and must fit the task's budget; it is
  still not an attempt and is not charged.
- Compatibility: tasks without the `execution` key, configurations without
  the section or with it off, and steps started without a record behave as
  before; nothing is migrated. The task page and the report word the new
  pause and stop reason. See "Adaptive Execution: limits and recorded calls"
  in `docs/USAGE.md` and section 20 of `docs/ARCHITECTURE.md`.
- Tests: `tests/test_budgets.py` (23 tests: each budget at its limit,
  unknown turns, idempotent Resume, frozen budgets across configuration
  changes and disabling, a refreshed agent setting, adapters, requests for
  tests, quota recovery within and at the budget, saved plans, uncertain
  attempts, releases, permission errors); `tests/test_execution.py` (12 new
  tests: the outcome table, the termination rule, admission). Step 04 tests
  adapted where budgets now refuse their third call or their scenario's
  second 40-turn call, and where releases now need the acknowledgement.
  Twenty-three new mutation protections; four patterns moved with the code.

### Adaptive Execution: budget integrity — November 9, 2026

Phase 2.5, Adaptive Agent Execution (the user's Step 05.1). Two ways around a
task's budget are closed.

- A recorded task is bound to its execution policy: its first record keeps
  the project's `execution` section as it was (`ExecutionRecord.policy`,
  schema 2), and every later step is recorded and resolved from it
  (`execution.governed`), whatever the project says afterwards. Turning the
  section off, removing it or choosing another profile applies to tasks
  recorded later; the task's budget keeps counting and a Resume never resets
  it. A step frozen without its step record is recorded when it starts.
- A record saved before the binding (schema 1) is not rewritten: it follows
  the project's section while that is on, and pauses with
  `execution_policy_missing` while it is off. A state that lost its record
  while its `runs` hold attempt folders pauses with
  `execution_record_missing` instead of running without one; both pauses are
  idempotent and call nothing.
- A request for tests of a recorded task is an attempt of the developer's
  step (`AttemptRecord.draft`, schema 2) in its own `test-draft-KK` folder:
  admitted by the step's and the task's budgets before anything starts,
  saved before its folder is made, charged once, held while uncertain and
  released like any call. The test-consent pause, the review and the gate are
  unchanged; a task never recorded asks as before.
- `release-attempt --confirm-stopped` stays the operator's assertion: a
  released timeout stays a timeout, an unknown outcome stays unknown, every
  charge is kept.
- Compatibility: tasks never recorded, flat files and saved schema-1
  records are read and run as before; nothing is migrated. The task page
  words the two new pauses and a request for tests.
- Tests: `tests/test_budgets.py` (now 37 tests: `BindingTests`,
  `RequestForTestsTests` rewritten for charged requests,
  `ReleaseMeaningTests`), `tests/test_execution.py` (`SchemaTwoTests`, schema
  versions), the stray-folder test of `tests/test_attempts.py` restaged in a
  recorded task. Twelve new mutation protections; two Step 05 protections of
  the uncharged request replaced; three patterns moved with the code.

### Adaptive Execution: checkpoint continuation briefs — November 9, 2026

Phase 2.5, Adaptive Agent Execution (the user's Step 06). A new module,
`continuation.py`, prepares what a new call of a step would be told after a
call of that step ended without the answer its role needs. Nothing calls it
yet: no run, prompt, command, event, checkpoint or pause changes.

- `gather` reads the task's records (the execution record and the step's
  last attempt, the frozen settings digest, criteria judged against the
  ledger, evidence assessed against the current fingerprint, findings, the
  handoff, the previous call's reply when it left one) and the worktree
  through Git's reader options, and writes nothing. `build` turns that
  `Checkpoint` into a bounded, deterministic `Brief`, different for the
  developer and the reviewer; `Brief.section` hands it only to the task,
  iteration and role it was built for.
- No brief is prepared (`Unavailable`, with a typed reason) for another
  task, iteration, phase or provider, without a record or an attempt, for an
  attempt that is not the step's last, that may still run, or that is a
  request for tests or gave its answer, while a process file is left in the
  task's runs, with other agent settings than the step's frozen ones, or
  with criteria that cannot be read.
- Every fact is labelled `VERIFIED` (a ledger record that counts, positive,
  about the files now), `OBSERVED`, `REPORTED` (an agent's words) or
  `UNKNOWN`. An agent's claim that tests pass never becomes `VERIFIED`; a
  check that passed on earlier files is stale and named for reverification;
  without a fingerprint nothing is `VERIFIED`.
- Agent text and file names are quoted data, redacted, without control
  characters, with the task's private paths replaced; sessions, process IDs,
  transcripts and the activity journal are never read.
- At most 12,000 characters by default. What a brief must say is never cut
  (`TOO_LARGE` instead); changed paths, evidence and findings, then excerpts
  are cut in that order, and what was left out is said.
- Tests: `tests/test_continuation.py` (31 tests);
  `tests/test_execution.py` now allows `continuation.py` to read the
  execution records, with read-only names only. Fourteen new mutation
  protections.

### Adaptive Execution: automatic continuation of the developer — November 9, 2026

Phase 2.5, Adaptive Agent Execution (the user's Step 07): the first step in
which PatchRondo starts an agent call by itself. Opt-in per task: only a
task bound to an execution policy with `"adaptive": true` and
`continuation.checkpoint_resume` true. Without it nothing changes.

- After a recorded developer call ends with Claude's own turn limit
  (`TURN_LIMIT_REACHED`), one new call of the same step starts at once: the
  same iteration, `develop` phase, provider and frozen agent settings, a new
  attempt (`ContinuationMode.CHECKPOINT_RESUME`) in its own folder, and the
  developer's prompt with the Step 06 checkpoint brief at its end. The tests,
  the review and the gate then run once, as before; `max_iterations` is not
  touched.
- Smaller calls from what is left: `ExecutionRecord.sized_call` lowers the
  turn limit and the timeout to what every line of the step's and the task's
  budgets has left, never above the step's frozen limits, never changing a
  charge (41 of 80 turns used: the next call gets `--max-turns 39`). No call
  with no provider call, no turn or under 60 seconds left.
- `execution.next_call` decides from the record; `core._next_call` adds what
  the run observed. No continuation after a quota limit, a timeout, an
  interruption, an error or an unusable answer; after a call of the step was
  released; for a call no process group or job object held (so none on
  native Windows, decision D4); while a process file is left in the task's
  runs; when no brief can be prepared; or once the step made its one
  automatic continuation (`AUTOMATIC_CONTINUATIONS`, temporary until progress
  can be measured). The task then pauses with `max_turns` as before, the
  message saying why.
- The environment is checked again before the new call. The continuation's
  event and its `STARTING` record are saved in one write; a crash never
  starts a call twice.
- New events `continuation_started` and `continuation_declined` (opted-in
  tasks only); `attempt_started` and `attempt_ended` name a continuation.
  The task page words them; no new control.
- An attempt may be saved with limits below its step's only as a
  continuation, never above; a request for tests is never a continuation.
  The brief now says when the budgets allow only a smaller next call, and
  tells the developer not to assume that the previous call's unfinished
  actions succeeded nor to repeat an action outside the worktree whose
  outcome is unknown.
- Tests: `tests/test_auto_continuation.py` (28 tests); the importer list of
  `tests/test_execution.py` names `core._next_call` and `core._develop`.
  Eighteen new mutation protections.

### Adaptive Execution: automatic continuation of the reviewer — November 9, 2026

Phase 2.5, Adaptive Agent Execution (the user's Step 08). The same opt-in as
the developer's continuation; without it nothing changes.

- A recorded reviewer call whose final answer could not be used as a review
  (`INVALID_FINAL_OUTPUT` with failure kind `empty_output`, `invalid_output`
  or `invalid_review`: empty, too large, truncated or malformed JSON, or an
  approval that leaves a criterion of the reviewer without an outcome) is
  followed at once by one new, complete and independent review: the same
  iteration, `review` phase, provider and frozen read-only settings, a new
  attempt (`CHECKPOINT_RESUME`) in its own `review/attempt-NNN/` folder, and
  the same review prompt with the checkpoint brief at its end
  (`core._review_prompt(continuation=…)`).
- A review that was read is never followed, whatever its verdict
  (`APPROVED`, `CHANGES_REQUESTED`, `BLOCKED`); nor a reviewer's turn limit,
  quota, a timeout, an interruption or an error. The second answer is read
  inside its own attempt by `parse_review` and `settle_review` and then
  settled once through findings, the ledger and the gate; an unusable answer
  records nothing.
- One decision and one controller for both roles: `execution.FOLLOWED` (the
  ending each phase may be followed after), `execution.next_call` with the
  new reasons `NOT_AN_UNUSABLE_ANSWER` (not opted in) and
  `WORKSPACE_CHANGED`, `core._next_call(…, role)`, and `core._continued`
  (Step 07's `core._develop`). Budgets as in Step 07: calls and seconds for
  Codex, Claude turns only for Claude; one automatic continuation per step;
  none on native Windows (D4).
- A reviewer call that changed the files, or files that changed after it,
  are never reviewed again automatically: the attempt keeps both
  fingerprints, and the next Resume runs the tests again first.
- `continuation_started` names the brief's SHA-256 (`brief`, both roles);
  `continuation_declined` gains `workspace`. The task page words the
  reviewer's continuation.
- The reviewer's brief asks for a new, complete and independent review,
  never a repair or reformat of the previous answer, whose text stays quoted
  as `[REPORTED]`. Brief eligibility is unchanged: an `INVALID_FINAL_OUTPUT`
  attempt was already accepted, a `COMPLETED` one is still refused.
- Tests: `tests/test_reviewer_continuation.py` (28 tests); one row of
  `test_auto_continuation.NextCallTests` (a reviewer's turn limit is now
  `NOT_AN_UNUSABLE_ANSWER`) and the importer list of `tests/test_execution.py`
  (`core._continued`) follow the change. Four new mutation protections; two
  Step 07 ones follow the generalized code, two more also run the new tests,
  and the one for the removed `NOT_THE_DEVELOPER` check is dropped (476 in
  all).

### Adaptive Execution: crash reconciliation and process supervision — November 10, 2026

Phase 2.5, Adaptive Agent Execution (the user's Step 09, parts 09A and 09B).
Tasks without Adaptive Execution, and every call that is not recorded, run
as before.

- **One reconciliation of an uncertain attempt** (`reconciliation.py`,
  `core._settle_attempts`). Each recorded call keeps a launch record,
  `launch.json` in its own folder: `prepared` once the folder exists,
  `launching` right before the process is created, `started` with the
  process's identity and what holds it, `ended` with whether its whole tree
  was confirmed ended, before its process file is removed. A run that finds
  a call that may still run reads it and the identity of the processes it
  names (`procinfo.tree_state`) and finds it `NEVER_STARTED`,
  `TERMINATED_UNRECORDED`, `ACTIVE`, `UNCERTAIN`, `CORRUPT` or (for the
  diagnosis) `TERMINATED_RECORDED`. Never started: closed as not started,
  no call charged, the run goes on. Ended with everything it started:
  recorded with the new termination `reconciled`, its outcome `UNKNOWN`
  (or the one it had), its charges kept, its files never used as an answer,
  the event `attempt_reconciled`, and one pause `attempt_reconciled` so that
  the user decides on a new call. Anything else: `attempt_uncertain` as
  before, with `last_error.reconciliation` (codes of what was shown and what
  is not known, never a PID) and the same in words. A PID alone, a missing
  process file or a reply file is never proof. Reading changes nothing, so a
  Resume while nothing changed still does nothing.
- **Release and diagnosis.** `patchrondo release-attempt TASK CX-NNNN`
  without `--confirm-stopped` prints what is on record of the call
  (`core.diagnose_attempt`) and still refuses. A release is refused while
  the call's launch record or a process file names a process shown running;
  a process file that names its process's identity is read by it
  (`storage.may_run`), one that does not keeps the earlier rule. A release
  still never marks a call answered, changes a charge or allows a
  continuation.
- **One rule for a process tree** (`procinfo.tree_state`, `group_state`),
  used by the reconciliation, the release and `--unlock`: a process by its
  identity, then on POSIX its group, on native Windows only a job object
  that stops its processes when its last handle closes, once the run that
  held it is gone. `--unlock` now also refuses a process file whose process
  group still has a member (POSIX), and on POSIX no longer takes a lock
  whose PID belongs to another program now for the runner. On native
  Windows a stale lock still needs a person.
- **A continuation that was due is carried out at the next Resume.** A run
  lost after a call's end was saved and before its continuation started, or
  a continuation saved and never given a process, no longer turns into a
  full-size call at the next Resume (nor into `budget_exhausted` when only
  the smaller call fits): `core._due` admits the continuation at its size,
  `core._resumed` carries it out with every condition checked again
  (`continuation_started` with `resumed: true`). A continuation that never
  started is not counted against the one per step
  (`StepExecution.last_call`).
- **Supervised agent calls** (`process.execute(supervise=True)` for every
  recorded call; `PATCHRONDO_SUPERVISION=0` starts them as before). On
  native Windows the job object is made first with kill-on-close, the CLI is
  created suspended, put in it, recorded, and only then resumed; a stop
  counts once the job reports no running process. On both systems what the
  CLI leaves running when it exits is stopped and the end of its tree
  confirmed; one that cannot be confirmed makes the call
  `process_tree_unconfirmed` (outcome `UNKNOWN`, `unverified`), whose answer
  is not used. The verification runner, the test stage and unrecorded calls
  are unchanged.
- **Decision D4, narrowed.** An automatic continuation follows only a call
  whose whole tree was held and confirmed ended, with nothing left running
  (new reasons `descendants_left`, `reconciled_attempt`,
  `windows_not_validated`). A real CLI on native Windows is still not
  continued (`core.WINDOWS_REAL_CONTINUATION` is false until the end-of-phase
  validation with real providers); with simulated providers a supervised
  call there may be.
- The task page words the new pauses, termination, reasons and event.
- Tests: `tests/test_reconciliation.py` (26) and `tests/test_supervision.py`
  (21, real local processes; job objects on native Windows, groups on
  POSIX). Changed to the new behavior: a call whose CLI exited before its
  end was saved is now reconciled (`test_attempts`, split in two), the Step
  07 and 08 crash tests now see the continuation carried out, the
  controllers' `held` gains `ended` and `left`, the emulated CLI calls the
  new launch hooks, the importer list of `tests/test_execution.py` names the
  new readers, and `test_execution_config` leaves `launch.json` out of the
  files it compares. 21 new mutation protections (`reconcile:`,
  `supervision:`), ten patterns follow the changed code (497 in all).
  Real Claude Code and Codex were not run inside a job object.

### Adaptive Execution: native session resume, off and simulated only — November 10, 2026

Phase 2.5, Adaptive Agent Execution (the user's Step 10). Tasks that do not
ask for native resume run as before, apart from the masking of sessions in
logs; no real CLI is resumed natively by this version.

- **What an installed CLI can do is read from its own help**
  (`sessions.py`: `Capability`, `capability_of`, `Status`, `Support`).
  `providers.PROBES` gains `claude --version`, `codex --version` and `codex
  exec resume --help` (`sessions.PROBE_COMMANDS`; no prompt, no session),
  asked by `OfficialCLI.resume_capability` once per adapter and never while
  providers are simulated. What the help does not show is unknown and never
  used. Observed November 10, 2026 and saved in `tests/fixtures/cli`: Claude
  Code 2.1.295 (native Windows) and 2.1.302 (WSL2) list every option a
  resume needs; codex-cli 0.161.0 (native Windows) and 0.160.1 (WSL2) list
  no `--sandbox` on `codex exec resume`, so no Codex session can be resumed
  with the step's restrictions.
- **The session a call reported**, only from Claude's `session_id`
  (`system`/`init`, `result`) and Codex's `thread_id` (`thread.started`),
  one lowercase UUID or none (`sessions.captured`). Kept only for a task
  that asks for it, with settings that keep sessions, and never for a real
  CLI: the ID in the call's launch record (`reconciliation.marker(session=)`),
  an opaque reference in `AttemptRecord.session` (`sessions.reference`).
  Transcripts, logs, the command shown in the Logs tab and failure messages
  now mask `session_id` and `thread_id` for every task (`sessions.mask`).
- **`execution.continuation.native_resume: "probe_then_use"`**, which needs
  `checkpoint_resume: true` (before: only `"off"`). For a task bound to it,
  each automatic continuation that the rules of Steps 07-09 already allow is
  a native resume when `sessions.select` finds every condition held, and a
  checkpoint resume otherwise, with one of 15 fixed reasons
  (`sessions.Unavailable`). The choice is recorded on the attempt
  (`execution.ResumeChoice`, attempt schema 3) and in the
  `continuation_started` event (`resume`). Native resumes count toward the
  one continuation per step and are admitted and charged as any call.
- **A separate command path** (`agents.resume_command`): the step's
  `agents.command` with the exact session added, every option checked
  against the help of the resume form, never `--continue`, `--last`, a fork
  or a new ID; refused for ephemeral Codex sessions, custom arguments of a
  Codex step, and a reviewer that would not be read-only. `agents.command`
  and the refusal of session options as custom arguments are unchanged.
- **A resume is confirmed or not used.** A resumed call that does not report
  the same session fails with `resume_unconfirmed`
  (`AttemptOutcome.UNSUPPORTED_RESUME`); a resume the CLI refuses fails
  that call; neither starts another process (`OfficialCLI.invoke` gives a
  resume no second start without the streaming options).
- The task page words the new pause, outcome and event detail.
- Tests: `tests/test_sessions.py` (27) and `tests/test_native_resume.py`
  (17), all with emulated or scripted CLIs. Changed to the new behavior: the
  newer-schema tests of `test_execution` (attempts now read schema 3), its
  importer list (four readers in `core.py`), and the configuration test
  that kept `native_resume` at `"off"`. 25 new mutation protections
  (`native:`), two patterns follow the changed code (522 in all). No real
  Claude Code or Codex session was created or resumed.

### Documentation alignment — October 17, 2026

- Group Phase 1 under 0.3.0 in development, preserving all dated entries,
  the 0.2.1 publication history and package metadata. Separate the 0.35
  proposal from the next 0.3 phase, Progress & Stagnation Detection.
- Keep local agent instructions ignored by Git and excluded from source
  distributions; do not incorporate their contents in public documentation.
- Identify CI results by commit: the failed history import belongs to
  `15750e6` and is corrected in `753d957`. Record the focused history recheck
  and GitHub connection check separately from the unfinished newer CI run.
- No orchestration runtime changes. Documentation and publication checks
  are recorded in [the technical review](docs/REVIEW.md#documentation-alignment-october-17-2026).

### CI duration — October 17, 2026

Published on `main` in commit `533ba8a` on October 18, 2026 (11:14 +02:00),
with the first three corrections of the next entry. Its CI run 39114549108
failed in three test jobs, each with one error from the race of the test
fixtures that the next entry corrects; every Windows shard passed.

- **The Windows test jobs no longer have to fit the whole suite in 15
  minutes.** On GitHub the suite took 514 to 772 s in the `windows-latest`
  jobs that ended (runs read on October 17, 2026), and the runs of `3f284bf`
  and `fb6ecb1` stopped
  Windows jobs at the 15-minute limit before the end. The test jobs now run
  it in shards: one job on Linux, two on macOS, four on Windows, for Python
  3.11 and 3.13 each. Every test still runs on every OS and Python, once.
- **`tools/run_tests.py`** runs the unittest suite as `unittest discover`
  does, measures each test from its start to its stop, and prints the
  slowest tests and modules, in GitHub Actions also on the job's page.
  `--shard I/N` splits it into parts of whole test classes, balanced by the
  seconds per class in `tools/test_timings.json`; every shard checks that the
  parts hold each collected test exactly once, and a part without tests
  fails. When a job is stopped from outside, it says which test was running
  and what took the time until then, and never counts that test as passed.
  `tools/test_timings.json` is in the source package with the tool. Tested
  by `tests/test_run_tests.py`, which also checks that the matrix of the
  workflow lists every shard of each system.
- **A first job compiles and collects every test** with Python 3.11 and
  3.13: a syntax error or a module that cannot be imported, such as the one
  of `15750e6`, fails in about a minute. The package check has a job of its
  own. **`CI result`** succeeds only when every job did; its name does not
  depend on the shards. A job stopped at its time limit ends as cancelled
  and fails `CI result` with that reason, never as a failed test; a run
  cancelled by a newer push skips it. The job names changed: no branch
  protection required the old ones.
- **Test repositories are copied, not built.** A trace of the processes the
  suite starts found 14,867 on native Windows, 83% of its time spent waiting
  for them; about 2,070 of them built the Git repositories of the tests. On
  native Windows the suite of `fb6ecb1` took 12.9% less time with copies.
  `tests/support.py` now builds each kind of repository with the same Git
  commands once per test process and copies it for each test (`copied`,
  `make_repo`): a repository of its own, with its own `.git`. Tested by
  `tests/test_support.py`.
- No runtime code changed. The engine's own Git commands, the larger part of
  the suite's time, are unchanged: the tests exercise them.

### CI duration: review corrections — October 18, 2026

Three defects a review of the CI duration change found, a fourth found while
correcting them and a fifth the CI run of `533ba8a` showed. Each new test
failed before its correction. The first three were published in `533ba8a`,
the fourth and the fifth in `eb295cc` (October 18, 2026, 16:26 +02:00),
whose CI run 39135998928 failed in one job only, from the defect of the
runtime corrected in "A timeout is not a refused start" above.

- **Time outside tests includes collecting them.** `tools/run_tests.py`
  started its clock after collecting the tests, while its summary said the
  time outside tests included collection: a test module that took 1.2 s to
  import was reported with 0.0 s outside tests. The clock now starts before
  collection. Each test is still measured from its start to its stop, and
  class and module fixtures stay outside tests; an interrupted run still
  reports what ran and leaves `--write-timings` unchanged. Tested with a
  module that sleeps 0.5 s when it is imported.
- **Test IDs stay whole in the GitHub summary.** IDs went between single
  backticks as they were: a valid test method named with a backtick and a
  pipe added a cell to the table of slowest tests. In the Markdown summary
  every test ID and module name (the slowest tests and modules, the tests
  that did not pass, a class fixture that failed, the test an interruption
  stopped) is now one code span on one line: a fence longer than any run of
  backticks in it, line breaks shown as `\n` and `\r`, pipes escaped in table
  cells. The JSON report and `--list` keep the IDs as unittest gives them.
  Tested with names unittest collects from a module whose classes and
  methods hold backticks, pipes, line breaks and table rows.
- **Two builders of one name keep their own repository.** `support.copied`
  kept a built folder under the builder's module and name, so a second
  function of the same name, such as two that one factory returns, got the
  first one's folder. It now keeps it under the builder itself. Tested with
  two builders of one factory.
- **A class stays in one shard whatever its method names hold.** The runner
  took a test's class as everything before the last dot of its ID, so
  `m.C.test_a.b` made a class `m.C.test_a` of its own and could go to
  another shard than the rest of `m.C`. The class is now read from each
  collected test (`class_of`) and given to `partition` with the IDs; each
  test of the JSON report records it (`class`), and the seconds per class
  (`classes`, `--write-timings`) are added up from it, so a class whose
  `setUpClass` skipped no longer adds an entry named after its skip record.
  Tested with a class whose method names hold dots, split into two shards.
- **Nothing changes a repository while it is copied.** On GitHub's Ubuntu and
  macOS runners (Git 2.55 and 2.56) the first copy of a repository just built
  by `support.copied` failed once in each of three jobs of run 39114549108:
  `.git/objects/maintenance.lock` went away while it was copied. A commit
  ends by starting Git's automatic maintenance, detached (`GIT_TRACE` shows
  `git maintenance run --auto --quiet --detach` with Git 2.50.1), which can
  still be at work on the folder. `copied` now runs a builder's Git commands
  with `maintenance.auto=false` and `gc.auto=0`, added through
  `GIT_CONFIG_COUNT` after any settings already there and removed when the
  build ends; the copies and the tests' own Git commands keep Git's
  settings. Tested by a builder whose commit is traced: it starts no
  maintenance, sees the settings already there, and leaves none behind.
- No runtime code changed. Apart from the five new tests, the suite collects
  the same IDs in the same order, and the parts of 1, 2 and 4 shards hold
  each test once. The checks are in
  [the technical review](docs/REVIEW.md#ci-duration-review-corrections-october-18-2026).

## 0.2.1 — zero configuration and self-healing

Source published on `main` in commit `624f60e` on October 12, 2026 (00:30
+02:00). No version tags or GitHub Releases exist as checked that day. The
[technical review](docs/REVIEW.md#zero-configuration-and-self-healing-october-11-2026)
records what was checked, where, and what was not. The engine keeps every
state transition of 0.2.0; what is new runs before the first agent call and
before each review.

### Validation responsibilities — October 17, 2026

- The full unittest matrix and package build/metadata checks belong to GitHub
  CI, without requiring duplicate runs during development or before a push.
  Local validation keeps the checks CI does not perform: browser, reliability,
  recovery protections and real-clock checks, opt-in sandbox checks and
  publication scanning, as applicable. Focused regression tests remain
  available locally; manual package inspection belongs to versioned releases.

### Documentation structure — October 14, 2026

Published on `main` in commit `b376702` on October 14, 2026 (22:54 +02:00).

- Shortened the README to the project overview, workflow, requirements, quick
  start and essential safety limits. Moved the operational reference to
  `docs/USAGE.md`, validation instructions to `docs/TESTING.md` and future
  development areas to `docs/ROADMAP.md`. Dated validation results remain in
  `docs/REVIEW.md`.

### Refused writes answered on Windows — October 14, 2026

Published on `main` in commit `8fe1f47` on October 14, 2026 (22:49 +02:00).

- The interface no longer loses its answer to a write it refuses on the
  headers: a missing or wrong session token, a foreign Host or Origin, a
  content type that is not JSON or a size out of bounds. A client may send the
  headers and the body separately, as Python's `http.client` does. When the
  server had answered and closed the connection before the body arrived, the
  body met a closed socket and the connection was reset, and on Windows the
  client lost the answer it had not read yet: it saw a network error instead
  of the refusal. After an answer the server now closes only its sending
  side, then reads and discards what the client still sends, at most 256 kB
  and for at most two seconds, until the client closes. A refused body is
  still never processed, and the checks keep their order.
- The fault dates from the interface of 0.2.0. It was found by the CI run for
  `303a8d3`: one of six jobs (Python 3.13 on `windows-latest`) failed in
  `test_every_api_route_requires_the_session_token` with
  `ConnectionAbortedError` (WinError 10053); the other five passed. A
  regression test sends the body after the answer. Against the previous
  server it fails on native Windows with the same error and on WSL2 with the
  reset recorded on the socket. A mutation check protects the correction.

### Test consent before the review — October 13, 2026

Published on `main` in commit `b7347fe` on October 13, 2026 (21:20 +02:00).

- **No review without the test consent.** With tests not enabled, a task
  pauses at the test checkpoint with `tests_disabled` after development and
  before the reviewer is called. Until this change it was reviewed once and
  then paused, and every Resume without the consent called the reviewer
  again.
- **Resume is idempotent.** Resuming such a pause while tests are still not
  enabled returns the saved state: no provider call, no environment check, no
  history event.
- **A saved review checkpoint.** When tests are disabled after a task reached
  the review, the next run goes back to the test checkpoint with
  `tests_disabled` and replaces the saved results with a skipped entry.
  Enabling tests runs them again before the review. `CHANGES_REQUESTED` still
  sends the feedback to the next developer iteration, within
  `max_iterations`.
- **An access problem is still reported first.** The reviewer's environment
  check runs before the pause for the missing consent, so a problem the review
  would meet is not hidden behind `tests_disabled`.
- **Real-provider check.** `tools/provider_e2e.py` without
  `--run-fixture-tests` makes one developer call per pairing and checks the
  developer side only; the reviewer's checks are printed as `not exercised`
  and counted in the result line, never as passed. With `--run-fixture-tests`
  the developer and the reviewer are checked as before.
- The pages and the documents that said "developed and reviewed once" say
  what now happens. Three removed protections were added to
  `tools/recovery_checks.py`.

### Agent settings — October 12, 2026

Published on `main` in commit `77cdc14` on October 12, 2026 (23:37 +02:00).

- **Agents page.** A card for the developer and one for the reviewer: who
  plays the role, a preset (*CLI default*, *Fast*, *Balanced*, *Max quality*,
  *Custom*), the model and the effort; *Advanced* unfolds every other setting
  of that CLI by category, each with a description, the option it becomes and,
  when it cannot be used, the reason. Custom presets can be saved, renamed,
  duplicated, replaced and deleted.
- **Independent settings per role and provider.** Model, effort, permission
  mode or sandbox, approval policy, profile, tools and deny rules, additional
  folders, limits (turns, budget, timeout), fallback model, MCP configuration,
  prompt additions, session and streaming choices, Codex configuration
  overrides and custom arguments. One provider can play both roles with two
  models.
- **Three levels with visible origin.** Global preferences
  (`<home>/agents.json`), a project (`agent_settings` in its `config.json`)
  and a task (`agent_settings` in its `state.json`); the nearest level that
  sets a value wins and the page shows where each value comes from. Homes and
  tasks without these keys behave as before, and with no settings every
  command is the one PatchRondo already ran.
- **What the CLIs offer.** Refresh asks the installed CLIs for the options of
  their help and Codex for its model catalog and profiles, without a model
  call. A setting whose option the installed CLI does not list is disabled
  with that reason. A model that nothing confirmed is marked as not verified,
  and a test call, made only after an explicit confirmation, settles it.
- **Really applied, and frozen per step.** Every call receives the model,
  effort, permissions and overrides of its role. The settings in effect are
  frozen when a step first starts and saved with the task (`agent_runs`); a
  resume, a retry and an automatic recovery repeat the step with them, and an
  edit applies from the next step or, on request, to the stopped one. The
  task page, the report and Live Activity name the model and effort used.
- **Test configuration.** Installation, login, options the installed CLI
  does not list, the model, missing folders and acknowledged risks, with a
  preview of the command (credentials masked). Nothing is started.
- **Two new failure kinds.** `model_unavailable` and `unsupported_option`
  pause the task with an explanation and the way to fix it; neither is
  retried by automatic recovery.
- **Safety.** The reviewer stays read-only and Claude Code stays
  `--restricted`, whatever is set. What widens the developer needs an
  explicit acknowledgement per role and level; options that remove a
  protection are refused. Values are typed and validated, model names cannot
  be read as options, and values that look like credentials are not stored.
- **Command line.** `patchrondo new` accepts `--developer-model`,
  `--developer-effort`, `--reviewer-model` and `--reviewer-effort`.
- Not confirmed with an authenticated CLI: the options come from the help of
  Claude Code 2.1.295 and codex-cli 0.161.0 and from the documentation of
  both, read on October 12, 2026. See the
  [technical review](docs/REVIEW.md#agent-settings-october-12-2026).

### Activity journal reader — October 12, 2026

Published on `main` in commit `a529273` on October 12, 2026 (20:43 +02:00).

- Reading a task's activity journal no longer fails when the reader meets the
  file in the instant between its creation and the writing of its first line.
  The reader measured the file before reading that line; when the line arrived
  in between, the measured size was smaller than what had already been read and
  the read raised an error, which ended an open activity stream. The size is
  now measured afterwards and never taken as smaller than the first line. Such
  a journal is read as empty, and what is written next is read from the cursor
  that answer gave.
- The fault is in the reader added with live activity in 0.2.0, not in the
  0.2.1 changes. It was found by the CI run for `624f60e`: one of six jobs
  (Python 3.13 on `ubuntu-latest`) failed in
  `test_events_reach_an_open_stream_within_a_second_and_in_order`; the other
  five passed. A regression test reproduces the same error against the
  published reader without depending on timing, and a mutation check protects
  the correction.

### Why

On native Windows 11 a Codex reviewer could not read the task worktree.
Python 3.13 creates PatchRondo's private folders with an access control list
of SYSTEM, Administrators and OWNER RIGHTS only, and the restricted token of
the Codex read-only sandbox (codex-cli 0.161.0, `[windows] sandbox =
"unelevated"`) is not checked against OWNER RIGHTS. The files were there, the
user could read them, the sandbox got "Permission denied", its shell started
in another folder and Git failed inside it. Both findings were reproduced in a
throwaway home before anything was changed.

### Added

- **Environment check** before any agent is called and again before each
  review (`environment.py`): the CLIs of the task's roles are installed and
  logged in, Git accepts the worktree, and the reviewer can read it and cannot
  write it. The record is saved in the task state under `environment`, with
  every check and its technical detail. Checks that start a real CLI run only
  in runs that start real CLIs; with simulated providers they are reported as
  skipped, never as passed. `PATCHRONDO_PREFLIGHT=0` switches the check off.
- **Reviewer preflight** (`preflight.py`), without a model and without quota:
  on native Windows the sandbox self-check of the installed Codex CLI
  (`codex sandbox -P :read-only --include-managed-config -C <worktree> -- …`,
  read from the help of codex-cli 0.161.0 and confirmed by running it) runs a
  probe that reports, each on its own: the working directory of a process, the
  location PowerShell starts in, a file read by absolute and by relative path,
  Git and its metadata, and that a canary file is intact and no file could be
  created. A write counts as refused only when the disk proves it. A CLI whose
  help does not list the options is not probed.
- **Windows access control policy** (`winacl.py`, `ctypes` only): the
  worktrees folder of a project gets one inheritable allow entry for the
  current user's SID with read and execute; a folder PatchRondo created above
  it gets "read attributes" on that folder only, when the sandbox could not
  otherwise see that it exists. Nothing is granted to a group, no deny entry
  is removed, nothing is elevated, links and junctions are never followed, and
  `tasks/`, `index/` and the configuration files keep their lists. Each change
  is recorded with the list it replaced and rolled back when a later step
  fails or the result does not verify. New homes and new projects are
  prepared when they are created.
- **Repairs** (`repair.py`): the access repair runs unattended, once per
  check, followed by a new check. Trusting a folder Git refuses for its
  ownership is offered only as a decision of the user, for the exact folders
  of that task, never as a wildcard. Every problem has a stable code, a
  category (`repairable`, `consent`, `action`, `temporary`, `permanent`) and a
  sentence without system jargon.
- **One description, one press.** The home page has a box for what should be
  done, the project (with *Choose folder…*), the two roles with their
  defaults, a readiness indicator and one *Start task* button. It creates the
  task and starts it; a repeated press creates nothing twice. The longer form
  stays under *More options*.
- **Preparation on the page.** The workflow starts with a *Prepare* step that
  follows the saved check. A problem found there is one sentence and one
  button (*Repair automatically*, or *Trust this folder and continue* after a
  confirmation); the checks, the paths and what was changed are under
  *Technical details*.
- **Test consent on the task page.** A task paused because the project's
  tests are not authorized shows the commands, asks for an explicit tick and
  resumes. Nothing else enables tests: not adding a project, not *Start task*.
- New API routes: `POST /api/projects/<id>/start` and
  `POST /api/projects/<id>/tasks/<task>/repair`. Task summaries carry
  `environment` and `error_category`; the agent status carries `system`.

### Changed

- **Claude's turn limit has its own failure kind**, `max_turns`, decided from
  the CLI's own `result` line (`error_max_turns`). It was `agent_error` since
  the correction below and `quota` before it. A login or usage diagnostic on
  standard error still decides first. It is never retried automatically. The
  subtype is known from an observed failure, the logs of a task that stopped
  at its 12-turn limit; it was not taken from the provider's source.
- **Git's "dubious ownership" has its own failure kind**,
  `git_dubious_ownership`, instead of `system_error`, with or without the
  environment check.
- The Windows note about access control lists in the README no longer says
  that the program leaves them alone: see above for exactly what it changes.

### Migration notes

- Nothing to do. States and configurations of 0.2.0 and 0.1 are read as they
  are; `environment` is added to a task state at its next run.
- An existing home created by Python 3.13 on Windows is repaired at the first
  run of a task whose reviewer is Codex: one entry on `worktrees`, and
  `environment_repaired` in the task's history. To undo it, the previous list
  of each changed folder is in the task state (`environment.repairs`).
- A manual repair made earlier (read and execute for your user on the home and
  on `worktrees`) is recognized as sufficient and left as it is.

## 0.2.0 — interface first, several projects, live activity

Source published on `main` on October 10 and 11, 2026, including live activity
in commit `c9f6e1e` and the failure classification correction in commit
`6937d9f`. No version tags or GitHub Releases exist as checked on
October 11, 2026. The [technical review](docs/REVIEW.md) records validation
by revision and platform. The engine of 0.1.0 is unchanged except as listed
below.

### Failure classification — October 11, 2026

Published on `main` in commit `6937d9f`.

- A reported streaming CLI error is classified only from its diagnostic and
  standard error. Activity messages and event names no longer turn an ordinary
  failure into a quota or authentication failure. In particular, Claude's
  `error_max_turns` alongside a `rate_limit_event` no longer schedules quota
  recovery. No new provider message form or stream event type is parsed.
- Regression coverage checks both adapters, genuine stderr diagnostics, and
  the task's pause without a recovery plan. A mutation check protects the
  classification boundary. The [technical review](docs/REVIEW.md) records the
  local validation of this correction separately from the published source.

### Live activity of the agents

Added on October 11, 2026. Checked with scripted CLIs only: no authorized real
provider call has confirmed the streaming options or the event formats.

#### Added

- **Live Activity** tab in the task workspace: which agent is working, its
  latest event, the time since the step started and the iteration; the
  agent's public messages, tool calls and their outcome, changed files, the
  project's test commands and results, errors and the end of every attempt.
  Rows are appended one at a time; *Follow* can be switched off; long messages
  and outputs open in place; a filter narrows the list; the full logs are one
  click away. The previous *Activity* tab, the steps recorded by the engine,
  is now called *Timeline*; its address is unchanged.
- `process.execute` accepts `on_stdout` and `on_stderr` observers that receive
  output while the process runs. Calls without observers run as before.
  `LineReader` reassembles lines and UTF-8 characters cut between chunks and
  drops lines longer than 2 MB.
- Claude Code is started with `--output-format stream-json --verbose` and
  Codex with `codex exec --json`. Tools, permissions, sandbox, login,
  `--json-schema`, `--output-schema` and `--output-last-message` are
  unchanged. `streams.py` turns both streams into the same events.
- `<task>/activity.jsonl`: a private, append-only journal with one event per
  line. Each event carries its time, type, provider, role, iteration, attempt
  and a sequence number that grows within the task. Retries and resumes add
  attempts and never rewrite earlier ones; a line cut by a crash is skipped.
- `GET /api/projects/{id}/tasks/{task}/activity` (pages by cursor) and
  `…/activity/stream` (server-sent events), with the checks of every API
  route. A cursor names a position in the journal, so a page that reconnects,
  or an interface that was restarted, continues without losing or repeating
  an event.
- `PATCHRONDO_LIVE_ACTIVITY=0` starts the CLIs with the previous options.
- `tools/provider_e2e.py --authorize-provider-calls` also checks that both
  CLIs reported activity while they worked.

#### Unchanged on purpose

- The outcome of a step comes from the CLI's final result only. A stream with
  events and no result is an `empty_output` failure. A reviewer's message that
  looks like a verdict is recorded as withheld, and a task is not approved
  before the final answer is validated and the tests have passed.
- Classification of quota, authentication and timeout failures, reset parsing,
  the Recovery Manager, the task lock, checkpoints, handoff and report.
- Internal reasoning (Claude thinking blocks, Codex reasoning items) is not
  shown and not saved: not in the journal, not in the step's transcript.
- If a CLI refuses the streaming options before it starts, the step is started
  again with the previous options and the journal says so. No activity is
  shown that a CLI did not report.
- A journal that cannot be written, or an observer that fails, does not fail
  the step.

#### Changed

- With a stream, `<role>.stdout.log` of a step holds the stream's transcript
  without reasoning, bounded to its first and last lines, instead of the
  single result object. The failure message saved in `state.json` prefers the
  error the CLI reported in its stream over raw output.
- The test stage and every agent call record their start and end in the
  journal, also with adapters that do not stream.

#### Tests and tools

- Tests of the observers with real processes (output seen before the process
  ends, both streams at once with megabytes of output, timeout, interrupt, a
  descendant that keeps a pipe open), of the journal (appending, crash damage,
  bounds, cursors), of both parsers with fragmented input, of the adapter and
  of whole tasks with a scripted CLI started as a real process.
- `tools/ui_e2e.py`: a ninth scenario, `live`.
- `tools/recovery_checks.py protections`: eight more safeguards.

### Interface

- `patchrondo` without a command starts the local server and opens the
  interface. It works on a new installation with no `config.json`: the first
  start shows a setup wizard (project, CLI status, default roles, tests, how
  plan limits are used) and writes nothing until its last step. If port 8765
  is taken a free port is used.
- New interface, replacing the single-page dashboard: Home, Tasks, a workspace
  per task (Overview, Activity, Logs, Tests, Review, Files changed, Handoff,
  Report, Advanced), New task, Projects, Settings and About. No page reloads;
  a sidebar that collapses to a rail and becomes a drawer on narrow windows; a
  command palette (Ctrl/Cmd+K); dark theme by default, light and system
  optional.
- A design system with tokens for color, type, space, radius and motion. The
  palette is sampled from the mascot image. Rondo appears in the brand, the
  wizard, empty states, the task note and the About page, using only the two
  official images; moods are a small motion and a status mark, switched off
  under reduced motion. See `docs/DESIGN.md`.
- The page is plain ES modules and style sheets served from the package, with
  no build step and no runtime dependency. Content written by agents is still
  inserted only as text nodes.
- New task as a page: project, title, description, acceptance criteria, roles,
  and optional limits for that task only. *Create and run* asks for the same
  confirmation as *Run* before any agent is called.
- Settings edit everything in a project's `config.json`: workflow limits,
  tests, the Recovery Manager, local retrieval (with *Update index now* and
  the result of the last manual update) and default roles, plus global
  preferences. Each form is validated by the backend before it is written.
  Enabling tests needs the trust checkbox each time the commands change.
- Timeline of the persisted events in words ("Claude started development",
  "Tests passed", "Codex: changes requested"), grouped by iteration.
- Log viewer for the run output and every saved transcript: only the end of a
  file is loaded, earlier output on request, new output is appended while a
  task runs, with Follow, Wrap, jump and Copy. (Corrected on October 11, 2026:
  an answer that arrived after another file had been chosen was drawn into
  that file's view, and a change noticed while a file was opening made the
  page read that file again from its first byte.)
- Changed files of a task with a bounded diff and the text of new files.

### Projects and settings

- Several projects in one home. `projects.json` holds the registry (stable
  `P-…` identifier, display name, state directory) and `settings.json` the
  global preferences. A project added in 0.2 keeps its state in
  `projects/<id>/`, with the layout the whole home had in 0.1.
- A 0.1 home is used where it is: a configuration at the root of the home is
  registered as a project in place, and another 0.1 home can be imported by
  reference. Nothing is moved or rewritten; worktrees keep their paths.
- A repository or a state directory is registered once. The check is repeated
  on the registry read under its mutex, so of two requests made at the same
  time one is refused.
- Removing a project deletes its registry entry only. The repository, tasks,
  worktrees and logs stay on disk and can be imported again.
- A folder picker that lists folder names on this computer, and a read-only
  repository check before anything is registered.
- A project may set default roles (`agents` in `config.json`); global defaults
  are in `settings.json`.

### Process truth and live updates

- The interface no longer shows a task as running because its saved status
  says so. `run` and `resume` record themselves in `<task>/attached/<pid>.json`
  while they live, and the lock records which process instance holds it. The
  backend verifies process ID and start time (`/proc` on Linux, the Windows
  process API, `ps` elsewhere) and answers *unverified* when it cannot.
- Five recovery situations are told apart: running; a process waiting to
  retry; a saved plan with no process; recovery ended; manual intervention.
  A saved plan is never described as a retry that will happen.
- A second automatic run is refused while a process waits for the same task; a
  manual run now stays possible. Simultaneous or repeated starts of one task
  create one process.
- A recorded process that cannot be verified is not taken as gone. A second
  automatic run is refused beside a waiting process of that kind, and a
  project is not removed from the list while one of its tasks has one; the
  task page names the file to delete once a person has checked. (Corrected on
  October 11, 2026: both actions used to be accepted, because only verified
  processes counted.)
- *Stop* interrupts a verified run or a waiting process, like Ctrl+C in its
  terminal (POSIX). *Release lock and resume* is offered when the holder of a
  lock is verified gone (POSIX); the engine still makes its own check. Native
  Windows shows both as unavailable and explains what to do.
- Live updates: one snapshot, then numbered server-sent events read with
  `fetch`, so the session token stays in a header. A gap, a server restart or
  a lost connection leads to a new snapshot; while disconnected the page shows
  the last confirmed state with its time. A hidden tab releases its
  connection. With no page listening the periodic scan stops after fifteen
  seconds and starts again with the next page. (Corrected on October 11, 2026:
  a page that connected at the instant the scan stopped could be left without
  one, and then received heartbeats but no changes until it reconnected.)

### Engine

- A task can override `max_iterations`, `agent_timeout_seconds`,
  `test_timeout_seconds` and `recovery.enabled`. The values are validated with
  the configuration's ranges, saved in `state.json` as `overrides` when the
  task is created and applied by `run_task` and the recovery supervisor. Tasks
  without overrides keep the previous state shape.
- New `tests_started` event, recorded when enabled tests begin.
- `core.validate_config` checks a configuration without reading or writing a
  file; editors use it before saving.
- The task lock also stores the start token of its process. Unlocking does not
  use it.
- On Windows, saving a state file and releasing the task lock wait up to two
  seconds for another process that has the file open, instead of failing with
  "access denied". The interface reads both files while a run writes them, so
  a run could end in the middle of a task, or leave its lock behind.
  (Corrected on October 11, 2026.)

### Command line

- Every 0.1 command still works, and a home with `config.json` at its root
  and no registry behaves as before without a registry being written. Once
  the interface has written a registry in such a home, commands without
  `--project` use the selected project, or the only registered one, as in any
  other home; with several projects and none selected they stop and ask for
  `--project`. (Corrected on October 11, 2026: the root configuration used to
  win over the selection, so `patchrondo new` could create a task in the 0.1
  repository while another project was selected.)
- New `--project ID|NAME` and `patchrondo projects`. Commands that take a task
  ID find the project that holds it. `init --repo` on a home that already has a
  registry adds a project; on a new home it creates the 0.1 layout as before.
- `ui` no longer requires `init`. `--port` and `--no-browser` are also accepted
  before the command.
- `main.sh` opens the interface without requiring a configuration; `init.sh`
  registers a repository only when one is given or found.

### Security

- Unchanged: loopback only, per-session token on every API call, Host and
  Origin checks, JSON-only bounded writes, nonce-based CSP, no external assets.
- The token is taken from the URL fragment, kept for the tab and removed from
  the address bar.
- Static files are served from a list built at startup; log and transcript
  files can only be requested by an identifier from the task's own listing.
- An unexpected server fault is answered in the common error shape with status
  500 instead of dropping the connection.

### Tests and tools

- Tests for the registry, isolation between projects, 0.1 homes, settings,
  input validation, every endpoint's protection, task creation, runs,
  duplicate starts, process verification, an interface restart during a run,
  the event stream and static checks of the shipped page.
- `tools/ui_e2e.py`: browser checks in headless Chrome or Edge through the
  DevTools protocol, with the standard library only (`tools/browser.py`).
- `tools/recovery_checks.py protections` also removes interface safeguards and
  expects the interface tests to fail.
- `tools/demo_dashboard.py` now creates two projects.

### Removed

- `static/dashboard.html` and the API routes of the previous dashboard
  (`/api/overview`, `/api/tasks/…`, `/api/settings/tests`).

### Published as source on `main` after 0.1.0 and included here

These entries describe the development snapshots as they were pushed on
October 9 and 10, 2026. Where 0.2.0 replaced something they mention (the first
dashboard, its routes, mascot images inlined as `data:` URIs, test counts), the
sections above are current.

- Add opt-in automatic quota recovery (`recovery` configuration section,
  `--auto-resume` / `--no-auto-resume` on `run` and `resume`). After a usage
  limit the foreground process saves a retry plan in `state.json`, waits
  without holding the task lock and retries the failed phase at the reset time
  stated by the provider plus a safety margin, or after an exponential backoff.
  Retries are limited by consecutive failures without progress and by a total
  wait budget; a reset beyond the budget is never brought forward. Only `quota`
  failures are retried. Disabled by default: existing configurations and
  commands behave as before.
- Keep the provider and an explicitly stated reset time with quota failures.
  Times without a date or UTC offset (apart from the Codex form below),
  impossible dates and expired resets are not used. Plans are checked again under the task lock, so concurrent waiting
  processes cannot call a provider twice for one plan or before it is due.
  Locks are never removed automatically; a lock found at retry time stops
  recovery with an error.
- Classify a failed CLI run from both output streams. Previously only standard
  error was read when both had content, so a usage limit or reset time printed
  on standard output was missed. Standard error still decides when it names a
  specific cause; the saved diagnostic shows a bounded excerpt of each stream.
- Honor a reset that an adapter states only as a wait in seconds: it is counted
  from the failure time and saved as an instant, and the later one is used when
  an absolute time is also given. Previously only the absolute form reached
  the retry decision. Bound the saved diagnostic of a Claude `is_error` result
  to an excerpt, as for a failed exit.
- Give login failures precedence over usage-limit wording when one message
  contains both; such a failure was previously classified as `quota`.
- Read the reset time that Codex CLI prints in local time without a zone
  (`try again at 3:45 PM.`, `try again at Oct 12th, 2026 3:45 PM.`), for Codex
  failures only. The form comes from the public source of codex-cli 0.160.1
  and has not been observed in a live usage-limit failure.
- Show the saved plan, provider, schedule source, retries used and the reason
  recovery ended in reports, `status`, and the dashboard task page. Record
  `recovery_scheduled`, `recovery_wait_started`, `recovery_retry_started`,
  `recovery_completed`, `recovery_stopped` and `recovery_cancelled` events.
- Add `tools/recovery_checks.py`: `protections` removes one recovery safeguard
  at a time from a temporary copy of the sources and expects tests to fail;
  `real-clock` interrupts and restarts a real wait with synthetic CLIs (POSIX).
- Add an `auto-resume` scenario to the synthetic reliability driver, and
  recovery tests with a simulated clock, bringing the local suite to 156 tests.
  Reset parsing and recovery have been exercised with simulated providers only,
  not with real usage limits.
- Add a local reliability driver with synthetic CLI subprocesses for both role
  pairings: review feedback and correction, developer/reviewer quota recovery,
  POSIX interruption and stale-lock recovery, and a three-file task. Record
  objective acceptance checks, iterations, attempted calls and elapsed times
  in an optional JSON report, without real provider usage.
- Restore GitHub Actions CI for Python 3.11 and 3.13 on Linux, Windows and
  macOS using only free standard runners for public repositories. Skip jobs
  for private repositories, cancel superseded runs and limit jobs to 15 minutes.
  Check packages on Linux without artifact uploads, Actions caches, provider
  calls or automatic publication; retain local publication review.
- Add `patchrondo ui`, a token-protected local dashboard (standard library only,
  loopback-bound) with live task status, reviews, tests, history and documents. It can
  create tasks, start or resume runs as separate CLI processes after confirmation,
  and edit test settings with explicit trust consent.
- Add `init.sh` (environment, install, configuration and CLI check) and `main.sh`
  (dashboard or `--demo` with sample data) for a two-command start. Without a
  path, `init.sh` uses the Git repository of the current directory; relative
  paths resolve from where it is run.
- Keep demo tests disabled until explicit user consent. Preserve demo files when
  runs are active, starts are pending, locks exist or shutdown is interrupted.
- Track run starts during dashboard shutdown, reject new starts once closing,
  bound the startup wait and prevent idle connections from delaying exit.
- Feature Rondo, the mascot, across the dashboard: favicon, sidebar brand, an
  overview greeting summarizing the board, empty and loading states, and a
  per-task status note. Two small WebP images ship in the package and are inlined
  as `data:` URIs at startup, so the page makes no extra requests.
- Support child runs from source-only checkouts and report immediate startup errors.
- Preserve command arguments through the test editor using Windows or POSIX
  quoting rules, including embedded quotes and trailing backslashes.
- Add `tools/provider_e2e.py`, an opt-in check of the real Claude Code and Codex
  CLIs on a throwaway repository with both pairings. It makes no model calls
  without `--authorize-provider-calls`, keeps the fixture's tests disabled unless
  `--run-fixture-tests` is also given, and stops on quota or login failures.
- Fix retrieval in a shared index: restrict matches to the searched worktree
  before taking the top candidates. Previously, more than 300 better-ranked
  chunks from other worktrees could hide relevant excerpts or return none.
- Extend local simulated-provider validation to 74 tests; native Windows skips
  the POSIX process-group test.
- Run two local real-provider checks on October 10, 2026: both pairings passed
  on WSL2 with Claude Code 2.1.291 and codex-cli 0.160.1. The first kept fixture
  tests disabled; the second completed both tasks with passing fixture tests
  and approved reviews.

## 0.1.0 — initial public MVP (source published October 8, 2026)

- Adopt the PatchRondo name for the package, CLI, state directory, environment variable and task branches.
- Local Claude Code / Codex developer and reviewer loop in a dedicated Git worktree.
- Atomic checkpoints, opt-in host tests, pause/resume and local reports.
- Correct Codex global approval argument placement and reject stale final replies.
- Restore Python 3.11 syntax compatibility and validate explicit boolean consent.
- Reject state directories inside the source repository and invalid task phases.
- Invalidate saved test results when worktree files or configured commands change.
- Pause on test timeouts and Git failures; keep diagnostic reports available.
- Bound captured output in memory and clean up POSIX process groups on timeout.
- Add simulated integration tests, publication checks, packaging and GitHub CI.
- Emit CLI output as UTF-8, including when redirected on Windows.
- Use English throughout documentation, CLI messages, task templates and reports.
- Keep local `AGENTS.md` instructions ignored by Git and excluded from public archives.
- Add optional local retrieval (SQLite FTS5/BM25, incremental, shared across worktrees)
  that adds referenced repository excerpts to developer and reviewer prompts, plus
  `patchrondo index` and `patchrondo search` commands.
- Publish the source repository on GitHub, validate the initial CI matrix and
  package checks, and add the Rondo mascot to the README.

Real authenticated provider runs have not yet been validated for this release.
