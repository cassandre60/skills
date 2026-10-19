# Workflow, Roles, and Evidence

## Per-task protocol

```bash
# Branch from origin/main, not local main
git remote set-url origin <url>
git fetch origin main
git worktree add ../<repo>-<slug> -b <type>/<slug> origin/main
cd ../<repo>-<slug>

# Per-worktree setup (each worktree needs its own generated files)
# Flutter: pub get + build_runner + discard lib/l10n churn
# JS/TS: npm/pnpm install as needed
# Python: whatever your lockfile needs

# Prove red on baseline
<test-cmd> --plain-name '<name>'   # FAIL on origin/main

# Minimal fix, then green
<test-cmd> --plain-name '<name>'   # PASS after fix

# Gates after committing (diff-based gates read origin/main...HEAD)
<format-cmd> lib test   # or src/ test/
<analyze-cmd> <touched files>        # one analyze per task, after last edit
./scripts/gates.sh t1                  # project's gate entry point; G08 is the tripwire
gh pr create -R <owner>/<repo> ...
```

Adapters per stack are in the SKILL.md Stack Adapters table.

## Evidence contract

| Class | What to attach |
|---|---|
| bugfix | Failing test on `origin/main` (red) + passing on branch (green) + G08 clean |
| test-only | Named survivor/ratchet it improves; red->green not required but preferred |
| perf | Before/after numbers on pinned harness, spread not just median, differential test vs old implementation |
| refactor | No test files edited (G08 verifies), mutation score not lower |
| feature | Spec updated first in a spec-change PR; properties for new invariants |
| gate/spec-change | Separate PR, human approval, red-team recall not reduced |

Every proof is base-relative: record the full sha of the base the red/green pair ran
against, and re-run it on fresh `origin/main` if main moved since (Reference 07). A
proof against a base nobody will merge is not evidence.

## Roles

- **Breaker:** Never patches `lib/`/`src/`. Produces an Issue with a failing test + invariant citation.
- **Builder:** Worktree-isolated, proves red on `origin/main`, minimal fix, gated PR.
- **Sentinel:** Read-only diff + CI inspection — no referee edits, no weakened tests, no fake abstractions.

## Git discipline

- Stage **by name**, never `git add -A` (one agent's `add -A` commits another's in-flight formatting churn).
- Never `push --force` or `reset --hard` outside your own worktree.
- Check `git status` before and after anything that touches the tree.
- Never delete a worktree you didn't create (`git worktree list` first).
