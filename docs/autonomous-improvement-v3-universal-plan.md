# Autonomous Improvement V3: Universal Autonomous Project Improvement Machine

**Date:** 2026-11-09  
**Status:** Architecture & Implementation Plan  
**Target:** `skills/autonomous-improvement`, `scripts/autonomous/`, `fleet-ops/`  
**Companion to:** `docs/autonomous-improvement-rsi-expansion.md`, `docs/autonomous-improvement-audit-2026-10-22.md`

---

## 1. Executive Summary & Vision

The original vision for `autonomous-improvement` was to provide an autonomous engine that continuously and reliably improves any software project. In practice, V1 and V2 collapsed into an almost exclusive loop of **Stream A (mutation testing)**: inverting boolean operators or arithmetic signs in leaf source files, writing companion unit tests to kill the mutant, and submitting PRs.

While mutation testing provides clean mathematical oracles, it represents a tiny slice of software engineering. An engineering team does not spend 100% of its time killing synthetic AST mutants while ignoring:
- Missing CI/CD pipelines and static analysis quality gates.
- Core business logic modules that have zero tests.
- Silent error swallowing, race conditions, and unhandled edge cases in production paths.
- Code debt, dead code, and bloated architectures.
- Structural research, dependency evaluation, and architectural RFCs.

**The V3 Vision:**  
Transform `@autonomous-improvement` into a **universal, multi-track, 24/7 project improvement machine**. 
When invoked at the root of *any* repository (Flutter, Rust, Python, Go, TypeScript, etc.), or run continuously on a VM/server, it:
1. Harvests hundreds of high-leverage improvement candidates from diverse project signals into a structured, persistent **Idea Backlog**.
2. Dispatches specialized agent roles across multiple tracks (Testing, Bug Hunting, CI/CD, Refactoring, Research).
3. Produces high-signal, bite-sized PRs and RFC issues that human maintainers can asynchronously review and merge.
4. **Guarantees zero "fake work"**: strictly bans cosmetic busywork and enforces an **Honest Halt** when the repository reaches saturation.
5. **Completely solves rate-limit floods**: eliminates empty nudge signals via state-change gating, exponential backoff, and model pool failover.
6. **Eliminates redundant or duplicate work**: coordinates parallel agents through atomic task leases, semantic fingerprinting, and a persistent negative ledger.

---

## 2. Diagnosis: Why V2 Collapsed to Mutation Testing

To build the new architecture, we must understand the exact failure modes of V2:

| V2 Mechanism | What Happened | Consequence |
|---|---|---|
| **The "Mechanical Oracle" Dogma** | *"If the oracle cannot be written as a command before work begins, it is not a stream."* | Banned any task without a pre-existing 0-token script. Only mutation testing had an automated command generator (`mutation_engine.py`). |
| **"Model Must Not Browse for Work"** | The LLM was forbidden from exploring files or reading architecture specs to propose tasks. | Disabled the LLM's primary strength: semantic comprehension of application design, test gaps, and business logic. |
| **`survey.py` Mathematical Zero-Out** | In clean repos where CI is green, every other stream (defect repair, docs, flakes, security, fuzz, simplification) scores `0.0`. | Mutation testing was the *only* tool scoring above threshold (`0.5`), guaranteeing it was chosen on every single run. |
| **Infinite Supply of Trivial Targets** | `mutants.py` generates thousands of syntax mutants across every file. | The mutation queue never drains, so the system never needs to look for real work. |
| **Unbounded Watchdog Nudges** | Fixed-interval wake timers fired even when models were rate-limited (429) or state had not changed. | Resulted in 80+ empty nudge prompts flooding frozen sessions, burning tokens and locking up machines. |

---

## 3. The "No Fake Work" Doctrine: Real Impact vs. Artificial Productivity

A critical failure mode of high-autonomy coding agents is **fake work (objective hacking)**: when an agent lacks genuine high-signal tasks, it manufactures trivial tasks to appear productive.

### 3.1 Banned Patterns (Zero Tolerance)
The machine will strictly reject and abort tasks that fall into these categories:
1. **Cosmetic Renaming**: Renaming variables, private functions, or classes for "clarity" without a behavioral or architectural change.
2. **Comment/Doc Churn**: Reformatting markdown, rearranging docstrings, or adding obvious comments (e.g. `// constructor`).
3. **Vacuous / Mock-Only Testing**: Testing mocks against mocks, testing compiler guarantees, or writing tautological assertions (`expect(true, isTrue)`).
4. **Pedantic Synthetic Edge Cases**: Adding defensive guards for types or conditions that are already statically impossible in the language's type system.
5. **Code Churn for Metric Gaming**: Moving code between files or creating gratuitous helper abstractions that increase indirection without reducing complexity.

### 3.2 The Value Gate (Track-Appropriate Verification)
Instead of forcing every task through Law 1 (a pre-existing test must fail before any code is written), V3 introduces **Track-Appropriate Verification**:

- **Bug Hunt Track**: Must produce a reproduction test that fails on the unmodified `main` branch and passes on the branch with the minimal fix.
- **Test Engineering Track**: Must target a production symbol that previously had **zero coverage or missing branch coverage**. The test must pass, run in < 5 seconds, and fail if the target method's logic is inverted.
- **CI / Tooling Track**: The workflow or configuration file must pass syntax validation (`actionlint`, schema checkers) and a local dry-run or lint check.
- **Refactoring / Simplification Track**: 100% of existing tests must pass. Cyclomatic complexity or line count must decrease; public APIs must remain 100% backward-compatible.
- **Research Track**: Emits a structured **GitHub Issue / Markdown RFC**, NEVER code. Must cite primary sources (official docs, CVE databases, upstream repos, profiling benchmarks).

### 3.3 The "Honest Halt" Protocol (Convergence & Saturation)
An autonomous agent that works 24/7 on a project will eventually finish all high-leverage automated tasks. **Halting honestly is a sign of excellence, not failure.**

1. **Saturation Detection**:
   When:
   - The Idea Backlog has no unaddressed items with priority $\ge$ P3.
   - All candidate finders (linter, test gaps, CI audit, security scan, bug scout) return zero new items above threshold.
   - All tests pass and CI is clean.
2. **Action on Saturation**:
   - The engine does NOT invent cosmetic tasks or start mutating leaf code.
   - It writes a structured **Project Saturation Report** (`.autonomous/SATURATION.md` or a GitHub issue/summary).
   - It transitions the runner into **Deep Standby Mode**:
     - Reduces polling cadence to once every 24–48 hours.
     - Wakes *only* if:
       - A human pushes new commits to `main`.
       - A human opens an issue or comments on an existing PR.
       - A new CVE is published affecting a project dependency.
       - Upstream tracked repositories release a new version.

---

## 4. Rate-Limit Defense & Eradication of Empty Nudge Floods

Past deployments suffered from timer-driven loops that repeatedly nudged frozen or rate-limited agent sessions. V3 implements a multi-layer defense against 429s, quota exhaustion, and empty wakes.

```
                           [ Wake Event Trigger ]
                                     │
                                     ▼
                ┌──────────────────────────────────────────┐
                │ Layer 1: External State-Change Predicate │
                │   - Has main moved?                      │
                │   - Has a PR merged or closed?           │
                │   - Has CI status changed?               │
                │   - Has a human commented or assigned?   │
                └──────────────────────────────────────────┘
                                     │ (No change -> Exit 0 immediately)
                                     ▼ (State changed)
                ┌──────────────────────────────────────────┐
                │ Layer 2: Provider Cooldown & Backoff     │
                │   - Check .autonomous/state/cooldown.json│
                │   - Is now < cooldown_until?             │
                └──────────────────────────────────────────┘
                                     │ (Cooldown active -> Exit 0 immediately)
                                     ▼ (Provider ready)
                ┌──────────────────────────────────────────┐
                │ Layer 3: Circuit Breaker                 │
                │   - Are consecutive failed wakes >= 3?   │
                └──────────────────────────────────────────┘
                                     │ (Tripped -> Require human reset)
                                     ▼ (OK)
                ┌──────────────────────────────────────────┐
                │ Layer 4: Model Pool Failover             │
                │   - Primary model ready? If 429: rotate  │
                │     to secondary provider in pool        │
                └──────────────────────────────────────────┘
```

### 4.1 Layer 1: "Quiet Until Changed" (State-Change Gating)
The systemd timer or cron job runs a lightweight 0-token script (`fleet.py needs-brain` or `state_gate.py`).
A wake signal is **only** permitted if at least one of these external events occurred:
- Remote `main` HEAD SHA has changed since the last run.
- An open fleet PR changed status (merged, closed, review requested, CI completed).
- A new issue with the `autonomous` label was created.
- A human posted an instruction comment on an open PR or issue.
- A deliberate, scheduled long-term interval has passed AND ready items exist in the Idea Backlog.

If none of these conditions are met, the script exits immediately with code 0. **No harness process is spawned, and zero prompt tokens are sent.**

### 4.2 Layer 2: Provider Cooldown with Exponential Backoff + Jitter
When any agent encounters an HTTP 429, a quota exhaustion error, or a provider rate-limit response:
1. The error interceptor extracts the `Retry-After` header if available.
2. If absent, it applies tiered backoff:
   - 1st rate limit: 15 minutes cooldown.
   - 2nd rate limit: 1 hour cooldown.
   - Daily quota exhaustion: sleep until `00:05 UTC` (next rollover).
3. The cooldown is written to `.autonomous/state/cooldown.json`:
   ```json
   {
     "provider": "anthropic",
     "model": "claude-3-5-sonnet",
     "cooldown_until": 1794268800,
     "reason": "429: rate_limit_exceeded"
   }
   ```
4. Any subsequent wake attempts check this timestamp *before* making any network requests. If `now < cooldown_until`, the runner silently terminates without executing.

### 4.3 Layer 3: Circuit Breaker on Consecutive Empty/Failed Wakes
If 3 consecutive wake cycles result in:
- A crash or unhandled error, OR
- An empty turn (no commit, no PR, no issue, no backlog update),
the circuit breaker trips:
- Writes `.autonomous/CIRCUIT_BREAKER_TRIPPED`.
- Halts all automated dispatching.
- Posts a summary alert for the human maintainer.
- Requires explicit clearing (`python3 scripts/autonomous/fleet.py reset-circuit`) or a new human commit on `main` to re-arm.

### 4.4 Layer 4: Model Pool Rotation
When a provider hits quota limits, the system does not stubbornly retry the same provider. It maintains a prioritized pool (e.g., Anthropic $\to$ OpenAI $\to$ Google Gemini $\to$ Local/Self-hosted). It falls back to the next eligible model; only when all models in the pool are on cooldown does it schedule global hibernation.

---

## 5. The Multi-Track Idea Harvester & Persistent Backlog

Instead of relying solely on regex mutation generators, V3 introduces an extensible **Idea Harvester** that continuously discovers real opportunities and maintains an indexed backlog of 50–100+ tasks.

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

### 5.1 Discovery Harvesters

#### Track 1: CI/CD & Developer Experience (`ci`)
- Inspects `.github/workflows/`.
- Checks for missing gates:
  - Is there a PR test runner?
  - Is there a linter / format checker?
  - Are tests run across required platforms (Linux, macOS, Windows / web, mobile)?
  - Are actions using pinned commit SHAs?
  - Is there a stale-issue or dependency-review workflow?

#### Track 2: Test Engineering & Blind Spots (`test`)
- Analyzes repo structure to find core production files with **zero companion test files** (e.g. `lib/src/model/foo.dart` with no `test/model/foo_test.dart`).
- Analyzes existing tests for empty/vacuous assertions.
- Prioritizes testing state machines, parsers, and financial/gameplay logic over trivial UI wrappers.

#### Track 3: Bug Hunting & Edge Cases (`bug`)
- Scans ASTs and source code for known defect patterns:
  - Empty `catch (e) {}` blocks that swallow errors silently.
  - Forced null assertions (`!` or `unwrap()`) in async/network paths.
  - Missing timeout/cancellation handling on futures/promises.
  - State variables updated out of sync in UI lifecycles.
- Re-scans previously failing CI logs to identify recurring crash patterns.

#### Track 4: Architecture & Safe Simplification (`refactor`)
- Detects files > 500 lines with high cyclomatic complexity.
- Identifies unused private methods, constants, and obsolete imports.
- Finds duplicate business logic across decoupled modules.

#### Track 5: Sourced Research & Upstream Spikes (`research`)
- Analyzes package manifests (`pubspec.yaml`, `Cargo.toml`, `package.json`, `pyproject.toml`) for outdated major versions.
- Scans for deprecated framework APIs.
- Investigates upstream divergence (for forks like `chesssrs` tracking Lichess Mobile).
- Emits structured Research Issues proposing architectural roadmaps with citations.

### 5.2 Unified Backlog Schema (`.autonomous/backlog.json`)

```json
{
  "$schema": "autonomous/backlog/v3",
  "updated_at": "2026-11-09T14:30:00Z",
  "items": [
    {
      "id": "CI-001",
      "track": "ci",
      "priority": "P1",
      "title": "Add GitHub Actions workflow for PR static analysis and tests",
      "target_paths": [".github/workflows/pr.yml"],
      "fingerprint": "sha256:7a9f...",
      "status": "ready",
      "context": "Repository currently lacks any automated CI checks on pull requests."
    },
    {
      "id": "TEST-012",
      "track": "test",
      "priority": "P2",
      "title": "Create unit test suite for PgnEvaluation state transitions",
      "target_paths": ["lib/src/model/pgn/pgn_evaluation.dart"],
      "fingerprint": "sha256:4b1e...",
      "status": "ready",
      "context": "Core PGN evaluation model has 340 lines and zero test coverage."
    },
    {
      "id": "BUG-005",
      "track": "bug",
      "priority": "P2",
      "title": "Handle corrupt PGN string without crashing the practice screen",
      "target_paths": ["lib/src/model/pgn/pgn_parser.dart"],
      "fingerprint": "sha256:8c2d...",
      "status": "ready",
      "context": "Unchecked substring slicing throws FormatException on truncated PGN."
    }
  ]
}
```

---

## 6. Deduplication, Concurrency & Never Redoing Past Work

A major failure of naive multi-agent systems is **redundancy**: two agents picking the same task, or an agent spending tokens on an idea the owner rejected last month.

### 6.1 Atomic Task Leases (`.autonomous/leases/`)
When an agent is dispatched to work on an item from the backlog:
1. It acquires an atomic lock file `.autonomous/leases/<item_id>.json`:
   ```json
   {
     "item_id": "TEST-012",
     "agent_id": "agent-worker-1",
     "worktree": "../chesssrs-task-test-012",
     "branch": "feat/test-pgn-evaluation",
     "acquired_at": "2026-11-09T15:00:00Z",
     "expires_at": "2026-11-09T16:00:00Z"
   }
   ```
2. Any other running agent checks active leases before selecting work. If an item has an active lease whose TTL has not expired, it is skipped.
3. If an agent crashes, the lease expires automatically after 60 minutes, allowing a subsequent agent to clean up the stale worktree.

### 6.2 The Persistent Negative Ledger (`.autonomous/negative-ledger.json`)
The machine must have permanent memory of **what not to do**.

When a human closes a PR unmerged, adds a `wontfix` label, or when an agent proves a hypothesis false during verification:
1. The item's **semantic fingerprint** is recorded in the Negative Ledger:
   ```json
   {
     "fingerprint": "sha256:9f3c...",
     "track": "refactor",
     "target_paths": ["lib/src/model/srs/scheduler.dart"],
     "title": "Extract SRS review queue into global provider",
     "decision": "REJECTED",
     "reason": "Owner prefers local component state over global Riverpod provider here",
     "recorded_at": "2026-11-09T12:00:00Z",
     "expires_at": null
   }
   ```
2. Harvesters query the Negative Ledger before admitting any candidate into the backlog. If the fingerprint matches an active rejection, it is discarded immediately at 0 token cost.

---

## 7. Role-Based Agent Workforce & Specialized Prompts

Instead of a single monolithic prompt, V3 defines specialized agent roles with sharp constraints:

### 7.1 Role: `CI / Tooling Engineer`
- **Scope**: Modifies `.github/workflows/`, linting configs, build scripts, pre-commit hooks.
- **Rules**:
  - Never touches application source code.
  - Must validate syntax locally (`actionlint`, `yamllint`, or dry-run).
  - Keeps diffs minimal; avoids adding 20 heavy external GitHub Actions.
- **Output**: Atomic PR with CI configuration.

### 7.2 Role: `Test Engineer`
- **Scope**: Modifies or creates files in `test/`.
- **Rules**:
  - Never touches production code in `lib/` or `src/`.
  - Must target real application behavior, not implementation details.
  - All tests must pass, finish within 5 seconds, and have zero flaky dependencies (no network, no unmocked wall-clock delays).
- **Output**: Atomic PR adding contract/unit/integration tests.

### 7.3 Role: `Bug Scout & Fixer`
- **Scope**: Identifies a concrete bug, writes a reproduction test, then writes the fix.
- **Rules**:
  - Step 1: Write test in `test/`. Run it on `main` $\to$ must fail (RED).
  - Step 2: Apply minimal fix in `lib/` or `src/`. Run test $\to$ must pass (GREEN).
  - Diff budget: < 50 lines changed in production code.
- **Output**: Atomic PR containing the bugfix and the regression test.

### 7.4 Role: `Architecture Refactorer`
- **Scope**: Dead code removal, decoupling, simplifying complex methods.
- **Rules**:
  - Zero change to public API contracts.
  - 100% of existing tests must pass before and after the change.
  - No new dependencies or external abstraction frameworks introduced.
- **Output**: Atomic PR with proven metric improvement (e.g. -50 lines, reduced cyclomatic complexity).

### 7.5 Role: `Research Analyst`
- **Scope**: Technology investigations, dependency upgrades, upstream divergence analysis.
- **Rules**:
  - **NEVER creates a branch or commits code.**
  - Outputs a structured Markdown RFC filed directly as a GitHub Issue labeled `research`.
  - Must cite primary sources (documentation URLs, commit hashes, benchmarks).
- **Output**: High-signal GitHub Issue with actionable recommendations.

---

## 8. Governor, Backpressure & Scheduling

To ensure the machine respects the human owner's attention and review capacity:

1. **WIP PR Cap (Backpressure)**:
   - Maximum **3 to 5 open pull requests** at any time.
   - If the cap is reached, the governor halts code-modifying workers.
   - It pivots to:
     - Tending existing PRs (resolving merge conflicts, addressing review comments, fixing red CI).
     - Read-only research tasks (filed as issues).
     - Sleeping until the owner reviews/merges open PRs.
2. **Track Balancing (Round-Robin)**:
   - Prevents starvation or monopolization by any single track.
   - Dispatch sequence: `CI` $\to$ `Bug` $\to$ `Test` $\to$ `Refactor` $\to$ `Research` $\to$ repeat.
   - If a track has no eligible items, it yields to the next track.
3. **Diff Budgets**:
   - Every PR is limited to **1 logical change, $\le 3$ files touched, $\le 100$ lines changed** (excluding auto-generated code).
   - Easily reviewable in under 3 minutes by the maintainer.

---

## 9. Implementation Roadmap & Milestones

The transition from V2 to V3 is structured into five concrete milestones:

### Milestone 1: Rate-Limit Shield & State-Change Gating (P0)
- [ ] Implement `state_gate.py`: 0-token check verifying external state changes before allowing any session wake.
- [ ] Implement `cooldown.py`: intercept 429 errors, record timestamps, and enforce silent exits during cooldown.
- [ ] Add circuit breaker logic to `fleet.py`: halt after 3 consecutive empty/failed cycles.

### Milestone 2: Universal Idea Harvester & Backlog System (P1)
- [ ] Create `harvester/`:
  - `ci_harvester.py`: detect missing workflows and linter gates.
  - `test_gap_harvester.py`: detect zero-coverage core domain files.
  - `bug_scout_harvester.py`: scan for empty catch blocks, forced unwraps, and race conditions.
  - `research_harvester.py`: detect outdated dependencies and deprecated APIs.
- [ ] Implement `.autonomous/backlog.json` schema, read/write utilities, and deduplication logic.

### Milestone 3: Negative Ledger & Atomic Task Leases (P1)
- [ ] Implement `.autonomous/leases/` mechanism for distributed worktree isolation and agent locking.
- [ ] Implement `.autonomous/negative-ledger.json` to store rejected ideas and prevent recurring fake work.
- [ ] Connect `fleet.py sync-rejections` to automatically register closed-unmerged PRs into the negative ledger.

### Milestone 4: Multi-Track Agent Roles & Specialized Prompts (P2)
- [ ] Define role prompts in `prompts/`: `ci_engineer.md`, `test_builder.md`, `bug_hunter.md`, `refactorer.md`, `researcher.md`.
- [ ] Add track-specific verification gates (syntax check for CI, red-to-green for bugs, zero-regression for refactors, issue-only for research).
- [ ] Update `survey.py` to balance priorities dynamically across tracks instead of defaulting to mutation testing.

### Milestone 5: The "Honest Halt" & Saturation Engine (P2)
- [ ] Implement `saturation.py`: evaluates backlog health, test coverage, and linter state.
- [ ] Generate `.autonomous/SATURATION.md` when no high-signal tasks remain.
- [ ] Implement deep standby wake scheduling (24–48h cadence) during saturation.

---

## 10. Conclusion

By separating **Idea Harvesting** from **Task Execution**, providing **Track-Appropriate Verification**, enforcing an **Honest Halt**, and locking down **Rate-Limit & Deduplication Guards**, Autonomous Improvement V3 transforms from a narrow, repetitive mutation runner into a truly universal, high-signal autonomous engineering partner for any codebase.
