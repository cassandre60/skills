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
python3 scripts/autonomous/mutation_engine.py --source <target> --test-cmd "<targeted test cmd>" \
  --output-json .autonomous-last-run.json
python3 scripts/autonomous/mutants.py ingest --report .autonomous-last-run.json

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
python3 scripts/autonomous/mutants.py record --id <file:line:hash> \
  --disposition KILLED-BY --pr '#<n>' --reason "<assertion that kills it>"
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

- performs the **cold start** in `05-unattended-operation.md` before anything
  else: find the state dir, learn and write its own session id, arm the wake
  grid, start or install the watchdog (or degrade to beats-only), read the
  journal tail — only then decide anything about the queue;
- **governs the dispatch rate (step 0 of every wake)**: runs
  `python3 scripts/autonomous/fleet.py govern --json`. If a kill-switch file is
  present, or open fleet PRs have reached `fleet.max_open_prs` (default 5), or the
  14-day acceptance rate is below floor, **do not dispatch new workers** — spend
  the wake tending existing PRs (fixes or owner asks in the `tend` queue).
  Tending the queue is work; generating into a full queue is the new parking;
- derives the queue from `ledger.py next` plus the open-PR list, and
  re-checks both immediately before each dispatch (main moves; see `07`);
- dispatches at most 3 worker subagents at a time (tune to capacity), each
  running the per-task protocol above in its own isolated worktree;
- **rotates the model pool on quota failures**: a worker that dies with a
  rate-limit, 429 or "$0 balance" error is re-dispatched on a different live
  model — never the provider/model that just failed — and the dead model is
  recorded on the journal's dead-pool list. When every pool is dry, it reports
  that plainly instead of spinning workers against dead pools;
- **never parks a task because of a pool**: the task is re-dispatched on a live
  model — the session's own, dispatched unpinned, first — or it is reported as
  blocked-with-reason. Idle because a pinned pool died is the failure mode
  this fleet is most prone to (verified 2026-10-20: three pinned pools died
  within minutes, the fleet stopped, and the session's own pool was alive and
  untried the whole time);
- **re-derives the queue from the codebase**: when the tracked targets are
  exhausted, the next candidates are the files with no ledger row yet. An
  exhausted list is a signal to look wider, not to idle;
- **owns every PR from creation to merge**: it reads that PR's checks on every
  wake, drives a red check that sits inside the diff back to green (re-dispatch
  the fix to the same worker session, or to a fresh worker on a live model),
  re-asks the owner per item when a protected-path gate needs a label, and
  merges only when the checks are green **on the current head sha**. Workers
  open PRs and never merge; the merge decision stays with the
  orchestrator/owner.

The three roles a worker (or a solo run) plays:

- **Breaker:** never edits production code. Produces an issue with a failing
  test or a survivor and the invariant it breaks.
- **Builder:** worktree-isolated, proves red/green, minimal change, gated PR.
- **Sentinel:** read-only diff and CI inspection. Checks evidence, file
  ownership, test weakening, and scope. Does not polish.

## The CI green loop

A PR is not done when it is created; it is done when it is merged. The gate
"merge only when green" is a stop sign, not a process — without a loop, PRs
sit red and the fleet looks busy while nothing converges.

1. **Worker, after opening the PR:** read the checks once
   (`gh pr checks <n> -R <repo>`).
   - Red for a reason *inside the diff* (test failure, formatting, analyze, a
     weakening tripwire) → fix it, re-push, re-read the checks. Up to **two**
     iterations.
   - Red for a protected-path/referee gate → **stop and report it to the
     orchestrator**. A worker never tries to make a referee gate pass.
2. **Orchestrator, every wake:** for each open PR, read the checks.
   - Red and fixable → dispatch the fix to the same worker session (resume it
     by session id) or to a fresh worker on a live model, with the failing log
     excerpt attached. The fix is a normal task: same worktree, same branch.
   - Red on a protected-path gate → ask the owner per item **and re-ask on
     every subsequent wake until answered**. An ask that is never repeated is
     an ask that never lands.
   - Green → Sentinel (evidence, ownership, no weakening) → merge.
3. **Green means green on the head sha.** Compare the sha the run reported with
   `gh pr view <n> --json headRefOid`. A green run for an *older* sha, or a
   head sha that never received a run, is not a verdict — it is a different
   commit's verdict. (Verified 2026-10-20: a PR whose head carried a second
   commit had exactly one green run, for the first commit.)
4. **Iteration cap:** two fix iterations, then escalate to the owner with the
   failing log excerpt. Do not spin a third, fourth or fifth attempt.
5. **Per-PR state line**, kept in the journal or state dir:
   `PR# | head sha | checks | owner-ask pending? | iterations`. A frozen or
   restarted session reads this list instead of guessing, and a PR whose line
   says `owner-ask pending` is re-asked, not forgotten. Emitted deterministically
   by `python3 scripts/autonomous/fleet.py pr-state --json` (0 model tokens).
6. **Nothing red goes quiet.** If a PR has been red for two wakes without an
   owner answer, say so in the owner report — in plain terms, with what the
   red check is and what the owner is being asked to accept.
9. **An instrument that reports false negatives must be fixed before its
   output is trusted.** Two failures in one day had the same shape: the
   *measurement* was wrong, not the code. A metrics query silently fell back
   to a field set without `mergedAt` and reported "200 PRs opened, 0 merged,
   0% acceptance" for a fleet that had merged twenty; and the shared test
   scope measured every string about twice as wide as the app draws it, so
   22 of 24 reported layout overflows were artefacts. Before you act on an
   instrument's output, ask what it would report if the instrument were
   wrong — and when it *is* wrong, fix it before drawing conclusions from
   it. A green run of a broken instrument is worse than a red one, because
   it is trusted.

8. **Batched ledger rows conflict with each other.** Every ledger row is
   inserted at the top of the same table, so two ledger branches collide on
   the same lines. When the orchestrator's ledger branch merges a base that
   has moved, resolve it by REBUILDING: take the base's ledger, re-insert
   the rows the branch contributes, and commit that — never commit conflict
   markers, and verify the pushed file contains none before moving on. A
   conflicted ledger pushed to a PR is worse than a missing row.

7. **Protected-file bookkeeping is batched, not bundled.** When a repo treats
   an audit ledger as a protected path, a worker's row for it blocks the PR on
   a gate label — and the gate's own message says such an edit "must be a
   separate PR, not bundled with product code". So workers must not touch that
   file: they report the row text, and the orchestrator collects the rows
   into **one** separate ledger PR per wave. One owner decision per wave
   instead of one per PR, and the product PRs ship green. (Verified
   2026-10-20: five audit PRs were each blocked on the same kind of one-line
   append; batching them turned five owner decisions into one.)

## The owner ask: plain terms, and a record

A gate that needs a human is not a technical question, and an ask written in
gate jargon does not get answered. When the orchestrator needs a label, a
decision, or a change of policy, the ask states **three things in plain
language**, with no gate vocabulary in them:

1. **What changes** — what the owner will have, in terms of the product or the
   repo, not in terms of a protected path.
2. **What it costs** — the time, the risk, the review burden, whatever is
   really being asked for.
3. **What happens if the owner declines** — the default that applies instead,
   so the answer can be "no" without leaving a hole.

### Consent is per item, and it is recorded

An owner may give a **standing consent for a class of edit** — "apply the label
to protected-path PRs of this kind, and do the merges". That consent does not
travel to the next PR: it covers the class it was given for, and every item
still gets its own record.

**The record goes on the PR**, in plain language, and carries four things:

| Field | What it says |
|---|---|
| the **class** | what kind of edit is being authorised (for example: a bookkeeping row in the fleet's own audit registry) |
| the **authority** | on whose instruction the label was applied, quoted or summarised |
| the **date** | when |
| the **withdrawal path** | how the owner revokes it — delete the label, close the PR |

Without that record a label on a PR is indistinguishable from a label applied
without asking, and a later reader cannot tell consent from assumption. With
it, the owner can withdraw any single item without undoing the standing
consent, and a later agent can see the basis.

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
