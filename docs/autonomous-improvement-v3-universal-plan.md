# Autonomous Improvement V3: Universal Autonomous Project Improvement Machine

**Date:** 2026-11-09 (revised 2026-11-10 after a review against `main` @ `ed3ac03`)  
**Status:** Architecture & Implementation Plan (reviewed; open owner decisions in §11)  
**Target:** `skills/autonomous-improvement/` — mainly `scripts/autonomous/` and `fleet-ops/`  
**Companion to:** `docs/autonomous-improvement-rsi-expansion.md`, `docs/autonomous-improvement-audit-2026-10-22.md`

---

## 0. Review Notes (2026-11-10)

The first draft was written as if V2 had no rate-limit, dedup, claim, or
governor machinery. Most of that machinery already exists. This revision
keeps the vision and the "no fake work" doctrine, and makes these changes:

1. **It builds on what exists instead of rebuilding it.** §2.1 maps every V3
   mechanism to the code that already ships it. Several milestones were
   either done already or would have duplicated working code: leases vs.
   `fleet.py claim`, the negative ledger vs. `REJECTED`/`WONTFIX` rows plus
   `ledger.py sync-rejections`, model pools vs. `fleet.py pool`, WIP caps
   vs. `fleet.py govern`.
2. **It fixes conflicts with the skill's own rules.** The CI track edited the
   referee, which SKILL.md "Final rules" forbids. The circuit breaker would
   have tripped on honest `no gap` turns. The model pool skipped the
   owner-scoped provider list. Leases kept in local files cannot coordinate
   agents on different machines. Details in §4–§8.
3. **It names the real root cause.** The stream contract (deterministic
   finder + mechanical oracle) was not the problem. The problem is that
   `survey.py` has **no signal at all** for CI gaps, untested modules, or
   complexity, and hardcodes simplification and fuzz to `0.0`. V3's
   harvesters are deterministic finders, so they pass the contract.
4. **It drops overclaims.** "Guarantees", "completely solves", and
   "eliminates" became measurable acceptance criteria.
5. **It fixes factual errors**: the `sync-rejections` owner (`ledger.py`,
   not `fleet.py`), the survey threshold (`0.25`, not `0.5`), the
   ambiguous priority bound in the saturation rule, and an outdated model id
   in the cooldown example.

---

## 1. Executive Summary & Vision

`autonomous-improvement` was meant to be an engine that keeps improving any
software project, reliably and without supervision. In practice its
unattended runs collapsed into a near-exclusive loop of **Stream A (mutation
testing)**: invert an operator in a leaf source file, write a test that kills
the mutant, open a PR.

Mutation testing has a clean mathematical oracle, but it covers a small slice
of engineering work. It ignores:
- Missing CI/CD pipelines and static-analysis gates.
- Core business-logic modules with zero tests.
- Silent error swallowing, race conditions, and unhandled edge cases on
  production paths.
- Code debt, dead code, and bloated architecture.
- Dependency evaluation, upstream divergence, and architectural RFCs.

**The V3 Vision:**  
Turn `autonomous-improvement` into a **multi-track project improvement
machine** that works on any stack and keeps the guarantees that make V2
trustworthy (the five laws, the stream contract, autonomy tiers). Invoked at
the root of any repository (Flutter, Rust, Python, Go, TypeScript, …), or run
on a VM through the stateless wake, it:
1. Harvests improvement candidates from many project signals into a
   persistent, deduplicated **Idea Backlog**. Every harvester is a 0-token
   deterministic finder.
2. Ranks candidates across tracks (CI, Testing, Bug Hunting, Refactoring,
   Research) and runs one pass per task under that track's oracle.
3. Produces small, evidence-backed PRs, or issue-only RFCs for tracks that
   have no mechanical oracle, sized for asynchronous human review.
4. **Refuses fake work**: cosmetic busywork is banned at admission, and the
   engine reports an honest `no gap` / saturation result instead of
   manufacturing tasks.
5. **Spends zero tokens on wakes where nothing changed.** It closes the
   remaining gaps in the existing quiet-until-changed and pool-rotation
   machinery (§4).
6. **Never redoes declined or in-flight work.** It extends the existing
   draft-PR claims and content-hash dispositions with idea-level fingerprints
   (§6).

---

## 2. Diagnosis: Why V2 Collapsed to Mutation Testing

| V2 Mechanism | What Happened | Consequence | Verified against `main` |
|---|---|---|---|
| **Signal coverage in `survey.py`** | `survey.py` scores ten tools from 0-token signals. None of them detects a missing CI gate, an untested core module, or high complexity. `simplification` and `fuzz` are hardcoded to strength `0.0`, and `performance` has weight `0.0` unless a bench directory exists. | On a repo with green CI, no drift, no secrets, no recorded flakes, and no smells found by `stream_b_scan.py`, the only tool that can clear the `0.25` threshold is `mutation` (weight `0.5` × strength `1.0` whenever `ledger.py next --stream A` finds an unaudited target). | `scripts/autonomous/survey.py` (`THRESHOLD`, `DEFAULT_WEIGHTS`, `survey_repo`) |
| **Infinite supply of trivial targets** | Every file matching `candidates.A.include` is a Stream A target until audited at its current content hash. | The mutation queue rarely drains, so the system never needs to look for other work. | `survey.check_mutation`, `ledger.py next` |
| **Finder coverage, not the contract** | The stream contract (08) demands a deterministic finder and a mechanical oracle. Mutation, adversarial audit, flakes, and docs have finders. CI, test-gap, complexity, and upstream research have none. | The contract is sound; the missing finders are the gap. V3 adds finders and keeps the contract. | `references/08-recursive-self-improvement.md` §stream contract |
| **Watchdog nudge floods (historical)** | Before 2026-10-23, fixed-interval nudges were delivered into frozen or rate-limited sessions (a 7× burst after a ~9 h freeze on 2026-10-20; six stale nudges on 2026-10-22). | **Mostly mitigated since:** knock rate-limit, tiered model ladder, three-class failure handling, exhaustion backoff, `ALERT.md`, and the stateless `needs-brain` digest. The residual gaps are listed in §4.0. | `fleet-ops/references/05-unattended-operation.md`, `fleet-ops/assets/watchdog.sh`, `fleet.py needs-brain` |

### 2.1 What already exists (build on it, do not rebuild)

| V3 mechanism | Already shipped | V3 delta |
|---|---|---|
| State-change gating | `fleet.py needs-brain` / `fleet.py wake`: digest of open-PR states + reasons in `state/wake.json`; unchanged digest ⇒ 0-token quiet | Add `main` HEAD SHA, labelled issues, and human comments to the digest (§4.1) |
| Cooldown / backoff | `watchdog.sh` `model_failures` + `ORCH_MODEL_COOLDOWN` (3600 s), `ORCH_RETRY_AFTER_EXHAUSTION` (1800 s); `stateless-wake.sh` `wake_failures` | `fleet.py wake` does not persist per-model failures across ticks; no `Retry-After` parsing (§4.2) |
| Circuit breaker | Exhausted-ladder exit `3` + `OnFailure=` alert + `ALERT.md` | Consecutive-*failed*-wake breaker that does not count honest `no gap` (§4.3) |
| Model pool | `fleet.py pool show|ladder`, `fleet.model_pool`, `ORCH_MODEL_LADDER`, `ORCH_AUTHORIZED_PROVIDERS` | None in design; wire `wake` to the shared cooldown list |
| WIP cap / backpressure | `fleet.py govern`: `max_open_prs` (5), `max_new_prs_per_day` (6), `min_acceptance_rate_14d` (0.5), `max_worktrees` (5), kill switch `.autonomous/STOP`, disk floor, doctor preflight | Per-track caps (§8) |
| Task leases | `fleet.py claim`: draft PR at minute 1; `ledger.py next` skips targets in an open PR's file list | Claim backlog **items**, not only file targets (§6.1) |
| Negative memory | Ledger `REJECTED`/`WONTFIX`/`SUPERSEDED` rows keyed to the composite hash; `ledger.py sync-rejections`; `profile.py` suppresses a pass for 30 days after 3 consecutive rejections | Idea-level fingerprint for non-file ideas (§6.2) |
| Per-repo learning | `profile.py` priors (Laplace-smoothed acceptance) feed `survey.py` | Priors per track as well as per tool |
| Roles | `references/04`: Orchestrator + Breaker / Builder / Sentinel | Track-specific briefs layered on these roles (§7) |
| Saturation | `survey.py` returns `chosen: null` ("no gap"); `fleet.py stats` renders the weekly owner digest | Saturation report + standby cadence (§3.3) |
| Autonomy tiers | T1 issue-only → T2 draft → T3 ready, promotion by acceptance floor (08) | Every new track starts at the tier in §5.0 |

---

## 3. The "No Fake Work" Doctrine: Real Impact vs. Artificial Productivity

A known failure mode of high-autonomy coding agents is **fake work (objective
hacking)**: when an agent has no genuine high-signal task, it invents trivial
tasks to look productive.

### 3.1 Banned Patterns (Zero Tolerance)
The machine rejects, at admission, tasks in these categories:
1. **Cosmetic Renaming**: renaming variables, private functions, or classes
   "for clarity" with no behavioral or architectural change.
2. **Comment/Doc Churn**: reformatting markdown, rearranging docstrings, or
   adding obvious comments (e.g. `// constructor`). Docs work enters only
   through Stream I's drift oracle (`docs_truth.py`).
3. **Vacuous / Mock-Only Testing**: testing mocks against mocks, testing
   compiler guarantees, or writing tautological assertions
   (`expect(true, isTrue)`). `stream_b_scan.py` and
   `test_weakening_check.py` already detect part of this.
4. **Pedantic Synthetic Edge Cases**: defensive guards for conditions the
   language's type system already makes impossible.
5. **Code Churn for Metric Gaming**: moving code between files or adding
   gratuitous helper abstractions that add indirection without reducing
   complexity.

Enforcement: each harvester rule carries a **ban-list check**, and every
audit-shaped finding goes through the mandatory refutation pass (SKILL.md
"The toolbox"). A finding counts only with file, line, a concrete trigger,
and a failing test or a reproducible measurement.

### 3.2 The Value Gate (Track-Appropriate Verification)
Law 1 (fail-to-pass) still binds every track that lands code. V3 only names
the right *defect* for each track:

- **Bug Hunt Track**: a reproduction test that fails on unmodified
  `origin/main` and passes with the minimal fix (`repair.py verify-red /
  verify-green`, `evidence.py defect`).
- **Test Engineering Track**: the target is a production symbol with **zero
  coverage or missing branch coverage**. The defect is a mutant of that
  symbol: the new test must kill at least one mutant that survives the
  current suite (`mutation_engine.py` on the target, `evidence.py mutant`).
  The test must pass and run in < 5 s.
- **CI / Tooling Track**: CI configuration is **referee** surface (SKILL.md
  "Final rules"). This track files an evidence-backed issue or proposal
  containing a validated workflow: `actionlint` / schema check clean, plus
  a local dry run where possible. It opens a PR only as a separate,
  human-approved change (§11, decision 1).
- **Refactoring / Simplification Track**: Stream J's oracle. Targeted tests
  stay green, no tracked test file is deleted, no previously killed mutant
  newly survives, and the mutation kill rate does not drop. A Stream M
  ratchet adds the metric: complexity or LOC strictly lower. Public APIs stay
  backward-compatible.
- **Research Track**: a structured **GitHub Issue / Markdown RFC, never
  code** (T1 permanently). It must cite primary sources: official docs,
  advisory databases, upstream commits, benchmarks.

### 3.3 The "Honest Halt" Protocol (Convergence & Saturation)
An agent working 24/7 on a project will eventually exhaust the high-leverage
automated tasks. **Halting honestly is a correct result, not a failure.**

1. **Saturation Detection.** The repo is saturated when all of these hold:
   - The Idea Backlog has no `ready` item at priority P0–P3 (P0 is the
     highest).
   - Every harvester (§5.1) and every `survey.py` signal returns zero
     candidates above threshold, so `survey.py` yields `chosen: null`.
   - Default-branch CI is green and no fleet PR is red.
2. **Action on Saturation:**
   - The engine does NOT invent cosmetic tasks or fall back to leaf mutation.
   - It writes a run record with a new `saturated` outcome (`runrecord.py
     write`; today's `OUTCOMES` end at `no-gap`, so this is an addition) and
     adds a **Saturation section** to the `fleet.py stats` owner digest. A
     standalone `.autonomous/SATURATION.md` is optional; because
     `.autonomous/*` is gitignored, it is a local report, not shared state.
   - It enters **Deep Standby**:
     - The scheduled-interval wake reason is suppressed for 24–48 h.
     - Any event in §4.1 (main moved, a labelled issue, a human comment, a
       PR state change) still wakes it immediately, because those events
       change the digest.
     - "New CVE" and "upstream release" wakes need a 0-token detector first.
       `advisory.py` currently audits against a *stub* advisory JSON, so
       these triggers stay out until a finder exists (Milestone 5).

---

## 4. Rate-Limit Defense & Empty-Wake Suppression

Most of this layer exists (§2.1). This section states the target design and
the gaps that remain.

```
                           [ Wake Event Trigger ]
                                     │
                                     ▼
                ┌──────────────────────────────────────────┐
                │ Layer 1: needs-brain digest (exists)     │
                │   - open-PR states/heads/checks (exists) │
                │   - main HEAD SHA (NEW)                  │
                │   - labelled issues / human comments(NEW)│
                └──────────────────────────────────────────┘
                                     │ (digest unchanged -> exit 0, 0 tokens)
                                     ▼ (state changed)
                ┌──────────────────────────────────────────┐
                │ Layer 2: per-model cooldown list         │
                │   - shared model_failures/wake_failures  │
                │   - Retry-After when present (NEW)       │
                └──────────────────────────────────────────┘
                                     │ (all models cooling -> exit 0)
                                     ▼
                ┌──────────────────────────────────────────┐
                │ Layer 3: circuit breaker (NEW)           │
                │   - 3 consecutive FAILED wakes           │
                │   - honest no-gap does not count         │
                └──────────────────────────────────────────┘
                                     │ (tripped -> alert, human re-arm)
                                     ▼ (OK)
                ┌──────────────────────────────────────────┐
                │ Layer 4: owner-scoped ladder (exists)    │
                │   - fleet.py pool ladder                 │
                │   - quota/dead -> rotate; outage -> wait │
                └──────────────────────────────────────────┘
```

### 4.0 Residual gaps (verified 2026-11-10)
1. `_wake_digest` hashes only open-PR states and reasons. With no open PRs,
   the reason is the constant `queue-idle`, so **new commits on `main` never
   wake an idle fleet**, and neither does a new issue or a human comment.
2. `fleet.py wake` walks the ladder within one tick but records no per-model
   failure, so the next tick retries a model that just hit quota. The
   shell wrappers keep `model_failures` / `wake_failures`; the Python path
   does not.
3. No component reads `Retry-After`.
4. No component counts consecutive failed wakes.

### 4.1 Layer 1: "Quiet Until Changed" (State-Change Gating)
The timer runs `fleet.py wake`. It calls `needs-brain` and spawns no harness
when the digest is unchanged. **No new `state_gate.py`:** extend the digest
with:
- the remote default-branch HEAD SHA (`git ls-remote`, 0 tokens);
- the IDs and `updatedAt` of open issues carrying the claim label
  (`CLAIM_LABEL`, currently `autonomous-improvement`) or `owner-ask`;
- the latest human (non-bot) comment timestamp on open fleet PRs;
- a coarse scheduled bucket (e.g. `floor(now / interval)`), included only
  while the backlog has `ready` items and the repo is not saturated.

If the digest is unchanged, the wake exits 0. **No harness process is
spawned and no prompt tokens are sent.**

### 4.2 Layer 2: Per-Model Cooldown with Backoff
On a quota error, classify it with the existing three-class table (05 "Model
rotation"):
- **Quota / dead model** → park *that model* in the cooldown list. Use
  `Retry-After` when the harness surfaces it, else `ORCH_MODEL_COOLDOWN`
  (default 3600 s). On a repeated quota failure within the TTL, double the
  cooldown, capped at 24 h, with ±10 % jitter.
- **Outage** (`503`, `Endpoint is unavailable`, `overloaded`) → park
  nothing and let the next tick retry. Parking a healthy model because its
  endpoint was briefly down is a verified past failure (2026-10-22).

The cooldown list is per **model**, not per provider, and lives in the
existing state dir (`ORCH_STATE_DIR`, default `~/.local/share/orchestrator`).
It reuses the `model_failures` format (`model timestamp`) so the watchdog,
`stateless-wake.sh`, and `fleet.py wake` share one list. Do not create a
separate `.autonomous/state/cooldown.json`. If a structured form is needed,
use one JSON file with a list of entries:

```json
{
  "schema": "model-cooldown/1",
  "entries": [
    {
      "model": "<provider/model-id>",
      "cooldown_until": 1794268800,
      "class": "quota",
      "reason": "HTTP 429 rate_limit_exceeded",
      "strikes": 2
    }
  ]
}
```

Every wake checks the list **before** any network request. Models still
cooling are skipped; if every ladder entry is cooling, the wake exits 0
quietly.

### 4.3 Layer 3: Circuit Breaker on Consecutive Failed Wakes
The breaker trips after 3 consecutive wakes (as recorded in `wakes.jsonl`)
that ended in:
- `launch-failed` (every ladder entry failed), a crash, or an unhandled
  error; OR
- a turn that ran but left **no trace**: no commit, PR, issue, ledger row,
  run record, *and* no `no gap` / `saturated` result.

An honest `no gap` turn is **not** a failure and never counts toward the
breaker. Without this rule, saturation (§3.3) would trip it.

When tripped:
- Write `CIRCUIT_BREAKER` (with the last three wake records) in the state
  dir, next to the existing `ALERT.md`.
- `fleet.py govern` and `fleet.py wake` refuse to dispatch.
- Re-arm with `fleet.py reset-circuit` (new subcommand) or automatically
  when a human commit lands on `main`.

### 4.4 Layer 4: Model Pool Rotation (exists — keep its constraints)
Rotation uses the **owner-recorded pool** (`fleet.py pool ladder`, from
`fleet.model_pool` / `ORCH_MODEL_LADDER`), filtered by
`ORCH_AUTHORIZED_PROVIDERS`. It never uses a hardcoded cross-vendor chain:
premium or paid pools stay unreachable unless the owner listed them, and a
"$0 balance" gateway is a paid pool with no credit, not a free fallback
(Law 5). Global hibernation starts only when every ladder entry is cooling.

---

## 5. The Multi-Track Idea Harvester & Persistent Backlog

V3 adds **deterministic harvesters** next to the existing finders. A
harvester is a 0-token script that emits candidates with file, line, rule
id, and evidence. The model judges candidates; it does not browse for them
(stream contract clause 1). The aim is not a fixed backlog size: it is
**every candidate that clears its harvester's false-positive bar**.

```
                              [ IDEA HARVESTER ]
                                      │
  ┌───────────────────┬───────────────┼───────────────┬───────────────────┐
  ▼                   ▼               ▼               ▼                   ▼
Track 1: CI/CD     Track 2: Test   Track 3: Bug    Track 4: Refactor   Track 5: Research
- Missing checks   - Zero-coverage - Exception     - High complexity   - Dep upgrades
- Stale actions      core modules    swallowing    - Dead code         - Deprecations
- Matrix gaps      - Uncovered     - Race conds    - Tangled modules   - Architecture
- Pre-commit         branches      - Null unsafety - Duplication         RFCs
```

### 5.0 Track → stream mapping and starting tier

Tracks are a *harvesting and reporting* view. Work still runs under stream
letters, so the ledger, the tiers, and the `ledger.py` planned-letter guard
keep working. A track whose stream is not unlocked is issue-only until its
finder, oracle, and eval fixture land (08).

| Track | Runs under | New harvester | Oracle | Starts at |
|---|---|---|---|---|
| `ci` | new letter (proposed **N**, "CI & tooling") | `ci_audit` | `actionlint`/schema clean + dry run | **T1** (CI is referee) |
| `test` | **A** + **B** (extend) | `test_gap` | new test kills ≥ 1 surviving mutant of the target | T2 |
| `bug` | **E** (+ **C** for parsers) | `bug_scout` | `repair.py` red on main → green | T3 path via E |
| `refactor` | **J** + **M** | `complexity` | J oracle + M strict ratchet | T1 (J first wave) |
| `research` | **K** / **H** | `upstream` | none; citations required | **T1 permanently** |

### 5.1 Discovery Harvesters

#### Track 1: CI/CD & Developer Experience (`ci`)
- Inspects `.github/workflows/` (and other CI configs the stack adapter
  names).
- Checks for missing gates:
  - Is there a PR test runner?
  - Is there a linter / format checker?
  - Do tests run on every platform the project ships to (Linux, macOS,
    Windows / web, mobile)?
  - Are third-party actions pinned to commit SHAs?
  - Is there a dependency-review workflow?
- Output is a T1 proposal containing the validated workflow file (§3.2).

#### Track 2: Test Engineering & Blind Spots (`test`)
- Finds production files with **no mapped companion test**, using config
  `test_for_source` and the same heuristics as `ledger.py record` (e.g.
  `lib/src/model/foo.dart` with no `test/model/foo_test.dart`).
- Ranks candidates with `ledger.py next --rank risk` (churn, fan-in proxy,
  size, and `coverage_report` when configured).
- Existing tests with empty or vacuous assertions are already Stream B
  (`stream_b_scan.py`); do not duplicate that finder.
- Prioritizes state machines, parsers, and domain logic over thin UI
  wrappers.

#### Track 3: Bug Hunting & Edge Cases (`bug`)
- Static defect-pattern rules, one per language in the stack adapters:
  - Empty `catch (e) {}` / `except: pass` blocks that swallow errors.
  - Forced null assertions (`!`, `unwrap()`) on async or network paths.
  - Missing timeout/cancellation handling on futures/promises.
  - State updated after dispose/unmount in UI lifecycles.
- Mines failing CI logs for recurring crash signatures.
- These rules have **high false-positive rates**. A hit is only a lead: it
  becomes work only after the refutation pass *and* a reproduction test
  that is red on `origin/main` (Stream E intake). Each rule's measured FP
  rate is recorded so `profile.py` can down-weight noisy rules.

#### Track 4: Architecture & Safe Simplification (`refactor`)
- Flags files over a configurable size (default 500 lines) with high
  cyclomatic complexity, as a Stream M ratchet candidate.
- Dead code: extends `simplify_gate.py finder` (currently Python only)
  through the stack adapters.
- Duplicate logic across modules: token-level clone detection, reported as
  T1 only.

#### Track 5: Sourced Research & Upstream Spikes (`research`)
- Reads package manifests (`pubspec.yaml`, `Cargo.toml`, `package.json`,
  `pyproject.toml`) for outdated major versions. The bump itself stays
  Stream K's oracle-first `advisory.py bump`.
- Scans for deprecated framework APIs.
- Checks upstream divergence for forks that track an upstream (e.g. a
  Lichess Mobile fork), using `git` against a configured upstream remote.
- Emits structured research issues with citations. Never code.

### 5.2 Unified Backlog Schema (`.autonomous/backlog.json`)

The backlog is a **derived cache**: harvesters can rebuild it from the repo
at any time. It is not the source of truth for claims or rejections. Claims
live in open PRs (§6.1) and rejections in the git-tracked ledger (§6.2).
This matters because the skill's `assets/gitignore.fragment.txt` ignores
`.autonomous/*` (only `config.json` is tracked), so anything stored only
there is invisible to other machines and other worktrees.

**Fingerprint definition** (the first draft used the term without defining
it): `sha256(track | rule_id | normalized target_paths | anchor)`, where
`anchor` is the content hash of the smallest enclosing symbol, or of the
file when no symbol applies. The fingerprint is computed by the harvester,
never from LLM-written title text. Two different wordings of the same
finding therefore collide, and a content change at the anchor re-opens it,
matching Law 2.

```json
{
  "$schema": "autonomous/backlog/v3",
  "updated_at": "2026-11-09T14:30:00Z",
  "head_sha": "ed3ac03…",
  "items": [
    {
      "id": "CI-001",
      "track": "ci",
      "stream": "N",
      "rule_id": "ci.missing-pr-test-runner",
      "priority": "P1",
      "title": "Add GitHub Actions workflow for PR static analysis and tests",
      "target_paths": [".github/workflows/pr.yml"],
      "fingerprint": "sha256:7a9f...",
      "status": "ready",
      "evidence": "No workflow triggers on pull_request."
    },
    {
      "id": "TEST-012",
      "track": "test",
      "stream": "A",
      "rule_id": "test.no-companion",
      "priority": "P2",
      "title": "Create unit test suite for PgnEvaluation state transitions",
      "target_paths": ["lib/src/model/pgn/pgn_evaluation.dart"],
      "fingerprint": "sha256:4b1e...",
      "status": "ready",
      "evidence": "340 lines, no mapped test, 12 surviving mutants."
    },
    {
      "id": "BUG-005",
      "track": "bug",
      "stream": "E",
      "rule_id": "bug.unchecked-substring",
      "priority": "P2",
      "title": "Handle corrupt PGN string without crashing the practice screen",
      "target_paths": ["lib/src/model/pgn/pgn_parser.dart"],
      "fingerprint": "sha256:8c2d...",
      "status": "lead",
      "evidence": "Unchecked substring slicing at L88; needs a red repro to become ready."
    }
  ]
}
```

`status` is one of `lead` (a harvester hit not yet refuted or reproduced),
`ready`, `claimed` (derived from an open PR, never written by hand), or
`done`.

---

## 6. Deduplication, Concurrency & Never Redoing Past Work

### 6.1 Claims, not lease files
`references/02` already explains why an in-progress record in a local or
git-tracked file cannot deduplicate parallel agents: it is invisible to
other worktrees and machines until merged. The working mechanism is the
**draft PR at minute 1** (`fleet.py claim`), which `ledger.py next` and
`claim` both check through open-PR file overlap.

V3 changes:
- `fleet.py claim --item <backlog-id>`: same flow; the draft body carries the
  item fingerprint, and the overlap check also matches fingerprints in open
  PR bodies, so non-file items (CI proposals, research issues) are
  protected too.
- For issue-only tracks, an open issue carrying the fingerprint is the
  claim.
- Stale claims: a draft with no new commit for `fleet.claim_ttl_hours`
  (proposed default 24) is reported in the `tend` queue. It is never closed
  automatically, because a human may be working on it.
- Optional host-local lock (`flock` in the state dir) only to stop two
  workers on the *same host* racing between `next` and `claim`.

### 6.2 Negative memory: extend the ledger, do not fork it
The ledger's `REJECTED`/`WONTFIX` rows, `ledger.py sync-rejections`, and
`profile.py` suppression already form the "memory of no". They are
file-scoped and hash-scoped by design: "a rejection is *not this diff*, not
*never this file*" (02).

Gap: ideas that are not one file's diff, such as an architectural refactor
the owner rejected on principle. V3:
- Adds an optional `Fingerprint` column (or a fingerprint inside the existing
  summary cell, if a schema change proves too costly) so a `REJECTED` row
  can carry the §5.2 idea fingerprint.
- Extends `sync-rejections` to read the fingerprint from closed-unmerged PR
  bodies and from issues closed as `not planned` / labelled `wontfix`.
- Has harvesters drop any candidate whose fingerprint matches an active
  rejection, at 0 tokens.
- Gives idea-level rejections a **default expiry of 180 days**. A
  never-expiring rejection is allowed only with an explicit owner reason,
  e.g. *"Owner prefers local component state over a global provider here."*
- Keeps the rows git-tracked. A `.autonomous/negative-ledger.json` would be
  gitignored and per-machine, which defeats the purpose.

---

## 7. Track Briefs on Top of the Existing Roles

The Orchestrator / Breaker / Builder / Sentinel roles (04) stay. V3 adds a
short **track brief** per track, kept short because 150-line briefs have
killed workers before the work started (04, verified 2026-10-22).

### 7.1 `CI / Tooling` (Breaker-shaped, T1)
- **Scope**: proposes changes to `.github/workflows/`, linting configs, build
  scripts, pre-commit hooks.
- **Rules**: never touches application source; validates syntax locally
  (`actionlint`, `yamllint`, or a dry run); keeps the change minimal (no pile
  of third-party actions); never weakens an existing gate.
- **Output**: an issue or proposal with the validated file. A PR only as a
  separate, human-approved change.

### 7.2 `Test Engineer` (Builder)
- **Scope**: creates or modifies test files only.
- **Rules**: never touches production code; tests real behavior, not
  implementation details; every test passes, runs in < 5 s, and has no
  network or unmocked wall-clock dependency; Law 1 via a killed mutant.
- **Output**: one PR adding tests for one target.

### 7.3 `Bug Scout & Fixer` (Breaker → Builder)
- **Scope**: turns a `lead` into a reproduction, then a fix.
- **Rules**:
  - Step 1: write the test; run it on `origin/main` → must fail (RED). A
    lead that cannot be reproduced is recorded as refuted and dropped.
  - Step 2: apply the minimal fix and run the test → must pass (GREEN).
  - Diff budget: < 50 lines changed in production code.
- **Output**: one PR with the fix and the regression test.

### 7.4 `Refactorer` (Builder, J/M)
- **Scope**: dead-code removal, decoupling, simplifying complex methods.
- **Rules**: no change to public API contracts; Stream J's oracle passes; no
  new dependencies or abstraction frameworks.
- **Output**: T1 proposal until J/M is promoted; then a PR with the measured
  metric delta (e.g. −50 lines, lower complexity).

### 7.5 `Research Analyst` (read-only)
- **Scope**: technology investigations, dependency upgrades, upstream
  divergence.
- **Rules**: **never creates a branch or commits code**; files a structured
  RFC as a GitHub issue labelled `research`; cites primary sources
  (documentation URLs, commit hashes, benchmarks).
- **Output**: one GitHub issue with actionable recommendations.

The Sentinel reviews every track's output against the ban list (§3.1) and
the track's oracle before publication (`fleet.py publish` already refuses
incomplete or stale evidence).

---

## 8. Governor, Backpressure & Scheduling

`fleet.py govern` remains the single dispatch gate.

1. **WIP PR cap (exists).** `fleet.max_open_prs` (default 5),
   `fleet.max_new_prs_per_day` (default 6), and `min_acceptance_rate_14d`
   (default 0.5). At the cap, no new code-modifying work: the wake tends
   existing PRs (conflicts, review comments, red CI), runs read-only or T1
   tracks, or sleeps until the owner acts. V3 adds an optional per-track cap
   (`fleet.track_caps`, e.g. `{"refactor": 1}`) so one track cannot fill the
   queue.
2. **Track balancing: evidence-ranked with an anti-starvation guard, not
   strict round-robin.** Strict round-robin would run a low-value track just
   because it is that track's turn, which contradicts "one pass at a time,
   picked by evidence". Instead, `survey.py` scores every track's best
   candidate (weight × strength × `profile.py` prior). A track that was
   picked in the last *k* dispatches (proposed *k* = 3) gets a decaying
   penalty, and a track with eligible items that was not picked in the last
   *n* dispatches (proposed *n* = 10) gets a floor boost. A track with no
   eligible items yields.
3. **Diff budgets.** One logical change per PR. Defaults: ≤ 3 files and
   ≤ 100 changed lines, excluding generated code and lockfiles; < 50
   production lines for bug fixes. Make these config keys
   (`fleet.diff_budget`) and check them in `orchestrator.py verify-gate`,
   because 08 clause 4 currently states the budget in prose only. Target: a
   maintainer can review a PR in under 3 minutes.

---

## 9. Implementation Roadmap & Milestones

Constraints that bind every milestone: Python 3.9+, stdlib only, argv lists
(no `shell=True`); a red-first test for each behavior change; a SPEC.md row
for each new rule; SKILL.md stays ≤ 200 lines (`test_skill_is_short`), so
new content goes into references; README regenerated with
`scripts/generate_readme.py` from a clean checkout; a new stream letter
stays rejected by `ledger.py` until its finder, oracle, and eval fixture
land.

### Milestone 1: Close the Rate-Limit Gaps (P0) — *Complete (PR #150)*
- [x] Add `main` HEAD SHA, labelled issues, and human-comment timestamps to
  `_wake_digest` in `fleet.py` (no new `state_gate.py`). *Test: an idle
  fleet with a moved `main` reports `needs_brain: true`; unchanged state
  stays quiet.*
- [x] Have `fleet.py wake` read and write the shared per-model cooldown list
  (same semantics as `model_failures` / `wake_failures`), with `Retry-After`
  when available and outage-class failures not parked. *Test: a stub harness
  returning a 429 line parks that model; the next tick skips it; an outage
  line parks nothing.*
- [x] Add the circuit breaker plus `fleet.py reset-circuit`, counting only
  failed or traceless wakes. *Test: three `no gap` wakes do not trip it;
  three `launch-failed` wakes do.*

### Milestone 2: Survey Signals for the Missing Tracks (P1) — *Complete (PR #151)*
This milestone attacks the diagnosed root cause directly.
- [x] Add `test_gap`, `ci_audit`, and `complexity` signals to `survey.py`,
  each backed by a finder script, so a clean repo with real gaps no longer
  defaults to mutation. *Test/eval: a fixture repo with green CI, one
  untested core module, and unaudited Stream A targets must pick the
  test-gap pass, not mutation.*
- [x] Replace the hardcoded `0.0` strengths for `simplification` and `fuzz`
  with real signals (the `simplify_gate.py finder` count; parser-shaped
  targets for fuzz).
- [x] Add a non-Python eval fixture for each new finder (e.g. Dart and
  TypeScript).

### Milestone 3: Backlog, Fingerprints & Item Claims (P1) — *Complete (PR #152)*
- [x] Implement the `.autonomous/backlog.json` derived cache with the §5.2
  fingerprint, plus a `harvest` entrypoint that rebuilds it.
- [x] Add `fleet.py claim --item` with fingerprint overlap checks against
  open PRs and issues.
- [x] Extend the ledger with idea fingerprints and default-expiring
  idea-level rejections; extend `ledger.py sync-rejections` to read them
  from closed PRs and `not planned` issues.
- [x] Add the `bug_scout` and `upstream` harvesters (leads only, T1).

### Milestone 4: Track Briefs & Track-Specific Gates (P2) — *Complete (PR #155)*
- [x] Add track briefs to `references/04-workflow-and-roles.md`, or one
  short reference per track. Keep each brief under ~30 lines.
- [x] Wire the track gates: `actionlint`/schema for `ci`, a killed mutant
  for `test`, red→green for `bug`, the J/M oracle for `refactor`, and
  issue-only for `research`.
- [x] Make `fleet.diff_budget` enforced in `orchestrator.py verify-gate`.
- [x] Add track caps and the anti-starvation guard to `fleet.py govern` /
  `survey.py`.

### Milestone 5: Honest Halt & Deep Standby (P2) — *Complete (PR #157)*
- [x] Add a `saturated` outcome to `survey.py` / `runrecord.py` `OUTCOMES` when §3.3's
  conditions hold, and a Saturation section in the `fleet.py stats` owner
  digest.
- [x] Suppress the scheduled-interval digest bucket for 24–48 h while
  saturated; event-driven wakes stay live.
- [ ] Optional, gated on a 0-token detector: advisory-feed and
  upstream-release wake triggers.

### Measuring success (replaces the first draft's "guarantees")
Read these from `fleet.py stats` and the run records over a 14-day window:
- Share of dispatched passes that were not Stream A ≥ 50 % on repos where
  the new harvesters find candidates.
- Fleet acceptance rate stays ≥ `min_acceptance_rate_14d`. A falling rate
  after V3 means the new tracks produce churn, and the offending track is
  demoted automatically (08).
- Zero harness launches on wakes with an unchanged digest, and zero
  launches on a model that is still cooling (both read from `wakes.jsonl`).
- Zero re-proposals of a fingerprint with an active rejection.

---

## 10. Conclusion

V3 separates **Idea Harvesting** (deterministic, 0-token finders) from
**Task Execution** (one pass, one track oracle, one small PR). It does this
inside the existing stream contract, laws, and tiers rather than around
them. The rate-limit and dedup machinery mostly exists already; V3 closes
its specific gaps instead of rebuilding it. The decisive change is in
Milestone 2: give `survey.py` signals for the work it cannot currently see,
so mutation testing becomes one tool among many again.

---

## 11. Open Owner Decisions

1. **CI track autonomy.** CI config is referee surface. Recommended: T1
   issue/proposal only, with the owner applying the change or approving a
   separate PR. The alternative is an explicit carve-out that lets the fleet
   open draft PRs that *add* gates but never modify or remove them.
2. **Idea-level rejection expiry.** Recommended: 180 days by default, with
   permanent only on an explicit owner reason.
3. **Track balancing.** Recommended: evidence-ranked with an
   anti-starvation guard (§8.2) rather than strict round-robin.
4. **New stream letter for CI** (proposed `N`), or fold it into `M`
   (ratchet) as a "gate count" ratchet.
