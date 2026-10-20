#!/usr/bin/env bash
# Verify that the `skills` installer discovers every skill in this repository.
#
# `npx skills add cassandre60/skills` does not fail when a SKILL.md cannot be
# parsed: the installer prints a "Skipped ..." warning and installs the rest.
# The result is a silent partial install, which is exactly what happened when
# autonomous-improvement/SKILL.md carried a description with an unescaped
# ": " and therefore was not valid YAML.
#
# This gate reproduces the installer's own discovery step against the working
# tree and fails when anything is skipped, or when the number of discovered
# skills does not match the number of skill directories.
#
# Usage: scripts/check_discovery.sh [skills-cli-version]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI_VERSION="${1:-${SKILLS_CLI_VERSION:-1.7.4}}"

cd "$REPO_ROOT"

expected="$(find skills -mindepth 2 -maxdepth 2 -name SKILL.md | wc -l | tr -d '[:space:]')"
if [ "$expected" -eq 0 ]; then
  echo "No skills found under skills/ — nothing to verify." >&2
  exit 1
fi

set +e
output="$(npx --yes "skills@${CLI_VERSION}" add . --list 2>&1)"
status=$?
set -e

clean="$(printf '%s\n' "$output" | sed -E 's/\x1b\[[0-9;]*[A-Za-z]//g')"
printf '%s\n' "$clean"

if [ "$status" -ne 0 ]; then
  echo "::error::the skills installer exited with status ${status}" >&2
  exit 1
fi

if printf '%s\n' "$clean" | grep -q 'Skipped '; then
  echo "::error::the skills installer skipped one or more skills (see the warnings above)" >&2
  exit 1
fi

found="$(printf '%s\n' "$clean" | sed -nE '/node_modules/!s/.*Found ([0-9]+) skill.*/\1/p' | head -n 1)"
if [ -z "$found" ]; then
  echo "::error::could not read the discovered skill count from the installer output" >&2
  exit 1
fi

if [ "$found" -ne "$expected" ]; then
  echo "::error::the skills installer discovered ${found} of ${expected} skills" >&2
  exit 1
fi

echo "OK: the skills installer discovers all ${expected} skills."
