"""Tests for pycode v0.5: permissions, subagents, and rewind/branching."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from pycode.permissions import (
    PermissionGuard, PermissionDecision, make_permission_confirm,
    _parse_toml_list, _parse_toml_str, _parse_toml_bool,
)
from pycode.rewind import RewindManager, Checkpoint
from pycode.subagents import Subagent, SubagentRegistry, task_tool_schema
from pycode.agent import Agent
from pycode.provider import LLMProviderError


# ---------------------------------------------------------------------------
# Permission guard
# ---------------------------------------------------------------------------

class TestPermissionGuard(unittest.TestCase):
    def test_default_blocks_catastrophic_bash(self):
        g = PermissionGuard()
        for cmd in ["rm -rf /", "shutdown", "mkfs /dev/sda"]:
            d = g.check("bash", {"command": cmd})
            self.assertFalse(d.allowed, f"{cmd} should be blocked")

    def test_safe_bash_allowed(self):
        g = PermissionGuard()
        self.assertTrue(g.check("bash", {"command": "ls -la"}).allowed)
        self.assertTrue(g.check("read", {"path": "x.py"}).allowed)

    def test_write_requires_confirm(self):
        g = PermissionGuard()
        d = g.check("write", {"path": "src/a.py"})
        self.assertTrue(d.allowed)
        self.assertTrue(d.requires_confirm)

    def test_bash_mutating_requires_confirm(self):
        g = PermissionGuard()
        d = g.check("bash", {"command": "echo hi > out.txt"})
        self.assertTrue(d.allowed)
        self.assertTrue(d.requires_confirm)

    def test_tool_allowlist(self):
        g = PermissionGuard(allow_tools=["read", "glob"])
        self.assertTrue(g.check("read", {}).allowed)
        self.assertFalse(g.check("write", {"path": "x"}).allowed)

    def test_write_root_restriction(self):
        g = PermissionGuard(write_root="src/")
        d_in = g.check("write", {"path": "src/a.py"})
        d_out = g.check("write", {"path": "lib/b.py"})
        self.assertTrue(d_in.allowed)
        self.assertFalse(d_out.allowed)
        self.assertIn("outside", d_out.reason)

    def test_yolo_bypasses_but_keeps_blocklist(self):
        g = PermissionGuard(yolo=True)
        # safe op is auto-approved
        self.assertTrue(g.check("write", {"path": "x"}).allowed)
        self.assertFalse(g.check("write", {"path": "x"}).requires_confirm)
        # catastrophic bash is still blocked
        self.assertFalse(g.check("bash", {"command": "rm -rf /"}).allowed)

    def test_toml_parsing(self):
        text = '''
allow = ["read", "bash", "write"]
bash_blocklist = ["rm -rf /", "shutdown"]
write_root = "src/"
yolo = true
'''
        self.assertEqual(_parse_toml_list(text, "allow"), ["read", "bash", "write"])
        self.assertEqual(_parse_toml_list(text, "bash_blocklist"), ["rm -rf /", "shutdown"])
        self.assertEqual(_parse_toml_str(text, "write_root"), "src/")
        self.assertTrue(_parse_toml_bool(text, "yolo"))

    def test_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            f.write('allow = ["read"]\nyolo = false\n')
            path = f.name
        try:
            g = PermissionGuard.from_file(path)
            self.assertEqual(g.allow_tools, ["read"])
            self.assertFalse(g.yolo)
        finally:
            os.unlink(path)

    def test_make_permission_confirm_declines_denied(self):
        g = PermissionGuard(allow_tools=["read"])
        confirm = make_permission_confirm(g, prompt=lambda *a: True)
        self.assertFalse(confirm("write", {"path": "x"}))
        self.assertTrue(confirm("read", {}))


class TestAgentPermissionEnforcement(unittest.TestCase):
    def test_denied_write_is_not_dispatched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "blocked.txt")
            agent = Agent(api_key="k", verbose=False, project_root=directory, auto_approve=True)
            agent._permissions = PermissionGuard(allow_tools=["read"])

            class StubProvider:
                def __init__(self):
                    self.calls = 0

                def chat_stream(self, messages, tools=None):
                    self.calls += 1
                    if self.calls == 1:
                        return {
                            "content": "",
                            "tool_calls": [{
                                "id": "call_1",
                                "function": {
                                    "name": "write",
                                    "arguments": json.dumps({"path": path, "content": "blocked"}),
                                },
                            }],
                        }
                    return {"content": "done", "tool_calls": []}

            agent.provider = StubProvider()
            self.assertEqual(agent.run("try a write"), "done")
            self.assertFalse(os.path.exists(path))
            self.assertIn("Permission denied", agent.messages[-2]["content"])

    def test_task_is_not_available_by_default(self):
        agent = Agent(api_key="k", verbose=False)
        names = {schema["function"]["name"] for schema in agent._all_tool_schemas()}
        self.assertNotIn("task", names)
        agent.enable_subagents = True
        names = {schema["function"]["name"] for schema in agent._all_tool_schemas()}
        self.assertIn("task", names)


# ---------------------------------------------------------------------------
# Rewind / branching
# ---------------------------------------------------------------------------

def _msgs(n: int):
    base = [{"role": "system", "content": "sys"}]
    for i in range(n):
        base.append({"role": "user", "content": f"q{i}"})
        base.append({"role": "assistant", "content": f"a{i}"})
    return base


class TestRewind(unittest.TestCase):
    def test_snapshot_and_rewind(self):
        mgr = RewindManager()
        msgs = _msgs(3)
        cp = mgr.snapshot(msgs, label="after 3 turns")
        self.assertEqual(cp.index, 0)
        # rewind restores the exact snapshot
        restored = mgr.rewind_to(0, msgs)
        self.assertEqual(len(restored), len(msgs))
        self.assertEqual(restored[1], msgs[1])

    def test_rewind_keeps_deepcopy(self):
        mgr = RewindManager()
        msgs = _msgs(2)
        cp = mgr.snapshot(msgs)
        # mutate the original AFTER snapshot
        msgs.append({"role": "user", "content": "later"})
        restored = mgr.rewind_to(0, msgs)
        self.assertNotIn("later", [m.get("content") for m in restored])

    def test_branch_from(self):
        mgr = RewindManager()
        msgs = _msgs(2)
        mgr.snapshot(msgs, "cp0")
        # later messages
        later = [{"role": "user", "content": "more"}]
        branch = mgr.branch(0, later)
        # branch = original 2-turn msgs + 1 new user msg
        self.assertEqual(branch[-1], later[0])
        # a new checkpoint was recorded
        self.assertEqual(len(mgr.checkpoints), 2)

    def test_list_and_latest(self):
        mgr = RewindManager()
        msgs = _msgs(1)
        mgr.snapshot(msgs, "start")
        self.assertEqual(len(mgr.list()), 1)
        self.assertIn("start", mgr.list()[0])
        self.assertIsNotNone(mgr.latest())

    def test_save_load_branches(self):
        with tempfile.TemporaryDirectory() as d:
            mgr = RewindManager()
            mgr.snapshot(_msgs(1), "one")
            path = os.path.join(d, "branches.jsonl")
            n = mgr.save_branches(path)
            self.assertEqual(n, 1)
            loaded = RewindManager.load_from_file(path)
            self.assertEqual(len(loaded.checkpoints), 1)
            self.assertEqual(loaded.checkpoints[0].label, "one")


class TestAgentRewind(unittest.TestCase):
    def test_agent_checkpoint_rewind(self):
        agent = Agent(api_key="k")
        agent.messages.append({"role": "user", "content": "hi"})
        idx = agent.checkpoint(label="greeting")
        self.assertEqual(idx, 0)
        agent.messages.append({"role": "assistant", "content": "hello"})
        agent.messages.append({"role": "user", "content": "again"})
        self.assertEqual(len(agent.messages), 4)
        # rewind to checkpoint 0 (was 2 messages: system + user)
        count = agent.rewind(0)
        self.assertEqual(count, 2)

    def test_agent_branch_from(self):
        agent = Agent(api_key="k")
        agent.messages.append({"role": "user", "content": "first"})
        agent.checkpoint()
        agent.messages.append({"role": "assistant", "content": "ok"})
        count = agent.branch_from(0, "second instruction")
        # system + first user + second instruction
        self.assertGreaterEqual(count, 3)
        self.assertEqual(agent.messages[-1], {"role": "user", "content": "second instruction"})


# ---------------------------------------------------------------------------
# Subagents
# ---------------------------------------------------------------------------

class TestSubagents(unittest.TestCase):
    def test_task_tool_schema(self):
        s = task_tool_schema()
        self.assertEqual(s["type"], "function")
        self.assertEqual(s["function"]["name"], "task")
        self.assertIn("task", s["function"]["parameters"]["required"])

    def test_subagent_shares_provider_config(self):
        parent = Agent(api_key="k", api_base="http://x", model="m1")
        reg = SubagentRegistry(parent)
        sa = reg.spawn("worker", "do the thing")
        # child uses the parent's provider model + base
        self.assertEqual(sa.agent.provider.api_key, "k")
        self.assertEqual(sa.agent.provider.model, "m1")
        # child runs auto-approve (unattended)
        self.assertFalse(sa.agent.auto_approve)
        self.assertEqual(sa.agent.allowed_tools, {"read", "glob", "grep", "symbols"})

    def test_subagent_result_returns_summary(self):
        parent = Agent(api_key="k")
        reg = SubagentRegistry(parent)
        # Stub the child's LLM so run() returns a known summary
        class StubProvider:
            def chat(self, messages, tools=None):
                return {"content": "I did the task", "tool_calls": []}
            def chat_stream(self, messages, tools=None):
                raise LLMProviderError("no stream")
        sa = reg.spawn("worker", "do the thing")
        sa.agent.provider = StubProvider()
        sa.verbose = False
        out = sa.run()
        self.assertIn("I did the task", out)

    def test_run_task_wraps_result(self):
        parent = Agent(api_key="k")
        reg = SubagentRegistry(parent)
        sa = reg.spawn("task-runner", "summarize")
        class StubProvider:
            def chat(self, messages, tools=None):
                return {"content": "summary body", "tool_calls": []}
            def chat_stream(self, messages, tools=None):
                raise LLMProviderError("no stream")
        sa.agent.provider = StubProvider()
        sa.verbose = False
        sa.result = sa.run()
        wrapped = json.loads(sa.as_tool_result())
        self.assertEqual(wrapped["subagent"], "task-runner")
        self.assertIn("summary body", wrapped["result"])


if __name__ == "__main__":
    unittest.main()
