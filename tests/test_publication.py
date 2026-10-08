import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "check_publication", Path(__file__).resolve().parents[1] / "tools" / "check_publication.py")
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)


class PublicationTests(unittest.TestCase):
    def test_finds_credentials_without_printing_value(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            secret = "ghp_" + "A" * 36
            (root / "settings.py").write_text(f"token = '{secret}'", encoding="utf-8")
            (root / ".env").write_text("password=private", encoding="utf-8")
            _, findings = publication.scan(root)
        self.assertTrue(any("GitHub token" in f for f in findings))
        self.assertTrue(any(".env" in f for f in findings))
        self.assertNotIn(secret, "\n".join(findings))

    def test_skips_builds_but_flags_local_agent_state(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "dist").mkdir()
            (root / "dist" / "package.whl").write_bytes(b"\xff")
            (root / ".patchrondo").mkdir()
            (root / "README.md").write_text("Public documentation", encoding="utf-8")
            count, findings = publication.scan(root)
        self.assertEqual(count, 1)
        self.assertEqual(len(findings), 1)
        self.assertIn(".patchrondo", findings[0])


if __name__ == "__main__":
    unittest.main()
