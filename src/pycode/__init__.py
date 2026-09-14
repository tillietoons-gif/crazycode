"""pycode - Python AI Coding Agent (Claude Code alternative)."""

__version__ = "0.7.0"

from pycode.agent import Agent
from pycode.cli import main
from pycode.context import load_context, find_context_files
from pycode.context_manager import trim_messages, conversation_tokens, context_stats
from pycode.cost import CostTracker, TokenUsage, pricing_for
from pycode.diff_reviewer import DiffReviewer, render_diff, review_single
from pycode.failover import FailoverProvider, ProviderConfig
from pycode.mcp import MCPRegistry, MCPServer
from pycode.permissions import PermissionGuard, make_permission_confirm
from pycode.provider import LLMProvider, LLMProviderError, build_cached_system_messages
from pycode.providers import PRESETS, get_preset, detect_preset
from pycode.rewind import RewindManager, Checkpoint
from pycode.scaffold import generate as generate_context, render_template
from pycode.session import save_session, load_session, latest_session, list_sessions
from pycode.subagents import Subagent, SubagentRegistry
from pycode.tools import TOOL_SCHEMAS, TOOLS
from pycode.tui_commands import install_completion, canonicalize, help_text
from pycode.tui_diff_review import InteractiveDiffReviewer
from pycode.tui_feed import ActivityFeed
from pycode.tui_markdown import render_markdown, highlight_code
from pycode.tui_statusbar import update_status, build_status_line

__all__ = [
    "Agent", "LLMProvider", "LLMProviderError", "build_cached_system_messages", "main", "TOOLS", "TOOL_SCHEMAS",
    "PRESETS", "get_preset", "detect_preset",
    "load_context", "find_context_files",
    "trim_messages", "conversation_tokens", "context_stats",
    "CostTracker", "TokenUsage", "pricing_for",
    "DiffReviewer", "render_diff", "review_single",
    "FailoverProvider", "ProviderConfig",
    "PermissionGuard", "make_permission_confirm",
    "MCPRegistry", "MCPServer",
    "RewindManager", "Checkpoint",
    "Subagent", "SubagentRegistry",
    "generate_context", "render_template",
    "save_session", "load_session", "latest_session", "list_sessions",
    "install_completion", "canonicalize", "help_text",
    "InteractiveDiffReviewer", "ActivityFeed",
    "render_markdown", "highlight_code",
    "update_status", "build_status_line",
    "__version__",
]

