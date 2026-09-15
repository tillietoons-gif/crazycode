"""Agent hooks: shell commands fired on agent lifecycle events.

Hooks are declared in the ``[hooks]`` section of a pycode config file::

    [hooks]
    post_tool  = "black --quiet {path}"
    on_turn    = "echo turn done"

Supported events: ``pre_tool``, ``post_tool``, ``on_turn``.

Placeholders available in command templates: ``{tool}``, ``{path}``,
``{args_json}``, ``{ok}``. A value may be a single command string or a list
of them. Hook failures are recorded but never abort the agent; hooks run
with a short timeout in the agent's working directory.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from typing import Any, Dict, List, Optional

HOOK_EVENTS = ("pre_tool", "post_tool", "on_turn")
_HOOK_TIMEOUT = 30


def _normalize_commands(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return [str(value)]


def expand_template(template: str, context: Dict[str, Any]) -> str:
    """Expand ``{placeholders}`` in a hook command template.

    Unknown placeholders are left as-is (they may be shell syntax like
    ``{1..5}`` or awk programs).
    """
    out = template
    for key, value in context.items():
        out = out.replace("{" + key + "}", str(value))
    return out


class HookResult:
    def __init__(self, event: str, command: str, returncode: int, output: str):
        self.event = event
        self.command = command
        self.returncode = returncode
        self.output = output

    def __repr__(self) -> str:
        return f"HookResult({self.event!r}, rc={self.returncode})"


class HookRunner:
    """Runs configured shell commands when the agent emits events."""

    def __init__(self, commands: Optional[Dict[str, List[str]]] = None):
        self.commands: Dict[str, List[str]] = commands or {}

    @classmethod
    def from_config(cls, cfg: Optional[Dict[str, Any]]) -> "HookRunner":
        """Build from a config dict's ``hooks`` section (or the whole config)."""
        raw = (cfg or {}).get("hooks") or {}
        commands: Dict[str, List[str]] = {}
        for key, value in raw.items():
            event = str(key).strip().lower()
            if event not in HOOK_EVENTS:
                continue
            cmds = _normalize_commands(value)
            if cmds:
                commands[event] = cmds
        return cls(commands)

    @classmethod
    def from_file(cls, path: str) -> "HookRunner":
        """Build from a TOML file's ``[hooks]`` section."""
        from pycode.config import parse_toml
        try:
            with open(path, encoding="utf-8") as fh:
                return cls.from_config(parse_toml(fh.read()))
        except (OSError, ValueError):
            return cls()

    @property
    def enabled(self) -> bool:
        return bool(self.commands)

    def emit(self, event: str, context: Optional[Dict[str, Any]] = None) -> List[HookResult]:
        """Fire all commands registered for ``event``. Never raises."""
        results: List[HookResult] = []
        for template in self.commands.get(event, []):
            cmd = expand_template(template, context or {})
            results.append(self._run(event, cmd))
        return results

    def _run(self, event: str, command: str) -> HookResult:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=_HOOK_TIMEOUT,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            return HookResult(event, command, proc.returncode, output.strip()[:4000])
        except subprocess.TimeoutExpired:
            return HookResult(event, command, -1, "hook timed out")
        except Exception as exc:  # noqa: BLE001
            return HookResult(event, command, -1, f"hook failed: {exc}")

    def describe(self) -> str:
        """Human-readable hook summary for the inspector."""
        lines = []
        for event in HOOK_EVENTS:
            for template in self.commands.get(event, []):
                lines.append(f"  {event}: {template}")
        return "\n".join(lines)


def hook_context(tool: str, args: Dict[str, Any], ok: Optional[bool] = None) -> Dict[str, str]:
    """Build the placeholder context dict for a tool hook."""
    ctx = {
        "tool": tool,
        "path": str(args.get("path", "")),
        "args_json": json.dumps(args, ensure_ascii=False, default=str),
    }
    if ok is not None:
        ctx["ok"] = "true" if ok else "false"
    return ctx
