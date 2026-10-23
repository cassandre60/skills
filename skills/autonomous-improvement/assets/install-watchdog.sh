#!/bin/bash
# Install or uninstall the out-of-session orchestrator watchdog.
#
#   ./install-watchdog.sh            # install + enable + start the timer
#   ./install-watchdog.sh --check    # compare the installed copy with the asset
#   ./install-watchdog.sh --uninstall
#
# Idempotent. The state directory (ORCH_STATE_DIR, default
# ~/.local/share/orchestrator) is never deleted by --uninstall.

set -euo pipefail

ASSET_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${ORCH_INSTALL_DIR:-$HOME/.local/share/orchestrator}"
STATE_DIR="${ORCH_STATE_DIR:-$HOME/.local/share/orchestrator}"
DEFAULT_DIR="$HOME/.local/share/orchestrator"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE="orchestrator-watchdog.service"
TIMER="orchestrator-watchdog.timer"
SNAP_SERVICE="orchestrator-snapshot.service"
SNAP_TIMER="orchestrator-snapshot.timer"
WAKE_SERVICE="orchestrator-wake.service"
WAKE_TIMER="orchestrator-wake.timer"

have() { command -v "$1" >/dev/null 2>&1; }

do_uninstall() {
  if have systemctl; then
    systemctl --user stop "$TIMER" "$SNAP_TIMER" "$WAKE_TIMER" 2>/dev/null || true
    systemctl --user disable "$TIMER" "$SNAP_TIMER" "$WAKE_TIMER" 2>/dev/null || true
  fi
  rm -f "$UNIT_DIR/$TIMER" "$UNIT_DIR/$SERVICE" "$UNIT_DIR/$SNAP_TIMER" "$UNIT_DIR/$SNAP_SERVICE" \
        "$UNIT_DIR/$WAKE_TIMER" "$UNIT_DIR/$WAKE_SERVICE" \
        "$INSTALL_DIR/watchdog.sh" "$INSTALL_DIR/snapshot.sh" "$INSTALL_DIR/wake.sh"
  if have systemctl; then
    systemctl --user daemon-reload
  fi
  echo "[+] Uninstalled watchdog, snapshot & wake units and scripts."
  echo "    State kept in ${ORCH_STATE_DIR:-$HOME/.local/share/orchestrator}."
}

# NOT named install(): a shell function shadows /usr/bin/install, so the
# `install -m ...` calls below would recurse into this function until the
# stack blows. Same reason for do_uninstall.
do_install() {
  mkdir -p "$INSTALL_DIR" "$STATE_DIR" "$UNIT_DIR"
  install -m 0755 "$ASSET_DIR/watchdog.sh" "$INSTALL_DIR/watchdog.sh"
  install -m 0755 "$ASSET_DIR/snapshot.sh" "$INSTALL_DIR/snapshot.sh"
  install -m 0755 "$ASSET_DIR/wake.sh" "$INSTALL_DIR/wake.sh"
  install -m 0644 "$ASSET_DIR/$SERVICE" "$UNIT_DIR/$SERVICE"
  install -m 0644 "$ASSET_DIR/$TIMER" "$UNIT_DIR/$TIMER"
  install -m 0644 "$ASSET_DIR/$SNAP_SERVICE" "$UNIT_DIR/$SNAP_SERVICE"
  install -m 0644 "$ASSET_DIR/$SNAP_TIMER" "$UNIT_DIR/$SNAP_TIMER"
  install -m 0644 "$ASSET_DIR/$WAKE_SERVICE" "$UNIT_DIR/$WAKE_SERVICE"
  install -m 0644 "$ASSET_DIR/$WAKE_TIMER" "$UNIT_DIR/$WAKE_TIMER"

  # The shipped unit points ExecStart/EnvironmentFile at the default
  # directory. If this install uses custom dirs, rewrite the installed
  # unit to match — otherwise the timer runs a script (and reads an env
  # file) that is not the one just installed.
  if [ "$INSTALL_DIR" != "$DEFAULT_DIR" ]; then
    sed -i "s|%h/.local/share/orchestrator/watchdog.sh|$INSTALL_DIR/watchdog.sh|" "$UNIT_DIR/$SERVICE"
    sed -i "s|%h/.local/share/orchestrator/snapshot.sh|$INSTALL_DIR/snapshot.sh|" "$UNIT_DIR/$SNAP_SERVICE"
    sed -i "s|%h/.local/share/orchestrator/wake.sh|$INSTALL_DIR/wake.sh|" "$UNIT_DIR/$WAKE_SERVICE"
  fi
  if [ "$STATE_DIR" != "$DEFAULT_DIR" ]; then
    sed -i "s|%h/.local/share/orchestrator/watchdog.env|$STATE_DIR/watchdog.env|" "$UNIT_DIR/$SERVICE"
    sed -i "s|%h/.local/share/orchestrator/watchdog.env|$STATE_DIR/watchdog.env|" "$UNIT_DIR/$SNAP_SERVICE"
    sed -i "s|%h/.local/share/orchestrator/watchdog.env|$STATE_DIR/watchdog.env|" "$UNIT_DIR/$WAKE_SERVICE"
  fi

  # Configuration the systemd service can actually see: shell-profile
  # exports never reach it. Never overwrite an existing env file.
  if [ ! -f "$STATE_DIR/watchdog.env" ]; then
    printf '%s\n' \
      '# Watchdog configuration (read by the systemd service at every start).' \
      '# See the CONFIGURATION comment in watchdog.sh for every knob.' \
      '# Model ladder, most-preferred first. Every entry must carry quota' \
      '# independent of the others, and must actually be free. Example:' \
      '#ORCH_MODEL_LADDER=provider/model-a provider/model-b' \
      'ORCH_MODEL_LADDER=' \
      '#ORCH_HB_MAX_AGE=2700' \
      '#ORCH_NUDGE_TIMEOUT=1800' \
      >"$STATE_DIR/watchdog.env"
    chmod 0600 "$STATE_DIR/watchdog.env"
    echo "[+] Wrote $STATE_DIR/watchdog.env — set ORCH_MODEL_LADDER there."
  fi

  if ! have systemctl; then
    echo "[!] systemctl not found; files were copied but the timer was not enabled."
    return 0
  fi

  systemctl --user daemon-reload
  if have systemd-analyze; then
    systemd-analyze --user verify "$UNIT_DIR/$SERVICE" "$UNIT_DIR/$TIMER" "$UNIT_DIR/$SNAP_SERVICE" "$UNIT_DIR/$SNAP_TIMER" "$UNIT_DIR/$WAKE_SERVICE" "$UNIT_DIR/$WAKE_TIMER"
  fi
  systemctl --user enable --now "$TIMER" "$SNAP_TIMER"
  systemctl --user list-timers --all "$TIMER" "$SNAP_TIMER" || true
  echo "[*] Stateless wake units installed but NOT enabled (enable one starter only):"
  echo "      systemctl --user enable --now $WAKE_TIMER   # after setting fleet.harness_cmd"

  echo
  echo "[+] Installed. Before the first run, write the session id:"
  echo "      printf '%s\n' '<session-id>' > \"${ORCH_STATE_DIR:-$HOME/.local/share/orchestrator}/opencode_session_id\""
  echo "    Touch the heartbeat from every wake turn:"
  echo "      touch \"${ORCH_STATE_DIR:-$HOME/.local/share/orchestrator}/last_beat\""
  echo
  LINGER="$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo unknown)"
  if [ "$LINGER" != "yes" ]; then
    echo "[!] User timers stop when you log out. Run this yourself (needs privileges):"
    echo "      sudo loginctl enable-linger $USER"
  fi
  echo
  echo "Logs: journalctl --user -u $SERVICE   (or ${ORCH_STATE_DIR:-$HOME/.local/share/orchestrator}/watchdog.log)"
}

# The installed script is a COPY, so a fix in assets/ is invisible to a running
# fleet until it is reinstalled. Verified 2026-10-23: the fleet ran a copy one
# day older than the asset (it still poisoned the heartbeat and had no outage
# class), and the watchdog log looked like the old bug for seven hours after
# the fix existed. Run this at every cold start and after every skill update.
do_check() {
  local rc=0 asset installed unit env_file exec_path
  for asset in watchdog.sh snapshot.sh; do
    installed="$INSTALL_DIR/$asset"
    if [ ! -f "$installed" ]; then
      echo "[!] $installed is missing; run ./install-watchdog.sh"
      rc=1
    elif ! cmp -s "$ASSET_DIR/$asset" "$installed"; then
      echo "[!] $installed differs from the skill asset — the installed watchdog is stale"
      echo "    reinstall: ./install-watchdog.sh"
      diff -u "$installed" "$ASSET_DIR/$asset" | head -20
      rc=1
    fi
  done
  # The env file is inert unless the installed unit loads it. A hand-written
  # unit that omits EnvironmentFile leaves every ORCH_* setting at its default
  # — including an empty ladder — while the script bytes look current.
  # Verified 2026-10-23: a fleet ran with an empty ladder because its unit
  # only set ORCH_STATE_DIR, so a merged watchdog fix could never rotate.
  unit="$UNIT_DIR/$SERVICE"
  if [ ! -f "$unit" ]; then
    echo "[!] $unit is missing; run ./install-watchdog.sh"
    rc=1
  else
    env_file="$(sed -n 's/^EnvironmentFile=-*//p' "$unit" | head -1)"
    env_file="${env_file/#%h/$HOME}"
    exec_path="$(sed -n 's/^ExecStart=//p' "$unit" | head -1)"
    exec_path="${exec_path/#%h/$HOME}"
    if [ -z "$env_file" ]; then
      echo "[!] $unit has no EnvironmentFile=; ORCH_* settings (the model ladder) never reach the service"
      rc=1
    elif [ ! -f "$env_file" ]; then
      echo "[!] $unit reads $env_file, which does not exist"
      rc=1
    fi
    if [ -n "$exec_path" ] && [ "$exec_path" != "$INSTALL_DIR/watchdog.sh" ]; then
      echo "[!] $unit runs '$exec_path', not the installed '$INSTALL_DIR/watchdog.sh'"
      rc=1
    fi
  fi
  if [ "$rc" -eq 0 ]; then
    echo "[+] installed watchdog and snapshot match the skill assets; the unit loads its env file"
  fi
  return "$rc"
}

case "${1:-}" in
  --check)
    do_check
    ;;
  --uninstall)
    do_uninstall
    ;;
  "")
    do_install
    ;;
  *)
    echo "usage: $0 [--uninstall]" >&2
    exit 2
    ;;
esac
