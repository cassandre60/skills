# Unattended Operation (advanced)

This reference is for multi-day, owner-away fleets and for every session
running as the Orchestrator (`04`) — its cold start lives here. A single
session doing an explicit Stream A/B task does not need it. Harness sections
default to opencode + systemd on Linux; adapt or ignore for another harness.

## Cold start (skill-only boot)

A session whose only instruction is this skill starts its own operation
before anything else. First turn, in order:

1. **Find the state.** Look for the durable state dir outside the repo
   (`~/.local/share/<project>-oracle/`, or the name a prior run deployed).
   It should hold a journal, a session-id file and — when the snapshot
   timer runs — a snapshots log. A missing dir means first deployment:
   create it with the same naming pattern. The journal is the fleet's
   memory; do not skip it.
2. **Learn your own session id** from the harness and write it into the
   session-id file the watchdog reads (opencode: a no-op
   `session_rename` returns it). Until this is current, every revive
   nudge knocks a dead session.
3. **Arm the wake grid** as backgrounded sleeps FIRST — beats cost no
   tokens and are the only self-wake that needs no helper.
4. **Watchdog.** If one is deployed but stopped, start it after step 2 —
   it reads the same session-id file. Otherwise install from
   `assets/install-watchdog.sh`. If neither works, degrade to
   beats-only and record that in the journal: beats alone keep the loop
   alive; the watchdog is armor, not a requirement.
5. **Read the journal tail** (and the snapshots log after any gap):
   ledger status, open PRs, in-flight tabs supersede session memory —
   right after a restart this file is the fastest reliable read.

Only then derive the queue (`ledger.py next` + open PRs) and dispatch.

## The one invariant

**Never end a turn with zero pending wakeups.** A background job that completes
wakes the agent even when the user is away; it is the only self-wake mechanism a
session has.

- Arm the wake chain **first**, before housekeeping, in every turn. If the turn
  dies mid-housekeeping, the pending sleeps still fire.
- Use a staggered grid (for example 10, 30, and 60 minutes), not one long sleep.
- On each wake, re-arm only what expired. Pending sleeps cost no model quota.
- Keep a journal **outside the repo** (for example
  `~/.local/share/<project>-oracle/journal.md`) so recovery does not depend on
  reconstructing state from memory.
- One batched shell call per wake; one line of prose when nothing changed.
- Stretch the grid when free-pool pressure is high (for example 30, 60, 120
  minutes): completed sleeps are free, but every wake turn spends tokens
  whether or not the pool has any left.

This invariant cannot save a dead session process. The external watchdog below
covers that case. It also cannot save a session whose **model pool is dry** —
that third failure mode is next.

## Freeze anatomy: token starvation is a third mode

A pending sleep needs no model tokens, but **waking because it fired does**.
Every wake channel — a completed beat, a watchdog nudge, a queued user
message — spends the session's own model pool, so a dry pool freezes the
session while the process stays alive and "healthy":

- pending beats keep firing; each wake turn dies silently on quota;
- nudges queue as user messages; none of them conjures tokens;
- when the pool returns, every queued nudge is delivered at once. Verified
  2026-10-20: a ~9 h freeze produced a 7x burst of identical nudges.

Two counters keep one frozen window cheap:

1. **Knock rate-limit.** A `last_nudge` marker: exit while the previous nudge
   is younger than ~35 min. `flock` prevents concurrent runs, not stacked
   queued ones. One frozen window delivers one queued nudge.
2. **Tier ladder** through `ORCH_RESUME_CMD`, escalating by staleness depth:
   the session's own model recovers transient freezes; after ~1h45m stale,
   retry on a **different free pool** (`-m <provider/model>` — a different
   quota bucket, not the same one renamed); beyond that, a cheap paid model
   only with explicit owner consent. A tier run that fails logs and retries
   on the next timer tick.

## External watchdog: one code path

`assets/watchdog.sh`:

1. Takes a non-blocking `flock`; if a previous run is active, exits.
2. Reads `last_beat`; if it is fresh (`ORCH_HB_MAX_AGE`, default 45 min), exits.
3. Otherwise runs **one foreground nudge** into the same session, bounded by
   `timeout --signal=TERM --kill-after=60 "$ORCH_NUDGE_TIMEOUT"`.
4. Logs the exit status (`124` = timed out) and exits 0.

There is no `pgrep`, no background branch, and no `setsid`. V1's dead-process
branch launched `setsid nohup ... &` from a `Type=oneshot` unit; systemd tracks
the cgroup, not the session, so the child was killed when the oneshot exited.
**Verified on 2026-10-19** with a throwaway `Type=oneshot` unit on systemd 262:
the background child was gone one second after the unit stopped. The single
foreground path sidesteps the cgroup problem entirely.

### Configuration

`ORCH_STATE_DIR` is the single configuration point (default
`~/.local/share/orchestrator`):

- `last_beat` — touched by every orchestrator wake turn.
- `opencode_session_id` — the session to nudge.
- `watchdog.log` — append-only log.
- `watchdog.lock` — flock file.

The nudge command is `ORCH_RESUME_CMD`, a `bash` snippet where `$1` is the
session id and `$2` the message. It is run as
`bash -c "$ORCH_RESUME_CMD" _ "$SID" "$NUDGE_MSG"` — never via `eval` of
interpolated user text.

**Default (opencode-specific):**

```sh
opencode run --auto -s "$1" "$2"
```

`-s` continues a specific session; `--auto` avoids a headless stall on the first
permission prompt. For another harness, read its docs for the headless
resume-session flags and set `ORCH_RESUME_CMD`; do not copy opencode flags.

### Install / uninstall

```sh
./assets/install-watchdog.sh
./assets/install-watchdog.sh --uninstall
```

The installer is idempotent: copies `watchdog.sh` to
`~/.local/share/orchestrator/`, copies
`orchestrator-watchdog.{service,timer}` to `~/.config/systemd/user/`, runs
`daemon-reload`, `systemd-analyze --user verify`, and
`systemctl --user enable --now orchestrator-watchdog.timer`. It prints (does not
run) `sudo loginctl enable-linger "$USER"` if linger is off.

Manual equivalents if you prefer:

```sh
install -m 0755 assets/watchdog.sh ~/.local/share/orchestrator/watchdog.sh
install -m 0644 assets/orchestrator-watchdog.service ~/.config/systemd/user/
install -m 0644 assets/orchestrator-watchdog.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now orchestrator-watchdog.timer
loginctl enable-linger "$USER"          # user timers while logged out
systemctl --user list-timers            # verify
journalctl --user -u orchestrator-watchdog.service
```

Uninstall stops/disables the timer, removes the units and script, and leaves the
state directory alone.

The unit sets `TimeoutStartSec=2h` and `KillMode=process` as backstops. The
script's own `timeout` is the primary bound; a hung nudge cannot block every
future timer run.

### Assumption

A headless `<resume-cmd>` turn exits when the turn ends. If your harness leaves a
long-lived process instead, launch the nudge in its own cgroup:

```sh
systemd-run --user --collect --unit="orch-revive-$(date +%s)" <cmd>
```

or set `KillMode=process` on the unit (already set here).

## Blast radius for unattended `--auto`

`--auto` approval is a high-trust mode. Prompt-level rules ("never merge") are
not a security boundary.

- Use a fine-grained token limited to the repos, PRs, and issues the fleet
  needs; no merge, no admin, no org settings.
- Enforce required review and required CI through branch protection, so a
  misbehaving agent **cannot** merge even if it tries.
- Keep secrets out of worktrees. Do not copy `.env`, cloud credentials, or SSH
  keys into a worktree an agent can read.
- Prefer a dedicated OS user for the unattended session. The watchdog state dir
  and journal should be writable only by that user.
- Audit through the journal, the watchdog log, and `gh pr list`/CI. Assume any
  unattended run may need a human merge queue.
- Do not mask sleep/suspend targets without explicit owner consent, and record
  the exact undo command the moment you do it.

## Harness-specific notes (opencode)

- Get the session id from a no-op session operation (for example renaming the
  session to a stable title) and write it to
  `<state>/opencode_session_id`.
- `opencode run --auto -s <sessionID> "<message>"` continues a specific session
  headlessly. It is the only verified injection point here.
- Free-model pooling and `tools.opencode.models` are opencode features. Another
  harness needs its own model enumeration.
- An opencode server restart preserves sessions and background subagents but
  kills background sleep beats. First action after a restart: re-arm the beat
  grid, then continue. Verified twice (2026-10-19, 2026-10-20).
- Attribution footers are a **policy convention**, not a skill requirement. If a
  repo asks for them, append them to PR/issue **bodies** only, never titles or
  repo files.

## Quota-aware worker protocol

Put this in every dispatch prompt:

1. **Checkpoint with local commits.** After each unit (one mutation shard, one
   batch of conversion sites, one audit row), stage by name and commit locally.
   A cancelled tab leaves a resumable base.
2. **Scope ceiling.** Give each tab an explicit cap ("first ~15 of ~30 sites,
   then stop and report").
3. **PARTIAL beats death.** After 2–3 consecutive quota failures, finish the
   current unit, commit the valid prefix, push, and open a PR marked PARTIAL
   with the remaining count. Never silently die.
4. **Report quota episodes** so the orchestrator can rotate models.

On free models, a focused file conversion or a mutation shard can take 1.5–4
hours. That is normal. Health-check by worktree forensics (dirty files, local
commits, no progress for >3h), never by prompting a tab mid-flight.

## Fleet coordination

- Keep a live **file-ownership map**: tab → owned paths. Two tabs never own the
  same file. Schedule later waves "after #X merges" rather than racing.
- Re-check `gh pr list` immediately before pushing. If another PR touches the
  file, stop and comment instead of racing.
- Treat the queue as **derived state**, re-derived at every dispatch. Never treat
  dispatch memory as ownership.
- On a shared repo, other fleets may use different branch names and rewrite the
  ledger wholesale. Read their file list and diff before commenting; do not
  rebase, delete, or rewrite their rows. Flag merge-order expectations for the
  owner.
- No attribution footers on other fleets' PRs; they are not your artifacts.

## Snapshot accumulator (no-model evidence)

A second `--user` timer, hourly, that needs no model tokens: append to an
append-only log (`snapshots.log`) the state the orchestrator must not lose to
a freeze — current `main` sha, open PRs with head branches, per-worktree
own-commit and dirty counts. While the orchestrator cannot read anything,
the log keeps accruing; the recovery wake replays it instead of rebuilding
state from `gh`. Verified 2026-10-20: snapshots caught a full merge wave
while the orchestrator was frozen.

## Sentinel review checklist (per worker PR)

0. Fresh fetch + premise recheck on current `origin/main` (see
   `07-moving-main.md`). A dead premise means close/supersede, not polish.
1. Files touched match the ownership grant. No generated files, no referee edits
   without a gate-change class.
2. Evidence matches `04-workflow-and-roles.md`: red/green or survivor proof,
   full-file result, base sha.
3. PARTIAL is honest-but-incomplete: check the remaining count and re-dispatch.
   No partial ships with a broken build or weakened assertion.
4. For this repo's policies, footers/comments applied as required; PR left open.

## Failure anatomy and relaunch

When a fleet is cancelled (rate limits, harness restart):

1. Inventory what survived: worktrees, journal, open PRs/issues, ledger rows.
2. Never rerun codegen in an existing worktree. Check `git status`/branch first.
3. Re-arm the wake grid in the first half of the relaunch turn, **then** dispatch.
4. Dispatch replacements with the quota-aware protocol above.

## Hard lessons (verified)

- [2026-10-19] A `Type=oneshot` systemd unit kills `setsid nohup ... &`
  children when the unit stops; systemd tracks cgroups, not sessions. Verified by
  the throwaway-unit experiment above. Use one foreground path.
- [2026-10-19] `git checkout <ref> -- <path>` **stages** the copied file. After
  copying a tool from another branch, `git restore --staged <path>` and treat it
  as never-committed. Verified by a staged mutation-engine copy surviving until
  an explicit unstage.
- [2026-10-19] `opencode run --auto -s <sessionID> "msg"` continues a specific
  session headlessly. Verified by a successful watchdog nudge.
- [2026-10-20] Free-pool exhaustion freezes the session with the process alive
  and all wake channels funneling through the same empty pool; queued nudges
  flush only when the pool returns (7x identical burst after a ~9 h freeze).
  Knock rate-limiting plus a tiered `-m` cross-pool fallback bound one window
  to one queued nudge and let the first open window serve the wake.
