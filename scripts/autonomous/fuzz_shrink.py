#!/usr/bin/env python3
"""Stream C: recorded-seed fuzzing with a deterministic shrink oracle.

references/08-recursive-self-improvement.md, stream C at its capped tier:
the finder is *script-made* and deterministic (recorded seeds, no hidden
generation state), and so is the oracle:

* ``run``      — pipe every seed in the corpus through the repo-supplied
  property harness. The first failing seed is shrunk with delta-debugging
  (deterministic: the same corpus + harness always yields the same
  counterexample) and written to ``corpus/regressions/CE-<sha8>.txt`` as a
  retained regression seed. Exit 1 when a counterexample is found.
* ``replay``   — run the harness against one counterexample file: rc 1
  (red) before the Builder fix, rc 0 (green) after. This red->green flip
  is the stream's mechanical oracle.

The harness command is a repo file; the tool feeds input on stdin and
never edits the harness, the source, or any repo file except writing the
counterexample artifact (the retained seed IS the artifact the breaker
adds to the corpus).

Usage:
  python3 scripts/autonomous/fuzz_shrink.py run --harness tests/property.py \
      --corpus corpus/seeds.txt --json
  python3 scripts/autonomous/fuzz_shrink.py replay --harness tests/property.py \
      --counterexample corpus/regressions/CE-1a2b3c4d.txt
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional

from common import EXIT_OK, Fatal, eprint

SCHEMA = "fuzz-seed/1"


def run_harness(repo: Path, harness: str, text: str, timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [sys.executable, harness],
            cwd=str(repo),
            input=text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise Fatal(f"harness timed out after {timeout}s: {exc}") from exc


def fails_property(repo: Path, harness: str, text: str, timeout: int) -> bool:
    return run_harness(repo, harness, text, timeout).returncode != 0


def shrink(
    repo: Path,
    harness: str,
    text: str,
    fails: Callable[[str], bool],
) -> str:
    """Delta-debug: remove decreasing chunks while the property still fails.

    Fully deterministic for a given starting input: candidates are tried in
    a fixed order and accepted only when they still fail, so identical
    corpus + harness + source always produce identical output.
    """
    chars: List[str] = list(text)
    chunk_size = max(1, len(chars) // 2)
    while chunk_size >= 1:
        index = 0
        while index < len(chars):
            candidate = "".join(chars[:index] + chars[index + chunk_size:])
            if candidate != text and fails(candidate):
                chars = chars[:index] + chars[index + chunk_size:]
            else:
                index += chunk_size
        if chunk_size == 1:
            break
        chunk_size = max(1, chunk_size // 2)
    return "".join(chars)


def load_seeds(corpus: Path) -> List[str]:
    if not corpus.is_file():
        raise Fatal(f"corpus not found: {corpus}")
    seeds = [line.rstrip("\n") for line in corpus.read_text(encoding="utf-8", errors="replace").splitlines()]
    return [s for s in seeds if s != ""]


def cmd_run(args: argparse.Namespace) -> int:
    repo = Path.cwd()
    harness = repo / args.harness
    if not harness.is_file():
        raise Fatal(f"harness not found: {harness}")
    corpus = repo / args.corpus
    seeds = load_seeds(corpus)

    def fails(text: str) -> bool:
        return fails_property(repo, args.harness, text, args.timeout)

    failing_seed: Optional[str] = None
    failing_index = -1
    for index, seed in enumerate(seeds):
        if fails(seed):
            failing_seed, failing_index = seed, index
            break
    if failing_seed is None:
        payload = {
            "schema": SCHEMA,
            "tool": "fuzz_shrink.py run",
            "result": "clean",
            "corpus": str(corpus.name),
            "seeds_run": len(seeds),
            "note": "every recorded seed passed the property harness",
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            eprint(f"[fuzz] clean: {len(seeds)} recorded seeds passed the property")
        return EXIT_OK

    shrunk = shrink(repo, args.harness, failing_seed, fails)
    digest = hashlib.sha256(shrunk.encode("utf-8")).hexdigest()[:8]
    regressions = repo / args.regressions_dir
    regressions.mkdir(parents=True, exist_ok=True)
    ce_file = regressions / f"CE-{digest}.txt"
    ce_file.write_text(shrunk + "\n", encoding="utf-8")
    payload = {
        "schema": SCHEMA,
        "tool": "fuzz_shrink.py run",
        "result": "counterexample",
        "corpus": str(corpus.name),
        "seeds_run": failing_index + 1,
        "counterexample": {
            "file": ce_file.relative_to(repo).as_posix(),
            "input": shrunk,
            "from_seed": failing_seed,
            "shrunk": len(shrunk) < len(failing_seed),
        },
        "note": "counterexample retained in corpus; replay is the red->green oracle",
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        eprint(f"[fuzz] counterexample: {ce_file.relative_to(repo)}")
        eprint(f"[fuzz] shrunk input: {shrunk!r} (seed was {failing_seed!r})")
        eprint("[fuzz] next: replay the counterexample after the fix.")
    return 1


def cmd_replay(args: argparse.Namespace) -> int:
    repo = Path.cwd()
    harness = repo / args.harness
    if not harness.is_file():
        raise Fatal(f"harness not found: {harness}")
    ce_path = repo / args.counterexample
    if not ce_path.is_file():
        raise Fatal(f"counterexample not found: {ce_path}")
    text = ce_path.read_text(encoding="utf-8").rstrip("\n")
    proc = run_harness(repo, args.harness, text, args.timeout)
    if proc.returncode == 0:
        payload = {
            "schema": SCHEMA,
            "tool": "fuzz_shrink.py replay",
            "status": "green",
            "counterexample": args.counterexample,
            "tail": (proc.stdout or "").strip().splitlines()[-1:],
            "note": "retained seed passes; the fix (or a behavior change) closed it",
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            eprint(f"[fuzz] green: {args.counterexample} no longer breaks the property")
        return EXIT_OK
    payload = {
        "schema": SCHEMA,
        "tool": "fuzz_shrink.py replay",
        "status": "red",
        "counterexample": args.counterexample,
        "tail": (proc.stdout or "").strip().splitlines()[-1:],
        "note": "the retained seed still fails the property harness",
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        eprint(f"[fuzz] red: {args.counterexample} still breaks the property:")
        eprint("  " + (proc.stdout or "").strip().splitlines()[-1] if (proc.stdout or "").strip() else "")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="run recorded seeds; shrink + retain the first counterexample")
    run_p.add_argument("--harness", required=True, help="repo harness script; input arrives on stdin")
    run_p.add_argument("--corpus", required=True, help="seed file, one input per line")
    run_p.add_argument("--regressions-dir", default="corpus/regressions",
                       help="where retained counterexamples are written")
    run_p.add_argument("--timeout", type=int, default=60)
    run_p.add_argument("--json", action="store_true")
    run_p.set_defaults(func=cmd_run)

    replay_p = sub.add_parser("replay", help="red/green oracle for one counterexample file")
    replay_p.add_argument("--harness", required=True)
    replay_p.add_argument("--counterexample", required=True)
    replay_p.add_argument("--timeout", type=int, default=60)
    replay_p.add_argument("--json", action="store_true")
    replay_p.set_defaults(func=cmd_replay)

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
