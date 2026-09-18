"""Subagent system: spawn a child Agent for a subtask, return a summary.

A subagent runs in its own conversation with a focused system prompt, so it
doesn't pollute the parent's context. The parent asks the subagent to do a
scoped task (e.g. "explore the auth module and list its public functions")
and gets back a text summary to fold into its own reasoning.

This mirrors Claude Code's subagent / Task tool.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from pycode.context_manager import trim_messages


class Subagent:
    """A focused child agent that does one task and returns a summary."""

    def __init__(
        self,
        parent: "Agent",
        name: str,
        task: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[str]] = None,
        max_iterations: Optional[int] = None,
        model: Optional[str] = None,
        verbose: bool = False,
    ):
        self.name = name
        self.task = task
        self._tools = tools  # optional whitelist of tool names
        self._max_iterations = max_iterations or 15
        self.verbose = verbose
        self._parent = parent

        # Build a child agent that shares the parent's provider + config but
        # has a fresh, focused conversation.
        from pycode.agent import \
            Agent  # deferred: avoid circular import at module load

        provider = parent.provider
        child_cfg = dict(
            api_key=provider.api_key,
            api_base=provider.api_base,
            model=model or parent.provider.model,
            temperature=provider.temperature,
            max_tokens=provider.max_tokens,
            system_prompt_extra=system_prompt or self._default_system_prompt(),
            project_root=parent.project_root,
            auto_approve=parent.auto_approve,
            dry_run=parent.dry_run,
            max_iterations=self._max_iterations,
            verbose=verbose,
            enable_subagents=False,
            allowed_tools=tools or ["read", "glob", "grep", "symbols"],
        )
        self.agent = Agent(**child_cfg)
        # Inherit permissions and MCP from the parent
        if hasattr(parent, "_permissions"):
            self.agent._permissions = parent._permissions
        self.agent.mcp = parent.mcp

    def _default_system_prompt(self) -> str:
        return (
            f"You are a focused subagent named '{self.name}'. "
            "Complete the given task using your tools, then return a concise "
            "text summary of your findings and any changes made. "
            "Do NOT commit code. Be specific: include file paths, function "
            "names, and exact snippets where relevant."
        )

    # ------------------------------------------------------------------
    # Run the subagent
    # ------------------------------------------------------------------

    def run(self) -> str:
        """Execute the task and return the subagent's final text summary."""
        result = self.agent.run(self.task)
        self.result = result  # store so as_tool_result() always works
        return result

    def as_tool_result(self) -> str:
        """Return the summary formatted for inclusion in a parent tool call."""
        result = getattr(self, "result", "") or self.run()
        return json.dumps({"subagent": self.name, "task": self.task, "result": result})


# ---------------------------------------------------------------------------
# Registry + dispatch for the LLM-facing `task` tool
# ---------------------------------------------------------------------------


class SubagentRegistry:
    """Holds subagent definitions and runs them on demand."""

    def __init__(self, parent: "Agent"):
        self.parent = parent
        self._active: Dict[str, Subagent] = {}

    def spawn(
        self,
        name: str,
        task: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[str]] = None,
        model: Optional[str] = None,
        max_iterations: Optional[int] = None,
    ) -> Subagent:
        sa = Subagent(
            self.parent,
            name,
            task,
            system_prompt=system_prompt,
            tools=tools,
            model=model,
            max_iterations=max_iterations,
        )
        self._active[sa.name] = sa
        return sa

    def run_task(
        self,
        task: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[str]] = None,
        model: Optional[str] = None,
    ) -> str:
        """Spawn a subagent for a one-off task and return its summary."""
        sa = self.spawn(
            "task-runner",
            task,
            system_prompt=system_prompt,
            tools=tools,
            model=model,
        )
        sa.result = sa.run()
        return sa.as_tool_result()


def task_tool_schema() -> Dict[str, Any]:
    """OpenAI function schema for the `task` tool (subagent dispatcher)."""
    return {
        "type": "function",
        "function": {
            "name": "task",
            "description": (
                "Spawn a focused subagent to handle a scoped subtask "
                "(e.g. explore a module, summarize a file, find all usages). "
                "The subagent runs in isolation and returns a text summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "What the subagent should do",
                    },
                    "system_prompt": {
                        "type": "string",
                        "description": "Optional focused instructions",
                    },
                    "tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional whitelist of tools the subagent may use",
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional model override",
                    },
                },
                "required": ["task"],
            },
        },
    }
