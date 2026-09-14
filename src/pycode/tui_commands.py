"""Slash-command tab-completion + aliases for the pycode REPL.

Wires `readline` to complete `/save`, `/resume`, `/rewind`, `/branch`,
`/context`, `/cost`, ... and defines short aliases (`:s`, `:c`, `:q`, ...)
so power users can type less.

The module is pure (no side effects on import) - the CLI calls
`install_completion(agent)` once at REPL start.
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Command table: full name -> (aliases, help)
# ---------------------------------------------------------------------------

COMMANDS: Dict[str, str] = {
    "/clear": "reset conversation",
    "/cost": "show token/cost accounting",
    "/context": "show context window usage",
    "/help": "list commands",
    "/new-context": "generate CLAUDE.md project-instructions file",
    "/plan": "plan-then-execute: draft steps for a goal and run each",
    "/preset": "show available provider presets",
    "/map": "show the project symbol map",
    "/symbols": "look up symbols in the project index (/symbols name)",
    "/rewind": "roll back to a checkpoint (or list them)",
    "/branch": "branch from a checkpoint with new instruction",
    "/resume": "load a saved session",
    "/save": "save session to JSONL",
    "/sessions": "list saved sessions",
    "/theme": "show or switch color theme (/theme <name>)",
}

# alias -> canonical command
ALIASES: Dict[str, str] = {
    "/s": "/save",
    "/r": "/resume",
    "/c": "/clear",
    "/q": "/clear",  # (q is usually exit; keep clear here, exit handled separately)
    "/:": "/clear",
    ":s": "/save",
    ":c": "/clear",
    ":clear": "/clear",
    ":q": "/quit",
    ":quit": "/quit",
    ":exit": "/quit",
    ":h": "/help",
    "/?": "/help",
    "?": "/help",
}

# user-defined slash commands (populated by the CLI from .pycode/commands/)
USER_COMMANDS: set = set()


def canonicalize(user_input: str) -> str:
    """Expand a typed command/alias to its canonical form.

    Non-command input (plain prompts) passes through unchanged. Full command
    names are preserved verbatim (``/cost`` must not collapse to ``/clear``),
    so prefix expansion is limited to colon-style aliases.
    """
    s = user_input.strip()
    # exact alias (also covers "/s" -> "/save", "?" -> "/help")
    if s in ALIASES:
        return ALIASES[s]
    # prefix match on colon aliases only (e.g. ":cl" -> ":clear" -> "/clear")
    for alias, target in ALIASES.items():
        if alias.startswith(":") and len(alias) >= 2 and s.startswith(alias):
            return target
    return s


def _complete(prefix: str) -> List[str]:
    """Return candidate completions for `prefix`."""
    cands: List[str] = []
    all_cmds = sorted(set(list(COMMANDS.keys()) + list(ALIASES.keys()) + list(USER_COMMANDS)))
    for cmd in all_cmds:
        if cmd.startswith(prefix) or (prefix.lstrip("/:") and cmd.startswith(prefix.lstrip("/:"))):
            cands.append(cmd)
    # also complete known session filenames for /save, /resume, /sessions
    if prefix.split()[0] in ("/save", "/resume", "/sessions", "/branch"):
        try:
            from pycode.session import list_sessions
            for s in list_sessions()[:8]:
                base = os.path.basename(s)
                if base.startswith(prefix.split()[-1] if " " in prefix else ""):
                    cands.append(f"{prefix.split()[0]} {base}")
        except Exception:
            pass
    return cands


def install_completion() -> None:
    """Install readline tab-completion for slash commands. No-op if no TTY."""
    if not sys.stdin.isatty():
        return
    try:
        import readline
    except ImportError:
        return

    def completer(text: str, state: int) -> Optional[str]:
        opts = _complete(text)
        return opts[state] if state < len(opts) else None

    readline.set_completer(completer)
    readline.parse_and_bind("tab: complete")


def help_text() -> str:
    """Return a formatted help listing of commands + aliases."""
    lines = ["  commands:"]
    for cmd, desc in sorted(COMMANDS.items()):
        lines.append(f"    {cmd:<16} {desc}")
    alias_str = ", ".join(sorted(ALIASES.keys()))
    lines.append(f"  aliases: {alias_str}")
    return "\n".join(lines)
