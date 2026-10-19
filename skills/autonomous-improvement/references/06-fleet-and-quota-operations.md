# Reference 06 — Fleet and Quota Operations (model rotation, quota-aware fleets)

Running several improvement tabs in parallel on free/rationed models, plus what to do
when a **second fleet** is operating in the same repo. All items verified on a real
multi-day run (3 workers, 7 free models, zero merges, ~10 open PRs, one full rate-limit
fleet cancellation).

## Model pool and allocation

- Enumerate free models at dispatch time (e.g. opencode: `tools.opencode.models`,
  filter `-free`), and keep a **rotation list** in the orchestrator journal.
- Assign models per tab and record it: **A task's failure mode is model-dependent**;
  when one model produces sloppy work, rotate rather than re-dispatching to the same model.
- Track rate-limit episodes per tab in the journal. Shrink to ceil(active/1.5) tabs if
  throttling repeats; raise again after 24h of clean behavior.
- Proven defaults from that run (YMMV): strong models for Breaker/precision tasks
  (red-team triage, gate-maintenance), mid-tier for file conversions, and any free
  model for single-file mechanical retrofits. Cheap-but-precise beats cheap-but-sloppy
  for referee-adjacent work.

## Quota-aware worker protocol (put this in every dispatch prompt)

1. **Progress checkpoints via local commits.** After every unit of work (a batch of
   5 conversion sites, one mutation shard, one of 4 audit rows), `git add <files by
   name>` + local commit. A cancelled tab's next session sees a resumable base.
2. **Scope ceilings.** Give single-tab tasks an explicit cap ("convert the first ~15
   of ~30 sites and stop; report the remainder") so uncheckpointed work stays small.
3. **PARTIAL beats death.** After 2-3 consecutive quota failures, finish
   the current unit, commit the valid prefix, push, and open a PR labeled PARTIAL
   (title + body), with the remaining count in the body. **Never** silently die.
4. **Report rate-limit episodes** (counted per episode) in the final report, so the
   orchestrator rotates the model for a follow-up.

## File-ownership map (prevention of cross-tab collisions)

The orchestrator keeps a live map: *(tab -> owned paths)* and consults it on every
dispatch and at `git pr list` planning time.

- Two tabs **never** share a file (a large test file is one tab's file).
- Where a later wave needs the same file an open PR holds, schedule it keyed on
  merges ("after #X merges") rather than cache it.
- Re-check `gh pr list` immediately before pushing: another agent may have opened a
  parallel PR on the same file between your dispatch and push. If one exists, stop
  and escalate in a PR comment rather than racing.

## Cross-fleet coexistence

On a shared repo, other agents (possibly different harnesses, different naming
conventions e.g. `arena/...` branches) may open PRs concurrently — including
**wholesale ledger rewrites** while your fleet only appends. Ground rules:

- Read every foreign PR's file list and diff stat before commenting; your sentinel
  opinion should be limited to objective collision facts + policy flags
  ("feature-creep policy questions are the owner's call"), not style opinions.
- Never rebase/delete or rewrite other agents' rows/cases/sections.
- Ledger-style append files are append-only for your fleet; if a foreign PR
  rewrites the file wholesale, flag merge-order expectations for the owner in a PR
  comment and leave it alone.
- No attribution footers on foreign PRs — they are not your artifacts.

## Attribution discipline (PR/issue bodies)

When **you** create or edit PRs/issues via `gh`, append the footer (body only —
never titles, comments, or repo files):

```
---
Machine: <hostname>
Harness: OpenCode
Model: <model ID (with variant if known)>
```

- Workers dispatched before this rule arrives produce footerless PRs; the
  orchestrator appends footers at sentinel-review time (body reshape only).
- `gh pr edit` can be hard-broken by provider GraphQL deprecations
  (`Projects (classic)` errors). Fall back to REST:
  `jq -n --rawfile b body.md '{body: $b}' | gh api repos/<o>/<r>/pulls/<n> -X PATCH --input -`

## Ledger and protected-path friction (designed, not defects)

Where a repo marks its work ledger a protected path, every append-only PR tripis the
"T1 fast gates" G07-class check **by design** (owner approval / `gate-approved` label
at merge). Instruct workers:

- Do NOT drop the ledger row to go green; document the expected G07 red in the PR
  body AND paste the approval-bypassed local run (`APPROVED=1 ./scripts/gates.sh t1`).
- This is the designed friction of an append-only ledger — the owner, not the
  agent, decides expedited merges.

## Sentinel-review checklist (per worker PR)

1. Files touched == the ownership map grant (no `lib/` for Breakers, no generated
   files staged, `lib/l10n/**` never, protected referee files only with a documented
   gate-change class).
2. Evidence contract matches Reference 04's table (red/green or rg counts, full-file
   run results, PR-body statements about assertion strength).
3. Partials: PARTIAL status may be honest-but-incomplete — check remaining work
   and re-dispatch or queue rather than accept as full completion. (No partial should
   ever ship with a broken build or weakened assertion; check G08 output, trailer
   acks (`Ack-G08: <reason>`), and CI verdicts.)
4. Footers applied (body), sentinel comment posted (facts only), PR left open.
