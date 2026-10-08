#!/usr/bin/env bash
# Set up everything needed to start: virtual environment, package install,
# configuration for your repository and a CLI check (no model calls).
#
# Usage: /path/to/patchrondo/init.sh [/path/to/your/repository]
# Without a path, the Git repository containing the current directory is used,
# so you can run it from your project folder. Works on Linux, macOS, WSL2 and
# Git Bash. Set PATCHRONDO_HOME to use a state directory other than ~/.patchrondo.
set -euo pipefail
CALLER_DIR=$(pwd)
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
  REPO="${1:-$CALLER_DIR}"
  case "$REPO" in
    /*|[A-Za-z]:*) ;;                   # absolute (POSIX or Windows drive)
    *) REPO="$CALLER_DIR/$REPO" ;;      # relative to where the script was run
  esac
  [ -d "$REPO" ] || fail "Directory not found: $REPO"
  ROOT=$(git -C "$REPO" rev-parse --show-toplevel 2>/dev/null) ||
    fail "Not inside a Git repository: $REPO
       Run init.sh from your project folder, or pass its path: ./init.sh /path/to/repository"
  if [ -z "${1:-}" ] && [ "$ROOT" -ef "$(pwd)" ]; then
    fail "The current directory is PatchRondo itself.
       Run init.sh from your project folder, or pass its path: ./init.sh /path/to/repository"
  fi
  [ -n "${1:-}" ] || echo "→ No path given: using the Git repository of the current directory"
  REPO=$ROOT
  if command -v cygpath >/dev/null 2>&1; then REPO=$(cygpath -w "$REPO"); fi
  echo "→ Initializing configuration for $REPO"
  "$VENV_PYTHON" -m patchrondo init --repo "$REPO"
fi

echo "→ Checking Claude Code and Codex CLIs"
"$VENV_PYTHON" -m patchrondo doctor || true

cat <<EOF

Setup complete. Next steps:
  • Tests are disabled by default. To allow PatchRondo to run your project's
    tests, use "Edit tests" in the dashboard or the "tests" section of
    $STATE_DIR/config.json (see README).
  • ./main.sh          open the dashboard
  • ./main.sh --demo   try the dashboard with sample data
EOF
