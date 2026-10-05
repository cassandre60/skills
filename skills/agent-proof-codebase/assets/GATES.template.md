# GATES: the registry of everything that can reject a change

Protected path. Every gate is listed here with what it catches, what it cannot catch, how to run it
locally, and what a failure means. If a gate is not in this file, it does not exist.

Run everything locally with the same entry point CI uses: `./scripts/gates.sh [tier]`

| ID | Gate | Tier | Runtime | Catches | Known blind spots | Local command | Owner |
|----|------|------|---------|---------|-------------------|---------------|-------|
| G01 | format + lint + strict types | T0/T1 | <30s | style drift, unsafe casts, dead code | logic errors | `<cmd>` | |
| G02 | architecture rules (import boundaries) | T1 | <10s | core importing I/O or UI | dynamic imports | `<cmd>` | |
| G03 | banned APIs in core | T1 | <5s | hidden clocks/randomness | aliasing of banned calls | `python3 scripts/banned_apis_check.py --config .gates/banned-apis.json` | |
| G04 | unit + property tests (seeded) | T1 | <5min | regressions, invariant breaks | unspecified behavior | `<cmd>` | |
| G05 | spec traceability | T1 | <5s | invariants without tests | weak tests that cite IDs | `python3 scripts/spec_trace_check.py --spec SPEC.md --tests test` | |
| G06 | diff-scoped mutation testing | T2 | <20min | tests that do not actually check | equivalent mutants | `<cmd>` | |
| G07 | protected paths | T1 | <5s | referee edits without review | none (server side) | `python3 scripts/protected_paths_check.py --base origin/main` | |
| G08 | test-weakening tripwire | T1 | <5s | removed asserts, new skips/suppressions | subtle weakening | `python3 scripts/test_weakening_check.py --base origin/main` | |
| G09 | fail-to-pass (bug-fix PRs) | T2 | <10min | tests that cannot fail | test fails for wrong reason (use EXPECT_PATTERN) | `bash scripts/fail_to_pass_check.sh origin/main "<test cmd>"` | |
| G10 | ratchets (coverage, mutation, size, perf) | T1/T2 | <5min | silent regressions | metrics gamed in isolation | `python3 scripts/ratchet_check.py ...` | |
| G11 | dependency and secret scan | T1 | <2min | vulnerable deps, leaked keys | zero-days | `<cmd>` | |
| G12 | crash-consistency / simulation | T3 | nightly | lost or corrupt data | unmodeled faults | `<cmd>` | |
| G13 | fuzzing | T3 | nightly | crashes on malformed input | deep semantic bugs | `<cmd>` | |
| G14 | red-team corpus | T3 | nightly + on gate changes | weak gates | unknown unknowns | `python3 scripts/redteam_runner.py --gate-cmd "./scripts/gates.sh t1"` | |

## Gate health (update quarterly)
| Gate | Rejections | False positives | Flaky reruns | Escapes traced to it | Action |
|------|-----------|-----------------|--------------|----------------------|--------|

## Escapes log (every bug that got through: root cause and the gate added)
| Date | What escaped | Root cause | New gate / invariant / red-team case |
|------|--------------|------------|--------------------------------------|
