# Publishing updates

## Current publication status

PatchRondo's initial **0.1.0 alpha/MVP source** was published on GitHub on
October 8, 2026, at
[github.com/luigiggigo/patchrondo](https://github.com/luigiggigo/patchrondo).
Development after 0.1.0 (the first dashboard, the startup scripts, automatic
quota recovery and the reliability tools) was pushed to `main` as source on
October 9 and 10, 2026; the CI runs for those commits are recorded in
[REVIEW.md](REVIEW.md).

**0.2.0** is published as source on `main`: the interface was pushed on
October 10, 2026, live activity on October 11, 2026, in commit `c9f6e1e`, and
the failure classification correction the same day in commit `6937d9f`.
[REVIEW.md](REVIEW.md#current-source-and-ci-status-october-18-2026) records the
current commit's CI status and the dated local checks. The GitHub repository
has no version tags or GitHub Releases as checked on October 18, 2026.
Source publication, a versioned release and publishing to PyPI are separate
steps.

**0.2.1** (the environment check, its repairs and the one-press start) is
published as source on `main` in commit `624f60e`, pushed on October 12, 2026
(00:30 +02:00). The package metadata remains 0.2.1; its dated publication
history is preserved in the changelog. It is not tagged or released. Its
local validation is recorded in
[REVIEW.md](REVIEW.md#zero-configuration-and-self-healing-october-11-2026).
The CI run for that commit failed in one of six jobs; the correction of the
fault it found, in the reader of the activity journal, is published in commit
`a529273`, pushed on October 12, 2026 (20:43 +02:00). Its CI run passed in
all six jobs. See
[REVIEW.md](REVIEW.md#activity-journal-reader-october-12-2026).

The agent settings (commit `77cdc14`, October 12, 2026), the test consent
before the review (`b7347fe`, October 13), the capability and acceptance
engine (`303a8d3`, October 14), refused writes answered on Windows
(`8fe1f47`, October 14), the documentation structure (`b376702`, October 14)
the review settled by kind (`b9e5283`, October 16), and the criteria
records, review findings and workflow events of October 16 and 17 (`d4c0ecd`
to `15750e6`, pushed on October 17, 2026 at 19:00 +02:00) keep the same
package version. Their validation is recorded in [REVIEW.md](REVIEW.md); the
[current status](REVIEW.md#current-source-and-ci-status-october-18-2026)
lists the CI runs of these commits.

The remote `main` was checked again on October 17, 2026 (22:20–22:50
+02:00) at `fb6ecb1`, three commits after `753d957`, which adds the
verification runner; tags and GitHub Releases were still empty. The run of
`753d957` was cancelled by the next push before its Windows jobs ended; the
runs of `3f284bf` and `fb6ecb1` had Windows jobs stopped at the 15-minute
limit, and the run of `fb6ecb1` failed one macOS test. The failed run
39051863842 belongs to `15750e6`, whose import error is corrected in
`753d957`; do not carry a result of one commit forward to another.

On October 18, 2026 (12:58–13:18 +02:00) the remote `main` was at
`533ba8a`, pushed by the user at 11:14 +02:00 on top of `fb6ecb1`: the CI
duration change, which runs the suite in shards, and the first three of its
review corrections. Tags and GitHub Releases were still empty. Its CI run
39114549108 failed in 4 min 49 s: three test jobs (Python 3.11 and 3.13 on
Ubuntu, Python 3.13 in the first macOS shard) each had one error, a copy of a
test repository that Git's background maintenance was changing; no assertion
failed, no job was cancelled or reached its time limit, and every Windows
shard passed. That result belongs to `533ba8a` only; the correction of the
fixture is recorded in
[REVIEW.md](REVIEW.md#ci-duration-review-corrections-october-18-2026).

At 17:13 +02:00 the same day the remote `main` was at `eb295cc`, committed by
the user at 16:26 +02:00 on top of `533ba8a`: the last two corrections of the
CI duration change, in tests, tools and documentation. Tags and GitHub
Releases were still empty. Its CI run 39135998928 failed in 3 min 59 s in
one job, Python 3.11 in the first macOS shard, where
`test_a_timeout_stops_the_command_and_what_it_started` recorded a refused
start instead of a timeout; the 13 other test jobs, the collection and the
package check passed, none was cancelled or reached its time limit, and
`CI result` failed for that job only. The cause, in the runtime, is in
[REVIEW.md](REVIEW.md#a-timeout-is-not-a-refused-start-october-18-2026).

The metadata remains 0.2.1. The changelog now groups Task Contract, Findings,
Workflow Events and their integration under **0.3.0 — Unreleased**, preserving
all dated entries. This names a development series, not a new published
package. The [roadmap](ROADMAP.md) keeps the separate 0.35 proposal distinct.
See [REVIEW.md](REVIEW.md#current-source-and-ci-status-october-18-2026) for
the CI result of each exact commit.

The repository is already initialized and has an `origin` remote. Future updates
do not require `git init` or creating another GitHub repository.

The CI workflow tests Python 3.11 and 3.13 on Linux, Windows and macOS, with
package checks on Linux. Jobs use only free standard GitHub-hosted runners for
public repositories and are skipped for private repositories; no artifacts or
Actions caches are uploaded. See [Project tests](TESTING.md#project-tests-no-provider-quota-usage)
for the cost controls and triggers. A CI result covers the commit it ran on and
nothing later. Local publication review is still required, and CI does not
publish packages or source changes.

## Validate an update

Leave the full unittest matrix, package build and metadata checks to the
[CI workflow](../.github/workflows/ci.yml). They are not required locally before
a push or after each edit. Run the applicable checks that CI does not perform
as described in [TESTING.md](TESTING.md), and review publication findings.
Run from the directory containing `pyproject.toml`:

```bash
python -m pip install -e ".[dev]"
python tools/check_publication.py
```

For source-only tests, set `PYTHONPATH=src`. Automated checks use synthetic or
mocked providers and do not call models. When an update changes provider
arguments, sandbox modes or reply extraction, also run the opt-in check under
[Authenticated-provider validation](#authenticated-provider-validation). Tests of repositories managed by
PatchRondo remain subject to explicit `tests.trust_acknowledged=true` consent.

The publication check scans the working tree for credential patterns, private
state and logs without printing sensitive values. It excludes build environments
and local agent instructions. It does not inspect Git history or guarantee that
every secret is absent. Resolve or review each finding, including expected binary
assets, before publishing; a failed scan is not a clean result.

For a versioned release, build the wheel and sdist from the intended release
revision for manual inspection; this is separate from routine source-update
validation. Inspect their file
lists and verify that the wheel includes the whole interface:
`patchrondo/static/index.html`, `static/css/*.css`, `static/js/*.js`,
`static/js/views/*.js` and the two `.webp` images. A wheel without them
installs but cannot serve the page. Install the wheel in a separate environment and exercise `--help` and
the relevant commands without making provider calls.

The MIT license is already included. The generic contributor name does not
certify code ownership: before publishing, verify that you can distribute all
sources and retain any required third-party attribution.

## Review and publish source changes

Review tracked and untracked files before staging:

```bash
git status --short
git diff --stat
git diff
```

Stage the intended files, then inspect the staged content:

```bash
git diff --cached --stat
git diff --cached
```

Keep credentials, private task state, logs and generated environments out of the
commit. `.gitignore` does not remove files already tracked.

`AGENTS.md` contains local working instructions and is intentionally ignored by
Git. It is also excluded from public ZIP archives and package distributions.
Keep it in your local checkout; it is not part of the published source.

When the update is approved for publication, commit with a descriptive message
and push through the repository's normal review process. Record the applicable
local check and publication-review results, and the CI result for that commit
when it is available. For a versioned release, also record the manual package
inspection and installed-wheel results.
Include OS and Python versions; historical CI results do not validate newer revisions.

## Create a versioned release

When a versioned release is intended:

- Agree on the version and update both `pyproject.toml` and the package version.
- Check that the changelog entry of that version lists what it contains, is
  dated, and says whether it is pushed, tagged and released.
- Ensure the release commit has passed the relevant tests and package checks.
- Create the version tag and GitHub Release from that reviewed commit.
- Keep the release identified as an alpha/prerelease while the documented
  authenticated-provider compatibility checks remain incomplete.

Tagging, pushing, creating a GitHub Release and publishing packages are explicit
publication actions. They are not performed automatically by PatchRondo.

## Authenticated-provider validation

Two real-provider checks passed on October 10, 2026 on WSL2 with Claude Code
2.1.291 and codex-cli 0.160.1, both role pairings: the first kept fixture tests
disabled; the second reached passing fixture tests and approved reviews (see
[REVIEW.md](REVIEW.md)). Those checks covered 0.1.0. They have not been
repeated for 0.2.0, and no authorized real run has confirmed live activity's
streaming options and event formats.

`tools/provider_e2e.py` automates the repeatable part. It creates a throwaway
repository with a small change and a deterministic test, then runs both role
combinations: Claude developer / Codex reviewer, and Codex developer / Claude
reviewer. It checks the worktree, the review and, with `--run-fixture-tests`,
the tests and final approval. It prints the OS, CLI versions and Claude login
method, without tokens or account details.

```bash
python tools/provider_e2e.py                             # no model calls
python tools/provider_e2e.py --authorize-provider-calls --run-fixture-tests
```

Calls consume account quota, so the check runs only with explicit authorization
and stays separate from the simulated local checks. Record the date and printed
summary in [REVIEW.md](REVIEW.md); do not copy the kept task state or provider
transcripts into the repository. Resumption after an interruption is not
covered by the tool and remains a manual test.
