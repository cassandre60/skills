# Ratchets and budgets

Contents: ratchets vs budgets, metric catalogue, baseline mechanics, performance measurement method, synthetic
data, memory/size/dependency budgets, noise handling.

Script: `scripts/ratchet_check.py`. Baseline: `assets/ratchet-baseline.example.json`.

## Ratchets vs budgets
- **Ratchet:** relative to the committed baseline; may only improve. Good for quality metrics where today's value
  is acceptable and regression is not (mutation score, warnings, suppressions, LOC, dependencies).
- **Budget:** an absolute requirement from the product (startup under N ms, memory under M MB, binary under S MB).
  Enforce both: the budget is the ceiling, the ratchet stops slow drift below it.

## Metric catalogue (choose what matters; start small)
Quality: branch coverage on R0/R1 modules; mutation score per module; analyzer warnings (target 0); lint/type
suppressions count; skipped/quarantined tests count (with expiry); flaky-test count; TODO/FIXME in R0 code.
Size and complexity: core LOC; max function/file length; cyclomatic complexity hot-spots; direct dependency count.
Performance: cold start; operation latency at p50/p95/p99 on seeded datasets at several scales; frame times for
UI; throughput of import/hash/queue-build; allocation counts.
Resources: peak memory; artifact/binary size; on-disk bytes per record; battery or CPU proxies where measurable.

## Baseline mechanics
1. A job produces `metrics.json` (flat: name to number) from real measurements on every PR.
2. `ratchet_check.py --baseline .gates/ratchet-baseline.json --current metrics.json` fails on any regression
   beyond the metric's tolerance and on any metric that disappeared (removing a metric is a regression).
3. Tolerances absorb measurement noise only; keep them tight and justified. Deterministic metrics get zero.
4. Improvements tighten the baseline: either `--update` in a trusted post-merge job that commits the new
   baseline, or manually in a PR. The script never loosens.
5. Loosening means editing the baseline by hand in a gate-change PR with human approval and a written reason.
6. The baseline file is a protected path.

## Performance measurement method
- Build in the mode users get (release/profile), not debug.
- Pin the environment: same runner class or device model, fixed OS image, no other load. Record environment
  metadata with every result.
- Warm up, then run many iterations; report median and spread (MAD or percentiles), not a single run.
- Compare against baseline using a threshold above measured noise, or a nonparametric test across repeats.
- Prefer deterministic proxies in PR CI where possible (allocation counts, bytes, instructions, rows scanned,
  queries issued) and wall-clock on a stable benchmark runner or a real device farm nightly.
- Measure end-to-end user-visible operations plus micro-benchmarks for hot paths; optimize only what the budget
  or profiling says matters. Every optimization ships with before/after numbers and an equivalence test.
- For UI frame budgets, assert on frame-timing summaries from scripted flows on a reference device class.

## Synthetic datasets
Generate seeded datasets at multiple scales (for example 1k, 100k, 1M records) with realistic skew and
adversarial shapes (deep chains, many duplicates, worst-case keys). Commit generators, not data. Benchmark
complexity growth: if 10x data costs 100x time, you have an algorithmic problem regardless of the absolute number.

## Memory, size and dependency budgets
- Track peak RSS in a scripted scenario; investigate allocations per operation in hot loops.
- Track artifact size by component; tree-shake, split debug symbols, remove unused assets/fonts/native libs.
- Dependency budget: a `DEPENDENCIES.md` with a reason per direct dependency; CI fails when the lockfile adds
  a direct dependency without an entry. Each native dependency also costs startup and size.

## Handling noise without hiding regressions
Re-run failing benchmarks a fixed number of times; require consistent failure to reject, but log every rerun
and never retry until green silently. Investigate flaky benchmarks as bugs in the harness.
