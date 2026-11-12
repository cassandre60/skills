#!/bin/bash
# Behavioural test for fleet-ops/assets/stateless-wake.sh — the timer-driven
# launcher.
#
# The claim under test (issue #18): a stateless wake starts a FRESH headless
# run per wake; it consults `fleet.py needs-brain` first so an unchanged
# state costs ZERO tokens; it needs NO session-id file; it walks the model
# ladder on error-line-anchored quota failures; and a tick that ends in
# total failure exits 3 so systemd OnFailure=/owner notification can fire.
#
# Run: bash fleet-ops/tests/stateless_wake_test.sh
set -u
export ORCH_MIN_FREE_GB="${ORCH_MIN_FREE_GB:-0}"

# This test lives at fleet-ops/tests/, one level deeper than the skill's own
# tests: fleet-ops/ holds the elective keep-alive machinery. FLEET_OPS is the
# machinery folder (its assets/), SKILL_ROOT the skill root (its
# scripts/autonomous and, in case 6, the repo a hermetic wake runs in).
FLEET_OPS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ASSETS="$FLEET_OPS/assets/stateless-wake.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/state"
FAILED=0

fail() { echo "FAIL: $1"; FAILED=1; }
ok() { echo "ok: $1"; }

# Stub gh: serves the PR list needs-brain reads (GH_JSON).
cat >"$TMP/bin/gh" <<'EOF'
#!/bin/sh
while IFS= read -r line; do printf '%s\n' "$line"; done < "$GH_JSON"
EOF
chmod +x "$TMP/bin/gh"

# Stub harness CLI: records the prompt + model it was handed, and answers
# according to the model name (quota fail vs success).
cat >"$TMP/bin/fake-harness" <<'EOF'
#!/bin/bash
echo "ARGS: $*" >> "$WAKE_LOG"
case "$*" in
  *quota-free*) echo "Error: Rate limit exceeded. Please try again later." ; exit 1 ;;
  *mention-free*) echo "wake: journal summary — rate limit hit twice yesterday" ; exit 0 ;;
  *parse-error*) echo "Error: You must provide a message" ; exit 1 ;;
  *ansi-free-limit*) printf '\x1b[91m\x1b[1mError: \x1b[0mFree usage limit reached. Connect a provider or subscribe to OpenCode Go to keep going, or switch models.\n' ; exit 1 ;;
  *model-unavailable*) printf '\x1b[91m\x1b[1mError: \x1b[0mUpstream request failed: Model is unavailable.\n' ; exit 1 ;;
  *timeout-model*) exit 124 ;;
  *usage-credits*) printf '\x1b[91m\x1b[1mError: \x1b[0mAdd usage credits or subscribe and save up to 66%%: https://ollama.com/plans\n' ; exit 1 ;;
  *garbage-free*) printf '> build · free\n<tool_call> garbled output\n' ; exit 0 ;;
  *) echo "wake: housekeeping done" ; exit 0 ;;
esac
EOF
chmod +x "$TMP/bin/fake-harness"

# PR fixtures: one red PR (a wake is warranted).
printf '[{"number": 7, "headRefName": "task/pr-7", "headRefOid": "sha-7",
  "statusCheckRollup": [{"name": "unit-tests", "status": "COMPLETED",
  "conclusion": "FAILURE"}]}]\n' >"$TMP/prs-red.json"
printf '[]\n' >"$TMP/prs-clean.json"

run_wake() { # $1 = PR fixture file, $2 = ladder
  rm -f "$TMP/state/last_wake" "$TMP/state/watchdog.log" "$TMP/state/daily_exhausted_until" "$TMP/state/wake_failures"
  # wake.json is removed explicitly per case; run_wake does not wipe the quiet
  # digest or case 2 would never see "unchanged" state.
  PATH="$TMP/bin:$PATH" \
    ORCH_REPO="$SKILL_ROOT" \
    ORCH_STATE_DIR="$TMP/state" \
    ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
    ORCH_MODEL_LADDER="$2" \
    ORCH_WAKE_TIMEOUT=20 \
    ORCH_MIN_INTERVAL=0 \
    GH_JSON="$1" \
    bash "$ASSETS" >/dev/null 2>&1
}

# --- case 1: a changed state starts a fresh run --------------------------
export WAKE_LOG="$TMP/wake.log"
: >"$WAKE_LOG"
run_wake "$TMP/prs-red.json" "opencode/alpha-free"
RC=$?
if [ "$RC" -eq 0 ]; then ok "wake on changed state exits 0"; else fail "wake exited $RC"; fi
if grep -q "ARGS:" "$WAKE_LOG" 2>/dev/null; then
  ok "the harness CLI was launched"
else
  fail "no fresh harness run happened"
fi
if grep -q "opencode/alpha-free" "$WAKE_LOG" 2>/dev/null; then
  ok "the ladder model reached the harness"
else
  fail "the ladder model did not reach the harness"
fi
if [ ! -f "$TMP/state/opencode_session_id" ]; then
  ok "no session-id file is involved in a stateless wake"
else
  fail "a session-id file was created (that is the shape this replaces)"
fi

# --- case 2: unchanged state costs ZERO tokens ---------------------------
: >"$WAKE_LOG"
run_wake "$TMP/prs-red.json" "opencode/alpha-free"   # same state again
RC=$?
if [ ! -s "$WAKE_LOG" ]; then
  ok "second wake on unchanged state launched nothing (0 tokens)"
else
  fail "a second wake on unchanged state still spent a model turn"
fi
if [ "$RC" -eq 0 ]; then ok "quiet wake exits 0"; else fail "quiet wake exited $RC"; fi

# --- case 3: changed state again (PR went green) -------------------------
printf '[{"number": 7, "headRefName": "task/pr-7", "headRefOid": "sha-7",
  "statusCheckRollup": [{"name": "unit-tests", "status": "COMPLETED",
  "conclusion": "SUCCESS"}]}]\n' >"$TMP/prs-green.json"
: >"$WAKE_LOG"
run_wake "$TMP/prs-green.json" "opencode/alpha-free"
if [ -s "$WAKE_LOG" ]; then
  ok "a changed state reopens the wake"
else
  fail "a changed state did not reopen the wake"
fi

# --- case 4: quota failure rotates, all-fail exits 3 --------------------
: >"$WAKE_LOG"
: >"$TMP/state/wake_failures"
printf '[]\n' >"$TMP/prs-clean.json"
run_wake "$TMP/prs-clean.json" "opencode/quota-free opencode/quota-free"
RC=$?
if [ "$RC" -eq 3 ]; then ok "total failure exits 3 (distress)"; else fail "total failure exited $RC (expected 3)"; fi
if grep -q '^opencode/quota-free ' "$TMP/state/wake_failures" 2>/dev/null; then
  ok "failing model recorded for cooldown"
else
  fail "failing model was not recorded"
fi

# --- case 5: a success that MENTIONS limits parks nobody ----------------
: >"$WAKE_LOG"
rm -f "$TMP/state/wake_failures"
run_wake "$TMP/prs-red.json" "opencode/mention-free"
RC=$?
if [ "$RC" -eq 0 ]; then ok "mention-only success exits 0"; else fail "mention-only success exited $RC"; fi
if [ -s "$TMP/state/wake_failures" ]; then
  fail "mention-only output parked a model"
else
  ok "a success that mentions limits parks nobody"
fi

# --- case 6: the launcher writes a fallback run record --------------------
# Hermetic repo (not SKILL_ROOT): scripts installed, gh stub shows a red PR
# so needs-brain says yes, the wake succeeds — a run-record/1 JSON with
# source stateless-wake and the exit code must exist even if the model
# itself never writes one.
WAKE_REPO="$TMP/rr-repo"
mkdir -p "$WAKE_REPO/scripts"
cp -r "$SKILL_ROOT/scripts/autonomous" "$WAKE_REPO/scripts/autonomous"
( cd "$WAKE_REPO" && git init -q -b main && git add -A \
  && GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t \
     git commit -qm base ) >/dev/null 2>&1
printf '[{"number": 7, "headRefName": "task/pr-7", "headRefOid": "sha-7",
  "statusCheckRollup": [{"name": "unit-tests", "status": "COMPLETED",
  "conclusion": "FAILURE"}]}]\n' >"$TMP/prs-rr.json"
mkdir -p "$TMP/state-rr"   # fresh digest: earlier cases already digested the same PR shape
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$WAKE_REPO" \
  ORCH_STATE_DIR="$TMP/state-rr" \
  ORCH_WAKE_CMD='true' \
  ORCH_MODEL_LADDER="opencode/alpha-free" \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-rr.json" \
  bash "$ASSETS" >/dev/null 2>&1
RC=$?
if [ "$RC" -eq 0 ]; then ok "hermetic wake exits 0"; else fail "hermetic wake exited $RC"; fi
RECORD="$(ls "$WAKE_REPO/.autonomous/run-records/"rr-*.json 2>/dev/null | head -1)"
if [ -n "$RECORD" ]; then
  ok "the launcher wrote a fallback run record"
  if grep -q '"stateless-wake"' "$RECORD" && grep -q '"wake-complete"' "$RECORD" \
     && grep -q '"exit_code": 0' "$RECORD"; then
    ok "record carries source/outcome/exit-code"
  else
    fail "record fields wrong: $(cat "$RECORD")"
  fi
else
  fail "no run record appeared under .autonomous/run-records/"
fi


# --- case 7: a configured-but-missing prompt file is a loud misconfig ------
# (issue #98: a fleet timer raced the config writer and every tick launched a
# doomed run with an empty message, recorded as handled.)
: >"$WAKE_LOG"
rm -f "$TMP/state/wake.json"
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$SKILL_ROOT" \
  ORCH_STATE_DIR="$TMP/state" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_PROMPT_FILE="$TMP/state/no-such-prompt.md" \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-red.json" \
  bash "$ASSETS" >/dev/null 2>&1
RC=$?
if [ "$RC" -eq 2 ]; then ok "a configured-but-missing prompt file exits 2 (misconfig)"; else fail "missing prompt file exited $RC (expected 2)"; fi
if grep -q "ARGS:" "$WAKE_LOG" 2>/dev/null; then
  fail "a wake was launched with no prompt file present"
else
  ok "no launch attempt happened without a prompt"
fi

# --- case 8: a non-quota harness failure retries via the total-failure lane -
# (issue #97: exit 1 from the harness was recorded as done — exit 0, digest
# kept — so a broken launcher parked the factory silently for hours.)
: >"$WAKE_LOG"
rm -f "$TMP/state/wake.json" "$TMP/state/wake_failures"
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$SKILL_ROOT" \
  ORCH_STATE_DIR="$TMP/state" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_MODEL_LADDER="opencode/parse-error" \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-red.json" \
  bash "$ASSETS" >/dev/null 2>&1
RC=$?
if [ "$RC" -eq 3 ]; then ok "a non-quota harness failure exits 3 (total failure)"; else fail "non-quota harness failure exited $RC (expected 3)"; fi
if [ -f "$TMP/state/wake.json" ] && grep -q '"cleared_by"' "$TMP/state/wake.json" 2>/dev/null; then
  ok "the digest was cleared so the next tick retries the same state"
else
  fail "the digest survived a failed wake (next tick would go quiet)"
fi
if [ -s "$TMP/state/wake_failures" ]; then
  fail "a non-quota failure parked the model in cooldown"
else
  ok "a non-quota failure parks nobody"
fi

# --- case 9: with no env ladder, the recorded model pool feeds the ladder ---
# (issue #103: the pool is the owner's recorded spend surface; the launcher
# derives the ladder from it when ORCH_MODEL_LADDER is unset, and the env
# still wins when set. The python3 wrapper stubs `pool ladder` and passes
# everything else (the needs-brain predicate) to the real interpreter.)
cat >"$TMP/bin/python3" <<'EOF'
#!/bin/bash
case "$*" in
  *"pool ladder"*) echo "opencode/pool-free" ;;
  *) exec /usr/bin/python3 "$@" ;;
esac
EOF
chmod +x "$TMP/bin/python3"
: >"$WAKE_LOG"
rm -f "$TMP/state/wake.json"
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$SKILL_ROOT" \
  ORCH_STATE_DIR="$TMP/state" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-red.json" \
  bash "$ASSETS" >/dev/null 2>&1
if grep -q -- "-m opencode/pool-free" "$WAKE_LOG" 2>/dev/null; then
  ok "with no env ladder, the recorded model pool feeds the harness"
else
  fail "the recorded model pool did not reach the harness"
fi
: >"$WAKE_LOG"
rm -f "$TMP/state/wake.json"
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$SKILL_ROOT" \
  ORCH_STATE_DIR="$TMP/state" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_MODEL_LADDER="opencode/env-first" \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-red.json" \
  bash "$ASSETS" >/dev/null 2>&1
if grep -q -- "-m opencode/env-first" "$WAKE_LOG" 2>/dev/null && ! grep -q -- "pool-free" "$WAKE_LOG" 2>/dev/null; then
  ok "an explicit ORCH_MODEL_LADDER still wins over the pool"
else
  fail "the env ladder did not take precedence over the recorded pool"
fi
rm -f "$TMP/bin/python3"

# --- case 10: ANSI-escaped free usage limit rotates per-model without parking live siblings (issue #164) ---
: >"$WAKE_LOG"
rm -f "$TMP/state/wake.json" "$TMP/state/wake_failures" "$TMP/state/cockpit-events.log"
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$SKILL_ROOT" \
  ORCH_STATE_DIR="$TMP/state" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_MODEL_LADDER="opencode/beam-free-ansi-free-limit opencode/other-free" \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-red.json" \
  bash "$ASSETS" >/dev/null 2>&1
RC=$?
if [ "$RC" -eq 0 ] && grep -q '^opencode/beam-free-ansi-free-limit ' "$TMP/state/wake_failures" 2>/dev/null; then
  ok "ANSI-colored free limit error recorded in wake_failures"
else
  fail "ANSI-colored free limit error was NOT recorded in wake_failures"
fi
if ! grep -q '^opencode/other-free ' "$TMP/state/wake_failures" 2>/dev/null \
   && grep -q -- "-m opencode/other-free" "$WAKE_LOG" 2>/dev/null; then
  ok "sibling opencode/* free model rotated cleanly and succeeded without provider-wide ban"
else
  fail "sibling opencode/* free model was falsely parked or skipped"
fi
if grep -q 'Wake finished cleanly on opencode/other-free' "$TMP/state/cockpit-events.log" 2>/dev/null; then
  ok "cockpit-events.log recorded wake completion without synthetic inbox spam"
else
  fail "cockpit-events.log did NOT record completion event"
fi

# --- case 11: upstream model unavailable is recognized as quota/availability failure (issue #109) ---
: >"$WAKE_LOG"
rm -f "$TMP/state/wake.json" "$TMP/state/wake_failures" "$TMP/state/daily_exhausted_until"
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$SKILL_ROOT" \
  ORCH_STATE_DIR="$TMP/state" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_MODEL_LADDER="opencode/nemotron-model-unavailable" \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-red.json" \
  bash "$ASSETS" >/dev/null 2>&1
if grep -q '^opencode/nemotron-model-unavailable ' "$TMP/state/wake_failures" 2>/dev/null; then
  ok "upstream unavailable model recorded in wake_failures"
else
  fail "upstream unavailable model was NOT recorded in wake_failures"
fi

# --- case 12: disk headroom below floor stays quiet (0 tokens, no launch) (issue #115) ---
: >"$WAKE_LOG"
rm -f "$TMP/state/wake.json" "$TMP/state/wake_failures"
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$SKILL_ROOT" \
  ORCH_STATE_DIR="$TMP/state" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_MODEL_LADDER="opencode/alpha-free" \
  ORCH_MIN_FREE_GB=999999 \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-red.json" \
  bash "$ASSETS" >/dev/null 2>&1
RC=$?
if [ "$RC" -eq 0 ] && ! grep -q "ARGS:" "$WAKE_LOG" 2>/dev/null && grep -qi "disk headroom exhausted" "$TMP/state/wake.log" 2>/dev/null; then
  ok "disk headroom below floor stays quiet and does not launch"
else
  fail "disk headroom floor did NOT prevent wake launch"
fi

# --- case 13: worktrees exceeding max_worktrees stays quiet (0 tokens, no launch) (issue #115) ---
: >"$WAKE_LOG"
rm -f "$TMP/state/wake.json" "$TMP/state/wake_failures"
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$SKILL_ROOT" \
  ORCH_STATE_DIR="$TMP/state" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_MODEL_LADDER="opencode/alpha-free" \
  ORCH_MAX_WORKTREES=0 \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-red.json" \
  bash "$ASSETS" >/dev/null 2>&1
RC=$?
if [ "$RC" -eq 0 ] && ! grep -q "ARGS:" "$WAKE_LOG" 2>/dev/null && grep -qi "active worktrees .* exceed max_worktrees" "$TMP/state/wake.log" 2>/dev/null; then
  ok "worktree cap prevents wake launch when exceeded"
else
  fail "worktree cap did NOT prevent wake launch"
fi

# --- case 14: exit 124 timeout records model for cooldown (issue #162) ---
: >"$WAKE_LOG"
rm -f "$TMP/state/wake.json" "$TMP/state/wake_failures"
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$SKILL_ROOT" \
  ORCH_STATE_DIR="$TMP/state" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_MODEL_LADDER="provider/timeout-model provider/mention-free" \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-red.json" \
  bash "$ASSETS" >/dev/null 2>&1
RC=$?
if [ "$RC" -eq 0 ] && grep -q "provider/timeout-model" "$TMP/state/wake_failures" 2>/dev/null; then
  ok "exit 124 timeout records model for cooldown in wake_failures"
else
  fail "exit 124 timeout was NOT recorded in wake_failures"
fi

# --- case 15: usage-credits error triggers quota rotation (issue #162) ---
: >"$WAKE_LOG"
rm -f "$TMP/state/wake.json" "$TMP/state/wake_failures"
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$SKILL_ROOT" \
  ORCH_STATE_DIR="$TMP/state" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_MODEL_LADDER="ollama/usage-credits provider/mention-free" \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-red.json" \
  bash "$ASSETS" >/dev/null 2>&1
RC=$?
if [ "$RC" -eq 0 ] && grep -q "ollama/usage-credits" "$TMP/state/wake_failures" 2>/dev/null; then
  ok "usage-credits error triggers quota rotation and records in wake_failures"
else
  fail "usage-credits error was NOT rotated or recorded in wake_failures"
fi

# --- case 16: exit 124 timeout uses transient ORCH_TIMEOUT_COOLDOWN, not 24h ORCH_MODEL_COOLDOWN (issue #164) ---
: >"$WAKE_LOG"
rm -f "$TMP/state/wake.json" "$TMP/state/wake_failures"
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$SKILL_ROOT" \
  ORCH_STATE_DIR="$TMP/state" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_MODEL_LADDER="provider/timeout-model provider/mention-free" \
  ORCH_MODEL_COOLDOWN=86400 \
  ORCH_TIMEOUT_COOLDOWN=1 \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-red.json" \
  bash "$ASSETS" >/dev/null 2>&1
sleep 2
: >"$WAKE_LOG"
rm -f "$TMP/state/wake.json"
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$SKILL_ROOT" \
  ORCH_STATE_DIR="$TMP/state" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_MODEL_LADDER="provider/timeout-model provider/mention-free" \
  ORCH_MODEL_COOLDOWN=86400 \
  ORCH_TIMEOUT_COOLDOWN=1 \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-red.json" \
  bash "$ASSETS" >/dev/null 2>&1
if grep -q -- "-m provider/timeout-model" "$WAKE_LOG" 2>/dev/null; then
  ok "exit 124 transient timeout cooldown expired independently of 24h quota cooldown"
else
  fail "exit 124 timeout stayed parked under 24h quota cooldown"
fi

# --- case 16b: exit 0 with leaked <tool_call> garbage output cools model and rotates (P1-4) ---
: >"$WAKE_LOG"
rm -f "$TMP/state/wake.json" "$TMP/state/wake_failures"
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$SKILL_ROOT" \
  ORCH_STATE_DIR="$TMP/state" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_MODEL_LADDER="openrouter/garbage-free opencode/mention-free" \
  ORCH_TIMEOUT_COOLDOWN=60 \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-red.json" \
  bash "$ASSETS" >/dev/null 2>&1
RC=$?
if [ "$RC" -eq 0 ] && grep -q "openrouter/garbage-free" "$TMP/state/wake_failures" 2>/dev/null && grep -q "opencode/mention-free" "$WAKE_LOG" 2>/dev/null; then
  ok "exit 0 leaked <tool_call> garbage rotated to next model and cooled broken model"
else
  fail "exit 0 leaked <tool_call> garbage was NOT rotated or cooled"
fi

# --- case 17: ORCH_WAKE_ON_BACKLOG=1 wakes via built-in work-gate and passes multi-track prompt (issue #167) ---
: >"$WAKE_LOG"
rm -f "$TMP/state/wake.json" "$TMP/state/wake_failures"
MT_REPO="$TMP/mt-repo"
mkdir -p "$MT_REPO/scripts" "$MT_REPO/src" "$MT_REPO/tests"
cp -r "$SKILL_ROOT/scripts/autonomous" "$MT_REPO/scripts/autonomous"
cat >"$MT_REPO/src/untested_mod.py" <<'EOF'
def compute(x):
    return x + 1
EOF
( cd "$MT_REPO" && git init -q -b main && git add -A \
  && GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t \
     git commit -qm base ) >/dev/null 2>&1
# First wake digests empty PRs; clear WAKE_LOG, then second wake on unchanged PR state
# must STILL wake because ORCH_WAKE_ON_BACKLOG=1 triggers multi-track `fleet.py work-gate`.
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$MT_REPO" \
  ORCH_STATE_DIR="$TMP/state-mt" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_MODEL_LADDER="opencode/mention-free" \
  ORCH_WAKE_ON_BACKLOG=1 \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-clean.json" \
  bash "$ASSETS" >/dev/null 2>&1
: >"$WAKE_LOG"
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$MT_REPO" \
  ORCH_STATE_DIR="$TMP/state-mt" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_MODEL_LADDER="opencode/mention-free" \
  ORCH_WAKE_ON_BACKLOG=1 \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-clean.json" \
  bash "$ASSETS" >/dev/null 2>&1
if grep -q "survey.py --json" "$WAKE_LOG" 2>/dev/null && grep -q "backlog.py harvest" "$WAKE_LOG" 2>/dev/null; then
  ok "ORCH_WAKE_ON_BACKLOG=1 woke on unchanged PR state with multi-track survey+backlog prompt"
else
  fail "ORCH_WAKE_ON_BACKLOG=1 did NOT wake or lacked multi-track prompt"
fi
: >"$WAKE_LOG"
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$MT_REPO" \
  ORCH_STATE_DIR="$TMP/state-mt" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_MODEL_LADDER="opencode/mention-free" \
  ORCH_WAKE_ON_BACKLOG=1 \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-clean.json" \
  bash "$ASSETS" >/dev/null 2>&1
if [ ! -s "$WAKE_LOG" ]; then
  ok "ORCH_WAKE_ON_BACKLOG=1 stayed quiet on third tick when backlog digest unchanged"
else
  fail "ORCH_WAKE_ON_BACKLOG=1 spun again on third tick despite unchanged backlog state"
fi

# --- case 18: update-skills.sh syncs from origin/main, updates wake.sh, and rolls back on failure (issue #167) ---
UPD_ORIGIN="$TMP/skills-origin.git"
UPD_CLONE="$TMP/skills-clone"
UPD_INSTALL="$TMP/installed-skills"
UPD_ORCH="$TMP/installed-orch"
git init -q --bare -b main "$UPD_ORIGIN"
mkdir -p "$TMP/seed/skills/autonomous-improvement/fleet-ops/assets" "$TMP/seed/scripts"
cat >"$TMP/seed/scripts/check_skills.py" <<'EOF'
import sys
sys.exit(0)
EOF
mkdir -p "$TMP/seed/skills/autonomous-improvement/tests" "$TMP/seed/skills/autonomous-improvement/fleet-ops/tests"
cat >"$TMP/seed/skills/autonomous-improvement/tests/test_ok.py" <<'EOF'
import unittest
class T(unittest.TestCase):
    def test_ok(self): self.assertTrue(True)
EOF
cat >"$TMP/seed/skills/autonomous-improvement/fleet-ops/tests/stateless_wake_test.sh" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x "$TMP/seed/skills/autonomous-improvement/fleet-ops/tests/stateless_wake_test.sh"
cp "$FLEET_OPS/assets/stateless-wake.sh" "$TMP/seed/skills/autonomous-improvement/fleet-ops/assets/stateless-wake.sh"
cp "$FLEET_OPS/assets/update-skills.sh" "$TMP/seed/skills/autonomous-improvement/fleet-ops/assets/update-skills.sh"
cp "$FLEET_OPS/assets/snapshot.sh" "$TMP/seed/skills/autonomous-improvement/fleet-ops/assets/snapshot.sh"
cp "$FLEET_OPS/assets/watchdog.sh" "$TMP/seed/skills/autonomous-improvement/fleet-ops/assets/watchdog.sh"
cp "$FLEET_OPS/assets/wake.sh" "$TMP/seed/skills/autonomous-improvement/fleet-ops/assets/wake.sh"
( cd "$TMP/seed" && git init -q -b main && git add -A \
  && GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t git commit -qm v1 \
  && git remote add origin "$UPD_ORIGIN" && git push -q -u origin main ) >/dev/null 2>&1
git clone -q "$UPD_ORIGIN" "$UPD_CLONE"
mkdir -p "$UPD_ORCH"
# Pre-install stateless wake.sh so update-skills.sh detects stateless mode
cp "$TMP/seed/skills/autonomous-improvement/fleet-ops/assets/stateless-wake.sh" "$UPD_ORCH/wake.sh"
# Push v2 upstream
echo "# v2-marker" >> "$TMP/seed/skills/autonomous-improvement/fleet-ops/assets/stateless-wake.sh"
( cd "$TMP/seed" && git add -A && GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t git commit -qm v2 && git push -q origin main ) >/dev/null 2>&1

ORCH_SKILLS_CLONE="$UPD_CLONE" \
ORCH_SKILLS_INSTALL_DIR="$UPD_INSTALL" \
ORCH_INSTALL_DIR="$UPD_ORCH" \
ORCH_STATE_DIR="$TMP/upd-state" \
ORCH_SYSTEMD_USER_DIR="$TMP/no-systemd" \
  bash "$FLEET_OPS/assets/update-skills.sh" >/dev/null 2>&1
if grep -q "v2-marker" "$UPD_ORCH/wake.sh" 2>/dev/null && grep -q "v2-marker" "$UPD_INSTALL/autonomous-improvement/fleet-ops/assets/stateless-wake.sh" 2>/dev/null; then
  ok "update-skills.sh synced v2 skills and updated stateless wake.sh"
else
  fail "update-skills.sh did NOT sync v2 skills or wake.sh"
fi

# Push broken v3 -> check_skills.py fails -> clone must roll back and wake.sh stay at v2
cat >"$TMP/seed/scripts/check_skills.py" <<'EOF'
import sys
sys.exit(1)
EOF
echo "# v3-broken" >> "$TMP/seed/skills/autonomous-improvement/fleet-ops/assets/stateless-wake.sh"
( cd "$TMP/seed" && git add -A && GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t git commit -qm v3 && git push -q origin main ) >/dev/null 2>&1
ORCH_SKILLS_CLONE="$UPD_CLONE" \
ORCH_SKILLS_INSTALL_DIR="$UPD_INSTALL" \
ORCH_INSTALL_DIR="$UPD_ORCH" \
ORCH_STATE_DIR="$TMP/upd-state" \
ORCH_SYSTEMD_USER_DIR="$TMP/no-systemd" \
ORCH_FORCE_SYNC=1 \
  bash "$FLEET_OPS/assets/update-skills.sh" >/dev/null 2>&1
UPD_RC=$?
if [ "$UPD_RC" -ne 0 ] && ! grep -q "v3-broken" "$UPD_ORCH/wake.sh" 2>/dev/null; then
  ok "update-skills.sh aborted and preserved v2 install on failing gate"
else
  fail "update-skills.sh overwrote install despite failing gate"
fi

# --- case 19: update-skills.sh defaults to isolated cache mirror and refuses dirty tracked trees (P0-4) ---
ISO_HOME="$TMP/isolated-home"
DEV_COPY="$ISO_HOME/github-projects/skills"
mkdir -p "$DEV_COPY/skills/autonomous-improvement/fleet-ops/assets"
cp "$FLEET_OPS/assets/update-skills.sh" "$DEV_COPY/skills/autonomous-improvement/fleet-ops/assets/update-skills.sh"
( cd "$DEV_COPY" && git init -q -b feature/wip && git add -A \
  && GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t git commit -qm wip ) >/dev/null 2>&1
# Reset origin back to working v2 commit so the mirror clone passes gates
( cd "$TMP/seed" && git reset --hard HEAD~1 >/dev/null 2>&1 && git push -qf origin main >/dev/null 2>&1 )
env -u ORCH_SKILLS_CLONE -u XDG_CACHE_HOME \
  HOME="$ISO_HOME" \
  ORCH_SKILLS_REPO="$UPD_ORIGIN" \
  ORCH_SKILLS_INSTALL_DIR="$TMP/iso-install" \
  ORCH_INSTALL_DIR="$TMP/iso-orch" \
  ORCH_STATE_DIR="$TMP/iso-state" \
  ORCH_SYSTEMD_USER_DIR="$TMP/no-systemd" \
  ORCH_UPDATE_SKIP_TESTS=1 \
  bash "$DEV_COPY/skills/autonomous-improvement/fleet-ops/assets/update-skills.sh" >/dev/null 2>&1
if [ -d "$ISO_HOME/.cache/orchestrator/skills-mirror/.git" ] && [ "$(git -C "$DEV_COPY" rev-parse --abbrev-ref HEAD)" = "feature/wip" ]; then
  ok "update-skills.sh defaults to XDG cache mirror and leaves dev checkout untouched"
else
  fail "update-skills.sh touched dev checkout or missed isolated cache mirror"
fi

# Dirty tracked file in ORCH_SKILLS_CLONE must abort before fetch/checkout/reset
echo "# dirty-local-edit" >> "$UPD_CLONE/scripts/check_skills.py"
ORCH_SKILLS_CLONE="$UPD_CLONE" \
ORCH_SKILLS_INSTALL_DIR="$UPD_INSTALL" \
ORCH_INSTALL_DIR="$UPD_ORCH" \
ORCH_STATE_DIR="$TMP/upd-state-dirty" \
ORCH_SYSTEMD_USER_DIR="$TMP/no-systemd" \
ORCH_FORCE_SYNC=1 \
  bash "$FLEET_OPS/assets/update-skills.sh" >/dev/null 2>&1
DIRTY_RC=$?
if [ "$DIRTY_RC" -ne 0 ] && grep -q "dirty-local-edit" "$UPD_CLONE/scripts/check_skills.py" 2>/dev/null \
   && grep -qi "uncommitted tracked changes" "$TMP/upd-state-dirty/skill-update.log" 2>/dev/null; then
  ok "update-skills.sh aborted on dirty tracked tree and preserved local edit"
else
  fail "update-skills.sh did NOT abort safely on dirty tracked tree"
fi

# --- case 20: failed wake attempt reclaims any worktree it created (issue #177) ---
WT_GC_REPO="$TMP/wt-gc-repo"
mkdir -p "$WT_GC_REPO/scripts"
cp -r "$SKILL_ROOT/scripts/autonomous" "$WT_GC_REPO/scripts/autonomous"
( cd "$WT_GC_REPO" && git init -q -b main && git add -A \
  && GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t git commit -qm base ) >/dev/null 2>&1
cat >"$TMP/bin/timeout-creating-wt" <<'EOF'
#!/bin/bash
git worktree add "$PWD-aborted-task" -b "test/aborted-task" -q
echo "unsaved junk" > "$PWD-aborted-task/junk.txt"
exit 124
EOF
chmod +x "$TMP/bin/timeout-creating-wt"
mkdir -p "$TMP/state-wtgc"
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$WT_GC_REPO" \
  ORCH_STATE_DIR="$TMP/state-wtgc" \
  ORCH_WAKE_CMD='timeout-creating-wt' \
  ORCH_MODEL_LADDER="opencode/alpha-free" \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-red.json" \
  bash "$ASSETS" >/dev/null 2>&1
if [ ! -d "${WT_GC_REPO}-aborted-task" ] && [ "$(git -C "$WT_GC_REPO" worktree list --porcelain | grep -c '^worktree ')" -eq 1 ]; then
  ok "failed wake attempt automatically cleaned up its orphaned worktree"
else
  fail "failed wake attempt left orphaned worktree behind"
fi

# --- case 21: pre-wake sweep-worktrees reclaims settled worktree before max_worktrees check (issue #177) ---
git -C "$WT_GC_REPO" worktree add -q "${WT_GC_REPO}-audit-settled" -b "audit/settled-task"
cat >"$TMP/bin/gh-settled" <<'EOF'
#!/bin/sh
echo '[{"number": 7, "headRefName": "audit/settled-task", "headRefOid": "sha-7", "state": "MERGED", "statusCheckRollup": [{"name": "unit-tests", "status": "COMPLETED", "conclusion": "FAILURE"}]}]'
EOF
chmod +x "$TMP/bin/gh-settled"
# Create a bin dir where `gh` returns the settled PR list
mkdir -p "$TMP/bin-settled"
cp "$TMP/bin/fake-harness" "$TMP/bin-settled/fake-harness"
cp "$TMP/bin/gh-settled" "$TMP/bin-settled/gh"
: >"$WAKE_LOG"
rm -f "$TMP/state-wtgc/wake.json" "$TMP/state-wtgc/wake_failures"
PATH="$TMP/bin-settled:$PATH" \
  ORCH_REPO="$WT_GC_REPO" \
  ORCH_STATE_DIR="$TMP/state-wtgc" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_MODEL_LADDER="opencode/mention-free" \
  ORCH_MAX_WORKTREES=1 \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  bash "$ASSETS" >/dev/null 2>&1
if [ ! -d "${WT_GC_REPO}-audit-settled" ] && grep -q "ARGS:" "$WAKE_LOG" 2>/dev/null; then
  ok "pre-wake sweep-worktrees reclaimed settled worktree before max_worktrees check"
else
  fail "pre-wake sweep-worktrees did NOT reclaim settled worktree or blocked wake"
fi

if [ "$FAILED" -eq 0 ]; then
  echo "PASS: stateless wake launcher"
  exit 0
fi
echo "FAILED: stateless wake launcher"
exit 1
