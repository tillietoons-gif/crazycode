"""Export a session (list of messages) to a self-contained styled HTML file.

The HTML is single-file (inline CSS, no external assets) so it can be opened
in any browser or shared. Each message is a card colored by role; tool calls
are rendered as expandable `<details>` blocks with syntax-highlighted JSON.
"""

from __future__ import annotations

import html
import json
import time
from typing import Any, Dict, List

_ROLE_META = {
    "system": ("System", "#1f2937", "#f3f4f6"),
    "user": ("User", "#1e40af", "#dbeafe"),
    "assistant": ("Assistant", "#7c3aed", "#ede9fe"),
    "tool": ("Tool result", "#047857", "#d1fae5"),
}


def _escape_block(content: Any) -> str:
    """Return safe HTML for a message content (str or structured)."""
    if isinstance(content, str):
        return html.escape(content).replace("\n", "<br>")
    try:
        pretty = json.dumps(content, ensure_ascii=False, indent=2, default=str)
        return html.escape(pretty).replace("\n", "<br>")
    except Exception:  # noqa: BLE001
        return html.escape(str(content))


def _tool_calls_block(tool_calls: List[Dict[str, Any]]) -> str:
    out = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        name = html.escape(fn.get("name", "?"))
        raw = fn.get("arguments", "{}")
        try:
            pretty = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pretty = html.escape(raw)
        out.append(
            f'<details class="tc"><summary><span class="badge">{name}</span>'
            f'</summary><pre>{html.escape(pretty)}</pre></details>'
        )
    return "\n".join(out)


def export_to_html(
    messages: List[Dict[str, Any]],
    out_path: str,
    title: str = "pycode session",
) -> str:
    """Render `messages` to a styled HTML file at `out_path`; return the path."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    cards: List[str] = []
    for m in messages:
        role = m.get("role", "user")
        label, color, bg = _ROLE_META.get(role, (role, "#374151", "#f9fafb"))
        body_parts: List[str] = []
        content = m.get("content")
        if content not in (None, ""):
            body_parts.append(f'<div class="content">{_escape_block(content)}</div>')
        tcs = m.get("tool_calls") or []
        if tcs:
            body_parts.append(_tool_calls_block(tcs))
        card = (
            f'<section class="msg" style="border-left:4px solid {color};background:{bg};">'
            f'<div class="meta" style="color:{color};">{html.escape(label)}'
            f' <span class="ts">{ts}</span></div>'
            + "\n".join(body_parts)
            + "</section>"
        )
        cards.append(card)

    doc = _TEMPLATE.format(
        title=html.escape(title),
        body="\n".join(cards),
        n=len(messages),
    )
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return out_path


_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, 'Segoe UI', Roboto, sans-serif;
         max-width: 880px; margin: 2rem auto; padding: 0 1rem; color:#111; }}
  h1 {{ border-bottom:2px solid #e5e7eb; padding-bottom:.5rem; }}
  .msg {{ border-radius:8px; padding:.7rem .9rem; margin:.6rem 0; }}
  .meta {{ font-weight:700; font-size:.8rem; text-transform:uppercase;
           letter-spacing:.05em; margin-bottom:.4rem; }}
  .ts {{ float:right; color:#9ca3af; font-weight:400; text-transform:none; }}
  .content {{ white-space:normal; line-height:1.5; }}
  pre {{ background:#0f172a; color:#e2e8f0; padding:.6rem; border-radius:6px;
        overflow:auto; font-size:.85rem; }}
  details.tc {{ margin:.3rem 0; }}
  .badge {{ display:inline-block; background:#111827; color:#f9fafb;
            padding:1px 8px; border-radius:999px; font-size:.75rem; }}
  .summary {{ color:#9ca3af; font-size:.85rem; }}
</style></head><body>
<h1>{title}</h1>
<p class="summary">{n} messages</p>
{body}
</body></html>
"""
