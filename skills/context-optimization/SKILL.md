---
name: context-optimization
version: 2.1.0
description: Measure where an agent's per-turn context tokens go and cut what the machine owns — bill one model turn's fixed cost (skill catalog, instruction baseline files, harness catalogs, tool declarations, date/env tail) from any capture source, then prove every saving with a fresh successful capture. Levers — park or prune skills, compress instruction files, remove duplicates, pick lite model/thinking variants, keep the cacheable prefix stable. Sources — CLIProxyAPI request-log captures (find captures, verify the logging switch, render a readable transcript/system-prompt/raw bundle), raw OpenAI-compatible request/response JSON pairs from any gateway or session log, or already-rendered system-prompt and transcript Markdown. Use when someone asks why a trivial prompt costs 15K tokens or 40 seconds, which skills or instructions dominate context, how to trim agent context and prove the saving, where CLIProxyAPI logs are, why logging-to-file is not capturing request bodies, how to read a v1-chat-completions or error-v1-chat-completions capture, how to extract a system prompt or model reply from proxy logs, or how to diagnose request-log, auth-dir, and management log settings.
---

# Context optimization

Two jobs, one chain: first turn a captured model exchange into documents a
human can read, then use those documents to reduce the agent's fixed per-turn
context cost — with every claim measured, shown as a before/after, and
re-measured fresh.

**Fully agnostic core, pluggable capture sources.** The budget analyzer and
the levers work on any OpenAI-compatible request/response pair or any rendered
system-prompt/transcript document. Today's detailed capture plumbing is the
CLIProxyAPI source adapter: its log finder, config verification, and faithful
bundle renderer. New sources (other gateways, other harness logs) need only to
produce the same two documents or a request/response JSON pair.

**The original capture remains the authority.** Presentation changes (JSON
indentation, Markdown headings, quoted reasoning blocks) never alter values.
The renderer re-parses every rendered JSON body, checks every stream payload,
and verifies the thinking and reply text before reporting success.

## Non-negotiable rules

1. **Measure before trimming; verify after trimming.** Character counts from a
   capture are measurements. Token totals are model-reported facts. The
   chars-per-token conversion is an estimate and is labelled as one. Savings
   from a change are only proven by a fresh successful capture — an error
   capture carries no usage and cannot prove anything.
2. **Optimize only what the machine owns.** The skill catalog, agent baseline
   files, and model/thinking selection are owner-addressable. The harness body,
   tool declarations, and Code Mode catalog change with releases, not config.
   Do not report them as savings you caused.
3. **Keep prefixes stable.** When trimming instruction text, do not churn
   section order and do not move the date/env block earlier; the cacheable
   prefix is the leading, byte-stable part of the prompt.
4. **Section markers are a taxonomy, not a law.** The analyzer anchors on the
   OpenCode section markers; a system message from another harness bills as
   one `unmapped_system_message` total with generic levers — read it, do not
   guess sections into it.

## Rules specific to the CLIProxyAPI capture source

5. **`logging-to-file` is not request logging.** It writes application logs to
   rotating files. Request and response bodies require `request-log`. Enabling
   one does nothing for the other.
6. **Do not assume the config layout.** CLIProxyAPI 8.0.27 nests these keys under
   `observability.logs`; older documentation and panels may show top-level keys.
   Read the active file and find the uncommented key. See
   [the logging reference](references/01-request-logging.md).
7. **Logs live under `auth-dir`, not automatically under `~/.cli-proxy-api`.**
   Resolve `auth-dir` first, then use `<auth-dir>/logs`.
8. **`error-*.log` is not proof that request capture is on.** Error logs are
   retained even when request logging is disabled. Successful captures are
   `v1-*.log`; their presence can be historical.
9. **The management endpoint is not a body dump.** `GET /v0/management/logs`
   returns application log lines, never request or response bodies.
10. **Never repair a truncated capture.** CLIProxyAPI can truncate long upstream
    errors in the log. Preserve the visible text; do not fill in the missing JSON
    or claim the full error is known.

## Workflow

### 1. Pick a source and obtain one turn

- **CLIProxyAPI capture** (works offline from stored logs): run the finder and
  renderer from this skill's directory:
  ```sh
  python3 scripts/find_request_logs.py
  python3 scripts/render_request_log.py CAPTURE.log --output-dir READABLE_DIR
  ```
  The finder reads the local config, resolves `auth-dir`, lists request captures
  and error logs, and warns when `request-log` is off (`--json` for machine
  output; `--config/--logs-dir` for non-default paths). The renderer writes the
  transcript, the system-prompt document, and the raw reference; existing
  outputs are not overwritten without `--force`; exit `4` means do not treat
  the bundle as faithful.
- **Any OpenAI-compatible gateway or session log**: save the request JSON (needs
  `messages`, optionally `tools`, `model`) and the response JSON (needs
  `usage`). Redact secrets first — request bodies carry prompts and possibly
  credentials.
- **Rendered Markdown**: pass `--system-prompt` and `--transcript` document
  paths directly.

### 2. Measure the context budget

```sh
python3 scripts/analyze_context_budget.py READABLE_DIR          # rendered bundle
python3 scripts/analyze_context_budget.py --request req.json --response resp.json
python3 scripts/analyze_context_budget.py --request req.json --response resp.json \
    --latency-ms 41839                                          # recorded latency
```

`--json` for machine output, `--top N` for the skill ranking length. The report
is a per-section budget with character counts (authoritative) and estimated
tokens (at 3.85 chars/token): harness and identity, Code Mode catalog, skills
catalog, agent baseline files, date/env tail — plus the skill-position rank
inside the catalog, duplicate-similarity pairs, the captured usage facts
(tokens, body bytes, tool count, thinking level, server time), and provider
cache hits when the response reported `prompt_tokens_details.cached_tokens`.
For a non-OpenCode system message the whole body bills as
`unmapped_system_message` with generic levers; section-level work needs the
markers first.

The big sections have known owners:

| Section | Owner | Typical share |
| --- | --- | ---: |
| skills catalog | the machine's skill directories | 25-50% |
| agent baseline files | the agent's own AGENTS.md files | 15-30% |
| Code Mode catalog + tool declarations | the harness | 15-25% |
| harness and identity, date/env | the harness | <5% |
| unmapped system body | whoever wrote the prompt | read it first |

### 3. Choose and apply levers, one at a time

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
- **Cash in cache, then protect it.** When the response reports cached prompt
  tokens, the stable prefix is already earning a discount: trim the tail, never
  churn the head — and re-measure to confirm the discount survived.
- **Billing precision, not vibes.** Sections include markup boundaries and
  quote character ranges per section; the sum of sections sits slightly under
  the header-declared length by design, and tool declarations live in the body,
  not the system message.

Report each lever with before → after character counts for its section, the
direction of the change, and what was verified.

### 4. Re-measure with a fresh successful capture

Trigger one minimal new exchange on the same model and endpoint, capture it the
same way as step 1, run the analyzer again, and present the before/after table.
If the chain does not produce a fresh success — for example the default model
fell back to a dead provider and only error captures exist — say so and state
which numbers are derived from payload sizes rather than a 200 capture.

### 5. Read the rendered CLIProxyAPI bundle correctly

- Start with the transcript for the human answer.
- Open the system prompt document when the question is about instructions,
  context, or why the model behaved a certain way.
- Open the raw reference when exact bytes, headers, upstream attempts, usage, or
  an error body matter.
- State which document a quotation came from. Do not merge the system prompt
  into the transcript and call the result one log.

## Supported shapes

- `POST /v1/chat/completions`, streaming and non-streaming, plus multi-part
  `request-log-parts-*` bodies.
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
- Raw request/response JSON pairs with string or text-only message content;
  non-text content parts are flagged, not invented.
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
| "A non-OpenCode prompt bills as one lump" | Its section markers differ; totals are still measured. Add the taxonomy recognition or accept the generic framing. |

## Files

| Path | Purpose |
| --- | --- |
| `scripts/find_request_logs.py` | CLIProxyAPI config/log discovery, capture inventory, mismatch warnings, `--json` |
| `scripts/render_request_log.py` | transcript, system prompt, and raw-reference renderer with verification |
| `scripts/analyze_context_budget.py` | per-section context budget, skill ranking, duplicate flags, measured usage/latency/caching facts and levers, from any supported source |
| `references/01-request-logging.md` | the two switches, config layout drift, where logs go, how to enable and verify |
| `references/02-log-anatomy.md` | section map, error-capture variants, stream/tool-call shapes, fidelity rules |
| `references/03-context-budget.md` | the optimization method, ownership map, capture sources, worked before/after examples, verification protocol |
| `FIELD-NOTES.md` | why this skill exists and the setup already completed on the originating machine |
| `tests/` | scoped fixtures and renderer/finder/analyzer tests |

Run the tests narrowly:

```sh
python3 -m unittest discover -s skills/context-optimization/tests -v
```

Requires Python 3.10 or newer. No third-party packages are needed at runtime.
