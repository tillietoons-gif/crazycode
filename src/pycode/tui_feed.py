"""Tool-call activity feed: a compact timeline with status glyphs.

Each tool call is rendered as a one-line entry:

    ✓ read  src/agent.py        (182 lines)
    ✓ grep  "def run"  → 4 hits
    ✗ bash  pip install x      (exit 1)

When a tool call is in flight the feed shows a pending marker, and failed
calls carry an expandable error detail (toggle with `e`).

The feed is a simple append-only log; the CLI can also dump it to a file for
post-mortem.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pycode.tui import c, dim, redact_text, tool_badge

_OK = "✓"
_FAIL = "✗"
_PEND = "…"
_MCP = "→"

# per-tool detail extractors: (args, result_str) -> short detail
import json as _json


def _detail_bash(args: dict, result: str) -> str:
    try:
        d = _json.loads(result)
        code = d.get("exit_code", "?")
        return f"exit {code}" if str(code) != "0" else "ok"
    except Exception:
        return ""


def _detail_read(args: dict, result: str) -> str:
    try:
        n = len(result.splitlines())
        return f"{n} lines"
    except Exception:
        return ""


def _detail_grep(args: dict, result: str) -> str:
    try:
        hits = len(_json.loads(result))
        return f"{hits} hits"
    except Exception:
        return ""


def _detail_write(args: dict, result: str) -> str:
    try:
        d = _json.loads(result)
        if d.get("dry_run"):
            return "dry-run diff"
        return f"{d.get('bytes_written', '?')} bytes"
    except Exception:
        return ""


def _detail_edit(args: dict, result: str) -> str:
    try:
        d = _json.loads(result)
        if d.get("dry_run"):
            return "dry-run diff"
        return f"{d.get('replacements', '?')} replaced"
    except Exception:
        return ""


def _detail_error(args: dict, result: str) -> str:
    try:
        error = _json.loads(result).get("error", "")
        return redact_text(str(error))[:120]
    except Exception:
        return redact_text(result.strip().replace("\n", " "))[:120]


_DETAIL_FNS = {
    "bash": _detail_bash,
    "read": _detail_read,
    "grep": _detail_grep,
    "write": _detail_write,
    "edit": _detail_edit,
}


@dataclass
class FeedEntry:
    tool: str
    args: dict
    result: str
    ok: bool
    started_at: float
    finished_at: float
    mcp: bool = False

    @property
    def duration_ms(self) -> int:
        return int((self.finished_at - self.started_at) * 1000)

    def detail(self) -> str:
        if not self.ok:
            error = _detail_error(self.args, self.result)
            if error:
                return error
        fn = _DETAIL_FNS.get(self.tool)
        if fn:
            detail = fn(self.args, self.result)
            if detail:
                return detail
        return ""

    def short_label(self) -> str:
        # one-arg summary for the feed line
        if self.tool == "bash":
            return (self.args.get("command") or "")[:40]
        if self.tool in ("read", "write", "edit"):
            return self.args.get("path", "")
        if self.tool == "grep":
            return f'"{self.args.get("pattern","")}"'
        if self.tool == "glob":
            return f'"{self.args.get("pattern","")}"'
        if self.tool == "webfetch":
            return self.args.get("url", "")
        if self.tool == "web_search":
            return f'"{self.args.get("query","")}"'
        if self.tool == "view_image":
            return self.args.get("path", "")
        if self.tool == "task":
            return (self.args.get("task") or "")[:40]
        return ""


class ActivityFeed:
    """Append-only tool-call log with rendering helpers."""

    def __init__(self, use_color: bool = True):
        self.entries: List[FeedEntry] = []
        self._inflight: Dict[str, float] = {}  # tool_call_id -> start ts
        self.use_color = use_color

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def begin(self, tc_id: str, tool: str, args: dict) -> None:
        self._inflight[tc_id] = time.time()

    def end(
        self,
        tc_id: str,
        tool: str,
        args: dict,
        result: str,
        ok: bool,
        mcp: bool = False,
    ) -> FeedEntry:
        started = self._inflight.pop(tc_id, time.time())
        entry = FeedEntry(
            tool=tool, args=args, result=result, ok=ok,
            started_at=started, finished_at=time.time(), mcp=mcp,
        )
        self.entries.append(entry)
        return entry

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _glyph(self, e: FeedEntry) -> str:
        if e.mcp:
            return _MCP if e.ok else f"{_MCP!r}"
        if e.ok:
            return c("green", _OK) if self.use_color else _OK
        return c("red", _FAIL) if self.use_color else _FAIL

    def render_entry(self, e: FeedEntry) -> str:
        detail = e.detail()
        extra = f" ({detail})" if detail else ""
        dur = f"  {e.duration_ms}ms" if e.duration_ms > 0 else ""
        prefix = f"  {self._glyph(e)} "
        return (
            f"{prefix}{tool_badge(e.tool):<14} "
            f"{dim(e.short_label())}{extra}{dim(dur)}"
        )

    def render_all(self) -> str:
        return self.render_entries(self.entries)

    def render_entries(self, entries: List[FeedEntry]) -> str:
        return "\n".join(self.render_entry(e) for e in entries)

    def last_n(self, n: int) -> str:
        return "\n".join(self.render_entry(e) for e in self.entries[-n:])

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def dump(self, path: str) -> None:
        """Write the feed to a JSONL file (one entry per line)."""
        with open(path, "w", encoding="utf-8") as fh:
            for e in self.entries:
                fh.write(_json.dumps({
                    "tool": e.tool, "args": e.args, "ok": e.ok,
                    "mcp": e.mcp, "duration_ms": e.duration_ms,
                    "detail": e.detail(),
                    "result_preview": e.result[:500],
                }, ensure_ascii=False, default=str) + "\n")

    def stats(self) -> Dict[str, int]:
        ok = sum(1 for e in self.entries if e.ok)
        return {"total": len(self.entries), "ok": ok, "failed": len(self.entries) - ok}
