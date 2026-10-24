# 01 — Wiring, discovery, and the slash trap

## Why a model doesn't appear in `/model`

Check in this order:

1. **Does the gateway serve it?** `curl $GW/v1/models`. If absent, nothing downstream matters.
2. **Is the provider block shaped right?** See below.
3. **Is the model key the upstream ID verbatim?** No provider prefix, no renaming.
4. **Did OpenCode reload?** Config is watched and the picker reflects edits without a
   restart (verified: new models appear, removed providers disappear). If a change doesn't
   show, the edit didn't land where you think — re-check the file, don't just restart.
5. **Are you looking in `/model`, not `/connect`?** `/connect` manages credentials only. It
   never lists models. This is a common wrong-turn.

## Provider block shapes

OpenCode accepts two keys. Both may be present in one file. The plural form is overlaid on the
singular form and **wins on overlap**; the singular form is what most gateways and tooling
write. If both exist, keep the provider objects identical or you get silent divergence.

```jsonc
// under "provider"  — the documented key
"mygateway": {
  "npm": "@ai-sdk/openai-compatible",
  "name": "My Gateway",
  "options": { "baseURL": "http://localhost:PORT/v1", "apiKey": "sk-..." },
  "models": { "<upstream-id>": { "name": "Display Name", "limit": { "context": 0, "output": 0 } } }
}
```

```jsonc
// under "providers" — same object, different field names
"mygateway": {
  "name": "My Gateway",
  "package": "@opencode-ai/ai/providers/openai-compatible",
  "settings": { "baseURL": "http://localhost:PORT/v1", "apiKey": "sk-..." },
  "models": { "<upstream-id>": {} }
}
```

Then `jq . opencode.json >/dev/null` and restart the client.

## Hiding a model without deleting it does not work on custom providers

Verified through three failed attempts on the same entry: `status: "deprecated"` disables
selection but leaves the picker entry visible; `blacklist` with the short model key does
nothing; `blacklist` with the full `provider/model` ID does nothing either. The only lever
that removes a model from the picker is **deleting the entry** (back the file up first —
restoration is one copy). Do not burn rounds on the other mechanisms; they are documented
for built-in providers and silently inert here.

## A provider id that exists in models.dev imports its whole catalog

The `models` map is not the whole story when the provider **id** matches a provider that
models.dev knows. OpenCode merges that provider's catalog into the picker, so the listing
contains models you never configured:

| Provider id | Entries in config | Entries listed | Why |
| --- | --- | --- | --- |
| `nvidia` | 2 | 23 | `nvidia` is a models.dev provider |
| `agentrouter` | 2 | 7 | `agentrouter` is a models.dev provider |
| `cliproxy` | 30 | 30 | absent from models.dev |

Measured with `opencode models`, which prints the same merged catalog the picker builds:
`rg -c '^nvidia/'` returned 23 while the config held 2 entries. `provider.whitelist` does not
fix this — the built-in `opencode` provider had 10 whitelisted entries and still listed 82.

Check an id before using it:

```bash
curl -s https://models.dev/api.json | jq 'has("nvidia")'       # true  -> collides
curl -s https://models.dev/api.json | jq 'has("nvidia-direct")' # false -> safe
```

Renaming the provider id from `nvidia` to `nvidia-direct` reduced the picker to exactly the
two configured models. The display `name` and the model keys are untouched by the rename;
only the client-facing prefix changes (`nvidia-direct/<model>`). Choose the id as deliberately
as you choose the model keys, and prefer no direct provider at all (see SKILL.md rule 6).

## Two files, one name, no import between them

Adding a model is always **two independent registrations of the same string**, and neither
file imports the other:

1. **Gateway config** — register the ID the gateway will accept (`alias`, or the bare
   upstream ID). After restart/reload, the gateway's `/v1/models` lists it. This proves
   serving, and nothing else.
2. **Client config** — add an entry with that **exact same string** as the model key.
   The picker shows only what is written here. This proves visibility, and nothing else.

A model works end-to-end only when both sides carry the identical ID. "It's in the catalog
but not in `/model`" means step 2 is missing. "It's in `/model` but errors" means step 1
is missing or wrong. Diagnose which side is absent before touching either file.

## `name` is the display, the key is the wire

Inside a model entry the two fields have opposite risk profiles, and they sit adjacent
enough to invite the same treatment:

```jsonc
"tokenharbor/deepseek-v4-flash:free": {     // key: sent to the gateway. Verbatim upstream ID.
  "name": "TokenHarbor DeepSeek V4 Flash Free_1M",  // name: drawn in the picker. Cosmetic.
  "limit": { "context": 1000000, "output": 384000 }
}
```

Rewrite the `name` freely — append a context label, drop a redundant vendor prefix, fix a
typo. Nothing about routing depends on it. **Never rewrite the key to match a rename**; the
key is the wire format and a changed key is a `model_not_found` at the gateway.

`name` is also optional. When it's absent the picker falls back to the model key, so an
entry can look unlabelled while being perfectly valid. When adding a label to such an entry,
label the key rather than inventing a display name for a model whose upstream name you
don't know.

To prove a batch of renames stayed cosmetic, diff the config against itself with `name`
removed from both sides:

```bash
strip() { jq -S 'walk(if type=="object" then del(.name) else . end)' "$1"; }
diff <(strip before.json) <(strip after.json)   # empty output: only labels moved
```

`scripts/label-models.py` does the labelling and runs this assertion itself.

## The slash trap — read this twice

OpenCode splits the full model ID at the **first** slash: provider before, model after.
The model half may itself contain slashes, and OpenCode will accept that.

**The gateway will not necessarily agree.** Most gateway families read the segment before a
slash as a **credential prefix selector** — "route this request to the credential group named
`X`". The config schema documents it plainly:

```yaml
prefix: "mygroup"   # requests must look like "mygroup/model" to target this credential
```

So this OpenCode entry:

```jsonc
"models": { "antigravity/gemini-3.8-flash": {} }   // full ID: mygateway/antigravity/gemini-3.8-flash
```

reaches the gateway as `antigravity/gemini-3.8-flash`, the gateway looks for a credential
group prefixed `antigravity`, finds none, and returns:

```
unknown provider for model antigravity/gemini-3.8-flash
```

The message blames a "provider" — which, from the gateway's perspective, is what it is. This
error is a **gateway** error even though it appears inside OpenCode.

## Fixing it: register the namespaced ID at the gateway

Namespacing by source is a good goal (`mygateway/antigravity/foo` tells you where it came
from). It requires the gateway to know the exact string.

**OAuth / file-backed credentials** — model aliases, with `fork: true` so the plain ID keeps
working too:

```yaml
oauth:
  model-alias:
    antigravity:
      - name: "gemini-3.8-flash-high"              # upstream model
        alias: "antigravity/gemini-3.8-flash-high" # client-visible, must match the OpenCode key
        fork: true                                 # also keep the un-namespaced ID
```

**API-key credentials** — a `models:` list per group (see 02):

```yaml
api-keys:
  openai-compatibility:
    - name: "my-relay"
      base-url: "https://relay.example/v1"
      keys:
        - api-key: "sk-..."
      models:
        - name: "claude-opus-5"                     # upstream model
          alias: "relay1/claude-opus-5"             # client-visible ID, matches OpenCode key
          display-name: "Claude Opus 5"
```

Then **probe the namespaced ID directly against the gateway** before trusting it:

```bash
curl -s $GW/v1/chat/completions -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"antigravity/gemini-3.8-flash-high","messages":[{"role":"user","content":"hi"}],"max_tokens":30}'
```

## The base-url double-path trap

A group's `base-url` must be the upstream **root**, not a full endpoint path. The gateway
appends its own path (`/v1beta/models/<m>:generateContent`, `/chat/completions`, …) to
whatever you configured. If the `base-url` already contains that path segment, the upstream
receives a doubled URL and returns 404:

```
# gateway log (this exact line is the diagnosis — read logs before theorising):
upstream execution failed: provider=gemini model=gemini-3.5-flash
  err=Post "https://generativelanguage.googleapis.com/v1beta/openai/v1beta/models/gemini-3.5-flash:generateContent"
```

| `base-url` | Gateway appends | Upstream sees | Result |
| --- | --- | --- | --- |
| `https://generativelanguage.googleapis.com` | `/v1beta/models/…` | correct URL | works |
| `https://generativelanguage.googleapis.com/v1beta/openai/` | `/v1beta/models/…` | doubled path | 404 |

**An existing group with `keys: []` is not evidence for any base-url.** An empty group was
never exercised, so its `base-url` was never tested — do not copy it blindly. Verify with a
single-key probe: put one known-good key in the group, restart, send one completion through
the gateway. Only then onboard the rest of the keys.

## `prefix` + `alias` together cause duplicates

Setting both `prefix: "group"` **and** an `alias` registers the model twice — once bare, once
prefixed — inflating the catalog and showing duplicates in the picker.

| Want | Use | Don't use |
| --- | --- | --- |
| one ID, your own name | `alias` alone | `prefix` + `alias` |
| one ID, namespaced | `prefix` alone (gateway generates the name) | `prefix` + `alias` |
| two IDs for one model | `prefix` alone | — |

Pick one. Then confirm the catalog count dropped as expected:

```bash
curl -s $GW/v1/models -H "Authorization: Bearer $KEY" | jq '.data | length'
```

## Why OAuth providers "just work" and API-key groups don't

OAuth/file credentials ship with their own model catalog, so the gateway can enumerate them.
API-key (`openai-compatibility`) groups have no catalog to read — the upstream's
`/v1/models` is usually blocked or requires separate credentials — so the gateway **requires an
explicit `models:` list**. There is no auto-discovery and, in the CPAMC panel, the models UI is
a read-only viewer for OAuth auth files (`AuthFileModelsModal`), not an editor.

Consequence: adding an API-key provider can require enumerating model IDs by hand. Probe
candidates individually rather than trusting the vendor's marketing page.

## Auto-discovery plugins

A plugin may register its own provider (e.g. one mirroring the gateway catalog into the
picker). Check the conflict surface before adopting one:

- what **provider ID** does it register? A different ID than your manual block means no
  collision — but also two overlapping providers to maintain.
- does it read existing settings first, or overwrite them?
- is its display naming trustworthy? Some enrich names from public model metadata, so a model
  appears under its marketing name rather than the upstream ID. Cosmetic, but confusing when
  auditing a picker.

Read the plugin's source before trusting its claims; these are small enough to read.

## Relay streaming protocol substitution (`openai-compatibility` vs `claude`)

Many third-party AI gateways and relay vendors natively speak the Anthropic wire protocol
(`/v1/messages`) and provide an OpenAI compatibility layer (`/v1/chat/completions`) as an
afterthought.

A common failure mode in these secondary OpenAI translation layers is an **asynchronous buffer flush race**:
the relay signals a terminal choice (`{"finish_reason": "stop"}` + usage stats) while a few
remaining output tokens are still buffered in its pipeline, flushing the trailing text delta
in a subsequent SSE chunk immediately after the finish chunk.

OpenCode's OpenAI stream consumer enforces strict completion state: receiving any content delta
after a non-empty `finish_reason` raises:
```
Error: OpenAI Chat received content after the finish reason
```

### The Fix
Do not attempt to modify OpenCode or write a proxy if the relay also exposes `/v1/messages`.
Instead, switch CLIProxyAPI's upstream provider from `openai-compatibility` to `claude`:

```yaml
api-keys:
  claude:
    - name: "vendor-claude-gateway"
      base-url: "https://relay.example.com"  # note: root URL; CLIProxyAPI appends /v1/messages
      keys:
        - api-key: "sk-..."
      models:
        - name: "upstream-model-name"
          alias: "vendor/upstream-model-name"
```

CLIProxyAPI's internal Anthropic-to-OpenAI translator consumes the relay's native Anthropic stream
and re-emits a spec-compliant OpenAI SSE stream to OpenCode:
`content deltas` → `finish_reason` → `usage` → `[DONE]`. OpenCode's client configuration, model
aliases, and rotation pools remain unchanged.

## Upstream that speaks only the Responses API: a `codex` API-key group

Some relays expose their GPT models only on `/v1/responses` and reject
`/v1/chat/completions`. The gateway's `codex` executor speaks the Responses protocol upstream
and translates it for both downstream surfaces, so register the upstream as an `api-keys.codex`
group instead of a direct client provider:

```yaml
api-keys:
  codex:
    - name: "my-relay-codex"
      base-url: "https://relay.example/v1"   # API root; the executor appends /responses
      headers:
        "User-Agent": "claude-cli/2.1.220 (external, cli)"  # exact value from the working client
        "X-App": "cli"
      keys:
        - api-key: "sk-..."
          disable-codex-cloaking: true   # keep YOUR headers; the gateway otherwise forces the official Codex UA/Originator
      models:
        - name: "<upstream-model>"
          alias: "relay/<upstream-model>"   # client-visible ID; must match the OpenCode model key
```

`disable-codex-cloaking` is the field that matters for an identity-allowlisting relay: the
gateway otherwise replaces the request's `User-Agent` and `Originator` with the official
Codex values, discarding the headers that got you access.

Verify **both** surfaces through the gateway, because they take different paths:

```bash
# native Responses surface
curl -s $GW/v1/responses -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"relay/<upstream-model>","input":"Reply with only: ok","max_output_tokens":30}'

# chat bridge — what an openai-compatible client actually calls
curl -s $GW/v1/chat/completions -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"relay/<upstream-model>","messages":[{"role":"user","content":"Reply with only: ok"}],"max_tokens":30}'
```

A passing `/v1/responses` probe alone does not prove the client path: OpenCode's
openai-compatible provider calls the chat surface, and a field injected by the gateway can
break one surface without the other. Probe both; see TROUBLESHOOTING.md.

## Editing the v8 gate config without a YAML round-trip

CLIProxyAPI v8 keeps the whole `openai-compatibility` group list as **inline JSON on a single
YAML line**. A PyYAML load/dump round-trip rewrites the file and deletes its comments — and
most of that file is comments. Edit the one line:

```python
import json

lines = open(path).read().splitlines(keepends=True)
i = next(i for i, line in enumerate(lines)
         if line.lstrip().startswith("openai-compatibility:"))
prefix, payload = lines[i].split("openai-compatibility:", 1)
groups = json.loads(payload.strip())
groups.append(new_group)
lines[i] = prefix + "openai-compatibility: " + json.dumps(groups, separators=(",", ": ")) + "\n"
open(path, "w").write("".join(lines))
```

Then assert on the artifact, not the write: load the backup and the result with
`yaml.safe_load`, compare their top-level key sets, and diff the files to confirm only that
line changed. Restart the service and probe the aliases through the gateway.