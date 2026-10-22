"""Regression corpus entries are retained forever once found.

seeds.txt is the recorded corpus (stream C, T1-tier seeding: recorded
seeds, no hidden generation state). The first counterexample found is
shrunk deterministically; the shrunk input is written under
corpus/regressions/ as a permanent regression seed (CE-<sha8>.txt) and
replayed against the harness by the oracle.
"""

