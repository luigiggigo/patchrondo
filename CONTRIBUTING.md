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
Never invoke real agents in CI or commit credentials, prompts from private projects,
transcripts or task state. The publication check is a heuristic, not a complete
secret scanner or a substitute for reviewing the Git diff.

Contributions are distributed under the project's MIT license. Only submit code
you are entitled to contribute. Report security issues through `SECURITY.md`.

## Documentation and version history

The initial 0.1.0 alpha/MVP source is already public. Record changes made after
publication under **Unreleased** in `CHANGELOG.md` until they are published.
Keep the README, architecture and security notes consistent with the code, and
label validation results with their date and scope. Historical CI results do not
verify newer local changes. Follow `docs/PUBLISHING.md` for subsequent updates.
