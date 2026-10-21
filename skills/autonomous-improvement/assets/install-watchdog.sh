#!/bin/bash
# Install or uninstall the out-of-session orchestrator watchdog.
#
#   ./install-watchdog.sh            # install + enable + start the timer
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

have() { command -v "$1" >/dev/null 2>&1; }

do_uninstall() {
  if have systemctl; then
    systemctl --user stop "$TIMER" 2>/dev/null || true
    systemctl --user disable "$TIMER" 2>/dev/null || true
  fi
  rm -f "$UNIT_DIR/$TIMER" "$UNIT_DIR/$SERVICE" "$INSTALL_DIR/watchdog.sh"
  if have systemctl; then
    systemctl --user daemon-reload
  fi
  echo "[+] Uninstalled watchdog units and script."
  echo "    State kept in ${ORCH_STATE_DIR:-$HOME/.local/share/orchestrator}."
}

# NOT named install(): a shell function shadows /usr/bin/install, so the
# `install -m ...` calls below would recurse into this function until the
# stack blows. Same reason for do_uninstall.
do_install() {
  mkdir -p "$INSTALL_DIR" "$STATE_DIR" "$UNIT_DIR"
  install -m 0755 "$ASSET_DIR/watchdog.sh" "$INSTALL_DIR/watchdog.sh"
  install -m 0644 "$ASSET_DIR/$SERVICE" "$UNIT_DIR/$SERVICE"
  install -m 0644 "$ASSET_DIR/$TIMER" "$UNIT_DIR/$TIMER"

  # The shipped unit points ExecStart/EnvironmentFile at the default
  # directory. If this install uses custom dirs, rewrite the installed
  # unit to match — otherwise the timer runs a script (and reads an env
  # file) that is not the one just installed.
  if [ "$INSTALL_DIR" != "$DEFAULT_DIR" ]; then
    sed -i "s|%h/.local/share/orchestrator/watchdog.sh|$INSTALL_DIR/watchdog.sh|" "$UNIT_DIR/$SERVICE"
  fi
  if [ "$STATE_DIR" != "$DEFAULT_DIR" ]; then
    sed -i "s|%h/.local/share/orchestrator/watchdog.env|$STATE_DIR/watchdog.env|" "$UNIT_DIR/$SERVICE"
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
    systemd-analyze --user verify "$UNIT_DIR/$SERVICE" "$UNIT_DIR/$TIMER"
  fi
  systemctl --user enable --now "$TIMER"
  systemctl --user list-timers --all "$TIMER" || true

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

case "${1:-}" in
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
