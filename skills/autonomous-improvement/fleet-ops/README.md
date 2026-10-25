# fleet-ops/ — elective keep-alive machinery

Everything in this folder is the **elective** keep-alive stack: the watchdog,
the stateless-wake launcher, the systemd units, and their reference manuals.
It runs only on a `vm`/`ci` environment or after an explicit owner ask —
**never** as a laptop default. The improvement engine (passes, ledger,
streams) lives in the skill root; the entry point is [`../SKILL.md`](../SKILL.md),
whose "Unattended operation (elective)" section is the single pointer here.

- `fleet-ops/references/05-unattended-operation.md` — the manual: heartbeats, the
  watchdog's model ladder, fleet coordination, the improvement cadence.
- `fleet-ops/references/harness-adapters.md` — per-harness command table.
- `assets/` — `watchdog.sh`, `stateless-wake.sh`, `wake.sh`, `snapshot.sh`,
  `install-watchdog.sh`, and the `orchestrator-*.{service,timer}` units.
- `tests/` — the behavioural shell tests for the watchdog rotation and the
  stateless wake (`bash fleet-ops/tests/<name>.sh`).
