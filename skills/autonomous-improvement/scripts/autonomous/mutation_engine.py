#!/usr/bin/env python3
"""Regex-based mutation engine with per-language rule packs.

This is intentionally not an AST mutator. It mutates one source line at a time,
runs one targeted test command per mutant, and reports five states:

  KILLED        tests failed (the mutant is caught)
  SURVIVED      tests passed and the clean-source confirmation pass holds
                (a proven gap, subject to human triage)
  UNCOMPILABLE  an anchored compiler-error pattern matched
  TIMEOUT       the test command exceeded the effective timeout
  UNVERIFIED    a survivor whose clean-source confirmation run failed: the
                suite is not reliable on clean source, so the verdict would
                be a coin flip, not evidence (#16). Not reported as work.

Every survivor needs human triage: equivalent mutants, string/comment noise, and
regex false positives are real. The engine never runs the full suite unless the
``--test-cmd`` you give it does.

Safety: the source file is backed up to ``<file>.mutbak`` with a
``<file>.mutlock`` pid file before mutation. SIGTERM/SIGINT/SIGHUP restore it;
a later run restores it after SIGKILL. Shards must run in separate worktrees.
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import random
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from common import (
    EXIT_ENV,
    EXIT_NEGATIVE,
    EXIT_OK,
    EXIT_USAGE,
    EnvError,
    Fatal,
    UsageError,
    git as _git,
    load_config,
    repo_root,
    write_json_atomic,
)

RULES_DIR = Path(__file__).resolve().parent / "mutation_rules"
LANG_ALIASES = {
    "py": "python",
    "python": "python",
    "dart": "dart",
    "ts": "typescript",
    "tsx": "typescript",
    "typescript": "typescript",
    "js": "javascript",
    "jsx": "javascript",
    "javascript": "javascript",
    "go": "go",
}
EXTENSION_LANG = {
    ".py": "python",
    ".dart": "dart",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
}

_active_pgid: Optional[int] = None


@dataclass
class Mutation:
    file_path: str
    line_no: int
    column: int
    original_text: str
    mutated_text: str
    description: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file_path,
            "line": self.line_no,
            "column": self.column,
            "original": self.original_text.rstrip("\n"),
            "mutated": self.mutated_text.rstrip("\n"),
            "description": self.description,
        }


def load_pack(language: Optional[str], source: str) -> Dict[str, Any]:
    if language:
        lang = LANG_ALIASES.get(language.lower())
        if not lang:
            raise UsageError(
                f"unknown language {language!r}; supported: python, dart, typescript, javascript, go"
            )
    else:
        lang = EXTENSION_LANG.get(Path(source).suffix.lower())
        if not lang:
            supported = ", ".join(sorted(EXTENSION_LANG))
            raise UsageError(
                f"unsupported source extension {Path(source).suffix!r} for {source}; "
                f"supported extensions: {supported} (or pass --lang)"
            )
    path = RULES_DIR / f"{lang}.json"
    if not path.exists():
        raise EnvError(f"rule pack missing: {path}")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            pack = json.load(handle)
    except json.JSONDecodeError as exc:
        raise EnvError(f"invalid rule pack {path}: {exc}")
    pack["name"] = pack.get("name", lang)
    pack.setdefault("rules", [])
    pack.setdefault("compile_failure_patterns", [])
    for rule in pack["rules"]:
        try:
            re.compile(rule["pattern"])
        except (KeyError, re.error) as exc:
            raise EnvError(f"invalid rule in {path}: {rule!r}: {exc}")
    return pack


def _mask_line(line: str, state: Dict[str, Any], pack: Dict[str, Any]) -> List[bool]:
    """Mark positions that are code (not string/comment).

    ``state`` is mutated in place so multi-line strings carry across lines.
    """
    mask = [False] * len(line)
    line_comment = pack.get("line_comment")
    block_start, block_end = (pack.get("block_comment") or [None, None])[:2]
    quotes = pack.get("string_quotes") or ["'", '"']
    triple = bool(pack.get("triple_quotes"))
    multiline_single = set(pack.get("multiline_single_quotes") or [])
    i = 0
    while i < len(line):
        if state.get("block"):
            if block_end and line.startswith(block_end, i):
                state["block"] = False
                i += len(block_end)
            else:
                i += 1
            continue
        quote = state.get("quote")
        if quote:
            token = quote
            if state.get("triple"):
                token = quote * 3
            if line.startswith(token, i):
                state["quote"] = None
                state["triple"] = False
                i += len(token)
                continue
            if line[i] == "\\" and not state.get("triple"):
                i += 2
                continue
            if line[i] == "\\" and state.get("triple"):
                i += 2
                continue
            i += 1
            continue
        if line_comment and line.startswith(line_comment, i):
            break
        if block_start and line.startswith(block_start, i):
            state["block"] = True
            i += len(block_start)
            continue
        matched_triple = False
        if triple:
            for candidate in quotes:
                token = candidate * 3
                if line.startswith(token, i):
                    state["quote"] = candidate
                    state["triple"] = True
                    matched_triple = True
                    i += len(token)
                    break
        if matched_triple:
            continue
        if i < len(line) and line[i] in quotes:
            char = line[i]
            if char in multiline_single:
                state["quote"] = char
                state["triple"] = False
            else:
                # Single-line string: skip to its end on this line.
                j = i + 1
                while j < len(line):
                    if line[j] == "\\":
                        j += 2
                        continue
                    if line[j] == char:
                        j += 1
                        break
                    j += 1
                else:
                    j = len(line)
                i = j
                continue
            i += 1
            continue
        mask[i] = True
        i += 1
    if state.get("quote") and not state.get("triple") and state["quote"] not in multiline_single:
        # An unterminated ordinary quote does not continue to the next line.
        state["quote"] = None
    return mask


def generate_mutations(
    source_path: str,
    pack: Optional[Dict[str, Any]] = None,
    only_lines: Optional[Set[int]] = None,
) -> List[Mutation]:
    if pack is None:
        pack = load_pack(None, source_path)
    try:
        with open(source_path, "r", encoding="utf-8", newline="") as handle:
            lines = handle.readlines()
    except OSError as exc:
        raise EnvError(f"cannot read source {source_path}: {exc}")

    state: Dict[str, Any] = {"block": False, "quote": None, "triple": False}
    mutations: List[Mutation] = []
    seen: Set[Tuple[int, str]] = set()
    skip_prefixes = tuple(pack.get("skip_line_prefixes") or [])

    for line_index, line in enumerate(lines):
        line_no = line_index + 1
        mask = _mask_line(line, state, pack)
        stripped = line.lstrip()
        if skip_prefixes and stripped.startswith(skip_prefixes):
            continue
        if not stripped.strip():
            continue
        if only_lines is not None and line_no not in only_lines:
            continue

        for rule in pack.get("rules", []):
            for match in re.finditer(rule["pattern"], line):
                start, end = match.span()
                if start == end:
                    continue
                if not all(mask[start:end]):
                    continue
                prefix_skip = rule.get("context_skip_prefix")
                if prefix_skip and re.search(prefix_skip, line[:start]):
                    continue
                replacement = match.expand(rule["replacement"])
                mutated = line[:start] + replacement + line[end:]
                if mutated == line:
                    continue
                key = (line_no, mutated)
                if key in seen:
                    continue
                seen.add(key)
                mutations.append(
                    Mutation(
                        file_path=source_path,
                        line_no=line_no,
                        column=start,
                        original_text=line,
                        mutated_text=mutated,
                        description=rule.get("description", "mutation"),
                    )
                )
    return sorted(mutations, key=lambda item: (item.line_no, item.column))


def parse_line_filter(value: Optional[str]) -> Optional[Set[int]]:
    if not value:
        return None
    lines: Set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise UsageError(f"invalid --lines range: {part!r}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start:
            raise UsageError(f"invalid --lines range: {part!r}")
        lines.update(range(start, end + 1))
    return lines


def repo_root_for(path: Path) -> Optional[Path]:
    proc = _git(["rev-parse", "--show-toplevel"], cwd=path.parent if path.parent.exists() else Path.cwd())
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip()).resolve()


def diff_lines(source: Path, base_ref: str) -> Set[int]:
    root = repo_root_for(source)
    if root is None:
        raise UsageError("--diff-base requires the source to be inside a git repository")
    try:
        relative = source.resolve().relative_to(root).as_posix()
    except ValueError:
        raise UsageError("--diff-base requires the source to be inside the repository")
    proc = _git(["diff", "-U0", f"{base_ref}...HEAD", "--", relative], cwd=root)
    if proc.returncode != 0:
        raise UsageError(f"git diff failed for base {base_ref}: {proc.stderr.strip()}")
    pattern = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
    lines: Set[int] = set()
    for match in pattern.finditer(proc.stdout):
        start = int(match.group(1))
        count = 1 if match.group(2) is None else int(match.group(2))
        lines.update(range(start, start + count))
    return lines


def select_mutations(
    mutations: List[Mutation],
    max_mutants: int,
    seed: int,
    spread: bool = False,
) -> List[Mutation]:
    if max_mutants < 0:
        raise UsageError("--max-mutants must be >= 0")
    if len(mutations) <= max_mutants:
        return list(mutations)
    if max_mutants == 0:
        return []
    rng = random.Random(seed)
    if spread:
        by_line: Dict[int, List[Mutation]] = {}
        for mutation in mutations:
            by_line.setdefault(mutation.line_no, []).append(mutation)
        chosen: List[Mutation] = []
        while len(chosen) < max_mutants and by_line:
            for line_no in list(by_line):
                if len(chosen) >= max_mutants:
                    break
                chosen.append(by_line[line_no].pop(0))
                if not by_line[line_no]:
                    del by_line[line_no]
        return sorted(chosen, key=lambda item: (item.line_no, item.column))
    return sorted(rng.sample(mutations, max_mutants), key=lambda item: (item.line_no, item.column))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class SourceGuard:
    def __init__(self, path: Path):
        self.path = path
        self.bak = Path(str(path) + ".mutbak")
        self.lock = Path(str(path) + ".mutlock")
        self._previous: Dict[int, Any] = {}

    def _on_signal(self, signum: int, frame: Any) -> None:
        global _active_pgid
        if _active_pgid:
            try:
                os.killpg(_active_pgid, signal.SIGKILL)
            except OSError:
                pass
        self.restore()
        raise SystemExit(128 + signum)

    def __enter__(self) -> "SourceGuard":
        recover_stale(self.path)
        if self.bak.exists():
            raise EnvError(f"backup already exists for {self.path}; refusing to overwrite it")
        self.bak.write_bytes(self.path.read_bytes())
        self.lock.write_text(str(os.getpid()), encoding="utf-8")
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            self._previous[signum] = signal.signal(signum, self._on_signal)
        atexit.register(self.restore)
        return self

    def restore(self) -> None:
        if self.bak.exists():
            self.path.write_bytes(self.bak.read_bytes())
            self.bak.unlink(missing_ok=True)
            self.lock.unlink(missing_ok=True)

    def __exit__(self, *exc: Any) -> bool:
        self.restore()
        for signum, handler in self._previous.items():
            signal.signal(signum, handler)
        return False


def recover_stale(path: Path) -> bool:
    bak = Path(str(path) + ".mutbak")
    lock = Path(str(path) + ".mutlock")
    if not bak.exists() and not lock.exists():
        return False
    if lock.exists():
        try:
            pid = int(lock.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pid = -1
        if pid > 0 and _pid_alive(pid):
            raise EnvError(f"another mutation run owns {path} (pid {pid})")
    if bak.exists():
        path.write_bytes(bak.read_bytes())
        bak.unlink()
        lock.unlink(missing_ok=True)
        print(f"[!] restored {path} from stale backup", file=sys.stderr)
        return True
    lock.unlink(missing_ok=True)
    return False


def ensure_clean(source: Path, allow_dirty: bool) -> None:
    root = repo_root_for(source)
    if root is None:
        return
    try:
        relative = source.resolve().relative_to(root).as_posix()
    except ValueError:
        return
    proc = _git(["status", "--porcelain", "--", relative], cwd=root)
    if proc.returncode == 0 and proc.stdout.strip():
        if allow_dirty:
            print(f"[warn] source is dirty; --allow-dirty was passed: {source}", file=sys.stderr)
        else:
            raise Fatal(
                f"source has uncommitted changes: {source}\n"
                "commit/stash it, or pass --allow-dirty to mutate the working copy"
            )


def _split_test_cmd(test_cmd: str) -> List[str]:
    try:
        argv = shlex.split(test_cmd)
    except ValueError as exc:
        raise UsageError(f"cannot parse --test-cmd: {exc}")
    if not argv:
        raise UsageError("--test-cmd is empty")
    return argv


def _effective_timeout(
    baseline_elapsed: float,
    timeout: Optional[float],
    timeout_factor: float = 5.0,
    timeout_floor: float = 20.0,
) -> float:
    """Per-mutant cap: an explicit --timeout wins; the default scales off the
    measured baseline instead of a fixed number (timeout_factor x baseline,
    timeout_floor as the minimum, #16). A contended box has slow tests in it —
    a fixed timeout counts them as KILLED by coin flip, which manufactures
    kill rate."""
    if timeout is not None:
        return timeout
    return max(timeout_factor * baseline_elapsed, timeout_floor)


def _bust_pycache(source: Path) -> None:
    """Stale-bytecode trap from the evidence work (#14/PR #28): a same-size
    mutant re-written within one second keeps the (mtime, size) pair a .pyc
    validates against, so a fast test command can execute the *unmutated*
    bytecode and fabricate a survivor. Mirrors evidence._bust_pycache."""
    pycache = source.parent / "__pycache__"
    if pycache.is_dir():
        shutil.rmtree(pycache, ignore_errors=True)


def _resolve_backend(backend: str) -> str:
    # The regex engine ("sregex") is the always-available backend; a stryker
    # adapter ships with this tree and is selected by --backend or by
    # mutation_backend/mutation_backends config (see references/01).
    if backend == "auto":
        print(
            "[*] backend: sregex (auto: no usable external mutator resolved; "
            "the regex engine stays the always-available backend)"
        )
        return "sregex"
    print(f"[*] backend: {backend}")
    return backend


_STRYKER_STATUS = {
    "Killed": "killed",
    "Survived": "survived",
    "NoCoverage": "survived",
    "CompileError": "uncompilable",
    "RuntimeError": "uncompilable",
    "Timeout": "timeout",
}


def run_stryker_backend(
    source_path: str,
    test_cmd: str,
    cmd: Optional[List[str]],
    report_path: str,
    timeout_counts_as: str = "killed",
) -> Dict[str, Any]:
    """External-mutator adapter (stryker): run the configured mutator, read its
    report, and map report statuses onto the shared verdict vocabulary. The
    adapter trusts the mutator's own executions — the regex engine's
    confirmation runs apply only to mutants this engine applies itself."""
    source = Path(source_path)
    if not source.exists():
        raise UsageError(f"source file not found: {source_path}")
    if cmd:
        argv = [str(part).replace("{source}", str(source)) for part in cmd]
        print(f"[*] Running external mutator: {' '.join(argv)}")
        proc = subprocess.run(argv, capture_output=True, text=True)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()[:2000]
            raise EnvError(f"external mutator failed (exit {proc.returncode}): {detail}")
    report_file = Path(report_path.replace("{source}", str(source)))
    if not report_file.exists():
        raise EnvError(
            f"stryker report not found: {report_file} "
            "(configure mutation_backends.stryker.cmd/report or --backend-report)"
        )
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EnvError(f"stryker report is not valid JSON: {exc}")
    files = report.get("files") if isinstance(report, dict) else None
    if not isinstance(files, dict) or not files:
        raise UsageError("stryker report has no files object")
    wanted = str(source)
    entry = files.get(wanted)
    matched_key = wanted
    if entry is None:
        for key, value in files.items():
            if key.endswith(wanted) or wanted.endswith(key):
                entry, matched_key = value, key
                break
    if entry is None:
        raise UsageError(
            f"stryker report has no entry for {wanted} (has: {', '.join(sorted(files))})"
        )

    source_lines = source.read_text(encoding="utf-8", errors="ignore").splitlines()
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "killed": [], "survived": [], "uncompilable": [], "timeout": [],
    }
    for mutant in entry.get("mutants") or []:
        verdict = _STRYKER_STATUS.get(str(mutant.get("status") or ""))
        if verdict is None:
            continue
        location = mutant.get("location") or {}
        start = location.get("start") or {}
        end = location.get("end") or {}
        line_no = int(start.get("line") or 0)
        original_line = (
            source_lines[line_no - 1] if 1 <= line_no <= len(source_lines) else ""
        )
        mutated_line = original_line
        replacement = str(mutant.get("replacement") or "")
        if (
            end.get("line") == start.get("line")
            and isinstance(start.get("column"), int)
            and isinstance(end.get("column"), int)
        ):
            mutated_line = (
                original_line[: start["column"] - 1]
                + replacement
                + original_line[end["column"] - 1:]
            )
        buckets[verdict].append(
            {
                "file": matched_key,
                "line": line_no,
                "column": int(start.get("column") or 0),
                "original": original_line,
                "mutated": mutated_line,
                "description": f"{mutant.get('mutatorName') or 'mutant'} (stryker)",
            }
        )

    timeout_killed = len(buckets["timeout"]) if timeout_counts_as == "killed" else 0
    timeout_survived = len(buckets["timeout"]) if timeout_counts_as == "survived" else 0
    denominator = (
        len(buckets["killed"]) + len(buckets["survived"])
        + timeout_killed + timeout_survived
    )
    kill_rate = (
        (len(buckets["killed"]) + timeout_killed) / denominator * 100.0
        if denominator else None
    )
    counts = {
        "killed": len(buckets["killed"]),
        "survived": len(buckets["survived"]),
        "uncompilable": len(buckets["uncompilable"]),
        "timeout": len(buckets["timeout"]),
        "unverified": 0,
        "total": sum(len(items) for items in buckets.values()),
    }
    _print_summary(
        str(source), buckets["killed"], buckets["survived"],
        buckets["uncompilable"], buckets["timeout"], counts, kill_rate,
    )
    return {
        "source": str(source),
        "language": str(entry.get("language") or "stryker"),
        "backend": "stryker",
        "test_cmd": test_cmd,
        "seed": 0,
        "shard": None,
        "timeout_mode": "external-report",
        "timeout_effective": None,
        "confirm_survivors": 0,
        "cache_hits": 0,
        "counts": counts,
        "kill_rate": kill_rate,
        "timeout_counts_as": timeout_counts_as,
        "killed": buckets["killed"],
        "survived": buckets["survived"],
        "uncompilable": buckets["uncompilable"],
        "timeout": buckets["timeout"],
        "unverified": [],
    }


def _cache_path(source: Path) -> Optional[Path]:
    # Prefer the git root so worktree-relative state lands in one place;
    # fall back beside the source for non-git usage (or mid-setup).
    try:
        stem = repo_root(str(source.parent))
    except EnvError:
        return source.parent / ".autonomous" / "mutant-cache.json"
    return stem / ".autonomous" / "mutant-cache.json"


def _cache_composite(source_bytes: bytes, test_cmd: str) -> str:
    # The (source, test) pair: a verdict is only valid for both together.
    return (
        hashlib.sha256(source_bytes).hexdigest()[:16]
        + ":"
        + hashlib.sha256(test_cmd.encode("utf-8")).hexdigest()[:12]
    )


def _load_cache(path: Optional[Path], composite: str) -> Dict[str, str]:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        print("[warn] verdict cache unreadable; ignoring it for this run")
        return {}
    if not isinstance(data, dict) or data.get("v") != 1:
        return {}
    if data.get("composite") != composite:
        # Source content or test command changed: the cache is stale by design.
        return {}
    entries = data.get("entries")
    return entries if isinstance(entries, dict) else {}


def _save_cache(
    path: Optional[Path], composite: str, entries: Dict[str, str]
) -> None:
    if path is None:
        return
    payload = {"v": 1, "composite": composite, "entries": entries}
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, payload)


def _run_test_cmd(
    argv: List[str], timeout: float
) -> Tuple[int, str, str, float, bool]:
    global _active_pgid
    start = time.time()
    # A same-size mutant applied within the same second keeps the (mtime,
    # size) pair a .pyc validates against; without this, a fast Python test
    # command can execute the unmutated bytecode and report a false survivor.
    try:
        # A mutant rewrite keeps byte-length and mtime-second equal, so a
        # stale pyc (clean or mutant bytecode) can look fresh and the test
        # subprocess will believe it: false survivors, poisoned baselines.
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.Popen(
            argv,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError:
        raise EnvError(f"required tool not found: {argv[0]}")
    # Assign immediately so the signal handler can kill the child's process
    # group if SIGTERM arrives during communicate().
    _active_pgid = proc.pid
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
            return proc.returncode, out or "", err or "", time.time() - start, False
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            out, err = proc.communicate()
            return -9, out or "", err or "", time.time() - start, True
    finally:
        _active_pgid = None


def is_compile_failure(output: str, pack: Dict[str, Any]) -> bool:
    for pattern in pack.get("compile_failure_patterns") or []:
        if re.search(pattern, output, re.MULTILINE):
            return True
    return False


def _print_summary(
    source: str, killed: List[Dict[str, Any]], survived: List[Dict[str, Any]],
    uncompilable: List[Dict[str, Any]], timeouts: List[Dict[str, Any]], counts: Dict[str, int],
    kill_rate: Optional[float],
) -> None:
    print("\n" + "=" * 60)
    print(f"MUTATION TESTING SUMMARY for {source}")
    print(f"Total mutants evaluated: {counts['total']}")
    print(f"Killed:                  {counts['killed']}")
    print(f"Survived:                {counts['survived']}")
    print(f"Uncompilable (excluded): {counts['uncompilable']}")
    print(f"Timed out:               {counts['timeout']}")
    if kill_rate is not None:
        print(f"Mutation kill rate:      {kill_rate:.1f}%")
    if survived:
        print("NOTE: every survivor needs human triage — equivalent mutants and regex noise exist.")

def run_mutation_pass(
    source_path: str,
    test_cmd: str,
    language: Optional[str] = None,
    max_mutants: int = 30,
    timeout: Optional[float] = None,
    baseline_timeout: float = 600.0,
    seed: int = 0,
    shard: Optional[Tuple[int, int]] = None,
    diff_base: Optional[str] = None,
    lines_filter: Optional[str] = None,
    allow_dirty: bool = False,
    timeout_counts_as: str = "killed",
    spread: bool = False,
    timeout_factor: float = 5.0,
    timeout_floor: float = 20.0,
    backend: str = "auto",
    confirm_survivors: int = 1,
    no_cache: bool = False,
) -> Dict[str, Any]:
    source = Path(source_path)
    if not source.exists():
        raise UsageError(f"source file not found: {source_path}")
    pack = load_pack(language, str(source))

    recover_stale(source)
    ensure_clean(source, allow_dirty)
    actual_backend = _resolve_backend(backend)
    # A pyc written before this pass (by an earlier tool run, a doc example,
    # an IDE) stays "fresh" across this pass's same-second rewrites; drop the
    # source's sibling cache so the baseline and every mutant load compile
    # from source. Combined with PYTHONDONTWRITEBYTECODE in _run_test_cmd
    # this closes the stale-bytecode window.
    pycache = source.parent / "__pycache__"
    if pycache.is_dir():
        shutil.rmtree(pycache, ignore_errors=True)
    print(f"[*] Source: {source} (rule pack: {pack['name']})")
    print(f"[*] If interrupted: git checkout -- {source}")

    argv = _split_test_cmd(test_cmd)
    print("[*] Running baseline test command...")
    baseline_rc, baseline_out, baseline_err, baseline_elapsed, baseline_timed_out = _run_test_cmd(
        argv, baseline_timeout
    )
    if baseline_timed_out:
        raise EnvError(f"baseline test command timed out after {baseline_timeout}s")
    if baseline_rc != 0:
        raise EnvError(
            "baseline test command failed; refusing to mutate a red suite\n"
            f"{(baseline_out + baseline_err).strip()[:4000]}"
        )
    print(f"    [+] baseline passed ({baseline_elapsed:.1f}s)")
    # Per-mutant timeout: baseline-relative by default (#16), explicit flag wins.
    effective_timeout = _effective_timeout(
        baseline_elapsed, timeout, timeout_factor, timeout_floor
    )
    timeout_mode = "fixed" if timeout is not None else "baseline-relative"
    print(f"    [+] per-mutant timeout: {effective_timeout:.1f}s ({timeout_mode})")

    only_lines = parse_line_filter(lines_filter)
    if diff_base:
        diff_set = diff_lines(source, diff_base)
        only_lines = diff_set if only_lines is None else (only_lines & diff_set)

    mutations = generate_mutations(str(source), pack=pack, only_lines=only_lines)
    print(f"[*] Generated {len(mutations)} unique candidate mutations")
    if shard:
        index, total = shard
        mutations = [
            item for position, item in enumerate(mutations) if position % total == index - 1
        ]
        print(f"[*] Shard {index}/{total}: {len(mutations)} candidate mutations")
    mutations = select_mutations(mutations, max_mutants, seed, spread)
    print(f"[*] Evaluating {len(mutations)} mutants (seed={seed}, shard={shard or 'all'})")

    # Verdict cache (#16): keyed on the (source bytes, test command) composite
    # plus the mutant id; a survived entry is stored only post-confirmation.
    cache_path = None if no_cache else _cache_path(source)
    _composite = _cache_composite(source.read_bytes(), test_cmd)
    cache: Dict[str, str] = _load_cache(cache_path, _composite)
    cache_hits = 0

    def mutation_cache_key(mutation_obj: "Mutation") -> str:
        return (
            f"{mutation_obj.line_no}:"
            f"{hashlib.sha256(mutation_obj.mutated_text.encode('utf-8')).hexdigest()[:12]}"
        )

    killed: List[Dict[str, Any]] = []
    survived: List[Dict[str, Any]] = []
    unconfirmed: List[Dict[str, Any]] = []
    uncompilable: List[Dict[str, Any]] = []
    timeouts: List[Dict[str, Any]] = []
    unverified: List[Dict[str, Any]] = []

    with SourceGuard(source):
        original = source.read_bytes()
        _bust_pycache(source)
        for position, mutation in enumerate(mutations, start=1):
            cache_key = mutation_cache_key(mutation)
            cached_verdict = cache.get(cache_key)
            if cached_verdict in ("killed", "survived", "uncompilable", "timeout"):
                cache_hits += 1
                print(
                    f"  [{position}/{len(mutations)}] CACHED ({cached_verdict}): "
                    f"line {mutation.line_no}: {mutation.description}"
                )
                verdict_lists = {
                    "killed": killed,
                    "survived": survived,
                    "uncompilable": uncompilable,
                    "timeout": timeouts,
                }
                verdict_lists[cached_verdict].append(mutation.to_dict())
                continue

            lines = original.splitlines(keepends=True)
            lines[mutation.line_no - 1] = mutation.mutated_text.encode("utf-8")
            source.write_bytes(b"".join(
                line if isinstance(line, bytes) else line.encode("utf-8") for line in lines
            ))
            rc, out, err, elapsed, timed_out = _run_test_cmd(argv, effective_timeout)
            source.write_bytes(original)

            if timed_out:
                timeouts.append(mutation.to_dict())
                cache[cache_key] = "timeout"
                print(
                    f"  [{position}/{len(mutations)}] TIMEOUT ({elapsed:.1f}s): "
                    f"line {mutation.line_no}: {mutation.description}"
                )
            elif rc != 0:
                if is_compile_failure(out + "\n" + err, pack):
                    uncompilable.append(mutation.to_dict())
                    cache[cache_key] = "uncompilable"
                    print(
                        f"  [{position}/{len(mutations)}] UNCOMPILABLE ({elapsed:.1f}s): "
                        f"line {mutation.line_no}: {mutation.description}"
                    )
                else:
                    killed.append(mutation.to_dict())
                    cache[cache_key] = "killed"
                    print(
                        f"  [{position}/{len(mutations)}] KILLED ({elapsed:.1f}s): "
                        f"line {mutation.line_no}: {mutation.description}"
                    )
            else:
                # A pass under the mutant is a survivor only when clean source
                # still passes: the confirmation rerun executes the same argv on
                # restored source (#16). A failing/timeout confirm means the
                # suite is flaky or broken on clean source — not work.
                confirmed = True
                if confirm_survivors > 0:
                    for _ in range(confirm_survivors):
                        _bust_pycache(source)
                        crc, _, _, _, ctimed_out = _run_test_cmd(argv, effective_timeout)
                        if ctimed_out or crc != 0:
                            confirmed = False
                            break
                if confirmed:
                    survived.append(mutation.to_dict())
                    cache[cache_key] = "survived"
                    print(
                        f"  [{position}/{len(mutations)}] SURVIVED: line {mutation.line_no}: "
                        f"{mutation.original_text.strip()} -> {mutation.mutated_text.strip()} "
                        f"({mutation.description})"
                    )
                else:
                    unverified.append(
                        {
                            **mutation.to_dict(),
                            "reason": (
                                "clean-source confirmation run failed; the suite is "
                                "unreliable, so the mutant pass is a coin flip (#16)"
                            ),
                        }
                    )
                    print(
                        f"  [{position}/{len(mutations)}] UNVERIFIED: line {mutation.line_no}: "
                        f"{mutation.description} (clean-source confirmation failed)"
                    )

        if cache_path is not None:
            _save_cache(cache_path, _composite, cache)

    timeout_killed = len(timeouts) if timeout_counts_as == "killed" else 0
    timeout_survived = len(timeouts) if timeout_counts_as == "survived" else 0
    denominator = len(killed) + len(survived) + timeout_killed + timeout_survived
    kill_rate = ((len(killed) + timeout_killed) / denominator * 100.0) if denominator else None
    counts = {
        "killed": len(killed),
        "survived": len(survived),
        "uncompilable": len(uncompilable),
        "timeout": len(timeouts),
        "unverified": len(unverified),
        "total": len(mutations),
    }
    _print_summary(
        str(source), killed, survived, uncompilable, timeouts, counts, kill_rate
    )
    if unverified:
        print(
            f"Unverified (confirm failed): {len(unverified)} — not reported as work"
        )

    return {
        "source": str(source),
        "language": pack["name"],
        "backend": actual_backend,
        "test_cmd": test_cmd,
        "seed": seed,
        "shard": {"index": shard[0], "total": shard[1]} if shard else None,
        "timeout_mode": timeout_mode,
        "timeout_effective": round(effective_timeout, 3),
        "confirm_survivors": confirm_survivors,
        "cache_hits": cache_hits,
        "counts": counts,
        "kill_rate": kill_rate,
        "timeout_counts_as": timeout_counts_as,
        "killed": killed,
        "survived": survived,
        "uncompilable": uncompilable,
        "timeout": timeouts,
        "unverified": unverified,
    }


def format_issue_markdown(surviving_mutant: Dict[str, Any], test_cmd: str) -> str:
    file = surviving_mutant["file"]
    line = surviving_mutant["line"]
    original = surviving_mutant["original"]
    mutated = surviving_mutant["mutated"]
    description = surviving_mutant["description"]
    return f"""### Test gap: surviving mutant in `{file}:{line}`

#### Mutation

{description}

```diff
--- {file} (line {line})
+++ mutated
- {original}
+ {mutated}
```

The targeted test command passed with this mutant applied:

```bash
{test_cmd}
```

#### Required proof (fail-to-pass)

1. Triage first: confirm this is not an equivalent mutant, string/comment
   noise, or a regex false positive. Close the issue if it is.
2. Add a test that is **red with this mutant applied** and **green on clean
   source**. The test does not need to be red on `origin/main` — the defect
   here is the mutant, not a bug in the repository.
3. Run the targeted test once with the mutant and once without it; paste both
   results into the PR body.
4. Keep the PR to the source-adjacent test file plus any fixture it needs.
"""


def _parse_shard(value: Optional[str]) -> Optional[Tuple[int, int]]:
    if not value:
        return None
    match = re.fullmatch(r"(\d+)/(\d+)", value.strip())
    if not match:
        raise UsageError("--shard must look like i/N (e.g. 2/4)")
    index, total = int(match.group(1)), int(match.group(2))
    if total < 1 or index < 1 or index > total:
        raise UsageError("--shard requires 1 <= i <= N")
    return index, total


# --- External backends (issue #16) -----------------------------------------
# The regex engine stays the guaranteed-available backend. External mutators
# normalize into the same verdict schema, so triage/ledger/evidence flows do
# not change. Supported today: Stryker's mutation-report JSON. Configure via
# config `mutation_backend` + `mutation_backends.<name>.{cmd,report}` (argv
# array, `{source}` substituted) or --backend-cmd/--backend-report.

_STRYKER_STATUS = {
    "Killed": "killed",
    "Survived": "survived",
    "NoCoverage": "survived",
    "CompileError": "uncompilable",
    "RuntimeError": "uncompilable",
    "Timeout": "timeout",
}


def run_stryker_backend(
    source_path: str,
    test_cmd: str,
    cmd: Optional[List[str]],
    report_path: str,
    timeout_counts_as: str = "killed",
) -> Dict[str, Any]:
    source = Path(source_path)
    if not source.exists():
        raise UsageError(f"source file not found: {source_path}")
    if cmd:
        argv = [str(part).replace("{source}", str(source)) for part in cmd]
        print(f"[*] Running external mutator: {' '.join(argv)}")
        proc = subprocess.run(argv, capture_output=True, text=True)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()[:2000]
            raise EnvError(f"external mutator failed (exit {proc.returncode}): {detail}")
    report_file = Path(report_path.replace("{source}", str(source)))
    if not report_file.exists():
        raise EnvError(
            f"stryker report not found: {report_file} "
            "(configure mutation_backends.stryker.cmd/report or --backend-report)"
        )
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EnvError(f"stryker report is not valid JSON: {exc}")
    files = report.get("files") if isinstance(report, dict) else None
    if not isinstance(files, dict) or not files:
        raise UsageError("stryker report has no files object")
    wanted = str(source)
    entry = files.get(wanted)
    matched_key = wanted
    if entry is None:
        for key, value in files.items():
            if key.endswith(wanted) or wanted.endswith(key):
                entry, matched_key = value, key
                break
    if entry is None:
        raise UsageError(
            f"stryker report has no entry for {wanted} (has: {', '.join(sorted(files))})"
        )

    source_lines = source.read_text(encoding="utf-8", errors="ignore").splitlines()
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "killed": [], "survived": [], "uncompilable": [], "timeout": [],
    }
    for mutant in entry.get("mutants") or []:
        verdict = _STRYKER_STATUS.get(str(mutant.get("status") or ""))
        if verdict is None:
            continue
        location = mutant.get("location") or {}
        start = location.get("start") or {}
        end = location.get("end") or {}
        line_no = int(start.get("line") or 0)
        original_line = (
            source_lines[line_no - 1] if 1 <= line_no <= len(source_lines) else ""
        )
        mutated_line = original_line
        replacement = str(mutant.get("replacement") or "")
        if (
            end.get("line") == start.get("line")
            and isinstance(start.get("column"), int)
            and isinstance(end.get("column"), int)
        ):
            mutated_line = (
                original_line[: start["column"] - 1]
                + replacement
                + original_line[end["column"] - 1:]
            )
        buckets[verdict].append(
            {
                "file": matched_key,
                "line": line_no,
                "column": int(start.get("column") or 0),
                "original": original_line,
                "mutated": mutated_line,
                "description": f"{mutant.get('mutatorName') or 'mutant'} (stryker)",
            }
        )

    timeout_killed = len(buckets["timeout"]) if timeout_counts_as == "killed" else 0
    timeout_survived = len(buckets["timeout"]) if timeout_counts_as == "survived" else 0
    denominator = (
        len(buckets["killed"]) + len(buckets["survived"])
        + timeout_killed + timeout_survived
    )
    kill_rate = (
        (len(buckets["killed"]) + timeout_killed) / denominator * 100.0
        if denominator else None
    )
    counts = {
        "killed": len(buckets["killed"]),
        "survived": len(buckets["survived"]),
        "unconfirmed": 0,
        "uncompilable": len(buckets["uncompilable"]),
        "timeout": len(buckets["timeout"]),
        "total": sum(len(items) for items in buckets.values()),
    }
    _print_summary(
        str(source), buckets["killed"], buckets["survived"],
        buckets["uncompilable"], buckets["timeout"], counts, kill_rate,
    )
    return {
        "source": str(source),
        "backend": "stryker",
        "language": str(entry.get("language") or "external"),
        "test_cmd": test_cmd,
        "seed": 0,
        "shard": None,
        "counts": counts,
        "kill_rate": kill_rate,
        "timeout_counts_as": timeout_counts_as,
        "timeout_effective_s": None,
        "baseline_s": None,
        "cache_hits": 0,
        "killed": buckets["killed"],
        "survived": buckets["survived"],
        "unconfirmed": [],
        "uncompilable": buckets["uncompilable"],
        "timeout": buckets["timeout"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Regex-based mutation engine with per-language rule packs (triage required)"
    )
    parser.add_argument("--source", required=True, help="source file to mutate")
    parser.add_argument("--test-cmd", required=True,
                        help="targeted test command (parsed with shlex; no shell). "
                             "Quote inner arguments as you would in a shell: "
                             "\"pytest -k 'test_foo and test_bar'\".")
    parser.add_argument("--lang", help="rule pack language (default: from file extension)")
    parser.add_argument("--max-mutants", type=int, default=30, help="maximum mutants to evaluate")
    parser.add_argument("--seed", type=int, default=0, help="sampling seed (recorded in JSON)")
    parser.add_argument("--shard", help="deterministic shard i/N; use separate worktrees")
    parser.add_argument("--diff-base", help="only mutate lines changed in <ref>...HEAD")
    parser.add_argument("--lines", help="only mutate these line ranges, e.g. 10-20,42")
    parser.add_argument("--allow-dirty", action="store_true", help="allow a dirty source file")
    parser.add_argument(
        "--backend",
        choices=["auto", "sregex", "stryker"],
        default="auto",
        help="mutator backend: auto resolves to a configured external mutator "
        "when its command is on PATH, otherwise the always-available regex "
        "engine (see references/01)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="fixed per-mutant timeout in seconds "
        "(default: baseline-relative, see --timeout-factor/--timeout-floor)",
    )
    parser.add_argument("--timeout-factor", type=float, default=5.0,
                        help="per-mutant timeout = factor x baseline (default: 5)")
    parser.add_argument("--timeout-floor", type=float, default=20.0,
                        help="minimum per-mutant timeout in seconds (default: 20)")
    parser.add_argument("--backend-cmd", help="external mutator command ({source} substituted)")
    parser.add_argument("--backend-report", help="external mutator report path")
    parser.add_argument("--baseline-timeout", type=float, default=600.0, help="baseline timeout in seconds")
    parser.add_argument(
        "--confirm-survivors",
        type=int,
        default=1,
        help="clean-source confirmation runs a survivor must pass before being reported (0 disables)",
    )
    parser.add_argument("--no-cache", action="store_true", help="skip the verdict cache for this run")
    parser.add_argument(
        "--timeout-counts-as",
        choices=["killed", "survived", "excluded"],
        default="killed",
        help="how timeouts affect the kill rate",
    )
    parser.add_argument("--spread", action="store_true", help="round-robin across lines before sampling")
    parser.add_argument("--fail-on-survivors", action="store_true", help="exit 1 when survivors exist")
    parser.add_argument("--output-json", help="write full results as JSON")
    parser.add_argument("--output-issue", help="write the first surviving mutant as markdown")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        shard = _parse_shard(args.shard)
        cfg: Dict[str, Any] = {}
        try:
            cfg = load_config(repo_root())
        except Exception:
            cfg = {}
        specs = cfg.get("mutation_backends") or {}
        spec = specs.get("stryker") if isinstance(specs, dict) else None
        spec = spec if isinstance(spec, dict) else {}
        backend = args.backend
        if backend == "auto":
            configured_cmd = spec.get("cmd") if cfg.get("mutation_backend") == "stryker" else None
            if configured_cmd and shutil.which(str(configured_cmd[0])):
                backend = "stryker"
            # no usable external mutator: auto stays auto and run_mutation_pass
            # resolves it to the regex engine (and says so).
        if backend == "stryker":
            cmd: Optional[List[str]] = None
            if args.backend_cmd:
                cmd = _split_test_cmd(args.backend_cmd)
            elif spec.get("cmd"):
                cmd = [str(part) for part in spec["cmd"]]
            report = (
                args.backend_report or spec.get("report")
                or "reports/mutation/mutation.json"
            )
            results = run_stryker_backend(
                source_path=args.source,
                test_cmd=args.test_cmd,
                cmd=cmd,
                report_path=str(report),
                timeout_counts_as=args.timeout_counts_as,
            )
        else:
            results = run_mutation_pass(
                source_path=args.source,
                test_cmd=args.test_cmd,
                language=args.lang,
                max_mutants=args.max_mutants,
                timeout=args.timeout,
                baseline_timeout=args.baseline_timeout,
                seed=args.seed,
                shard=shard,
                diff_base=args.diff_base,
                lines_filter=args.lines,
                allow_dirty=args.allow_dirty,
                timeout_counts_as=args.timeout_counts_as,
                spread=args.spread,
                timeout_factor=args.timeout_factor,
                timeout_floor=args.timeout_floor,
                backend=backend,
                confirm_survivors=args.confirm_survivors,
                no_cache=args.no_cache,
            )
        if args.output_json:
            path = Path(args.output_json)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(results, handle, indent=2)
                handle.write("\n")
            print(f"[+] Saved results to {path}")
        if args.output_issue and results["survived"]:
            path = Path(args.output_issue)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(format_issue_markdown(results["survived"][0], args.test_cmd))
            print(f"[+] Saved issue draft to {path}")
        if args.fail_on_survivors and results["survived"]:
            return EXIT_NEGATIVE
        return EXIT_OK
    except UsageError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return EXIT_USAGE
    except EnvError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return EXIT_ENV
    except Fatal as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return EXIT_NEGATIVE
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
