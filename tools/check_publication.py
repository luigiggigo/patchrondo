"""Heuristic pre-publication check. Reports paths/lines, never matched secrets.

Scans the working tree, not Git history. Generated caches/builds are excluded.
Use a dedicated secret scanner and review the staged diff before publishing.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re

SKIP_DIRS = {".git", ".venv", "venv", ".build-venv", ".verify-venv", ".test-tmp",
             "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
             "build", "dist", "htmlcov", "node_modules"}
PRIVATE_DIRS = {".patchrondo", ".codex", ".claude", ".aws"}
SKIP_FILES = {"AGENTS.md", "agents.md"}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    "provider key": re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{24,}\b"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "credential assignment": re.compile(
        r'''(?i)["']?(?:api[_-]?key|access[_-]?token|password|secret)["']?\s*[:=]\s*["'][A-Za-z0-9_+/=-]{24,}["']'''),
}


def scan(root: Path) -> tuple[int, list[str]]:
    findings = []
    count = 0
    for folder, directories, files in os.walk(root, followlinks=False):
        base = Path(folder)
        for name in list(directories):
            path = base / name
            if name in PRIVATE_DIRS:
                findings.append(f"{path.relative_to(root)}: private local state")
                directories.remove(name)
            elif path.is_symlink():
                findings.append(f"{path.relative_to(root)}: symlink requires manual review")
                directories.remove(name)
            elif name in SKIP_DIRS or name.endswith(".egg-info"):
                directories.remove(name)
        for name in sorted(files):
            if name in SKIP_FILES:
                continue
            path = base / name
            relative = path.relative_to(root)
            if path.is_symlink():
                findings.append(f"{relative}: symlink requires manual review")
                continue
            if name.endswith((".pyc", ".pyo")):
                continue
            count += 1
            if (name == ".env" or (name.startswith(".env.") and name != ".env.example")
                or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".log"}
                or name in {"state.json", ".run.lock"}
                or name.endswith(".active-process.json")):
                findings.append(f"{relative}: credential, log or runtime-state file")
                continue
            if path.stat().st_size > 2_000_000:
                findings.append(f"{relative}: large file requires manual review")
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                findings.append(f"{relative}: binary/non-UTF-8 file requires manual review")
                continue
            for index, line in enumerate(content.splitlines(), 1):
                for label, pattern in PATTERNS.items():
                    if pattern.search(line):
                        findings.append(f"{relative}:{index}: possible {label}")
    return count, findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        parser.error("root must be an existing directory")
    count, findings = scan(root)
    for item in findings:
        print(item)
    print(f"Checked {count} files; {len(findings)} finding(s). Git history is not scanned.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
