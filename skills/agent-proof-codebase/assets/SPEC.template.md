# SPEC: what must always be true

Human-owned. This file is a protected path: edits need a dedicated "spec-change" PR and human review.
Gates verify that the code conforms to this spec; they cannot tell you the spec is right, so read
every line as if it were a contract you would defend.

How to use
- One invariant = one falsifiable sentence with a stable ID (INV-001, never renumbered or reused).
- Every invariant names how a violation would be detected (the "Oracle") and where it is enforced ("Gates").
- Tests cite the ID in the test name or a comment so `scripts/spec_trace_check.py` can verify coverage.
- Retire an invariant by marking it `RETIRED (reason, date)`, never by deleting it.

Tier legend: T0 = loss/corruption/security if violated, T1 = wrong behavior, T2 = degraded UX/perf.

---

## A. Data integrity (T0)

### INV-001 Accepted input is never lost
Once an operation has been acknowledged to the user, its effect survives process kill and power loss.
- Oracle: crash-consistency test killing at every write boundary, then reopening and comparing with the model.
- Gates: crash-consistency suite, fault-injecting storage fake.
- Tier: T0

### INV-002 Each user action is applied exactly once
Retries, double taps and replays do not change state beyond the first application.
- Oracle: model-based test that replays random command sequences with duplicates.
- Gates: stateful property test, idempotency-key unit tests.
- Tier: T0

### INV-003 Import is idempotent and order-independent
Importing the same data twice, or in a different order, yields identical state.
- Oracle: property test comparing final state across permutations.
- Gates: property suite, differential test against a reference importer.
- Tier: T0

## B. Correctness of core logic (T1)

### INV-010 <core function> is a pure function of its inputs
No hidden clock, randomness, or global state.
- Oracle: banned-API gate + determinism test (same inputs, same outputs, 1000 random cases).
- Gates: `banned_apis_check.py`, property test.
- Tier: T1

### INV-011 <scheduling/ordering/score> is monotone in <input>
Improving <input> never makes <output> worse.
- Oracle: metamorphic property test.
- Tier: T1

## C. Migrations and compatibility (T0)

### INV-020 Every shipped schema version upgrades to the current one without data loss
- Oracle: migration test per shipped version using frozen fixture databases.
- Gates: migration suite in tier-2 CI.
- Tier: T0

## D. Budgets (T2)

### INV-030 <operation> completes within <N> ms at <dataset size> on the reference device class
- Oracle: pinned benchmark with budget and ratchet.
- Gates: perf harness, `ratchet_check.py`.
- Tier: T2

---

## Change log
| Date | Invariant | Change | Reviewer |
|------|-----------|--------|----------|
