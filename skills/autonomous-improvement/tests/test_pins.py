"""Phase 5.4: the pin ratchet — wording-pin debt is visible and only shrinks.

``pins.py scan`` maps every assertIn(<literal>, ...) pin in the doc-reading
test files; ``tests/pins.json`` maps each to an eval case or null; the
baseline file holds the wording-only count and may only go down.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support

SCRIPTS = support.SCRIPTS
SKILL_ROOT = support.SKILL_ROOT



class PinScanTests(unittest.TestCase):
    def test_pins_json_matches_live_scan(self) -> None:
        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "pins.py"), "scan", "--json"],
            cwd=SKILL_ROOT,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        live = json.loads(proc.stdout)
        mapped = json.loads((SKILL_ROOT / "tests" / "pins.json").read_text())["pins"]
        self.assertEqual(
            [(p["file"], p["pin"]) for p in live],
            [(p["file"], p["pin"]) for p in mapped],
            "tests/pins.json is stale: re-run `pins.py scan --json` and remap",
        )

    def test_baseline_is_a_single_integer(self) -> None:
        baseline = json.loads(
            (SKILL_ROOT / "tests" / "pins.baseline.json").read_text()
        )
        self.assertEqual(set(baseline), {"null_pins"})
        self.assertIsInstance(baseline["null_pins"], int)

    def test_check_passes_on_the_committed_map(self) -> None:
        proc = support.run(
            [support.PYTHON, str(SCRIPTS / "pins.py"), "check"], cwd=SKILL_ROOT
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


class PinCheckLogicTests(unittest.TestCase):
    """The check's failure modes, exercised on copies (never the real map)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # A miniature tests dir with one doc-reading file carrying two pins.
        tests = self.tmp / "tests"
        tests.mkdir()
        (tests / "test_sample.py").write_text(
            "# reads SKILL.md\n"
            "import unittest\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_x(self):\n"
            "        self.assertIn('toolbox of passes', 'x')\n"
            "        self.assertIn('bare invocation', 'x')\n",
            encoding="utf-8",
        )
        # Copy pins.py next to the miniature tree and point its constants at it.
        self.mini = self.tmp / "scripts" / "autonomous"
        self.mini.mkdir(parents=True)
        (self.mini / "pins.py").write_text(
            (SCRIPTS / "pins.py").read_text().replace(
                'SKILL_ROOT = Path(__file__).resolve().parents[2]',
                f'SKILL_ROOT = Path({str(self.tmp)!r})',
            ),
            encoding="utf-8",
        )

    def _map(self, pins: list, null_pins: int) -> None:
        (self.tmp / "tests" / "pins.json").write_text(
            json.dumps({"pins": pins}), encoding="utf-8"
        )
        (self.tmp / "tests" / "pins.baseline.json").write_text(
            json.dumps({"null_pins": null_pins}), encoding="utf-8"
        )

    def _check(self) -> list:
        import importlib.util

        spec = importlib.util.spec_from_file_location("mini_pins", self.mini / "pins.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.check_maps()

    def test_disappeared_pin_is_reported(self) -> None:
        self._map(
            [
                {"file": "tests/test_sample.py", "pin": "toolbox of passes", "case": None},
                {"file": "tests/test_sample.py", "pin": "no longer there", "case": None},
            ],
            null_pins=2,
        )
        problems = self._check()
        self.assertTrue(any("disappeared" in p for p in problems), problems)

    def test_null_ratchet_violation_is_reported(self) -> None:
        self._map(
            [
                {"file": "tests/test_sample.py", "pin": "toolbox of passes", "case": None},
                {"file": "tests/test_sample.py", "pin": "bare invocation", "case": None},
            ],
            null_pins=1,  # floor below the actual 2 wording-only pins
        )
        problems = self._check()
        self.assertTrue(any("rose" in p for p in problems), problems)

    def test_fewer_nulls_than_baseline_passes(self) -> None:
        self._map(
            [
                {"file": "tests/test_sample.py", "pin": "toolbox of passes", "case": "E7-1-weak-test"},
                {"file": "tests/test_sample.py", "pin": "bare invocation", "case": None},
            ],
            null_pins=5,  # room to shrink later
        )
        self.assertEqual(self._check(), [])


if __name__ == "__main__":
    unittest.main()
