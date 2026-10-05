#!/usr/bin/env python3
"""Keep nondeterminism and forbidden APIs out of designated directories (e.g. the pure core).

Config (JSON):
  {"rules": [{"paths": ["lib/core/**", "src/domain/**"],
              "forbid": ["DateTime\\.now", "Random\\(", "Date\\.now", "Math\\.random", "time\\.time\\(",
                         "import .*dart:io", "import .*package:flutter"],
              "why": "core must be pure: inject Clock/Random/Storage instead"}]}
Patterns are Python regexes matched per line; globs use fnmatch ('*' crosses '/').
A line containing 'gate-allow: <reason>' is exempt, and each exemption is printed so review can see it.

Usage: banned_apis_check.py --config .gates/banned-apis.json [--root .]
Exit codes: 0 clean, 1 violations, 2 usage error.
"""
import argparse
import fnmatch
import json
import os
import re
import sys

SKIP = {".git", "node_modules", ".dart_tool", "build", "dist", "target", "__pycache__", ".venv"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    try:
        with open(args.config, encoding="utf-8") as fh:
            rules = json.load(fh)["rules"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: bad config: {exc}", file=sys.stderr)
        return 2

    compiled = [(r["paths"], [re.compile(p) for p in r["forbid"]], r.get("why", "")) for r in rules]
    violations, exempt = [], []
    for dp, dns, fns in os.walk(args.root):
        dns[:] = [d for d in dns if d not in SKIP]
        for fn in fns:
            path = os.path.relpath(os.path.join(dp, fn), args.root).replace(os.sep, "/")
            active = [(pats, why) for globs, pats, why in compiled if any(fnmatch.fnmatch(path, g) for g in globs)]
            if not active:
                continue
            try:
                with open(os.path.join(dp, fn), encoding="utf-8") as fh:
                    lines = fh.readlines()
            except (OSError, UnicodeDecodeError):
                continue
            for n, line in enumerate(lines, 1):
                for pats, why in active:
                    for pat in pats:
                        if pat.search(line):
                            if "gate-allow:" in line:
                                exempt.append(f"{path}:{n}: {line.strip()[:100]}")
                            else:
                                violations.append(f"{path}:{n}: matches /{pat.pattern}/ - {why}")
    for e in exempt:
        print("exempted:", e)
    if violations:
        print("Forbidden API usage:")
        for v in violations:
            print("  ", v)
        return 1
    print("banned-API check clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
