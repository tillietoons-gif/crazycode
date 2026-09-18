"""Tests for pycode v0.10 (M1 agentic power): background jobs, hooks,
real SSE streaming, and plan mode."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from pycode import provider as provider_mod
from pycode.agent import Agent
from pycode.config import _flatten, minimal_toml
from pycode.hooks import HookRunner, expand_template, hook_context
from pycode.jobs import JobManager
from pycode.provider import LLMProvider, LLMProviderError, accepts_kwarg
from pycode.tools import TOOL_SCHEMAS, TOOLS, dispatch_tool, is_destructive

# ---------------------------------------------------------------------------
# Background jobs
# ---------------------------------------------------------------------------


class TestJobManager(unittest.TestCase):
    def setUp(self):
        # ignore_cleanup_errors: on Windows a just-killed child can hold the
        # log file open for a moment (WinError 32)
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.jobs = JobManager(output_dir=self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _wait_done(self, job_id, timeout=10):
        for _ in range(100):
            st = self.jobs.status(job_id)
            if not st.get("running"):
                return st
            time.sleep(0.05)
        return self.jobs.status(job_id)

    def test_start_and_output(self):
        res = self.jobs.start("echo hello-job")
        self.assertTrue(res["ok"])
        job_id = res["job_id"]
        self._wait_done(job_id)
        out = self.jobs.output(job_id)
        self.assertFalse(out["running"])
        self.assertEqual(out["exit_code"], 0)
        self.assertIn("hello-job", out["output"])

    def test_status_unknown(self):
        self.assertIn("error", self.jobs.status("job-999"))

    def test_list_and_reap(self):
        self.jobs.start("echo a")
        self.jobs.start("echo b")
        self._wait_done("job-1")
        self._wait_done("job-2")
        self.assertEqual(len(self.jobs.list()), 2)
        self.assertEqual(self.jobs.reap(), 2)
        self.assertEqual(self.jobs.list(), [])

    def test_kill_running_job(self):
        res = self.jobs.start("sleep 30")
        job_id = res["job_id"]
        time.sleep(0.2)
        out = self.jobs.kill(job_id)
        self.assertTrue(out.get("ok"))
        st = self.jobs.status(job_id)
        self.assertFalse(st["running"])

    def test_start_invalid_workdir(self):
        res = self.jobs.start("echo x", workdir="/nonexistent-dir-xyz")
        self.assertIn("error", res)


class TestJobTools(unittest.TestCase):
    def test_dispatch_bash_background(self):
        out = json.loads(dispatch_tool("bash_background", {"command": "echo bg-ok"}))
        self.assertTrue(out.get("ok"))
        job_id = out["job_id"]
        for _ in range(100):
            st = json.loads(dispatch_tool("job_output", {"job_id": job_id}))
            if not st.get("running"):
                break
            time.sleep(0.05)
        self.assertEqual(st.get("exit_code"), 0)
        self.assertIn("bg-ok", st.get("output", ""))

    def test_dispatch_job_list(self):
        out = json.loads(dispatch_tool("job_list", {}))
        self.assertIn("jobs", out)

    def test_dispatch_job_kill_unknown(self):
        out = json.loads(dispatch_tool("job_kill", {"job_id": "job-999"}))
        self.assertIn("error", out)

    def test_bash_background_destructive_like_bash(self):
        self.assertTrue(is_destructive("bash_background", {"command": "rm -rf /tmp/x"}))
        self.assertFalse(is_destructive("bash_background", {"command": "echo hi"}))
        self.assertFalse(is_destructive("job_kill", {"job_id": "job-1"}))


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


class TestHooks(unittest.TestCase):
    def test_expand_template(self):
        self.assertEqual(
            expand_template("fmt {path} --ok {ok}", {"path": "a.py", "ok": "true"}),
            "fmt a.py --ok true",
        )

    def test_expand_leaves_shell_braces(self):
        self.assertEqual(expand_template("awk '{print $1}'", {}), "awk '{print $1}'")

    def test_from_config_filters_unknown_events(self):
        runner = HookRunner.from_config(
            {
                "hooks": {
                    "post_tool": "echo done",
                    "bogus_event": "echo nope",
                    "on_turn": ["echo one", "echo two"],
                }
            }
        )
        self.assertEqual(sorted(runner.commands), ["on_turn", "post_tool"])
        self.assertEqual(runner.commands["on_turn"], ["echo one", "echo two"])
        self.assertTrue(runner.enabled)

    def test_from_config_empty(self):
        self.assertFalse(HookRunner.from_config({}).enabled)
        self.assertFalse(HookRunner.from_config(None).enabled)

    def test_emit_runs_command(self):
        with tempfile.TemporaryDirectory() as d:
            marker = os.path.join(d, "hooked.txt")
            runner = HookRunner.from_config(
                {"hooks": {"on_turn": f"echo turned > {os.path.join(d, 'out.txt')}"}}
            )
            results = runner.emit("on_turn", {"goal": "x"})
            self.assertEqual(results[0].returncode, 0)
            with open(os.path.join(d, "out.txt"), encoding="utf-8") as fh:
                self.assertIn("turned", fh.read())

    def test_emit_failure_is_not_fatal(self):
        runner = HookRunner({"post_tool": ["exit 3"]})
        results = runner.emit("post_tool", hook_context("edit", {}))
        self.assertEqual(results[0].returncode, 3)

    def test_emit_unknown_event_noop(self):
        runner = HookRunner({"pre_tool": ["true"]})
        self.assertEqual(runner.emit("nonexistent", {}), [])

    def test_hook_context_fields(self):
        ctx = hook_context("edit", {"path": "f.py", "old_string": "a"}, ok=False)
        self.assertEqual(ctx["tool"], "edit")
        self.assertEqual(ctx["path"], "f.py")
        self.assertEqual(ctx["ok"], "false")
        self.assertIn("old_string", ctx["args_json"])

    def test_describe_lists_commands(self):
        runner = HookRunner({"on_turn": ["echo hi"]})
        self.assertIn("on_turn: echo hi", runner.describe())


class TestConfigHooksSection(unittest.TestCase):
    def test_hooks_section_preserved(self):
        flat = _flatten({"hooks": {"post_tool": "echo x"}, "theme": "mono"})
        self.assertEqual(flat["hooks"], {"post_tool": "echo x"})
        self.assertEqual(flat["theme"], "mono")

    def test_minimal_toml_parses_hooks(self):
        data = minimal_toml('[hooks]\npost_tool = "black {path}"\n')
        self.assertEqual(data["hooks"]["post_tool"], "black {path}")


# ---------------------------------------------------------------------------
# Real SSE streaming
# ---------------------------------------------------------------------------


class _FakeStreamResp:
    def __init__(self, lines, status_code=200):
        self._lines = lines
        self.status_code = status_code
        self.text = ""

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)

    def json(self):
        return {
            "choices": [{"message": {"content": "plain-json", "tool_calls": []}}],
            "usage": {"total_tokens": 5},
        }

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestSSEStreaming(unittest.TestCase):
    def _provider(self):
        return LLMProvider(api_key="k", api_base="http://localhost:9", model="m")

    def test_parses_content_and_usage(self):
        p = self._provider()
        lines = [
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            'data: {"choices":[{"delta":{},"usage":null}]}',
            'data: {"choices":[],"usage":{"total_tokens":42}}',
            "data: [DONE]",
        ]
        got = []
        with unittest.mock.patch.object(
            provider_mod.requests,
            "post",
            return_value=_FakeStreamResp(lines),
        ):
            result = p.chat_stream(
                [{"role": "user", "content": "hi"}],
                on_delta=got.append,
            )
        self.assertEqual(result["content"], "Hello")
        self.assertEqual(result["tool_calls"], [])
        self.assertEqual(result["usage"]["total_tokens"], 42)
        self.assertEqual(got, ["Hel", "lo"])
        self.assertEqual(p.last_usage["total_tokens"], 42)

    def test_assembles_tool_call_deltas(self):
        p = self._provider()
        lines = [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
            '"function":{"name":"read","arguments":"{\\"pa"}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"arguments":"th\\":\\"x\\"}"}}]}}]}',
            "data: [DONE]",
        ]
        with unittest.mock.patch.object(
            provider_mod.requests, "post", return_value=_FakeStreamResp(lines)
        ):
            result = p.chat_stream([{"role": "user", "content": "hi"}])
        tc = result["tool_calls"][0]
        self.assertEqual(tc["id"], "call_1")
        self.assertEqual(tc["function"]["name"], "read")
        self.assertEqual(json.loads(tc["function"]["arguments"]), {"path": "x"})

    def test_empty_stream_falls_back_to_json(self):
        p = self._provider()
        with unittest.mock.patch.object(
            provider_mod.requests, "post", return_value=_FakeStreamResp([])
        ) as post:
            result = p.chat_stream([{"role": "user", "content": "hi"}])
        # fallback used the plain JSON body
        self.assertEqual(result["content"], "plain-json")
        # the fallback request was made without stream=True
        _, kwargs = post.call_args
        self.assertNotIn("stream", kwargs.get("json", {}))

    def test_http_error_raises(self):
        class ErrResp(_FakeStreamResp):
            def __init__(self):
                super().__init__([])
                self.status_code = 401
                self.text = "denied"

        p = self._provider()
        with unittest.mock.patch.object(
            provider_mod.requests, "post", return_value=ErrResp()
        ):
            with self.assertRaises(LLMProviderError):
                p.chat_stream([{"role": "user", "content": "hi"}])

    def test_accepts_kwarg_helper(self):
        def f(a, on_delta=None):
            pass

        def g(a):
            pass

        self.assertTrue(accepts_kwarg(f, "on_delta"))
        self.assertFalse(accepts_kwarg(g, "on_delta"))
        self.assertFalse(accepts_kwarg(lambda: None, "on_delta"))


# ---------------------------------------------------------------------------
# Plan mode
# ---------------------------------------------------------------------------


class _PlanProvider:
    model = "fake"
    api_base = "http://localhost"

    def __init__(self):
        self.chats = 0

    def chat(self, messages, tools=None, **kwargs):
        self.chats += 1
        return (
            "1. Write the module\n"
            "2) Add tests\n"
            "3. Run the suite\n"
            "Not a numbered line"
        )

    def chat_stream(self, messages, tools=None, on_delta=None):
        return {"content": "done", "tool_calls": [], "usage": {}}


class TestPlanMode(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.agent = Agent(project_root=self._tmp.name, verbose=False)
        self.agent.provider = _PlanProvider()

    def tearDown(self):
        self._tmp.cleanup()

    def test_parse_plan(self):
        steps = Agent._parse_plan("1. a\n2) b\n3] c\nnope\n 4. d")
        self.assertEqual(steps, ["a", "b", "c", "d"])

    def test_parse_plan_ignores_non_numbered(self):
        self.assertEqual(Agent._parse_plan("just text\nmore"), [])

    def test_draft_plan(self):
        steps = self.agent.draft_plan("build thing")
        self.assertEqual(steps, ["Write the module", "Add tests", "Run the suite"])

    def test_run_plan_executes_steps(self):
        summary = self.agent.run_plan("build thing")
        self.assertEqual(len(summary["steps"]), 3)
        self.assertEqual(len(summary["results"]), 3)
        self.assertEqual(summary["results"][0]["result"], "done")
        self.assertGreaterEqual(self.agent.provider.chats, 1)

    def test_run_plan_no_steps(self):
        self.agent.provider.chat = lambda messages, tools=None, **kw: "no list here"
        summary = self.agent.run_plan("goal")
        self.assertIn("error", summary)
        self.assertEqual(summary["results"], [])

    def test_run_plan_provider_error(self):
        def boom(messages, tools=None, **kwargs):
            raise RuntimeError("down")

        self.agent.provider.chat = boom
        summary = self.agent.run_plan("goal")
        self.assertIn("plan draft failed", summary["error"])


if __name__ == "__main__":
    unittest.main()
