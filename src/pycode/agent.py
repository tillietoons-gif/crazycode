"""Agent loop: processes user input, calls LLM with tools, executes tool results."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from pycode.provider import LLMProvider
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
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": _build_system_prompt(system_prompt_extra)}
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, user_input: str) -> str:
        """Process user input and return the agent's final response."""
        self.messages.append({"role": "user", "content": user_input})

        for iteration in range(1, self.max_iterations + 1):
            if self.verbose:
                print(f"\n[iteration {iteration}]", file=sys.stderr)

            # Get LLM response
            response = self.provider.chat_stream(self.messages, TOOL_SCHEMAS)
            content = response.get("content", "")
            tool_calls = response.get("tool_calls", [])

            if content and self.verbose:
                print(f"\n--- Assistant ---\n{content}\n", file=sys.stderr)

            # Append assistant message
            assistant_msg: Dict[str, Any] = {"role": "assistant"}
            if content:
                assistant_msg["content"] = content
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            self.messages.append(assistant_msg)

            # No tool calls means we're done
            if not tool_calls:
                return content or "[no response]"

            # Execute each tool call
            for tc in tool_calls:
                tc_id = tc.get("id", "")
                fn_name = tc.get("function", {}).get("name", "")
                raw_args = tc.get("function", {}).get("arguments", "{}")

                # Parse arguments
                try:
                    args: Dict[str, Any] = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    args = {}

                if self.verbose:
                    print(f"[tool: {fn_name}] {json.dumps(args, default=str)[:500]}", file=sys.stderr)

                # Dispatch
                result = dispatch_tool(fn_name, args)

                if self.verbose:
                    preview = result[:300] + "..." if len(result) > 300 else result
                    print(f"[result: {fn_name}] {preview}", file=sys.stderr)

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result,
                })

        # Hit max iterations
        return "[max iterations reached]"

    def clear(self) -> None:
        """Reset conversation history."""
        self.messages = [self.messages[0]]  # keep system prompt

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

        # API key
        key = os.getenv("PYCODE_API_KEY", "")
        if not key:
            # Fallback to common user-facing vars (user must set these)
            key = os.getenv("OPENAI_API_KEY", "")
        if key:
            cfg["api_key"] = key

        # API base
        base = os.getenv("PYCODE_API_BASE", "")
        if not base:
            base = os.getenv("OPENAI_API_BASE", "")
        if base:
            cfg["api_base"] = base.rstrip("/")

        # Model
        model = os.getenv("PYCODE_MODEL", "")
        if not model:
            model = os.getenv("OPENAI_MODEL", "gpt-4o")
        if model:
            cfg["model"] = model

        # Temperature
        temp = os.getenv("PYCODE_TEMPERATURE", "")
        if temp:
            try:
                cfg["temperature"] = float(temp)
            except ValueError:
                pass

        # Max tokens
        mt = os.getenv("PYCODE_MAX_TOKENS", "")
        if mt:
            try:
                cfg["max_tokens"] = int(mt)
            except ValueError:
                pass

        # System prompt file
        spf = os.getenv("PYCODE_SYSTEM_PROMPT_FILE", "")
        if spf:
            p = Path(spf)
            if p.is_file():
                try:
                    cfg["system_prompt_extra"] = p.read_text(encoding="utf-8")
                except Exception:
                    pass

        return cfg
