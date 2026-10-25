# autonomous-improvement

Spend agent tokens on mechanically falsifiable work: mutation testing that
finds unprotected behavior, and adversarial audits that find tests which cannot
fail. The entry point is [`SKILL.md`](SKILL.md); references are loaded on demand.

## Requirements

- `git`, `python3` >= 3.9 (standard library only).
- `gh` is optional and only used to detect open PRs; without it `next` prints a
  warning and continues. `--require-gh` makes that fatal.
- The unattended watchdog needs Linux, `bash`, `timeout`, `flock`, and systemd
  user units. It is optional and harness-specific (defaults to opencode).

## Install

This folder is a skill for opencode, Claude Code, and agents that read
`SKILL.md`-style skill directories. Copy it into your harness's skills directory,
for example:

```sh
cp -r autonomous-improvement ~/.config/opencode/skills/autonomous-improvement
```

To use it inside a target repo, run the scripts from the skill folder — no
copying into the repo (the repo root comes from git in your cwd):

```sh
python3 <skill>/scripts/autonomous/onboard.py detect
python3 <skill>/scripts/autonomous/onboard.py write    # <repo>/.autonomous/config.json
python3 <skill>/scripts/autonomous/onboard.py verify   # baseline test run once
mkdir -p <repo>/docs
cp <skill>/assets/AUTONOMOUS_WORK_LEDGER.template.md <repo>/docs/AUTONOMOUS_WORK_LEDGER.md
```

Config resolution order: `--config`, the `AUTONOMOUS_CONFIG` env var,
`<repo>/.autonomous/config.json`, then the legacy
`<repo>/scripts/autonomous/config.json` copy (still supported). Every
project-specific command lives in the config as an argv array; no
project-specific test runner, remote, or path is hardcoded. Then follow the
Quick Start in `SKILL.md`.

## What is in the folder

```
SKILL.md              laws, Streams A/B, quick start, references
references/           deep dives (engine, ledger, workflow, gates, examples)
scripts/autonomous/   ledger.py, mutation_engine.py, orchestrator.py,
                      test_weakening_check.py, mutation_rules/*.json, common.py
assets/               ledger template, gitattributes/protected-path fragments,
                      optional systemd watchdog + installer
examples/             ready-to-copy Dart and Python configs
tests/                stdlib unittest suite (no network or toolchains)
evals/                behavioral eval cases for an agent using the skill
docs/history/         design records kept as history (not skill instructions)
```

## Verify

```sh
python3 -m unittest discover -s tests -v
```

The suite creates temporary git repositories; it needs only `git` and
`python3`. Watchdog tests are skipped when `bash`, `flock`, or `timeout` is
absent. The systemd units themselves are not exercised in the suite (no systemd
in CI containers); the watchdog shell path is tested when those tools exist.

Released under the [MIT License](LICENSE).
