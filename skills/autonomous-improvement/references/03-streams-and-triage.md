# Streams and Triage Checklists

## Stream A — Mutation Testing

1. Pick a file from the active target list (R0/R1 first).
2. Run the engine:
   ```bash
   python3 scripts/autonomous/mutation_engine.py \
     --source <file> \
     --test-cmd "<targeted test command>" \
     --max-mutants 20 \
     --output-issue docs/tasks/mutant_issue.md
   ```
3. Triage every survivor (see `01-mutation-engine.md`). For each non-equivalent gap:
   - Write a test that **fails on the mutant, passes on clean code**.
   - Cite the invariant it defends (`// SPEC INV-xxx` or equivalent).
   - Keep the PR to one file + one test file.

## Stream B — Adversarial Test Audit

Pick a test file and ask:

**Can it actually fail?**
- Does it read back real state, or only assert that a write didn't throw?
- Would it still pass if the code it claims to cover did the wrong thing?
- Fix: snapshot the table / count writes / assert the side effect.

**Does it assert behavior or the mock?**
- Does the mock return X and the test assert X, without exercising domain logic?
- Fix: in-memory entities, fake clocks, in-memory stores instead of mocks.

**Is it a timing bomb?**
- `sleep`, `Future.delayed`, `setTimeout`, `time.sleep` with a fixed duration?
- Under CPU contention less work fits in that window — the test flakes.
- Fix: poll on a condition or use a virtual clock.

## Stream C — Property Fuzzing (Planned)

Needs a nightly harness. Examples:
- Round-trip: `import -> export -> import` structural equivalence (not literal string identity — normalization breaks that).
- Monotonicity: higher stability never shortens intervals; difficulty clamped; retrievability in `[0,1]`.
- State machine: random action sequences under every continuity/edge mode; no deadlocks, no negative counts, no uncaught exceptions.

Don't start this stream to "use tokens" before the harness exists and is proven nightly.

## Stream D — Migration Archeology (Planned)

Needs fixture databases per historical schema version.
- One on-disk fixture per old version, populated with realistic data.
- Forward migration to current version, assert zero lost rows.
- Orphan-row and cascade regression tests.

Track it in the project plan. Don't run it as busywork per PR.
