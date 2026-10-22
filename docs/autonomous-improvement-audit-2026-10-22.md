# Session audit — 2026-10-21/22, ChessSRS fleet

One overnight session of this skill run as an orchestrator over
`cassandre60/ChessSRS`: ~12 h, **40 audit rounds merged, 6 releases, 13 real
production defects found and fixed**, 2 skill PRs merged mid-session.

Every item below is anchored to something that happened in that session, with
the measurement that produced it. The session's own rule — merged as skill PR
#43 during the session — is the frame for most of it: *an instrument that
reports false negatives must be fixed before its output is trusted; a green run
of a broken instrument is worse than a red one, because it is trusted.*

The session found that shape **three more times, inside the skill's own
tooling**. This document is the evidence behind issues #45–#51.

---

## 1. The instruments lied, and the lies were silent and green

### 1.1 The queue tool reported an empty queue while 79 targets waited

`scripts/autonomous/ledger.py next --stream A` printed *"No eligible targets
remain for this stream at their current hashes."* Two silent causes:

- `candidate_paths()` returns `[]` when `candidates.<stream>.include` is empty.
  The repo's `scripts/autonomous/config.json` is **gitignored by design**
  (`.gitignore`: *"The fleet config is project-local"*), so a fresh checkout has
  none and the tooling runs on built-in defaults, where that list is empty.
- With no config, the Dart mapping guesses
  `test/<path-with-lib/-stripped>_test.dart`, so `lib/src/view/analysis/analysis_screen.dart`
  maps to `test/src/view/analysis/analysis_screen_test.dart` — a path this
  repo's test tree does not have. The tool found an existing companion for
  **0 of 329** lib files; the real convention (`lib/src/<rest>.dart` ↔
  `test/<rest>_test.dart`) has one for 119.

Measured: queue **0 → 79** once the project-local config exists. The fleet's
work list for the whole session came from a hand-written script, not from the
tool meant to produce it. → **issue #46**

### 1.2 The ledger was invisible to the dedup law that reads it

The ledger showed 85 audits. The tool's own parser saw **27**, and **34 of the
48** lib files it names would have been re-audited — including files audited
*and fixed* hours earlier. Three defects, all invisible:

1. **54 rows carried a decorated status cell** (`COMPLETED (second round: 98
   candidates, 6 of 7 open items closed)`). The parser accepts only a cell that
   is exactly `COMPLETED` or `SOUND`; every other cell is skipped.
2. **Six files had their bugfix row below their round row**, and the round row
   recorded the *pre-change* blob. The authoritative row is the greatest
   `(Date, -position)`, so the stale hash won.
3. **One row named two files in the target cell and two hashes in the hash
   cell.** The model is one target with an optional companion, so that lib file
   was protected by a row that could never match it. → **issue #45**

### 1.3 A shared test harness measured text twice as wide as production

The shared widget-test scope built a `MaterialApp` with **no `theme:`** and ran
on the engine's test fonts, so every string measured ~2× wide. 270 reported
"layout overflow" occurrences across 16 files; **22 of the 24** triaged sites
were artefacts of the harness, and two candidate production defects disappeared
once the fonts and theme were right. Fixing the harness took the measured
occurrences **268 → 78 with zero production sites** — and then exposed a real
defect the noise had hidden. → **issue #47**

---

## 2. The measurement contract was weaker than the evidence it produced

- **The kill criterion named one test file; the oracle lives in several.** The
  repo's own AGENTS.md: *"A mutation sweep's `--test-cmd` must name every test
  file that owns the audited invariant's oracle."* The fleet's own measurement:
  the mapped file alone killed 23 of 54 killable mutants; adding the sibling
  `stockfish_nnue_service_test.dart` killed 35 of 54. → **issue #48**
- **The engine's blind shapes need a deliberate supplement.** About a third of
  the session's pinned gaps came from mutants the regex engine cannot express:
  a plain field copy, a returned expression, a dropped argument, a swapped
  branch, a `??` fallback, a changed literal, a different separator, a dropped
  `&&` operand. → **issue #48**
- **Two engine flags silently changed the number.** `--timeout-counts-as`
  defaults to counting a hang as a kill (one round had 3 hangs among 98
  candidates); `--max-mutants` samples silently (one round lost two
  candidates). → **issue #48**

---

## 3. The worker lifecycle had three failure classes, and two were mishandled

- **"Endpoint is unavailable" is an outage, not a dead pool.** Two tabs died
  simultaneously with it; the model had been healthy minutes earlier. The
  skill's taxonomy is quota-only, and rotating or parking a pool on an outage
  is wrong. → **issue #49**
- **Three tabs died silently after long briefs** (150+ lines of context-dense
  instructions); every tab that survived got a shorter one; the pool was
  healthy throughout. → **issue #49**
- **"Commit locally" did not save untracked work.** A dead tab left 46 KB of
  new tests, untracked, in its worktree — in no commit, so the checkpoint did
  not cover it. → **issue #49**
- **Three workers corrected the brief's premise, and each correction saved a
  round**: a file already audited at the same blob (the ledger pre-check), a
  file that is not what the brief said it was, and a file with no test file at
  all. → **issue #49**

---

## 4. Git, protection and merge churn cost more than any single defect

- **Strict protection + squash merge: a PR must contain main's tip.** Six
  green PRs became unmergeable with `2 of 2 required status checks are
  expected`; `--admin` did not bypass it. → **issue #50**
- **The skill contradicts itself**: `references/07` says rebase, `references/04`
  says never force-push — and a pushed branch cannot be rebased without one.
  The session merged `main` into the branch six times instead. → **issue #50**
- **Batching the fleet's own deliveries** (one PR, one commit per change) beat
  three separate merges: one CI cycle instead of three, one protected-path
  decision instead of three. → **issue #50**
- **A branch created inside an existing worktree inherits that worktree's
  commits**, so a docs PR picked up an unrelated protected-path change and
  failed a gate for a file it never meant to touch. → **issue #50**
- **The primary checkout was stranded on a stale branch** whose content had
  been squash-merged — 43 files behind, invisible from the PR list, and the
  owner's acceptance runs against that checkout. → **issue #50**

---

## 5. Owner interaction

- **Consent is recorded per item, on the PR, with the withdrawal path**: what
  is being accepted, on whose authority, and how to withdraw it. → **issue #51**
- **`gh pr edit` and `gh issue view` can fail with a Projects-classic GraphQL
  error**; the working path is the REST API. → **issue #51**

---

## 6. What the skill already covered

The content-hash dedup mechanism, the "forensics before reports" rule, the
batched-ledger-rows workflow, the CI green loop's "green on the current head
sha", the unpinned-dispatch and never-park rules, and the "a failed read is not
a deletion" rule all worked as designed. The failures were not in the
mechanisms; they were in **the contract the tools enforce not being written
down**, so a reasonable actor produced output the tooling silently ignored.

A green answer from a mis-specified tool is indistinguishable from a green
answer from a correct one — and that is the failure mode that hides.
