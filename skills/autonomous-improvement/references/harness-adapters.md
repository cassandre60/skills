# Harness Adapters

The skill is harness-neutral; a harness is a set of commands. This table is
the one place that names them. A row is **verified** only when its commands
were actually exercised against the live harness (the verification is cited);
everything else is a starting point to confirm from the harness' own docs —
**never treat an unverified row as fact**.

The watchdog and the wake launcher read every touchpoint from the
environment, so adapting a harness means setting variables — never editing
`assets/watchdog.sh`:

| Touchpoint | Where | Contract |
|---|---|---|
| headless resume | `ORCH_RESUME_CMD` | `bash -c <cmd> _ <session-id> <nudge-msg> <model>` (`$3` empty = session default) |
| one-shot wake | `ORCH_WAKE_CMD` / `fleet.harness_cmd` | argv with `{prompt}`/`{prompt_file}`/`{model}` substitution; the prompt is appended as the final positional argument when no placeholder appears |
| session-id file | `ORCH_SESSION_ID_FILE` | filename inside the state dir (default `opencode_session_id`) |
| read session model | `ORCH_SESSION_GET_CMD` | `bash -c <cmd> _ <session-id>`; stdout JSON whose objects may carry `model.id`/`model.providerID` |
| switch session model | `ORCH_SESSION_SET_CMD` | `bash -c <cmd> _ <session-id> <provider/id>`; exit 0 = switched |
| model ladder / allowlist | `ORCH_MODEL_LADDER`, `ORCH_AUTHORIZED_PROVIDERS`, `fleet.model_ladder` | `provider/model` ids; every entry needs independent quota and the owner's approval |

## opencode — verified

The defaults implement this row; it is the harness the unattended stack was
built and verified on.

- headless resume: `opencode run -s "$1" ${3:+-m "$3"} "$2"`
- one-shot wake: `opencode run ${2:+-m "$2"} "$1"`
- headless approval: `--auto` ("Auto-approve permissions that are not
  explicitly denied") is a **top-level** flag on current builds —
  `opencode --auto run …` — NOT a `run` flag; placed after `run` it is
  swallowed and the positional message never arrives (`Error: You must
  provide a message` — that log line is the signature of a flag/message
  mismatch). The portable default is positional without `--auto`; add the
  global form only if unattended permission approval is wanted.
- session model read: `opencode api get "/api/session/$1"`
- session model switch: `opencode api post "/api/session/$1/model" -d '{"model":{"id":"…","providerID":"…"}}'`
  (204 No Content on success; endpoint documented as "Switch the model used by
  subsequent provider turns")
- session id: a no-op `session_rename` returns it (write it to the session-id
  file the watchdog reads)
- model enumeration: `tools.opencode.models` — a listed model may still be
  quota-exhausted; the list is a catalogue, not a health check
- worker dispatch: in-run subagent sessions
- notes: an opencode server restart preserves sessions and background
  subagents but kills background sleep beats — after a restart, re-arm the
  beat grid first (verified twice, 2026-10-19/20). Free-model pooling and
  `tools.opencode.models` are opencode features.

## Claude Code — unverified

Community-documented, not exercised here. Confirm every flag before relying
on it: headless `claude -p [--output-format json] [--max-turns N]`,
`--resume <session-id>` to continue a session. Assume separate headless runs
for workers (no in-run subagent dispatch from the fleet). Until the
resume/switch commands are verified, run Claude Code rows in the interactive
single-session mode only.

## Codex — unverified

`codex exec` for headless one-shot prompts. Same status as Claude Code:
assume separate headless runs for workers; verify resume/session-model
equivalents from the CLI's own docs before using the fallback cold start.

## ZCode — interactive only (no verified headless resume)

Verified in interactive use only (2026-10-24): the toolbox runs inside the
session's own turns; worktrees, PRs, and the per-task protocol all work.
No headless resume command is verified here, so **do not** install the
watchdog or wake grid for it — a nudge that cannot be delivered is a silent
freeze. Run it in single-session mode (`session.environment: laptop`), and
note the desktop AppImage harness poisons `sys.executable` inside its shell
(read the host repo's AGENTS.md for the `exec -a` workaround when running
Python test suites).

## Adding a row

A row graduates from unverified to verified by exercising, against the live
harness: (1) the one-shot wake, (2) the resume command with a model argument,
(3) the session-model read/switch pair, and (4) one watchdog rotation test
with that harness' fake CLI — then recording the date here. Remember the
owner-scope gate: a ladder entry whose provider is not in
`ORCH_AUTHORIZED_PROVIDERS` is refused before any command runs, so the
allowlist is part of the adapter, not an afterthought. Anything less stays
unverified.
