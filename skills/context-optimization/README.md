# context-optimization

Read captured model exchanges without drowning in escaped JSON — then bill
the agent context they expose and cut it.

A capture contains the full request body, the translated upstream request, the
upstream response, and the downstream response, all in one line per section. That
is complete and nearly unreadable. This skill obtains one model turn from any
supported source, renders it into readable documents, and then measures
where the per-turn context tokens go — the skill catalog, agent baseline files,
harness fixed cost, per-request latency levers — with savings proven by a fresh
capture:

- a transcript with the conversation, quoted thinking, reply, tool calls, and
  compact request/response details;
- the full system and developer prompt, verbatim, as Markdown;
- a raw reference with every header, request body, and stream payload;
- a context budget report per section with ownership map and levers.

The original capture is never modified. The raw reference preserves values;
presentation changes only. The analyzer alone needs no capture plumbing at
all: an OpenAI-compatible request/response JSON pair from any gateway or
session log (or plain rendered Markdown documents) is enough.

## What this covers

| Path | What it is |
| --- | --- |
| [SKILL.md](SKILL.md) | entrypoint: rules, workflow, capture sources, supported shapes, traps |
| [references/01-request-logging.md](references/01-request-logging.md) | CLIProxyAPI source: the two switches, config layout drift, `auth-dir`, enabling and verifying capture |
| [references/02-log-anatomy.md](references/02-log-anatomy.md) | CLIProxyAPI source: section map, error captures, SSE, tool calls, fidelity contract |
| [references/03-context-budget.md](references/03-context-budget.md) | optimization method, ownership map, capture sources, levers with receipts, verification protocol |
| [FIELD-NOTES.md](FIELD-NOTES.md) | why the skill exists and the setup already completed on the originating machine |
| [scripts/find_request_logs.py](scripts/find_request_logs.py) | CLIProxyAPI source: config and log discovery; request/error inventory; mismatch warnings; `--json` |
| [scripts/render_request_log.py](scripts/render_request_log.py) | CLIProxyAPI source: transcript, system prompt, and raw-reference renderer with automatic verification |
| [scripts/analyze_context_budget.py](scripts/analyze_context_budget.py) | per-section context budget from any source: skill ranking, duplicate flags, latency and caching levers |
| [tests/](tests/) | fixture captures and scoped renderer/finder/analyzer tests |

The central distinction: `logging-to-file` writes application logs.
`request-log` writes request and response bodies. Error logs can exist while
request logging is off, and the management logs endpoint never returns bodies.

## Install

Copy this directory into wherever your agent reads skills from:

```sh
cp -r skills/context-optimization ~/.config/opencode/skills/
```

Or install the whole set from the repository root with the `skills` installer.

Requires Python 3.10 or newer. The scripts use only the standard library.

## Use

### Source: CLIProxyAPI request-log capture

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

### Source: any OpenAI-compatible gateway or session log

Save one request and its response as JSON (`messages` in the request, `usage`
in the response; redact secrets first) and bill the turn directly:

```sh
python3 scripts/analyze_context_budget.py \
  --request req.json --response resp.json --latency-ms 41839
```

### Source: rendered Markdown documents

Already-readable docs work too — pass both paths:

```sh
python3 scripts/analyze_context_budget.py --system-prompt sp.md --transcript tx.md
```

Then pick a lever:

```sh
python3 scripts/analyze_context_budget.py /tmp/readable
python3 scripts/analyze_context_budget.py /tmp/readable --json --top 20
```

Both transcript shapes (table-based and prose-based) are parsed, and error
captures are supported with their limits stated: a failed exchange carries no
usage, so a post-change victory must be proven on a fresh successful capture.

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
python3 -m unittest discover -s skills/context-optimization/tests -v
```

The tests cover a streaming success, a gateway failure with no upstream request,
a non-streaming tool call, an unsupported endpoint, overwrite refusal, JSON
preservation, and deliberate verification failures (mismatched JSON, an altered
reply, and an altered system prompt). The analyzer tests pin the section split,
both transcript shapes, duplicate flagging, lever content, CLI exits, and
newest-bundle selection. The verification tests are guards against the checks
being vacuous.

## Limitations

- One capture is one request, not a session replay.
- Upstream payloads are preserved but not interpreted; the transcript is built
  from the downstream OpenAI-compatible exchange.
- Truncated errors stay truncated. The renderer does not invent missing text.
- Captures contain sensitive prompts and possibly credentials in headers. Keep
  them out of version control and redact before sharing.
- The context budget converts characters to tokens with a fixed ratio; its
  token counts are estimates, its character counts are measurements.
- Model/provider internals that own the harness catalog and tool declarations
  are out of scope: report them as fixed cost, not savings.
