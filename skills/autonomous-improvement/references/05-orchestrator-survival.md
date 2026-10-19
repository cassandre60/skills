# Reference 05 — Orchestrator Survival (multi-day unattended operation)

How to keep an orchestrating agent alive for hours-to-days while the owner is away,
on rate-limited free models, without an external babysitter. Everything here was
learned on a real multi-day run that failed once in a specific, reproducible way
(all worker tabs cancelled by provider throttling; the orchestrator's next turn
died mid-housekeeping and ended with **zero pending background jobs**, so the
session sat frozen for hours). The defenses below are ordered so that each alone
would have recovered that failure.

## The one invariant

**Never end a turn with zero pending background jobs.**

A background job that completes wakes the agent even when the user is away — that
is the only self-wake mechanism a session has. Chained background `sleep`s are the
cheapest possible such jobs: they consume no model quota, so they keep firing while
throttled.

- Arm the wake chain **first**, before any housekeeping, in every turn. If the turn
  dies mid-housekeeping (quota cut, abort), the already-pending sleeps still fire.
- Use a **staggered grid** (e.g. 10 min + 30 min + 60 min), not one long sleep: any
  single loss is survivable, and after a quota window opens, the next catch-up is
  at most one beat away.
- On each wake, re-arm only what expired. Pending sleeps are free; firing is not.

## Turn budget (frugality under quota)

Every wake handler runs on the same limited model pool that workers use. A wall of
prose in a no-op wake can be the difference between surviving and being throttled.

- One batched shell call per wake (fetch state + nudges + re-arm in a single command).
- One-line prose when nothing changed. No re-reading large outputs on a schedule.
- Keep a journal file OUTSIDE the repo (e.g. `~/.local/share/<project>-oracle/journal.md`)
  so crash recovery is cheap — never reconstruct state from memory.

## External watchdog (out-of-session layer)

In-session beats cannot help if the session process itself dies or if ALL pending
beats get consumed during a single long turn. Repair both with a system-level
watchdog that runs without any model:

1. Every wake turn: `touch <state>/last_beat`.
2. A `systemd --user` timer (20 min) runs a script that:
   - reads `last_beat` mtime; if the opencode process is alive but the heartbeat is
     stale (>45 min), the session is frozen/throttled — inject a **headless nudge
     into the same session** with the CLI:
     `opencode run -s <sessionID> "watchdog nudge: run housekeeping per journal"` —
     verified against `opencode run --help` (`-s` continues a specific session);
   - if the process is dead, the same command revives the session directly.
3. Get your own session ID reliably via a no-op session op (e.g. rename the session
   to a stable title) and write it to `<state>/opencode_session_id`.

Serial signs of throttle-freeze that should trigger investigation: a tab's final
report never arrived, the PR you expected never opened, and the watchdog log shows
stale-heartbeat nudges. Do not reprompt mid-window; wait for the nudge to land.

Copy the working script + units from `assets/watchdog.sh`,
`assets/opencode-watchdog.service`, `assets/opencode-watchdog.timer`.

## The machine itself

Sleeping kills the whole loop. With owner approval:

```sh
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target  # on
sudo systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target # undo
```

Record the undo command the moment you mask. Never mask without owner consent.

## Worker pace expectations

On free models, a tab given one focused file to convert (~15–30 call sites), or a
sharded mutation run (5-7 shards via a fixed engine), takes **1.5-4 hours**. This
is normal. Do not "check in" on running tabs (there is no clean way; prompts sent
mid-flight queue behind the current turn).

- Health check = worktree forensics only: dirty files present (working), local
  commits appearing (checkpointing), nothing for >3h (suspect; see below).
- Orchestration sizing keeps the total turn budget dominated by wake handlers and
  sentinel reviews, not by polling.

## Failure anatomy and the relaunch protocol

When a fleet is cancelled (rate limits, harness restart):

1. Verify what survived: worktrees on disk (codegen setups are precious), your
   journal, open PRs/issues (dry `gh pr list`), ledger rows.
2. Never rerun codegen in an existing worktree — dispatch replacements with a
   resume preamble that checks `git status` / branch first.
3. Instruct replacement workers with the quota-aware protocol (Reference 06):
   progress checkpoints, PARTIAL PRs on repeated failure, scope ceilings.
4. First half of the relaunch turn: re-arm the beat grid. Then dispatch.

## Hard lesson ledger (verified)

- [2026-10-19] A turn that ends with zero pending wakeups, after a full worker
  fleet was cancelled, freezes the whole setup for hours. Verified by: the owner
  finding the session dead after ~several hours; watchdog + staggered grid added
  the same day; every subsequent freeze attempt recovered in ≤1 beat.
- [2026-10-19] `git checkout <ref> -- <path>` **stages** the copied file. A worker
  instructed to copy an uncommitted tool from another branch will stage that file
  without knowing it. Explicitly instruct `git restore --staged <path>` after such
  a copy and treat the artifact as never-committed. Verified by: the staged
  mutation_engine copy surviving until an explicit unstage.
- [2026-10-19] `opencode run -s <sessionID> "msg"` continues a specific session
  headless — the only verified injection point for a tooling-driven revival.
  Verified by: watchdog successfully nudging the orchestrator session.
