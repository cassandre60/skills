"""Unit tests for root validation scripts (check_skills.py and generate_readme.py)."""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[1]


def _load_script(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / filename)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


class RootScriptsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_generate_readme_layout_ignores_untracked_files_in_git_repo(self) -> None:
        gen = _load_script("generate_readme", "generate_readme.py")
        repo = self.tmp / "repo"
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.name", "Test")
        _git(repo, "config", "user.email", "test@example.com")

        skill = repo / "skills" / "demo-skill"
        (skill / "references").mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: Use when testing.\n---\n# Demo\n",
            encoding="utf-8",
        )
        (skill / "references" / "tracked.md").write_text("# Tracked\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial")

        # Plant untracked files both at top-level of skill and inside subdirectory
        (skill / "UNTRACKED_NOTE.md").write_text("scratch\n", encoding="utf-8")
        (skill / "references" / "untracked_scratch.md").write_text("scratch\n", encoding="utf-8")

        gen.ROOT = repo
        gen.SKILLS_DIR = repo / "skills"
        layout = gen.render_layout()
        self.assertNotIn("UNTRACKED_NOTE.md", layout)
        self.assertIn("references/ # 1 file", layout)

    def test_check_skills_catches_gemini_keys_root_docs_secrets_and_fleet_ops_refs(self) -> None:
        chk = _load_script("check_skills", "check_skills.py")
        repo = self.tmp / "repo"
        skill = repo / "skills" / "alpha"
        skill.mkdir(parents=True)
        # Construct synthetic token dynamically so this test file never trips secret regexes
        fake_gemini_key = "AIza" + ("B" * 35)
        fake_aws_key = "AKIA" + ("C" * 16)

        (skill / "SKILL.md").write_text(
            "---\nname: alpha\ndescription: Use when testing.\n---\n"
            "Broken ref: `fleet-ops/missing-runner.sh`\n"
            "Allowed local ref: `evals/heldout.local.json`\n",
            encoding="utf-8",
        )
        (skill / "leak.txt").write_text(f"key={fake_gemini_key}\n", encoding="utf-8")

        docs_dir = repo / "docs"
        docs_dir.mkdir()
        (docs_dir / "notes.md").write_text(f"aws={fake_aws_key}\n", encoding="utf-8")

        chk.ROOT = repo
        chk.SKILLS_DIR = repo / "skills"

        issues: list[str] = []
        chk.check_secrets(skill, issues)
        chk.check_secrets(docs_dir, issues)
        chk.check_relative_references(skill, issues)

        joined = "\n".join(issues)
        self.assertIn("Google/Gemini API key", joined)
        self.assertIn("AWS access key ID", joined)
        self.assertIn("`fleet-ops/missing-runner.sh`", joined)
        self.assertNotIn("heldout.local.json", joined)


if __name__ == "__main__":
    unittest.main()
