"""Subagent trace: a nested, indented feed of subagent (task) activity.

When the LLM invokes the `task` tool, the child agent runs its own tool
loop. This module renders that nested activity as an indented trace:

    → [task] explore the auth module
       ✓ read   src/auth.py
       ✓ grep   "login" → 4 hits
    ← [task] found 3 functions: login(), logout(), validate_token()

The trace can also be dumped to a log file for post-mortem.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pycode.tui import c, dim, tool_badge


@dataclass
class SubagentEvent:
    """One line in a subagent trace."""
    kind: str            # 'start' | 'tool' | 'end'
    label: str
    detail: str = ""
    ok: bool = True
    ts: float = field(default_factory=time.time)


class SubagentTrace:
    """Accumulates nested tool activity from a child agent and renders it."""

    def __init__(self, name: str = "task"):
        self.name = name
        self.events: List[SubagentEvent] = []
        self.started_at = time.time()
        self._indent = "   "

    def start(self, task: str) -> None:
        self.events.append(SubagentEvent("start", f"[{self.name}] {task[:60]}"))

    def tool(self, tool_name: str, ok: bool, detail: str = "") -> None:
        glyph = c("green", "✓") if ok else c("red", "✗")
        badge = f"[{tool_name}]"
        self.events.append(SubagentEvent("tool", f"{glyph} {badge:<6} {detail}", ok=ok))

    def end(self, summary: str) -> None:
        self.events.append(SubagentEvent(
            "end", f"[{self.name}] {summary[:80]}"))

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(self, use_color: bool = True) -> str:
        out: List[str] = []
        for ev in self.events:
            if ev.kind == "start":
                out.append(c("cyan", f"→ {ev.label}"))
            elif ev.kind == "end":
                out.append(c("cyan", f"← {ev.label}"))
            else:
                glyph = ev.label[:1]
                rest = ev.label[1:]
                if use_color and sys_has_color():
                    color = "green" if ev.ok else "red"
                    out.append(self._indent + c(color, glyph) + dim(rest))
                else:
                    out.append(self._indent + ev.label)
        return "\n".join(out)

    def summary_line(self) -> str:
        """One-line trace: start + end + tool count."""
        starts = [e for e in self.events if e.kind == "start"]
        ends = [e for e in self.events if e.kind == "end"]
        tools = [e for e in self.events if e.kind == "tool"]
        head = starts[0].label if starts else f"[{self.name}]"
        tail = ends[-1].label if ends else "(incomplete)"
        return f"{head}  ({len(tools)} tool calls)  {tail}"

    def duration_s(self) -> float:
        return time.time() - self.started_at

    def dump(self, path: str) -> None:
        import json
        with open(path, "w", encoding="utf-8") as fh:
            for ev in self.events:
                fh.write(json.dumps({
                    "kind": ev.kind, "label": ev.label,
                    "ok": ev.ok, "ts": ev.ts,
                }, ensure_ascii=False) + "\n")


def sys_has_color() -> bool:
    import sys
    return sys.stderr.isatty()
