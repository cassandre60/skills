import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "render_request_log.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_renderer():
    spec = importlib.util.spec_from_file_location("render_request_log", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_render(fixture: str, output_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(FIXTURES / fixture),
            "--output-dir",
            str(output_dir),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


class RendererTest(unittest.TestCase):
    def test_renders_streaming_transcript_and_system_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            result = run_render("successful-stream.log", output_dir)
            self.assertEqual(result.returncode, 0, result.stderr)

            transcript = (output_dir / "successful-stream.md").read_text(encoding="utf-8")
            self.assertIn("# Chat transcript: test-model", transcript)
            self.assertIn("say hi", transcript)
            self.assertIn("> Thinking about it.", transcript)
            self.assertIn("Hi there", transcript)
            self.assertIn("| Total tokens | 17 |", transcript)
            self.assertIn("successful-stream-system-prompt.md", transcript)
            self.assertIn("successful-stream-raw-payloads.md", transcript)

            system_prompt = (output_dir / "successful-stream-system-prompt.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("You are a test harness.", system_prompt)

            raw = (output_dir / "successful-stream-raw-payloads.md").read_text(encoding="utf-8")
            self.assertIn('"max_completion_tokens": 64', raw)
            self.assertIn('"reasoning_content":"Thinking about it.\\n"', raw)
            self.assertIn("```text\n[DONE]\n```", raw)
            # Both request bodies and the downstream JSON payloads are present.
            self.assertGreaterEqual(raw.count("```json"), 5)

    def test_renders_gateway_failure_without_inventing_a_reply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            result = run_render("failed-gateway.log", output_dir)
            self.assertEqual(result.returncode, 0, result.stderr)

            transcript = (output_dir / "failed-gateway.md").read_text(encoding="utf-8")
            self.assertIn("# Failed request: test-model", transcript)
            self.assertIn("HTTP 429", transcript)
            self.assertIn("model_cooldown", transcript)
            self.assertIn("Individual quota reached", transcript)
            self.assertIn("No upstream request section was written", transcript)
            self.assertNotIn("### Assistant — reply", transcript)

            raw = (output_dir / "failed-gateway-raw-payloads.md").read_text(encoding="utf-8")
            self.assertIn('"last_upstream_error"', raw)
            self.assertIn('"reset_seconds": 3600', raw)

    def test_renders_nonstreaming_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            result = run_render("nonstreaming-tool-call.log", output_dir)
            self.assertEqual(result.returncode, 0, result.stderr)

            transcript = (output_dir / "nonstreaming-tool-call.md").read_text(encoding="utf-8")
            self.assertIn("### Assistant — reply", transcript)
            self.assertIn("Done", transcript)
            self.assertIn("### Assistant — tool calls", transcript)
            self.assertIn("**1. `read`**", transcript)
            self.assertIn('"path": "README.md"', transcript)
            self.assertIn("System prompt: none captured", transcript)

            self.assertFalse((output_dir / "nonstreaming-tool-call-system-prompt.md").exists())

    def test_rejects_unsupported_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"
            result = run_render("models-endpoint.log", output_dir)
            self.assertEqual(result.returncode, 3)
            self.assertIn("only /v1/chat/completions captures are supported", result.stderr)
            self.assertFalse(output_dir.exists())

    def test_refuses_to_overwrite_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            first = run_render("successful-stream.log", output_dir)
            self.assertEqual(first.returncode, 0, first.stderr)

            second = run_render("successful-stream.log", output_dir)
            self.assertEqual(second.returncode, 2)
            self.assertIn("pass --force", second.stderr)

            forced = run_render("successful-stream.log", output_dir, "--force")
            self.assertEqual(forced.returncode, 0, forced.stderr)


class RendererInternalsTest(unittest.TestCase):
    def test_merge_tool_call_argument_fragments(self) -> None:
        module = load_renderer()
        merged = module.merge_tool_calls(
            [
                {"index": 0, "id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"pa'}},
                {"index": 0, "function": {"arguments": 'th":"README.md"}'}},
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["function"]["name"], "read")
        self.assertEqual(merged[0]["function"]["arguments"], '{"path":"README.md"}')

    def test_verification_rejects_mismatched_json(self) -> None:
        module = load_renderer()
        with tempfile.TemporaryDirectory() as directory:
            transcript_path = Path(directory) / "transcript.md"
            raw_path = Path(directory) / "raw.md"
            transcript_path.write_text("transcript\n", encoding="utf-8")
            raw_path.write_text("```json\n{}\n```\n", encoding="utf-8")
            bundle = module.RenderedBundle(
                transcript="transcript\n",
                system_prompt=None,
                raw_reference="",
                expected_json_blocks=[{"different": True}],
                raw_stream_payloads=[],
                thinking="",
                reply="",
            )
            with self.assertRaises(module.VerificationError):
                module.verify_bundle(transcript_path, raw_path, bundle)

    def test_verification_rejects_altered_reply(self) -> None:
        module = load_renderer()
        with tempfile.TemporaryDirectory() as directory:
            transcript_path = Path(directory) / "transcript.md"
            raw_path = Path(directory) / "raw.md"
            raw_path.write_text("```json\n{\"ok\": true}\n```\n", encoding="utf-8")
            transcript_path.write_text("### Assistant — reply\n\nchanged\n", encoding="utf-8")
            bundle = module.RenderedBundle(
                transcript="",
                system_prompt=None,
                raw_reference="",
                expected_json_blocks=[{"ok": True}],
                raw_stream_payloads=[],
                thinking="",
                reply="original",
            )
            with self.assertRaises(module.VerificationError):
                module.verify_bundle(transcript_path, raw_path, bundle)

    def test_error_details_extracts_truncated_nested_error(self) -> None:
        module = load_renderer()
        completion = module.Completion(
            error={
                "code": "model_cooldown",
                "message": (
                    'All credentials for test-model are cooling down (last error: '
                    '{"error":{"code":429,"message":"Quota reached.","status":"RESOURCE_EXHAUSTED"'
                ),
                "last_upstream_error": (
                    '{"error": {"code": 429, "message": "Quota reached.", '
                    '"status": "RESOURCE_EXHAUSTED"'
                ),
            },
            error_body="",
        )
        status, code, message = module.error_details(completion, "429")
        self.assertEqual(status, "429")
        self.assertEqual(code, "model_cooldown")
        self.assertIn("All credentials for test-model are cooling down", message)
        self.assertIn("upstream reported RESOURCE_EXHAUSTED: Quota reached.", message)
        self.assertNotIn('"error"', message)

    def test_response_with_status_then_bare_error_body(self) -> None:
        module = load_renderer()
        section = module.Section(
            name="API RESPONSE",
            lines=[
                "Timestamp: 2026-10-26T10:00:00.000000000+01:00",
                "",
                "Status: 429",
                "",
                '{"error":{"code":"quota"}}',
            ],
        )
        block = module.parse_response_block(section)
        self.assertEqual(block.status, "429")
        self.assertEqual(block.headers, [])
        self.assertEqual(block.body, '{"error":{"code":"quota"}}')

    def test_stream_unknown_lines_are_preserved(self) -> None:
        module = load_renderer()
        payloads, ignored = module.parse_sse(
            "event: message\n"
            'data: {"choices":[]}\n'
            "\n"
            ": keep-alive\n"
            "data: [DONE]\n"
        )
        self.assertEqual(payloads, ['{"choices":[]}', "[DONE]"])
        self.assertEqual(ignored, ["event: message", ": keep-alive"])

    def test_verification_rejects_altered_system_prompt(self) -> None:
        module = load_renderer()
        with tempfile.TemporaryDirectory() as directory:
            transcript_path = Path(directory) / "transcript.md"
            raw_path = Path(directory) / "raw.md"
            system_path = Path(directory) / "system.md"
            raw_path.write_text("```json\n{\"ok\": true}\n```\n", encoding="utf-8")
            transcript_path.write_text("transcript\n", encoding="utf-8")
            system_path.write_text("## Message 1 (system)\n\nchanged\n", encoding="utf-8")
            bundle = module.RenderedBundle(
                transcript="",
                system_prompt="original",
                raw_reference="",
                expected_json_blocks=[{"ok": True}],
                raw_stream_payloads=[],
                thinking="",
                reply="",
                system_contents=["original"],
            )
            with self.assertRaises(module.VerificationError):
                module.verify_bundle(transcript_path, raw_path, bundle, system_path)

    def test_failed_verification_does_not_replace_existing_outputs(self) -> None:
        module = load_renderer()
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            existing = output_dir / "successful-stream.md"
            existing.write_text("previous verified transcript\n", encoding="utf-8")

            def fail(*_args: object, **_kwargs: object) -> None:
                raise module.VerificationError("forced verification failure")

            typed_module = cast(Any, module)
            original = typed_module.verify_bundle
            typed_module.verify_bundle = fail
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    code = typed_module.main(
                        [
                            str(FIXTURES / "successful-stream.log"),
                            "--output-dir",
                            str(output_dir),
                            "--force",
                        ]
                    )
            finally:
                typed_module.verify_bundle = original

            self.assertEqual(code, 4)
            self.assertEqual(
                existing.read_text(encoding="utf-8"),
                "previous verified transcript\n",
            )
            self.assertEqual([path.name for path in output_dir.iterdir()], [existing.name])

    def test_rejects_non_text_response_content(self) -> None:
        module = load_renderer()
        response = module.ResponseBlock(
            timestamp="",
            status="200",
            headers=[("Content-Type", "application/json")],
            body=json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": [{"type": "text", "text": "hi"}],
                            },
                            "finish_reason": "stop",
                        }
                    ]
                }
            ),
        )
        with self.assertRaises(module.UnsupportedCapture):
            module.decode_completion(response)

    def test_parse_timestamp_accepts_z_suffix(self) -> None:
        module = load_renderer()
        moment = module.parse_timestamp("2026-10-26T10:11:12.123456789Z")
        self.assertIsNotNone(moment)
        assert moment is not None
        self.assertIsNotNone(moment.tzinfo)

    def test_extract_json_fences_preserves_order(self) -> None:
        module = load_renderer()
        text = "```json\n{\"first\": 1}\n```\n\ntext\n\n```json\n{\"second\": 2}\n```\n"
        self.assertEqual(
            module.extract_json_fences(text),
            [{"first": 1}, {"second": 2}],
        )


if __name__ == "__main__":
    unittest.main()
