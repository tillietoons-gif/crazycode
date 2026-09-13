"""Project context loader - auto-discovers and loads project instruction files."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

# Files the agent will auto-load, in priority order (first found wins)
_CONTEXT_FILES = [
    "CLAUDE.md",
    ".pycode.md",
    ".pycode/context.md",
    "AGENTS.md",
    "AGENT.md",
    "pycode.md",
]


def find_context_files(root: Optional[str] = None) -> List[str]:
    """Search upward from `root` (default cwd) for known context files.

    Returns absolute paths of files found, in priority order.
    """
    base = Path(root) if root else Path(os.getcwd())
    found: List[str] = []

    # Search current dir and all parents
    candidates = [base]
    parent = base.parent
    for _ in range(6):  # max 6 levels up
        if parent == base:
            break
        candidates.append(parent)
        parent = parent.parent

    seen = set()
    for cand_dir in candidates:
        for fname in _CONTEXT_FILES:
            fpath = cand_dir / fname
            if fpath.is_file():
                key = str(fpath.resolve())
                if key not in seen:
                    seen.add(key)
                    found.append(key)

    return found


def load_context(root: Optional[str] = None) -> str:
    """Load and return the project context content (empty string if none found).

    Looks for CLAUDE.md / .pycode.md / AGENTS.md etc. in cwd and parents.
    """
    files = find_context_files(root)
    if not files:
        return ""
    parts: List[str] = []
    for f in files:
        try:
            content = Path(f).read_text(encoding="utf-8")
            if content.strip():
                parts.append(f"### {f}\n{content.strip()}")
        except Exception:  # noqa: BLE001
            continue
    return "\n\n".join(parts)


def context_header(files: List[str]) -> str:
    """Build a short header showing which context files were loaded."""
    if not files:
        return ""
    return "\n".join([f"  Loaded project context: {f}" for f in files])
