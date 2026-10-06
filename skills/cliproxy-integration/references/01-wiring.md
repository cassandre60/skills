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