import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from patchrondo.cli import parser


class CLITests(unittest.TestCase):
    def test_state_home_uses_patchrondo_name(self):
        with patch.dict(os.environ):
            os.environ.pop("PATCHRONDO_HOME", None)
            self.assertEqual(parser().parse_args(["list"]).home,
                             Path("~/.patchrondo").expanduser())
        with patch.dict(os.environ, {"PATCHRONDO_HOME": "custom-state"}):
            self.assertEqual(parser().parse_args(["list"]).home, Path("custom-state"))

    def test_help_works_with_legacy_redirected_output_encoding(self):
        root = Path(__file__).resolve().parents[1]
        env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONPATH": str(root / "src")}
        result = subprocess.run([sys.executable, "-m", "patchrondo", "--help"],
                                cwd=root, env=env, capture_output=True,
                                text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Claude ↔ Codex", result.stdout)


if __name__ == "__main__":
    unittest.main()
