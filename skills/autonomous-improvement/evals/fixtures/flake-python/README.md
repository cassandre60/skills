# Stream F fixture: deterministic pseudo-flake

`tests/test_counter_flake.py` fails for exactly 3 of every 10 runs, seeded by
`state/run-counter` (no wall clock, so CI timing stays stable); its failure
rate under the oracle is exactly 0.3 over any 10-run window. The FIXED
sibling, `test_counter_fixed.py`, pins its own inputs (the allowed
deterministic fix shape) and the oracle demands 0/N there.

`history.json` is the mining fixture: the flaky test shows both outcomes
across re-runs at the same shas; every other test stays green, so the finder
must emit the one candidate and nothing else. Use with
`python3 scripts/autonomous/flakes.py finder --history <repo>/history.json`.
