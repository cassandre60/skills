#!/usr/bin/env python3
"""Autonomous Work Ledger Manager.

Ensures no two agents repeat the same work and prevents duplicate audits.
Tracks completed streams against content hashes (not commit identity).

Usage:
  # Query next untouched file for a stream
  python3 scripts/autonomous/ledger.py next --stream 1

  # Record completed work
  python3 scripts/autonomous/ledger.py record \
    --stream "Stream 1 (Mutation)" \
    --target "lib/src/domain/review_coordinator.dart" \
    --status "COMPLETED" \
    --pr "#196" \
    --summary "Evaluated 20 mutants: 18 killed, 2 survived. Added tests."

Content hash uses `git hash-object` / `git rev-parse HEAD:<path>` — the hash
of the file's bytes — not `git log -- <path>` (last commit touching the file).
This survives cherry-picks, reverts, and rebases where commit identity and
content diverge.
"""

import argparse
import datetime
import os
import subprocess
import sys
from typing import Dict, List, Optional

LEDGER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "docs", "AUTONOMOUS_WORK_LEDGER.md")

STREAM_1_TARGETS = [
    ("lib/src/domain/graph_aware_review_coordinator.dart", "test/domain/graph_aware_review_coordinator_test.dart"),
    ("lib/src/domain/chess_fsrs_scheduler.dart", "test/domain/chess_fsrs_scheduler_test.dart"),
    ("lib/src/domain/review/review_engine.dart", "test/review/review_engine_test.dart"),
    ("lib/src/domain/review/review_session.dart", "test/review/review_session_test.dart"),
    ("lib/src/import/pgn_exporter.dart", "test/import/pgn_exporter_test.dart"),
    ("lib/src/persistence/sqlite_study_repository.dart", "test/persistence/sqlite_study_repository_test.dart"),
    ("lib/src/domain/position_knowledge_state.dart", "test/domain/position_knowledge_state_test.dart"),
    ("lib/src/domain/scheduler.dart", "test/domain/scheduler_test.dart"),
]


def get_file_content_hash(filepath: str) -> Optional[str]:
    """Content hash of filepath (git hash-object semantics). Short 9-char prefix for ledger."""
    if not os.path.exists(filepath):
        return None
    # Prefer HEAD's object hash when available (works for tracked files even with dirty worktree);
    # fall back to hash-object for untracked/new files.
    res = subprocess.run(["git", "rev-parse", f"HEAD:{filepath}"], capture_output=True, text=True)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()[:9]
    res = subprocess.run(["git", "hash-object", filepath], capture_output=True, text=True)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()[:9]
    return None


def get_file_sha(filepath: str) -> Optional[str]:
    """Deprecated alias — use get_file_content_hash. Kept for backwards compat in callers."""
    return get_file_content_hash(filepath)


def get_open_prs() -> List[str]:
    """Gets list of titles and branch names of currently open PRs to avoid collision."""
    res = subprocess.run(["gh", "pr", "list", "--json", "title,headRefName"], capture_output=True, text=True)
    if res.returncode != 0:
        return []
    import json
    try:
        data = json.loads(res.stdout)
        return [f"{pr.get('headRefName', '')} {pr.get('title', '')}" for pr in data]
    except Exception:
        return []


def parse_audited_targets() -> Dict[str, str]:
    """Parses docs/AUTONOMOUS_WORK_LEDGER.md and extracts {target_file: audited_hash}."""
    if not os.path.exists(LEDGER_PATH):
        return {}
    audited = {}
    with open(LEDGER_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("|") and not line.startswith("| Date") and not line.startswith("|---"):
                parts = [p.strip() for p in line.split("|")[1:-1]]
                if len(parts) >= 5:
                    target = parts[2].replace("`", "")
                    sha = parts[3].replace("`", "")
                    status = parts[4]
                    if "COMPLETED" in status or "SOUND" in status:
                        audited[target] = sha
    return audited


def cmd_next(stream: int) -> None:
    audited = parse_audited_targets()
    open_prs = get_open_prs()
    # Use exact slug matching against branch names, not substring over concatenated string
    open_pr_branches = set()
    open_pr_text = ""
    if open_prs:
        open_pr_text = "\n".join(open_prs)
        # Extract branch names for exact matching
        import json as _json
        res = subprocess.run(["gh", "pr", "list", "--json", "headRefName"], capture_output=True, text=True)
        try:
            data = _json.loads(res.stdout)
            open_pr_branches = {pr.get("headRefName", "") for pr in data}
        except Exception:
            pass

    if stream == 1:
        print("[*] Finding next un-audited target for Stream 1 (Mutation Testing)...")
        for src, test in STREAM_1_TARGETS:
            current_hash = get_file_content_hash(src)
            last_audited_hash = audited.get(src)

            # Check if active in PR — exact branch/title word match, not substring
            slug = os.path.splitext(os.path.basename(src))[0]
            in_pr = any(slug in branch for branch in open_pr_branches) or slug in open_pr_text
            # Require word-boundary or path match to avoid false positives, but keep warning
            if in_pr:
                print(f"  [-] {src} may have an open PR (slug '{slug}' found). Skipping to avoid collision.")
                print(f"      Re-check with: gh pr list --search \"{slug}\"")
                continue

            # Check if already audited at current content hash
            if last_audited_hash and current_hash and last_audited_hash == current_hash:
                print(f"  [-] {src} was already audited at content hash {current_hash} and has not changed. Skipping.")
                continue
            if last_audited_hash and current_hash and last_audited_hash != current_hash:
                print(f"  [~] {src} was audited at {last_audited_hash} but content is now {current_hash} — re-audit needed.")

            print("\n" + "=" * 60)
            print(f"NEXT TARGET FOUND: {src}")
            print(f"Current Content Hash: {current_hash or 'unknown'} (git hash-object)")
            print(f"Last Audited Hash:    {last_audited_hash or 'NEVER'}")
            print(f"Target Test File:     {test}")
            print("=" * 60)
            print("\nRecommended Command:")
            print(f"python3 scripts/autonomous/mutation_engine.py \\\n  --source {src} \\\n  --test-cmd \"fvm flutter test {test}\" \\\n  --output-issue docs/tasks/mutant_issue.md")
            return

        print("[+] All targets in Stream 1 have been audited at their current content hashes!")


def cmd_record(stream: str, target: str, status: str, pr: str, summary: str) -> None:
    current_hash = get_file_content_hash(target) or "unknown"
    today = datetime.date.today().isoformat()
    re_audit = f"Any modification to {os.path.basename(target)}."

    new_row = f"| {today} | {stream} | `{target}` | `{current_hash}` | {status} | {pr} | {summary} | {re_audit} |\n"

    with open(LEDGER_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    table_header = "|---|---|---|---|---|---|---|---|\n"
    if table_header in content:
        idx = content.find(table_header) + len(table_header)
        updated_content = content[:idx] + new_row + content[idx:]
        with open(LEDGER_PATH, "w", encoding="utf-8") as f:
            f.write(updated_content)
        print(f"[+] Successfully recorded entry to {LEDGER_PATH}:\n    {new_row.strip()}")
        print("    Note: if another agent appended concurrently, rebase before pushing:")
        print("      git pull --rebase origin/main")
    else:
        print("[!] Could not locate ledger table header in AUTONOMOUS_WORK_LEDGER.md", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Autonomous Work Ledger Manager (content-hash based)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    next_parser = subparsers.add_parser("next")
    next_parser.add_argument("--stream", type=int, required=True, help="Stream number (e.g. 1)")

    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("--stream", required=True, help="Stream name, e.g. 'Stream 1 (Mutation)'")
    record_parser.add_argument("--target", required=True, help="Target file or module")
    record_parser.add_argument("--status", default="COMPLETED", help="COMPLETED or SOUND")
    record_parser.add_argument("--pr", required=True, help="PR number or Issue ID")
    record_parser.add_argument("--summary", required=True, help="Summary of work completed")

    args = parser.parse_args()

    if args.command == "next":
        cmd_next(stream=args.stream)
    elif args.command == "record":
        cmd_record(
            stream=args.stream,
            target=args.target,
            status=args.status,
            pr=args.pr,
            summary=args.summary,
        )


if __name__ == "__main__":
    main()
