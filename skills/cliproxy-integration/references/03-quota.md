# 03 — Credential pools, quota semantics, and routing

Applies to gateways that pool multiple accounts (OAuth files, API keys) behind one endpoint:
CLIProxyAPI, OmniRoute, and similar.

## The quota insight that resolves most confusion

Providers show **more than one quota dimension**, and a request needs headroom in **all** of
them. A panel can truthfully display "you have access to model X" and every request still
fail.

Typical shape:

| Meter | Scope | Effect |
| --- | --- | --- |
| Short rolling window (5h, 1h) | speed | throttle bursts; recovers in minutes/hours |
| Weekly / monthly allowance | total | **hard stop** until it resets days later |

Reading a panel correctly:

- "Quota available / N% remaining" on the short window means *not currently throttled*.
- **0% remaining on the weekly meter overrides that.** No weekly allowance = no requests,
  regardless of the short window being green. The short window's "refreshes in 16m" is a red
  herring — refreshing an empty allowance changes nothing.
- Panels group models. A group listed as "Claude Opus, Claude Sonnet, GPT-OSS" means those
  models **share one allowance**; a sibling group (e.g. "Gemini Flash, Gemini Pro") is
  independent. One group's exhaustion explains why some models fail while siblings work —
  and tells you the blast radius before you test each model.

**Confirm the grouping by probe, not by inference.** Test one model per advertised group. If a
group's models fail together, the group is capped.

## Distinguish exhaustion from a real outage

```bash
curl -s $GW/v1/chat/completions -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"<model>","messages":[{"role":"user","content":"hi"}],"max_tokens":5}'
```

Read the failure vocabulary:

| Message | Meaning | Action |
| --- | --- | --- |
| `429 Individual quota reached` / `RESOURCE_EXHAUSTED` | that account's allowance is spent | wait for reset, or add accounts |
| instant `429 RESOURCE_EXHAUSTED` on every account for one model, while tiny/no-system prompts pass | upstream client-name filter on the system prompt, not quota | see the section below |
| an `INSUFFICIENT_*`-style reason with no credits | allowance spent **and** no paid credits to cover overage | credits may help — verify the fallback actually fires |
| `Budget pool quota has been exhausted. Please ask an administrator to increase the limit or select another budget pool.` | the relay's **group budget pool** is spent — not your key's model quota | only the relay operator or a pool reset clears it; rotating keys inside the same pool does nothing. Re-probe later before treating a key as dead — these can recover on their own |
| `All credentials for model X are cooling down` | gateway-side state, not upstream truth | restart the gateway, then walk the pool with tiny probes (one per credential; automatic under round-robin) — the warn line's remaining values are not proof of exhaustion |
| `circuit breaker open` | transient upstream instability | retry with backoff |

Some gateways advertise a paid-credit fallback for exhausted quota. **Verify it works in your
version before relying on it** — it is a known silent failure in several gateway projects
(the config flag exists, the fallback path never triggers). Test by exhausting quota in a
controlled account, or read the project's issue tracker for the flag name.

## Pool sizing is the real fix

Rolling-window caps plus per-account ceilings mean a pool sized for occasional use will
exhaust. A pool of ~12 metered accounts sustained bursty use and hit weekly walls on one model
group while a sibling group still had headroom.

Sizing guidance that generalises:

- **More accounts, not bigger windows.** Window size is not yours to change.
- **Group-aware:** size for the *group* with the smallest cap, since its models fail first.
- **Watch the weekly clock**, not the 5-hour one. Exhausted-weekly accounts will not recover
  quickly; plan around that.
- **Consider paid credits** where the group's model quality justifies it — but only after
  confirming the fallback path works.

## Routing strategy trade-offs

Most of these gateways offer `round-robin`, `fill-first`, and weighted variants.

| Strategy | Spreads load | Per-credential cache warmth | Hits caps |
| --- | --- | --- | --- |
| `round-robin` | evenest | lowest (every account's cache cools) | latest |
| `fill-first` | concentrated on one | **highest** | earliest — one account burns its window while others idle |
| weighted | by configured weight | tunable | tunable |

There is no free lunch. Concentrating load keeps a credential's cache warm (cheaper, faster
per-request) but consumes its ceiling faster; distributing load protects against caps but
pays more cache-miss cost everywhere.

**Anti-ban consideration:** these pools are frequently multi-account and IP-rotating, which is
a terms-of-service risk with the upstream provider. Confirm your use is permitted before
scaling a pool; capacity planning does not change that.

## Diagnosing "my account still has quota"

When one account's panel shows headroom but the pool still fails:

1. Test **several** models spanning the advertised groups (see grouping above).
2. If a whole group fails while another works → group allowance, not per-account flakiness.
3. Restart the service to clear cooldown state, then re-test — you may be reading gateway
   state rather than upstream truth.
4. Only conclude "this account still has quota" from a request **routed to that account**.
   Pool routing decides who gets used; an idle healthy account proves nothing until selected.
   If the gateway supports per-credential prefixes, target it explicitly.

## The cooldown warn line is the pool's state dump

When selection fails, the gateway logs one warn line per attempt containing the whole pool
state. Read it before touching anything:

```
auth unavailable: 10 of 10 candidate(s) for model "X" are in cooldown:
  [provider=antigravity auth_file=antigravity-name.json, reason=quota, remaining=130h27m22s], ...
```

- **Candidate accounting.** `N of M` against the auth dir's file count: files missing from
  the list are excluded silently — a `disabled` flag in the auth JSON, a dead refresh
  token, or a credential pinned busy by an in-flight request (session affinity). Diff the
  list against the directory; the dashboard does not show exclusions.
- **The bookkeeping can be flat wrong.** Observed on a live pool: every credential sat
  behind `reason=quota` cooldowns of ~125–134h, mutually aligned as if reset-parsed —
  after a service restart, all ten answered `200` on back-to-back tiny probes. A
  reset-aligned warn line is a hypothesis, not proof of exhaustion. The decisive, bounded
  test for pool-wide cooldown: restart the gateway (clears in-memory state) and walk the
  pool — one tiny probe per credential (automatic under `round-robin`; re-cooling failed
  credentials is the cost of the walk). Trust the probes over the warn line.
- **Short, divergent `remaining` values** are windows lifting soon. Lift time = journal
  timestamp (UTC) + `remaining`; the app-embedded timestamp may be local time.
- **An interval-retrying client re-arms cooldowns at lift time.** The first retry after
  expiry is a real upstream request, its 429 starts a fresh cooldown, and the pool looks
  permanently dead. Stop the retrying client *before* the earliest lift — not merely while
  diagnosing — then send one tiny probe.

## A disabled credential is invisible, not cooling

OAuth auth files carry `"disabled": true/false`. A disabled credential is dropped from the
candidate set without a log line and without entering cooldown — while its dashboard keeps
showing quota. When a named account "still has quota" but is never selected:

1. Read the auth JSON's `disabled` and `expired` fields — not the dashboard.
2. Flip `disabled` to `false` (back up the file first). Auth files hot-reload — the watcher
   logs `auth file changed (WRITE)`, no restart needed.
3. The access token refreshes automatically on use. If the refresh token itself is dead the
   file is re-marked — re-auth the account rather than flipping the flag again.

A probe scheduled for just after the earliest cooldown lift answers "does this account
still have quota" with one tiny request — once the retrying client is stopped.

## A 429 that is not quota: upstream client-name filtering

An upstream can return a genuine-looking `429 RESOURCE_EXHAUSTED` for requests it does not
want to serve, for reasons unrelated to quota. Verified 2026-11-11 against a live
Antigravity pool: the backend serving one model returned an instant, account-independent
429 for any request whose **system** prompt contained the substring `opencode`
(case-insensitive). Every account refused it; every restart, cooldown clear, and failover
was irrelevant; the panel's quota meters were unrelated. The client's own harness prompt
self-identifies (`You are OpenCode, …`), so that client could never succeed on the model.

Tells that separate this from real exhaustion:

- Tiny probes pass in the same minute the real client fails. Size is not the trigger — a
  70 KB filler system prompt passed while a 25 KB real one failed.
- The same text **as a user message** passes; as a **system message** it fails. The filter
  reads the system field.
- It is instant and account-independent: failover walks the whole pool (auth-file
  token-refresh mtimes prove dispatch) and every credential returns the same 429.
- It can be model-scoped: a sibling model on the same pool accepted the same word.

Isolation protocol that found the trigger (tiny sequential probes, same pool, same minute):

1. Replicate the client's exact shape with curl (system content, `stream`, `tools`,
   `max_tokens`). When the replica fails and every stripped-down variant passes, bisect:
   control → stream-only → +tools+output-cap → full replica.
2. Swap the role: the same text as a user message. A pass relocates the trigger to the
   system field specifically.
3. Swap the content for same-size filler. A pass rules out size, leaving content.
4. Binary-search the failing text — restarting the gateway when a probe returns the
   cooldown wrapper keeps the walk hitting live credentials — down to a minimal string,
   then test word candidates directly (`opencode` vs `OpenCode` vs `open code`).

Remedy at the gateway — Antigravity sensitive-word obfuscation:

```yaml
# On config-version: 8, oauth.providers.antigravity is the canonical path
# (flattenV8 maps it to cfg.Antigravity and overwrites any legacy top-level antigravity: block).
oauth:
  providers:
    antigravity:
      sensitive-words:
        - "OpenCode"
        - "opencode"
```

- **The knob must be wired in the build.** v8.0.16 parsed the key and never called the
  obfuscator on Antigravity request paths; v8.0.67 calls it on execute, stream, and
  token-count paths. A config knob that parses is not a knob that works — confirm against
  the release's source, or probe live.
- The obfuscation inserts **U+034F (combining grapheme joiner)** after the first rune of
  each alphanumeric subword. The tested upstream honored that (`o\u034Fpencode` → 200) but
  **stripped U+200B zero-width space** (`o\u200Bpencode` → still 429). Invisible-character
  schemes are not interchangeable; the CGJ variant is the one that worked.
- It rewrites only `request.systemInstruction` — which is exactly where the
  chat-completions→Antigravity translator places client system messages, i.e. the poisoned
  field. The client needs no changes; the gateway transforms the payload before dispatch.
- Same-pool scope check: one probe of a sibling antigravity model with the same word tells
  you whether the filter is model-specific or endpoint-wide.

Upgrading the gateway binary in place under a systemd user unit: `systemctl --user stop`,
replace the binary (verify the release `checksums.txt`), `start` — copying over a running
binary fails with `Text file busy`. A same-major upgrade kept the v8 config and auth
files; back up both first.

## Mixed-tier credential pools: capability hints and model pinning

When pooling accounts under one provider (e.g. Google Antigravity OAuth), credentials often
have different subscription tiers. Some accounts may be paid Pro or Ultra (carrying access
to frontier models like Gemini 4 Argon or Claude 5.5), while standard or free accounts do
not.

If the gateway routes requests blindly across all credentials, requests for tier-restricted
models fail when dispatched to standard accounts, triggering unnecessary failover walks and
cooldown cascades.

### 1. Discover capabilities from auth files without spending tokens

In CLIProxyAPI Antigravity OAuth files, upstream returns capability metadata recorded in
`antigravity_capability_hints.model_ids` (and `web_search_model_ids`):

```json
{
  "antigravity_capability_hints": {
    "model_ids": [
      "claude-opus-5-5-high",
      "claude-sonnet-5-5-high",
      "gemini-3.8-flash-high",
      "gemini-4-argon-high",
      ...
    ]
  }
}
```

Whereas standard accounts explicitly mark unsupported models or omit them:

```json
{
  "antigravity_capability_hints": {
    "model_ids": [
      "claude-opus-4-6-thinking",
      "claude-sonnet-4-6",
      "gemini-3.8-flash-high",
      "gemini-4-argon-disabled",
      ...
    ]
  }
}
```

Read these directly across the auth directory with `jq` to map your pool's capabilities
without spending tokens or sending test completions:

```bash
for f in ~/.cli-proxy-api/antigravity-*.json; do
  jq -r --arg n "$(basename "$f" .json)" '[$n, ([.antigravity_capability_hints.model_ids[]? | select(test("5-5|argon"))] | join(", "))] | @tsv' "$f"
done
```

### 2. Pin models to capable credentials via `excluded_models`

To ensure tier-restricted models only route to capable accounts, add `excluded_models` to the
auth JSON of the non-capable accounts:

```json
{
  "excluded_models": [
    "claude-opus-5-5-high",
    "claude-opus-5-5-low",
    "claude-opus-5-5-medium",
    "claude-sonnet-5-5-high",
    "claude-sonnet-5-5-low",
    "claude-sonnet-5-5-medium",
    "gemini-4-argon-high",
    "gemini-4-argon-low",
    "gemini-4-argon-medium"
  ]
}
```

Key operational facts:

- **Gateway honors it:** The gateway excludes any credential from candidate selection if
  the requested model appears in that credential's `excluded_models` array.
- **Survives automatic token refresh:** The gateway's token auto-refresh loop preserves
  `excluded_models` when writing back updated access tokens.
- **Append, do not overwrite:** If an auth file already has exclusions, append the new
  models (`.excluded_models = ((.excluded_models // []) + $list)`).
- **Include tier variants:** Include `-low`, `-medium`, and `-high` variants if upstream
  uses internal tiering, so any aliased or resolved model name is covered.
- **Re-check hints when plans change:** Capability hints refresh dynamically as accounts
  upgrade or get revoked; if a previously excluded account gains hints for frontier models,
  remove its stale `excluded_models` entries so traffic can route to it.