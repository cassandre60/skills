# Gateway integration skill (OpenCode + Codex)

Field manual for wiring OpenAI-compatible AI gateways into
[OpenCode](https://opencode.ai) and Codex CLI — and, just as importantly, for the
failure modes that cost real time when nobody has written them down.

Contains an installable OpenCode skill plus this repository's supporting material.

## Contents

| Path | What it is |
| --- | --- |
| `skill/SKILL.md` | the skill entrypoint — workflow, rules, diagnostic index |
| `skill/references/` | one file per failure class, loaded on demand |
| `skill/scripts/probe-models.sh` | list a gateway's models and prove each answers |
| `skill/scripts/sync-limits.py` | re-source model limits from a live catalog (idempotent) |
| `skill/scripts/onboard-keys.py` | onboard a messy file of API keys: validate, keep the good ones, register models |
| `TROUBLESHOOTING.md` | symptom → cause → fix quick reference |
| `FIELD-NOTES.md` | the lessons behind the rules, including the wrong turns |
| `LICENSE` | MIT |

## Install

```bash
git clone https://github.com/<owner>/cliproxy-integration-skill.git
mkdir -p ~/.config/opencode/skills
ln -s "$PWD/skill" ~/.config/opencode/skills/cliproxy-integration-skill
```

Then restart OpenCode, or start a session and use `/skills`.

Symlinking (rather than copying) keeps the skill updatable with `git pull`.

## The five rules the skill exists to enforce

1. **Never invent a model ID.** Query `/v1/models`. Plausible-looking names are not evidence.
2. **Never invent a limit.** Source `context` / `output` from metadata. Low guesses make the
   client compact early and truncate replies — you pay for the tokens regardless.
3. **A model key is the upstream ID, verbatim.** The client builds `provider/model`; it does
   not translate.
4. **Verify with a request, not a config read.** Valid JSON proves nothing. Send a 5-token
   completion per model.
5. **Kill a hypothesis the moment it is contradicted.** Carrying a dead theory into a "fix" is
   the single largest time sink in this domain.

## Quick start

```bash
# what does this gateway actually serve, and does each model answer?
./skill/scripts/probe-models.sh --base-url http://localhost:8317/v1 --api-key sk-...

# source real limits into the OpenCode config
./skill/scripts/sync-limits.py --base-url http://localhost:20128/v1 \
    --api-key sk-... --provider omniroute

# onboard a file of API keys (some dead) and register two models — dry run first
./skill/scripts/onboard-keys.py --keys-file keys.txt --model gemini-2.5-flash
```

## Scope

Applies to CLIProxyAPI, OmniRoute, and any gateway exposing an OpenAI-compatible surface.
Gated relays are in scope too — one of the hard-won lessons here is that an upstream can be
reachable with the right key and still reject a client on identity headers alone.