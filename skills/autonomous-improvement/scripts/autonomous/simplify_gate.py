#!/usr/bin/env python3
"""Stream J finder + oracle: simplification guarded by mutation score.

The finder is deliberately humble. It emits *candidates* for dead Python:
statements after a block terminator and private module-level definitions
with no textual fan-in (and no coverage evidence of being exercised when a
coverage.py JSON report is supplied). It never deletes anything and never
claims a symbol is dead merely because static analysis cannot see a caller.

The oracle is the part that may authorize a deletion. It refuses a change
unless the current targeted tests are green, no tracked test file was
deleted, and the source's mutation kill rate after the change is no lower
than before it. It also refuses a result in which a previously killed
mutant survives, and allows the mutant denominator to shrink only for
mutants whose original lines were actually removed.

Usage:
  python3 scripts/autonomous/simplify_gate.py finder [paths...] [--json]
  python3 scripts/autonomous/simplify_gate.py compare \
      --before-json before.json --after-json after.json
  python3 scripts/autonomous/simplify_gate.py oracle \
      --source src/app.py --base HEAD --test-cmd '<targeted tests>'
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from common import (
    EXIT_NEGATIVE,
    EXIT_OK,
    EnvError,
    Fatal,
    UsageError,
    candidate_paths,
    format_exit,
    glob_match,
    git,
    load_config,
    ls_files,
    rel_path,
    repo_root,
    run,
)
from test_weakening_check import DEFAULT_TEST_GLOBS, _test_globs

DYNAMIC_RE = re.compile(
    r"(?:\bgetattr\s*\(|\bsetattr\s*\(|\bglobals\s*\(|\blocals\s*\(|"
    r"\bvars\s*\(|\b(?:eval|exec)\s*\(|\bimportlib\b|__dict__\s*\[|"
    r"\bentry_points\b|\bregister\s*\()"
)
TERMINATORS = (ast.Return, ast.Raise, ast.Break, ast.Continue)


# ---------------------------------------------------------------------------
# Finder
# ---------------------------------------------------------------------------


def _display_path(root: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        try:
            relative = os.path.relpath(resolved, Path.cwd().resolve())
        except ValueError:
            return str(resolved)
        return relative if not relative.startswith(".." + os.sep) else str(resolved)


def _resolve_input(root: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    from_cwd = Path.cwd() / path
    return from_cwd if from_cwd.exists() else root / path


def _collect_targets(
    root: Path, cfg: Dict[str, Any], raw_paths: Sequence[str]
) -> List[Path]:
    files: List[Path] = []
    seen: Set[Path] = set()

    def add(path: Path) -> None:
        resolved = path.resolve()
        if resolved.suffix == ".py" and resolved not in seen:
            seen.add(resolved)
            files.append(resolved)

    if not raw_paths:
        for relative in candidate_paths(root, cfg, "J"):
            path = root / relative
            if path.exists():
                add(path)
        return files

    for raw in raw_paths:
        path = _resolve_input(root, raw)
        if path.is_dir():
            for child in sorted(path.rglob("*.py")):
                if child.is_file():
                    add(child)
        elif path.exists():
            if path.suffix != ".py":
                raise UsageError(f"Stream J finder v1 supports Python files only: {raw}")
            add(path)
        else:
            raise Fatal(f"path not found: {raw}")
    return files


def _collect_scan_files(
    root: Path, scan_roots: Sequence[str], targets: Iterable[Path]
) -> Dict[Path, str]:
    paths: Set[Path] = {path.resolve() for path in targets}
    ignored_parts = {
        ".git",
        ".mypy_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
    if not scan_roots:
        # The default fan-in universe is the tracked repo, not every Python
        # file that happens to be under the checkout (virtualenvs, build
        # output, or hook samples would make the scan slow and noisy).
        for relative in ls_files(root):
            if relative.endswith(".py"):
                paths.add((root / relative).resolve())
    else:
        roots = [_resolve_input(root, raw) for raw in scan_roots]
        for scan_root in roots:
            if scan_root.is_dir():
                for child in scan_root.rglob("*.py"):
                    if child.is_file() and not ignored_parts.intersection(child.parts):
                        paths.add(child.resolve())
            elif scan_root.is_file() and scan_root.suffix == ".py":
                paths.add(scan_root.resolve())
            else:
                raise Fatal(f"scan root not found or not Python: {scan_root}")

    texts: Dict[Path, str] = {}
    for path in sorted(paths):
        try:
            texts[path] = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    return texts


def _load_coverage(raw: Optional[str], root: Path) -> Dict[str, Dict[str, Any]]:
    if not raw:
        return {}
    path = _resolve_input(root, raw)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UsageError(f"cannot read coverage JSON {path}: {exc}")
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        raise UsageError("coverage JSON must be a coverage.py report with a 'files' object")
    return {
        str(name).replace("\\", "/"): value
        for name, value in files.items()
        if isinstance(value, dict)
    }


def _coverage_entry(
    coverage: Dict[str, Dict[str, Any]], root: Path, path: Path
) -> Optional[Dict[str, Any]]:
    if not coverage:
        return None
    absolute = path.resolve().as_posix()
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = path.name

    best: Optional[Tuple[int, Dict[str, Any]]] = None
    for name, entry in coverage.items():
        normalized = name.lstrip("./")
        if (
            normalized in (absolute, relative)
            or absolute.endswith("/" + normalized)
            or normalized.endswith("/" + relative)
        ):
            score = len(normalized)
            if best is None or score > best[0]:
                best = (score, entry)
    return best[1] if best else None


def _line_set(entry: Optional[Dict[str, Any]], key: str) -> Set[int]:
    if not entry:
        return set()
    values = entry.get(key) or []
    return {int(value) for value in values if isinstance(value, (int, float))}


def _exported_names(tree: ast.Module) -> Set[str]:
    exported: Set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            continue
        value = node.value
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            for element in value.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    exported.add(element.value)
    return exported


def _finding(
    display: str,
    line: int,
    kind: str,
    text: str,
    evidence: str,
    **extra: Any,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "file": display,
        "line": line,
        "kind": kind,
        "text": text.strip()[:200],
        "evidence": evidence,
    }
    result.update(extra)
    return result


def _unreachable_findings(
    display: str, tree: ast.AST, lines: Sequence[str]
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        for _field, value in ast.iter_fields(node):
            if not isinstance(value, list) or not value:
                continue
            if not all(isinstance(item, ast.stmt) for item in value):
                continue
            terminator: Optional[ast.stmt] = None
            for statement in value:
                if terminator is not None:
                    line = getattr(statement, "lineno", 0) or 0
                    text = lines[line - 1] if 0 < line <= len(lines) else ""
                    findings.append(
                        _finding(
                            display,
                            line,
                            "unreachable",
                            text,
                            f"statement follows {type(terminator).__name__.lower()} "
                            f"at line {getattr(terminator, 'lineno', '?')}",
                        )
                    )
                    break
                if isinstance(statement, TERMINATORS):
                    terminator = statement
    return findings


def _body_lines(node: ast.AST) -> Set[int]:
    # Walk body statements only. The ``def`` line/arguments execute at import
    # time even when the function is never called, so including them would
    # make coverage suppress precisely the unused functions we are hunting.
    lines: Set[int] = set()
    for statement in getattr(node, "body", []):
        for child in ast.walk(statement):
            line = getattr(child, "lineno", None)
            if isinstance(line, int):
                lines.add(line)
    return lines


def _private_findings(
    display: str,
    tree: ast.Module,
    source: str,
    lines: Sequence[str],
    scan_texts: Iterable[str],
    coverage_entry: Optional[Dict[str, Any]],
    coverage_supplied: bool,
) -> List[Dict[str, Any]]:
    # Dynamic dispatch makes a zero-hit search materially less trustworthy.
    # A name in a string would already count as fan-in, but computed names
    # would not; suppress the entire private-symbol heuristic for such files.
    if DYNAMIC_RE.search(source):
        return []

    exported = _exported_names(tree)
    definitions: Dict[str, int] = {}
    candidates: List[Any] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        definitions[node.name] = definitions.get(node.name, 0) + 1
        if (
            node.name.startswith("_")
            and not (node.name.startswith("__") and node.name.endswith("__"))
            and node.name not in exported
            and not getattr(node, "decorator_list", [])
        ):
            candidates.append(node)

    executed = _line_set(coverage_entry, "executed_lines")
    missing = _line_set(coverage_entry, "missing_lines")
    findings: List[Dict[str, Any]] = []
    joined_scan_texts = list(scan_texts)
    for node in candidates:
        pattern = re.compile(rf"(?<!\w){re.escape(node.name)}(?!\w)")
        occurrences = sum(len(pattern.findall(text)) for text in joined_scan_texts)
        references = occurrences - definitions.get(node.name, 0)
        if references > 0:
            continue

        zero_coverage: Optional[bool] = None
        if coverage_supplied:
            body_lines = _body_lines(node)
            measured = body_lines & (executed | missing)
            if body_lines & executed:
                continue
            zero_coverage = bool(measured) and measured <= missing

        line = int(getattr(node, "lineno", 0) or 0)
        text = lines[line - 1] if 0 < line <= len(lines) else node.name
        evidence = "private module-level symbol has zero textual fan-in in scanned Python"
        if zero_coverage:
            evidence += "; measured body lines are all missing from coverage"
        elif coverage_supplied:
            evidence += "; coverage did not prove the body either way"
        findings.append(
            _finding(
                display,
                line,
                "zero-fanin-private",
                text,
                evidence,
                symbol=node.name,
                fan_in=0,
                zero_coverage=zero_coverage,
            )
        )
    return findings


def find_candidates(
    root: Path,
    targets: Sequence[Path],
    scan_texts: Dict[Path, str],
    coverage: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    all_texts = list(scan_texts.values())
    for path in targets:
        source = scan_texts.get(path.resolve())
        if source is None:
            try:
                source = path.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                raise Fatal(f"cannot read {path}: {exc}")
        display = _display_path(root, path)
        lines = source.splitlines()
        try:
            tree = ast.parse(source, filename=display)
        except SyntaxError:
            continue
        findings.extend(_unreachable_findings(display, tree, lines))
        if isinstance(tree, ast.Module):
            findings.extend(
                _private_findings(
                    display,
                    tree,
                    source,
                    lines,
                    all_texts,
                    _coverage_entry(coverage, root, path),
                    bool(coverage),
                )
            )
    unique: Dict[Tuple[str, int, str, str], Dict[str, Any]] = {}
    for finding in findings:
        key = (
            str(finding["file"]),
            int(finding["line"]),
            str(finding["kind"]),
            str(finding.get("symbol", "")),
        )
        unique.setdefault(key, finding)
    return sorted(
        unique.values(), key=lambda item: (item["file"], item["line"], item["kind"])
    )


# ---------------------------------------------------------------------------
# Mutation-report comparison
# ---------------------------------------------------------------------------


def _bucket(result: Dict[str, Any], name: str) -> List[Dict[str, Any]]:
    direct = result.get(name)
    if isinstance(direct, list):
        return [item for item in direct if isinstance(item, dict)]
    mutants = result.get("mutants")
    if isinstance(mutants, dict) and isinstance(mutants.get(name), list):
        return [item for item in mutants[name] if isinstance(item, dict)]
    return []


def _count(result: Dict[str, Any], name: str) -> int:
    counts = result.get("counts")
    if isinstance(counts, dict) and isinstance(counts.get(name), (int, float)):
        return int(counts[name])
    return len(_bucket(result, name))


def _timeout_policy(result: Dict[str, Any]) -> str:
    policy = str(result.get("timeout_counts_as") or "killed").lower()
    return policy if policy in {"killed", "survived", "excluded"} else "killed"


def score_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        raise UsageError("mutation report must be a JSON object")
    killed = _count(result, "killed")
    survived = _count(result, "survived")
    timeout = _count(result, "timeout")
    unconfirmed = _count(result, "unconfirmed")
    uncompilable = _count(result, "uncompilable")
    total = _count(result, "total") or (
        killed + survived + timeout + unconfirmed + uncompilable
    )
    policy = _timeout_policy(result)
    denominator = killed + survived + (timeout if policy != "excluded" else 0)
    numerator = killed + (timeout if policy == "killed" else 0)
    return {
        "source": result.get("source"),
        "test_cmd": result.get("test_cmd"),
        "counts": {
            "total": total,
            "killed": killed,
            "survived": survived,
            "timeout": timeout,
            "unconfirmed": unconfirmed,
            "uncompilable": uncompilable,
        },
        "denominator": denominator,
        "kill_rate": round(numerator / denominator, 6) if denominator else None,
        "timeout_counts_as": policy,
    }


def _normalized_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _mutant_key(result: Dict[str, Any], mutant: Dict[str, Any]) -> Tuple[str, str, str, str]:
    file_value = str(mutant.get("file") or result.get("source") or "").replace("\\", "/")
    return (
        Path(file_value).name,
        _normalized_line(mutant.get("original")),
        _normalized_line(mutant.get("mutated")),
        _normalized_line(mutant.get("description")),
    )


def _render_key(key: Tuple[str, str, str, str]) -> str:
    file_name, original, mutated, description = key
    return f"{file_name}: {original!r} -> {mutated!r} ({description})"


def _same_source(before: Any, after: Any) -> bool:
    if not before or not after:
        return True
    def tail(value: Any) -> str:
        parts = [part for part in str(value).replace("\\", "/").split("/") if part]
        return "/".join(parts[-2:]) if len(parts) >= 2 else (parts[0] if parts else "")

    return tail(before) == tail(after)


def denominator_mutants(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    mutants = _bucket(result, "killed") + _bucket(result, "survived")
    if _timeout_policy(result) != "excluded":
        mutants += _bucket(result, "timeout")
    return mutants


def compare_results(
    before: Dict[str, Any],
    after: Dict[str, Any],
    allow_denominator_drop: int = 0,
) -> Dict[str, Any]:
    if allow_denominator_drop < 0:
        raise UsageError("--allow-denominator-drop cannot be negative")
    before_summary = score_summary(before)
    after_summary = score_summary(after)
    reasons: List[str] = []

    if not _same_source(before_summary["source"], after_summary["source"]):
        reasons.append("mutation reports do not describe the same source file")
    if (
        before_summary["test_cmd"]
        and after_summary["test_cmd"]
        and before_summary["test_cmd"] != after_summary["test_cmd"]
    ):
        reasons.append("mutation reports used different test commands")
    if before_summary["timeout_counts_as"] != after_summary["timeout_counts_as"]:
        reasons.append("mutation reports used different timeout scoring policies")

    for label, summary in (("before", before_summary), ("after", after_summary)):
        if summary["denominator"] == 0:
            reasons.append(f"{label} mutation score is inconclusive: denominator is zero")
        if summary["counts"]["unconfirmed"]:
            reasons.append(
                f"{label} mutation result has {summary['counts']['unconfirmed']} "
                "unconfirmed survivor(s); rerun before trusting the score"
            )

    before_score = before_summary["kill_rate"]
    after_score = after_summary["kill_rate"]
    if (
        isinstance(before_score, (int, float))
        and isinstance(after_score, (int, float))
        and after_score + 1e-9 < before_score
    ):
        reasons.append(
            "mutation score is lower after the change: "
            f"{before_score:.1%} -> {after_score:.1%}"
        )

    denominator_drop = before_summary["denominator"] - after_summary["denominator"]
    if denominator_drop > allow_denominator_drop:
        reasons.append(
            "mutation denominator shrank by "
            f"{denominator_drop}, beyond the {allow_denominator_drop} mutant(s) "
            "attributable to removed lines"
        )

    killed_before = {_mutant_key(before, mutant) for mutant in _bucket(before, "killed")}
    survived_after = {_mutant_key(after, mutant) for mutant in _bucket(after, "survived")}
    regressed = sorted(killed_before & survived_after)
    if regressed:
        reasons.append(
            f"{len(regressed)} previously killed mutant(s) survive after the change"
        )

    return {
        "schema": 1,
        "stream": "J",
        "verdict": "accepted" if not reasons else "refused",
        "before": before_summary,
        "after": after_summary,
        "allow_denominator_drop": allow_denominator_drop,
        "regressed_mutants": [_render_key(key) for key in regressed],
        "reasons": reasons,
    }


def _load_report(raw: str, root: Path) -> Dict[str, Any]:
    path = _resolve_input(root, raw)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UsageError(f"cannot read mutation report {path}: {exc}")
    if not isinstance(data, dict):
        raise UsageError(f"mutation report must be a JSON object: {path}")
    return data


# ---------------------------------------------------------------------------
# Full oracle
# ---------------------------------------------------------------------------


def _split_cmd(command: str) -> List[str]:
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise UsageError(f"cannot parse --test-cmd: {exc}")
    if not argv:
        raise UsageError("--test-cmd is empty")
    return argv


def _run_test_cmd(command: str, cwd: Path, timeout: float) -> Dict[str, Any]:
    argv = _split_cmd(command)
    started = time.time()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise EnvError(f"required tool not found: {argv[0]}") from exc

    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        stdout, stderr = proc.communicate()
        exit_code = 124

    lines = [line.strip() for line in (stderr or stdout or "").splitlines() if line.strip()]
    return {
        "cmd": command,
        "exit": exit_code,
        "seconds": round(time.time() - started, 3),
        "timed_out": timed_out,
        "summary": lines[-1][:300] if lines else "",
    }


def _deleted_test_files(root: Path, base: str, cfg: Dict[str, Any]) -> List[str]:
    proc = git(
        ["diff", "--name-only", "-z", "--diff-filter=D", base, "--"], cwd=root
    )
    if proc.returncode != 0:
        raise UsageError(f"cannot diff against {base}: {proc.stderr.strip()}")
    deleted = [part for part in proc.stdout.split("\0") if part]
    globs = _test_globs(cfg) or DEFAULT_TEST_GLOBS
    return [path for path in deleted if any(glob_match(path, glob) for glob in globs)]


def _removed_source_lines(root: Path, base: str, source: str) -> Set[str]:
    proc = git(
        ["diff", "--unified=0", "--no-color", base, "--", source], cwd=root
    )
    if proc.returncode != 0:
        raise UsageError(f"cannot diff {source} against {base}: {proc.stderr.strip()}")
    removed: Set[str] = set()
    for line in proc.stdout.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            normalized = _normalized_line(line[1:])
            if normalized:
                removed.add(normalized)
    return removed


def _run_engine(
    cwd: Path,
    source: str,
    test_cmd: str,
    output: Path,
    max_mutants: int,
    mutant_timeout: Optional[float],
    engine_timeout: float,
    allow_dirty: bool,
) -> Dict[str, Any]:
    engine = Path(__file__).resolve().with_name("mutation_engine.py")
    argv: List[str] = [
        sys.executable,
        str(engine),
        "--source",
        source,
        "--test-cmd",
        test_cmd,
        "--backend",
        "sregex",
        "--max-mutants",
        str(max_mutants),
        "--seed",
        "0",
        "--spread",
        "--confirm-survivors",
        "1",
        "--no-cache",
        "--output-json",
        str(output),
    ]
    if mutant_timeout is not None:
        argv += ["--timeout", str(mutant_timeout)]
    if allow_dirty:
        argv.append("--allow-dirty")

    proc = run(argv, cwd=cwd, quiet=True, timeout=engine_timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = "\n".join(detail[-12:])
        raise EnvError(f"mutation engine failed in {cwd} ({proc.returncode}):\n{tail}")
    return _load_report(str(output), cwd)


def run_oracle(args: argparse.Namespace, root: Path) -> Dict[str, Any]:
    if args.max_mutants <= 0:
        raise UsageError("--max-mutants must be positive")
    source = rel_path(root, args.source)
    if not (root / source).is_file():
        raise UsageError(
            f"source does not exist in the current tree: {source}; "
            "whole-file deletion is outside this per-file oracle"
        )

    base_proc = git(["rev-parse", "--verify", f"{args.base}^{{commit}}"], cwd=root)
    if base_proc.returncode != 0:
        raise UsageError(f"cannot resolve base commit {args.base!r}")
    base_sha = base_proc.stdout.strip()

    at_base = git(["cat-file", "-e", f"{base_sha}:{source}"], cwd=root)
    if at_base.returncode != 0:
        raise UsageError(
            f"source does not exist at base {args.base}: {source}; "
            "the oracle needs a before-score for the same file"
        )

    cfg = load_config(root, args.config)
    deleted_tests = _deleted_test_files(root, base_sha, cfg)
    current_test = _run_test_cmd(args.test_cmd, root, args.test_timeout)
    base_payload: Dict[str, Any] = {
        "schema": 1,
        "stream": "J",
        "source": source,
        "base": args.base,
        "base_sha": base_sha,
        "test_cmd": args.test_cmd,
        "current_test": current_test,
        "deleted_test_files": deleted_tests,
    }

    if current_test["exit"] != 0:
        return {
            **base_payload,
            "verdict": "refused",
            "before": None,
            "after": None,
            "allow_denominator_drop": 0,
            "removed_line_mutants": 0,
            "regressed_mutants": [],
            "reasons": [
                "current targeted test command is not green "
                f"(exit {current_test['exit']})"
            ],
        }

    diff_proc = git(["diff", "--quiet", base_sha, "--", source], cwd=root)
    if diff_proc.returncode == 0:
        return {
            **base_payload,
            "verdict": "refused",
            "before": None,
            "after": None,
            "allow_denominator_drop": 0,
            "removed_line_mutants": 0,
            "regressed_mutants": [],
            "reasons": ["source is unchanged relative to base; no simplification to judge"],
        }
    if diff_proc.returncode != 1:
        raise UsageError(
            f"cannot diff {source} against {args.base}: {diff_proc.stderr.strip()}"
        )

    with tempfile.TemporaryDirectory(prefix="stream-j-oracle-") as temporary:
        temp_root = Path(temporary)
        before_output = temp_root / "before.json"
        after_output = temp_root / "after.json"
        base_worktree = temp_root / "base"

        added = git(
            ["worktree", "add", "--detach", str(base_worktree), base_sha], cwd=root
        )
        if added.returncode != 0:
            raise EnvError(
                f"could not create temporary base worktree: {added.stderr.strip()}"
            )
        try:
            before = _run_engine(
                base_worktree,
                source,
                args.test_cmd,
                before_output,
                args.max_mutants,
                args.mutant_timeout,
                args.engine_timeout,
                allow_dirty=False,
            )
        finally:
            git(["worktree", "remove", "--force", str(base_worktree)], cwd=root)
            git(["worktree", "prune"], cwd=root)

        after = _run_engine(
            root,
            source,
            args.test_cmd,
            after_output,
            args.max_mutants,
            args.mutant_timeout,
            args.engine_timeout,
            allow_dirty=True,
        )

    removed_lines = _removed_source_lines(root, base_sha, source)
    removed_line_mutants = sum(
        1
        for mutant in denominator_mutants(before)
        if _normalized_line(mutant.get("original")) in removed_lines
    )
    payload = compare_results(before, after, removed_line_mutants)
    payload.update(base_payload)
    payload["removed_line_mutants"] = removed_line_mutants

    if deleted_tests:
        payload["reasons"].append(
            "tracked test file(s) were deleted: " + ", ".join(deleted_tests)
        )
        payload["verdict"] = "refused"
    return payload


def _print_gate(payload: Dict[str, Any], as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        before = payload.get("before") or {}
        after = payload.get("after") or {}

        def score(summary: Dict[str, Any]) -> str:
            value = summary.get("kill_rate")
            return "n/a" if value is None else f"{float(value):.1%}"

        print(
            f"[{'OK' if payload['verdict'] == 'accepted' else 'REFUSED'}] "
            f"Stream J mutation score: {score(before)} -> {score(after)}"
        )
        for reason in payload.get("reasons", []):
            print(f"  - {reason}")
    return EXIT_OK if payload["verdict"] == "accepted" else EXIT_NEGATIVE


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="simplify_gate.py",
        description="Stream J dead-code candidates and mutation-score oracle.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    finder = subparsers.add_parser(
        "finder", help="emit conservative dead-code candidates (never edits)"
    )
    finder.add_argument("paths", nargs="*", help="Python files/dirs (default: candidates.J)")
    finder.add_argument(
        "--scan-root",
        action="append",
        default=[],
        help="Python tree searched for fan-in; repeatable (default: repo root)",
    )
    finder.add_argument("--coverage-json", help="coverage.py JSON report")
    finder.add_argument("--config", help="path to config.json")
    finder.add_argument("--json", action="store_true", help="machine-readable output")

    compare = subparsers.add_parser(
        "compare", help="compare two mutation_engine.py JSON reports"
    )
    compare.add_argument("--before-json", required=True)
    compare.add_argument("--after-json", required=True)
    compare.add_argument("--allow-denominator-drop", type=int, default=0)
    compare.add_argument("--test-cmd", help="also require this current command to be green")
    compare.add_argument("--test-timeout", type=float, default=300.0)
    compare.add_argument("--json", action="store_true", help="machine-readable output")

    oracle = subparsers.add_parser(
        "oracle", help="run tests and mutation scoring on base vs current source"
    )
    oracle.add_argument("--source", required=True)
    oracle.add_argument("--base", default="HEAD", help="base commit/ref (default: HEAD)")
    oracle.add_argument("--test-cmd", required=True)
    oracle.add_argument("--max-mutants", type=int, default=200)
    oracle.add_argument("--mutant-timeout", type=float)
    oracle.add_argument("--test-timeout", type=float, default=300.0)
    oracle.add_argument("--engine-timeout", type=float, default=1800.0)
    oracle.add_argument("--config", help="path to config.json")
    oracle.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = repo_root()
        if args.command == "finder":
            cfg = load_config(root, args.config)
            targets = _collect_targets(root, cfg, args.paths)
            scan_texts = _collect_scan_files(root, args.scan_root, targets)
            coverage = _load_coverage(args.coverage_json, root)
            findings = find_candidates(root, targets, scan_texts, coverage)
            if args.json:
                print(json.dumps(findings, indent=2))
            else:
                for finding in findings:
                    print(
                        f"{finding['file']}:{finding['line']} [{finding['kind']}] "
                        f"{finding['text']} — {finding['evidence']}"
                    )
                print(
                    f"[*] {len(findings)} Stream J candidate(s) — candidates only; "
                    "run the oracle before deleting anything.",
                    file=sys.stderr,
                )
            return EXIT_OK

        if args.command == "compare":
            payload = compare_results(
                _load_report(args.before_json, root),
                _load_report(args.after_json, root),
                args.allow_denominator_drop,
            )
            if args.test_cmd:
                current = _run_test_cmd(args.test_cmd, root, args.test_timeout)
                payload["current_test"] = current
                if current["exit"] != 0:
                    payload["reasons"].append(
                        "current targeted test command is not green "
                        f"(exit {current['exit']})"
                    )
                    payload["verdict"] = "refused"
            return _print_gate(payload, args.json)

        payload = run_oracle(args, root)
        return _print_gate(payload, args.json)
    except (UsageError, EnvError, Fatal) as exc:
        return format_exit(exc)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
