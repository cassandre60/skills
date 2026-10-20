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
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE="orchestrator-watchdog.service"
TIMER="orchestrator-watchdog.timer"

have() { command -v "$1" >/dev/null 2>&1; }

uninstall() {
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

install() {
  mkdir -p "$INSTALL_DIR" "$UNIT_DIR"
  install -m 0755 "$ASSET_DIR/watchdog.sh" "$INSTALL_DIR/watchdog.sh"
  install -m 0644 "$ASSET_DIR/$SERVICE" "$UNIT_DIR/$SERVICE"
  install -m 0644 "$ASSET_DIR/$TIMER" "$UNIT_DIR/$TIMER"

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
    uninstall
    ;;
  "")
    install
    ;;
  *)
    echo "usage: $0 [--uninstall]" >&2
    exit 2
    ;;
esac
