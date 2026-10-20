#!/usr/bin/env python3
"""Shared helpers for the autonomous-improvement scripts.

Standard library only; Python >= 3.9. Everything project-specific comes from
``config.json`` (see ``config.example.json``); nothing in this module knows
about a particular language, repo, or remote.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

EXIT_OK = 0
EXIT_NEGATIVE = 1
EXIT_USAGE = 2
EXIT_ENV = 3

DEFAULT_CONFIG = {
    "base_ref": "origin/main",
    "ledger_path": "docs/AUTONOMOUS_WORK_LEDGER.md",
    "origin_url": None,
    "setup_cmds": [],
    "format_check_cmd": None,
    "analyze_cmd": None,
    "analyze_extensions": [],
    "gate_cmd": None,
    "test_cmd": None,
    "test_for_source": [],
    "candidates": {
        "A": {"include": [], "exclude": [], "require_test": True, "priority_paths": []},
        "B": {"include": [], "exclude": [], "priority_paths": []},
    },
    "machine_budget": {"max_concurrent_tests": 1},
    "pr_required": {"COMPLETED": True, "SOUND": False},
}


class UsageError(Exception):
    """Bad flags or unsupported input (exit 2)."""


class EnvError(Exception):
    """Missing tool / broken environment (exit 3)."""


class Fatal(Exception):
    """A refusal or negative result (exit 1)."""


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def run(
    argv: Sequence[str],
    cwd: Optional[os.PathLike] = None,
    check: bool = False,
    timeout: Optional[float] = None,
    quiet: bool = False,
) -> subprocess.CompletedProcess:
    """Run an argv list. Never uses a shell.

    Raises :class:`EnvError` when the executable is missing and
    :class:`Fatal` when ``check`` is true and the command fails.
    """
    argv = [str(a) for a in argv]
    if not quiet:
        print("[*] Running: " + " ".join(argv) + (f" (cwd: {cwd})" if cwd else ""))
    try:
        proc = subprocess.run(
            argv,
            cwd=None if cwd is None else str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise EnvError(f"required tool not found: {argv[0]}")
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        if isinstance(err, bytes):
            err = err.decode(errors="replace")
        raise EnvError(f"command timed out after {timeout}s: {' '.join(argv)}\n{out}{err}".strip())
    if check and proc.returncode != 0:
        raise Fatal(
            f"command failed ({proc.returncode}): {' '.join(argv)}\n"
            f"{(proc.stderr or proc.stdout).strip()}"
        )
    return proc


def git(
    args: Sequence[str],
    cwd: Optional[os.PathLike] = None,
    check: bool = False,
    timeout: Optional[float] = None,
    quiet: bool = True,
) -> subprocess.CompletedProcess:
    return run(
        ["git"] + [str(a) for a in args], cwd=cwd, check=check, timeout=timeout, quiet=quiet
    )


def repo_root(start: Optional[os.PathLike] = None) -> Path:
    proc = git(["rev-parse", "--show-toplevel"], cwd=start or Path.cwd())
    if proc.returncode != 0:
        raise EnvError(
            "not inside a git repository (run this from a checkout, or "
            "`git init` first)"
        )
    return Path(proc.stdout.strip()).resolve()


def git_common_dir(root: Path) -> Path:
    proc = git(["rev-parse", "--git-common-dir"], cwd=root, check=True)
    path = Path(proc.stdout.strip())
    if not path.is_absolute():
        path = (root / path).resolve()
    return path


def main_repo_root(start: Optional[os.PathLike] = None) -> Path:
    """Return the main checkout root, even when called from a linked worktree."""
    root = repo_root(start)
    common = git_common_dir(root)
    if common.name == ".git":
        return common.parent
    return root


def _deep_merge(defaults: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(defaults)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def config_path(root: Path, explicit: Optional[str] = None) -> Path:
    if explicit:
        path = Path(explicit)
        return path if path.is_absolute() else (Path.cwd() / path).resolve()
    return root / "scripts" / "autonomous" / "config.json"


def load_config(root: Path, explicit: Optional[str] = None) -> Dict[str, Any]:
    path = config_path(root, explicit)
    if not path.exists():
        if explicit:
            raise UsageError(f"config not found: {path}")
        return _deep_merge(DEFAULT_CONFIG, {})
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise UsageError(f"invalid JSON in {path}: {exc}")
    if not isinstance(data, dict):
        raise UsageError(f"config must be a JSON object: {path}")
    return _deep_merge(DEFAULT_CONFIG, data)


def rel_path(root: Path, path: str) -> str:
    """Return a repo-root-relative posix path, refusing paths outside the repo.

    Relative inputs are interpreted from the repository root (not the process
    cwd), so every command produces the same result from a subdirectory.
    """
    candidate = Path(path)
    if candidate.is_absolute():
        candidate = candidate.resolve()
    else:
        candidate = (root / candidate).resolve()
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError:
        raise UsageError(f"path is outside the repository: {path}")


def path_at_head(root: Path, path: str) -> bool:
    proc = git(["cat-file", "-e", f"HEAD:{path}"], cwd=root)
    return proc.returncode == 0


def hash_at_head(root: Path, path: str) -> Optional[str]:
    """9-char blob hash of ``path`` at HEAD, or None when absent."""
    proc = git(["rev-parse", f"HEAD:{path}"], cwd=root)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout.strip()[:9]


def hash_worktree(root: Path, path: str) -> Optional[str]:
    full = root / path
    if not full.exists():
        return None
    proc = git(["hash-object", path], cwd=root)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout.strip()[:9]


def is_dirty(root: Path, path: str) -> bool:
    proc = git(["status", "--porcelain", "--", path], cwd=root)
    return bool(proc.stdout.strip())


def ls_files(root: Path) -> List[str]:
    proc = git(["ls-files", "-z"], cwd=root)
    if proc.returncode != 0:
        raise EnvError("git ls-files failed")
    return sorted(p for p in proc.stdout.split("\0") if p)


def glob_to_regex(pattern: str) -> str:
    """Translate a small glob dialect (``**``, ``*``, ``?``) to a regex."""
    out: List[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern[i : i + 2] == "**":
                if pattern[i : i + 3] == "**/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    return "^" + "".join(out) + "$"


def glob_match(path: str, pattern: str) -> bool:
    return re.match(glob_to_regex(pattern), path) is not None


def match_any(path: str, patterns: Iterable[str]) -> bool:
    return any(glob_match(path, pattern) for pattern in patterns)


def _sorted_by_priority(paths: Iterable[str], priorities: Iterable[str]) -> List[str]:
    priorities = [p for p in priorities if p]

    def key(path: str) -> Tuple[int, str]:
        first = 0
        for priority in priorities:
            prefix = priority.rstrip("/")
            if path == prefix or path.startswith(prefix + "/") or glob_match(path, priority):
                break
        else:
            first = 1
        return (first, path)

    return sorted(paths, key=key)


def candidate_paths(root: Path, cfg: Dict[str, Any], stream: str) -> List[str]:
    section = cfg.get("candidates", {}).get(stream, {})
    include = section.get("include") or []
    exclude = section.get("exclude") or []
    if not include:
        return []
    paths = [p for p in ls_files(root) if match_any(p, include) and not match_any(p, exclude)]
    return _sorted_by_priority(paths, section.get("priority_paths") or [])


def _mapping_candidates(path: str) -> List[str]:
    """Language-neutral guessed test locations, used only when config maps nothing."""
    p = Path(path)
    stem = p.stem
    suffix = p.suffix.lower()
    parent = p.parent.as_posix()
    parent = "" if parent == "." else parent
    guesses: List[str] = []
    if suffix == ".dart":
        rel = path[len("lib/") :] if path.startswith("lib/") else path
        guesses.append("test/" + rel[: -len(".dart")] + "_test.dart")
    elif suffix == ".py":
        guesses.append((parent + "/" if parent else "") + "test_" + stem + ".py")
        guesses.append((parent + "/" if parent else "") + stem + "_test.py")
        guesses.append("tests/test_" + stem + ".py")
        guesses.append("test_" + stem + ".py")
    elif suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        base = (parent + "/" if parent else "") + stem
        guesses.extend([base + ".test" + suffix, base + ".spec" + suffix])
        guesses.append("test/" + stem + ".test" + suffix)
        guesses.append("tests/" + stem + ".test" + suffix)
    elif suffix == ".go":
        guesses.append((parent + "/" if parent else "") + stem + "_test.go")
    elif suffix in (".rs",):
        guesses.append((parent + "/" if parent else "") + stem + "_test.rs")
    return guesses


def test_for_source(root: Path, cfg: Dict[str, Any], source: str) -> Optional[str]:
    """Map a source path to a test path using config, then conservative guesses."""
    for entry in cfg.get("test_for_source") or []:
        if not isinstance(entry, dict):
            continue
        pattern = entry.get("src")
        template = entry.get("test")
        if not pattern or not template:
            continue
        try:
            if re.search(pattern, source):
                mapped = re.sub(pattern, template, source)
                if path_at_head(root, mapped):
                    return mapped
                return mapped
        except re.error as exc:
            raise UsageError(f"invalid test_for_source regex {pattern!r}: {exc}")
    for guess in _mapping_candidates(source):
        if path_at_head(root, guess):
            return guess
    guesses = _mapping_candidates(source)
    return guesses[0] if guesses else None


def normalize_stream(value: str) -> str:
    raw = str(value).strip()
    upper = raw.upper()
    aliases = {
        "1": "A",
        "2": "B",
        "A": "A",
        "B": "B",
        "STREAM A": "A",
        "STREAM B": "B",
        "A (MUTATION)": "A",
        "B (TEST AUDIT)": "B",
    }
    if upper in aliases:
        return aliases[upper]
    if upper in ("C", "D", "3", "4", "STREAM C", "STREAM D"):
        raise UsageError(f"stream {upper} is planned, not implemented (use A or B)")
    raise UsageError(f"unknown stream {value!r}; use A or B (1/2 are aliases)")


def stream_label(stream: str) -> str:
    return {"A": "A (Mutation)", "B": "B (Test Audit)"}[stream]


def configured_commands(value: Any, key: str) -> List[List[str]]:
    """Accept null, a single argv array, or a list of argv arrays."""
    if value is None:
        return []
    if not isinstance(value, list) or (value and not isinstance(value[0], list)):
        raise UsageError(f"config key {key} must be a list of argv arrays")
    for argv in value:
        if not all(isinstance(item, str) for item in argv):
            raise UsageError(f"config key {key} contains a non-string argument")
    return value


def command(value: Any, key: str) -> Optional[List[str]]:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise UsageError(f"config key {key} must be an argv array or null")
    return value


def append_command(base: List[str], values: Sequence[str]) -> List[str]:
    return list(base) + list(values)


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def format_exit(exc: BaseException) -> int:
    if isinstance(exc, UsageError):
        eprint(f"[!] {exc}")
        return EXIT_USAGE
    if isinstance(exc, EnvError):
        eprint(f"[!] {exc}")
        return EXIT_ENV
    if isinstance(exc, Fatal):
        eprint(f"[!] {exc}")
        return EXIT_NEGATIVE
    eprint(f"[!] unexpected error: {exc}")
    return EXIT_ENV
