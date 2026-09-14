"""Live status bar: a one-line in-place meter for context, cost, model, turn.

The status bar renders a fixed-width line that the CLI updates after each
LLM call. It uses ANSI carriage-return rewriting so the line refreshes in
place on a TTY and degrades to plain (re-)print in non-TTY mode.

Example:
    [iter 3/30]  ctx 42.3k/60.0k (70%)  ·  $0.083  ·  deepseek-v4-1-flash
"""

from __future__ import annotations

import sys
from typing import Dict, Optional

from pycode.tui import c, dim


def _fmt_k(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _fmt_usd(v: float) -> str:
    if v == 0:
        return "$0.00"
    if v < 0.01:
        return f"${v:.4f}"
    return f"${v:.3f}"


def build_status_line(
    iteration: int,
    max_iterations: int,
    ctx_used: int,
    ctx_budget: int,
    cost_session: float,
    model: str,
) -> str:
    """Compose the raw (unANSI-colored) status line."""
    pct = int(100 * ctx_used / ctx_budget) if ctx_budget else 0
    ctx_bar = _bar(ctx_used, ctx_budget, width=20)
    return (
        f"[iter {iteration}/{max_iterations}] "
        f"ctx {ctx_bar} {_fmt_k(ctx_used)}/{_fmt_k(ctx_budget)} ({pct}%) "
        f"· {_fmt_usd(cost_session)} · {model}"
    )


def _bar(used: int, budget: int, width: int = 20) -> str:
    """ASCII progress bar, e.g. ████░░░░."""
    if budget <= 0:
        return "░" * width
    ratio = min(1.0, used / budget)
    filled = int(round(width * ratio))
    return "█" * filled + "░" * (width - filled)


def paint(line: str, use_color: bool = True) -> str:
    """Apply subtle ANSI coloring to a status line (dim bar, normal rest)."""
    if not use_color or not sys.stderr.isatty():
        return line
    # color the progress-bar segment (between "ctx " and " (")
    idx = line.find("█")
    if idx == -1:
        idx = line.find("░")
    if idx == -1:
        return dim(line)
    end = line.find(")", idx)
    if end == -1:
        end = len(line)
    head = line[:idx]
    bar = line[idx:end + 1]
    tail = line[end + 1:]
    # color over-budget contexts red
    pct = 0
    try:
        pct = int(bar.split("(")[-1].split(")")[0].replace("%", ""))
    except (IndexError, ValueError):
        pct = 0
    bar_color = "red" if pct >= 100 else ("yellow" if pct >= 80 else "gray")
    return c("gray", head) + c(bar_color, bar) + c("gray", tail)


def update_status(
    iteration: int,
    max_iterations: int,
    ctx_used: int,
    ctx_budget: int,
    cost_session: float,
    model: str,
    use_color: bool = True,
) -> None:
    """Print the status line to stderr (in-place on a TTY, append otherwise)."""
    raw = build_status_line(iteration, max_iterations, ctx_used, ctx_budget,
                            cost_session, model)
    painted = paint(raw, use_color=use_color)
    if sys.stderr.isatty():
        # fixed-width rewrite so the line overwrites in place
        print("\r" + painted.ljust(64), end="", file=sys.stderr, flush=True)
    else:
        print(painted, file=sys.stderr, flush=True)


def clear_status() -> None:
    """Erase the status line (TTY only)."""
    if sys.stderr.isatty():
        print("\r" + " " * 64, end="", file=sys.stderr, flush=True)
