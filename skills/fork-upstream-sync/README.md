# fork-upstream-sync

Maintain continuous, high-signal alignment with an upstream parent repository without
re-reading historical diffs or burning LLM tokens on deleted files.

## What it solves

- **Eliminates state amnesia**: Tracks every upstream commit in a machine-readable negative ledger (`.upstream/ledger.json`). Decisions to skip cut features or custom presentation are recorded once and never re-audited.
- **Zero-token mechanical filtering**: Skips cut subsystems, deleted files, and chore commits using Python standard library (0 LLM tokens, 20ms execution time).
- **Automated weekly alerts**: Provides a GitHub Actions workflow that automatically opens or updates an issue whenever actionable candidate PRs appear upstream.

## Installation

```sh
npx skills@latest add cassandre60/skills
```

Or copy the `fork-upstream-sync` folder into your agent's skills directory (`~/.config/opencode/skills/`, `.agents/skills/`, or `.claude/skills/`).

## Quick Start

In any git fork repository:

1. Create `.upstream/config.json`:
   ```json
   {
     "upstream_repo": "owner/parent-repo",
     "upstream_branch": "main",
     "base_sha": "abc123456",
     "cut_paths": ["src/legacy", "src/unused"],
     "chore_subjects": ["^Bump version", "translations"]
   }
   ```
2. Copy `scripts/tracker_template.py` to `.upstream/tracker.py` and run:
   ```bash
   python3 .upstream/tracker.py sync
   ```
3. View candidates:
   ```bash
   python3 .upstream/tracker.py candidates
   ```

See [SKILL.md](./SKILL.md) for full instructions, workflows, and porting recipes.

## Licence

MIT
