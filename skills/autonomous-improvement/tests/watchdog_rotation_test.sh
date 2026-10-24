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
if [ "\${1:-}" = "api" ]; then
  if [ "\${2:-}" = "get" ]; then
    echo "{\\"model\\":{\\"id\\":\\"\$(cat "\$ORCH_STATE_DIR/current_model" 2>/dev/null || echo old-model)\\",\\"providerID\\":\\"opencode\\"}}"
    exit 0
  fi
  printf '%s\n' "\$*" >>"\$ORCH_STATE_DIR/switch_calls"
  exit 0
fi
echo "ARGS: \$*"
case "\$*" in
  *$1*) echo "Error: Rate limit exceeded. Please try again later." ;;
  *) echo "wake: housekeeping done" ;;
esac
EOF
  chmod +x "$TMP/bin/opencode"
}

write_fake_dead() { # $1: the model the harness reports as unavailable
  cat >"$TMP/bin/opencode" <<EOF
#!/bin/bash
if [ "\${1:-}" = "api" ]; then
  if [ "\${2:-}" = "get" ]; then
    echo "{\\"model\\":{\\"id\\":\\"\$(cat "\$ORCH_STATE_DIR/current_model" 2>/dev/null || echo old-model)\\",\\"providerID\\":\\"opencode\\"}}"
    exit 0
  fi
  printf '%s\n' "\$*" >>"\$ORCH_STATE_DIR/switch_calls"
  exit 0
fi
echo "ARGS: \$*"
case "\$*" in
  *$1*) echo "Error: Model unavailable: opencode/$1" ;;
  *) echo "wake: housekeeping done" ;;
esac
EOF
  chmod +x "$TMP/bin/opencode"
}

write_fake_unknown_error() { # exit 1 with a signature nobody has taught the script
  cat >"$TMP/bin/opencode" <<'EOF'
#!/bin/bash
if [ "${1:-}" = "api" ]; then
  if [ "${2:-}" = "get" ]; then
    echo "{\\"model\\":{\\"id\\":\\"$(cat \"$ORCH_STATE_DIR/current_model\" 2>/dev/null || echo old-model)\\",\\"providerID\\":\\"opencode\\"}}"
    exit 0
  fi
  printf '%s\n' "$*" >>"$ORCH_STATE_DIR/switch_calls"
  exit 0
fi
echo "ARGS: $*"
echo "Error: something the watchdog has never seen before"
exit 1
EOF
  chmod +x "$TMP/bin/opencode"
}

write_fake_timeout() { # $1: "wake" touches the heartbeat before hanging
  local wake=""
  [ "${1:-}" = "wake" ] && wake='sleep 2; touch "$ORCH_STATE_DIR/last_beat"'
  cat >"$TMP/bin/opencode" <<EOF
#!/bin/bash
if [ "\${1:-}" = "api" ]; then
  if [ "\${2:-}" = "get" ]; then
    echo "{\\"model\\":{\\"id\\":\\"\$(cat "\$ORCH_STATE_DIR/current_model" 2>/dev/null || echo old-model)\\",\\"providerID\\":\\"opencode\\"}}"
    exit 0
  fi
  printf '%s\n' "\$*" >>"\$ORCH_STATE_DIR/switch_calls"
  exit 0
fi
echo "ARGS: \$*"
$wake
sleep 20
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
  rm -f "$TMP/state/watchdog.log" "$TMP/state/model_failures" "$TMP/state/last_nudge" \
        "$TMP/state/ALERT.md" "$TMP/state/switch_calls" "$TMP/state/exhausted_at"
  printf 'ses_TEST\n' >"$TMP/state/opencode_session_id"
  touch "$TMP/state/last_beat"
  touch -d '3 hours ago' "$TMP/state/last_beat"
  PATH="$TMP/bin:$PATH" ORCH_STATE_DIR="$TMP/state" \
    ORCH_MODEL_LADDER="opencode/alpha-free opencode/beta-free" \
    ORCH_NUDGE_TIMEOUT=20 ORCH_MIN_INTERVAL=0 ORCH_HB_MAX_AGE=60 \
    bash "$ASSETS" >/dev/null 2>&1
}

run_watchdog_rc() { # same, but the exit code is kept for contract assertions
  run_watchdog
  LAST_RC=$?
}

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
  ok "an infrastructure outage left the cooldown list untouched"
fi
if grep -qi 'outage' "$TMP/state/watchdog.log"; then
  ok "the outage was recorded in the log"
else
  fail "the outage was not recorded in the log"
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

# --- case 4b: a dead model is a failure, not a success -------------------
# Verified production failure (2026-10-23): `Error: Model unavailable` matched
# neither the quota nor outage signatures, fell through to the success branch,
# and the ladder was never tried for seven hours.
write_fake_dead "alpha-free"
run_watchdog
if grep -q -- '-m opencode/beta-free' "$TMP/state/watchdog.log"; then
  ok "a dead model rotated to the next ladder entry"
else
  fail "a dead model did not rotate"
fi
if grep -q '^opencode/alpha-free ' "$TMP/state/model_failures" 2>/dev/null; then
  ok "the dead model was parked in the cooldown list"
else
  fail "the dead model was not parked"
fi
if grep -q "nudge on 'opencode/alpha-free' finished with exit 0" "$TMP/state/watchdog.log"; then
  fail "a failed dead-model nudge was logged as a success"
else
  ok "the dead-model nudge was not logged as a success"
fi

# --- case 4c: an unclassified non-zero exit still rotates ----------------
# The general form of the bug: the classifier only knows the errors it was
# taught, so any new error string becomes a silent success. Exit status is
# the contract; the signature only decides how to record it.
write_fake_unknown_error
run_watchdog
if grep -q -- '-m opencode/beta-free' "$TMP/state/watchdog.log"; then
  ok "an unclassified failure rotated"
else
  fail "an unclassified failure was treated as success"
fi
if grep -q 'without a known signature' "$TMP/state/watchdog.log"; then
  ok "the unclassified failure was logged as such"
else
  fail "the unclassified failure was not logged"
fi
if [ -s "$TMP/state/ALERT.md" ] && grep -q 'Next actions' "$TMP/state/ALERT.md"; then
  ok "exhaustion left an alert the owner can see"
else
  fail "exhaustion left no alert"
fi

run_watchdog_timeout() {
  rm -f "$TMP/state/watchdog.log" "$TMP/state/model_failures" "$TMP/state/last_nudge" \
        "$TMP/state/ALERT.md" "$TMP/state/switch_calls" "$TMP/state/exhausted_at" "$TMP/state/current_model"
  printf 'ses_TEST\n' >"$TMP/state/opencode_session_id"
  touch -d '3 hours ago' "$TMP/state/last_beat"
  PATH="$TMP/bin:$PATH" ORCH_STATE_DIR="$TMP/state" \
    ORCH_MODEL_LADDER="opencode/alpha-free opencode/beta-free" \
    ORCH_NUDGE_TIMEOUT=5 ORCH_MIN_INTERVAL=0 ORCH_HB_MAX_AGE=60 \
    bash "$ASSETS" >/dev/null 2>&1
}

# --- case 5: a timeout that never moved the heartbeat rotates ------------
# A preempted model hangs until the nudge budget runs out. Nothing woke, so
# the next model must be tried within the same tick.
write_fake_timeout
run_watchdog_timeout
if grep -q -- '-m opencode/beta-free' "$TMP/state/watchdog.log"; then
  ok "a timeout without a heartbeat movement rotated"
else
  fail "a timeout without a heartbeat movement was treated as done"
fi

# --- case 5b: a timeout that did move the heartbeat is a wake ------------
# The turn may simply be long. The session's own heartbeat is the witness:
# if it moved after the attempt started, do not inject a second nudge.
write_fake_timeout wake
run_watchdog_timeout
if grep -q -- '-m opencode/beta-free' "$TMP/state/watchdog.log"; then
  fail "a timeout that woke the session still rotated (double nudge)"
else
  ok "a timeout that woke the session did not rotate"
fi
if grep -q 'timed out but the session woke' "$TMP/state/watchdog.log"; then
  ok "the woken timeout was explained in the log"
else
  fail "the woken timeout was not explained"
fi

# --- case 6: the ladder may only name owner-authorised providers ---------
# An operator once left a live session on a gateway the owner had not
# authorised; the watchdog must refuse such an entry outright.
rm -f "$TMP/state/watchdog.log" "$TMP/state/model_failures" "$TMP/state/last_nudge" \
      "$TMP/state/ALERT.md" "$TMP/state/switch_calls"
printf 'ses_TEST\n' >"$TMP/state/opencode_session_id"
touch -d '3 hours ago' "$TMP/state/last_beat"
write_fake_opencode "alpha-free" "beta-free"
PATH="$TMP/bin:$PATH" ORCH_STATE_DIR="$TMP/state" \
  ORCH_MODEL_LADDER="cliproxy/evil/gemini opencode/beta-free" \
  ORCH_NUDGE_TIMEOUT=20 ORCH_MIN_INTERVAL=0 ORCH_HB_MAX_AGE=60 \
  bash "$ASSETS" >/dev/null 2>&1
if grep -q 'refusing ladder model .cliproxy/evil/gemini' "$TMP/state/watchdog.log"; then
  ok "an unauthorised provider was refused"
else
  fail "an unauthorised provider was not refused"
fi
if grep -q -- '-m cliproxy' "$TMP/state/watchdog.log"; then
  fail "an unauthorised provider was used"
else
  ok "no request went to the unauthorised provider"
fi
if grep -q -- '-m opencode/beta-free' "$TMP/state/watchdog.log"; then
  ok "the authorised model still ran"
else
  fail "the authorised model did not run"
fi

# --- case 7: the model that carried the nudge becomes the session model --
# The owner's complaint on 2026-10-23 was "the model did not get switched":
# rotating per turn is not enough if every interactive turn stays on the dead
# default.
write_fake_opencode "alpha-free" "beta-free"
run_watchdog
if grep -q 'post /api/session/ses_TEST/model' "$TMP/state/switch_calls" 2>/dev/null &&
  grep -q 'beta-free' "$TMP/state/switch_calls"; then
  ok "the session model was switched to the model that carried the nudge"
else
  fail "the session model was not switched after rotation"
fi

# --- case 8: model_failures entries older than the TTL are pruned --------
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

# --- case 8b: a tick that ends in total failure exits 3 ------------------
# Exit 3 is the distress code: systemd `OnFailure=` or an owner hook fires
# on it. A silent fleet is the failure mode the watchdog exists to prevent.
write_fake_opencode "free" "free"
run_watchdog_rc
if [ "$LAST_RC" -eq 3 ]; then
  ok "a tick that ends in total failure exits 3"
else
  fail "total-failure tick exited $LAST_RC (expected 3)"
fi

# --- case 8c: an EMPTY model_failures file must not break the ladder ------
# Regression: failed_recently() read into a global 'm', clobbering the
# caller's ladder loop variable. With the file present but empty, read's
# EOF blanked the model and the ladder silently degraded to the session
# default - the very failure the watchdog exists to prevent. The pruning
# added in issue #19 can produce exactly this state.
write_fake_opencode "alpha-free" "beta-free"
# 8b left the exhaustion state behind (it ended in total failure by design);
# an exhaustion back-off would skip this tick entirely and mask the ladder.
rm -f "$TMP/state/watchdog.log" "$TMP/state/last_nudge" \
      "$TMP/state/exhausted_at" "$TMP/state/ALERT.md"
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

# --- case 9: an exhausted tick must not flood the session ----------------
# Each attempt delivers its message BEFORE the model call fails. With every
# model dead, one tick per 20 minutes would pile messages into a session that
# cannot run a turn (verified 2026-10-22: six stale nudges queued). The next
# tick must back off and leave the alert to speak.
write_fake_unknown_error
run_watchdog_rc
if [ "$LAST_RC" -eq 3 ]; then
  ok "the unclassified total failure also exits 3"
else
  fail "unclassified total-failure tick exited $LAST_RC (expected 3)"
fi
: >"$TMP/state/watchdog.log"
touch -d '3 hours ago' "$TMP/state/last_beat"
rm -f "$TMP/state/last_nudge"
PATH="$TMP/bin:$PATH" ORCH_STATE_DIR="$TMP/state" \
  ORCH_MODEL_LADDER="opencode/alpha-free opencode/beta-free" \
  ORCH_NUDGE_TIMEOUT=20 ORCH_MIN_INTERVAL=0 ORCH_HB_MAX_AGE=60 \
  bash "$ASSETS" >/dev/null 2>&1
BACKOFF_RC=$?
if [ "$BACKOFF_RC" -eq 0 ]; then
  ok "a backed-off tick exits 0 (the alert is the signal, not a crash)"
else
  fail "backed-off tick exited $BACKOFF_RC (expected 0)"
fi
if grep -q 'backing off without nudging' "$TMP/state/watchdog.log"; then
  ok "an exhausted tick backs off instead of flooding the session"
else
  fail "an exhausted tick kept nudging"
fi
if grep -q 'nudge attempt' "$TMP/state/watchdog.log"; then
  fail "a backed-off tick still delivered a nudge"
else
  ok "no nudge was delivered while backing off"
fi

# --- case 11: do not override a session already on a live ladder model -
# The ladder is tried before the session default, so the first entry carries
# the nudge even when the owner's model is healthy. Switching then would move
# the session without cause (and outside the owner's choice).
printf 'beta-free\n' >"$TMP/state/current_model"
write_fake_opencode "alpha-free" "beta-free"
run_watchdog
if grep -q 'already on .opencode/beta-free., a live ladder model; not switching' "$TMP/state/watchdog.log"; then
  ok "a healthy session model was not overridden"
else
  fail "the session model was overridden while healthy"
fi
if [ -s "$TMP/state/switch_calls" ]; then
  fail "a switch was issued while the session was on a live ladder model"
else
  ok "no switch was issued"
fi

# --- case 12: a non-opencode harness drives the whole tick via overrides --
# The watchdog is harness-neutral: every opencode touchpoint is an env
# override (ORCH_RESUME_CMD, ORCH_SESSION_ID_FILE, ORCH_SESSION_GET_CMD,
# ORCH_SESSION_SET_CMD). This case runs a full rotation with NO `opencode`
# binary on PATH: a foreign fake harness carries the nudge, answers the
# model read, and receives the switch. references/harness-adapters.md is
# the contract this case proves.
mkdir -p "$TMP/bin2"
cat >"$TMP/bin2/myagent" <<EOF
#!/bin/bash
case "\$1" in
  run)
    msg="\$*"
    case "\$msg" in
      *alpha-free*) echo "Error: Rate limit exceeded. Please try again later." ; exit 1 ;;
      *) echo "wake: housekeeping done" ;;
    esac ;;
  get-model)
    echo "{\\"model\\":{\\"id\\":\\"old-default\\",\\"providerID\\":\\"myprov\\"}}" ;;
  set-model)
    printf '%s\n' "\$*" >>"\$ORCH_STATE_DIR/switch_calls" ;;
esac
EOF
chmod +x "$TMP/bin2/myagent"
rm -f "$TMP/state/watchdog.log" "$TMP/state/model_failures" "$TMP/state/last_nudge" \
      "$TMP/state/switch_calls" "$TMP/state/exhausted_at" "$TMP/state/ALERT.md"
printf 'ses_TEST\n' >"$TMP/state/agent_session"
rm -f "$TMP/state/opencode_session_id"
touch -d '3 hours ago' "$TMP/state/last_beat"
PATH="$TMP/bin2:$PATH" ORCH_STATE_DIR="$TMP/state" \
  ORCH_MODEL_LADDER="myprov/alpha-free myprov/beta-free" \
  ORCH_AUTHORIZED_PROVIDERS=myprov \
  ORCH_RESUME_CMD='myagent run -s "$1" ${3:+-m "$3"} "$2"' \
  ORCH_SESSION_ID_FILE=agent_session \
  ORCH_SESSION_GET_CMD='myagent get-model "$1"' \
  ORCH_SESSION_SET_CMD='myagent set-model "$1" "$2"' \
  ORCH_NUDGE_TIMEOUT=20 ORCH_MIN_INTERVAL=0 ORCH_HB_MAX_AGE=60 \
  bash "$ASSETS" >/dev/null 2>&1
if grep -q "nudge attempt 2 on model 'myprov/beta-free'" "$TMP/state/watchdog.log" &&
  grep -q 'wake: housekeeping done' "$TMP/state/watchdog.log"; then
  ok "the foreign harness carried the nudge through the overridden resume command"
else
  fail "the overridden resume command was not used (no foreign-agent attempt in log)"
fi
if grep -q '^myprov/alpha-free ' "$TMP/state/model_failures" 2>/dev/null; then
  ok "rotation semantics (cooldown on the dead model) hold for the foreign harness"
else
  fail "the dead foreign model was not parked in model_failures"
fi
if grep -q 'set-model ses_TEST myprov/beta-free' "$TMP/state/switch_calls" 2>/dev/null; then
  ok "the switch went through the overridden ORCH_SESSION_SET_CMD"
else
  fail "the session-model switch did not use the overridden set command"
fi
if grep -q 'opencode' "$TMP/state/switch_calls" 2>/dev/null; then
  fail "the default opencode endpoint was called despite the override"
else
  ok "the default opencode endpoint was not touched"
fi

if [ "$FAILED" -eq 0 ]; then
  echo "PASS: watchdog model rotation"
  exit 0
fi
echo "FAILED: watchdog model rotation"
exit 1
