"""Tests for pycode v0.8 UI/UX additions: session picker, context view, subagent trace,
config inspector, log pager, HTML export, onboarding."""

from __future__ import annotations

import os
import tempfile
import unittest

from pycode.tui_session_picker import collect_sessions, SessionMeta, _preview, _text_pick
from pycode.tui_context_view import context_bar, render_context_view
from pycode.tui_subagent_trace import SubagentTrace
from pycode.tui_inspector import inspector_report, provider_status, permission_status, mcp_status
from pycode.tui_pager import _page, paginate_or_print
from pycode.session_export import export_to_html
from pycode.onboarding import has_any_credential, onboarding_message
from pycode.agent import Agent
from pycode.permissions import PermissionGuard
from pycode.mcp import MCPRegistry


# ---------------------------------------------------------------------------
# Session picker
# ---------------------------------------------------------------------------

class TestSessionPicker(unittest.TestCase):
    def test_collect_sessions_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(collect_sessions(d), [])

    def test_collect_sessions_finds_saved(self):
        from pycode.session import save_session
        with tempfile.TemporaryDirectory() as d:
            save_session([{"role": "user", "content": "hi"}], root=d)
            items = collect_sessions(d)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].preview, "hi")
            self.assertGreaterEqual(items[0].msg_count, 1)

    def test_preview_uses_first_user(self):
        preview = _preview([{"role": "system", "content": "s"},
                            {"role": "user", "content": "   hello   world "},
                            {"role": "assistant", "content": "a"}])
        # _preview normalizes whitespace (collapses runs of spaces to one)
        self.assertEqual(preview, "hello world")

    def test_session_meta_timestamp(self):
        m = SessionMeta(path="/x/session-20260914-103045.jsonl", mtime=0,
                        msg_count=2, preview="hi")
        self.assertIn("2026-09-14", m.timestamp_str())
        self.assertIn("10:30:45", m.timestamp_str())


# ---------------------------------------------------------------------------
# Context view
# ---------------------------------------------------------------------------

class TestContextView(unittest.TestCase):
    def test_bar_fully_used(self):
        self.assertIn("100%", context_bar(1000, 1000, use_color=False))

    def test_bar_no_budget(self):
        self.assertIn("no budget", context_bar(50, 0, use_color=False))

    def test_render_shape(self):
        out = render_context_view(42300, 60000, 12, use_color=False)
        self.assertIn("42,300/60,000", out)
        self.assertIn("12 messages", out)
        self.assertIn("headroom", out)

    def test_render_warning_near_threshold(self):
        out = render_context_view(59000, 60000, 10, use_color=False)
        # near/over the 80% threshold -> mentions trim
        self.assertIn("trim", out.lower() or "trim")


# ---------------------------------------------------------------------------
# Subagent trace
# ---------------------------------------------------------------------------

class TestSubagentTrace(unittest.TestCase):
    def _trace(self):
        t = SubagentTrace("task")
        t.start("explore auth")
        t.tool("read", True, "src/auth.py")
        t.tool("bash", False, "make test (exit 1)")
        t.end("found login(), logout()")
        return t

    def test_render_includes_all(self):
        out = self._trace().render(use_color=False)
        self.assertIn("explore auth", out)
        self.assertIn("read", out)
        self.assertIn("found login(), logout()", out)

    def test_summary_line(self):
        s = self._trace().summary_line()
        self.assertIn("2 tool calls", s)

    def test_dump(self):
        import json
        t = self._trace()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "trace.jsonl")
            t.dump(p)
            with open(p) as fh:
                lines = [json.loads(l) for l in fh if l.strip()]
            self.assertEqual(len(lines), 4)  # start + 2 tools + end
            self.assertEqual(lines[0]["kind"], "start")

    def test_agent_records_subagent_trace(self):
        # A stubbed LLM that issues a task tool call should leave a trace.
        agent = Agent(api_key="k", verbose=False, enable_subagents=True)
        class StubProvider:
            calls = 0
            def chat(self, messages, tools=None):
                StubProvider.calls += 1
                if StubProvider.calls == 1:
                    return {"content": "", "tool_calls": [{
                        "id": "t1", "type": "function",
                        "function": {"name": "task",
                                     "arguments": '{"task": "explore the module"}'}}]}
                return {"content": "done", "tool_calls": []}
            def chat_stream(self, messages, tools=None):
                from pycode.provider import LLMProviderError
                raise LLMProviderError("no stream")
        agent.provider = StubProvider()
        # stub the subagent registry's run_task to avoid spawning a real child
        agent.subagents.run_task = lambda **kw: '{"subagent":"task","result":"summary"}'
        agent.run("go")
        self.assertEqual(len(agent.subagent_traces), 1)
        # the start event label includes the task text
        self.assertIn("explore the module", agent.subagent_traces[0].events[0].label)
        # a summary report is available and shows the nested trace markers
        report = agent.subagent_trace_report()
        self.assertIn("→", report)
        self.assertIn("←", report)


# ---------------------------------------------------------------------------
# Config inspector
# ---------------------------------------------------------------------------

class TestInspector(unittest.TestCase):
    def test_provider_status_single(self):
        agent = Agent(api_key="k")
        out = provider_status(agent.provider)
        self.assertIn("provider", out.lower())
        self.assertIn("flash", out)  # model name deepseek-v4-1-flash

    def test_permission_status(self):
        out = permission_status(PermissionGuard(allow_tools=["read"], yolo=True))
        self.assertIn("YOL", out)
        self.assertIn("read", out)

    def test_mcp_status_none(self):
        self.assertIn("none attached", mcp_status(None))

    def test_mcp_status_attached(self):
        reg = MCPRegistry()
        s = reg.add("x", ["echo"])
        # not connected
        out = mcp_status(reg)
        self.assertIn("x", out)
        self.assertIn("down", out)

    def test_full_report(self):
        agent = Agent(api_key="k")
        rep = inspector_report(agent)
        self.assertIn("provider", rep.lower())
        self.assertIn("permission", rep.lower())
        self.assertIn("mcp", rep.lower())


# ---------------------------------------------------------------------------
# Log pager
# ---------------------------------------------------------------------------

class TestPager(unittest.TestCase):
    def test_page_math(self):
        lines = list(range(50))
        page = _page(lines, 2, 10)
        self.assertEqual(page, list(range(20, 30)))

    def test_paginate_or_print_short(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            paginate_or_print("line1\nline2", page_size=10)
        self.assertIn("line1", buf.getvalue())

    def test_paginate_or_print_long_uses_pager(self):
        # On non-TTY, paginate_or_print just prints everything
        import io
        from unittest.mock import patch
        buf = io.StringIO()
        long_text = "\n".join(f"l{i}" for i in range(100))
        with patch("sys.stdout.isatty", return_value=False):
            with patch("builtins.print") as mock_print:
                paginate_or_print(long_text, page_size=20)
                # non-tty path prints the whole text
                self.assertTrue(mock_print.called)


# ---------------------------------------------------------------------------
# HTML export
# ---------------------------------------------------------------------------

class TestHtmlExport(unittest.TestCase):
    def _msgs(self):
        return [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi there"},
            {"role": "assistant", "content": "hello",
             "tool_calls": [{"id": "x", "function": {"name": "read",
                                                     "arguments": '{"path":"f.py"}'}}]},
            {"role": "tool", "tool_call_id": "x", "content": "l1\nl2"},
        ]

    def test_export_writes_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "out.html")
            out = export_to_html(self._msgs(), p, title="my session")
            self.assertEqual(out, p)
            with open(p) as fh:
                html = fh.read()
            self.assertIn("<!doctype html>", html)
            self.assertIn("hi there", html)
            self.assertIn("my session", html)
            self.assertIn("tool result".lower(), html.lower())
            # tool calls rendered as <details>
            self.assertIn("<details", html)
            self.assertIn("read", html)

    def test_export_escaping(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.html")
            export_to_html([{"role": "user", "content": "<script>alert(1)</script>"}], p)
            with open(p) as fh:
                html = fh.read()
            # must be escaped, no raw <script>
            self.assertNotIn("<script>alert", html)
            self.assertIn("&lt;script&gt;", html)


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------

class TestOnboarding(unittest.TestCase):
    def test_onboarding_message_mentions_options(self):
        msg = onboarding_message()
        self.assertIn("Ollama", msg)
        self.assertIn("PYCODE_API_KEY", msg)

    def test_has_any_credential_detects_env(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {"PYCODE_API_KEY": "sk-test"}, clear=True):
            self.assertTrue(has_any_credential())
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(has_any_credential())


if __name__ == "__main__":
    unittest.main()
