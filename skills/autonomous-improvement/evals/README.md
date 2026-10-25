# Behavioral evals

`evals.json` (version 2) holds behavior checks for an agent using this skill:
the five original cases (weak test, vacuous test, dedup, Stream C refusal,
open-PR collision) plus four orchestrator cases added by the eval-harness work
(quota rotation, red CI, protected ledger row, cold start). Each case names a
fixture under `fixtures/`, a prompt, setup commands, expected behavior, and
forbidden behavior. Use `<skill>` for this folder and `<repo>` for the
temporary fixture copy. The setup commands are written for `bash`; run them
from inside `<repo>` unless the command says otherwise.

## Two layers of verification

1. **Deterministic half** — `evals/run.py` runs each scripted check against a
   fresh temporary copy of the fixture (setup commands with `<skill>`/`<repo>`
   substituted, environment from the case). No agent involved; fast enough to
   run as a CI step; it also *proves it can fail* via `--negative-control`.
   Cases without scripted checks report as `agent-only`.

   ```sh
   python3 evals/run.py --deterministic                      # all cases
   python3 evals/run.py --deterministic --case E7-7-red-ci   # one case
   python3 evals/run.py --negative-control                   # prove it can fail
   python3 evals/run.py --record-file /tmp/results.json      # JSON summary
   ```

   The deterministic checks live in `CASE_CHECKS` at the top of `run.py`; a
   case is agent-graded when it has no entry there.

2. **Agent half** — full agent evals run on demand on a real model:

   ```sh
   python3 evals/run.py --plan-agent E7-7-red-ci --harness opencode --model <model>
   python3 evals/run.py --plan-agent trigger-set  --harness opencode --model <model>
   ```

   `--plan-agent <case>` prints the exact headless protocol: case **A** with
   the skill installed, case **B** without it (same setup, repo, and prompt —
   B proves the behavior is skill-borne, not the base model's default), graded
   against the case's `expected`/`forbidden` lists. `--plan-agent trigger-set`
   prints the description triggering set (below).

Executed agent runs are recorded under `evals/results/` (see "Recorded runs")
so any future skill or model change can be diffed against a real baseline —
never guessed.

## Trigger set

The skill's *selection* behavior is as important as its behavior after
selection. Four should-trigger prompts (distrust of green tests, test audit,
"run an improvement round", unattended fleet setup) and two should-not-trigger
prompts (feature work) are defined in `run.py:` TRIGGER_PROMPTS`. Rate them
with **3 runs per prompt** on each harness/model pair you report: a prompt
counts as triggered when the model picks this skill from the available skill
list for that prompt. Should-not-trigger rates over ~15% mean the description
is over-selecting and is bleeding into feature work; should-trigger rates under
~85% mean real cases go unpicked. Commit the resulting table with the current
description's rates *before* any description edit, so description changes are
measured, not guessed.

## Grading agent halves

Grading is a transcript review of the agent sessions:

- Every `expected` item should be observable, not merely asserted by the agent.
- Any `forbidden` item is a failure even if the final diff looks small.
- The skill's own rule applies: a green test that also passes with the mutant
  applied is not evidence.

The Python fixtures use plain pytest-style test functions. Install `pytest` in
the eval environment or translate `test_cmd` to the runner you use; the point
of the eval is agent behavior, not the runner.

## Recorded runs

Agent runs are recorded as one JSON file per (case, harness, model) pair:

```json
{
  "case": "E7-7-red-ci",
  "harness": "opencode",
  "model": "opencode/<model>",
  "with_skill": true,
  "transcript": "<paste of the headless run>",
  "observed": ["pr-state-classified-red-in-diff", ...],
  "forbidden_seen": [],
  "verdict": "pass"
}
```

Deterministic harness greens are checkpointing the *script layer*; the agent
halves are the real evals. A failing agent run with an analysis in `verdict`
is an acceptable first landing; an unrun eval is not.

## Measurement matrix (matrix.py)

`matrix.json` defines the fixture matrix (dev split gates changes; held-out
split is never shown to the improver except as aggregate verdicts). Each
fixture plants one known condition; `matrix.py` runs the skill against a
fixture copy and scores 0–1: 0.3 completion without a blocking question,
0.3 chosen tool within `expected_tools`, 0.2 complete evidence, 0.2 scope
within the diff budget. Any forbidden item is a hard fail (0). Tokens and
wall time are reported beside the score, never inside it. Question and tool
detection are heuristics — transcripts are kept for human review.

```sh
python3 evals/matrix.py plan --split dev
python3 evals/matrix.py run --split dev --skill-ref skill-baseline \
    --harness opencode --model <model> --runs 3
python3 evals/matrix.py grade --run-dir <dir>        # offline
python3 evals/matrix.py report [--json]
python3 evals/matrix.py compare baseline.json next.json
python3 evals/matrix.py --negative-control
```

Fixtures whose `toolcheck` tools are missing are skipped and reported as
skipped, never failed. `run` pins the skill at `--skill-ref` (git ref or a
skill directory) by extracting it into the fixture copy's
`.opencode/skills/`, shadowing the global install. A run-dir holds
`meta.json`, `transcript.txt`, `diff.patch`, and `artifacts.json` (prs,
issues, chosen_tool, questions_asked, final_message, evidence, tokens,
wall_s, package_named); grade is a pure function over it.

Records land in `evals/results/<case>__<harness>__<model>__<stamp>.json`
(schema `matrix-record/1`: the recorded-run fields above plus `skill_ref`,
`fixture`, `score_parts`). Transcripts are never committed. The baseline
table lives in `evals/results/BASELINE.md`. The owner can add real
repositories to the held-out set via an untracked `evals/heldout.local.json`
(`{"fixtures": [{"id", "path", "stack", "planted", "expected_tools"}]}`).
`evals/gates.json` holds the owner-only thresholds a change must clear
(dev mean delta, new hard fails, token ratio, held-out floor).

