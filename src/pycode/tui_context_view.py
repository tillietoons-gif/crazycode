"""Context window visualization: a human-friendly bar of token usage.

Renders a multi-line block showing how full the context window is, which
parts of the conversation are being kept vs. trimmed, and how close to the
auto-trim threshold we are.
"""

from __future__ import annotations

import sys
from typing import Dict, Optional

from pycode.tui import c, dim

# color thresholds (percent of budget used)
_WARN_AT = 80
_CRITICAL_AT = 95


def context_bar(used: int, budget: int, width: int = 30, use_color: bool = True) -> str:
    """Return a colorized progress bar string for context usage."""
    if budget <= 0:
        return dim("ctx: no budget set")
    ratio = min(1.0, used / budget)
    filled = int(round(width * ratio))
    pct = int(100 * ratio)
    bar = "█" * filled + "░" * (width - filled)
    if not use_color or not sys.stdout.isatty():
        return f"ctx [{bar}] {used:,}/{budget:,} ({pct}%)"
    if pct >= _CRITICAL_AT:
        color = "red"
    elif pct >= _WARN_AT:
        color = "yellow"
    else:
        color = "green"
    label = f"ctx [{bar}] {used:,}/{budget:,} ({pct}%)"
    note = ""
    if pct >= _CRITICAL_AT:
        note = c("red", "  ⚠ over critical - trimming soon")
    elif pct >= _WARN_AT:
        note = c("yellow", "  ⚠ near trim threshold")
    return c(color, label) + note


def render_context_view(
    used: int,
    budget: int,
    message_count: int,
    trim_threshold_pct: int = 80,
    use_color: bool = True,
) -> str:
    """A multi-line context-window report."""
    bar = context_bar(used, budget, use_color=use_color)
    lines = [
        bar,
        dim(f"  {message_count} messages · auto-trim at {trim_threshold_pct}%"),
    ]
    if budget > 0:
        pct = int(100 * used / budget)
        headroom = max(0, budget - used)
        lines.append(dim(f"  headroom: {headroom:,} tokens (~{pct}% used)"))
    return "\n".join(lines)


def print_context(used: int, budget: int, message_count: int,
                  use_color: bool = True) -> None:
    print(render_context_view(used, budget, message_count, use_color=use_color),
          file=sys.stderr, flush=True)
