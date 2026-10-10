"""The protection check must stay in step with the sources and never touch the working tree."""
import hashlib
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("recovery_checks", ROOT / "tools" / "recovery_checks.py")
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)


def fingerprint() -> str:
    digest = hashlib.sha256()
    for file in sorted((ROOT / "src" / "patchrondo").glob("*.py")):
        digest.update(file.name.encode() + file.read_bytes())
    return digest.hexdigest()


class RecoveryCheckTests(unittest.TestCase):
    def test_every_protection_pattern_matches_the_sources_once(self):
        names = [mutation.name for mutation in checks.MUTATIONS]
        self.assertEqual(len(names), len(set(names)))
        for mutation in checks.MUTATIONS:
            with self.subTest(protection=mutation.name):
                text = (checks.PACKAGE / mutation.file).read_bytes().decode("utf-8")
                self.assertEqual(text.count(mutation.old), 1)
                self.assertNotEqual(mutation.old, mutation.new)

    def test_removed_protection_is_detected_without_changing_the_working_tree(self):
        before = fingerprint()
        mutation = next(m for m in checks.MUTATIONS if m.name == "standard output ignored beside standard error")
        self.assertEqual(checks.run_mutation(None, tests=("test_adapters",)), (False, "OK"))
        detected, summary = checks.run_mutation(mutation, tests=("test_adapters",))
        self.assertTrue(detected, summary)
        self.assertIn("FAILED", summary)
        # A pattern that no longer matches is reported, never silently skipped.
        stale = mutation._replace(old="this text is not in the sources")
        self.assertEqual(checks.run_mutation(stale, tests=("test_adapters",)),
                         (False, "pattern occurs 0 times in providers.py"))
        self.assertEqual(fingerprint(), before)


if __name__ == "__main__":
    unittest.main()
