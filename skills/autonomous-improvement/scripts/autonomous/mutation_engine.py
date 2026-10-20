#!/usr/bin/env python3
"""Regex-based mutation engine with per-language rule packs.

This is intentionally not an AST mutator. It mutates one source line at a time,
runs one targeted test command per mutant, and reports four states:

  KILLED        tests failed (the mutant is caught)
  SURVIVED      tests passed (a proven gap, subject to human triage)
  UNCOMPILABLE  an anchored compiler-error pattern matched
  TIMEOUT       the test command exceeded --timeout

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
import json
import os
import random
import re
import shlex
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


def _run_test_cmd(
    argv: List[str], timeout: float
) -> Tuple[int, str, str, float, bool]:
    global _active_pgid
    start = time.time()
    try:
        proc = subprocess.Popen(
            argv,
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
    timeout: float = 60.0,
    baseline_timeout: float = 600.0,
    seed: int = 0,
    shard: Optional[Tuple[int, int]] = None,
    diff_base: Optional[str] = None,
    lines_filter: Optional[str] = None,
    allow_dirty: bool = False,
    timeout_counts_as: str = "killed",
    spread: bool = False,
) -> Dict[str, Any]:
    source = Path(source_path)
    if not source.exists():
        raise UsageError(f"source file not found: {source_path}")
    pack = load_pack(language, str(source))

    recover_stale(source)
    ensure_clean(source, allow_dirty)
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

    killed: List[Dict[str, Any]] = []
    survived: List[Dict[str, Any]] = []
    uncompilable: List[Dict[str, Any]] = []
    timeouts: List[Dict[str, Any]] = []

    with SourceGuard(source):
        original = source.read_bytes()
        for position, mutation in enumerate(mutations, start=1):
            lines = original.splitlines(keepends=True)
            lines[mutation.line_no - 1] = mutation.mutated_text.encode("utf-8")
            source.write_bytes(b"".join(
                line if isinstance(line, bytes) else line.encode("utf-8") for line in lines
            ))
            rc, out, err, elapsed, timed_out = _run_test_cmd(argv, timeout)
            source.write_bytes(original)

            if timed_out:
                timeouts.append(mutation.to_dict())
                print(
                    f"  [{position}/{len(mutations)}] TIMEOUT ({elapsed:.1f}s): "
                    f"line {mutation.line_no}: {mutation.description}"
                )
            elif rc != 0:
                if is_compile_failure(out + "\n" + err, pack):
                    uncompilable.append(mutation.to_dict())
                    print(
                        f"  [{position}/{len(mutations)}] UNCOMPILABLE ({elapsed:.1f}s): "
                        f"line {mutation.line_no}: {mutation.description}"
                    )
                else:
                    killed.append(mutation.to_dict())
                    print(
                        f"  [{position}/{len(mutations)}] KILLED ({elapsed:.1f}s): "
                        f"line {mutation.line_no}: {mutation.description}"
                    )
            else:
                survived.append(mutation.to_dict())
                print(
                    f"  [{position}/{len(mutations)}] SURVIVED: line {mutation.line_no}: "
                    f"{mutation.original_text.strip()} -> {mutation.mutated_text.strip()} "
                    f"({mutation.description})"
                )

    timeout_killed = len(timeouts) if timeout_counts_as == "killed" else 0
    timeout_survived = len(timeouts) if timeout_counts_as == "survived" else 0
    denominator = len(killed) + len(survived) + timeout_killed + timeout_survived
    kill_rate = ((len(killed) + timeout_killed) / denominator * 100.0) if denominator else None
    counts = {
        "killed": len(killed),
        "survived": len(survived),
        "uncompilable": len(uncompilable),
        "timeout": len(timeouts),
        "total": len(mutations),
    }
    _print_summary(str(source), killed, survived, uncompilable, timeouts, counts, kill_rate)

    return {
        "source": str(source),
        "language": pack["name"],
        "test_cmd": test_cmd,
        "seed": seed,
        "shard": {"index": shard[0], "total": shard[1]} if shard else None,
        "counts": counts,
        "kill_rate": kill_rate,
        "timeout_counts_as": timeout_counts_as,
        "killed": killed,
        "survived": survived,
        "uncompilable": uncompilable,
        "timeout": timeouts,
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
    parser.add_argument("--timeout", type=float, default=60.0, help="per-mutant timeout in seconds")
    parser.add_argument("--baseline-timeout", type=float, default=600.0, help="baseline timeout in seconds")
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
