"""Tests for pycode v0.6: cost tracking, failover, prompt caching, web search + vision."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from pycode.cost import CostTracker, TokenUsage, pricing_for
from pycode.failover import FailoverProvider, ProviderConfig
from pycode.provider import LLMProvider, LLMProviderError, build_cached_system_messages
from pycode.tools import dispatch_tool, TOOL_SCHEMAS, TOOLS
from pycode.agent import Agent


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------

class TestCostTracker(unittest.TestCase):
    def test_pricing_for_known_model(self):
        p = pricing_for("gpt-4o")
        self.assertEqual(p["input"], 2.50)
        self.assertEqual(p["output"], 10.00)

    def test_pricing_prefix_match(self):
        # "openai-gpt-4o-2024-11-20" should match "gpt-4o"
        self.assertEqual(pricing_for("openai-gpt-4o-2024-11-20")["input"], 2.50)

    def test_pricing_default_for_unknown(self):
        p = pricing_for("some-unknown-model")
        self.assertEqual(p["input"], 0.50)

    def test_record_accumulates(self):
        t = CostTracker(model="gpt-4o")
        t.record({"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
        t.record({"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
        self.assertEqual(t.session.total_tokens, 300)

    def test_cached_tokens_discounted(self):
        t = CostTracker(model="gpt-4o")
        # 1M prompt tokens, all cached -> should cost ~10% of 1M * 2.50 = $2.50
        t.record({
            "prompt_tokens": 1_000_000,
            "completion_tokens": 0,
            "total_tokens": 1_000_000,
            "prompt_tokens_details": {"cached_tokens": 1_000_000},
        })
        # cached: 1M * 2.50 * 0.1 / 1M = 0.25 ; fresh prompt = 0
        self.assertAlmostEqual(t.session_cost(), 0.25, places=3)

    def test_turn_delta(self):
        t = CostTracker(model="gpt-4o")
        t.record({"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
        t.begin_turn()
        t.record({"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
        delta = t.turn_delta()
        self.assertEqual(delta.total_tokens, 150)  # only the post-baseline usage
        self.assertEqual(t.session.total_tokens, 300)

    def test_turn_count(self):
        t = CostTracker()
        for _ in range(3):
            t.begin_turn()
        self.assertEqual(t.turn_count, 3)

    def test_summary_shape(self):
        t = CostTracker(model="gpt-4o")
        s = t.summary()
        for key in ("model", "turns", "session_tokens", "session_cost_usd",
                    "last_turn_tokens", "last_turn_cost_usd"):
            self.assertIn(key, s)

    def test_reset(self):
        t = CostTracker()
        t.record({"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110})
        t.reset()
        self.assertEqual(t.session.total_tokens, 0)
        self.assertEqual(t.turn_count, 0)

    def test_local_model_free(self):
        t = CostTracker(model="llama3.1:8b")
        t.record({"prompt_tokens": 100000, "completion_tokens": 50000, "total_tokens": 150000})
        self.assertEqual(t.session_cost(), 0.0)


# ---------------------------------------------------------------------------
# Failover
# ---------------------------------------------------------------------------

class _FakeProvider:
    """Stands in for LLMProvider inside a ProviderConfig for testing."""

    def __init__(self, model: str, should_fail: bool = False, usage=None):
        self.model = model
        self.api_base = f"http://fake/{model}"
        self._should_fail = should_fail
        self._usage = usage

    def chat_stream(self, messages, tools=None):
        if self._should_fail:
            raise LLMProviderError(f"{self.model}: boom")
        return {
            "content": f"reply from {self.model}",
            "tool_calls": [],
            "usage": self._usage or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }


def _make_provider_with_fake(model, should_fail=False):
    """Build a ProviderConfig whose .provider is a fake (no real HTTP)."""
    pc = ProviderConfig(model=model, name=model)
    pc.provider = _FakeProvider(model, should_fail=should_fail)
    return pc


class TestFailover(unittest.TestCase):
    def test_falls_back_to_second_on_error(self):
        p1 = _make_provider_with_fake("venice", should_fail=True)
        p2 = _make_provider_with_fake("openrouter")
        fo = FailoverProvider([p1, p2])
        resp = fo.chat_stream([{"role": "user", "content": "hi"}])
        self.assertIn("openrouter", resp["content"])
        self.assertEqual(fo.last_good, 1)

    def test_sticky_to_last_good(self):
        p1 = _make_provider_with_fake("venice", should_fail=True)
        p2 = _make_provider_with_fake("openrouter")
        fo = FailoverProvider([p1, p2], default=0)
        fo.chat_stream([{"role": "user", "content": "hi"}])
        # now last_good is 1; next call should start from provider 2 directly
        self.assertEqual(fo.model, "openrouter")

    def test_all_fail_raises(self):
        p1 = _make_provider_with_fake("a", should_fail=True)
        p2 = _make_provider_with_fake("b", should_fail=True)
        fo = FailoverProvider([p1, p2])
        with self.assertRaises(LLMProviderError):
            fo.chat_stream([{"role": "user", "content": "hi"}])

    def test_records_usage_on_success(self):
        p1 = _make_provider_with_fake("venice", should_fail=True)
        p2 = _make_provider_with_fake("openrouter")
        fo = FailoverProvider([p1, p2])
        tracker = CostTracker(model="openrouter")
        resp = fo.chat_stream([{"role": "user", "content": "hi"}], usage_sink=tracker)
        self.assertEqual(tracker.session.total_tokens, 15)
        self.assertEqual(tracker.model, "openrouter")

    def test_pin(self):
        p1 = _make_provider_with_fake("a")
        p2 = _make_provider_with_fake("b")
        fo = FailoverProvider([p1, p2], default=0)
        fo.pin(1)
        self.assertEqual(fo.model, "b")

    def test_status_lists_all(self):
        p1 = _make_provider_with_fake("a")
        p2 = _make_provider_with_fake("b")
        fo = FailoverProvider([p1, p2])
        self.assertEqual(len(fo.status()), 2)

    def test_agent_attach_failover(self):
        agent = Agent(api_key="k")
        p1 = _make_provider_with_fake("bad", should_fail=True)
        p2 = _make_provider_with_fake("good")
        fo = FailoverProvider([p1, p2])
        agent.attach_failover(fo)
        # provider is the failover chain; starts at default index 0
        self.assertIs(agent.provider, fo)
        # running a call fails on p1 (bad) and lands on p2 (good)
        agent.provider.chat_stream([{"role": "user", "content": "hi"}])
        self.assertEqual(agent.provider.model, "good")
        self.assertIsInstance(agent.cost_tracker, CostTracker)


# ---------------------------------------------------------------------------
# Prompt caching
# ---------------------------------------------------------------------------

class TestPromptCaching(unittest.TestCase):
    def test_stable_system_prefix(self):
        msgs = build_cached_system_messages(
            system_content="You are pycode.",
            user_messages=[{"role": "user", "content": "hi"}],
        )
        # system prompt first, byte-stable
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[0]["content"], "You are pycode.")
        self.assertEqual(msgs[1], {"role": "user", "content": "hi"})

    def test_no_system_when_empty(self):
        msgs = build_cached_system_messages(
            system_content="",
            user_messages=[{"role": "user", "content": "hi"}],
        )
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "user")

    def test_repeated_calls_yield_identical_prefix(self):
        a = build_cached_system_messages("SYS", [{"role": "user", "content": "x"}])
        b = build_cached_system_messages("SYS", [{"role": "user", "content": "y"}])
        # the system prefix is identical across calls -> cacheable
        self.assertEqual(a[0], b[0])


# ---------------------------------------------------------------------------
# Web search + vision
# ---------------------------------------------------------------------------

class TestWebSearchAndVision(unittest.TestCase):
    def test_tools_registered(self):
        self.assertIn("web_search", TOOLS)
        self.assertIn("view_image", TOOLS)
        names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        self.assertIn("web_search", names)
        self.assertIn("view_image", names)

    def test_view_image_missing_file(self):
        out = json.loads(dispatch_tool("view_image", {"path": "/no/such/file.png"}))
        self.assertIn("error", out)

    def test_view_image_reads_real_file(self):
        # write a tiny 1x1 PNG (1 byte signature + a few bytes)
        import struct
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            # minimal PNG header + IHDR + IEND (enough for mime sniffing)
            f.write(b"\x89PNG\r\n\x1a\n")
            path = f.name
        try:
            out = json.loads(dispatch_tool("view_image", {"path": path}))
            self.assertIn("b64", out)
            self.assertEqual(out["mime"], "image/png")
            self.assertGreater(out["size_bytes"], 0)
        finally:
            os.unlink(path)

    def test_web_search_returns_graceful_error_offline(self):
        # In a sandboxed test env the network may be unavailable; we only assert
        # the shape, not the content.
        out = json.loads(dispatch_tool("web_search", {"query": "pycode", "max_results": 1}))
        self.assertIn("query", out)
        self.assertIn("results", out)


# ---------------------------------------------------------------------------
# Agent cost tracking integration
# ---------------------------------------------------------------------------

class TestAgentCostIntegration(unittest.TestCase):
    def test_agent_records_usage_on_llm_call(self):
        agent = Agent(api_key="k", verbose=False)
        # Stub the provider to return usage
        agent.provider = MagicMock()
        agent.provider.chat_stream = MagicMock(
            return_value={
                "content": "done",
                "tool_calls": [],
                "usage": {"prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280},
            }
        )
        agent.provider.model = "gpt-4o"
        result = agent.run("hello")
        self.assertEqual(result, "done")
        # cost tracker should have recorded the usage
        self.assertEqual(agent.cost_tracker.session.total_tokens, 280)
        self.assertGreater(agent.cost_tracker.session_cost(), 0)

    def test_cost_report_disabled(self):
        agent = Agent(api_key="k", verbose=False)
        agent.cost_tracker = None
        rep = agent.cost_report()
        self.assertTrue(rep.get("disabled"))


if __name__ == "__main__":
    unittest.main()
