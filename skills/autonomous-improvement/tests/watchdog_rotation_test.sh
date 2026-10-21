#!/bin/bash
# Behavioural test for assets/watchdog.sh — model rotation on a quota error.
#
# The claim under test: a nudge whose model answers with a quota/balance
# failure is retried INSIDE THE SAME TICK on the next model of the ladder,
# and the failed model is remembered (cooldown) while the healthy one is not.
#
# Verified failure this replaces: the watchdog's tier-1 nudge resumed the
# session on the model whose quota had just died, so seven consecutive nudges
# all answered "Rate limit exceeded" and the session stayed frozen for ~8 h
# while another free model was alive (2026-10-20).
#
# Run: bash tests/watchdog_rotation_test.sh
set -u

ASSETS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/assets/watchdog.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/state"
FAILED=0

fail() { echo "FAIL: $1"; FAILED=1; }
ok() { echo "ok: $1"; }

write_fake_opencode() { # $1: model that fails ("" = none), $2: model that succeeds
  cat >"$TMP/bin/opencode" <<EOF
#!/bin/bash
echo "ARGS: \$*"
case "\$*" in
  *$1*) echo "Error: Rate limit exceeded. Please try again later." ;;
  *) echo "wake: housekeeping done" ;;
esac
EOF
  chmod +x "$TMP/bin/opencode"
}

run_watchdog() {
  rm -f "$TMP/state/watchdog.log" "$TMP/state/model_failures" "$TMP/state/last_nudge"
  printf 'ses_TEST\n' >"$TMP/state/opencode_session_id"
  touch "$TMP/state/last_beat"
  touch -d '3 hours ago' "$TMP/state/last_beat"
  PATH="$TMP/bin:$PATH" ORCH_STATE_DIR="$TMP/state" \
    ORCH_MODEL_LADDER="opencode/alpha-free opencode/beta-free" \
    ORCH_NUDGE_TIMEOUT=20 ORCH_MIN_INTERVAL=0 ORCH_HB_MAX_AGE=60 \
    bash "$ASSETS" >/dev/null 2>&1
}

# --- case 1: first model fails, second succeeds -------------------------
write_fake_opencode "alpha-free" "beta-free"
run_watchdog
if grep -q -- '-m opencode/alpha-free' "$TMP/state/watchdog.log"; then
  ok "first attempt used the first ladder model"
else
  fail "first attempt did not use opencode/alpha-free"
fi
if grep -q -- '-m opencode/beta-free' "$TMP/state/watchdog.log"; then
  ok "rotated to the second model in the same tick"
else
  fail "no rotation to opencode/beta-free happened"
fi
if grep -q '^opencode/alpha-free ' "$TMP/state/model_failures" 2>/dev/null; then
  ok "failed model recorded for cooldown"
else
  fail "failed model was not recorded"
fi
if grep -q '^opencode/beta-free ' "$TMP/state/model_failures" 2>/dev/null; then
  fail "successful model was wrongly marked failed"
else
  ok "successful model not marked failed"
fi

# --- case 2: every model fails -> both recorded -------------------------
write_fake_opencode "free" "free"
run_watchdog
WD_RC=$?
if [ "$WD_RC" -ne 0 ]; then
  ok "total-failure tick exits nonzero so OnFailure can fire"
else
  fail "total-failure tick exited 0; systemd cannot signal distress"
fi
if grep -q '^opencode/alpha-free ' "$TMP/state/model_failures" 2>/dev/null &&
  grep -q '^opencode/beta-free ' "$TMP/state/model_failures" 2>/dev/null; then
  ok "both ladder models recorded when all fail"
else
  fail "not every failing model was recorded"
fi
# Nobody woke up, so the heartbeat must not have earned freshness from the
# attempt marker: the next tick has to find it stale and retry.
HB_AGE=$(( $(date +%s) - $(stat -c %Y "$TMP/state/last_beat") ))
if [ "$HB_AGE" -gt 60 ]; then
  ok "heartbeat left stale after an all-quota-failure tick"
else
  fail "heartbeat looks fresh (${HB_AGE}s) after every model failed"
fi

# --- case 3: models in cooldown -> single session-default attempt -------
write_fake_opencode "alpha-free" "beta-free"
printf 'opencode/alpha-free %s\nopencode/beta-free %s\n' "$(date +%s)" "$(date +%s)" \
  >"$TMP/state/model_failures"
rm -f "$TMP/state/last_nudge"
touch -d '3 hours ago' "$TMP/state/last_beat"
PATH="$TMP/bin:$PATH" ORCH_STATE_DIR="$TMP/state" \
  ORCH_MODEL_LADDER="opencode/alpha-free opencode/beta-free" \
  ORCH_NUDGE_TIMEOUT=20 ORCH_MIN_INTERVAL=0 ORCH_HB_MAX_AGE=60 \
  bash "$ASSETS" >/dev/null 2>&1
if grep -q 'every model in the ladder is in cooldown' "$TMP/state/watchdog.log"; then
  ok "cooldown respected: falls back to the session default once"
else
  fail "cooldown was not respected"
fi

# --- case 4: success that merely MENTIONS limits is not a quota failure --
# The tightened signatures need an error-shaped line or a failing exit; an
# agent summarizing a journal incident must not park its own model.
cat >"$TMP/bin/opencode" <<'EOF'
#!/bin/bash
echo "ARGS: $*"
echo "wake: housekeeping done; journal mentions an old rate limit quota incident"
EOF
chmod +x "$TMP/bin/opencode"
run_watchdog
if grep -q '^opencode/alpha-free ' "$TMP/state/model_failures" 2>/dev/null; then
  fail "successful model parked for merely mentioning rate limits"
else
  ok "prose mention of limits on a successful nudge does not park the model"
fi
if grep -q 'rotating' "$TMP/state/watchdog.log"; then
  fail "watchdog rotated away from a successful nudge"
else
  ok "no rotation after a successful nudge"
fi

# --- case 5: model_failures older than retention are pruned --------------
write_fake_opencode "zzz-never-matches" "beta-free"
OLD_TS=$(( $(date +%s) - 8 * 86400 ))
printf 'opencode/ancient-free %s\nopencode/beta-free %s\n' "$OLD_TS" "$(date +%s)" \
  >"$TMP/state/model_failures"
rm -f "$TMP/state/watchdog.log" "$TMP/state/last_nudge"
printf 'ses_TEST\n' >"$TMP/state/opencode_session_id"
touch -d '3 hours ago' "$TMP/state/last_beat"
PATH="$TMP/bin:$PATH" ORCH_STATE_DIR="$TMP/state" \
  ORCH_MODEL_LADDER="opencode/alpha-free opencode/beta-free" \
  ORCH_NUDGE_TIMEOUT=20 ORCH_MIN_INTERVAL=0 ORCH_HB_MAX_AGE=60 \
  bash "$ASSETS" >/dev/null 2>&1
if grep -q '^opencode/ancient-free ' "$TMP/state/model_failures" 2>/dev/null; then
  fail "8-day-old failure entry was not pruned"
else
  ok "stale failure entries pruned beyond retention"
fi
if grep -q '^opencode/beta-free ' "$TMP/state/model_failures" 2>/dev/null; then
  ok "recent failure entry kept"
else
  fail "recent failure entry was pruned too"
fi

if [ "$FAILED" -eq 0 ]; then
  echo "PASS: watchdog model rotation"
  exit 0
fi
echo "FAILED: watchdog model rotation"
exit 1
