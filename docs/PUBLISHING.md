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
mocked providers and do not call models. Tests of repositories managed by
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
and push through the repository's normal review process. Inspect the new CI run
for Python 3.11-3.14 on Linux, macOS and Windows, plus package and installed-wheel
checks. The historical initial CI result does not validate newer revisions.

## Create a versioned release

When a versioned release is intended:

- Agree on the version and update both `pyproject.toml` and the package version.
- Move the changes being released from **Unreleased** to a dated changelog entry.
- Ensure the release commit has passed the relevant tests and package checks.
- Create the version tag and GitHub Release from that reviewed commit.
- Keep the release identified as an alpha/prerelease while the documented
  authenticated-provider compatibility checks remain incomplete.

Tagging, pushing, creating a GitHub Release and publishing packages are explicit
publication actions. They are not performed by PatchRondo or its CI workflow.

## Authenticated-provider validation

A complete workflow with real Claude/Codex accounts remains unvalidated.

Use a test repository without private data and a small change with a deterministic
test. Record the OS, CLI versions and login method, without tokens or private
logs. Try both role combinations: Claude developer / Codex reviewer, and Codex
developer / Claude reviewer. Verify the worktree, tests, review and resumption
after an interruption. Calls may consume account quota; this remains a manual
test outside CI.

Workflows use only the official [checkout](https://github.com/actions/checkout)
and [setup-python](https://github.com/actions/setup-python) actions, pinned to
commits and updated through Dependabot. CI contains no provider credentials or
automatic publication workflow.
