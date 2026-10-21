# defect-python — Stream E eval fixture (issue #22)

Seeded one-bug python repo for the defect-repair stream:

- `src/calculator.py`: `median` mishandles even-length input
  (`sorted(values)[n // 2]` instead of averaging the middle pair).
- `docs/bugreports/issue-42.md`: reproducible report (names Expected/Actual).
- `docs/bugreports/issue-99.md`: deliberately unreproducible report — the
  intake must bounce it with the missing facts and change zero files.

## Agent-half run (graded behavior, offline)

1. **Intake (0-token):**
   `python3 scripts/autonomous/repair.py intake --reports docs/bugreports`
   — issue-42 becomes a candidate (expected vs actual named); issue-99 is
   bounced with the missing-facts comment.
2. **Breaker:** write `tests/test_repro_median.py` asserting
   `median([1, 2, 3, 4]) == 2.5`, commit nothing but the test, then:
   `repair.py verify-red --test-script tests/test_repro_median.py
   --evidence .autonomous/repair-evidence.json --base-ref <seed sha>`
   — records base sha + red proof; refuses any non-test diff.
3. **Builder:** minimal fix to `src/calculator.py` (average the middle
   pair), then:
   `repair.py verify-green --test-script tests/test_repro_median.py
   --evidence .autonomous/repair-evidence.json --spot-check 2`
   — repro green, ≤ `--max-fix-files` fix files, mutation spot-check on the
   touched lines via the engine's `--diff-base` requires 0 survivors.
4. **Ledger:** row under stream E with the evidence file attached.

The bounces never produce a diff; candidates the reproducer cannot turn into
a failing test are returned to the reporter with the missing facts named.
