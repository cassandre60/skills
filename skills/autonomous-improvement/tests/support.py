"""Helpers for the autonomous-improvement regression suite.

Every test builds a throwaway git repository under a TemporaryDirectory. No
network, no gh, no language toolchain is required.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts" / "autonomous"
EXAMPLES = SKILL_ROOT / "examples"
ASSETS = SKILL_ROOT / "assets"
PYTHON = sys.executable


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def run(
    argv: Sequence[str],
    cwd: Path,
    env: Optional[Dict[str, str]] = None,
    timeout: float = 90,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(item) for item in argv],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd=repo)


def init_repo(
    parent: Path,
    name: str = "repo",
    with_origin: bool = False,
) -> Tuple[Path, Optional[Path]]:
    repo = parent / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    origin: Optional[Path] = None
    if with_origin:
        origin = parent / f"{name}-origin.git"
        run(["git", "init", "-q", "--bare", str(origin)], cwd=parent)
        git(repo, "remote", "add", "origin", str(origin))
    return repo, origin


def install_scripts(repo: Path) -> Path:
    target = repo / "scripts" / "autonomous"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        SCRIPTS,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return target


def load_example(name: str = "config.python.json") -> Dict[str, Any]:
    with open(EXAMPLES / name, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_config(repo: Path, cfg: Optional[Dict[str, Any]] = None) -> Path:
    path = repo / "scripts" / "autonomous" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cfg or load_example(), handle, indent=2)
        handle.write("\n")
    return path


def commit(repo: Path, message: str = "commit") -> None:
    git(repo, "add", "-A")
    proc = git(repo, "commit", "-qm", message)
    if proc.returncode != 0:
        raise AssertionError(f"git commit failed: {proc.stderr}")


def push(repo: Path, branch: str = "main", set_upstream: bool = True) -> None:
    args = ["push"]
    if set_upstream:
        args.append("-u")
    args += ["origin", branch]
    proc = git(repo, *args)
    if proc.returncode != 0:
        raise AssertionError(f"git push failed: {proc.stderr}")


def script(repo: Path, name: str, *args: str, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    path = repo / "scripts" / "autonomous" / name
    return run([PYTHON, str(path), *args], cwd=repo, env=env)


def stub_path(parent: Path, files: Dict[str, str]) -> Path:
    """Create an executable directory for PATH stubs."""
    bin_dir = parent / "bin"
    bin_dir.mkdir(exist_ok=True)
    for filename, content in files.items():
        path = bin_dir / filename
        write(path, content)
        path.chmod(0o755)
    return bin_dir


def path_with_only(parent: Path, names: Iterable[str] = ("git",)) -> Path:
    """A PATH containing only symlinks to the named real tools."""
    bin_dir = parent / "minimal-bin"
    bin_dir.mkdir(exist_ok=True)
    for name in names:
        real = shutil.which(name)
        if not real:
            raise AssertionError(f"required test tool missing: {name}")
        link = bin_dir / name
        if not link.exists():
            link.symlink_to(real)
    return bin_dir


def env_with_path(bin_dir: Path, **extra: str) -> Dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = str(bin_dir)
    env.update(extra)
    return env


def ledger_text(*rows: str) -> str:
    header = (
        "# Autonomous Work Ledger\n\n"
        "| Date | Stream | Target File / Module | Content Hash | Status | PR / Issue | "
        "Summary / Outcome | Re-Audit Trigger |\n"
        "|---|---|---|---|---|---|---|---|\n"
    )
    return header + "".join(row + "\n" for row in rows)


def row(
    target: str,
    content_hash: str,
    status: str = "SOUND",
    date: str = "2026-01-01",
    pr: str = "—",
    summary: str = "audited",
) -> str:
    return (
        f"| {date} | A (Mutation) | `{target}` | `{content_hash}` | {status} | "
        f"{pr} | {summary} | Any modification. |"
    )


def python_repo(parent: Path, name: str = "repo", with_origin: bool = False) -> Tuple[Path, Optional[Path]]:
    repo, origin = init_repo(parent, name=name, with_origin=with_origin)
    install_scripts(repo)
    write_config(repo)
    write(repo / "src" / "app.py", "def f(a, b):\n    return a == b\n")
    write(repo / "tests" / "test_app.py", "from src.app import f\n\ndef test_f():\n    assert f(1, 1) is True\n")
    write(repo / "docs" / "AUTONOMOUS_WORK_LEDGER.md", ledger_text())
    return repo, origin
