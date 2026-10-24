# scripts/autonomous

Copy this **whole folder** into the target repo at `<repo>/scripts/autonomous/`.
The scripts import `common.py`, so copying a single file out of the folder will
not work.

```sh
cp -r scripts/autonomous <repo>/scripts/autonomous
cp <repo>/scripts/autonomous/config.example.json <repo>/scripts/autonomous/config.json
```

Then edit `config.json`. Every project-specific command is an argv array. A
missing key is skipped with `[skip] <step> not configured`; there are no hidden
defaults for test runners, remotes, or worktree names.

Files:

| File | Purpose |
|---|---|
| `evidence.py` | `mutant` / `defect` / `check`; fail-to-pass evidence artifact (`.autonomous/evidence.json`) |
| `fleet.py` | `snapshot` / `pr-state` / `govern` / `stats` / `claim` / `publish`; state spine, governor, PR transport |
| `stream_b_scan.py` | Stream B mechanical pre-filter (vacuous/sleep/mock smells) |
| `simplify_gate.py` | Stream J dead-code `finder`, mutation-report `compare`, and full base-vs-current `oracle` |
| `ledger.py` | `next` / `record` / `check` / `sync-rejections`; content-hash dedup |
| `mutants.py` | Per-mutant triage ledger: `ingest` / `record` / `open` / `check` |
| `mutation_engine.py` | Per-line mutation runner with language rule packs |
| `mutation_rules/*.json` | Python, Dart, TypeScript, JavaScript, Go |
| `orchestrator.py` | Registered worktrees + configured gate sequence |
| `test_weakening_check.py` | Local tripwire: removed assertions/skips/deleted tests |
| `common.py` | Config loading, repo-root resolution, table/argv helpers |
| `config.example.json` | Python starting config |

Requirements: Python >= 3.9, standard library only; `git`; optional `gh`.
