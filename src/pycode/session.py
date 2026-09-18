"""Session persistence: save/restore agent conversation to JSONL files."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_SESSION_DIR = ".pycode-sessions"


def session_dir(root: Optional[str] = None) -> Path:
    base = Path(root) if root else Path(os.getcwd())
    d = base / _SESSION_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_session(
    messages: List[Dict[str, Any]],
    path: Optional[str] = None,
    root: Optional[str] = None,
) -> str:
    """Save a message list to a JSONL file. Returns the file path."""
    if path is None:
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = str(session_dir(root) / f"session-{ts}.jsonl")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        for msg in messages:
            fh.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")
    return str(p)


def load_session(path: str) -> List[Dict[str, Any]]:
    """Load a JSONL session file into a message list."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Session not found: {path}")
    msgs: List[Dict[str, Any]] = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                msgs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return msgs


def list_sessions(root: Optional[str] = None) -> List[str]:
    """Return sorted list of session file paths in the current project."""
    d = session_dir(root)
    if not d.is_dir():
        return []
    return sorted([str(f) for f in d.glob("session-*.jsonl")], reverse=True)


def latest_session(root: Optional[str] = None) -> Optional[str]:
    """Return the most recent session file path, or None."""
    sessions = list_sessions(root)
    return sessions[0] if sessions else None
