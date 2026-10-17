"""Bounded, non-shell subprocesses with child-process cleanup."""
from __future__ import annotations

from dataclasses import dataclass
import io
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time

CHUNK_BYTES = 65536
LINE_BYTES = 2_000_000  # one line of streamed output; a longer one is dropped whole
# After the process has exited its pipes are read to the end. A descendant that kept them
# open could delay that forever, so reading stops this long after the exit.
DRAIN_SECONDS = 2.0


# How the processes a command starts are held, so that stopping it stops them too (`Result.process_tree`).
GROUP = "process_group"   # POSIX: always
JOB = "job_object"        # native Windows, with `tree`
CHILD = "child_only"      # native Windows: no job object was asked for, or none could hold the child


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    process_tree: str | None = None  # GROUP, JOB or CHILD; None from a caller's own `execute`


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


class _Job:
    """A Windows job object holding a child and every process it starts, so that stopping it stops all of them.

    POSIX gets the same from the process group. A child that cannot be put
    in a job (the call is refused) is stopped alone, as without one, and its
    `Result` says so (`CHILD`).
    """

    def __init__(self, proc: subprocess.Popen):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel, self.handle = kernel, kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "The job object could not be created")
        if not kernel.AssignProcessToJobObject(self.handle, int(proc._handle)):
            code = ctypes.get_last_error()
            self.close()
            raise OSError(code, "The process could not be put in a job object")

    def kill(self) -> None:
        if self.handle:
            self.kernel.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        handle, self.handle = self.handle, None
        if handle:
            self.kernel.CloseHandle(handle)


def _job(proc: subprocess.Popen, tree: bool) -> tuple[_Job | None, str]:
    """The job object that holds the child, if any, and how what it starts is held (`Result.process_tree`)."""
    if os.name == "posix":
        return None, GROUP
    if not tree or getattr(proc, "_handle", None) is None:
        return None, CHILD
    try:
        return _Job(proc), JOB
    except (OSError, AttributeError, ValueError):
        return None, CHILD  # the command still runs; stopping it stops it alone, and the result says so


def _terminate(proc: subprocess.Popen, job: _Job | None = None) -> None:
    if job is not None:
        job.kill()  # the child and what it started; what follows waits for the child and reaps it
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
            pid_file: Path | None = None, on_stdout=None, on_stderr=None, tree: bool = False) -> Result:
    """Run a command to its end and return its bounded output.

    `on_stdout` and `on_stderr` receive the bytes of each stream as they are
    written, before the process ends. A chunk can end anywhere, also inside a
    line or a UTF-8 sequence: `LineReader` puts lines back together. Observers
    cannot change the outcome: one that raises is no longer called. Without
    observers the process writes to files exactly as it always did.

    `tree` stops, on native Windows, every process the command started when
    it is stopped (`_Job`); on POSIX the process group always does.
    `Result.process_tree` says which of the two held them, or `CHILD` when
    neither did: stopping the command then stops it alone.
    """
    if not argv or not all(isinstance(x, str) and x for x in argv):
        raise ValueError("Invalid command: expected a list of nonempty arguments")
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        if on_stdout is None and on_stderr is None:
            return _execute(argv, cwd, stdin=stdin, timeout=timeout, pid_file=pid_file,
                            output=output, errors=errors, tree=tree)
        return _observed(argv, cwd, stdin=stdin, timeout=timeout, pid_file=pid_file,
                         output=output, errors=errors, observers=(on_stdout, on_stderr), tree=tree)


def _execute(argv, cwd, *, stdin, timeout, pid_file, output, errors, tree=False) -> Result:
    proc = subprocess.Popen(
        argv, cwd=cwd, env=safe_env(),
        stdin=subprocess.PIPE, stdout=output, stderr=errors,
        text=True, encoding="utf-8", errors="replace", start_new_session=(os.name == "posix"),
    )
    job, held = _job(proc, tree)
    try:
        if pid_file is not None:
            from .storage import save_json
            save_json(pid_file, {"pid": proc.pid, "command": argv[0]})
        proc.communicate(input=stdin, timeout=timeout)
        return Result(proc.returncode, _captured(output), _captured(errors), process_tree=held)
    except subprocess.TimeoutExpired:
        _terminate(proc, job)
        proc.communicate()
        return Result(proc.returncode or 124, _captured(output), _captured(errors), timed_out=True, process_tree=held)
    except BaseException:
        _terminate(proc, job)
        raise
    finally:
        if job is not None:
            job.close()
        if pid_file is not None and proc.poll() is not None:
            pid_file.unlink(missing_ok=True)


class _Capture:
    """Where the reader threads put what they read; closed before the files behind it are."""

    def __init__(self):
        self.lock = threading.Lock()
        self.closed = False


def _pump(source, sink, observer, capture: _Capture) -> None:
    """Copy one pipe into its file, showing each chunk to the observer. Runs in its own thread."""
    try:
        while True:
            chunk = source.read1(CHUNK_BYTES)  # returns what is available; empty only at the end
            if not chunk:
                return
            with capture.lock:
                if capture.closed:
                    return
                sink.write(chunk)
            if observer is not None:
                try:
                    observer(chunk)
                except Exception:  # noqa: BLE001 - observing must never disturb the process observed
                    observer = None
    except (OSError, ValueError):
        pass  # the pipe was closed under the reader
    finally:
        try:
            source.close()
        except OSError:
            pass


def _feed(pipe, text: str) -> None:
    """Write the input and close it, as `communicate` does, while the output is being read."""
    try:
        # The same wrapper `Popen(text=True)` builds, so the child receives the same bytes.
        with io.TextIOWrapper(pipe, encoding="utf-8", errors="replace", write_through=True) as stream:
            stream.write(text)
    except (OSError, ValueError):
        pass  # the child closed its input early


def _drain(threads: list[threading.Thread], seconds: float) -> None:
    deadline = time.monotonic() + seconds
    for thread in threads:
        if thread.ident is not None:
            thread.join(max(0.0, deadline - time.monotonic()))


def _observed(argv, cwd, *, stdin, timeout, pid_file, output, errors, observers, tree=False) -> Result:
    """`_execute` with both streams on pipes, each read by its own thread so neither can fill up."""
    proc = subprocess.Popen(argv, cwd=cwd, env=safe_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=(os.name == "posix"))
    job, held = _job(proc, tree)
    capture = _Capture()
    readers = [threading.Thread(target=_pump, args=(source, sink, observer, capture), daemon=True,
                                name=f"patchrondo-read-{name}")
               for name, source, sink, observer in (("stdout", proc.stdout, output, observers[0]),
                                                    ("stderr", proc.stderr, errors, observers[1]))]
    writer = threading.Thread(target=_feed, args=(proc.stdin, stdin), daemon=True, name="patchrondo-write-stdin")

    def captured(timed_out: bool) -> Result:
        _drain(readers, DRAIN_SECONDS)
        with capture.lock:
            capture.closed = True  # a reader still blocked on a descendant's pipe writes nothing more
            code = (proc.returncode or 124) if timed_out else proc.returncode
            return Result(code, _captured(output), _captured(errors), timed_out=timed_out, process_tree=held)

    try:
        if pid_file is not None:
            from .storage import save_json
            save_json(pid_file, {"pid": proc.pid, "command": argv[0]})
        for thread in (*readers, writer):
            thread.start()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate(proc, job)
            proc.wait()
            return captured(True)
        return captured(False)
    except BaseException:
        _terminate(proc, job)
        _drain(readers, DRAIN_SECONDS)
        with capture.lock:
            capture.closed = True
        raise
    finally:
        if job is not None:
            job.close()
        if pid_file is not None and proc.poll() is not None:
            pid_file.unlink(missing_ok=True)


class LineReader:
    """Turn byte chunks of any shape into complete text lines.

    Bytes are kept until their newline arrives, so a line or a multi-byte
    character split between two chunks is decoded whole. A line longer than
    `limit` bytes is dropped and counted in `oversized`, which bounds memory
    whatever the process prints. `close` hands over a last line without a newline.
    """

    def __init__(self, on_line, limit: int = LINE_BYTES):
        self.on_line, self.limit = on_line, limit
        self.pending = bytearray()
        self.skipping = False
        self.oversized = 0

    def _keep(self, piece: bytes) -> None:
        if self.skipping:
            return
        if len(self.pending) + len(piece) > self.limit:
            self.pending.clear()
            self.skipping = True
        else:
            self.pending += piece

    def _end(self) -> None:
        if self.skipping:
            self.skipping = False
            self.oversized += 1
            return
        line = self.pending.decode("utf-8", errors="replace")
        self.pending.clear()
        self.on_line(line[:-1] if line.endswith("\r") else line)

    def feed(self, chunk: bytes) -> None:
        start = 0
        while True:
            end = chunk.find(b"\n", start)  # a newline byte is never part of a longer UTF-8 sequence
            if end < 0:
                self._keep(chunk[start:])
                return
            self._keep(chunk[start:end])
            self._end()
            start = end + 1

    def close(self) -> None:
        if self.pending or self.skipping:
            self._end()


def short_log(text: str, length: int = 24000) -> str:
    if len(text) <= length:
        return text
    return text[:length] + "\n...[output truncated]...\n"
