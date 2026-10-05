# Test strategies that can fail

Contents: the menu, choosing by module type, property tests, model-based tests, differential tests,
metamorphic tests, crash-consistency, fuzzing, simulation, characterization tests, beyond testing,
anti-slop catalogue, test quality rubric, conventions.

A test is worth keeping only if it would fail when the code is wrong. Mutation testing (mutation-testing.md)
is the final judge; these strategies are how you write tests that survive it.

## The menu
| Strategy | Oracle | Best for | Typical catch | How an agent games it |
|----------|--------|----------|---------------|-----------------------|
| Example-based | hand-picked expected values | documentation, regressions, edge cases found by other means | specific known bugs | hardcode, tautology |
| Property-based | a law that holds for all inputs | pure functions, parsers, codecs, normalizers | edge cases nobody listed | weak properties ("returns something") |
| Model-based (stateful) | simple reference model run in lockstep | stateful systems, storage, schedulers, UI state | sequence bugs, lost updates | model copied from implementation |
| Differential | independent implementation or reference vectors | algorithms, legality rules, optimized vs simple code | semantic drift | oracle derived from the code under test |
| Metamorphic | relation between outputs of related inputs | no exact oracle (ranking, search, ML-ish) | monotonicity/symmetry bugs | trivial relations |
| Round trip | inverse function | serializers, import/export, migrations | lossy encoding | comparing only a subset of fields |
| Characterization (golden master) | recorded current behavior | legacy code before refactor | unintended change | blind re-recording |
| Crash-consistency / fault injection | invariants after interruption | persistence, migrations, imports | corruption, partial writes | faults never actually injected |
| Fuzzing | no crash, no hang, sanitizer clean, invariants | untrusted input, parsers | crashes, memory/time blowups | tiny corpus, no invariants |
| Deterministic simulation | invariants checked continuously under injected faults | distributed/concurrent/long-running behavior | rare interleavings | simulator that never fails |
| Concurrency/race tests | race detector, deterministic interleavings | shared state, async | races, deadlocks | single-threaded runs |
| Migration tests | frozen fixtures from every shipped version | any persisted data | data loss on upgrade | only latest version tested |
| Benchmarks | budgets and ratchets | hot paths | perf regressions | measuring something trivial |
| Contract tests | recorded interface expectations | API boundaries | silent protocol drift | stale contracts |
| UI behavior/golden tests | rendered output, semantics tree | key flows, accessibility | broken flows, missing labels | auto-updated goldens |

## Choosing by module type
- Pure core logic (R1): property + differential + metamorphic; examples only for documented edge cases.
- Persistence and migrations (R0): model-based + crash-consistency + migration fixtures + fuzz of stored data.
- Importers/parsers (R0/R1): round trip + fuzz + differential against a reference parser + idempotence.
- Schedulers/scorers (R1): simulation of years of events against a simple model; monotonicity and bound
  properties; reference vectors from the published algorithm when one exists.
- Network clients (R1/R2): scripted fake server with faults; properties on retry/idempotency; never real network in gates.
- UI (R2): flow tests of key paths, semantics/accessibility assertions, minimal goldens with reviewed updates.

## Property-based tests: how to write ones that bite
- Generators must reach edges: empty, one element, maximum size, duplicates, unicode, boundary numbers,
  adversarial structures. Bias generators toward corner cases; log generator statistics when unsure.
- Always log the seed and shrunk counterexample; a failing run must replay with one command.
- Do not re-implement the function inside the test; assert laws instead:
  idempotence `f(f(x)) == f(x)`, round trip `decode(encode(x)) == x`, invariant preserved, commutativity or
  order independence, monotonicity, conservation, "equals simple reference", "never throws except typed errors".
- Run a moderate example count in PR gates and a high count with random seeds nightly.
- Properties are specs: tag with INV IDs and keep them readable.

## Model-based (stateful) tests: recipe
1. Define the command set (operations a user or caller can perform), each with preconditions.
2. Write a trivially simple model (in-memory map/list) with obviously correct semantics.
3. Generate random command sequences (including duplicates, retries, interleavings).
4. Apply each command to model and system; after every step compare observable state.
5. On failure, shrink the sequence to a minimal reproducer and print it as a replayable script.
6. Add commands for restarts (close and reopen), clock jumps, and faults to cover durability and time.
The model must come from the spec, not from reading the implementation, or you will copy its bugs.

## Differential testing
Pick an oracle written independently: another library, a reference implementation in another language,
published test vectors, or the slow-but-obvious version of your own algorithm. Compare outputs on random and
curated inputs. When optimizing, the old implementation stays in the test tree as the oracle.
Generate golden vectors by running the independent oracle, not your code, and commit them with provenance.

## Metamorphic testing
When no exact answer is known, assert relations: adding irrelevant data does not change the result; a
better input never scores worse; permuting inputs permutes outputs; scaling inputs scales outputs.

## Crash-consistency and fault injection
- Wrap storage in a fault-injecting fake: fail or crash at every write/flush boundary in turn (enumerate, do
  not sample, for short operations), then reopen and check invariants (acknowledged data present, no partial
  state, indexes consistent, migrations resumable).
- Inject: disk full, I/O error, short write, corrupted read, out-of-memory where the platform allows, process kill.
- Also run a real-database kill test (spawn process, kill -9 at random points, reopen, verify) for the shell layer.
- A fault test that cannot be shown failing against a deliberately broken commit-ordering is not trustworthy;
  keep such a broken variant in the red-team corpus.

## Fuzzing
- Target every parser and deserializer of untrusted or user-supplied data (files, URLs, imported text,
  stored blobs). Oracle: no crash, no hang, no sanitizer finding, plus round-trip or validity invariants.
- Keep a seed corpus from real fixtures and past bugs; commit discovered crashers as regression tests.
- Run short fuzz in PR (seconds to minutes), long fuzz nightly.

## Deterministic simulation
Drive the system from one seed through simulated time, injecting faults and checking invariants every step.
Requires the determinism seams in testable-architecture.md. Even a modest version (simulate N years of
user activity against a model, with random restarts and clock jumps) finds bugs unit tests cannot.

## Characterization (golden master) tests
Use only to pin legacy behavior before refactoring. Generate from real inputs, review the recorded outputs
once, mark them as temporary, and replace with invariants as understanding grows. Never auto-update snapshots
in CI; updates are protected-path changes.

## Beyond testing (use sparingly, on small kernels)
Model checking (TLA+, Alloy) for protocols and state machines; bounded model checking or verification
tools for tiny critical kernels (for example Kani for Rust, Dafny, SMT-based checks); sanitizers and Miri/UB
detectors for unsafe code. Worth it when a bug is catastrophic and the kernel is small; not for whole apps.

## Anti-slop catalogue: what bad tests look like
| Smell | Why it is useless | Fix |
|-------|-------------------|-----|
| No assertion, or only "does not throw" | cannot fail when logic is wrong | assert on outputs/state/effects |
| Asserting truthy/non-null | passes for most wrong answers | assert exact values or laws |
| Test mirrors the implementation | tautology; bug copied into test | independent oracle or property |
| Mocks the subject or everything around it | tests the mocks | use fakes with real semantics at boundaries |
| Asserts only on mock call counts | locks implementation, not behavior | assert observable outcomes |
| Blind snapshot update | rubber stamp | reviewed goldens, protected updates |
| Random without seed logging | irreproducible failures | log seed, add replay command |
| Time or order dependent | flaky | inject clock, isolate state |
| Sleeps and polling | flaky and slow | deterministic executor, explicit signals |
| Catch-all in the test | hides failures | let it fail; assert the specific error |
| Only the happy path | misses errors, boundaries | boundaries, error paths, faults |
| Giant test with many unrelated asserts | unclear failure, easy to weaken | one behavior per test, named by invariant |
| Tests pass when the code is deleted | proves nothing | mutation testing; delete or fix |

## Test quality rubric (grade existing tests 0 to 3)
0 cannot fail (no assertion, tautology, mocks the subject). 1 fails only on gross breakage. 2 pins real behavior
with exact assertions but few edge cases. 3 states an invariant, hits edges, and kills relevant mutants.
Plan: delete score-0 tests, fix or replace score-1, keep and tag 2 and 3. Report counts to the user.

## Conventions
- Name tests by behavior and invariant: `INV-012 retry does not double-apply`.
- One behavior per test; arrange/act/assert visible.
- Fixtures are frozen and provenance-documented; generated data is seeded.
- No network, no real clock, no shared global state in gate tests.
- Slow tests are tagged and run in the right tier (ci-and-feedback.md).
