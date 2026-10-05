# CI design and agent-facing feedback

Contents: tiers, ordering and cost, local parity, flakiness, feedback format, attempt budgets, gate health metrics.

Skeleton: `assets/github-actions.gates.example.yml`. Registry: `assets/GATES.template.md`.

If an agent may need a hundred attempts, feedback must be cheap, fast and actionable, or the loop will not converge
or will burn money.

## Tiers
| Tier | When | Target time | Contents |
|------|------|-------------|----------|
| T0 | pre-commit / local save | seconds | format, lint, strict type check, banned APIs, quick tests of touched modules |
| T1 | every PR, blocking | under ~5 min | full unit + property tests (moderate examples, logged seeds), architecture rules, spec traceability, protected paths, tripwire, ratchets that are cheap |
| T2 | every PR, blocking, after T1 | under ~30 min | diff-scoped mutation, fail-to-pass for bug fixes, perf harness on pinned runner, migration tests, longer property runs |
| T3 | nightly / scheduled | hours | full mutation, long fuzzing, deterministic simulation soak, crash-consistency matrix, dependency and secret audits, red-team recall, device/OS matrix |
| T4 | release candidate | as needed | real-device runs, upgrade-from-every-shipped-version, store/platform checks, manual checklist, performance on reference devices |

Ordering: cheapest and highest-rejection gates first; fail fast; do not spend T2 minutes on a PR that fails T1.
Use caching, parallelism and test selection by changed paths, but keep a full run on a schedule so selection
bugs cannot hide failures.

## Local parity
One command runs the same gates locally: `./scripts/gates.sh [t0|t1|t2]` wrapping the project's real tools and the
scripts in `scripts/`. The agent runs it before pushing. CI must call the same entry point so there is exactly one
definition of each gate.

## Flakiness is poison
A flaky gate teaches agents to retry until green and teaches humans to ignore red.
- Detect: rerun suspicious failures N times on a branch; track pass-rate per test.
- Quarantine: move to a quarantine list with owner and expiry date; quarantined tests still run and report;
  expired quarantine fails the build.
- Never auto-retry to green silently; if retries exist they are counted and reported in the summary.
- Eliminate causes: inject time, seed randomness, isolate state, remove sleeps, bound concurrency with a
  deterministic executor.

## Feedback format (what a failing gate must print)
Aim for short, structured, reproducible output. For each failure:
1. **Gate and invariant:** `G09 fail-to-pass` / `INV-012 review applied exactly once`.
2. **Minimal counterexample:** shrunk input or command sequence, or the surviving mutant as a diff.
3. **Reproduce:** the exact command and seed, for example `./scripts/gates.sh t1 --seed 8841723`.
4. **Category of fix:** one line (for example "state is applied twice on retry; make the write idempotent").
5. **What not to do:** "do not edit the test or the baseline; fix the code."
Also emit a machine-readable summary (JSON) so the agent harness and dashboards can parse results. Truncate
logs to the first failing assertion plus context; huge logs waste an agent's context.

Example:
```
FAIL G06 mutation (diff-scoped)  src/core/schedule.dart:88
  Surviving mutant: `if (interval >= max)` -> `if (interval > max)`   (boundary)
  Related invariant: INV-011 intervals never exceed the configured maximum
  Add a test exercising interval == max. Reproduce: ./scripts/gates.sh t2 --module core --lines 80-95
  Do not exclude this line or mark the mutant equivalent without a written argument.
```

## Attempt budgets and escalation
- Cap attempts per PR per gate (for example 3 failed tries on the same gate, then stop and report findings).
- Escalate to a stronger model or a human with the failure history rather than looping.
- Back off on identical failures; require the next attempt to differ materially (new hypothesis).
- Track cost: CI minutes and agent tokens per accepted PR.

## Gate health metrics (review quarterly, record in GATES.md)
Rejection rate per gate; false-positive rate; flaky rerun rate; median time to green; escapes per quarter and the
gate added for each; red-team recall; mutation score trend; time per tier. A gate with zero rejections and zero
red-team catches over a long period is a candidate for deletion; a gate with many false positives needs fixing.

## Cost control
Gate runtime is a budget too: set per-tier time ceilings and ratchet them. Heavy gates go to scheduled tiers,
with PR-time versions that are diff-scoped. Spend compute where risk tiers say it pays.
