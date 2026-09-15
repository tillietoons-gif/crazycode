"""First-run setup wizard: interactive provider configuration.

On the first interactive run with no credentials, pycode offers a short
wizard: pick a provider preset, paste an API key, optionally override the
model. The result is written as a TOML config file (project-level when the
directory is writable, user-level otherwise) and the wizard is marked done
so it does not nag again.

Everything is injectable (input/output functions) so the wizard can be
driven - and tested - non-interactively. Non-TTY runs skip it entirely.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable, Dict, Optional

from pycode.providers import PRESETS, get_preset

WIZARD_MARKER_NAME = ".wizard_done"

# menu order for the wizard
_WIZARD_PROVIDERS = ["openai", "anthropic", "ollama", "venice", "openrouter"]


def wizard_marker_path(home: Optional[str] = None) -> str:
    home = home or os.path.expanduser("~")
    return os.path.join(home, ".pycode", WIZARD_MARKER_NAME)


def wizard_done(home: Optional[str] = None) -> bool:
    return os.path.exists(wizard_marker_path(home))


def mark_wizard_done(home: Optional[str] = None) -> None:
    path = wizard_marker_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("1")


def reset_wizard(home: Optional[str] = None) -> None:
    """Forget that the wizard ran (mostly for tests / --wizard)."""
    path = wizard_marker_path(home)
    if os.path.exists(path):
        os.unlink(path)


def needs_wizard(has_api_key: bool, interactive: bool,
                 force: bool = False, no_wizard: bool = False,
                 home: Optional[str] = None) -> bool:
    """True when the wizard should run this launch."""
    if no_wizard:
        return False
    if force:
        return True
    if not interactive or has_api_key:
        return False
    return not wizard_done(home)


def _default_input(prompt: str) -> str:
    return input(prompt)


def _default_output(text: str) -> None:
    print(text, flush=True)


def _ask_choice(out, ask, prompt: str, options, allow_default: bool = False,
                default: str = "") -> str:
    """Render a numbered menu; returns the chosen option value."""
    for i, name in enumerate(options, 1):
        out(f"  {i}. {name}")
    while True:
        raw = ask(prompt).strip()
        if allow_default and not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        if raw in options:
            return raw
        out(f"  ? pick 1-{len(options)}" + (" or press Enter for default" if allow_default else ""))


def run_wizard(
    has_api_key: bool = False,
    input_fn: Callable[[str], str] = _default_input,
    output_fn: Callable[[str], None] = _default_output,
    root: str = ".",
    home: Optional[str] = None,
) -> Dict[str, Any]:
    """Interactive provider setup. Returns a config dict (may be empty).

    Writes the merged result to a config file (project-level when the
    directory is writable, else user-level) and marks the wizard done.
    """
    out = output_fn
    ask = input_fn

    out("")
    out("  Welcome to pycode! Let's set up an LLM provider.")
    out("  (You can change this later in .pycode/config.toml)")
    out("")

    names = [f"{n} ({get_preset(n)['model']})" for n in _WIZARD_PROVIDERS]
    pick = _ask_choice(out, ask, "  provider [1-5]: ", names)
    provider = _WIZARD_PROVIDERS[names.index(pick)]
    preset = get_preset(provider)

    api_key = ""
    if provider != "ollama":
        env_var = PRESETS[provider]["env_key"]
        out("")
        out(f"  Paste your {provider} API key (input hidden from logs; stored in the")
        out(f"  config file, or press Enter to set {env_var} yourself later):")
        api_key = ask("  api key: ").strip()

    out("")
    out(f"  Model [{preset['model']}]:")
    model = ask("  model: ").strip() or ""

    cfg: Dict[str, Any] = {"provider": {}}
    if api_key:
        cfg["provider"]["api_key"] = api_key
    if model:
        cfg["provider"]["model"] = model
    cfg["provider"]["api_base"] = preset["api_base"]

    path = write_config_for(cfg, root=root, home=home)
    if path:
        out("")
        out(f"  Saved: {path}")
    mark_wizard_done(home)

    if not api_key and provider != "ollama":
        out("")
        out(f"  No key stored. Export it before running: export {preset['env_key']}=sk-...")

    out("")
    out("  Setup complete. Run: pycode \"your task here\"")
    out("")
    return {"provider": provider, "api_key": api_key, "model": model,
            "config_path": path or ""}


# ---------------------------------------------------------------------------
# Config writing
# ---------------------------------------------------------------------------

def _quote(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def dump_config(cfg: Dict[str, Any]) -> str:
    """Serialize a flat/one-level-nested config dict to TOML text.

    Top-level scalar keys first, then nested dicts as ``[section]`` tables.
    """
    lines = []
    for key, value in cfg.items():
        if isinstance(value, dict):
            continue
        lines.append(f"{key} = {_quote(value)}")
    for key, value in cfg.items():
        if isinstance(value, dict):
            if lines:
                lines.append("")
            lines.append(f"[{key}]")
            for sub_key, sub_val in value.items():
                if isinstance(sub_val, dict):
                    continue
                lines.append(f"{sub_key} = {_quote(sub_val)}")
    return "\n".join(lines) + "\n"


def write_config_for(cfg: Dict[str, Any], root: str = ".",
                     home: Optional[str] = None) -> Optional[str]:
    """Write cfg to the project config when possible, else the user config.

    An existing file is parsed and merged (wizard values win); comments are
    not preserved. Returns the path written, or None if both failed.
    """
    from pycode.config import parse_toml, project_config_path, user_config_paths

    candidates = [project_config_path(root)]
    candidates.extend(user_config_paths(home))
    for path in candidates:
        try:
            merged: Dict[str, Any] = {}
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    merged = parse_toml(fh.read()) or {}
            for key, value in cfg.items():
                if isinstance(value, dict):
                    merged.setdefault(key, {}).update(value)
                else:
                    merged[key] = value
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(dump_config(merged))
            return path
        except (OSError, ValueError):
            continue
    return None
