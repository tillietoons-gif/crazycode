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

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Send a chat completion request with tool support.

        Returns a dict with:
          - content: str
          - tool_calls: list of {id, type, function: {name, arguments}}
        """

    def chat(self, messages: List[Dict[str, Any]], **kwargs) -> str:
        """Simple non-tool chat (for testing/debugging)."""
        return self.chat_stream(messages, **kwargs).get("content", "")
