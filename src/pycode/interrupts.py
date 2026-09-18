"""Esc-to-abort support for long-running turns.

During a turn the CLI installs an ``EscListener`` that watches stdin for a lone
ESC byte (``0x1b``) while the agent works. When pressed, the shared
``AbortController`` is flipped; the agent loop checks it between LLM calls and
tool executions via :func:`check_abort` and stops the turn cleanly.

Everything degrades to a no-op on non-TTY streams, on Windows, or when
``termios`` is unavailable. A global "current" controller lets the provider and
agent consult abort state without threading a parameter through every call.
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Optional, TextIO


class Aborted(Exception):
    """Raised when the user aborts the current turn (e.g. presses Esc)."""


class AbortController:
    """A thread-safe one-way abort flag."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._aborted = False

    @property
    def aborted(self) -> bool:
        return self._aborted

    def abort(self) -> None:
        with self._lock:
            self._aborted = True

    def reset(self) -> None:
        with self._lock:
            self._aborted = False

    def check(self) -> bool:
        """Raise :class:`Aborted` if the flag is set, else return False."""
        if self._aborted:
            raise Aborted("aborted by user")
        return False


_current: Optional[AbortController] = None


def set_current(controller: Optional[AbortController]) -> None:
    global _current
    _current = controller


def get_current() -> Optional[AbortController]:
    return _current


def clear_current() -> None:
    global _current
    _current = None


def new_controller() -> AbortController:
    """Create, install, and return a fresh controller."""
    controller = AbortController()
    set_current(controller)
    return controller


def check_abort() -> bool:
    """Raise :class:`Aborted` if the current controller has been aborted."""
    controller = _current
    if controller is not None and controller.aborted:
        raise Aborted("aborted by user")
    return False


def supports_esc(stream: Optional[TextIO] = None) -> bool:
    """True when this stream/OS can deliver a raw ESC byte."""
    if os.name != "posix":
        return False
    stream = stream or sys.stdin
    try:
        import termios  # noqa: F401

        return bool(stream.isatty())
    except Exception:  # noqa: BLE001
        return False


class EscListener:
    """Context manager that aborts ``controller`` when ESC is pressed.

    On a TTY the stream is switched to cbreak mode and a daemon thread polls
    for a single ``0x1b`` byte. On exit the original terminal attributes are
    restored. On unsupported streams entering/exiting is a no-op.
    """

    def __init__(
        self, controller: AbortController, stream: Optional[TextIO] = None
    ) -> None:
        self.controller = controller
        self.stream = stream or sys.stdin
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._fd: Optional[int] = None
        self._saved = None
        self._active = False

    def __enter__(self) -> "EscListener":
        if not supports_esc(self.stream):
            return self
        try:
            import termios
            import tty

            self._fd = self.stream.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        except Exception:  # noqa: BLE001
            self._active = False
            return self
        self._active = True
        self._thread = threading.Thread(
            target=self._watch, name="pycode-esc", daemon=True
        )
        self._thread.start()
        return self

    def _watch(self) -> None:
        import select

        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([self._fd], [], [], 0.1)
            except Exception:  # noqa: BLE001
                break
            if not ready:
                continue
            try:
                data = os.read(self._fd, 1)
            except Exception:  # noqa: BLE001
                break
            if not data:
                break
            if data == b"\x1b":
                self.controller.abort()
                break

    def __exit__(self, *exc_info) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.3)
        if self._active and self._saved is not None and self._fd is not None:
            try:
                import termios

                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            except Exception:  # noqa: BLE001
                pass
        self._active = False
        return False
