"""Config inspector: REPL commands that show active provider/permission/MCP state.

Renders the live agent configuration so a user can verify which providers are
in the failover chain, which permission rules are active, and which MCP
servers are connected - without re-running anything.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pycode.tui import c, dim, bold


def provider_status(provider) -> str:
    """Render the provider / failover chain state."""
    lines: List[str] = []
    if hasattr(provider, "status"):
        # FailoverProvider
        entries = provider.status()
        last_good = getattr(provider, "last_good", 0)
        for i, e in enumerate(entries):
            active = "•" if i == last_good else " "
            marker = c("green", active) if i == last_good else dim(active)
            lines.append(f"  {marker} {e['name']:<12} {e['model']}  @ {dim(e['base'])}")
        lines.insert(0, bold("providers (failover chain):"))
        lines.append(dim(f"  sticky to provider #{last_good}"))
    else:
        # single LLMProvider
        lines.append(bold("provider:"))
        lines.append(f"  • {getattr(provider, 'model', '(unknown)')}  @ {dim(getattr(provider, 'api_base', '(unknown)'))}")
    return "\n".join(lines)


def permission_status(guard) -> str:
    """Render the active permission policy."""
    lines = [bold("permissions:")]
    src = getattr(guard, "source", "builtin")
    if guard.allow_tools:
        lines.append(f"  allow tools: {guard.allow_tools}")
    if guard.write_root:
        lines.append(f"  write root:  {guard.write_root}")
    if guard.yolo:
        lines.append(c("red", "  YOLO mode: confirmations skipped"))
    if getattr(guard, "bash_blocklist", None):
        lines.append(dim(f"  bash blocklist: {len(guard.bash_blocklist)} patterns"))
    lines.append(dim(f"  source: {src}"))
    return "\n".join(lines)


def mcp_status(mcp) -> str:
    """Render connected MCP servers."""
    lines = [bold("mcp servers:")]
    if mcp is None or not getattr(mcp, "servers", None):
        lines.append(dim("  (none attached)"))
        return "\n".join(lines)
    for s in mcp.servers:
        state = "connected" if s.connected else "down"
        color = "green" if s.connected else "red"
        n_tools = len(s.get_schemas()) if s.connected else 0
        lines.append(f"  • {s.name:<12} {c(color, state)}  ({n_tools} tools)")
    return "\n".join(lines)


def inspector_report(agent) -> str:
    """Full configuration snapshot for the /config command."""
    parts = [
        provider_status(agent.provider),
        permission_status(agent._permissions),
        mcp_status(agent.mcp),
    ]
    return "\n\n".join(p for p in parts if p)
