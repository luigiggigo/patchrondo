"""Git worktree isolation, preserving the caller's checkout."""
from __future__ import annotations

from pathlib import Path
import hashlib
import os
import subprocess


def git(*args: str, cwd: Path, hooks_dir: Path | None = None) -> str:
    cmd = ["git"]
    if hooks_dir is not None:
        cmd += ["-c", f"core.hooksPath={hooks_dir}"]
    proc = subprocess.run(cmd + ["-C", str(cwd), *args], text=True,
                          encoding="utf-8", errors="replace", capture_output=True, timeout=45, check=False)
    if proc.returncode:
        raise RuntimeError(f"Git: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.rstrip("\r\n")


def repo_root(repo: Path) -> Path:
    if not repo.is_dir():
        raise ValueError(f"Repository not found: {repo}")
    root = Path(git("rev-parse", "--show-toplevel", cwd=repo)).resolve()
    if root != repo.resolve():
        raise ValueError("Provide the Git repository root, not a subdirectory")
    return root


def create_worktree(repo: Path, destination: Path, task_id: str, hooks_dir: Path) -> str:
    if git("status", "--porcelain", cwd=repo):
        raise RuntimeError("Main repository is not clean: commit or stash changes before creating a task")
    if destination.exists():
        raise RuntimeError(f"Worktree already exists: {destination}")
    base = git("rev-parse", "HEAD", cwd=repo)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    git("worktree", "add", "--no-track", "-b", f"patchrondo/{task_id}",
        str(destination), base, cwd=repo, hooks_dir=hooks_dir)
    return base


def changes(worktree: Path) -> list[str]:
    # --porcelain handles tracked and untracked paths; avoid leaking file contents.
    return git("status", "--short", "--untracked-files=all", cwd=worktree).splitlines()


def stat(worktree: Path) -> str:
    return git("diff", "--stat", "HEAD", cwd=worktree)


def fingerprint(worktree: Path) -> str:
    """Digest tracked/untracked files, symlink targets and HEAD, excluding ignored files."""
    digest = hashlib.sha256()
    digest.update(git("rev-parse", "HEAD", cwd=worktree).encode("utf-8"))
    digest.update(git("status", "--porcelain", cwd=worktree).encode("utf-8"))
    names = git("ls-files", "-z", "--cached", "--others", "--exclude-standard", cwd=worktree)
    for name in sorted(set(names.split("\0")) - {""}):
        digest.update(name.encode("utf-8") + b"\0")
        path = worktree / name
        if path.is_symlink():
            digest.update(b"symlink\0" + os.readlink(path).encode("utf-8"))
        elif path.is_file():
            content = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(65536), b""):
                    content.update(chunk)
            digest.update(b"file\0" + content.digest())
        else:
            digest.update(b"missing-or-directory\0")
    return digest.hexdigest()
