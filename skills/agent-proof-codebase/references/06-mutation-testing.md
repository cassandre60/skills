# Mutation testing: measuring whether tests actually check anything

Contents: why, how it works, adoption path, diff-scoped gating, reading survivors, equivalent mutants, tiers
and thresholds, tools and fallbacks, the agent loop.

## Why
Coverage tells you a line ran. Mutation testing tells you a test would notice if the line were wrong.
It is the most direct countermeasure to tests written to pass: assertion-free, tautological or mock-everything
tests leave mutants alive. Google's production system made it practical at scale by mutating only the lines a
change touches, filtering unproductive mutants, and surfacing survivors during code review.

## How it works
1. A tool makes small deliberate bugs ("mutants") in the code: flip `<` to `<=`, `+` to `-`, negate a condition,
   remove a statement, return a constant, drop an error check, swap arguments.
2. For each mutant, run the tests that cover that code.
3. Killed = some test failed (good). Survived = all passed (a gap). Score = killed / (killed + survived).

## Adoption path
1. **Pick scope:** R0/R1 modules first (core logic, persistence, importers). Exclude generated code, UI glue,
   logging, and trivial getters.
2. **Baseline:** run once, record the score per module in ratchet-baseline.json. Do not demand a number you
   cannot meet yet; ratchet from today's value.
3. **Make it fast:** run only tests that cover each mutated line (coverage-guided selection), use incremental
   mode and parallelism, set per-mutant timeouts (a hang counts as killed).
4. **Diff-scope it in PRs:** mutate only changed lines; fail the PR on surviving non-equivalent mutants in R0/R1
   code. Full runs go to the nightly tier.
5. **Triage survivors** (below), fix tests, then tighten the ratchet.
6. **Protect the config:** mutation settings, excluded paths and suppressions live in protected paths so an
   agent cannot exclude the code it is changing.

## Reading survivors
| Survivor type | Meaning | Action |
|---------------|---------|--------|
| Boundary (`<` vs `<=`) | no test at the edge | add edge-value test or boundary property |
| Removed statement/side effect | effect never asserted | assert the effect (state, call, persistence) |
| Return-value change | result not checked precisely | assert exact value or law |
| Negated condition | branch not exercised | add test that takes the other branch |
| Removed error handling | error path untested | add fault-injection or failing-input test |
| Unreachable/dead code | nothing can observe it | delete the code |
| Equivalent mutant | behavior truly unchanged | mark with justification in the protected suppression list |
| Defensive redundancy | duplicate check | decide: keep and assert via contract, or delete |

## Equivalent mutants and suppressions
Some mutants cannot change behavior (for example `x < 0` to `x <= 0` inside an absolute-value function, since
`-0 == 0`). Allow a suppression list with a written reason per entry, stored in a protected file and reviewed
by a human. Count suppressions as a ratchet (they may only decrease) so the list cannot grow quietly.

## Tiers and thresholds (starting guidance; calibrate to your baseline)
- R0: aim for a very high kill rate on changed lines, with every survivor justified in writing.
- R1: high kill rate on changed lines; full-module score ratcheted upward.
- R2: lighter; focus on logic extracted from widgets/controllers.
The exact numbers matter less than the rules that the score never falls and that survivors are explained.

## Tools and fallbacks
See ecosystem-map.md for per-language tools (StrykerJS, mutmut/cosmic-ray, cargo-mutants, PIT, go-mutesting/
gremlins, Stryker.NET, Muter, Mull, and others). Check maintenance status before adopting.
When the ecosystem has no mature tool (check before assuming), use `scripts/manual_mutation_runner.py`:
hand-pick 20 to 100 mutants around invariants (boundaries, ordering, negation, off-by-one, removed
statements, swapped arguments, dropped error handling) in a JSON list, run the test command per mutant,
fail on survivors, and declare provably equivalent ones with `"equivalent": true`. Hand-picked mutants guided
by the spec often find more than a blind mutation tool, and they double as red-team cases.

## The agent loop (use mutation feedback to improve tests)
1. Run mutation on the module; list survivors with file, line, and the mutated diff.
2. For each survivor: write a test that fails on the mutant and passes on the original, tagged with the
   relevant invariant. Verify by applying the mutant by hand or via the tool.
3. Never "fix" a survivor by excluding the line or marking it equivalent without an argument a human can check.
4. Re-run; update the ratchet; commit tests separately from product code when practical.
A second, fresh-context agent should review the new tests against the survivors, not the author.

## Pitfalls
- Slow suites make mutation impractical; fix test speed first (determinism seams, in-memory fakes).
- Flaky tests produce false kills; quarantine flakes before enabling the gate.
- A high score on trivial code is cheap; weight by risk tier and look at where survivors cluster.
- Mutation score can be gamed by tests that fail on any change (over-specified tests). Pair it with behavior
  tests reviewed by the rubric in test-strategies.md.
