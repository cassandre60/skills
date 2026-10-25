# SPEC — autonomous-improvement's own rules

This is the skill's authoritative rule map: every rule the skill enforces on
its rounds, where it is enforced, and the proof — a test that fails if the
rule breaks. It exists so the failure mode that motivates the **spec-audit**
pass (a rule with no code, no test, or a test that cannot fail) can never
silently appear in the skill itself: run the audit on this file with

```sh
python3 scripts/autonomous/docs_truth.py check --paths . --spec SPEC.md
```

and treat a finding here like a finding anywhere else. When a rule changes,
change its row in the same PR. Rule ids are `R-nn` on purpose: they are row
anchors, not the `G05`-style citation ids `docs_truth` traces both ways.

| Rule | Enforcement | Proof |
|---|---|---|
| `R-01` **Fail-to-pass**: no fix or test lands without a red/green pair against the defect; publish refuses an incomplete evidence artifact | `scripts/autonomous/evidence.py`, `scripts/autonomous/orchestrator.py` | `test_publish_pr_mode_renders_evidence_and_flips_ready`, `test_verify_gate_after_commit` |
| `R-02` **Content-hash dedup**: audits dedup on `<source9>:<test9>`, newest row wins, rows are append-only with escaped cells | `scripts/autonomous/ledger.py`, `references/02-ledger-and-dedup.md` | `test_reaudit_newest_wins`, `test_pipe_roundtrip` |
| `R-03` **Worktree isolation**: tasks run in registered worktrees; cleanup refuses dirty, unpushed, or unregistered trees without `--force` | `scripts/autonomous/orchestrator.py` | `test_cleanup_refuses_dirty_unpushed_unregistered`, `test_clean_pushed_worktree_cleans_up` |
| `R-04` **Machine budget**: one targeted test per mutant, one full-file run per task, no whole-suite local runs, argv lists only (no shell) | `references/04-workflow-and-roles.md`, `scripts/autonomous/common.py` | `MutationMeasurementContractTests`, `test_filename_with_space_passed_as_one_argv` |
| `R-05` **Pool rotation**: a quota error rotates the model; an outage parks nothing; exit status is the contract | `assets/watchdog.sh`, `references/harness-adapters.md` | `tests/watchdog_rotation_test.sh` |
| `R-06` **No test weakening**: `skip`/`xfail`/`.only`/`# noqa`/widened tolerance cannot buy a green gate; sidecars block verify-gate | `scripts/autonomous/test_weakening_check.py`, `scripts/autonomous/orchestrator.py` | `test_verify_gate_fails_on_weakening_edit`, `test_verify_gate_rejects_sidecars` |
| `R-07` **Elective keep-alive + recorded consent**: `session.environment` defaults to `laptop`; a live timer under the laptop profile is a named warning, never a silent default; arming any keep-alive timer requires a recorded one-time owner ask (`session.keepalive_consent`), and a live timer without one is a hard doctor failure | `scripts/autonomous/common.py`, `scripts/autonomous/fleet.py` | `test_laptop_profile_with_live_timer_warns_about_elective_machinery`, `test_live_keepalive_without_recorded_consent_is_a_hard_failure`, `test_recorded_keepalive_consent_clears_the_check`, `test_vm_profile_without_consent_is_soft_until_a_timer_exists` |
| `R-08` **Harness adapters**: every harness touchpoint is an environment override; adapting harnesses means setting variables, never editing scripts | `assets/watchdog.sh`, `references/harness-adapters.md` | `tests/watchdog_rotation_test.sh` |
| `R-09` **Toolbox router**: invocation runs the foundation passes and asks the owner before any heavy tool; fleet mode is opt-in | `SKILL.md`, `references/10-toolbox.md` | `test_skill_routes_a_toolbox_not_a_formula` |
| `R-10` **SKILL.md stays short** (≤ 200 lines) and routes; the details live in the references | `SKILL.md` | `test_skill_is_short` |
| `R-11` **No leaked identity or forbidden machinery strings** in docs or scripts | `tests/test_v2.py` | `test_no_removed_terms`, `test_no_project_strings` |
| `R-12` **No project strings in the runtime**: candidate globs, commands, and mappings live in `config.json`, not in code | `scripts/autonomous/common.py`, `scripts/autonomous/config.example.json` | `test_no_project_strings`, `test_origin_untouched_on_success_and_failure` |
| `R-13` **Referee isolation**: a stream without a shipped finder + oracle is rejected; the referee's own thresholds are owner-only | `scripts/autonomous/common.py`, `references/08-recursive-self-improvement.md` | `StreamRegistryTests` |
| `R-14` **Claims are code**: a target is claimed by an open draft PR, deduped by exact path; publish is idempotent | `scripts/autonomous/fleet.py`, `references/02-ledger-and-dedup.md` | `test_claim_opens_one_draft_pr_and_refuses_claimed_target`, `test_publish_issue_mode_is_idempotent` |
| `R-15` **Generated files stay generated**: the README's SKILLS/LAYOUT blocks are regenerated, never hand-edited | scripts/generate_readme.py | .github/workflows/check.yml |
| `R-16` **Installable skills are discoverable**: frontmatter parses, `name:` matches the directory, links resolve | scripts/check_skills.py, scripts/check_discovery.sh | .github/workflows/check.yml |
