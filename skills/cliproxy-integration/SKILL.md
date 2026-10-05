---
name: cliproxy-integration
version: 1.2.3
description: Wire an OpenAI-compatible AI gateway (CLIProxyAPI / OmniRoute / any /v1 gateway) into OpenCode and Codex CLI correctly - discover real model IDs instead of guessing, register providers that resolve, source model limits instead of inventing them, attribute models to their upstream, and diagnose the gateway errors that block model selection. Use whenever someone asks to add models to OpenCode, connect OpenCode or Codex to a gateway/proxy/relay, fix "unknown provider for model", "model_not_found", 401/403 from an upstream gateway, missing models in the /model picker, wrong context/output limits, model routing or credential-pool behaviour, quota/429 exhaustion on pooled accounts, or WARP/proxy egress setup for API traffic.
---

# Gateway → OpenCode integration

Goal: every model the gateway serves appears in OpenCode's `/model` picker, under an ID that
says which upstream it came from, with limits that reflect reality — and when something is
missing, you know which of six failure classes it is.

**Read this first.** Nearly every wasted hour in this domain comes from doing one of these
in the wrong order. Discover, then wire, then attribute, then limit, then verify.

## Non-negotiable rules

1. **Never invent a model ID.** Query the gateway's `/v1/models`. If it's not in the response,
   it does not exist, no matter how plausible the name looks.
2. **Never invent a limit.** Source `context` / `output` from gateway or provider metadata.
   Guessed low values make OpenCode compact far too early and truncate replies — you pay for
   the tokens anyway.
3. **A model key inside a provider block is the *upstream* ID, verbatim.** OpenCode builds the
   full ID as `provider-key/model-key`; it does not translate. Prefixing the key with the
   provider name breaks it.
4. **Verify with a real request, not a config read.** A valid JSON file proves nothing. Send a
   completion through the gateway, per model, with `max_tokens` 30–100 (not 5 — thinking
   models return `content: null` when the budget truncates them). `content: null` plus
   `finish_reason: max_tokens` is *inconclusive, raise the budget and retry* — never a
   failure verdict. Parse the full JSON; never truncate the body before parsing.
   Verify at the **gateway** (curl / `probe-models.sh`), not inside the client: a client
   test attaches the whole system prompt and tool definitions, burning thousands of input
   tokens to prove what a ~50-token gateway probe already proved. Reserve in-client tests
   for wiring (does the ID resolve in `/model`), never for model health.
5. **Back up before every edit**, and keep the backup nameable.

## Workflow

### 0. Establish facts before changing anything

```bash
curl -s "$GW/v1/models" -H "Authorization: Bearer $KEY" | jq -r '.data[] | "\(.id)  |  \(.owned_by)"'
```

Record the count and the owner of every entry. This single output answers most questions
later ("is my model even served?", "which upstream is this?", "why does the picker disagree
with me?").

If the gateway requires a management key or blocks `/v1/models`, do not guess the models —
probe candidates individually (see `scripts/probe-models.sh`).

### 0b. Three dashboard mentions (remind, point, don't touch)

These need no specialist knowledge, and items 2–3 live in the gateway dashboard — so
the agent's job is to *remind and point*, not to configure. Mention each one when
wiring or onboarding; change nothing unless the user explicitly delegates it. If a
state is already good, one line ("on") suffices:

1. **Sensitive Word Obfuscation (Antigravity) — recommend populated.** Zero-width
   characters are injected into matching words in Antigravity system instructions so
   upstream exact-keyword matching fails soft. The list lives at
   `oauth.providers.antigravity.sensitive-words` (dashboard config page) and should
   name the client identifiers in play (e.g. `OpenCode`, `opencode`, `harness`).
   Matching is case-insensitive; empty entries are dropped and entries under
   2 characters are ignored by the backend.
2. **Proxy egress (e.g. WARP) — mention only.** Default direct egress is fine; a
   proxy is worth mentioning only for region-blocked upstreams or egress privacy.
   Tell the user it is one global Proxy URL in the dashboard that affects *all*
   providers. Set nothing yourself.
3. **Session Affinity Routing — mention only.** One line on what it does (sticky
   sessions reuse prompt/KV cache; off suits heavy parallel use) plus where it lives
   in the dashboard. Set nothing yourself.

### 1. Register the provider (both twins)

OpenCode reads `provider` (singular, documented) and `providers` (plural). The plural form is
overlaid on top and wins on overlap. If your tooling writes both, keep them **identical**.

- under `provider`: `npm: "@ai-sdk/openai-compatible"`, connection under `options`
- under `providers`: `package: "@opencode-ai/ai/providers/openai-compatible"`, connection under `settings`

Both `options` and `settings` carry `baseURL` + `apiKey`.

### 2. Attribute models to their upstream (careful — this is a trap)

A model ID like `cliproxy/antigravity/gemini-3.8-flash` is *not* free-form. The gateway sees
`antigravity/gemini-3.8-flash` and interprets the segment before the slash as a **credential
prefix selector**. If no credential with that prefix exists you get:

```
unknown provider for model antigravity/gemini-3.8-flash
```

That is a gateway error wearing OpenCode's clothes. To namespace an ID by source you must
**register that exact ID at the gateway**. Two mechanisms, pick by credential type:

| Credential type | Mechanism | Where |
| --- | --- | --- |
| OAuth / file-backed | model aliases, `fork: true` keeps the plain ID working | gateway config, oauth section |
| API-key (`openai-compatibility` etc.) | per-group `models:` list with `alias` | gateway config, api-keys section |

Verify a namespaced ID works **before** writing it into OpenCode. If it fails, the slash is
your problem, not OpenCode's.

### 3. Source the limits

From the gateway's `/v1/models` if it publishes `context_length` / `max_output_tokens`;
otherwise from a sibling gateway serving the same upstream models; otherwise from provider
metadata. Label them as sourced, not measured. Never fall back to a "safe" 128000/8192 in a
file you control — that is a silent downgrade that costs tokens.

Exception — free-tier keys: cap *below* the published maxima on purpose. A free key
advertises e.g. 1M context / 64K output, but its per-minute token budget is tiny; every
request may legally claim the full ceiling and burn the minute's allowance in one or
two turns. For free keys, deliberately register tight caps (e.g. 128000/8192) while
keeping full sourced limits on premium providers (Antigravity, paid relays). The output
cap is the one that matters: it becomes max tokens upstream, so it directly controls
TPM burn and 429 frequency. Differentiate free from premium per provider, never
blanket-wide.

### 4. Verify — the part everyone skips

For **each** model: a completion through the gateway (`max_tokens` 30–100, see rule 4 for
why 5 is too few), and then the model visible in OpenCode's picker. Config validity ≠
working model. See `scripts/probe-models.sh`.

Cost discipline: **listing calls are free, completions are not.** `/v1/models` and per-key
validation endpoints consume no tokens — use them for discovery, key validation, and
existence checks. Reserve completions for the final proof that a model answers. When tokens
are scarce, exhaust everything free before spending anything.

## Diagnosing failures

Work this ladder top-down. Each rung eliminates a class. Do not skip to a fix.

| Symptom | Class | Go to |
| --- | --- | --- |
| Model absent from `/model` | discovery / config shape | `references/01-wiring.md` |
| `unknown provider for model X/Y` | slash read as credential prefix | `references/01-wiring.md` |
| `model_not_found` right after a base-url change, gateway log shows a doubled path (`.../v1beta/openai/v1beta/models/...`) | group `base-url` already contains the path the gateway appends | `references/01-wiring.md` |
| Every model works, one doesn't | that upstream | `references/02-upstream-401.md` |
| 429 / "no quota" on some models only | quota semantics | `references/03-quota.md` |
| Pool-wide failure after a change | egress / network | `references/04-network.md` |
| Gateway unstable, config reverts | two writers | `references/05-conflicts.md` |
| Given a file of API keys, some dead | key onboarding | `references/06-key-dump-onboarding.md` |
| Porting keys between gateways on one host | credential porting | `references/08-credential-porting.md` |
| Codex TUI login loop, `/model` OpenAI-only, or file config silently ignored | second-client (Codex) wiring | `references/07-codex-client.md` |

**Highest-value habit:** when a hypothesis is contradicted by a later test, kill it
immediately and say so. Do not carry a dead theory forward into a "fix".

## The two hard lessons from the field

**Slash semantics differ per hop.** OpenCode tolerates slashes in a model key; most gateways
do not, because they read the pre-slash segment as a credential selector. Test the *gateway*
with the exact string before trusting OpenCode's acceptance of it.

**A gateway that works through one client can still reject another, for reasons unrelated to
keys.** Before blaming your config, confirm with a control: does the *same gateway, same key,
same endpoint* succeed through a different client? If yes, the difference is request shape —
headers, protocol, or body — not credentials, and not your setup. Compare with a working
reference implementation rather than guessing.

**Read the gateway's logs before theorising.** The gateway logs the exact upstream URL it
built (`journalctl --user -u <service>`, docker logs, or the service's log file). One row
showing a doubled path or a wrong host settles base-url and routing questions instantly.
Never reverse-engineer the gateway binary (`strings`, decompilers) to answer a question its
logs already answer — that path costs turns and produces weaker evidence.

## Scripts

- `scripts/probe-models.sh` — list a gateway's models and probe each with a tiny completion.
  Use for discovery when `/v1/models` is unavailable, and after every config change.
- `scripts/sync-limits.py` — re-source `limit.context` / `limit.output` from a live gateway
  into an OpenCode config. Idempotent; touches only limits; never adds or removes models.
- `scripts/onboard-keys.py` — onboard a messy file of API keys: validates each key, keeps
  only the working ones, writes a provider group, registers models in the client, backs up
  both files. Dry-run by default. See `references/06-key-dump-onboarding.md`.

## Adding models without hand-editing

For the two most common asks, the scripts do the work and the guidance above tells you what
to check:

| The user says | Do this |
| --- | --- |
| "here's a gateway URL and key, add models X and Y" | `probe-models.sh` to confirm X and Y answer → add the provider block (both twins) → `sync-limits.py` if limits are published |
| "here's a file of 100 keys, some dead, add model Z" | `onboard-keys.py --keys-file … --model Z` (dry run first) |