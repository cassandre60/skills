# Writing the spec: invariants that tests and gates can enforce

Contents: what an invariant is, categories, how to extract them, quality checklist, traceability, examples,
review protocol, runtime contracts.

Template: `assets/SPEC.template.md`. Traceability script: `scripts/spec_trace_check.py`.

## What an invariant is
A single falsifiable sentence about behavior that must hold in every reachable state or for every input in
scope. It has a stable ID, a tier, an oracle (how a violation is detected) and gates (where it is enforced).
Tests map to invariants, not to functions. This keeps the suite aligned with intent and makes gaps visible.

Good: "Importing the same file twice yields state identical to importing it once."
Bad: "The importer works correctly." (not falsifiable)
Bad: "`parse()` returns a list." (implementation detail, trivial)

## Categories (use them as prompts when extracting)
- **Safety:** nothing bad happens (no data loss, no duplicate application, no negative balance, no leaked secret).
- **Liveness:** something good eventually happens (every queued job completes or fails visibly).
- **Idempotence:** repeating an operation changes nothing further.
- **Round trip:** decode(encode(x)) == x; export then import preserves everything.
- **Conservation:** totals preserved across transfers, merges, migrations.
- **Monotonicity:** better input never produces worse output; counters never decrease.
- **Equivalence:** the optimized implementation equals the simple reference on all inputs.
- **Bounds:** values stay in range; memory, time and size stay within budgets.
- **Ordering and determinism:** same inputs and seed produce same outputs; stable ordering.
- **Atomicity and durability:** all-or-nothing writes; acknowledged writes survive crashes.
- **Compatibility:** every shipped data version upgrades; old clients tolerate new data as specified.
- **Security and privacy:** untrusted input cannot escape its sandbox; sensitive data is never logged.
- **Failure behavior:** on error X the system does Y and never Z (partial state, silent success).

## Extracting invariants (sources, in order of yield)
1. **Bug history.** Every past bug becomes an invariant plus a regression test. Mine issue trackers, commits
   with "fix", audit documents, and support notes.
2. **Failure-mode walk.** For every write, ask what happens if the process dies right here, the disk is full,
   the input is malformed, the clock jumps, the network drops, the same request arrives twice.
3. **Read the code and ask "what must always hold here?"** Assertions, comments such as "never", "always",
   "must", and defensive checks reveal unwritten invariants.
4. **Types and schemas.** Constraints encoded in column types, enums and validators are invariants; promote them.
5. **Domain rules** from docs and from the user. Ask the user to confirm T0/T1 invariants; do not guess domain truth.
6. **Differences between the simple model and the real system.** Each difference is either a bug or a documented exception.

Aim for 30 to 60 invariants on a focused app, far fewer on a library. Prefer a short list of sharp invariants
to a long list of vague ones.

## Quality checklist (apply to each invariant)
- One sentence, no "and" hiding two claims.
- Falsifiable: you can describe an input or sequence that would violate it.
- Scoped: states what it applies to (module, operation, versions).
- Has an oracle: reference model, independent implementation, property, round trip, crash-and-reopen, benchmark.
- Has a tier (T0/T1/T2) so effort matches stakes.
- Not a restatement of the implementation (that makes a tautological test).
- Stable ID, never reused.

## Traceability convention
- Spec lines contain IDs like `INV-012`.
- Tests mention the ID in the test name, tag, or comment: `test('INV-012 review applied exactly once', ...)`.
- `spec_trace_check.py` fails on invariants with no test and on tests citing unknown IDs.
- The check proves coverage of intent, not test quality; mutation testing proves quality. Use both.

## Examples by domain
**Local database / persistence**
- INV: an acknowledged write survives kill at any instruction boundary.
- INV: schema upgrade from every shipped version preserves all rows and derived identities.
- INV: no query result depends on insertion order unless ordering is specified.
- INV: concurrent readers never observe partial transactions.

**Scheduler / ranking / scoring**
- INV: output is a pure function of (state, event, time).
- INV: a strictly better rating never schedules the next review earlier.
- INV: intervals stay within configured bounds; no overflow for 100 years of simulated history.
- INV: replaying the same event log reproduces the same state byte for byte.

**Parser / importer**
- INV: any byte string either parses to a valid structure or returns a typed error, never crashes or hangs.
- INV: normalize(normalize(x)) == normalize(x).
- INV: re-importing exported data yields identical state.
- INV: malformed input never leaves partial state behind.

**Network client**
- INV: every request has a timeout; retries are bounded and idempotent.
- INV: credentials are sent only to the configured host and never logged.
- INV: stale responses never overwrite newer state.

**UI state machine**
- INV: every reachable state has a defined exit; no dead-end screens.
- INV: displayed value equals the model value after every event sequence.
- INV: every interactive element is reachable and labeled for assistive technology.

**Ledger / money**
- INV: sum of all entries is conserved by every operation.
- INV: no operation can produce a negative balance unless explicitly allowed.

## Review protocol
- The spec is human-owned. When drafting, mark each invariant `proposed` until the user confirms T0/T1 ones.
- SPEC.md is a protected path; changes ship as a separate `spec-change` PR with the reason and the gates affected.
- Never edit the spec to make failing code pass. A failing invariant is a bug until a human decides otherwise.
- Periodically sample invariants and ask: what bug would this miss? Add sharper ones.

## Runtime contracts
Put cheap assertions in production code for preconditions, postconditions and impossible states (as mature
databases do). They turn every test, fuzz run and simulation into an invariant check and make failures loud
and local. Keep them side-effect free, and decide per build whether they run in release (usually yes for R0 code).
