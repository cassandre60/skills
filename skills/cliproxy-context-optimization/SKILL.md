---
name: cliproxy-context-optimization
version: 2.0.0
description: Find CLIProxyAPI request logs, verify which logging switch is actually enabled, render a captured /v1/chat/completions exchange as a readable Markdown transcript, full system prompt, and raw payload reference without altering values, and then measure where those per-turn context tokens go and how to cut them — skill-catalog pruning, global baseline compression, latency levers from thinking level and server timing, and a stable-prefix caching check. Use when someone asks where CLIProxyAPI logs are, why logging-to-file is not capturing request bodies, how to read a v1-chat-completions or error-v1-chat-completions capture, how to extract a system prompt or model reply from proxy logs, how to diagnose request-log, auth-dir, and management log settings, why a trivial prompt costs 15K tokens or 40 seconds, which skills or instructions dominate the context, or how to trim agent context and prove the saving with a fresh capture.
---

# CLIProxyAPI context optimization

Two jobs, one chain: first turn a CLIProxyAPI request capture into documents a
human can read, then use those documents to reduce the agent's fixed per-turn
context cost — with every claim measured, shown as a before/after, and
re-measured fresh.

**The original `.log` remains the authority.** Presentation changes (JSON
indentation, Markdown headings, quoted reasoning blocks) never alter values.
The renderer re-parses every rendered JSON body, checks every stream payload,
and verifies the thinking and reply text before reporting success.

## Non-negotiable rules

1. **`logging-to-file` is not request logging.** It writes application logs to
   rotating files. Request and response bodies require `request-log`. Enabling
   one does nothing for the other.
2. **Do not assume the config layout.** CLIProxyAPI 8.0.27 nests these keys under
   `observability.logs`; older documentation and panels may show top-level keys.
   Read the active file and find the uncommented key. See
   [the logging reference](references/01-request-logging.md).
3. **Logs live under `auth-dir`, not automatically under `~/.cli-proxy-api`.**
   Resolve `auth-dir` first, then use `<auth-dir>/logs`.
4. **`error-*.log` is not proof that request capture is on.** Error logs are
   retained even when request logging is disabled. Successful captures are
   `v1-*.log`; their presence can be historical.
5. **The management endpoint is not a body dump.** `GET /v0/management/logs`
   returns application log lines, never request or response bodies.
6. **Never repair a truncated capture.** CLIProxyAPI can truncate long upstream
   errors in the log. Preserve the visible text; do not fill in the missing JSON
   or claim the full error is known.
7. **Measure before trimming; verify after trimming.** Character counts from a
   capture are measurements. Token totals are model-reported facts. The
   chars-per-token conversion is an estimate and is labelled as one. Savings
   from a change are only proven by a fresh successful capture — an error
   capture carries no usage and cannot prove anything.
8. **Optimize only what the machine owns.** The skill catalog, agent baseline
   files, and model/thinking selection are owner-addressable. The harness body,
   tool declarations, and Code Mode catalog change with releases, not config.
   Do not report them as savings you caused.
9. **Keep prefixes stable.** When trimming instruction text, do not churn
   section order and do not move the date/env block earlier; the cacheable
   prefix is the leading, byte-stable part of the prompt.

## Workflow

### 1. Establish whether capture is enabled

Run the finder from this skill's directory:

```sh
python3 scripts/find_request_logs.py
```

It reads the local config, resolves `auth-dir`, lists request captures and error
logs, and reports the mismatch that matters: request logging off while captures
or error logs exist. Add `--json` for a machine-readable report, or
`--config PATH` and `--logs-dir PATH` when the process uses non-default paths.

If the finder says `request-log` is false, stop and fix that before looking for a
new capture. Editing a comment or enabling `logging-to-file` does not help.

### 2. Pick one capture

A successful capture looks like:

```
v1-chat-completions-2026-10-25T233717-a2056a8e.log
```

A failure capture looks like:

```
error-v1-chat-completions-2026-10-26T001413-4d6e2ba9.log
```

Both are supported. Prefer the newest capture whose timestamp and model match
the question. One log is one request, not a session replay; do not concatenate
unrelated captures without saying so.

### 3. Render the bundle

```sh
python3 scripts/render_request_log.py CAPTURE.log --output-dir READABLE_DIR
```

This writes the transcript (`<id>.md`), the system prompt document
(`<id>-system-prompt.md`), and the raw reference (`<id>-raw-payloads.md`).
Existing outputs are not overwritten without `--force`.

Exit codes: `0` verified success, `2` usage or output conflict, `3` unsupported
capture, `4` verification failure. A non-zero exit means do not present the
bundle as faithful.

### 4. Read the right document

- Start with the transcript for the human answer.
- Open the system prompt document when the question is about instructions,
  context, or why the model behaved a certain way.
- Open the raw reference when exact bytes, headers, upstream attempts, usage, or
  an error body matter.
- State which document a quotation came from. Do not merge the system prompt
  into the transcript and call the result one log.

### 5. Verify before quoting

The renderer already checks:

- every rendered JSON body re-parses to the same value as the capture;
- every stream payload appears in the raw reference;
- every system/developer message appears verbatim in the system-prompt document;
- the transcript's thinking and reply are exact renderings of the captured text.

If the renderer rejects the capture, inspect the original log. Do not switch to
an ad-hoc parser that skips the failed section.

### 6. Measure the context budget

```sh
python3 scripts/analyze_context_budget.py READABLE_DIR
```

It points at the newest rendered bundle in the directory (or pass
`--system-prompt` / `--transcript` explicitly, `--json` for machine output).
The report is a per-section budget with character counts (authoritative) and
estimated tokens (at a configurable chars-per-token ratio, default 3.85):
harness and identity, Code Mode catalog, skills catalog, agent baseline files,
date/env tail, plus the skill-position ranking within the catalog, the largest
descriptions, pair-similarity flags for possible duplicates, and the captured
usage facts (prompt/completion/reasoning tokens, body bytes, tool count,
thinking level, server timing).

The big sections have known owners:

| Section | Owner | Typical share |
| --- | --- | ---: |
| skills catalog | the machine's skill directories | 25-50% |
| agent baseline files | the agent's own AGENTS.md files | 15-30% |
| Code Mode catalog + tool declarations | the harness | 15-25% |
| harness and identity, date/env | the harness | <5% |

### 7. Choose and apply lever, one at a time

Work the largest owner-addressable section first. Proven plays (see
[the context-budget reference](references/03-context-budget.md) for full
method and worked numbers):

- **Park skills, do not delete them.** Move the unused skill directory outside
  the discovery directories (`~/.agents/skills-parked/`). The catalog pays for
  every skill's description on every turn; removing 26 Dart/Flutter skills cut
  ~9.7K chars on the originating machine. Restore with a single `mv`.
- **Compress baseline prose to bullets.** One measured trim kept every rule and
  semantic and cut the file in half (16,689 → 8,331 chars; the cost floor is
  ~7-8K chars before rules start dropping). A global baseline buys no precision
  from narrative.
- **Remove duplicate skills.** Near-identical descriptions signal role overlap
  (a `tdd`/`test-driven-development` pair). The similarity flags are advisory:
  read both before deciding.
- **Latency is not context.** First tune context; but a 5-char prompt that took
  42.0 s end-to-end with `thinking level high` and 41.8 s of `Server-Timing`
  needs a lite variant or lower thinking level for chat-scale turns. The
  analyzer flags this exact shape.
- **Do not touch prompt caching.** Keep section order and the leading sections
  byte-stable across edits so repeated turns share one cacheable prefix.

Report each lever with before → after character counts for its section, the
direction of the change, and what was verified.

### 8. Re-measure with a fresh successful capture

Trigger one minimal new exchange, render it, run the analyzer again, and
present the before/after table. If the chain does not produce a fresh success —
for example the default model fell back to a dead provider and only error
captures exist — say so and state which numbers are derived from payload sizes
rather than a 200 capture.

## Supported shapes

- `POST /v1/chat/completions`, streaming and non-streaming.
- Transcripts render as tables or compact prose headings; the analyzer reads
  both.
- Downstream OpenAI-compatible SSE with `delta.content`,
  `delta.reasoning_content` (or `reasoning`), streamed tool calls, usage, and
  `[DONE]`.
- Non-streaming JSON with `choices[0].message`, tool calls, and usage.
- Failure captures with a downstream 4xx/5xx and a JSON error object, including
  an unnumbered `API RESPONSE` written directly after its timestamp.
- Multiple numbered upstream attempts.
- No system message (the system-prompt document is then omitted; the analyzer
  reports the zero-budget sections as absent).
- Long tool results in request history: the transcript shows the first 2,000
  characters in a code block and says so; the raw reference keeps the full value.

Unsupported: other endpoints, image/non-text message content, malformed JSON,
split `request-log-parts-*` bodies, and capture files that do not use the
`=== SECTION ===` format. These fail with a message rather than a guessed
transcript.

## Traps

| Symptom | Actual cause |
| --- | --- |
| "I enabled logging but see no bodies" | `logging-to-file` was enabled; `request-log` was not. |
| "The setting is at the top level, but it is ignored" | The running version nests it under `observability.logs`; a legacy top-level key may be shadowed or migrated. Read the active file. |
| "There are error logs, so request logging must be on" | Error logs survive with request logging off; only `v1-*.log` proves a successful capture exists. |
| "The management logs endpoint should show the prompt" | It returns application logs. Bodies are only in the request captures. |
| "The error message ends in `...`" | The log truncated it. Preserve the truncation; do not complete it. |
| "The reply is empty" | Check `finish_reason` and the raw final chunks; an empty `content` with tool calls or a token limit is not the same as a refusal. |
| "I trimmed 20K chars, where are the savings?" | Confirm a fresh successful capture exists (a default model may have fallen back to a dead provider and only errors were logged); compare prompt tokens across bundles, not session-to-session chatter. |
| "Savings are smaller than the summary table" | Sections include XML/markup boundaries; the sum is slightly under the header-declared length and tool declarations live in the body, not the system message. Quote character ranges, not vibes. |
| "Tokens went up after parking skills" | Another lever fired first (model switch, larger capture with history). Compare like-for-like: same model, same endpoint, near-empty user prompt. |

## Files

| Path | Purpose |
| --- | --- |
| `scripts/find_request_logs.py` | config/log discovery, capture inventory, mismatch warnings, `--json` |
| `scripts/render_request_log.py` | transcript, system prompt, and raw-reference renderer with verification |
| `scripts/analyze_context_budget.py` | per-section context budget, skill ranking, duplicate flags, measured usage latencies and levers |
| `references/01-request-logging.md` | the two switches, config layout drift, where logs go, how to enable and verify |
| `references/02-log-anatomy.md` | section map, error-capture variants, stream/tool-call shapes, fidelity rules |
| `references/03-context-budget.md` | the optimization method, ownership map, worked before/after examples, verification protocol |
| `FIELD-NOTES.md` | why this skill exists and the setup already completed on the originating machine |
| `tests/` | scoped fixtures and renderer/finder/analyzer tests |

Run the tests narrowly:

```sh
python3 -m unittest discover -s skills/cliproxy-context-optimization/tests -v
```

Requires Python 3.10 or newer. No third-party packages are needed at runtime.
