# cliproxy-log-inspection

Read CLIProxyAPI request captures without drowning in escaped JSON.

A capture contains the full request body, the translated upstream request, the
upstream response, and the downstream response, all in one line per section. That
is complete and nearly unreadable. This skill finds the right capture, verifies
which logging switch produced it, and renders three documents:

- a transcript with the conversation, quoted thinking, reply, tool calls, and
  compact request/response details;
- the full system and developer prompt, verbatim, as Markdown;
- a raw reference with every header, request body, and stream payload.

The original `.log` is never modified. The raw reference preserves values;
presentation changes only.

## What this covers

| Path | What it is |
| --- | --- |
| [SKILL.md](SKILL.md) | entrypoint: rules, workflow, supported shapes, traps |
| [references/01-request-logging.md](references/01-request-logging.md) | the two switches, config layout drift, `auth-dir`, enabling and verifying capture |
| [references/02-log-anatomy.md](references/02-log-anatomy.md) | section map, error captures, SSE, tool calls, fidelity contract |
| [FIELD-NOTES.md](FIELD-NOTES.md) | why the skill exists and the setup already completed on the originating machine |
| [scripts/find_request_logs.py](scripts/find_request_logs.py) | config and log discovery; request/error inventory; mismatch warnings; `--json` |
| [scripts/render_request_log.py](scripts/render_request_log.py) | transcript, system prompt, and raw-reference renderer with automatic verification |
| [tests/](tests/) | fixture captures and scoped renderer/finder tests |

The central distinction: `logging-to-file` writes application logs.
`request-log` writes request and response bodies. Error logs can exist while
request logging is off, and the management logs endpoint never returns bodies.

## Install

Copy this directory into wherever your agent reads skills from:

```sh
cp -r skills/cliproxy-log-inspection ~/.config/opencode/skills/
```

Or install the whole set from the repository root with the `skills` installer.

Requires Python 3.10 or newer. The scripts use only the standard library.

## Use

Find out whether capture is enabled and where the files are:

```sh
python3 scripts/find_request_logs.py
```

Render one capture:

```sh
python3 scripts/render_request_log.py \
  ~/.cli-proxy-api/logs/v1-chat-completions-2026-10-25T233717-a2056a8e.log \
  --output-dir /tmp/readable
```

For that capture, the output directory contains:

```
v1-chat-completions-2026-10-25T233717-a2056a8e.md
v1-chat-completions-2026-10-25T233717-a2056a8e-system-prompt.md
v1-chat-completions-2026-10-25T233717-a2056a8e-raw-payloads.md
```

Existing outputs are not overwritten without `--force`. Exit code `4` means the
renderer's fidelity check failed; do not treat the bundle as verified in that
case.

## What the renderer supports

- Streaming and non-streaming `/v1/chat/completions` responses.
- Reasoning (`reasoning_content` or `reasoning`), streamed or complete tool
  calls, usage, and `[DONE]`.
- Failure captures, including an unnumbered `API RESPONSE` with a bare JSON error
  and no upstream request.
- Multiple numbered upstream attempts.
- No system message (the system-prompt file is omitted).
- Long tool results: previewed in the transcript, complete in the raw reference.

It rejects other endpoints, non-text message content, malformed JSON, and
incomplete split bodies rather than guessing.

## Verify

Run the scoped tests:

```sh
python3 -m unittest discover -s skills/cliproxy-log-inspection/tests -v
```

The tests cover a streaming success, a gateway failure with no upstream request,
a non-streaming tool call, an unsupported endpoint, overwrite refusal, JSON
preservation, and deliberate verification failures (mismatched JSON, an altered
reply, and an altered system prompt). The verification tests are guards against
the checks being vacuous.

## Limitations

- One capture is one request, not a session replay.
- Upstream payloads are preserved but not interpreted; the transcript is built
  from the downstream OpenAI-compatible exchange.
- Split `request-log-parts-*` bodies are reported by the finder but not joined by
  the renderer.
- Truncated errors stay truncated. The renderer does not invent missing text.
- Captures contain sensitive prompts and possibly credentials in headers. Keep
  them out of version control and redact before sharing.
