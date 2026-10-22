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

# Ledger status vocabulary. An audit says "this is done"; a disposition says
# "stop asking". The parser accepts ONLY these bare tokens: any extra text in
# the Status cell makes the row invisible, which is why the sets live here and
# are asserted by the validator.
AUDITED_STATUSES = {"COMPLETED", "SOUND"}
DISPOSITION_STATUSES = {"REJECTED", "WONTFIX", "SUPERSEDED"}
STATUSES = ("COMPLETED", "SOUND", "REJECTED", "WONTFIX", "SUPERSEDED")

EXIT_OK = 0
EXIT_NEGATIVE = 1
EXIT_USAGE = 2
EXIT_ENV = 3

DEFAULT_CONFIG = {
    "base_ref": "origin/main",
    "ledger_path": "docs/AUTONOMOUS_WORK_LEDGER.md",
    "mutant_ledger_path": "docs/AUTONOMOUS_MUTANTS.md",
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
    "fleet": {
        "max_open_prs": 5,
        "max_new_prs_per_day": 6,
        "min_acceptance_rate_14d": 0.5,
        "kill_switch_file": ".autonomous/STOP",
        "merge_policy": "never",
        "ledger_mode": "batched",
    },
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


def worktree_registry_path(root: Path) -> Path:
    return git_common_dir(root) / "autonomous-worktrees.json"


def load_worktree_registry(root: Path) -> List[Dict[str, Any]]:
    path = worktree_registry_path(root)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        raise Fatal(f"cannot read worktree registry {path}: {exc}")
    if not isinstance(data, list):
        raise Fatal(f"worktree registry is not a JSON list: {path}")
    return [item for item in data if isinstance(item, dict)]


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


# --- Markdown table helpers -------------------------------------------------
# Both the work ledger and the mutant ledger are git-tracked, append-only
# markdown tables. They must escape and split cells identically so a row written
# by one tool is legible to the other and to a human reading the diff.

SEPARATOR_RE = re.compile(r"^\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")


def escape_cell(value: str) -> str:
    """Escape one markdown table cell (and collapse line breaks)."""
    value = str(value).replace("\r", " ").replace("\n", " ")
    return value.replace("\\", "\\\\").replace("|", "\\|")


def split_table_row(line: str) -> Optional[List[str]]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    pieces = re.split(r"(?<!\\)\|", stripped)[1:-1]
    return [
        piece.strip().replace("\\|", "|").replace("\\\\", "\\")
        for piece in pieces
    ]


def insert_table_rows(path: Path, rows: List[str], header_first: str = "date") -> None:
    """Insert rows directly below the markdown table header, preserving order.

    Callers pass rows newest-first: every reader resolves a same-date tie by
    preferring the topmost row, so the first row in ``rows`` must win.
    """
    if not path.exists():
        raise Fatal(f"table not found: {path}")
    with open(path, "r", encoding="utf-8", newline="") as handle:
        content = handle.read()
    newline = "\r\n" if "\r\n" in content else "\n"
    lines = content.splitlines()

    header_index: Optional[int] = None
    for index, line in enumerate(lines):
        cells = split_table_row(line)
        if cells and cells[0].strip().lower() == header_first:
            header_index = index
            break
    if header_index is None:
        raise Fatal(f"could not locate table header in {path}")
    separator_index: Optional[int] = None
    for index in range(header_index + 1, len(lines)):
        if SEPARATOR_RE.match(lines[index].strip()):
            separator_index = index
            break
    if separator_index is None:
        raise Fatal(f"could not locate table separator in {path}")

    insert_at = separator_index + 1
    lines[insert_at:insert_at] = rows
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(
            newline.join(lines) + (newline if content.endswith(("\n", "\r\n")) else "")
        )


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
        # The config order IS the priority order: a path matching an earlier
        # entry outranks one matching a later entry. Paths matching nothing
        # sort after every prioritized path, lexicographically.
        for rank, priority in enumerate(priorities):
            prefix = priority.rstrip("/")
            if path == prefix or path.startswith(prefix + "/") or glob_match(path, priority):
                return (rank, path)
        return (len(priorities), path)

    return sorted(paths, key=key)


def candidate_paths(root: Path, cfg: Dict[str, Any], stream: str) -> List[str]:
    """The candidate source files for a stream.

    An empty ``include`` list means the fleet is **not configured**, not that
    there is no work. Returning an empty list here would make ``next`` answer
    "no eligible targets remain" - a confident, wrong, green answer that looks
    exactly like an exhausted queue. So it is a configuration error, named.
    """
    section = cfg.get("candidates", {}).get(stream, {})
    include = section.get("include") or []
    exclude = section.get("exclude") or []
    if not include:
        path = config_path(root, None)
        where = f" (no config file at {path})" if not path.exists() else ""
        raise UsageError(
            f"no candidates configured for stream {stream}: set "
            f"candidates.{stream}.include in {path}{where}. "
            "An empty list means 'not configured', not 'nothing to do'."
        )
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


# Stream registry: the catalog from references/08-recursive-self-improvement.md.
# A stream becomes "available" only when its finder + oracle + eval fixture
# ship; until then normalize_stream rejects it by name, so no session can
# start work under a letter that exists only as an encouraging paragraph.
STREAMS: Dict[str, Dict[str, Any]] = {
    "A": {"label": "A (Mutation)", "status": "available"},
    "B": {"label": "B (Test Audit)", "status": "available"},
    "C": {"label": "C (Property / Fuzz)", "status": "planned"},
    "D": {"label": "D (Data / Migration)", "status": "planned"},
    "E": {"label": "E (Defect Repair)", "status": "planned"},
    "F": {"label": "F (Flake & Determinism)", "status": "planned"},
    "G": {"label": "G (Performance)", "status": "planned"},
    "H": {"label": "H (Security & Supply Chain)", "status": "planned"},
    "I": {"label": "I (Docs & Spec Truthfulness)", "status": "planned"},
    "J": {"label": "J (Simplification / Dead Code)", "status": "planned"},
    "K": {"label": "K (Dependency Evolution)", "status": "planned"},
    "L": {"label": "L (Meta: Fleet Self-Improvement)", "status": "planned"},
}
_STREAM_ALIASES: Dict[str, str] = {"1": "A", "2": "B"}
for _sid, _info in STREAMS.items():
    _STREAM_ALIASES[_sid] = _sid
    _STREAM_ALIASES[f"STREAM {_sid}"] = _sid
    _STREAM_ALIASES[str(_info["label"]).upper()] = _sid


def normalize_stream(value: str) -> str:
    raw = str(value).strip()
    upper = raw.upper()
    stream = _STREAM_ALIASES.get(upper)
    if stream is None:
        known = ", ".join(STREAMS)
        raise UsageError(
            f"unknown stream {value!r}; known streams: {known} (1/2 alias A/B)"
        )
    info = STREAMS[stream]
    if info["status"] != "available":
        raise UsageError(
            f"stream {info['label']} is planned, not implemented "
            "(use A or B; see references/08-recursive-self-improvement.md)"
        )
    return stream


def stream_label(stream: str) -> str:
    return str(STREAMS[stream]["label"])


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
