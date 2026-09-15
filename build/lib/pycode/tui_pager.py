"""ANSI-aware log pager: page through long tool outputs in the terminal.

When a tool result or a subagent trace is long, instead of dumping it all at
once the CLI can page it. Keys:
    j / down / Space / Enter   next page
    k / up                      prev page
    g                          first page
    G                          last page
    q / Esc / Ctrl-C           quit
On a non-TTY (or if the user prefers), a plain `--` no-op is provided that
just prints everything.
"""

from __future__ import annotations

import sys
from typing import List, Optional


def _page(text_lines: List[str], page: int, page_size: int) -> List[str]:
    start = page * page_size
    return text_lines[start:start + page_size]


def pager(
    text: str,
    page_size: int = 20,
    use_tty: bool = True,
) -> None:
    """Page `text` to stdout. If not a TTY, print everything at once."""
    lines = text.split("\n")
    if not use_tty or not sys.stdout.isatty():
        print(text)
        return

    total_pages = max(1, (len(lines) + page_size - 1) // page_size)
    page = 0
    import termios
    import tty

    fd = sys.stdout.fileno()
    old = termios.tcgetattr(fd)

    def draw() -> None:
        sys.stdout.write("\033[H\033[J")
        chunk = _page(lines, page, page_size)
        for ln in chunk:
            print(ln)
        show = f" page {page + 1}/{total_pages} · j/Space=next k=prev g=first G=last q=quit"
        print("\033[1m" + dim_text(show) + "\033[0m")

    def dim_text(s: str) -> str:
        return "\033[2m" + s + "\033[0m"

    try:
        tty.setraw(fd)
        draw()
        while True:
            ch = sys.stdin.read(1)
            if ch in ("q", "\x1b", "\x03"):
                break
            elif ch in ("j", " ", "\n", "n", "\x1b[B"):
                page = min(total_pages - 1, page + 1)
                draw()
            elif ch in ("k", "\x1b[A"):
                page = max(0, page - 1)
                draw()
            elif ch == "g":
                page = 0
                draw()
            elif ch == "G":
                page = total_pages - 1
                draw()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\033[?1049h")  # restore alt screen buffer
        sys.stdout.write("\n")


def paginate_or_print(text: str, page_size: int = 20) -> None:
    """Convenience: page on a TTY, otherwise print whole text."""
    use_tty = sys.stdout.isatty()
    if not use_tty:
        print(text)
        return
    # only actually page if it's long enough to be worth it
    if len(text.split("\n")) <= page_size:
        print(text)
        return
    pager(text, page_size=page_size, use_tty=True)
