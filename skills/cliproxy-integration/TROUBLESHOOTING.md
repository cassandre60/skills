# Troubleshooting: symptom → cause → fix

Quick index. Each row is a trap that reliably costs hours. The `references/` files in the skill
carry the full reasoning.

## Models missing or unresolvable

| Symptom | Cause | Fix |
| --- | --- | --- |
| Model absent from `/model` | never registered | check the gateway catalog first, then the config block |
| Model absent, using `/connect` | wrong command | `/connect` is credentials only; models live in `/model` |
| `unknown provider for model a/b` | gateway read `a` as a credential prefix | register that exact ID as an alias at the gateway (see below) |
| `model_not_found` with your own provider name in the model field | you prefixed the key with the provider name | keys must be the bare upstream ID |
| Config valid, model still missing | client didn't reload | restart the client |
| `model_not_found` after a base-url change | `base-url` already contains the path the gateway appends (doubled URL in gateway log) | use the upstream root as `base-url`; an empty-keys group's `base-url` is not evidence |
| Probe returns `content: null` + `finish_reason: max_tokens` | budget truncated a thinking model — inconclusive, not failure | re-probe with `max_tokens` 30–100; parse full JSON, never truncate before parsing |
| Models appear twice | `prefix` **and** `alias` both set | use one; `alias` alone for a custom name |
| One provider works, sibling provider empty | API-key groups need an explicit `models:` list | enumerate IDs; no auto-discovery exists for them |

## Namespacing by upstream (`provider/source/model`)

The gateway sees `source/model` and treats `source` as a credential prefix. To namespace:

- OAuth / file credentials → gateway model-alias with `fork: true` (keeps the plain ID)
- API-key groups → `models:` entry with `alias`
- Then **probe the namespaced ID directly** before putting it in the client config

## Upstream rejects the request (401 / 403)

Work the ladder; each rung eliminates a class.

| Step | Test | If it fails |
| --- | --- | --- |
| 1. Control | same upstream+key via a *different* client | your config is fine → shape differs (step 3) |
| 2. Direct | straight at the upstream with your key | key/endpoint/vendor problem |
| 3a. Protocol | `/chat/completions` vs `/messages` | configure the accepted executor |
| 3b. Headers | `User-Agent`, `X-App`, `anthropic-version` | set them on the gateway group |
| 3c. Body | required fields the other client always sends | match the working client |

Read the working client's source for exact header values rather than guessing.

**Beware:** "works with no UA, fails with any UA" is usually an artifact of the test client
sending a default UA. Re-verify with a client that truly suppresses it (`curl -H 'User-Agent:'`)
before concluding anything.

## Quota and pools

| Symptom | Meaning | Action |
| --- | --- | --- |
| `429 Individual quota reached` / `RESOURCE_EXHAUSTED` | account allowance spent | wait for reset, or add accounts |
| Panel says "access available" but requests fail | the **weekly** meter is spent; the short window being green doesn't matter | read both meters |
| Some models fail, siblings work | quota **groups** — models share an allowance | probe one model per advertised group |
| `All credentials ... cooling down` | gateway-side state | restart to clear in-memory cooldown, then re-read reality |
| Persistent backoff after an auth error | retries can't fix auth | change the request or the credentials |

## Egress and network

| Symptom | Cause | Fix |
| --- | --- | --- |
| VPN "connects" then drops, `No Network` | resolver parse failure (scoped IPv6 in `resolv.conf`) | see below |
| Gateway logs a proxy that isn't listening | the proxy came from VPN proxy-mode | start it, or remove the dependency |
| Trace reports the tunnel off | proxy mode, not full-tunnel | verify with a request that uses the proxy port |
| Gateway calls fail only through the gateway | egress/proxy precedence | test direct vs proxied; set proxy at the narrowest scope |

### Resolver trap

```bash
cat -A /etc/resolv.conf                                  # exact content
journalctl -u <vpn> | grep -i -E 'resolv|invalid IP'     # confirm the parser
ip -6 addr show scope global                              # is real IPv6 even in use?
sudo sed -i '/<scoped-addr>/d' /etc/resolv.conf           # temporary
sudo nmcli con mod "<CONN>" ipv6.ignore-auto-dns yes     # durable - do this one
```

`fe80::1%iface` is accepted by glibc and rejected by stricter resolvers. Warn that the
temporary fix reverts on DHCP renewal.

## Config keeps reverting

Two writers. Establish facts before editing again:
```bash
ls -la --time-style=full-iso ~/.config/opencode/opencode.json*
diff <(jq -S . opencode.json.bak-<date>) <(jq -S . opencode.json)
```

The pattern tells you which subtree the other writer owns. Then give each key exactly one
owner, and make your repair step idempotent and re-runnable rather than hand-patching.

## Codex CLI on the same gateway (0.160.0)

| Symptom | Cause | Fix |
| --- | --- | --- |
| TUI login loop despite `codex login` | login screen validates against OpenAI; gateway key fails there | drive Codex at the gateway (`-c openai_base_url=…`); pick "provide your own API key" with the gateway key |
| Requests go to api.openai.com though config points at the gateway | file-level network/model keys silently ignored (parse ok, `--strict-config` passes) | pass `-c openai_base_url=… -c model=…` per run; re-test the file after each `codex update` |
| `wire_api = "chat"` rejected | chat completions removed; responses-only | `wire_api = "responses"` (verify `/v1/responses` on the gateway first) |
| `doctor` sees `OPENAI_API_KEY` but requests carry no auth | env is recognized, never attached | `codex login --with-api-key` (stored `auth.json` is the only working auth) |
| `/model` shows only OpenAI models | picker needs provider discovery + fresh launch | `--enable api_key_model_discovery`, `model_catalog_json` at root, restart the TUI |
| One exec cools the whole pool (all keys 429) | ~10 touches/exec × gateway retries vs tight quota | stop traffic, wait out cooldowns (or restart gateway to clear), verify with one curl, then run once |