#!/usr/bin/env python3
"""Onboard a file of API keys into a gateway provider group + register models in OpenCode.

The use case this exists for: a vendor sold you (or you accumulated) a text file of API keys,
some working and some not, and you want a working provider in your gateway with a couple of
specific models showing up in OpenCode's picker - without hand-editing two config files.

What it does, in order:

  1. Extract candidate keys from a text file (skipping prose / junk lines).
  2. Validate each key with ONE request against the provider's model-list endpoint.
     That single response also yields the models the key can actually serve, which is how
     we check that a requested model ID is real instead of plausible.
  3. Keep only the keys that work. Report how many died and why.
  4. Upsert a provider group in the gateway config containing only the good keys.
  5. Register the requested models in the OpenCode config (both the singular `provider`
     and plural `providers` blocks, which must stay identical).
  6. Back up both files before writing.
  7. Probe each model through the gateway (--gateway-base + --gateway-key) so you learn
     whether it actually answers.

Dry-run by default. Nothing is written without --apply.

Safety properties:
  - dry-run unless --apply
  - backs up every file it touches, naming the backup
  - never invents a key: only lines from your file are used
  - never invents a model: requested models are checked against the provider's own listing
  - never writes a guessed limit: models with no published limit are reported, not fabricated
  - idempotent: re-running replaces the group rather than appending duplicates

Examples
--------
  # see what you have, change nothing
  ./onboard-keys.py --keys-file keys.txt --model gemini-2.5-flash

  # do it
  ./onboard-keys.py --keys-file keys.txt --model gemini-2.5-flash --apply

  # different vendor, your own endpoints
  ./onboard-keys.py --keys-file keys.txt --provider openai-compatibility \\
      --base-url https://api.vendor.example/v1 --list-path models \\
      --key-pattern 'sk-[A-Za-z0-9_-]{20,}' --model some-model --apply
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Provider presets: where to list models, and what to call the group in gateway config.
PROVIDERS = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com",
        "list_path": "/v1beta/models",
        "key_pattern": r"^AIza[0-9A-Za-z_\-]{20,}$",
        "auth": "query",
    },
    "openai-compatibility": {
        "base_url": "",
        "list_path": "/models",
        "key_pattern": r"^sk-[A-Za-z0-9_\-]{20,}$",
        "auth": "header",
    },
}

DEFAULT_GATEWAY_CONFIG = "~/.cli-proxy-api/config.yaml"
DEFAULT_OPENCODE_CONFIG = "~/.config/opencode/opencode.json"


# --------------------------------------------------------------------------- keys

def extract_keys(path: Path, pattern: str) -> tuple[list[str], int]:
    """Pull candidate keys from a messy text file.

    Real key dumps are rarely clean: vendors ship notes, blank lines, URLs, separators.
    We take any line matching the pattern and ignore everything else.
    """
    rx = re.compile(pattern)
    keys: list[str] = []
    seen: set[str] = set()
    total_lines = 0
    for raw in path.read_text(errors="replace").splitlines():
        total_lines += 1
        line = raw.strip().strip('"').strip("'").strip()
        if not line or rx.match(line):
            if line and line not in seen:
                seen.add(line)
                keys.append(line)
    return keys, total_lines


def validate_key(key: str, base_url: str, list_path: str, timeout: int,
                 auth_style: str = "query") -> dict:
    """One request. Returns validity plus the model list that key can serve.

    auth_style "query" (Gemini) sends the key as ?key=; "header" (OpenAI-style)
    sends Authorization: Bearer. Using the wrong one makes every key fail, which
    is exactly the kind of false negative this script must never produce.
    """
    url = f"{base_url.rstrip('/')}{list_path}"
    headers = {"Accept": "application/json"}
    if auth_style == "header":
        headers["Authorization"] = f"Bearer {key}"
    else:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}key={urllib.parse.quote(key)}"
    req = urllib.request.Request(url, headers=headers)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode(errors="replace")[:200]
        except Exception:
            pass
        if exc.code == 429:
            return {"ok": False, "why": "rate-limited", "detail": exc.code, "ms": 0}
        return {"ok": False, "why": "rejected", "detail": f"HTTP {exc.code}", "ms": 0}
    except Exception as exc:  # timeout, DNS, TLS...
        return {"ok": False, "why": "error", "detail": type(exc).__name__, "ms": 0}

    models = []
    for item in (payload.get("models") or payload.get("data") or []):
        name = item.get("name") or item.get("id")
        if name:
            # Gemini returns "models/gemini-2.5-flash"; the gateway wants the bare id.
            models.append(name.split("/")[-1])
    return {"ok": True, "why": "ok", "detail": f"{len(models)} models",
            "models": models, "ms": int((time.time() - started) * 1000)}


# ----------------------------------------------------------------------- gateway yaml

def find_key_line(lines: list[str], key: str, indent: int) -> int:
    """Index of a line whose stripped content starts with `key:` at the given indent."""
    prefix = " " * indent + key
    for i, line in enumerate(lines):
        if line.startswith(prefix) and line[len(prefix):].lstrip().startswith(":"):
            return i
    return -1


def block_extent(lines: list[str], start: int, indent: int) -> int:
    """End (exclusive) of a block that begins at `start`: every following line indented
    deeper than `indent`, plus interior blanks. Stops at the first line at or above."""
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if not line.strip():
            continue
        current = len(line) - len(line.lstrip())
        if current <= indent:
            return j
    return len(lines)


def upsert_gateway_group(config_path: Path, provider: str, group_name: str,
                         keys: list[str], base_url: str | None = None,
                         models: list[tuple[str, str | None]] | None = None,
                         ) -> tuple[bool, str]:
    """Insert or replace one provider group under the top-level `api-keys:` block.

    Surgical text editing, deliberately: this file is mostly commented documentation and a
    round-trip through a YAML library would discard it.

    Ownership rules (what gets rebuilt vs preserved):
    - `keys` is always rebuilt - key onboarding is the point of this script.
    - `base_url`, when given, rewrites the field; when None, any existing one is kept.
    - `models`, when given as [(upstream, alias|None)], rewrites the list; when None,
      any existing list is kept. Needed because openai-compatibility-style groups serve
      NOTHING without an explicit models list - but writing one for a native provider
      (gemini) would only restrict what it already serves, so callers skip it there.
    - Every other field on the group (headers, disabled, prefix, display names...)
      is preserved untouched. A re-run must never strip configuration it doesn't own -
      e.g. auth headers that gate access would break a working setup if dropped.
    """
    if models is not None and len(models) == 0:
        # Defensive: [] means "nothing requested", not "delete the list". The caller
        # normalizes this too; belt and suspenders because the failure mode is silent
        # data loss on a working provider.
        models = None
    text = config_path.read_text()
    lines = text.split("\n")

    api_idx = find_key_line(lines, "api-keys", 0)
    if api_idx == -1:
        # Create the block immediately before `management:` (or at end of file),
        # then fall through so group insertion happens in exactly one place.
        anchor = find_key_line(lines, "management", 0)
        insert_at = anchor if anchor != -1 else len(lines)
        lines[insert_at:insert_at] = ["api-keys:", f"    {provider}:"]
        api_idx = insert_at

    api_end = block_extent(lines, api_idx, 0)

    prov_idx = -1
    for i in range(api_idx + 1, api_end):
        if lines[i].startswith(f"    {provider}:"):
            prov_idx = i
            break
    if prov_idx == -1:
        lines.insert(api_idx + 1, f"    {provider}:")
        prov_idx = api_idx + 1

    prov_end = block_extent(lines, prov_idx, 4)

    # Find the item whose block carries `"name": "<group>"`. The name may sit on the
    # `-` line itself or on its own field line - both layouts exist in real files,
    # so match either instead of assuming one.
    item_start = item_end = -1
    i = prov_idx + 1
    while i < prov_end:
        line = lines[i]
        indent = len(line) - len(line.lstrip())
        if line.strip().startswith("- ") and indent == 8:
            j = i + 1
            while j < prov_end:
                lj = lines[j]
                if not lj.strip():
                    j += 1
                    continue
                if (len(lj) - len(lj.lstrip())) <= 8:
                    break
                j += 1
            if any(re.search(rf'"name":\s*"{re.escape(group_name)}"',
                             lines[k]) for k in range(i, j)):
                item_start, item_end = i, j
                break
            i = j
        else:
            i += 1

    # Partition an existing item into kept fields. Managed fields (base-url when
    # explicitly given, always keys, models when explicitly given) are rebuilt
    # below; everything else survives verbatim, re-indented canonically.
    kept: list[str] = []
    if item_start != -1:
        raw: list[tuple[int, str]] = []
        head = re.sub(r"^\s*-\s", "", lines[item_start])
        if head.strip():
            raw.append((10, head.strip()))
        for k in range(item_start + 1, item_end):
            if lines[k].strip():
                raw.append((len(lines[k]) - len(lines[k].lstrip()), lines[k].strip()))
        dropping = False
        for ind, text_ in raw:
            m = re.match(r'^"([^"]+)":', text_)
            if m and ind == 10:
                key = m.group(1)
                if key == "name":
                    dropping = False
                    continue  # re-emitted canonically below
                if ((key == "base-url" and base_url is not None)
                        or key == "keys"
                        or (key == "models" and models is not None)):
                    dropping = True  # managed: dropped here, rebuilt below
                    continue
                dropping = False
                kept.append(" " * 10 + text_)
            elif dropping:
                continue  # child line of a dropped managed field
            else:
                kept.append(" " * ind + text_)

    new_item = [f'        - "name": "{group_name}"']
    if base_url:
        new_item.append(f'          "base-url": "{base_url}"')
    new_item.extend(kept)
    new_item.append(f'          "keys":')
    for k in keys:
        new_item.append(f'            - "api-key": "{k}"')
    if models is not None:
        new_item.append(f'          "models":')
        for upstream, alias in models:
            new_item.append(f'            - "name": "{upstream}"')
            if alias:
                new_item.append(f'              "alias": "{alias}"')

    if item_start != -1:
        lines[item_start:item_end] = new_item
        action = f"replaced group {group_name} ({len(keys)} keys)"
    else:
        lines[prov_end:prov_end] = new_item
        action = f"added group {group_name} under api-keys.{provider}"
    config_path.write_text("\n".join(lines))
    return True, action


# ---------------------------------------------------------------------- opencode json

def register_models(config_path: Path, opencode_provider: str,
                    model_ids: list[str], limits: dict[str, tuple[int, int]]) -> tuple[int, list[str]]:
    """Add models to both provider blocks. Returns (added_count, already_present)."""
    config = json.loads(config_path.read_text())
    added, present = 0, []

    for section in ("provider", "providers"):
        provider = (config.get(section) or {}).get(opencode_provider)
        if not isinstance(provider, dict):
            continue
        models = provider.setdefault("models", {})
        for model_id in model_ids:
            if model_id in models:
                present.append(model_id)
                continue
            entry: dict = {}
            ctx, out = limits.get(model_id, (None, None))
            if ctx:
                entry["limit"] = {"context": ctx}
                if out:
                    entry["limit"]["output"] = out
            models[model_id] = entry
            added += 1

    config_path.write_text(json.dumps(config, indent=2) + "\n")
    return added, present


# ------------------------------------------------------------------------------ probe

def probe(base_url: str, api_key: str, model: str, timeout: int) -> str:
    """One tiny completion. 30 tokens, not 5: thinking models return null content when
    the budget truncates them, which looks like failure at max_tokens=8."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    body = json.dumps({
        "model": model, "messages": [{"role": "user", "content": "Reply with only: ok"}],
        "max_tokens": 30,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        if data.get("choices"):
            return "OK"
        return f"no choices: {str(data)[:80]}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except Exception as exc:
        return type(exc).__name__


# -------------------------------------------------------------------------------- main

def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--keys-file", required=True)
    p.add_argument("--provider", default="gemini", choices=sorted(PROVIDERS))
    p.add_argument("--base-url", default=None, help="override the provider's base URL")
    p.add_argument("--list-path", default=None, help="override the model-list path")
    p.add_argument("--key-pattern", default=None, help="regex selecting key lines")
    p.add_argument("--model", action="append", default=[], dest="models",
                   help="upstream model to register (repeatable)")
    p.add_argument("--alias-prefix", default=None,
                   help="namespace gateway IDs as PREFIX/model, e.g. --alias-prefix "
                        "codecraftapi serves 'codecraftapi/claude-opus-5'. The gateway "
                        "reads the pre-slash segment as a credential selector, so the "
                        "alias must be verified against the gateway before trusting it.")
    p.add_argument("--group-name", default=None, help="gateway group name")
    p.add_argument("--gateway-config", default=DEFAULT_GATEWAY_CONFIG)
    p.add_argument("--opencode-config", default=DEFAULT_OPENCODE_CONFIG)
    p.add_argument("--opencode-provider", default="cliproxy")
    p.add_argument("--gateway-key", default=None,
                   help="client-facing gateway key for probing through the gateway "
                        "afterwards (requires --gateway-base)")
    p.add_argument("--gateway-base", default=None,
                   help="gateway chat base, e.g. http://localhost:8317/v1 - "
                        "probe each model through the gateway, not the upstream")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--timeout", type=int, default=15)
    p.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = p.parse_args()

    preset = PROVIDERS[args.provider]
    base_url = (args.base_url or preset["base_url"]).rstrip("/")
    list_path = args.list_path or preset["list_path"]
    pattern = args.key_pattern or preset["key_pattern"]
    group_name = args.group_name or f"{args.provider}-keys"

    keys_file = Path(args.keys_file).expanduser()
    if not keys_file.is_file():
        print(f"error: keys file not found: {keys_file}", file=sys.stderr)
        return 2

    # ---- 1. extract ---------------------------------------------------------
    keys, total_lines = extract_keys(keys_file, pattern)
    print(f"keys file: {keys_file}")
    print(f"lines read: {total_lines}   candidate keys: {len(keys)}")
    if not keys:
        print("error: no lines matched the key pattern. Pass --key-pattern.",
              file=sys.stderr)
        return 2

    # ---- 2. validate --------------------------------------------------------
    print(f"\nvalidating against {base_url}{list_path} ({args.workers} workers)...")
    good: list[str] = []
    available: set[str] = set()
    reasons: dict[str, int] = {}
    auth_style = preset.get("auth", "query")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(validate_key, k, base_url, list_path, args.timeout,
                               auth_style): k
                   for k in keys}
        done = 0
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                res = fut.result()
            except Exception:
                res = {"ok": False, "why": "error", "detail": "exception"}
            done += 1
            if res["ok"]:
                good.append(key)
                available.update(res.get("models", []))
            else:
                reasons[res["why"]] = reasons.get(res["why"], 0) + 1
            if done % 10 == 0 or done == len(keys):
                print(f"  {done}/{len(keys)} checked")

    print(f"\nvalid: {len(good)}   invalid: {len(keys) - len(good)}")
    for why, count in sorted(reasons.items()):
        print(f"  {why}: {count}")
    if not good:
        print("error: no usable keys. Nothing written.", file=sys.stderr)
        return 1

    # ---- 3. model sanity ----------------------------------------------------
    if available:
        print(f"\nmodels the valid keys can serve: {len(available)}")
    if args.models:
        print("\nrequested models:")
        for m in args.models:
            if available and m not in available:
                print(f"  {m:<40} NOT in provider listing - may not work")
            elif available:
                print(f"  {m:<40} confirmed by provider listing")
            else:
                print(f"  {m:<40} could not verify (no listing available)")

    # client_ids are what the OpenCode picker will show. With --alias-prefix these
    # are the namespaced gateway aliases; without it, the bare upstream IDs.
    # gateway_models carries (upstream, alias|None) for the gateway models list.
    if args.alias_prefix:
        _prefix = args.alias_prefix.strip().strip("/")
        client_ids = [f"{_prefix}/{m}" for m in args.models]
        gateway_models: list[tuple[str, str | None]] | None = list(zip(args.models, client_ids))
    else:
        client_ids = list(args.models)
        # openai-compatibility-style groups serve NOTHING without an explicit models
        # list, so write bare entries. Native providers (gemini) serve their whole
        # catalog - writing a list there would only restrict it, so skip it.
        gateway_models = [(m, None) for m in args.models] if args.provider != "gemini" else None

    # Normalize: an empty models list means "no models requested", i.e. preserve -
    # never "wipe the existing list". [] is not None, so without this the replace
    # path below would silently delete a working models list.
    if not gateway_models:
        gateway_models = None

    # ---- 4. plan ------------------------------------------------------------
    gateway_config = Path(args.gateway_config).expanduser()
    opencode_config = Path(args.opencode_config).expanduser()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    mode = "APPLY" if args.apply else "DRY RUN (nothing written)"
    print(f"\n== {mode} ==")
    print(f"  gateway : {gateway_config}   group {args.provider}/{group_name} "
          f"with {len(good)} key(s)")
    if args.models:
        if args.alias_prefix:
            print(f"  opencode: {opencode_config}   models {', '.join(client_ids)}")
            print(f"            (gateway aliases for upstream: {', '.join(args.models)})")
        else:
            print(f"  opencode: {opencode_config}   models {', '.join(args.models)}")
    else:
        print(f"  opencode: unchanged (no --model given)")

    if not args.apply:
        print("\nre-run with --apply to write")
        return 0

    # ---- 5. write -----------------------------------------------------------
    backups: list[tuple[Path, Path]] = []
    if not gateway_config.is_file():
        print(f"error: gateway config not found: {gateway_config}", file=sys.stderr)
        return 2

    bak = gateway_config.with_suffix(gateway_config.suffix + f".bak-{stamp}")
    shutil.copy2(gateway_config, bak)
    backups.append((gateway_config, bak))

    if args.models and opencode_config.is_file():
        bak2 = opencode_config.with_suffix(opencode_config.suffix + f".bak-{stamp}")
        shutil.copy2(opencode_config, bak2)
        backups.append((opencode_config, bak2))

    try:
        _, note = upsert_gateway_group(gateway_config, args.provider, group_name,
                                       good, base_url=args.base_url or None,
                                       models=gateway_models)
        print(f"\ngateway: {note}")
        if gateway_models:
            print(f"  gateway models list: {', '.join(a or u for u, a in gateway_models)}")
            print(f"  opencode model IDs : {', '.join(client_ids)}")
    except Exception as exc:
        print(f"error: gateway edit failed ({exc}); restoring", file=sys.stderr)
        shutil.copy2(bak, gateway_config)
        return 1

    if args.models and opencode_config.is_file():
        try:
            added, present = register_models(opencode_config, args.opencode_provider,
                                             client_ids, {})
            print(f"opencode: {added} model entry/entries added, "
                  f"{len(set(present))} already present")
        except Exception as exc:
            print(f"error: opencode edit failed ({exc}); restoring both", file=sys.stderr)
            shutil.copy2(bak, gateway_config)
            for path, backup in backups[1:]:
                shutil.copy2(backup, path)
            return 1

    for path, backup in backups:
        print(f"backup: {backup}")

    # ---- 5b. structural self-check -----------------------------------------
    # We deliberately edit YAML as text (no parser available, and round-tripping through
    # one would discard the file's commented documentation). That makes a cheap
    # structural check worth doing: even indentation, balanced quotes on our lines, and
    # the groups we touched still present.
    problems: list[str] = []
    gtext = gateway_config.read_text()
    for lineno, line in enumerate(gtext.split("\n"), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent % 2 != 0:
            problems.append(f"line {lineno}: odd indent {indent}")
        if stripped.count('"') % 2 != 0:
            problems.append(f"line {lineno}: unbalanced quote: {stripped[:60]}")
    if f'"name": "{group_name}"' not in gtext:
        problems.append(f"group {group_name} missing after write")
    if problems:
        print("\nstructural check found problems:", file=sys.stderr)
        for prob in problems[:10]:
            print(f"  {prob}", file=sys.stderr)
        print(f"\nROLLBACK: cp {backups[0][1]} {gateway_config}", file=sys.stderr)
        return 1
    print("\nstructural check: passed (indentation + quotes + group present)")
    print("NOTE: no YAML parser was available for a true parse test. The authoritative")
    print("      check is restarting the gateway - malformed YAML makes it refuse to")
    print(f"      start. Rollback: cp {backups[0][1]} {gateway_config}")

    # ---- 6. verify ----------------------------------------------------------
    print("\n== limits note ==")
    print("  No limit values were written. Inventing them is worse than omitting them:")
    print("  a wrong low limit makes the client compact early and truncate replies.")
    print("  Source them from provider metadata, then use sync-limits.py if a sibling")
    print("  gateway publishes them.")

    gw_key = args.gateway_key
    # Probe the client-visible IDs (aliases when namespaced), not the raw upstream
    # names - the alias is what must resolve at the gateway.
    probe_ids = client_ids if args.models else []
    if gw_key and args.gateway_base and probe_ids:
        print(f"\n== probing gateway {args.gateway_base} ==")
        for m in probe_ids:
            print(f"  {m:<40} {probe(args.gateway_base, gw_key, m, 60)}")
    elif gw_key and probe_ids:
        print("\nNOTE: --gateway-key probes through the gateway, so it needs "
              "--gateway-base <gateway chat base, e.g. http://localhost:8317/v1>. "
              "Probing the upstream with a gateway client key always fails; skipped.")
    elif args.models:
        print("\nPass --gateway-base <base> --gateway-key <key> to probe the gateway after writing.")

    print("\nRestart the gateway service, then reload the client, then check /model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())