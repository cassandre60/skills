import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analyze_context_budget.py"


def load_analyzer():
    spec = importlib.util.spec_from_file_location("analyze_context_budget", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def skill_block(pid: str, description: str) -> str:
    return f"<skill>\n<id>{pid}</id>\n<name>{pid}</name>\n<description>{description}</description>\n</skill>\n"


def make_system_doc(skill_blocks: list[str], baseline: str = "BASELINE BODY") -> str:
    skills = f"<available_skills>\n{''.join(skill_blocks)}</available_skills>\n"
    return (
        "# System prompt\n\n"
        "The 12,345-character system message sent to `test-model` on a day.\n\n"
        "---\n\n"
        "You are an AI agent running in OpenCode, a coding agent harness.\n"
        "# Harness\n- Responses are rendered as GitHub-flavored Markdown.\n"
        "# Code Mode\nUse the `execute` tool to call the tools listed below.\n"
        f"{skills}\n"
        f"Instructions from: /home/x/.config/opencode/AGENTS.md\n{baseline}\n"
        "Today's date: Sat Oct 25 2026\n\n"
        "Here is some useful information about the environment:\n<env>\n"
        "  Working directory: /home/x\n</env>\n"
    )


PROSE_TRANSCRIPT = (
    "# Chat transcript: test-model\n\n"
    'The user said "tell me about the weather".\n\n'
    "## Request details\n\n"
    "- Request body: 64,920 bytes.\n"
    "- Tools: 12 declared — edit, glob, grep.\n"
    "- Generation: reasoning effort high, thinking level `high` with thoughts returned.\n\n"
    "## Upstream request\n\n"
    "- Google reported 41.8 s of server time.\n\n"
    "## Response\n\n"
    "- Usage: 15,354 prompt tokens + 183 completion tokens "
    "(174 of them reasoning) = 15,537 total.\n"
)

TABLE_TRANSCRIPT = (
    "# Chat completions log: test-model\n\n"
    "| User prompt | `say hi` |\n"
    "| Request body: 64,920 bytes. |\n"
    "| Tools: 12 |\n"
    "| Tokens (prompt / completion / reasoning / total) | 15,354 / 183 / 174 / 15,537 |\n"
    "## 4.3 Generation configuration\n\n"
    "| generationConfig.thinkingConfig.thinkingLevel | high |\n"
    "| Server-Timing | gfet4t7; dur=41839 |\n"
)

ERROR_TRANSCRIPT = (
    "# Failed exchange\n\n- Status 502, no upstream request.\n"
    "- Request body: 64,920 bytes.\n"
)


class AnalyzerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_analyzer()

    def test_sections_present_with_exact_catalog(self):
        blocks = [skill_block("big-a", "x" * 100), skill_block("med-b", "y" * 50)]
        doc = make_system_doc(blocks)
        sp = self.mod.parse_system_prompt_doc(doc)
        self.assertEqual(sp["model"], "test-model")
        self.assertEqual(sp["system_message_chars"], 12345)
        self.assertEqual(sp["catalog"]["skill_count"], 2)
        # Per-skill chars include the full <skill>...</skill> element; the
        # paragraph builder's trailing newline is not part of the element.
        expected = [len(b.rstrip("\n")) for b in blocks]
        catalog_sizes = [s["chars"] for s in sp["catalog"]["skills_sorted"]]
        self.assertEqual(sorted(catalog_sizes, reverse=True), sorted(expected, reverse=True))
        self.assertEqual(catalog_sizes, expected)
        for section in self.mod.SECTION_ORDER:
            self.assertGreater(sp["sections"][section], 0, section)
        instr_index = doc.index("Instructions from:")
        today_index = doc.index("Today's date")
        self.assertEqual(
            sp["sections"]["baseline_files"], today_index - instr_index
        )

    def test_zero_sections_when_absent(self):
        doc = (
            "The 100-character system message sent to `m` on a day.\n"
            "---\n\nYou are an AI agent.\n"
        )
        sp = self.mod.parse_system_prompt_doc(doc)
        for name in self.mod.SECTION_ORDER:
            self.assertEqual(sp["sections"][name], 0, name)
        self.assertIsNone(sp["catalog"])
        self.assertEqual(sp["errors"], [])

    def test_prose_transcript_usage(self):
        usage = self.mod.parse_transcript(PROSE_TRANSCRIPT)
        self.assertEqual(usage["prompt_tokens"], 15354)
        self.assertEqual(usage["completion_tokens"], 183)
        self.assertEqual(usage["reasoning_tokens"], 174)
        self.assertEqual(usage["total_tokens"], 15537)
        self.assertEqual(usage["request_body_bytes"], 64920)
        self.assertEqual(usage["tools_count"], 12)
        self.assertEqual(usage["thinking_level"], "high")
        self.assertEqual(usage["server_ms"], 41800.0)
        self.assertEqual(usage["user_prompt_chars"], len("tell me about the weather"))
        self.assertEqual(usage["model"], "test-model")

    def test_table_transcript_usage(self):
        usage = self.mod.parse_transcript(TABLE_TRANSCRIPT)
        self.assertEqual(usage["prompt_tokens"], 15354)
        self.assertEqual(usage["completion_tokens"], 183)
        self.assertEqual(usage["reasoning_tokens"], 174)
        self.assertEqual(usage["total_tokens"], 15537)
        self.assertEqual(usage["server_ms"], 41839.0)
        self.assertEqual(usage["thinking_level"], "high")
        self.assertEqual(usage["tools_count"], 12)

    def test_error_capture_reports_no_usage(self):
        usage = self.mod.parse_transcript(ERROR_TRANSCRIPT)
        self.assertIsNone(usage["prompt_tokens"])
        self.assertEqual(
            usage["errors"], ["usage line not found (error captures carry none)"]
        )

    def test_duplicate_flagging_and_threshold(self):
        near = "Drives development with tests using the red-green-refactor loop."
        sibling = "Drives development with repeated tests using the red-green-refactor cycle."
        other = "Compiles and packages C/C++ source code into dynamic or static libraries."
        doc = make_system_doc(
            [skill_block("near-a", near), skill_block("near-b", sibling), skill_block("far-c", other)]
        )
        catalog = self.mod.parse_skills_catalog(
            doc[doc.index("<available_skills>") : doc.index("</available_skills>")]
        )
        pairs = catalog["possible_overlap_pairs"]
        self.assertTrue(pairs, "similar pair must be flagged")
        self.assertEqual({*pairs[0]["pair"]}, {"near-a", "near-b"})
        self.assertNotIn(
            "far-c", {sid for pair in pairs for sid in pair["pair"]}
        )

    def test_levers_cover_measured_findings(self):
        long_baseline = "RULE LINE " * 650  # 7,150 chars, above the 6,000 floor
        usage = self.mod.parse_transcript(PROSE_TRANSCRIPT)
        sp = self.mod.parse_system_prompt_doc(
            make_system_doc(
                [skill_block(f"s{i}", chr(97 + i) * 20) for i in range(25)],
                baseline=long_baseline.rstrip(),
            )
        )
        levers = "\n".join(self.mod.build_levers(usage, sp))
        self.assertIn("Agent baseline files", levers)
        self.assertIn("compress", levers)
        # The skill-catalog lever fires at >=20 skills or >8000 chars.
        self.assertIn("25 skills", levers)
        self.assertIn("thinking", levers)
        self.assertIn("cacheable prefix", levers.replace("Caching:", "cacheable prefix"))
        self.assertIn("re-run after any change", levers)

    def test_cli_json_output(self):
        doc = make_system_doc([skill_block("only", "only skill")])
        with tempfile.TemporaryDirectory() as directory:
            sp_path = Path(directory) / "cap-system-prompt.md"
            tx_path = Path(directory) / "cap.md"
            sp_path.write_text(doc, encoding="utf-8")
            tx_path.write_text(PROSE_TRANSCRIPT, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), str(directory), "--json"],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["prompt_tokens"], 15354)
        self.assertEqual(data["catalog_skill_count"], 1)
        self.assertEqual(data["model"], "test-model")

    def test_cli_bundle_picks_newest_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "old-cap.md").write_text(ERROR_TRANSCRIPT, encoding="utf-8")
            (base / "old-cap-system-prompt.md").write_text(
                make_system_doc([skill_block("only", "only skill")]), encoding="utf-8"
            )
            (base / "new-cap.md").write_text(PROSE_TRANSCRIPT, encoding="utf-8")
            (base / "new-cap-system-prompt.md").write_text(
                make_system_doc([skill_block("only", "only skill")]), encoding="utf-8"
            )
            now = 1_000_000_000
            os.utime(base / "old-cap.md", (now - 30, now - 30))
            os.utime(base / "new-cap.md", (now, now))
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), str(base), "--json"],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["prompt_tokens"], 15354)

    def test_cli_rejects_missing_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), str(directory)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
