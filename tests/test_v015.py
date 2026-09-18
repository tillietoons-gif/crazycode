"""Tests for pycode v1.2 UI overhaul: framed input box, turn summary
panel, live tool spinner gating, and agent last_turn tracking."""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from pycode.agent import Agent
from pycode.tui_input import (bottom_border, read_input, supports_box,
                              top_border)
from pycode.tui_turn import (_fmt_stat, _git_numstat, render_summary,
                             summarize_turn)


def _git_repo(root):
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


# ---------------------------------------------------------------------------
# Input box
# ---------------------------------------------------------------------------


class TestInputBox(unittest.TestCase):
    def test_top_border_shape(self):
        line = top_border("you", width=30)
        self.assertTrue(line.startswith("╭─"))
        self.assertTrue(line.endswith("╮"))
        self.assertIn("you", line)
        self.assertEqual(len("╭─ you " + "─" * (30 - 8) + "╮"), 30)

    def test_bottom_border(self):
        line = bottom_border(width=30)
        self.assertTrue(line.startswith("╰"))
        self.assertTrue(line.endswith("╯"))

    def test_read_input_boxed(self):
        outputs = io.StringIO()
        with redirect_stdout(outputs):
            line = read_input(
                input_fn=lambda prompt: (
                    outputs.write("PROMPT:" + prompt),
                    "hello box",
                )[-1],
                use_box=True,
            )
        self.assertEqual(line, "hello box")
        text = outputs.getvalue()
        self.assertIn("╭─", text)
        self.assertIn("❯", text)
        self.assertIn("╰─", text)

    def test_read_input_plain(self):
        outputs = io.StringIO()
        with redirect_stdout(outputs):
            line = read_input(
                input_fn=lambda prompt: (outputs.write("P:" + prompt), "plain")[-1],
                use_box=False,
            )
        self.assertEqual(line, "plain")
        self.assertIn(">>>", outputs.getvalue())
        self.assertNotIn("╭", outputs.getvalue())

    def test_bottom_border_printed_on_eof(self):
        outputs = io.StringIO()

        def raising_input(prompt):
            outputs.write("P:" + prompt)
            raise EOFError

        with redirect_stdout(outputs):
            with self.assertRaises(EOFError):
                read_input(input_fn=raising_input, use_box=True)
        # box was opened AND closed despite the exception
        self.assertIn("╭─", outputs.getvalue())
        self.assertIn("╰─", outputs.getvalue())

    def test_supports_box_non_tty_false(self):
        self.assertFalse(supports_box(io.StringIO()))


# ---------------------------------------------------------------------------
# Turn summary
# ---------------------------------------------------------------------------


class TestTurnSummary(unittest.TestCase):
    def test_fmt_stat(self):
        self.assertEqual(_fmt_stat(3, None), "+3")
        self.assertEqual(_fmt_stat(None, 2), "−2")
        self.assertEqual(_fmt_stat(None, None), "")

    def test_git_numstat(self):
        with tempfile.TemporaryDirectory() as d:
            _git_repo(d)
            path = os.path.join(d, "app.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("def a():\n    pass\n")
            subprocess.run(["git", "add", "."], cwd=d, check=True)
            subprocess.run(["git", "commit", "-qm", "i"], cwd=d, check=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("def b():\n    pass\n\n\n\n")
            stats = _git_numstat([path], d)
            self.assertIn(path, stats)
            self.assertEqual(stats[path]["add"], 5)
            self.assertEqual(stats[path]["del"], 0)

    def test_numstat_failure_is_empty(self):
        self.assertEqual(_git_numstat(["/x"], "/nonexistent-root-xyz"), {})

    def test_summarize_without_git(self):
        with tempfile.TemporaryDirectory() as d:
            s = summarize_turn(3, 2.5, [os.path.join(d, "a.py")], root=d)
            self.assertEqual(s["tool_calls"], 3)
            self.assertEqual(len(s["files"]), 1)
            self.assertIsNone(s["files"][0]["add"])
            self.assertEqual(s["duration_s"], 2.5)
            self.assertFalse(s["reasoning"])

    def test_render_with_files(self):
        summary = {
            "tool_calls": 2,
            "duration_s": 3.4,
            "files": [{"path": "src/app.py", "add": 12, "del": 3}],
            "reasoning": True,
        }
        text = render_summary(summary, width=50)
        self.assertIn("╭", text)
        self.assertIn("╰", text)
        self.assertIn("✎", text)
        self.assertIn("src/app.py", text)
        self.assertIn("+12", text)
        self.assertIn("−3", text)
        self.assertIn("reasoned", text)

    def test_render_headless(self):
        text = render_summary(
            {"tool_calls": 0, "duration_s": 0.0, "files": [], "reasoning": False},
            width=40,
        )
        self.assertIn("╰", text)
        self.assertNotIn("✎", text)


class TestAgentLastTurn(unittest.TestCase):
    def _agent_with_write_then_done(self, root):
        agent = Agent(project_root=root, verbose=False)

        class _P:
            model = "fake"
            api_base = "x"
            calls = 0

            def chat_stream(self, messages, tools=None, **kw):
                _P.calls += 1
                if _P.calls == 1:
                    return {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "1",
                                "type": "function",
                                "function": {
                                    "name": "write",
                                    "arguments": json.dumps(
                                        {
                                            "path": os.path.join(root, "f.txt"),
                                            "content": "hello",
                                        }
                                    ),
                                },
                            }
                        ],
                        "usage": {},
                        "reasoning": "",
                    }
                return {
                    "content": "done",
                    "tool_calls": [],
                    "usage": {},
                    "reasoning": "",
                }

        agent.provider = _P()
        return agent

    def test_last_turn_tracks_tools_and_files(self):
        with tempfile.TemporaryDirectory() as d:
            agent = self._agent_with_write_then_done(d)
            result = agent.run("make a file")
            self.assertEqual(result, "done")
            self.assertEqual(agent.last_turn["tool_calls"], 1)
            self.assertEqual(len(agent.last_turn["files"]), 1)
            self.assertIn("f.txt", agent.last_turn["files"][0]["path"])
            self.assertFalse(agent.last_turn["reasoning"])

    def test_last_turn_reasoning_flag(self):
        with tempfile.TemporaryDirectory() as d:
            agent = self._agent_with_write_then_done(d)

            def stream(messages, tools=None, **kw):
                agent.provider.calls += 1
                if agent.provider.calls == 1:
                    return {
                        "content": "",
                        "tool_calls": [],
                        "usage": {},
                        "reasoning": "pondering",
                    }
                return {"content": "ok", "tool_calls": [], "usage": {}, "reasoning": ""}

            agent.provider.chat_stream = stream
            agent.run("hi")
            self.assertTrue(agent.last_turn["reasoning"])

    def test_last_turn_on_llm_error(self):
        with tempfile.TemporaryDirectory() as d:
            agent = Agent(project_root=d, verbose=False)

            class _P:
                model = "fake"
                api_base = "x"

                def chat_stream(self, messages, tools=None, **kw):
                    raise RuntimeError("boom")

            agent.provider = _P()
            result = agent.run("hi")
            self.assertIn("[LLM error", result)
            self.assertEqual(agent.last_turn["tool_calls"], 0)


if __name__ == "__main__":
    unittest.main()
