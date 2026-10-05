# 02 — Upstream auth failures: the 401/403 ladder

A gateway returning `401`, `403`, or "unauthorized" for models it *lists* is a distinct
failure class. The model exists (it's in the catalog) and your gateway config is probably fine.
Work the ladder; each rung eliminates a class.

## Rung 1 — Control test: is it your setup at all?

Ask the decisive question before touching config:

> Does the **same upstream, same key, same endpoint** succeed through a *different* client?

| Control result | Meaning | Next |
| --- | --- | --- |
| Different client works | your gateway config is fine; the difference is request shape | Rung 3 |
| Different client also fails | key/endpoint/account problem | Rung 2 |
| No other client available | keep going; don't assume | Rung 2 |

Skipping this rung is how hours disappear. If a control works, stop theorising about your own
config and start diffing request shapes.

## Rung 2 — Eliminate key and endpoint

```bash
# same key, endpoint, model — straight at the upstream
curl -s https://upstream.example/v1/chat/completions \
  -H "Authorization: Bearer $UPSTREAM_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"<model>","messages":[{"role":"user","content":"hi"}],"max_tokens":5}'
```

- **Fails too** → the key/endpoint is the problem. Check the vendor's dashboard for real model
  IDs, key status, or account restrictions.
- **Succeeds** → key and endpoint are fine. Go to Rung 3.

Watch for masked credentials. A gateway may store keys encrypted (`enc:v1:...`) in its own
database, so comparing "the key" against your config file can produce a false mismatch. Only
conclude keys differ if you compared plaintext against plaintext.

Also verify you are not comparing against a **commented-out example** when scraping a config
file for its active keys — a regex over the whole file will happily match documentation.

## Rung 3 — Request shape: protocol, then headers, then body

Only meaningful once Rung 2 passes.

**Protocol.** Some upstreams gate access by endpoint, not by client. Test each surface:

```bash
# OpenAI-style
curl -s https://upstream.example/v1/chat/completions ... -d '{"model":...,"messages":[...]}'
# Anthropic-style
curl -s https://upstream.example/v1/messages \
  -H "x-api-key: $KEY" -H 'anthropic-version: 2023-06-01' -H 'Content-Type: application/json' \
  -d '{"model":...,"max_tokens":16,"messages":[...]}'
```

If one protocol is accepted and the other isn't, configure the gateway to use the accepted one
(the gateway usually exposes a native executor per protocol — e.g. an anthropic-style group
instead of an openai-compatible one).

**Headers.** Many relay gateways gate on client identity. Test the obvious identity headers,
and use your own successful client as the source of truth for the exact value:

| Header | Why gateways check it |
| --- | --- |
| `User-Agent` | most common allowlist by client identity |
| `X-App`, `X-Client-*` | secondary client markers |
| `anthropic-version` | protocol gate |

**Read the working client's source** for the exact string. A gateway that proxies an official
CLI will have that CLI's user-agent template in its own source; grep it rather than guessing
version numbers:

```bash
strings ./gateway-binary | grep -i 'client-version\|/v1\|user.agent' | head
grep -rn "User-Agent" /path/to/client/src/ | head
```

Then set it on the gateway group:

```yaml
api-keys:
  openai-compatibility:
    - name: "my-relay"
      base-url: "https://upstream.example/v1"
      headers:
        "User-Agent": "<exact string from the working client>"
      keys:
        - api-key: "sk-..."
```

**Body.** Less common, but a gateway may require a field the other client always sends
(`max_tokens`, a `system` block, a specific stream flag). Diff the successful request body
against yours if headers were ruled out.

## A caution about negative UA results

A result of the form "works without a header, fails with any value for that header" is
**usually a false positive of your own test client**, not a finding about the upstream. HTTP
clients often send a default `User-Agent` from a runtime, transport, or proxy layer you didn't
intend — and "I didn't set it" is not the same as "it wasn't sent".

Before concluding an upstream is header-hostile, verify with a client that genuinely suppresses
the header (`curl -H 'User-Agent:'`) and re-test the "working" path the same way. Mismatched
test clients produce phantom conclusions that send you fixing the wrong thing.

## When a pool rotates past the failure

Multi-credential gateways retry across credentials. One upstream error can therefore surface
as a **generic pool message** that hides the cause:

```
auth_unavailable: no auth available (providers=mygroup, model=x; last upstream error: ...)
```

Read the `last upstream error` tail — it holds the real status and body. And note that one
failure can **cool down the credential**, causing subsequent requests to report
`auth_unavailable` / "all credentials cooling" for a model that is otherwise fine. If
cooldowns are in-memory only (the usual default), restarting the service clears them and
restores an accurate picture immediately — a cheap diagnostic that also unblocks you.

Treat a long retry backoff after a *persistent* auth error as wasted time. Auth failures don't
heal by retrying; you must change the request or the credentials.