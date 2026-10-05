## Quality gates: rules for any agent or contributor working in this repo

This repository is gated. Your job is to get a change accepted by the gates honestly, not to make the
gates go green. Read `SPEC.md` (invariants), `GATES.md` (what can reject you) and `.gates/` before editing.

1. Classify your change (bugfix, perf, refactor, feature, test-only, dependency) and gather the evidence
   that class requires (see PULL_REQUEST_TEMPLATE.md). A bugfix starts with a test that fails on the current code.
2. Never edit the referee to get a pass: SPEC.md, GATES.md, CODEOWNERS, .gates/, redteam/, scripts/, CI config,
   lint/type/coverage/mutation configs, ratchet baselines, frozen fixtures. If you think a gate is wrong, stop
   and propose a separate gate-change with evidence.
3. Never weaken a check: no deleting or loosening assertions, no skip/ignore/xfail, no new lint or type suppressions,
   no widened tolerances, no mocking the thing under test, no hardcoding expected values, no catch-all error handling.
4. When a gate fails, fix the code. Read the counterexample, reproduce it with the printed seed/command, and address
   the root cause. After 3 failed attempts on the same gate, stop and report what you learned instead of iterating blindly.
5. Run the same gates locally (`./scripts/gates.sh`) before pushing; do not use `--no-verify`.
6. Keep changes small and single-purpose. No drive-by refactors, reformatting, or dependency additions without
   a written justification in the PR.
7. Report honestly: what you ran, what passed, what you could not run, and what remains unverified.
