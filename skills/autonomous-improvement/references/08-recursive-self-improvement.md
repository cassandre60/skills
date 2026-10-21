# Recursive Self-Improvement: Beyond Test Hardening

Streams A/B (mutation, test audit) are the bootstrap: they have the cleanest
oracles. They are not the ceiling. This reference defines how improvement
generalizes — first to every falsifiable defect class in a codebase, then to
the fleet itself — without losing the properties that make A/B trustworthy.

Design record: `docs/autonomous-improvement-rsi-expansion.md` in the skills repo.

## The stream contract (admission test for any new work type)

A work type is a **stream** only if it satisfies all five clauses. Otherwise
it is issue-only work: file the evidence, let a human drive.

1. **Deterministic finder.** A script enumerates candidates (0-token first
   pass) with a measurable false-positive rate. The model judges; it does
   not browse for work.
2. **Mechanical oracle.** A fail-before/pass-after pair, a differential
   check, or a monotone ratchet plus equivalence proof — runnable as a
   command. If the oracle cannot be a command, it is not a stream.
3. **Content-addressed memory.** Dedup key *and* rejection key, both derived
   from content (Law 2). The fleet must be able to learn "no".
4. **Bounded blast radius.** Diff budget, file scope, trivial revert:
   one PR = one revertable hypothesis.
5. **Referee isolation.** The stream never edits its own oracle, finder
   thresholds, or scoring (Law: do not edit the referee). That surface is
   level L4 below.

## Autonomy tiers (earned, not assigned)

| Tier | Fleet may | Promotion rule |
|---|---|---|
| **T1** | File evidence-backed issues only | Every stream starts here |
| **T2** | Open draft PRs; human promotes | Acceptance ≥ floor over ≥10 decided (from fleet stats), zero referee incidents |
| **T3** | Open ready PRs under the normal CI-green loop | Sustained T2 record |

Demotion is automatic when the floor is breached. Stream L (meta) is capped
at T2, and at T1 for anything referee-adjacent.

## The catalog

Available now: **A** mutation, **B** adversarial test audit (`03`),
**C** property/fuzz (`fuzz_shrink.py`: recorded seeds, deterministic
shrink, retained counterexamples, replay oracle) and **G** performance
(`perf_gate.py`: pinned bench delta beyond the band plus differential
equivalence — both halves mandatory, "faster because it does less" is
rejected). **F**, **I**, **H+K** and **E** ship in their own PRs — see
those for their tooling.

| Stream | Oracle in one line | Starts |
|---|---|---|
| **C** Property / fuzz | shrunk counterexample red on base, green after fix; corpus kept | T2 |
| **D** Data / migration | fixture-DB migrations: zero row loss, zero new orphans | T2 |
| **E** Defect repair | reproduction test red on `origin/main`, green after minimal fix | T3 path |
| **F** Flake & determinism | N-run matrix: flake rate > threshold → 0/N, cause class named | T3 path |
| **G** Performance | pinned-bench delta beyond noise **and** differential equivalence | T2 |
| **H** Security & supply chain | PoC/regression test flips, or vulnerable version exits green | T3 deps / T2 code |
| **I** Docs & spec truthfulness | executable examples flip fail→pass; spec-id trace complete | T3 path |
| **J** Simplification / dead code | suite green **and** touched-area mutation score not lower | T2 |
| **K** Dependency evolution | build + targeted suite green on the bump; majors one per PR | T2 |
| **L** Meta (fleet self-improvement) | proposal beats incumbent on history replay; eval set does not regress | T1/T2 capped |

`ledger.py` rejects C–L with "planned, not implemented" until a stream ships
its finder + oracle + eval fixture. A stream that exists only as an
encouraging paragraph produces cosmetic churn with a fancy name — do not
start work under these letters before their tooling lands; offer A/B, or
file the finding as an issue (that *is* T1 behavior).

Sequencing rationale in brief: **F** repairs the oracle A/B/E trust; **E**
is the largest value unlock (pre-justified defects via issue/CI intake);
**H/K** findings come pre-triaged by advisory databases; **I** protects
what future workers read as truth; **C** manufactures defects for E; **G/J**
have statistical/heavier oracles; **D** costs the most fixtures; **L** last.

## Levels of recursion

```
L4  goals & oracles    Five Laws, this contract, eval sets, tier floors   OWNER ONLY
L3  process            finders, prompts, stream weights, model routing   fleet proposes → replay + evals → owner merges
L2  oracle assets      tests, benches, fuzz corpora, specs, fixtures     fleet PRs
L1  product code       fixes, perf, deletions, upgrades                  fleet PRs
```

Rules that keep the recursion honest:

- L1 is validated by L2 oracles it did not write — or, for defect repair,
  by a reproduction that is red on unmodified `origin/main`. That base is
  the one anchor a worker cannot fake.
- L3 proposals (a new finder rule, a prompt clause, a routing change) are
  mined from fleet telemetry — rejection reasons, CI iterations, triage
  false-positives, cost per merged PR — and are promoted only by
  **replay + canary**: re-run against recorded history, then a slice of
  live queue against the incumbent. Losers go in the negative ledger.
- L4 never changes by fleet PR. The fleet files *issues* at L4 (e.g. "this
  failure class deserves a gate"); the owner authors/approves referee
  changes. A self-improving loop that can edit its own evaluator will
  eventually optimize the evaluator instead of the work — this is the
  documented objective-hacking failure of self-modifying agents, and the
  reason the evaluator lives outside the mutable surface.
- Mined lessons are hypotheses with expiry: each carries its evidence and
  a re-validate-by condition. A lesson contradicted by newer telemetry is
  demoted in the stats report, not defended. Unverified accumulated
  "learnings" are how skill libraries silently rot (library drift).
- Never score the fleet on PR count. Per stream: accepted value minus
  owner minutes, CI minutes, and tokens — and no stream may buy its metric
  by degrading another stream's oracle.

## Where this leaves the five laws

Unchanged — they are L4. Stream expansion adds finders and oracles
*underneath* them; recursion adds a governed L3 on top. If a proposed
improvement cannot name its finder command and its oracle command, it does
not ship as a stream.
