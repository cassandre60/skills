#!/bin/bash
# Stateless-wake launcher (issue #18): a timer STARTS a fresh headless run
# instead of resuming one immortal session.
#
# Flow per tick:
#   1. flock (a slow wake must not overlap the next timer run);
#   2. fleet kill switch present -> do nothing (the operator said stop);
#   3. `fleet.py needs-brain --json` in $ORCH_REPO — unchanged state means
#      QUIET: nothing is launched, 0 tokens spent;
#   4. otherwise launch $ORCH_WAKE_CMD once, walking $ORCH_MODEL_LADDER on
#      quota failures (error-line anchored, same rule as the watchdog);
#   5. record the attempt; on total failure exit 3 and CLEAR the recorded
#      digest so the next tick retries the same state.
#
# No session id is read or written anywhere: that is the whole point.
#
# Environment:
#   ORCH_REPO          repo to run the predicate in (default: cwd)
#   ORCH_STATE_DIR     durable state dir holding wake.json (default: ~/.local/share/orchestrator)
#   ORCH_WAKE_CMD      bash snippet; $1 = prompt text, $2 = model id ("" = harness default)
#                      (default: opencode run --auto ${2:+-m "$2"} "$1")
#   ORCH_PROMPT_FILE   file whose contents become $1 (default: $STATE_DIR/wake-prompt.md;
#                      falls back to a built-in prompt naming the skill's wake protocol)
#   ORCH_MODEL_LADDER  space-separated model ids, most-preferred first (empty = one attempt)
#   ORCH_WAKE_TIMEOUT  seconds before the run is killed (default: 3600)
#   ORCH_MIN_INTERVAL  minimum seconds between launches (default: 0)
#   ORCH_MODEL_COOLDOWN / ORCH_FAILURE_TTL  shared semantics with the watchdog
#                      (wake_failures cooldown 3600s, prune older than 7 days)
#
# Exit codes: 0 = quiet (unchanged state) or a model woke the session;
#             2 = misconfiguration (missing ORCH_REPO / predicate);
#             3 = total failure (every attempted model failed) — wire
#                 systemd OnFailure= or an owner notification to it.
#
# Harness note: the ORCH_WAKE_CMD contract is the adapter row's one-shot
# form. Verified for opencode (`opencode run --auto [-m model] "<prompt>"`,
# `--format json` for machine-readable output; no turn/budget cap flag
# exists there, so scope the prompt). Claude Code / Codex rows in
# references/05 are unverified — set ORCH_WAKE_CMD after checking your
# installed version, and never pass a resume flag: this shape starts fresh.

set -u

STATE_DIR="${ORCH_STATE_DIR:-$HOME/.local/share/orchestrator}"
REPO="${ORCH_REPO:-$PWD}"
LOG="$STATE_DIR/wake.log"
FAIL_FILE="$STATE_DIR/wake_failures"
HB="$STATE_DIR/last_wake"
LADDER="${ORCH_MODEL_LADDER:-}"
NUDGE_TIMEOUT="${ORCH_WAKE_TIMEOUT:-3600}"
MIN_INTERVAL="${ORCH_MIN_INTERVAL:-0}"
COOLDOWN="${ORCH_MODEL_COOLDOWN:-3600}"
FAIL_TTL="${ORCH_FAILURE_TTL:-604800}"
PROMPT_FILE="${ORCH_PROMPT_FILE:-$STATE_DIR/wake-prompt.md}"
WAKE_CMD="${ORCH_WAKE_CMD:-opencode run --auto \${2:+-m \"\$2\"} \"\$1\"}"

mkdir -p "$STATE_DIR"

echo "$(date -Is) tick: repo=$REPO ladder=[$LADDER] interval=${MIN_INTERVAL}s" >>"$LOG"

exec 9>"$STATE_DIR/stateless-wake.lock"
if ! flock -n 9; then
  echo "$(date -Is) previous wake is still active; exiting" >>"$LOG"
  exit 0
fi

# 1. The operator's global stop wins over everything.
for ks in "$REPO/.autonomous/STOP" "$PWD/.autonomous/STOP"; do
  if [ -e "$ks" ]; then
    echo "$(date -Is) fleet kill switch present ($ks); not launching a wake" >>"$LOG"
    exit 0
  fi
done

if [ ! -d "$REPO" ]; then
  echo "[!] ORCH_REPO does not exist: $REPO" >&2
  exit 2
fi
PREDICATE="$REPO/scripts/autonomous/fleet.py"
if [ ! -f "$PREDICATE" ]; then
  echo "[!] predicate not found: $PREDICATE (install the skill scripts first)" >&2
  exit 2
fi

# 2. Quiet hours: spend a model turn only when state changed.
NEEDS_BRAIN="$(
  cd "$REPO" && python3 "$PREDICATE" needs-brain --json 2>>"$LOG" \
    | python3 -c 'import json,sys; print("yes" if json.load(sys.stdin).get("needs_brain") else "no")' 2>>"$LOG"
)"
if [ "${NEEDS_BRAIN:-no}" != "yes" ]; then
  echo "$(date -Is) quiet: state unchanged since the last wake (0 tokens)" >>"$LOG"
  exit 0
fi

# 3. Rate limit the launches (independent of the predicate's own cadence).
if [ -f "$HB" ]; then
  SINCE=$(( $(date +%s) - $(stat -c %Y "$HB") ))
  if [ "$SINCE" -lt "$MIN_INTERVAL" ]; then
    echo "$(date -Is) woke ${SINCE}s ago (< ${MIN_INTERVAL}s); skipping this tick" >>"$LOG"
    exit 0
  fi
fi
touch "$HB"

# 4. Cooldown bookkeeping, shared shape with the watchdog.
prune_failures() {
  local now tmp m ts
  [ -f "$FAIL_FILE" ] || return 0
  now=$(date +%s)
  tmp="$(mktemp)"
  while read -r m ts; do
    [ -n "${m:-}" ] || continue
    case "$ts" in ''|*[!0-9]*) continue ;; esac
    [ $(( now - ts )) -lt "$FAIL_TTL" ] && printf '%s %s\n' "$m" "$ts" >>"$tmp"
  done <"$FAIL_FILE"
  mv "$tmp" "$FAIL_FILE"
}
prune_failures

failed_recently() { # $1: model id
  # `m` MUST be local: a global would clobber the caller's ladder loop
  # variable (and an empty failure file blanks it via read's EOF).
  local model="$1" now ts m
  now=$(date +%s)
  [ -f "$FAIL_FILE" ] || return 1
  while read -r m ts; do
    [ "$m" = "$model" ] || continue
    [ $(( now - ts )) -lt "$COOLDOWN" ] && return 0
  done <"$FAIL_FILE"
  return 1
}

mark_failed() { printf '%s %s\n' "$1" "$(date +%s)" >>"$FAIL_FILE"; }

# Fallback run record (plan 5.1): counts and exit codes only, written even
# when the model forgets or dies. Absent runrecord.py (older installs) or
# any failure here must never affect the wake itself.
record_wake() { # $1 = exit code, $2 = duration s, $3 = model label
  ( cd "$REPO" && python3 scripts/autonomous/runrecord.py write \
    --source stateless-wake --autonomy auto --environment "${ORCH_ENVIRONMENT:-vm}" \
    --harness wake-launcher --model "${3:-}" \
    --outcome "$( [ "$1" -eq 0 ] && echo wake-complete || echo wake-failed )" \
    --exit-code "$1" --wall-s "$2" ) >>"$LOG" 2>&1 || true
}

# Error-line anchored quota signatures: a success that merely mentions
# limits must not park its model (issue #19 lesson, shared here).
ERROR_LINE_RE='^[[:space:]]*(Error|ERROR|error)\b|^[[:space:]]*HTTP[[:space:]]*429|^[[:space:]]*429[[:space:]]|curl: \(22\)|^[[:space:]]*[A-Za-z]+Error:'
QUOTA_RE='rate limit|rate_limit|429|quota|too many requests|insufficient balance|balance is at|exceeded your current|overloaded'
quota_failure() { grep -E "$ERROR_LINE_RE" "$1" 2>/dev/null | grep -Eiq "$QUOTA_RE"; }

PROMPT="$(cat "$PROMPT_FILE" 2>/dev/null || true)"
if [ -z "$PROMPT" ]; then
  PROMPT="Read references/05-unattended-operation.md §Stateless wake and run one wake: \
read state/*.json + the ledger + the journal tail, act on fleet.py needs-brain's reasons \
(fix/triage red PRs, drive green PRs to merge), then exit. Do not resume a session."
fi

candidates=()
if [ -n "$LADDER" ]; then
  for model_id in $LADDER; do
    failed_recently "$model_id" && continue
    candidates+=("$model_id")
  done
  if [ "${#candidates[@]}" -eq 0 ]; then
    echo "$(date -Is) every ladder model is in cooldown; trying the harness default once" >>"$LOG"
    candidates+=("")
  fi
else
  candidates+=("")
fi

echo "$(date -Is) DBG me=$0 sha=$(sha256sum "$0" | cut -c1-12) n=${#candidates[@]} first=[${candidates[0]:-NONE}]" >>"$LOG"
echo "$(date -Is) candidates: ${candidates[*]:-<harness-default>}" >>"$LOG"
OUT="$STATE_DIR/wake.out"
attempt=0
WAKE_START=$(date +%s)
for model in "${candidates[@]}"; do
  attempt=$(( attempt + 1 ))
  label="${model:-harness-default}"
  echo "$(date -Is) wake attempt $attempt on '$label'" >>"$LOG"
  timeout --signal=TERM --kill-after=60 "$NUDGE_TIMEOUT" \
    bash -c "$WAKE_CMD" _ "$PROMPT" "$model" >"$OUT" 2>&1
  RC=$?
  tail -c 800 "$OUT" >>"$LOG"
  if quota_failure "$OUT"; then
    echo "$(date -Is) model '$label' answered with a quota/balance failure; rotating" >>"$LOG"
    [ -n "$model" ] && mark_failed "$model"
    continue
  fi
  echo "$(date -Is) wake on '$label' finished with exit $RC; done" >>"$LOG"
  record_wake "$RC" "$(( $(date +%s) - WAKE_START ))" "$label"
  exit 0
done

# Total failure: nobody woke. Clear the recorded digest so the NEXT tick
# retries this same state instead of reading it as "already handled".
python3 - "$STATE_DIR/wake.json" <<'PY' 2>>"$LOG" || true
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
if path.is_file():
    data = json.loads(path.read_text(encoding="utf-8"))
    data["digest"] = None
    data["cleared_by"] = "stateless-wake total failure"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
echo "$(date -Is) every attempted model failed; digest cleared for retry; exiting 3 (total failure)" >>"$LOG"
record_wake 3 "$(( $(date +%s) - WAKE_START ))" ""
exit 3