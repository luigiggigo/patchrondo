"""Bounded, non-shell subprocesses with child-process cleanup."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import tempfile


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def safe_env() -> dict[str, str]:
    env = os.environ.copy()
    # Prevent silently charging API accounts instead of subscribed CLIs.
    # Also remove common keys from child environments (not a full OS sandbox).
    for key in list(env):
        upper = key.upper()
        if any(s in upper for s in ("PASSWORD", "SECRET", "CREDENTIAL", "TOKEN", "API_KEY", "PRIVATE_KEY")) or upper in {
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY",
            "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
            "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
            "GITHUB_TOKEN", "GH_TOKEN", "GIT_ASKPASS", "SSH_ASKPASS",
            "AWS_PROFILE", "AWS_CONFIG_FILE", "GOOGLE_APPLICATION_CREDENTIALS",
            "PYTHONPATH", "PYTHONSTARTUP", "NODE_OPTIONS", "BASH_ENV", "ENV",
        } or upper.startswith("GIT_CONFIG_"):
            env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _terminate(proc: subprocess.Popen) -> None:
    if os.name != "posix" and proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass
        proc.wait()
    finally:
        if os.name == "posix":
            # The group may outlive its leader or contain children ignoring TERM.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def _captured(stream, limit: int = 60000) -> str:
    size = stream.seek(0, os.SEEK_END)
    stream.seek(0)
    if size <= limit:
        return stream.read().decode("utf-8", errors="replace")
    head = stream.read(limit // 2)
    stream.seek(-limit // 2, os.SEEK_END)
    tail = stream.read(limit // 2)
    return head.decode("utf-8", errors="replace") + "\n...[output truncated]...\n" + tail.decode("utf-8", errors="replace")


def execute(argv: list[str], cwd: Path, *, stdin: str = "", timeout: int = 600,
            pid_file: Path | None = None) -> Result:
    if not argv or not all(isinstance(x, str) and x for x in argv):
        raise ValueError("Invalid command: expected a list of nonempty arguments")
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        return _execute(argv, cwd, stdin=stdin, timeout=timeout, pid_file=pid_file,
                        output=output, errors=errors)


def _execute(argv, cwd, *, stdin, timeout, pid_file, output, errors) -> Result:
    proc = subprocess.Popen(
        argv, cwd=cwd, env=safe_env(),
        stdin=subprocess.PIPE, stdout=output, stderr=errors,
        text=True, encoding="utf-8", errors="replace", start_new_session=(os.name == "posix"),
    )
    try:
        if pid_file is not None:
            from .storage import save_json
            save_json(pid_file, {"pid": proc.pid, "command": argv[0]})
        proc.communicate(input=stdin, timeout=timeout)
        return Result(proc.returncode, _captured(output), _captured(errors))
    except subprocess.TimeoutExpired:
        _terminate(proc)
        proc.communicate()
        return Result(proc.returncode or 124, _captured(output), _captured(errors), timed_out=True)
    except BaseException:
        _terminate(proc)
        raise
    finally:
        if pid_file is not None and proc.poll() is not None:
            pid_file.unlink(missing_ok=True)


def short_log(text: str, length: int = 24000) -> str:
    if len(text) <= length:
        return text
    return text[:length] + "\n...[output truncated]...\n"
