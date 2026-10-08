#!/usr/bin/env bash
# Set up everything needed to start: virtual environment, package install,
# configuration for your repository and a CLI check (no model calls).
#
# Usage: ./init.sh [/path/to/your/repository]
# Works on Linux, macOS, WSL2 and Git Bash. Set PATCHRONDO_HOME to use a
# state directory other than ~/.patchrondo.
set -euo pipefail
cd "$(dirname "$0")"

fail() { echo "Error: $*" >&2; exit 1; }

find_python() {
  local candidate
  for candidate in python3.14 python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

PYTHON=$(find_python) || fail "Python 3.11 or later is required"
command -v git >/dev/null 2>&1 || fail "Git is required"

if [ ! -d .venv ]; then
  echo "→ Creating virtual environment in .venv"
  "$PYTHON" -m venv .venv
fi
if [ -x .venv/bin/python ]; then VENV_PYTHON=.venv/bin/python; else VENV_PYTHON=.venv/Scripts/python.exe; fi
[ -x "$VENV_PYTHON" ] || fail "Virtual environment is incomplete; delete .venv and run again"

echo "→ Installing PatchRondo"
"$VENV_PYTHON" -m pip install --quiet --disable-pip-version-check -e .

STATE_DIR="${PATCHRONDO_HOME:-$HOME/.patchrondo}"
if [ -f "$STATE_DIR/config.json" ]; then
  echo "→ Configuration already exists: $STATE_DIR/config.json"
else
  REPO="${1:-}"
  if [ -z "$REPO" ]; then
    read -r -p "Path of the Git repository to work on: " REPO
  fi
  [ -n "$REPO" ] || fail "A repository path is required"
  [ -d "$REPO" ] || fail "Directory not found: $REPO"
  REPO=$(cd "$REPO" && pwd)
  if command -v cygpath >/dev/null 2>&1; then REPO=$(cygpath -w "$REPO"); fi
  echo "→ Initializing configuration for $REPO"
  "$VENV_PYTHON" -m patchrondo init --repo "$REPO"
fi

echo "→ Checking Claude Code and Codex CLIs"
"$VENV_PYTHON" -m patchrondo doctor || true

cat <<EOF

Setup complete. Next steps:
  • Tests are disabled by default. To allow PatchRondo to run your project's
    tests, edit the "tests" section of $STATE_DIR/config.json (see README).
  • ./main.sh          open the dashboard
  • ./main.sh --demo   try the dashboard with sample data
EOF
