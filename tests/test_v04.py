"""Tests for pycode v0.4: diff reviewer and scaffold (context file generator)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pycode.diff_reviewer import (DECISION_APPROVE, DECISION_REJECT,
                                  DiffReviewer, render_diff)
from pycode.scaffold import _detect_project, exists, generate, render_template
from pycode.tools import dispatch_tool


class TestDiffReviewer(unittest.TestCase):
    def _change(self, d: str, fname: str = "f.txt", old: str = "a\n", new: str = "b\n"):
        p = os.path.join(d, fname)
        with open(p, "w") as f:
            f.write(old)
        staged = json.loads(
            dispatch_tool("write", {"path": p, "content": new}, dry_run=True)
        )
        staged["_tool"] = "write"
        staged["_args"] = {"path": p, "content": new}
        return staged

    def test_auto_approve_applies(self):
        with tempfile.TemporaryDirectory() as d:
            change = self._change(d)
            r = DiffReviewer(auto_approve=True)
            r.stage(change)
            s = r.review()
            self.assertEqual(s["approved"], 1)
            self.assertEqual(s["rejected"], 0)
            with open(change["_args"]["path"]) as fh:
                self.assertEqual(fh.read(), "b\n")

    def test_reject_keeps_original(self):
        with tempfile.TemporaryDirectory() as d:
            change = self._change(d)
            r = DiffReviewer()
            r.stage(change)
            with patch(
                "pycode.diff_reviewer._prompt_decision", return_value=DECISION_REJECT
            ):
                s = r.review()
            self.assertEqual(s["rejected"], 1)
            self.assertEqual(s["approved"], 0)
            with open(change["_args"]["path"]) as fh:
                self.assertEqual(fh.read(), "a\n")

    def test_approve_applies(self):
        with tempfile.TemporaryDirectory() as d:
            change = self._change(d)
            r = DiffReviewer()
            r.stage(change)
            with patch(
                "pycode.diff_reviewer._prompt_decision", return_value=DECISION_APPROVE
            ):
                s = r.review()
            self.assertEqual(s["approved"], 1)
            with open(change["_args"]["path"]) as fh:
                self.assertEqual(fh.read(), "b\n")

    def test_render_diff_no_changes(self):
        self.assertIn("no changes", render_diff("(no changes)"))

    def test_render_diff_colorizes(self):
        rendered = render_diff("+added\n-removed", use_color=False)
        self.assertIn("added", rendered)
        self.assertIn("removed", rendered)


class TestScaffold(unittest.TestCase):
    def test_detect_python_project(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "pyproject.toml").write_text("[project]\nname = 'x'\n")
            Path(d, "pytest.ini").write_text("[pytest]\n")
            info = _detect_project(Path(d))
            self.assertIn("Python", info["language"])
            self.assertIn("pytest", info["test_runner"])

    def test_detect_node_project(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "package.json").write_text("{}")
            Path(d, "pnpm-lock.yaml").write_text("")
            info = _detect_project(Path(d))
            self.assertIn("Node", info["language"])
            self.assertIn("pnpm", info["package_manager"])

    def test_render_template(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "go.mod").write_text("module x\n")
            out = render_template(root=Path(d))
            self.assertIn("Go", out)

    def test_generate_creates_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = generate(name="CLAUDE.md", root=d)
            self.assertTrue(os.path.exists(path))
            self.assertTrue(exists(d))

    def test_generate_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            generate(name="CLAUDE.md", root=d)
            with self.assertRaises(FileExistsError):
                generate(name="CLAUDE.md", root=d, overwrite=False)

    def test_generate_force_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            generate(name="AGENTS.md", root=d)
            generate(name="AGENTS.md", root=d, overwrite=True)  # no raise


if __name__ == "__main__":
    unittest.main()
