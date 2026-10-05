#!/usr/bin/env python3
"""Monotone ratchet checker.

Compares a flat metrics JSON (current run) against a committed baseline and fails
if any metric got worse. It can TIGHTEN the baseline when metrics improve, but it
never loosens it: loosening means hand-editing the baseline file, which must live
in a protected path that needs human review.

Baseline format (see assets/ratchet-baseline.example.json):
  {"metrics": {"mutation_score": {"value": 0.82, "direction": "higher_is_better",
                                   "tolerance": 0.0}, ...}}
Current format: {"mutation_score": 0.84, "p95_review_ms": 11.2, ...}

Exit codes: 0 ok, 1 regression or missing metric, 2 usage/format error.
"""
import argparse
import json
import sys

HIGHER = "higher_is_better"
LOWER = "lower_is_better"


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--current", required=True)
    ap.add_argument("--update", action="store_true",
                    help="write improved values back into the baseline (tighten only)")
    args = ap.parse_args()

    baseline = load(args.baseline)
    current = load(args.current)
    metrics = baseline.get("metrics")
    if not isinstance(metrics, dict):
        print("error: baseline has no 'metrics' object", file=sys.stderr)
        return 2

    failures, improved = [], []
    for name, spec in sorted(metrics.items()):
        direction = spec.get("direction")
        if direction not in (HIGHER, LOWER):
            print(f"error: metric {name!r} has invalid direction {direction!r}", file=sys.stderr)
            return 2
        base = spec["value"]
        tol = spec.get("tolerance", 0.0)
        if name not in current:
            failures.append(f"{name}: missing from current metrics (a removed metric is a regression)")
            continue
        cur = current[name]
        worse = cur < base - tol if direction == HIGHER else cur > base + tol
        better = cur > base if direction == HIGHER else cur < base
        if worse:
            failures.append(f"{name}: {cur} is worse than baseline {base} (tolerance {tol}, {direction})")
        elif better:
            improved.append((name, base, cur))

    for name in sorted(set(current) - set(metrics)):
        print(f"note: metric {name!r} is not in the baseline yet (add it to start ratcheting)")

    for name, base, cur in improved:
        print(f"improved: {name}: {base} -> {cur}")

    if failures:
        print("\nRATCHET FAILED:")
        for line in failures:
            print(f"  - {line}")
        print("Fix the regression. Do not edit the baseline to make this pass.")
        return 1

    if args.update and improved:
        for name, _base, cur in improved:
            metrics[name]["value"] = cur
        with open(args.baseline, "w", encoding="utf-8") as fh:
            json.dump(baseline, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"baseline tightened for {len(improved)} metric(s)")
    print("ratchet ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
