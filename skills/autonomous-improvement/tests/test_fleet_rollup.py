"""The rollup judges one run per check context, on the PR head.

``gh`` keeps a superseded run in a PR's ``statusCheckRollup`` after a re-run,
so judging every entry let a CANCELLED attempt that a newer push replaced count
as a failure. The fleet called green PRs red when that happened (#376, #398).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "autonomous"
sys.path.insert(0, str(SCRIPTS))

import fleet  # noqa: E402

HEAD = "1" * 40
OLD = "2" * 40


def check(name: str, conclusion: str, at: str = "", sha: str = "") -> dict:
    item = {"name": name, "status": "COMPLETED", "conclusion": conclusion}
    if at:
        item["completedAt"] = at
    if sha:
        item["commit"] = {"oid": sha}
    return item


def state(rollup: list) -> dict:
    return fleet.evaluate_pr_checks(
        {"number": 1, "headRefOid": HEAD, "statusCheckRollup": rollup}
    )


class SupersededRunTests(unittest.TestCase):
    def test_a_superseded_cancellation_does_not_redden_a_green_pr(self) -> None:
        result = state(
            [
                check("T1 fast gates", "CANCELLED", "2026-10-22T20:00:00Z", HEAD),
                check("Docs check", "SUCCESS", "2026-10-22T20:05:00Z", HEAD),
                check("T1 fast gates", "SUCCESS", "2026-10-22T20:10:00Z", HEAD),
                check("Tests", "SUCCESS", "2026-10-22T20:15:00Z", HEAD),
                check("T3 red-team recall", "SKIPPED", "2026-10-22T20:15:00Z", HEAD),
            ]
        )
        self.assertEqual(result["checks"], "green")
        self.assertIsNone(result["red_kind"])

    def test_the_verdict_that_replaced_a_cancellation_wins(self) -> None:
        result = state(
            [
                check("T1 fast gates", "CANCELLED"),
                check("T1 fast gates", "SUCCESS"),
            ]
        )
        self.assertEqual(result["checks"], "green")

    def test_a_newer_failure_is_red_not_hidden_by_an_older_success(self) -> None:
        result = state(
            [
                check("Tests", "SUCCESS", "2026-10-22T20:00:00Z", HEAD),
                check("Tests", "FAILURE", "2026-10-22T20:10:00Z", HEAD),
            ]
        )
        self.assertEqual(result["checks"], "red")

    def test_a_cancellation_only_context_waits_instead_of_failing(self) -> None:
        result = state([check("Tests", "CANCELLED", "2026-10-22T20:00:00Z", HEAD)])
        self.assertEqual(result["checks"], "pending")

    def test_runs_from_another_commit_are_not_this_commits_verdict(self) -> None:
        result = state(
            [
                check("Tests", "SUCCESS", "2026-10-22T20:00:00Z", OLD),
                check("T1 fast gates", "SUCCESS", "2026-10-22T20:00:00Z", OLD),
            ]
        )
        self.assertEqual(result["checks"], "pending")

    def test_a_current_failure_is_red_next_to_a_superseded_cancellation(self) -> None:
        result = state(
            [
                check("T1 fast gates", "CANCELLED", "2026-10-22T20:00:00Z", HEAD),
                check("T1 fast gates", "FAILURE", "2026-10-22T20:10:00Z", HEAD),
            ]
        )
        self.assertEqual(result["checks"], "red")


class GovernorDailyCapTests(unittest.TestCase):
    def test_daily_cap_counts_merged_and_closed_prs_within_24h(self) -> None:
        import argparse
        import datetime
        import io
        import json
        import tempfile
        from unittest import mock

        now_iso = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(hours=1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        def fake_fetch_gh_prs(root, state="open", limit=100, require_gh=False):
            if state == "open":
                return [], None
            return [
                {
                    "number": 10,
                    "state": "MERGED",
                    "createdAt": now_iso,
                    "mergedAt": now_iso,
                },
                {
                    "number": 11,
                    "state": "CLOSED",
                    "createdAt": now_iso,
                    "mergedAt": None,
                },
            ], None

        cfg = {
            "fleet": {
                "max_open_prs": 5,
                "max_new_prs_per_day": 2,
                "min_acceptance_rate_14d": 0.0,
                "min_free_gb": 0,
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = argparse.Namespace(
                config=None, json=True, require_gh=False
            )
            stdout = io.StringIO()
            with (
                mock.patch.object(fleet, "main_repo_root", return_value=root),
                mock.patch.object(fleet, "load_config", return_value=cfg),
                mock.patch.object(fleet, "fetch_gh_prs", side_effect=fake_fetch_gh_prs),
                mock.patch("sys.stdout", stdout),
            ):
                rc = fleet.cmd_govern(args)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, fleet.EXIT_NEGATIVE, payload)
            self.assertFalse(payload["dispatch"])
            self.assertIn("daily new PR cap reached (2 >= 2)", payload["reason"])


class WatchdogAssetContractTests(unittest.TestCase):
    def setUp(self) -> None:
        watchdog_path = (
            Path(__file__).resolve().parents[1]
            / "fleet-ops"
            / "assets"
            / "watchdog.sh"
        )
        self.watchdog = watchdog_path.read_text(encoding="utf-8")

    def test_the_free_pool_phrase_is_a_quota_failure(self) -> None:
        # Verified 2026-10-23/24: glm's free pool said "Free usage limit
        # reached. Subscribe to OpenCode Go" and none of the earlier terms
        # matched, so the tick retried the deadness instead of parking it.
        self.assertIn("usage limit|subscribe to opencode", self.watchdog)

    def test_the_unnamed_default_parks_the_session_model(self) -> None:
        # An unnamed default cannot enter the cooldown list on its own; the
        # model the session actually runs must be read and parked instead.
        for needle in ("unnamed=\"$(current_session_model)\"",):
            self.assertIn(needle, self.watchdog)
        self.assertGreaterEqual(self.watchdog.count("unnamed="), 2)

    def test_an_exit_0_nudge_without_a_wake_is_counted(self) -> None:
        self.assertIn("unblessed", self.watchdog)
        self.assertIn("pausing until the heartbeat moves", self.watchdog)

    def test_the_watchdog_never_fakes_the_heartbeat(self) -> None:
        # A pre-attempt touch would blind the unblessed counter for a whole
        # MAX_AGE window per nudge.
        self.assertNotIn("Mark the attempt before running", self.watchdog)

    def test_the_alert_survives_an_attempt_less_tick(self) -> None:
        # alert_exhausted can be reached before any attempt ran (the unblessed
        # path), so TRIED must exist under set -u and the nudge.out tail must
        # tolerate a missing file.
        self.assertIn('TRIED=""', self.watchdog)
        self.assertIn('tail -c 600 "$OUT" 2>/dev/null || true', self.watchdog)


class GarbageAndTracelessWakeTests(unittest.TestCase):
    def test_garbage_tool_call_exit_0_parks_model_and_trips_circuit_breaker(self) -> None:
        import support

        with support.tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            repo, _ = support.init_repo(tmp, name="garbage-wake", with_origin=True)
            support.install_scripts(repo)
            support.write_config(repo)
            bin_dir = support.path_with_only(tmp, names=("git",))
            gh = support.write(bin_dir / "gh", "#!/bin/sh\necho '[]'\n")
            gh.chmod(0o755)
            state_dir = repo / ".autonomous" / "state"
            env = support.env_with_path(
                bin_dir,
                ORCH_STATE_DIR=str(state_dir),
                ORCH_MIN_FREE_GB="0",
            )
            bad_harness = support.write(
                tmp / "bad_harness.sh",
                "#!/bin/sh\nprintf '> build · free\\n<tool_call> garbled\\n'\nexit 0\n",
            )
            bad_harness.chmod(0o755)

            for i in range(3):
                env_iter = dict(env)
                # Ensure unique state or reset model_failures after first failure to prove 3 consecutive failed wakes
                fail_file = state_dir / "model_failures"
                if i > 0 and fail_file.exists():
                    fail_file.unlink()
                res = support.script(
                    repo,
                    "fleet.py",
                    "wake",
                    "--harness-cmd",
                    f"{bad_harness} {{prompt_file}}",
                    "--model",
                    "openrouter/garbage-free",
                    env=env_iter,
                )
                self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
                if i == 0:
                    self.assertTrue(fail_file.exists())
                    self.assertIn("openrouter/garbage-free", fail_file.read_text(encoding="utf-8"))

            self.assertTrue(
                (state_dir / "CIRCUIT_BREAKER").exists(),
                "3 consecutive garbage-output exit-0 wakes must trip CIRCUIT_BREAKER",
            )

    def test_traceless_wakes_trip_circuit_breaker(self) -> None:
        import support

        with support.tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            repo, _ = support.init_repo(tmp, name="traceless-wake", with_origin=True)
            support.install_scripts(repo)
            support.write_config(repo)
            bin_dir = support.path_with_only(tmp, names=("git",))
            gh = support.write(bin_dir / "gh", "#!/bin/sh\necho '[]'\n")
            gh.chmod(0o755)
            state_dir = repo / ".autonomous" / "state"
            env = support.env_with_path(
                bin_dir,
                ORCH_STATE_DIR=str(state_dir),
                ORCH_MIN_FREE_GB="0",
                ORCH_REQUIRE_AGENT_TRACE="1",
            )
            noop_harness = support.write(
                tmp / "noop_harness.sh",
                "#!/bin/sh\necho 'nothing happened'\nexit 0\n",
            )
            noop_harness.chmod(0o755)

            for _ in range(3):
                support.script(
                    repo,
                    "fleet.py",
                    "wake",
                    "--harness-cmd",
                    f"{noop_harness} {{prompt_file}}",
                    "--model",
                    "test/noop",
                    env=env,
                )

            self.assertTrue(
                (state_dir / "CIRCUIT_BREAKER").exists(),
                "3 consecutive traceless wakes under ORCH_REQUIRE_AGENT_TRACE=1 must trip CIRCUIT_BREAKER",
            )


if __name__ == "__main__":
    unittest.main()
