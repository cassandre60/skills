#!/usr/bin/env python3
"""Heuristic detector for changes that weaken the test suite or silence analyzers.

Scans `git diff -U0 BASE...HEAD` and reports:
  - removed assertion-like lines in test files
  - removed test-case declarations in test files
  - added skip/ignore/disable markers in test files
  - added lint/type suppressions in ANY file
It is a tripwire, not proof: findings require a human (or a justified note in the PR).
Patterns are deliberately language-agnostic and noisy on purpose; tune with --extra-* flags.

Exit codes: 0 clean, 4 findings, 2 usage error.
"""
import argparse
import re
import subprocess
import sys

TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|specs|e2e|integration_test)(/|$)|(_test|_spec|\.test|\.spec)\.|(^|/)test_[^/]*$", re.I)
ASSERT = re.compile(r"\b(assert\w*|expect\w*|should\w*|verify\w*|require\w*|check\w*|ok|equal\w*|matcher)\s*[(.\s]|\bassert\b|\bExpect\b", re.I)
CASE = re.compile(r"^\s*(def test_\w+|func Test\w+|fn test_\w+|@Test\b|@pytest|\b(it|test|testWidgets|group|describe|property|forAll)\s*\(|#\[test\])")
SKIP = re.compile(r"(\.skip\b|\bskip\b|\bxit\b|\bxdescribe\b|\bxtest\b|\bxfail\b|@Ignore|@Disabled|\bt\.Skip|\bpending\b|\.only\b|\btodo\b|\bignore\b|#\[ignore\]|markSkipped|skip:)", re.I)
SUPPRESS = re.compile(r"(eslint-disable|@ts-ignore|@ts-expect-error|@ts-nocheck|type:\s*ignore|noqa|nolint|# pylint: disable|#\[allow\(|//\s*ignore:|// ignore_for_file|@SuppressWarnings|NOSONAR|// nosemgrep|# nosec|unsafe\s*\{|\bas\s+any\b|\bdynamic\b\s+\w+\s*=)")


def diff_lines(base, head):
    out = subprocess.run(["git", "diff", "-U0", "--no-color", "--no-renames", f"{base}...{head}"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        print(out.stderr, file=sys.stderr)
        sys.exit(2)
    path, lineno = None, 0
    for raw in out.stdout.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else None
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            lineno = int(m.group(1)) if m else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            yield path, lineno, "+", raw[1:]
            lineno += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            yield path, lineno, "-", raw[1:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", default="HEAD")
    args = ap.parse_args()

    findings = []
    for path, lineno, sign, text in diff_lines(args.base, args.head):
        if not path:
            continue
        is_test = bool(TEST_PATH.search(path))
        if sign == "-" and is_test and ASSERT.search(text):
            findings.append((path, lineno, "removed assertion-like line", text.strip()))
        elif sign == "-" and is_test and CASE.search(text):
            findings.append((path, lineno, "removed test declaration", text.strip()))
        elif sign == "+" and is_test and SKIP.search(text):
            findings.append((path, lineno, "added skip/ignore marker in test", text.strip()))
        elif sign == "+" and SUPPRESS.search(text):
            findings.append((path, lineno, "added suppression", text.strip()))

    if not findings:
        print("no test-weakening patterns found")
        return 0
    print("Possible weakening (needs human judgement):")
    for path, lineno, kind, text in findings:
        print(f"  {path}:{lineno}: {kind}: {text[:110]}")
    print("\nIf intentional, explain in the PR why the removed check is obsolete or where its "
          "coverage moved. Never weaken a check to make a gate pass.")
    return 4


if __name__ == "__main__":
    sys.exit(main())
