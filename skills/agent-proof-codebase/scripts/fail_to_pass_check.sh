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
REGEX="${TEST_PATH_REGEX:-(^|/)(tests?|__tests__|spec|specs|integration_test)(/|$)|(_test|_spec|\.test|\.spec)\.|(^|/)test_[^/]*$}"

mapfile -t TEST_FILES < <(git diff --name-only --diff-filter=AM --no-renames "$BASE...$HEAD_REF" | grep -E "$REGEX" || true)
if [[ ${#TEST_FILES[@]} -eq 0 ]]; then
  echo "FAIL: the change adds or modifies no test files; a bug fix must ship a regression test." >&2
  exit 1
fi
echo "test files from the change:"; printf '  %s\n' "${TEST_FILES[@]}"

WT="$(mktemp -d)"
cleanup() { git worktree remove --force "$WT" >/dev/null 2>&1 || rm -rf "$WT"; }
trap cleanup EXIT
git worktree add --detach "$WT" "$BASE" >/dev/null 2>&1 || { echo "cannot create worktree at $BASE" >&2; exit 2; }

for f in "${TEST_FILES[@]}"; do
  mkdir -p "$WT/$(dirname "$f")"
  git show "$HEAD_REF:$f" > "$WT/$f" || { echo "cannot read $f at $HEAD_REF" >&2; exit 2; }
done

echo "== running tests against OLD source + NEW tests (must fail) =="
OUT_BASE="$(cd "$WT" && bash -c "$CMD" 2>&1)"; RC_BASE=$?
echo "$OUT_BASE" | tail -n 40
if [[ $RC_BASE -eq 0 ]]; then
  echo "FAIL: tests pass on the base code, so they do not demonstrate the bug." >&2; exit 1
fi
if [[ -n "${EXPECT_PATTERN:-}" ]] && ! grep -Eq "$EXPECT_PATTERN" <<<"$OUT_BASE"; then
  echo "FAIL: base run failed, but output does not match EXPECT_PATTERN ($EXPECT_PATTERN); check it fails for the right reason." >&2
  exit 1
fi

echo "== running tests on the change itself (must pass) =="
git checkout -q --detach "$HEAD_REF" 2>/dev/null || true
bash -c "$CMD" >/tmp/f2p_head.log 2>&1; RC_HEAD=$?
tail -n 20 /tmp/f2p_head.log
if [[ $RC_HEAD -ne 0 ]]; then echo "FAIL: tests do not pass on the change." >&2; exit 1; fi
echo "OK: fail-to-pass verified"
