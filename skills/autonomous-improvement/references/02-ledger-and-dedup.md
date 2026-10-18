# Ledger and Deduplication

## Schema

```markdown
| Date | Stream | Target File / Module | Content Hash | Status | PR / Issue | Summary / Outcome | Re-Audit Trigger |
|---|---|---|---|---|---|---|---|
```

- `Content Hash` is `git hash-object <file>` (or `git rev-parse HEAD:<file>`) truncated to 9 chars — the hash of the file's bytes, not commit identity.
- `Status` is `COMPLETED` (audited, gaps fixed or documented), `SOUND` (audited, no gaps), or `IN_PROGRESS` (claimed, not yet merged).
- `Target File / Module` is the source file or logical module.

Do not use `git log -- <file>` (last commit touching the file). A revert or cherry-pick can give the same commit hash for different content, or different hashes for identical content.

## CLI

```bash
python3 scripts/autonomous/ledger.py next --stream 1      # find next unaudited target
python3 scripts/autonomous/ledger.py record \
  --stream "Stream A (Mutation)" \
  --target "src/domain/coordinator.ts" \
  --status "COMPLETED" \
  --pr "#42" \
  --summary "27 mutants: 20 killed, 3 invalid, 2 gaps fixed"
```

`next` prints the first target whose content hash differs from the ledger (or is absent), skipping anything with an open PR. `record` inserts a row after the table header.

## Concurrency

Two agents appending to one markdown table will conflict by construction.

- Before recording: `gh pr list` — skip if an open PR already touches the target.
- On conflict at push: `git pull --rebase origin/main` and retry. Don't overwrite the other row.
- Re-check `gh pr list` immediately before pushing — another agent may have opened a PR between your `next` and your push.

For high-contention repos, consider a JSON-lines ledger or per-file ledger files. The markdown table is the default because it's readable in review.

## Migration from commit-hash ledgers

Old rows that stored `git log` hashes remain valid as "last audited commit" markers. New rows must use content hashes. Re-audit is triggered by content change, not commit identity — so a content-hash ledger is stricter and never weaker.
