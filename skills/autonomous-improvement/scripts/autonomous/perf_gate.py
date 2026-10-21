#!/usr/bin/env python3
"""Stream G: performance gate with two mandatory halves.

references/08-recursive-self-improvement.md, stream G: a faster
implementation only becomes a change the fleet can keep when

1. **bench half** — the pinned generated workload runs through the old and
   the new implementation; the spread of the per-sample times is reported
   and the median speedup must exceed the configured band
   (``--min-speedup``), and
2. **differential half** — a deterministic set of generated inputs is run
   through both implementations and the outputs must match exactly.

"Faster because it does less" is mechanically rejected by half 2 (the
fixture's ``cheat.py`` drops the first-occurrence-order contract and the
gate prints the first mismatching input). Both halves always run; rc 0
requires both verdicts green.

Usage:
  python3 scripts/autonomous/perf_gate.py gate --old src/hot.py \
      --new src/optimized.py --fn-name dedup --json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

from common import EXIT_OK, Fatal, eprint

SCHEMA = "perf-gate/1"


def load_callable(path: Path, fn_name: str) -> Callable[..., Any]:
    if not path.is_file():
        raise Fatal(f"implementation file not found: {path}")
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None  # for type-checkers
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, fn_name, None)
    if not callable(fn):
        raise Fatal(f"{path} has no callable {fn_name!r}")
    return fn


def gen_list(rng: random.Random, size: int, alphabet: int = 32) -> List[int]:
    return [rng.randrange(alphabet) for _ in range(size)]


def bench_half(
    old_fn: Callable[..., Any],
    new_fn: Callable[..., Any],
    bench_size: int,
    samples: int,
    min_speedup: float,
) -> Dict[str, Any]:
    rng = random.Random(2026)
    workload = gen_list(rng, bench_size)
    ratios: List[float] = []
    old_medians: List[float] = []
    new_medians: List[float] = []
    for _ in range(samples):
        start = time.perf_counter()
        old_fn(workload)
        old_elapsed = time.perf_counter() - start
        start = time.perf_counter()
        new_fn(workload)
        new_elapsed = time.perf_counter() - start
        old_medians.append(old_elapsed)
        new_medians.append(new_elapsed)
        if new_elapsed > 0:
            ratios.append(old_elapsed / new_elapsed)
    median_old = statistics.median(old_medians)
    median_new = statistics.median(new_medians)
    speedup = median_old / median_new if median_new > 0 else float("inf")
    median_ratio = statistics.median(ratios) if ratios else 0.0
    return {
        "verdict": "beyond-band" if speedup >= min_speedup else "within-band",
        "speedup": round(speedup, 4),
        "median_old_s": round(median_old, 6),
        "median_new_s": round(median_new, 6),
        "samples": old_medians,
        "spread_old": round(max(old_medians) / min(old_medians), 4) if min(old_medians) > 0 else None,
        "workload_size": bench_size,
        "min_speedup": min_speedup,
        "_median_ratio": median_ratio,
    }


def differential_half(
    old_fn: Callable[..., Any],
    new_fn: Callable[..., Any],
    count: int,
) -> Dict[str, Any]:
    rng = random.Random(1999)
    mismatches: List[Dict[str, Any]] = []
    for index in range(count):
        size = rng.randrange(0, 200)
        xs = gen_list(rng, size, alphabet=9)  # small alphabet: forced duplicates
        out_old = old_fn(xs)
        out_new = new_fn(xs)
        if out_old != out_new:
            mismatches.append({"index": index, "input": xs[:50] if len(xs) <= 50 else xs[:50], "old": out_old[:50], "new": out_new[:50]})
            break
    return {
        "verdict": "equivalent" if not mismatches else "mismatch",
        "inputs": count,
        "mismatch": mismatches[0] if mismatches else None,
    }


def cmd_gate(args: argparse.Namespace) -> int:
    base = Path.cwd()
    old_fn = load_callable(base / args.old, args.fn_name)
    new_fn = load_callable(base / args.new, args.fn_name)
    bench = bench_half(old_fn, new_fn, args.bench_size, args.samples, args.min_speedup)
    differential = differential_half(old_fn, new_fn, args.differential_count)
    bench.pop("_median_ratio")
    passed = bench["verdict"] == "beyond-band" and differential["verdict"] == "equivalent"
    payload = {
        "schema": SCHEMA,
        "tool": "perf_gate.py gate",
        "old": args.old,
        "new": args.new,
        "fn_name": args.fn_name,
        "bench": bench,
        "differential": differential,
        "verdict": "pass" if passed else "rejected",
        "note": "both halves are mandatory: pinned-bench delta AND output equivalence",
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        eprint(f"[gate] bench: {bench['verdict']} speedup={bench['speedup']} (spread old x{bench['spread_old']})")
        eprint(f"[gate] differential: {differential['verdict']} over {differential['inputs']} generated inputs")
        if differential["verdict"] == "mismatch":
            mismatch = differential["mismatch"]
            eprint(
                f"[gate] first mismatch at input #{mismatch['index']}: "
                f"old={mismatch['old']!r} new={mismatch['new']!r}"
            )
            eprint("[gate] rejected: faster because it does less.")
        elif bench["verdict"] != "beyond-band":
            eprint(f"[gate] rejected: speedup {bench['speedup']} within the band (< {args.min_speedup})")
    return EXIT_OK if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    sub = parser.add_subparsers(dest="cmd", required=True)

    gate_p = sub.add_parser("gate", help="run both mandatory halves against a candidate")
    gate_p.add_argument("--old", required=True, help="baseline implementation file")
    gate_p.add_argument("--new", required=True, help="candidate implementation file")
    gate_p.add_argument("--fn-name", required=True, help="function name in both modules")
    gate_p.add_argument("--min-speedup", type=float, default=2.0, help="configured noise band floor")
    gate_p.add_argument("--samples", type=int, default=3)
    gate_p.add_argument("--bench-size", type=int, default=60000)
    gate_p.add_argument("--differential-count", type=int, default=200)
    gate_p.add_argument("--json", action="store_true")
    gate_p.set_defaults(func=cmd_gate)

    args = parser.parse_args()
    try:
        return args.func(args)
    except Fatal as exc:
        eprint(f"[!] {exc}")
        return 2
    except OSError as exc:
        eprint(f"[!] {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
