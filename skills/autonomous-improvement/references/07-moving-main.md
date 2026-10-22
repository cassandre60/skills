# Reference 07 — Moving Main (working on a repo that keeps merging)

The queue is derived state and main is a moving target. Any agent — a local tab, a laptop
fleet, a second machine — can burn hours hardening a symptom that main already fixed at the
root, or open a PR against a base nobody will merge. Every rule here exists because its
failure happened on a real run.

## Merge reality: strict protection and squash merges

A repo with **strict branch protection** and **squash merges** adds a fourth
failure shape that is not about drift at all: a PR that is **green and still
cannot merge**, because it does not contain the base branch's tip.

- The rule is usually *"the PR must contain the base branch's head"*. Every
  other agent's merge invalidates that, so a green PR goes stale the moment
  anyone lands anything — including the owner.
- **`--admin` does not bypass it.** The refusal looks like
  `GraphQL: 2 of 2 required status checks are expected` — a message about
  *checks*, not about staleness, which is why it reads like a CI problem and
  sends you looking at the wrong thing.
- **The recovery is always the same**: re-sync the branch, wait for a fresh
  green **on the new head**, then merge. A green run for the previous head is
  not a verdict (see the CI green loop in `04`).

### Re-syncing a pushed branch: merge, never rebase

A branch that is already pushed **cannot be rebased without a force-push**, and
`references/04-workflow-and-roles.md`'s git discipline forbids force-pushing.
So the two references
must agree, and the answer is: **merge the base into the branch**.

```sh
git fetch origin
git merge origin/main --no-edit -m "Merge origin/main into <branch>"
git push origin HEAD:<branch>
```

Then wait for a fresh green on the new head. This is safe on a shared repo:
it adds a merge commit rather than rewriting published history.

### A branch created in an existing worktree inherits that worktree's commits

`git checkout -B <new-branch>` in a worktree that already carries commits puts
those commits **in the new branch's history**. The PR's diff against the base
is the branch's *whole* history, not the files you last touched — so a PR can
touch a file it never meant to, and fail a gate for it.

Before opening a PR, read what the PR will actually contain:

```sh
git diff origin/main...HEAD --stat
```

If that lists a file the task never touched, the branch carries someone else's
commit. Create the branch in a **fresh worktree** instead.

## The three failure shapes

1. **Obsolete premise** — the defect the task targets is gone from main, often via a
   lib-side root-cause fix landing while a test-side tab was still studying the symptom.
2. **Drifted base** — the work is fine but old: CI red or conflicting against new main,
   and the red/green proof was run against a base nobody will merge.
3. **Measurement lie from the wave** — an old-base worktree makes `origin/main..HEAD`
   include other people's merged commits. Naive counting turns a merge wave into
   "25 commits of my own progress".

## State to track (orchestrator journal)

One line per in-flight task:

```
TabK | task=<slug> | base=ed86543f1 | dispatched=HH:MM
```

The base is the sha the premise was verified against — not the label "origin/main".

## Detection: per-wake drift check (cheap, no interruption)

One remote call per wake, per fleet — compare each in-flight tab's base against current
main:

```bash
git ls-remote origin refs/heads/main | cut -d' ' -f1
```

On drift, append `main moved to <sha> while TabK in flight (base <old>)` to the journal.
Do NOT interrupt the tab mid-flight; the completion gate below is where drift is resolved.
The check exists so the sentinel knows, before reviewing, that a premise recheck is due.
Also note: the source of drift is ANY merge — the owner's, another fleet's, yours.

## The completion gate (sentinel, non-skippable)

At every worker completion, before polishing anything:

```bash
git fetch origin
git merge-base HEAD origin/main        # the branch's ACTUAL base
```

1. **Recheck the premise on fresh main** — re-run the exact command that established the
   defect (test fails on main / the sleep site exists / the vacuous assertion is present).
2. Branch on the outcome:
   - **Premise gone** → do NOT polish. Close the PR as superseded, cite the fixing commit,
     and record `closed upstream by <sha>` in the ledger so queue re-derivation does not
     resurrect the task.
     - The substance may have landed under a different number (a collision absorbed: #Y
       merged while #X closes). Record `superseded by #Y` with the disposition so a ledger
       row citing closed #X stays resolvable.
   - **Premise holds** → rebase onto fresh origin/main, re-run the red/green pair on the
     NEW base, then gates + CI. A proof run against an old base is not evidence.
   - **Premise holds but the file changed materially** → treat as a collision: re-diff,
     confirm the assertions are still meaningful on the new shape, re-aim the harden.

After a merge, both colliding PRs' bases go stale, not just one (two PRs on one file
become two rebase jobs plus a merge-order decision — the owner's).

## Worker-side tripwires (put in every dispatch prompt)

- Before the final push: `git fetch origin && git log --oneline HEAD..origin/main | wc -l`.
  If > 0: rebase onto origin/main, re-verify premise + red/green, THEN push.
- "If the lines you are about to edit changed on main since your base, STOP and report
  instead of pushing a stale fix."
- Progress checkpoints (Reference 05) bound the cost of whatever drift you do hit.

## Base-relative artifacts

Audits and negative results expire when main moves in the audited area:

- Every ledger row / audit report cites the full base sha it was verified against.
- "No real gaps found" is true AT a sha, not forever. If main moves meaningfully in the
  audited area, the verdict's validity window closes; record the re-audit trigger instead
  of silently trusting the old result.

## Worktree reuse (the stale-base trap)

Worktrees are reused across relaunches to skip codegen setup — which is exactly how stale
bases persist. Before re-dispatching into a reused worktree: confirm dirty files are yours
(ownership map, Reference 05), then `git fetch origin && git rebase origin/main` — or
`reset --hard origin/main` on a clean worktree you own. Never reset a worktree you do not
own, and never reset around dirty files you did not create.

## Hard lessons (verified)

- [2026-10-19] A merge wave turned an old-base worktree's `origin/main..HEAD` into other
  people's commits; two tabs were declared wedged partly on that lie. Measure own progress
  against `git merge-base HEAD origin/main`, and count only commits you authored.
- [2026-10-19] Main eliminated an offline-game sleep via a lib-side fix (fakeable clock on
  package:clock) while a tab was mid-study on the test-side symptom; the tab's salvageable
  value became the insight, not a test change. Test-side hardening on a repo that fixes
  root causes carries structural obsolescence risk — dispatch-time premise checks only
  reduce, not remove, this.
- [2026-10-19] Tasks blocked on other PRs are keyed on merge events ("after #X merges");
  treat the queue as re-derived at every dispatch, never as owned memory. Journal rule that
  has prevented multiple stale dispatches.
