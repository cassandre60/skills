#!/bin/bash
# Stateless wake launcher for the autonomous-improvement Orchestrator.
#
# Primary shape for unattended operation (references/05 §Stateless wake). A
# timer runs this script on cadence:
#   1. checks .autonomous/STOP — global kill-switch, exits 0 if present;
#   2. checks fleet doctor's disk headroom floor — exits 0 if exhausted;
#   3. `fleet.py needs-brain --json` in $ORCH_REPO — unchanged state means
#      it exits 0 (0 tokens spent, quiet hours stay free);
#   4. otherwise launch $ORCH_WAKE_CMD once, walking $ORCH_MODEL_LADDER on
#      quota failures (error-line anchored, same rule as the watchdog);
#   5. exit 0 on success; exit 3 on total failure (every ladder model failed)
#      so systemd OnFailure= / an owner notification can fire.
#
# Unlike the watchdog, this shape starts a FRESH headless run each wake:
#   * needs NO session id (the whole point of the stateless switch);
#   * creates NO runner processes when state has not changed;
#   * works with opencode run / Claude Code headless / Codex headless
#     (see fleet-ops/references/harness-adapters.md).
#
# Environment variables (all optional; sensible defaults apply):
#   ORCH_REPO          repo to wake in (default: $PWD)
#   ORCH_STATE_DIR     directory holding wake.log, wake.json, and the
#                      flock lockfile (default: $HOME/.local/share/orchestrator)
#   ORCH_FLEET_PY      explicit path to fleet.py (default: $ORCH_REPO/scripts/autonomous/fleet.py)
#   ORCH_MODEL_LADDER  space-separated model ids to try in order (default: one harness-default attempt)
#   ORCH_WAKE_CMD      bash snippet; $1 = prompt text, $2 = model id ("" = harness default)
#                      (default: opencode run ${2:+-m "$2"} "$1" — positional message; see the adapter row for why --auto is NOT in the default)
#   ORCH_PROMPT_FILE   file holding the prompt text (default: $ORCH_STATE_DIR/wake-prompt.md;
#                      if absent, a minimal default prompt is used)
#   ORCH_WAKE_TIMEOUT  seconds before the run is killed (default: 3600)
#   ORCH_MIN_INTERVAL  minimum seconds between launches (default: 0)
#   ORCH_MODEL_COOLDOWN / ORCH_FAILURE_TTL  shared semantics with the watchdog
#                      (wake_failures cooldown 3600s, prune older than 7 days)
#   ORCH_MASTER_SESSION_ID optional session ID for Master Cockpit status telemetry
#   ORCH_WORK_GATE     optional executable gate to allow wake on backlog work even if PR state unchanged
#
# Exit codes: 0 = quiet (unchanged state) or the harness run completed;
#             2 = misconfiguration (missing ORCH_REPO / predicate / a
#             configured ORCH_PROMPT_FILE that is unreadable or empty);
#             3 = total failure (every attempted model failed, quota or
#             otherwise) — wire systemd OnFailure= or an owner notification
#             to it. Any failed attempt clears the recorded digest on the way
#             out so the next tick retries the same state.
#
# Harness note: the ORCH_WAKE_CMD contract is the adapter row's one-shot
# form. Verified for opencode (`opencode run [-m model] "<prompt>"`;
# `--auto` is a top-level flag on builds that support it, never a `run`
# flag — see the adapter row; no turn/budget cap flag exists, so scope the
# prompt). Claude Code / Codex rows in
# fleet-ops/references/05 are unverified — set ORCH_WAKE_CMD after checking your
# installed version, and never pass a resume flag: this shape starts fresh.

set -u

STATE_DIR="${ORCH_STATE_DIR:-$HOME/.local/share/orchestrator}"
REPO="${ORCH_REPO:-$PWD}"
LOG="$STATE_DIR/wake.log"
FAIL_FILE="$STATE_DIR/wake_failures"
HB="$STATE_DIR/last_wake"
# The fleet script used for the predicate and the model pool (R-08: env
# override, never an edited script).
FLEET_PY="${ORCH_FLEET_PY:-$REPO/scripts/autonomous/fleet.py}"
# Ladder precedence: ORCH_MODEL_LADDER env > the recorded model pool
# (fleet.model_pool, free-first; issue #103) > empty (one harness-default
# attempt). A pool that fails to resolve degrades to empty, never blocks.
LADDER="${ORCH_MODEL_LADDER:-}"
if [ -z "$LADDER" ] && [ -f "$FLEET_PY" ]; then
  LADDER="$(cd "$REPO" && python3 "$FLEET_PY" pool ladder 2>>"$LOG" | tr '\n' ' ' || true)"
  [ -n "$LADDER" ] && echo "$(date -Is) ladder from the recorded model pool: [$LADDER]" >>"$LOG"
fi
NUDGE_TIMEOUT="${ORCH_WAKE_TIMEOUT:-3600}"
MIN_INTERVAL="${ORCH_MIN_INTERVAL:-0}"
COOLDOWN="${ORCH_MODEL_COOLDOWN:-3600}"
FAIL_TTL="${ORCH_FAILURE_TTL:-604800}"
PROMPT_FILE="${ORCH_PROMPT_FILE:-$STATE_DIR/wake-prompt.md}"
WAKE_CMD="${ORCH_WAKE_CMD:-opencode run \${2:+-m \"\$2\"} \"\$1\"}"

mkdir -p "$STATE_DIR"

notify_cockpit() { # $1: badge, $2: text
  local badge="$1"
  local text="${2:-}"
  local master="${ORCH_MASTER_SESSION_ID:-}"
  [ -n "$master" ] || return 0
  command -v opencode >/dev/null 2>&1 || return 0
  opencode api PATCH "/api/session/$master" -d "{\"title\": \"Master Cockpit [$badge]\"}" >/dev/null 2>&1 || true
  if [ -n "$text" ]; then
    local escaped
    escaped=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$text" 2>/dev/null || echo "\"$text\"")
    opencode api POST "/api/session/$master/synthetic" -d "{\"text\": $escaped, \"resume\": false}" >/dev/null 2>&1 || true
  fi
}

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

# 1a. Routine build artifact purge and worktree prune (reclaim space).
purge_build_artifacts() {
  for d in "$REPO"-*; do
    if [ -d "$d" ] && [ "$d" != "$REPO-*" ]; then
      rm -rf "$d/build" "$d/.dart_tool"
    fi
  done
  if [ -d "$REPO/.git" ] || git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "$REPO" worktree prune 2>/dev/null || true
  fi
}
purge_build_artifacts

# 1b. Disk headroom floor: never launch into an almost-full filesystem (issue #115).
MIN_FREE_GB="${ORCH_MIN_FREE_GB:-5}"
FREE_KB="$(df -k "$REPO" 2>/dev/null | awk 'NR==2 {print $4}')"
if [ -n "$FREE_KB" ] && [ "$FREE_KB" -gt 0 ] 2>/dev/null; then
  FREE_GB=$(( FREE_KB / 1024 / 1024 ))
  if [ "$FREE_GB" -lt "$MIN_FREE_GB" ]; then
    echo "$(date -Is) disk headroom exhausted (${FREE_GB} GiB free < ${MIN_FREE_GB} GiB floor); not launching a wake" >>"$LOG"
    exit 0
  fi
fi

# 1c. Concurrent worktree cap (issue #115: prevent unbounded worktree buildup).
MAX_WORKTREES="${ORCH_MAX_WORKTREES:-5}"
if [ -d "$REPO/.git" ] || git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  git -C "$REPO" worktree prune 2>/dev/null || true
  WT_COUNT="$(git -C "$REPO" worktree list --porcelain 2>/dev/null | grep -c '^worktree ' || echo 0)"
  if [ "$WT_COUNT" -gt "$MAX_WORKTREES" ]; then
    echo "$(date -Is) active worktrees ($WT_COUNT) exceed max_worktrees ($MAX_WORKTREES); not launching a wake" >>"$LOG"
    exit 0
  fi
fi

PREDICATE="$FLEET_PY"
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
  # PR state can be frozen for days while the owner is away (open PRs are
  # handled later). The digest alone would park the factory forever, so a
  # configured work-gate may still wake it when the backlog has a free target.
  if [ -n "${ORCH_WORK_GATE:-}" ] && [ -x "$ORCH_WORK_GATE" ] && "$ORCH_WORK_GATE" >>"$LOG" 2>&1; then
    echo "$(date -Is) work-gate: backlog work available; waking despite unchanged PR state" >>"$LOG"
  else
    echo "$(date -Is) quiet: state unchanged since the last wake (0 tokens)" >>"$LOG"
    notify_cockpit "💤 Idle" ""
    exit 0
  fi
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
# ANSI escape sequences are stripped before matching (issue #109): opencode
# prints errors in color, and the escape prefix breaks the ^ anchor.
ERROR_LINE_RE='^[[:space:]]*(Error|ERROR|error)\b|^[[:space:]]*HTTP[[:space:]]*429|^[[:space:]]*429[[:space:]]|curl: \(22\)|^[[:space:]]*[A-Za-z]+Error:'
QUOTA_RE='rate limit|rate_limit|429|quota|too many requests|insufficient balance|balance is at|exceeded your current|overloaded|usage limit|usage credits|subscribe and save|model is unavailable|is no longer available|temporarily unavailable'
quota_failure() {
  sed -E 's/\x1B\[[0-9;]*[a-zA-Z]//g' "$1" 2>/dev/null \
    | grep -E "$ERROR_LINE_RE" \
    | grep -Eiq "$QUOTA_RE"
}
DAILY_LIMIT_RE='usage limit|model is unavailable'
daily_limit_failure() {
  sed -E 's/\x1B\[[0-9;]*[a-zA-Z]//g' "$1" 2>/dev/null \
    | grep -E "$ERROR_LINE_RE" \
    | grep -Eiq "$DAILY_LIMIT_RE"
}

# Provider-level cooldown (issue #109): when one opencode/* free model hits
# the daily limit, all models sharing that provider prefix are exhausted too.
# Probing each one individually just creates dead sessions.
mark_provider_failed() { # $1: model id that failed
  local provider="${1%%/*}"
  local m
  for m in $LADDER; do
    case "$m" in "$provider"/*) mark_failed "$m" ;; esac
  done
}

# A prompt the OPERATOR pointed at (ORCH_PROMPT_FILE set) must be readable and
# non-empty: launching a doomed run would otherwise be recorded as a completed
# wake and park the factory (issues #97/#98). The built-in fallback applies
# only when no file was named.
if [ -n "${ORCH_PROMPT_FILE:-}" ]; then
  PROMPT="$(cat "$PROMPT_FILE" 2>/dev/null || true)"
  if [ -z "$PROMPT" ]; then
    echo "[!] ORCH_PROMPT_FILE is set but unreadable or empty: $PROMPT_FILE" >&2
    echo "$(date -Is) misconfiguration: ORCH_PROMPT_FILE unreadable/empty ($PROMPT_FILE); no launch" >>"$LOG"
    exit 2
  fi
else
  PROMPT="$(cat "$PROMPT_FILE" 2>/dev/null || true)"
  if [ -z "$PROMPT" ]; then
    PROMPT="Read fleet-ops/references/05-unattended-operation.md §Stateless wake and run one wake: \
read state/*.json + the ledger + the journal tail, act on fleet.py needs-brain's reasons \
(fix/triage red PRs, drive green PRs to merge), then exit. Do not resume a session."
  fi
fi

candidates=()
if [ -n "$LADDER" ]; then
  for model_id in $LADDER; do
    failed_recently "$model_id" && continue
    candidates+=("$model_id")
  done
  if [ "${#candidates[@]}" -eq 0 ]; then
    echo "$(date -Is) every ladder model is in cooldown; staying quiet to preserve account quota." >>"$LOG"
    notify_cockpit "⚠️ Cooldown" "Every ladder model is in cooldown; staying quiet."
    exit 0
  fi
else
  candidates+=("")
fi

echo "$(date -Is) DBG me=$0 sha=$(sha256sum "$0" | cut -c1-12) n=${#candidates[@]} first=[${candidates[0]:-NONE}]" >>"$LOG"
echo "$(date -Is) candidates: ${candidates[*]:-<harness-default>}" >>"$LOG"
OUT="$STATE_DIR/wake.out"
# The harness must start inside the repository: AGENTS.md, project config and
# the session project namespace are cwd-derived. Without this the wake lands
# in $HOME's project, so the owner's ChessSRS TUI never shows the tab.
cd "$REPO" || { echo "$(date -Is) repo missing: $REPO; no launch" >>"$LOG"; exit 2; }
echo "$(date -Is) launch cwd=$(pwd)" >>"$LOG"
attempt=0
WAKE_START=$(date +%s)
for model in "${candidates[@]}"; do
  attempt=$(( attempt + 1 ))
  label="${model:-harness-default}"
  # Skip models whose provider was marked failed earlier in THIS tick
  # (provider-level cooldown writes to FAIL_FILE during the loop).
  [ -n "$model" ] && failed_recently "$model" && {
    echo "$(date -Is) skipping '$label' (provider already cooled down this tick)" >>"$LOG"
    continue
  }
  echo "$(date -Is) wake attempt $attempt on '$label'" >>"$LOG"
  notify_cockpit "⏳ $label" "🚀 **Wake Started**: Attempting run on \`$label\`."
  timeout --signal=TERM --kill-after=60 "$NUDGE_TIMEOUT" \
    bash -c "$WAKE_CMD" _ "$PROMPT" "$model" >"$OUT" 2>&1
  RC=$?
  tail -c 800 "$OUT" >>"$LOG"
  if [ "$RC" -ne 0 ]; then
    if quota_failure "$OUT"; then
      echo "$(date -Is) model '$label' answered with a quota/balance failure; rotating" >>"$LOG"
      notify_cockpit "⚠️ Quota: $label" "⚠️ **Quota Limit**: Model \`$label\` returned quota/balance failure; rotating."
      if daily_limit_failure "$OUT"; then
        echo "$(date -Is) model '$label' exceeded DAILY limit; marking provider failed and backing off until 23:59" >>"$LOG"
        tomorrow=$(python3 -c 'import datetime; now=datetime.datetime.now(); tomorrow=now.replace(hour=0,minute=0,second=0,microsecond=0)+datetime.timedelta(days=1); print(int(tomorrow.timestamp()))')
        echo "$tomorrow" > "$STATE_DIR/daily_exhausted_until"
        [ -n "$model" ] && mark_provider_failed "$model"
      else
        [ -n "$model" ] && mark_provider_failed "$model"
      fi
      # Session hygiene: a failed opencode run creates a session that we don't
      # want to linger in the owner's session list (three messages, then dead).
      if command -v opencode >/dev/null 2>&1; then
        RECENTID=$(python3 -c "import sqlite3, time; p='$STATE_DIR'+'/w.db'; import os; db_path=os.environ.get('ORCH_SESDB', '/home/ubuntu/.local/share/opencode/opencode.db'); db=sqlite3.connect('file:'+db_path+'?mode=ro', uri=True); rows=db.execute('select id from session_v2 order by time_created desc limit 1').fetchall(); print(rows[0][0] if rows else '')" || true)
        if [ -n "$RECENTID" ]; then
          # Cleanup only sessions created in the last 2 minutes
          if python3 -c "import sqlite3, time, sys; sys.exit(0 if (lambda d: True)(0))" >/dev/null 2>&1; then
            opencode session delete "$RECENTID" >/dev/null 2>&1 || true
          fi
        fi
      fi
    else
      echo "$(date -Is) wake on '$label' failed with exit $RC (non-quota); rotating" >>"$LOG"
      # A timeout (124) means the model endpoint hung; cool it down so the
      # next tick does not waste another 30 minutes on the same dead endpoint.
      if [ "$RC" -eq 124 ] && [ -n "$model" ]; then
        mark_failed "$model"
      fi
    fi
    continue
  fi
  echo "$(date -Is) wake on '$label' finished; done" >>"$LOG"
  notify_cockpit "🟢 Done" "✅ **Wake Finished**: Run on \`$label\` completed successfully."
  purge_build_artifacts
  record_wake 0 "$(( $(date +%s) - WAKE_START ))" "$label"
  exit 0
done

purge_build_artifacts

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
notify_cockpit "🚨 Blocked" "🚨 **Wake Blocked**: Every attempted model failed or hit limits."
record_wake 3 "$(( $(date +%s) - WAKE_START ))" ""
exit 3
