"""TUI helpers: colors, spinner, badges, and pretty output for the terminal."""

from __future__ import annotations

import sys
import time
from typing import Optional

# ---------------------------------------------------------------------------
# ANSI colors (no external deps)
# ---------------------------------------------------------------------------

if sys.stdout.isatty() and not sys.platform.startswith("win"):
    _C = {
        "reset": "\033[0m",
        "bold": "\033[1m",
        "dim": "\033[2m",
        "red": "\033[31m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "blue": "\033[34m",
        "magenta": "\033[35m",
        "cyan": "\033[36m",
        "gray": "\033[90m",
    }
else:
    _C = {k: "" for k in ["reset", "bold", "dim", "red", "green", "yellow", "blue", "magenta", "cyan", "gray"]}


def colors_enabled() -> bool:
    """True when ANSI coloring is active for stdout."""
    return bool(_C.get("reset"))


def c(color: str, text: str) -> str:
    """Wrap text in an ANSI color."""
    return f"{_C.get(color, '')}{text}{_C['reset']}"


def bold(text: str) -> str:
    return c("bold", text)


def dim(text: str) -> str:
    return c("gray", text)


# ---------------------------------------------------------------------------
# Tool badges
# ---------------------------------------------------------------------------

_TOOL_ICONS = {
    "bash": ">>>",
    "read": "R",
    "write": "W",
    "edit": "E",
    "glob": "*",
    "grep": "G",
    "webfetch": "W",
    "todo": "T",
}

_TOOL_COLORS = {
    "bash": "yellow",
    "read": "blue",
    "write": "magenta",
    "edit": "magenta",
    "glob": "cyan",
    "grep": "cyan",
    "webfetch": "green",
    "todo": "blue",
}


def tool_badge(name: str) -> str:
    icon = _TOOL_ICONS.get(name, "?")
    color = _TOOL_COLORS.get(name, "gray")
    return c(color, f"[{icon}]") + bold(name)


def result_badge(ok: bool) -> str:
    if ok:
        return c("green", "ok")
    return c("red", "FAIL")


# ---------------------------------------------------------------------------
# Spinner
# ---------------------------------------------------------------------------

_SPINNER_FRAMES = ["-", "\\", "|", "/", "+", "x"]


class Spinner:
    """Simple terminal spinner for long-running LLM calls."""

    def __init__(self, message: str = "thinking..."):
        self.message = message
        self._stop = False
        self._thread: Optional[object] = None
        self._enabled = sys.stderr.isatty()

    def _run(self) -> None:
        i = 0
        while not self._stop:
            frame = _SPINNER_FRAMES[i % len(_SPINNER_FRAMES)]
            print(f"\r{c('cyan', frame)} {self.message}", end="", file=sys.stderr, flush=True)
            i += 1
            time.sleep(0.1)
        # clear the line
        print("\r" + " " * (len(self.message) + 4), end="", file=sys.stderr, flush=True)

    def start(self) -> None:
        if not self._enabled:
            print(dim(f"... {self.message}"), file=sys.stderr, flush=True)
            return
        import threading
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._stop = True
            self._thread.join()
            self._thread = None

    def __enter__(self) -> "Spinner":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# Pretty output helpers
# ---------------------------------------------------------------------------


def print_tool_start(name: str, args_preview: str) -> None:
    print(f"\n{tool_badge(name)} {dim(args_preview[:120])}", file=sys.stderr, flush=True)


def print_tool_result(name: str, ok: bool, preview: str) -> None:
    print(f"      {result_badge(ok)} {dim(preview[:200])}", file=sys.stderr, flush=True)


def print_assistant(content: str) -> None:
    print(f"\n{bold('Assistant')} {c('dim', '─' * 40)}", file=sys.stderr, flush=True)
    print(content, file=sys.stderr, flush=True)


def print_banner() -> None:
    from pycode import __version__
    print("=" * 56, file=sys.stderr, flush=True)
    print(bold(c("cyan", f"  pycode v{__version__} - Python AI Coding Agent")),
          file=sys.stderr, flush=True)
    print("=" * 56, file=sys.stderr, flush=True)
    print(
        "  quit/exit to stop, :clear to reset, /resume to load session\n"
        "  /save to persist, /help for more commands",
        file=sys.stderr,
        flush=True,
    )
    print("=" * 56, file=sys.stderr, flush=True)
    print(file=sys.stderr, flush=True)
