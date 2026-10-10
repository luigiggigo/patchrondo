# Publishing updates

## Current publication status

PatchRondo's initial **0.1.0 alpha/MVP source** was published on GitHub on
October 8, 2026, at
[github.com/luigiggigo/patchrondo](https://github.com/luigiggigo/patchrondo).
The initial CI run passed the test matrix and package checks, and private
vulnerability reporting is enabled. The original review and subsequent local
validation are recorded in [REVIEW.md](REVIEW.md).

The GitHub repository has no version tags or GitHub Releases as checked on
October 9, 2026. Source publication and a versioned release are separate steps;
publishing to PyPI is also a separate task. Changes in the current working tree,
including the dashboard and startup scripts, are listed under **Unreleased** in
[CHANGELOG.md](../CHANGELOG.md). They are not part of the initial public snapshot.

The repository is already initialized and has an `origin` remote. Future updates
do not require `git init` or creating another GitHub repository.

The GitHub Actions CI workflow was disabled on October 9, 2026. The current
checkout restores it for Python 3.11 and 3.13 on Linux, Windows and macOS, with
package checks on Linux. Jobs use only free standard GitHub-hosted runners for
public repositories and are skipped for private repositories; no artifacts or
Actions caches are uploaded. See [Project tests](../README.md#project-tests-no-provider-quota-usage)
for the cost controls and triggers. The restored workflow has not yet been run
on GitHub; initial CI results above remain historical. Local publication review
is still required, and CI does not publish packages or source changes.

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
lists and verify that the wheel includes the dashboard HTML when publishing that
feature. Install the wheel in a separate environment and exercise `--help` and
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
- Move the changes being released from **Unreleased** to a dated changelog entry.
- Ensure the release commit has passed the relevant tests and package checks.
- Create the version tag and GitHub Release from that reviewed commit.
- Keep the release identified as an alpha/prerelease while the documented
  authenticated-provider compatibility checks remain incomplete.

Tagging, pushing, creating a GitHub Release and publishing packages are explicit
publication actions. They are not performed automatically by PatchRondo.

## Authenticated-provider validation

One real-provider check passed on October 10, 2026 (WSL2, both pairings,
fixture tests disabled; see [REVIEW.md](REVIEW.md)). A complete workflow
through passing tests and approval with real Claude/Codex accounts remains
unvalidated.

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
