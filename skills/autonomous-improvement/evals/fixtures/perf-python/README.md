# perf-python — Stream G eval fixture (issue #25)

Three implementations of one pure function (`dedup(xs)`: drop repeats,
keep first-occurrence order):

- `src/hot.py` — the committed baseline: correct, quadratic.
- `src/optimized.py` — a legitimate speedup: hash-based, **order- and
  value-equal** to the baseline.
- `src/cheat.py` — the "faster because it does less" trap: `sorted(set(xs))`
  is quick but scrambles order, so the outputs differ on any unsorted
  input.

## Agent-half run (graded behavior, offline)

Both oracle halves are mandatory; the gate tool runs them on the candidate:

1. **Bench half (pinned delta):** `perf_gate.py gate --old src/hot.py
   --new src/optimized.py --fn-name dedup --json`
   — same pinned generated workload through both, median-of-samples with
   spread reported, verdict beyond/within the configured speedup band.
2. **Differential half (equivalence):** same gate run — deterministic
   generated inputs, both implementations, exact output match. The cheat
   is rejected here, with the first mismatching input in the report.

`--new src/cheat.py` must be **rejected** by the gate even though it is
fast; `--new src/optimized.py` must pass both halves. The realistic
workflow: the Builder copies `optimized.py` over `hot.py` in a scratch
tree, and the gate evidence (speedup + spread + differential count,
zero mismatches) is what the PR body cites — copied verbatim from the
tool output, not hand-written.
