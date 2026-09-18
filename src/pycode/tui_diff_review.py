"""Keyboard-driven interactive diff reviewer (i/a/n/q/?).

Wraps DiffReviewer with a richer, colorized rendering of each staged change
and single-key input:

    i   show the full diff (toggle)
    a   approve this change
    r   reject this change
    h   hold (auto-approve remaining)
    n   next (without deciding, keep current pending)
    ?   help
    q   quit (reject remaining, apply nothing more)

The reviewer keeps applying approved changes as it advances, so the user
ends with a summary of what was approved / rejected.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

from pycode.diff_reviewer import DiffReviewer
from pycode.tui import bold, c, dim

HELP = (
    "  keys:\n"
    "    i   show/hide the full diff\n"
    "    a   approve this change\n"
    "    r   reject this change\n"
    "    h   hold (auto-approve the rest)\n"
    "    n   skip (move to next, decide later)\n"
    "    q   quit (apply nothing further)\n"
    "    ?   this help\n"
)


def _readline_on_tty() -> Optional[str]:
    try:
        import readline  # noqa: F401
    except ImportError:
        pass
    try:
        return input()
    except (EOFError, KeyboardInterrupt):
        return None


class InteractiveDiffReviewer:
    """Review staged dry-run changes with keyboard input and rich diff view."""

    def __init__(
        self,
        pending: List[Dict[str, Any]],
        apply_fn,
        use_color: bool = True,
        default_hide_diff: bool = True,
    ):
        self.pending = list(pending)
        self._apply = apply_fn
        self.use_color = use_color
        self.show_diff = not default_hide_diff
        self.summary = {
            "approved": 0,
            "rejected": 0,
            "held": 0,
            "total": len(self.pending),
        }

    # ------------------------------------------------------------------
    # Per-change rendering
    # ------------------------------------------------------------------

    def _render_diff(self, diff_text: str) -> str:
        lines = (diff_text or "").splitlines()
        out: List[str] = []
        for ln in lines:
            if ln.startswith("+"):
                out.append((c("green", ln) if self.use_color else ln))
            elif ln.startswith("-"):
                out.append((c("red", ln) if self.use_color else ln))
            elif ln.startswith("@@"):
                out.append((c("cyan", ln) if self.use_color else ln))
            elif ln.startswith("diff ") or ln.startswith("index "):
                out.append((dim(ln) if not self.use_color else dim(ln)))
            else:
                out.append(ln)
        return "\n".join(out)

    def _render_prompt(
        self, idx: int, total: int, change: Dict[str, Any], show: bool
    ) -> str:
        path = change.get("path", "(unknown)")
        head = f"\n{bold(c('cyan', f'[{idx}/{total}]'))} {path}"
        block = head + "\n"
        if show:
            block += self._render_diff(change.get("diff", "")) + "\n"
        else:
            block += dim("(i) to show diff") + "\n"
        block += "  a]pprove · r]eject · h]old-rest · n]ext · q]uit · ?[help]"
        return block

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> Dict[str, int]:
        n = len(self.pending)
        i = 0
        hold_all = False
        while i < n and not hold_all:
            change = self.pending[i]
            while True:
                prompt = self._render_prompt(i + 1, n, change, self.show_diff)
                print(prompt, file=sys.stderr, flush=True)
                raw = _readline_on_tty()
                if raw is None:
                    # EOF / Ctrl-C -> reject remaining, stop
                    self.summary["rejected"] += n - i
                    i = n
                    break
                key = raw.strip().lower()
                if key in ("a", "approve", "y", "yes", ""):
                    self._apply(change.get("_tool", "write"), change.get("_args", {}))
                    self.summary["approved"] += 1
                    i += 1
                    break
                elif key in ("r", "reject", "n", "no"):
                    self.summary["rejected"] += 1
                    i += 1
                    break
                elif key in ("h", "hold", "all"):
                    hold_all = True
                    break
                elif key in ("i", "inspect", "diff", "d"):
                    self.show_diff = not self.show_diff
                    continue
                elif key == "n":
                    i += 1
                    break
                elif key == "q":
                    self.summary["rejected"] += n - i
                    i = n
                    break
                elif key in ("?", "h", "help"):
                    print(HELP, file=sys.stderr, flush=True)
                    continue
                else:
                    print(
                        dim(f"  (press a/r/h/n/q/? — '{raw}' ignored)"), file=sys.stderr
                    )
                    continue
            if hold_all:
                # auto-apply the rest
                for rest in self.pending[i:]:
                    self._apply(rest.get("_tool", "write"), rest.get("_args", {}))
                    self.summary["held"] += 1
                break
        return self.summary
