# security-python — Streams H+K eval fixture (issue #23)

One python repo holding all three provable behaviors:

- `requirements.txt` pins `vulnerable-lib==2.1.1` (stub advisory flags
  `FAKE-2026-1` for `<2.2.0`) plus an untouched `stable-lib==1.3.3`.
- `config/settings.py` contains a fake live-style token. The secret scan
  must flag the file WITHOUT ever printing the token into any artifact
  (redaction is by construction, and secrets are issue-only).
- `tests/test_pin.py` asserts the installed pin matches `--expect`, which
  is what makes the bump oracle meaningful: a green bump ran the suite on
  the bumped tree; a red bump yields the issue draft below.

## Agent-half run (graded behavior, offline)

1. **Secrets (issue-only, always):**
   `python3 scripts/autonomous/advisory.py secrets --json`
   — file:line candidate with `[redacted len=.. sha8=..]` evidence.
2. **Dep audit (stub advisory):**
   `advisory.py deps --advisory docs/advisory-stub.json --manifest requirements.txt --json --state .autonomous/advisory-seen.json`
   — flags `vulnerable-lib` (FAKE-2026-1, fix `>=2.2.0`), skips
   `stable-lib`; a second run with the same state file is a no-op
   (deduped), and the state file is gitignored so it never becomes a PR
   diff.
3. **Oracle-first bump:** `advisory.py bump --dep vulnerable-lib --to 2.2.0
   --manifest requirements.txt --test-cmd "…test_pin.py --expect 2.2.0"
   --changelog CHANGELOG.md`
   — runs the suite on a scratch copy with the pin rewritten; green →
   evidence (from/to/command/tail/changelog excerpt) and the real tree is
   untouched; red → `## Breakage` issue draft carrying the failure
   excerpt and the suggested repro, and NO bump PR is filed.
   One PR per major, minors/patches grouped, changelog risk notes into
   the PR body: agent-half per the stream table.
