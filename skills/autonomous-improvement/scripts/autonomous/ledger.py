#!/usr/bin/env python3
"""Autonomous Work Ledger Manager (content-hash deduplication).

The ledger is a git-tracked markdown table. For each target the authoritative
row is the newest one (Date desc, then topmost on a tie, because ``record``
inserts directly below the header). Only ``COMPLETED`` and ``SOUND`` rows count
as audited. The stored hash is composite: ``<source9>:<companion9>`` where the
companion is the mapped test file (or ``-``).

Usage:
  python3 scripts/autonomous/ledger.py next --stream A
  python3 scripts/autonomous/ledger.py record --stream A --target src/app.py \
      --status COMPLETED --pr '#42' --summary '...'
  python3 scripts/autonomous/ledger.py check --target src/app.py
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    EXIT_NEGATIVE,
    EXIT_OK,
    EnvError,
    Fatal,
    SEPARATOR_RE,
    UsageError,
    candidate_paths,
    config_path,
    escape_cell as esc,
    format_exit,
    git,
    hash_at_head,
    insert_table_rows,
    is_dirty,
    load_config,
    match_any,
    normalize_stream,
    path_at_head,
    rel_path,
    repo_root,
    run,
    split_table_row as split_row,
    stream_label,
    test_for_source,
)
from mutants import open_mutants_by_file

AUDITED_STATUSES = {"COMPLETED", "SOUND"}
# Negative dispositions: "the fleet was told no". They are memory, not audits:
# a disposition applies only while the composite hash still matches, so any
# content change re-opens the target (same rule as an audit going stale).
DISPOSITION_STATUSES = {"REJECTED", "WONTFIX", "SUPERSEDED"}
STATUSES = ("COMPLETED", "SOUND", "REJECTED", "WONTFIX", "SUPERSEDED")


@dataclass
class LedgerRow:
    line_index: int
    date: str
    target: str
    content_hash: str
    status: str
    pr: str
    summary: str
    trigger: str
    cells: List[str]

    @property
    def source_hash(self) -> str:
        return self.content_hash.split(":", 1)[0].strip("` ")

    @property
    def companion_hash(self) -> Optional[str]:
        if ":" not in self.content_hash:
            return None
        return self.content_hash.split(":", 1)[1].strip("` ") or "-"

    @property
    def legacy(self) -> bool:
        return ":" not in self.content_hash

    @property
    def composite(self) -> str:
        return self.content_hash.strip("` ")


def _clean_target(value: str) -> str:
    return value.strip().strip("`").strip()


def _clean_hash(value: str) -> str:
    return value.strip().strip("`").strip()


def parse_ledger(path: Path) -> List[LedgerRow]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        lines = handle.read().splitlines()

    header_index: Optional[int] = None
    for index, line in enumerate(lines):
        cells = split_row(line)
        if cells and cells[0].strip().lower() == "date":
            header_index = index
            break
    if header_index is None:
        return []

    separator_index: Optional[int] = None
    for index in range(header_index + 1, len(lines)):
        if SEPARATOR_RE.match(lines[index].strip()):
            separator_index = index
            break
    if separator_index is None:
        return []

    rows: List[LedgerRow] = []
    for index in range(separator_index + 1, len(lines)):
        cells = split_row(lines[index])
        if cells is None:
            if lines[index].strip() == "":
                continue
            break
        if len(cells) < 8:
            continue
        status = cells[4].strip().strip("*`").upper()
        if status not in AUDITED_STATUSES and status not in DISPOSITION_STATUSES and status != "EXAMPLE":
            continue
        rows.append(
            LedgerRow(
                line_index=index,
                date=cells[0].strip(),
                target=_clean_target(cells[2]),
                content_hash=_clean_hash(cells[3]),
                status=status,
                pr=cells[5],
                summary=cells[6],
                trigger=cells[7],
                cells=cells,
            )
        )
    return rows


def authoritative_rows(path: Path) -> Dict[str, LedgerRow]:
    """Newest row per target. Date wins; on a tie the topmost row wins."""
    newest: Dict[str, LedgerRow] = {}
    for row in parse_ledger(path):
        if row.status not in AUDITED_STATUSES:
            continue
        current = newest.get(row.target)
        key = (row.date or "0000-00-00", -row.line_index)
        if current is None:
            newest[row.target] = row
            continue
        current_key = (current.date or "0000-00-00", -current.line_index)
        if key > current_key:
            newest[row.target] = row
    return newest


def authoritative_dispositions(path: Path) -> Dict[str, LedgerRow]:
    """Newest negative disposition per target (REJECTED / WONTFIX / SUPERSEDED).

    Same newest-wins rule as :func:`authoritative_rows`. A disposition only
    suppresses a target while the composite hash still matches; content
    changes re-open it, exactly like a stale audit.
    """
    newest: Dict[str, LedgerRow] = {}
    for row in parse_ledger(path):
        if row.status not in DISPOSITION_STATUSES:
            continue
        current = newest.get(row.target)
        key = (row.date or "0000-00-00", -row.line_index)
        if current is None:
            newest[row.target] = row
            continue
        current_key = (current.date or "0000-00-00", -current.line_index)
        if key > current_key:
            newest[row.target] = row
    return newest


def composite_hash(source_hash: str, companion_hash: Optional[str]) -> str:
    return f"{source_hash}:{companion_hash or '-'}"


def _matches(row: LedgerRow, source_hash: str, companion_hash: Optional[str]) -> bool:
    if row.legacy:
        return row.source_hash == source_hash
    return row.source_hash == source_hash and row.companion_hash == (companion_hash or "-")


def _ledger_path(root: Path, cfg: Dict[str, Any], explicit: Optional[str]) -> Path:
    raw = explicit or cfg.get("ledger_path") or "docs/AUTONOMOUS_WORK_LEDGER.md"
    path = Path(raw)
    return path if path.is_absolute() else (root / path)


def _gh_claims(root: Path, require_gh: bool) -> Tuple[Dict[str, str], Optional[str]]:
    """Return {claimed path or branch token: PR label}, plus a warning.

    Primary rule: exact file overlap from ``gh pr list --json files``.
    Fallback: whole-token branch-name matching, never free-text titles.
    """
    try:
        proc = run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--json",
                "number,title,headRefName,files",
                "--limit",
                "200",
            ],
            cwd=root,
            quiet=True,
        )
    except Exception as exc:  # missing tool or timeout
        if require_gh:
            raise
        return {}, f"gh not available ({exc}); skipping open-PR collision check"
    if proc.returncode != 0:
        # Older gh versions do not expose ``files`` in --json. Retry with the
        # fields every version supports and use branch-token matching.
        retry = run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--json",
                "number,title,headRefName",
                "--limit",
                "200",
            ],
            cwd=root,
            quiet=True,
        )
        if retry.returncode != 0:
            message = (proc.stderr or proc.stdout).strip().splitlines()
            detail = message[0] if message else f"exit {proc.returncode}"
            if require_gh:
                raise EnvError(f"gh pr list failed: {detail}")
            return {}, f"gh unavailable ({detail}); skipping open-PR collision check"
        proc = retry
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        if require_gh:
            raise EnvError("gh pr list returned invalid JSON")
        return {}, "gh returned invalid JSON; skipping open-PR collision check"
    if not isinstance(data, list):
        if require_gh:
            raise EnvError("gh returned an unexpected payload")
        return {}, "gh returned an unexpected payload; skipping open-PR collision check"

    claims: Dict[str, str] = {}
    for pr in data:
        if not isinstance(pr, dict):
            continue
        label = f"#{pr.get('number', '?')} {pr.get('headRefName', '')}".strip()
        files = pr.get("files")
        if isinstance(files, list):
            for item in files:
                if isinstance(item, dict) and item.get("path"):
                    claims[str(item["path"])] = label
            continue
        # Older gh versions may omit ``files``. Fall back to whole branch
        # segments, never substrings and never free-text titles.
        branch = str(pr.get("headRefName") or "")
        normalized = branch.lower().replace("_", "-")
        for segment in normalized.split("/"):
            if segment:
                claims.setdefault("token:" + segment, label)
    return claims, None


def _claimed(claims: Dict[str, str], target: str, test: Optional[str]) -> Optional[str]:
    if target in claims:
        return claims[target]
    if test and test in claims:
        return claims[test]
    slug = Path(target).stem.lower().replace("_", "-")
    return claims.get("token:" + slug)


def _candidate_records(
    root: Path,
    cfg: Dict[str, Any],
    stream: str,
    audited: Dict[str, LedgerRow],
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    section = cfg.get("candidates", {}).get(stream, {})
    require_test = bool(section.get("require_test", True))
    for target in candidate_paths(root, cfg, stream):
        source_hash = hash_at_head(root, target)
        if not source_hash:
            continue
        test: Optional[str] = None
        test_hash: Optional[str] = None
        if stream == "A":
            test = test_for_source(root, cfg, target)
            if test and path_at_head(root, test):
                test_hash = hash_at_head(root, test)
            elif require_test:
                records.append(
                    {
                        "target": target,
                        "stream": stream,
                        "source_hash": source_hash,
                        "test": test,
                        "test_hash": None,
                        "composite_hash": composite_hash(source_hash, None),
                        "skip": f"no test file mapped (require_test is true)",
                        "priority": False,
                    }
                )
                continue
            else:
                test = None
        record = audited.get(target)
        matches = bool(record and _matches(record, source_hash, test_hash))
        records.append(
            {
                "target": target,
                "stream": stream,
                "source_hash": source_hash,
                "test": test,
                "test_hash": test_hash,
                "composite_hash": composite_hash(source_hash, test_hash),
                "audited_hash": record.composite if record else None,
                "legacy": bool(record and record.legacy),
                "audited": matches,
                "priority": False,
            }
        )
    priorities = section.get("priority_paths") or []
    for record in records:
        target = record["target"]
        record["priority"] = any(
            target == str(p).rstrip("/")
            or target.startswith(str(p).rstrip("/") + "/")
            for p in priorities
        )
    return records


def cmd_next(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root, args.config)
    stream = normalize_stream(args.stream)
    ledger = _ledger_path(root, cfg, args.ledger)
    audited = authoritative_rows(ledger)
    dispositions = authoritative_dispositions(ledger)
    claims, warning = _gh_claims(root, args.require_gh)
    if warning:
        print(f"[warning] {warning}", file=sys.stderr)

    records = _candidate_records(root, cfg, stream, audited)
    mutant_index = open_mutants_by_file(root, cfg)
    eligible: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for record in records:
        if record.get("skip"):
            skipped.append(record)
            continue
        open_rows = mutant_index.get(record["target"]) or []
        if open_rows:
            record = dict(record, open_mutants=len(open_rows))
        if record["audited"]:
            if not open_rows:
                skipped.append(dict(record, skip="already audited at current content hash"))
                continue
            # An audit is per file; open survivors still need killing or triage.
            record["audit_state"] = "partial"
        else:
            disposition = dispositions.get(record["target"])
            if disposition and _matches(
                disposition, record["source_hash"], record.get("test_hash")
            ):
                skipped.append(
                    dict(
                        record,
                        disposition=disposition.status,
                        skip=(
                            f"{disposition.status} at current content hash "
                            f"({disposition.pr or 'no PR'}): {disposition.summary}"
                        ),
                    )
                )
                continue
        claimed_by = _claimed(claims, record["target"], record.get("test"))
        if claimed_by:
            skipped.append(dict(record, skip=f"open PR {claimed_by}"))
            continue
        eligible.append(record)

    selected = eligible[: max(0, args.limit)]
    for record in selected:
        record["recommended_test_cmd"] = None
        if record.get("test") and cfg.get("test_cmd"):
            record["recommended_test_cmd"] = list(cfg["test_cmd"]) + [record["test"]]

    if args.json:
        json.dump(
            {
                "stream": stream,
                "ledger": str(ledger),
                "candidates": selected,
                "skipped": skipped,
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return EXIT_OK

    print(f"[*] Stream {stream_label(stream)} — ledger: {ledger}")
    for record in selected:
        print("\n" + "=" * 60)
        print(f"NEXT TARGET FOUND: {record['target']}")
        print(f"Current source hash: {record['source_hash']}")
        print(f"Mapped test:         {record.get('test') or '—'}")
        print(f"Composite hash:      {record['composite_hash']}")
        print(f"Last audited hash:   {record.get('audited_hash') or 'NEVER'}")
        if record.get("open_mutants"):
            print(
                f"Open mutants:        {record['open_mutants']} "
                "(partially audited — kill or triage before calling it done)"
            )
        print("=" * 60)
        if stream == "A" and record.get("test"):
            test_cmd = record.get("recommended_test_cmd")
            if test_cmd:
                print("\nRecommended command:")
                print(
                    "python3 scripts/autonomous/mutation_engine.py \\\n"
                    f"  --source {record['target']} \\\n"
                    f"  --test-cmd \"{' '.join(test_cmd)}\" \\\n"
                    "  --output-issue docs/tasks/mutant_issue.md"
                )
    if not selected:
        print("[+] No eligible targets remain for this stream at their current hashes.")
    if args.verbose:
        for record in skipped:
            print(f"  [-] {record['target']}: {record.get('skip')}")
    return EXIT_OK


def _require_pr(cfg: Dict[str, Any], status: str) -> bool:
    setting = cfg.get("pr_required")
    if isinstance(setting, dict):
        return bool(setting.get(status, status == "COMPLETED"))
    if isinstance(setting, bool):
        return setting
    return status == "COMPLETED"


def _row_text(
    stream: str,
    target: str,
    composite: str,
    status: str,
    pr: str,
    summary: str,
    trigger: str,
) -> str:
    return (
        f"| {esc(datetime.date.today().isoformat())} "
        f"| {esc(stream_label(stream))} "
        f"| `{esc(target)}` "
        f"| `{esc(composite)}` "
        f"| {esc(status)} "
        f"| {esc(pr)} "
        f"| {esc(summary)} "
        f"| {esc(trigger)} |"
    )


def _insert_rows(ledger: Path, new_rows: List[str]) -> None:
    """Insert rows directly below the table header, preserving list order."""
    if not ledger.exists():
        raise Fatal(
            f"ledger not found: {ledger} "
            "(copy assets/AUTONOMOUS_WORK_LEDGER.template.md first)"
        )
    insert_table_rows(ledger, new_rows)


def cmd_record(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root, args.config)
    stream = normalize_stream(args.stream)
    ledger = _ledger_path(root, cfg, args.ledger)
    target = rel_path(root, args.target)
    if not path_at_head(root, target):
        raise Fatal(f"target does not exist at HEAD: {target}")

    status = args.status.upper()
    if status not in STATUSES:
        raise UsageError(f"status must be one of {', '.join(STATUSES)}")

    companion: Optional[str] = None
    if args.test:
        companion = rel_path(root, args.test)
        if not path_at_head(root, companion):
            raise Fatal(f"test file does not exist at HEAD: {companion}")
    elif stream == "A":
        mapped = test_for_source(root, cfg, target)
        if mapped and path_at_head(root, mapped):
            companion = mapped

    source_hash = hash_at_head(root, target)
    if not source_hash:
        raise Fatal(f"cannot hash target at HEAD: {target}")
    companion_hash = hash_at_head(root, companion) if companion else None
    composite = composite_hash(source_hash, companion_hash)

    if is_dirty(root, target):
        message = f"working tree differs from HEAD for {target}; the recorded hash describes HEAD"
        if args.strict:
            raise Fatal(message + " (--strict)")
        print(f"[warn] {message}", file=sys.stderr)

    pr = (args.pr or "").strip() or "—"
    if status in AUDITED_STATUSES and _require_pr(cfg, status) and pr == "—":
        raise Fatal(f"--pr is required for status {status} (pass --pr, or configure pr_required)")

    # Duplicate detection spans audits and dispositions: re-recording the same
    # "no" at the same hash is noise, and --force is the explicit override.
    existing_audit = authoritative_rows(ledger).get(target)
    if status in AUDITED_STATUSES and existing_audit and existing_audit.composite == composite:
        message = (
            f"{target} already has an authoritative {existing_audit.status} row at {composite} "
            f"({existing_audit.date}, {existing_audit.pr})"
        )
        if not args.force:
            raise Fatal(message + "; pass --force to record a duplicate")
        print(f"[warn] {message} (--force)", file=sys.stderr)

    existing_disposition = authoritative_dispositions(ledger).get(target)
    if (
        status in DISPOSITION_STATUSES
        and existing_disposition
        and existing_disposition.composite == composite
        and existing_disposition.status == status
    ):
        message = (
            f"{target} already has a {status} row at {composite} "
            f"({existing_disposition.date}, {existing_disposition.pr})"
        )
        if not args.force:
            raise Fatal(message + "; pass --force to record a duplicate")
        print(f"[warn] {message} (--force)", file=sys.stderr)

    trigger = args.trigger or f"Any modification to {Path(target).name}."
    row = _row_text(stream, target, composite, status, pr, args.summary, trigger)
    _insert_rows(ledger, [row])

    print(f"[+] Recorded in {ledger}:")
    print(f"    {row}")
    print("    If another agent edited the ledger concurrently, rebase before pushing.")
    if status in DISPOSITION_STATUSES:
        print(
            f"    {target} stays suppressed only while its composite hash is {composite}; "
            "any content change re-opens it."
        )
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root, args.config)
    ledger = _ledger_path(root, cfg, args.ledger)
    target = rel_path(root, args.target)
    if not path_at_head(root, target):
        raise Fatal(f"target does not exist at HEAD: {target}")
    companion: Optional[str] = None
    stream = normalize_stream(args.stream)
    if stream == "A":
        mapped = test_for_source(root, cfg, target)
        if mapped and path_at_head(root, mapped):
            companion = mapped
    source_hash = hash_at_head(root, target) or ""
    companion_hash = hash_at_head(root, companion) if companion else None
    current = composite_hash(source_hash, companion_hash)
    row = authoritative_rows(ledger).get(target)
    if row and _matches(row, source_hash, companion_hash):
        print(f"[+] audited at {current}" + (" (legacy source-only row)" if row.legacy else ""))
        open_rows = open_mutants_by_file(root, cfg).get(target) or []
        if open_rows:
            print(
                f"[!] {len(open_rows)} open mutant(s) at this hash — the file is only "
                "partially audited; run `mutants.py open`"
            )
        return EXIT_OK
    if row:
        print(f"[!] not audited at {current}; newest row is {row.composite} ({row.date}, {row.status})")
    else:
        print(f"[!] no audited row for {target}")
    disposition = authoritative_dispositions(ledger).get(target)
    if disposition and _matches(disposition, source_hash, companion_hash):
        print(
            f"[!] {disposition.status} at {current} ({disposition.pr or 'no PR'}): "
            f"{disposition.summary} — suppressed until the content changes"
        )
    return EXIT_NEGATIVE


def _stream_for_target(cfg: Dict[str, Any], target: str) -> Optional[str]:
    """Which configured stream owns this path, if any."""
    for stream in ("A", "B"):
        section = cfg.get("candidates", {}).get(stream, {})
        include = section.get("include") or []
        exclude = section.get("exclude") or []
        if include and match_any(target, include) and not match_any(target, exclude):
            return stream
    return None


def _closed_prs(
    root: Path, limit: int, require_gh: bool
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Closed PRs with their file lists, newest first as gh orders them."""
    fields_variants = (
        "number,title,state,mergedAt,files,labels",
        "number,title,state,mergedAt,files",
    )
    detail = "unknown error"
    for fields in fields_variants:
        argv = [
            "gh",
            "pr",
            "list",
            "--state",
            "closed",
            "--json",
            fields,
            "--limit",
            str(limit),
        ]
        try:
            proc = run(argv, cwd=root, quiet=True)
        except Exception as exc:  # missing tool or timeout
            if require_gh:
                raise EnvError(f"gh not available: {exc}")
            return [], f"gh not available ({exc}); cannot sync rejections"
        if proc.returncode != 0:
            message = (proc.stderr or proc.stdout).strip().splitlines()
            detail = message[0] if message else f"exit {proc.returncode}"
            continue
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            detail = "invalid JSON"
            continue
        if not isinstance(data, list):
            detail = "unexpected payload"
            continue
        return [item for item in data if isinstance(item, dict)], None
    if require_gh:
        raise EnvError(f"gh pr list failed: {detail}")
    return [], f"gh pr list failed ({detail}); cannot sync rejections"


def cmd_sync_rejections(args: argparse.Namespace) -> int:
    """Turn closed-unmerged fleet PRs into REJECTED rows (the fleet's memory of "no")."""
    root = repo_root()
    cfg = load_config(root, args.config)
    ledger = _ledger_path(root, cfg, args.ledger)
    prs, warning = _closed_prs(root, args.limit, args.require_gh)
    if warning:
        print(f"[warning] {warning}", file=sys.stderr)

    audited = authoritative_rows(ledger)
    dispositions = authoritative_dispositions(ledger)
    rows: List[str] = []
    proposed: List[Dict[str, Any]] = []
    seen: set = set()

    for pr in prs:
        if pr.get("mergedAt"):
            continue  # merged PRs are work that landed, not a rejection
        if str(pr.get("state") or "").upper() != "CLOSED":
            continue
        number = pr.get("number")
        title = " ".join(str(pr.get("title") or "").split())
        files = pr.get("files") or []
        if not isinstance(files, list):
            continue
        for item in files:
            path = item.get("path") if isinstance(item, dict) else None
            if not path:
                continue
            target = str(path)
            if target in seen:
                continue
            stream = _stream_for_target(cfg, target)
            if stream is None:
                continue
            if not path_at_head(root, target):
                continue  # file was deleted with the rejected change
            source_hash = hash_at_head(root, target)
            if not source_hash:
                continue
            companion: Optional[str] = None
            companion_hash: Optional[str] = None
            if stream == "A":
                mapped = test_for_source(root, cfg, target)
                if mapped and path_at_head(root, mapped):
                    companion = mapped
                    companion_hash = hash_at_head(root, mapped)
            composite = composite_hash(source_hash, companion_hash)

            already_audited = audited.get(target)
            if already_audited and _matches(already_audited, source_hash, companion_hash):
                continue  # audited since the PR closed; an audit outranks a rejection
            remembered = dispositions.get(target)
            if (
                remembered
                and remembered.composite == composite
                and remembered.status == "REJECTED"
            ):
                continue  # already recorded at this content hash

            seen.add(target)
            summary = f"owner closed #{number} unmerged: {title}".strip()
            trigger = f"Any modification to {Path(target).name}."
            rows.append(
                _row_text(stream, target, composite, "REJECTED", f"#{number}", summary, trigger)
            )
            proposed.append(
                {
                    "target": target,
                    "stream": stream,
                    "composite_hash": composite,
                    "pr": f"#{number}",
                    "summary": summary,
                }
            )

    if rows and not args.dry_run:
        _insert_rows(ledger, rows)

    if args.json:
        json.dump(
            {
                "ledger": str(ledger),
                "dry_run": bool(args.dry_run),
                "recorded": proposed,
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return EXIT_OK

    for item in proposed:
        print(f"[+] REJECTED {item['target']} at {item['composite_hash']} from {item['pr']}")
    if not proposed:
        print("[+] No new closed-unmerged PRs to remember.")
    elif args.dry_run:
        print(f"    (dry run: {len(proposed)} row(s) not written)")
    else:
        print(f"    Wrote {len(proposed)} REJECTED row(s) to {ledger}.")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Autonomous Work Ledger Manager (content-hash based, config-driven)"
    )
    common_parent = argparse.ArgumentParser(add_help=False)
    common_parent.add_argument(
        "--config", help="path to config.json (default: scripts/autonomous/config.json)"
    )
    common_parent.add_argument("--ledger", help="override ledger path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    next_parser = subparsers.add_parser(
        "next", parents=[common_parent], help="find the next unaudited target"
    )
    next_parser.add_argument("--stream", required=True, help="A/B (1/2 accepted as aliases)")
    next_parser.add_argument("--limit", type=int, default=1, help="number of candidates to print")
    next_parser.add_argument("--json", action="store_true", help="machine-readable output")
    next_parser.add_argument("--require-gh", action="store_true", help="fail if gh is unavailable")
    next_parser.add_argument("--verbose", action="store_true", help="print skip reasons")
    next_parser.set_defaults(func=cmd_next)

    record_parser = subparsers.add_parser(
        "record", parents=[common_parent], help="append an audit row"
    )
    record_parser.add_argument("--stream", required=True, help="A/B (1/2 accepted as aliases)")
    record_parser.add_argument("--target", required=True, help="repo-relative target path")
    record_parser.add_argument("--status", required=True, help="COMPLETED or SOUND")
    record_parser.add_argument("--pr", help="PR/issue ref (required for COMPLETED by default)")
    record_parser.add_argument("--summary", required=True, help="what was done")
    record_parser.add_argument("--test", help="companion test path (default: mapped from config)")
    record_parser.add_argument("--trigger", help="re-audit trigger text")
    record_parser.add_argument("--force", action="store_true", help="allow a duplicate row")
    record_parser.add_argument("--strict", action="store_true", help="fail if the target is dirty")
    record_parser.set_defaults(func=cmd_record)

    check_parser = subparsers.add_parser(
        "check", parents=[common_parent], help="exit 0 iff audited at the current hash"
    )
    check_parser.add_argument("--target", required=True, help="repo-relative target path")
    check_parser.add_argument("--stream", default="A", help="A/B (default A)")
    check_parser.set_defaults(func=cmd_check)

    sync_parser = subparsers.add_parser(
        "sync-rejections",
        parents=[common_parent],
        help="record REJECTED rows for closed-unmerged fleet PRs",
    )
    sync_parser.add_argument("--limit", type=int, default=100, help="closed PRs to inspect")
    sync_parser.add_argument("--dry-run", action="store_true", help="show rows without writing")
    sync_parser.add_argument("--json", action="store_true", help="machine-readable output")
    sync_parser.add_argument("--require-gh", action="store_true", help="fail if gh is unavailable")
    sync_parser.set_defaults(func=cmd_sync_rejections)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        return format_exit(exc)


if __name__ == "__main__":
    sys.exit(main())
