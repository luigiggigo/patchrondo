#!/usr/bin/env bash
# Start PatchRondo: opens the local interface to set up projects and to create,
# run and inspect tasks. On a new installation it opens the setup wizard.
# Opening it makes no model calls; only Run/Resume uses your plan quota.
#
# Usage: ./main.sh [--demo] [--port PORT] [--no-browser]
#   --demo   sample projects and tasks in a throwaway directory, deleted on exit
# Run ./init.sh once before the first start.
set -euo pipefail
cd "$(dirname "$0")"

fail() { echo "Error: $*" >&2; exit 1; }

if [ -x .venv/bin/python ]; then VENV_PYTHON=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then VENV_PYTHON=.venv/Scripts/python.exe
else fail "Environment not found: run ./init.sh first"
fi

if [ "${1:-}" = "--demo" ]; then
  shift
  exec "$VENV_PYTHON" tools/demo_dashboard.py "$@"
fi

exec "$VENV_PYTHON" -m patchrondo "$@"
