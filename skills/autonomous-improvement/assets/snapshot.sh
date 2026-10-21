#!/bin/bash
# Hourly snapshot accumulator for the autonomous-improvement fleet.
# Run by orchestrator-snapshot.timer or cron on the host.

set -euo pipefail

STATE_DIR="${ORCH_STATE_DIR:-$HOME/.local/share/orchestrator}"
REPO_DIR="${ORCH_REPO_DIR:-$PWD}"
PYTHON="${ORCH_PYTHON:-python3}"

mkdir -p "$STATE_DIR"
exec 9>"$STATE_DIR/snapshot.lock"
if ! flock -n 9; then
  exit 0
fi

if [ -f "$REPO_DIR/scripts/autonomous/fleet.py" ]; then
  "$PYTHON" "$REPO_DIR/scripts/autonomous/fleet.py" snapshot --state-dir "$STATE_DIR" --quiet
fi
