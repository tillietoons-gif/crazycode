"""Named color themes for the TUI.

A theme maps the semantic color roles used by ``tui.c()`` to ANSI SGR codes.
Applying a theme mutates ``pycode.tui._C`` in place, so every existing call to
``tui.c(role, text)`` picks up the new palette. ``bold``/``dim``/``reset`` are
left untouched by every theme (the ``mono`` theme only clears the color roles).

Usage::

    from pycode.tui_theme import apply_theme, available_themes
    apply_theme("nord")
"""

from __future__ import annotations

from typing import Dict, List

THEMES: Dict[str, Dict[str, str]] = {
    "default": {
        "red": "31",
        "green": "32",
        "yellow": "33",
        "blue": "34",
        "magenta": "35",
        "cyan": "36",
        "gray": "90",
    },
    "mono": {
        "red": "",
        "green": "",
        "yellow": "",
        "blue": "",
        "magenta": "",
        "cyan": "",
        "gray": "",
    },
    "dracula": {
        "red": "91",
        "green": "92",
        "yellow": "93",
        "blue": "94",
        "magenta": "95",
        "cyan": "96",
        "gray": "90",
    },
    "nord": {
        "red": "38;5;174",
        "green": "38;5;150",
        "yellow": "38;5;179",
        "blue": "38;5;109",
        "magenta": "38;5;139",
        "cyan": "38;5;116",
        "gray": "38;5;102",
    },
    "solarized": {
        "red": "38;5;160",
        "green": "38;5;64",
        "yellow": "38;5;136",
        "blue": "38;5;33",
        "magenta": "38;5;125",
        "cyan": "38;5;37",
        "gray": "38;5;244",
    },
}

DEFAULT_THEME = "default"

_current = DEFAULT_THEME


def available_themes() -> List[str]:
    """Names of all built-in themes."""
    return list(THEMES)


def get_theme(name: str) -> Dict[str, str]:
    """Return the role -> SGR-code mapping for ``name`` (falls back to default)."""
    return THEMES.get(name, THEMES[DEFAULT_THEME])


def current_theme() -> str:
    """Name of the currently applied theme."""
    return _current


def resolve_theme(name: str | None) -> str:
    """Return ``name`` if known, else the default theme name."""
    if name in THEMES:
        return name
    return DEFAULT_THEME


def _sgr(code: str) -> str:
    return f"\033[{code}m" if code else ""


def apply_theme(name: str, force: bool = False) -> str:
    """Apply ``name`` to the TUI palette and return the applied theme name.

    No-op on the live palette when coloring is disabled (non-TTY) unless
    ``force`` is True. Raises ``ValueError`` for an unknown theme.
    """
    if name not in THEMES:
        raise ValueError(
            f"unknown theme: {name!r} (have: {', '.join(available_themes())})"
        )

    from pycode import tui

    global _current
    _current = name
    if not tui.colors_enabled() and not force:
        return name
    for role, code in THEMES[name].items():
        tui._C[role] = _sgr(code)
    return name


def theme_swatch(name: str = DEFAULT_THEME) -> str:
    """A one-line colored sample using every role in ``name``."""
    from pycode.tui import c

    name = resolve_theme(name)
    return " ".join(
        c(role, role)
        for role in ("red", "green", "yellow", "blue", "magenta", "cyan", "gray")
    )
