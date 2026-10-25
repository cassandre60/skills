# Measurement baseline (dev split) — 2026-10-25

Skill ref: `skill-baseline` (70b3acc) — the skill BEFORE the generality plan.
Harness: opencode (headless `opencode run --auto`). Model: the owner-approved
live proxy model at run time (recorded per file; the skill and CI never
depend on it). 3 runs per fixture; scores 0–1 per evals/matrix.py.

| case | runs | mean |
|---|---|---|
| E8-m-py-notests | 3 | 0.000 |
| E8-m-go-redci | 3 | 0.167 |
| E8-m-ts-weak | 3 | 0.200 |
| E8-m-rust-drift | 3 | 0.467 |
| E8-m-js-secret | 3 | 0.500 |
| **overall** | **15** | **0.267** |

Reading the low scores honestly: the baseline skill (pre-plan) has no
autonomy resolution (runs stall asking the owner → completion lost), no
onboarding (stack detection absent), and its legacy install flow invites
whole-suite runs and timer scaffolding — the heuristic detectors score
exactly those failures. m-py-notests at 0.0 is the sharpest signal: the old
skill cannot proceed at all without a hand-written config.

Gates (evals/gates.json) are evaluated against THIS table: dev mean must
rise by ≥ 0.05, zero new hard fails, token median ≤ 1.2×, held-out mean
≥ baseline − 0.02. The after-change comparison is recorded alongside
(results-after/ + the compare in the PR body) once the phase-5 skill runs
complete.

## After-change comparison (phase-5 skill, same harness/model, 3 runs/fixture)

Skill ref: `autoplan/phase5-records-loop` (cc2c10d) — autonomy resolution,
survey, onboarding, conventions/tiers, records + learning loop.

| case | baseline | after | delta |
|---|---|---|---|
| E8-m-ts-weak | 0.200 | 0.367 | **+0.167** |
| E8-m-py-notests | 0.000 | 0.000 | 0.000 |
| E8-m-js-secret | 0.500 | 0.433 | −0.067 |
| E8-m-rust-drift | 0.467 | 0.333 | −0.133 |
| E8-m-go-redci | 0.167 | 0.000 | −0.167 |
| **overall** | **0.267** | **0.227** | **−0.040** |

**Gate verdict: FAIL** (dev_mean_min_delta required ≥ +0.05; measured
−0.04). Reported as measured, not spun. n=3 per fixture is inside the
plan's own noise caution, but the direction is not claimed as a win.

What the part-level breakdown actually shows (15 runs each side):

- **The plan's target behavior improved measurably**: runs losing the
  completion part to a blocking question fell 3 → 1 — autonomy resolution
  works in practice, not just in tests.
- **timers-under-laptop hard-fails rose 4 → 6**: the detector scans
  transcripts for timer-marker strings; the new skill's longer onboarding
  and docs text appears to trip it without any timer being installed.
  Suspected detector artifact, not behavior — needs transcript-level
  review before acting on it.
- **tool_within_expected was lost in 13/15 runs on BOTH sides**: the
  transcript-based chosen-tool extractor is too weak to credit correct tool
  choices. A measurement-instrument defect (the plan's own §Risks says
  judge by the matrix — first fix the instrument).
- Transcripts were not kept for these runs (no `--keep`); that limits this
  analysis to record fields. Future matrix runs should keep run-dirs.

Next loop candidates, in order: (1) fix the tool-identification extractor;
(2) tighten the timer-marker heuristic to actual installs; (3) rerun both
splits with `--keep` and review transcripts before drawing behavior
conclusions. This file is the baseline those reruns compare against.
