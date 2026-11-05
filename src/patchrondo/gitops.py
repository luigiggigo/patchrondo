"""Git worktree isolation, preserving the caller's checkout."""
from __future__ import annotations

from pathlib import Path
import hashlib
import os
import re
import stat as filemode  # `stat` is the diff summary below
import subprocess


class GitError(RuntimeError):
    """A Git command failed. `kind` is set when the failure has a cause of its own."""

    def __init__(self, message: str, kind: str = "system_error", folder: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.folder = folder  # the folder Git refused, when it named one


# Git refuses a repository that belongs to another user (safe.directory in git-config(1), Git 2.35.2
# and later) with "fatal: detected dubious ownership in repository at '<folder>'".
_DUBIOUS = re.compile(r"detected dubious ownership in repository at '([^'\n]+)'")


def git_failure(output: str) -> GitError:
    """Classify the error output of a Git command."""
    match = _DUBIOUS.search(output)
    if match:
        return GitError(f"Git: {output}", "git_dubious_ownership", match.group(1))
    return GitError(f"Git: {output}")


def git(*args: str, cwd: Path, hooks_dir: Path | None = None) -> str:
    cmd = ["git"]
    if hooks_dir is not None:
        cmd += ["-c", f"core.hooksPath={hooks_dir}"]
    proc = subprocess.run(cmd + ["-C", str(cwd), *args], text=True,
                          encoding="utf-8", errors="replace", capture_output=True, timeout=45, check=False)
    if proc.returncode:
        raise git_failure(proc.stderr.strip() or proc.stdout.strip())
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


class FingerprintError(OSError):
    """The state of the task's files could not be established: nothing may be taken as verified on it."""

    kind = "fingerprint_unavailable"


NESTED = 3          # repositories inside repositories (submodules, clones) whose files are hashed
READS = 3           # attempts at reading a file that keeps changing while it is read
MOUNT_POINT = 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT of winnt.h, a junction; `stat` defines it on Windows only


# Options of a reading by somebody who must not disturb the copy (the dashboard): no optional lock (`git status`
# then does not write the index it refreshed) and no file-system monitor program named by the repository's
# configuration. Neither changes what Git prints, so the digest is the same.
READER = ("--no-optional-locks", "-c", "core.fsmonitor=false")


def _names(worktree: Path, options: tuple[str, ...] = ()) -> list[bytes]:
    """What Git does not ignore, as the bytes Git names it with: a name that is not UTF-8 is still the file's name."""
    proc = subprocess.run(["git", "-C", str(worktree), *options, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                          capture_output=True, timeout=45, check=False)
    if proc.returncode:
        raise git_failure((proc.stderr or proc.stdout).decode("utf-8", "replace").strip())
    return sorted(set(proc.stdout.split(b"\0")) - {b""})


def _linked(path: Path) -> bool:
    """A symbolic link or a junction. Other reparse points (a cloud file, a deduplicated one) hold their own data."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    return filemode.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) == MOUNT_POINT


def _through_link(worktree: Path, name: str, seen: dict) -> bool:
    """Whether a folder on the way to `name` is a link or a junction: what lies behind it is outside the copy."""
    folder = worktree
    for part in name.rstrip("/").split("/")[:-1]:
        folder = folder / part
        if folder not in seen:
            seen[folder] = _linked(folder)
        if seen[folder]:
            return True
    return False


def _same(first: os.stat_result, second: os.stat_result) -> bool:
    if (first.st_size, first.st_mtime_ns) != (second.st_size, second.st_mtime_ns):
        return False
    # The file index is compared where it is reliable; on Windows size and time decide.
    return os.name == "nt" or (first.st_ino, first.st_dev) == (second.st_ino, second.st_dev)


def content_digest(path: Path, name: str | None = None) -> bytes:
    """SHA-256 of a regular file, read by PatchRondo itself, that stayed the same file while it was read.

    A link at the end of the way is never followed (POSIX `O_NOFOLLOW`; a
    file that became a link is noticed by its size and time on Windows). A
    file that keeps changing while it is read, or that cannot be read, raises
    FingerprintError: an inconsistent read is never a digest.
    """
    shown = name or str(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        for _ in range(READS):
            before = os.lstat(path)
            if not filemode.S_ISREG(before.st_mode) or _linked(path):
                raise FingerprintError(f"{shown} stopped being a regular file while the task's files were read")
            fd = os.open(path, flags)
            with os.fdopen(fd, "rb") as stream:
                opened = os.fstat(stream.fileno())
                content, size = hashlib.sha256(), 0
                for chunk in iter(lambda: stream.read(1 << 20), b""):
                    content.update(chunk)
                    size += len(chunk)
                ended = os.fstat(stream.fileno())
            if _same(before, opened) and _same(opened, ended) and size == ended.st_size and _same(ended, os.lstat(path)):
                return content.digest()
    except FingerprintError:
        raise
    except OSError as exc:
        raise FingerprintError(f"{shown} cannot be read: {exc.strerror or exc}") from exc
    raise FingerprintError(f"{shown} kept changing while it was read")


def fingerprint(worktree: Path, _depth: int = 0, *, reader: bool = False) -> str:
    """Digest of HEAD, `git status` and every file Git does not ignore: its name, and its content or link target.

    Names are sorted as bytes, so the order of a listing never matters, and a
    name is hashed as Git gives it, without the platform's separator. Ignored
    files are not part of it (a check can declare the ones it reads:
    `verification.Check.inputs`). Nothing behind a folder that is a link or a
    junction is read: such a file is hashed as being outside. A repository
    inside the copy (a submodule, a clone) is hashed the same way, up to
    `NESTED` levels deep. A file that cannot be read, or that keeps changing
    while it is read, raises FingerprintError: the digest of files PatchRondo
    could not read is never given.

    For a copy without links, nested repositories or names Git cannot give as
    UTF-8, the digest is the one earlier versions computed. `reader` reads
    with `READER`, for a caller that does not hold the task lock: the same
    digest, without writing anything into the copy's Git directory.
    """
    options = READER if reader else ()
    digest = hashlib.sha256()
    try:
        head = git(*options, "rev-parse", "HEAD", cwd=worktree)
    except GitError:
        if not _depth:
            raise
        head = ""  # a nested repository without a commit yet
    digest.update(head.encode("utf-8"))
    digest.update(git(*options, "status", "--porcelain", cwd=worktree).encode("utf-8"))
    seen: dict = {}
    for raw in _names(worktree, options):
        digest.update(raw + b"\0")
        name = os.fsdecode(raw)
        path = worktree / name
        try:
            if _through_link(worktree, name, seen):
                digest.update(b"outside\0")
            elif path.is_symlink():
                digest.update(b"symlink\0" + os.fsencode(os.readlink(path)))
            elif path.is_file():
                digest.update(b"file\0" + content_digest(path, name))
            elif _depth < NESTED and path.is_dir() and not _linked(path) and (path / ".git").exists():
                try:
                    digest.update(b"repository\0" + bytes.fromhex(fingerprint(path, _depth + 1, reader=reader)))
                except GitError as exc:
                    raise FingerprintError(f"Git could not describe the repository {name} inside the copy: {exc}") from exc
            else:
                digest.update(b"missing-or-directory\0")
        except FingerprintError:
            raise
        except OSError as exc:
            raise FingerprintError(f"{name} cannot be read: {exc.strerror or exc}") from exc
    return digest.hexdigest()
