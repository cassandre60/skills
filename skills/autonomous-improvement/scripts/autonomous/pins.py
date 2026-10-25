#!/usr/bin/env python3
"""Pin ratchet: make wording-pin debt visible and monotone shrinking.

Phase 5.4 of the generality plan. The suite pins many exact SKILL.md /
reference phrases with ``assertIn("<literal>", ...)`` — 261 assertIn sites
at the last count, most with no behavioral oracle behind them. This tool
does not rewrite a single existing test. It maps every pin
(``tests/pins.json``, each pin mapped to the eval case that proves the
behavior, or ``null`` when the pin is wording-only), fails when a mapped
pin disappears (wording drifted), and ratchets the wording-only count:
``tests/pins.baseline.json`` holds the number, it may only go down.

Usage:
  python3 scripts/autonomous/pins.py scan [--json]
  python3 scripts/autonomous/pins.py check
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SKILL_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = SKILL_ROOT / "tests"
PINS_JSON = TESTS_DIR / "pins.json"
BASELINE_JSON = TESTS_DIR / "pins.baseline.json"


def _string_arg(node: ast.AST) -> Optional[str]:
    """A constant string argument, including implicit concatenation."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _string_arg(node.left)
        right = _string_arg(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def scan_pins(tests_dir: Path = TESTS_DIR) -> List[Dict[str, Any]]:
    """Every assertIn(<string literal>, ...) pin in doc-reading test files."""
    pins: List[Dict[str, Any]] = []
    seen = set()
    for path in sorted(tests_dir.glob("test_*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # A doc-pinning test file READS the docs it pins: it names SKILL.md,
        # or reads reference files (mentioning references/ alone is not
        # enough — fixture strings quote paths without pinning any doc).
        if "SKILL.md" not in source and not (
            "references/" in source and "read_text" in source
        ):
            continue  # not a doc-pinning test file
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        stack: List[str] = []
        classes: Dict[ast.AST, str] = {}
        functions: Dict[ast.AST, str] = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes[node] = node.name
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[node] = node.name

        def enclosing(node: ast.AST) -> tuple:
            # Attribute the call to the innermost enclosing function and,
            # when that function sits in a class, to that class.
            for func, name in functions.items():
                if func.lineno <= getattr(node, "lineno", 0) <= getattr(
                    func, "end_lineno", func.lineno
                ):
                    for cls, cname in classes.items():
                        if cls.lineno <= func.lineno <= getattr(
                            cls, "end_lineno", cls.lineno
                        ):
                            return cname, name
                    return None, name
            return None, None

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name != "assertIn" or not node.args:
                continue
            literal = _string_arg(node.args[0])
            if literal is None or not literal.strip():
                continue
            cls, fn = enclosing(node)
            entry = {
                "file": f"tests/{path.name}",
                "class": cls,
                "test": fn,
                "pin": literal,
            }
            key = (entry["file"], literal)
            if key in seen:
                continue
            seen.add(key)
            pins.append(entry)
    pins.sort(key=lambda p: (p["file"], p["test"] or "", p["pin"]))
    return pins


def check_maps(
    map_path: Path = PINS_JSON, baseline_path: Path = BASELINE_JSON
) -> List[str]:
    """Problems with the current pins map: drift, rot, ratchet violations."""
    problems: List[str] = []
    data = json.loads(map_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    live = {(p["file"], p["pin"]) for p in scan_pins(map_path.parent)}
    mapped = {(p["file"], p["pin"]): p.get("case") for p in data.get("pins", [])}
    for (file, pin), case in sorted(mapped.items()):
        if (file, pin) not in live:
            problems.append(f"pin disappeared from {file}: {pin[:60]!r}")
    null_pins = sum(1 for case in mapped.values() if case is None)
    floor = int(baseline.get("null_pins", 0))
    if null_pins > floor:
        problems.append(
            f"wording-only (null-case) pins rose: {null_pins} > baseline {floor}; "
            "map the new pins to eval cases or raise them behaviorally"
        )
    return problems


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    scan = sub.add_parser("scan", help="list every doc pin the suite carries")
    scan.add_argument("--json", action="store_true")
    sub.add_parser("check", help="fail on pin drift or ratchet violations")
    args = parser.parse_args(argv)

    if args.cmd == "scan":
        pins = scan_pins()
        if args.json:
            print(json.dumps(pins, indent=2, sort_keys=True))
        else:
            for pin in pins:
                where = f"{pin['file']}::{pin['class'] or ''}.{pin['test'] or '<module>'}"
                print(f"{where}: {pin['pin'][:70]!r}")
        print(f"total pins: {len(pins)}", file=sys.stderr)
        return 0

    problems = check_maps()
    data = json.loads(PINS_JSON.read_text(encoding="utf-8"))
    null_pins = sum(1 for p in data.get("pins", []) if p.get("case") is None)
    print(
        f"pins mapped: {len(data.get('pins', []))}; wording-only: {null_pins} "
        f"(baseline floor: {json.loads(BASELINE_JSON.read_text(encoding='utf-8'))['null_pins']})"
    )
    for problem in problems:
        print(f"[!] {problem}", file=sys.stderr)
    if problems:
        print(f"FAIL: {len(problems)} pin problem(s)", file=sys.stderr)
        return 1
    print("OK: no pin drift; wording-only count at or below baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
