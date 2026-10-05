# 08 — Porting credentials between gateways on the same machine

The scenario: the keys you need already live inside another gateway on the same host (stored
encrypted in its database), and you want them in this gateway's config without retyping —
and without ever learning whether they work by burning completions.

## Find where the keys actually live

Gateway credentials are rarely where you first look. A provider may appear under a generic
group id (`openai-compatible-chat-<uuid>`) rather than its vendor name. Join the connection
table against the node/endpoint table on the group id:

```sql
SELECT id, name, base_url FROM provider_nodes;
SELECT id, provider, name, auth_type, is_active FROM provider_connections
WHERE provider = '<group-id-from-above>';
```

Confirm the base URL matches the vendor you expect before touching any key material.

## Decrypt locally, verify before writing

Stored keys are typically AES-256-GCM (`enc:v1:<iv>:<ciphertext>:<tag>`) under a key derived
from an env file on the same machine (scrypt with a static salt is one common construction —
read the gateway's own decrypt routine rather than guessing parameters). Reimplementing the
derivation takes a few lines once you've read it; the env file gives you the secret.

Then, in order:

1. **Validate every key** against the vendor's free listing endpoint. Keep only working ones.
2. **Confirm the target model is served** to at least one working key — same free call.
3. **Write** the keys plus a `models:` entry into the destination config (back it up first).
4. **Destroy temp key material** (`shred -u` the intermediate file). The config file itself
   already holds keys in plaintext by design; the temp file must not survive alongside it.

## Two rules this workflow earned

**Reconstruct secrets only from source, never from redacted output.** Terminal output that
masks keys (`sk-abc…xyz`) is for humans. Rebuilding a "full" key from a prefix plus a suffix
produces a plausible-looking string that authenticates nowhere — and it fails silently at
request time, far from where it was written. If you catch yourself assembling a key from
fragments, stop and go back to the decrypted source.

**Listing calls are the free validation layer.** A model-list endpoint answers "is this key
alive" and "does it serve this model" for zero tokens. That covers everything except the
final proof that a completion succeeds — which is the only step that should ever cost
tokens (see SKILL.md step 4, cost discipline).