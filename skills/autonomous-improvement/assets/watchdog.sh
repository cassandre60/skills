#!/bin/bash
# Orchestrator watchdog — one code path, no process sniffing.
#
# If the heartbeat is fresh, exit. Otherwise run a foreground nudge into the
# same session and let systemd reap it. A hung nudge is bounded by
# ORCH_NUDGE_TIMEOUT (default 30 min) and an flock prevents overlapping runs.
#
# MODEL ROTATION (2026-10-20): a nudge that resumes the session on the SAME
# model whose quota just died is a no-op — verified: seven identical nudges,
# every one answered "Rate limit exceeded", over a frozen 8-hour window while
# another free model was alive. So the nudge now walks a MODEL LADDER: on a
# quota/balance failure it marks that model, and immediately retries the same
# tick on the next model. Staleness depth is a weaker signal than the failure
# signature — a quota error rotates at once, whatever the heartbeat age.
#
# See references/05-unattended-operation.md. Install with install-watchdog.sh.
#
# Configuration:
#   ORCH_STATE_DIR    durable state, outside the repo (default: ~/.local/share/orchestrator)
#   ORCH_HB_MAX_AGE   seconds before the heartbeat is stale (default: 2700 = 45 min)
#   ORCH_NUDGE_TIMEOUT seconds before the nudge is killed (default: 1800 = 30 min)
#   ORCH_RESUME_CMD   bash snippet; $1 = session id, $2 = nudge message,
#                     $3 = model id for this attempt ("" = session default)
#                     (default: opencode run --auto -s "$1" ${3:+-m "$3"} "$2")
#   ORCH_NUDGE_MSG    the nudge text
#   ORCH_MODEL_LADDER space-separated model ids, most-preferred first. Each
#                     model must have quota INDEPENDENT of the others, and must
#                     actually be free: a gateway reporting "$0 balance" is a
#                     paid pool with no credit, not a free fallback.
#                     Empty = no rotation (session default only).
#   ORCH_MODEL_COOLDOWN seconds a model that failed with a quota error is skipped
#                     (default 3600 = 1 h)
#   ORCH_NUDGE_MODELS  how many ladder models to try per tick (default 3)
#   ORCH_MIN_INTERVAL  minimum seconds between nudge rounds (default 600)
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
FAIL_FILE="$STATE_DIR/model_failures"
MAX_AGE="${ORCH_HB_MAX_AGE:-2700}"
NUDGE_TIMEOUT="${ORCH_NUDGE_TIMEOUT:-1800}"
NUDGE_MSG="${ORCH_NUDGE_MSG:-watchdog nudge: run orchestrator housekeeping per journal protocol, re-arm beats, yield promptly.}"
RESUME_CMD_DEFAULT='opencode run --auto -s "$1" ${3:+-m "$3"} "$2"'
RESUME_CMD="${ORCH_RESUME_CMD:-$RESUME_CMD_DEFAULT}"
LADDER="${ORCH_MODEL_LADDER:-}"
COOLDOWN="${ORCH_MODEL_COOLDOWN:-3600}"
MAX_MODELS="${ORCH_NUDGE_MODELS:-3}"
MIN_INTERVAL="${ORCH_MIN_INTERVAL:-600}"

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

# A quota error is a stronger wake signal than a stale heartbeat: rotate the
# model immediately, do not wait for the age threshold.
QUOTA_RE='rate limit|rate_limit|429|quota|too many requests|insufficient balance|balance is at|exceeded your current|overloaded'

failed_recently() { # $1: model id -> 0 when the model is in cooldown
  local model="$1" now ts
  now=$(date +%s)
  [ -f "$FAIL_FILE" ] || return 1
  while read -r m ts; do
    [ "$m" = "$model" ] || continue
    [ $(( now - ts )) -lt "$COOLDOWN" ] && return 0
  done <"$FAIL_FILE"
  return 1
}

mark_failed() { # $1: model id
  local model="$1" now
  now=$(date +%s)
  printf '%s %s\n' "$model" "$now" >>"$FAIL_FILE"
}

# Models to try this tick: the ladder minus models in cooldown. An empty
# ladder means "use the session default" (no rotation is possible then).
candidates=()
if [ -n "$LADDER" ]; then
  for m in $LADDER; do
    failed_recently "$m" && continue
    candidates+=("$m")
    [ "${#candidates[@]}" -ge "$MAX_MODELS" ] && break
  done
  if [ "${#candidates[@]}" -eq 0 ]; then
    echo "$(date -Is) every model in the ladder is in cooldown; trying the session default once" >>"$LOG"
    candidates+=("")
  fi
else
  candidates+=("")
fi

if [ -f "$HB" ]; then
  AGE=$(( $(date +%s) - $(stat -c %Y "$HB") ))
  if [ "$AGE" -le "$MAX_AGE" ]; then
    echo "$(date -Is) heartbeat fresh (${AGE}s <= ${MAX_AGE}s); nothing to do" >>"$LOG"
    exit 0
  fi
  echo "$(date -Is) stale heartbeat (${AGE}s > ${MAX_AGE}s) -> foreground nudge to $SID" >>"$LOG"
else
  AGE=-1
  echo "$(date -Is) heartbeat missing -> foreground nudge to $SID" >>"$LOG"
fi

if [ -f "$STATE_DIR/last_nudge" ]; then
  SINCE=$(( $(date +%s) - $(stat -c %Y "$STATE_DIR/last_nudge") ))
  if [ "$SINCE" -lt "$MIN_INTERVAL" ]; then
    echo "$(date -Is) nudged ${SINCE}s ago (< ${MIN_INTERVAL}s); skipping this tick" >>"$LOG"
    exit 0
  fi
fi
touch "$STATE_DIR/last_nudge"

# Mark the attempt before running so a crash does not cause a nudge storm.
touch "$HB"

OUT="$STATE_DIR/nudge.out"
attempt=0
for model in "${candidates[@]}"; do
  attempt=$(( attempt + 1 ))
  label="${model:-session-default}"
  echo "$(date -Is) nudge attempt $attempt on model '$label'" >>"$LOG"
  timeout --signal=TERM --kill-after=60 "$NUDGE_TIMEOUT" \
    bash -c "$RESUME_CMD" _ "$SID" "$NUDGE_MSG" "$model" >"$OUT" 2>&1
  RC=$?
  tail -c 800 "$OUT" >>"$LOG"
  if grep -Eiq "$QUOTA_RE" "$OUT"; then
    echo "$(date -Is) model '$label' answered with a quota/balance failure; rotating" >>"$LOG"
    if [ -n "$model" ]; then
      mark_failed "$model"
    fi
    continue
  fi
  echo "$(date -Is) nudge on '$label' finished with exit $RC (124 = timed out); done" >>"$LOG"
  exit 0
done

echo "$(date -Is) every attempted model failed with a quota error; leaving heartbeat stale for the next tick" >>"$LOG"
exit 0
