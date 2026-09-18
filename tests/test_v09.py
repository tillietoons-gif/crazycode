"""Tests for pycode v0.9 additions: layered config files, color themes,
and Esc-to-abort support."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest

from pycode import tui
from pycode import interrupts
from pycode.config import (
    minimal_toml,
    parse_toml,
    load_config,
    find_config_files,
    project_config_path,
    coalesce,
    _flatten,
    _strip_comment,
)
from pycode.tui_theme import (
    THEMES,
    available_themes,
    apply_theme,
    current_theme,
    get_theme,
    resolve_theme,
    theme_swatch,
)
from pycode.interrupts import (
    Aborted,
    AbortController,
    EscListener,
    check_abort,
    clear_current,
    get_current,
    new_controller,
    set_current,
    supports_esc,
)
from pycode.cli import _run_with_abort
from pycode.tui import LiveDashboard, build_dashboard_state, render_linear_dashboard
from pycode.tui_commands import canonicalize


class TestCanonicalizeCommands(unittest.TestCase):
    def test_full_commands_preserved(self):
        for cmd in ("/cost", "/context", "/theme", "/config", "/export"):
            self.assertEqual(canonicalize(cmd), cmd, cmd)

    def test_colon_prefix_still_expands(self):
        self.assertEqual(canonicalize(":cl"), "/clear")
        self.assertEqual(canonicalize(":s"), "/save")

    def test_theme_command_registered(self):
        from pycode.tui_commands import COMMANDS
        self.assertIn("/theme", COMMANDS)


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

class TestMinimalToml(unittest.TestCase):
    def test_scalars_and_sections(self):
        text = (
            "# a comment\n"
            "theme = \"nord\"\n"
            "auto_approve = true\n"
            "context_budget = 80000\n"
            "max_iterations = 12\n"
            "\n"
            "[provider]\n"
            "model = 'gpt-4o'\n"
            "temperature = 0.2\n"
        )
        data = minimal_toml(text)
        self.assertEqual(data["theme"], "nord")
        self.assertIs(data["auto_approve"], True)
        self.assertEqual(data["context_budget"], 80000)
        self.assertEqual(data["provider"]["model"], "gpt-4o")
        self.assertAlmostEqual(data["provider"]["temperature"], 0.2)

    def test_inline_comment_stripped(self):
        data = minimal_toml("model = \"gpt-4o\"  # the model\ncost = false # off\n")
        self.assertEqual(data["model"], "gpt-4o")
        self.assertIs(data["cost"], False)

    def test_hash_inside_string_preserved(self):
        data = minimal_toml('api_key = "sk-#123"\n')
        self.assertEqual(data["api_key"], "sk-#123")

    def test_strip_comment_quotes(self):
        self.assertEqual(_strip_comment('"a # b" # c'), '"a # b"')

    def test_parse_toml_matches_minimal(self):
        text = "[ui]\ntheme = \"dracula\"\n"
        self.assertEqual(parse_toml(text)["ui"]["theme"], "dracula")


class TestConfigLoading(unittest.TestCase):
    def test_flatten_known_keys_only(self):
        raw = {"provider": {"model": "m", "bogus": 1}, "unknown": 2, "theme": "mono"}
        flat = _flatten(raw)
        self.assertEqual(flat, {"model": "m", "theme": "mono"})

    def test_project_overrides_user(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root:
            user_dir = os.path.join(home, ".config", "pycode")
            os.makedirs(user_dir)
            with open(os.path.join(user_dir, "config.toml"), "w", encoding="utf-8") as fh:
                fh.write('theme = "nord"\nmax_iterations = 10\nmodel = "user-model"\n')
            proj_dir = os.path.join(root, ".pycode")
            os.makedirs(proj_dir)
            with open(os.path.join(proj_dir, "config.toml"), "w", encoding="utf-8") as fh:
                fh.write('theme = "dracula"\nmax_iterations = 20\n')

            cfg = load_config(root, home=home)
            self.assertEqual(cfg["theme"], "dracula")
            self.assertEqual(cfg["max_iterations"], 20)
            self.assertEqual(cfg["model"], "user-model")

    def test_find_config_files_order(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root:
            user_dir = os.path.join(home, ".config", "pycode")
            os.makedirs(user_dir)
            user_cfg = os.path.join(user_dir, "config.toml")
            open(user_cfg, "w", encoding="utf-8").close()
            proj_dir = os.path.join(root, ".pycode")
            os.makedirs(proj_dir)
            proj_cfg = project_config_path(root)
            open(proj_cfg, "w", encoding="utf-8").close()

            found = find_config_files(root, home=home)
            self.assertEqual(found, [user_cfg, proj_cfg])

    def test_missing_files_yield_empty(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(load_config(root), {})

    def test_coalesce(self):
        self.assertEqual(coalesce(None, None, 5), 5)
        self.assertIsNone(coalesce(None, None))
        self.assertEqual(coalesce(0, 9), 0)


# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------

class TestThemes(unittest.TestCase):
    def tearDown(self):
        apply_theme("default", force=True)

    def test_available(self):
        for name in ("default", "mono", "dracula", "nord", "solarized"):
            self.assertIn(name, available_themes())
        self.assertIn("default", THEMES)

    def test_get_unknown_falls_back(self):
        self.assertEqual(get_theme("nope"), THEMES["default"])

    def test_resolve_theme(self):
        self.assertEqual(resolve_theme("nord"), "nord")
        self.assertEqual(resolve_theme(None), "default")
        self.assertEqual(resolve_theme("bogus"), "default")

    def test_apply_updates_palette_forced(self):
        apply_theme("nord", force=True)
        self.assertEqual(current_theme(), "nord")
        self.assertIn("38;5;", tui._C["blue"])

    def test_mono_clears_colors(self):
        apply_theme("mono", force=True)
        self.assertEqual(tui._C["red"], "")
        self.assertEqual(tui._C["gray"], "")

    def test_apply_unknown_raises(self):
        with self.assertRaises(ValueError):
            apply_theme("does-not-exist", force=True)

    def test_swatch_returns_string(self):
        self.assertIsInstance(theme_swatch("default"), str)

    def test_apply_without_force_on_non_tty_keeps_name(self):
        apply_theme("dracula")
        self.assertEqual(current_theme(), "dracula")


class TestLinearDashboard(unittest.TestCase):
    def test_render_linear_dashboard(self):
        dashboard = render_linear_dashboard(
            title="pycode",
            project="crazycode",
            nav=[("Inbox", 8), ("Active", 3), ("Review", 2)],
            cards=[
                {"id": "ENG-142", "title": "Refine terminal dashboard", "status": "In review", "priority": "High"},
                {"id": "ENG-143", "title": "Polish status bar", "status": "In progress", "priority": "Med"},
            ],
            selected={"id": "ENG-142", "title": "Refine terminal dashboard", "status": "In review", "priority": "High"},
        )
        self.assertIn("pycode", dashboard)
        self.assertIn("Active", dashboard)
        self.assertIn("ENG-142", dashboard)
        self.assertIn("Refine terminal dashboard", dashboard)

    def test_live_dashboard_navigation_updates_selection(self):
        board = LiveDashboard(
            nav=[("Inbox", 8), ("Active", 3)],
            cards=[
                {"id": "ENG-142", "title": "Refine terminal dashboard", "status": "In review", "priority": "High"},
                {"id": "ENG-143", "title": "Polish status bar", "status": "In progress", "priority": "Med"},
            ],
            selected_index=0,
        )
        self.assertEqual(board.selected_index, 0)
        board.handle_key("B")
        self.assertEqual(board.selected_index, 1)
        board.handle_key("A")
        self.assertEqual(board.selected_index, 0)

    def test_build_dashboard_state_uses_project_sessions(self):
        with tempfile.TemporaryDirectory() as root:
            sess_dir = os.path.join(root, ".pycode-sessions")
            os.makedirs(sess_dir)
            with open(os.path.join(sess_dir, "session-20260918-120000.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"role": "user", "content": "Refactor the dashboard"}) + "\n")
                fh.write(json.dumps({"role": "assistant", "content": "Done"}) + "\n")
            state = build_dashboard_state(root)
            self.assertTrue(state["cards"])
            self.assertIn("Refactor the dashboard", state["cards"][0]["title"])
            self.assertEqual(state["nav"][0][0], "Inbox")

    def test_build_dashboard_state_exposes_live_project_stats(self):
        with tempfile.TemporaryDirectory() as root:
            sess_dir = os.path.join(root, ".pycode-sessions")
            os.makedirs(sess_dir)
            with open(os.path.join(sess_dir, "session-20260918-120000.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"role": "user", "content": "Refactor the dashboard"}) + "\n")
                fh.write(json.dumps({"role": "assistant", "content": "Working on it"}) + "\n")
            state = build_dashboard_state(root)
            self.assertIn("stats", state)
            self.assertEqual(state["stats"]["session_count"], 1)
            self.assertTrue(state["stats"]["last_activity"])


# ---------------------------------------------------------------------------
# Interrupts / Esc-to-abort
# ---------------------------------------------------------------------------

class TestAbortController(unittest.TestCase):
    def tearDown(self):
        clear_current()

    def test_flags(self):
        c = AbortController()
        self.assertFalse(c.aborted)
        c.abort()
        self.assertTrue(c.aborted)
        c.reset()
        self.assertFalse(c.aborted)

    def test_check_raises_when_aborted(self):
        c = AbortController()
        self.assertFalse(c.check())
        c.abort()
        with self.assertRaises(Aborted):
            c.check()

    def test_current_controller(self):
        c = new_controller()
        self.assertIs(get_current(), c)
        self.assertFalse(check_abort())
        c.abort()
        with self.assertRaises(Aborted):
            check_abort()
        clear_current()
        self.assertFalse(check_abort())

    def test_set_current_none(self):
        set_current(None)
        self.assertIsNone(get_current())
        self.assertFalse(check_abort())

    def test_supports_esc_false_for_stringio(self):
        self.assertFalse(supports_esc(io.StringIO()))

    def test_esc_listener_noop_on_stringio(self):
        c = AbortController()
        with EscListener(c, stream=io.StringIO()) as listener:
            self.assertFalse(listener._active)
        self.assertFalse(c.aborted)


class TestRunWithAbort(unittest.TestCase):
    def tearDown(self):
        clear_current()

    class _Agent:
        def __init__(self, result="ok"):
            self.result = result
            self.called = 0

        def run(self, **kwargs):
            self.called += 1
            return self.result

    class _AbortingAgent(_Agent):
        def run(self, **kwargs):
            from pycode import interrupts as _i
            controller = _i.get_current()
            controller.abort()
            raise Aborted("aborted by user")

    def test_plain_run_returns_result(self):
        agent = self._Agent("hello")
        self.assertEqual(_run_with_abort(agent, use_tui=False, user_input="hi"), "hello")
        self.assertEqual(agent.called, 1)

    def test_abort_returns_message(self):
        agent = self._AbortingAgent()
        self.assertEqual(_run_with_abort(agent, use_tui=False, user_input="hi"), "[aborted by user]")

    def test_controller_cleared_after_run(self):
        agent = self._Agent()
        _run_with_abort(agent, use_tui=False, user_input="hi")
        self.assertIsNone(get_current())


class TestAgentAbort(unittest.TestCase):
    def tearDown(self):
        clear_current()

    def test_agent_run_returns_early_when_aborted(self):
        from pycode.agent import Agent

        class _Provider:
            model = "fake"
            api_base = "http://localhost"

            def chat_stream(self, messages, tools=None):
                raise AssertionError("LLM should not be called after abort")

        with tempfile.TemporaryDirectory() as root:
            agent = Agent(project_root=root, verbose=False)
            agent.provider = _Provider()
            new_controller().abort()
            self.assertEqual(agent.run("hello"), "[aborted by user]")


if __name__ == "__main__":
    unittest.main()
