# Assess the repo and plan the gates

Contents: orientation, risk tiers, maturity ladder, the plan document, rollout phases, retrofitting
legacy or low-quality code, scaling to project size, definition of done.

## 1. Orientation (do this before changing anything)
1. Read README, AGENTS.md/CLAUDE.md, docs, manifests, CI config, directory layout.
2. Run `python3 scripts/assess_repo.py .` for a quick factual scan (languages, test ratio, CI, lint,
   coverage, mutation, property tests, CODEOWNERS, specs). Treat output as hypotheses.
3. Establish: what the product is, who is hurt if it is wrong, target platforms, how it is built, tested,
   released, and what "release" means (store, web deploy, registry, self-hosted).
4. Run the existing quality commands and record the true baseline: do tests pass, how long do they take,
   are there flaky tests, what does analysis report. Never plan on top of a red baseline you have not seen.
5. Find the irreversible decisions: persisted formats, schemas, identifiers, public APIs, export formats.
   Once real users exist these are expensive to change; get them right and freeze them early.

## 2. Risk tiers (spend rigor where errors are catastrophic)
Classify each module or directory:

| Tier | Meaning | Examples | Gate expectation |
|------|---------|----------|------------------|
| R0 | loss, corruption, security, money | persistence, migrations, auth, ledger, import | everything: spec invariants, property + model + differential, crash-consistency, mutation on all code, fuzz |
| R1 | wrong behavior of core logic | scheduling, ranking, parsing, domain rules | properties, mutation on changed lines, determinism seams |
| R2 | UI glue, adapters | screens, view models, API clients | strict types, behavior tests of key flows, accessibility checks, lighter mutation |
| R3 | generated or vendored code | codegen output, vendored libs | exclude from metrics, pin versions, scan for vulnerabilities |

Write the tier next to each module in GATES_PLAN.md. Ratchets and mutation thresholds differ by tier.

## 3. Maturity ladder
- L0 nothing enforced.
- L1 tests exist, lint/format run locally.
- L2 CI required on every PR; coverage tracked and ratcheted; reproducible builds; dependency lockfile.
- L3 SPEC.md invariants; pure core with enforced boundaries; strict analysis; property tests; seeded randomness.
- L4 diff-scoped mutation gate; fail-to-pass for bug fixes; protected referee (CODEOWNERS + trusted CI scripts);
  evidence-contract PR template; test-weakening tripwire.
- L5 deterministic simulation/crash-consistency; fuzzing; performance budgets with ratchets; red-team
  corpus with recall tracking; gate-health metrics; escape-to-gate flywheel running.

Assess per module, not just per repo: the data layer may deserve L5 while a settings screen needs L2.

## 4. The plan document (GATES_PLAN.md)
Produce a short, concrete plan the user can approve:
- Context: product, stakes, stack, release target, current maturity per tier.
- Irreversible decisions to freeze and how.
- Gap table: gate, what it would catch, cost to build (S/M/L), leverage (S/M/L), order.
- Phase list with exit criteria (below).
- What you will deliberately NOT do, and why.
- Risks: gate maintenance cost, CI minutes, flakiness, false-positive handling.
Ask the user only about decisions that are genuinely theirs (spending money, removing user-facing features,
changing public formats). Otherwise decide and proceed.

## 5. Rollout phases (each ends with all gates green)
- **Phase 0 Subtract.** Delete dead code, unused dependencies, orphaned screens, legacy features that are
  out of scope. Fewer lines means fewer gates and fewer bugs. Record LOC and dependency count as the first ratchets.
- **Phase 1 Baseline and freeze.** Make CI required. Record current metrics (coverage, warnings, suppressions,
  size, benchmark numbers) into ratchet-baseline.json at today's values. From now on nothing regresses.
- **Phase 2 Protect the referee.** CODEOWNERS, protected-paths file, trusted-checkout CI scripts, branch
  protection, PR template with Class line, test-weakening tripwire. This early because everything after
  it is worthless if the author can edit it.
- **Phase 3 Spec and core.** Write SPEC.md invariants (spec-and-invariants.md). Carve a pure core and inject
  clock/random/storage/network (testable-architecture.md). Enforce boundaries with banned-API and import rules.
- **Phase 4 Tests that can fail.** Property, model-based, differential, crash-consistency tests for R0/R1
  modules (test-strategies.md). Tag tests with INV IDs; turn on spec traceability.
- **Phase 5 Strength gates.** Diff-scoped mutation testing; fail-to-pass for bug fixes; raise ratchets as scores improve
  (mutation-testing.md, evidence-contract.md).
- **Phase 6 Deep tiers.** Nightly fuzzing, simulation soak, long-seed property runs, device/OS matrix,
  migration-from-every-shipped-version tests (ci-and-feedback.md).
- **Phase 7 Test the gates.** Red-team corpus, gate-breaker session, gate-health metrics (red-team-corpus.md).
- **Phase 8 Operate.** Escape-to-gate flywheel, quarterly gate review, delete gates that stopped paying.

## 6. Retrofitting legacy or low-quality code
- Do not rewrite blindly. First pin current behavior with characterization (golden-master) tests on the
  public seams, generated from real inputs, so refactors are safe.
- Strictness applies to new and changed lines immediately (diff-scoped), while old code is held by ratchets
  that only tighten. This "clean as you code" approach avoids a big-bang cleanup that never finishes.
- Convert each past bug into an invariant and a regression test before touching the area again.
- Delete before you test: dead code needs no tests; unreachable code that survives mutation testing should be removed.
- Prefer strangling a bad module behind a stable interface and replacing it, with the old implementation
  kept temporarily as a differential oracle for the new one.

## 7. Scaling to project size
- Tiny script or prototype: L2 plus a handful of property tests on the one risky function. Skip simulation,
  red-team corpus, device matrices.
- Small app with local data (the sweet spot for this skill): L4 across the core, L5 for the persistence layer.
- Service with users, money or untrusted input: full ladder, plus fuzzing and security review.
Say explicitly which gates you chose to skip and why; unspoken omissions look like oversights.

## 8. Definition of done for the gate system
- Every invariant in SPEC.md has at least one gate and a named oracle; spec_trace_check passes.
- A deliberately wrong change in each risk-tier-R0 module is rejected (shown by red-team recall).
- The referee cannot be edited by a normal PR without human approval.
- A new agent can run the full local gate command with one line and get actionable feedback.
- GATES.md lists every gate with runtime, blind spots and owner; ratchet baselines are committed.
- The user can say what an accepted PR proves, in one sentence per PR class.

## Do-not list
- Do not add gates you cannot keep green and fast; a flaky gate is worse than none.
- Do not chase 100% coverage as a goal; chase mutation kill rate and invariants on R0/R1.
- Do not mass-generate tests to raise numbers; each test must name the invariant or bug it protects.
- Do not bundle gate changes with product code.
- Do not leave TODO gates in CI that always pass.
