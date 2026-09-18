"""User plugins: local Python tools and custom slash commands.

Two plugin kinds, both living in the project's ``.pycode/`` directory:

Python tool SDK - ``.pycode/tools/<name>.py``::

    SCHEMA = {
        "type": "function",
        "function": {
            "name": "mytool",
            "description": "Does a thing",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    DESTRUCTIVE = False  # optional; True adds a confirmation prompt

    def run(args: dict) -> dict:
        return {"ok": True}

Custom slash commands - ``.pycode/commands/<name>.md``::

    Summarize the file $ARGS using the project conventions.

Typed as ``/<name>`` in the REPL; ``$ARGS`` is replaced with whatever the
user typed after the command.

Everything here is best-effort: a broken plugin is reported, never fatal.
Tool-file hooks (``HOOKS = {"post_tool": ["..."]}``) are merged into the
agent's hook runner.
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

HOOK_EVENTS = ("pre_tool", "post_tool", "on_turn")

# populated by register_user_tools; consulted by tools.is_destructive
USER_DESTRUCTIVE: set = set()
# names successfully registered into tools.TOOLS
LOADED_TOOLS: List[str] = []


def _plugin_dir(root: str, kind: str) -> Path:
    return Path(root) / ".pycode" / kind


# ---------------------------------------------------------------------------
# Python tool SDK
# ---------------------------------------------------------------------------


def load_user_tools(root: str) -> List[Dict[str, Any]]:
    """Load ``.pycode/tools/*.py`` modules exposing SCHEMA + run(args)."""
    tools_dir = _plugin_dir(root, "tools")
    out: List[Dict[str, Any]] = []
    if not tools_dir.is_dir():
        return out
    for path in sorted(tools_dir.glob("*.py")):
        entry: Dict[str, Any] = {"path": str(path)}
        try:
            spec = importlib.util.spec_from_file_location(
                f"pycode_user_{path.stem}", path
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            schema = getattr(module, "SCHEMA", None)
            run = getattr(module, "run", None)
            if not isinstance(schema, dict) or not callable(run):
                raise ValueError("plugin must define SCHEMA (dict) and run(args)")
            name = schema.get("function", {}).get("name", "")
            if not name:
                raise ValueError("SCHEMA is missing function.name")
            entry.update(
                {
                    "name": name,
                    "schema": schema,
                    "run": run,
                    "destructive": bool(getattr(module, "DESTRUCTIVE", False)),
                }
            )
        except Exception as exc:  # noqa: BLE001 - report, never fatal
            entry["error"] = str(exc)
        out.append(entry)
    return out


def _wrap_user_run(run: Any) -> Any:
    """Adapt a plugin's ``run(args)`` to the dispatcher's ``fn(**args)`` call."""

    def wrapper(**kwargs):
        return run(kwargs)

    wrapper.__name__ = getattr(run, "__name__", "user_run")
    return wrapper


def register_user_tools(root: str) -> Dict[str, Any]:
    """Register user tools into pycode.tools; returns a summary dict.

    Built-in names win: a plugin named like an existing tool is skipped.
    """
    from pycode import tools as tools_mod

    loaded: List[str] = []
    errors: List[Dict[str, str]] = []
    for entry in load_user_tools(root):
        name = entry.get("name")
        if not name:
            errors.append({"path": entry["path"], "error": entry.get("error", "?")})
            continue
        if name in tools_mod.TOOLS:
            errors.append(
                {
                    "path": entry["path"],
                    "error": f"name {name!r} collides with a built-in tool",
                }
            )
            continue
        tools_mod.TOOLS[name] = _wrap_user_run(entry["run"])
        tools_mod.TOOL_SCHEMAS.append(entry["schema"])
        if entry.get("destructive"):
            USER_DESTRUCTIVE.add(name)
        loaded.append(name)
    LOADED_TOOLS[:] = loaded
    return {"loaded": loaded, "errors": errors}


# ---------------------------------------------------------------------------
# Custom slash commands
# ---------------------------------------------------------------------------


def load_user_commands(root: str) -> Dict[str, Dict[str, str]]:
    """Load ``.pycode/commands/*.md`` as name -> {path, template}."""
    cmds_dir = _plugin_dir(root, "commands")
    out: Dict[str, Dict[str, str]] = {}
    if not cmds_dir.is_dir():
        return out
    for path in sorted(cmds_dir.glob("*.md")):
        try:
            template = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if template:
            out[path.stem] = {"path": str(path), "template": template}
    return out


def apply_command(template: str, args: str) -> str:
    """Substitute ``$ARGS``/``${ARGS}`` in a command template."""
    return template.replace("${ARGS}", args).replace("$ARGS", args)


# ---------------------------------------------------------------------------
# Tool-file hooks
# ---------------------------------------------------------------------------


def collect_user_hooks(root: str) -> Dict[str, List[str]]:
    """Merge ``HOOKS`` dicts from tool plugin modules (best-effort).

    Called after load_user_tools data is needed but without re-importing:
    we re-load modules cheaply since plugin files are small.
    """
    hooks: Dict[str, List[str]] = {}
    for entry in load_user_tools(root):
        if "error" in entry:
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"pycode_user_hooks_{Path(entry['path']).stem}", entry["path"]
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:  # noqa: BLE001
            continue
        raw = getattr(module, "HOOKS", None)
        if not isinstance(raw, dict):
            continue
        for event, value in raw.items():
            event = str(event).strip().lower()
            if event not in HOOK_EVENTS:
                continue
            cmds = value if isinstance(value, list) else [value]
            hooks.setdefault(event, []).extend(str(c) for c in cmds if c)
    return hooks


def merge_hooks(
    base: Dict[str, List[str]], extra: Dict[str, List[str]]
) -> Dict[str, List[str]]:
    """Return base hooks extended with extra (does not mutate base)."""
    out = {event: list(cmds) for event, cmds in base.items()}
    for event, cmds in extra.items():
        out.setdefault(event, [])
        for cmd in cmds:
            if cmd not in out[event]:
                out[event].append(cmd)
    return out
