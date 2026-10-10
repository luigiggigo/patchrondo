#!/usr/bin/env bash
# Set up everything needed to start: virtual environment, package install and a
# CLI check (no model calls). Projects are added in the interface that
# ./main.sh opens; passing a repository here registers it right away.
#
# Usage: /path/to/patchrondo/init.sh [/path/to/your/repository]
# Without a path, the Git repository containing the current directory is
# registered if there is one (never PatchRondo's own checkout). Works on Linux,
# macOS, WSL2 and Git Bash. Set PATCHRONDO_HOME to use a state directory other
# than ~/.patchrondo.
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
if [ -f "$STATE_DIR/config.json" ] || [ -f "$STATE_DIR/projects.json" ]; then
  echo "→ PatchRondo is already set up in $STATE_DIR"
else
  REPO="${1:-$CALLER_DIR}"
  case "$REPO" in
    /*|[A-Za-z]:*) ;;                   # absolute (POSIX or Windows drive)
    *) REPO="$CALLER_DIR/$REPO" ;;      # relative to where the script was run
  esac
  ROOT=""
  if [ -d "$REPO" ]; then ROOT=$(git -C "$REPO" rev-parse --show-toplevel 2>/dev/null) || ROOT=""; fi
  if [ -n "${1:-}" ]; then
    [ -d "$REPO" ] || fail "Directory not found: $REPO"
    [ -n "$ROOT" ] || fail "Not inside a Git repository: $REPO"
  elif [ -z "$ROOT" ] || [ "$ROOT" -ef "$(pwd)" ]; then
    ROOT=""                             # nothing to register: the interface will ask
  else
    echo "→ No path given: using the Git repository of the current directory"
  fi
  if [ -n "$ROOT" ]; then
    REPO=$ROOT
    if command -v cygpath >/dev/null 2>&1; then REPO=$(cygpath -w "$REPO"); fi
    echo "→ Registering $REPO"
    "$VENV_PYTHON" -m patchrondo init --repo "$REPO"
  else
    echo "→ No repository registered yet: ./main.sh opens a guided setup"
  fi
fi

echo "→ Checking Claude Code and Codex CLIs"
"$VENV_PYTHON" -m patchrondo doctor || true

cat <<EOF

Setup complete. Next steps:
  • ./main.sh          open PatchRondo in your browser (projects, tasks, settings)
  • ./main.sh --demo   try it with sample data
  Tests of your projects are disabled until you enable them in Settings → Tests.
EOF
