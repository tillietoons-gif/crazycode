"""Tests for pycode v1.1: first-run setup wizard, config-file api_key,
and installer/release plumbing."""

from __future__ import annotations

import os
import tempfile
import unittest

from pycode.config import parse_toml, _flatten, project_config_path
from pycode.wizard import (
    dump_config,
    needs_wizard,
    reset_wizard,
    run_wizard,
    wizard_done,
    wizard_marker_path,
    write_config_for,
)


class FakeInput:
    """Scripted stdin: pops answers, raising EOFError when exhausted."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError("end of scripted input")
        return self.answers.pop(0)


def collector():
    lines = []
    return (lambda text: lines.append(text)), lines


class TestNeedsWizard(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name

    def tearDown(self):
        reset_wizard(self.home)
        self._tmp.cleanup()

    def test_first_interactive_run_without_key(self):
        self.assertTrue(needs_wizard(False, interactive=True, home=self.home))

    def test_skipped_when_key_present(self):
        self.assertFalse(needs_wizard(True, interactive=True, home=self.home))

    def test_skipped_when_not_interactive(self):
        self.assertFalse(needs_wizard(False, interactive=False, home=self.home))

    def test_skipped_when_already_done(self):
        mark = wizard_marker_path(self.home)
        os.makedirs(os.path.dirname(mark), exist_ok=True)
        open(mark, "w").write("1")
        self.assertTrue(wizard_done(self.home))
        self.assertFalse(needs_wizard(False, interactive=True, home=self.home))

    def test_force_and_no_wizard_flags(self):
        self.assertFalse(needs_wizard(False, interactive=True, no_wizard=True, home=self.home))
        self.assertTrue(needs_wizard(False, interactive=False, force=True, home=self.home))


class TestRunWizard(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.home = tempfile.mkdtemp()

    def tearDown(self):
        reset_wizard(self.home)
        self._tmp.cleanup()

    def test_happy_path_openai(self):
        fake = FakeInput(["1", "sk-wizard-key", ""])
        out, lines = collector()
        result = run_wizard(input_fn=fake, output_fn=out, root=self.root, home=self.home)

        self.assertEqual(result["provider"], "openai")
        self.assertEqual(result["api_key"], "sk-wizard-key")
        self.assertTrue(result["config_path"])
        self.assertTrue(wizard_done(self.home))

        # project config is writable -> wizard prefers it
        self.assertEqual(result["config_path"], project_config_path(self.root))
        text = open(result["config_path"], encoding="utf-8").read()
        self.assertIn('[provider]', text)
        self.assertIn('api_key = "sk-wizard-key"', text)
        self.assertIn('api_base = "https://api.openai.com/v1"', text)

        # config parses and yields the key
        data = parse_toml(text)
        self.assertEqual(data["provider"]["api_key"], "sk-wizard-key")

    def test_ollama_needs_no_key(self):
        fake = FakeInput(["3", "llama3.1:8b"])
        out, _ = collector()
        result = run_wizard(input_fn=fake, output_fn=out, root=self.root, home=self.home)
        self.assertEqual(result["provider"], "ollama")
        self.assertEqual(result["api_key"], "")
        text = open(result["config_path"], encoding="utf-8").read()
        self.assertIn('api_base = "http://localhost:11434/v1"', text)
        self.assertNotIn("api_key", text)

    def test_eof_at_first_prompt_aborts(self):
        fake = FakeInput([])
        out, lines = collector()
        with self.assertRaises(EOFError):
            run_wizard(input_fn=fake, output_fn=out, root=self.root, home=self.home)

    def test_invalid_choice_reprompts(self):
        fake = FakeInput(["9", "0", "2", "sk-anthropic", "claude-x"])
        out, lines = collector()
        result = run_wizard(input_fn=fake, output_fn=out, root=self.root, home=self.home)
        self.assertEqual(result["provider"], "anthropic")
        self.assertEqual(result["model"], "claude-x")
        # two rejection hints before the accepted pick
        self.assertEqual(sum(1 for l in lines if l.startswith("  ? pick")), 2)

    def test_write_config_merges_existing(self):
        cfg_path = project_config_path(self.root)
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as fh:
            fh.write('theme = "nord"\n\n[provider]\nmodel = "gpt-4o-mini"\n')

        out, _ = collector()
        run_wizard(input_fn=FakeInput(["1", "sk-new", ""]),
                   output_fn=out, root=self.root, home=self.home)

        data = parse_toml(open(cfg_path, encoding="utf-8").read())
        self.assertEqual(data["theme"], "nord")            # preserved
        self.assertEqual(data["provider"]["model"], "gpt-4o-mini")  # preserved
        self.assertEqual(data["provider"]["api_key"], "sk-new")     # added

    def test_write_config_falls_back_to_user_home(self):
        # make the project dir read-only by pointing root at a file path
        bogus_root = os.path.join(self.root, "not-a-dir.txt")
        open(bogus_root, "w").write("x")
        out, _ = collector()
        result = run_wizard(input_fn=FakeInput(["5", "sk-or", ""]),
                            output_fn=out, root=bogus_root, home=self.home)
        self.assertNotEqual(result["config_path"], project_config_path(bogus_root))
        self.assertTrue(os.path.isfile(result["config_path"]))
        self.assertTrue(result["config_path"].startswith(self.home))


class TestDumpConfig(unittest.TestCase):
    def test_roundtrip(self):
        cfg = {"theme": "nord", "cost": True, "loop_max": 3,
               "provider": {"api_key": 'with "quotes"', "model": "m"}}
        text = dump_config(cfg)
        data = parse_toml(text)
        self.assertEqual(data["theme"], "nord")
        self.assertIs(data["cost"], True)
        self.assertEqual(data["loop_max"], 3)
        self.assertEqual(data["provider"]["api_key"], 'with "quotes"')
        self.assertEqual(data["provider"]["model"], "m")

    def test_scalar_then_sections(self):
        text = dump_config({"a": 1, "provider": {"x": 2}, "ui": {"y": 3}})
        # scalars before sections
        self.assertLess(text.index("a = 1"), text.index("[provider]"))
        self.assertLess(text.index("[provider]"), text.index("[ui]"))


class TestConfigApiKey(unittest.TestCase):
    def test_provider_api_key_survives_flatten(self):
        flat = _flatten({"provider": {"api_key": "sk-file", "model": "m"}, "theme": "mono"})
        self.assertEqual(flat["api_key"], "sk-file")
        self.assertEqual(flat["model"], "m")


if __name__ == "__main__":
    unittest.main()
