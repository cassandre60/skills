# Autonomous Improvement machine — session close notes (2026-11-12)

Machine repo: `~/github-projects/skills` (branch `main` @ `1970cdd`). Machine name:
**autonomous-improvement** (the skill folder `skills/autonomous-improvement/`).

## What the machine is doing right now (live on `chesssrs` @ `/home/ubuntu/github-projects/chesssrs`)

- `survey.py --json` (installed copy, `~/.agents/skills/autonomous-improvement/scripts/autonomous/survey.py`)
  currently returns:
  - **chosen: `defect-repair`**, track `bug`, stream `E`, score 1.0 —
    "failing checks or bug reports detected"
  - **runner-up: `complexity` / `refactor`, score 0.55** (top: `review_controller.dart`,
    `review_session.dart`, `sqlite_study_repository.dart` — real complexity, l10n filtered)
  - **third pick: `upstream` / `research`, score 0.50** — third/fourth: `test-gap` (0.41,
    recency-penalized because `test` ran recently), etc.
- So the machine now reaches bug + refactor + research picks; the earlier
  "everything is mutation testing" behavior was fixed by PRs #176/#179.
- `fleet.py work-gate --json` on chesssrs: `work_available: true`, track `bug`,
  digest `faddbca41a236e7d` — the VM wakes target bug-scout leads, not mutation tests.

## What was delivered in this session (all verified, CI green on GitHub)

- PR **#176** on `cassandre60/skills` (docs): V3 post-implementation audit + remediation plan. Merged 2026-11-12T22:06Z.
- PR **#179** on `cassandre60/skills` (feat): multi-track discovery + generated-l10n
  filter + test-gap evidence + multi-agent dispatch protocol. Merged 2026-11-12T22:09Z.
  Contents (squash commits `71ed86c`, `1970cdd`): survey.py adds
  `bug-scout` (Stream E) + `upstream` (Stream K, track research) with 0-token signal
  finders, `_normalize_track` fixes free-form run-record strings (anti-starvation),
  `is_generated_source` in complexity.py/test_gap.py/bug_scout.py skips
  `lib/l10n/l10n_*.dart`, docs_truth.py gains `ignore_spec_pattern` (chesssrs configured
  to ignore `A00`–`E99`), evidence.py/fleet.py/fleet_cockpit.py gain `test-gap` evidence
  + track-aware WIP titles, `references/04-workflow-and-roles.md` gains the
  "Multi-Agent Project Improvement" interactive dispatch loop, SKILL.md ≤200 lines.
- `update-skills.sh` run at 22:10Z: VM installed skills @ `1970cdd`, synced
  `wake.sh`. 2026-11-12 survey on chesssrs: chosen `bug-scout` 1.0 (session-start
  state); now flipped to `defect-repair` (skill moved since).

## Open work on chesssrs — user handles merging (30 open PRs)

`#721–#828`: ~10 new since the last summary (e.g. `#828` rollup batch, `#827`, `#826`,
`#825`… down to `#721`). Mixed: mutation pins, swallowed-error fixes, unit-test coverage.
Everything remains human-review territory — the machine never merges without owner
authority; merging is the owner's job.

Unrelated dirty state to leave alone (not mine):
- `chesssrs`: modified `lib/src/persistence/chapter_orientation_repair_migration.dart`,
  modified `test/persistence/json_adapters_test.dart`; untracked `.autonomous/`.
- `skills` main checkout: modified `mutation_engine.py`, `fleet_pool.py`, `test_v2.py`
  (someone else's; the `skills-model-prefs` worktree on `feat/model-quality-preferences`
  also belongs to someone else — do not touch).

## Ideas — how to make the machine truly "universal" (the open gaps, best next moves)

Your original goal was: invoke `@autonomous-improvement` at any repo root → harvest an
idea pool → dispatch several specialized agents (bug / research / test / refactor / ci) →
reviewable PRs. It is ~70% there; the missing pieces:

1. **Batch dispatch at invoke** (the biggest gap). Today invoke = survey → 1 best pick →
   1 PR. Ideal: invoke = survey → rank top 3–5 candidates **across tracks** → claim →
   spawn subagents (bug-hunter, researcher, refacterer, test-builder) in parallel →
   reviewable PRs. The pieces exist (survey picks, `fleet.py claim`, `references/04`
   "Multi-Agent Project Improvement", Master Cockpit `dashboard`/`worker`) — nothing
   drives them from the plain invocation point.
2. **Make `backlog.py harvest` the primary discovery input.** `survey.py` reads live
   signals; the persistent backlog (386 items on chesssrs: 207 test / 87 refactor /
   78 research / 14 bug) is separate and rarely feeds back into the invoke flow.
3. **Generic discovery signal.** Add red-CI intake (`repair.py intake`) — currently
   `defect-repair` leads only score when something red already exists.
4. **Read the V3 universal plan's owner decisions.** `docs/autonomous-improvement-v3-universal-plan.md`
   (706 lines, in skills repo) §11 lists open decisions (quota-rotation for V2 engines,
   track-softening, ...). Approve or record declines there.
5. **Stack universality tuning.** Several signals are chesssrs-shaped (test-gap,
   docs-truth chess tokens, l10n rule, complexity threshold 500 lines). Fine for now;
   the real test is a fresh repo with default config.
6. Optional: per-invoke `--budget N` flag to cap parallel dispatches per session.

Suggested prototyping order (smallest visible win first): (1b) rank `backlog.py` output
by track/severity and print a dispatch menu; (1) the `fleet.py dispatch` command that
claims the top picks and spawns subagents.

## Where things live

- Machine repo (all skill code): `~/github-projects/skills` — worktree list: `main` @ `1970cdd`,
  plus the pre-existing `skills-model-prefs` worktree. `main` is **2 behind origin/main**
  (`origin/main` @ `38a702a` "feat: add phone-debugging skill"); report-only — the
  owner's checkout, needs `git pull --ff-only` (authorized).
- User-visible review backlog: chesssrs PRs `#721–#828` open, merge with you.
- Key skill files: `skills/autonomous-improvement/SKILL.md`, `references/04-workflow-and-roles.md`,
  `scripts/autonomous/survey.py`, `backlog.py`, `fleet.py`, `evidence.py`.
- V3 docs: `docs/autonomous-improvement-v3-universal-plan.md` (status: architecture plan),
  `docs/autonomous-improvement-v3-remediation-plan.md` (all implemented).
- Live machine config: `chesssrs/scripts/autonomous/config.json` (gitignored) — has the
  new `docs_truth.ignore_spec_pattern` for chess tokens; `.autonomous/` untracked (runtime state).
- Machine health trail: `/home/ubuntu/github-projects/skills/docs/autonomous-improvement-v3-universal-plan.md`,
  `docs/autonomous-improvement-audit-2026-10-21/22.md`, wake log `/home/ubuntu/.local/share/orchestrator/wake.log`.

## Quick way back up

`gh pr view 176 -R cassandre60/skills --json state,mergedAt`, `gh pr view 179 -R ...` — both MERGED.
Survey on chesssrs: `python3 ~/.agents/skills/autonomous-improvement/scripts/autonomous/survey.py --json`.
Backlog: `python3 ~/.agents/skills/autonomous-improvement/scripts/autonomous/backlog.py harvest --json | head -50`.
