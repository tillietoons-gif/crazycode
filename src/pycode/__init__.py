"""pycode - Python AI Coding Agent (Claude Code alternative)."""

__version__ = "0.3.0"

from pycode.agent import Agent
from pycode.cli import main
from pycode.context import load_context, find_context_files
from pycode.context_manager import trim_messages, conversation_tokens, context_stats
from pycode.mcp import MCPRegistry, MCPServer
from pycode.provider import LLMProvider, LLMProviderError
from pycode.providers import PRESETS, get_preset, detect_preset
from pycode.session import save_session, load_session, latest_session, list_sessions
from pycode.tools import TOOL_SCHEMAS, TOOLS

__all__ = [
    "Agent", "LLMProvider", "LLMProviderError", "main", "TOOLS", "TOOL_SCHEMAS",
    "PRESETS", "get_preset", "detect_preset",
    "load_context", "find_context_files",
    "trim_messages", "conversation_tokens", "context_stats",
    "MCPRegistry", "MCPServer",
    "save_session", "load_session", "latest_session", "list_sessions",
    "__version__",
]

