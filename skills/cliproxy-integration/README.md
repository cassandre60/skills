# cliproxy-integration

Wiring an OpenAI-compatible AI gateway (CLIProxyAPI / OmniRoute / any `/v1`) into
OpenCode and Codex CLI — and, just as importantly, the failure modes that cost
real time when nobody has written them down.

## What this covers

| Path | What it is |
| --- | --- |
| `SKILL.md` | entrypoint: workflow, rules, diagnostic index |
| `references/` | one file per failure class, loaded on demand |
| `scripts/` | probing and repair helpers |
| `FIELD-NOTES.md` | observed behaviour of real gateways |
| `TROUBLESHOOTING.md` | symptom → cause index |

The recurring lesson: **discover real model IDs instead of guessing, and source
model limits instead of inventing them.** A gateway that resolves no models, or
resolves them with fabricated context windows, fails in ways that look like
OpenCode bugs and are not.

## Install

Copy this directory into wherever your agent reads skills from:

```sh
cp -r skills/cliproxy-integration ~/.config/opencode/skills/
```

Or install the whole set from the repository root.

## Notes on credentials

Nothing here contains a live credential. Keys are read from the environment
(`OMNIROUTE_API_KEY` and friends) or from a vault file you keep outside the repo.
If you add a key dump or onboarding helper to `scripts/`, keep it reading from the
environment and never from a committed file.
