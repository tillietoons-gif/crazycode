"""Tests for pycode v0.2 enhancements: context, session, confirmation, TUI, provider."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from pycode.agent import Agent
from pycode.context import find_context_files, load_context
from pycode.provider import LLMProvider, LLMProviderError
from pycode.session import (latest_session, list_sessions, load_session,
                            save_session)
from pycode.tools import dispatch_tool, is_destructive


class TestProjectContext(unittest.TestCase):
    def test_no_context_files(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load_context(d), "")
            self.assertEqual(find_context_files(d), [])

    def test_claude_md_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "CLAUDE.md"), "w") as f:
                f.write("# Project rules\nUse tabs, not spaces.")
            ctx = load_context(d)
            self.assertIn("Use tabs, not spaces.", ctx)
            self.assertEqual(len(find_context_files(d)), 1)

    def test_agents_md_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "AGENTS.md"), "w") as f:
                f.write("agent notes")
            self.assertIn("agent notes", load_context(d))

    def test_agent_autoloads_context(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ".pycode.md"), "w") as f:
                f.write("custom rules here")
            agent = Agent(api_key="k", project_root=d)
            sys_msg = agent.messages[0]["content"]
            self.assertIn("custom rules here", sys_msg)


class TestSessionPersistence(unittest.TestCase):
    def test_save_and_load(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        with tempfile.TemporaryDirectory() as d:
            path = save_session(msgs, root=d)
            self.assertTrue(os.path.exists(path))
            loaded = load_session(path)
            self.assertEqual(len(loaded), 3)
            self.assertEqual(loaded[1]["content"], "hi")

    def test_latest_session(self):
        with tempfile.TemporaryDirectory() as d:
            save_session([{"role": "user", "content": "a"}], root=d)
            latest = latest_session(d)
            self.assertIsNotNone(latest)
            self.assertTrue(latest.endswith(".jsonl"))

    def test_list_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(list_sessions(d), [])
            save_session([{"role": "user", "content": "a"}], root=d)
            self.assertEqual(len(list_sessions(d)), 1)

    def test_agent_save_restore(self):
        with tempfile.TemporaryDirectory() as d:
            agent = Agent(api_key="k", project_root=d)
            agent.messages.append({"role": "user", "content": "question"})
            agent.messages.append({"role": "assistant", "content": "answer"})
            path = agent.save_session()
            restored = Agent.restore_session(path)
            self.assertEqual(len(restored), 3)
            self.assertEqual(restored[0]["role"], "system")


class TestDestructiveDetection(unittest.TestCase):
    def test_write_is_destructive(self):
        self.assertTrue(is_destructive("write", {"path": "/tmp/x"}))

    def test_edit_is_destructive(self):
        self.assertTrue(is_destructive("edit", {"path": "/tmp/x"}))

    def test_bash_rm_is_destructive(self):
        self.assertTrue(is_destructive("bash", {"command": "rm -rf /tmp/x"}))

    def test_bash_ls_is_not(self):
        self.assertFalse(is_destructive("bash", {"command": "ls -la"}))

    def test_read_not_destructive(self):
        self.assertFalse(is_destructive("read", {"path": "/tmp/x"}))

    def test_confirm_callback_blocks(self):
        # Decline the write
        declined = dispatch_tool(
            "write",
            {"path": "/tmp/should_not_exist", "content": "x"},
            confirm=lambda n, a: False,
        )
        self.assertIn("declined", declined)

    def test_auto_approve_skips_confirm(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("orig")
            path = f.name
        try:
            result = json.loads(
                dispatch_tool(
                    "write", {"path": path, "content": "new"}, auto_approve=True
                )
            )
            self.assertTrue(result["ok"])
        finally:
            os.unlink(path)

    def test_agent_always_yes_skips_confirm(self):
        agent = Agent(api_key="k", verbose=False, always_yes=True)
        self.assertTrue(agent.auto_approve)


class TestLLMProvider(unittest.TestCase):
    def test_defaults(self):
        p = LLMProvider(api_key="k")
        self.assertEqual(p.model, "deepseek-v4-1-flash")
        self.assertEqual(p.api_base, "https://api.venice.ai/api/v1")

    def test_fallback_in_provider(self):
        # Simulate a provider that fails streaming by monkeypatching chat
        class FakeProvider(LLMProvider):
            def chat(self, messages, **kwargs):
                return "fallback answer"

            def chat_stream(self, messages, **kwargs):
                raise LLMProviderError("streaming unsupported")

        p = FakeProvider(api_key="k")
        self.assertEqual(p.chat([{"role": "user", "content": "hi"}]), "fallback answer")

    def test_agent_fallback(self):
        agent = Agent(api_key="k")

        class StubProvider:
            def chat(self, messages, tools=None):
                return {"content": "ok", "tool_calls": []}

            def chat_stream(self, messages, tools=None):
                raise LLMProviderError("no stream")

        agent.provider = StubProvider()
        result = agent.run("hello")
        self.assertEqual(result, "ok")


class TestAgentIntegration(unittest.TestCase):
    def test_agent_runs_and_uses_tools(self):
        """Mock the LLM to force a tool call, then verify dispatch + result flow."""
        agent = Agent(api_key="k", verbose=False, auto_approve=True)

        # First call: LLM asks for a bash tool; second call: LLM finalizes.
        class StubProvider:
            call_count = 0

            def chat(self, messages, tools=None):
                StubProvider.call_count += 1
                if StubProvider.call_count == 1:
                    return {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "bash",
                                    "arguments": json.dumps(
                                        {"command": "echo agent-test"}
                                    ),
                                },
                            }
                        ],
                    }
                return {"content": "all done", "tool_calls": []}

            def chat_stream(self, messages, tools=None):
                raise LLMProviderError("force fallback")

        agent.provider = StubProvider()
        result = agent.run("do the thing")
        self.assertEqual(result, "all done")
        # messages should contain the tool result
        roles = [m["role"] for m in agent.messages]
        self.assertIn("tool", roles)
        tool_msg = [m for m in agent.messages if m["role"] == "tool"][0]
        self.assertIn("agent-test", tool_msg["content"])


if __name__ == "__main__":
    unittest.main()
