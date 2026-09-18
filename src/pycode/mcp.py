"""MCP (Model Context Protocol) server client for external tool plugins.

pycode can connect to an MCP server (stdio-based) and use its exposed tools as
if they were native. The MCP protocol uses JSON-RPC 2.0:

  1. initialize      - handshake, agree on capabilities
  2. tools/list      - discover available tools
  3. tools/call      - invoke a tool by name

pycode wraps these as OpenAI-style function schemas so the agent can call
external tools alongside its built-in ones.

The MCP server is launched as a subprocess and communicated with over
stdin/stdout using line-delimited JSON.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, List, Optional

from pycode.tools import TOOL_SCHEMAS

_MCP_VERSION = "2024-11-05"


class MCPServer:
    """Client for a single MCP stdio server (JSON-RPC over newline-delimited)."""

    def __init__(
        self, name: str, command: List[str], env: Optional[Dict[str, str]] = None
    ):
        self.name = name
        self.command = command
        self.env = env
        self._proc: Optional[subprocess.Popen] = None
        self._id = 0
        self._tools: List[Dict[str, Any]] = []
        self._connected = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch the server process and initialize."""
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.env,
        )
        self._rpc(
            "initialize",
            {
                "protocolVersion": _MCP_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pycode", "version": "0.2.0"},
            },
        )
        # initialized notification (no response expected)
        self._notify("notifications/initialized", {})
        self._tools = self._list_tools()
        self._connected = True

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------
    # JSON-RPC plumbing
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _rpc(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params,
        }
        return self._send(req, expect_response=True)

    def _notify(self, method: str, params: Dict[str, Any]) -> None:
        req = {"jsonrpc": "2.0", "method": method, "params": params}
        self._send(req, expect_response=False)

    def _send(self, req: Dict[str, Any], expect_response: bool) -> Dict[str, Any]:
        assert self._proc and self._proc.stdin and self._proc.stdout
        self._proc.stdin.write(json.dumps(req) + "\n")
        self._proc.stdin.flush()
        if not expect_response:
            return {}
        line = self._proc.stdout.readline()
        if not line:
            raise ConnectionError(f"MCP server '{self.name}' closed unexpectedly")
        return json.loads(line)

    # ------------------------------------------------------------------
    # Tool discovery + invocation
    # ------------------------------------------------------------------

    def _list_tools(self) -> List[Dict[str, Any]]:
        resp = self._rpc("tools/list", {})
        tools = resp.get("result", {}).get("tools", [])
        self._tools = tools
        return tools

    def get_schemas(self) -> List[Dict[str, Any]]:
        """Return OpenAI-compatible function schemas for this server's tools."""
        schemas: List[Dict[str, Any]] = []
        for t in self._tools:
            name = t.get("name", "")
            if not name:
                continue
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": f"mcp_{self.name}_{name}",
                        "description": t.get(
                            "description", f"MCP tool {name} on {self.name}"
                        ),
                        "parameters": t.get(
                            "inputSchema", {"type": "object", "properties": {}}
                        ),
                    },
                }
            )
        return schemas

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Call an MCP tool. `tool_name` may be prefixed with the server name."""
        # strip the "mcp_<server>_" prefix if present
        prefix = f"mcp_{self.name}_"
        if tool_name.startswith(prefix):
            tool_name = tool_name[len(prefix) :]
        resp = self._rpc("tools/call", {"name": tool_name, "arguments": arguments})
        result = resp.get("result", {})
        # MCP tools/call returns {content: [{type:"text", text:"..."}], ...}
        contents = result.get("content", [])
        parts = []
        for c in contents:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif isinstance(c, str):
                parts.append(c)
        if result.get("isError"):
            return json.dumps({"error": "MCP tool error", "detail": parts})
        return "\n".join(parts) if parts else json.dumps(result, default=str)


class MCPRegistry:
    """Manages multiple MCP servers and merges their tool schemas."""

    def __init__(self):
        self.servers: List[MCPServer] = []

    def add(
        self, name: str, command: List[str], env: Optional[Dict[str, str]] = None
    ) -> MCPServer:
        server = MCPServer(name, command, env)
        self.servers.append(server)
        return server

    def connect_all(self) -> None:
        for s in self.servers:
            s.start()

    def disconnect_all(self) -> None:
        for s in self.servers:
            s.stop()

    def all_schemas(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for s in self.servers:
            if s.connected:
                out.extend(s.get_schemas())
        return out

    def dispatch(self, name: str, args: Dict[str, Any]) -> Optional[str]:
        """Try to route a tool call to an MCP server. Returns None if no server owns it."""
        for s in self.servers:
            if not s.connected:
                continue
            prefix = f"mcp_{s.name}_"
            if name.startswith(prefix):
                clean_name = name[len(prefix) :]
                return s.call_tool(clean_name, args)
        return None
