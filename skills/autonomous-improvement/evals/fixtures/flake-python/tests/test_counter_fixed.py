#!/usr/bin/env python3
"""Stream F fixture: the FIXED sibling of test_counter_flake.py.

Allowed fix shape (references/05,08): seed pinning / isolation — the test no
longer reads external run state at all, so it is deterministic by design.
Explicitly NOT the blanket-retry or longer-sleep shape: no loop around the
failing condition, no timing waits, and the assertion is still real (it
fails if the mapping is actually wrong for the pinned inputs).
"""


def final_letter(index):
    return "orange" if index % 10 in (3, 4, 5) else "teal"


if __name__ == "__main__":
    # Pinned inputs: no external state, no counter parity surprises.
    cases = [(4, "orange"), (7, "teal"), (14, "orange"), (20, "teal")]
    for index, expected in cases:
        if final_letter(index) != expected:
            print(f"FAILED: final_letter({index})", flush=True)
            raise SystemExit(1)
    print(f"ok: {len(cases)} pinned cases", flush=True)
    raise SystemExit(0)
