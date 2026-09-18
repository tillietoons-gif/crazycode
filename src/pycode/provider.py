"""LLM provider - OpenAI-compatible API client with SSE streaming + fallback."""

from __future__ import annotations

import inspect
import json
import os
import sys
from typing import Any, Dict, List, Optional

import requests


class LLMProviderError(Exception):
    """Raised when the LLM API request fails."""


def accepts_kwarg(fn: Any, name: str) -> bool:
    """True if ``fn``'s signature has a parameter named ``name``.

    Used to stay compatible with duck-typed providers/fakes that may not
    accept newer keyword arguments.
    """
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


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
        self.api_key = (
            api_key
            or os.getenv("PYCODE_API_KEY", "")
            or os.getenv("OPENAI_API_KEY", "")
        )
        self.api_base = (
            api_base
            or os.getenv("PYCODE_API_BASE", "")
            or os.getenv("OPENAI_API_BASE", "")
            or "https://api.venice.ai/api/v1"
        ).rstrip("/")
        self.model = model or os.getenv("PYCODE_MODEL", "deepseek-v4-1-flash")
        self.temperature = temperature if temperature is not None else 0.3
        self.max_tokens = max_tokens or 8192
        # last recorded token usage from the API (OpenAI shape)
        self.last_usage: Optional[Dict[str, Any]] = None

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        on_delta: Optional[Any] = None,
        on_reasoning: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Send a chat completion request with tool support.

        Tries real SSE streaming first (so tokens appear live and Esc can
        abort between chunks); any streaming failure falls back to a single
        non-streaming POST. ``on_delta`` (if given) is called with each
        content string as it arrives; ``on_reasoning`` with each
        reasoning/thinking string from reasoning models.

        Returns a dict with:
          - content: str
          - tool_calls: list of {id, type, function: {name, arguments}}
          - usage: raw token usage dict (prompt/completion/cached)
          - reasoning: accumulated reasoning_content ("" if none)
        """
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        url = f"{self.api_base}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            with requests.post(
                url, headers=headers, json=payload, timeout=120, stream=True
            ) as resp:
                if resp.status_code >= 400:
                    raise LLMProviderError(
                        f"LLM API {resp.status_code}: {resp.text[:400]}"
                    )
                content, tool_calls, usage, reasoning = self._consume_sse(
                    resp, on_delta, on_reasoning
                )
                # An empty stream means the server ignored stream=True and
                # answered with a plain JSON body - retry without streaming.
                if not content and not tool_calls and usage is None:
                    return self._chat_nonstream(messages, tools)
                self.last_usage = usage or {}
                return {
                    "content": content,
                    "tool_calls": tool_calls,
                    "usage": self.last_usage,
                    "reasoning": reasoning,
                }
        except LLMProviderError:
            raise
        except Exception:  # noqa: BLE001 - stream failed; fall back below
            return self._chat_nonstream(messages, tools)

    def _consume_sse(
        self,
        resp: Any,
        on_delta: Optional[Any] = None,
        on_reasoning: Optional[Any] = None,
    ):
        """Parse an OpenAI-style SSE body into (content, tool_calls, usage, reasoning)."""
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        slots: Dict[int, Dict[str, str]] = {}
        usage: Optional[Dict[str, Any]] = None
        for raw_line in resp.iter_lines(decode_unicode=True):
            if isinstance(raw_line, bytes):
                raw_line = raw_line.decode("utf-8", errors="replace")
            line = raw_line.strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content")
            if piece:
                content_parts.append(piece)
                if on_delta is not None:
                    try:
                        on_delta(piece)
                    except Exception:  # noqa: BLE001
                        pass
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = slots.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                fn = tc.get("function") or {}
                slot["id"] = tc.get("id") or slot["id"]
                slot["name"] += fn.get("name") or ""
                slot["arguments"] += fn.get("arguments") or ""
            think = delta.get("reasoning_content") or delta.get("reasoning")
            if think:
                reasoning_parts.append(think)
                if on_reasoning is not None:
                    try:
                        on_reasoning(think)
                    except Exception:  # noqa: BLE001
                        pass
        tool_calls = [
            {
                "id": slots[i]["id"] or f"call_{i}",
                "type": "function",
                "function": {
                    "name": slots[i]["name"],
                    "arguments": slots[i]["arguments"] or "{}",
                },
            }
            for i in sorted(slots)
        ]
        return "".join(content_parts), tool_calls, usage, "".join(reasoning_parts)

    def _chat_nonstream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Single-shot request (the original pre-streaming path)."""
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
        reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""

        tool_calls = []
        for tc in tool_calls_raw:
            tool_calls.append(
                {
                    "id": tc.get("id", ""),
                    "type": tc.get("type", "function"),
                    "function": {
                        "name": tc.get("function", {}).get("name", ""),
                        "arguments": tc.get("function", {}).get("arguments", "{}"),
                    },
                }
            )

        # Record token usage for cost tracking
        self.last_usage = data.get("usage") or {}

        return {
            "content": content,
            "tool_calls": tool_calls,
            "usage": self.last_usage,
            "reasoning": reasoning,
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
