#!/bin/bash
# Stateless wake starter for the autonomous-improvement fleet.
# Run by orchestrator-wake.timer or cron on the host.
#
# Each tick: fleet.py wake refreshes the snapshot, asks the 0-token
# needs-brain predicate whether anything awaits a decision, and only then
# launches ONE fresh headless Orchestrator run (fleet.harness_cmd). Quiet
# hours cost no model tokens. There is no session to resume and no
# session-id file; see fleet-ops/references/05 "Primary shape: stateless wakes".

set -euo pipefail

STATE_DIR="${ORCH_STATE_DIR:-$HOME/.local/share/orchestrator}"
REPO_DIR="${ORCH_REPO_DIR:-$PWD}"
PYTHON="${ORCH_PYTHON:-python3}"

mkdir -p "$STATE_DIR"
exec 9>"$STATE_DIR/wake.lock"
if ! flock -n 9; then
  exit 0
fi

if [ -f "$REPO_DIR/scripts/autonomous/fleet.py" ]; then
  "$PYTHON" "$REPO_DIR/scripts/autonomous/fleet.py" wake --state-dir "$STATE_DIR"
fi
