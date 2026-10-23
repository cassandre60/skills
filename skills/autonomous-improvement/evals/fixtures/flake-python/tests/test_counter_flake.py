#!/usr/bin/env python3
"""Deterministic pseudo-flake, seeded by a run counter (Stream F fixture).

The failure is a stand-in for a real timing/external-state race: a fixture
"product" function returns a value that depends on how many times the suite
has run, while the assertion pins one literal expectation. Failure count is
exact — 3 of every 10 runs — with no wall clock, so CI itself stays stable.

The red-half story: ``flake finder``/``oracle`` must flag this; the fixed
sibling (test_counter_fixed.py) pins its own inputs instead of reading
external state, so the oracle demands 0/N there.
"""
import pathlib


def final_letter(index):
    return "orange" if index % 10 in (3, 4, 5) else "teal"


def run():
    state = pathlib.Path(__file__).resolve().parent / "state"
    state.mkdir(exist_ok=True)
    counter = state / "run-counter"
    count = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(count))
    return count, final_letter(count)


if __name__ == "__main__":
    count, actual = run()
    fixture_bug_expected = "teal"  # ignores the parity bucket that final_letter reads
    print(f"run #{count}: expects {fixture_bug_expected}, actual {actual}", flush=True)
    if actual != fixture_bug_expected:
        print("FAILED: assertion mismatch (seeded)", flush=True)
        raise SystemExit(1)
    raise SystemExit(0)
