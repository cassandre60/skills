# The context budget: measuring and cutting per-turn context cost

This reference records a verified optimization run (25 October 2026, one
`gemini-3.8-flash` machine). It defines the measurement method, the ownership
map, the levers, and the verification protocol. Every number here was measured
off a rendered capture, not estimated.

## 1. What a capture proves

A successful `v1-*.log` capture is a complete bill for one model turn:

- the full request body (what was actually sent), `Content-Length` included;
- the exact system message, verbatim in the rendered system-prompt document;
- the upstream request and its headers (including `Server-Timing`);
- the response usage: prompt/completion/reasoning/total tokens, sometimes with
  a per-modality breakdown.

Prompt tokens for a trivial turn are the baseline tax every later turn pays
before any user content. That tax is what this skill reduces.

## 2. The worked example (measured)

One capture, `gemini-3.8-flash`, user prompt "say hi", streamed:

| Fact | Value |
| --- | ---: |
| Request body | 64,920 bytes |
| System message | 50,217 chars |
| Prompt / completion / reasoning tokens | 15,354 / 183 / 174 |
| Total | 15,537 |
| End-to-end latency | 42.0 s — 41.8 s inside the provider (`Server-Timing: gfet4t7; dur=41839`) |
| Thinking level | `high` |
| Tool declarations | 12 |

The system message split (character counts are authoritative; token shares are
estimates at 3.85 chars/token):

| Section | Chars | ~Tokens | % |
| --- | ---: | ---: | ---: |
| skills catalog (62 skills) | 26,544 | ~6,600 | 53% |
| agent baseline files | 16,689 | ~4,300 | 33% |
| Code Mode catalog | 4,817 | ~1,200 | 10% |
| harness + identity | ~1,500 | ~400 | 3% |
| date/env tail | ~600 | ~160 | 1% |

Sections do not sum to the header length: boundary text and the document
header account for the remainder. Quote character ranges per section, not a
single global number.

## 3. Ownership map

| Component | Who can change it | How |
| --- | --- | --- |
| Skill catalog descriptions | the machine owner | park or prune skill directories |
| Agent baseline files (global AGENTS.md) | the agent owner | compress prose to terse bullets |
| Model choice + thinking level | the configuration | lite/high variants per task class |
| Harness identity block, Code Mode catalog, tool declarations | the harness release | not configurable — report as fixed cost |
| Date/env tail | the harness | keep it last; do not move it up |

Report savings only for rows the machine owner actually changed.

## 4. Lever catalog (with the originating machine's receipts)

### 4.1 Park skills (biggest, reversible)

The catalog cost is the skill's XML description block — no matter whether the
skill is ever invoked. Two facts from the run:

- 62 skills carried 26.5K chars; the single heaviest skill was ~1.0K chars.
- Parking the 26 Dart/Flutter skills (~9.7K chars), three near-duplicate pairs
  (~1.0K), an MCP-less browser skill (~0.5K), and six personal-workflow skills
  (~1.9K) took the catalog from 26.5K to ~13.5K chars.

Method:

1. Rank descriptions by size; map each to a repo/config/artifact it might serve.
2. Park clearly unused domains: `mv <skill-dir> ~/.agents/skills-parked/`
   (outside every discovery root: project, `~/.config/opencode/skills`,
   `~/.agents/skills`). Never delete inside a shared skills tree — other
   harnesses read the same files.
3. Remove any now-dangling symlinks in other discovery directories.
4. Restore any parked skill with one `mv` back.

### 4.2 Compress the baseline files

A 16,689-char global baseline carried policy as narrative (multi-paragraph
protocols with example prompts). Compressing to one-bullet-one-rule kept every
rule verbatim in semantics and halved the file to 8,331 chars. The floor is
around 7-8K chars before rules must be dropped: below that point the change is
a policy edit, not a formatting edit, and needs explicit owner sign-off.

Rules that must survive any rewrite: attribution footer shape, the hard test
rule, CI thresholds and merge-queue behavior, escalation trigger list,
verifiable-lesson write-back rules, and the human-identity protections.

### 4.3 Fix trivial-turn latency separately

Context size and latency are different problems:

- The example capture answered a 5-char prompt in 42.0 s while burning 174
  thought tokens — provider server time dominated, driven by
  `thinkingLevel: high` plus a 15.4K prefill.
- Halving the system message does not halve that 42.0 s; thinking time and
  prefill from cached tokens do not linearly stack.
- The right tool for chat-scale turns is a cheaper variant (`*-flash-lite`,
  or a lower thinking setting), picked per task class. The analyzer flags the
  exact pattern: `thinking level high` + short user prompt + server `dur`
  above ≈15 s.

### 4.4 Preserve the cacheable prefix

Repeated turns of a session resend the whole prompt; provider implicit caching
discounts a byte-stable prefix. Therefore:

- Keep section order stable when editing.
- Keep the unstable per-session context (working directory, date) at the tail —
  that is where the harness already puts it.
- A prefix that stays identical across turns keeps cache discounts; churn costs
  re-caching.

### 4.5 Duplicate roles get read-and-choose

Similarity flags are advisory (character-similarity threshold 0.5 on
descriptions). The measured run kept `tdd`, `code-review`,
`debugging-and-error-recovery` and parked their overlapping siblings after
reading both. Never park on the flag alone — descriptions can be similar
while tools differ.

## 5. Verification protocol

For every applied lever:

1. Before: run `analyze_context_budget.py` on a recent successful capture.
2. Apply one lever at a time.
3. Trigger one minimal new exchange through the same model and endpoint.
4. Render the new capture; run the analyzer again.
5. Report a before/after table: section chars, prompt tokens, and latency.
6. If the fresh capture never arrives (for example the CLI's default-model
   chain fell back to a dead provider and only `error-*.log` files exist):
   measured work remains valid because payload sizes are themselves captured,
   but say plainly that the post-change token delta is derived, not measured,
   and name the blocker.

Measured end state on the originating machine: 62 → 26 advertised skills
(catalog 26.5K → ~13.5K chars), baseline 16,689 → 8,331 chars, and a fixed
system message of ~28.8K chars — about a 43% reduction in per-turn fixed
context, with an expected prompt-token drop of roughly a third on
new sessions.

## 6. Failure modes

| Symptom | Cause |
| --- | --- |
| Analyzer reports 0 for a section that visibly exists | The marker string changed in the renderer output; update the parser and its tests together. |
| Savings claimed but no fresh capture | Derived, not measured — must be labelled. |
| Skill parked but prompt unchanged | A symlink in another discovery directory still resolves, or the parked path is inside a scanned root. |
| Catalog unchanged after "pruning" | The skill was defined in a second harness-specific location; search all discovery roots. |
| Catalog edit breaks discovery | Symlink targets or a discovery whitelist in config; re-run the repo's discovery checks on the source repo. |
