"""Unit tests for cliproxy-integration helper scripts."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _StubGatewayHandler(BaseHTTPRequestHandler):
    require_bearer: str | None = None
    models_payload: dict = {"data": []}
    reject_status: int | None = None
    reject_body: str = ""

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return

    def _check_auth(self) -> bool:
        if self.require_bearer is not None:
            expected = f"Bearer {self.require_bearer}"
            if self.headers.get("Authorization") != expected:
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"unauthorized"}}')
                return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        if self.reject_status is not None:
            self.send_response(self.reject_status)
            self.end_headers()
            self.wfile.write(self.reject_body.encode("utf-8"))
            return
        if not self._check_auth():
            return
        if self.path.startswith("/v1/models") or self.path.startswith("/models"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(self.models_payload).encode("utf-8"))
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if not self._check_auth():
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"code":"bad_json","message":"malformed json"}}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        resp = {
            "id": "chatcmpl-1",
            "model": body.get("model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
        }
        self.wfile.write(json.dumps(resp).encode("utf-8"))


class CliproxyScriptsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _start_server(
        self,
        *,
        require_bearer: str | None = None,
        models_payload: dict | None = None,
        reject_status: int | None = None,
        reject_body: str = "",
    ) -> str:
        class Handler(_StubGatewayHandler):
            pass

        Handler.require_bearer = require_bearer
        Handler.models_payload = models_payload or {"data": []}
        Handler.reject_status = reject_status
        Handler.reject_body = reject_body

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return f"http://127.0.0.1:{server.server_port}/v1"

    def test_label_models_provider_flag_ignores_unlabelled_sibling_providers(self) -> None:
        cfg_path = self.tmp / "opencode.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "provider": {
                        "cliproxy": {
                            "models": {
                                "gemini-pro": {
                                    "name": "Gemini Pro",
                                    "limit": {"context": 1048576, "output": 65536},
                                }
                            }
                        },
                        "other-gw": {
                            "models": {
                                "unlabelled-m": {
                                    "name": "Unlabelled Sibling",
                                    "limit": {"context": 200000, "output": 8192},
                                }
                            }
                        },
                    },
                    "providers": {
                        "cliproxy": {
                            "models": {
                                "gemini-pro": {
                                    "name": "Gemini Pro",
                                    "limit": {"context": 1048576, "output": 65536},
                                }
                            }
                        },
                        "other-gw": {
                            "models": {
                                "unlabelled-m": {
                                    "name": "Unlabelled Sibling",
                                    "limit": {"context": 200000, "output": 8192},
                                }
                            }
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "label-models.py"),
                "--config",
                str(cfg_path),
                "--provider",
                "cliproxy",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")
        written = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(
            written["provider"]["cliproxy"]["models"]["gemini-pro"]["name"],
            "Gemini Pro_1M",
        )
        self.assertEqual(
            written["provider"]["other-gw"]["models"]["unlabelled-m"]["name"],
            "Unlabelled Sibling",
        )

    def test_sync_limits_accepts_omniroute_api_key_and_preserves_existing_output(self) -> None:
        base_url = self._start_server(
            require_bearer="omni-secret-token",
            models_payload={
                "data": [
                    {"id": "m-ctx-only", "context_length": 1000000},
                    {"id": "m-no-limits"},
                ]
            },
        )
        cfg_path = self.tmp / "opencode.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "provider": {
                        "omniroute": {
                            "models": {
                                "m-ctx-only": {"limit": {"context": 128000, "output": 16384}},
                                "m-no-limits": {"limit": {"context": 64000, "output": 4096}},
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        env = {k: v for k, v in os.environ.items() if k != "GATEWAY_API_KEY"}
        env["OMNIROUTE_API_KEY"] = "omni-secret-token"
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "sync-limits.py"),
                "--base-url",
                base_url,
                "--config",
                str(cfg_path),
                "--provider",
                "omniroute",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")
        written = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(
            written["provider"]["omniroute"]["models"]["m-ctx-only"]["limit"],
            {"context": 1000000, "output": 16384},
        )
        self.assertEqual(
            written["provider"]["omniroute"]["models"]["m-no-limits"]["limit"],
            {"context": 64000, "output": 4096},
        )

    def test_onboard_keys_validate_key_surfaces_http_error_body(self) -> None:
        base_url = self._start_server(
            reject_status=403,
            reject_body='{"error":"API_KEY_INVALID: key expired"}',
        )
        mod = _load_module("onboard_keys", "onboard-keys.py")
        res = mod.validate_key("badkey", base_url, "/models", timeout=5, auth_style="header")
        self.assertFalse(res["ok"])
        self.assertEqual(res["why"], "rejected")
        self.assertIn("403", str(res["detail"]))
        self.assertIn("API_KEY_INVALID", str(res["detail"]))

    def test_probe_models_works_on_keyless_local_gateway_and_escapes_model_id(self) -> None:
        tricky_id = 'local/model"quoted'
        base_url = self._start_server(
            require_bearer=None,
            models_payload={"data": [{"id": tricky_id, "owned_by": "local"}]},
        )
        proc = subprocess.run(
            ["bash", str(SCRIPTS / "probe-models.sh"), "--base-url", base_url],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("all models responded", proc.stdout)


if __name__ == "__main__":
    unittest.main()
