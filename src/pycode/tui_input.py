"""Framed input box for the REPL: a bordered prompt with a title.

Renders::

    ╭─ you ─────────────────────────────────────╮
    │ ❯ type your request here_
    ╰───────────────────────────────────────────╯

The box is pure ANSI decoration around a plain ``input()`` call, so
readline editing, history, and tab-completion all keep working. On a
non-TTY it degrades to the classic ``>>> `` prompt.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Optional

from pycode.tui import c, dim, bold

INPUT_WIDTH = 58

_TOP = "╭─"
_CORNER_TL, _CORNER_TR = "╭", "╮"
_CORNER_BL, _CORNER_BR = "╰", "╯"
_H, _V = "─", "│"


def _tty(stream=sys.stdin) -> bool:
    try:
        return stream.isatty()
    except Exception:  # noqa: BLE001
        return False


def supports_box(stream=sys.stdin) -> bool:
    """True when unicode box drawing is safe on this terminal."""
    if not _tty(stream):
        return False
    if os.name == "nt":  # legacy cmd.exe fonts are unreliable
        try:
            return sys.stdout.encoding.lower().startswith("utf")
        except Exception:  # noqa: BLE001
            return False
    return True


def top_border(title: str = "you", width: int = INPUT_WIDTH) -> str:
    label = f"{_H} {title} "
    body = label + _H * max(0, width - len(label) - 2)
    return _CORNER_TL + body + _CORNER_TR


def bottom_border(width: int = INPUT_WIDTH) -> str:
    return _CORNER_BL + _H * (width - 2) + _CORNER_BR


def read_input(
    prompt_str: Optional[str] = None,
    input_fn: Optional[Callable[[str], str]] = None,
    title: str = "you",
    use_box: Optional[bool] = None,
) -> str:
    """Read a line of user input, framed in a box when possible.

    Returns the raw line (stripped of the trailing newline by input()).
    """
    if use_box is None:
        use_box = supports_box()
    inp = input_fn or input

    if not use_box:
        return inp(prompt_str or ">>> ")

    print(dim(top_border(title, INPUT_WIDTH)), flush=True)
    try:
        line = inp(f"{_V} " + bold(c("cyan", "❯")) + " ")
    finally:
        # bottom border always closes the box, even after ^D/^C
        print(dim(bottom_border(INPUT_WIDTH)), flush=True)
    return line
