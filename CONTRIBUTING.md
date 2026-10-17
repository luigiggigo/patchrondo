# Contributing

This is an experimental local orchestrator for trusted repositories. Bug reports,
documentation improvements and focused pull requests are welcome.

## Development

Use Python 3.11 or later and Git. Real Claude/Codex accounts are not needed for tests.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python tools/check_publication.py
```

In PowerShell activate with `.venv\Scripts\Activate.ps1`. Full orchestration on
Windows is recommended through WSL2; native Windows tests use synthetic CLI scripts.

Keep changes small and explain the behavior before and after. Add regression tests
for changes to state transitions, consent, provider arguments and process handling.
Test quota recovery with an injected clock and scripted providers, as
`tests/test_recovery.py` does: no test may wait in real time or reach a provider CLI.
The [CI workflow](.github/workflows/ci.yml) owns the full test suite on Python
3.11 and 3.13 across Linux, Windows and macOS, and the package build and metadata
checks on Linux. These checks are not required locally before a push or after
each edit. Run focused tests locally when needed to diagnose a failure or
verify a regression, using simulated providers. Keep the local checks below
that CI does not perform, and rerun those applicable to a code change after
follow-up edits. CI runs only for public repositories on free standard
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
It covers both role pairings, feedback, quota recovery, automatic resume on a
virtual clock and a multi-file fixture.
Use WSL2 or another POSIX host for the interruption and stale-lock scenarios;
native Windows reports those scenarios as skipped. The JSON report includes
objective checks and timings, not a model-quality score.

The environment check (`environment.py`, `preflight.py`, `winacl.py`,
`repair.py`) is tested with a scripted sandbox, access lists kept in memory
and a scripted CLI status, so those tests run on every system and start no
CLI. Keep it that way: a check that starts a real CLI must stay behind `live`,
and `providers.probe` must stay the only function besides the adapter that
starts one. `tests/test_winacl.py` also exercises the real access control
lists of temporary folders on Windows. To run the preflight in the real Codex
sandbox, which starts the installed CLI without sending a prompt, opt in on
native Windows:

```powershell
$env:PATCHRONDO_REAL_SANDBOX = '1'
python -m unittest discover -s tests -p test_sandbox_real.py -v
```

Without the switch, on another system, or with a CLI that has no sandbox
self-check, those tests are skipped; a skip is not a pass. Add a `codex
sandbox` form only from the help of a named CLI version that you ran. Do not
add a repair that weakens a sandbox, grants to a group, elevates, writes a
wildcard to Git's `safe.directory`, or enables tests: when a problem cannot be
solved without one of these, the task pauses and says why.

After changing quota recovery, error classification, the environment check,
locking, process verification, the acceptance criteria and their evidence, or
what the interface may start, also run
`python tools/recovery_checks.py`. `protections` removes one safeguard at a
time from a temporary copy of the sources and expects the tests named for it to
fail; your working tree is not modified. When you add a safeguard, add
an entry for it. `real-clock` interrupts and restarts a real 12-second wait
with synthetic CLIs; it needs POSIX signals and is skipped on native Windows.
Neither makes model calls.

### Working on the interface

The interface is plain files in `src/patchrondo/static`: `index.html`, three
style sheets and ES modules. There is no build step and no package manager;
what is in the repository is what is served. The server reads these files once
at startup, so restart it to see a change, or set `PATCHRONDO_DEV_ASSETS=1` to
have them re-read on every request while you work.

- Insert dynamic text only through `h()` and `append()` in `js/dom.js`. Task
  data, logs and reviews are untrusted; `tests/test_frontend.py` fails on
  `innerHTML` and similar APIs, on inline scripts or styles, and on anything
  loaded from another origin.
- Use the tokens in `css/tokens.css` and the components in `js/ui.js`;
  `docs/DESIGN.md` describes both, and how Rondo is used.
- Keep operational decisions in the backend. The page shows `task.runtime`
  from the API; it must not work out by itself whether a process exists or
  which actions are possible.
- New endpoints go through `app.py` and get the same checks as the others in
  `ui.py`; add them to the token test in `tests/test_ui.py`.
- A criterion is verified only by records of the evidence ledger. Do not add
  a path that marks one verified from what an agent says, do not let the
  reviewer verify a criterion that is not its to verify, and do not add an
  action to the page that the backend does not carry out. A new kind of
  evidence is a new record kind in `evidence.py`, written under the task lock.
  Capability detection reads and never starts anything; it never grants.
- Live activity only observes. What `streams.py` emits must come from a line
  the CLI printed: add a line or item type from the CLI's documentation or
  source and name the version; never record internal reasoning; never take a
  step's answer from the stream. `tools/recovery_checks.py protections` removes
  these safeguards one at a time and expects the tests to notice.

Check the result in a real browser with
`python tools/ui_e2e.py --screenshots .test-tmp/shots`. It needs a local Chrome
or Edge (or `PATCHRONDO_BROWSER`), uses scripted providers only and covers the
setup wizard, a live run, the live activity of an agent while it runs, an
interface restart during a run, reconnection, the
recovery states, two projects, a 0.1 home, responsive layout, keyboard use,
large logs, the one-press start, the agent settings and the criteria of a
task with their evidence. Without a browser it reports that it was skipped. It is a local
check and not part of CI.

Contributions are distributed under the project's MIT license. Only submit code
you are entitled to contribute. Report security issues through `SECURITY.md`.

## Documentation and version history

Keep package metadata, published source and development milestones distinct.
Metadata remains 0.2.1; Phase 1 belongs to the 0.3.0 development section of
[CHANGELOG.md](CHANGELOG.md#030--unreleased). Preserve dates, commit references
and validation results when grouping entries; do not rewrite the published
0.2.1 history. The [roadmap](docs/ROADMAP.md) separates the planned 0.3 Progress
& Stagnation Detection phase from the 0.35 Evidence Engine proposal.

Verify the remote commit, tags, GitHub Releases and CI before describing
publication status. Keep source publication distinct from a tagged release.
Keep README, architecture and security notes consistent with the code; date
validation results and name their platform and scope. Historical CI results
verify only their commit. Follow [publishing guidance](docs/PUBLISHING.md).
