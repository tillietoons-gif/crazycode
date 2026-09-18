"""pycode - Python AI Coding Agent (Claude Code alternative)."""

__version__ = "1.2.1"

from pycode.agent import Agent
from pycode.cli import main
from pycode.config import (find_config_files, load_config, parse_toml,
                           project_config_path)
from pycode.context import find_context_files, load_context
from pycode.context_manager import (context_stats, conversation_tokens,
                                    trim_messages)
from pycode.cost import CostTracker, TokenUsage, pricing_for
from pycode.diff_reviewer import DiffReviewer, render_diff, review_single
from pycode.failover import FailoverProvider, ProviderConfig
from pycode.hooks import HookRunner, hook_context
from pycode.index import ProjectIndex, extract_symbols
from pycode.interrupts import (AbortController, Aborted, EscListener,
                               check_abort, new_controller)
from pycode.jobs import JobManager
from pycode.mcp import MCPRegistry, MCPServer
from pycode.onboarding import has_any_credential, onboarding_message
from pycode.permissions import PermissionGuard, make_permission_confirm
from pycode.plugins import (apply_command, load_user_commands, load_user_tools,
                            register_user_tools)
from pycode.provider import (LLMProvider, LLMProviderError,
                             build_cached_system_messages)
from pycode.providers import PRESETS, detect_preset, get_preset
from pycode.rewind import Checkpoint, RewindManager
from pycode.scaffold import generate as generate_context
from pycode.scaffold import render_template
from pycode.session import (latest_session, list_sessions, load_session,
                            save_session)
from pycode.session_export import export_to_html
from pycode.subagents import Subagent, SubagentRegistry
from pycode.tools import TOOL_SCHEMAS, TOOLS
from pycode.tui import render_linear_dashboard
from pycode.tui_commands import canonicalize, help_text, install_completion
from pycode.tui_context_view import (context_bar, print_context,
                                     render_context_view)
from pycode.tui_diff_review import InteractiveDiffReviewer
from pycode.tui_feed import ActivityFeed
from pycode.tui_inspector import (inspector_report, mcp_status,
                                  permission_status, provider_status)
from pycode.tui_markdown import highlight_code, render_markdown
from pycode.tui_pager import paginate_or_print
from pycode.tui_session_picker import collect_sessions, pick_session
from pycode.tui_statusbar import build_status_line, update_status
from pycode.tui_subagent_trace import SubagentTrace
from pycode.tui_theme import (THEMES, apply_theme, available_themes,
                              resolve_theme)

__all__ = [
    "Agent",
    "LLMProvider",
    "LLMProviderError",
    "build_cached_system_messages",
    "main",
    "TOOLS",
    "TOOL_SCHEMAS",
    "PRESETS",
    "get_preset",
    "detect_preset",
    "load_config",
    "parse_toml",
    "find_config_files",
    "project_config_path",
    "Aborted",
    "AbortController",
    "EscListener",
    "check_abort",
    "new_controller",
    "apply_theme",
    "available_themes",
    "resolve_theme",
    "THEMES",
    "render_linear_dashboard",
    "JobManager",
    "HookRunner",
    "hook_context",
    "ProjectIndex",
    "extract_symbols",
    "load_user_tools",
    "load_user_commands",
    "register_user_tools",
    "apply_command",
    "load_context",
    "find_context_files",
    "trim_messages",
    "conversation_tokens",
    "context_stats",
    "CostTracker",
    "TokenUsage",
    "pricing_for",
    "DiffReviewer",
    "render_diff",
    "review_single",
    "FailoverProvider",
    "ProviderConfig",
    "has_any_credential",
    "onboarding_message",
    "PermissionGuard",
    "make_permission_confirm",
    "MCPRegistry",
    "MCPServer",
    "RewindManager",
    "Checkpoint",
    "Subagent",
    "SubagentRegistry",
    "SubagentTrace",
    "generate_context",
    "render_template",
    "save_session",
    "load_session",
    "latest_session",
    "list_sessions",
    "export_to_html",
    "install_completion",
    "canonicalize",
    "help_text",
    "context_bar",
    "render_context_view",
    "print_context",
    "inspector_report",
    "provider_status",
    "permission_status",
    "mcp_status",
    "InteractiveDiffReviewer",
    "ActivityFeed",
    "paginate_or_print",
    "pick_session",
    "collect_sessions",
    "render_markdown",
    "highlight_code",
    "update_status",
    "build_status_line",
    "__version__",
]
