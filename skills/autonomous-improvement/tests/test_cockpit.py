"""Tests for the Master Cockpit: fleet.py dashboard, directive, and worker."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import support


class MasterCockpitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = support.tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _stage_repo(self, prs=None, issues=None):
        repo, _origin = support.init_repo(self.tmp, name="cockpit-repo", with_origin=True)
        support.install_scripts(repo)
        cfg = support.load_example("config.python.json")
        cfg["fleet"] = {
            "max_open_prs": 5,
            "max_new_prs_per_day": 6,
            "max_worktrees": 5,
            "model_pool": {
                "entries": [
                    {"id": "provider-a/fast-free", "tier": "free"},
                    {"id": "provider-b/backup-free", "tier": "free"},
                ]
            },
        }
        support.write_config(repo, cfg)
        support.write(repo / "src" / "alpha.py", "def alpha():\n    return 1\n")
        support.write(repo / "tests" / "test_alpha.py", "def test_alpha():\n    assert True\n")
        support.write(repo / "src" / "beta.py", "def beta():\n    return 2\n")
        support.write(repo / "tests" / "test_beta.py", "def test_beta():\n    assert True\n")
        support.commit(repo, "feat: seed repo")

        prs_json = self.tmp / "prs.json"
        prs_json.write_text(json.dumps(prs or []) + "\n", encoding="utf-8")
        issues_json = self.tmp / "issues.json"
        issues_json.write_text(json.dumps(issues or []) + "\n", encoding="utf-8")

        bin_dir = support.path_with_only(self.tmp, names=("git",))
        gh = support.write(
            bin_dir / "gh",
            "#!/bin/sh\n"
            'case "$1 $2" in\n'
            f'  "pr list") while IFS= read -r l; do printf \'%s\\n\' "$l"; done < "{prs_json}" ;;\n'
            f'  "issue list") while IFS= read -r l; do printf \'%s\\n\' "$l"; done < "{issues_json}" ;;\n'
            '  *) echo "[]" ;;\n'
            "esac\n",
        )
        gh.chmod(0o755)
        state_dir = repo / ".autonomous" / "state"
        env = support.env_with_path(
            bin_dir,
            ORCH_STATE_DIR=str(state_dir),
        )
        return repo, env, prs_json, state_dir

    def test_directive_add_list_set_done_and_clear(self) -> None:
        repo, env, _prs_json, _state_dir = self._stage_repo()

        # 1. Add two directives
        r1 = support.script(
            repo,
            "fleet.py",
            "directive",
            "add",
            "Focus on alpha.py first",
            "--priority",
            "high",
            "--stream",
            "A",
            "--target",
            "src/alpha.py",
            "--json",
            env=env,
        )
        self.assertEqual(r1.returncode, 0, r1.stderr)
        d1 = json.loads(r1.stdout)["directive"]
        self.assertEqual(d1["id"], "dir-0001")
        self.assertEqual(d1["priority"], "high")
        self.assertEqual(d1["stream"], "A")
        self.assertEqual(d1["target"], "src/alpha.py")

        r2 = support.script(
            repo,
            "fleet.py",
            "directive",
            "add",
            "Repair red CI on PR #12",
            "--json",
            env=env,
        )
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual(json.loads(r2.stdout)["directive"]["id"], "dir-0002")

        # 2. List active directives (JSON + Markdown)
        listed = support.script(repo, "fleet.py", "directive", "list", "--json", env=env)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        active = json.loads(listed.stdout)["directives"]
        self.assertEqual([d["id"] for d in active], ["dir-0001", "dir-0002"])

        listed_md = support.script(repo, "fleet.py", "directive", "list", env=env)
        self.assertEqual(listed_md.returncode, 0, listed_md.stderr)
        self.assertIn("dir-0001", listed_md.stdout)
        self.assertIn("Focus on alpha.py first", listed_md.stdout)

        # 3. Mark dir-0001 done
        done_res = support.script(
            repo, "fleet.py", "directive", "done", "--id", "dir-0001", "--json", env=env
        )
        self.assertEqual(done_res.returncode, 0, done_res.stderr)
        self.assertEqual(
            [d["id"] for d in json.loads(done_res.stdout)["active"]],
            ["dir-0002"],
        )

        # 4. 'set' replaces remaining active directives with a single new active one
        set_res = support.script(
            repo,
            "fleet.py",
            "directive",
            "set",
            "Switch exclusively to Stream B",
            "--stream",
            "B",
            "--json",
            env=env,
        )
        self.assertEqual(set_res.returncode, 0, set_res.stderr)
        active_after_set = json.loads(set_res.stdout)["active"]
        self.assertEqual(len(active_after_set), 1)
        self.assertEqual(active_after_set[0]["id"], "dir-0003")
        self.assertEqual(active_after_set[0]["text"], "Switch exclusively to Stream B")

        # 5. 'clear' marks all active directives done
        clear_res = support.script(repo, "fleet.py", "directive", "clear", "--json", env=env)
        self.assertEqual(clear_res.returncode, 0, clear_res.stderr)
        self.assertEqual(json.loads(clear_res.stdout)["active"], [])

        all_res = support.script(repo, "fleet.py", "directive", "list", "--all", "--json", env=env)
        self.assertEqual(len(json.loads(all_res.stdout)["directives"]), 3)

    def test_directive_wakes_needs_brain_and_renders_in_wake_prompt(self) -> None:
        repo, env, _prs_json, state_dir = self._stage_repo(prs=[])

        first = support.script(repo, "fleet.py", "needs-brain", "--json", env=env)
        self.assertTrue(json.loads(first.stdout)["needs_brain"])

        second = support.script(repo, "fleet.py", "needs-brain", "--json", env=env)
        self.assertFalse(json.loads(second.stdout)["needs_brain"])

        # Owner adds a directive in the Master Cockpit -> next needs-brain wakes!
        support.script(
            repo,
            "fleet.py",
            "directive",
            "add",
            "Prioritize src/beta.py",
            "--priority",
            "high",
            "--target",
            "src/beta.py",
            env=env,
        )
        third = support.script(repo, "fleet.py", "needs-brain", "--json", env=env)
        payload = json.loads(third.stdout)
        self.assertTrue(payload["needs_brain"])
        self.assertTrue(
            any(r.startswith("owner-directive:dir-0001:") for r in payload["reasons"]),
            payload["reasons"],
        )

        # Wake prompt includes the active owner directive
        wake = support.script(
            repo,
            "fleet.py",
            "wake",
            "--state-dir",
            str(state_dir),
            "--print-prompt",
            env=env,
        )
        self.assertEqual(wake.returncode, 0, wake.stderr)
        self.assertIn("Owner directives (active", wake.stdout)
        self.assertIn("[dir-0001] (high, target src/beta.py): Prioritize src/beta.py", wake.stdout)

    def test_worker_register_update_list_and_prune(self) -> None:
        repo, env, _prs_json, _state_dir = self._stage_repo()

        reg1 = support.script(
            repo,
            "fleet.py",
            "worker",
            "register",
            "--id",
            "ses_worker_1",
            "--target",
            "src/alpha.py",
            "--stream",
            "A",
            "--slug",
            "alpha-mut",
            "--model",
            "provider-a/fast-free",
            "--summary",
            "Running mutation scan",
            "--json",
            env=env,
        )
        self.assertEqual(reg1.returncode, 0, reg1.stderr)
        w1 = json.loads(reg1.stdout)["worker"]
        self.assertEqual(w1["status"], "running")
        self.assertEqual(w1["target"], "src/alpha.py")

        # Register a second worker and complete both
        support.script(
            repo,
            "fleet.py",
            "worker",
            "register",
            "--id",
            "ses_worker_2",
            "--target",
            "src/beta.py",
            "--stream",
            "A",
            env=env,
        )
        upd1 = support.script(
            repo,
            "fleet.py",
            "worker",
            "update",
            "--id",
            "ses_worker_1",
            "--status",
            "completed",
            "--pr",
            "17",
            "--url",
            "https://example.invalid/pr/17",
            "--summary",
            "Killed 2 surviving mutants",
            "--json",
            env=env,
        )
        self.assertEqual(upd1.returncode, 0, upd1.stderr)
        w1_done = json.loads(upd1.stdout)["worker"]
        self.assertEqual(w1_done["status"], "completed")
        self.assertEqual(w1_done["pr"], 17)
        self.assertIn("finished_at", w1_done)

        support.script(
            repo,
            "fleet.py",
            "worker",
            "update",
            "--id",
            "ses_worker_2",
            "--status",
            "failed",
            "--summary",
            "Gate verify failed",
            env=env,
        )
        # Register a 3rd worker that stays running
        support.script(
            repo,
            "fleet.py",
            "worker",
            "register",
            "--id",
            "ses_worker_3",
            "--target",
            "src/gamma.py",
            "--status",
            "running",
            env=env,
        )

        # Prune terminal workers keeping only the newest 1 terminal worker
        pruned = support.script(
            repo, "fleet.py", "worker", "prune", "--keep", "1", "--json", env=env
        )
        self.assertEqual(pruned.returncode, 0, pruned.stderr)
        self.assertEqual(json.loads(pruned.stdout)["pruned"], 1)

        listed = support.script(repo, "fleet.py", "worker", "list", "--json", env=env)
        ids = [w["id"] for w in json.loads(listed.stdout)["workers"]]
        # Running worker (ses_worker_3) + newest terminal worker (ses_worker_2) remain
        self.assertEqual(ids, ["ses_worker_3", "ses_worker_2"])

    def test_dashboard_aggregates_cockpit_sections(self) -> None:
        prs = [
            {
                "number": 10,
                "title": "test(alpha): kill boundary mutant",
                "headRefName": "test/alpha-mutant",
                "headRefOid": "1111111111111111",
                "state": "OPEN",
                "isDraft": False,
                "mergeStateStatus": "CLEAN",
                "labels": [],
                "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "SUCCESS"}],
                "body": "Target: `src/alpha.py`\n",
            },
            {
                "number": 11,
                "title": "test(gamma): broken lint",
                "headRefName": "test/gamma-lint",
                "headRefOid": "2222222222222222",
                "state": "OPEN",
                "isDraft": False,
                "mergeStateStatus": "UNSTABLE",
                "labels": [],
                "statusCheckRollup": [
                    {"name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"}
                ],
                "body": "",
            },
        ]
        repo, env, _prs_json, state_dir = self._stage_repo(prs=prs)
        state_dir.mkdir(parents=True, exist_ok=True)
        # Put provider-a/fast-free in cooldown
        import time

        (state_dir / "wake_failures").write_text(
            f"provider-a/fast-free {int(time.time())}\n"
            f"provider-b/backup-free {int(time.time()) - 10} 2\n",
            encoding="utf-8",
        )
        (state_dir / "cockpit-events.log").write_text(
            "2026-11-11T19:00:00+00:00 [🟢 Done] Wake finished cleanly on provider-b/backup-free\n",
            encoding="utf-8",
        )

        # Add an active directive and a worker
        support.script(
            repo,
            "fleet.py",
            "directive",
            "add",
            "Fix PR #11 lint first",
            "--priority",
            "high",
            env=env,
        )
        support.script(
            repo,
            "fleet.py",
            "worker",
            "register",
            "--id",
            "ses_live_99",
            "--target",
            "src/gamma.py",
            "--model",
            "provider-b/backup-free",
            "--summary",
            "Fixing lint on #11",
            env=env,
        )

        dash_json = support.script(
            repo,
            "fleet.py",
            "dashboard",
            "--state-dir",
            str(state_dir),
            "--json",
            env=env,
        )
        self.assertEqual(dash_json.returncode, 0, dash_json.stderr)
        payload = json.loads(dash_json.stdout)
        self.assertEqual(payload["schema"], "cockpit-dashboard/1")
        self.assertTrue(payload["governor"]["dispatch"])
        self.assertEqual(payload["governor"]["tend_queue"], [11])
        self.assertEqual(payload["pr_rollup"]["green_ready"], [10])
        self.assertEqual(payload["pr_rollup"]["red_tending"], [11])
        self.assertEqual(len(payload["directives"]), 1)
        self.assertEqual(payload["directives"][0]["text"], "Fix PR #11 lint first")
        self.assertEqual(len(payload["workers"]), 1)
        self.assertEqual(payload["workers"][0]["id"], "ses_live_99")
        self.assertEqual(payload["model_pool"]["cooling_count"], 1)
        self.assertEqual(payload["model_pool"]["live_count"], 1)
        # src/alpha.py is claimed by PR #10, so next_candidates only offers unclaimed src/beta.py
        cand_targets = [c["target"] for c in payload["next_candidates"]]
        self.assertIn("src/beta.py", cand_targets)
        self.assertNotIn("src/alpha.py", cand_targets)

        dash_md = support.script(
            repo,
            "fleet.py",
            "dashboard",
            "--state-dir",
            str(state_dir),
            env=env,
        )
        self.assertEqual(dash_md.returncode, 0, dash_md.stderr)
        for heading in (
            "# Autonomous Improvement — Master Cockpit",
            "## Active Owner Directives",
            "## Subagent Workers & Active Worktrees",
            "## Open PR Matrix",
            "## Model Pool Status",
            "## Next Unclaimed Candidates",
            "## Recent Factory Events",
        ):
            self.assertIn(heading, dash_md.stdout)
        self.assertIn("ses_live_99", dash_md.stdout)
        self.assertIn("Fix PR #11 lint first", dash_md.stdout)
        self.assertIn("Wake finished cleanly on provider-b/backup-free", dash_md.stdout)


if __name__ == "__main__":
    unittest.main()
