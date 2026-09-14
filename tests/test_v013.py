"""Tests for pycode v0.12 (M4 model quality): reasoning/thinking models,
self-review pass, and the auto-fix loop."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from pycode import provider as provider_mod
from pycode.agent import Agent
from pycode.config import _flatten
from pycode.provider import LLMProvider

SSE_LINES = [
    'data: {"choices":[{"delta":{"reasoning_content":"Let me think."}}]}',
    'data: {"choices":[{"delta":{"reasoning":" more."}}]}',
    'data: {"choices":[{"delta":{"content":"Answer"}}]}',
    "data: [DONE]",
]

NONSTREAM_JSON = {
    "choices": [{"message": {"content": "ok", "reasoning_content": "because reasons",
                             "tool_calls": []}}],
    "usage": {"total_tokens": 9},
}


class _FakeStreamResp:
    def __init__(self, lines, status_code=200):
        self._lines = lines
        self.status_code = status_code
        self.text = ""

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)

    def json(self):
        return NONSTREAM_JSON

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---------------------------------------------------------------------------
# Reasoning / thinking models
# ---------------------------------------------------------------------------

class TestReasoningStreaming(unittest.TestCase):
    def test_consume_sse_collects_reasoning(self):
        p = LLMProvider(api_key="k", api_base="http://x", model="m")
        got = []
        with unittest.mock.patch.object(provider_mod.requests, "post",
                                        return_value=_FakeStreamResp(SSE_LINES)):
            result = p.chat_stream([{"role": "user", "content": "q"}],
                                   on_reasoning=got.append)
        self.assertEqual(result["reasoning"], "Let me think. more.")
        self.assertEqual(result["content"], "Answer")
        self.assertEqual(got, ["Let me think.", " more."])

    def test_nonstream_reasoning_field(self):
        p = LLMProvider(api_key="k", api_base="http://x", model="m")

        class Resp:
            status_code = 200
            text = ""

            def json(self):
                return NONSTREAM_JSON

        with unittest.mock.patch.object(provider_mod.requests, "post",
                                        return_value=Resp()):
            result = p.chat_stream([{"role": "user", "content": "q"}])
        # empty SSE -> falls back to the plain JSON body, which carries reasoning
        self.assertEqual(result["reasoning"], "because reasons")

    def test_reasoning_key_always_present(self):
        p = LLMProvider(api_key="k", api_base="http://x", model="m")

        class Resp:
            status_code = 200
            text = ""

            def json(self):
                return {"choices": [{"message": {"content": "plain"}}],
                        "usage": {}}

        with unittest.mock.patch.object(provider_mod.requests, "post",
                                        return_value=Resp()):
            result = p.chat_stream([{"role": "user", "content": "q"}])
        self.assertEqual(result["reasoning"], "")


# ---------------------------------------------------------------------------
# Self-review pass
# ---------------------------------------------------------------------------

def _git_repo(root):
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


class TestSelfReview(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        _git_repo(self.root)
        # a committed file so git diff is non-empty after edit
        with open(os.path.join(self.root, "app.py"), "w", encoding="utf-8") as fh:
            fh.write("def main():\n    return 1\n")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.root, check=True)

        self.agent = Agent(project_root=self.root, verbose=False, self_review=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_touched_paths_no_review(self):
        self.agent.provider = self.agent.provider  # unchanged
        note = self.agent._self_review_pass()
        self.assertEqual(note, "")

    def test_lgtm_note_appended(self):
        from pycode.agent import Agent

        class _P:
            model = "fake"
            api_base = "x"

            def chat(self, messages, tools=None, **kw):
                return "LGTM minor nit about naming"

            def chat_stream(self, messages, tools=None, **kw):
                return {"content": "edited", "tool_calls": [], "usage": {}}

        agent = Agent(project_root=self.root, verbose=False, self_review=True)
        agent.provider = _P()
        agent._touched = [os.path.join(self.root, "app.py")]
        agent._last_response = "edited the file"
        # dirty the working tree so git diff is non-empty
        with open(os.path.join(self.root, "app.py"), "a", encoding="utf-8") as fh:
            fh.write("\n# change\n")
        note = agent._self_review_pass()
        self.assertIn("[self-review]", note)
        self.assertIn("LGTM", note)

    def test_revision_requested_runs_one_fix_turn(self):
        calls = {"runs": 0}

        class _P:
            model = "fake"
            api_base = "x"

            def chat(self, messages, tools=None, **kw):
                return "REQUEST REVISION: handle the empty-input case"

            def chat_stream(self, messages, tools=None, **kw):
                # the corrective turn should also produce no tool calls
                calls["runs"] += 1
                return {"content": "fixed", "tool_calls": [], "usage": {}}

        agent = Agent(project_root=self.root, verbose=False, self_review=True)
        agent.provider = _P()
        agent._touched = [os.path.join(self.root, "app.py")]
        agent._last_response = "edited"
        with open(os.path.join(self.root, "app.py"), "a", encoding="utf-8") as fh:
            fh.write("\n# change\n")
        note = agent._self_review_pass()
        self.assertIn("revision requested", note)
        self.assertEqual(calls["runs"], 1)
        self.assertTrue(agent.self_review)  # restored after the revision

    def test_reviewer_unavailable_note(self):
        class _P:
            model = "fake"
            api_base = "x"

            def chat(self, messages, tools=None, **kw):
                raise RuntimeError("down")

            def chat_stream(self, messages, tools=None, **kw):
                return {"content": "", "tool_calls": [], "usage": {}}

        agent = Agent(project_root=self.root, verbose=False, self_review=True)
        agent.provider = _P()
        agent._touched = [os.path.join(self.root, "app.py")]
        agent._last_response = "edited"
        with open(os.path.join(self.root, "app.py"), "a", encoding="utf-8") as fh:
            fh.write("\n# change\n")
        note = agent._self_review_pass()
        self.assertIn("reviewer unavailable", note)

    def test_clean_diff_skips_review(self):
        # no working-tree change -> empty diff -> no reviewer call
        class _P:
            model = "fake"
            api_base = "x"
            called = 0

            def chat(self, messages, tools=None, **kw):
                _P.called += 1
                return "LGTM"

            def chat_stream(self, messages, tools=None, **kw):
                return {"content": "", "tool_calls": [], "usage": {}}

        agent = Agent(project_root=self.root, verbose=False, self_review=True)
        agent.provider = _P()
        agent._touched = [os.path.join(self.root, "app.py")]
        note = agent._self_review_pass()
        self.assertEqual(note, "")
        self.assertEqual(_P.called, 0)


# ---------------------------------------------------------------------------
# Auto-fix loop
# ---------------------------------------------------------------------------

class _LoopProvider:
    model = "fake"
    api_base = "http://localhost"

    def __init__(self, script):
        self.script = list(script)
        self.turns = []

    def chat(self, messages, tools=None, **kw):
        raise AssertionError("plan not used in loop tests")

    def chat_stream(self, messages, tools=None, **kw):
        self.turns.append(messages[-1]["content"][:60])
        reply = self.script.pop(0) if self.script else "still working"
        return {"content": reply, "tool_calls": [], "usage": {}}


class TestAutoFixLoop(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.agent = Agent(project_root=self._tmp.name, verbose=False)

    def tearDown(self):
        self._tmp.cleanup()

    def _with(self, script):
        self.agent.provider = _LoopProvider(script)
        return self.agent.provider

    def test_loop_achieves_on_second_turn(self):
        self._with(["partial progress", "all good GOAL_ACHIEVED"])
        summary = self.agent.run_loop("make it work", max_iterations=5)
        self.assertTrue(summary["achieved"])
        self.assertEqual(summary["attempts"], 2)

    def test_loop_hits_iteration_cap(self):
        self._with(["try 1", "try 2", "try 3"])
        summary = self.agent.run_loop("goal", max_iterations=3)
        self.assertFalse(summary["achieved"])
        self.assertEqual(summary["attempts"], 3)

    def test_loop_breaks_on_llm_error(self):
        self._with(["[LLM error: boom]", "never reached"])
        summary = self.agent.run_loop("goal", max_iterations=3)
        self.assertEqual(summary["attempts"], 1)

    def test_loop_continuation_prompt_contains_goal(self):
        prov = self._with(["progress", "GOAL_ACHIEVED"])
        self.agent.run_loop("make tests pass", max_iterations=2)
        # second turn's last user message must reference the goal + previous result
        last_msg = self.agent.messages[-1]["content"]
        # after the achieved turn, nothing new appended; check the turn before
        user_msgs = [m for m in self.agent.messages if m["role"] == "user"]
        self.assertIn("make tests pass", user_msgs[-1]["content"])
        self.assertIn("attempt 2 of 2", user_msgs[-1]["content"])

    def test_loop_respects_max_iterations_arg(self):
        self._with(["a"] * 10)
        summary = self.agent.run_loop("g", max_iterations=2)
        self.assertEqual(summary["attempts"], 2)


# ---------------------------------------------------------------------------
# Config plumbing
# ---------------------------------------------------------------------------

class TestConfigM4Keys(unittest.TestCase):
    def test_self_review_and_loop_max_pass_through(self):
        flat = _flatten({"self_review": True, "loop_max": 3})
        self.assertIs(flat["self_review"], True)
        self.assertEqual(flat["loop_max"], 3)


if __name__ == "__main__":
    unittest.main()
