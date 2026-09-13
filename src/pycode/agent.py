"""Agent loop: processes user input, calls LLM with tools, executes tool results."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pycode.provider import LLMProvider
from pycode.context import find_context_files, load_context
from pycode.session import save_session, load_session, latest_session
from pycode.tools import TOOL_SCHEMAS, dispatch_tool

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = """You are pycode, a professional AI coding agent that helps users with software engineering tasks.

## Capabilities
You can read and write files, run shell commands, search code, and fetch web content to accomplish coding tasks.

## Tool Usage Guidelines
- Use `read` to inspect files before editing
- Use `edit` for surgical in-place changes, `write` for new files or full rewrites
- Use `bash` to run builds, tests, git commands
- Use `glob` to find files, `grep` to search contents
- Use `webfetch` to check documentation
- Use `todo` to track multi-step tasks
- ALWAYS read a file before editing it
- Prefer existing code patterns and conventions in the project
- Run lint/typecheck after making changes if available

## Code Style
- Follow existing project conventions
- Never add comments unless asked
- Keep responses concise
- Verify changes work (run tests, build)

## Important
- Do NOT commit code unless explicitly asked
- Do NOT add emojis to files
- When creating new files, check existing files for style/conventions first
"""


def _build_system_prompt(extra: str = "") -> str:
    base = _DEFAULT_SYSTEM_PROMPT
    if extra:
        base += f"\n\n## Project-specific instructions\n{extra}"
    return base


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class Agent:
    """The main coding agent that orchestrates LLM calls and tool execution."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        system_prompt_extra: str = "",
        project_root: Optional[str] = None,
        auto_approve: bool = False,
        max_iterations: int = 30,
        verbose: bool = True,
    ):
        self.provider = LLMProvider(
            api_key=api_key,
            api_base=api_base,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.max_iterations = max_iterations
        self.verbose = verbose
        self.project_root = project_root or os.getcwd()
        self.auto_approve = auto_approve
        # _confirm is set by the CLI; returns False to decline a destructive tool
        self._confirm: Optional[Callable[[str, Dict[str, Any]], bool]] = None

        # Auto-load project context if present (CLAUDE.md / .pycode.md / AGENTS.md)
        auto_ctx = load_context(self.project_root)
        combined_extra = "\n".join(p for p in [system_prompt_extra, auto_ctx] if p)
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": _build_system_prompt(combined_extra)}
        ]

        # Public introspection helpers
        self._loaded_context_files = find_context_files(self.project_root)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_confirm(self, confirm: Optional[Callable[[str, Dict[str, Any]], bool]]) -> None:
        """Set a confirmation callback for destructive tool calls."""
        self._confirm = confirm

    def run(self, user_input: str) -> str:
        """Process user input and return the agent's final response."""
        self.messages.append({"role": "user", "content": user_input})

        for iteration in range(1, self.max_iterations + 1):
            if self.verbose:
                from pycode.tui import Spinner
                with Spinner(f"iteration {iteration}"):
                    try:
                        response = self._call_llm()
                    except Exception as exc:  # noqa: BLE001
                        return f"[LLM error: {exc}]"
            else:
                try:
                    response = self._call_llm()
                except Exception as exc:  # noqa: BLE001
                    return f"[LLM error: {exc}]"

            content = response.get("content", "")
            tool_calls = response.get("tool_calls", [])

            if content and self.verbose:
                from pycode.tui import print_assistant
                print_assistant(content)

            assistant_msg: Dict[str, Any] = {"role": "assistant"}
            if content:
                assistant_msg["content"] = content
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            self.messages.append(assistant_msg)

            if not tool_calls:
                return content or "[no response]"

            for tc in tool_calls:
                tc_id = tc.get("id", "")
                fn_name = tc.get("function", {}).get("name", "")
                raw_args = tc.get("function", {}).get("arguments", "{}")

                try:
                    args: Dict[str, Any] = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    args = {}

                if self.verbose:
                    from pycode.tui import print_tool_start
                    print_tool_start(fn_name, json.dumps(args, default=str))

                result = dispatch_tool(
                    fn_name,
                    args,
                    confirm=self._confirm,
                    auto_approve=self.auto_approve,
                )

                if self.verbose:
                    from pycode.tui import print_tool_result
                    ok = not (result.startswith('{"error"') or '"ok": false' in result)
                    print_tool_result(fn_name, ok, result)

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result,
                })

        return "[max iterations reached]"

    def _call_llm(self) -> Dict[str, Any]:
        """Call the LLM provider, falling back to non-streaming if needed.

        Normalizes the result to a dict with 'content' and 'tool_calls' keys
        regardless of whether the provider returned a string or a dict.
        """
        try:
            result = self.provider.chat_stream(self.messages, TOOL_SCHEMAS)
        except Exception:
            # Some providers (e.g. Venice) reject streaming; fall back
            result = self.provider.chat(self.messages, tools=TOOL_SCHEMAS)

        # Normalize: chat() returns a plain string; chat_stream() returns a dict
        if isinstance(result, dict):
            return result
        return {"content": result or "", "tool_calls": []}

    def clear(self) -> None:
        """Reset conversation history, keeping the system prompt."""
        self.messages = [self.messages[0]]

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------

    def save_session(self, path: Optional[str] = None) -> str:
        """Persist the current conversation to a JSONL file."""
        return save_session(self.messages, path=path, root=self.project_root)

    @staticmethod
    def restore_session(path: str) -> List[Dict[str, Any]]:
        """Load a conversation from a JSONL session file."""
        return load_session(path)

    @staticmethod
    def latest_session(root: Optional[str] = None) -> Optional[str]:
        return latest_session(root)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def load_env_config() -> Dict[str, Any]:
        """Load configuration from environment variables.

        Checks PYCODE_* first, then falls back to common LLM env vars
        that the user may already have set (OPENAI_API_KEY, etc.).
        Does NOT read or reference internal platform env vars.
        """
        cfg: Dict[str, Any] = {}

        key = os.getenv("PYCODE_API_KEY", "")
        if not key:
            key = os.getenv("OPENAI_API_KEY", "")
        if key:
            cfg["api_key"] = key

        base = os.getenv("PYCODE_API_BASE", "")
        if not base:
            base = os.getenv("OPENAI_API_BASE", "")
        if base:
            cfg["api_base"] = base.rstrip("/")

        model = os.getenv("PYCODE_MODEL", "")
        if not model:
            model = os.getenv("OPENAI_MODEL", "deepseek-v4-1-flash")
        if model:
            cfg["model"] = model

        temp = os.getenv("PYCODE_TEMPERATURE", "")
        if temp:
            try:
                cfg["temperature"] = float(temp)
            except ValueError:
                pass

        mt = os.getenv("PYCODE_MAX_TOKENS", "")
        if mt:
            try:
                cfg["max_tokens"] = int(mt)
            except ValueError:
                pass

        spf = os.getenv("PYCODE_SYSTEM_PROMPT_FILE", "")
        if spf:
            p = Path(spf)
            if p.is_file():
                try:
                    cfg["system_prompt_extra"] = p.read_text(encoding="utf-8")
                except Exception:
                    pass

        return cfg
