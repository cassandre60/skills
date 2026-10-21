# autonomous-improvement beyond testing: robust recursive self-improvement (RSI)

**Date:** 2026-10-21 · **Companion to:** `docs/autonomous-improvement-audit-2026-10-21.md` (PR #10)
**Premise (owner direction):** the skill is named *autonomous-improvement*, not
*autonomous-test-hardening*. Testing (Streams A/B) is the bootstrap stream because its oracle is
the cleanest — not the ceiling. This document defines how the skill grows to cover **every
falsifiable improvement a codebase can undergo**, and how the fleet improves *itself*
(recursion) without the failure modes that make self-improvement loops rot: Goodhart gaming,
self-grading, drift, and editing the referee.

The operational form of this design lives in the skill as
`skills/autonomous-improvement/references/08-recursive-self-improvement.md`.
This file is the reasoning record: alternatives, evidence, and the stream-by-stream math.

---

## 1. The generalizable insight: you already invented the admission test

Streams A/B work not because they are about tests, but because they satisfy five properties
(the Five Laws restated as a *stream contract*):

| Contract clause | A (mutation) today | Generalized requirement for any new stream |
|---|---|---|
| 1. Deterministic finder | regex engine verdicts, 0 tokens to enumerate mutants | A script enumerates candidate defects with a measurable false-positive rate. The LLM judges; it does not browse. |
| 2. Mechanical oracle | red-with-mutant / green-clean | A fail-before/pass-after pair, a differential check, or a monotone ratchet + equivalence proof. If you cannot write the oracle as a command, it is not a stream — it is an issue for a human. |
| 3. Content-addressed memory | composite `<source9>:<test9>` + (proposed) negative rows | Dedup key **and** rejection key, both content-derived. The machine must be able to learn "no" and "already tried". |
| 4. Bounded blast radius | one file, one test, worktree isolation | Diff budget + file scope + trivial revert. One PR = one revertable hypothesis. |
| 5. Referee isolation | never edit gates/specs to go green | The stream may never modify its own oracle, finder thresholds, or scoring. That surface is human-gated (L4 below). |

**Any improvement type that satisfies all five is a stream. Anything that fails clause 1 or 2
belongs in issue-only mode** (the fleet files a well-evidenced issue; a human or a supervised
session does the work). This is the same boundary AlphaEvolve/FunSearch hit from the other side:
*we can only optimize what we can grade* — every candidate is scored by an automatic evaluator,
correctness extends exactly as far as the evaluator covers, and the evaluator lives outside the
mutable surface ([AlphaEvolve white paper](https://arxiv.org/abs/2506.13131)). It is also why
"every possible way" must be read as *every possible way that admits an oracle* — the design
goal is to keep expanding the oracle-covered frontier (better finders, better differentials,
better benches), not to pretend taste is gradeable.

## 2. The stream catalog (A–L)

Autonomy tiers: **T3** = PR marked ready (oracle is binary and local), **T2** = draft PR, human
promotes (oracle is statistical or equivalence is partial), **T1** = issue-only (fleet produces
evidence + proposal, never a diff). A stream's tier is *earned* (§4): new streams start at T1.

| Stream | Finder (0-token first pass) | Oracle (fail → pass) | Evidence artifact | Start tier |
|---|---|---|---|---|
| **A Mutation** *(live)* | mutation engine / AST mutator | new test red w/ mutant, green clean + pass-to-pass | engine JSON + red/green | T3 |
| **B Test audit** *(live)* | smell scanner (proposed #17) | strengthened test fails when the behavior is sabotaged | sabotage diff + runs | T3 |
| **C Property / fuzz** *(planned)* | nightly fuzz/property harness, crash corpus | shrunk counterexample red on base, green after fix; corpus entry retained | seed + shrink trace | T2 |
| **D Data / migration** *(planned)* | schema-diff + historical fixtures | migration on fixture DBs: zero row loss / orphan delta = 0 | fixture versions + counts | T2 |
| **E Defect repair** | intake: open bug issues, red `main` CI, crash/assert reports, `git log` fix-revert mining | reproduction test red on `origin/main`, green after minimal fix (Law 1 bugfix branch — already in the evidence contract, never operationalized) | repro test + before/after | T3 |
| **F Flake & determinism** | CI history mining (same-test both-outcomes), `sleep` scan, order-shuffle runs | N-run statistics: flake rate > threshold before, 0/N after + root-cause class named (timing/order/shared-state) | run matrix (N≥20) | T3 |
| **G Performance** | bench harness + profile hotspots, slow-test report | pinned-harness delta beyond noise band (report spread, not median) **and** differential/property equivalence vs old impl | bench JSON before/after | T2 |
| **H Security & supply chain** | SAST (CodeQL/Semgrep), dep vuln DB, secret scan — true-positive filtered | exploit/regression test red before, green after; or vuln-version exit with full targeted suite green | advisory id + PoC test | T3 (deps) / T2 (code) |
| **I Docs & spec truthfulness** | executable-example runner, spec-id trace check, link/anchor check, code-vs-doc symbol diff | doc example fails before / passes after; every SPEC id cited & vice-versa | failing snippet + fix | T3 |
| **J Simplification / dead code** | reachability (compiler/`git grep` fan-in = 0) + coverage = 0 + no dynamic-reflection markers | full targeted suite green **and** mutation score of touched area not lower (deletion must not delete protection) | reachability report | T2 |
| **K Dependency & toolchain evolution** | version lag + changelog/advisory mining (Renovate-shaped, but oracle-first) | targeted suite + build green on the bump; majors split one-per-PR with changelog risk notes | version delta + suite | T2 |
| **L Meta: fleet self-improvement** | fleet telemetry mining (below) | **eval-gated**: proposed skill/config/prompt/finder change must not regress the eval set and must improve its target metric on replayed history | before/after eval + replay report | **T1 always for referee-adjacent changes; T2 max otherwise** |

Notes on the less obvious rows:

- **E is the largest unlocked value.** Everything needed already exists except intake: the
  evidence contract's bugfix row, worktree isolation, CI loop. An issue→repro→fix pipeline turns
  the fleet from "invents hardening work" into "also clears the defect backlog you already
  believe in" — pre-justified work with a human-authored spec (the issue), which is precisely
  what hosted agents (Copilot coding agent, OpenHands Resolver) industrialized.
- **F before G:** a flaky suite silently corrupts A's verdicts and E's reds. Flake-hunting is
  oracle-maintenance for every other stream — sequence it early.
- **G is T2 forever-ish:** perf oracles are statistical; the failure mode is "faster because it
  does less". The differential-equivalence requirement is the seatbelt; keep a human in it.
- **J's oracle is the subtle one:** "tests still pass" is necessary but gameable (delete code
  *and* its only test). Requiring mutation-score-non-decrease on the touched area (agent-proof
  sibling skill already states this for refactors) closes the obvious cheat.
- **I is underrated for agent fleets specifically**: agents read docs as ground truth. A stale
  README example poisons every future worker. Executable docs are oracle repair for the fleet's
  own inputs — a small recursion with outsized yield.

## 3. The recursion, made precise (L1–L4)

"Recursive self-improvement" decomposes into four levels, with strictly different rules.
Conflating them is how loops rot.

```
L4  GOALS & ORACLES      streams list, Five Laws, eval sets, tier floors     HUMAN ONLY
L3  PROCESS              finders, prompts, stream weights, model routing,   FLEET PROPOSES,
                         configs, thresholds                                eval + human-gated merge
L2  ORACLE ASSETS        tests, benches, fuzz corpora, specs, fixtures      fleet PRs (A/B/C/D/G-verifiers)
L1  PRODUCT CODE         fixes, perf, deletions, upgrades                   fleet PRs (E–K)
```

- **L1/L2 are the object level** — today's skill plus the new streams. Improvement is proven
  *downward*: an L1 change is validated by L2 oracles it did not write (or, when it writes the
  repro, the red must exist on unmodified `main` — the one un-gameable anchor).
- **L3 is where recursion actually lives.** The fleet mines its own telemetry — rejection
  reasons, CI iteration counts, triage false-positive rates per rule/finder, cost per merged PR
  per model, freeze causes — and proposes changes to *itself*: a new mutation rule, a finder
  threshold, a dispatch prompt clause, a stream weight. L3 changes are validated by **replay**
  (would this finder, run on last month's `main` snapshots, have produced more accepted
  findings per token?) and by the **eval set not regressing** — Darwin-Gödel-Machine-style
  empirical validation with a lineage archive, except the "archive" is simply git history of
  the skills repo and the gate is a human merge
  ([DGM, arXiv:2505.22954](https://arxiv.org/html/2505.22954v3): empirically validated
  self-modification, archive as stepping stones — and its appendix-H *objective-hacking*
  incident, where an agent deleted the detector markers instead of hallucinating less, is
  exactly why L3 may not touch L4).
- **L4 is immutable to the fleet.** The Five Laws, the stream contract, eval suites, tier
  definitions, merge policies. The fleet may *file issues* about L4 (Stream L at T1) — a
  proposed new gate arising from a repeated failure class is valuable output — but the PR that
  changes the referee is authored/approved by the owner. This is the skill's existing
  "do not edit the referee" rule promoted from etiquette to architecture. Voyager/Reflexion
  literature supplies the cautionary mechanism from the memory side: unverified self-lessons
  compound ("self-reinforcing error", "library drift" — skill libraries whose retrieval
  degrades silently as unverified entries accumulate). Hence L3 memory rules below: lessons
  are *hypotheses with expiry*, re-validated or dropped, never permanent prompt scar tissue
  by accretion alone. (Your dated "Hard lessons" lists are the good version of this — every
  entry carries its verifying incident. Keep that discipline as L3 automates lesson-mining.)

## 4. Robustness: how streams earn and lose autonomy

Expansion multiplies both value and blast radius. The controls, in order of importance:

1. **Tier promotion/demotion by measured acceptance.** Every stream starts T1 (issue-only).
   Promotion T1→T2→T3 requires a trailing acceptance rate (merged ÷ decided, from `fleet
   stats`) above a floor over a minimum sample (e.g. ≥70% over ≥10 decided PRs) and zero
   referee incidents. Demotion is automatic when the floor is breached — the governor stops
   dispatching that stream at the higher tier. Autonomy is thus a *measured property*, not a
   vibe. (This is the audit's P0 governor, generalized per-stream.)
2. **Multi-objective scoring; never optimize PR count.** Fleet score per stream =
   (accepted-value) − λ(owner minutes) − μ(CI minutes) − ν(tokens). Any single-metric target
   will be Goodharted; the Pareto guard is: no stream may trade away another stream's oracle
   (perf PR that lowers mutation score is rejected by construction, etc.).
3. **Held-out validation.** A portion of eval tasks and, where feasible, hidden tests are never
   visible to workers. L3 changes are scored on held-outs specifically to detect overfitting
   to the visible evals — the standard defense DGM/Reflexion analyses converge on.
4. **Champion/challenger for L3.** A proposed prompt/finder/routing change runs on a slice of
   the queue (canary wave) against the incumbent; promotion requires beating it on replay +
   canary without guardrail regression. Losers are recorded in the negative ledger so the fleet
   doesn't re-propose them next month.
5. **Lesson expiry & contradiction checks (L3 memory hygiene).** Mined lessons carry
   `evidence`, `scope`, `expires`/re-validate-by. A lesson contradicted by newer telemetry is
   demoted automatically in the stats report for owner review — the direct counter to
   library-drift/self-reinforcing-error.
6. **Diff & scope budgets scale with tier, not ambition.** T3 streams keep today's
   one-file-ish discipline. Larger refactors (J/K waves) run as *sequences* of budget-sized
   PRs, each independently revertable — never as one heroic diff.
7. **Sandboxing & identity** (audit F15/#19) become non-optional as E/H/K give the fleet
   write access to product code paths and dependency manifests: dedicated user, no-merge token
   unless policy says otherwise, doctor preflight in the dispatch path.
8. **Kill switch per stream**, not just per fleet (governor config gains `streams: {G: off}`).

## 5. Sequencing (what to build in which order, and why)

The audit's P0 (fleet state #11, negative ledger #12, governor #13) is *prerequisite plumbing*
for safe expansion — tiering, acceptance stats, and rejection memory are meaningless without
it. Given that, the value-ordered expansion sequence is:

1. **F (flake)** — repairs the oracle every other stream trusts; finder is pure CI-history math.
2. **E (defect repair)** — biggest value unlock; reuses Law 1's bugfix branch; needs #17's
   publish/claim rails for issue intake.
3. **H-deps + K (supply chain)** — external truth (advisory DBs) makes findings pre-triaged.
4. **I (docs truthfulness)** — cheap finders, protects the fleet's own inputs.
5. **C (fuzz/property)** — needs a nightly harness (the snapshot-timer pattern generalizes);
   feeds E with machine-found defects.
6. **G (perf)** then **J (simplification)** — statistically/heavier oracles, T2 by design.
7. **D (data/migration)** — highest fixture cost, repo-specific; worth it only where data loss
   is the top risk class.
8. **L (meta)** last and permanently capped — it is only safe once telemetry (#13), evals
   (#20), and tiering exist to validate its proposals.

Each stream lands as: finder script + oracle wired into evidence (#14) + ledger stream id +
eval fixture + reference section — never as prompt-only permission. A stream that exists only
as an encouraging paragraph is how you get cosmetic churn with a fancy name.

## 6. What this is not

- **Not weight training / open-ended AGI RSI.** Models stay frozen; improvement accretes in
  code, tests, configs, prompts, and ledgers — DGM-with-a-human-merge-gate, deliberately
  slower and auditable.
- **Not autonomous product direction.** Features, UX, architecture taste have no oracle
  (clause 2) and stay human-owned; the fleet's job is to make the *existing intent*
  progressively harder to break and cheaper to change.
- **Not a bigger prompt.** If a proposed expansion can't name its finder command and oracle
  command, it doesn't ship.
