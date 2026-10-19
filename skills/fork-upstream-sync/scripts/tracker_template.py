#!/usr/bin/env python3
"""Universal Upstream Tracker for Fork Repositories.

Zero-dependency Python 3 script. Reads configuration from .upstream/config.json,
tracks evaluated commits in .upstream/ledger.json, and deterministically
classifies upstream commits into CANDIDATE, APPLIED, or SKIPPED states.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_CONFIG_PATH = Path(".upstream/config.json")
DEFAULT_LEDGER_PATH = Path(".upstream/ledger.json")


def run_git(args: List[str], cwd: Optional[Path] = None) -> str:
    res = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def extract_pr_number(title: str) -> Optional[int]:
    match = re.search(r"\(#(\d+)\)", title)
    if match:
        return int(match.group(1))
    match = re.search(r"\bPR\s*#?(\d+)\b", title, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"Merge pull request #(\d+)", title, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {
            "upstream_repo": "",
            "base_sha": "",
            "cut_paths": [],
            "chore_subjects": [
                r"^Bump version",
                r"^New translations",
                r"Crowdin translations",
            ],
            "chore_files": [
                r"\.arb$",
                r"\.po$",
                r"Localizable\.xcstrings$",
                r"^\.gitattributes$",
            ],
            "known_applied": {},
        }
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_ledger(path: Path = DEFAULT_LEDGER_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {
            "version": 1,
            "upstream_repo": "",
            "last_synced_sha": None,
            "last_synced_date": None,
            "stats": {"total": 0, "candidates": 0, "applied": 0, "skipped": 0},
            "commits": {},
        }
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_ledger(ledger: Dict[str, Any], path: Path = DEFAULT_LEDGER_PATH) -> None:
    commits = ledger.get("commits", {})
    candidates = sum(1 for c in commits.values() if c.get("status") == "CANDIDATE")
    applied = sum(1 for c in commits.values() if c.get("status") == "APPLIED")
    skipped = sum(1 for c in commits.values() if str(c.get("status", "")).startswith("SKIPPED"))

    ledger["stats"] = {
        "total": len(commits),
        "candidates": candidates,
        "applied": applied,
        "skipped": skipped,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, sort_keys=True)
        f.write("\n")


def categorize_candidate(files: List[str]) -> str:
    if any(f in ("pubspec.yaml", "package.json", "Cargo.toml", "pyproject.toml", "go.mod") for f in files):
        return "dependency"
    if any(f.startswith(".github/") or f.startswith(".circleci/") or "ci" in f.lower() for f in files):
        return "ci_workflow"
    if any(f.startswith("test/") or f.startswith("tests/") or "test" in f.lower() for f in files):
        return "test"
    if any(f.startswith("docs/") or f.endswith(".md") for f in files):
        return "docs"
    return "logic"


def classify_commit(
    sha: str,
    subject: str,
    files: List[str],
    config: Dict[str, Any],
    repo_root: Path,
) -> Tuple[str, str, List[str]]:
    pr_num = extract_pr_number(subject)
    known_applied = config.get("known_applied", {})

    for known_sha, meta in known_applied.items():
        if sha.startswith(known_sha) or (pr_num is not None and meta.get("pr") == pr_num):
            return "APPLIED", "applied", [f for f in files if (repo_root / f).exists()]

    # Chores by commit subject regex
    for pat in config.get("chore_subjects", []):
        if re.search(pat, subject, re.IGNORECASE):
            return "SKIPPED_CHORE", "chore", []

    cut_prefixes = tuple(config.get("cut_paths", []))
    chore_file_pats = [re.compile(p) for p in config.get("chore_files", [])]

    surviving_files: List[str] = []
    cut_files: List[str] = []
    not_in_fork_files: List[str] = []
    chore_files: List[str] = []

    for f in files:
        if cut_prefixes and any(f.startswith(p) for p in cut_prefixes):
            cut_files.append(f)
        elif any(pat.search(f) for pat in chore_file_pats):
            chore_files.append(f)
        elif (repo_root / f).exists():
            surviving_files.append(f)
        else:
            not_in_fork_files.append(f)

    if not surviving_files:
        if cut_files:
            return "SKIPPED_CUT", "cut", []
        if chore_files:
            return "SKIPPED_CHORE", "chore", []
        return "SKIPPED_NOT_IN_FORK", "not_in_fork", []

    if all(any(pat.search(f) for pat in chore_file_pats) for f in surviving_files):
        return "SKIPPED_CHORE", "chore", []

    category = categorize_candidate(surviving_files)
    return "CANDIDATE", category, surviving_files


def cmd_sync(args: argparse.Namespace, repo_root: Path) -> int:
    config = load_config(DEFAULT_CONFIG_PATH)
    ledger = load_ledger(DEFAULT_LEDGER_PATH)

    upstream_repo = config.get("upstream_repo") or ledger.get("upstream_repo")
    if not upstream_repo:
        print("Error: upstream_repo is not configured in .upstream/config.json", file=sys.stderr)
        return 1

    base_sha = ledger.get("last_synced_sha") or config.get("base_sha")
    if not base_sha:
        print("Error: No base_sha found in ledger or config. Run init first.", file=sys.stderr)
        return 1

    remotes = run_git(["remote"], cwd=repo_root).split()
    if "upstream" not in remotes:
        print(f"Adding remote 'upstream' pointing to https://github.com/{upstream_repo}.git")
        run_git(["remote", "add", "upstream", f"https://github.com/{upstream_repo}.git"], cwd=repo_root)
        run_git(["remote", "set-url", "--push", "upstream", "no_push"], cwd=repo_root)

    upstream_branch = config.get("upstream_branch", "main")
    if args.fetch:
        print(f"Fetching upstream {upstream_branch} (depth: {args.depth})...")
        run_git(["fetch", f"--depth={args.depth}", "upstream", upstream_branch, "--no-tags"], cwd=repo_root)

    try:
        run_git(["rev-parse", "--verify", base_sha], cwd=repo_root)
    except subprocess.CalledProcessError:
        print(f"Base commit {base_sha[:9]} not found in shallow history. Fetching deeper...")
        run_git(["fetch", "--deepen=100", "upstream", upstream_branch, "--no-tags"], cwd=repo_root)

    rev_list = run_git(
        ["rev-list", "--reverse", f"{base_sha}..upstream/{upstream_branch}"],
        cwd=repo_root,
    )
    shas = [s.strip() for s in rev_list.split() if s.strip()]

    if not shas:
        print(f"Up to date. No new upstream commits found since {base_sha[:9]}.")
        return 0

    print(f"Found {len(shas)} new upstream commits to evaluate since {base_sha[:9]}.")

    commits_dict = ledger.setdefault("commits", {})
    new_candidates = 0
    new_skipped = 0
    new_applied = 0

    for sha in shas:
        short_sha = sha[:9]
        if short_sha in commits_dict or sha in commits_dict:
            continue

        subject = run_git(["log", "-1", "--format=%s", sha], cwd=repo_root)
        author = run_git(["log", "-1", "--format=%an", sha], cwd=repo_root)
        date = run_git(["log", "-1", "--format=%cI", sha], cwd=repo_root)
        files_raw = run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", sha], cwd=repo_root)
        files = [f.strip() for f in files_raw.splitlines() if f.strip()]

        status, category, surviving_files = classify_commit(sha, subject, files, config, repo_root)
        pr_num = extract_pr_number(subject)

        if status == "APPLIED":
            new_applied += 1
        elif status == "CANDIDATE":
            new_candidates += 1
        else:
            new_skipped += 1

        commits_dict[short_sha] = {
            "sha": sha,
            "short_sha": short_sha,
            "pr": pr_num,
            "title": subject,
            "author": author,
            "date": date,
            "status": status,
            "category": category,
            "surviving_files": surviving_files,
            "all_files": files,
            "fork_pr": None,
            "notes": "",
        }

    latest_sha = shas[-1]
    ledger["upstream_repo"] = upstream_repo
    ledger["last_synced_sha"] = latest_sha
    ledger["last_synced_date"] = datetime.now(timezone.utc).isoformat()
    save_ledger(ledger, DEFAULT_LEDGER_PATH)

    print("\n--- Sync Summary ---")
    print(f"Total new commits processed: {len(shas)}")
    print(f"  - Candidates requiring review: {new_candidates}")
    print(f"  - Applied: {new_applied}")
    print(f"  - Skipped (cut/chore/deleted): {new_skipped}")
    print(f"Latest synced commit: {latest_sha[:9]}")
    print(f"Ledger saved to {DEFAULT_LEDGER_PATH}")

    if new_candidates > 0:
        print(f"\nRun `python3 {sys.argv[0]} candidates` to inspect active candidates.")
    return 0


def cmd_status(args: argparse.Namespace, repo_root: Path) -> int:
    config = load_config(DEFAULT_CONFIG_PATH)
    ledger = load_ledger(DEFAULT_LEDGER_PATH)
    stats = ledger.get("stats", {})
    last_sha = ledger.get("last_synced_sha") or config.get("base_sha", "unknown")
    last_date = ledger.get("last_synced_date", "never")

    print("=== Upstream Fork Tracker Status ===")
    print(f"Upstream repository : {config.get('upstream_repo') or ledger.get('upstream_repo', 'not set')}")
    print(f"Last synced commit  : {last_sha[:9]} ({last_date})")
    print(f"Total commits logged: {stats.get('total', 0)}")
    print(f"  - Candidates      : {stats.get('candidates', 0)}")
    print(f"  - Applied         : {stats.get('applied', 0)}")
    print(f"  - Skipped         : {stats.get('skipped', 0)}")

    candidates = [c for c in ledger.get("commits", {}).values() if c.get("status") == "CANDIDATE"]
    if candidates:
        print(f"\nActive Candidates ({len(candidates)}):")
        for c in candidates:
            pr_label = f"#{c['pr']}" if c.get("pr") else c["short_sha"]
            print(f"  [{c['short_sha']}] {pr_label:8} ({c.get('category', 'general'):11}) : {c['title']}")
    else:
        print("\nNo pending candidates.")
    return 0


def cmd_candidates(args: argparse.Namespace, repo_root: Path) -> int:
    config = load_config(DEFAULT_CONFIG_PATH)
    ledger = load_ledger(DEFAULT_LEDGER_PATH)
    upstream_repo = config.get("upstream_repo") or ledger.get("upstream_repo", "")
    candidates = [c for c in ledger.get("commits", {}).values() if c.get("status") == "CANDIDATE"]

    if not candidates:
        if args.format == "json":
            print(json.dumps([], indent=2))
        else:
            print("No pending upstream candidates to review.")
        return 0

    if args.format == "json":
        print(json.dumps(candidates, indent=2))
        return 0

    if args.format == "markdown":
        lines = [
            "## Upstream Candidates for Review",
            "",
            f"Found **{len(candidates)}** upstream changes affecting files in this fork.",
            "",
            "| Upstream | Title | Category | Shared Files |",
            "|---|---|---|---|",
        ]
        for c in candidates:
            pr_str = f"[#{c['pr']}](https://github.com/{upstream_repo}/pull/{c['pr']})" if c.get("pr") else f"[`{c['short_sha']}`](https://github.com/{upstream_repo}/commit/{c['sha']})"
            files_summary = "<br>".join(f"`{f}`" for f in c.get("surviving_files", [])[:3])
            if len(c.get("surviving_files", [])) > 3:
                files_summary += f"<br>*(+{len(c['surviving_files']) - 3} more)*"
            clean_title = c['title'].replace("|", "\\|")
            lines.append(f"| {pr_str} | {clean_title} | `{c.get('category', 'general')}` | {files_summary} |")
        print("\n".join(lines))
        return 0

    print(f"=== {len(candidates)} Pending Upstream Candidates ===\n")
    for idx, c in enumerate(candidates, 1):
        pr_url = f"https://github.com/{upstream_repo}/pull/{c['pr']}" if c.get("pr") else f"https://github.com/{upstream_repo}/commit/{c['sha']}"
        print(f"{idx}. [{c['short_sha']}] {c['title']}")
        print(f"   Category: {c.get('category')}")
        print(f"   Upstream: {pr_url}")
        print("   Shared files:")
        for f in c.get("surviving_files", []):
            print(f"     - {f}")
        print()
    return 0


def cmd_mark(args: argparse.Namespace, repo_root: Path) -> int:
    ledger = load_ledger(DEFAULT_LEDGER_PATH)
    commits = ledger.setdefault("commits", {})
    target = args.target

    match_key = None
    target_pr = int(target) if target.isdigit() else None

    for k, c in commits.items():
        if k.startswith(target) or c.get("sha", "").startswith(target):
            match_key = k
            break
        if target_pr is not None and c.get("pr") == target_pr:
            match_key = k
            break

    if not match_key:
        print(f"Error: Commit or PR '{target}' not found in ledger.", file=sys.stderr)
        return 1

    entry = commits[match_key]
    old_status = entry.get("status")
    entry["status"] = args.status
    if args.fork_pr:
        entry["fork_pr"] = args.fork_pr
    if args.notes:
        entry["notes"] = args.notes

    save_ledger(ledger, DEFAULT_LEDGER_PATH)
    print(f"Updated {match_key} ({entry.get('title')}): {old_status} -> {args.status}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Universal Upstream Fork Tracker")
    subparsers = parser.add_subparsers(dest="command")

    p_sync = subparsers.add_parser("sync", help="Fetch and classify new upstream commits")
    p_sync.add_argument("--fetch", action="store_true", default=True, help="Fetch upstream first (default: true)")
    p_sync.add_argument("--no-fetch", action="store_false", dest="fetch", help="Do not fetch upstream")
    p_sync.add_argument("--depth", type=int, default=100, help="Fetch depth (default: 100)")

    subparsers.add_parser("status", help="Show summary status of the ledger")

    p_cand = subparsers.add_parser("candidates", help="List active candidates")
    p_cand.add_argument("--format", choices=["text", "markdown", "json"], default="text", help="Output format")

    p_mark = subparsers.add_parser("mark", help="Mark the status of a commit or PR")
    p_mark.add_argument("target", help="SHA prefix or PR number")
    p_mark.add_argument(
        "status",
        choices=["CANDIDATE", "APPLIED", "SKIPPED_CUT", "SKIPPED_VISUAL", "SKIPPED_CHORE", "SKIPPED_NOT_IN_FORK", "BACKLOG"],
        help="New evaluation status",
    )
    p_mark.add_argument("--fork-pr", type=int, help="Associated fork PR number")
    p_mark.add_argument("--notes", type=str, default="", help="Optional notes")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0

    repo_root = Path.cwd()
    if args.command == "sync":
        return cmd_sync(args, repo_root)
    elif args.command == "status":
        return cmd_status(args, repo_root)
    elif args.command == "candidates":
        return cmd_candidates(args, repo_root)
    elif args.command == "mark":
        return cmd_mark(args, repo_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
