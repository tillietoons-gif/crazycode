"""Tests for pycode v0.3: provider presets, context manager, MCP, dry-run diff."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from pycode.providers import PRESETS, get_preset, detect_preset
from pycode.context_manager import (
    estimate_tokens, conversation_tokens, trim_messages, context_stats,
)
from pycode.mcp import MCPRegistry, MCPServer
from pycode.tools import (
    dispatch_tool, dry_run_diff, compute_diff, is_destructive,
)
from pycode.agent import Agent


class TestProviderPresets(unittest.TestCase):
    def test_all_presets_have_required_keys(self):
        for name, p in PRESETS.items():
            self.assertIn("model", p)
            self.assertIn("api_base", p)
            self.assertIn("env_key", p)

    def test_get_preset(self):
        cfg = get_preset("openai", api_key="sk-test")
        self.assertEqual(cfg["api_key"], "sk-test")
        self.assertEqual(cfg["api_base"], "https://api.openai.com/v1")
        self.assertEqual(cfg["model"], "gpt-4o")

    def test_get_preset_ollama(self):
        cfg = get_preset("ollama", model="llama3.1:8b")
        self.assertEqual(cfg["api_base"], "http://localhost:11434/v1")
        self.assertEqual(cfg["model"], "llama3.1:8b")

    def test_unknown_preset_raises(self):
        with self.assertRaises(ValueError):
            get_preset("nonexistent")

    def test_detect_preset_uses_env(self):
        with unittest.mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-xyz"}, clear=False):
            cfg = detect_preset()
            self.assertEqual(cfg.get("api_key"), "sk-xyz")
            self.assertIn("api_base", cfg)


class TestContextManager(unittest.TestCase):
    def test_estimate_tokens(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertGreater(estimate_tokens("a" * 400), 0)

    def test_conversation_tokens(self):
        msgs = [
            {"role": "system", "content": "hello"},
            {"role": "user", "content": "hi"},
        ]
        self.assertGreater(conversation_tokens(msgs), 0)

    def test_trim_messages_keeps_system_and_recent(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old user msg " * 20},
            {"role": "tool", "tool_call_id": "t1", "content": "x" * 4000},
            {"role": "user", "content": "recent " * 20},
            {"role": "assistant", "content": "recent response " * 20},
        ]
        trimmed = trim_messages(msgs, max_tokens=200, keep_recent=2)
        # system prompt always kept
        self.assertEqual(trimmed[0]["role"], "system")
        # the big tool result should have been trimmed
        tool_msgs = [m for m in trimmed if m.get("role") == "tool"]
        if tool_msgs:
            self.assertIn("trimmed", tool_msgs[0]["content"])
        self.assertLessEqual(conversation_tokens(trimmed), conversation_tokens(msgs))

    def test_context_stats(self):
        msgs = [{"role": "user", "content": "a" * 400}]
        stats = context_stats(msgs, 1000)
        self.assertEqual(stats["messages"], 1)
        self.assertGreater(stats["used"], 0)
        self.assertIn("pct", stats)


class TestDryRunDiff(unittest.TestCase):
    def test_dry_run_write_new_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "new.txt")
            result = json.loads(dispatch_tool("write", {"path": p, "content": "hello"},
                                              dry_run=True))
            self.assertTrue(result["dry_run"])
            self.assertFalse(result["existed_before"])
            self.assertIn("+hello", result["diff"])
            # file must NOT have been created
            self.assertFalse(os.path.exists(p))

    def test_dry_run_write_existing(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.txt")
            with open(p, "w") as f:
                f.write("line1\nline2\n")
            result = json.loads(dispatch_tool("write", {"path": p, "content": "line1\nlineX\n"},
                                              dry_run=True))
            self.assertTrue(result["dry_run"])
            self.assertTrue(result["existed_before"])
            self.assertIn("-line2", result["diff"])
            self.assertIn("+lineX", result["diff"])
            # original file untouched
            self.assertEqual(open(p).read(), "line1\nline2\n")

    def test_dry_run_edit(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.py")
            with open(p, "w") as f:
                f.write("x = 1\n")
            result = json.loads(dispatch_tool(
                "edit", {"path": p, "old_string": "x = 1", "new_string": "x = 2"},
                dry_run=True))
            self.assertTrue(result["dry_run"])
            self.assertIn("-x = 1", result["diff"])
            self.assertIn("+x = 2", result["diff"])
            # file untouched
            self.assertEqual(open(p).read(), "x = 1\n")

    def test_compute_diff_no_change(self):
        self.assertEqual(compute_diff("a\n", "a\n"), "(no changes)")


class TestMCPRegistry(unittest.TestCase):
    def test_registry_routes_mcp_tool_names(self):
        reg = MCPRegistry()
        server = reg.add("demo", ["echo"])
        # fake a connected server with a known schema + result
        server._tools = [{"name": "greet", "description": "say hi"}]
        server._connected = True
        server._proc = MagicMock()
        server._proc.poll = lambda: None

        schemas = reg.all_schemas()
        names = [s["function"]["name"] for s in schemas]
        self.assertIn("mcp_demo_greet", names)

        # dispatch routes to the server; registry strips the mcp_<name>_ prefix
        # so the server receives the clean tool name
        captured = {}
        def fake_call(tool_name, arguments):
            captured["tool"] = tool_name
            captured["args"] = arguments
            return "hello from mcp"
        server.call_tool = fake_call
        out = reg.dispatch("mcp_demo_greet", {"who": "world"})
        self.assertEqual(out, "hello from mcp")
        # registry already stripped the prefix before calling the server
        self.assertEqual(captured["tool"], "greet")

    def test_dispatch_returns_none_for_unknown(self):
        reg = MCPRegistry()
        self.assertIsNone(reg.dispatch("mcp_nope_x", {}))


class TestAgentDryRunAndMCP(unittest.TestCase):
    def test_agent_dry_run_flag(self):
        agent = Agent(api_key="k", dry_run=True)
        self.assertTrue(agent.dry_run)

    def test_agent_attach_mcp(self):
        agent = Agent(api_key="k")
        reg = MCPRegistry()
        agent.attach_mcp(reg)
        self.assertIs(agent.mcp, reg)
        # schemas should be just the built-ins when no server connected
        self.assertEqual(len(agent._all_tool_schemas()), len(__import__("pycode.tools", fromlist=["TOOL_SCHEMAS"]).TOOL_SCHEMAS))


if __name__ == "__main__":
    unittest.main()
