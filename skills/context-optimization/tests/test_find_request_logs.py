import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "find_request_logs.py"


def run_finder(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def write_config(path: Path, auth_dir: Path, request_log: bool = True) -> None:
    path.write_text(
        "\n".join(
            [
                "config-version: 8",
                "observability:",
                "    logs:",
                f"        request-log: {'true' if request_log else 'false'}",
                "        logging-to-file: true",
                "        logs-max-total-size-mb: 100",
                "        error-logs-max-files: 2",
                "server:",
                "    port: 9999",
                "oauth:",
                f'    auth-dir: "{auth_dir}"',
                "",
            ]
        ),
        encoding="utf-8",
    )


class FinderTest(unittest.TestCase):
    def test_json_report_finds_request_and_error_captures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth_dir = root / "auth"
            logs_dir = auth_dir / "logs"
            logs_dir.mkdir(parents=True)
            config = root / "config.yaml"
            write_config(config, auth_dir)
            (logs_dir / "v1-chat-completions-2026-10-26T101112-abcdef12.log").write_text(
                "request\n", encoding="utf-8"
            )
            (logs_dir / "error-v1-chat-completions-2026-10-26T101113-abcdef13.log").write_text(
                "error\n", encoding="utf-8"
            )
            (logs_dir / "application.log").write_text("app\n", encoding="utf-8")
            (logs_dir / "request-log-parts-request-body-123").mkdir()

            result = run_finder(
                "--config",
                str(config),
                "--logs-dir",
                str(logs_dir),
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["config"]["values"]["request-log"], True)
            self.assertEqual(report["config"]["values"]["auth-dir"], str(auth_dir))
            self.assertEqual(
                report["management_logs_url"],
                "http://127.0.0.1:9999/v0/management/logs",
            )
            directory_report = next(
                item for item in report["log_directories"] if item["path"] == str(logs_dir)
            )
            self.assertEqual(directory_report["request_count"], 1)
            self.assertEqual(directory_report["error_count"], 1)
            self.assertEqual(directory_report["other_count"], 1)
            self.assertEqual(len(directory_report["part_directories"]), 1)
            self.assertEqual(directory_report["requests"][0]["endpoint"], "v1-chat-completions")
            self.assertEqual(directory_report["requests"][0]["captured_at"], "2026-10-26T10:11:12")

    def test_disabled_request_logging_is_called_out(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth_dir = root / "auth"
            logs_dir = auth_dir / "logs"
            logs_dir.mkdir(parents=True)
            config = root / "config.yaml"
            write_config(config, auth_dir, request_log=False)
            (logs_dir / "error-v1-chat-completions-2026-10-26T101113-abcdef13.log").write_text(
                "error\n", encoding="utf-8"
            )

            result = run_finder(
                "--config",
                str(config),
                "--logs-dir",
                str(logs_dir),
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertIn("request-log is false", " ".join(report["warnings"]))
            directory_report = next(
                item for item in report["log_directories"] if item["path"] == str(logs_dir)
            )
            self.assertIn("does not prove", " ".join(directory_report["notes"]))

    def test_human_output_reads_naturally(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth_dir = root / "auth"
            logs_dir = auth_dir / "logs"
            logs_dir.mkdir(parents=True)
            config = root / "config.yaml"
            write_config(config, auth_dir)
            (logs_dir / "v1-chat-completions-2026-10-26T101112-abcdef12.log").write_text(
                "request\n", encoding="utf-8"
            )

            result = run_finder("--config", str(config), "--logs-dir", str(logs_dir), "--limit", "1")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("request-log: true", result.stdout)
            self.assertIn("1 request capture", result.stdout)
            self.assertIn("Newest request captures", result.stdout)
            self.assertIn("Management endpoint:", result.stdout)

    def test_commented_settings_are_ignored_and_last_value_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.yaml"
            config.write_text(
                "# request-log: true\n"
                "observability:\n"
                "    logs:\n"
                "        request-log: true\n"
                "        request-log: false\n",
                encoding="utf-8",
            )
            result = run_finder("--config", str(config), "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["config"]["values"]["request-log"], False)

    def test_v8_nested_value_wins_over_legacy_top_level(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.yaml"
            config.write_text(
                "request-log: true\n"
                "auth-dir: /legacy\n"
                "observability:\n"
                "    logs:\n"
                "        request-log: false\n"
                "oauth:\n"
                "    auth-dir: /v8\n",
                encoding="utf-8",
            )
            result = run_finder("--config", str(config), "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["config"]["values"]["request-log"], False)
            self.assertEqual(report["config"]["values"]["auth-dir"], "/v8")
            self.assertEqual(
                report["config"]["sources"]["request-log"]["path"],
                "observability.logs.request-log",
            )

    def test_explicit_missing_config_fails(self) -> None:
        result = run_finder("--config", "/nonexistent/cliproxy/config.yaml")
        self.assertEqual(result.returncode, 2)
        self.assertIn("config not found", result.stderr)

    def test_negative_limit_is_rejected(self) -> None:
        result = run_finder("--limit", "-1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot be negative", result.stderr)


if __name__ == "__main__":
    unittest.main()
