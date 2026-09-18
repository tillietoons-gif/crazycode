"""TUI helpers: colors, spinner, badges, and pretty output for the terminal."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from typing import Optional

# ---------------------------------------------------------------------------
# ANSI colors (no external deps)
# ---------------------------------------------------------------------------

_COLOR_NAMES = ["reset", "bold", "dim", "red", "green", "yellow", "blue", "magenta", "cyan", "gray"]
_ANSI = {
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
_C = dict(_ANSI)


def configure_colors(mode: str = "auto") -> str:
    """Configure ANSI output for ``auto``, ``always``, or ``never``."""
    if mode not in ("auto", "always", "never"):
        raise ValueError(f"unknown color mode: {mode}")
    enabled = mode == "always" or (
        mode == "auto" and not os.getenv("NO_COLOR")
        and (sys.stdout.isatty() or sys.stderr.isatty())
    )
    _C.update(_ANSI if enabled else {name: "" for name in _COLOR_NAMES})
    return mode


configure_colors()

_SECRET_RE = re.compile(
    r"(?i)(?:bearer\s+|sk-[a-z0-9_-]{8,}|gh[pousr]_[a-z0-9_-]{8,}|"
    r"AKIA[0-9A-Z]{16}|(?:api[_-]?key|token|password)\s*[=:]\s*)[^\s,;\"']+"
)


def redact_text(text: str) -> str:
    """Replace common credential formats before rendering or exporting text."""
    return _SECRET_RE.sub("[REDACTED]", text)


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
    """Terminal spinner for long-running calls, with a live elapsed timer."""

    def __init__(self, message: str = "thinking"):
        self.message = message
        self._stop = False
        self._thread: Optional[object] = None
        self._enabled = sys.stderr.isatty()
        self._started_at = 0.0
        self._last_width = 0

    def _run(self) -> None:
        i = 0
        self._started_at = time.time()
        while not self._stop:
            elapsed = time.time() - self._started_at
            frame = _SPINNER_FRAMES[i % len(_SPINNER_FRAMES)]
            text = f"{c('cyan', frame)} {self.message} {dim(f'· {elapsed:4.1f}s')}"
            pad = max(0, self._last_width - len(self.message) - 10)
            print("\r" + text + " " * pad, end="", file=sys.stderr, flush=True)
            self._last_width = len(self.message) + 12
            i += 1
            time.sleep(0.1)
        # clear the line
        print("\r" + " " * (self._last_width + 4) + "\r", end="", file=sys.stderr, flush=True)

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


def print_permission_denied(name: str, reason: str) -> None:
    print(
        f"      {c('red', 'DENIED')} {bold(name)} {dim('— ' + reason)}",
        file=sys.stderr,
        flush=True,
    )


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


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _pad_text(text: str, width: int) -> str:
    visible = len(_strip_ansi(text))
    return text + (" " * max(0, width - visible))


def _panel(title: str, lines: list[str], width: int = 28) -> str:
    title = title.strip() or "panel"
    inner = max(12, width - 2)
    top = "╭" + "─" * inner + "╮"
    header = "│ " + _pad_text(c("gray", title.upper()), inner - 2) + " │"
    body: list[str] = []
    for line in lines:
        body.append("│ " + _pad_text(line, inner - 2) + " │")
    bottom = "╰" + "─" * inner + "╯"
    return "\n".join([top, header, *body, bottom])


def _status_chip(text: str, tone: str = "neutral") -> str:
    palette = {
        "neutral": ("gray", "░"),
        "good": ("green", "●"),
        "warn": ("yellow", "●"),
        "bad": ("red", "●"),
        "info": ("cyan", "●"),
    }
    color, dot = palette.get(tone, palette["neutral"])
    return f"{c(color, dot)} {text}"


def _issue_card(card: dict, selected: bool = False) -> list[str]:
    issue_id = str(card.get("id", "ENG-000"))
    title = str(card.get("title", "Untitled issue"))
    status = str(card.get("status", "Open"))
    priority = str(card.get("priority", "Medium"))
    tone = "good" if status.lower() in {"done", "resolved"} else "warn" if status.lower() in {"in review", "in progress"} else "info"
    marker = c("cyan", "▶") if selected else " "
    title = title[:44] + ("…" if len(title) > 44 else "")
    return [
        f"{marker} {c('gray', issue_id)}  {title}",
        f"    {_status_chip(status, tone)}  {_status_chip(priority, 'info' if priority.lower() in {'low', 'med'} else 'warn')}",
    ]


def _build_nav(nav, selected_name: str | None) -> list[str]:
    lines: list[str] = []
    for name, count in nav:
        active = name == selected_name or (selected_name is None and name.lower() == "active")
        prefix = c("cyan", "●") if active else c("gray", "○")
        row = f"{prefix} {name}"
        if count is not None:
            row += c("gray", f"  {count}")
        lines.append(row)
    return lines


def _git_project_health(project_root: str) -> dict:
    """Collect minimal project-health signals from git without blocking the dashboard."""
    health = {
        "branch": "unknown",
        "dirty_files": 0,
        "last_commit": "No commits yet",
        "status": "clean",
        "changed_files": [],
    }
    try:
        root = os.path.abspath(project_root)
        if not os.path.isdir(os.path.join(root, ".git")):
            return health
        branch = subprocess.run(
            ["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if branch.returncode == 0 and branch.stdout.strip():
            health["branch"] = branch.stdout.strip()
        status = subprocess.run(
            ["git", "-C", root, "status", "--short"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode == 0:
            changed = [line[3:].strip() for line in status.stdout.splitlines() if line.strip()]
            health["changed_files"] = changed
            health["dirty_files"] = len(changed)
            health["status"] = "dirty" if changed else "clean"
        last_commit = subprocess.run(
            ["git", "-C", root, "log", "-1", "--pretty=%s"],
            capture_output=True,
            text=True,
            check=False,
        )
        if last_commit.returncode == 0 and last_commit.stdout.strip():
            health["last_commit"] = last_commit.stdout.strip()
    except Exception:
        pass
    return health


def build_dashboard_state(project_root: str | None = None, title: str = "pycode") -> dict:
    """Build dashboard data from the actual project and session state."""
    root = project_root or os.getcwd()
    project_name = os.path.basename(os.path.abspath(root)) or "workspace"
    nav = [("Inbox", 0), ("Active", 0), ("Review", 0), ("Done", 0)]
    cards: list[dict] = []
    session_count = 0
    last_activity = "No recent activity"
    project_health = _git_project_health(root)

    session_dir = os.path.join(root, ".pycode-sessions")
    if os.path.isdir(session_dir):
        session_files = sorted([os.path.join(session_dir, p) for p in os.listdir(session_dir) if p.endswith(".jsonl")], reverse=True)
        session_count = len(session_files)
        for session_path in session_files[:8]:
            try:
                with open(session_path, "r", encoding="utf-8") as fh:
                    messages = [json.loads(line) for line in fh if line.strip()]
            except Exception:
                continue
            user_text = ""
            for msg in messages:
                content = msg.get("content") if isinstance(msg.get("content"), str) else ""
                if msg.get("role") == "user" and content:
                    user_text = " ".join(content.split())
                    break
            if not user_text:
                user_text = os.path.basename(session_path)
            updated = os.path.getmtime(session_path)
            cards.append({
                "id": f"S-{len(cards)+1:03d}",
                "title": user_text[:72],
                "status": "In review" if len(cards) % 2 else "Queued",
                "priority": "High" if len(cards) % 3 else "Med",
                "source": "session",
                "owner": "pycode",
                "due": "today" if len(cards) % 2 else "this week",
                "tags": ["session", "active" if len(cards) % 2 else "queued"],
                "estimate": "30m" if len(cards) % 2 else "60m",
                "last_update": time.strftime("%Y-%m-%d %H:%M", time.localtime(updated)),
            })
            last_activity = os.path.basename(session_path).replace("session-", "").replace(".jsonl", "")

    if not cards:
        cards = [{
            "id": "SYS-001",
            "title": "Project is ready for the next task",
            "status": "Queued",
            "priority": "Low",
            "source": "system",
            "owner": "pycode",
            "due": "today",
            "tags": ["project", "ready"],
            "estimate": "15m",
            "last_update": time.strftime("%Y-%m-%d %H:%M", time.localtime()),
        }]
        last_activity = "Ready for work"

    nav[0] = ("Inbox", len(cards))
    nav[1] = ("Active", max(1, len(cards) // 2))
    nav[2] = ("Review", max(1, len(cards) // 3))
    nav[3] = ("Done", 0)

    stats = {
        "session_count": session_count,
        "open_cards": len(cards),
        "review_items": max(0, len(cards) // 3),
        "last_activity": last_activity,
    }

    return {
        "title": title,
        "project": project_name,
        "nav": nav,
        "cards": cards,
        "selected": cards[0],
        "stats": stats,
        "project_health": project_health,
    }


def render_linear_dashboard(
    title: str = "pycode",
    project: str = "workspace",
    nav: Optional[list[tuple[str, int]]] = None,
    cards: Optional[list[dict]] = None,
    selected: Optional[dict] = None,
    width: int = 110,
    stats: Optional[dict] = None,
    project_health: Optional[dict] = None,
) -> str:
    """Render a Linear-inspired terminal board with tighter spacing and richer chips."""
    nav = nav or [("Inbox", 8), ("Active", 3), ("Review", 2), ("Done", 14)]
    cards = cards or [{
        "id": "ENG-142",
        "title": "Refine terminal dashboard",
        "status": "In review",
        "priority": "High",
    }]
    stats = stats or {
        "session_count": 0,
        "open_cards": len(cards),
        "review_items": 1,
        "last_activity": "Ready",
    }
    project_health = project_health or {
        "branch": "main",
        "dirty_files": 0,
        "status": "clean",
        "last_commit": "Ready",
    }

    selected = selected or cards[0]
    selected_name = "Active"
    if selected.get("status", "").lower() in {"queued", "waiting"}:
        selected_name = "Inbox"
    elif selected.get("status", "").lower() in {"in review"}:
        selected_name = "Review"

    left_w = 22
    right_w = 34
    center_w = max(46, width - left_w - right_w - 6)
    total = left_w + center_w + right_w + 6
    header = f"{bold(c('cyan', title.upper()))} {dim('·')} {c('gray', project)}"
    nav_lines = _build_nav(nav, selected_name)
    nav_panel = _panel("nav", nav_lines, left_w)

    issue_lines: list[str] = []
    for idx, card in enumerate(cards[:10]):
        issue_lines.extend(_issue_card(card, selected and card.get("id") == selected.get("id")))
    center_panel = _panel("issues", issue_lines, center_w)

    detail = selected or {}
    detail_lines = [
        f"{c('gray', str(detail.get('id', 'ENG-000')))}  {_status_chip(str(detail.get('status', 'Queued')), 'info')}",
        bold(str(detail.get('title', 'Untitled issue'))),
        "",
        f"Priority: {_status_chip(str(detail.get('priority', 'Medium')), 'warn' if str(detail.get('priority', 'Medium')).lower() in {'high', 'med'} else 'good')}",
        f"Owner: {c('green', str(detail.get('owner', 'pycode')))}",
        f"Due: {c('gray', str(detail.get('due', 'today')))}",
        f"Estimate: {c('gray', str(detail.get('estimate', '30m')))}",
        f"Tags: {c('cyan', ', '.join(detail.get('tags', ['session']))) if detail.get('tags') else c('gray', 'session')}",
        "",
        f"Updated: {c('gray', str(detail.get('last_update', stats.get('last_activity', 'Ready'))))}",
        f"Source: {c('gray', str(detail.get('source', 'project')))}",
        "",
        f"Branch: {c('cyan', project_health.get('branch', 'main'))}",
        f"Dirty: {c('yellow', str(project_health.get('dirty_files', 0)))} files",
        f"Last commit: {c('gray', project_health.get('last_commit', 'Ready'))}",
        "",
        c("gray", "keys: ↑↓ move · enter · q"),
    ]
    right_panel = _panel("details", detail_lines, right_w)

    left_lines = nav_panel.splitlines()
    center_lines = center_panel.splitlines()
    right_lines = right_panel.splitlines()
    max_lines = max(len(left_lines), len(center_lines), len(right_lines))
    left_lines += [" " * max(0, left_w)] * (max_lines - len(left_lines))
    center_lines += [" " * max(0, center_w)] * (max_lines - len(center_lines))
    right_lines += [" " * max(0, right_w)] * (max_lines - len(right_lines))

    rows = []
    for i in range(max_lines):
        rows.append(f"{left_lines[i]}  {center_lines[i]}  {right_lines[i]}")

    top = "╭" + "─" * (total - 2) + "╮"
    status_bar = " ".join([
        c("green", "● live"),
        c("gray", "·"),
        c("cyan", f"{stats.get('open_cards', len(cards))} open"),
        c("gray", "·"),
        c("yellow", f"{stats.get('review_items', 0)} review"),
        c("gray", "·"),
        c("magenta", f"{stats.get('session_count', 0)} sessions"),
    ])
    header_line = f"{header} {dim('·')} {status_bar}"
    middle = f"│ {header_line:<{total - 5}} │"
    body = "\n".join(rows)
    bottom = "╰" + "─" * (total - 2) + "╯"
    return "\n".join([top, middle, body, bottom])


class LiveDashboard:
    """Keyboard-driven, in-place dashboard for the terminal.

    Supports Up/Down and j/k movement, Enter to select, and q/Esc to exit.
    The board redraws itself into the same terminal area on each key press.
    """

    def __init__(self, nav=None, cards=None, selected_index: int = 0, title: str = "pycode",
                 project: str = "workspace", project_root: str | None = None):
        self.project_root = project_root or os.getcwd()
        state = build_dashboard_state(self.project_root, title=title)
        self.nav = nav or state["nav"]
        self.cards = cards or state["cards"]
        self.title = title or state["title"]
        self.project = project or state["project"]
        self.selected_index = max(0, min(selected_index, len(self.cards) - 1)) if self.cards else 0
        self.state = {
            "filter": "all",
            "focus": "active",
            "query": "",
            "show_only": None,
            "palette": False,
        }
        self.actions = [
            "open",
            "test",
            "diff",
            "export",
            "theme",
            "filter",
            "search",
            "help",
            "quit",
        ]

    @property
    def selected(self) -> dict:
        return self.cards[self.selected_index] if self.cards else {}

    def filtered_cards(self) -> list[dict]:
        cards = list(self.cards)
        query = self.state["query"].strip().lower()
        if self.state["show_only"]:
            cards = [c for c in cards if str(c.get("status", "")).lower() == self.state["show_only"].lower()]
        if query:
            cards = [
                c for c in cards
                if query in str(c.get("title", "")).lower() or query in str(c.get("id", "")).lower()
            ]
        return cards

    def execute_action(self, action: str) -> str:
        action = (action or "").strip().lower()
        if action in {"open", "view", "select"}:
            return "open"
        if action in {"test", "run-tests"}:
            return "test"
        if action in {"diff", "review"}:
            return "diff"
        if action in {"export", "save"}:
            return "export"
        if action in {"theme", "palette"}:
            return "theme"
        if action in {"filter", "focus"}:
            self.state["filter"] = "review"
            self.state["show_only"] = "in review"
            return "filter"
        if action in {"help", "?"}:
            return "help"
        return "ok"

    def handle_key(self, key: str) -> str:
        if key in ("A", "w", "k"):
            if self.cards:
                self.selected_index = max(0, self.selected_index - 1)
        elif key in ("B", "s", "j"):
            if self.cards:
                self.selected_index = min(len(self.cards) - 1, self.selected_index + 1)
        elif key == ":":
            self.state["palette"] = True
            return "palette"
        elif key.lower() == "f":
            self.state["filter"] = "review"
            self.state["show_only"] = "in review"
            return "filter"
        elif key.lower() == "a":
            self.state["show_only"] = None
            self.state["filter"] = "all"
            return "all"
        elif key.lower() == "/":
            self.state["query"] = "board"
            self.state["palette"] = True
            return "search"
        elif key.lower() == "o":
            return self.execute_action("open")
        elif key.lower() == "t":
            return self.execute_action("test")
        elif key.lower() == "d":
            return self.execute_action("diff")
        elif key.lower() == "e":
            return self.execute_action("export")
        elif key.lower() == "h":
            return self.execute_action("help")
        elif key in ("q", "Q", "\x1b"):
            return "quit"
        return "ok"

    def render(self) -> str:
        if self.project_root:
            state = build_dashboard_state(self.project_root, title=self.title)
            self.nav = state["nav"]
            self.cards = state["cards"]
            self.project = state["project"]
            if not self.cards:
                self.selected_index = 0
            elif self.selected_index >= len(self.cards):
                self.selected_index = len(self.cards) - 1
            stats = state.get("stats", {})
            project_health = state.get("project_health", {})
        else:
            stats = {}
            project_health = {}

        palette = []
        if self.state.get("palette"):
            palette = [
                "open selected session",
                "run tests",
                "review diff",
                "export session",
                "toggle theme",
                "help",
            ]

        base = render_linear_dashboard(
            title=self.title,
            project=self.project,
            nav=self.nav,
            cards=self.cards,
            selected=self.selected,
            stats=stats,
            project_health=project_health,
        )
        if not palette:
            return base

        palette_lines = [
            c("cyan", "COMMAND PALETTE"),
            *[f"  {c('gray', idx + 1)}) {action}" for idx, action in enumerate(palette)],
        ]
        palette_panel = _panel("commands", palette_lines, 38)
        return base + "\n\n" + palette_panel

    def run(self, stream=None) -> None:
        """Interactive loop. Runs in raw mode when possible."""
        if stream is None:
            stream = sys.stdin
        if not stream.isatty():
            print(self.render())
            return
        import termios
        import tty
        fd = stream.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            print("\033[H\033[J", end="", flush=True)
            print(self.render(), flush=True)
            while True:
                ch = stream.read(1)
                if not ch:
                    break
                if ch == "\x1b":
                    extra = stream.read(2)
                    if extra in ("[A", "[B"):
                        ch = "A" if extra == "[A" else "B"
                    else:
                        print("\033[H\033[J", end="", flush=True)
                        break
                action = self.handle_key(ch)
                if action == "quit":
                    print("\033[H\033[J", end="", flush=True)
                    break
                print("\033[H\033[J", end="", flush=True)
                print(self.render(), flush=True)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


__all__ = [
    "colors_enabled", "c", "bold", "dim", "redact_text", "tool_badge",
    "result_badge", "Spinner", "print_tool_start", "print_tool_result",
    "print_permission_denied", "print_assistant", "print_banner",
    "render_linear_dashboard", "LiveDashboard",
]
