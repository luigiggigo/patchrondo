"""A Git repository and its first commit, made only when the user asks for it while adding a project.

The dashboard needs a repository with at least one commit. For a folder that
is not one, the page offers to run `git init` and commit what the folder
holds. Nothing happens without that request:

- `plan` reads the folder and changes nothing. It lists the files a first
  commit would hold, as Git sees them (`.gitignore` applies), through a
  temporary repository outside the folder, and gives a digest of that list.
- `make` checks the folder again and refuses when the list no longer has the
  digest the user saw (`Changed`). Then it runs `git init` (a folder that is
  not a repository yet), adds exactly the listed files and commits them as
  `MESSAGE`. A failure leaves what Git already did in place and says so;
  nothing is deleted.

The user's Git settings decide the author, the branch name, hooks and
signing. When Git has no name or email, `make` takes the ones the user typed
for this one commit (`git -c`): no Git configuration file is written.
A whole drive, the home folder, PatchRondo's own state, a folder inside
Git's data or inside a repository Git does not open are refused, and so are
more than `LIMIT` files or `SIZE_LIMIT` bytes.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import threading

MESSAGE = "Initial commit"
LIMIT = 10_000            # files a first commit may hold
SIZE_LIMIT = 1 << 30      # bytes a first commit may hold
SHOWN = 200               # files listed to the user
TIMEOUT = 60              # seconds for one Git command
TEXT_LIMIT = 300          # characters of a Git message passed on
_BUSY = threading.Lock()  # one first commit at a time


class Refused(ValueError):
    """This folder cannot be given a first commit by PatchRondo; the message says why."""


class Changed(Exception):
    """The folder no longer holds the files the user reviewed."""


def _said(stderr: bytes) -> str:
    """The line of Git's error output that says what went wrong, shortened."""
    lines = [line.strip() for line in stderr.decode("utf-8", errors="replace").splitlines() if line.strip()]
    chosen = next((line for line in reversed(lines) if line.startswith(("fatal:", "error:"))), lines[-1] if lines else "")
    return (chosen or "Git failed")[:TEXT_LIMIT]


def _run(args: list[str], *, cwd: Path | None = None, stdin: bytes | None = None) -> bytes:
    command = ["git", *args]
    try:
        done = subprocess.run(command, cwd=cwd, input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                              capture_output=True, timeout=TIMEOUT, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Git did not answer in time") from exc
    if done.returncode:
        raise RuntimeError(_said(done.stderr))
    return done.stdout


def _names(output: bytes) -> list[bytes]:
    return [name for name in output.split(b"\0") if name]


def target(raw, workspace_root: Path) -> tuple[Path, str]:
    """The folder and what it needs: `init` (not a repository yet) or `commit` (a repository with no commit)."""
    if not isinstance(raw, str) or not raw.strip():
        raise Refused("Enter the path of a folder")
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        raise Refused("Use an absolute path")
    path = path.resolve()
    if not path.is_dir():
        raise Refused("That folder does not exist")
    if path.parent == path:
        raise Refused("PatchRondo does not make a repository of a whole drive. Choose the folder of the project")
    if path == Path.home().resolve():
        raise Refused("PatchRondo does not make a repository of your home folder. Choose the folder of the project")
    home = Path(workspace_root).resolve()
    if path == home or home.is_relative_to(path) or path.is_relative_to(home):
        raise Refused("PatchRondo keeps its private state in that folder. Choose another folder")
    if any(part.casefold() == ".git" for part in path.parts):
        raise Refused("That folder is part of Git's own data. Choose the folder of the project")
    try:
        top = Path(_run(["-C", str(path), "rev-parse", "--show-toplevel"]).decode("utf-8", errors="replace").strip())
    except RuntimeError as exc:
        # Git refused to open it, or it is no repository. A `.git` here or above says it is one Git did not open.
        for folder in (path, *path.parents):
            if os.path.lexists(folder / ".git"):
                raise Refused(f"{folder} holds a Git repository that Git does not open here ({exc}). "
                              "Solve that first, or choose another folder") from exc
        return path, "init"
    if top.resolve() != path:
        raise Refused("That folder is inside a repository. Use the repository root instead")
    try:
        _run(["-C", str(path), "rev-parse", "--verify", "--quiet", "HEAD"])
    except RuntimeError:
        return path, "commit"
    raise Refused("That repository already has commits")


def offer(raw, workspace_root: Path) -> str | None:
    """What the page may offer for this folder (`init` or `commit`), or None. Reads only."""
    try:
        return target(raw, workspace_root)[1]
    except (Refused, RuntimeError, OSError):
        return None


def _files(path: Path, mode: str) -> tuple[list[bytes], list[bytes]]:
    """(files the commit will hold, paths given to `git add`), as Git names them."""
    if mode == "init":
        # A repository of our own, outside the folder: `.gitignore` applies and nothing is written in the folder.
        with tempfile.TemporaryDirectory(prefix="patchrondo-first-", ignore_cleanup_errors=True) as scratch:
            _run(["init", "--quiet", "--bare", scratch])
            names = _names(_run(["--git-dir", scratch, "--work-tree", str(path), "ls-files", "-z", "--others",
                                 "--exclude-standard"], cwd=path))
        names = sorted(set(names))
        return names, names
    # A repository without commits may already have files in its index: the commit holds those too.
    staged = _names(_run(["-C", str(path), "ls-files", "-z", "--cached"]))
    others = _names(_run(["-C", str(path), "ls-files", "-z", "--others", "--exclude-standard"]))
    given = sorted(set(staged) | set(others))
    held = [name for name in given if os.path.lexists(path / os.fsdecode(name))]
    return held, given


def _digest(path: Path, mode: str, held: list[bytes], given: list[bytes]) -> str:
    sha = hashlib.sha256()
    for part in (mode.encode(), os.fsencode(str(path)), b"\1".join(held), b"\1".join(given)):
        sha.update(len(part).to_bytes(8, "big") + part)
    return sha.hexdigest()


def _identity(path: Path) -> str | None:
    """The author Git would write ("Name <email>"), or None when Git does not know who the user is."""
    try:
        idents = [_run(["var", name], cwd=path).decode("utf-8", errors="replace").strip()
                  for name in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT")]
    except RuntimeError:
        return None
    return idents[0].rsplit(" ", 2)[0] if idents[0] else None


def _survey(raw, workspace_root: Path) -> dict:
    path, mode = target(raw, workspace_root)
    held, given = _files(path, mode)
    if len(given) > LIMIT:
        raise Refused(f"That folder holds more than {LIMIT:,} files Git would commit. Make the first commit "
                      "yourself, or choose another folder")
    sizes = []
    for name in held:
        try:
            sizes.append(os.lstat(path / os.fsdecode(name)).st_size)
        except OSError:
            sizes.append(0)
    total = sum(sizes)
    if total > SIZE_LIMIT:
        raise Refused(f"Those files hold more than {SIZE_LIMIT >> 20:,} MB. Make the first commit yourself, "
                      "or choose another folder")
    return {"path": path, "mode": mode, "held": held, "given": given, "sizes": sizes, "bytes": total,
            "digest": _digest(path, mode, held, given)}


def plan(raw, workspace_root: Path) -> dict:
    """What `make` would do in this folder, for the user to review. Writes nothing in the folder."""
    survey = _survey(raw, workspace_root)
    shown = [{"name": name.decode("utf-8", errors="replace"), "size": size}
             for name, size in zip(survey["held"][:SHOWN], survey["sizes"][:SHOWN])]
    return {"path": str(survey["path"]), "mode": survey["mode"], "message": MESSAGE, "files": shown,
            "count": len(survey["held"]), "more": max(0, len(survey["held"]) - SHOWN), "bytes": survey["bytes"],
            "identity": _identity(survey["path"]), "digest": survey["digest"]}


def _typed_identity(name, email) -> list[str]:
    if not isinstance(name, str) or not isinstance(email, str):
        raise ValueError("Git does not know your name and email on this computer: enter them for this commit")
    name, email = name.strip(), email.strip()
    if not 0 < len(name) <= 100 or any(char in name for char in "<>\r\n\0"):
        raise ValueError("Enter your name, without < or >")
    if not 2 < len(email) <= 254 or "@" not in email or any(char.isspace() or char in "<>\0" for char in email):
        raise ValueError("Enter an email address such as you@example.com")
    return ["-c", f"user.name={name}", "-c", f"user.email={email}"]


def make(raw, workspace_root: Path, digest, name=None, email=None) -> Path:
    """Run `git init` if needed and commit the reviewed files. Returns the repository folder."""
    if not isinstance(digest, str) or not digest:
        raise ValueError("Review the files of the first commit before making it")
    with _BUSY:
        survey = _survey(raw, workspace_root)
        if survey["digest"] != digest:
            raise Changed("The folder changed since you reviewed it. Review its files again")
        path, mode = survey["path"], survey["mode"]
        who = [] if _identity(path) else _typed_identity(name, email)
        if mode == "init":
            _run(["-C", str(path), "init", "--quiet"])
        try:
            if survey["given"]:
                _run(["-C", str(path), "--literal-pathspecs", "add", "--all", "--pathspec-from-file=-",
                      "--pathspec-file-nul"], stdin=b"\0".join(survey["given"]) + b"\0")
            _run(["-C", str(path), *who, "commit", "--quiet", "--message", MESSAGE,
                  *([] if survey["given"] else ["--allow-empty"])])
        except RuntimeError as exc:
            done = "Git created the repository" if mode == "init" else "The repository is unchanged except its index"
            raise RuntimeError(f"{done} but did not make the first commit: {exc}") from exc
        return path
