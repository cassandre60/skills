# Autonomous Work Ledger

> Inspect before picking a target. Deduplication is on content hash
> (`git hash-object` / `git rev-parse HEAD:<file>`), not commit identity.
> `git log -- <file>` is the last commit touching the file — a revert or
> cherry-pick can give the same commit hash for different content.

| Date | Stream | Target File / Module | Content Hash | Status | PR / Issue | Summary / Outcome | Re-Audit Trigger |
|---|---|---|---|---|---|---|---|
| 2026-10-18 | Stream A (Mutation) | `src/example/service.ts` | `abc123def` | COMPLETED | PR #1 | Example row — delete this | Any modification to `service.ts` |

## Target Candidates (optional index)

List files/modules that are eligible for streams A/B. Keep this list derived
from the project's real plan/backlog — don't let it become a second backlog.
