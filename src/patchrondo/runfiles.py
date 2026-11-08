"""Where a task's runs write their files, and which of those places readers accept (Phase 2.5, Step 03).

    runs/iteration-NNN/<file>                       every agent call and check of an iteration, flat
    runs/iteration-NNN/test-draft-KK/<file>         one request for tests (`core._draft_folder`)
    runs/iteration-NNN/<phase>/attempt-NNN/<file>   one attempt of a step: phase `develop` (the developer)
                                                    or `review` (the reviewer), NNN its ordinal, 001-999

Since Step 04, a step recorded by Adaptive Execution writes each call in an
attempt folder that `core._attempt` allocates with the attempt's record
(`execution.AttemptRecord`, whose `phase` and `ordinal` name the folder); a
step without a record still writes flat, where a second call of a step
replaces the files of the first. The readers know all three layouts, so a
task that holds them side by side reads whole, and nothing is ever moved: a
task written flat stays flat.

Readers accept a name only when it fullmatches these patterns, never a link,
a junction or another reparse point, and never a path that leaves `runs`:
the Logs tab (`app._logs`), the evidence bundle (`LOG_NAME`, `bundle._part`)
and the log a review record names (`log_name`). The forced unlock is wider on
purpose (`process_files`): any process file at any of the depths a writer
uses counts, whatever its folder is called, because missing one would let a
second run start beside an agent that is still writing the worktree.

The checks of the verification runner (`verification.Runner`) and the test
stage keep their flat files: their names end in a suffix, so they never meet
the `develop` and `review` folders.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat

from . import winacl

RUNS = "runs"
ITERATION_LIMIT = 999
ITERATION_FOLDER = re.compile(r"iteration-(\d{3})")
DRAFT_FOLDER = re.compile(r"test-draft-(\d{2,6})")
PHASES = ("develop", "review")                       # the values of `execution.Phase`
ROLE_OF_PHASE = {"develop": "developer", "review": "reviewer"}
ATTEMPT_FOLDER = re.compile(r"attempt-(\d{3})")
ATTEMPT_LIMIT = 999                                  # `execution.ORDINAL_LIMIT`
FILE_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}"
# A log a record may name, relative to `runs`: flat, or inside one attempt folder. Never a request for tests (no
# record names its files), never `..`, a drive, a backslash or an attempt 000.
LOG_NAME = re.compile(rf"iteration-\d{{3}}(?:/(?:develop|review)/attempt-(?!000)\d{{3}})?/{FILE_NAME}")
PROCESS_SUFFIX = ".active-process.json"
PROCESS_DEPTH = 3  # iteration folder, one folder below it (a request for tests), two (an attempt)


class UnsafeFolder(RuntimeError):
    """A folder on the way to an attempt is a link, a junction or not a folder: nothing is written through it."""


def _number(value, name: str, limit: int) -> int:
    if type(value) is not int or not 1 <= value <= limit:
        raise ValueError(f"{name} must be a whole number from 1 to {limit}")
    return value


def _phase(value) -> str:
    if value not in PHASES:
        raise ValueError(f"phase must be one of {', '.join(PHASES)}")
    return str(value)


def attempt_ordinal(name: str) -> int | None:
    """The ordinal an attempt folder's name gives, or None for any other name (`attempt-000`, `attempt-1`, …)."""
    match = ATTEMPT_FOLDER.fullmatch(name)
    return (int(match.group(1)) or None) if match else None


def plain_folder(path: Path) -> bool:
    """Whether `path` is a folder itself: not a link, a junction or another reparse point."""
    try:
        info = os.lstat(path)
        return stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and not winacl.is_reparse_point(path)
    except OSError:
        return False


def plain_file(path: Path) -> bool:
    """Whether `path` is a regular file itself: not a link, a junction or another reparse point."""
    try:
        info = os.lstat(path)
        return stat.S_ISREG(info.st_mode) and not winacl.is_reparse_point(path)
    except OSError:
        return False


def attempt_folder(task_path: Path, iteration: int, phase: str, ordinal: int) -> Path:
    """The folder of one attempt, from validated parts. Nothing is created or read."""
    return (task_path / RUNS / f"iteration-{_number(iteration, 'iteration', ITERATION_LIMIT):03d}" / _phase(phase)
            / f"attempt-{_number(ordinal, 'ordinal', ATTEMPT_LIMIT):03d}")


def attempts(iteration_dir: Path) -> list[tuple[str, int, Path]]:
    """The attempt folders of one iteration folder a reader may open: (phase, ordinal, folder), develop first.

    Only folders named as `allocate` names them, each of them a folder itself,
    as `runs` and the iteration folder must be; anything else is left out.
    """
    found = []
    if not (ITERATION_FOLDER.fullmatch(iteration_dir.name) and plain_folder(iteration_dir)
            and plain_folder(iteration_dir.parent)):
        return found
    for phase in PHASES:
        phase_dir = iteration_dir / phase
        if not plain_folder(phase_dir):
            continue
        for entry in sorted(phase_dir.iterdir()):
            ordinal = attempt_ordinal(entry.name)
            if ordinal is not None and plain_folder(entry):
                found.append((phase, ordinal, entry))
    return found


def allocate(task_path: Path, iteration: int, phase: str, ordinal: int | None = None) -> Path:
    """Create the folder of a new attempt and return it. An existing folder is never reused.

    With `ordinal` the folder is exactly that attempt's (Step 04 passes the
    record's); without it, the number is past every attempt folder of the
    step already there, also one left by a crash that no state names. The
    folder is created exclusively: when it exists, `FileExistsError` and
    nothing is written, so two attempts never share files. `runs`, the
    iteration folder and the phase folder must be folders themselves
    (`UnsafeFolder`); they are created private when missing.
    """
    from .storage import private_dir  # storage imports this module for the lock

    _number(iteration, "iteration", ITERATION_LIMIT)
    _phase(phase)
    if ordinal is not None:
        _number(ordinal, "ordinal", ATTEMPT_LIMIT)
    folder = task_path / RUNS
    for name in (None, f"iteration-{iteration:03d}", phase):
        folder = folder / name if name else folder
        if os.path.lexists(folder) and not plain_folder(folder):
            raise UnsafeFolder(f"{folder} is not a folder of the task: no attempt is written through it")
        private_dir(folder)
    if ordinal is None:
        used = [number for entry in folder.iterdir() if (number := attempt_ordinal(entry.name)) is not None]
        ordinal = max(used, default=0) + 1
        if ordinal > ATTEMPT_LIMIT:
            raise ValueError(f"A step has at most {ATTEMPT_LIMIT} attempt folders")
    target = folder / f"attempt-{ordinal:03d}"
    target.mkdir(mode=0o700)  # never an existing folder
    return private_dir(target)


def log_name(task_path: Path, file: Path) -> str:
    """The name a record gives a file of the task's runs: its path from `runs`, with `/`.

    For a flat file it is `iteration-NNN/<file>`, as records always said. A
    name `LOG_NAME` does not accept raises ValueError: a record never names a
    file no reader would open.
    """
    name = file.relative_to(task_path / RUNS).as_posix()
    if not LOG_NAME.fullmatch(name):
        raise ValueError(f"{name} is not a name a record may give a log")
    return name


def process_files(runs: Path) -> list[Path]:
    """Every process file of a task's runs at the depths writers use, whatever its folders are called.

    For the forced unlock, which fails closed: a file in a folder no reader
    lists (a malformed attempt name, an unknown folder) still counts. The
    depth is bounded, so a link can never make this walk endlessly.
    """
    found = set()
    for depth in range(PROCESS_DEPTH):
        found.update(runs.glob("iteration-*/" + "*/" * depth + "*" + PROCESS_SUFFIX))
    return sorted(found)
