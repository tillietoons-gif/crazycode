"""Token / cost accounting.

Tracks token usage across the whole session and estimates cost from the
provider's pricing table. Per-turn deltas let the CLI show "this prompt
cost ~$0.012" and the `/cost` command show the session total.

Pricing is in USD per 1M tokens. We use a small built-in table for known
models and a conservative default otherwise. Venice / OpenRouter expose
their own pricing; we approximate with the OpenAI-style numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# USD per 1M tokens: (input, output). Conservative defaults for unknown models.
_DEFAULT_PRICING = {"input": 0.50, "output": 1.50}

# A few well-known model price points (approximate, 2026 pricing).
_PRICING: Dict[str, Dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-5.5": {"input": 6.25, "output": 37.50},
    "deepseek-v4-1-flash": {"input": 0.18, "output": 0.37},
    "deepseek-v4-flash": {"input": 0.18, "output": 0.37},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-opus-4-5": {"input": 15.00, "output": 75.00},
    "llama3.1:8b": {"input": 0.0, "output": 0.0},  # local Ollama = free
}


def pricing_for(model: str) -> Dict[str, float]:
    """Return USD-per-1M-tokens pricing for a model (defaults if unknown)."""
    if not model:
        return dict(_DEFAULT_PRICING)
    # try exact, then prefix (e.g. "openai-gpt-4o-2024-11-20" -> "gpt-4o")
    if model in _PRICING:
        return dict(_PRICING[model])
    for key, val in _PRICING.items():
        if key in model:
            return dict(val)
    return dict(_DEFAULT_PRICING)


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_prompt_tokens: int = 0

    def __iadd__(self, other: "TokenUsage") -> "TokenUsage":
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.cached_prompt_tokens += other.cached_prompt_tokens
        return self

    def to_dict(self) -> Dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_prompt_tokens": self.cached_prompt_tokens,
        }


class CostTracker:
    """Accumulates token usage and estimates cost for a session."""

    def __init__(self, model: str = ""):
        self.model = model
        self.pricing = pricing_for(model)
        self.session = TokenUsage()
        self._turn_base = TokenUsage()
        self.turn_count = 0

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, usage: Optional[Dict[str, Any]]) -> None:
        """Record a usage dict from an LLM API response (OpenAI shape).

        Recognized keys: prompt_tokens, completion_tokens, total_tokens,
        prompt_tokens_details.cached_tokens.
        """
        if not usage:
            return
        u = TokenUsage(
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            total_tokens=int(usage.get("total_tokens", 0) or 0),
            cached_prompt_tokens=int(
                (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
            ),
        )
        if u.total_tokens == 0:
            u.total_tokens = u.prompt_tokens + u.completion_tokens
        self.session += u

    def begin_turn(self) -> None:
        """Snapshot the session baseline so a turn delta can be computed."""
        self._turn_base = TokenUsage(
            self.session.prompt_tokens,
            self.session.completion_tokens,
            self.session.total_tokens,
            self.session.cached_prompt_tokens,
        )
        self.turn_count += 1

    def turn_delta(self) -> TokenUsage:
        """Tokens used since the last begin_turn()."""
        return TokenUsage(
            self.session.prompt_tokens - self._turn_base.prompt_tokens,
            self.session.completion_tokens - self._turn_base.completion_tokens,
            self.session.total_tokens - self._turn_base.total_tokens,
            self.session.cached_prompt_tokens - self._turn_base.cached_prompt_tokens,
        )

    # ------------------------------------------------------------------
    # Cost
    # ------------------------------------------------------------------

    def estimate_cost(self, usage: Optional[TokenUsage] = None) -> float:
        """USD estimate for `usage` (defaults to the whole session)."""
        u = usage or self.session
        # cached prompt tokens are billed at a discount; approximate at 10%
        cached_cost = u.cached_prompt_tokens * self.pricing["input"] * 0.1 / 1_000_000
        fresh_prompt = max(0, u.prompt_tokens - u.cached_prompt_tokens)
        prompt_cost = fresh_prompt * self.pricing["input"] / 1_000_000
        completion_cost = u.completion_tokens * self.pricing["output"] / 1_000_000
        return cached_cost + prompt_cost + completion_cost

    def session_cost(self) -> float:
        return self.estimate_cost(self.session)

    def turn_cost(self) -> float:
        return self.estimate_cost(self.turn_delta())

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        return {
            "model": self.model or "(default)",
            "pricing_per_1m": self.pricing,
            "turns": self.turn_count,
            "session_tokens": self.session.to_dict(),
            "session_cost_usd": round(self.session_cost(), 4),
            "last_turn_tokens": self.turn_delta().to_dict(),
            "last_turn_cost_usd": round(self.turn_cost(), 4),
        }

    def reset(self) -> None:
        self.session = TokenUsage()
        self._turn_base = TokenUsage()
        self.turn_count = 0
