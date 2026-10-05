#!/usr/bin/env python3
"""Check that every invariant in SPEC.md is referenced by at least one test (and vice versa).

Convention: invariants are written as IDs like INV-001 in the spec (a heading or a bullet).
Tests mention the ID in a test name, a tag, or a comment, e.g.  test('INV-012 review applied once').

Usage: spec_trace_check.py --spec SPEC.md --tests test [--tests integration_test ...] [--allow-uncovered INV-003,INV-007]
Exit codes: 0 ok, 1 uncovered invariants or tests citing unknown IDs, 2 usage error.
"""
import argparse
import os
import re
import sys

ID = re.compile(r"\bINV-\d{3,}\b")


def ids_in(path):
    with open(path, encoding="utf-8", errors="ignore") as fh:
        return set(ID.findall(fh.read()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--tests", action="append", required=True)
    ap.add_argument("--allow-uncovered", default="")
    args = ap.parse_args()

    if not os.path.isfile(args.spec):
        print(f"error: no spec at {args.spec}", file=sys.stderr)
        return 2
    spec_ids = ids_in(args.spec)
    cited = set()
    for root in args.tests:
        for dp, _dns, fns in os.walk(root):
            for fn in fns:
                cited |= ids_in(os.path.join(dp, fn))
    allowed = {x.strip() for x in args.allow_uncovered.split(",") if x.strip()}
    uncovered = sorted(spec_ids - cited - allowed)
    unknown = sorted(cited - spec_ids)
    print(f"{len(spec_ids)} invariants in spec, {len(cited & spec_ids)} referenced by tests")
    if uncovered:
        print("Invariants with no test:", ", ".join(uncovered))
    if unknown:
        print("Tests cite IDs missing from the spec:", ", ".join(unknown))
    return 1 if (uncovered or unknown) else 0


if __name__ == "__main__":
    sys.exit(main())
