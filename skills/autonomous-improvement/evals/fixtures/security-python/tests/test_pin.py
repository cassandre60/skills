import argparse
import glob
import os
import re
import sys

PIN_RE = re.compile(r"^vulnerable-lib==(\S+)$", re.MULTILINE)


def pinned_version() -> str:
    for line in open("requirements.txt", encoding="utf-8"):
        match = re.match(r"^vulnerable-lib==(\S+)", line.strip())
        if match:
            return match.group(1)
    raise SystemExit("no vulnerable-lib pin found in requirements.txt")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect", required=True)
    args = parser.parse_args()
    pinned = pinned_version()
    if pinned != args.expect:
        print(f"pinned={pinned} expected={args.expect}", file=sys.stderr)
        print(f"FAIL: pin mismatch (saw {pinned})", file=sys.stderr)
        return 1
    print(f"ok: pin {pinned} == expected {args.expect}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
