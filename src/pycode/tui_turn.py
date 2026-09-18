"""Turn summary panel: what happened in the last agent turn.

Renders a compact box under the assistant's reply::

    ╭─ turn ────────────────────────────────────╮
    │ ✔ 4 tool calls · 6.2s · 2 files changed   │
    │ ✎ src/app.py              +12 −3          │
    │ ✎ tests/test_app.py       +8 −0           │
    ╰───────────────────────────────────────────╯

File change counts come from ``git diff --numstat`` (best effort); without
a git repo the panel simply lists the touched files.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from pycode.tui import bold, c, dim


def _git_numstat(paths: List[str], root: str) -> Dict[str, Dict[str, int]]:
    """Map path -> {add, del} for the given paths (empty on any failure)."""
    import subprocess

    if not paths:
        return {}
    try:
        proc = subprocess.run(
            ["git", "diff", "--numstat", "--"] + paths,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
        )
        out: Dict[str, Dict[str, int]] = {}
        for line in (proc.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                add, dele, path = parts
                try:
                    key = path if os.path.isabs(path) else os.path.join(root, path)
                    out[key] = {"add": int(add), "del": int(dele)}
                except ValueError:
                    continue  # "-" means binary
        return out
    except Exception:  # noqa: BLE001
        return {}


def summarize_turn(
    tool_calls: int,
    duration_s: float,
    touched: List[str],
    root: str,
    reasoning: bool = False,
) -> Dict[str, Any]:
    """Build the data model for the panel (no rendering)."""
    numstat = _git_numstat(sorted(set(touched)), root)
    files = []
    for path in sorted(set(touched)):
        rel = os.path.relpath(path, root) if not os.path.isabs(path) else path
        stats = numstat.get(path) or numstat.get(rel) or {}
        files.append({"path": rel, "add": stats.get("add"), "del": stats.get("del")})
    return {
        "tool_calls": tool_calls,
        "duration_s": round(duration_s, 1),
        "files": files,
        "reasoning": reasoning,
    }


def _fmt_stat(add: Optional[int], dele: Optional[int]) -> str:
    a = f"+{add}" if add is not None else ""
    d = f"−{dele}" if dele is not None else ""
    sep = " " if a and d else ""
    if a == "" and d == "":
        return ""
    out = ""
    if a:
        out += c("green", a)
    out += sep
    if d:
        out += c("red", d)
    return out


def render_summary(summary: Dict[str, Any], width: int = 58) -> str:
    """Render the turn summary box (ANSI-safe, multiline string)."""
    files: List[Dict[str, Any]] = summary.get("files", [])
    head_bits = []
    n_tools = summary.get("tool_calls", 0)
    if n_tools:
        head_bits.append(f"{n_tools} tool call{'s' if n_tools != 1 else ''}")
    if summary.get("duration_s") is not None:
        head_bits.append(f"{summary['duration_s']}s")
    if files:
        head_bits.append(f"{len(files)} file{'s' if len(files) != 1 else ''} changed")
    if summary.get("reasoning"):
        head_bits.append("reasoned")

    lines: List[str] = []
    tl, tr, bl, br, h, v = "╭", "╮", "╰", "╯", "─", "│"

    def border(left: str, right: str) -> str:
        return dim(left + h * (width - 2) + right)

    if head_bits:
        head = " · ".join(head_bits)
        lines.append(dim(tl) + f"{_head_pad(head, width)}" + dim(tr))
        for f in files:
            path = f["path"]
            stat = _fmt_stat(f.get("add"), f.get("del"))
            stat_plain_len = (
                len(f"+{f['add']}") if f.get("add") is not None else 0
            ) + (len(f"−{f['del']}") if f.get("del") is not None else 0)
            pad = width - 4 - len(path) - stat_plain_len
            if pad < 1:
                path = path[: max(1, len(path) + pad - 3)] + "..."
                pad = max(1, width - 4 - len(path) - stat_plain_len)
            lines.append(
                f"{dim(v)} {c('magenta', '✎')} {path}{' ' * pad}{stat} {dim(v)}"
            )
    else:
        lines.append(border(tl, tr))
    lines.append(border(bl, br))
    return "\n".join(lines)


def _head_pad(head: str, width: int) -> str:
    pad = max(0, width - 2 - len(head))
    return " " + head + " " * pad


def print_summary(summary: Dict[str, Any], width: int = 58) -> None:
    import sys

    print(render_summary(summary, width), file=sys.stderr, flush=True)
