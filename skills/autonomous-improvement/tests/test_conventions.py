"""Tests for scripts/autonomous/conventions.py (Phase 4.6).

Covers the 0-token conventions reader (`read --json`, schema conventions/1)
and the trust-tier helpers fleet.py will call next phase. Every test builds
a throwaway git repository; gh is either absent (minimal PATH with only git)
or a stub that prints fixed JSON. No network.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from support import (
    SCRIPTS,
    commit,
    env_with_path,
    init_repo,
    install_scripts,
    path_with_only,
    script,
    write,
)

sys.path.insert(0, str(SCRIPTS))
import conventions as conventions_module  # noqa: E402


class ConventionsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def make(self, name: str = "repo") -> Path:
        repo, _ = init_repo(self.tmp, name=name)
        install_scripts(repo)
        return repo

    def read(self, repo: Path, env=None) -> dict:
        proc = script(repo, "conventions.py", "read", "--json", env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        return json.loads(proc.stdout)


class AiPolicyTests(ConventionsTestCase):
    def test_ai_policy_found_with_quote_and_trust_tier(self):
        repo = self.make()
        write(
            repo / "CONTRIBUTING.md",
            "# Contributing\n\nAll changes must be human-authored.\n",
        )
        commit(repo, "feat: seed")

        data = self.read(repo)
        self.assertEqual(data["schema"], "conventions/1")
        self.assertTrue(data["ai_policy"]["found"])
        self.assertEqual(data["ai_policy"]["file"], "CONTRIBUTING.md")
        self.assertEqual(data["ai_policy"]["line"], 3)
        self.assertIn("human-authored", data["ai_policy"]["quote"])
        self.assertEqual(data["trust_tier_suggested"], "T1")

        mode, reason = conventions_module.suggest_publish_mode({}, data)
        self.assertEqual(mode, "issue")
        self.assertTrue(reason)

    def test_no_policy_defaults_and_publish_modes(self):
        repo = self.make()
        commit(repo, "feat: seed")

        data = self.read(repo)
        self.assertFalse(data["ai_policy"]["found"])
        self.assertIsNone(data["trust_tier_suggested"])

        # Default tier is T1 -> issue mode even without a policy.
        self.assertEqual(
            conventions_module.suggest_publish_mode({}, data)[0], "issue"
        )
        # Explicit T2 without a policy -> PR mode.
        t2 = {"trust": {"tier": "T2"}}
        self.assertEqual(
            conventions_module.suggest_publish_mode(t2, data)[0], "pr"
        )
        # T2 with a found policy -> still issue mode.
        data["ai_policy"]["found"] = True
        self.assertEqual(
            conventions_module.suggest_publish_mode(t2, data)[0], "issue"
        )


class CodeownersTests(ConventionsTestCase):
    def test_codeowners_skips_comments_and_blanks(self):
        repo = self.make()
        write(
            repo / ".github" / "CODEOWNERS",
            "# default owners for everything\n\n* @default-team\n/docs @docs-team @alice\n",
        )
        # A root CODEOWNERS exists too, but .github/ takes precedence.
        write(repo / "CODEOWNERS", "* @shadow-team\n")
        commit(repo, "feat: seed")

        data = self.read(repo)
        self.assertEqual(
            data["codeowners"],
            [
                {"pattern": "*", "owners": ["@default-team"]},
                {"pattern": "/docs", "owners": ["@docs-team", "@alice"]},
            ],
        )


class CommitStyleTests(ConventionsTestCase):
    def test_conventional_ratio(self):
        repo = self.make()
        messages = (
            "feat: first",
            "fix: second",
            "docs: third",
            "chore(scope): fourth",
            "an odd commit with no prefix",
        )
        for index, message in enumerate(messages):
            write(repo / f"file{index}.txt", f"content {index}\n")
            commit(repo, message)

        data = self.read(repo)
        style = data["commit_style"]
        self.assertEqual(style["conventional_ratio"], 0.8)
        self.assertEqual(len(style["samples"]), 5)
        # git log is newest-first, so the odd commit (made last) leads.
        self.assertEqual(style["samples"][0], "an odd commit with no prefix")


class DcoAndTemplateTests(ConventionsTestCase):
    def test_dco_detected_from_pr_template(self):
        repo = self.make()
        write(
            repo / ".github" / "PULL_REQUEST_TEMPLATE.md",
            "## Summary\n\nDescribe the change.\n\n## Sign-off\n\n"
            "Signed-off-by: Your Name <you@example.com>\n",
        )
        write(
            repo / ".github" / "ISSUE_TEMPLATE" / "bug.md",
            "---\nname: bug\n---\n# Bug report\n",
        )
        commit(repo, "feat: seed")

        data = self.read(repo)
        self.assertEqual(data["templates"]["pr"], ["PULL_REQUEST_TEMPLATE.md"])
        self.assertEqual(data["templates"]["issue"], ["bug.md"])
        self.assertTrue(data["dco"]["required"])
        self.assertIn("PULL_REQUEST_TEMPLATE.md", str(data["dco"]["evidence"]))

    def test_fills_pr_template(self):
        repo = self.make()
        write(
            repo / ".github" / "PULL_REQUEST_TEMPLATE.md",
            "## Summary\n\n## Testing\n",
        )
        commit(repo, "feat: seed")
        conv = self.read(repo)

        ok, missing = conventions_module.fills_pr_template(
            conv, "## Summary\nfixes it\n\n## Testing\nran tests\n", root=repo
        )
        self.assertTrue(ok)
        self.assertEqual(missing, [])

        ok, missing = conventions_module.fills_pr_template(
            conv, "## Testing\nran tests\n", root=repo
        )
        self.assertFalse(ok)
        self.assertEqual(missing, ["Summary"])

        # No template in the repo -> nothing to fill, always ok.
        bare_conv = self.read(self.make("bare"))
        ok, missing = conventions_module.fills_pr_template(
            bare_conv, "any body at all"
        )
        self.assertTrue(ok)
        self.assertEqual(missing, [])


class TrustTierHelperTests(unittest.TestCase):
    def test_eligible_for_t2(self):
        self.assertEqual(conventions_module.ACCEPTANCE_FLOOR, 0.6)
        self.assertTrue(conventions_module.eligible_for_t2(0.7, 10, 0)[0])
        self.assertFalse(conventions_module.eligible_for_t2(0.7, 9, 0)[0])
        self.assertFalse(conventions_module.eligible_for_t2(0.7, 10, 1)[0])
        self.assertFalse(conventions_module.eligible_for_t2(0.5, 10, 0)[0])

    def test_eligible_for_t2_reports_reasons(self):
        ok, reason = conventions_module.eligible_for_t2(0.7, 9, 0)
        self.assertFalse(ok)
        self.assertIn("9", reason)
        ok, reason = conventions_module.eligible_for_t2(0.7, 10, 1)
        self.assertFalse(ok)
        self.assertIn("referee", reason)
        ok, reason = conventions_module.eligible_for_t2(0.5, 10, 0)
        self.assertFalse(ok)
        self.assertIn("acceptance", reason)
        ok, reason = conventions_module.eligible_for_t2(0.7, 10, 0)
        self.assertTrue(ok)
        self.assertTrue(reason)


class GhLabelsTests(ConventionsTestCase):
    def test_no_gh_labels_null_and_no_crash(self):
        repo = self.make()
        commit(repo, "feat: seed")
        bin_dir = path_with_only(self.tmp, ("git",))
        env = env_with_path(bin_dir)

        proc = script(repo, "conventions.py", "read", "--json", env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

        data = json.loads(proc.stdout)
        self.assertIsNone(data["labels"])
        self.assertEqual(data["note"], "gh unavailable")

    def test_gh_stub_labels_listed(self):
        repo = self.make()
        commit(repo, "feat: seed")
        bin_dir = path_with_only(self.tmp, ("git",))
        gh = write(
            bin_dir / "gh",
            "#!/bin/sh\nprintf '%s\\n' '[{\"name\":\"bug\"},{\"name\":\"chore\"}]'\n",
        )
        gh.chmod(0o755)
        env = env_with_path(bin_dir)

        data = self.read(repo, env=env)
        self.assertEqual(data["labels"], ["bug", "chore"])
        self.assertIsNone(data["note"])


class DeterminismTests(ConventionsTestCase):
    def test_two_runs_byte_identical(self):
        repo = self.make()
        write(repo / "CONTRIBUTING.md", "# Contributing\n\nNo AI contributions accepted.\n")
        write(repo / ".github" / "PULL_REQUEST_TEMPLATE.md", "## Summary\n\nSigned-off-by:\n")
        write(repo / ".github" / "CODEOWNERS", "* @core\n")
        for index, message in enumerate(("feat: a", "fix: b", "an odd one")):
            write(repo / f"f{index}.txt", "x\n")
            commit(repo, message)

        bin_dir = path_with_only(self.tmp, ("git",))
        gh = write(
            bin_dir / "gh",
            "#!/bin/sh\nprintf '%s\\n' '[{\"name\":\"bug\"}]'\n",
        )
        gh.chmod(0o755)
        env = env_with_path(bin_dir)

        first = script(repo, "conventions.py", "read", "--json", env=env)
        second = script(repo, "conventions.py", "read", "--json", env=env)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(first.stdout, second.stdout)


if __name__ == "__main__":
    unittest.main()
