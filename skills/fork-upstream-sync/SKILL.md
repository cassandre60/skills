---
name: fork-upstream-sync
description: Track, audit, filter, and synchronize changes from an upstream repository into a fork with a machine-readable negative ledger and 0-token deterministic pre-filtering. Use whenever working in a git fork, syncing updates from parent/upstream repos, backporting upstream commits or PRs, triaging upstream diffs, setting up automated weekly upstream alerts, or when an agent keeps re-auditing already-rejected upstream changes due to state amnesia.
---

# Fork Upstream Sync

Maintain continuous, high-signal alignment with an upstream parent repository without
re-reading historical diffs or burning LLM tokens on deleted files.

## The Fork Dilemma & Why Simple Diffs Fail

1. **State Amnesia (The Negative Ledger Trap):**
   In a modified fork, most upstream changes are intentionally rejected (cut features,
   re-skinned presentation, incompatible dependencies). Without a machine-readable record
   of *rejected* commits, running `git log HEAD..upstream/main` displays hundreds of past
   commits every time. An agent repeatedly audits the same 100 commits every few weeks.
2. **Non-Ancestor History (Squash & Graft Divergence):**
   When fixes reach a fork as cherry-picks or squashed PRs, git merge-base does not advance
   linearly. A blind `git merge upstream/main` will conflict on files that are already
   correct.
3. **The LLM Token Trap:**
   80–90% of upstream commits touch files that don't exist in the fork. Parsing commit
   diffs with an LLM for deleted paths burns massive context on work a 10-line Python
   script resolves in 20 milliseconds.

---

## The 3-Phase Protocol

```text
upstream/main (Parent Repo)
    │
    ▼ [Phase 1: Deterministic Filter (0 tokens, Python stdlib)]
    ├── Touches cut paths? ─────────────► Auto-mark SKIPPED_CUT
    ├── Touches deleted files? ──────────► Auto-mark SKIPPED_NOT_IN_FORK
    ├── Translation/version chore? ──────► Auto-mark SKIPPED_CHORE
    └── Touches surviving files?
            │
            ▼
      [.upstream/ledger.json] (High-water mark & status ledger)
            │
            ▼ [Phase 2: Automated Alerting (Weekly CI or on-demand)]
      [GitHub Issue #... / Agent summary: 1-5 active candidates]
            │
            ▼ [Phase 3: Surgical Porting (Isolated Worktree)]
      [Worktree -> Fail-to-pass test -> Port patch -> Gates clean -> Mark APPLIED]
```

---

## Quick Start in Any Fork Repo

### 1. Bootstrap Setup (`/upstream-init`)

Create the directory `.upstream/` in the repo root:

1. **Create `.upstream/config.json`**:
   ```json
   {
     "upstream_repo": "owner/parent-repo",
     "upstream_branch": "main",
     "base_sha": "<commit_hash_where_fork_split_or_last_audited>",
     "cut_paths": [
       "path/to/deleted_feature_1",
       "path/to/deleted_feature_2"
     ],
     "chore_subjects": [
       "^Bump version",
       "^New translations",
       "Crowdin"
     ],
     "chore_files": [
       "\\.arb$",
       "\\.po$",
       "Localizable\\.xcstrings$",
       "^\\.gitattributes$"
     ],
     "known_applied": {}
   }
   ```

2. **Copy the universal tracker script**:
   Copy `scripts/tracker_template.py` into `.upstream/tracker.py` and make it executable:
   ```bash
   chmod +x .upstream/tracker.py
   ```

3. **Initialize the ledger**:
   ```bash
   python3 .upstream/tracker.py sync
   ```

4. **(Optional) Add weekly GitHub Actions sync**:
   Copy `assets/upstream-sync.yml` into `.github/workflows/upstream-sync.yml`.
   It runs every Monday at 06:00 UTC, scans upstream, and creates/updates a GitHub Issue
   tagged `upstream-sync` whenever candidate PRs exist.

---

## Daily Operating Workflows

### Workflow A: Check for Upstream Candidates
```bash
# Fetch and classify new upstream commits
python3 .upstream/tracker.py sync

# View active candidate summary
python3 .upstream/tracker.py candidates

# View markdown table for reporting or pasting into an issue
python3 .upstream/tracker.py candidates --format markdown
```

### Workflow B: Port an Upstream Candidate
When you or an agent selects an upstream PR or commit (e.g. PR `#3786` / commit `2bcbbe7`):

1. **Create an isolated worktree**:
   ```bash
   git worktree add ../<repo>-upstream-3786 -b fix/upstream-3786 origin/main
   cd ../<repo>-upstream-3786
   ```

2. **Prove failure first (The Fail-to-Pass Invariant)**:
   Write a unit test covering the bug or behavior on current code before applying the
   upstream patch. Run it:
   ```bash
   <test-runner> <test-file>
   ```
   Confirm it fails.

3. **Apply the patch**:
   ```bash
   # If git cherry-pick works cleanly:
   git cherry-pick <upstream_sha>

   # Or fetch the PR patch from GitHub:
   curl -sL https://github.com/<owner>/<repo>/pull/3786.patch | git apply -v
   ```

4. **Verify and run gates**:
   - Confirm the test now passes.
   - Run linter / static analysis.
   - Run fast repo gates.

5. **Update the ledger**:
   ```bash
   python3 .upstream/tracker.py mark 2bcbbe7 APPLIED --fork-pr <your_fork_pr> --notes "Ported upstream fix"
   ```

6. **Submit PR, watch CI, and merge**.

---

### Workflow C: Mark Candidates as Skipped or Backlogged
If an upstream change touches a surviving file but is not relevant (e.g. presentation
styling your fork intentionally overrides):

```bash
# Mark as skipped presentation:
python3 .upstream/tracker.py mark <sha_or_pr> SKIPPED_VISUAL --notes "Fork uses bespoke design tokens"

# Mark as skipped cut:
python3 .upstream/tracker.py mark <sha_or_pr> SKIPPED_CUT --notes "Feature cut in this fork"

# Defer to backlog:
python3 .upstream/tracker.py mark <sha_or_pr> BACKLOG --notes "Evaluate after v2.0"
```

---

## Scar Tissue & Rules of Engagement

1. **The Remote Push-URL Trap**:
   Always set the push URL for `upstream` to `no_push` so you or an agent never
   accidentally pushes a branch or tag upstream:
   ```bash
   git remote set-url --push upstream no_push
   ```

2. **Do not run full test suites locally**:
   Follow local test budgets. Run only the test file covering the ported feature. Leave
   the full regression suite to CI on push.

3. **Content and intent over commit hashes**:
   Upstream commits will frequently conflict due to slight differences in imports or
   styling. Do not fight `git merge` conflicts across hundreds of files. Port the
   **subsystem or method** cleanly into the fork's architecture.

4. **Never let an agent guess upstream state**:
   Always inspect `.upstream/ledger.json` before performing diff audits. If a commit is
   marked `SKIPPED_*` or `APPLIED`, it is closed. Never re-investigate it.
