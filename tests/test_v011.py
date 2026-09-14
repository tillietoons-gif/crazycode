"""Tests for pycode v0.11 (M2 codebase intelligence): project symbol index,
the symbols tool, smart read, and project-map prompt injection."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from pycode.agent import Agent
from pycode.index import (
    ProjectIndex,
    extract_symbols,
    patterns_for,
    indexable,
    detect_language,
)
from pycode.tools import (
    tool_read,
    tool_symbols,
    get_project_index,
    dispatch_tool,
    TOOL_SCHEMAS,
)

PY_SRC = '''\
import os

class Greeter:
    """docstring"""

    def greet(self, name):
        return f"hello {name}"

def standalone():
    greeter = Greeter()
    return greeter.greet("world")

async def async_fn():
    pass
'''

JS_SRC = '''\
export class Widget {
  render() {}
}

function helper(x) {
  return x;
}

const onClick = () => {};
'''

GO_SRC = '''\
package main

import "fmt"

type Server struct {
    Port int
}

type Handler interface {
    Serve()
}

func main() {
    fmt.Println("hi")
}

func (s *Server) Start() error {
    return nil
}
'''

RS_SRC = '''\
pub struct Config {
    pub debug: bool,
}

pub enum Mode { Fast, Slow }

pub trait Runner {
    fn run(&self);
}

pub fn build(cfg: Config) -> Mode {
    Mode::Fast
}
'''


class TestSymbolExtraction(unittest.TestCase):
    def test_python(self):
        syms = extract_symbols("src/x.py", PY_SRC)
        names = [(s["kind"], s["name"], s["line"]) for s in syms]
        self.assertIn(("class", "Greeter", 3), names)
        self.assertIn(("def", "greet", 6), names)
        self.assertIn(("def", "standalone", 9), names)
        self.assertIn(("def", "async_fn", 13), names)

    def test_js(self):
        syms = extract_symbols("a.js", JS_SRC)
        names = {(s["kind"], s["name"]) for s in syms}
        self.assertIn(("class", "Widget"), names)
        self.assertIn(("function", "helper"), names)
        self.assertIn(("const", "onClick"), names)

    def test_go(self):
        syms = extract_symbols("m.go", GO_SRC)
        names = {(s["kind"], s["name"]) for s in syms}
        self.assertIn(("type", "Server"), names)
        self.assertIn(("type", "Handler"), names)
        self.assertIn(("func", "main"), names)
        self.assertIn(("func", "Start"), names)

    def test_rust(self):
        syms = extract_symbols("lib.rs", RS_SRC)
        names = {(s["kind"], s["name"]) for s in syms}
        self.assertIn(("struct", "Config"), names)
        self.assertIn(("enum", "Mode"), names)
        self.assertIn(("trait", "Runner"), names)
        self.assertIn(("fn", "build"), names)

    def test_unsupported_extension(self):
        self.assertIsNone(patterns_for(".xyz"))
        self.assertEqual(extract_symbols("a.xyz", "def fake():"), [])
        self.assertFalse(indexable("a.xyz"))

    def test_tsx_uses_ts_patterns(self):
        syms = extract_symbols("c.tsx", "interface Props { a: string }\n")
        self.assertEqual([s["name"] for s in syms], ["Props"])

    def test_detect_language(self):
        self.assertEqual(detect_language("x.py"), "generic")
        self.assertIsNone(detect_language("x.xyz"))


class TestProjectIndex(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        os.makedirs(os.path.join(self.root, "src"))
        with open(os.path.join(self.root, "src", "mod.py"), "w", encoding="utf-8") as fh:
            fh.write(PY_SRC)
        with open(os.path.join(self.root, "app.js"), "w", encoding="utf-8") as fh:
            fh.write(JS_SRC)
        os.makedirs(os.path.join(self.root, "node_modules", "dep"))
        with open(os.path.join(self.root, "node_modules", "dep", "x.js"), "w", encoding="utf-8") as fh:
            fh.write("function skipped() {}\n")

    def tearDown(self):
        self._tmp.cleanup()

    def _index(self, **kw):
        from pycode.index import ProjectIndex
        return ProjectIndex(self.root, **kw)

    def test_build_finds_and_skips(self):
        idx = self._index()
        stats = idx.build(save=False)
        self.assertEqual(stats["files"], 2)  # src/mod.py + app.js
        names = {s["name"] for s in idx.all_symbols()}
        self.assertIn("Greeter", names)
        self.assertIn("helper", names)
        self.assertNotIn("skipped", names)  # node_modules excluded

    def test_find_substring_and_kind(self):
        idx = self._index()
        idx.build(save=False)
        hits = idx.find("gree")
        self.assertTrue(any(s["name"] == "Greeter" for s in hits))
        defs = idx.find("greeter", kind="def")
        self.assertEqual(defs, [])
        fns = idx.find("", kind="def", limit=50)
        self.assertTrue(all(s["kind"] == "def" for s in fns))

    def test_definition_exact(self):
        idx = self._index()
        idx.build(save=False)
        d = idx.definition("standalone")
        self.assertIsNotNone(d)
        self.assertEqual(d["kind"], "def")
        self.assertIsNone(idx.definition("nope"))

    def test_references_excludes_defs(self):
        idx = self._index()
        idx.build(save=False)
        refs = idx.references("Greeter")
        self.assertTrue(refs)
        # the def line itself must not appear among references
        def_line = idx.definition("Greeter")["line"]
        self.assertFalse(any(r["line"] == def_line and r["path"] == idx.definition("Greeter")["path"]
                             for r in refs))
        self.assertTrue(any("Greeter()" in r["text"] or "Greeter" in r["text"] for r in refs))

    def test_summary_groups_by_file(self):
        idx = self._index()
        idx.build(save=False)
        text = idx.summary()
        self.assertIn("src/mod.py:", text)
        self.assertIn("app.js:", text)
        self.assertIn("class Greeter", text)

    def test_cache_roundtrip(self):
        idx = self._index()
        idx.build(save=True)
        cache_file = os.path.join(self.root, ".pycode", "index", "symbols.json")
        self.assertTrue(os.path.isfile(cache_file))

        # fresh instance loads from cache without touching files
        from pycode.index import ProjectIndex
        idx2 = ProjectIndex(self.root)
        idx2.load()
        self.assertEqual(idx2.stats()["files"], idx.stats()["files"])
        self.assertEqual(idx2.stats()["symbols"], idx.stats()["symbols"])

    def test_stats(self):
        idx = self._index()
        idx.build(save=False)
        st = idx.stats()
        self.assertEqual(st["root"], self.root)
        self.assertGreater(st["symbols"], 0)


class TestSymbolsTool(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        with open(os.path.join(self.root, "m.py"), "w", encoding="utf-8") as fh:
            fh.write(PY_SRC)
        get_project_index(self.root).build()

    def tearDown(self):
        self._tmp.cleanup()

    def test_dispatch_find(self):
        out = json.loads(dispatch_tool("symbols", {"query": "Greeter"}))
        self.assertEqual(out["mode"], "find")
        self.assertTrue(any(r["name"] == "Greeter" for r in out["results"]))

    def test_dispatch_map(self):
        out = json.loads(dispatch_tool("symbols", {"mode": "map"}))
        self.assertIn("map", out)
        self.assertIn("stats", out)
        self.assertGreater(out["stats"]["symbols"], 0)

    def test_dispatch_refs(self):
        out = json.loads(dispatch_tool("symbols", {"query": "Greeter", "mode": "refs"}))
        self.assertIn("references", out)

    def test_dispatch_refs_requires_query(self):
        out = json.loads(dispatch_tool("symbols", {"mode": "refs"}))
        self.assertIn("error", out)

    def test_registered_schema(self):
        names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        self.assertIn("symbols", names)


class TestSmartRead(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "m.py")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(PY_SRC)

    def tearDown(self):
        self._tmp.cleanup()

    def test_read_around_symbol(self):
        out = tool_read(self.path, symbol="standalone")
        self.assertIn("def standalone", out)
        # window starts above the def (line 9), so includes prior lines
        self.assertIn("return f\"hello {name}\"", out)

    def test_read_symbol_not_found(self):
        out = tool_read(self.path, symbol="nonexistent_fn")
        self.assertIn("Error", out)
        self.assertIn("nonexistent_fn", out)

    def test_read_without_symbol_unchanged(self):
        out = tool_read(self.path, offset=1, limit=5)
        self.assertIn("import os", out)

    def test_find_symbol_line_helper(self):
        from pycode.tools import _find_symbol_line
        self.assertEqual(_find_symbol_line(PY_SRC, "standalone"), 9)
        self.assertIsNone(_find_symbol_line(PY_SRC, "zzz"))


class TestProjectMapPrompt(unittest.TestCase):
    def test_map_injected_into_system_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "svc.py"), "w", encoding="utf-8") as fh:
                fh.write("def serve():\n    pass\n")
            get_project_index(d).build()
            agent = Agent(project_root=d, verbose=False, use_project_map=True)
            self.assertIn("## Project map", agent.messages[0]["content"])
            self.assertIn("def serve", agent.messages[0]["content"])

    def test_map_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "svc.py"), "w", encoding="utf-8") as fh:
                fh.write("def serve():\n    pass\n")
            agent = Agent(project_root=d, verbose=False, use_project_map=False)
            self.assertNotIn("## Project map", agent.messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
