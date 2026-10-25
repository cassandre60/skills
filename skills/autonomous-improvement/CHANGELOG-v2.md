# V2 Changelog — problem-ID resolution map

V2 is a rewrite of the scripts, references, and assets in place. Every problem
in the V2 specification's problem index (§1) is resolved below, with the test
or check that proves it. "Test" names refer to `tests/test_v2.py`.

## Verification summary

- `python3 -m unittest discover -s tests` — **50 tests, OK**.
- D1 experiment: a `Type=oneshot` user unit on systemd 262 ran
  `setsid nohup sleep 600 &`; the child was gone one second after the unit
  stopped. The background branch was removed rather than patched.
- Forbidden-string and layout greps over `scripts/`, `SKILL.md`, `README.md`,
  and `references/` (`chesssrs`, `cassandre`, `set-url`, `shell=True`,
  `opencode-watchdog`, `IN_PROGRESS`, duplicate scripts) — clean. The changelog
  and the spec naturally name the old strings when describing them.
- `SKILL.md` is under 200 lines; the frontmatter description covers Streams A/B
  only.
- Quick Start was exercised end to end in a fresh Python repo (see
  "Quick Start verification").

## A — Orchestrator and portability

| ID | Resolution | Evidence |
|---|---|---|
| A1 | Deleted `REPO_ORIGIN` and all automatic remote rewriting. Worktrees branch from config `base_ref` after `git fetch origin <branch>`. Remote repair is opt-in only: `origin_url` + `--set-origin --yes`, which prints the old/new URL first. | `test_origin_untouched_on_success_and_failure` |
| A2 | All project values moved to `config.json`: candidate globs, test mapping, setup/format/analyze/gate commands, ledger path. Worktree name is `<repo-dir>-<slug>`. Scripts and argparse help are language-neutral. `examples/config.dart.json` isolates the author's Dart setup. | `test_no_project_strings`, `test_pr_overlap_exact_path_and_branch_fallback`, forbidden-string grep |
| A3 | `common.run()` and the engine run argv lists only; no `shell=True`. Slugs are validated with `^[a-z0-9][a-z0-9._-]{0,63}$`. File lists come from NUL-delimited `git diff`/`git ls-files`, not whitespace splitting. | `test_slug_validation`, `test_filename_with_space_passed_as_one_argv` |
| A4 | Added `<git-common-dir>/autonomous-worktrees.json` registry. Cleanup refuses unregistered paths, dirty trees, and unpushed commits; `--force` prints what it discards; `--delete-branch` is explicit. | `test_cleanup_refuses_dirty_unpushed_unregistered`, `test_clean_pushed_worktree_cleans_up` |
| A5 | `verify-gate` unions committed (`base...HEAD`) and uncommitted changes, filters by `analyze_extensions`, and runs format → analyze + files → gate. It also rejects `*.mutbak`/`*.mutlock` sidecars, including ignored ones. | `test_verify_gate_after_commit`, `test_verify_gate_rejects_sidecars`, `test_verify_gate_skips_unconfigured_steps` |
| A6 | `gh` failures (missing tool, no remote, old `files` field, bad JSON) degrade to one warning and continue. `--require-gh` makes it fatal (exit 3). README documents `gh` as optional. | `test_next_without_gh`, `test_require_gh_rejects_bad_payload`, `test_pr_overlap_exact_path_and_branch_fallback` |

## B — Ledger

| ID | Resolution | Evidence |
|---|---|---|
| B1 | Authoritative row is `max(Date, -position)` — newest date, topmost on a tie. New rows are inserted under the header. | `test_reaudit_newest_wins` |
| B2 | Canonical streams A/B with `1`/`2` aliases. C/D exit 2. `record` stores `A (Mutation)` / `B (Test Audit)`. Candidates come from config globs via `git ls-files`, with `--limit` and `--json`. | `test_next_stream_b_c_and_alias`, `test_next_without_gh` |
| B3 | One `gh pr list` call with `files`; exact path overlap is primary. If `files` is unavailable, the fallback matches whole normalized branch segments, never substrings or titles. See deviation note below. | `test_pr_overlap_exact_path_and_branch_fallback` |
| B4 | `esc()`/`split_row()` round-trip `|`, `\`, and newlines; rows stay eight cells. | `test_pipe_roundtrip` |
| B5 | Separator regex accepts compact, spaced, and colon-aligned rows; `record` preserves LF/CRLF. | `test_separator_variants` |
| B6 | `--pr` is optional and defaults to `—`; it is required only for `COMPLETED` (config `pr_required`). | `test_sound_without_pr_and_completed_requires_pr` |
| B7 | `IN_PROGRESS` removed from schema, docs, and parser. Claim work by opening a draft PR early. | Documentation invariant test; grep |
| B8 | Composite `<source9>:<companion9>`; Stream A maps the companion test, Stream B uses `-`. Legacy single-hash rows match the source half and are flagged `legacy`; a changed source invalidates them like any other row. | `test_test_file_change_invalidates`, `test_legacy_hash_matches_source`, `test_changed_source_invalidates_legacy_hash` |
| B9 | Repo root comes from `git rev-parse --show-toplevel`; target paths are repo-root-relative; ledger path is config/`--ledger` against the root; dirty targets warn or fail with `--strict`. | `test_subdir_invocation`, `test_record_dirty_warns_and_strict_fails` |
| B10 | Shipped `assets/gitattributes.fragment.txt` (`merge=union`) and documented append-only rows. | `test_union_merge_two_branches` |
| B11 | Template example row has status `EXAMPLE`, which the parser ignores. | `test_template_example_is_ignored` |
| B12 | `record` rejects targets absent at HEAD and duplicate authoritative rows at the same composite hash unless `--force`. | `test_record_rejects_duplicates_and_missing_targets` |

## C — Mutation engine

| ID | Resolution | Evidence |
|---|---|---|
| C1 | JSON rule packs for Python, Dart, TypeScript, JavaScript, and Go, selected by extension or `--lang`; unknown input exits 2. Packs cover the required operators. | `test_python_mutants`, `test_exit_codes` |
| C2 | Anchored, per-language compile-error patterns with `re.MULTILINE`; ordinary assertion output is not misclassified. UNCOMPILABLE is excluded from the kill rate. | `test_compile_classifier` |
| C3 | Mutants de-duplicated by `(line, mutated_text)`; specific null rules run before generic equality; relational operators require whitespace, so generics are untouched. | `test_null_equality_single_mutant`, `test_generics_not_mutated` |
| C4 | `SourceGuard` writes `.mutbak`/`.mutlock`, restores on SIGTERM/SIGINT/SIGHUP, kills the test process group, recovers stale backups after SIGKILL, and aborts on a live pid lock. Dirty sources are refused unless `--allow-dirty`. `--shard i/N` is partitioned before sampling. | `test_sigterm_restores_source`, `test_sigkill_recovery`, `test_live_lock_aborts`, `test_dirty_source_refused`, `test_shards_partition_mutants` |
| C5 | Seeded `random.Random(seed).sample`, sorted by line; optional `--spread`; seed recorded in JSON. | `test_seeded_sampling_reaches_tail` |
| C6 | Added `--diff-base` (hunk parser) and `--lines`. | `test_diff_base_limits_lines` |
| C7 | Four states; timeouts reported separately and configurable via `--timeout-counts-as`; separate `--baseline-timeout`; missing executable aborts as environment failure. | `test_timeout_state_and_missing_executable` |
| C8 | Exit 0 on completion, 1 only with `--fail-on-survivors`, 2 usage, 3 environment (including red baseline and live lock). Documented in ref 01. | `test_exit_codes`, `test_live_lock_aborts` |
| C9 | Per-line lexer masks strings, triple-quoted/multiline strings, line comments, and block comments before applying rules. | `test_trailing_comment_and_multiline_string_guarded` |
| C10 | Issue text uses the Law 1 mutant wording and contains no project-specific files. | `format_issue_markdown`; inspection |
| C11 | Operand patterns accept an optional leading `-` on numeric literals. | `test_negative_literal_mutates`, `test_no_space_equality_mutates` |

## D — Watchdog and unattended operation

| ID | Resolution | Evidence |
|---|---|---|
| D1 | Removed the background branch entirely; one foreground nudge path remains. D1 was verified experimentally (see above). | systemd 262 experiment; `WatchdogTests` |
| D2 | Non-blocking `flock`, `timeout --signal=TERM --kill-after=60`, exit status logged (124 = timeout), unit `TimeoutStartSec=2h`. | `bash -n`; `test_fresh_heartbeat_skips_and_stale_runs_nudge` |
| D3 | Removed `pgrep`; liveness is the heartbeat alone. | code inspection; watchdog test |
| D4 | Added assets/install-watchdog.sh with idempotent install, `systemd-analyze --user verify`, enable/start, linger printout, list-timers, logging, and `--uninstall` (state preserved). Ref 05 has manual equivalents. | `bash -n`; `systemd-analyze --user verify` against a staged install |
| D5 | Units renamed `orchestrator-watchdog.{service,timer}`; `ORCH_RESUME_CMD` is configurable and invoked via `bash -c` with `$1`/`$2` (no `eval`); opencode material is marked harness-specific. | code + ref 05 |
| D6 | Added a "Blast radius" section: scoped token, branch protection, no secrets, dedicated user, audit trail, high-trust warning. | ref 05 |

## E — Skill design and documentation

| ID | Resolution | Evidence |
|---|---|---|
| E1 | Law 1 now distinguishes bugfix (red on main) from test-only mutation work (red with mutant, green on clean source). Ref 04, issue text, and evals use the same wording. | SKILL.md; refs 01/03/04; examples |
| E2 | Added `references/gates-and-vocabulary.md` defining every previously dangling term as optional, with a "no gates yet" fallback. Added `test_weakening_check.py`. All references now say what to do without the companion skill. | grep; `TestWeakeningTests` |
| E3 | Description rewritten for Streams A/B only, with concrete triggers and an explicit "do not use for feature work, refactors, or one-off bugfixes". C/D are a short "Planned (not available)" note. | SKILL.md |
| E4 | **Allowed fallback used instead of a split.** References 05/06 were consolidated into references/05-unattended-operation.md; harness-specific sections are marked; SKILL.md has one "Unattended operation (advanced)" pointer. Core Stream A/B material stays in the main skill. | file layout; SKILL.md |
| E5 | Replaced the Dart workflow with a toolchain-neutral 10-line quick start that points to ref 04, the single canonical per-task protocol. Dart/TS/Python/Go commands live in `references/stack-adapters.md`. | SKILL.md; ref 04; stack-adapters |
| E6 | Law 4 now gives a general machine-safety rationale. `machine_budget.max_concurrent_tests` is documented as the owner's cap for parallel shards/agents; the engine itself is serial. | SKILL.md; config examples |
| E7 | Added the 50-test stdlib suite, `evals/evals.json` with fixture repos for all five behavior cases, and `references/examples.md` with one end-to-end Stream A example and a good/bad Stream B contrast. | `tests/`, `evals/`, examples |
| E8 | Fixed `gh pr list`, "trips", the README requirements/claims, "language-agnostic" wording, and the `--stream` examples. Path/link sweep run over all docs. | grep; doc review |
| E9 | Deleted the three byte-identical top-level scripts. One copy lives under `scripts/autonomous/`; ledger paths resolve from the repo root, not `__file__`. | `test_no_duplicate_top_level_scripts`; find |
| E10 | README reduced to what it is, requirements, install, folder map, verification, and links. Laws/streams are not duplicated. | README.md |

## Deviations and judgment calls

1. **B3 branch fallback is segment-exact, not the literal `[/-]` regex.** The
   spec's acceptance test requires a `chess_fsrs_scheduler` PR not to claim
   `scheduler.dart`. A hyphen-boundary token regex would claim both. V2 matches
   whole normalized branch segments instead, which satisfies the acceptance and
   is strictly more precise.
2. **E4 fallback rather than a second skill.** The spec explicitly allows
   consolidating the fleet material into one marked reference; that keeps the
   deliverable self-contained and avoids a cross-skill install dependency.
3. **Sharding happens before sampling.** C4 calls for a partition of the
   de-duplicated list; `--max-mutants` then samples within each shard. This
   gives full coverage when shards are combined.
4. **`machine_budget` is advisory.** The engine is serial by construction, so
   `max_concurrent_tests` documents the owner's limit for parallel shards or
   agents rather than driving an internal pool.
5. **The test-weakening tripwire is heuristic.** It catches removed assertions
   (net of replacements), added skip/xfail/focus/suppression markers, and deleted
   test files. It does not attempt semantic detection of widened tolerances.
6. **The watchdog is Linux/systemd-only and harness-specific.** It is optional,
   clearly scoped, and uses `ORCH_RESUME_CMD` for another harness.

## Quick Start verification

In a fresh Python repo with the installed scripts, the Python example config
adapted to the stdlib `unittest` runner (pytest is not installed on this
machine), and a two-file test map: `ledger.py next` selected the source,
`orchestrator.py create-worktree` created the registered sibling worktree, the
engine reported a survivor, a stdlib-unittest test was red with the mutant and
green on clean source, `verify-gate` passed after committing, `record` appended
a composite-hash row, and the next `next` no longer listed the file while both
hashes were unchanged. The throwaway worktree and branch were then removed with
`cleanup-worktree`.
