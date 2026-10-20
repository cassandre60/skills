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
if grep -q '^opencode/alpha-free ' "$TMP/state/model_failures" 2>/dev/null &&
  grep -q '^opencode/beta-free ' "$TMP/state/model_failures" 2>/dev/null; then
  ok "both ladder models recorded when all fail"
else
  fail "not every failing model was recorded"
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

if [ "$FAILED" -eq 0 ]; then
  echo "PASS: watchdog model rotation"
  exit 0
fi
echo "FAILED: watchdog model rotation"
exit 1
