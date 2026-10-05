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
| an `INSUFFICIENT_*`-style reason with no credits | allowance spent **and** no paid credits to cover overage | credits may help — verify the fallback actually fires |
| `All credentials for model X are cooling down` | gateway-side state, not upstream truth | restart the service (clears in-memory cooldown) to get an accurate reading |
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