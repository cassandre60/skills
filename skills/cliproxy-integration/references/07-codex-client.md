# 07 — Codex CLI as a second client on the same gateway

Everything in 01–06 was learned driving OpenCode. This file is the Codex equivalent:
same gateway, same keys, different client quirks. Verified against `codex-cli 0.160.0`;
re-test after every `codex update`, because this client changes fast and silently.

## What works

A gateway serving OpenAI-compatible `/v1` (both `/v1/chat/completions` and
`/v1/responses` — verify each with curl before blaming Codex) drives Codex with no paid
account. The login screen is only for OpenAI OAuth; key-based providers bypass it.

## Wire API: `responses`, no alternative

```toml
# wire_api = "chat" is rejected outright on recent versions:
# "`wire_api = "chat"` is no longer supported. How to fix: set `wire_api = "responses"`"
wire_api = "responses"
```

## Auth must be stored, env is not enough

```bash
printf '%s' '<gateway-client-key>' | codex login --with-api-key
```

`codex doctor` acknowledges `OPENAI_API_KEY` from the environment ("auth is provided by
environment") but the transport never attaches it — proven with a header-dump listener:
neither WebSocket upgrades nor HTTPS POSTs carried any `Authorization` header until the
key was stored via login. `auth.json` (mode 600) is the only auth path that works.

## 0.160.0 silently ignores file-level network/model keys

`openai_base_url`, `model`, `model_provider`, and `[model_providers.*]` in
`~/.codex/config.toml` parse cleanly (including under `--strict-config`) and then do
nothing: requests still go to `api.openai.com` with the default model. Proven by
elimination — identical values passed via `-c` reach the gateway immediately.

Workaround: pass them per run. A shell wrapper keeps it tolerable:

```sh
codex-gw() {
  codex --enable api_key_model_discovery \
        -c openai_base_url='"http://localhost:8317/v1"' \
        -c model="\"${CODEX_GW_MODEL:-gemini-3.5-flash-lite}\"" \
        -c model_catalog_json='"/home/you/.codex/model-catalogs/gateway.json"' "$@"
}
# codex-gw exec "do X"              # default model
# codex-gw exec -m <gateway-id> "…" # any model the gateway serves, no import step
```

There is no "import everything" step: once the provider is wired, every model ID the
gateway serves is one `-m` away.

## The `/model` picker needs discovery, not just a catalog

`/model` lists the client's model list, not the gateway's. Two mechanisms:

1. **`api_key_model_discovery`** (feature flag, off by default): lists models from the
   provider's own `/v1/models` using the API key. Enable per run:
   `codex --enable api_key_model_discovery` (or `codex features enable …` to persist).
2. **`model_catalog_json`** (root-level path to `{"models": [...]}`): supplies picker
   entries with real `context_window`/`max_context_window` (source them as in section 3
   of the SKILL.md — wrong values cause premature compaction) and reasoning levels.
   Clone one built-in entry from `codex debug models` output and override only
   `slug`/`display_name`/`description`/windows. It **replaces** the built-in list.

Verify with `codex debug models` (must render your slugs; a single bad field discards
the whole file with `missing field …`, showing zero custom models), then **restart the
TUI** — a running session never re-reads the catalog.

## Transports and quota amplification

Codex tries WebSocket `/v1/responses` first, then falls back to HTTPS automatically.
Both work against a compatible gateway when upstream quota is healthy; every earlier
"connection reset" in this integration traced to all-credentials-cooling, not protocol.

Budget for ~10 upstream touches per `exec` (WS attempts + HTTPS retries, times the
gateway's own credential retries). Against a tight shared pool that is enough to cool
every key in one run (fill-first walks the whole list). If an exec 429s: wait for
cooldowns to expire (or restart the gateway to clear in-memory cooldowns), verify with
one curl, then run once — don't hammer.

## After `codex update`

Re-run this checklist: (1) does the config file get honored now (remove a `-c` and see
where traffic goes), (2) `codex debug models` still renders the catalog, (3) one
trivial `exec` answers. Delete workarounds the new version made redundant.
