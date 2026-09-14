"""Agent loop: processes user input, calls LLM with tools, executes tool results."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pycode.provider import LLMProvider, accepts_kwarg
from pycode.tui import dim
from pycode.providers import get_preset, detect_preset, PRESETS
from pycode.context import find_context_files, load_context
from pycode.context_manager import trim_messages, conversation_tokens, context_stats
from pycode.session import save_session, load_session, latest_session
from pycode.permissions import PermissionGuard, make_permission_confirm
from pycode.rewind import RewindManager
from pycode.subagents import SubagentRegistry, task_tool_schema
from pycode.tui_subagent_trace import SubagentTrace
from pycode.tools import TOOL_SCHEMAS, dispatch_tool
from pycode.cost import CostTracker
from pycode.failover import FailoverProvider, ProviderConfig
from pycode.hooks import hook_context
from pycode import interrupts

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
        use_project_map: bool = True,
        self_review: bool = False,
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
        # Activity feed: append-only log of every tool call for the TUI
        from pycode.tui_feed import ActivityFeed
        self.feed = ActivityFeed(use_color=False)
        # Nested subagent traces for the TUI (one SubagentTrace per task call)
        from pycode.tui_subagent_trace import SubagentTrace
        self.subagent_traces: List[SubagentTrace] = []
        # Lifecycle hooks (pre_tool/post_tool/on_turn), attached by the CLI
        self.hooks = None
        # Set while the LLM is streaming live deltas (suppresses duplicate print)
        self._streamed = False
        # True when reasoning deltas arrived during the last LLM call
        self._saw_reasoning = False
        # Self-review pass: a second reviewer call after edits are applied
        self.self_review = self_review

        # Auto-load project context if present (CLAUDE.md / .pycode.md / AGENTS.md)
        auto_ctx = load_context(self.project_root)
        combined_extra = "\n".join(p for p in [system_prompt_extra, auto_ctx] if p)
        sys_prompt = _build_system_prompt(combined_extra)

        # Project map: compact symbol summary injected into the system prompt
        if use_project_map:
            try:
                from pycode.tools import get_project_index
                idx = get_project_index(self.project_root)
                if not idx.files:
                    idx.build()
                summary = idx.summary(max_chars=3000)
                if summary:
                    sys_prompt += f"\n\n## Project map\n{summary}"
            except Exception:  # noqa: BLE001 - map is best-effort
                pass

        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": sys_prompt}
        ]

        # Public introspection helpers
        self._loaded_context_files = find_context_files(self.project_root)

    # ------------------------------------------------------------------
    # MCP plugin support
    # ------------------------------------------------------------------

    def attach_mcp(self, registry) -> None:
        """Attach an MCPRegistry so external tool plugins are available."""
        self.mcp = registry

    def attach_hooks(self, hooks) -> None:
        """Attach a HookRunner for pre_tool/post_tool/on_turn shell hooks."""
        self.hooks = hooks

    def _emit_hook(self, event: str, context: Optional[Dict[str, Any]] = None) -> None:
        if self.hooks is not None:
            try:
                self.hooks.emit(event, context)
            except Exception:  # noqa: BLE001 - hooks must never kill the agent
                pass

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

    def run(self, user_input: str, interactive_review: bool = False, use_tui: bool = False) -> str:
        """Process user input and return the agent's final response.

        Args:
            user_input: the prompt
            interactive_review: if True (and dry_run is on), present each
                staged write/edit diff for approval before applying.
            use_tui: if True, the dry-run reviewer uses the keyboard-driven
                InteractiveDiffReviewer (TUI) when interactive_review is on.
        """
        self.messages.append({"role": "user", "content": user_input})
        self._touched: List[str] = []

        # Record a checkpoint at this user turn so the agent can rewind to it
        self.checkpoint(label=" ".join(user_input.split())[:40])
        self._emit_hook("on_turn", {"goal": user_input[:200]})

        # Trim conversation to fit within the context budget before the LLM call
        if len(self.messages) > 2:
            self.messages = [self.messages[0]] + trim_messages(
                self.messages, self.max_context_tokens, keep_recent=8
            )

        for iteration in range(1, self.max_iterations + 1):
            try:
                interrupts.check_abort()
            except interrupts.Aborted:
                return "[aborted by user]"
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
                if self._streamed:
                    print()  # finish the streamed line instead of re-printing
                else:
                    print_assistant(content)

            reasoning = response.get("reasoning") or ""
            if reasoning and self.verbose:
                first = reasoning.splitlines()[0][:70] if reasoning.splitlines() else ""
                print(dim(f"  · thinking {len(reasoning)} chars: {first}"), file=sys.stderr)

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
                    final += self._apply_pending_diffs(interactive=interactive_review, use_tui=use_tui)
                self._pending_diffs = []
                if self.self_review:
                    final += self._self_review_pass()
                return final

            for tc in tool_calls:
                try:
                    interrupts.check_abort()
                except interrupts.Aborted:
                    return "[aborted by user]"
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
                    self._emit_hook("pre_tool", hook_context("task", args))
                    trace = SubagentTrace(name=args.get("task", "")[:20] or "task")
                    trace.start(args.get("task", ""))
                    result = self.subagents.run_task(
                        task=args.get("task", ""),
                        system_prompt=args.get("system_prompt"),
                        tools=args.get("tools"),
                        model=args.get("model"),
                    )
                    # parse the subagent summary for the trace end line
                    try:
                        summary = json.loads(result).get("result", result)
                    except (json.JSONDecodeError, AttributeError):
                        summary = result
                    trace.end(str(summary)[:80])
                    self.subagent_traces.append(trace)
                    self._emit_hook("post_tool", hook_context("task", args, ok=True))
                    self.feed.end(tc_id, "task", args, result, ok=True, mcp=False)
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": result,
                    })
                    continue

                self.feed.begin(tc_id, fn_name, args)
                self._emit_hook("pre_tool", hook_context(fn_name, args))
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
                    self.feed.end(tc_id, fn_name, args, result, ok=False)
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

                # Track touched file paths for the self-review pass
                if fn_name in ("write", "edit") and args.get("path"):
                    self._touched.append(str(args["path"]))

                ok = not (result.startswith('{"error"') or '"ok": false' in result)
                self._emit_hook("post_tool", hook_context(fn_name, args, ok=ok))
                self.feed.end(tc_id, fn_name, args, result, ok=ok, mcp=False)

                if self.verbose:
                    from pycode.tui import print_tool_result
                    print_tool_result(fn_name, ok, result)

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result,
                })

        return "[max iterations reached]"

    # ------------------------------------------------------------------
    # Plan mode: plan-then-execute
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_plan(raw: str) -> List[str]:
        """Extract numbered steps from an LLM plan response."""
        steps: List[str] = []
        for line in raw.splitlines():
            m = re.match(r"^\s*\d+[.)\]]\s+(.*)", line)
            if m and m.group(1).strip():
                steps.append(m.group(1).strip())
        return steps

    def draft_plan(self, goal: str, max_steps: int = 10) -> List[str]:
        """Ask the LLM for a numbered step plan for ``goal`` (no tools run)."""
        plan_prompt = (
            f"Goal: {goal}\n\n"
            f"Write a numbered step-by-step plan to accomplish this goal. "
            f"At most {max_steps} steps. Each step must be one concrete, "
            f"self-contained instruction. Return ONLY the numbered list."
        )
        raw = self.provider.chat(
            [self.messages[0], {"role": "user", "content": plan_prompt}],
            tools=None,
        )
        return self._parse_plan(raw)[:max_steps]

    def run_plan(self, goal: str, max_steps: int = 10) -> Dict[str, Any]:
        """Plan-then-execute: draft a plan, then run each step as its own
        checkpointed turn. Returns a summary dict for the CLI to render."""
        try:
            steps = self.draft_plan(goal, max_steps=max_steps)
        except Exception as exc:  # noqa: BLE001
            return {"goal": goal, "error": f"plan draft failed: {exc}", "results": []}
        if not steps:
            return {"goal": goal, "error": "no plan steps parsed", "results": []}
        results: List[Dict[str, Any]] = []
        total = len(steps)
        for i, step in enumerate(steps, 1):
            try:
                interrupts.check_abort()
            except interrupts.Aborted:
                break
            self.checkpoint(label=f"plan {i}: {step[:30]}")
            result = self.run(f"[plan step {i}/{total}] {step}")
            results.append({"step": step, "result": result})
        return {"goal": goal, "steps": steps, "results": results}

    # ------------------------------------------------------------------
    # Self-review pass
    # ------------------------------------------------------------------

    _REVIEW_PROMPT = """You are a strict code reviewer. The coding agent just modified these files:

{files}

Here is the current diff:

```diff
{diff}
```

The agent's final response was:

{response}

Check the diff for correctness bugs, broken logic, or missed edge cases.
If there is a problem worth fixing, reply starting with exactly:
REQUEST REVISION: <one-sentence instruction for the agent>
Otherwise reply starting with LGTM, optionally followed by minor notes."""

    def _self_review_pass(self) -> str:
        """Second-opinion review of this turn's edits (once per turn).

        Returns a short note appended to the final response. If the reviewer
        requests a revision, exactly one corrective turn is run (with
        self-review disabled to avoid recursion).
        """
        touched = getattr(self, "_touched", [])
        if not touched:
            return ""
        # A short git diff grounds the review; skip silently outside git
        diff_result = dispatch_tool(
            "bash",
            {"command": "git diff --unified=3 -- " +
                        " ".join(f"'{p}'" for p in sorted(set(touched))),
             "workdir": self.project_root},
            auto_approve=True,
        )
        try:
            diff = json.loads(diff_result).get("stdout", "")
        except json.JSONDecodeError:
            diff = ""
        if not diff.strip():
            return ""

        prompt = self._REVIEW_PROMPT.format(
            files="\n".join(f"- {p}" for p in sorted(set(touched))),
            diff=diff[:8000],
            response=(getattr(self, "_last_response", "") or "")[:1500],
        )
        try:
            reply = self.provider.chat(
                [self.messages[0], {"role": "user", "content": prompt}],
                tools=None,
            )
        except Exception as exc:  # noqa: BLE001 - review is best-effort
            return f"\n\n[self-review] reviewer unavailable: {exc}"

        if reply.strip().upper().startswith("REQUEST REVISION:"):
            instruction = reply.split(":", 1)[1].strip()[:300]
            self.self_review = False  # exactly one revision, no re-review
            try:
                self.run(f"A code review found problems in your last change: {instruction}. "
                         f"Fix them now.")
            finally:
                self.self_review = True
            return f"\n\n[self-review] revision requested: {instruction}"
        return f"\n\n[self-review] {reply.strip()[:200]}"

    # ------------------------------------------------------------------
    # Auto-fix loop
    # ------------------------------------------------------------------

    LOOP_DONE = "GOAL_ACHIEVED"

    @classmethod
    def _loop_done(cls, result: str) -> bool:
        return cls.LOOP_DONE in result

    def run_loop(self, goal: str, max_iterations: int = 5) -> Dict[str, Any]:
        """Auto-fix loop: attempt the goal repeatedly until the agent reports
        it is achieved or the iteration cap is reached.

        Because the user explicitly launched the loop, turns run with the
        agent's normal permissions (no extra prompting beyond policy).
        """
        results: List[str] = []
        prompt = goal
        for attempt in range(1, max_iterations + 1):
            try:
                interrupts.check_abort()
            except interrupts.Aborted:
                break
            result = self.run(prompt)
            results.append(result)
            if result.startswith("[LLM error") or self._loop_done(result):
                break
            prompt = (
                f"Continue working toward the goal (attempt {attempt + 1} of {max_iterations}):\n"
                f"Goal: {goal}\n\n"
                f"Previous turn result (end):\n{result[-1500:]}\n\n"
                f"If the goal is already fully achieved, reply with exactly {self.LOOP_DONE}. "
                f"Otherwise keep working: run builds/tests, fix what is broken, and verify."
            )
        achieved = bool(results) and self._loop_done(results[-1])
        return {"goal": goal, "attempts": len(results),
                "achieved": achieved, "results": results}

    def _call_llm(self) -> Dict[str, Any]:
        """Call the LLM provider, falling back to non-streaming if needed.

        Normalizes the result to a dict with 'content' and 'tool_calls' keys
        regardless of whether the provider returned a string or a dict.
        Records token usage into the session CostTracker. When verbose on a
        TTY, content deltas are printed live as they stream in.
        """
        schemas = self._all_tool_schemas()
        # Use a stable system-prompt prefix so providers with prompt caching
        # can cache it; messages[0] is the system prompt (byte-stable).
        result: Dict[str, Any]
        self._streamed = False
        live = self.verbose and sys.stdout.isatty()
        if hasattr(self.provider, "chat_stream"):
            try:
                kwargs: Dict[str, Any] = {}
                if accepts_kwarg(self.provider.chat_stream, "on_delta"):
                    kwargs["on_delta"] = self._on_stream_delta if live else None
                if accepts_kwarg(self.provider.chat_stream, "on_reasoning"):
                    kwargs["on_reasoning"] = self._on_reasoning_delta if live else None
                result = self.provider.chat_stream(self.messages, schemas, **kwargs)
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

    def _on_stream_delta(self, text: str) -> None:
        """Print a streamed content delta to stdout (TUI live output)."""
        if not self._streamed:
            self._streamed = True
            print()
        sys.stdout.write(text)
        sys.stdout.flush()

    def _on_reasoning_delta(self, text: str) -> None:
        """Swallow reasoning deltas; the summary is printed once per turn."""
        self._saw_reasoning = True

    def clear(self) -> None:
        """Reset conversation history, keeping the system prompt."""
        self.messages = [self.messages[0]]

    def _apply_pending_diffs(self, interactive: bool = False, use_tui: bool = False) -> str:
        """Apply staged dry-run changes via the DiffReviewer.

        When `use_tui` and `interactive` are both True, a keyboard-driven
        reviewer (i/a/r/h/n/q/?) is used instead of the auto-apply path.

        Returns a short summary line appended to the agent's response.
        """
        if not self._pending_diffs:
            return ""
        if interactive and use_tui and self._pending_diffs:
            from pycode.tui_diff_review import InteractiveDiffReviewer
            reviewer = InteractiveDiffReviewer(
                pending=self._pending_diffs,
                apply_fn=lambda tool, args: self._apply_change(tool, args),
                use_color=True,
            )
            summary = reviewer.run()
            self._pending_diffs = []
            applied = summary.get("approved", 0) + summary.get("held", 0)
            rejected = summary.get("rejected", 0)
        else:
            from pycode.diff_reviewer import DiffReviewer
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

    def _apply_change(self, tool: str, args: dict) -> None:
        """Apply a single staged change by re-dispatching without dry_run."""
        dispatch_tool(tool, args, confirm=self._confirm, auto_approve=True)

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

    def subagent_trace_report(self) -> str:
        """Render all captured subagent traces (for the TUI /dump-subagents)."""
        if not self.subagent_traces:
            return ""
        return "\n\n".join(t.render(use_color=True) for t in self.subagent_traces)

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
