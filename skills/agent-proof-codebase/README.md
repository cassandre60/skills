# agent-proof-codebase

Generic skill for building, auditing and working inside codebases whose deterministic gates make every accepted
change high-signal, even when written by weak or sloppy agents. Entry point: `SKILL.md`.

## Install

From the consolidated [`mansourvery-hub/skills`](https://github.com/mansourvery-hub/skills) repo:

```sh
npx skills@latest add mansourvery-hub/skills
```

Or copy this folder into wherever your agent reads skills from (folder name must stay `agent-proof-codebase`):

- Claude Code, all projects:  `~/.claude/skills/agent-proof-codebase/`
- Claude Code, one project:   `<repo>/.claude/skills/agent-proof-codebase/`
- opencode, all projects:     `~/.config/opencode/skills/agent-proof-codebase/`
- opencode, one project:      `<repo>/.opencode/skills/agent-proof-codebase/`
  (opencode also reads the `.claude/skills` locations)

## Use

Say what you want in plain words; the description triggers on requests like:
- "make this repo bulletproof / release-ready", "set up quality gates", "add mutation testing"
- "audit how strong our tests really are", "write invariants for this module"
- "stop agents from gaming the tests"
Or invoke explicitly, for example: "Use the agent-proof-codebase skill to audit this repo (mode B)."
For an open-ended run: "Use the agent-proof-codebase skill. Orient yourself, write your own plan, then build
the gates you judge most valuable. Decide for yourself; ask me only about product or irreversible decisions."

## Layout

```
SKILL.md          modes, conduct rules, workflows, quick reference
references/       13 deep-dive docs, read on demand
scripts/          9 gate building blocks (Python stdlib / bash), tested
assets/           templates: SPEC, GATES, PR template, CODEOWNERS, CI skeleton, gates.sh, AGENTS snippet...
```

No `gates.sh` is shipped: copy `assets/gates.sh.example` into the target repo as scripts/gates.sh
and adapt the TODO lines to the project's real tools.

## Notes

- Scripts need only Python 3 and git (plus bash for `fail_to_pass_check.sh`).
- Tool names in `references/13-ecosystem-map.md` change over time; verify maintenance before adopting.
- Protect the copied `scripts/`, `.gates/`, SPEC.md, GATES.md and CI files with CODEOWNERS, otherwise the gates are advisory.
- Released under the [MIT License](LICENSE).
