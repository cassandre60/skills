# Field notes

Why this skill is separate from `cliproxy-integration`, and what was already
established before it was written.

## The wrong turn that produced it

The first attempt to inspect a request enabled `logging-to-file` and then searched
the resulting files for the prompt. Nothing was there. The setting writes
application logs; it has no access to request or response bodies.

The switch that matters is `request-log`. On the machine that produced this
skill, the two were independent settings, and only request logging produced the
per-request capture. That distinction is now the first rule in the skill.

## The second wrong turn: assuming the config path

The config that was being edited was not shaped like the examples in older
documentation. In CLIProxyAPI 8.0.27 the log settings are nested under
`observability.logs`, and `auth-dir` is under `oauth`. Searching for a top-level
`request-log` or assuming the default `~/.cli-proxy-api` directory would have
missed the real location.

The finder reads the active file and reports the values it finds. It does not
claim that a local file is the file the running process loaded; it says so
explicitly, because that distinction caused real confusion.

## Error logs are not proof

The logs directory contained `error-v1-chat-completions-*.log` files while
`request-log` was false. That looked contradictory until the retention rule was
clear: error logs are kept even when request logging is disabled, up to
`error-logs-max-files`. They are useful for diagnosing failures, but they do not
show that successful request bodies are being captured.

The finder calls this out in its verdict. The renderer supports both kinds of
capture.

## The management endpoint is application logs only

`GET /v0/management/logs` was checked as a possible shortcut for request bodies.
It returns application log lines, not captures, and requires the management key.
The skill records that fact so the next person does not spend time on the same
detour.

## Setup already completed on the originating machine

The machine that produced this skill had already done the setup:

1. Enabled `request-log` after finding that `logging-to-file` did not capture
   bodies.
2. Enabled `logging-to-file` separately for ordinary application logs.
3. Confirmed `auth-dir` and located the logs under it.
4. Confirmed the management logs endpoint returns application logs only.
5. Produced the captures used to build and test the renderer.

That sequence is documented in the logging reference as the verified path, not
as pending work. On any machine, verify the current state with the finder before
changing anything.

## Why three documents instead of one

A single Markdown file containing a 50,000-character system prompt, a readable
conversation, and every raw SSE payload is technically complete and practically
unreadable. The split is:

- transcript: the human answer;
- system prompt: the full instruction context, verbatim;
- raw reference: the evidence, including every header and stream payload.

The split is presentation. The raw reference retains the full request bodies and
stream values; the original capture remains authoritative.

## Reading a log is not replaying a session

One capture is one request. It can contain a long conversation history because
the client sent that history, but it is not a complete session trace. The
transcript preserves the messages it has and does not invent turns that were not
in the request. When a client sends a large tool result, the transcript previews
it and points to the raw reference; that is a readability choice, not a claim
that the omitted text was unimportant.

## Why the renderer refuses to guess

The failure modes that matter are silent:

- a pretty-printed body that no longer parses;
- a stream payload accidentally dropped;
- a reasoning block quoted but altered;
- a truncated error completed with plausible text.

The renderer verifies JSON round-trips, payload presence, and transcript text
before printing success. When it cannot verify, it exits non-zero. That is the
point: an unverified bundle is not a deliverable.
