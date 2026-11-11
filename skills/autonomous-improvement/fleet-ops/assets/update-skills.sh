#!/bin/bash
# Self-updating installer for the skills bundle and fleet-ops runners.
#
# Pulls origin/main in the skills clone, verifies check_skills.py + tests
# before overwriting anything (rolling back automatically on failure), syncs
# every skill into $ORCH_SKILLS_INSTALL_DIR (~/.agents/skills) and refreshes
# installed runner scripts/units in $ORCH_INSTALL_DIR (~/.local/share/orchestrator).
#
# Environment overrides (all optional):
#   ORCH_SKILLS_REPO        git remote URL when cloning fresh (default: https://github.com/cassandre60/skills.git)
#   ORCH_SKILLS_CLONE       local git clone path (default: ${XDG_CACHE_HOME:-~/.cache}/orchestrator/skills-mirror)
#   ORCH_SKILLS_INSTALL_DIR destination for skill folders (default: $HOME/.agents/skills)
#   ORCH_INSTALL_DIR        destination for fleet-ops scripts (default: $HOME/.local/share/orchestrator)
#   ORCH_STATE_DIR          state directory holding lock & timestamps (default: $ORCH_INSTALL_DIR)
#   ORCH_SYSTEMD_USER_DIR   systemd --user unit dir (default: ${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user)
#   ORCH_WAKE_MODE          "stateless" or "fleet-wake" for wake.sh sync (default: auto-detect)
#   ORCH_UPDATE_SKIP_TESTS  set to 1 to skip unittest/shell suites (not recommended; default: 0)
#   ORCH_FORCE_SYNC         set to 1 to re-sync installed files even if main SHA did not move
set -euo pipefail

DEFAULT_REPO_URL="https://github.com/cassandre60/skills.git"
SKILLS_REPO_URL="${ORCH_SKILLS_REPO:-$DEFAULT_REPO_URL}"

detect_clone() {
  if [ -n "${ORCH_SKILLS_CLONE:-}" ]; then
    printf '%s\n' "$ORCH_SKILLS_CLONE"
    return 0
  fi
  printf '%s\n' "${XDG_CACHE_HOME:-$HOME/.cache}/orchestrator/skills-mirror"
}

SKILLS_CLONE="$(detect_clone)"
SKILLS_INSTALL_DIR="${ORCH_SKILLS_INSTALL_DIR:-$HOME/.agents/skills}"
INSTALL_DIR="${ORCH_INSTALL_DIR:-$HOME/.local/share/orchestrator}"
STATE_DIR="${ORCH_STATE_DIR:-$INSTALL_DIR}"
UNIT_DIR="${ORCH_SYSTEMD_USER_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}"
LOG="$STATE_DIR/skill-update.log"

mkdir -p "$SKILLS_INSTALL_DIR" "$INSTALL_DIR" "$STATE_DIR"

log() { printf '%s %s\n' "$(date -u +%FT%T%z)" "$*" | tee -a "$LOG"; }

exec 8>"$STATE_DIR/update-skills.lock"
if ! flock -n 8; then
  log "SKIP: another skill update is already running"
  exit 0
fi

# Hold guard: do not clobber an explicit operator/PR pin.
for hold in "$STATE_DIR/.update-hold" "$INSTALL_DIR/.update-hold" "${ORCH_HOLD_FILE:-}"; do
  if [ -n "$hold" ] && [ -f "$hold" ]; then
    log "SKIP: .update-hold exists at $hold ($(cat "$hold" 2>/dev/null || true))"
    exit 0
  fi
done

if [ ! -d "$SKILLS_CLONE/.git" ]; then
  log "cloning $SKILLS_REPO_URL into $SKILLS_CLONE"
  mkdir -p "$(dirname "$SKILLS_CLONE")"
  git clone -q "$SKILLS_REPO_URL" "$SKILLS_CLONE"
fi

cd "$SKILLS_CLONE"
if [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  log "ABORT: skills clone $SKILLS_CLONE has uncommitted tracked changes; refusing to overwrite"
  exit 1
fi
OLD="$(git rev-parse HEAD 2>/dev/null || echo "")"
if ! git fetch -q origin main; then
  log "WARN: git fetch origin main failed — keeping installed skills at ${OLD:-none}"
  exit 1
fi
NEW="$(git rev-parse origin/main)"

sync_needed="${ORCH_FORCE_SYNC:-0}"
if [ -z "$OLD" ] || [ "$OLD" != "$NEW" ]; then
  sync_needed="1"
elif [ -f "$INSTALL_DIR/stateless-wake.sh" ] && ! cmp -s "$SKILLS_CLONE/skills/autonomous-improvement/fleet-ops/assets/stateless-wake.sh" "$INSTALL_DIR/stateless-wake.sh"; then
  sync_needed="1"
elif [ ! -d "$SKILLS_INSTALL_DIR/autonomous-improvement" ]; then
  sync_needed="1"
fi

if [ "$sync_needed" != "1" ]; then
  log "no update: main unchanged at ${OLD:0:9} and installed assets match"
  date +%s >"$STATE_DIR/last_skills_update"
  exit 0
fi

if [ -z "$OLD" ] || [ "$OLD" != "$NEW" ]; then
  if [ -n "$OLD" ]; then
    COMMIT_COUNT="$(git log --oneline "$OLD..$NEW" | wc -l)"
    log "update available: ${OLD:0:9} -> ${NEW:0:9} ($COMMIT_COUNT commits)"
  else
    log "initial checkout of main @ ${NEW:0:9}"
  fi
  git checkout -q -B main origin/main
fi

rollback_clone() {
  if [ -n "$OLD" ]; then
    git reset -q --hard "$OLD"
  fi
}

if [ "${ORCH_UPDATE_SKIP_TESTS:-0}" != "1" ]; then
  log "gate 1: check_skills.py"
  if [ -f scripts/check_skills.py ]; then
    if ! python3 scripts/check_skills.py >"$STATE_DIR/skill-update-check.out" 2>&1; then
      log "ABORT: check_skills.py failed — install unchanged, clone rolled back"
      cat "$STATE_DIR/skill-update-check.out" >>"$LOG" || true
      rollback_clone
      exit 1
    fi
  fi

  log "gate 2: autonomous-improvement tests"
  mkdir -p "$STATE_DIR/tmp"
  if ! (
    cd "$SKILLS_CLONE/skills/autonomous-improvement" && \
    TMPDIR="$STATE_DIR/tmp" PYTHONPATH=tests timeout 300 python3 -m unittest discover -s tests >"$STATE_DIR/skill-update-tests.out" 2>&1 && \
    TMPDIR="$STATE_DIR/tmp" bash fleet-ops/tests/stateless_wake_test.sh >>"$STATE_DIR/skill-update-tests.out" 2>&1
  ); then
    log "ABORT: test suite failed — install unchanged, clone rolled back"
    tail -15 "$STATE_DIR/skill-update-tests.out" >>"$LOG" || true
    rollback_clone
    exit 1
  fi
fi

# Sync every skill folder into $SKILLS_INSTALL_DIR
SKILL_COUNT=0
for s in "$SKILLS_CLONE"/skills/*/; do
  [ -d "$s" ] || continue
  name="$(basename "$s")"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude='__pycache__' --exclude='.autonomous' "$s" "$SKILLS_INSTALL_DIR/$name/"
  else
    rm -rf "${SKILLS_INSTALL_DIR:?}/${name:?}"
    cp -a "$s" "$SKILLS_INSTALL_DIR/$name"
  fi
  SKILL_COUNT=$((SKILL_COUNT + 1))
done
log "installed $SKILL_COUNT skill(s) from main @ ${NEW:0:9}"

# Sync fleet-ops runnable assets into $INSTALL_DIR
ASSETS_SRC="$SKILLS_INSTALL_DIR/autonomous-improvement/fleet-ops/assets"
if [ -d "$ASSETS_SRC" ]; then
  for asset in stateless-wake.sh snapshot.sh watchdog.sh update-skills.sh; do
    if [ -f "$ASSETS_SRC/$asset" ]; then
      install -m 0755 "$ASSETS_SRC/$asset" "$INSTALL_DIR/$asset"
    fi
  done

  # Refresh wake.sh cleanly without clobbering stateless vs fleet.py-wake mode
  wake_mode="${ORCH_WAKE_MODE:-auto}"
  if [ "$wake_mode" = "auto" ]; then
    if [ -f "$INSTALL_DIR/wake.sh" ] && grep -qE "ORCH_MODEL_LADDER|stateless wake launcher|stateless-wake" "$INSTALL_DIR/wake.sh"; then
      wake_mode="stateless"
    elif [ ! -f "$INSTALL_DIR/wake.sh" ]; then
      wake_mode="stateless"
    else
      wake_mode="fleet-wake"
    fi
  fi
  if [ "$wake_mode" = "stateless" ] && [ -f "$ASSETS_SRC/stateless-wake.sh" ]; then
    install -m 0755 "$ASSETS_SRC/stateless-wake.sh" "$INSTALL_DIR/wake.sh"
    log "synced $INSTALL_DIR/wake.sh from stateless-wake.sh"
  elif [ -f "$ASSETS_SRC/wake.sh" ]; then
    install -m 0755 "$ASSETS_SRC/wake.sh" "$INSTALL_DIR/wake.sh"
    log "synced $INSTALL_DIR/wake.sh from wake.sh"
  fi

  # Refresh systemd user units if already installed
  if [ -d "$UNIT_DIR" ]; then
    unit_changed=0
    for unit in skills-updater.service skills-updater.timer orchestrator-wake.service orchestrator-wake.timer orchestrator-snapshot.service orchestrator-snapshot.timer; do
      if [ -f "$UNIT_DIR/$unit" ] && [ -f "$ASSETS_SRC/$unit" ]; then
        tmp_u="$(mktemp)"
        cp "$ASSETS_SRC/$unit" "$tmp_u"
        if [ "$INSTALL_DIR" != "$HOME/.local/share/orchestrator" ]; then
          sed -i "s|%h/.local/share/orchestrator/watchdog.sh|$INSTALL_DIR/watchdog.sh|g; s|%h/.local/share/orchestrator/snapshot.sh|$INSTALL_DIR/snapshot.sh|g; s|%h/.local/share/orchestrator/wake.sh|$INSTALL_DIR/wake.sh|g; s|%h/.local/share/orchestrator/update-skills.sh|$INSTALL_DIR/update-skills.sh|g" "$tmp_u"
        fi
        if [ "$STATE_DIR" != "$HOME/.local/share/orchestrator" ]; then
          sed -i "s|%h/.local/share/orchestrator/watchdog.env|$STATE_DIR/watchdog.env|g" "$tmp_u"
        fi
        if ! cmp -s "$tmp_u" "$UNIT_DIR/$unit"; then
          install -m 0644 "$tmp_u" "$UNIT_DIR/$unit"
          unit_changed=1
        fi
        rm -f "$tmp_u"
      fi
    done
    if [ "$unit_changed" = "1" ] && command -v systemctl >/dev/null 2>&1; then
      systemctl --user daemon-reload >/dev/null 2>&1 || true
    fi
  fi
fi

date +%s >"$STATE_DIR/last_skills_update"
if [ -n "$OLD" ] && [ "$OLD" != "$NEW" ]; then
  log "commits in this update:"
  git log --oneline "$OLD..$NEW" | while IFS= read -r line; do log "  $line"; done
fi
log "update complete"
