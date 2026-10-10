import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from patchrondo import rag
from patchrondo.core import initialize, create_task, run_task, config
from patchrondo.providers import AgentReply
from patchrondo.storage import read_json, save_json


class ChunkingTests(unittest.TestCase):
    def test_identifier_split_and_query_terms(self):
        self.assertEqual(rag.split_identifier("createWorktree"), ["create", "worktree"])
        self.assertEqual(rag.split_identifier("parse_HTTPResponse"), ["parse", "http", "response"])
        self.assertEqual(rag.split_identifier("token"), [])
        terms = rag.query_terms("Handle the JWT expiration in verifyToken and the token")
        self.assertEqual(terms, ["handle", "jwt", "expiration", "verifytoken", "verify", "token"])

    def test_chunks_follow_definitions_and_headings(self):
        code = "\n".join(["import os", ""] + [f"def f{i}():\n" + "    x = 1\n" * 8 for i in range(3)])
        chunks = rag.chunk_text(code, kind="python")
        self.assertEqual([c[2] for c in chunks], ["f0", "f1", "f2"])
        self.assertEqual(chunks[1][0], code.splitlines().index("def f1():") + 1)
        body = "        x = 1\n" * 8
        cls = "class Pool:\n" + "".join(f"    def m{i}(self):\n{body}" for i in range(2))
        cls += f"VALUE = 1\n    def stray(self):\n{body}"
        self.assertEqual([c[2] for c in rag.chunk_text(cls, kind="python")], ["Pool", "Pool.m1", "stray"])
        prose = "x = 1\n" * 9 + "    type of this module\n" + "y = 2\n" * 9
        self.assertEqual([c[2] for c in rag.chunk_text(prose, kind="python")], [""])
        doc = "# Title\n" + "text\n" * 10 + "## Usage\n" + "more\n" * 10
        self.assertEqual([c[2] for c in rag.chunk_text(doc, kind="doc")], ["Title", "Usage"])
        long = "\n".join(f"line {i}" for i in range(200))
        self.assertTrue(all(c[1] - c[0] < rag.MAX_CHUNK_LINES for c in rag.chunk_text(long)))

    def test_secret_and_generated_files_are_not_eligible(self):
        for name in (".env", "config/.env.prod", "certs/server.pem", "package-lock.json",
                     "dist/app.min.js", "image.png", "run.log"):
            self.assertFalse(rag.eligible(name), name)
        for name in ("src/app.py", ".env.example", "README.md", "Makefile", "web/index.tsx"):
            self.assertTrue(rag.eligible(name), name)

    def test_render_respects_budget_and_fences(self):
        hits = [rag.Hit("a.md", 1, 3, "A", "```py\ncode\n```", 2.0),
                rag.Hit("b.py", 1, 2, "", "x" * 5000, 1.0)]
        text = rag.render(hits, 1000)
        self.assertIn("### a.md:1-3 · A\n````\n```py", text)
        self.assertNotIn("b.py", text)
        self.assertEqual(rag.render([], 1000), "")


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        self.home = root / "state"
        self._git("init", "-q")
        (self.repo / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
        (self.repo / "auth.py").write_text(
            "def verify_token(token):\n    return check_expiration(token.exp)\n", encoding="utf-8")
        (self.repo / "billing.py").write_text("def charge_invoice(amount):\n    return amount\n", encoding="utf-8")
        (self.repo / "ignored.py").write_text("def verify_token_secret():\n    pass\n", encoding="utf-8")
        (self.repo / ".env").write_text("TOKEN=verify_token\n", encoding="utf-8")
        (self.repo / "blob.py").write_bytes(b"verify_token\0binary")

    def tearDown(self):
        self.tmp.cleanup()

    def _git(self, *args, cwd=None):
        subprocess.run(["git", "-C", str(cwd or self.repo), *args], check=True, capture_output=True)

    def _search(self, query, root=None):
        with rag.Index(rag.index_path(self.home)) as index:
            index.update(root or self.repo)
            return index.search(root or self.repo, query)

    def test_ranks_symbols_and_skips_ignored_secret_and_binary_files(self):
        hits = self._search("verifyToken expiration")
        self.assertEqual([h.path for h in hits], ["auth.py"])
        self.assertEqual((hits[0].start, hits[0].end, hits[0].symbol), (1, 2, "verify_token"))
        self.assertEqual(self._search("charge invoice")[0].path, "billing.py")
        self.assertEqual(self._search("the and"), [])

    def test_incremental_updates_and_deletions(self):
        with rag.Index(rag.index_path(self.home)) as index:
            first = index.update(self.repo)
            self.assertEqual(first["updated"], 2)  # auth.py and billing.py only
            self.assertEqual(index.update(self.repo)["chunks_added"], 0)
            (self.repo / "billing.py").write_text("def refund_payment():\n    pass\n", encoding="utf-8")
            (self.repo / "auth.py").unlink()
            stats = index.update(self.repo)
            self.assertEqual((stats["removed"], stats["chunks_added"]), (1, 1))
            self.assertEqual(index.search(self.repo, "verify_token charge_invoice"), [])
            self.assertEqual(index.search(self.repo, "refund payment")[0].path, "billing.py")
            # Chunks of deleted files and replaced versions are garbage-collected.
            self.assertEqual(index.db.execute("SELECT count(*) FROM chunk_map").fetchone()[0], 1)
            self.assertEqual(index.db.execute("SELECT count(*) FROM chunks_fts").fetchone()[0], 1)

    def test_worktrees_share_chunks_and_are_isolated(self):
        self._git("add", "-A")
        self._git("-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "init")
        tree = Path(self.tmp.name) / "tree"
        self._git("worktree", "add", "-q", str(tree))
        (tree / "auth.py").write_text("def verify_session(session):\n    pass\n", encoding="utf-8")
        with rag.Index(rag.index_path(self.home)) as index:
            index.update(self.repo)
            self.assertEqual(index.update(tree)["chunks_added"], 1)
            self.assertEqual(index.search(tree, "check_expiration"), [])
            self.assertEqual(index.search(self.repo, "check_expiration")[0].path, "auth.py")
            self.assertEqual(index.search(tree, "verify_session")[0].symbol, "verify_session")
            self._git("worktree", "remove", "--force", str(tree))
            index.update(self.repo)
            left = index.db.execute("SELECT count(*) FROM blobs WHERE path='auth.py'").fetchone()[0]
            self.assertEqual(left, 1)

    def test_candidate_limit_applies_within_the_searched_worktree(self):
        body = "    needle = needle + 1\n" * rag.MIN_CHUNK_LINES
        (self.repo / "many.py").write_text(
            "".join(f"def needle_{i}():\n{body}" for i in range(rag.CANDIDATES + 20)), encoding="utf-8")
        self._git("add", "-A")
        self._git("-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "init")
        tree = Path(self.tmp.name) / "tree"
        self._git("worktree", "add", "-q", str(tree))
        (tree / "many.py").write_text("def unrelated():\n    pass\n", encoding="utf-8")
        (tree / "target.py").write_text(
            "def lookup(value):\n" + "    value = value + 1\n" * 20 + "    return needle\n", encoding="utf-8")
        with rag.Index(rag.index_path(self.home)) as index:
            index.update(self.repo)
            index.update(tree)
            # More than CANDIDATES better-ranked chunks belong only to the other worktree.
            self.assertEqual([h.path for h in index.search(tree, "needle")], ["target.py"])
            self.assertEqual([h.path for h in index.search(self.repo, "needle")], ["many.py"] * rag.PER_FILE)


class WorkflowRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        self.home = root / "state"
        for args in (("init", "-q"), ("config", "user.name", "T"), ("config", "user.email", "t@example.com")):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        (self.repo / "tokens.py").write_text("def check_jwt_expiration(claims):\n    return True\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "init"], check=True, capture_output=True)
        initialize(self.home, self.repo)
        cfg = read_json(self.home / "config.json")
        cfg["tests"] = {"enabled": True, "trust_acknowledged": True,
                        "commands": [["git", "--version"]]}
        save_json(self.home / "config.json", cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self):
        prompts = []

        class Provider:
            def invoke(self, provider, role, prompt, workspace, run_dir):
                prompts.append((role, prompt))
                if role == "developer":
                    (workspace / "tokens.py").write_text("def check_jwt_expiration(claims):\n    return False\n",
                                                         encoding="utf-8")
                    return AgentReply(text="Changed tokens.py", provider=provider)
                return AgentReply(text=json.dumps({"verdict": "APPROVED", "summary": "ok", "issues": []}),
                                  provider=provider)

        task_id, _ = create_task(self.home, title="Reject expired JWT", description="Fix the jwt expiration check",
                                 acceptance=["Expired tokens fail"], developer="claude", reviewer="codex")
        return run_task(self.home, task_id, adapter=Provider()), dict(prompts)

    def test_prompts_include_retrieved_context(self):
        state, prompts = self._run()
        self.assertEqual(state["status"], "done")
        for role in ("developer", "reviewer"):
            self.assertIn("## Retrieved repository context", prompts[role])
            self.assertIn("### tokens.py:1-2 · check_jwt_expiration", prompts[role])
        self.assertIn("return False", prompts["reviewer"])

    def test_disabled_or_failing_retrieval_does_not_block(self):
        cfg = read_json(self.home / "config.json")
        cfg["rag"]["enabled"] = False
        save_json(self.home / "config.json", cfg)
        state, prompts = self._run()
        self.assertEqual(state["status"], "done")
        self.assertNotIn("Retrieved repository context", prompts["developer"])
        cfg["rag"]["enabled"] = True
        save_json(self.home / "config.json", cfg)
        with patch("patchrondo.rag.retrieve", side_effect=OSError("disk full")):
            state, prompts = self._run()
        self.assertEqual(state["status"], "done")
        self.assertIn("retrieval_failed", [e["event"] for e in state["history"]])

    def test_config_validation_and_legacy_default(self):
        cfg = read_json(self.home / "config.json")
        del cfg["rag"]
        save_json(self.home / "config.json", cfg)
        self.assertFalse(config(self.home)["rag"]["enabled"])
        cfg["rag"] = {"enabled": "yes", "max_chunks": 8, "max_chars": 12000}
        save_json(self.home / "config.json", cfg)
        with self.assertRaises(ValueError):
            config(self.home)
        cfg["rag"] = {"enabled": True, "max_chunks": 0, "max_chars": 12000}
        save_json(self.home / "config.json", cfg)
        with self.assertRaises(ValueError):
            config(self.home)


if __name__ == "__main__":
    unittest.main()
