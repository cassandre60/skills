#!/bin/bash
# Orchestrator watchdog — revive a throttled or dead agent session from OUTSIDE the
# session (no model quota needed to run this; the nudge costs one model call only).
# See references/05-orchestrator-survival.md for the design and rationale.
#
# Install:
#   1. Edit STATE_DIR below (or export ORCH_STATE_DIR) to a durable, OUTSIDE-REPO path.
#   2. Write your session ID into $STATE_DIR/opencode_session_id (get it via a no-op
#      session op, e.g. renaming the session).
#   3. Touch $STATE_DIR/last_beat from every orchestrator wake turn.
#   4. systemd --user units: see opencode-watchdog.service + .timer in this directory.
#
# Behavior:
#   - process alive + heartbeat stale (>45 min) -> headless nudge into the SAME session
#     via `opencode run -s <sessionID>` (throttle/freeze recovery).
#   - process dead -> same command revives the session (process-death recovery).

set -u
STATE_DIR="${ORCH_STATE_DIR:-$HOME/.local/share/orchestrator}"
LOG="$STATE_DIR/watchdog.log"
HB="$STATE_DIR/last_beat"
SID_FILE="$STATE_DIR/opencode_session_id"
MAX_AGE="${ORCH_HB_MAX_AGE:-2700}"   # 45 min
NUDGE_MSG="${ORCH_NUDGE_MSG:-watchdog nudge: run orchestrator housekeeping per journal protocol, re-arm beats, yield promptly.}"

mkdir -p "$STATE_DIR"
echo "== $(date -Is)" >> "$LOG"

SID=$(cat "$SID_FILE" 2>/dev/null || true)
[ -z "$SID" ] && { echo "no session id in $SID_FILE; nothing to do" >> "$LOG"; exit 0; }

if pgrep -f "opencode" >/dev/null 2>&1; then
  if [ -f "$HB" ]; then
    AGE=$(( $(date +%s) - $(stat -c %Y "$HB") ))
    echo "heartbeat age: ${AGE}s (process alive)" >> "$LOG"
    if [ "$AGE" -gt "$MAX_AGE" ]; then
      echo "$(date -Is) stale heartbeat (${AGE}s) -> headless nudge to $SID" >> "$LOG"
      touch "$HB"
      opencode run --auto -s "$SID" "$NUDGE_MSG" >> "$LOG" 2>&1
    fi
  else
    echo "heartbeat missing; process alive; creating it" >> "$LOG"
    touch "$HB"
  fi
else
  echo "$(date -Is) opencode process DEAD -> headless revive of $SID" >> "$LOG"
  touch "$HB"
  setsid nohup opencode run --auto -s "$SID" "$NUDGE_MSG" >> "$LOG" 2>&1 &
fi
