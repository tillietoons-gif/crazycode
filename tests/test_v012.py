"""Tests for pycode v0.11.5 (M3 extensibility): user Python tools,
custom slash commands, and tool-file hooks."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from pycode import plugins
from pycode import tools as tools_mod
from pycode.hooks import HookRunner
from pycode.plugins import (apply_command, collect_user_hooks,
                            load_user_commands, load_user_tools, merge_hooks,
                            register_user_tools)
from pycode.tui_commands import USER_COMMANDS, _complete

ECHO_TOOL = """
SCHEMA = {
    "type": "function",
    "function": {
        "name": "echo_it",
        "description": "Echo a message",
        "parameters": {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
    },
}

def run(args):
    return {"ok": True, "echo": args.get("msg", "")}
"""

DESTRUCTIVE_TOOL = """
SCHEMA = {
    "type": "function",
    "function": {
        "name": "wipe_dir",
        "description": "Wipe a directory",
        "parameters": {"type": "object", "properties": {}},
    },
}
DESTRUCTIVE = True

def run(args):
    return {"ok": True}
"""

HOOKED_TOOL = """
SCHEMA = {
    "type": "function",
    "function": {"name": "hooked", "description": "x", "parameters": {"type": "object"}},
}
HOOKS = {"post_tool": ["echo post"], "bogus": ["nope"], "on_turn": ["echo turn"]}

def run(args):
    return {"ok": True}
"""

BROKEN_TOOL = 'raise RuntimeError("boom at import")\n'

BASH_COLLISION = """
SCHEMA = {"type": "function", "function": {"name": "bash", "description": "x", "parameters": {"type": "object"}}}
def run(args):
    return {"ok": True}
"""

MISSING_RUN = """
SCHEMA = {"type": "function", "function": {"name": "ghost", "description": "x", "parameters": {"type": "object"}}}
"""


class PluginSandbox(unittest.TestCase):
    """Base: temp project with .pycode/ helpers."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.tools_dir = os.path.join(self.root, ".pycode", "tools")
        self.cmds_dir = os.path.join(self.root, ".pycode", "commands")
        os.makedirs(self.tools_dir)
        os.makedirs(self.cmds_dir)

    def tearDown(self):
        self._tmp.cleanup()
        plugins.USER_DESTRUCTIVE.clear()
        plugins.LOADED_TOOLS[:] = []
        USER_COMMANDS.clear()

    def write_tool(self, filename: str, content: str) -> str:
        path = os.path.join(self.tools_dir, filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    def write_command(self, name: str, body: str) -> str:
        path = os.path.join(self.cmds_dir, f"{name}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path


class TestUserTools(PluginSandbox):
    def test_load_valid_tool(self):
        self.write_tool("echo_it.py", ECHO_TOOL)
        entries = load_user_tools(self.root)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "echo_it")
        self.assertEqual(entries[0]["run"]({"msg": "hi"}), {"ok": True, "echo": "hi"})

    def test_register_and_dispatch(self):
        self.write_tool("echo_it.py", ECHO_TOOL)
        summary = register_user_tools(self.root)
        self.assertEqual(summary["loaded"], ["echo_it"])
        self.assertEqual(summary["errors"], [])
        out = json.loads(tools_mod.dispatch_tool("echo_it", {"msg": "hi"}))
        self.assertEqual(out["echo"], "hi")
        # schema was appended for LLM discovery
        names = {s["function"]["name"] for s in tools_mod.TOOL_SCHEMAS}
        self.assertIn("echo_it", names)

    def test_name_collision_rejected(self):
        self.write_tool("bash.py", BASH_COLLISION)
        summary = register_user_tools(self.root)
        self.assertEqual(summary["loaded"], [])
        self.assertEqual(len(summary["errors"]), 1)
        self.assertIn("collides", summary["errors"][0]["error"])
        # the built-in bash is untouched
        self.assertIn(
            "shell", tools_mod.TOOL_SCHEMAS[0]["function"]["description"].lower()
        )

    def test_broken_plugin_reported(self):
        self.write_tool("broken.py", BROKEN_TOOL)
        summary = register_user_tools(self.root)
        self.assertEqual(summary["loaded"], [])
        self.assertEqual(len(summary["errors"]), 1)
        self.assertIn("boom", summary["errors"][0]["error"])

    def test_missing_run_reported(self):
        self.write_tool("ghost.py", MISSING_RUN)
        summary = register_user_tools(self.root)
        self.assertEqual(summary["loaded"], [])
        self.assertIn("run(args)", summary["errors"][0]["error"])

    def test_destructive_flag(self):
        self.write_tool("wipe_dir.py", DESTRUCTIVE_TOOL)
        register_user_tools(self.root)
        self.assertTrue(tools_mod.is_destructive("wipe_dir", {}))
        self.assertFalse(tools_mod.is_destructive("echo_it", {}))

    def test_empty_dir_ok(self):
        self.assertEqual(register_user_tools(self.root), {"loaded": [], "errors": []})

    def test_missing_dir_ok(self):
        summary = register_user_tools(os.path.join(self.root, "nothing"))
        self.assertEqual(summary, {"loaded": [], "errors": []})


class TestUserCommands(PluginSandbox):
    def test_load_commands(self):
        self.write_command("review", "Please review $ARGS carefully.")
        self.write_command("empty", "")
        cmds = load_user_commands(self.root)
        self.assertIn("review", cmds)
        self.assertNotIn("empty", cmds)
        self.assertEqual(cmds["review"]["template"], "Please review $ARGS carefully.")

    def test_apply_command(self):
        self.assertEqual(
            apply_command("do $ARGS twice, yes $ARGS", "x"), "do x twice, yes x"
        )
        self.assertEqual(apply_command("braced ${ARGS}", "y"), "braced y")
        self.assertEqual(apply_command("no placeholder", "z"), "no placeholder")

    def test_apply_keeps_shell_vars(self):
        # other $ vars are untouched
        self.assertEqual(
            apply_command("export PATH=$PATH and $ARGS", "here"),
            "export PATH=$PATH and here",
        )

    def test_completion_includes_user_commands(self):
        USER_COMMANDS.add("/review")
        cands = _complete("/rev")
        self.assertIn("/review", cands)


class TestToolFileHooks(PluginSandbox):
    def test_collect_hooks_from_tool_files(self):
        self.write_tool("hooked.py", HOOKED_TOOL)
        hooks = collect_user_hooks(self.root)
        self.assertEqual(hooks.get("post_tool"), ["echo post"])
        self.assertEqual(hooks.get("on_turn"), ["echo turn"])
        self.assertNotIn("bogus", hooks)

    def test_merge_hooks(self):
        base = {"post_tool": ["a"]}
        merged = merge_hooks(base, {"post_tool": ["a", "b"], "pre_tool": ["c"]})
        self.assertEqual(merged["post_tool"], ["a", "b"])
        self.assertEqual(merged["pre_tool"], ["c"])
        # base not mutated
        self.assertEqual(base["post_tool"], ["a"])

    def test_runner_from_merged(self):
        self.write_tool("hooked.py", HOOKED_TOOL)
        hooks = collect_user_hooks(self.root)
        runner = HookRunner(merge_hooks({}, hooks))
        results = runner.emit("on_turn", {})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].returncode, 0)

    def test_merge_hooks_empty(self):
        self.assertEqual(merge_hooks({}, {}), {})


if __name__ == "__main__":
    unittest.main()
