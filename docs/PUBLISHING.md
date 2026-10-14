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
[REVIEW.md](REVIEW.md#current-source-and-ci-status-october-14-2026) records the
current commit's CI status and the dated local checks. The GitHub repository
has no version tags or GitHub Releases as checked on October 12, 2026.
Source publication, a versioned release and publishing to PyPI are separate
steps.

**0.2.1** (the environment check, its repairs and the one-press start) is
published as source on `main` in commit `624f60e`, pushed on October 12, 2026
(00:30 +02:00). The package metadata and changelog use 0.2.1. It is not
tagged or released. Its local validation is recorded in
[REVIEW.md](REVIEW.md#zero-configuration-and-self-healing-october-11-2026).
The CI run for that commit failed in one of six jobs; the correction of the
fault it found, in the reader of the activity journal, is published in commit
`a529273`, pushed on October 12, 2026 (20:43 +02:00). Its CI run passed in
all six jobs. See
[REVIEW.md](REVIEW.md#activity-journal-reader-october-12-2026).

The agent settings (commit `77cdc14`, October 12, 2026), the test consent
before the review (`b7347fe`, October 13) and the capability and acceptance
engine (`303a8d3`, pushed on October 14, 2026 at 22:14 +02:00 and the head of
`main` as checked that day) keep the same package version. Their validation
is recorded in [REVIEW.md](REVIEW.md); the
[current status](REVIEW.md#current-source-and-ci-status-october-14-2026)
lists the CI runs of these commits.

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

Run from the directory containing `pyproject.toml`:

```bash
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python tools/check_publication.py
python -m build
python -m twine check dist/*
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

Build the wheel and sdist from the intended release revision, inspect their file
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
and push through the repository's normal review process. Record the local test,
publication, package and installed-wheel results for the revision being published.
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
