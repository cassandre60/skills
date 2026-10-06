#!/usr/bin/env python3
"""Append a compact context label to OpenCode display names.

Why this exists
---------------
A provider block with fifteen models is a wall of similar-looking strings in the picker.
`Antigravity Claude Opus 5.5 High` and `CodeCraft Claude Opus 5.5` differ in ways the eye
has to work at. A two-character label (`_1M`) answers the question you actually have -
"how much context do I get before this compacts?" - without a trip to the config file.

Labels are 2 significant digits with a trailing `.0` stripped, so 1000000 and 1048576
both read `1M` rather than implying precision the gateway never claimed.

Safety properties
-----------------
* touches ONLY the display `name` field
* never changes a model key - the key is what gets sent to the gateway, the name is
  what gets drawn in the picker, and conflating them breaks routing
* never changes `limit` fields (that is sync-limits.py's job)
* idempotent - a prior `_1M`-style label is stripped before re-applying, so re-running
  after a limit change produces a new label instead of `_1M_1.1M`
* takes a backup before writing
* re-reads the file afterwards and asserts the labels are present

Usage
-----
  ./label-models.py                                    # every provider block, live config
  ./label-models.py --provider cliproxy --dry-run
  ./label-models.py --config ~/.config/opencode/opencode.json

With --config omitted, defaults to ~/.config/opencode/opencode.json. Both the singular
`provider` block and the plural `providers` block are updated, because OpenCode overlays
the plural on the singular and a label that appears in only one of them shows up in only
one place.

Models whose entry has no `limit.context` are reported and left alone: there is nothing
to derive a label from, and inventing one is worse than an unlabelled name.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import time
from pathlib import Path

# A label we may have written on a previous run: _1M, _1.1M, _0.13M
LABEL_RE = re.compile(r"_\d+(?:\.\d+)?M$")


def format_label(context: int) -> str:
    """1048576 -> '1M', 1050000 -> '1.1M', 128000 -> '0.13M'."""
    millions = context / 1_000_000
    if millions >= 1:
        rounded = math.floor(millions * 10 + 0.5) / 10
    else:
        rounded = math.floor(millions * 100 + 0.5) / 100
    text = f"{rounded:.2f}".rstrip("0").rstrip(".")
    return f"{text}M"


def backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = path.with_suffix(path.suffix + f".bak-{stamp}")
    shutil.copy2(path, dest)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config",
                        default=os.environ.get("OPENCODE_CONFIG") or "~/.config/opencode/opencode.json")
    parser.add_argument("--provider", default="all",
                        help="provider block to label, or 'all' (default)")
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    if not config_path.is_file():
        print(f"error: config not found: {config_path}", file=sys.stderr)
        return 2

    original_text = config_path.read_text()
    try:
        config = json.loads(original_text)
    except json.JSONDecodeError as exc:
        print(f"error: config is not valid JSON: {exc}", file=sys.stderr)
        return 2

    changed: list[tuple[str, str]] = []
    unchanged: list[str] = []
    unlabelled: list[str] = []

    for section in ("provider", "providers"):
        providers = config.get(section)
        if not isinstance(providers, dict):
            continue
        targets = (
            [args.provider] if args.provider != "all"
            else [name for name, block in providers.items()
                  if isinstance(block, dict) and isinstance(block.get("models"), dict)]
        )
        for provider_name in targets:
            provider = providers.get(provider_name)
            if not isinstance(provider, dict):
                continue
            models = provider.get("models")
            if not isinstance(models, dict):
                continue
            for model_id, entry in models.items():
                if not isinstance(entry, dict):
                    continue
                context = (entry.get("limit") or {}).get("context")
                if not isinstance(context, int) or context <= 0:
                    unlabelled.append(f"{section}:{provider_name}/{model_id}")
                    continue

                label = format_label(context)
                # Display name may be absent entirely; the key is then what the picker
                # shows, so label the key rather than inventing a marketing name.
                base = entry.get("name") or model_id
                before = base
                stripped = LABEL_RE.sub("", base).rstrip()
                after = f"{stripped}_{label}"

                label_str = f"{section}:{provider_name}/{model_id}"
                if before == after:
                    unchanged.append(label_str)
                    continue
                entry["name"] = after
                changed.append((label_str, f"{before!r} -> {after!r}"))

    total = len(changed) + len(unchanged)
    print(f"labelled: {len(changed)}  already correct: {len(unchanged)}  "
          f"no context data: {len(set(unlabelled))}")

    if changed:
        print("\nchanges:")
        for label, change in changed[:25]:
            print(f"  {label:<58} {change}")
        if len(changed) > 25:
            print(f"  ... and {len(changed) - 25} more")

    if unlabelled:
        preview = ", ".join(sorted(set(unlabelled))[:6])
        print(f"\nno limit.context for these ({len(set(unlabelled))} entries) - left alone:\n  {preview}")
        print("  source limits first: sync-limits.py --provider <name>")

    if args.dry_run:
        print("\ndry run - nothing written")
        return 0

    if not changed:
        print("\nnothing to do")
        return 0

    dest = backup(config_path)
    config_path.write_text(json.dumps(config, indent=2) + "\n")

    # Assert on the artifact, not on the success message.
    written = json.loads(config_path.read_text())
    mismatches = []
    for section in ("provider", "providers"):
        for provider_name, provider in (written.get(section) or {}).items():
            if not isinstance(provider, dict):
                continue
            for model_id, entry in (provider.get("models") or {}).items():
                if not isinstance(entry, dict):
                    continue
                context = (entry.get("limit") or {}).get("context")
                if not isinstance(context, int) or context <= 0:
                    continue
                if not str(entry.get("name", "")).endswith(format_label(context)):
                    mismatches.append(f"{section}:{provider_name}/{model_id}")

    print(f"\nbackup: {dest}")
    print(f"wrote:  {config_path}")
    if mismatches:
        print(f"error: {len(mismatches)} entries do not carry the expected label", file=sys.stderr)
        for item in mismatches[:10]:
            print(f"  {item}", file=sys.stderr)
        return 1
    print(f"verified: all {total} labelled entries present in the written file")
    print("display only - model keys and limits untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
