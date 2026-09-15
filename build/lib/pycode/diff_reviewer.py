"""Interactive diff reviewer: preview and approve/reject each dry-run change.

Given a dry-run diff result (from tools.dry_run_diff or dispatch_tool(...,
dry_run=True)), this module shows a colorized diff in the terminal and asks
the user to approve / reject / hold. Approved changes are applied; rejected
ones are skipped.

The design is a two-phase flow:
  1. dry-run phase  - agent runs with dry_run=True, collecting diffs
  2. review phase   - each diff is presented for approval; approved ones are
                      applied by re-calling the tool without dry_run

This keeps the agent's LLM loop unchanged: it always plans the change first,
and a human gates the actual write.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, Dict, List, Optional

from pycode.tools import dry_run_diff, dispatch_tool


# ---------------------------------------------------------------------------
# Colorized diff rendering
# ---------------------------------------------------------------------------

def _color_line(line: str, use_color: bool) -> str:
    """Colorize a single diff line."""
    if not use_color or not sys.stderr.isatty():
        return line
    if line.startswith("+"):
        return f"\033[32m{line}\033[0m"
    if line.startswith("-"):
        return f"\033[31m{line}\033[0m"
    if line.startswith("@@"):
        return f"\033[36m{line}\033[0m"
    if line.startswith("---") or line.startswith("+++") or line.startswith("diff "):
        return f"\033[1m{line}\033[0m"
    return line


def render_diff(diff_text: str, use_color: bool = True, max_lines: int = 120) -> str:
    """Render a unified diff, colorized and optionally truncated."""
    lines = diff_text.splitlines()
    if not lines or diff_text.strip() in ("(no changes)", ""):
        return "(no changes)"
    out = []
    for i, ln in enumerate(lines):
        out.append(_color_line(ln, use_color))
        if i + 1 >= max_lines and len(lines) > max_lines:
            out.append(f"... ({len(lines) - max_lines} more lines)")
            break
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Review decision types
# ---------------------------------------------------------------------------

DECISION_APPROVE = "approve"
DECISION_REJECT = "reject"
DECISION_HOLD = "hold"


def _prompt_decision(index: int, total: int, path: str, diff_text: str) -> str:
    """Ask the user to approve/reject/hold a single change.

    Returns one of the DECISION_* constants.
    """
    print(f"\n\033[1m[{index}/{total}] {path}\033[0m", file=sys.stderr)
    print(render_diff(diff_text), file=sys.stderr)
    print(file=sys.stderr)
    try:
        answer = input("  approve this change? [a]pprove / [r]eject / [h]old-all / Enter=a [a] ")
    except (EOFError, KeyboardInterrupt):
        return DECISION_REJECT
    answer = answer.strip().lower()
    if answer in ("r", "reject", "n", "no"):
        return DECISION_REJECT
    if answer in ("h", "hold", "all"):
        return DECISION_HOLD
    return DECISION_APPROVE  # 'a', 'approve', 'y', 'yes', or empty


# ---------------------------------------------------------------------------
# Reviewer
# ---------------------------------------------------------------------------

class DiffReviewer:
    """Collects pending dry-run changes and lets the user review each one.

    Usage:
        reviewer = DiffReviewer()
        for change in planned_changes:
            reviewer.stage(change)      # change is a dry_run result dict
        reviewer.review()               # interactive, applies approved ones
    """

    def __init__(self, auto_approve: bool = False, apply: Optional[Callable[[str, Dict[str, Any]], None]] = None):
        self.pending: List[Dict[str, Any]] = []
        self.auto_approve = auto_approve
        # apply(tool_name, args) -> None; defaults to re-running dispatch_tool
        self._apply = apply or self._default_apply

    def _default_apply(self, tool_name: str, args: Dict[str, Any]) -> None:
        dispatch_tool(tool_name, args)

    def stage(self, change: Dict[str, Any]) -> None:
        """Stage a dry-run change result for review."""
        self.pending.append(change)

    def review(self) -> Dict[str, Any]:
        """Interactively review all staged changes.

        Returns a summary: {"approved": n, "rejected": n, "total": n}.
        """
        summary = {"approved": 0, "rejected": 0, "total": len(self.pending)}
        hold_all = False

        for i, change in enumerate(self.pending, start=1):
            path = change.get("path", "(unknown)")
            diff_text = change.get("diff", "")
            tool_name = change.get("_tool", "write")
            args = change.get("_args", {})

            if self.auto_approve or hold_all:
                self._apply(tool_name, args)
                summary["approved"] += 1
                continue

            decision = _prompt_decision(i, summary["total"], path, diff_text)
            if decision == DECISION_APPROVE:
                self._apply(tool_name, args)
                summary["approved"] += 1
            elif decision == DECISION_REJECT:
                summary["rejected"] += 1
            else:  # hold-all
                hold_all = True
                summary["approved"] += 1
                # remaining changes are auto-applied via hold_all path
                # (loop continues; hold_all triggers _apply for rest)

        return summary


def review_single(change: Dict[str, Any], apply_fn: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> str:
    """Review one staged change; returns the decision taken."""
    r = DiffReviewer(apply=apply_fn)
    r.stage(change)
    s = r.review()
    return DECISION_APPROVE if s["approved"] else DECISION_REJECT
