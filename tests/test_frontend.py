"""Static checks of the shipped interface: untrusted text handling, self-contained assets, packaging."""
from importlib import resources
from pathlib import Path
import re
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "patchrondo" / "static"
FILES = sorted(file for file in STATIC.rglob("*") if file.is_file())
SCRIPTS = [file for file in FILES if file.suffix == ".js"]
STYLES = [file for file in FILES if file.suffix == ".css"]
# A link a person can follow from the page, and the SVG namespace name. Nothing is ever loaded from them.
DOCUMENTATION = ("https://github.com/luigiggigo/patchrondo", "http://www.w3.org/2000/svg")


class FrontendTests(unittest.TestCase):
    def test_agent_text_can_only_reach_the_page_as_text(self):
        """Task data, logs and reviews are untrusted: no API that parses a string as HTML or code is used."""
        forbidden = re.compile(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\s*\(|new Function|"
                               r"DOMParser|createContextualFragment|srcdoc|javascript:|setAttribute\(\s*[\"']on|"
                               r"set(?:Timeout|Interval)\(\s*[\"'`]")
        for file in SCRIPTS:
            text = file.read_text(encoding="utf-8")
            with self.subTest(file=file.name):
                self.assertIsNone(forbidden.search(text), forbidden.search(text))
        dom = (STATIC / "js" / "dom.js").read_text(encoding="utf-8")
        self.assertIn("el.append(child instanceof Node ? child : String(child))", dom)  # strings become text nodes

    def test_page_has_no_inline_code_and_marks_every_asset_with_the_nonce(self):
        page = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"<script(?![^>]*\bsrc=)[^>]*>", page))  # no inline script
        self.assertNotIn("<style", page)
        self.assertIsNone(re.search(r"\son[a-z]+\s*=", page))  # no inline event handlers
        self.assertIsNone(re.search(r"\sstyle\s*=", page))
        tags = re.findall(r"<(?:script|link rel=\"stylesheet\")[^>]*>", page)
        self.assertEqual(len(tags), 4)
        for tag in tags:
            self.assertIn('nonce="__NONCE__"', tag)
        self.assertIn('<html lang="en"', page)
        self.assertIn('<meta name="referrer" content="no-referrer">', page)

    def test_nothing_is_loaded_from_outside_the_package(self):
        names = {file.relative_to(STATIC).as_posix() for file in FILES}
        page = (STATIC / "index.html").read_text(encoding="utf-8")
        for reference in re.findall(r"(?:src|href)=\"([^\"]+)\"", page):
            with self.subTest(reference=reference):
                self.assertTrue(reference.startswith("/assets/") or reference == "#main", reference)
                if reference.startswith("/assets/"):
                    self.assertIn(reference.removeprefix("/assets/"), names)
        for file in SCRIPTS:
            text = file.read_text(encoding="utf-8")
            for target in re.findall(r"(?:^|\s)(?:import|export)\s[^;]*?from\s+\"([^\"]+)\"", text) + re.findall(r"import\(\s*\"([^\"]+)\"", text):
                with self.subTest(file=file.name, target=target):
                    self.assertTrue(target.startswith("."), "modules are imported by relative path only")
                    self.assertTrue((file.parent / target).resolve().is_file(), target)
            for asset in re.findall(r"[\"'`](/assets/[^\"'`?]+)[\"'`]", text):
                self.assertIn(asset.removeprefix("/assets/"), names, f"{file.name}: {asset}")
            for url in re.findall(r"https?://[^\s\"'`)]+", text):
                self.assertTrue(url.startswith(DOCUMENTATION), f"{file.name}: {url}")
            self.assertIsNone(re.search(r"\bfetch\(\s*[\"'`]https?:", text))
        for file in STYLES:
            text = file.read_text(encoding="utf-8")
            self.assertNotIn("@import", text)
            self.assertNotIn("http", text.lower(), file.name)
            self.assertNotIn("url(", text, file.name)  # no fonts, images or anything else pulled in by a style sheet

    def test_token_is_never_put_in_a_url(self):
        api = (STATIC / "js" / "api.js").read_text(encoding="utf-8")
        self.assertIn('"X-PatchRondo-Token": token', api)
        self.assertIn("history.replaceState", api)  # removed from the address bar on arrival
        for file in SCRIPTS:
            text = file.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"[?&]token=", text), file.name)
            self.assertNotIn("localStorage.setItem(KEY", text)

    def test_motion_and_focus_are_respected(self):
        tokens = (STATIC / "css" / "tokens.css").read_text(encoding="utf-8")
        self.assertIn("@media (prefers-reduced-motion: reduce)", tokens)
        self.assertIn(":focus-visible", tokens)
        self.assertIn('[data-theme="light"]', tokens)
        rondo = (STATIC / "js" / "rondo.js").read_text(encoding="utf-8")
        self.assertEqual(set(re.findall(r"/assets/([\w-]+\.webp)", rondo)), {"rondo.webp", "rondo-head.webp"})  # the official images only

    def test_every_asset_is_packaged_served_and_has_unix_line_endings(self):
        with (ROOT / "pyproject.toml").open("rb") as stream:
            patterns = tomllib.load(stream)["tool"]["setuptools"]["package-data"]["patchrondo"]
        package = ROOT / "src" / "patchrondo"
        packaged = {file for pattern in patterns for file in package.glob(pattern)}
        self.assertEqual(sorted(set(FILES) - packaged), [], "files missing from package-data")
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        for suffix in sorted({file.suffix for file in FILES}):
            self.assertIn(f"*{suffix}", manifest)
        from patchrondo.ui import ASSET_TYPES, _load_assets
        page, assets = _load_assets()
        self.assertEqual(sorted(assets), sorted(file.relative_to(STATIC).as_posix() for file in FILES if file.name != "index.html"))
        self.assertTrue(all(Path(name).suffix in ASSET_TYPES for name in assets))
        self.assertIn("__NONCE__", page)
        for file in FILES:
            if file.suffix in {".js", ".css", ".html"}:
                self.assertNotIn(b"\r\n", file.read_bytes(), file.name)
        self.assertTrue(resources.files("patchrondo").joinpath("static/index.html").is_file())


if __name__ == "__main__":
    unittest.main()
