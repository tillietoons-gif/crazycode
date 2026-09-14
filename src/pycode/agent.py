"""Agent loop: processes user input, calls LLM with tools, executes tool results."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pycode.provider import LLMProvider
from pycode.providers import get_preset, detect_preset, PRESETS
from pycode.context import find_context_files, load_context
from pycode.context_manager import trim_messages, conversation_tokens, context_stats
from pycode.session import save_session, load_session, latest_session
from pycode.permissions import PermissionGuard, make_permission_confirm
from pycode.rewind import RewindManager
from pycode.subagents import SubagentRegistry, task_tool_schema
from pycode.tools import TOOL_SCHEMAS, dispatch_tool
from pycode.cost import CostTracker
from pycode.failover import FailoverProvider, ProviderConfig

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
        dry_run: bool = False,
        max_iterations: int = 30,
        max_context_tokens: int = 60_000,
        verbose: bool = True,
    ):
        # Provider preset: if the user named a preset (openai/anthropic/ollama/venice/openrouter)
        # but didn't give an explicit base, fill from the preset.
        if model in PRESETS and not api_base:
            preset = get_preset(model)
            api_base = preset["api_base"]
            model = preset.get("model") or model
            if not api_key:
                api_key = preset.get("api_key")

        self.provider = LLMProvider(
            api_key=api_key,
            api_base=api_base,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        # Multi-provider failover: a FailoverProvider can replace `self.provider`
        # via attach_failover(). Both LLMProvider and FailoverProvider expose
        # chat_stream() / chat() / model / api_base, so the loop is agnostic.
        self.max_iterations = max_iterations
        self.max_context_tokens = max_context_tokens
        self.verbose = verbose
        self.project_root = project_root or os.getcwd()
        self.auto_approve = auto_approve
        self.dry_run = dry_run
        self.mcp = None  # set by CLI via attach_mcp()
        # _confirm is set by the CLI; returns False to decline a destructive tool
        self._confirm: Optional[Callable[[str, Dict[str, Any]], bool]] = None
        # Staged dry-run changes, applied via a DiffReviewer after the tool loop
        self._pending_diffs: List[Dict[str, Any]] = []

        # Permission guard: load project policy if present
        self._permissions = PermissionGuard.from_project(self.project_root)
        # Subagent dispatcher + rewind manager
        self.subagents = SubagentRegistry(self)
        self.rewinder = RewindManager()
        # Token / cost accounting for the whole session
        self.cost_tracker = CostTracker(model=self.provider.model)

        # Auto-load project context if present (CLAUDE.md / .pycode.md / AGENTS.md)
        auto_ctx = load_context(self.project_root)
        combined_extra = "\n".join(p for p in [system_prompt_extra, auto_ctx] if p)
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": _build_system_prompt(combined_extra)}
        ]

        # Public introspection helpers
        self._loaded_context_files = find_context_files(self.project_root)

    # ------------------------------------------------------------------
    # MCP plugin support
    # ------------------------------------------------------------------

    def attach_mcp(self, registry) -> None:
        """Attach an MCPRegistry so external tool plugins are available."""
        self.mcp = registry

    def attach_failover(self, failover: "FailoverProvider") -> None:
        """Replace the single provider with a multi-provider failover chain.

        After this, every LLM call tries the providers in order until one
        succeeds. Cost is recorded against the provider that actually answered.
        """
        self.provider = failover
        self.cost_tracker = CostTracker(model=failover.model)

    def cost_report(self) -> Dict[str, Any]:
        """Return the session token/cost summary for display."""
        if self.cost_tracker is None:
            return {"disabled": True}
        return self.cost_tracker.summary()

    def _all_tool_schemas(self) -> List[Dict[str, Any]]:
        schemas = list(TOOL_SCHEMAS)
        schemas.append(task_tool_schema())  # the `task` subagent tool
        if self.mcp is not None:
            schemas.extend(self.mcp.all_schemas())
        return schemas

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_confirm(self, confirm: Optional[Callable[[str, Dict[str, Any]], bool]]) -> None:
        """Set a confirmation callback for destructive tool calls."""
        self._confirm = confirm

    def run(self, user_input: str, interactive_review: bool = False) -> str:
        """Process user input and return the agent's final response.

        Args:
            user_input: the prompt
            interactive_review: if True (and dry_run is on), present each
                staged write/edit diff for approval before applying.
        """
        self.messages.append({"role": "user", "content": user_input})

        # Record a checkpoint at this user turn so the agent can rewind to it
        self.checkpoint(label=" ".join(user_input.split())[:40])

        # Trim conversation to fit within the context budget before the LLM call
        if len(self.messages) > 2:
            self.messages = [self.messages[0]] + trim_messages(
                self.messages, self.max_context_tokens, keep_recent=8
            )

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
                final = content or "[no response]"
                # Apply staged dry-run changes (if any) before returning
                if self.dry_run and self._pending_diffs:
                    final += self._apply_pending_diffs(interactive=interactive_review)
                self._pending_diffs = []
                return final

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

                # Route the `task` subagent tool to the subagent registry
                if fn_name == "task":
                    result = self.subagents.run_task(
                        task=args.get("task", ""),
                        system_prompt=args.get("system_prompt"),
                        tools=args.get("tools"),
                        model=args.get("model"),
                    )
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": result,
                    })
                    continue

                result = dispatch_tool(
                    fn_name,
                    args,
                    confirm=self._confirm,
                    auto_approve=self.auto_approve,
                    dry_run=self.dry_run,
                    mcp_registry=self.mcp,
                )

                # Permission guard: apply the project policy
                decision = self._permissions.check(fn_name, args)
                if not decision.allowed and not self.auto_approve:
                    result = json.dumps({
                        "error": f"Permission denied: {decision.reason}",
                        "tool": fn_name,
                    }, ensure_ascii=False)
                    # still record it so the LLM sees the denial
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": result,
                    })
                    if self.verbose:
                        from pycode.tui import print_tool_result
                        print_tool_result(fn_name, False, f"denied: {decision.reason}")
                    continue

                # When dry_run is on, stage mutating changes for the reviewer
                if self.dry_run and fn_name in ("write", "edit"):
                    try:
                        staged = json.loads(result)
                        if isinstance(staged, dict) and staged.get("dry_run"):
                            staged["_tool"] = fn_name
                            staged["_args"] = args
                            self._pending_diffs.append(staged)
                    except json.JSONDecodeError:
                        pass

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
        Records token usage into the session CostTracker.
        """
        schemas = self._all_tool_schemas()
        # Use a stable system-prompt prefix so providers with prompt caching
        # can cache it; messages[0] is the system prompt (byte-stable).
        result: Dict[str, Any]
        if hasattr(self.provider, "chat_stream"):
            try:
                result = self.provider.chat_stream(self.messages, schemas)
            except Exception:
                # Some providers (e.g. Venice) reject streaming; fall back
                result = self.provider.chat(self.messages, tools=schemas)
        else:
            result = self.provider.chat(self.messages, tools=schemas)

        # Record token usage for cost tracking
        if isinstance(result, dict) and result.get("usage") and self.cost_tracker is not None:
            self.cost_tracker.record(result.get("usage"))
        elif isinstance(result, str):
            result = {"content": result or "", "tool_calls": []}
        return result

    def clear(self) -> None:
        """Reset conversation history, keeping the system prompt."""
        self.messages = [self.messages[0]]

    def _apply_pending_diffs(self, interactive: bool = False) -> str:
        """Apply staged dry-run changes via the DiffReviewer.

        Returns a short summary line appended to the agent's response.
        """
        from pycode.diff_reviewer import DiffReviewer
        if not self._pending_diffs:
            return ""
        reviewer = DiffReviewer(auto_approve=self.auto_approve)
        for change in self._pending_diffs:
            reviewer.stage(change)
        summary = reviewer.review() if interactive else self._auto_apply(reviewer)
        applied = summary.get("approved", 0)
        rejected = summary.get("rejected", 0)
        self._pending_diffs = []
        note = f"\n\n[dry-run] applied {applied} change(s)"
        if rejected:
            note += f", rejected {rejected}"
        return note

    def _auto_apply(self, reviewer) -> Dict[str, Any]:
        """Apply all staged changes without prompting (non-interactive review)."""
        applied, rejected = 0, 0
        for change in list(reviewer.pending):
            try:
                reviewer._apply(change.get("_tool", "write"), change.get("_args", {}))
                applied += 1
            except Exception:  # noqa: BLE001
                rejected += 1
        reviewer.pending = []
        return {"approved": applied, "rejected": rejected, "total": applied + rejected}

    def review_pending(self, interactive: bool = True) -> Dict[str, int]:
        """Public: review/apply currently staged dry-run changes.

        Call after agent.run(..., interactive_review=False) if you want to
        gate changes manually rather than auto-applying them.
        """
        from pycode.diff_reviewer import DiffReviewer
        reviewer = DiffReviewer(auto_approve=self.auto_approve)
        for change in self._pending_diffs:
            reviewer.stage(change)
        summary = reviewer.review() if interactive else self._auto_apply(reviewer)
        self._pending_diffs = []
        return summary

    def context_usage(self) -> Dict[str, int]:
        """Return token-usage stats for the current conversation."""
        return context_stats(self.messages, self.max_context_tokens)

    # ------------------------------------------------------------------
    # Rewind / branching
    # ------------------------------------------------------------------

    def checkpoint(self, label: str = "") -> int:
        """Snapshot the current conversation. Returns the checkpoint index."""
        cp = self.rewinder.snapshot(self.messages, label)
        return cp.index

    def rewind(self, index: int, note: Optional[str] = None) -> int:
        """Roll back the conversation to checkpoint `index`.

        Optionally append a short `note` as a system message so the agent
        knows why it's re-planning. Returns the new message count.
        """
        restored = self.rewinder.rewind_to(index, self.messages)
        self.messages = restored
        if note:
            self.messages.append({"role": "system", "content": f"[rewind] {note}"})
        return len(self.messages)

    def branch_from(self, index: int, new_user_input: str) -> int:
        """Branch: go to checkpoint `index` and append `new_user_input`.

        Returns the new message count.
        """
        new_msgs = [{"role": "user", "content": new_user_input}]
        branch_msgs = self.rewinder.branch(index, new_msgs)
        self.messages = branch_msgs
        return len(self.messages)

    def rewind_points(self) -> List[str]:
        """Return a human-readable list of rewind points."""
        return self.rewinder.list()

    def auto_checkpoint(self) -> None:
        """Record a checkpoint at the current state (no-op if empty)."""
        if len(self.messages) > 1:
            self.checkpoint()

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
