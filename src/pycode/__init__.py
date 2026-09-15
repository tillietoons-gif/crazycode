"""pycode - Python AI Coding Agent (Claude Code alternative)."""

__version__ = "1.1.1"

from pycode.agent import Agent
from pycode.cli import main
from pycode.config import load_config, parse_toml, find_config_files, project_config_path
from pycode.context import load_context, find_context_files
from pycode.context_manager import trim_messages, conversation_tokens, context_stats
from pycode.cost import CostTracker, TokenUsage, pricing_for
from pycode.diff_reviewer import DiffReviewer, render_diff, review_single
from pycode.failover import FailoverProvider, ProviderConfig
from pycode.hooks import HookRunner, hook_context
from pycode.index import ProjectIndex, extract_symbols
from pycode.interrupts import Aborted, AbortController, EscListener, check_abort, new_controller
from pycode.jobs import JobManager
from pycode.mcp import MCPRegistry, MCPServer
from pycode.plugins import load_user_tools, load_user_commands, register_user_tools, apply_command
from pycode.onboarding import has_any_credential, onboarding_message
from pycode.permissions import PermissionGuard, make_permission_confirm
from pycode.provider import LLMProvider, LLMProviderError, build_cached_system_messages
from pycode.providers import PRESETS, get_preset, detect_preset
from pycode.rewind import RewindManager, Checkpoint
from pycode.scaffold import generate as generate_context, render_template
from pycode.session import save_session, load_session, latest_session, list_sessions
from pycode.session_export import export_to_html
from pycode.subagents import Subagent, SubagentRegistry
from pycode.tools import TOOL_SCHEMAS, TOOLS
from pycode.tui_commands import install_completion, canonicalize, help_text
from pycode.tui_context_view import context_bar, render_context_view, print_context
from pycode.tui_diff_review import InteractiveDiffReviewer
from pycode.tui_feed import ActivityFeed
from pycode.tui_inspector import inspector_report, provider_status, permission_status, mcp_status
from pycode.tui_markdown import render_markdown, highlight_code
from pycode.tui_pager import paginate_or_print
from pycode.tui_session_picker import pick_session, collect_sessions
from pycode.tui_statusbar import update_status, build_status_line
from pycode.tui_subagent_trace import SubagentTrace
from pycode.tui_theme import apply_theme, available_themes, resolve_theme, THEMES

__all__ = [
    "Agent", "LLMProvider", "LLMProviderError", "build_cached_system_messages", "main", "TOOLS", "TOOL_SCHEMAS",
    "PRESETS", "get_preset", "detect_preset",
    "load_config", "parse_toml", "find_config_files", "project_config_path",
    "Aborted", "AbortController", "EscListener", "check_abort", "new_controller",
    "apply_theme", "available_themes", "resolve_theme", "THEMES",
    "JobManager", "HookRunner", "hook_context",
    "ProjectIndex", "extract_symbols",
    "load_user_tools", "load_user_commands", "register_user_tools", "apply_command",
    "load_context", "find_context_files",
    "trim_messages", "conversation_tokens", "context_stats",
    "CostTracker", "TokenUsage", "pricing_for",
    "DiffReviewer", "render_diff", "review_single",
    "FailoverProvider", "ProviderConfig",
    "has_any_credential", "onboarding_message",
    "PermissionGuard", "make_permission_confirm",
    "MCPRegistry", "MCPServer",
    "RewindManager", "Checkpoint",
    "Subagent", "SubagentRegistry", "SubagentTrace",
    "generate_context", "render_template",
    "save_session", "load_session", "latest_session", "list_sessions",
    "export_to_html",
    "install_completion", "canonicalize", "help_text",
    "context_bar", "render_context_view", "print_context",
    "inspector_report", "provider_status", "permission_status", "mcp_status",
    "InteractiveDiffReviewer", "ActivityFeed",
    "paginate_or_print",
    "pick_session", "collect_sessions",
    "render_markdown", "highlight_code",
    "update_status", "build_status_line",
    "__version__",
]

