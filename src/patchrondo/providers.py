"""Official CLI adapters, with explicit least-privilege tool settings."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re

from .process import execute, short_log
from .storage import atomic_text


class AgentFailure(RuntimeError):
    def __init__(self, message: str, kind: str = "agent_error"):
        super().__init__(message)
        self.kind = kind


@dataclass
class AgentReply:
    text: str
    provider: str


def _classify_error(body: str) -> str:
    lowered = body.lower()
    if re.search(r"(rate.?limit|usage.?limit|quota|too many requests|out of credits|insufficient credits|limit reached|resets at)", lowered):
        return "quota"
    if re.search(r"(not logged in|authentication failed|unauthorized|please login|login required)", lowered):
        return "authentication"
    return "agent_error"


def _claude_text(body: str) -> str:
    try:
        obj = json.loads(body)
        if isinstance(obj, dict):
            if obj.get("is_error"):
                raise AgentFailure(str(obj.get("result", "Claude returned is_error")), _classify_error(str(obj.get("result", ""))))
            if obj.get("structured_output") is not None:
                structured = obj["structured_output"]
                return json.dumps(structured, ensure_ascii=False) if not isinstance(structured, str) else structured
            if isinstance(obj.get("result"), str):
                return obj["result"]
    except json.JSONDecodeError:
        pass
    return body


class OfficialCLI:
    def __init__(self, timeout: int = 600, claude_turns: int = 12):
        self.timeout = timeout
        self.claude_turns = claude_turns

    def invoke(self, provider: str, role: str, prompt: str, workspace: Path, run_dir: Path) -> AgentReply:
        if provider not in {"claude", "codex"} or role not in {"developer", "reviewer"}:
            raise ValueError("Invalid provider or role")
        run_dir.mkdir(parents=True, exist_ok=True)
        atomic_text(run_dir / f"{role}.prompt.md", prompt)
        schema = {
            "type": "object", "additionalProperties": False,
            "required": ["verdict", "summary", "issues"],
            "properties": {
                "verdict": {"type": "string", "enum": ["APPROVED", "CHANGES_REQUESTED", "BLOCKED"]},
                "summary": {"type": "string"},
                "issues": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["severity", "path", "description"],
                    "properties": {
                        "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                        "path": {"type": "string"}, "description": {"type": "string"},
                    },
                }},
            },
        }
        if provider == "claude":
            tools = "Read,Glob,Grep,Edit,Write" if role == "developer" else "Read,Glob,Grep"
            argv = ["claude", "-p", "--output-format", "json", "--restricted",
                    "--permission-mode", "dontAsk", "--tools", tools,
                    "--allowedTools", tools, "--disallowedTools", "mcp__*",
                    "--max-turns", str(self.claude_turns)]
            if role == "reviewer":
                argv += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
        else:
            out = run_dir / f"{role}.last-message.txt"
            out.unlink(missing_ok=True)
            argv = ["codex", "--ask-for-approval", "never", "exec", "--sandbox",
                    "workspace-write" if role == "developer" else "read-only",
                    "--cd", str(workspace),
                    "--ephemeral", "--output-last-message", str(out)]
            if role == "reviewer":
                schema_path = run_dir / "review.schema.json"
                atomic_text(schema_path, json.dumps(schema, indent=2))
                argv += ["--output-schema", str(schema_path)]
            argv.append("-")
        try:
            result = execute(argv, workspace, stdin=prompt, timeout=self.timeout,
                             pid_file=run_dir / f"{role}.active-process.json")
        except FileNotFoundError as exc:
            raise AgentFailure(f"CLI not found: {argv[0]}. Install the official CLI and authenticate it.", "configuration") from exc
        # These logs live outside the repository in a private directory.
        atomic_text(run_dir / f"{role}.stdout.log", short_log(result.stdout))
        atomic_text(run_dir / f"{role}.stderr.log", short_log(result.stderr))
        if result.timed_out:
            raise AgentFailure(f"Timeout after {self.timeout}s for {provider}/{role}", "timeout")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise AgentFailure(f"{provider}/{role} exit={result.returncode}: {short_log(detail, 1200)}", _classify_error(detail))
        if provider == "claude":
            text = _claude_text(result.stdout)
        else:
            outfile = run_dir / f"{role}.last-message.txt"
            if not outfile.is_file():
                raise AgentFailure(f"Missing final response from {provider}/{role}", "empty_output")
            if outfile.stat().st_size > 240000:
                raise AgentFailure("Final response is too large", "invalid_output")
            text = outfile.read_text(encoding="utf-8")
        if not text.strip():
            raise AgentFailure(f"Empty output from {provider}/{role}", "empty_output")
        atomic_text(run_dir / f"{role}.reply.md", short_log(text, 60000))
        return AgentReply(text=text, provider=provider)
