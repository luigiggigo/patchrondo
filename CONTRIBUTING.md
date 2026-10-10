# Contributing

This is an experimental local orchestrator for trusted repositories. Bug reports,
documentation improvements and focused pull requests are welcome.

## Development

Use Python 3.11 or later and Git. Real Claude/Codex accounts are not needed for tests.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python tools/check_publication.py
python -m build
python -m twine check dist/*
```

In PowerShell activate with `.venv\Scripts\Activate.ps1`. Full orchestration on
Windows is recommended through WSL2; native Windows tests use synthetic CLI scripts.

Keep changes small and explain the behavior before and after. Add regression tests
for changes to state transitions, consent, provider arguments and process handling.
Run validation locally with simulated providers. The [CI workflow](.github/workflows/ci.yml)
also runs the suite on Python 3.11 and 3.13 across Linux, Windows and macOS, and
checks packages on Linux. It runs only for public repositories on free standard
GitHub-hosted runners, without artifact uploads, Actions caches or real provider
calls. Review publication scanner findings locally before publishing.
Never commit credentials, prompts from private projects,
transcripts or task state. The publication check is a heuristic, not a complete
secret scanner or a substitute for reviewing the Git diff.

Changes to provider arguments, sandbox modes or reply extraction cannot be
confirmed by simulated providers. `python tools/provider_e2e.py` reports CLI
versions and login without model calls; with `--authorize-provider-calls` it
runs both pairings against your own accounts and uses plan quota. Run it only
by choice, and report the platform and CLI versions it prints with the result.

For repeatable local reliability checks without model calls, use
`python tools/reliability_e2e.py --repeat 3 --output .test-tmp/reliability.json`.
It covers both role pairings, feedback, quota recovery and a multi-file fixture.
Use WSL2 or another POSIX host for the interruption and stale-lock scenarios;
native Windows reports those scenarios as skipped. The JSON report includes
objective checks and timings, not a model-quality score.

Contributions are distributed under the project's MIT license. Only submit code
you are entitled to contribute. Report security issues through `SECURITY.md`.

## Documentation and version history

The initial 0.1.0 alpha/MVP source is already public. Record changes made after
publication under **Unreleased** in `CHANGELOG.md` until they are published.
Keep the README, architecture and security notes consistent with the code, and
label validation results with their date and scope. Historical CI results do not
verify newer local changes. Follow `docs/PUBLISHING.md` for subsequent updates.
