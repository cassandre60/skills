# Field notes

Why the rules in the skill are written the way they are. Includes the wrong turns, because a
rule you understand beats a rule you obey.

## The slash trap

The most valuable single lesson.

A client builds the full model ID as `provider/model` and splits on the **first** slash, so it
happily accepts a model key that itself contains slashes. The gateway then receives
`source/model`, reads `source` as a **credential prefix selector**, finds no credential group
with that prefix, and returns:

```
unknown provider for model source/model
```

The client displays this as if the *client* had an unknown provider. It hasn't — the gateway
rejected it. Two layers, each behaving correctly, producing a confusing error.

The tell: the same model works under its bare ID and fails when namespaced. Namespacing is
worth having (you can see which upstream serves what), but it must be **registered at the
gateway** — model aliases for OAuth credentials, `alias` in the group for API-key ones.

Related: setting `prefix` *and* `alias` registers the model twice (bare and prefixed). Pick
one mechanism.

## The contradicted hypothesis

A relay gateway rejected a request with an "unauthorized client" error. The theory that
absorbed the most time: the upstream rejects requests carrying a `User-Agent`.

That theory was built on one observation — a request from a language runtime succeeded, one
with an explicit header failed — and it never died, even after a later test appeared to
contradict it. The correct move was to declare it dead on the spot and return to first
principles.

Two errors compounded it:

1. **Treating an unset header as an absent header.** A runtime supplying its own default
   `User-Agent` is not the same as sending none. The control was invalid.
2. **Not using the available reference implementation.** A working client on the same machine,
   same key, same endpoint, was succeeding the whole time. Diffing against it would have
   answered the question in one step. Instead, a theory about headers was pursued while a
   working example sat unexamined.

The actual cause was a client-identity allowlist on the upstream: it accepted exactly one
`User-Agent` string, which was readable in the working client's source. Not the header's
presence — its *value*.

**Rules this produced:** verify a control before theorising; kill contradicted hypotheses
immediately; when a working reference exists, diff against it rather than guessing.

## Pushing back on the user was wrong

Asked to debug an upstream rejection, the advice "contact the vendor's support" was offered
while the user had already said the same integration worked elsewhere on the machine. The
user was right. Checking the working integration's own logs — one row showed the request had
been **translated into a different protocol** before dispatch — would have reframed the whole
problem immediately.

Cheap check, high value: before escalating outward, look for a working instance of the same
integration in your own environment and find out what it does differently.

## Probing beats inferring

Several conclusions in this domain are guessable but only measurable with a tiny request. A
5-token completion against every candidate model answers questions that reading config cannot:
does this ID exist, does it answer, is it quota-limited, is it gated.

Build that probe early. It converts "I think this model is configured" into "this model
answers `ok`", and it is the only reliable signal for a stale or wrong model ID.

## Deriving config from real traffic, not documentation

Guidance suggested obfuscating a specific string in requests to avoid a fingerprint block. The
string never appeared anywhere in actual traffic, because the client in use sends a different
fingerprint than the one the guidance assumed.

Cheap check, real payoff: grep the gateway's own request logs for the candidate strings before
adding them to a config. A config that lists terms with zero occurrences in traffic is inert at
best — and it misrepresents what you verified.

The underlying mechanism (injecting invisible characters to break a fingerprint) was still
correct and worth enabling; only the word list needed to come from evidence.

## Quota has more than one dimension

A panel displayed "quota available" for a model group while every request failed. Not a
contradiction: **two** meters were shown, the short rolling window had headroom, and the weekly
allowance was spent. A request needs room in both.

The mistake was dismissing the panel rather than reading it precisely, and inferring "the quota
you see must be a different model's" instead of checking whether models were grouped. Panels
group models into shared allowances — that grouping predicts exactly which models fail
together, which is a faster diagnostic than probing each model.

**Rule:** when a quota display disagrees with observed behaviour, the display is describing a
dimension you haven't identified. Read every number on it, including the small ones.

## Repair scripts beat hand-edits

A tool rewrote a config subtree with placeholder limits on every use of its UI. Hand-patching
was obviously fragile, so the fix was a small idempotent script that re-sources real values.

Two properties made it trustworthy, and both came from testing it:

- **It must be idempotent.** An early version reported "changes" forever on models whose
  catalog entry lacked an output limit — technically true, operationally useless.
- **It must distinguish *absent* from *present-but-incomplete*.** The first version lumped both
  into "not in catalog", which pointed at the wrong remedy entirely.

Test scripts against real data, run them twice, and check the second run is a no-op.

## Implicit dependencies in the network layer

A pooled gateway's upstream calls began failing. The gateway config was correct; the cause was
two levels down: a VPN's proxy port, which the gateway had come to depend on, was gone. Nobody
had configured that dependency deliberately.

The second failure in the same area was a malformed resolver entry — a scoped IPv6 address with
an interface suffix — that the operating system accepted and the VPN's stricter parser
rejected. The symptom pointed at the VPN; the cause was a DNS config file.

**Rules:** before changing network tooling, inventory what depends on it implicitly. When a
service reports "no network", read its logs for the actual parse error rather than the summary
status. And any fix applied to an auto-generated file needs a durable variant, or it silently
reverts on the next renewal.
## Assert on artifacts, not on return values

A config-writing function reported success while never saving the file. The message said
"added group", the caller printed it, and the script exited 0 — with the target file byte-for-byte
unchanged.

It was caught only because the test asserted on the **file's contents** after calling the
function, rather than trusting the success message it returned.

This is the same failure class as the config-reverting problem, one layer down: a tool that
reports success without producing an effect. Two edits were also lost to the same oversight —
a second early-return branch that skipped the write, and a branch that skipped writing the
values entirely.

**Rules:** after any write operation, re-read the artifact and assert the change is present.
Never let a success string be the only evidence. And be suspicious of early returns in a
function that mutates state — they are where the write gets skipped.

## Write the failure path first

The first test of the key-onboarding script used obviously fake keys. Every one was rejected
and the script declined to write — correct behaviour, but it proved only the unhappy path.

The write path is where the damage lives: malformed indentation into a config the service
parses at startup. Testing that against a **local fake server** (one that accepts a specific
key and rejects others) exercised validation-success, config surgery, backup, and the
structural check in a single pass, with no real credentials and no real config touched.

Worth the small detour: a throwaway HTTP server is a cheap way to test credential-handling
code end to end without spending anyone's quota.

## The log row that settled it

Onboarding 46 Gemini keys (33 valid), the gateway returned 404 for models that answered
fine direct. Several turns went into grepping the gateway binary for endpoint construction.
The answer was one log row the whole time:

```
err=Post "https://generativelanguage.googleapis.com/v1beta/openai/v1beta/models/gemini-3.5-flash:generateContent"
```

A group `base-url` ending in `/v1beta/openai/` plus the gateway's own appended
`/v1beta/models/…` — a doubled path. Native root as `base-url`, one completion through the
gateway, fixed. The pre-existing empty-keys group had carried that `base-url` untested, and
it was copied without questioning.

Same session, second avoidable detour: probes at `max_tokens=5` returned `content: null`
for thinking models, and a harness that truncated the JSON body before parsing misread a
success as failure. Budget 30–100 and full-body parsing from the start would have saved
both.

**Rules:** gateway logs before binary forensics, always; an unexercised config value is not
evidence no matter how long it has sat in the file; a null-content probe at a tiny budget
is inconclusive, not failed.

## The client that ignored its own config file

Wiring the same gateway into Codex CLI (`codex-cli 0.160.0`) produced a full evening of
misdirection with one root pattern: **the client accepted config and then didn't use it.**

- `wire_api = "chat"` was the only loud failure (an explicit error with a fix hint).
  Everything else failed silently: `openai_base_url`, `model`, `model_provider`, and
  `[model_providers.*]` all parsed cleanly — including under `--strict-config` — while
  requests kept going to `api.openai.com` with the default model. Identical values via
  `-c` worked first try.
- `codex doctor` reported `OPENAI_API_KEY` as present ("auth is provided by environment")
  while a header-dump listener proved no request carried any `Authorization` header.
  Only `codex login --with-api-key` (stored `auth.json`) attached credentials.
- `/model` ignored a parse-valid `model_catalog_json` until `api_key_model_discovery`
  (off by default) was enabled — the picker reads provider discovery, and a running TUI
  never re-reads any of it (restart required).

Each layer was isolated with one decisive test (header dump, `-c` vs file, `debug
models`), which is the only reason the evening ended in a working setup instead of
another theory. **Rules:** when a client ignores config, bisect file vs flag vs stored
state with one variable changed at a time; never trust a config checker that only
validates syntax; `doctor`-style diagnostics describe recognition, not use.

A second, quieter lesson from the same night: one `codex exec` fans out to ~10 upstream
touches (WebSocket attempts plus HTTPS fallback, times gateway-side credential retries),
which cooled all 33 pool keys in a single run against a tight quota. Retrying harder
made it worse; waiting (or restarting the gateway to clear in-memory cooldowns) fixed
it. **Rule:** when every credential cools at once, stop traffic first, then diagnose —
each additional probe is also load.
