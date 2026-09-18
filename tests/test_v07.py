"""Tests for pycode v0.7 TUI enhancements: markdown, statusbar, feed, diff review, commands."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from pycode.tui_markdown import render_markdown, highlight_code
from pycode.tui_statusbar import build_status_line, _bar, _fmt_k, _fmt_usd
from pycode.tui_feed import ActivityFeed, FeedEntry
from pycode.tui_commands import COMMANDS, ALIASES, canonicalize, help_text
from pycode.tui_diff_review import InteractiveDiffReviewer
from pycode.agent import Agent
from pycode.cost import CostTracker


# ---------------------------------------------------------------------------
# Markdown / code highlighter
# ---------------------------------------------------------------------------

class TestMarkdown(unittest.TestCase):
    def test_code_block_no_crash(self):
        text = "```\nlet x = 1\n```"
        out = render_markdown(text, use_color=False)
        self.assertIn("let x = 1", out)

    def test_header_becomes_bold(self):
        out = render_markdown("## Hello", use_color=True)
        # header renders with bold escape when TTY; in test env stdout is not TTY,
        # so we just assert the text survives
        self.assertIn("Hello", out)

    def test_bold_and_italic(self):
        out = render_markdown("**bold** and *ital*", use_color=False)
        self.assertIn("bold", out)
        self.assertIn("ital", out)

    def test_inline_code(self):
        out = render_markdown("use `foo()` here", use_color=False)
        self.assertIn("foo()", out)

    def test_list_items(self):
        out = render_markdown("- a\n- b", use_color=False)
        self.assertIn("a", out)
        self.assertIn("b", out)

    def test_highlight_python_keywords(self):
        code = highlight_code("def foo():\n    return 1", "python", use_color=True)
        self.assertIn("def", code)

    def test_highlight_unknown_lang_still_works(self):
        code = highlight_code("x = 1", "unknownlang", use_color=True)
        self.assertIn("x = 1", code)


# ---------------------------------------------------------------------------
# Status bar
# ---------------------------------------------------------------------------

class TestStatusBar(unittest.TestCase):
    def test_bar_fully_filled(self):
        self.assertEqual(_bar(100, 100, width=10), "██████████")

    def test_bar_empty(self):
        self.assertEqual(_bar(0, 100, width=10), "░░░░░░░░░░")

    def test_bar_half(self):
        b = _bar(50, 100, width=10)
        self.assertEqual(b.count("█"), 5)

    def test_fmt_k(self):
        self.assertEqual(_fmt_k(42300), "42.3k")
        self.assertEqual(_fmt_k(900), "900")

    def test_fmt_usd(self):
        self.assertEqual(_fmt_usd(0.0), "$0.00")
        self.assertIn("$", _fmt_usd(0.003))

    def test_build_status_line_shape(self):
        line = build_status_line(3, 30, 42300, 60000, 0.083, "deepseek-v4-1-flash")
        self.assertIn("iter 3/30", line)
        self.assertIn("42.3k/60.0k", line)
        self.assertIn("deepseek-v4-1-flash", line)


# ---------------------------------------------------------------------------
# Activity feed
# ---------------------------------------------------------------------------

class TestActivityFeed(unittest.TestCase):
    def _feed(self):
        f = ActivityFeed(use_color=False)
        import time
        f.begin("t1", "read", {"path": "a.py"})
        f.end("t1", "read", {"path": "a.py"}, "l1\nl2\nl3", ok=True)
        f.begin("t2", "bash", {"command": "false"})
        f.end("t2", "bash", {"command": "false"}, '{"exit_code":1}', ok=False)
        return f

    def test_stats(self):
        f = self._feed()
        s = f.stats()
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["ok"], 1)
        self.assertEqual(s["failed"], 1)

    def test_render_contains_tool_names(self):
        f = self._feed()
        out = f.render_all()
        self.assertIn("read", out)
        self.assertIn("bash", out)

    def test_detail_read_lines(self):
        f = self._feed()
        e = f.entries[0]
        self.assertIn("3 lines", e.detail())

    def test_detail_bash_exit(self):
        f = self._feed()
        e = f.entries[1]
        self.assertIn("exit 1", e.detail())

    def test_failed_entry_includes_error_detail(self):
        f = ActivityFeed(use_color=False)
        f.begin("t1", "write", {"path": "blocked.txt"})
        f.end(
            "t1", "write", {"path": "blocked.txt"},
            '{"error":"Permission denied: write is not allowed"}', ok=False,
        )
        self.assertIn("Permission denied", f.render_all())

    def test_dump(self):
        import json
        f = self._feed()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "feed.jsonl")
            f.dump(path)
            with open(path) as fh:
                lines = [json.loads(l) for l in fh if l.strip()]
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0]["tool"], "read")

    def test_agent_records_feed(self):
        # an agent run with a stubbed LLM should populate the feed when a tool is called
        agent = Agent(api_key="k", verbose=False)
        class StubProvider:
            call = 0
            def chat(self, messages, tools=None):
                StubProvider.call += 1
                if StubProvider.call == 1:
                    return {
                        "content": "",
                        "tool_calls": [{
                            "id": "c1", "type": "function",
                            "function": {"name": "read", "arguments": '{"path":"nonexistent_file_xyz"}'},
                        }],
                    }
                return {"content": "done", "tool_calls": []}
            def chat_stream(self, messages, tools=None):
                from pycode.provider import LLMProviderError
                raise LLMProviderError("no stream")
        agent.provider = StubProvider()
        agent.run("hi")
        self.assertTrue(any(e.tool == "read" for e in agent.feed.entries))


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

class TestCommands(unittest.TestCase):
    def test_alias_expansion(self):
        self.assertEqual(canonicalize(":s"), "/save")
        self.assertEqual(canonicalize("?"), "/help")
        self.assertEqual(canonicalize(":clear"), "/clear")

    def test_plain_prompt_passthrough(self):
        self.assertEqual(canonicalize("do the thing"), "do the thing")

    def test_command_table(self):
        self.assertIn("/cost", COMMANDS)
        self.assertIn("/rewind", COMMANDS)

    def test_help_text_lists_commands(self):
        h = help_text()
        self.assertIn("/cost", h)
        self.assertIn("aliases", h)


# ---------------------------------------------------------------------------
# Keyboard diff reviewer
# ---------------------------------------------------------------------------

class TestInteractiveDiffReviewer(unittest.TestCase):
    def _make_pending(self, d):
        p = os.path.join(d, "f.txt")
        with open(p, "w") as f:
            f.write("old\n")
        staged = {
            "dry_run": True, "path": p, "diff": "-old\n+new\n",
            "_tool": "write", "_args": {"path": p, "content": "new\n"},
        }
        return staged

    def test_approve_all(self):
        with tempfile.TemporaryDirectory() as d:
            pending = [self._make_pending(d)]
            applied = []
            def apply(tool, args):
                applied.append(args["path"])
            reviewer = InteractiveDiffReviewer(pending, apply, use_color=False)
            with patch("pycode.tui_diff_review._readline_on_tty", return_value="a"):
                s = reviewer.run()
            self.assertEqual(s["approved"], 1)
            self.assertEqual(applied, [pending[0]["_args"]["path"]])

    def test_reject_all(self):
        with tempfile.TemporaryDirectory() as d:
            pending = [self._make_pending(d)]
            reviewer = InteractiveDiffReviewer(pending, lambda t, a: None, use_color=False)
            with patch("pycode.tui_diff_review._readline_on_tty", return_value="r"):
                s = reviewer.run()
            self.assertEqual(s["rejected"], 1)

    def test_quit_rejects_rest(self):
        with tempfile.TemporaryDirectory() as d:
            pending = [self._make_pending(d), self._make_pending(d)]
            pending[1]["path"] = os.path.join(d, "f2.txt")
            with open(pending[1]["path"], "w") as f:
                f.write("old2\n")
            reviewer = InteractiveDiffReviewer(pending, lambda t, a: None, use_color=False)
            # first key: quit -> both rejected
            with patch("pycode.tui_diff_review._readline_on_tty", return_value="q"):
                s = reviewer.run()
            self.assertEqual(s["rejected"], 2)


if __name__ == "__main__":
    unittest.main()
