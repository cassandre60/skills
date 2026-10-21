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
- `model_failures` — `model timestamp` per quota-failed model (cooldown list).
- `last_nudge` — timestamp of the last nudge round (storm protection).
- `watchdog.env` — the `ORCH_*` variables, read by the systemd service via
  `EnvironmentFile=` at every start. Shell-profile exports never reach a
  systemd service; the installer writes this file (mode `0600`) if absent.
  This is where `ORCH_MODEL_LADDER` must live for the timer-launched
  watchdog to see it.

The nudge command is `ORCH_RESUME_CMD`, a `bash` snippet where `$1` is the
session id, `$2` the message and `$3` the model for this attempt (`""` = the
session's default model). It is run as
`bash -c "$ORCH_RESUME_CMD" _ "$SID" "$NUDGE_MSG" "$MODEL"` — never via `eval`
of interpolated user text.

**Default (opencode-specific):**

```sh
opencode run --auto -s "$1" ${3:+-m "$3"} "$2"
```

`-s` continues a specific session; `--auto` avoids a headless stall on the first
permission prompt. For another harness, read its docs for the headless
resume-session flags and set `ORCH_RESUME_CMD`; do not copy opencode flags.

### Model ladder

`ORCH_MODEL_LADDER` is a space-separated list of model ids, most-preferred
first. The watchdog walks it **within one tick**: a nudge whose output carries
a quota/balance signature (`rate limit`, `429`, `quota`, `insufficient
balance`, `balance is at $0`, `overloaded`) marks that model in
`model_failures` and immediately retries the next one, up to
`ORCH_NUDGE_MODELS` (default 3). A failed model is skipped for
`ORCH_MODEL_COOLDOWN` seconds (default 3600). `ORCH_MIN_INTERVAL` (default
600) is the minimum spacing between nudge rounds.

Set the ladder so that **every entry has quota independent of the others**,
and verify each is actually free before relying on it — a paid gateway with a
$0 balance fails like an exhausted pool, just slower. An empty ladder means
"session default only": the watchdog then cannot rotate, and a freeze on that
model will not lift on its own.

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

## Model rotation

A quota error is a routing decision, not a retry decision. The orchestrator
rotates the **model**, it never re-dispatches the one that just failed.

1. **Enumerate live models** (opencode: `tools.opencode.models`). A model
   listed as active may still be quota-exhausted — the list is a catalogue,
   not a health check. Probe cheaply before committing a task to a pool.
2. **Rotate on failure, not on schedule.** A worker that dies with "Rate limit
   exceeded", a "$0 balance" gateway, or a 429 is re-dispatched immediately on
   a different model. Prefer a different *provider* first (a different quota
   bucket), then a different model inside the same provider.
3. **Keep a dead-pool ledger** in the journal: model, provider, error, date.
   Never re-pick a model from that list until it is probed alive again.
4. **A "$0 balance" gateway is not a free pool.** A paid route with no credit
   fails exactly like an exhausted free quota, only slower. Keep such routes
   out of the ladder unless the owner funds them.
5. **The resume that revives a frozen session must name a live model.** If the
   session's default model is the one that died, the nudge is a no-op. The
   watchdog's ladder (`ORCH_MODEL_LADDER`) exists for this: it retries the
   next model within the same tick on a quota signature.
6. **When every pool is dry**, beats alone keep the loop alive and the
   snapshot accumulator keeps the state legible; do not spin workers against
   dead pools, and do not pretend the fleet is progressing.
7. **A dead pool is a fleet event, not a tab event.** Workers pinned to
   different models are a good way to spread load, and they turn one pool's
   exhaustion into a fleet-wide stop. So: **dispatch unpinned by default** — a
   subagent inherits the session's model, and the session's pool is the one you
   can see is alive. Pin only with a reason, and when a pinned pool dies,
   re-dispatch that task unpinned before you call the fleet idle.
8. **Never park a task because of a pool.** A task is either re-dispatched on
   a live model, or reported as blocked-with-reason. "No capacity" is true
   only when every pool is dead — and that is a statement for the owner, not
   a reason to go quiet. A wake that ends with zero dispatched work and no
   owner-facing explanation is a failed wake, even when every technical check
   passes.
9. **An exhausted target list is a signal to look wider, not to idle.** When
   the tracked targets are all audited, the next candidates come from the code
   that has no ledger row yet (the ledger is the registry of what has been
   covered, so "not in the ledger" is a work list). A finite catalogue is a
   starting point; treating it as the whole codebase is how a 24/7 fleet
   goes quiet while the repo still has hundreds of uncovered files.

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

A second `--user` timer, hourly, that needs no model tokens: run by
`assets/orchestrator-snapshot.service` and `assets/orchestrator-snapshot.timer`
(installed automatically alongside the watchdog by `assets/install-watchdog.sh`),
invoking `python3 scripts/autonomous/fleet.py snapshot`.

It appends to `snapshots.jsonl` and atomically updates `snapshot.json` with the
state the orchestrator must not lose to a freeze — current `main` sha, open PRs
with head branches and check states, and per-worktree own-commit and dirty counts.
While the orchestrator cannot read anything, the log keeps accruing; the recovery
wake replays it instead of rebuilding state from `gh`. Verified 2026-10-20:
snapshots caught a full merge wave while the orchestrator was frozen.

Before dispatching new work, the orchestrator runs:
```sh
python3 scripts/autonomous/fleet.py govern --json
```
which evaluates the kill switch (`.autonomous/STOP`), WIP caps (`fleet.max_open_prs`),
and acceptance floors. For fleet reporting, `fleet.py stats --days 14` computes
acceptance rates and renders the weekly owner digest.

## Sentinel review checklist (per worker PR)

0. Fresh fetch + premise recheck on current `origin/main` (see
   `07-moving-main.md`). A dead premise means close/supersede, not polish.
1. Checks are green **on the current head sha** — compare the sha the run
   reported with `gh pr view <n> --json headRefOid`. A green run for an older
   sha, or a head sha that never received a run, is not a verdict. A red check
   inside the diff is fixed and re-pushed (see "The CI green loop" in `04`),
   not waived.
2. Files touched match the ownership grant. No generated files, no referee edits
   without a gate-change class.
3. Evidence matches `04-workflow-and-roles.md`: red/green or survivor proof,
   full-file result, base sha.
4. PARTIAL is honest-but-incomplete: check the remaining count and re-dispatch.
   No partial ships with a broken build or weakened assertion.
5. For this repo's policies, footers/comments applied as required; PR left open.

Per-wake duty, on top of the checklist: every open PR carries a state line
(`PR# | head sha | checks | owner-ask pending? | iterations`). A red PR is
either being fixed, or it is an owner ask — and an owner ask is re-asked every
wake until it is answered.

## Failure anatomy and relaunch

When a fleet is cancelled (rate limits, harness restart):

0. **Forensics before reports.** A tab that returned an empty report may
   still be running: check for a live mutation engine, a moving file mtime,
   and dirty files in its worktree. A killed tab leaves its worktree in a
   state no report describes.
   - **A tab killed mid-mutation leaves the MUTANT APPLIED.** The engine
     restores only when it receives a signal it can act on. Before reusing,
     reporting on, or handing off any worktree, check
     `git diff origin/main -- lib` — it must be empty — and restore it
     otherwise. A mutant that reaches a PR is a production bug wearing a
     test's clothes.
   - **One engine per file.** Two tabs mutating the same file corrupt each
     other's measurements (uncompilable verdicts flip to survivors). Before
     re-dispatching a task whose previous attempt may be alive, confirm it
     is dead by forensics; if it is not, use a FRESH worktree and branch,
     and tell the new tab which worktree the old one owns.
   - **Stop the zombie explicitly.** A live tab keeps burning the machine
     and may open a duplicate PR. Resume its session with an explicit stop
     instruction (kill its processes, restore the worktree, do not push) —
   and verify the result yourself.

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
- [2026-10-20] A nudge that resumes the session on the **same model whose quota
  just died** is a no-op, and no amount of staleness depth fixes it: the
  watchdog's tier-1 "plain nudge" used the session default, so seven
  consecutive nudges all answered "Rate limit exceeded" and the session stayed
  frozen for ~8 h while another free model (minimax) was alive the whole time.
  The failure signature is a stronger wake signal than the heartbeat age —
  rotate the model the moment you see one. The ladder's second entry was a
  "free" gateway that answered "Your Token Harbor balance is at $0": a paid
  pool with no credit is not a free pool, and it must not sit in a ladder.
  Verified by: a fake-`opencode` harness where model A always answers
  "Rate limit exceeded" and model B succeeds — the nudge rotated to B in the
  same tick, A was marked failed, B was not.
