"""Multi-provider failover: try providers in order, move to the next on error.

Given a list of provider configs, the FailoverProvider calls them in order
until one succeeds. This is useful when your primary (e.g. Venice with X402
credits) is exhausted and you want to fall back to OpenRouter or a local
Ollama instance.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pycode.provider import LLMProvider, LLMProviderError
from pycode.cost import pricing_for


class ProviderConfig:
    """One candidate provider in the failover chain."""

    def __init__(
        self,
        api_key: str = "",
        api_base: str = "",
        model: str = "gpt-4o",
        name: str = "",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        self.name = name or (model or "provider")
        self.provider = LLMProvider(
            api_key=api_key,
            api_base=api_base,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    @property
    def model(self) -> str:
        return self.provider.model

    @property
    def api_base(self) -> str:
        return self.provider.api_base


class FailoverProvider:
    """Tries each provider in order; returns the first successful response."""

    def __init__(self, providers: List[ProviderConfig], default: Optional[int] = None):
        if not providers:
            raise ValueError("FailoverProvider needs at least one provider")
        self.providers = providers
        self.default_index = default if default is not None else 0
        # last successful provider index, for sticky failover
        self.last_good: int = self.default_index

    # ------------------------------------------------------------------
    # Properties mirroring LLMProvider
    # ------------------------------------------------------------------

    @property
    def model(self) -> str:
        return self.providers[self.last_good].model

    @property
    def api_base(self) -> str:
        return self.providers[self.last_good].api_base

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        usage_sink: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Call providers in order starting from last_good; return first success.

        If usage_sink is a CostTracker, record usage on the provider that
        actually succeeded.
        """
        n = len(self.providers)
        order = [self.last_good] + [(self.last_good + 1 + i) % n for i in range(n - 1)]
        last_err: Optional[Exception] = None
        for idx in order:
            p = self.providers[idx]
            try:
                resp = p.provider.chat_stream(messages, tools)
                if usage_sink is not None:
                    self._record_usage(usage_sink, p, resp)
                self.last_good = idx
                resp["_provider"] = p.name
                return resp
            except LLMProviderError as exc:
                last_err = exc
                continue
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue
        raise LLMProviderError(f"all {n} providers failed; last error: {last_err}")

    def chat(self, messages: List[Dict[str, Any]], **kwargs) -> str:
        return self.chat_stream(messages, **kwargs).get("content", "")

    def _record_usage(self, tracker: Any, p: ProviderConfig, resp: Dict[str, Any]) -> None:
        """Update tracker.model/pricing to the provider that succeeded, then record."""
        usage = resp.get("usage") if isinstance(resp, dict) else None
        if tracker is not None:
            # align the tracker with the actual provider used this turn
            tracker.model = p.model
            tracker.pricing = pricing_for(p.model)
            tracker.record(usage)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def status(self) -> List[Dict[str, str]]:
        return [
            {"name": p.name, "model": p.model, "base": p.api_base}
            for p in self.providers
        ]

    def pin(self, index: int) -> None:
        """Force subsequent calls to start from a specific provider."""
        if not (0 <= index < len(self.providers)):
            raise IndexError(f"provider index {index} out of range")
        self.last_good = index
