# skills

Agent skills for engineering work where correctness matters more than speed.

Each skill is a `SKILL.md` with YAML frontmatter. The `description` is the only
part a model sees before deciding whether to open the file, so it is written as
*when to reach for this* rather than *what this is*.

## Install

```sh
npx skills@latest add mansourvery-hub/skills
```

Or copy the folder you want into wherever your agent reads skills from
(`.claude/skills/`, `.agents/skills/`, …).

## Skills

<!-- SKILLS:START -->
| Skill | Use it when |
|---|---|
| **[agent-proof-codebase](./skills/agent-proof-codebase/SKILL.md)** | Build, audit, and work inside codebases whose deterministic gates (invariant specs, property/model/differential/crash-consistency tests, mutation testing, fail-to-pass bug-fix proof, ratchets, protected referee files, red-team corpus) make any accepted change high-signal even from weak or sloppy AI agents. Use whenever the user wants to harden a repo against low-quality or AI-generated code, design CI quality gates, make tests that can actually fail instead of checkbox tests, audit test-suite strength, set up mutation testing, write invariants or a SPEC, add performance budgets or ratchets, prepare a project for release with rigorous verification, stop agents from gaming tests, or asks for bulletproof, antifragile, or "premium quality" code. Also use when a repo already has SPEC.md, GATES.md, or .gates/ and you are asked to change it. |
| **[cliproxy-integration](./skills/cliproxy-integration/SKILL.md)** | Wire an OpenAI-compatible AI gateway (CLIProxyAPI / OmniRoute / any /v1 gateway) into OpenCode and Codex CLI correctly - discover real model IDs instead of guessing, register providers that resolve, source model limits instead of inventing them, attribute models to their upstream, and diagnose the gateway errors that block model selection. Use whenever someone asks to add models to OpenCode, connect OpenCode or Codex to a gateway/proxy/relay, fix "unknown provider for model", "model_not_found", 401/403 from an upstream gateway, missing models in the /model picker, wrong context/output limits, model routing or credential-pool behaviour, quota/429 exhaustion on pooled accounts, or WARP/proxy egress setup for API traffic. |
| **[mobile-app-to-web-demo](./skills/mobile-app-to-web-demo/SKILL.md)** | Port a real mobile app into an interactive demo embedded in a web page, generated from the app's own code so it cannot drift. Use when a user wants a site to show a working demo of their mobile app (Android/iOS/Flutter/React Native), complains the demo "doesn't look like the app", or asks to embed the real app in a website. |
<!-- SKILLS:END -->

## What the first two have in common

Both were written after getting the thing wrong, and both say so.

`mobile-app-to-web-demo` shipped a hand-written HTML re-implementation of a Flutter
app. It loaded fast, it passed every test, and it was still wrong — because a
re-implementation is a *second* implementation of the app, so every fix is
whack-a-mole and no release ever tells you the demo has drifted. The skill's spine
is that decision, and the traps section is scar tissue in the order it was hit.

`agent-proof-codebase` exists for the same species of failure one level up: an
agent that optimises the reward it can see. It writes the test and the
implementation, the test passes, and success is reported. Every gate in it exists
to make that path fail.

## Layout

<!-- LAYOUT:START -->
```
skills/
  agent-proof-codebase/
    SKILL.md
    LICENSE
    README.md
    assets/     # 11 files
    references/ # 13 files
    scripts/    # 9 files
  cliproxy-integration/
    SKILL.md
    FIELD-NOTES.md
    README.md
    TROUBLESHOOTING.md
    references/ # 8 files
    scripts/    # 4 files
  mobile-app-to-web-demo/
    SKILL.md
```
<!-- LAYOUT:END -->

References are split out so a model loads one concern when it reaches that step,
instead of carrying every detail in the entrypoint.

## History

Each of these was its own repository. One place to install from and one place to
keep conventions consistent beats a repo per skill — and keeping them apart means
two copies drift, which is the exact failure `agent-proof-codebase` exists to
prevent.

Skills that stay private keep their own repo. Nothing is duplicated here, so
there is no second copy to drift and no visibility change nobody asked for.

## Support

Building small, useful software — if this project saves you time, consider [buying me a coffee](https://buymeacoffee.com/cassandre60) to help keep it maintained.

<a href="https://buymeacoffee.com/cassandre60"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" width="150" alt="Buy Me A Coffee"></a>

## Licence

MIT. The skills here were developed independently; the `SKILL.md` frontmatter
convention (`name` + trigger-rich `description`) follows the widely used Agent
Skills format.
