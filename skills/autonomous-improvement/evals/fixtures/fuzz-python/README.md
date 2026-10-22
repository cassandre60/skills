# fuzz-python — Stream C eval fixture (issue #25)

A seeded idempotence bug + a repo-supplied property harness:

- `src/text_ops.py` — `normalize` lowercases only the FIRST 'A'
  (`text.replace("A", "a", 1)`), which breaks the harness property
  `normalize(normalize(x)) == normalize(x)` for inputs holding two
  uppercase As (`'AA'` → `'aA'` → `'aa'`). Inputs without a doubled 'A'
  pass, which is why the shrink matters: the recorded seed is a long
  sentence, the minimal counterexample is `'AA'`. The Builder fix is
  plain `text.lower()`.
- `tests/property.py` — the repo-owned harness: reads the candidate
  input on stdin, exits 1 with a one-line violation when the property
  breaks.
- `corpus/seeds.txt` — recorded seeds (the deterministic, non-coverage
  tier). The first failing seed is shrunk by the tool and retained under
  `corpus/regressions/` forever (a retained corpus entry is a regression
  test, not a log line).

## Agent-half run (graded behavior, offline)

1. `python3 scripts/autonomous/fuzz_shrink.py run --harness tests/property.py --corpus corpus/seeds.txt --json`
   — finds the failing seed, shrinks it deterministically (byte-for-byte
   identical across runs), writes `corpus/regressions/CE-<sha8>.txt`.
2. `fuzz_shrink.py replay --harness tests/property.py --counterexample <file> --json`
   — the red/green oracle: red before the fix, green after.
3. The retained counterexample stays in the corpus and must STILL replay
   green after the fix (a regression seed, kept, not deleted with the
   bug).

The `run` command creates `corpus/regressions/`: that write is the
Finder's artifact (deliberately persistent repo content), unlike the
zero-diff scans of Stream H — the deliverable here is the retained seed.
