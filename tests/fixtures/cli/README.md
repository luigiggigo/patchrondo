# Help output of the provider CLIs

What the installed CLIs printed for the model-free questions of
`sessions.PROBE_COMMANDS`, saved unchanged on November 10, 2026 (Phase 2.5,
Step 10). No prompt was given, no session was created and no quota was used.

| Folder | Platform | Commands |
|---|---|---|
| `claude-2.1.295` | native Windows | `claude --version`, `claude --help` |
| `claude-2.1.302` | WSL2 (Ubuntu) | `claude --version`, `claude --help` |
| `codex-0.161.0` | native Windows | `codex --version`, `codex exec --help`, `codex exec resume --help` |
| `codex-0.160.1` | WSL2 (Ubuntu) | `codex --version`, `codex exec --help`, `codex exec resume --help` |

They are evidence of what those binaries list, not of what a resumed call
does: no native resume was run with a real provider. `tests/test_sessions.py`
reads them through `sessions.capability_of`.
