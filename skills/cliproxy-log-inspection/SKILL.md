---
name: cliproxy-log-inspection
version: 1.0.0
description: Find CLIProxyAPI request logs, verify which logging switch is actually enabled, and render a captured /v1/chat/completions exchange as a readable Markdown transcript, full system prompt, and raw payload reference without altering values. Use when someone asks where CLIProxyAPI logs are, why logging-to-file is not capturing request bodies, how to read a v1-chat-completions or error-v1-chat-completions capture, how to extract a system prompt or model reply from proxy logs, or how to diagnose request-log, auth-dir, and management log settings.
---

# CLIProxyAPI log inspection

Goal: turn a CLIProxyAPI request capture into three documents a human can read —
the conversation, the full system prompt, and a raw reference — and answer
"why are there no request logs?" without guessing which switch is on.

**The original `.log` remains the authority.** The renderer changes presentation
(JSON indentation, Markdown headings, a quoted reasoning block), not values. It
re-parses every rendered JSON body, checks every stream payload, and verifies the
thinking and reply text before reporting success.

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

For `v1-chat-completions-2026-10-25T233717-a2056a8e.log`, this writes:

| File | Contents |
| --- | --- |
| `v1-chat-completions-2026-10-25T233717-a2056a8e.md` | transcript: user messages, quoted thinking, reply, compact request/upstream/response details |
| `v1-chat-completions-2026-10-25T233717-a2056a8e-system-prompt.md` | every system/developer message, verbatim, rendered as Markdown |
| `v1-chat-completions-2026-10-25T233717-a2056a8e-raw-payloads.md` | all headers, pretty-printed JSON bodies, every raw `data:` payload |

The command refuses to overwrite existing files. Pass `--force` after checking
that the old bundle is not the only remaining evidence.

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

## Supported shapes

- `POST /v1/chat/completions`, streaming and non-streaming.
- Downstream OpenAI-compatible SSE with `delta.content`,
  `delta.reasoning_content` (or `reasoning`), streamed tool calls, usage, and
  `[DONE]`.
- Non-streaming JSON with `choices[0].message`, tool calls, and usage.
- Failure captures with a downstream 4xx/5xx and a JSON error object, including
  an unnumbered `API RESPONSE` written directly after its timestamp.
- Multiple numbered upstream attempts.
- No system message (the system-prompt document is then omitted).
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

## Files

| Path | Purpose |
| --- | --- |
| `scripts/find_request_logs.py` | config/log discovery, capture inventory, mismatch warnings, `--json` |
| `scripts/render_request_log.py` | transcript, system prompt, and raw-reference renderer with verification |
| `references/01-request-logging.md` | the two switches, config layout drift, where logs go, how to enable and verify |
| `references/02-log-anatomy.md` | section map, error-capture variants, stream/tool-call shapes, fidelity rules |
| `FIELD-NOTES.md` | why this skill exists and the setup already completed on the originating machine |
| `tests/` | scoped fixtures and renderer/finder tests |

Run the tests narrowly:

```sh
python3 -m unittest discover -s skills/cliproxy-log-inspection/tests -v
```

Requires Python 3.10 or newer. No third-party packages are needed at runtime.
