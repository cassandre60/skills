---
name: autonomous-improvement
description: Run high-signal autonomous improvement cycles without creating fake work. Use when asked to "improve the repo", "audit and fix bugs", "find and fix performance issues", "increase test coverage meaningfully", or when an agent otherwise needs to spend tokens on mechanically falsifiable work — mutation testing, adversarial test audits, property fuzzing, and migration archeology — rather than cosmetic churn, vacuous tests, or full-suite local runs.
---

# Autonomous Improvement

Spend tokens on mechanically falsifiable work — not on prose, renames, or checkbox tests.

Every task here must clear three bars: it **fails without the fix** (fail-to-pass), it **does not already exist at this content hash** (deduplication), and it **does not melt the machine** (targeted tests + CI for the full suite). If any bar is skipped, the work is fake by definition.

This skill is complementary to `agent-proof-codebase`. That skill builds the gates that make fake work fail. This skill spends tokens finding the real work — surviving mutants, vacuous tests, flaky sleeps, migration gaps, and data-loss orphans — and turning each one into a small, gated PR with fail-to-pass proof. If the repo has no gates yet, run `agent-proof-codebase` mode A/B first.

## The 4 Laws

These are non-negotiable. Violating any one of them turns a cycle into fake work.

| Law | Rule | Why it exists |
|-----|------|---------------|
| **1. Fail-to-pass** | No bugfix or test lands without a test that **fails on `origin/main`** before the fix and passes after. | A test that passes on unmodified code has zero signal. |
| **2. Content-hash dedup** | Deduplicate on **content hash** (`git hash-object` / `git rev-parse HEAD:<file>`), not commit identity (`git log -- <file>`). A revert or cherry-pick can give the same commit hash for different content. | Prevents re-auditing unchanged files and endless duplicate PRs across parallel agents. |
| **3. Worktree isolation** | Every task runs in an **isolated worktree** branched from `origin/main` (not local `main`). Never `git add -A`; stage by name. Never run destructive git commands outside the assigned worktree. | One agent's `reset --hard` in a shared tree silently destroys another's uncommitted work — unrecoverable. |
| **4. Machine budget** | Run **one targeted test** at a time (e.g. `--plain-name` for a single test), one full-file run at the end. Never run the full suite or `./verify` locally — that is CI's job. One `analyze` per task, on touched files, after the last edit. | A full run pins every core and has heated a workstation to hard-reboot territory here before. |

## Quick Start

When asked to "improve the repo" or equivalent, follow this 5-step loop:

1. **Pick + dedup:** Select the next ready task from the project's real plan (`IMPLEMENTATION_PLAN.md`, backlog, or equivalent). For quality work below, query the ledger:
   ```bash
   python3 scripts/autonomous/ledger.py next --stream <n>
   ```
   If the file's content hash has not changed since its last `COMPLETED`/`SOUND` ledger entry, skip it. Run `gh pr list` and re-check before pushing — another agent may have opened a PR in between.
2. **Prove failure:** Write a test or mutation that **fails on `origin/main`**.
3. **Isolate:** Create `../<repo>-<slug>` from `origin/main` and work there.
4. **Gate:** Verify the fix passes, run the project's gate entry point **after committing** (diff-based gates read `origin/main...HEAD` and see nothing while uncommitted), and attach evidence.
5. **Record:** Append the ledger row in the same PR. On conflict (`git pull --rebase` needed), rebase and retry — don't overwrite the other row.

## The 4 Improvement Streams

Two streams are **active on day one**. Two are **planned** — they need a harness first and are not executed to "use tokens". Copy the stream that matches your stack; ignore the rest.

### Stream A — Active: Mutation Testing (Breaker -> Builder)

Mechanically find blindspots where logic could break without tests noticing.

- **How:** Invert semantic operators per file — relational (`==`/`!=`, `<`/`<=`), logical (`&&`/`||`), null checks, booleans, collection predicates (`.isEmpty`), off-by-one — then run the matching test file.
- **Signal:** `KILLED` means the suite caught it. `SURVIVED` means a proven gap (triage first — regex-based engines produce equivalent mutants). `UNCOMPILABLE` is noise, excluded from kill rate.
- **Output:** One small PR per triaged survivor — a test that fails on the mutant, passes on clean code, citing the invariant (`// SPEC INV-xxx` or equivalent).
- **Promotion:** Diff-scoped mutation becomes a blocking gate only after two green runs without false positives.
- **Tool:** `scripts/autonomous/mutation_engine.py` (regex-based, no AST — every survivor needs human triage; uses `shell=False` via `shlex`).

### Stream B — Active: Adversarial Test Audit (The 3 Sins)

Harden the existing suite. Every flag needs human judgment — a flagged line is not automatically bad.

1. **Can it actually fail? (Vacuity):** Does the test assert real state changes, or trivialities? Example: a test titled "does not rewrite the table" that never reads the table.
2. **Does it assert behavior or the mock? (Over-mocking):** Replace mocks that echo their setup with in-memory domain entities, fake clocks, or in-memory stores.
3. **Is it a timing bomb? (Flakiness):** Replace fixed wall-clock sleeps (`sleep(80)`, `Future.delayed`) with condition polling or virtual clocks. Never add a sleep to "stabilize" a test.

### Stream C — Planned: Property Fuzzing & Model Checking

Verify invariants across thousands of randomized transitions. Requires a nightly harness before it can gate PRs.

- **Examples:** Round-trip idempotence (structural equivalence after `import -> export -> import`, not literal string identity), rate/scheduling monotonicity, queue state-machine fuzzing (deadlocks, negative counts).
- **Not yet:** Wire the harness first; don't claim this stream is "proven" until it runs nightly.

### Stream D — Planned: Migration & Data Preservation Archeology

Prove durability across every shipped schema version.

- **Examples:** Fixture databases per historical schema version, forward-migration assertions with zero lost rows, orphan-row regression tests, foreign-key/cascade checks.
- **Not yet:** Needs a fixture harness and representative data; track it in the project plan, not as busywork.

## Workflow (one task)

```bash
# 0. Branch from origin/main, not local main (which may carry another agent's unpushed work)
git remote set-url origin <url>
git fetch origin main
git worktree add ../<repo>-<slug> -b <type>/<slug> origin/main
cd ../<repo>-<slug>

# 1. Baseline worktree setup (each worktree needs its own generated files)
# Flutter/Dart: build_runner; other stacks: whatever generates ignored outputs
# Example (Flutter):
fvm flutter pub get
fvm dart run build_runner build --delete-conflicting-outputs
fvm dart format lib/l10n/  # discard codegen churn; don't commit it (if applicable)

# 2. Prove red on baseline (one test, not the file — keeps the loop cool)
<test-cmd> --plain-name '<test name>'    # must FAIL on origin/main

# 3. Minimal fix, then green
<test-cmd> --plain-name '<test name>'    # must PASS after fix

# 4. Gates after committing (diff-based gates read HEAD, not the working tree)
<format-cmd> lib test
<analyze-cmd> <touched-files>
./scripts/gates.sh t1                      # or the project's gate entry point
# Direct tripwire when gates.sh is unavailable:
python3 scripts/gates/test_weakening_check.py --base origin/main

# 5. Stage by name, push, PR with evidence
git status
git push -u origin <branch>
gh pr create -R <owner>/<repo> --title "<type>(<area>): <summary>" --body "<fail-to-pass evidence>"
```

Loop budget: one `--plain-name` iteration, one full-file run at the end, one `analyze` after the last edit. The fail-on-old-code check counts as the second run — then stop. CI runs the full suite on every push so a local wide run is duplicate heat, not extra safety.

## Roles (optional, for parallel agents)

For teams running multiple agents, the ledger and worktrees act as the message bus. A single agent can do all three.

- **Breaker:** Never edits `lib/`/`src/`. Finds survivors, fuzz failures, or smell flags. Output: Issue with a copy-pasteable failing test and a cited invariant.
- **Builder:** Isolated worktree, proves red on `origin/main`, minimal fix, `gates.sh t1`, gated PR.
- **Sentinel:** Read-only diff + CI inspection — no protected-file edits, no weakened tests, no fake abstractions.

## Work Ledger

Each repo keeps `docs/AUTONOMOUS_WORK_LEDGER.md` (or equivalent) with one row per completed audit:

```markdown
| Date | Stream | Target File / Module | Content Hash | Status | PR / Issue | Summary / Outcome | Re-Audit Trigger |
|---|---|---|---|---|---|---|---|
| 2026-10-18 | Stream A (Mutation) | `src/auth/token_service.ts` | `a1b2c3d` | COMPLETED | PR #42 | 25 mutants: 20 killed, 3 invalid, 2 gaps fixed | Any modification to `token_service.ts` |
```

Invalidation: current content hash matches ledger `Content Hash` + status `COMPLETED`/`SOUND` -> skip. Hash changed or absent -> eligible. Two agents appending will conflict — `git pull --rebase origin/main` and retry.

## What Not To Do

- Cosmetic renames, import reordering, splitting 5-line functions, or any refactor without a behavioral invariant it defends.
- Tests that assert literals, echo mocks, or pass regardless of implementation.
- A "fix" that is really `skip`/`xfail`/`// eslint-disable`/`@ts-ignore`/widened tolerance/catch-all.
- Editing the referee (specs, gate configs, CI, `CODEOWNERS`, ratchet baselines, linter config) to get a pass — that's a separate gate-change PR with human approval.
- Inventing features the plan hasn't scheduled. The default answer for a new capability is **no**.

## Stack Adapters

- **Dart/Flutter:** `fvm flutter test <file> --plain-name '<name>'`, `fvm flutter analyze <files>`, `dart format .` (page width from `analysis_options.yaml` — run `pub get` first or the formatter silently falls back to 80 and rewrites everything), `build_runner` per worktree, `lib/l10n` churn discard.
- **JS/TS (Biome or ESLint + Prettier):** `biome check --write` or `eslint`+`prettier` on touched files; `vitest`/`jest` with `--run -t '<name>'` or equivalent; never run the full suite locally as the inner loop.
- **Python:** `ruff check --fix` + `ruff format`, `pyright`/`mypy`, `pytest <file> -k <name>`.
- **Other:** Discover the project's own `AGENTS.md`/`CONTRIBUTING.md`/`Makefile`/`justfile`/`package.json` scripts and use those commands — don't guess generic ones.

## Assets & Scripts

Copy from `assets/` and `scripts/` into the target repo; protect them with `CODEOWNERS` once copied.

| Path | Purpose |
|---|---|
| `scripts/autonomous/mutation_engine.py` | Regex-based mutation runner — language-agnostic pattern list, per-language tuning noted in header |
| `scripts/autonomous/ledger.py` | Content-hash ledger manager (`next` / `record`) |
| `scripts/autonomous/orchestrator.py` | Worktree lifecycle + `gates.sh t1` helper |
| `assets/AUTONOMOUS_WORK_LEDGER.template.md` | Starter ledger table |
| `assets/protected-paths.fragment.txt` | Lines to append to a protected-paths file for these assets |

Mutations are regex-based, not AST-based. Every surviving mutant must be triaged — many are equivalent mutants or hit string literals. Promotion to a blocking gate requires two green runs without false positives on that repo.

## Honest Limits

- Gates verify conformance to the spec, not the truth of the spec; the spec stays human-owned.
- Mutation here is not a replacement for AST-based tooling (Stryker, PIT, mutmut, etc.) — it's a lightweight, zero-dependency breaker for repos without one.
- Benchmarks and flakiness are measurement problems first: fix the instrument before optimizing, report spread not just median, and re-run a suspect test in isolation before blaming a change.
- Gate maintenance is real work. Delete gates that stop paying.

## References

Load on demand; don't carry every detail in the entrypoint.

| File | When to read |
|---|---|
| `references/01-mutation-engine.md` | Tuning mutations, triaging survivors, language-specific patterns |
| `references/02-ledger-and-dedup.md` | Ledger schema, hash choice, merge contention, PR collision avoidance |
| `references/03-streams-and-triage.md` | Deep dive on all four streams, triage checklists, promotion criteria |
| `references/04-workflow-and-roles.md` | Full per-task protocol, worktree discipline, PR evidence contract |
