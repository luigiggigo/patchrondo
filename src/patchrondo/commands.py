"""Test command lines as people type them, converted to and from argument arrays.

Commands are stored as argv arrays and executed without a shell. Windows uses
the Microsoft C runtime rules (double quotes group, single quotes are literal,
backslashes are path separators); other platforms use POSIX quoting.
"""
from __future__ import annotations

import os
import shlex
import subprocess


def _split_windows(line: str) -> list[str]:
    """Parse a command line with the Microsoft C runtime rules that list2cmdline targets."""
    args: list[str] = []
    current: list[str] = []
    in_arg = quoted = False
    i = 0
    while i < len(line):
        char = line[i]
        if char == "\\":
            end = i
            while end < len(line) and line[end] == "\\":
                end += 1
            count = end - i
            if end < len(line) and line[end] == '"':
                # 2n backslashes + quote: n backslashes, quote toggles; 2n+1: n backslashes + literal quote.
                current.append("\\" * (count // 2))
                if count % 2:
                    current.append('"')
                    end += 1
            else:
                current.append("\\" * count)
            in_arg, i = True, end
            continue
        if char == '"':
            quoted, in_arg = not quoted, True
        elif char in " \t" and not quoted:
            if in_arg:
                args.append("".join(current))
                current, in_arg = [], False
        else:
            current.append(char)
            in_arg = True
        i += 1
    if quoted:
        raise ValueError("No closing quotation")
    if in_arg:
        args.append("".join(current))
    return args


def split_command(line: str, windows: bool | None = None) -> list[str]:
    """Split one test command line: POSIX shell rules, or Windows rules where backslashes are paths."""
    if windows is None:
        windows = os.name == "nt"
    return _split_windows(line) if windows else shlex.split(line)


def join_command(command: list[str], windows: bool | None = None) -> str:
    """Inverse of split_command, used to show saved commands for editing."""
    if windows is None:
        windows = os.name == "nt"
    return subprocess.list2cmdline(command) if windows else shlex.join(command)
