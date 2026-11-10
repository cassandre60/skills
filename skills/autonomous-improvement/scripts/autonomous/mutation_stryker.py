#!/usr/bin/env python3
"""External Stryker mutation backend adapter for mutation_engine.py."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import EnvError, UsageError

_STRYKER_STATUS = {
    "Killed": "killed",
    "Survived": "survived",
    "NoCoverage": "survived",
    "CompileError": "uncompilable",
    "RuntimeError": "uncompilable",
    "Timeout": "timeout",
}


def _print_summary(
    source: str,
    killed: List[Dict[str, Any]],
    survived: List[Dict[str, Any]],
    uncompilable: List[Dict[str, Any]],
    timeouts: List[Dict[str, Any]],
    counts: Dict[str, int],
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
        print(
            "NOTE: every survivor needs human triage — equivalent mutants and regex noise exist."
        )


def run_stryker_backend(
    source_path: str,
    test_cmd: str,
    cmd: Optional[List[str]],
    report_path: str,
    timeout_counts_as: str = "killed",
    timeout_s: float = 300.0,
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
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout_s
            )
        except subprocess.TimeoutExpired as exc:
            raise EnvError(
                f"external mutator timed out after {timeout_s}s: {' '.join(argv)}"
            ) from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()[:2000]
            raise EnvError(
                f"external mutator failed (exit {proc.returncode}): {detail}"
            )
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
        "killed": [],
        "survived": [],
        "uncompilable": [],
        "timeout": [],
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
                + original_line[end["column"] - 1 :]
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
    timeout_survived = (
        len(buckets["timeout"]) if timeout_counts_as == "survived" else 0
    )
    denominator = (
        len(buckets["killed"])
        + len(buckets["survived"])
        + timeout_killed
        + timeout_survived
    )
    kill_rate = (
        (len(buckets["killed"]) + timeout_killed) / denominator * 100.0
        if denominator
        else None
    )
    counts = {
        "killed": len(buckets["killed"]),
        "survived": len(buckets["survived"]),
        "unconfirmed": 0,
        "unverified": 0,
        "uncompilable": len(buckets["uncompilable"]),
        "timeout": len(buckets["timeout"]),
        "total": sum(len(items) for items in buckets.values()),
    }
    _print_summary(
        str(source),
        buckets["killed"],
        buckets["survived"],
        buckets["uncompilable"],
        buckets["timeout"],
        counts,
        kill_rate,
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
        "unverified": [],
        "uncompilable": buckets["uncompilable"],
        "timeout": buckets["timeout"],
    }
