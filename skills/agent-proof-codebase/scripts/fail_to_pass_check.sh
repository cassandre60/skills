#!/usr/bin/env bash
# Verify the fail-to-pass rule for a bug-fix change.
#
# A bug-fix PR must ship a test that FAILS on the base commit's code and PASSES on the PR.
# We take the PR's changed test files, drop them onto a clean checkout of BASE (old source),
# run the test command (must fail), then run it on HEAD (must pass).
#
# Usage:
#   fail_to_pass_check.sh BASE_REF "TEST COMMAND" [HEAD_REF]
# Env:
#   TEST_PATH_REGEX   extended regex selecting test files (default covers common layouts)
#   EXPECT_PATTERN    optional regex that must appear in the failing output, so that a
#                     compile error or unrelated breakage does not count as "fails"
# Exit codes: 0 ok, 1 not fail-to-pass, 2 usage/setup error.
set -uo pipefail

BASE="${1:-}"; CMD="${2:-}"; HEAD_REF="${3:-HEAD}"
if [[ -z "$BASE" || -z "$CMD" ]]; then
  echo "usage: $0 BASE_REF \"TEST COMMAND\" [HEAD_REF]" >&2; exit 2
fi
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "error: not inside a git repository" >&2; exit 2; }
cd "$REPO_ROOT"
REGEX="${TEST_PATH_REGEX:-(^|/)(tests?|__tests__|spec|specs|integration_test)(/|$)|(_test|_spec|\.test|\.spec)\.|(^|/)test_[^/]*$}"

mapfile -t TEST_FILES < <(git -c core.quotePath=false diff --name-only --diff-filter=AM --no-renames "$BASE...$HEAD_REF" | grep -E "$REGEX" || true)
if [[ ${#TEST_FILES[@]} -eq 0 ]]; then
  echo "FAIL: the change adds or modifies no test files; a bug fix must ship a regression test." >&2
  exit 1
fi
echo "test files from the change:"; printf '  %s\n' "${TEST_FILES[@]}"

WT_BASE="$(mktemp -d)"
WT_HEAD="$(mktemp -d)"
HEAD_LOG="$(mktemp)"
cleanup() {
  git worktree remove --force "$WT_BASE" >/dev/null 2>&1 || rm -rf "$WT_BASE"
  git worktree remove --force "$WT_HEAD" >/dev/null 2>&1 || rm -rf "$WT_HEAD"
  git worktree prune >/dev/null 2>&1 || true
  rm -f "$HEAD_LOG"
}
trap cleanup EXIT
git worktree add --detach "$WT_BASE" "$BASE" >/dev/null 2>&1 || { echo "cannot create worktree at $BASE" >&2; exit 2; }

for f in "${TEST_FILES[@]}"; do
  mkdir -p "$WT_BASE/$(dirname "$f")"
  git show "$HEAD_REF:$f" > "$WT_BASE/$f" || { echo "cannot read $f at $HEAD_REF" >&2; exit 2; }
done

echo "== running tests against OLD source + NEW tests (must fail) =="
OUT_BASE="$(cd "$WT_BASE" && bash -c "$CMD" 2>&1)"; RC_BASE=$?
echo "$OUT_BASE" | tail -n 40
if [[ $RC_BASE -eq 0 ]]; then
  echo "FAIL: tests pass on the base code, so they do not demonstrate the bug." >&2; exit 1
fi
if [[ -n "${EXPECT_PATTERN:-}" ]] && ! grep -Eq "$EXPECT_PATTERN" <<<"$OUT_BASE"; then
  echo "FAIL: base run failed, but output does not match EXPECT_PATTERN ($EXPECT_PATTERN); check it fails for the right reason." >&2
  exit 1
fi

echo "== running tests on the change itself (must pass) =="
git worktree add --detach "$WT_HEAD" "$HEAD_REF" >/dev/null 2>&1 || { echo "cannot create worktree at $HEAD_REF" >&2; exit 2; }
(cd "$WT_HEAD" && bash -c "$CMD") >"$HEAD_LOG" 2>&1; RC_HEAD=$?
tail -n 20 "$HEAD_LOG"
if [[ $RC_HEAD -ne 0 ]]; then echo "FAIL: tests do not pass on the change." >&2; exit 1; fi
echo "OK: fail-to-pass verified"
