# Workflow, Roles, and Evidence

This is the one canonical per-task protocol. `SKILL.md` carries only a summary.

## Per-task protocol

All commands come from the repo's `config.json` copy of
`scripts/autonomous/config.example.json` (argv arrays). The examples below use
placeholders; do not copy a stack's command into the skill.

```bash
# 1. Select a target (Stream A shown; use --stream B for test audits).
python3 scripts/autonomous/ledger.py next --stream A

# 2. Isolated worktree from the configured base, normally origin/main.
python3 scripts/autonomous/orchestrator.py create-worktree --slug <slug> --type test
cd ../<repo>-<slug>

# 3. Baseline: one targeted test, on the mapped test file.
<test_cmd> <test_file>                  # must pass on the base

# 4. Find the gap. For Stream A, run the engine; for Stream B, read the file.
python3 scripts/autonomous/mutation_engine.py --source <target> --test-cmd "<targeted test cmd>"

# 5. Prove red/green for one finding:
<test_cmd> <test_file> -k <name>        # RED with the mutant / defect applied
<test_cmd> <test_file> -k <name>        # GREEN on clean source

# 6. Commit, then run the local gate mirror on touched files.
git add -- <files by name>
git commit -m "<type>(<area>): <summary>"
python3 scripts/autonomous/orchestrator.py verify-gate --worktree .

# 7. Push and open the PR, then record the audit in that same branch/PR.
git push -u origin <type>/<slug>
gh pr create --title "<type>(<area>): <summary>" --body "<evidence>"
python3 scripts/autonomous/ledger.py record --stream A --target <target> \
  --status COMPLETED --pr '#<n>' --summary "<seed, counts, gap fixed>"
git add -- docs/AUTONOMOUS_WORK_LEDGER.md
git commit -m "docs: record <target> audit"
git push
```

Run `verify-gate` **after committing**: its touched-file set is
`git diff <base>...HEAD` plus uncommitted changes. Before committing, analysis
would miss the branch work it is meant to check.

Re-run `ledger.py next` (or check open PRs) immediately before the first push in
step 7. Another agent may have claimed the same target since step 1.

## Evidence contract

| Class | What the PR body must attach |
|---|---|
| bugfix | Failing test on `origin/main` (red) + passing on the branch (green), with the full base sha |
| test-only (Stream A) | Named mutant; test red **with the mutant applied**, green on clean source; seed/scope if generated |
| test-only (Stream B) | The vacuous/flaky behavior, the stronger assertion, and a before/after run |
| perf | Before/after numbers on a pinned harness, spread not just median, differential test vs the old implementation |
| refactor | No test files edited; same behavior proven by existing tests; mutation score not lower in the touched area |
| feature | Spec updated first in a separate spec-change PR; new invariants have tests |
| gate/spec change | Separate PR, human approval; red-team recall not reduced |

Every proof is **base-relative**: record the full sha of the base the red/green
pair ran against, and re-run it on fresh `origin/main` if main moved. A proof
against a base nobody will merge is not evidence. See
`07-moving-main.md`.

For Stream A, do not claim the test "fails on main". It passes on main; the
mutant is the defect. This is Law 1's test-only branch.

## Roles

Who runs a round depends on why the session exists:

- **Explicit task ask** ("audit this target", "fix this gap"): one session can
  play all three build roles below and run the per-task protocol itself.
- **Skill as the session's only instruction** (multi-day dispatch, unattended
  fleet): the receiving session is the **Orchestrator** and must not build;
  it supervises. This is not optional — a busy orchestrator that burns its
  whole context on one task leaves the fleet unwatched.

The Orchestrator:

- arms the unattended stack in `05-unattended-operation.md` **first**: wake
  chain before any housekeeping, watchdog live, session id and journal
  written — it becomes the keeper/meta-agent only after that;
- derives the queue from `ledger.py next` plus the open-PR list, and
  re-checks both immediately before each dispatch (main moves; see `07`);
- dispatches at most 3 worker subagents at a time (tune to capacity), each
  running the per-task protocol above in its own isolated worktree;
- passes each finished PR through the Sentinel check, and merges only when
  the PR's checks are green per the repo's merge policy. Workers open PRs
  and never merge; the merge decision stays with the orchestrator/owner.

The three roles a worker (or a solo run) plays:

- **Breaker:** never edits production code. Produces an issue with a failing
  test or a survivor and the invariant it breaks.
- **Builder:** worktree-isolated, proves red/green, minimal change, gated PR.
- **Sentinel:** read-only diff and CI inspection. Checks evidence, file
  ownership, test weakening, and scope. Does not polish.

## Git discipline

- Stage by name, never `git add -A`.
- Never `push --force` or `reset --hard` outside your own worktree.
- Never delete a worktree you did not create. `orchestrator.py
  cleanup-worktree` only removes paths in its registry and refuses dirty or
  unpushed work without `--force`.
- Check `git status` before and after anything that touches the tree.
- Do not edit the referee to get green. That is a separate gate-change PR.
- Do not weaken tests (`skip`, `xfail`, `.only`, `# noqa`, widened tolerance).
  Use `test_weakening_check.py` locally when the repo has no gate.

## When the premise moves

If main changed in the audited area, re-run the premise before polishing. If the
gap is gone, close the PR as superseded and record that. A stale-base proof is
not evidence; see `07-moving-main.md`.
