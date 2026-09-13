"""End-to-end tests for pycode tools and agent loop."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

from pycode.tools import TOOLS, TOOL_SCHEMAS, dispatch_tool
from pycode.agent import Agent
from pycode.provider import LLMProvider


class TestTools(unittest.TestCase):
    def test_bash(self):
        result = json.loads(dispatch_tool("bash", {"command": "echo hello"}))
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("hello", result["stdout"])

    def test_read_write(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("line1\nline2\nline3\n")
            path = f.name
        try:
            content = dispatch_tool("read", {"path": path, "limit": 2})
            self.assertIn("line1", content)
            self.assertIn("line2", content)
            self.assertNotIn("line3", content)

            result = json.loads(dispatch_tool("write", {"path": path, "content": "rewritten"}))
            self.assertTrue(result["ok"])
            self.assertEqual(open(path).read(), "rewritten")
        finally:
            os.unlink(path)

    def test_edit(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def foo():\n    return 1\n")
            path = f.name
        try:
            result = json.loads(dispatch_tool("edit", {
                "path": path,
                "old_string": "return 1",
                "new_string": "return 2",
            }))
            self.assertTrue(result["ok"])
            self.assertIn("return 2", open(path).read())
        finally:
            os.unlink(path)

    def test_glob(self):
        results = dispatch_tool("glob", {"pattern": "src/**/*.py", "path": "/workspace"})
        self.assertIn("/workspace/src/pycode/agent.py", json.loads(results))

    def test_grep(self):
        results = json.loads(dispatch_tool("grep", {
            "pattern": "import",
            "path": "/workspace/src",
            "include": "*.py",
            "max_results": 5,
        }))
        self.assertGreater(len(results), 0)
        self.assertIn("import", results[0]["text"])

    def test_todo(self):
        dispatch_tool("todo", {"clear": True})
        result = json.loads(dispatch_tool("todo", {
            "add": [{"content": "task1", "status": "pending", "priority": "high"}]
        }))
        self.assertEqual(len(result["todos"]), 1)


class TestProvider(unittest.TestCase):
    def test_init_defaults(self):
        p = LLMProvider(api_key="test-key")
        self.assertEqual(p.api_key, "test-key")
        self.assertEqual(p.model, "gpt-4o")
        self.assertIsInstance(p.max_tokens, int)

    def test_init_custom(self):
        p = LLMProvider(api_key="k", api_base="http://localhost:8080", model="test-model")
        self.assertEqual(p.api_base, "http://localhost:8080")
        self.assertEqual(p.model, "test-model")


class TestAgent(unittest.TestCase):
    def test_load_env_config(self):
        os.environ["PYCODE_API_KEY"] = "test-key-123"
        os.environ["PYCODE_MODEL"] = "test-model"
        try:
            cfg = Agent.load_env_config()
            self.assertEqual(cfg.get("api_key"), "test-key-123")
            self.assertEqual(cfg.get("model"), "test-model")
        finally:
            del os.environ["PYCODE_API_KEY"]
            del os.environ["PYCODE_MODEL"]

    def test_system_prompt_contains_tools(self):
        agent = Agent(api_key="dummy")
        self.assertIn("pycode", agent.messages[0]["content"])
        self.assertIn("bash", agent.messages[0]["content"])


class TestSchemas(unittest.TestCase):
    def test_schema_count(self):
        self.assertEqual(len(TOOL_SCHEMAS), 8)

    def test_schema_names(self):
        names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        self.assertEqual(names, {"bash", "read", "write", "edit", "glob", "grep", "webfetch", "todo"})

    def test_all_tools_registered(self):
        for s in TOOL_SCHEMAS:
            name = s["function"]["name"]
            self.assertIn(name, TOOLS)


if __name__ == "__main__":
    unittest.main()
