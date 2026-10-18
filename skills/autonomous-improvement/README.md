# autonomous-improvement

Spend tokens on mechanically falsifiable work — not on prose, renames, or checkbox tests.

Entry point: `SKILL.md`. References are split out so a model loads one concern when it reaches that step.

## Install

From the consolidated skills repo:

```sh
npx skills@latest add cassandre60/skills
```

Or copy this folder into wherever your agent reads skills from (folder name must stay `autonomous-improvement`):

- opencode, all projects:     `~/.config/opencode/skills/autonomous-improvement/`
- opencode, one project:      `<repo>/.opencode/skills/autonomous-improvement/`
- Claude Code, all projects:  `~/.claude/skills/autonomous-improvement/`
- Claude Code, one project:   `<repo>/.claude/skills/autonomous-improvement/`
  (opencode also reads the `.claude/skills` locations)
- agents (generic):           `~/.agents/skills/autonomous-improvement/` or `<repo>/.agents/skills/`

## Use

Say what you want in plain words; the description triggers on:

- "improve the repo", "audit and fix bugs", "run autonomous improvements"
- "find performance issues", "increase test coverage meaningfully"
- "mutation testing", "test gaps", "flaky tests"

Or invoke explicitly:

- "Use the autonomous-improvement skill to run Stream A against src/auth/"
- "Use the autonomous-improvement skill — Stream B audit of test/payment/"
- "Improve the repo — autonomous-improvement, Streams A+B"

## What it enforces

Four laws: fail-to-pass, content-hash dedup, worktree isolation, machine budget. Two active streams (mutation testing + adversarial test audit) on day one, two planned streams (property fuzzing + migration archeology) when the harnesses exist. No cosmetic churn, no vacuous tests, no wide local runs.

## Relationship to agent-proof-codebase

`agent-proof-codebase` builds the gates that make fake work fail. This skill spends tokens finding the real work — surviving mutants, vacuous tests, flaky sleeps, migration gaps. Use that skill first if the repo has no gates yet.

## Layout

```
SKILL.md            laws, streams, quick start, workflow, references index
references/         4 deep dives, read on demand
scripts/            3 building blocks (Python stdlib only): mutation_engine, ledger, orchestrator
assets/             ledger template + protected-paths fragment
```

## Why this skill exists

Agents given an open-ended "improve this codebase" prompt either stall in an hour (nothing is broken on the surface) or degenerate into fake work (renames, vacuous tests, scope creep). This skill channels tokens into mechanically falsifiable loops — a surviving mutant or a vacuous test is objective evidence that work remains, and fail-to-pass proof ensures each PR earned its keep.

## Notes

- Scripts need only Python 3 and git (plus bash for fail-to-pass helpers in the companion skill).
- The mutation engine is regex-based, not AST-based — every survivor needs human triage.
- Released under the [MIT License](LICENSE).
