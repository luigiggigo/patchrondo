# Preparing the public repository

The source is ready for an initial **alpha/MVP** publication, subject to the
limitations in the [review](REVIEW.md). It has not yet been validated with real
Claude/Codex accounts. These steps publish the code on GitHub; publishing to
PyPI is a separate task.

## Local checks

Run from the directory containing `pyproject.toml`:

```bash
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python tools/check_publication.py
python -m build
python -m twine check dist/*
```

The publication check looks for credential patterns and state/log files in the
working tree without printing sensitive values. It excludes virtual environments,
builds and local agent instruction files. It does not inspect Git history or
guarantee the absence of every secret: also review the file list and first-commit
diff. These checks do not call models.

The MIT license is already included. The generic contributor name does not
certify code ownership: before publishing, verify that you can distribute all
sources and retain any required third-party attribution.

## First commit

The original project did not contain `.git`, so there is no previous history to
clean in the supplied source. Initialize from the project root:

```bash
git init -b main
git add .
git status --short
git diff --cached --stat
git diff --cached
git commit -m "Initial public MVP"
```

Use the Git name/email you want to make public; GitHub also provides `noreply`
addresses. Keep `.patchrondo`, credentials, logs and virtual environments out of
the commit. `.gitignore` does not remove files that are already tracked.

`AGENTS.md` contains local working instructions and is intentionally ignored by
Git. It is also excluded from public ZIP archives and package distributions.
Keep it in your local checkout; it is not part of the published source.

## Create the GitHub repository

Repository: [`luigiggigo/patchrondo`](https://github.com/luigiggigo/patchrondo).

Suggested description:
> Local, resumable Claude Code / Codex development and review loop with Git worktrees and opt-in tests.

Suggested topics: `python`, `cli`, `ai-agents`, `code-review`, `claude-code`, `codex`, `git-worktree`.

In your chosen account or organization, create an empty **Public** repository
without generating another README, `.gitignore` or license. Copy the URL GitHub
provides:

```bash
git remote add origin REPOSITORY_URL
git push -u origin main
```

Replace the `REPOSITORY_URL` placeholder. Alternatively, with GitHub CLI already
authenticated, replace `OWNER` with your account/organization:

```bash
gh repo create OWNER/patchrondo --public --source=. --remote=origin --push
```

Creating the repository and pushing makes the files public.

## After the first push

- Wait for CI results on Python 3.11-3.14, Linux, macOS and Windows.
- Enable private vulnerability reporting so `SECURITY.md` has a private channel.
- Enable the available GitHub secret scanning and push protection checks.
- Keep the repository and issue-tracker URLs in `pyproject.toml` up to date.
- Protect `main` by requiring CI and review before merging, if your plan supports it.
- Keep the initial release marked as a prerelease until testing with authorized accounts is complete.

## Real smoke test still required

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
