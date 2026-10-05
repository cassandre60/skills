# 06 — Onboarding a key dump into a provider

The scenario: a vendor sold you (or you accumulated) a text file of API keys. Some work,
some are dead. You want a working provider in your gateway with a couple of models showing
up in the client — without hand-editing two config files and guessing which keys are good.

## The file will be messy

Real key dumps contain prose, separators, blank lines, notes from the seller, and the
occasional wrapped line. Do not assume one clean key per line.

```text
Here are your keys, thanks for your business!
----- account 1 -----
AIzaSyRealKey...
random note from seller
```

The script filters by pattern, so prose is skipped. For Google keys the default pattern is
`^AIza[0-9A-Za-z_\-]{20,}$`; override with `--key-pattern` for other vendors.

## One request per key tells you everything

`GET {base}/v1beta/models?key=KEY` on Google returns the key's **usable model list** when the
key is valid, and a clear error when it isn't. So a single request per key answers both
questions — is this key alive, and which models can it actually serve?

That second half matters more than it looks. It lets you check a requested model ID against
the provider's own listing instead of trusting that a plausible name exists.

## Use the script

```bash
# 1. dry run - validate keys, confirm models, change nothing
./scripts/onboard-keys.py --keys-file keys.txt --model gemini-2.5-flash --model gemini-2.5-pro

# 2. apply
./scripts/onboard-keys.py --keys-file keys.txt \
    --model gemini-2.5-flash --model gemini-2.5-pro \
    --gateway-key <gateway-access-key> \
    --apply
```

It reports keys found / valid / dead (grouped by reason), confirms each requested model
against the provider listing, writes the provider group with **only the working keys**,
registers the models in the client config (both `provider` and `providers` blocks), backs
both files up, and probes each model through the gateway if you pass `--gateway-key`.

Re-running replaces the group rather than appending, so it is safe to repeat after adding
more keys. Re-running preserves everything the script doesn't own — auth headers, `disabled`
flags, existing aliases — and only rebuilds keys (always) plus base-url/models-list (only
when you explicitly pass new values).

## Namespacing with --alias-prefix

`--alias-prefix codecraftapi` registers each model under `codecraftapi/<model>` at the
gateway and writes that same namespaced ID into the client, so the picker shows the source.
Without it, bare upstream IDs are used. Either way the script probes the exact ID it is
about to write — never the upstream name and hope.

For `openai-compatibility`-style groups the script always writes an explicit models list,
because those groups serve **nothing** without one. Native providers (gemini) are left
alone: they serve their whole catalog, and a list there would only restrict it.

## Before you run it on a gateway you already use

**The script creates a new group; it does not fill an existing empty one.** Gateways often
already contain a provider section with `keys: []` — a group someone started and abandoned.
If you want to populate that one instead, pass its exact name via `--group-name`. Using a
different name leaves you with two groups for the same provider, which is valid but confusing.

Check what is already there first:

```bash
grep -n '^api-keys:' -A 30 ~/.cli-proxy-api/config.yaml
```

An existing group with `keys: []` tells you the group *name* to reuse (via `--group-name`),
nothing else. Its `base-url` was never exercised, so it is not evidence for which base the
executor expects — a native root (`https://generativelanguage.googleapis.com`) versus an
OpenAI-compat suffix (`.../v1beta/openai/`) decides whether the gateway builds a correct
upstream URL or a doubled one (see `01-wiring.md`, "The base-url double-path trap"). If in
doubt, probe one known-good key under each candidate base through the gateway before
running `--apply` on the full file.

## Endpoints per provider

| Provider | Model list | Native endpoint |
| --- | --- | --- |
| `gemini` | `/v1beta/models` | `https://generativelanguage.googleapis.com` |
| `openai-compatibility` | `/models` | whatever you pass as `--base-url` |

Some gateways route Google keys through the OpenAI-compatible surface instead
(`.../v1beta/openai/`) so the models can be served over a chat-completions API. Both work; the
native path is the default when you omit `--base-url`. Match whichever your gateway already
uses for that provider, otherwise you end up with the same model served two ways.

## Limits: deliberately not written

The script writes model entries with **no** limit values and says so. This is intentional and
worth keeping: a wrong low limit makes the client compact context far too early and truncate
long replies, while you still pay for every token. An omitted limit degrades gracefully; a
fabricated one costs you quietly.

Source real values afterwards (see `03` and `sync-limits.py`) if a sibling gateway publishes
them for the same models.

## Verifying YAML without a parser

Gateway configs are mostly commented documentation. Round-tripping them through a YAML library
would discard that, so this script edits the text surgically. That trades a real parse check
for preservation of comments, so compensate:

- the script runs a **structural self-check** (even indentation, balanced quotes on touched
  lines, group present) and refuses to report success if it fails;
- the authoritative check is **restarting the gateway** — malformed YAML makes it refuse to
  start, loudly;
- a backup is written before any change, and the exact rollback command is printed.

If you have `yq` or PyYAML available, adding a real parse check to this script is a small,
worthwhile improvement.

## Order of operations after applying

1. Restart the gateway service.
2. Confirm it stayed up (this is the YAML validation).
3. Reload the client.
4. Check the model appears and answers.

Do not skip step 2 on the assumption that a clean script exit means a working config.