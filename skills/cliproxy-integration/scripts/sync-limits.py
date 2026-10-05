#!/usr/bin/env python3
"""Re-source OpenCode model limits from a live gateway catalog.

Why this exists
---------------
`limit.context` and `limit.output` are OpenCode-side metadata describing a model's real
capacity. Get them wrong and OpenCode compacts context too early and truncates long replies
- you pay for the tokens either way. Guessing is worse than sourcing.

Some gateways (and some management panels) rewrite their provider block in the OpenCode config
with flat placeholder limits. Re-run this afterwards to restore real values.

Safety properties
-----------------
* touches ONLY limit.context / limit.output
* never adds or removes models
* leaves models absent from the live catalog completely untouched
* idempotent - safe to run repeatedly
* takes a backup before writing

Usage
-----
  OMNIROUTE_API_KEY=sk-... ./sync-limits.py \
      --base-url http://localhost:20128/v1 \
      --config ~/.config/opencode/opencode.json \
      --provider omniroute

If --config is omitted, defaults to ~/.config/opencode/opencode.json.
OpenCode keys to update are the provider's model keys in both the singular `provider` block and
the plural `providers` block (they should stay identical).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SECONDS_PER_DAY = 86400


def fetch_catalog(base_url: str, api_key: str, timeout: int) -> dict[str, tuple[int, int]]:
    url = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)

    entries = payload.get("data", payload) if isinstance(payload, dict) else payload
    catalog: dict[str, tuple[int, int]] = {}
    for item in entries or []:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not model_id:
            continue
        ctx = item.get("context_length") or item.get("max_context_window_tokens")
        out = item.get("max_output_tokens")
        ctx = ctx if isinstance(ctx, int) and ctx > 0 else None
        out = out if isinstance(out, int) and out > 0 else None
        catalog[model_id] = (ctx, out)
    return catalog


def backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = path.with_suffix(path.suffix + f".bak-{stamp}")
    shutil.copy2(path, dest)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=os.environ.get("GATEWAY_BASE_URL"),
                        help="gateway base URL (e.g. http://localhost:20128/v1)")
    parser.add_argument("--api-key", default=None,
                        help="gateway API key (overrides GATEWAY_API_KEY env)")
    parser.add_argument("--config",
                        default=os.environ.get("OPENCODE_CONFIG") or "~/.config/opencode/opencode.json")
    parser.add_argument("--provider", default=os.environ.get("OPENCODE_PROVIDER", "omniroute"))
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing")
    args = parser.parse_args()

    if not args.base_url:
        print("error: --base-url or GATEWAY_BASE_URL is required", file=sys.stderr)
        return 2

    api_key = args.api_key or os.environ.get("GATEWAY_API_KEY", "")
    if not api_key:
        print("warning: no --api-key / GATEWAY_API_KEY; catalog fetch may be rejected",
              file=sys.stderr)

    config_path = Path(args.config).expanduser()
    if not config_path.is_file():
        print(f"error: config not found: {config_path}", file=sys.stderr)
        return 2

    try:
        catalog = fetch_catalog(args.base_url, api_key, args.timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"error: could not read catalog: {exc}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, OSError) as exc:
        print(f"error: bad catalog response: {exc}", file=sys.stderr)
        return 2

    if not catalog:
        print("error: catalog empty - refusing to touch the config", file=sys.stderr)
        return 2

    config = json.loads(config_path.read_text())

    updated: list[tuple[str, str, tuple[int, int]]] = []
    unchanged: list[str] = []
    absent: list[str] = []
    no_limits: list[str] = []

    # The plural block is overlaid on the singular one by OpenCode; keep both in step.
    for section in ("provider", "providers"):
        provider = (config.get(section) or {}).get(args.provider)
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        for model_id, entry in models.items():
            if not isinstance(entry, dict):
                continue
            limits = entry.setdefault("limit", {})
            ctx, out = catalog.get(model_id, (None, None))
            if model_id not in catalog:
                absent.append(f"{section}:{model_id}")
                continue
            if ctx is None:
                # In the catalog, but the gateway publishes no context limit for it.
                no_limits.append(f"{section}:{model_id}")
                continue
            before = (limits.get("context"), limits.get("output"))
            # A catalog may publish context but not output. In that case keep whatever the
            # config already has - otherwise the comparison below never matches and the
            # script reports a change on every run (non-idempotent).
            after = (ctx, out if out is not None else before[1])
            if before == after:
                unchanged.append(f"{section}:{model_id}")
                continue
            limits["context"] = ctx
            if out is not None:
                limits["output"] = out
            updated.append((f"{section}:{model_id}", f"{before} -> {after}", after))

    print(f"catalog: {len(catalog)} models from {args.base_url}")
    print(f"updated: {len(updated)}  unchanged: {len(unchanged)}  "
          f"no-limit-data: {len(set(no_limits))}  absent-from-catalog: {len(set(absent))}")

    if updated:
        print("\nchanges:")
        for label, change, _ in updated[:25]:
            print(f"  {label:<52} {change}")
        if len(updated) > 25:
            print(f"  ... and {len(updated) - 25} more")

    if no_limits:
        preview = ", ".join(sorted({m.split(":", 1)[1] for m in no_limits})[:6])
        print(f"\nthis gateway publishes no context limit for these "
              f"({len(set(no_limits))} entries) - left alone:\n  {preview}")
        print("  source limits from provider metadata or a sibling gateway instead")

    if absent:
        preview = ", ".join(sorted({m.split(":", 1)[1] for m in absent})[:6])
        print(f"\nnot served by this gateway ({len(set(absent))} entries) - "
              f"probably stale config:\n  {preview}")
        print("  verify with: probe-models.sh --model <id>")

    if args.dry_run:
        print("\ndry run - nothing written")
        return 0

    if updated:
        dest = backup(config_path)
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        print(f"\nbackup: {dest}")
        print(f"wrote:  {config_path}")

    # Fail loudly if we just wrote something OpenCode would reject.
    try:
        json.loads(config_path.read_text())
    except json.JSONDecodeError as exc:
        print(f"error: wrote invalid JSON: {exc}", file=sys.stderr)
        return 1
    print("config parses as valid JSON")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())