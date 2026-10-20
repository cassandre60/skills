# Gates and Vocabulary

Every term below is either defined here or marked optional. **None of these
files need to exist for this skill to work.** They are conventions from the
companion skill `agent-proof-codebase`; if that skill is not installed, use the
"no gates yet" fallback at the bottom.

| Term | One-line definition | Required? |
|---|---|---|
| **gate** | Any deterministic command that rejects a change. A repo may have none, one, or many. | optional |
| **T1 fast gates** | The subset of gates meant to run locally in seconds (lint, types, focused tests). Heavier suites stay in CI. | optional |
| **`gates.sh`** | A repo-local entry point that mirrors CI (`./scripts/gates.sh t1`). A convenience, not a dependency. | optional |
| **G05** | *Spec traceability*: every invariant/requirement id in `SPEC.md` is cited by at least one test, and tests cite only real ids. | optional |
| **G07** | *Protected paths*: a diff touching the referee (specs, gate config, CI, `CODEOWNERS`, ratchet baselines, lint config) fails unless a human-approved label/flag is present. | optional |
| **G08** | *Test-weakening tripwire*: removed assertions, new skips/xfails/suppressions, or deleted test files fail the diff. | optional (this skill ships a heuristic version) |
| **`GATES_PLAN.md`** | The companion skill's maturity/gap plan for each risk tier. Nothing here reads it. | optional |
| **`SPEC.md`** | A human-owned list of invariants and requirements. Tests cite ids from it. | optional |
| **`AGENTS.md`** | Repo-local agent instructions (build/test/style). If present, it wins over generic advice. | optional |
| **ratchet** | A monotone budget (coverage, mutation score, LOC, dependency count, performance) that may only improve. Enforced by a script and a committed baseline. | optional |
| **R0/R1/R2/R3** | Risk tiers: R0 = loss/corruption/security, R1 = core logic, R2 = glue, R3 = cosmetic. Used for prioritization only. | optional |
| **`gate-approved` label / `APPROVED=1`** | A human approval mechanism for a protected-path change. Exact spelling is project-defined. | optional |
| **`agent-proof-codebase`** | Companion skill that builds gates and evidence contracts. This skill works without it. | optional |

## What this skill actually assumes

1. `git` and `python3` exist.
2. The target repo has (or can name in config) a **targeted test command**.
3. Optionally, `gh` exists for open-PR collision detection.

It does **not** assume `gates.sh`, `SPEC.md`, `AGENTS.md`, `CODEOWNERS`,
ratchets, G05/G07/G08, or the companion skill.

## No gates yet: fallback

If the repo has no gate system:

1. Put the project's own CI commands into `config.json` as `format_check_cmd`,
   `analyze_cmd`, and `test_cmd`. Run them on touched files only
   (`orchestrator.py verify-gate`); never run the whole suite locally.
2. Run the bundled tripwire:
   ```bash
   python3 scripts/autonomous/test_weakening_check.py --base origin/main
   ```
   It flags removed assertions, new skips/focus markers, and deleted test files.
   Every hit is a review question, not an automatic rejection.
3. For the "referee" (specs, CI, lint config, ratchet baselines), use
   `CODEOWNERS` if it exists; otherwise state in the PR body that no referee
   files were touched.
4. Never block on a missing `gates.sh`. A missing optional gate is skipped, not
   failed.

If the companion skill **is** installed, its gates are a better long-term
version of the same checks; this skill's workflow still works unchanged.

## If a repo protects the ledger

Some repos make `docs/AUTONOMOUS_WORK_LEDGER.md` a protected path. Then every
ledger-recording PR trips G07 by design. Do not drop the row to go green:
document the expected red in the PR body and ask the owner to approve the
append-only change. Apply the protected-path and `merge=union` fragments
together (see `02-ledger-and-dedup.md`).
