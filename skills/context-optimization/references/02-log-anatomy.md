# Log anatomy: what each section means

A capture is a sequence of `=== SECTION ===` blocks. Sections are line-oriented:
a label, then either `Key: Value` metadata, headers, or a body. Bodies are not
fenced and can contain blank lines, so the renderer uses the next section marker
to end the current one.

## Successful chat-completions capture

The normal order is:

```
=== REQUEST INFO ===
=== HEADERS ===
=== REQUEST BODY ===
=== API REQUEST 1 ===
=== API RESPONSE 1 ===
=== RESPONSE ===
```

| Section | Meaning |
| --- | --- |
| `REQUEST INFO` | Proxy version, downstream URL and method, transports, request start timestamp. |
| `HEADERS` | Downstream request headers, including session and client-identifying headers. |
| `REQUEST BODY` | The client's JSON body: model, messages, tools, sampling settings. This is where the system prompt lives. |
| `API REQUEST n` | The translated upstream request: timestamp, upstream URL, method, masked auth metadata, headers, body. |
| `API RESPONSE n` | The upstream response: timestamp, status, headers, and body. The body may be SSE or JSON. |
| `RESPONSE` | The downstream response returned to the client: status, headers, and body. |

The downstream response is the OpenAI-compatible surface even when the upstream
speaks a different protocol. Render the transcript from `REQUEST BODY` and
`RESPONSE`; keep the upstream sections for verification and for questions about
retries, routing, or provider-specific errors.

## Failure capture

A failure still has `REQUEST INFO`, `HEADERS`, `REQUEST BODY`, and `RESPONSE`,
but the upstream section can be missing or shaped differently:

```
=== API RESPONSE ===
Timestamp: 2026-10-26T00:14:13.594003541+01:00
{"error":{"code":"model_cooldown","last_upstream_error":"..."}}

=== RESPONSE ===
Status: 429
Retry-After: 1031
Content-Type: application/json

{"error":{"code":"model_cooldown",...}}
```

Two variants matter:

1. **Rejected before dispatch.** There is no `API REQUEST` section. The gateway
   refused the request locally because no credential/model was available, or all
   credentials were cooling down. The unnumbered `API RESPONSE` may contain a
   bare JSON error immediately after its timestamp, with no status, headers, or
   `Body:` label.
2. **Failed after dispatch.** Numbered `API REQUEST n` and `API RESPONSE n`
   sections exist. A retry adds more numbers. A request can also exist without a
   response if the upstream connection failed.

A failure capture can be `error-*.log` even when request logging is disabled.
Its existence is not evidence that successful request bodies are being written.

## SSE bodies

Downstream streams are OpenAI-style server-sent events:

```
data: {"choices":[{"index":0,"delta":{"reasoning_content":"..."}}]}

data: {"choices":[{"index":0,"delta":{"content":"Hello"}}]}

data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{...}}

data: [DONE]
```

Rules the renderer applies:

- Each `data:` value is one JSON event. `[DONE]` is a terminator, not JSON.
- `delta.content` fragments concatenate in order.
- `delta.reasoning_content` (or `delta.reasoning`) fragments concatenate into the
  thinking block.
- `delta.tool_calls` fragments merge by `index`; `function.arguments` fragments
  concatenate before being shown.
- `finish_reason` is taken from the last event that sets it.
- `usage` is taken from the last event that contains it; it often arrives in the
  same event as the finish reason.
- Multiple `choices` are preserved in the raw reference. The transcript shows
  choice 0 and says so.

An upstream SSE body may use a different provider protocol. The renderer does
not interpret upstream streams for the transcript; it preserves every payload in
the raw reference.

## Non-streaming bodies

A non-streaming response is one JSON object:

```json
{
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Done",
        "tool_calls": []
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {}
}
```

The transcript uses `choices[0].message.content`, the same reasoning fields, and
tool calls. `finish_reason` and usage come from the same object.

## Tool calls

Tool calls can arrive in two shapes:

- Streamed: `delta.tool_calls` entries with an `index`, a function name, and
  argument fragments.
- Non-streaming: a complete `message.tool_calls` array.

The renderer merges fragments by index and renders each call's name and full
arguments. If the arguments are valid JSON, they are shown as JSON; if they are
incomplete or invalid, they are shown as text rather than silently discarded.

Tool results in the request history are context, not model output. The transcript
renders them in a code block. Results longer than 2,000 characters are previewed
with an explicit note; the complete value stays in the raw reference.

## System and developer messages

The system prompt is a message in `REQUEST BODY`, not a separate log section. It
can be one message or several, and the role can be `system` or `developer`. The
renderer concatenates nothing: each message is written verbatim under its own
heading in the system-prompt document.

If there is no system or developer message, the document is omitted and the
transcript says `System prompt: none captured.` That is different from a system
message with empty content; the latter is rendered as an empty message.

## Truncated values

CLIProxyAPI can truncate long error details. A captured value may end in `...`
inside an otherwise valid JSON string, and the nested JSON may be unterminated.
Examples:

- `last_upstream_error` contains a JSON object that stops mid-string.
- The outer `message` repeats that truncated fragment.

This is not a parsing bug and not a reason to reconstruct the missing text. The
renderer extracts the readable fields it can see and preserves the raw fragment.
When the full error matters, consult the upstream provider; do not present a
completion you invented.

## Split bodies and unsupported shapes

The finder reports sibling directories named `request-log-parts-*`. They can
hold large request bodies in pieces. The renderer expects a complete
`REQUEST BODY` in the capture and rejects a body that is not valid JSON. It does
not join parts. If a capture is split, inspect the parts manually or join them in
a temporary copy before rendering, and say that you did so.

Other unsupported shapes:

- endpoints other than `POST /v1/chat/completions`;
- image or other non-text message content;
- capture files without `=== SECTION ===` markers;
- duplicate section names or missing required sections.

These fail explicitly. A readable-looking but guessed transcript is worse than
no transcript.

## Fidelity contract

The renderer verifies before it reports success:

- Every rendered JSON body is parsed again and compared with the source value.
  Pretty-printing changes whitespace, not values.
- Every `data:` payload from the source appears in the raw reference.
- Every system/developer message appears verbatim in the system-prompt document.
- The thinking block and the reply in the transcript are exactly the captured
  text after presentation (blockquote markers, headings) is removed.

The original `.log` is still the final authority. The bundle is a reading aid,
not a replacement for the capture.
