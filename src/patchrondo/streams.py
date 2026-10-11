"""Activity streams of the provider CLIs, turned into provider-independent events.

Both CLIs can print what they do as one JSON object per line while they work:

- Claude Code with `--output-format stream-json --verbose`. The options were
  read from `claude --help` of Claude Code 2.1.291 (WSL2) and 2.1.295 (native
  Windows) on October 11, 2026; the line shapes used here (`system`/`init`,
  `system`/`api_retry`, `assistant`, `user`, `result`) from
  https://code.claude.com/docs/en/headless read the same day, where the last
  line is the `result` object that `--output-format json` prints alone.
  `--include-partial-messages` is deliberately not used: it adds one line per
  token, and those lines also carry thinking deltas.
- Codex with `codex exec --json`, read from `codex exec --help` of codex-cli
  0.160.1; the event and item types are those of
  codex-rs/exec/src/exec_events.rs at rust-v0.160.1.

Neither form has been confirmed here by a call to an authenticated CLI.

A parser emits `message`, `tool`, `file`, `status` and `error` events through
the `emit` it is given, and keeps a bounded, redacted transcript for the step's
log. Internal reasoning (Claude `thinking` blocks, Codex `reasoning` items,
partial deltas) is dropped from both. Lines of an unknown type are named in the
transcript and nothing else: what is shown is what is known to be public.

Nothing here decides the outcome of a step. The final answer is read by the
adapter from the CLI's own result (Claude's `result` line, Codex's
last-message file) and validated by the engine; a reviewer's message that looks
like a verdict is recorded as withheld.
"""
from __future__ import annotations

from collections import OrderedDict, deque
import json
import os
from pathlib import Path
import re
import threading

from .activity import clip, redact
from .process import LineReader

SWITCH = "PATCHRONDO_LIVE_ACTIVITY"
OUTPUT_TAIL = 1500       # characters kept from the end of a command's output
PENDING_CALLS = 200      # tool calls remembered until their result arrives
WITHHELD = ("The reviewer wrote its answer. It counts only after PatchRondo has validated it and checked the "
            "tests: the Review tab shows the verified result.")
_VERDICT = re.compile(r"\"verdict\"\s*:|\b(?:APPROVED|CHANGES_REQUESTED|BLOCKED)\b")
_REFUSED = re.compile(r"unknown option|unknown argument|unexpected argument|unrecognized (?:option|argument)"
                      r"|invalid (?:option|value|choice)|is invalid|not a valid", re.I)
_STREAM_OPTIONS = {"claude": re.compile(r"--verbose|stream-json"), "codex": re.compile(r"--json(?![\w-])")}
CLAUDE_FILE_TOOLS = {"Write": "written", "Edit": "edited", "MultiEdit": "edited", "NotebookEdit": "edited"}
CLAUDE_SUMMARY_KEYS = ("file_path", "notebook_path", "pattern", "path", "glob", "command", "query", "url", "description")


def enabled() -> bool:
    """False when the environment switches live activity off; the CLIs are then started as before."""
    return os.environ.get(SWITCH, "1").strip().lower() not in {"0", "false", "off", "no"}


def refused(provider: str, output: str) -> bool:
    """Whether a CLI stopped at its own arguments, naming an option that only streaming adds.

    Both CLIs check their arguments before they start a session, so nothing was
    sent to a model when this is true and the step can be started again without them.
    """
    option = _STREAM_OPTIONS[provider]
    return any(_REFUSED.search(line) and option.search(line) for line in output.splitlines()[:40])


class Excerpt:
    """The first and the last lines of a long text, kept in bounded memory."""

    def __init__(self, limit: int = 22000, line_limit: int = 4000):
        self.half, self.line_limit = limit // 2, line_limit
        self.head: list[str] = []
        self.tail: deque[str] = deque()
        self.head_size = self.tail_size = 0
        self.cut = False

    def add(self, line: str) -> None:
        line = clip(line, self.line_limit)
        size = len(line) + 1
        if not self.tail and self.head_size + size <= self.half:
            self.head.append(line)
            self.head_size += size
            return
        self.tail.append(line)
        self.tail_size += size
        while self.tail_size > self.half and len(self.tail) > 1:
            self.tail_size -= len(self.tail.popleft()) + 1
            self.cut = True

    def text(self) -> str:
        lines = [*self.head, *(["...[output truncated]..."] if self.cut else []), *self.tail]
        return "\n".join(lines) + "\n" if lines else ""


def _compact(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _number(value) -> int | None:
    return value if type(value) is int else None


class Stream:
    """One CLI run: bytes in, events out. Safe to feed from a reader thread and close from another."""

    def __init__(self, role: str, workspace: Path, emit):
        self.role, self.emit = role, emit
        self.root = os.path.abspath(str(workspace))
        self.lock = threading.Lock()
        self.reader = LineReader(self._line)
        self.transcript = Excerpt()
        self.errors: list[str] = []     # what the CLI itself reported as a failure of the run
        self.result: dict | None = None  # Claude's final `result` object
        self.recognized = 0             # lines of a known type
        self.fed = self.closed = False
        self._last_error = None

    def feed(self, chunk: bytes) -> None:
        with self.lock:
            if not self.closed:
                self.fed = True
                self.reader.feed(chunk)

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.reader.close()
            self.closed = True
            if self.reader.oversized:
                self.emit("status", status="notice", code="oversized_lines", count=self.reader.oversized,
                          text="Some output lines were too long to be read and are not shown.")

    def _line(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        obj = None
        if text.startswith("{"):
            try:
                obj = json.loads(text)
            except ValueError:
                pass
        if not isinstance(obj, dict):
            self.transcript.add(redact(text))  # plain output of the CLI: kept for the log, never an event
            return
        try:
            keep = self.handle(obj)
        except Exception:  # noqa: BLE001 - an unexpected shape is left out; the run is not disturbed
            keep = {"type": str(obj.get("type"))[:60], "note": "not shown: unexpected shape"}
        if keep is True:
            self.transcript.add(redact(text))
        elif isinstance(keep, dict):
            self.transcript.add(redact(_compact(keep)))

    def handle(self, obj: dict):
        """Emit the events of one line. Return True to keep the line in the transcript, a dict to keep that instead."""
        raise NotImplementedError

    def _unknown(self, obj: dict) -> dict:
        return {"type": str(obj.get("type"))[:60], "note": "not shown: unknown line type"}

    def _message(self, text) -> None:
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            return
        if self.role == "reviewer" and _VERDICT.search(text):
            # A candidate answer, not a verdict: only the validated final result is one.
            self.emit("message", text=WITHHELD, withheld=True)
        else:
            self.emit("message", text=text)

    def _error(self, text, fatal: bool = True) -> None:
        text = text.strip() if isinstance(text, str) else ""
        if not text or text == self._last_error:
            return
        self._last_error = text
        if fatal and len(self.errors) < 20:
            self.errors.append(text[:2000])
        self.emit("error", text=text)

    def _path(self, value) -> str:
        """A path inside the worktree is shown relative to it."""
        text = str(value)
        try:
            relative = os.path.relpath(text, self.root) if os.path.isabs(text) else text
        except ValueError:  # another drive on Windows
            return text
        return text if relative.startswith("..") else relative.replace("\\", "/")


class ClaudeStream(Stream):
    def __init__(self, role: str, workspace: Path, emit):
        super().__init__(role, workspace, emit)
        self.calls: OrderedDict[str, tuple[str, str, str]] = OrderedDict()  # call id -> name, summary, file

    def handle(self, obj: dict):
        kind = obj.get("type")
        if kind == "assistant":
            self.recognized += 1
            return self._assistant(obj)
        if kind == "user":
            self.recognized += 1
            return self._user(obj)
        if kind == "result":
            self.recognized += 1
            self.result = obj
            if obj.get("is_error"):
                self._error(str(obj.get("result") or obj.get("subtype") or "Claude reported an error"))
            self.emit("status", status="result", turns=_number(obj.get("num_turns")),
                      duration_ms=_number(obj.get("duration_ms")),
                      outcome=obj.get("subtype") if isinstance(obj.get("subtype"), str) else None)
            return True
        if kind == "system":
            return self._system(obj)
        if kind == "stream_event":
            return None  # partial deltas are not requested; if they arrive they may carry thinking text
        return self._unknown(obj)

    def _system(self, obj: dict):
        subtype = obj.get("subtype")
        if subtype == "init":
            self.recognized += 1
            model = obj.get("model") if isinstance(obj.get("model"), str) else None
            self.emit("status", status="session", model=model)
            tools = [tool for tool in obj.get("tools") or [] if isinstance(tool, str)][:60]
            return {"type": "system", "subtype": "init", "model": model, "tools": tools}
        if subtype == "api_retry":
            self.recognized += 1
            fields = {"attempt": _number(obj.get("attempt")), "max_retries": _number(obj.get("max_retries")),
                      "retry_delay_ms": _number(obj.get("retry_delay_ms")), "http_status": _number(obj.get("error_status")),
                      "reason": obj.get("error") if isinstance(obj.get("error"), str) else None}
            self.emit("status", status="retry", **fields)
            return {"type": "system", "subtype": "api_retry", **fields}
        return {"type": "system", "subtype": str(subtype)[:60], "note": "not shown"}

    def _summary(self, name: str, arguments) -> str:
        if name == "StructuredOutput" or not isinstance(arguments, dict):
            return ""  # the structured answer is the final result: it is validated, not previewed
        parts = []
        for key in CLAUDE_SUMMARY_KEYS:
            value = arguments.get(key)
            if isinstance(value, (str, int)) and str(value):
                parts.append(self._path(value) if key.endswith("path") else str(value))
        return " · ".join(parts)

    def _assistant(self, obj: dict):
        message = obj.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        kept = []
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                self._message(block.get("text"))
                kept.append({"type": "text", "text": block.get("text")})
            elif block.get("type") == "tool_use":
                name, call = str(block.get("name") or "tool"), str(block.get("id") or "")
                summary = self._summary(name, block.get("input"))
                target = block["input"].get("file_path") or block["input"].get("notebook_path") \
                    if isinstance(block.get("input"), dict) else None
                self.calls[call] = (name, summary, self._path(target) if isinstance(target, str) else "")
                while len(self.calls) > PENDING_CALLS:
                    self.calls.popitem(last=False)
                self.emit("tool", status="started", id=call, name=name, summary=summary)
                kept.append({"type": "tool_use", "id": call, "name": name,
                             "input": None if name == "StructuredOutput" else block.get("input")})
            # thinking and redacted_thinking blocks, and anything unknown, are dropped here
        if not kept:
            return None
        return {"type": "assistant", "content": kept, "parent_tool_use_id": obj.get("parent_tool_use_id")}

    @staticmethod
    def _text(content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(block["text"] for block in content
                             if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str))
        return ""

    def _user(self, obj: dict):
        message = obj.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        kept = []
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue  # the prompt is already saved with the step
            call = str(block.get("tool_use_id") or "")
            name, summary, target = self.calls.pop(call, ("tool", "", ""))
            text, failed = self._text(block.get("content")), bool(block.get("is_error"))
            # What a tool returned is file content more often than not: its size is recorded, its text
            # only when the tool failed and the text is the reason.
            self.emit("tool", status="failed" if failed else "completed", id=call, name=name, summary=summary,
                      output=text if failed else None, size=len(text))
            if not failed and name in CLAUDE_FILE_TOOLS and target:
                self.emit("file", path=target, change=CLAUDE_FILE_TOOLS[name])
            kept.append({"tool_use_id": call, "is_error": failed, "content": clip(text, 2000)})
        return {"type": "user", "tool_results": kept} if kept else None


class CodexStream(Stream):
    def handle(self, obj: dict):
        kind = obj.get("type")
        if kind in {"item.started", "item.updated", "item.completed"}:
            self.recognized += 1
            item = obj.get("item")
            return self._item(kind.partition(".")[2], item, obj) if isinstance(item, dict) else self._unknown(obj)
        if kind == "thread.started":
            self.recognized += 1
            self.emit("status", status="session")
            return True
        if kind == "turn.started":
            self.recognized += 1
            return True
        if kind == "turn.completed":
            self.recognized += 1
            usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
            self.emit("status", status="turn_completed", input_tokens=_number(usage.get("input_tokens")),
                      output_tokens=_number(usage.get("output_tokens")))
            return True
        if kind == "turn.failed":
            self.recognized += 1
            error = obj.get("error")
            self._error(error.get("message") if isinstance(error, dict) else "The turn failed")
            return True
        if kind == "error":
            self.recognized += 1
            self._error(obj.get("message"))
            return True
        return self._unknown(obj)

    def _item(self, phase: str, item: dict, obj: dict):
        kind, call = item.get("type"), str(item.get("id") or "")
        status = item.get("status") if isinstance(item.get("status"), str) else None
        ended = "started" if phase != "completed" else "completed" if status in (None, "completed") else \
            "declined" if status == "declined" else "failed"
        if kind == "reasoning":
            return None  # the reasoning summary is not public output
        if kind == "agent_message":
            if phase == "completed":
                self._message(item.get("text"))
            return True
        if kind == "command_execution":
            command = item.get("command") if isinstance(item.get("command"), str) else ""
            if phase == "updated":
                return None  # output so far: the completed item carries all of it
            output = item.get("aggregated_output") if isinstance(item.get("aggregated_output"), str) else ""
            self.emit("tool", status=ended, id=call, name="shell", kind="command", summary=command,
                      output=output[-OUTPUT_TAIL:] if phase == "completed" and output else None,
                      exit_code=_number(item.get("exit_code")))
            return True
        if kind == "file_change":
            changes = item.get("changes") if isinstance(item.get("changes"), list) else []
            for change in changes[:50]:
                if isinstance(change, dict) and isinstance(change.get("path"), str):
                    self.emit("file", path=self._path(change["path"]), change=str(change.get("kind") or "update"),
                              status=status if status in {"failed", "in_progress"} else None)
            return True
        if kind == "mcp_tool_call":
            error = item.get("error") if isinstance(item.get("error"), dict) else {}
            name = f"{item.get('server')}.{item.get('tool')}"
            self.emit("tool", status=ended, id=call, name=name, output=error.get("message"))
            return {"type": obj["type"], "item": {"id": call, "type": kind, "server": item.get("server"),
                                                  "tool": item.get("tool"), "status": status, "error": error or None}}
        if kind == "collab_tool_call":
            self.emit("tool", status=ended, id=call, name=f"agents.{item.get('tool')}")
            return {"type": obj["type"], "item": {"id": call, "type": kind, "tool": item.get("tool"), "status": status}}
        if kind == "web_search":
            query = item.get("query") if isinstance(item.get("query"), str) else ""
            self.emit("tool", status="started" if phase == "started" else "completed", id=call, name="web_search",
                      summary=query)
            return {"type": obj["type"], "item": {"id": call, "type": kind, "query": query}}
        if kind == "todo_list":
            steps = [{"text": step.get("text"), "completed": bool(step.get("completed"))}
                     for step in item.get("items") or [] if isinstance(step, dict) and isinstance(step.get("text"), str)]
            self.emit("status", status="plan", items=steps, done=sum(step["completed"] for step in steps), total=len(steps))
            return True
        if kind == "error":
            self._error(item.get("message"), fatal=False)  # surfaced by the CLI as not ending the run
            return True
        return {"type": obj["type"], "item": {"id": call, "type": str(kind)[:60]}, "note": "not shown: unknown item type"}


def open_stream(provider: str, role: str, workspace: Path, emit) -> Stream:
    return (ClaudeStream if provider == "claude" else CodexStream)(role, workspace, emit)
