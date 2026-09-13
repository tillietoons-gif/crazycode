"""Context window management: estimate token usage and trim conversation history.

Uses a rough 4-chars-per-token estimate (good enough for budgeting). Keeps the
system prompt and the most recent messages; replaces older tool results with a
short placeholder so the agent stays within its context budget.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

# Rough chars-per-token ratio. 4 works for most English/code text.
_CHARS_PER_TOKEN = 4

# Placeholder text that replaces oversized tool results during trimming
_TRIM_MARKER = "[trimmed: tool output removed to save context]"


def estimate_tokens(text: str) -> int:
    """Rough token estimate for a string."""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def message_tokens(msg: Dict[str, Any]) -> int:
    """Estimate token cost of a single message."""
    tokens = 0
    content = msg.get("content") or ""
    if isinstance(content, str):
        tokens += estimate_tokens(content)
    elif content:
        tokens += estimate_tokens(json.dumps(content, default=str))
    # tool_calls add cost
    for tc in msg.get("tool_calls", []) or []:
        fn = tc.get("function", {})
        tokens += estimate_tokens(fn.get("arguments", ""))
        tokens += estimate_tokens(fn.get("name", ""))
    # role overhead
    tokens += 4
    return tokens


def conversation_tokens(messages: List[Dict[str, Any]]) -> int:
    """Estimate total tokens in a conversation."""
    return sum(message_tokens(m) for m in messages)


def trim_messages(
    messages: List[Dict[str, Any]],
    max_tokens: int,
    keep_recent: int = 6,
) -> List[Dict[str, Any]]:
    """Trim a conversation to fit within `max_tokens`.

    Strategy:
      1. Always keep the system prompt (index 0) and the last `keep_recent`
         messages intact.
      2. For older messages, replace large tool-result content with a short
         placeholder (preserving role / tool_call_id so the LLM protocol
         stays valid).
      3. If still over budget, drop the oldest non-system, non-recent messages.

    Returns a new list (does not mutate the input).
    """
    if not messages:
        return []

    result = [dict(m) for m in messages]  # shallow copy per message

    # First pass: shrink old tool results
    system_count = 1 if result and result[0].get("role") == "system" else 0
    total = conversation_tokens(result)
    if total <= max_tokens:
        return result

    # Replace oversized tool-result bodies in the older half of the conversation
    cutoff = max(system_count, len(result) - keep_recent)
    for i in range(system_count, cutoff):
        msg = result[i]
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if estimate_tokens(content) > 200:  # only trim big outputs
                result[i] = {**msg, "content": _TRIM_MARKER}

    total = conversation_tokens(result)
    if total <= max_tokens:
        return result

    # Second pass: drop oldest non-system, non-recent messages entirely
    protect = set(range(system_count, system_count)) | set(range(len(result) - keep_recent, len(result)))
    dropped = []
    for i in range(system_count, len(result) - keep_recent):
        if i in protect:
            continue
        dropped.append(i)
    for idx in reversed(dropped):
        result = [m for j, m in enumerate(result) if j != idx]
        if conversation_tokens(result) <= max_tokens:
            break

    return result


def context_stats(messages: List[Dict[str, Any]], max_tokens: int) -> Dict[str, int]:
    """Return token usage stats for display."""
    used = conversation_tokens(messages)
    return {
        "used": used,
        "budget": max_tokens,
        "pct": min(100, round(100 * used / max_tokens)) if max_tokens else 0,
        "messages": len(messages),
    }
