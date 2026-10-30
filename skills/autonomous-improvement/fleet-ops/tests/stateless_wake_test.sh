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
  rm -f "$TMP/state/last_wake" "$TMP/state/watchdog.log"
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

# --- case 10: ANSI-escaped free usage limit is recognized as quota failure and cools down opencode/* (issue #109) ---
: >"$WAKE_LOG"
rm -f "$TMP/state/wake.json" "$TMP/state/wake_failures"
PATH="$TMP/bin:$PATH" \
  ORCH_REPO="$SKILL_ROOT" \
  ORCH_STATE_DIR="$TMP/state" \
  ORCH_WAKE_CMD='fake-harness "$1" ${2:+-m "$2"}' \
  ORCH_MODEL_LADDER="opencode/beam-free-ansi-free-limit opencode/other-free" \
  ORCH_WAKE_TIMEOUT=20 \
  ORCH_MIN_INTERVAL=0 \
  GH_JSON="$TMP/prs-red.json" \
  bash "$ASSETS" >/dev/null 2>&1
if grep -q '^opencode/beam-free-ansi-free-limit ' "$TMP/state/wake_failures" 2>/dev/null; then
  ok "ANSI-colored free limit error recorded in wake_failures"
else
  fail "ANSI-colored free limit error was NOT recorded in wake_failures"
fi
if grep -q '^opencode/other-free ' "$TMP/state/wake_failures" 2>/dev/null; then
  ok "sibling opencode/* free model also marked failed (provider-level cooldown)"
else
  fail "sibling opencode/* free model was NOT marked failed"
fi

# --- case 11: upstream model unavailable is recognized as quota/availability failure (issue #109) ---
: >"$WAKE_LOG"
rm -f "$TMP/state/wake.json" "$TMP/state/wake_failures"
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

if [ "$FAILED" -eq 0 ]; then
  echo "PASS: stateless wake launcher"
  exit 0
fi
echo "FAILED: stateless wake launcher"
exit 1
