#!/usr/bin/env python3
"""Durable per-mutant triage ledger.

The work ledger is per **file**. That is the right unit for "audit src/app.py",
but it loses mutant-level memory: fixing one of N survivors marks the whole file
audited and orphans the rest, and an equivalent mutant is re-triaged by an LLM
every time the file changes. This module stores one row per surviving mutant in
a git-tracked, append-only markdown table:

    | Date | Mutant ID | Source Hash | Disposition | PR / Issue | Reason |

A mutant id is ``file:line:sha256(mutated-text)[:12]``. The newest row for an id
wins, exactly like the work ledger. ``OPEN`` is active state; ``KILLED-BY``,
``EQUIVALENT``, ``NOISE`` and ``WONTFIX`` are terminal and are never re-reported
for that id, even after the file hash changes. An ``OPEN`` row only counts while
its source hash still matches HEAD, so a content change drops stale survivors
and a fresh engine run repopulates them.

Usage:
  python3 scripts/autonomous/mutants.py ingest --report run.json --json
  python3 scripts/autonomous/mutants.py open --json
  python3 scripts/autonomous/mutants.py record --id src/app.py:12:ab12cd34ef56 \
      --disposition EQUIVALENT --reason 'no observable change'
  python3 scripts/autonomous/mutants.py check --id src/app.py:12:ab12cd34ef56
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from common import (
    EXIT_NEGATIVE,
    EXIT_OK,
    Fatal,
    UsageError,
    escape_cell,
    format_exit,
    hash_at_head,
    insert_table_rows,
    is_dirty,
    load_config,
    path_at_head,
    rel_path,
    repo_root,
    split_table_row,
)

DEFAULT_MUTANT_LEDGER = "docs/AUTONOMOUS_MUTANTS.md"
TERMINAL_DISPOSITIONS = {"KILLED-BY", "EQUIVALENT", "NOISE", "WONTFIX"}
DISPOSITIONS = TERMINAL_DISPOSITIONS | {"OPEN"}
MUTANT_ID_RE = re.compile(r"^(?P<file>.+):(?P<line>\d+):(?P<digest>[0-9a-f]{12})$")


@dataclass
class MutantRow:
    line_index: int
    date: str
    mutant_id: str
    source_hash: str
    disposition: str
    pr: str
    reason: str

    @property
    def file(self) -> str:
        match = MUTANT_ID_RE.match(self.mutant_id)
        return match.group("file") if match else ""

    @property
    def line(self) -> int:
        match = MUTANT_ID_RE.match(self.mutant_id)
        return int(match.group("line")) if match else 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.mutant_id,
            "file": self.file,
            "line": self.line,
            "source_hash": self.source_hash,
            "disposition": self.disposition,
            "pr": self.pr,
            "reason": self.reason,
            "date": self.date,
        }


def mutant_id(file: str, line: int, mutated: str) -> str:
    """Stable id for a mutation of one line: file:line:hash(mutated text)."""
    digest = hashlib.sha256(mutated.encode("utf-8")).hexdigest()[:12]
    return f"{file}:{int(line)}:{digest}"


def parse_mutants(path: Path) -> List[MutantRow]:
    if not path.exists():
        return []
    rows: List[MutantRow] = []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        lines = handle.read().splitlines()
    for index, line in enumerate(lines):
        cells = split_table_row(line)
        if not cells or len(cells) < 6:
            continue
        date = cells[0].strip().strip("`").strip()
        mutant = cells[1].strip().strip("`").strip()
        source_hash = cells[2].strip().strip("`").strip()
        disposition = cells[3].strip().strip("`").strip().upper()
        if not MUTANT_ID_RE.match(mutant):
            continue
        if disposition not in DISPOSITIONS and disposition != "EXAMPLE":
            continue
        rows.append(
            MutantRow(
                line_index=index,
                date=date,
                mutant_id=mutant,
                source_hash=source_hash,
                disposition=disposition,
                pr=cells[4].strip(),
                reason=cells[5].strip(),
            )
        )
    return rows


def authoritative_mutants(path: Path) -> Dict[str, MutantRow]:
    """Newest row per mutant id. Date wins; on a tie the topmost row wins."""
    newest: Dict[str, MutantRow] = {}
    for row in parse_mutants(path):
        if row.disposition == "EXAMPLE":
            continue
        current = newest.get(row.mutant_id)
        key = (row.date or "0000-00-00", -row.line_index)
        if current is None:
            newest[row.mutant_id] = row
            continue
        current_key = (current.date or "0000-00-00", -current.line_index)
        if key > current_key:
            newest[row.mutant_id] = row
    return newest


def mutant_ledger_path(
    root: Path, cfg: Dict[str, Any], explicit: Optional[str] = None
) -> Path:
    raw = explicit or cfg.get("mutant_ledger_path") or DEFAULT_MUTANT_LEDGER
    path = Path(raw)
    return path if path.is_absolute() else (root / path)


def open_mutants_by_file(root: Path, cfg: Dict[str, Any]) -> Dict[str, List[MutantRow]]:
    """Active survivors, grouped by file, tied to the file's current HEAD hash.

    A missing mutant ledger means "no mutant-level memory", not an error: repos
    that have not adopted the mutant ledger keep working exactly as before.
    Stale OPEN rows (the file changed since the run) are ignored.
    """
    path = mutant_ledger_path(root, cfg)
    if not path.exists():
        return {}
    grouped: Dict[str, List[MutantRow]] = {}
    hashes: Dict[str, Optional[str]] = {}
    for row in authoritative_mutants(path).values():
        if row.disposition != "OPEN" or not row.file:
            continue
        if row.file not in hashes:
            hashes[row.file] = hash_at_head(root, row.file)
        current = hashes[row.file]
        if current and current == row.source_hash:
            grouped.setdefault(row.file, []).append(row)
    for rows in grouped.values():
        rows.sort(key=lambda item: item.line)
    return grouped


def _row_text(
    mutant: str,
    source_hash: str,
    disposition: str,
    pr: str,
    reason: str,
) -> str:
    return (
        f"| {escape_cell(datetime.date.today().isoformat())} "
        f"| `{escape_cell(mutant)}` "
        f"| `{escape_cell(source_hash)}` "
        f"| {escape_cell(disposition)} "
        f"| {escape_cell(pr)} "
        f"| {escape_cell(reason)} |"
    )


def _insert(path: Path, rows: List[str]) -> None:
    if not path.exists():
        raise Fatal(
            f"mutant ledger not found: {path} "
            "(copy assets/AUTONOMOUS_MUTANT_LEDGER.template.md first)"
        )
    insert_table_rows(path, rows)


def _parse_id(raw: str) -> Tuple[str, int]:
    match = MUTANT_ID_RE.match(raw.strip().strip("`"))
    if not match:
        raise UsageError(
            "mutant id must look like file:line:<12 hex chars> "
            "(use the id printed by `ingest` or `open --json`)"
        )
    return match.group("file"), int(match.group("line"))


def cmd_ingest(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root, args.config)
    ledger = mutant_ledger_path(root, cfg, args.ledger)
    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = root / report_path
    if not report_path.exists():
        raise Fatal(f"report not found: {report_path}")
    try:
        with open(report_path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
    except json.JSONDecodeError as exc:
        raise Fatal(f"invalid JSON in {report_path}: {exc}")
    if not isinstance(report, dict):
        raise Fatal(f"report is not a JSON object: {report_path}")

    source = str(report.get("source") or "").strip()
    if not source:
        raise Fatal(f"report has no source: {report_path}")
    source_rel = rel_path(root, source)
    source_hash = hash_at_head(root, source_rel)
    if not source_hash:
        raise Fatal(f"source does not exist at HEAD: {source_rel}")
    if is_dirty(root, source_rel):
        print(
            f"[warn] working tree differs from HEAD for {source_rel}; "
            "the recorded hash describes HEAD",
            file=sys.stderr,
        )

    survivors = report.get("survived")
    if not isinstance(survivors, list):
        raise Fatal(f"report has no survived list: {report_path}")

    existing = authoritative_mutants(ledger)
    opened: List[Dict[str, Any]] = []
    triaged: List[Dict[str, str]] = []
    already_open = 0
    rows: List[str] = []
    seen: set = set()
    for item in survivors:
        if not isinstance(item, dict):
            continue
        mutated = str(item.get("mutated") or "")
        line = item.get("line")
        if not mutated or not isinstance(line, int):
            continue
        file = rel_path(root, str(item.get("file") or source_rel))
        mid = mutant_id(file, line, mutated)
        if mid in seen:
            continue
        seen.add(mid)
        current = existing.get(mid)
        if current and current.disposition in TERMINAL_DISPOSITIONS:
            triaged.append({"id": mid, "disposition": current.disposition})
            continue
        if (
            current
            and current.disposition == "OPEN"
            and current.source_hash == source_hash
        ):
            already_open += 1
            continue
        reason = " ".join(str(item.get("description") or "survived").split())
        rows.append(_row_text(mid, source_hash, "OPEN", "—", reason))
        opened.append(
            {
                "id": mid,
                "file": file,
                "line": line,
                "source_hash": source_hash,
                "reason": reason,
            }
        )

    if rows:
        _insert(ledger, rows)

    if args.json:
        json.dump(
            {
                "ledger": str(ledger),
                "source": source_rel,
                "source_hash": source_hash,
                "opened": opened,
                "triaged": triaged,
                "already_open": already_open,
                "already_triaged": len(triaged),
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return EXIT_OK

    for item in opened:
        print(f"[+] OPEN {item['id']}  ({item['reason']})")
    if not opened:
        print("[+] No new survivors to record.")
    print(
        f"    opened={len(opened)} already_open={already_open} "
        f"already_triaged={len(triaged)} ledger={ledger}"
    )
    return EXIT_OK


def cmd_record(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root, args.config)
    ledger = mutant_ledger_path(root, cfg, args.ledger)
    file, _line = _parse_id(args.id)
    target = rel_path(root, file)
    if not path_at_head(root, target):
        raise Fatal(f"mutant file does not exist at HEAD: {target}")
    source_hash = hash_at_head(root, target)
    if not source_hash:
        raise Fatal(f"cannot hash mutant file at HEAD: {target}")

    disposition = args.disposition.strip().upper()
    if disposition not in TERMINAL_DISPOSITIONS:
        raise UsageError(
            "disposition must be one of " + ", ".join(sorted(TERMINAL_DISPOSITIONS))
        )
    if not args.reason.strip():
        raise UsageError("--reason is required (one line: why this disposition is correct)")

    existing = authoritative_mutants(ledger).get(args.id.strip().strip("`"))
    if existing and existing.disposition == disposition:
        message = (
            f"{args.id} already has a {disposition} row "
            f"({existing.date}, {existing.pr})"
        )
        if not args.force:
            raise Fatal(message + "; pass --force to record a duplicate")
        print(f"[warn] {message} (--force)", file=sys.stderr)

    pr = args.pr.strip() if args.pr else "—"
    row = _row_text(args.id.strip().strip("`"), source_hash, disposition, pr, args.reason)
    _insert(ledger, [row])
    print(f"[+] Recorded in {ledger}:")
    print(f"    {row}")
    return EXIT_OK


def cmd_open(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root, args.config)
    grouped = open_mutants_by_file(root, cfg)
    if args.source:
        wanted = rel_path(root, args.source)
        grouped = {key: value for key, value in grouped.items() if key == wanted}

    if args.json:
        files = [
            {
                "target": target,
                "source_hash": rows[0].source_hash,
                "open": [row.to_dict() for row in rows],
            }
            for target, rows in sorted(grouped.items())
        ]
        json.dump(
            {
                "files": files,
                "total": sum(len(item["open"]) for item in files),
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return EXIT_OK

    total = sum(len(rows) for rows in grouped.values())
    if not total:
        print("[+] No open mutants at the current content hashes.")
        return EXIT_OK
    for target, rows in sorted(grouped.items()):
        print(f"{target}: {len(rows)} open mutant(s)")
        for item in rows:
            print(f"  {item.mutant_id}  {item.reason}")
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root, args.config)
    ledger = mutant_ledger_path(root, cfg, args.ledger)
    row = authoritative_mutants(ledger).get(args.id.strip().strip("`"))
    if row and row.disposition in TERMINAL_DISPOSITIONS:
        print(
            f"[+] {row.mutant_id} is {row.disposition} "
            f"({row.pr or 'no PR'}): {row.reason}"
        )
        return EXIT_OK
    if row:
        print(f"[!] {row.mutant_id} is {row.disposition} (not yet triaged)")
    else:
        print(f"[!] no row for {args.id}")
    return EXIT_NEGATIVE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Durable per-mutant triage ledger (survivors -> dispositions)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common_flags(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--config", help="path to config.json")
        sub.add_argument("--ledger", help="override mutant ledger path")

    ingest_parser = subparsers.add_parser(
        "ingest", help="record survivors from a mutation_engine --output-json run"
    )
    common_flags(ingest_parser)
    ingest_parser.add_argument("--report", required=True, help="mutation engine JSON report")
    ingest_parser.add_argument("--json", action="store_true", help="machine-readable output")
    ingest_parser.set_defaults(func=cmd_ingest)

    record_parser = subparsers.add_parser("record", help="record a terminal disposition")
    common_flags(record_parser)
    record_parser.add_argument("--id", required=True, help="mutant id from ingest/open")
    record_parser.add_argument("--disposition", required=True, help="KILLED-BY|EQUIVALENT|NOISE|WONTFIX")
    record_parser.add_argument("--pr", help="PR/issue that killed it (for KILLED-BY)")
    record_parser.add_argument("--reason", required=True, help="one line of evidence")
    record_parser.add_argument("--force", action="store_true", help="allow a duplicate row")
    record_parser.set_defaults(func=cmd_record)

    open_parser = subparsers.add_parser("open", help="list active survivors")
    common_flags(open_parser)
    open_parser.add_argument("--source", help="only this file")
    open_parser.add_argument("--json", action="store_true", help="machine-readable output")
    open_parser.set_defaults(func=cmd_open)

    check_parser = subparsers.add_parser(
        "check", help="exit 0 iff the mutant already has a terminal disposition"
    )
    common_flags(check_parser)
    check_parser.add_argument("--id", required=True, help="mutant id")
    check_parser.set_defaults(func=cmd_check)
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return args.func(args)
    except Exception as exc:
        return format_exit(exc)


if __name__ == "__main__":
    sys.exit(main())
