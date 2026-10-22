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

write_fake_outage() { # every model answers with an infrastructure outage
  cat >"$TMP/bin/opencode" <<EOF
#!/bin/bash
echo "ARGS: \$*"
echo "Error: 503 Service Unavailable - the model server is overloaded"
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

# Same as run_watchdog but keeps the exit code in LAST_RC (distress codes
# are part of the contract now: 3 = the tick ended in total failure).
run_watchdog_rc() {
  run_watchdog
  LAST_RC=$?
}
LAST_RC=0

# --- case 0: an infrastructure outage is NOT a quota failure -------------
# The model is not exhausted. Rotating would burn every ladder entry against
# the same broken endpoint, and marking one dead would park a usable model for
# an hour. So: no rotation, no cooldown entry, and the tick ends.
write_fake_outage
run_watchdog
if grep -q -- '-m opencode/beta-free' "$TMP/state/watchdog.log"; then
  fail "rotated to the next model after an infrastructure outage"
else
  ok "no rotation after an infrastructure outage"
fi
if [ -s "$TMP/state/model_failures" ]; then
  fail "an infrastructure outage marked a model as failed: $(cat "$TMP/state/model_failures")"
else
  ok "an infrastructure outage did not park a model"
fi

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

# --- case 4: a SUCCESSFUL nudge that merely MENTIONS the limits ---------
# Tightened signatures: a quota term counts only on an ERROR-shaped line, so
# an agent summarizing the journal ("rate limit handled, quota notes filed")
# does not park its model. Verified failure this replaces: the loose
# substring match parked a healthy model whose summary discussed limits.
write_fake_opencode_mention() {
  cat >"$TMP/bin/opencode" <<'EOF'
#!/bin/bash
echo "wake: journal summary — rate limit hit twice yesterday; quota notes archived"
echo "housekeeping done"
EOF
  chmod +x "$TMP/bin/opencode"
}
write_fake_opencode_mention
run_watchdog_rc
if [ -s "$TMP/state/model_failures" ]; then
  fail "mention-only output parked a model (loose quota signatures)"
else
  ok "a success that mentions limits parks nobody"
fi
if grep -q "rotating" "$TMP/state/watchdog.log"; then
  fail "rotation happened for a successful nudge"
else
  ok "no rotation for a successful nudge"
fi
if [ "$LAST_RC" -eq 0 ]; then
  ok "successful tick exits 0"
else
  fail "successful tick exited $LAST_RC"
fi

# --- case 5: stale model_failures entries are pruned (7-day TTL) --------
write_fake_opencode "nomatch" "alpha-free"
rm -f "$TMP/state/watchdog.log" "$TMP/state/last_nudge"
printf 'ses_TEST\n' >"$TMP/state/opencode_session_id"
touch -d '3 hours ago' "$TMP/state/last_beat"
printf 'opencode/gamma-free %s\nopencode/delta-free %s\n' \
  "$(( $(date +%s) - 8 * 86400 ))" "$(date +%s)" >"$TMP/state/model_failures"
PATH="$TMP/bin:$PATH" ORCH_STATE_DIR="$TMP/state" \
  ORCH_MODEL_LADDER="opencode/alpha-free opencode/beta-free" \
  ORCH_NUDGE_TIMEOUT=20 ORCH_MIN_INTERVAL=0 ORCH_HB_MAX_AGE=60 \
  bash "$ASSETS" >/dev/null 2>&1
if grep -q '^opencode/gamma-free ' "$TMP/state/model_failures" 2>/dev/null; then
  fail "8-day-old model_failures entry was not pruned"
else
  ok "stale model_failures entry pruned"
fi
if grep -q '^opencode/delta-free ' "$TMP/state/model_failures" 2>/dev/null; then
  ok "fresh cooldown entry survives the prune"
else
  fail "prune removed a still-valid cooldown entry"
fi

# --- case 6: total failure exits with the distress code -----------------
write_fake_opencode "free" "free"
run_watchdog_rc
if [ "$LAST_RC" -eq 3 ]; then
  ok "a tick that ends in total failure exits 3"
else
  fail "total-failure tick exited $LAST_RC (expected 3)"
fi

# --- case 7: an EMPTY model_failures file must not break the ladder -------
# Regression: failed_recently() read into a global 'm', clobbering the
# caller's ladder loop variable. With the file present but empty, read's
# EOF blanked the model and the ladder silently degraded to the session
# default - the very failure the watchdog exists to prevent. The pruning
# added in issue #19 can produce exactly this state.
write_fake_opencode "alpha-free" "beta-free"
rm -f "$TMP/state/watchdog.log" "$TMP/state/last_nudge"
printf 'ses_TEST\n' >"$TMP/state/opencode_session_id"
touch -d '3 hours ago' "$TMP/state/last_beat"
: >"$TMP/state/model_failures"   # exists, but empty
PATH="$TMP/bin:$PATH" ORCH_STATE_DIR="$TMP/state" \
  ORCH_MODEL_LADDER="opencode/alpha-free opencode/beta-free" \
  ORCH_NUDGE_TIMEOUT=20 ORCH_MIN_INTERVAL=0 ORCH_HB_MAX_AGE=60 \
  bash "$ASSETS" >/dev/null 2>&1
if grep -q -- '-m opencode/alpha-free' "$TMP/state/watchdog.log"; then
  ok "empty model_failures still uses the ladder's first model"
else
  fail "empty model_failures broke the ladder (fell back to the session default)"
fi

if [ "$FAILED" -eq 0 ]; then
  echo "PASS: watchdog model rotation"
  exit 0
fi
echo "FAILED: watchdog model rotation"
exit 1
