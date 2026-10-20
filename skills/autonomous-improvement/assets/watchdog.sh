#!/bin/bash
# Orchestrator watchdog — one code path, no process sniffing.
#
# If the heartbeat is fresh, exit. Otherwise run ONE foreground nudge into the
# same session and let systemd reap it. A hung nudge is bounded by
# ORCH_NUDGE_TIMEOUT (default 30 min) and an flock prevents overlapping runs.
#
# See references/05-unattended-operation.md. Install with install-watchdog.sh.
#
# Configuration:
#   ORCH_STATE_DIR    durable state, outside the repo (default: ~/.local/share/orchestrator)
#   ORCH_HB_MAX_AGE   seconds before the heartbeat is stale (default: 2700 = 45 min)
#   ORCH_NUDGE_TIMEOUT seconds before the nudge is killed (default: 1800 = 30 min)
#   ORCH_RESUME_CMD   bash snippet; $1 = session id, $2 = nudge message
#                     (default: opencode run --auto -s "$1" "$2")
#   ORCH_NUDGE_MSG    the nudge text
#
# Harness-specific: the default resume command is for opencode. Read your
# harness docs for its headless resume-session flags; do not copy these flags
# to a different harness.
#
# Security note: ORCH_RESUME_CMD is passed to bash -c with SID and NUDGE_MSG
# as positional arguments ($1, $2). Never source ORCH_RESUME_CMD or the
# session-id file from untrusted input.

set -u

STATE_DIR="${ORCH_STATE_DIR:-$HOME/.local/share/orchestrator}"
LOG="$STATE_DIR/watchdog.log"
HB="$STATE_DIR/last_beat"
SID_FILE="$STATE_DIR/opencode_session_id"
MAX_AGE="${ORCH_HB_MAX_AGE:-2700}"
NUDGE_TIMEOUT="${ORCH_NUDGE_TIMEOUT:-1800}"
NUDGE_MSG="${ORCH_NUDGE_MSG:-watchdog nudge: run orchestrator housekeeping per journal protocol, re-arm beats, yield promptly.}"
RESUME_CMD="${ORCH_RESUME_CMD:-opencode run --auto -s \"\$1\" \"\$2\"}"

mkdir -p "$STATE_DIR"

# D2: a hung nudge must not overlap the next timer run.
exec 9>"$STATE_DIR/watchdog.lock"
if ! flock -n 9; then
  echo "$(date -Is) previous watchdog run is still active; exiting" >>"$LOG"
  exit 0
fi

SID="$(cat "$SID_FILE" 2>/dev/null || true)"
if [ -z "$SID" ]; then
  echo "$(date -Is) no session id in $SID_FILE; nothing to do" >>"$LOG"
  exit 0
fi

if [ -f "$HB" ]; then
  AGE=$(( $(date +%s) - $(stat -c %Y "$HB") ))
  if [ "$AGE" -le "$MAX_AGE" ]; then
    echo "$(date -Is) heartbeat fresh (${AGE}s <= ${MAX_AGE}s); nothing to do" >>"$LOG"
    exit 0
  fi
  echo "$(date -Is) stale heartbeat (${AGE}s > ${MAX_AGE}s) -> foreground nudge to $SID" >>"$LOG"
else
  echo "$(date -Is) heartbeat missing -> foreground nudge to $SID" >>"$LOG"
fi

# Mark the attempt before running so a crash does not cause a nudge storm.
touch "$HB"

timeout --signal=TERM --kill-after=60 "$NUDGE_TIMEOUT" \
  bash -c "$RESUME_CMD" _ "$SID" "$NUDGE_MSG" >>"$LOG" 2>&1
RC=$?
echo "$(date -Is) nudge finished with exit $RC (124 = timed out)" >>"$LOG"
exit 0
