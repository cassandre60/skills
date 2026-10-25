#!/usr/bin/env python3
"""Onboard a repository: detect its stack, write its config, verify its baseline.

Phase 4.4 of the generality plan: no hand-written config, no copied tooling.
``detect`` derives candidate test commands from manifests, CI files (via a
line-based scan — the standard library has no YAML parser, and the output
says so), Makefile/justfile targets and stack defaults; commands carrying
shell metacharacters are refused into ``needs_owner`` and never run.
``write`` turns a detection into ``.autonomous/config.json`` (the canonical
location; see common.config_path). ``verify`` runs the detected command once
in a temporary worktree of HEAD — never the working tree — and records the
baseline verdict: a red baseline disables stream A and prefers defect
repair.

Usage:
  python3 scripts/autonomous/onboard.py detect
  python3 scripts/autonomous/onboard.py write [--force]
  python3 scripts/autonomous/onboard.py verify [--timeout 300]
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DEFAULT_CONFIG, eprint, repo_root  # noqa: E402

SCHEMA = "onboard-detect/1"
CONFIG_REL = Path(".autonomous") / "config.json"

STACK_DEFAULT_TEST: Dict[str, List[str]] = {
    "python": ["python3", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
    "node": ["node", "--test", "test/"],
    "go": ["go", "test", "./..."],
    "rust": ["cargo", "test"],
    "dart": ["flutter", "test"],
}
MANIFESTS: Dict[str, Tuple[str, ...]] = {
    "python": ("pyproject.toml", "setup.cfg", "requirements.txt"),
    "node": ("package.json",),
    "go": ("go.mod",),
    "rust": ("Cargo.toml",),
    "dart": ("pubspec.yaml",),
    "java": ("pom.xml", "build.gradle"),
    "ruby": ("Gemfile",),
    "elixir": ("mix.exs",),
    "php": ("composer.json",),
}
# A CI/make line is only a test-command candidate if it names testing; this
# keeps `npm ci` and `pip install -e .` out of the command map.
TESTISH = ("test", "jest", "pytest", "vitest", "mocha")
METACHARS = ("&&", "|", ";", "$(", "<", ">", ">>")


def _has_metachars(command: str) -> Optional[str]:
    for marker in METACHARS:
        if marker in command:
            return marker
    return None


def _argv_or_refused(command: str) -> Tuple[Optional[List[str]], Optional[Dict[str, Any]]]:
    marker = _has_metachars(command)
    if marker:
        return None, {
            "command": command,
            "reason": f"contains shell metacharacter(s): {marker}; "
            "argv lists only — resolve by hand",
        }
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return None, {
            "command": command,
            "reason": f"cannot split into argv: {exc}",
        }
    if not argv:
        return None, {"command": command, "reason": "empty command"}
    return argv, None


def _ci_commands(root: Path) -> List[str]:
    """Line-based CI scan (no YAML parser in the stdlib; the source label says so)."""
    found: List[str] = []
    for path in sorted((root / ".github" / "workflows").glob("*.yml")) + sorted(
        (root / ".github" / "workflows").glob("*.yaml")
    ):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^\s*-?\s*run:\s*(.+?)\s*$", line)
            if match:
                found.append(match.group(1))
    gitlab = root / ".gitlab-ci.yml"
    if gitlab.is_file():
        for line in gitlab.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^\s*-\s+(.+?)\s*$", line)
            if match and not match.group(1).startswith(("uses:", "run:")):
                found.append(match.group(1))
    return found


def _makefile_test_target(root: Path) -> Optional[str]:
    for name in ("Makefile", "makefile", "justfile"):
        path = root / name
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for index, line in enumerate(lines):
            if re.match(r"^test\s*:", line) or line.startswith("test "):
                for candidate in lines[index + 1 : index + 4]:
                    if candidate.startswith("\t") and candidate.strip():
                        return candidate.strip()
                    if candidate.strip() and not candidate.startswith(("\t", " ")):
                        break
        return None
    return None


def _package_json_script(root: Path) -> Optional[str]:
    path = root / "package.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    script = (data.get("scripts") or {}).get("test")
    return str(script) if script else None


def _detect_test_cmd(
    package_root: Path,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(test_cmd block, needs_owner entries) with the documented precedence."""
    refused: List[Dict[str, Any]] = []

    for raw in _ci_commands(package_root):
        if not any(word in raw for word in TESTISH):
            continue
        argv, refusal = _argv_or_refused(raw)
        if refusal:
            refused.append(refusal)
            continue
        return (
            {"argv": argv, "source": "ci-yaml-linescan", "confidence": "medium"},
            refused,
        )

    make_target = _makefile_test_target(package_root)
    if make_target:
        argv, refusal = _argv_or_refused(make_target)
        if refusal:
            refused.append(refusal)
        else:
            return (
                {"argv": argv, "source": "makefile-target", "confidence": "medium"},
                refused,
            )

    script = _package_json_script(package_root)
    if script:
        argv, refusal = _argv_or_refused(script)
        if refusal:
            refused.append(refusal)
        else:
            return (
                {"argv": argv, "source": "package-json-script", "confidence": "high"},
                refused,
            )

    stacks = [
        stack
        for stack, manifests in MANIFESTS.items()
        if any((package_root / name).is_file() for name in manifests)
    ]
    if stacks:
        # Deterministic: manifest priority order, not directory order.
        for stack in (k for k in MANIFESTS if k in stacks):
            return (
                {
                    "argv": list(STACK_DEFAULT_TEST[stack]),
                    "source": "stack-default",
                    "confidence": "low",
                },
                refused,
            )
    return {"argv": None, "source": "none", "confidence": "none"}, refused


def _stream_a(package_root: Path, prefix: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Infer stream-A globs + source→test mapping from the repo's own naming."""
    tests = sorted(package_root.glob("tests/test_*.py"))
    src = package_root / "src"
    if tests and src.is_dir() and any(src.glob("*.py")):
        pattern_src = re.escape(prefix).replace(re.escape("."), r"\.")
        return (
            [f"{prefix}src/**/*.py"],
            [
                {
                    "src": f"^{prefix}src/(?:.*/)?([^/]+)\\.py$",
                    "test": f"{prefix}tests/test_\\1.py",
                }
            ],
        )
    return [], []


def _mutation_backend(root: Path) -> Optional[str]:
    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and "[tool.mutmut]" in pyproject.read_text(
        encoding="utf-8", errors="replace"
    ):
        return "mutmut"
    if (root / "stryker.conf.json").is_file():
        return "stryker"
    cargo = root / "Cargo.toml"
    if cargo.is_file() and (root / ".cargo-mutants").is_dir():
        return "cargo-mutants"
    pom = root / "pom.xml"
    if pom.is_file() and "<pit>" in pom.read_text(encoding="utf-8", errors="replace"):
        return "pit"
    return None


def _base_ref(root: Path) -> str:
    proc = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return proc.stdout.strip().replace("refs/remotes/", "")
    for candidate in ("origin/main", "origin/master"):
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", candidate],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            return candidate
    return "main"


def detect(root: Path) -> Dict[str, Any]:
    needs_owner: List[Dict[str, Any]] = []
    packages: List[Dict[str, Any]] = []

    roots: List[Tuple[str, str, Path]] = [(".", ".", root)]
    if (root / "packages").is_dir():
        roots += [
            (child.name, f"packages/{child.name}", child)
            for child in sorted(root.glob("packages/*"))
            if child.is_dir()
        ]

    for name, rel, package_root in roots:
        stacks = sorted(
            stack
            for stack, manifests in MANIFESTS.items()
            if any((package_root / m).is_file() for m in manifests)
        )
        if not stacks and name != ".":
            continue
        if not stacks:
            continue
        test_cmd, refused = _detect_test_cmd(package_root)
        needs_owner.extend(refused)
        include, mappings = _stream_a(package_root, "" if rel == "." else f"{rel}/")
        packages.append(
            {
                "package": name,
                "path": rel,
                "stacks": stacks,
                "test_cmd": test_cmd,
                "stream_a": {"include": include},
                "test_for_source": mappings,
            }
        )

    return {
        "schema": SCHEMA,
        "packages": sorted(packages, key=lambda p: p["path"]),
        "needs_owner": sorted(needs_owner, key=lambda e: e["command"]),
        "base_ref": _base_ref(root),
        "mutation_backend": _mutation_backend(root),
        "note": "CI commands come from a line-based scan (the standard library "
        "has no YAML parser); every command carries its source and confidence. "
        "Commands in needs_owner are never run.",
    }


def _write(root: Path, force: bool) -> int:
    dest = root / CONFIG_REL
    if dest.exists() and not force:
        eprint(f"config already exists: {dest} (use --force to overwrite)")
        return 2
    payload = detect(root)
    root_block = next((p for p in payload["packages"] if p["path"] == "."), None)
    source_block = root_block or (payload["packages"][0] if payload["packages"] else None)
    config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy of defaults
    if source_block:
        if source_block["test_cmd"]["argv"]:
            config["test_cmd"] = source_block["test_cmd"]["argv"]
        config["candidates"]["A"]["include"] = source_block["stream_a"]["include"]
        if source_block["test_for_source"]:
            config["test_for_source"] = source_block["test_for_source"]
    config["base_ref"] = payload["base_ref"]
    # New repos start at T1 (issue-mode publish); the owner promotes to T2.
    config["trust"] = {"tier": "T1"}
    config["onboard"] = {
        "detected_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "sources": {
            "test_cmd": source_block["test_cmd"]["source"] if source_block else "none",
            "stream_a": "test-naming"
            if source_block and source_block["stream_a"]["include"]
            else "unconfigured",
            "base_ref": payload["base_ref"],
        },
        "confidence": source_block["test_cmd"]["confidence"] if source_block else "none",
        "verified_baseline": None,
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"[+] wrote {dest} (test_cmd from {config['onboard']['sources']['test_cmd']})")
    return 0


def _verify(root: Path, timeout: int) -> int:
    dest = root / CONFIG_REL
    if not dest.is_file():
        eprint(f"no config to verify: {dest} (run `onboard.py write` first)")
        return 2
    config = json.loads(dest.read_text(encoding="utf-8"))
    argv = config.get("test_cmd")
    if not argv:
        eprint("config has no test_cmd; nothing to verify")
        return 2

    worktree = Path(tempfile.mkdtemp(prefix="onboard-verify-")) / "head"
    add = subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree), "HEAD"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if add.returncode != 0:
        eprint(f"cannot create worktree of HEAD: {add.stderr.strip()}")
        return 2
    import time

    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv, cwd=str(worktree), capture_output=True, text=True,
            timeout=timeout, check=False,
        )
        exit_code, tail = proc.returncode, (proc.stdout + proc.stderr)[-800:]
    except subprocess.TimeoutExpired:
        exit_code, tail = 124, f"timed out after {timeout}s"
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=str(root), capture_output=True, check=False,
        )
        subprocess.run(
            ["git", "worktree", "prune"], cwd=str(root),
            capture_output=True, check=False,
        )
    duration = round(time.monotonic() - started, 1)

    print(f"[verify] baseline command: {' '.join(argv)} (worktree of HEAD)")
    print(f"exit_code: {exit_code}")
    print(f"duration_seconds: {duration}")
    print("output_tail:")
    for line in tail.splitlines()[-12:]:
        print(f"  {line}")

    config.setdefault("onboard", {})
    if exit_code == 0:
        config["onboard"]["verified_baseline"] = True
        dest.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        print("[verify] baseline is GREEN: stream A configured and verified.")
        return 0
    config["onboard"]["verified_baseline"] = False
    config.setdefault("candidates", {}).setdefault("A", {})["include"] = []
    dest.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    eprint(
        "[verify] baseline is RED: stream A disabled (no oracle to mutate "
        "against); prefer defect repair (survey weight 1.0) until the suite "
        "is green."
    )
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("detect", help="print the detection JSON; change nothing")
    write = sub.add_parser("write", help="write .autonomous/config.json from the detection")
    write.add_argument("--force", action="store_true", help="overwrite an existing config")
    verify = sub.add_parser("verify", help="run the detected test command once in a worktree of HEAD")
    verify.add_argument("--timeout", type=int, default=300, help="per-run seconds")
    args = parser.parse_args(argv)

    root = repo_root()
    if args.cmd == "detect":
        print(json.dumps(detect(root), indent=2, sort_keys=True))
        return 0
    if args.cmd == "write":
        return _write(root, args.force)
    return _verify(root, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
