# Architecture that makes bugs hard to write and easy to catch

Contents: functional core and imperative shell, determinism seams, enforcement, data modeling, storage,
concurrency, performance-aware design, runtime contracts.

Gates are only as good as the code is testable. Many "hard to test" problems are architecture problems.
When retrofitting, change structure only behind characterization tests (assess-and-plan.md section 6).

## Functional core, imperative shell
- **Core:** pure functions and immutable data holding all decisions: domain rules, scheduling, parsing,
  validation, state transitions. No I/O, UI, clock, randomness, globals, or framework imports.
- **Shell:** thin adapters for storage, network, UI, platform. They call the core and perform the effects it
  returns. Keep them boring and covered by integration and crash-consistency tests.
- A useful shape for state: `step(state, event) -> (newState, effects)`. It is easy to property-test, replay
  from logs, simulate for years, and compare against a reference model.
- Rule of thumb: if you cannot run the core's tests with no emulator, no database and no network, the boundary
  is in the wrong place.

## Determinism seams
Inject every source of nondeterminism behind a small interface, with a real and a deterministic fake:
| Source | Interface | Fake used in tests |
|--------|-----------|--------------------|
| Time | `Clock.now()` | manual clock, jumps, skew, DST, leap seconds |
| Randomness | `Random` with seed | seeded generator; seed logged on every run |
| Storage | repository/port | in-memory model + fault-injecting wrapper |
| Filesystem | file port | in-memory FS with failures (disk full, partial write) |
| Network | HTTP port | scripted responses, drops, delays, duplicates, reorderings |
| Concurrency | scheduler/executor | deterministic single-threaded executor with controlled interleavings |
| Environment | config object | explicit values, no global reads |

Payoff: every failure replays from a seed, simulated time runs faster than real time, and fault injection
becomes a loop rather than a hope.

## Enforce the architecture with tools, not discipline
- Import/boundary rules: the core may not import UI, I/O, platform or storage packages. Use your ecosystem's
  tool (ecosystem-map.md) or a plain test that scans imports.
- Banned APIs in core paths: `scripts/banned_apis_check.py` with `assets/banned-apis.example.json` (wall clock,
  global randomness, file/network/UI imports). Exemptions need `gate-allow: <reason>` on the line and appear in output.
- Strictest analyzer/type settings available; treat warnings as errors on gated paths.
- Size caps (file length, function length, cyclomatic complexity) as ratchets, not hard walls on legacy code.
- Dependency budget: every direct dependency carries a written justification; additions show up in review.

## Data modeling: make illegal states unrepresentable
- Use sum types/enums with exhaustive matching for states and events instead of booleans and nullable fields.
- Wrap identifiers and units in distinct types so IDs, durations and counts cannot be swapped.
- Prefer immutable values and explicit transitions over shared mutable objects.
- Model errors as typed results at module boundaries; reserve exceptions for bugs. Never swallow errors.
- Validate at the boundary once, then pass validated types inward.

## Storage design
- Wrap multi-step writes in transactions; make each user action atomic and idempotent (idempotency keys or
  natural keys) so retries and replays are safe.
- Identity: choose stable, canonical keys deliberately (normalization rules written in the spec). Changing an
  identity scheme after release means a migration across all user data.
- Migrations: every shipped schema version has a frozen fixture; tests upgrade each to current and compare
  with an independent expectation. Migrations are forward-only and tested for interruption.
- Time: store instants as integers (epoch) in UTC; do calendar math in one place with an injected clock and zone.
- Push filtering, ordering and limits into the database with indexes; do not load whole tables to filter in memory.
- Keep an export/import path that round-trips everything; it doubles as a backup and as a test oracle.

## Concurrency
- Prefer single-writer designs and message passing to shared mutable state.
- Make races testable: deterministic executor in tests plus a stress test with the platform's race detector
  where available.
- Long operations (imports, hashing) run off the UI thread with cancellation and progress; state updates are
  applied exactly once on completion (generation counters prevent stale results from applying).

## Performance-aware design
- Choose data layout and algorithms for the dominant operation; document complexity in the spec for R0/R1 code.
- Prefer compact keys and integer encodings over strings in hot maps; avoid per-row parsing of text formats.
- Budget memory and startup explicitly (ratchets-and-budgets.md); lazy-load what the first screen does not need.
- Keep the simple implementation as a test oracle for the optimized one (evidence-contract.md, perf class).

## Runtime contracts
Use assertions for preconditions, postconditions and impossible states in core and storage code. They convert
tests, fuzzers and simulations into invariant checkers. Keep them cheap and side-effect free.
