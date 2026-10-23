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
#   ORCH_FAILURE_TTL  seconds before a model_failures entry is pruned
#                     (default 604800 = 7 days; keeps the file bounded)
#   ORCH_NUDGE_MODELS  how many ladder models to try per tick (default 3)
#   ORCH_MIN_INTERVAL  minimum seconds between nudge rounds (default 600)
#   ORCH_AUTHORIZED_PROVIDERS  providers the owner authorises for the fleet
#                     (default: opencode). A ladder entry outside this list is
#                     refused and logged: rotation must never move the session
#                     onto a provider the owner did not approve. (Verified
#                     2026-10-23: an ad-hoc `-m` probe left the live session on
#                     a non-authorised gateway; the owner noticed first.)
#   ORCH_SWITCH_SESSION_MODEL  1 = once a ladder model carries a nudge, make it
#                     the session's model too, so the next interactive turn
#                     also runs on a live, authorised pool (default: 1)
#   ORCH_ALERT_INTERVAL seconds between repeated exhaustion alerts (default 21600)
#   ORCH_RETRY_AFTER_EXHAUSTION  seconds to wait after a tick exhausted every
#                     model before nudging again (default 1800). Without it a
#                     dead pool floods the session: each attempt delivers its
#                     message BEFORE the model call fails, so a 4-model ladder
#                     at a 20-minute timer is 4 stale messages per tick.
#
# Exit codes: 0 = tick done (nothing to do, or a model woke the session);
#             3 = total failure (every attempted model failed — wire
#             systemd `OnFailure=` or an owner notification to it, else a
#             dead fleet is discovered by silence).
#
# Quota signatures are error-line anchored: a quota term counts only on a
# line shaped like a harness ERROR, so a nudge that SUCCEEDS while merely
# mentioning limits does not park its model. A harness reporting quota on a
# bare non-error line is not detected (accepted risk; the retry ladder and
# the stale-heartbeat path cover it) — see references/05.
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
AUTHORIZED="${ORCH_AUTHORIZED_PROVIDERS:-opencode}"
SWITCH_MODEL="${ORCH_SWITCH_SESSION_MODEL:-1}"
ALERT_FILE="$STATE_DIR/ALERT.md"
ALERT_INTERVAL="${ORCH_ALERT_INTERVAL:-21600}"
EXHAUSTED_FILE="$STATE_DIR/exhausted_at"
RETRY_AFTER_EXHAUSTION="${ORCH_RETRY_AFTER_EXHAUSTION:-1800}"

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
#
# Signatures are tight on purpose (issue #19): a quota term only counts on a
# line that looks like a harness ERROR, so a nudge that SUCCEEDS while merely
# discussing limits ("rate limit handled yesterday, quota notes filed") does
# not park its model. Accepted risk, documented in references/05: a harness
# that reports quota exhaustion on a bare line with no error prefix is not
# detected here — the attempt-marker still backdates nothing, so the next
# stale tick retries and walks the ladder anyway.
ERROR_LINE_RE='^[[:space:]]*(Error|ERROR|error)\b|^[[:space:]]*HTTP[[:space:]]*429|^[[:space:]]*429[[:space:]]|curl: \(22\)|^[[:space:]]*[A-Za-z]+Error:'
QUOTA_RE='rate limit|rate_limit|429|quota|too many requests|insufficient balance|balance is at|exceeded your current'

# An INFRASTRUCTURE outage is not a quota failure. The model is not exhausted:
# rotating the ladder would burn every pool against the same broken endpoint,
# and marking one dead would park a perfectly usable model for an hour. So an
# outage ends the tick with the pool list untouched. "overloaded" is deliberately
# NOT a quota term: a gateway saying "overloaded" is usually an outage.
OUTAGE_RE='endpoint is unavailable|upstream request failed|service unavailable|bad gateway|gateway timeout|connection reset|connection refused|temporarily unavailable|econnrefused|socket hang up|502|503|504'

quota_failure() { # $1: output file -> 0 when an ERROR-shaped line carries a quota signal
  grep -E "$ERROR_LINE_RE" "$1" 2>/dev/null | grep -Eiq "$QUOTA_RE"
}

# Cooldown entries older than the TTL are pruned every tick so
# model_failures cannot grow without bound (issue #19).
FAIL_TTL="${ORCH_FAILURE_TTL:-604800}" # 7 days
prune_failures() {
  local now tmp m ts
  [ -f "$FAIL_FILE" ] || return 0
  now=$(date +%s)
  tmp="$(mktemp)"
  while read -r m ts; do
    [ -n "${m:-}" ] || continue
    case "$ts" in ''|*[!0-9]*) continue ;; esac
    if [ $(( now - ts )) -lt "$FAIL_TTL" ]; then
      printf '%s %s\n' "$m" "$ts" >>"$tmp"
    fi
  done <"$FAIL_FILE"
  mv "$tmp" "$FAIL_FILE"
}
prune_failures

# A DEAD MODEL is neither a quota exhaustion nor an outage: the pool/model was
# removed or disabled, and retrying it on this tick or the next changes
# nothing. Rotate at once and park it in the cooldown list. (Verified
# 2026-10-23: the installed watchdog had no such class, so `Model unavailable`
# fell through to the success branch — seven hours of nudges answered
# "Model unavailable" were logged as "done", and the ladder never ran.)
DEAD_MODEL_RE='model unavailable|model is not available|no such model|model not found|unknown model|model disabled'

failed_recently() { # $1: model id -> 0 when the model is in cooldown
  # `m` MUST be local: read into a global would clobber the caller's
  # ladder loop variable, and an EMPTY failure file (which the TTL prune
  # below can produce) blanks the model via read's EOF - the ladder would
  # silently degrade to the session default.
  local model="$1" now ts m
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

authorized_model() { # $1: model id -> 0 when its provider is owner-authorised
  local provider="${1%%/*}"
  case " $AUTHORIZED " in
    *" $provider "*) return 0 ;;
    *) return 1 ;;
  esac
}

switch_session_model() { # $1: model id that just carried a nudge
  local provider id
  [ -n "${1:-}" ] || return 0
  [ "$SWITCH_MODEL" = "1" ] || return 0
  provider="${1%%/*}"
  id="${1#*/}"
  # 204 No Content on success; the endpoint is documented as
  # "Switch the model used by subsequent provider turns".
  if opencode api post "/api/session/$SID/model" \
      -d "{\"model\":{\"id\":\"$id\",\"providerID\":\"$provider\"}}" >/dev/null 2>&1; then
    echo "$(date -Is) session model switched to '$1': the previous default failed this tick, so the next interactive turn runs on the live pool too" >>"$LOG"
    printf '%s\n' "$1" >"$STATE_DIR/last_good_model"
  else
    echo "$(date -Is) could not switch the session model to '$1'; the ladder still carries the next nudge" >>"$LOG"
  fi
}

alert_exhausted() { # no live model could revive the session this tick
  local now
  now=$(date +%s)
  if [ -f "$ALERT_FILE" ] && [ $(( now - $(stat -c %Y "$ALERT_FILE") )) -lt "$ALERT_INTERVAL" ]; then
    return 0
  fi
  {
    echo "# Watchdog alert — $(date -Is)"
    echo
    echo "Session \`$SID\` did not wake: every model tried this tick failed."
    echo
    echo "Tried: $TRIED"
    echo
    echo "## Next actions (in order)"
    echo
    echo '1. Probe live models **in a throwaway session**, never with the live session id:'
    echo '   `cd /tmp && opencode run -m provider/model "reply OK"` — and only provider models the owner authorises.'
    echo '2. Put the live ones into `ORCH_MODEL_LADDER` in `watchdog.env`, then `systemctl --user restart <watchdog unit>`.'
    echo '3. If the session default itself is dead, switch it:'
    echo "   \`opencode api post /api/session/$SID/model -d '{\"model\":{\"id\":\"<id>\",\"providerID\":\"<provider>\"}}'\`"
    echo
    echo "Last attempt output (tail of nudge.out):"
    echo '```'
    tail -c 600 "$OUT" 2>/dev/null
    echo '```'
  } >"$ALERT_FILE"
  echo "$(date -Is) ALERT: no model could wake the session; wrote $ALERT_FILE" >>"$LOG"
}

# Models to try this tick: the ladder minus models in cooldown. An empty
# ladder means "use the session default" (no rotation is possible then).
candidates=()
if [ -n "$LADDER" ]; then
  for m in $LADDER; do
    if ! authorized_model "$m"; then
      echo "$(date -Is) refusing ladder model '$m': provider '${m%%/*}' is not in ORCH_AUTHORIZED_PROVIDERS ('$AUTHORIZED')" >>"$LOG"
      continue
    fi
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

# The previous tick exhausted every model. Do not keep injecting messages into
# a session that cannot run a turn: each attempt delivers its message before
# the model call fails. Back off, keep the heartbeat stale (the state is still
# broken), and let the alert carry the news.
if [ -f "$EXHAUSTED_FILE" ]; then
  SINCE_EXHAUSTED=$(( $(date +%s) - $(stat -c %Y "$EXHAUSTED_FILE") ))
  if [ "$SINCE_EXHAUSTED" -lt "$RETRY_AFTER_EXHAUSTION" ]; then
    echo "$(date -Is) previous tick exhausted every model ${SINCE_EXHAUSTED}s ago; backing off without nudging (see $ALERT_FILE)" >>"$LOG"
    exit 0
  fi
fi
touch "$STATE_DIR/last_nudge"

# Mark the attempt before running so a crash does not cause a nudge storm.
touch "$HB"
ATTEMPT_TS=$(date +%s)

OUT="$STATE_DIR/nudge.out"
attempt=0
TRIED=""
for model in "${candidates[@]}"; do
  attempt=$(( attempt + 1 ))
  label="${model:-session-default}"
  TRIED="${TRIED}${TRIED:+, }${label}"
  echo "$(date -Is) nudge attempt $attempt on model '$label'" >>"$LOG"
  timeout --signal=TERM --kill-after=60 "$NUDGE_TIMEOUT" \
    bash -c "$RESUME_CMD" _ "$SID" "$NUDGE_MSG" "$model" >"$OUT" 2>&1
  RC=$?
  tail -c 800 "$OUT" >>"$LOG"
  if grep -Eiq "$OUTAGE_RE" "$OUT"; then
    echo "$(date -Is) model '$label' answered with an infrastructure outage; not a quota failure - the ladder and the cooldown list are left untouched, and the next tick retries" >>"$LOG"
    exit 0
  fi
  if quota_failure "$OUT"; then
    echo "$(date -Is) model '$label' answered with a quota/balance failure; rotating" >>"$LOG"
    [ -n "$model" ] && mark_failed "$model"
    continue
  fi
  if grep -Eiq "$DEAD_MODEL_RE" "$OUT"; then
    echo "$(date -Is) model '$label' is dead (not quota, not outage); rotating and parking it" >>"$LOG"
    [ -n "$model" ] && mark_failed "$model"
    continue
  fi
  if [ "$RC" -eq 124 ]; then
    # A timeout is ambiguous: the turn may have run for the full window, or
    # the model may have been preempted before it ever woke. The heartbeat is
    # the only trustworthy witness - the session touches it when it wakes.
    if [ "$(stat -c %Y "$HB" 2>/dev/null || echo 0)" -gt "$ATTEMPT_TS" ]; then
      echo "$(date -Is) nudge on '$label' timed out but the session woke (heartbeat moved); done" >>"$LOG"
      rm -f "$EXHAUSTED_FILE"
      exit 0
    fi
    echo "$(date -Is) nudge on '$label' timed out without the heartbeat moving; rotating" >>"$LOG"
    continue
  fi
  if [ "$RC" -ne 0 ]; then
    # An unclassified failure is still a failure. Treating it as success is
    # how a dead pool stays silent for hours (verified 2026-10-23: seven hours
    # of `Model unavailable` logged as "done", ladder never tried).
    echo "$(date -Is) nudge on '$label' exited $RC without a known signature; rotating" >>"$LOG"
    continue
  fi
  echo "$(date -Is) nudge on '$label' finished with exit 0; done" >>"$LOG"
  [ -n "$model" ] && switch_session_model "$model"
  rm -f "$EXHAUSTED_FILE"
  exit 0
done

# Every attempt failed, so nobody actually woke up. Undo the attempt-marker:
# backdate the heartbeat past the staleness threshold so the next timer tick
# (bounded by last_nudge/MIN_INTERVAL) retries at once instead of waiting out
# a freshness the session never earned. Then leave evidence the owner can see
# without reading this log.
#
# Exit 3 (distinct distress code, issue #19): the tick ended in total
# failure. Wire systemd `OnFailure=` or the owner heartbeat to it —
# otherwise a dead fleet is discovered by silence.
touch -d "@$(( $(date +%s) - MAX_AGE - 1 ))" "$HB"
touch "$EXHAUSTED_FILE"
alert_exhausted
echo "$(date -Is) every attempted model failed; heartbeat left stale, an alert written, and exiting 3 (total failure)" >>"$LOG"
exit 3
