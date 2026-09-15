"""Session rewind / branching: checkpoint the conversation and roll back.

The agent records a checkpoint every time it processes a user turn. When the
agent goes down a wrong path, you can rewind to a prior checkpoint, optionally
append a corrected instruction, and let it re-plan from there. This is
Claude Code's "rewind" superpower: recover from a bad tool-call sequence
without losing the whole conversation.

Checkpoints are in-memory on the Agent; you can also persist them to a
branching session file via save_branch().
"""

from __future__ import annotations

import copy
import json
import time
from typing import Any, Dict, List, Optional


class Checkpoint:
    """A snapshot of the conversation at a user-turn boundary."""

    def __init__(self, index: int, messages: List[Dict[str, Any]], label: str = ""):
        self.index = index
        self.messages = copy.deepcopy(messages)
        self.label = label or _short_label(messages)
        self.created_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "label": self.label,
            "created_at": self.created_at,
            "messages": self.messages,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Checkpoint":
        return cls(
            index=d.get("index", 0),
            messages=d.get("messages", []),
            label=d.get("label", ""),
        )


def _short_label(messages: List[Dict[str, Any]]) -> str:
    """Derive a short label from the most recent user message."""
    for m in reversed(messages):
        if m.get("role") == "user":
            text = m.get("content", "")
            if isinstance(text, str):
                text = " ".join(text.split())
                return text[:40]
    return "(checkpoint)"


class RewindManager:
    """Manages checkpoints for an Agent instance."""

    def __init__(self):
        self.checkpoints: List[Checkpoint] = []

    def snapshot(self, messages: List[Dict[str, Any]], label: str = "") -> Checkpoint:
        cp = Checkpoint(len(self.checkpoints), messages, label)
        self.checkpoints.append(cp)
        return cp

    def list(self) -> List[str]:
        return [f"#{cp.index}: {cp.label}" for cp in self.checkpoints]

    def rewind_to(self, index: int, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return the messages list for checkpoint `index` (does not mutate the
        agent; the caller assigns the result back to agent.messages)."""
        cp = self.checkpoints[index]
        return copy.deepcopy(cp.messages)

    def latest(self) -> Optional[List[Dict[str, Any]]]:
        if not self.checkpoints:
            return None
        return copy.deepcopy(self.checkpoints[-1].messages)

    def branch(self, index: int, new_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Create a branch: take checkpoint `index` and append `new_messages`."""
        cp = self.checkpoints[index]
        branch_msgs = copy.deepcopy(cp.messages)
        branch_msgs.extend(copy.deepcopy(new_messages))
        # Record the branch as a new checkpoint so it's rewindable too
        self.snapshot(branch_msgs, label=f"branch from #{index}")
        return branch_msgs

    # ------------------------------------------------------------------
    # Persistence (for /rewind across sessions)
    # ------------------------------------------------------------------

    def save_branches(self, path: str) -> int:
        with open(path, "w", encoding="utf-8") as fh:
            for cp in self.checkpoints:
                fh.write(json.dumps(cp.to_dict(), ensure_ascii=False, default=str) + "\n")
        return len(self.checkpoints)

    @staticmethod
    def load_from_file(path: str) -> "RewindManager":
        mgr = RewindManager()
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    mgr.checkpoints.append(Checkpoint.from_dict(json.loads(line)))
                except json.JSONDecodeError:
                    continue
        return mgr
