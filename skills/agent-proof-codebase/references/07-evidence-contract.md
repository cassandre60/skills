# The evidence contract: what every kind of change must prove

Contents: why classes, the class table, mechanics per class, PR rules, how CI verifies, what "high-signal" means,
reviewer role.

Template: `assets/PULL_REQUEST_TEMPLATE.md`. Scripts: `fail_to_pass_check.sh`, `ratchet_check.py`,
`test_weakening_check.py`, `protected_paths_check.py`.

## Why classes
Different changes owe different proof. Declaring the class lets CI demand exactly the right evidence and lets
reviewers review the evidence instead of reading every line. A change that cannot name its class and attach its
proof is not ready. The PR template has a `Class:` line; CI maps labels or that line to checks.

## The table
| Class | Required evidence | Mechanical check | Why it resists gaming |
|-------|-------------------|------------------|-----------------------|
| bugfix | regression test that fails on the base commit's code and passes on the change | `fail_to_pass_check.sh` (use `EXPECT_PATTERN` so it must fail for the right reason) | a test written to pass cannot fail on old code |
| perf | before/after numbers on the pinned harness beyond noise, plus equivalence with the old implementation | benchmark job + ratchet + differential test | speed without equivalence fails the oracle |
| refactor | no test files modified, mutation score not lower, behavior-equivalence evidence | protected-test-edit check, mutation ratchet, differential/characterization tests | behavior preservation is demonstrated, not claimed |
| feature | SPEC.md updated first (spec-change reviewed), properties for each new invariant, complexity/size ratchets hold | spec traceability, mutation on new lines | forces intent before code |
| test-only | names the surviving mutant, invariant or ratchet it improves; must kill something new | mutation delta or ratchet increase | tests that kill nothing are noise |
| dependency | written justification, license/vulnerability check, size and startup delta | dependency scan, budget ratchet | each dependency is a liability |
| docs | no code changes | path filter | cheap lane stays cheap |
| gate-change | explains what it stops catching, red-team recall not reduced, human approval | protected paths + red-team run | the referee changes slowly and visibly |
| spec-change | reason, affected gates, human approval | protected paths | a wrong spec is enforced perfectly |

## Mechanics

### bugfix
1. Reproduce the bug with a failing test on the current code (name it, tag the invariant).
2. Fix the code. The test must pass.
3. CI checks out the base, drops only the changed test files onto old source, runs the tests: they must fail;
   then runs them on the change: they must pass. If a bug cannot be tested, the PR states why and a human decides.
4. Add the escape to GATES.md (what slipped through, why) and ask whether a general invariant or gate would
   have caught the whole class; if yes, add it (this is the antifragile loop).

### perf
1. State the budget or metric and the dataset (synthetic, seeded, at stated scales).
2. Keep the old implementation as an oracle; add a differential/property test that old == new over random inputs.
3. Measure before and after on the same pinned runner or device class: many iterations, warm-up, report median
   and spread; require improvement beyond noise (ratchets-and-budgets.md).
4. Prefer deterministic proxies in CI (allocations, instruction counts, bytes, query counts) and wall-clock on
   a stable benchmark runner or device.
5. Reject micro-optimizations without a measured win on a path the budget cares about.

### refactor
No test file edits (the protected-edit check enforces it). If tests must change, it is not a pure refactor:
split the PR. Show equivalence with characterization tests recorded before the change, differential tests
against the old code, and unchanged-or-better mutation score.

### feature
Update SPEC.md in a preceding or same-series spec-change PR. Each new invariant gets an oracle and a test tagged
with its ID. Scope stays within the product definition; out-of-scope features are rejected on principle.

### test-only
Must demonstrably increase strength: kills a named survivor, closes an uncovered invariant, or raises a
ratchet. Tests that only raise line coverage are rejected.

### dependency
Record why it is needed, alternatives considered, license, maintenance status, binary-size and startup cost,
and transitive additions. Prefer deleting dependencies to adding them.

## PR hygiene rules (mechanically checked where possible)
- One concern per PR; diff-size caps per class (for example refactor and feature PRs above a threshold must split).
- No drive-by formatting or renames in functional PRs.
- No new suppressions, skips, or lowered thresholds (tripwire).
- No edits to protected paths except in gate-change/spec-change PRs.
- Every PR includes an honest status section: ran, did not run, unverified.
- Commit style per project convention (for example Conventional Commits); machine-readable class label.

## How CI verifies
- Class from a label (for example `class:bugfix`) or the PR body; unknown class fails fast with instructions.
- Class-specific jobs (see assets/github-actions.gates.example.yml) run scripts from the trusted base checkout.
- Output follows the feedback format in ci-and-feedback.md so a weak agent can act on a rejection.

## What "high-signal" means (acceptance checklist)
A merged change must satisfy: states its class; solves a named problem or improves a measured property; carries
mechanically verified evidence for that class; touches no protected paths without human approval; weakens no
check; adds no unexplained dependency or suppression; leaves every ratchet equal or better.

## The reviewer's job
Review the verifier and the evidence, not every line: Is the invariant right? Does the test fail for the right
reason? Is the benchmark honest? Did a gate change slip into a product PR? This is a much smaller, higher-leverage
surface than the whole diff, and it is the part that should stay human.
