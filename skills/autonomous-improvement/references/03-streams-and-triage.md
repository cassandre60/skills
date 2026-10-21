# Streams and Triage

Two streams are available. Two more are planned and must not be promised to a
user or started as busywork.

## Stream A — mutation testing

1. `ledger.py next --stream A` selects the next source file with a mapped test
   that is not audited at its current composite hash. Priority paths from config
   come first. (Risk tiers such as R0/R1 are project-defined and optional; see
   `gates-and-vocabulary.md`.)
2. Run the engine with a targeted command for the mapped test:
   ```bash
   python3 scripts/autonomous/mutation_engine.py \
     --source src/app.py \
     --test-cmd "<targeted test command>" \
     --max-mutants 20 --seed 0 \
     --output-json .autonomous-last-run.json \
     --output-issue docs/tasks/mutant_issue.md
   ```
3. Triage every `SURVIVED` mutant (see `01-mutation-engine.md`). For each real
   gap:
   - State the behavior the mutant breaks.
   - Write a test that is **red with the mutant applied** and **green on clean
     source**. It passes on `origin/main` by construction.
   - Cite the invariant it defends if the repo has one.
   - Keep the PR to one test file plus at most one fixture.
4. Record the audit as `COMPLETED` (with the PR) or `SOUND` (no PR needed).
   Include the seed and scope in the summary.

Do not mutate generated files (`*.g.dart`, `*.freezed.dart`, build output).
Exclude them in config rather than adding engine special cases.

## Stream B — adversarial test audit

Pick a test file from `candidates.B`. Read it and ask:

### Can it actually fail? (vacuity)

- Does it assert real state, or only that a call did not throw?
- Would it still pass if the implementation did the opposite?
- Example smell: a test named "does not rewrite the table" that never reads the
  table.
- Fix: snapshot or count the side effect; assert the value, not the call.

### Does it assert behavior or the mock? (over-mocking)

- Does the mock return X and the test assert X, with no domain logic in between?
- Fix: use in-memory entities, fake clocks, or in-memory stores. Keep a mock only
  at an external boundary you do not own.

### Is it a timing bomb? (flakiness)

- Fixed `sleep`, `Future.delayed`, or `setTimeout` windows shrink under CPU
  contention.
- Fix: poll a condition with a deadline, or inject a virtual clock. Never add a
  sleep to "stabilize" a test.
- A flaky test is not proven flaky by one red run. Re-run it in isolation;
  report the reproduction command.

Every Stream B finding needs evidence: the line, why it cannot fail, and the
proposed stronger assertion. "Rename this test" or "reformat this file" is not a
finding.

## Triage checklist (both streams)

- [ ] Is the finding real on the current base, not an old worktree?
- [ ] Is it already covered by another test or an open PR?
- [ ] Is the proposed test fail-to-pass under the wording of Law 1?
- [ ] Is the change limited to the target and its test?
- [ ] Did you avoid editing the referee (specs, gates, CI, lint config, ratchets)?

If any box is unchecked, stop and record why; a documented "no real gap" is a
valid `SOUND` result.

## Stream C — property fuzzing / model checking (planned, unavailable)

This needs a nightly harness that can run thousands of randomized transitions
and shrink failures. Until that exists, do not promise or run it. Typical
targets for later: round-trip idempotence, scheduling monotonicity, queue
state-machine deadlocks.

## Stream D — migration and data-preservation archeology (planned, unavailable)


This needs fixture databases for every historical schema version and a
migration harness. Until then, do not run it as per-PR busywork. Typical targets
for later: zero-row-loss migrations, orphan-row checks, cascade behavior.

For the full planned catalog (streams C–L, including defect repair,
flakiness, performance, security, docs truthfulness, simplification,
dependency evolution, and the capped meta stream), the stream contract a
new stream must pass, and the autonomy tiers it earns, see
`08-recursive-self-improvement.md`. C and D above keep their original
sketches; `08` is the admission gate for all of them.
