"""Session history picker: arrow-key navigation of saved sessions.

Lists the `.pycode-sessions/` JSONL files with a short timestamp + first-user-
message preview, and lets the user scroll through them. On a TTY it uses
curses-style arrow keys; without a TTY it falls back to a numbered list the
user types an index for.
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from typing import List, Optional

from pycode.session import list_sessions, load_session


@dataclass
class SessionMeta:
    path: str
    mtime: float
    msg_count: int
    preview: str  # first user message, truncated

    @property
    def short_name(self) -> str:
        return os.path.basename(self.path)

    def timestamp_str(self) -> str:
        # filename is session-YYYYMMDD-HHMMSS.jsonl
        m = re.search(r"session-(\d{8})-(\d{6})", self.short_name)
        if m:
            d, t = m.group(1), m.group(2)
            return f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:4]}:{t[4:6]}"
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.mtime))


def _preview(messages: List[dict]) -> str:
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            text = " ".join(m["content"].split())
            return text[:48] + ("…" if len(text) > 48 else "")
    return "(no user messages)"


def collect_sessions(root: Optional[str] = None) -> List[SessionMeta]:
    out: List[SessionMeta] = []
    for p in list_sessions(root):
        try:
            st = os.stat(p)
            msgs = load_session(p)
            out.append(
                SessionMeta(
                    path=p,
                    mtime=st.st_mtime,
                    msg_count=len(msgs),
                    preview=_preview(msgs),
                )
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  warn: could not load session {p}: {exc}", file=sys.stderr)
            continue
    return out


def _render_line(idx: int, sel: int, s: SessionMeta, n: int) -> str:
    marker = ">" if idx == sel else " "
    ts = s.timestamp_str()
    return f"{marker} {idx + 1}/{n}  {ts}  {s.short_name}  ({s.msg_count} msgs)  {s.preview}"


def _read_key() -> str:
    """Read a single keypress (or a 3-byte arrow sequence) from stdin."""
    ch = sys.stdin.read(1)
    if ch == "\x1b" or ch == "\033":
        # possible escape sequence
        rest = sys.stdin.read(2)
        if len(rest) == 2 and rest[0] == "[":
            return rest[1]  # 'A'=up, 'B'=down, 'C'=right, 'D'=left
        return "\x1b"  # lone Esc
    return ch


def _curses_pick(items: List[SessionMeta]) -> int:
    """Arrow-key picker using raw terminal reads. Returns selected index or -1."""
    import termios
    import tty

    sel = 0
    n = len(items)
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            sys.stdout.write("\033[H\033[J")  # clear screen + scroll region
            print(
                "  Select a session to resume (↑/↓ or j/k to move, Enter to pick, q to cancel)"
            )
            for i, s in enumerate(items):
                print(_render_line(i, sel, s, n))
            key = _read_key()
            if key in ("\r", "\n"):
                return sel
            if key == "\x1b" or key in ("q", "\x03"):
                return -1
            if key in ("A", "w", "k"):  # up
                sel = max(0, sel - 1)
            elif key in ("B", "s", "j"):  # down
                sel = min(n - 1, sel + 1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\033[H\033[J")


def _text_pick(items: List[SessionMeta]) -> int:
    """Numbered-list picker for non-TTY. Returns selected index or -1."""
    n = len(items)
    print("  Sessions (type a number to resume, Enter for latest, q to cancel):")
    for i, s in enumerate(items):
        print(
            f"    {i + 1}/{n}  {s.timestamp_str()}  {s.short_name}  ({s.msg_count} msgs)  {s.preview}"
        )
    try:
        ans = input("  ").strip()
    except (EOFError, KeyboardInterrupt):
        return -1
    if ans.lower() in ("q", "x", ""):
        return n - 1 if ans == "" else -1
    try:
        idx = int(ans) - 1
        if 0 <= idx < n:
            return idx
    except ValueError:
        pass
    return -1


def pick_session(root: Optional[str] = None, tty: bool = True) -> Optional[SessionMeta]:
    """Run the session picker; returns the chosen SessionMeta or None."""
    items = collect_sessions(root)
    if not items:
        print("  No saved sessions found.", file=sys.stderr)
        return None
    idx = _curses_pick(items) if tty and sys.stdin.isatty() else _text_pick(items)
    if idx < 0:
        return None
    return items[idx]
