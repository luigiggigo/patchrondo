# Roadmap

## Versions and evidence

| Series | Status | Scope |
|---|---|---|
| 0.2.1 | Published experimental source; current package metadata | Environment check, repairs, agent settings and the dated maintenance history |
| 0.3.0 | In development; not a tagged release | Phase 1 functionally implemented; later phases planned |
| 0.35 | Separate Evidence Engine proposal | Verification runner step implemented in `753d957`; remaining proposal not completed |

No stable release is established by the available publication record. On
October 18, 2026, GitHub `main` was checked at `533ba8a`, then at `eb295cc`,
with no version tags or GitHub Releases (on October 17 it had been checked at
`753d957`, then at `fb6ecb1`). Source publication is distinct from a versioned release
and a package publication. Package metadata remains 0.2.1; do not infer release
readiness from it. See [publication status](PUBLISHING.md#current-publication-status)
and the [dated validation record](REVIEW.md#current-source-and-ci-status-october-18-2026).

## 0.3.0 — Phase 1: functionally complete

| Increment | Implemented scope | Code and evidence |
|---|---|---|
| Step 1.1 — Acceptance Task Contract | Persistent, versioned criteria; code approval separate from completion; ledger-based verification; legacy tasks | `acceptance.py`, `capabilities.py`, `evidence.py`; `tests/test_acceptance.py` |
| Step 1.2 — Structured Review Findings | Persistent RF IDs; checked links across reviews; OPEN and PENDING_VERIFICATION; no automatic resolution | `findings.py`; `tests/test_findings.py` |
| Step 1.3 — Structured Workflow Events | Versioned WE IDs; references; checkpoint persistence and Resume; explicit truncation | `history.py`; `tests/test_history.py` |
| Step 1.4 — Phase 1 Integration | Consistency of all four records; legacy and crash/Resume coverage; readable UI and reports | `tests/test_coherence.py`, `report.py`, dashboard history and review views |

These are implemented capabilities, not a claim that every test passed on
every platform or that authenticated providers confirmed the new formats.
[Changelog](../CHANGELOG.md#030--unreleased) preserves each dated increment;
[architecture](ARCHITECTURE.md#03-record-boundaries) explains the boundaries.

## 0.3.0 — Next phase: Progress & Stagnation Detection

Planned, not implemented: detect progress and stagnation using Git changes,
test results, persistent review findings and workflow events. A record that
proves an individual finding resolved is also still missing. Existing RF and
WE identities provide inputs; they do not constitute a progress detector.
No additional phase numbering, dates or delivery commitments are assigned here.

## Separate proposal — 0.35 Evidence Engine

The request called its verification runner "Phase 2, step 1". That numbering
belongs to this separate proposal; using it for the next 0.3 phase would
conflict with Progress & Stagnation Detection. The two plans are kept apart
pending an explicit decision about their relationship.

Implemented in `753d957`: the existing evidence ledger and test stage support
typed configured checks, JUnit XML reports, observed results, masked logs,
snapshots and process cleanup, under the existing test consent. See
[architecture section 16](ARCHITECTURE.md#16-verification-runner) and
[its validation record](REVIEW.md#checks-patchrondo-runs-itself-october-17-2026).

Still proposed: invalidation based on the files a check depends on, provenance
validation beyond the current snapshot, and use of the reserved `stale` value.
Current invalidation on changes to task files and named artifacts already
exists; it does not implement dependency-based invalidation. No independent
executor or new authorization is granted by this roadmap.

## Requirements before a 0.3 release decision

- Resolve the scope of the next phase and its relationship to the separate
  0.35 proposal; do not mark planned functionality implemented.
- Verify CI for the exact candidate commit and review applicable browser,
  reliability, recovery, compatibility and crash/Resume checks.
- Confirm review outcomes per criterion, RF links, live activity and agent
  settings against named CLI versions with explicitly authorized real runs,
  or document the remaining limits in the release decision.
- Review publication scanner findings and source/package contents, then make
  an explicit version, tag and release decision. No date is assigned.

## Research and other proposals — not approved release commitments

- Optional embedding-based reranking and decision-log indexing if lexical
  retrieval proves insufficient.
- Dedicated OS sandbox for tests (Docker/VM with minimal privileges).
- Real-provider validation per platform and CLI version, including resumption
  after interruption and observed quota/reset formats; named time zones and
  a possible supervisor service. A saved retry plan still needs a live run.
- Agent process-tree supervision on native Windows, needed for Stop and stale
  lock recovery there. Check process trees are handled by the runner; agent
  trees remain a separate limitation.
- Risky-tool approval requests and notifications; an independently consented
  executor for builds, packaging and integration checks beyond configured checks.
- Changing criterion kinds after task creation; continuing provider CLI
  sessions across steps, which currently start a fresh call each time.
- Word-by-word Claude output with thinking deltas filtered; a diff per
  iteration; additional Rondo illustrations ([design](DESIGN.md)).
- Browser validation on Linux/macOS and in Firefox/Safari.
