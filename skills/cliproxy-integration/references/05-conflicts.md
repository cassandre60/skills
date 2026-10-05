# 05 — Config conflicts, writers, and plugins

When a config is correct but keeps reverting, you have **two writers**. This file covers how
to identify them and settle ownership.

## Symptom: my edits keep coming back

```bash
ls -la --time-style=full-iso ~/.config/opencode/opencode.json*
```

Compare timestamps: if the file's mtime is *after* your edit, something rewrote it. Diff
against your backup to see exactly what survived:

```bash
diff <(jq -S . opencode.json.bak-<date>) <(jq -S . opencode.json)
```

Read the diff as evidence about the writer's behaviour: did it preserve your values for
*some* models but not others? Did it keep your top-level keys but replace one subtree? The
pattern identifies which code path wrote it.

## Who writes an OpenCode config

| Writer | Scope | Notes |
| --- | --- | --- |
| OpenCode itself (TUI, config-update command) | whole file, preserving the rest | writes to the preferred filename |
| A **plugin** | whatever it decides | read its source; some register providers, few write config |
| A **management panel** / onboarding wizard | usually one provider subtree | the "connect my gateway" flow is a common culprit |
| A **sync script** | whatever it targets | check for a system-wide `*.path` unit watching the file |

Narrow it by elimination: rename the file and see what recreates it, disable the plugin, or
watch it:

```bash
sudo inotifywait -m ~/.config/opencode/opencode.json     # if available
# or
stat -c '%y %n' ~/.config/opencode/opencode.json; sleep 30; stat -c '%y %n' ~/.config/opencode/opencode.json
```

Then re-run your edit and watch which mtime moves first.

## Give each key exactly one owner

The durable fix is not "edit more carefully" — it's making the file single-writer.

1. **Pick one tool per subtree.** If a panel manages your gateway provider, let it. Put your
   manual work in a subtree it doesn't touch.
2. **Make your post-edit step re-runnable.** A repair script beats a hand patch because it can
   be re-applied after the other writer runs. Make it idempotent and touch only its own keys.
3. **Automate the reconcile, not the edit.** A lightweight path-triggered unit that re-applies
   your values is often the honest answer when a panel rewrite is unavoidable — but weigh it
   against the complexity. A cron/timer that fires when nothing changed is just noise; if it
   only ever fixes a rare event, a manual re-run command is better.
4. **Change the workflow instead, when possible.** If a GUI rewrites the block, stop using the
   GUI for that block. Script the equivalent edit.
5. **Expect a panel save to reformat the file.** A management-panel save can rewrite a whole
   config from block style to single-line flow style. The content is equivalent, but every
   subsequent hand-edit must match the new style exactly — one stale anchor string and the
   edit silently misses (or worse, a brace gets eaten and the service refuses to start).
   After any panel save, re-read the region before editing it.

## A note on auto-discovery plugins

Plugins that mirror a gateway's catalog into the model picker are convenient, and they
introduce a second source of truth alongside any manual config:

- they register their **own provider ID**, so they coexist with a manual block rather than
  replacing it — you then maintain two overlapping sets;
- they may rename models from public metadata, so the picker shows marketing names
  ("Nano Banana") instead of upstream IDs — cosmetic, but it makes auditing harder;
- they generally do **not** write your config file, so they are rarely the reverting writer.

Read the source before adopting one. Small plugins are short enough to verify, and the
questions worth answering are: what provider ID does it claim, does it read existing settings,
and does it write anything to disk?

**When to drop one:** if you need deterministic IDs, source-attributed naming, or limits you
control, a hand-maintained block beats a plugin that guesses. If you just want "everything the
gateway serves, automatically", the plugin is the right trade.