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

| Skill | Use it when |
|---|---|
| **[mobile-app-to-web-demo](./skills/mobile-app-to-web-demo/SKILL.md)** | Putting a real mobile app into a web page as an interactive demo — and specifically when someone says the demo "doesn't look like the app" |
| **[agent-proof-codebase](./skills/agent-proof-codebase/SKILL.md)** | Hardening a repo so an accepted change is high-signal: invariant specs, tests that can actually fail, mutation testing, ratchets, protected referee files |

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

```
skills/
  mobile-app-to-web-demo/
    SKILL.md
  agent-proof-codebase/
    SKILL.md
    references/   # one file per concern, loaded on demand
    scripts/      # assess_repo.py, ratchet/mutation/protected-path checks
    assets/       # templates to copy, not rewrite
```

References are split out so a model loads one concern when it reaches that step,
instead of carrying every detail in the entrypoint.

## History

These were separate repositories, one skill each. Consolidating open skills into
one means a single place to install from and a single place to keep conventions
consistent — a repo per skill does not scale past a handful.

Skills that stay private keep their own repo. Nothing is duplicated here, so
there is no second copy to drift and no visibility change nobody asked for.

Skills that stay private keep their own repo. Nothing is duplicated here, so
there is no second copy to drift and no visibility change nobody asked for.

## Licence

MIT. The skills here were developed independently; the `SKILL.md` frontmatter
convention (`name` + trigger-rich `description`) follows the widely used Agent
Skills format.
