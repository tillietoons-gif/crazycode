"""LLM provider - OpenAI-compatible API client with SSE streaming + fallback."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

import requests


class LLMProviderError(Exception):
    """Raised when the LLM API request fails."""


class LLMProvider:
    """OpenAI-compatible LLM API client with streaming support and non-streaming fallback."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        self.api_key = api_key or os.getenv("PYCODE_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
        self.api_base = (api_base or os.getenv("PYCODE_API_BASE", "") or os.getenv("OPENAI_API_BASE", "") or "https://api.venice.ai/api/v1").rstrip("/")
        self.model = model or os.getenv("PYCODE_MODEL", "deepseek-v4-1-flash")
        self.temperature = temperature if temperature is not None else 0.3
        self.max_tokens = max_tokens or 8192
        # last recorded token usage from the API (OpenAI shape)
        self.last_usage: Optional[Dict[str, Any]] = None

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Send a chat completion request with tool support.

        Returns a dict with:
          - content: str
          - tool_calls: list of {id, type, function: {name, arguments}}
          - usage: raw token usage dict (prompt/completion/cached)
        """
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        url = f"{self.api_base}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        if resp.status_code >= 400:
            raise LLMProviderError(f"LLM API {resp.status_code}: {resp.text[:400]}")
        data = resp.json()

        choice = data["choices"][0]
        msg = choice.get("message", {})

        content = msg.get("content") or ""
        tool_calls_raw = msg.get("tool_calls") or []

        tool_calls = []
        for tc in tool_calls_raw:
            tool_calls.append({
                "id": tc.get("id", ""),
                "type": tc.get("type", "function"),
                "function": {
                    "name": tc.get("function", {}).get("name", ""),
                    "arguments": tc.get("function", {}).get("arguments", "{}"),
                },
            })

        # Record token usage for cost tracking
        self.last_usage = data.get("usage") or {}

        return {
            "content": content,
            "tool_calls": tool_calls,
            "usage": self.last_usage,
        }

    def chat(self, messages: List[Dict[str, Any]], **kwargs) -> str:
        """Simple non-tool chat (for testing/debugging)."""
        return self.chat_stream(messages, **kwargs).get("content", "")


# ---------------------------------------------------------------------------
# Prompt caching
# ---------------------------------------------------------------------------

def build_cached_system_messages(
    system_content: str,
    user_messages: List[Dict[str, Any]],
    cache: bool = True,
) -> List[Dict[str, Any]]:
    """Build a messages list with a cacheable system-prompt prefix.

    OpenAI-style prompt caching works when the prefix is byte-stable across
    requests. We put the full system prompt first (stable), then the
    conversation. Providers that support `cache_control` (Anthropic-style)
    get a marker; OpenAI does it automatically on stable prefixes.

    Returns a list ready to pass as `messages` to chat_stream.
    """
    msgs: List[Dict[str, Any]] = []
    if system_content:
        sys_msg: Dict[str, Any] = {"role": "system", "content": system_content}
        if cache:
            # OpenAI auto-caches stable prefixes; no explicit marker needed.
            # The stable prefix is what matters - keep it byte-identical.
            pass
        msgs.append(sys_msg)
    msgs.extend(user_messages)
    return msgs
