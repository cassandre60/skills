"""Tests for V3 Milestone 1: Rate limit defense, cooldown, and wake circuit breaker."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import support


class RateLimitAndCircuitBreakerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = support.tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _stage_wake_repo(self, prs=None):
        repo, origin = support.init_repo(self.tmp, name="wake-repo", with_origin=True)
        support.install_scripts(repo)
        support.write_config(repo)
        json_path = self.tmp / "wake-prs.json"
        json_path.write_text(json.dumps(prs or []) + "\n")
        bin_dir = support.path_with_only(self.tmp, names=("git",))
        gh = support.write(
            bin_dir / "gh",
            "#!/bin/sh\nwhile IFS= read -r line; do printf '%s\\n' \"$line\"; done < \"$GH_JSON\"\n",
        )
        gh.chmod(0o755)
        state_dir = repo / ".autonomous" / "state"
        env = support.env_with_path(
            bin_dir,
            GH_JSON=str(json_path),
            ORCH_STATE_DIR=str(state_dir),
        )
        return repo, env, json_path, state_dir

    def test_moved_main_wakes_idle_fleet(self) -> None:
        repo, env, _json_path, _state_dir = self._stage_wake_repo(prs=[])
        # First wake: idle queue -> needs_brain is True, records digest
        first = support.script(repo, "fleet.py", "needs-brain", "--json", env=env)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue(json.loads(first.stdout)["needs_brain"])

        # Second wake: state unchanged -> quiet
        second = support.script(repo, "fleet.py", "needs-brain", "--json", env=env)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertFalse(json.loads(second.stdout)["needs_brain"])

        # Now main moves: new commit on repo
        support.write(repo / "new_file.txt", "new work landed on main\n")
        support.git(repo, "add", "new_file.txt")
        support.git(repo, "commit", "-qm", "feat: landed on main")

        # Third wake: queue is still idle, BUT main moved -> needs_brain must be True!
        third = support.script(repo, "fleet.py", "needs-brain", "--json", env=env)
        self.assertEqual(third.returncode, 0, third.stderr)
        payload = json.loads(third.stdout)
        self.assertTrue(payload["needs_brain"], "moved main must wake an idle fleet")

    def test_labelled_issue_wakes_idle_fleet(self) -> None:
        repo, _env, _json_path, _state_dir = self._stage_wake_repo(prs=[])
        bin_dir = support.path_with_only(self.tmp, names=("git",))
        issue_json = self.tmp / "issues.json"
        issue_json.write_text("[]\n")
        gh = support.write(
            bin_dir / "gh",
            "#!/bin/sh\n"
            'case "$1 $2" in\n'
            '  "pr list") echo "[]" ;;\n'
            f'  "issue list") while IFS= read -r l; do printf \'%s\\n\' "$l"; done < "{issue_json}" ;;\n'
            '  *) echo "[]" ;;\n'
            'esac\n',
        )
        gh.chmod(0o755)
        env = support.env_with_path(bin_dir, ORCH_STATE_DIR=str(repo / ".autonomous" / "state"))

        first = support.script(repo, "fleet.py", "needs-brain", "--json", env=env)
        self.assertTrue(json.loads(first.stdout)["needs_brain"])

        second = support.script(repo, "fleet.py", "needs-brain", "--json", env=env)
        self.assertFalse(json.loads(second.stdout)["needs_brain"])

        # Add an issue with autonomous-improvement label
        issue_json.write_text(
            json.dumps([
                {
                    "number": 42,
                    "title": "Fix flaky parser",
                    "labels": [{"name": "autonomous-improvement"}],
                    "updatedAt": "2026-11-10T12:00:00Z",
                }
            ])
            + "\n"
        )

        third = support.script(repo, "fleet.py", "needs-brain", "--json", env=env)
        payload = json.loads(third.stdout)
        self.assertTrue(payload["needs_brain"], "new labelled issue must wake fleet")

    def test_wake_cooldown_parks_429_model(self) -> None:
        repo, env, _json_path, state_dir = self._stage_wake_repo(prs=[])
        harness = support.write(
            self.tmp / "fake_harness.sh",
            "#!/bin/sh\necho 'HTTP 429: rate limit exceeded' >&2\nexit 1\n",
        )
        harness.chmod(0o755)
        res = support.script(
            repo,
            "fleet.py",
            "wake",
            "--harness-cmd",
            f"{harness} {{prompt_file}}",
            "--model",
            "test-provider/test-model",
            env=env,
        )
        self.assertNotEqual(res.returncode, 0)
        fail_file = state_dir / "model_failures"
        self.assertTrue(fail_file.exists())
        self.assertIn("test-provider/test-model", fail_file.read_text())

        marker = self.tmp / "harness_ran.txt"
        probe_harness = support.write(
            self.tmp / "probe_harness.sh",
            f"#!/bin/sh\ntouch {marker}\nexit 0\n",
        )
        probe_harness.chmod(0o755)
        res2 = support.script(
            repo,
            "fleet.py",
            "wake",
            "--harness-cmd",
            f"{probe_harness} {{prompt_file}}",
            "--model",
            "test-provider/test-model",
            env=env,
        )
        self.assertEqual(res2.returncode, 0)
        self.assertFalse(marker.exists(), "cooling model should not be invoked")

    def test_wake_outage_does_not_park_model(self) -> None:
        repo, env, _json_path, state_dir = self._stage_wake_repo(prs=[])
        harness = support.write(
            self.tmp / "outage_harness.sh",
            "#!/bin/sh\necho 'Error: 503 Endpoint is unavailable' >&2\nexit 1\n",
        )
        harness.chmod(0o755)
        support.script(
            repo,
            "fleet.py",
            "wake",
            "--harness-cmd",
            f"{harness} {{prompt_file}}",
            "--model",
            "test-provider/outage-model",
            env=env,
        )
        fail_file = state_dir / "model_failures"
        if fail_file.exists():
            self.assertNotIn("test-provider/outage-model", fail_file.read_text())

    def test_circuit_breaker_trips_after_three_failed_wakes_and_resets(self) -> None:
        repo, env, _json_path, state_dir = self._stage_wake_repo(prs=[])
        failing_harness = support.write(
            self.tmp / "fail_harness.sh",
            "#!/bin/sh\nexit 1\n",
        )
        failing_harness.chmod(0o755)
        for _ in range(3):
            support.script(
                repo,
                "fleet.py",
                "wake",
                "--harness-cmd",
                f"{failing_harness} {{prompt_file}}",
                env=env,
            )

        cb_file = state_dir / "CIRCUIT_BREAKER"
        self.assertTrue(cb_file.exists(), "circuit breaker must trip after 3 failed wakes")

        gov = support.script(repo, "fleet.py", "govern", "--json", env=env)
        self.assertEqual(gov.returncode, 1)
        self.assertIn("circuit breaker", gov.stdout.lower() + gov.stderr.lower())

        reset = support.script(repo, "fleet.py", "reset-circuit", env=env)
        self.assertEqual(reset.returncode, 0)
        self.assertFalse(cb_file.exists(), "reset-circuit must clear CIRCUIT_BREAKER")

    def test_honest_no_gap_does_not_trip_circuit_breaker(self) -> None:
        repo, env, _json_path, state_dir = self._stage_wake_repo(prs=[])
        for _ in range(3):
            support.script(repo, "fleet.py", "wake", env=env)

        cb_file = state_dir / "CIRCUIT_BREAKER"
        self.assertFalse(cb_file.exists(), "quiet wakes must not trip circuit breaker")

    def test_wake_honors_retry_after_header_cooldown(self) -> None:
        import time

        repo, env, _json_path, state_dir = self._stage_wake_repo(prs=[])
        harness = support.write(
            self.tmp / "retry_after_harness.sh",
            "#!/bin/sh\nprintf 'HTTP 429 Too Many Requests\\nRetry-After: 1\\n' >&2\nexit 1\n",
        )
        harness.chmod(0o755)
        res = support.script(
            repo,
            "fleet.py",
            "wake",
            "--harness-cmd",
            f"{harness} {{prompt_file}}",
            "--model",
            "test-provider/ra-model",
            env=env,
        )
        self.assertNotEqual(res.returncode, 0)
        fail_file = state_dir / "model_failures"
        self.assertTrue(fail_file.exists())
        line = fail_file.read_text(encoding="utf-8").strip().splitlines()[-1]
        self.assertEqual(len(line.split()), 3, f"expected 3-col cooldown line, got: {line!r}")
        self.assertEqual(line.split()[2], "1")

        time.sleep(1.2)
        marker = self.tmp / "ra_recovered.txt"
        probe_harness = support.write(
            self.tmp / "ra_probe.sh",
            f"#!/bin/sh\n: > \"{marker}\"\necho 'done'\nexit 0\n",
        )
        probe_harness.chmod(0o755)
        res2 = support.script(
            repo,
            "fleet.py",
            "wake",
            "--harness-cmd",
            f"{probe_harness} {{prompt_file}}",
            "--model",
            "test-provider/ra-model",
            env=env,
        )
        self.assertEqual(res2.returncode, 0, res2.stderr)
        self.assertTrue(marker.exists(), "model should recover after Retry-After: 1s expires")


if __name__ == "__main__":
    unittest.main()
