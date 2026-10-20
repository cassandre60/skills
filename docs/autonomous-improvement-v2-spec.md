# `autonomous-improvement` — V1 Problem Report and V2 Implementation Spec

**Audience:** a local coding agent that will build V2 of the skill.
**Input:** the V1 skill folder (`SKILL.md`, `README.md`, `references/01–07`, `scripts/`, `assets/`).
**Output:** a V2 skill folder, plus tests, that fixes every problem below.

---

## 0. How to use this document

1. **Read V1 first.** Open `SKILL.md`, then every file in `scripts/autonomous/`, `assets/`, and `references/`. This document points at files, functions and line numbers (line numbers are for V1 as reviewed; they may drift by a few lines).
2. **Verify before fixing.** Every problem has an *Evidence* tag:
   - `[VERIFIED]` — reproduced by running V1 code in a scratch git repo. The repro is given. Re-run it first to confirm, then write it as a regression test.
   - `[READ]` — found by reading the code or docs, not executed (usually because it needs systemd, `opencode`, or a real toolchain). Confirm by reading and, where a procedure is given, by experiment. If you disagree after checking, say so in your report instead of silently skipping it.
3. **Severity:**
   - **P0** — unsafe or unusable in any repo other than the author's. Must be fixed.
   - **P1** — wrong results or broken documented behavior. Must be fixed.
   - **P2** — design/maintainability. Should be fixed.
   - **P3** — polish. Fix if cheap.
4. **Constraints for V2:**
   - Python scripts: **standard library only**, Python ≥ 3.9, runnable on Linux/macOS. Required external tools: `git`; `gh` only for PR-aware features, and the scripts must degrade gracefully (clear message, non-crash) when `gh` is absent.
   - No hardcoded project names, URLs, or toolchain commands in scripts. Everything project-specific lives in one config file (§4.2).
   - Never use `shell=True` with interpolated values. Run commands as argv lists.
   - Preserve the **intent** of the four laws (fail-to-pass, content-hash dedup, worktree isolation, machine budget). Law 1's wording changes (see E1); the others keep their meaning.
   - Keep `SKILL.md` under ~200 lines; push detail into `references/`.
5. **Deliver:** the V2 folder, a `tests/` suite that passes with `python3 -m unittest`, and a short `CHANGELOG-v2.md` mapping each problem ID below to what you did (or why you did not).

### Original skill in one paragraph (so you have context)

The skill (`name: autonomous-improvement`) gives an AI agent a protocol for spending tokens on *mechanically falsifiable* work instead of cosmetic churn. Stream A is mutation testing (a regex-based engine mutates source, runs targeted tests, and surviving mutants become "write a test that kills this" tasks). Stream B is an adversarial audit of existing tests. Streams C and D are planned, not implemented. A markdown ledger (`docs/AUTONOMOUS_WORK_LEDGER.md`) records audited files by **content hash** so unchanged files are never re-audited. An orchestrator script manages isolated git worktrees and a gate check. References 05–07 and a systemd watchdog cover multi-day unattended operation with an "orchestrator" agent and worker agents. The skill was extracted from a Flutter/Dart project (ChessSRS), and much of that project's specifics leaked into the "generic" tooling. That leakage is the main theme of this report.

---

## 1. Problem index

| ID | Sev | Area | Problem | Evidence |
|----|-----|------|---------|----------|
| A1 | P0 | orchestrator | `create-worktree` overwrites the user's `origin` with the author's repo URL | VERIFIED |
| A2 | P0 | portability | ChessSRS/Dart/fvm hardcoded in `ledger.py`, `orchestrator.py`, docs | VERIFIED (ledger) / READ |
| A3 | P0 | orchestrator | `shell=True` with interpolated slugs/filenames | READ |
| A4 | P0 | orchestrator | `cleanup-worktree` always uses `--force` (discards uncommitted work) | READ |
| A5 | P1 | orchestrator | `verify-gate` analyze step is a no-op in the prescribed flow | READ + logic |
| A6 | P1 | ledger | Missing `gh` crashes `ledger next`; `gh` dependency undocumented | VERIFIED |
| B1 | P0 | ledger | After a re-audit, parser keeps the **oldest** row → infinite re-audit | VERIFIED |
| B2 | P1 | ledger | `next --stream 2` silently does nothing; stream naming inconsistent | VERIFIED |
| B3 | P1 | ledger | Open-PR collision check is substring-based (false skips) | VERIFIED |
| B4 | P1 | ledger | `\|` in a summary breaks the table row | VERIFIED |
| B5 | P1 | ledger | `record` fails if a formatter rewrites the table separator row | VERIFIED |
| B6 | P1 | ledger | `--pr` is required even for `SOUND` results (no PR exists) | VERIFIED |
| B7 | P2 | ledger | `IN_PROGRESS` status is documented but meaningless | READ |
| B8 | P1 | ledger | Dedup hashes the source file only; test changes don't invalidate | READ |
| B9 | P2 | ledger | Hash/paths depend on cwd; dirty tree not detected | READ |
| B10 | P2 | ledger | Ledger edits conflict between parallel agents | VERIFIED (fix) |
| B11 | P3 | ledger | Template's example row is parsed as real data | READ |
| B12 | P3 | ledger | `record` accepts duplicates and nonexistent targets | READ |
| C1 | P1 | mutation | "Language-agnostic" is false; rules are Dart-only (Python → zero mutants) | VERIFIED |
| C2 | P1 | mutation | Compile-failure detection misclassifies real test failures | VERIFIED |
| C3 | P1 | mutation | Duplicate mutants; generics (`List<int>`) mutated into noise | VERIFIED |
| C4 | P1 | mutation | In-place mutation is not crash-safe; shards can corrupt each other | READ (fix tested) |
| C5 | P2 | mutation | `--max-mutants` takes the first N → bias toward top of file | READ |
| C6 | P2 | mutation | Docs promise "diff-scoped mutation" but the engine has no such option | VERIFIED |
| C7 | P2 | mutation | Timeouts counted as kills; no separate baseline timeout | READ |
| C8 | P3 | mutation | Exit code 1 on survivors conflates "found work" with "failure" | READ |
| C9 | P3 | mutation | Trailing comments and multi-line strings not guarded | READ |
| C10 | P3 | mutation | Generated issue text contradicts the fail-to-pass wording; project-specific refs | READ |
| C11 | P3 | mutation | Negative literals (`== -1`) never mutated | READ |
| D1 | P1 | watchdog | `setsid nohup … &` inside a `Type=oneshot` unit likely gets killed by systemd | READ |
| D2 | P1 | watchdog | Foreground nudge has no timeout; a hang blocks all future timer runs | READ |
| D3 | P2 | watchdog | `pgrep -f opencode` is too broad (and unnecessary) | READ |
| D4 | P2 | watchdog | Install instructions are incomplete (enable, linger, copy, chmod) | READ |
| D5 | P2 | watchdog | Harness-specific (opencode) but advertised as generic | READ |
| D6 | P3 | watchdog | No blast-radius guidance for unattended `--auto` sessions | READ |
| E1 | P1 | docs | Law 1 contradicts Stream A (mutant-killing tests pass on `origin/main`) | READ |
| E2 | P1 | docs | Dangling dependencies (G05/G07/G08, `gates.sh t1`, companion skill, R0/R1…) | READ |
| E3 | P1 | docs | Description triggers on unimplemented streams and on vague prompts | READ |
| E4 | P2 | docs | Core skill and multi-day fleet material serve different audiences | READ |
| E5 | P2 | docs | `SKILL.md` Workflow block is Dart-specific and duplicated in ref 04 | READ |
| E6 | P2 | docs | Law 4 rationale is a personal anecdote | READ |
| E7 | P2 | docs | No evals, no tests, no worked examples | READ |
| E8 | P3 | docs | Typos/incorrect commands in references; README claims | VERIFIED |
| E9 | P3 | layout | Byte-identical duplicate scripts; top-level copy resolves ledger path wrongly | VERIFIED |
| E10 | P3 | docs | README duplicates SKILL.md content | READ |

---

## 2. V2 goals and non-goals

**Goals**
- A skill that works unmodified (after one config file) in any git repo with a test runner: Dart, Python, TypeScript/JavaScript, Go at minimum.
- Scripts that are safe by default: no remote rewriting, no forced deletes, no shell interpolation, crash-safe source mutation.
- A ledger whose dedup is *correct* (newest row wins, source+test hash, escaped cells) and whose PR-collision check is precise.
- Docs that are internally consistent (laws, workflow, scripts agree) with no dangling references.
- A regression test suite derived from the repros below, plus evals for agent behavior.

**Non-goals**
- AST-based mutation. The engine stays regex-based; its limits stay documented ("every survivor needs human triage").
- Implementing Streams C and D. Keep them out of the description; keep a short "planned" note in the body.
- Replacing the ledger with a database. Stay with a git-tracked markdown table.

---

## 3. Problems and fixes

### A. Orchestrator and portability

#### A1 — [P0] [VERIFIED] `create-worktree` overwrites the user's `origin`

**Where:** `scripts/autonomous/orchestrator.py` — `REPO_ORIGIN = "https://github.com/cassandre60/ChessSRS.git"` (line 22); `cmd_create_worktree` runs `git remote set-url origin {REPO_ORIGIN}` (line 42) before fetching. `SKILL.md` Workflow step 0 also shows a bare `git remote set-url origin <url>` line.

**Evidence / repro:**
```bash
git init -q -b main r && cd r && git remote add origin https://github.com/me/my-real-repo.git
# commit something, copy orchestrator.py in, then:
python3 scripts/autonomous/orchestrator.py create-worktree --slug demo   # fails later (network)
git remote get-url origin   # → https://github.com/cassandre60/ChessSRS.git  (was my-real-repo)
```
The remote is changed even though the command then failed.

**Why it matters:** silent, persistent corruption of the user's repo config; the user's next `git push` could go to someone else's repository (or fail confusingly).

**Fix:**
- Delete `REPO_ORIGIN` and every `set-url` call from the scripts.
- Remove the `git remote set-url origin <url>` line from the `SKILL.md` Workflow and anywhere else it appears in `references/` (grep for `set-url`).
- If the author's environment needs an origin fix-up (e.g., a mirror), make it **opt-in**: config key `origin_url` plus an explicit `--set-origin` flag that prints the old and new URL and asks for `--yes`. Default behavior never touches remotes.
- `create-worktree` should only `git fetch origin <base-branch>` (base from config, default `main`).

**Acceptance:** regression test — after `create-worktree` (success *or* failure), `git remote get-url origin` is unchanged.

---

#### A2 — [P0] [VERIFIED for ledger, READ for orchestrator] Project-specific values hardcoded in "generic" tools

**Where:**
- `ledger.py` — the hardcoded `STREAM_1_TARGETS` list used by `cmd_next` (Dart paths such as `lib/src/domain/graph_aware_review_coordinator.dart`).
- `orchestrator.py` — worktree path `../chesssrs-{slug}` (lines 36, 85); `fvm flutter pub get`, `fvm dart run build_runner …`, `fvm dart format lib/l10n/` (lines 50–53); `fvm dart format --output=none --set-exit-if-changed lib test` (62); `fvm flutter analyze` (70); `./scripts/gates.sh t1` (78); docstring example `../chesssrs-pks-null-check` (12).
- `mutation_engine.py` — argparse text says "Autonomous Dart Mutation Engine" / "Path to Dart source file".
- `SKILL.md` Workflow block (Flutter commands) — see E5.

**Evidence / repro:** in an unrelated repo, with a stub `gh` that returns `[]`:
```bash
python3 scripts/autonomous/ledger.py next --stream 1
# → NEXT TARGET FOUND: lib/src/domain/graph_aware_review_coordinator.dart   (file does not exist)
```
`SKILL.md` Quick Start step 1 is exactly this command, so the skill's first step fails for everyone but the author.

**Fix:**
- Introduce the config file (§4.2). All project-specific commands, globs and mappings come from it.
- `ledger.py next` must enumerate candidates from config globs via `git ls-files` (see B2), never from a literal list. Skip files that do not exist at HEAD.
- `orchestrator.py`: worktree dir = sibling of the repo, named `<repo-dir-name>-<slug>` where repo-dir-name is the basename of `git rev-parse --show-toplevel`. Setup, format, analyze and gate commands are argv lists from config; if a key is absent, skip that step and print `[skip] <step> not configured`.
- Ship two example configs: `examples/config.dart.json` (reproducing the author's ChessSRS behavior: fvm, build_runner, `gates.sh t1`) and `examples/config.python.json`.
- Update all docstrings and argparse help to be language-neutral.

**Acceptance:** run `ledger.py next` and `orchestrator.py create-worktree` in a fresh Python repo with only the Python example config → no Dart/fvm/ChessSRS strings appear in output or are executed. `grep -ri 'chesssrs\|cassandre\|fvm\|build_runner' scripts/ SKILL.md README.md` returns nothing except inside `examples/config.dart.json` and docs that explicitly present it as an example.

---

#### A3 — [P0] [READ] `shell=True` with interpolated values

**Where:** `orchestrator.py` `run_cmd(cmd: str, …)` calls `subprocess.run(cmd, shell=True, …)` (line ~27); callers interpolate `slug`, `worktree_path`, and filenames parsed from `git status` (lines 42, 70, 91). Irony: `mutation_engine.py` explicitly avoids shell injection via `shlex`.

**Why it matters:** a slug or filename containing spaces, `;`, `$()` or quotes breaks the command or executes arbitrary code. Filenames with spaces are also mis-split by `line.strip().split()[-1]` (line ~66).

**Fix:**
- Replace `run_cmd` with `run(argv: list[str], cwd=None, check=True, timeout=None)` using `subprocess.run(argv, …)` (no shell). Catch `FileNotFoundError` and report `required tool not found: <argv[0]>`.
- Validate slugs: `^[a-z0-9][a-z0-9._-]{0,63}$`; reject otherwise with a clear message.
- Obtain file lists with `git diff --name-only -z` / `git status --porcelain -z` and split on `\0`.
- Tested snippet:
```python
import re, subprocess
SLUG = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')
def run(argv, cwd=None, check=True, timeout=300):
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise SystemExit(f"required tool not found: {argv[0]}")
    if check and p.returncode != 0:
        raise SystemExit(f"command failed ({p.returncode}): {' '.join(argv)}\n{p.stderr.strip()}")
    return p
```
**Acceptance:** tests — slug `a;rm -rf /` and `Has Space` are rejected; a tracked file named `my file.dart` is passed to the analyzer as a single argv element.

---

#### A4 — [P0] [READ] `cleanup-worktree` always force-removes

**Where:** `orchestrator.py` line 91: `git worktree remove {worktree_path} --force`.

**Why it matters:** `--force` deletes the worktree even with uncommitted changes or unpushed commits. This contradicts Law 3 ("never run destructive git commands outside the assigned worktree") and the ownership rule in the references ("never delete a worktree you didn't create").

**Fix:**
- Maintain a registry of worktrees created by the tool: `<git-common-dir>/autonomous-worktrees.json` with `{slug, path, branch, base_sha, created_at}`. `create-worktree` appends; `cleanup-worktree` refuses unregistered paths.
- Default `cleanup-worktree` = plain `git worktree remove` (no `--force`), after checking: working tree clean (`git status --porcelain` empty) and no unpushed commits (`git rev-list --count @{u}..HEAD` is 0, or branch has no upstream and HEAD is reachable from `origin/<base>`). On failure, print exactly what would be lost and exit 1.
- Add `--force` as an explicit flag that prints what is being discarded; optionally `--delete-branch`.

**Acceptance:** tests — cleanup refuses (exit ≠ 0) with a dirty file, refuses with an unpushed commit, refuses an unregistered path, and succeeds on a clean pushed worktree; `--force` succeeds on the dirty one.

---

#### A5 — [P1] [READ + logic] `verify-gate`'s analyze step cannot fire in the prescribed flow

**Where:** `orchestrator.py` `cmd_verify_gate` (lines ~58–80): it finds files to analyze with `git status --porcelain` and filters `.dart`. `SKILL.md` Workflow step 4 and the script's own note (line ~81) say to run gates **after committing**.

**Why it matters:** after a commit, `git status --porcelain` lists only *uncommitted* changes → empty → the script prints "No changed … files to analyze" and skips analysis. The step is dead code in exactly the flow the skill prescribes. (Also `.dart`-only.)

**Fix:**
- Touched files = union of `git diff --name-only --diff-filter=ACMR -z <base>...HEAD` and `git diff --name-only --diff-filter=ACMR -z HEAD` (uncommitted), de-duplicated, filtered by config `analyze_extensions`.
- Run `format_check_cmd` (if configured), then `analyze_cmd + files` (if configured), then `gate_cmd` (if configured). Fail on first non-zero; print which step failed.

**Acceptance:** test — in a scratch repo, commit a change to a tracked file on a branch off main with a stub `analyze_cmd` that records its argv; `verify-gate` invokes it with that file.

---

#### A6 — [P1] [VERIFIED] Missing `gh` crashes `ledger next`; dependency undocumented

**Where:** `ledger.py` `get_open_prs` (line 66) handles only a non-zero return code, not `FileNotFoundError`; `README.md` says scripts need "only Python 3 and git".

**Evidence:** without `gh` on PATH, `ledger.py next --stream 1` dies with a `FileNotFoundError` traceback.

**Fix:** catch `FileNotFoundError` and non-zero exits; print `[warn] gh not available; skipping open-PR collision check` once; continue. Add `--require-gh` to make that fatal. Document `gh` (optional but recommended) in README and in a `compatibility` note.

**Acceptance:** test — with `gh` absent from PATH, `next` completes and warns.

### B. Ledger (`scripts/autonomous/ledger.py`, `assets/AUTONOMOUS_WORK_LEDGER.template.md`)

Ledger row schema in V1 (8 columns): `Date | Stream | Target | Content Hash | Status | PR | Summary | Re-Audit Trigger`. `record` inserts new rows **at the top** of the table (newest first).

#### B1 — [P0] [VERIFIED] After a re-audit, the parser keeps the oldest row

**Where:** `parse_audited_targets` (line 79). It iterates table rows top-to-bottom and does `audited[target] = hash` for every COMPLETED/SOUND row, so later (= older) rows overwrite earlier (= newer) ones.

**Evidence / repro:**
```bash
# repo with ledger + src/app.py
ledger.py record --stream "Stream A (Mutation)" --target src/app.py --status COMPLETED --pr "#1" --summary first
echo 'print(2)' > src/app.py && git commit -qam change
ledger.py record --stream "Stream A (Mutation)" --target src/app.py --status COMPLETED --pr "#2" --summary second
# ledger now has: row "second" (new hash d0e0fd661) above row "first" (old hash b917a726c)
python3 -c "import ledger; print(ledger.parse_audited_targets()['src/app.py'])"   # → b917a726c  (WRONG; current hash is d0e0fd661)
```
**Why it matters:** the headline feature (content-hash dedup) breaks on the second audit of any file. `next` compares the current hash to the *old* recorded hash, concludes "changed since audit", and re-proposes the file forever, producing duplicate PRs.

**Fix:**
- Define the semantics explicitly in `references/02-ledger-and-dedup.md`: *for each target, the authoritative row is the most recent one*; ordering = (Date desc, then file position top-to-bottom, since `record` inserts at top).
- Implement: parse all rows into a list preserving position; for each target keep the row with the greatest `(date, -position)`. The simplest correct implementation given top-insertion is "first occurrence wins" (`if target not in audited`), but write it via the explicit ordering so hand-edited ledgers (rows appended at the bottom with a newer date) still work.
- Only `COMPLETED` and `SOUND` rows count as audited. A newer row of another status (e.g. `ABANDONED`, if you add it) does not erase an older audited row unless the spec says so — decide, document, test.

**Acceptance:** regression test reproducing the repro above: after the second `record`, `parse_audited_targets()['src/app.py']` equals the *new* hash and `next` does not list the file while its hash is unchanged.

---

#### B2 — [P1] [VERIFIED] `next --stream 2` is a silent no-op; stream naming is inconsistent

**Where:** `cmd_next(stream: int)` (line 97) only implements stream 1. `SKILL.md` names streams **A/B/C/D**; the CLI takes `--stream 1`; `record --stream` takes free text like `"Stream A (Mutation)"`.

**Evidence:** `ledger.py next --stream 2` prints nothing and exits 0 (verified with a stub `gh`).

**Fix:**
- Canonical stream ids: `A`, `B` (accept `1`/`2` as aliases for compatibility). `C`/`D` → exit 2 with `stream C is planned, not implemented`.
- `record --stream` accepts the same ids and stores a normalized label (e.g. `A (Mutation)`).
- `next` enumerates candidates from config (`candidates.<stream>.include/exclude` globs via `git ls-files`):
  - Stream A candidates = source files (config `candidates.A`), requiring a mapped test file to exist (via `test_for_source`, §4.2) unless `require_test: false`.
  - Stream B candidates = test files (`candidates.B`).
- Order: configured `priority_paths` first, then lexicographic. Provide `--limit N` (default 1) and `--json`.
- Unknown/unsupported input must **never** exit 0 silently.

**Acceptance:** tests — `next --stream B` returns a test file; `next --stream C` exits 2 with a message; `next --stream 1` ≡ `--stream A`.

---

#### B3 — [P1] [VERIFIED] Open-PR collision check uses substring matching

**Where:** `cmd_next`: `in_pr = any(slug in branch …) or slug in open_pr_text` (comments claim "exact slug matching … not substring"). It also calls `gh pr list` twice.

**Evidence:** a PR titled "test: harden chess_fsrs_scheduler" on branch `test/chess-fsrs-scheduler` makes both `chess_fsrs_scheduler` **and** `scheduler` report as "has an open PR" (checked directly: `scheduler in text` → True).

**Fix:**
- One `gh pr list --state open --json number,title,headRefName,files --limit 200` call (verify the `files` field is available in the installed `gh`; run `gh pr list --json` with no argument to see the field list). If `files` is unavailable, fall back to branch-name token matching.
- Primary rule — **file overlap**: a candidate target is "claimed" if its path appears in any open PR's `files[].path`. This is exact and slug-free.
- Fallback rule — token match on branch names only: normalize `_`→`-`, lowercase; match `(^|[/-])<slug>([/-]|$)`. Do **not** search free text of titles/bodies.
- Report *which* PR claimed the target (`#123 branch-name`) so a human can sanity-check.

**Acceptance:** tests (stub `gh` output) — PR touching `lib/a/chess_fsrs_scheduler.dart` does not claim `lib/b/scheduler.dart`; does claim the first; branch-only fallback matches whole tokens only.

---

#### B4 — [P1] [VERIFIED] A `|` in a summary breaks the table row

**Where:** `cmd_record` (line 149) writes the summary raw into a `|`-delimited row; mutation summaries naturally contain `||`.

**Evidence:** `--summary 'swapped a || b to a && b: 1 gap'` produced a row that splits into 10 cells instead of 8, shifting the Re-Audit Trigger column.

**Fix:** escape on write, split on unescaped pipes on read. Tested helpers:
```python
import re
def esc(s):  return s.replace('\\','\\\\').replace('|','\\|').replace('\n',' ')
def split_row(line):
    cells = re.split(r'(?<!\\)\|', line.strip())[1:-1]
    return [c.strip().replace('\\|','|').replace('\\\\','\\') for c in cells]
```
Use `split_row` everywhere rows are parsed. Also collapse newlines/CR in summaries.

**Acceptance:** round-trip test — a summary containing `||`, `|`, and a backslash survives `record` → parse unchanged; row has exactly 8 cells.

---

#### B5 — [P1] [VERIFIED] `record` fails if a formatter rewrites the separator row

**Where:** `cmd_record` looks for the exact string `|---|---|---|---|---|---|---|---|\n`.

**Evidence:** after a markdown formatter (e.g. Prettier) rewrites it as `| --- | --- | … |`, `record` prints `Could not locate ledger table header`.

**Fix:** locate the header with a regex that accepts any spacing/alignment colons, e.g.
`^\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$`, and insert immediately after the first match following a header row that starts with `| Date`. Preserve the file's existing line endings.

**Acceptance:** tests for `|---|…|`, `| --- | … |`, `| :--- | ---: | … |`.

---

#### B6 — [P1] [VERIFIED] `--pr` is required even for `SOUND`

**Where:** `main()` declares `--pr` with `required=True`.

**Evidence:** `record … --status SOUND --summary "no gaps"` → `error: the following arguments are required: --pr`.

**Fix:** `--pr` optional, default `—` (em dash). `--status` gets `choices=["COMPLETED","SOUND"]` (see B7 for other statuses). Require `--pr` only when `--status COMPLETED` **and** the stream's config says `pr_required: true` (default true for COMPLETED, false for SOUND).

**Acceptance:** `record --status SOUND` without `--pr` succeeds; `record --status COMPLETED` without `--pr` fails with a clear message.

---

#### B7 — [P2] [READ] `IN_PROGRESS` is documented but cannot work

**Where:** `references/02-ledger-and-dedup.md` lists an `IN_PROGRESS` status; the parser ignores it.

**Why:** the ledger is a git-tracked file that only changes when a branch merges. A claim written in one agent's worktree is invisible to every other agent until merged, so an in-file claim cannot deduplicate parallel work. (The open-PR check in B3 is what actually prevents collisions.)

**Fix:** remove `IN_PROGRESS` from the schema and docs. Replace with the practical rule in `SKILL.md`/ref 02/ref 04: **open a draft PR early** (an empty or WIP commit is enough) to claim a target; B3's file-overlap check then protects it. Valid statuses become exactly `COMPLETED` and `SOUND`.

**Acceptance:** grep shows no remaining `IN_PROGRESS` in V2; docs describe the draft-PR claim.

---

#### B8 — [P1] [READ] Dedup hashes only the source file

**Why it matters:** a Stream A result depends on *both* the source and its test file. If the test file changes (tests weakened, deleted, or rewritten), the old audit is stale, but the ledger still says "unchanged → skip".

**Fix:**
- Store a composite hash in the existing column as `<src9>:<test9>` (9-char `git` hashes, as today). No schema change.
- `record` takes `--test PATH` (default: mapped from config via `test_for_source`); computes both hashes; if no test exists, stores `<src9>:-`.
- `next`/`check` consider a target audited only if **both** halves match. Legacy rows (no `:`) match on the source half only and are flagged `legacy` in `--json` output, so the agent can choose to re-audit them.
- Format is `<target9>:<companion9>`. For Stream A the target is the source file and the companion is its mapped test (or `-`). For Stream B the target is the test file and the companion is always `-`. Make the parser tolerant of rows with no `:` (legacy).

**Acceptance:** tests — changing only the test file makes `next` list the source file again; changing nothing keeps it skipped; legacy single-hash rows still match on source hash.

---

#### B9 — [P2] [READ] Hashing and paths depend on cwd; dirty tree is not detected

**Where:** `get_file_content_hash` (line 46) prefers `git rev-parse HEAD:<path>` but checks `os.path.exists(path)` relative to the cwd; `LEDGER_PATH` is derived from `__file__` (line 32).

**Fix:**
- Resolve the repo root once: `git rev-parse --show-toplevel`; make all target paths repo-root-relative; run git commands with `cwd=<root>`.
- Ledger path comes from config `ledger_path` (default `docs/AUTONOMOUS_WORK_LEDGER.md`) or `--ledger`, resolved against the repo root — never from `__file__` (see also E9).
- Hash semantics (document in ref 02): "hash of the blob at HEAD". If the working-tree file differs from HEAD (`git diff --quiet -- <path>` non-zero), `record` warns (or fails with `--strict`) because the recorded hash would not describe what was audited.

**Acceptance:** tests — running from a subdirectory yields the same results; `record` on a dirty target warns.

---

#### B10 — [P2] [VERIFIED fix] Parallel agents conflict on ledger edits

**Why:** every `record` inserts at the same spot (right under the header), so two branches both inserting there conflict at merge time.

**Fix (tested):** add a git merge driver for the ledger. Ship `assets/gitattributes.fragment.txt` containing
```
docs/AUTONOMOUS_WORK_LEDGER.md merge=union
```
and document it in ref 02 (and `README`). Verified: two branches each inserting a row directly under the table header merge cleanly with `merge=union`, keeping both rows. Caveats to document: union merge keeps lines from both sides, so rows must stay append-only (never edit an existing row; supersede with a new row — which B1's newest-wins rule supports); and if the repo protects the ledger path with a gate (ref 06), the fragment and the protected-path fragment must be applied together.

**Acceptance:** doc + asset present; a scripted test (two branches, merge, assert both rows present, no conflict markers).

---

#### B11 — [P3] [READ] The template's example row is treated as real data

**Where:** `assets/AUTONOMOUS_WORK_LEDGER.template.md` contains a sample row with status `COMPLETED` and target `src/example/service.ts`; the parser accepts it.

**Fix:** give the sample row status `EXAMPLE` (ignored by the parser) or move it into an HTML comment. Say in the template: "delete this line".

---

#### B12 — [P3] [READ] `record` accepts duplicates and nonexistent targets

**Fix:** refuse (exit 1) if (a) the target path does not exist at HEAD, or (b) an authoritative row already exists with the same composite hash and a COMPLETED/SOUND status — unless `--force`. Print the existing row.

---

### C. Mutation engine (`scripts/autonomous/mutation_engine.py`)

#### C1 — [P1] [VERIFIED] "Language-agnostic" is false; rules are Dart-only

**Where:** `SKILL.md` Assets table and `references/01-mutation-engine.md` line 3 claim a "language-agnostic" engine with "per-language tuning noted in header". `MUTATION_RULES` contains Dart idioms (`.isEmpty`/`.isNotEmpty`, `.first`/`.last`, `true`/`false`, `== null`), and `_is_compile_failure` uses Dart markers. `SKILL.md` Stack Adapters lists Python and JS/TS.

**Evidence:** on this Python file the engine produced **zero** mutants:
```python
def f(a, b):
    if a is not None and b:
        return True
    return a in b or not a
```
(no rules exist for `is not None`, `and`/`or`, `True`/`False`, `in`, `not`).

**Fix:**
- Split rules into per-language **rule packs** (`scripts/autonomous/mutation_rules/<lang>.json` or Python modules), selected by file extension, overridable with `--lang`. Each pack defines: file extensions, comment token(s), rules `[{pattern, replacement, description}]`, and compile-failure patterns (see C2).
- Ship packs for `dart`, `python`, `typescript` (also `javascript`), `go`. A Rust/Java pack is optional.
- Minimum Python pack: `==`↔`!=`; `<`,`<=`,`>`,`>=` boundary swaps; `and`↔`or`; `True`↔`False`; `is None`↔`is not None`; `in`↔`not in`; `+ 1`/`- 1` constant tweaks; leading `not ` removal. TS/JS: `===`/`!==`/`==`/`!=`, `&&`↔`||`, `true`/`false`, `??` removal, relational swaps. Go: `==`/`!=`, `&&`/`||`, relational swaps, `true`/`false`, `nil` checks.
- Unknown extension → exit 2 with the supported list (never "0 mutants, success").
- Fix wording in `SKILL.md`, ref 01, README: "regex-based, per-language rule packs".
- Argparse text becomes language-neutral.

**Acceptance:** tests — the Python snippet above yields ≥ 5 mutants (each of: `is not None`→`is None`, `and`→`or`, `True`→`False`, `in`→`not in`, `or`→`and`); unknown extension exits 2.

---

#### C2 — [P1] [VERIFIED] Compile-failure detection misclassifies real test failures

**Where:** `_is_compile_failure` (line 111) checks whether markers such as `"error: "` appear **anywhere** in the combined output.

**Evidence:** both of these ordinary *test-failure* outputs are classified as compile failures (UNCOMPILABLE) and dropped from the kill-rate denominator:
```
00:01 +0 -1: t [E]
  Expected: <3>
    Actual: <4>
  Unhandled error: Bad state
```
```
FAILED t.py::test_x - AssertionError
last error: connection refused
```
Real kills are silently excluded, skewing the score.

**Fix:** anchored, per-language regexes with `re.MULTILINE`, defined in each rule pack. Tested starting points (verify against real toolchain output and adjust):
```python
PATS = {
 "dart":   r'^\S+\.dart:\d+:\d+: Error: ',
 "python": r'^\s*(SyntaxError|IndentationError|TabError):',
 "ts":     r'\berror TS\d+:',
 "go":     r'^\S+\.go:\d+:\d+: ',
 "rust":   r'^error(\[E\d+\])?: ',
}
```
(Both false-positive samples above → `False`; a real Dart/Python/TS compile error → `True`.) Also treat the case "exit code non-zero **and** no test-runner summary line" as suspicious but still count as killed unless an anchored pattern matches. Document that classification is heuristic and that UNCOMPILABLE counts are reported separately.

**Acceptance:** unit tests with the five samples above (2 negative, 3 positive) per language.

---

#### C3 — [P1] [VERIFIED] Duplicate mutants and generics noise

**Evidence:** for `if (x == null) return false;` the engine emits **two identical** mutants (`x != null`), from the generic `==` rule and the `== null` rule, consuming `--max-mutants` budget and double-counting. For `Future<int> foo()` / `List<int> xs` the `<` rule produces `List <= int>` — uncompilable noise.

**Fix:**
- De-duplicate: `list(dict.fromkeys((line_no, mutated_text) …))`.
- Specific-before-generic ordering plus de-dupe; additionally skip the generic `==` rule when an operand is the literal null/None/nil.
- Relational operators only when **surrounded by whitespace** and not part of `=>`, `->`, `<=`/`>=` partials. Tested:
```python
REL = re.compile(r'(?<=\s)(<=|>=|<|>)(?=\s)')
# matches: "if (a < b)", "if (n >= 3)"
# no match: "List<int> xs", "Map<String, List<int>> m", "x => y", "a -> b", "Future<void> f()"
```
**Acceptance:** tests — the `== null` line yields exactly one mutant; `List<int> xs = [];` yields none; `if (a < b)` yields the boundary mutants.

---

#### C4 — [P1] [READ; fix tested] In-place mutation is not crash-safe

**Where:** `run_mutation_pass` (line 167) overwrites the real source file with each mutant and restores it in a `finally`.

**Why it matters:** `finally` does not run on SIGKILL, and Python's default SIGTERM handling terminates without running it. Unattended runs get killed (quota deaths, timeouts, the very scenario in refs 05/06), leaving a **mutated source file on disk** — which could be committed. Two shards mutating the same file in one worktree corrupt each other.

**Fix (tested):** a guard that (1) refuses to start on a dirty source file unless `--allow-dirty`; (2) writes a sidecar backup `<file>.mutbak` and a pid lockfile `<file>.mutlock`; (3) installs SIGTERM/SIGINT/SIGHUP handlers that restore and exit; (4) on the *next* run, auto-recovers from a stale backup whose lock pid is dead, and refuses if the lock pid is alive. Verified behavior: SIGTERM → file restored, no leftovers; SIGKILL → file left mutated *with* sidecars, next run restores it ("restored … from stale backup"). Reference implementation:
```python
import atexit, os, signal, sys
from pathlib import Path

def _pid_alive(pid):
    try: os.kill(pid, 0); return True
    except ProcessLookupError: return False
    except PermissionError: return True

def recover_stale(path):
    p = Path(path); bak = Path(str(p)+".mutbak"); lock = Path(str(p)+".mutlock")
    if not bak.exists(): return False
    if lock.exists():
        try:
            if _pid_alive(int(lock.read_text().strip())):
                raise RuntimeError(f"another mutation run owns {p}")
        except ValueError:
            pass
    p.write_bytes(bak.read_bytes()); bak.unlink(); lock.unlink(missing_ok=True)
    print(f"[!] restored {p} from stale backup", file=sys.stderr); return True

class SourceGuard:
    def __init__(self, path):
        self.path = Path(path); self.bak = Path(str(path)+".mutbak"); self.lock = Path(str(path)+".mutlock"); self._prev = {}
    def __enter__(self):
        recover_stale(self.path)
        self.bak.write_bytes(self.path.read_bytes()); self.lock.write_text(str(os.getpid()))
        for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            self._prev[s] = signal.signal(s, self._on_signal)
        atexit.register(self.restore); return self
    def _on_signal(self, signum, frame):
        self.restore(); sys.exit(128+signum)
    def restore(self):
        if self.bak.exists():
            self.path.write_bytes(self.bak.read_bytes()); self.bak.unlink(); self.lock.unlink(missing_ok=True)
    def __exit__(self, *exc):
        self.restore()
        for s, h in self._prev.items(): signal.signal(s, h)
        return False
```
Additional requirements:
- Run each test command in its own process group (`start_new_session=True`) and have the signal handler kill that group, so a killed engine does not leave orphan test runners.
- Add `.mutbak`/`.mutlock` to the ignore guidance (README / `.gitignore` fragment) and to `verify-gate`'s pre-commit sanity check: fail if any `*.mutbak` exists.
- **Sharding:** add `--shard i/N` (deterministic partition of the de-duplicated mutant list, see C5). Document that shards must each run in a **separate worktree/checkout**; the lockfile makes accidental same-file concurrency fail loudly.
- Print a recovery hint at start: `if interrupted: git checkout -- <file>`.

**Acceptance:** tests — (a) SIGTERM during a run leaves the original bytes and no sidecars; (b) after SIGKILL, the next run restores and reports it; (c) a live lock owned by another pid aborts with a clear message; (d) dirty source refuses without `--allow-dirty`.

---

#### C5 — [P2] [READ] `--max-mutants` takes the first N

**Why:** the first N mutants are all near the top of the file; deep code is never sampled.

**Fix:** after de-dupe, if the count exceeds `--max-mutants`, sample with `random.Random(seed).sample(...)`, then sort by line; `--seed` (default: fixed, e.g. 0 — record the seed in JSON output) so runs are reproducible and shardable. Prefer even coverage: optional `--spread` that takes at most K mutants per line before sampling more.

**Acceptance:** same seed → same selection; selection includes lines from the last third of a 300-line fixture.

---

#### C6 — [P2] [VERIFIED] Docs promise diff-scoped mutation; the engine cannot do it

**Where:** `SKILL.md` line 50 and `references/01-mutation-engine.md` line 34 say the engine is promoted to "a diff-scoped blocking gate". There is no such option (`grep 'diff\|--lines\|--base'` finds nothing in the CLI).

**Fix:** add `--diff-base <ref>`: only mutate lines added/changed in `git diff -U0 <ref>...HEAD -- <source>`. Tested hunk parser:
```python
H = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', re.M)
lines = set()
for m in H.finditer(diff_text):
    start = int(m.group(1)); cnt = 1 if m.group(2) is None else int(m.group(2))
    lines.update(range(start, start+cnt))        # cnt == 0 → pure deletion → nothing
```
(Verified on a file with one replaced line, a 2-line deletion and an appended line → `[5, 19]`.) Also add `--lines A-B` for manual scoping.

**Acceptance:** test — with a base ref and a one-line change, only mutants on that line are generated.

---

#### C7 — [P2] [READ] Timeouts count as kills; baseline timeout is the same as the per-mutant timeout

**Where:** `_run_test_cmd` (line 95) returns rc=1 on timeout; `FileNotFoundError` returns rc=2 — both end up as "killed".

**Fix:**
- Result states: `KILLED`, `SURVIVED`, `UNCOMPILABLE`, `TIMEOUT`. Report TIMEOUT separately and (by default) count it as killed but list the count, with `--timeout-counts-as {killed,survived,excluded}`.
- `FileNotFoundError`/exec failure → abort the whole run with a clear error (it means the test command is broken, not that a mutant was killed).
- Separate `--baseline-timeout` (default e.g. 600 s) from per-mutant `--timeout` (default 60 s): cold Flutter/TS test startup can exceed 60 s and make the baseline fail spuriously.

---

#### C8 — [P3] [READ] Exit codes conflate "found work" with "failure"

**Where:** `sys.exit(1 if survived else 0)`; baseline failure exits 2.

**Fix:** exit 0 on any completed run; `--fail-on-survivors` restores the gate behavior (exit 1). Use the shared exit-code table in §4.3 (2 = usage/unsupported input, 3 = environment failure including a failing baseline — V1 used 2 for a failing baseline, so call out the change). Document in ref 01.

---

#### C9 — [P3] [READ] Comments and multi-line strings not guarded

**Where:** `generate_mutations` skips whole lines that *start* with `//`/`@`/quotes and uses a quote-count heuristic, but trailing comments (`x == y // a == b`) and continuation lines of multi-line strings are still mutated.

**Fix:** use the pack's comment token to cut the line at the first comment marker outside a string before matching; skip matches inside the comment segment; for languages with triple-quoted/multi-line strings, track a simple in-string state across lines (Python `'''`/`"""`, JS template literals, Dart `'''`). Keep it heuristic and documented.

---

#### C10 — [P3] [READ] Generated issue text contradicts Law 1; has project-specific references

**Where:** `format_issue_markdown` (line 241): "Verify fail-to-pass proof against origin/main" and references to a project-specific spec/invariants file; docstring references `GATES_PLAN.md row 9`.

**Fix:** wording per E1 — "write a test that is **red with this mutant applied** and green on clean source"; remove project-specific file references or make them config-driven.

---

#### C11 — [P3] [READ] Negative literals never mutate

`x == -1` is not matched because the operand pattern `[a-zA-Z0-9_\.]+` cannot match `-1`. Allow an optional leading `-` on numeric operands.

### D. Watchdog and unattended-operation assets (`assets/watchdog.sh`, `assets/opencode-watchdog.{service,timer}`, `references/05`, `06`)

V1 behavior: a systemd `--user` timer runs `watchdog.sh` every 20 minutes. If a process matching `opencode` exists and the heartbeat file (`last_beat`) is older than 45 minutes, it runs `opencode run --auto -s <session> "<nudge>"` in the foreground. If no such process exists, it runs the same command in the background with `setsid nohup … &`.

#### D1 — [P1] [READ] `setsid nohup … &` inside a `Type=oneshot` unit will probably be killed

**Where:** `watchdog.sh` last branch; `opencode-watchdog.service` is `Type=oneshot` with default `KillMode=control-group`.

**Why:** systemd tracks processes by cgroup, not session. When the oneshot's main process (the script) exits, the unit stops and, with the default `KillMode=control-group`, the remaining processes in its cgroup — including the `setsid`'d child — are killed. `setsid` does not escape the cgroup. The reference's "verified" note covers only the **foreground** nudge branch; the dead-process branch is likely untested.

**Verify first (≈2 minutes):** create `killtest.service` (`Type=oneshot`, `ExecStart=/tmp/t.sh`) where `/tmp/t.sh` runs `setsid nohup sleep 600 >/dev/null 2>&1 &`; `systemctl --user start killtest.service; sleep 1; pgrep -af 'sleep 600'`. No output = the child was killed (bug confirmed). Record the result in `CHANGELOG-v2.md`.

**Fix (preferred — remove the branch):** a single code path. The two V1 branches run the *same command*, and the heartbeat already measures what matters. New logic: if the heartbeat is fresh → exit; otherwise → run one foreground nudge (see D2/D3). This removes `pgrep`, the background branch, and the cgroup problem together. State the assumption in ref 05: *a headless `<resume-cmd>` turn exits when the turn ends*. If the harness instead leaves a long-lived process, launch it via `systemd-run --user --collect --unit=orch-revive-$(date +%s) <cmd>` (its own cgroup) or set `KillMode=process` in the unit.

---

#### D2 — [P1] [READ] The foreground nudge has no timeout and nothing prevents overlap

**Why:** `opencode run …` can hang (provider throttling, the very failure being recovered from). A `Type=oneshot` unit has no start timeout by default, so a hung nudge keeps the unit "activating" forever and the timer cannot start new runs. Overlapping runs are also possible if a nudge takes longer than the 20-minute interval.

**Fix:**
- Wrap the call: `timeout --signal=TERM --kill-after=60 "${ORCH_NUDGE_TIMEOUT:-1800}" …`.
- Take a non-blocking lock at the top: `exec 9>"$STATE_DIR/watchdog.lock"; flock -n 9 || { echo "previous run active" >> "$LOG"; exit 0; }`.
- Add `TimeoutStartSec=2h` to the service as a backstop.
- Log the nudge's exit status (124 = timed out).

---

#### D3 — [P2] [READ] `pgrep -f "opencode"` is too broad (and unnecessary after D1)

**Why:** it matches any process whose command line contains "opencode": another terminal, an editor tab, `tail -f opencode.log`. A second unrelated instance makes the watchdog believe the orchestrator is alive; a script installed under a path containing "opencode" could match itself. Resolved by D1's single-path design (drop `pgrep`). If you keep any liveness check, match on the session id in argv *only if* the harness actually exposes it there; otherwise rely on the heartbeat alone.

---

#### D4 — [P2] [READ] Install steps are incomplete

**Where:** `watchdog.sh` header "Install" list; ref 05.

**Missing:** copying `watchdog.sh` to `~/.local/share/orchestrator/` and `chmod +x`; copying the units to `~/.config/systemd/user/`; `systemctl --user daemon-reload && systemctl --user enable --now <name>.timer`; `loginctl enable-linger "$USER"` (needed for user timers to run while logged out — which is the whole "owner away" scenario); verification (`systemctl --user list-timers`, `journalctl --user -u <name>`); uninstall.

**Fix:** ship `assets/install-watchdog.sh` (and `--uninstall`) that does these steps idempotently, runs `systemd-analyze --user verify` on the units, and **prints** (does not run) the `enable-linger` command if it needs privileges. Document in ref 05. Keep `ORCH_STATE_DIR` as the single configuration point.

---

#### D5 — [P2] [READ] Harness-specific, but described as generic

**Where:** unit names `opencode-watchdog.*`, hardcoded `opencode run --auto -s`, ref 05/06 vocabulary (`opencode.json`, "free models", attribution footers). `README` lists Claude Code as supported; none of this works there.

**Fix:** make the resume command configurable: `ORCH_RESUME_CMD` (a string run via `bash -c "$ORCH_RESUME_CMD" _ "$SID" "$NUDGE_MSG"`, so the command refers to `$1`/`$2` — no `eval` of interpolated text). Default for opencode: `opencode run --auto -s "$1" "$2"`. Document how to adapt to other harnesses ("use your harness's headless resume-session flag; check its docs for the exact flags — do not copy flags from this skill"). Rename units to `orchestrator-watchdog.*`. Mark opencode-specific parts of refs 05/06 clearly (see E4).

---

#### D6 — [P3] [READ] No blast-radius guidance for unattended `--auto` sessions

The skill's rule "workers create PRs and issues but never merge" is a prompt-level rule only. Add a short "Blast radius" section to ref 05: use a fine-grained token limited to PRs/issues (no merge, no admin); enforce review + required CI via branch protection so a misbehaving agent *cannot* merge; keep secrets out of worktrees; optionally run the unattended session under a dedicated OS user; audit with the journal/log. State that `--auto` approval is a high-trust mode.

---

### E. Skill design, documentation, layout

#### E1 — [P1] [READ] Law 1 contradicts Stream A

**Where:** `SKILL.md` Law 1: "No bugfix or test lands without a test that **fails on `origin/main`** before the fix and passes after." Stream A's output is a test that closes a mutation gap in *correct* code — it **passes on `origin/main` by construction**. The proof is red against the *mutant*. Ref 04's evidence table softens this ("test-only: red→green not required"), but an agent reading `SKILL.md` alone cannot satisfy Law 1 for Stream A.

**Fix:** replace Law 1 (suggested text in Appendix A):
> **1. Fail-to-pass** — No fix or test lands without proof that the new test **fails against the defect** and passes once the defect is addressed. For a bugfix the defect is the bug on `origin/main` (red on main, green after the fix). For a test-only PR that closes a mutation gap the defect is the surviving mutant (red with the mutant applied, green on clean source).

Make the "Evidence" requirements in ref 04, the PR body template, the issue text produced by the engine (C10), and the evals (E7) all use this wording.

---

#### E2 — [P1] [READ] Dangling dependencies

**What is referenced but never defined in the skill:** the companion skill `agent-proof-codebase`; gate ids **G05/G07/G08**; `./scripts/gates.sh t1` and the "T1 fast gates" tier; `GATES_PLAN.md row 9`; `AGENTS.md`/`SPEC.md` conventions; "R0/R1" (ref 03); "ratchets"; `gate-approved` label and `APPROVED=1` (ref 06). In a fresh repo the agent is told to pass gates that do not exist.

**Fix:**
- Add `references/gates-and-vocabulary.md`: define each term in one line, state which are *optional*, and give a **"no gates yet" fallback**: run the project's own CI commands locally on touched files only; run the test-weakening check (below); never block on a missing `gates.sh`.
- Confirm meanings with the author before writing definitions (ask, or read the companion repo if available). From the V1 text: G08 appears to be the test-weakening tripwire (`scripts/gates/test_weakening_check.py`); G07 appears to be a protected-path/append-only check (inferred from ref 06) — **do not guess; verify**.
- Optional but valuable: ship a small stdlib `scripts/autonomous/test_weakening_check.py --base <ref>` that scans `git diff <ref>...HEAD` of test files and fails on removed assertions, newly added skips/ignores (`skip`, `xit`, `@pytest.mark.skip`, `@Skip`, `.only`), deleted test files, and loosened matchers. This makes the "direct tripwire when gates.sh is unavailable" line in the Workflow real.
- Every reference to the companion skill must say what to do if it is not installed.

**Acceptance:** `grep -nE 'G0[0-9]|gates\.sh|GATES_PLAN|agent-proof-codebase|R0|R1|ratchet' SKILL.md references/` → every hit is either defined in the vocabulary file or marked optional with a fallback.

---

#### E3 — [P1] [READ] Description over-reaches

**Where:** frontmatter `description` mentions "property fuzzing, and migration archeology" (Streams C/D — unimplemented; the body says not to run them) and the trigger "improve the repo" is broad for a heavy, opinionated protocol.

**Fix:** rewrite the description (suggested text in Appendix A) to cover only Streams A/B, name concrete triggers ("tests pass but I don't trust them", "mutation testing", "find vacuous/flaky tests", "improve test quality, not coverage"), keep a clear "do not use for feature work, refactors, or one-off bugfixes" clause, and stay within ~100 words. Move Streams C/D to a "Planned (not available)" note at the bottom of `SKILL.md`.

---

#### E4 — [P2] [READ] Two audiences in one skill

**Core audience:** a single agent (or a few) doing Streams A/B with the four laws.
**Fleet audience:** a human running a multi-day orchestrator + workers under quota pressure (refs 05, 06, the watchdog, opencode specifics, attribution footers, "free models").

**Fix:** split into two skills — `autonomous-improvement` (core: laws, streams, scripts, ledger, refs 01–04, 07 if it is mostly about stale-base/Law 3; **assess ref 07 and decide**) and `unattended-orchestration` (refs 05–06, watchdog assets, install script, blast-radius). Cross-link both ways in one line each. Keep `SKILL.md` ≤ ~200 lines. If you decide *not* to split, still mark every opencode-specific passage and move it under a single "Unattended operation (advanced)" reference.

---

#### E5 — [P2] [READ] Workflow block is Dart-specific and duplicated

**Where:** `SKILL.md` lines 75–110 ("Workflow (one task)") include `fvm flutter pub get`, `build_runner`, `dart format lib/l10n/`, `<format-cmd> lib test`, and the `set-url` line (A1). Ref 04 repeats most of the protocol.

**Fix:** rewrite the Workflow block in toolchain-neutral terms driven by config (`$TEST_CMD`, `$FORMAT_CMD`, `$ANALYZE_CMD`, `$GATE_CMD`), referencing `references/stack-adapters.md` for Dart/TS/Python/Go examples (move the "Stack Adapters" section there). Keep **one** canonical per-task protocol (recommend ref 04) and have `SKILL.md` carry a ≤ 12-line summary pointing to it.

---

#### E6 — [P2] [READ] Law 4's rationale is a personal anecdote

**Where:** "…has heated a workstation to hard-reboot territory here before." **Fix:** general rationale (Appendix A). Also make the budget configurable (`machine_budget`: max concurrent test processes, "targeted test first, full-file once, never the full suite") and document that CI is the place for the full run.

---

#### E7 — [P2] [READ] No evals, no tests, no worked examples

**Fix:** add
- `tests/` — stdlib `unittest` suite covering every regression listed in §6 (no network, no `gh`/toolchains required; use stubs and temp git repos).
- `evals/evals.json` (+ fixture repos) — behavior checks for an agent using the skill. Minimum cases:
  1. "improve the repo" on a tiny Python repo with one weak test → agent runs the engine on a named file, writes a test that is **red with the mutant** and green on clean source, records a ledger row, does **not** run the full suite, does **not** touch unrelated files.
  2. Repo with a vacuous test (`assert True`) and a `sleep`-based flaky test → Stream B findings with evidence; no cosmetic edits.
  3. Same file audited twice without changes → second `next` does not re-propose it (B1/B8).
  4. Request to run "property fuzzing" → agent says Stream C is not available and offers A/B (E3).
  5. A target already covered by an open PR → agent skips it (B3).
- `references/examples.md` — one end-to-end Stream A example (mutant → test → proof → ledger row → PR body) and one good vs. bad Stream B finding.

---

#### E8 — [P3] [VERIFIED] Typos and incorrect statements

- `references/06-fleet-and-quota-operations.md` line 37: ``git pr list`` is not a command → `gh pr list`.
- Same file, line 81: "tripis" → "trips".
- `README.md`: "Scripts need only Python 3 and git" → also `gh` (optional), bash + systemd (watchdog only).
- `SKILL.md` Assets table / ref 01: "language-agnostic" (see C1).
- `SKILL.md` Quick Start uses `--stream <n>` (see B2).
Run a spell-check and a link/path check over all docs; every path mentioned must exist.

---

#### E9 — [P3] [VERIFIED] Byte-identical duplicate scripts; top-level copy resolves the ledger wrongly

**Where:** `scripts/{ledger,mutation_engine,orchestrator}.py` are identical to `scripts/autonomous/*.py` (checked with `diff`). `SKILL.md` documents only `scripts/autonomous/…`. `ledger.py` derives `LEDGER_PATH` with three `dirname()` calls from `__file__`: correct for `<repo>/scripts/autonomous/ledger.py`, but run in place from `<skill>/scripts/ledger.py` it points at a `docs/` folder **outside** the skill (verified: resolves to the skill's parent directory).

**Fix:** keep **one** copy under `<skill>/scripts/autonomous/` (it mirrors the install location), delete the top-level duplicates, and resolve the ledger path from the repo root + config (B9), never from `__file__`. Add a `scripts/autonomous/README` line: "copy this folder into `<repo>/scripts/autonomous/`". Optionally ship `install.sh` that does the copy and prints the protected-paths/gitattributes fragments to append.

---

#### E10 — [P3] [READ] README duplicates SKILL.md

Reduce `README.md` to: what it is, install (copy folder / `npx` if applicable), requirements (`git`, `python3 ≥ 3.9`, `gh` optional, systemd for the watchdog), and links. Duplicated content (laws, streams) drifts out of sync.

---

## 4. Target V2 design

### 4.1 Folder layout

```
autonomous-improvement/
├── SKILL.md                       # ≤ ~200 lines; laws, streams A/B, quick start, pointers
├── README.md                      # install + requirements only
├── LICENSE
├── CHANGELOG-v2.md                # problem-ID → resolution map (you produce this)
├── references/
│   ├── 01-mutation-engine.md      # rule packs, statuses, exit codes, seeds/shards, diff-base
│   ├── 02-ledger-and-dedup.md     # schema, newest-wins, composite hash, escaping, merge=union, draft-PR claims
│   ├── 03-streams-and-triage.md
│   ├── 04-workflow-and-roles.md   # the ONE canonical per-task protocol
│   ├── 07-moving-main.md          # (keep or move per E4)
│   ├── gates-and-vocabulary.md    # NEW (E2)
│   ├── stack-adapters.md          # NEW (E5)
│   └── examples.md                # NEW (E7)
├── scripts/autonomous/
│   ├── ledger.py
│   ├── mutation_engine.py
│   ├── mutation_rules/{dart,python,typescript,go}.json
│   ├── orchestrator.py
│   ├── test_weakening_check.py    # optional (E2)
│   ├── install.sh                 # optional (E9)
│   └── config.example.json
├── assets/
│   ├── AUTONOMOUS_WORK_LEDGER.template.md
│   ├── protected-paths.fragment.txt
│   └── gitattributes.fragment.txt # NEW (B10)
├── examples/
│   ├── config.dart.json           # reproduces the author's ChessSRS setup
│   └── config.python.json
├── tests/                         # stdlib unittest (E7)
├── evals/                         # evals.json + fixtures (E7)
└── (unattended-orchestration/ …)  # separate skill if you split (E4): refs 05–06, watchdog, install-watchdog.sh
```

### 4.2 Config file

Default location: `<repo>/scripts/autonomous/config.json` (override: `--config PATH`). All keys optional; a missing key means "skip that step" with a printed `[skip]` note. All commands are **argv arrays**.

```json
{
  "base_ref": "origin/main",
  "ledger_path": "docs/AUTONOMOUS_WORK_LEDGER.md",
  "origin_url": null,
  "setup_cmds": [["python3", "-m", "pip", "install", "-e", ".[dev]"]],
  "format_check_cmd": ["ruff", "format", "--check", "."],
  "analyze_cmd": ["ruff", "check"],
  "analyze_extensions": [".py"],
  "gate_cmd": null,
  "test_cmd": ["python3", "-m", "pytest", "-x", "-q"],
  "test_for_source": [
    { "src": "^src/(.*)\\.py$", "test": "tests/test_\\1.py" }
  ],
  "candidates": {
    "A": { "include": ["src/**/*.py"], "exclude": ["**/__init__.py"], "require_test": true, "priority_paths": [] },
    "B": { "include": ["tests/**/*.py"], "exclude": [] }
  },
  "machine_budget": { "max_concurrent_tests": 1 }
}
```
`examples/config.dart.json` should reproduce V1's behavior for the author's project (fvm/flutter/build_runner/`gates.sh t1`, `lib/**/*.dart` ↔ `test/**/*_test.dart`, excluding `*.g.dart`/`*.freezed.dart`).

### 4.3 CLI contracts (keep V1 subcommand names; add flags)

```
ledger.py next   --stream {A|B|1|2} [--limit N] [--json] [--config P] [--require-gh]
ledger.py record --stream {A|B|1|2} --target PATH --status {COMPLETED|SOUND}
                 [--pr REF] --summary TEXT [--test PATH] [--trigger TEXT] [--force] [--strict]
ledger.py check  --target PATH            # exit 0 if audited at current composite hash, else 1
orchestrator.py create-worktree  --slug S [--type fix|test|chore] [--base REF] [--set-origin --yes]
orchestrator.py verify-gate      --worktree PATH
orchestrator.py cleanup-worktree --slug S [--force] [--delete-branch]
mutation_engine.py --source PATH --test-cmd "CMD" [--lang L] [--max-mutants N] [--seed N]
                   [--shard i/N] [--diff-base REF] [--lines A-B] [--allow-dirty]
                   [--timeout S] [--baseline-timeout S] [--timeout-counts-as killed|survived|excluded]
                   [--fail-on-survivors] [--output-json P] [--output-issue P]
```
Exit codes, shared by all three scripts (document in the references, test them):

| Code | Meaning |
|------|---------|
| 0 | Completed (including "found survivors" unless `--fail-on-survivors`) |
| 1 | Negative result by request (`check` says not audited; `--fail-on-survivors` with survivors) |
| 2 | Usage error or unsupported input (unknown stream, unknown language, bad flags) |
| 3 | Environment failure (required tool missing, baseline tests fail, lock held by another run) |

---

## 5. Suggested implementation order

1. **Safety first (A1, A3, A4):** remove `set-url`, argv runner + slug validation, safe cleanup + registry. Add tests.
2. **Config + de-hardcoding (A2, B9, E9):** config loader, repo-root resolution, delete duplicate scripts, example configs.
3. **Ledger correctness (B1, B4, B5, B6, B8, B12, B11):** newest-wins, escaping, separator regex, optional `--pr`, composite hash. Add tests *before* changing the parser (they should fail on V1).
4. **`next` and PR collision (B2, B3, A6, B7):** config-driven candidates, Stream B, file-overlap check, graceful `gh`.
5. **Orchestrator (A5):** diff-based touched files; gate sequence.
6. **Mutation engine (C1–C11):** rule packs + per-language compile patterns first (C1, C2), then dedupe/generics (C3), `SourceGuard` + sharding (C4, C5), `--diff-base` (C6), statuses/timeouts/exit codes (C7, C8), comments/strings (C9), wording (C10), negatives (C11).
7. **Docs (E1, E2, E3, E5, E6, E8, E10)** after the code is stable, so docs describe what exists.
8. **Watchdog (D1–D6)** and the optional skill split (E4).
9. **Evals and examples (E7).**

---

## 6. Regression tests to write (derived from the repros)

Use `unittest` + `tempfile` + real `git` in temp repos; stub `gh` by putting a fake executable first on `PATH`.

| Test | Asserts | Problem |
|------|---------|---------|
| `test_origin_untouched` | `git remote get-url origin` identical before/after `create-worktree` (success and failure paths) | A1 |
| `test_no_project_strings` | running `next`/`create-worktree` with the Python example config never prints/executes `fvm`, `build_runner`, `ChessSRS` | A2 |
| `test_slug_validation`, `test_filename_with_space` | `a;rm -rf /` and `Has Space` rejected; `my file.dart` passed as one argv element | A3 |
| `test_cleanup_refuses_dirty_unpushed_unregistered` | exit ≠ 0 in each case; `--force` works | A4 |
| `test_verify_gate_after_commit` | analyze stub receives the committed file | A5 |
| `test_next_without_gh` | completes, warns | A6 |
| `test_reaudit_newest_wins` | after 2nd `record` with changed content, parsed hash == new hash; `next` skips it | B1 |
| `test_next_stream_b`, `test_next_stream_c_exit2`, `test_stream_alias` | per B2 | B2 |
| `test_pr_overlap_exact_path`, `test_branch_token_fallback` | `scheduler` not claimed by a `chess_fsrs_scheduler` PR | B3 |
| `test_pipe_roundtrip` | 8 cells; `\|`, `\\` survive | B4 |
| `test_separator_variants` | compact, spaced, and colon-aligned separator rows all work | B5 |
| `test_sound_without_pr` | succeeds; COMPLETED without PR fails | B6 |
| `test_test_file_change_invalidates` / `test_legacy_hash_matches_source` | per B8 | B8 |
| `test_subdir_invocation` | same results from a subdirectory | B9 |
| `test_union_merge_two_branches` | both rows present, no conflict markers | B10 |
| `test_python_mutants` | the 5-construct Python snippet yields ≥ 5 mutants | C1 |
| `test_compile_classifier` | 2 false-positive samples → False; real errors → True (per language) | C2 |
| `test_null_equality_single_mutant`, `test_generics_not_mutated` | per C3 | C3 |
| `test_sigterm_restores`, `test_sigkill_recovery`, `test_live_lock_aborts`, `test_dirty_refused` | per C4 | C4 |
| `test_seeded_sampling_reaches_tail` | per C5 | C5 |
| `test_diff_base_limits_lines` | only the changed line is mutated | C6 |
| `test_timeout_state_and_exec_failure` | TIMEOUT reported separately; missing test binary aborts | C7 |
| `test_exit_codes` | per C8 | C8 |
| `test_trailing_comment_not_mutated` | per C9 | C9 |

Behavioral evals (E7) are separate and run with an agent, not in `unittest`.

---

## 7. Definition of done

- [ ] Every problem ID in §1 is resolved or has an explicit, justified "won't fix" in `CHANGELOG-v2.md`.
- [ ] `python3 -m unittest discover tests` passes on a machine with only `git` and `python3`.
- [ ] `grep -riE 'chesssrs|cassandre|set-url|shell=True' scripts/ SKILL.md README.md references/` → no hits outside `examples/config.dart.json` and docs that present it as an example.
- [ ] `SKILL.md` ≤ ~200 lines; description rewritten (E3); Laws 1 and 4 reworded (E1, E6); no references to undefined gates/skills (E2).
- [ ] Running the Quick Start verbatim in a fresh Python repo with `config.python.json` works end to end: `next` → worktree → mutation run → test → `record` → `next` no longer lists the file.
- [ ] No duplicate script copies; no `__pycache__` or `*.mutbak`/`*.mutlock` in the shipped folder.
- [ ] Docs, CLI help and scripts agree on stream ids, statuses, exit codes, and hash format.
- [ ] A short final report lists anything you verified differently from this document.

---

## Appendix A — Suggested replacement text

**Frontmatter description (E3):**
> Run falsifiable, high-signal improvement cycles on a codebase instead of cosmetic churn — mutation testing to find tests that don't actually protect behavior, and adversarial audits of existing tests (vacuous assertions, flaky sleeps, over-mocking). Use when asked to "improve the repo", "audit the tests", "find weak or flaky tests", "improve test quality, not just coverage", "run mutation testing", or when "tests pass but I don't trust them" — or when an agent has spare budget and needs work that can be proven real (fails without the fix, not already done at this file's content, cheap enough to run locally). Given as a session's only prompt, it turns that session into an orchestrator that dispatches worker subagents instead of building itself (references/04-workflow-and-roles.md). Do not use for feature work, refactors, or one-off bugfixes.

> Note: the em dash after "cosmetic churn" is deliberate. A plain `": "`
> inside an unquoted YAML scalar makes the frontmatter invalid, and the
> `skills` installer then skips the whole skill with a warning instead of
> failing. `scripts/check_skills.py` and `scripts/check_discovery.sh` now
> fail CI on it.

**Law 1 (E1):** see E1 above.

**Law 4 (E6):**
> **4. Machine budget** — Run **one targeted test** at a time (e.g. by test name), one full-file run at the end. Never run the whole suite locally — that is CI's job. One analyze/lint pass per task, on touched files, after the last edit. | A full local run saturates every core for minutes, can throttle or crash a workstation, and duplicates what CI already runs on every push.

**Planned streams note (E3):**
> *Planned, not available:* Stream C (property fuzzing / model checking) and Stream D (migration & data-preservation archeology). Do not start work under these names; offer Streams A/B instead.
