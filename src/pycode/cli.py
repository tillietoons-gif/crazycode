"""CLI entry point for pycode - interactive REPL with readline support."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys

from pycode.agent import Agent
from pycode.mcp import MCPRegistry
from pycode.providers import PRESETS, get_preset, detect_preset
from pycode.session import latest_session
from pycode.tui import print_banner, c, bold, dim, result_badge
from pycode.tui_commands import install_completion, canonicalize, help_text
from pycode.tui_markdown import print_markdown
from pycode.tui_feed import ActivityFeed
from pycode.tui_statusbar import update_status
from pycode.tui_diff_review import InteractiveDiffReviewer
from pycode.tui_session_picker import pick_session
from pycode.tui_context_view import print_context
from pycode.tui_inspector import inspector_report
from pycode.tui_pager import paginate_or_print
from pycode.session_export import export_to_html
from pycode.onboarding import has_any_credential, onboarding_message, should_show_onboarding, mark_onboarded


def _print_config(cfg: dict) -> None:
    model = cfg.get("model", "(default)")
    base = cfg.get("api_base", "(default)")
    key = cfg.get("api_key")
    key_status = "set" if key else "NOT SET"
    print(f"  Model:   {model}")
    print(f"  Base:    {base}")
    print(f"  API key: {key_status}")
    print()


def _confirm_prompt(tool: str, args: dict) -> bool:
    """Interactive confirmation for destructive tool calls."""
    summary = ""
    if tool == "bash":
        summary = args.get("command", "")
    elif tool == "write":
        summary = f"write -> {args.get('path','?')}"
    elif tool == "edit":
        summary = f"edit {args.get('path','?')} (old={args.get('old_string','')[:60]!r})"
    print(f"\n{bold(c('red', '[CONFIRM]'))} Destructive action: {summary}", file=sys.stderr)
    try:
        answer = input("  approve? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


def _build_mcp_registry(specs) -> MCPRegistry:
    """Build an MCPRegistry from a list of JSON/JSONL MCP server specs.

    Each spec is a JSON object: {"name": "x", "command": ["npx","..."]}
    (either one object per line, or an array of objects).
    """
    reg = MCPRegistry()
    for path in specs:
        with open(path, encoding="utf-8") as fh:
            text = fh.read().strip()
        if not text:
            continue
        # Array form
        if text.startswith("["):
            entries = json.loads(text)
        else:
            # JSONL: one object per line
            entries = [json.loads(line) for line in text.splitlines() if line.strip()]
        for entry in entries:
            name = entry.get("name")
            command = entry.get("command")
            if not name or not command:
                print(f"{c('yellow','warn:')} MCP spec missing name/command: {entry}", file=sys.stderr)
                continue
            if isinstance(command, str):
                command = shlex.split(command)
            reg.add(name, command, env=entry.get("env"))
    return reg


def main() -> None:
    parser = argparse.ArgumentParser(description="pycode - Python AI Coding Agent")
    parser.add_argument("prompt", nargs="*", help="Optional initial prompt (if omitted, interactive mode)")
    parser.add_argument("--api-key", help="LLM API key (or set PYCODE_API_KEY)")
    parser.add_argument("--api-base", help="LLM API base URL (or set PYCODE_API_BASE)")
    parser.add_argument("--model", help="Model name, or a preset (openai/anthropic/ollama/venice/openrouter)")
    parser.add_argument("--preset", choices=list(PRESETS) + ["auto"], default="auto",
                        help="Provider preset (default: auto-detect from env)")
    parser.add_argument("--temperature", type=float, help="Sampling temperature")
    parser.add_argument("--max-tokens", type=int, help="Max response tokens")
    parser.add_argument("--system-prompt-file", help="Path to extra system instructions")
    parser.add_argument("--max-iterations", type=int, default=30, help="Max tool-loop iterations per prompt")
    parser.add_argument("--quiet", action="store_true", help="Suppress stderr progress output")
    parser.add_argument("--non-interactive", action="store_true", help="Run prompt then exit (no REPL)")
    parser.add_argument("--auto-approve", action="store_true",
                        help="Auto-approve destructive tools (skip confirmations)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview write/edit changes as diffs instead of applying")
    parser.add_argument("--context-budget", type=int, default=60000,
                        help="Context token budget for auto-trimming (default: 60000)")
    parser.add_argument("--resume", metavar="FILE",
                        help="Resume from a saved JSONL session file")
    parser.add_argument("--project-root", default=os.getcwd(),
                        help="Project root for context file discovery (default: cwd)")
    parser.add_argument("--mcp", metavar="FILE", action="append", default=[],
                        help="MCP server config (JSONL/JSON), repeatable. "
                             "e.g. --mcp mcp_servers.jsonl")
    parser.add_argument("--permissions", metavar="FILE",
                        help="Path to a permissions.toml policy file")
    parser.add_argument("--yolo", action="store_true",
                        help="YOLO mode: skip all confirmations (dangerous)")
    parser.add_argument("--enable-subagents", action="store_true",
                        help="Expose the `task` subagent tool to the LLM")
    parser.add_argument("--failover", metavar="FILE",
                        help="Provider failover config JSON: {\"providers\": [{\"api_key\",\"api_base\",\"model\",\"name\"}],\"default\":0}")
    parser.add_argument("--cost", action="store_true",
                        help="Show token/cost accounting on every turn")
    parser.add_argument("--no-cost", action="store_true",
                        help="Disable cost accounting entirely")
    parser.add_argument("--plain", action="store_true",
                        help="Disable fancy TUI (markdown, status bar, feed, keyboard review) "
                             "- plain text output only")
    parser.add_argument("--no-tab-complete", action="store_true",
                        help="Disable slash-command tab completion")
    parser.add_argument("--export-html", metavar="FILE",
                        help="Export the session to a styled HTML file when done")
    parser.add_argument("--force-onboard", action="store_true",
                        help="Show the first-run onboarding even if a key is present")

    args = parser.parse_args()
    # Plain mode disables all fancy TUI enhancements
    args.use_tui = not args.plain and sys.stderr.isatty()

    # Determine provider config: preset takes priority over raw env, but explicit
    # --api-key/--api-base/--model flags override everything.
    env_cfg = Agent.load_env_config()
    if args.preset == "auto":
        preset_cfg = detect_preset()
    else:
        preset_cfg = get_preset(args.preset)

    cfg = {
        "api_key": args.api_key or env_cfg.get("api_key") or preset_cfg.get("api_key"),
        "api_base": args.api_base or env_cfg.get("api_base") or preset_cfg.get("api_base"),
        "model": args.model or env_cfg.get("model") or preset_cfg.get("model"),
        "temperature": args.temperature if args.temperature is not None else env_cfg.get("temperature"),
        "max_tokens": args.max_tokens if args.max_tokens is not None else env_cfg.get("max_tokens"),
        "system_prompt_extra": args.system_prompt_file and open(args.system_prompt_file).read()
                               or env_cfg.get("system_prompt_extra", ""),
    }

    agent = Agent(
        api_key=cfg["api_key"],
        api_base=cfg["api_base"],
        model=cfg["model"],
        temperature=cfg["temperature"],
        max_tokens=cfg["max_tokens"],
        system_prompt_extra=cfg.get("system_prompt_extra", ""),
        project_root=args.project_root,
        auto_approve=args.auto_approve or args.yolo,
        dry_run=args.dry_run,
        max_iterations=args.max_iterations,
        max_context_tokens=args.context_budget,
        verbose=not args.quiet,
    )

    # Wire up confirmation unless auto-approve / yolo is on
    if not args.auto_approve and not args.yolo and not args.non_interactive and sys.stdin.isatty():
        agent.set_confirm(_confirm_prompt)

    # Load a custom permissions policy if requested
    if args.permissions:
        from pycode.permissions import PermissionGuard, make_permission_confirm
        guard = PermissionGuard.from_file(args.permissions)
        agent._permissions = guard
        if not args.yolo and sys.stdin.isatty():
            agent.set_confirm(make_permission_confirm(guard, prompt=_confirm_prompt))
        if not args.quiet:
            print(dim(f"  permissions: {guard.source}"), file=sys.stderr)

    # Show loaded permission policy (auto-discovered) even without a flag
    if not args.quiet and agent._permissions.source != "builtin":
        print(dim(f"  permissions: {agent._permissions.source}"), file=sys.stderr)

    # Attach MCP servers if provided
    if args.mcp:
        registry = _build_mcp_registry(args.mcp)
        registry.connect_all()
        agent.attach_mcp(registry)
        if not args.quiet:
            n_tools = len(registry.all_schemas())
            print(dim(f"  mcp: {len(registry.servers)} server(s), {n_tools} tool(s)"), file=sys.stderr)

    # Attach multi-provider failover if a config file is given
    if args.failover:
        from pycode.failover import FailoverProvider, ProviderConfig
        with open(args.failover, encoding="utf-8") as fh:
            spec = json.load(fh)
        provs = [
            ProviderConfig(
                api_key=p.get("api_key", ""),
                api_base=p.get("api_base", ""),
                model=p.get("model", "gpt-4o"),
                name=p.get("name", ""),
                temperature=p.get("temperature"),
                max_tokens=p.get("max_tokens"),
            )
            for p in spec.get("providers", [])
        ]
        if not provs:
            print(f"{c('yellow','warn:')} no providers in {args.failover}", file=sys.stderr)
        else:
            failover = FailoverProvider(provs, default=spec.get("default", 0))
            agent.attach_failover(failover)
            if not args.quiet:
                print(dim(f"  failover: {len(provs)} provider(s): " + ", ".join(p.name for p in provs)), file=sys.stderr)

    # Cost accounting toggle
    if args.no_cost:
        agent.cost_tracker = None  # type: ignore[assignment]

    # Install slash-command tab completion (TTY only)
    if args.use_tui and not args.no_tab_complete and sys.stdin.isatty():
        install_completion()

    if not cfg.get("api_key"):
        print("Warning: No API key found.", file=sys.stderr)
        # First-run onboarding: show copy-pasteable setup guidance once
        if should_show_onboarding(force=args.force_onboard):
            print(c("yellow", "  \U0001f680 first run - no LLM key detected"), file=sys.stderr)
            print(onboarding_message(), file=sys.stderr)
            mark_onboarded()
        else:
            print("  Set PYCODE_API_KEY or --api-key, or use a preset with its env key.", file=sys.stderr)

    print_banner()
    _print_config({k: v for k, v in cfg.items() if v})

    # Show loaded project context
    if agent._loaded_context_files:
        for f in agent._loaded_context_files:
            print(dim(f"  context: {f}"), file=sys.stderr)
        print(file=sys.stderr)

    # Resume from a saved session if requested
    if args.resume:
        try:
            msgs = agent.restore_session(args.resume)
            agent.messages = [agent.messages[0]] + msgs
            print(dim(f"  resumed session with {len(msgs)} messages"), file=sys.stderr)
        except FileNotFoundError as e:
            print(f"  {c('red','error:')} {e}", file=sys.stderr)
        except Exception as e:
            print(f"  {c('red','error')} resuming session: {e}", file=sys.stderr)

    # Determine the prompt
    prompt_parts = " ".join(args.prompt).strip()

    if prompt_parts:
        interactive_review = args.dry_run and not args.non_interactive and sys.stdin.isatty()
        result = agent.run(prompt_parts, interactive_review=interactive_review)
        print(result)
        if args.non_interactive or not sys.stdin.isatty():
            return

    # Interactive REPL
    try:
        import readline  # noqa: F401
    except ImportError:
        pass

    print("\nEntering interactive mode. Type 'quit' or 'exit' to stop.\n", file=sys.stderr)

    while True:
        try:
            user_input = input(">>> ")
        except (EOFError, KeyboardInterrupt):
            print("\nBye.", file=sys.stderr)
            break

        user_input = user_input.strip()
        # canonicalize aliases (":s" -> "/save", "??" -> "/help", etc.)
        user_input = canonicalize(user_input)
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q", "/quit"):
            print("Bye.", file=sys.stderr)
            break

        # Built-in slash/colon commands
        if user_input == "/clear" or user_input.lower() == "clear":
            agent.clear()
            print("Conversation cleared.", file=sys.stderr)
            continue
        if user_input in ("/help", ":help", "help", "?"):
            print(help_text(), file=sys.stderr)
            usage = agent.context_usage()
            print(
                f"\n  context: {usage['used']}/{usage['budget']} tokens ({usage['pct']}%)\n"
                f"  presets: {', '.join(PRESETS)}\n",
                file=sys.stderr,
            )
            continue
        if user_input.startswith("/context"):
            usage = agent.context_usage()
            if args.use_tui:
                print_context(usage["used"], usage["budget"], usage["messages"], use_color=True)
            else:
                print(
                    f"  context window: {usage['used']}/{usage['budget']} tokens "
                    f"({usage['pct']}%), {usage['messages']} messages",
                    file=sys.stderr,
                )
            continue
        if user_input.startswith("/preset"):
            print("  presets: " + ", ".join(PRESETS), file=sys.stderr)
            continue
        if user_input.startswith("/rewind"):
            pts = agent.rewind_points()
            if not pts:
                print("No checkpoints recorded yet.", file=sys.stderr)
                continue
            parts = user_input.split(None, 1)
            if len(parts) > 1:
                try:
                    target = int(parts[1].strip())
                except ValueError:
                    target = None
                    print("usage: /rewind <checkpoint-index>", file=sys.stderr)
            else:
                target = None
            if target is None:
                print("\n  Rewind points:", file=sys.stderr)
                for p in pts:
                    print(f"    {p}", file=sys.stderr)
                print("  Usage: /rewind <index> (optional note after)", file=sys.stderr)
                continue
            if target >= len(agent.rewinder.checkpoints):
                print(f"checkpoint {target} out of range (0-{len(agent.rewinder.checkpoints)-1})", file=sys.stderr)
                continue
            agent.rewind(target, note="rewound by user")
            print(f"Rewound to checkpoint {target}.", file=sys.stderr)
            continue
        if user_input.startswith("/new-context") or user_input.startswith("/context-file"):
            from pycode.scaffold import generate, exists
            target = "CLAUDE.md"
            try:
                path = generate(name=target, root=args.project_root, overwrite=True)
                print(f"Wrote {path} (edit it to add your project's conventions)", file=sys.stderr)
            except Exception as e:
                print(f"error generating context file: {e}", file=sys.stderr)
            continue
        if user_input.startswith("/save"):
            parts = user_input.split(None, 1)
            target = parts[1].strip() if len(parts) > 1 else None
            path = agent.save_session(target)
            print(f"Session saved: {path}", file=sys.stderr)
            continue
        if user_input.startswith("/resume"):
            parts = user_input.split(None, 1)
            target = parts[1].strip() if len(parts) > 1 else None
            if not target:
                target = latest_session(args.project_root)
                if not target:
                    print("No saved sessions found.", file=sys.stderr)
                    continue
            try:
                msgs = agent.restore_session(target)
                agent.messages = [agent.messages[0]] + msgs
                print(f"Resumed session: {target} ({len(msgs)} messages)", file=sys.stderr)
            except Exception as e:
                print(f"Error resuming: {e}", file=sys.stderr)
            continue
        if user_input.startswith("/sessions"):
            # interactive picker on a TTY, plain list otherwise
            if args.use_tui and sys.stdin.isatty():
                chosen = pick_session(args.project_root, tty=True)
                if chosen:
                    _resume_meta = None
                    try:
                        msgs = agent.restore_session(chosen.path)
                        agent.messages = [agent.messages[0]] + msgs
                        print(f"Resumed session: {chosen.short_name} ({len(msgs)} messages)", file=sys.stderr)
                    except Exception as e:
                        print(f"Error resuming {chosen.short_name}: {e}", file=sys.stderr)
            else:
                from pycode.session import list_sessions
                sessions = list_sessions(args.project_root)
                if not sessions:
                    print("No saved sessions in .pycode-sessions/.", file=sys.stderr)
                else:
                    for s in sessions[:10]:
                        print(f"  {s}", file=sys.stderr)
            continue
        if user_input.startswith("/cost"):
            rep = agent.cost_report()
            if rep.get("disabled"):
                print("  cost accounting disabled (--no-cost)", file=sys.stderr)
            else:
                print(
                    f"  model: {rep['model']}\n"
                    f"  session: {rep['session_tokens']['total_tokens']} tokens, "
                    f"~${rep['session_cost_usd']:.4f} over {rep['turns']} turn(s)\n"
                    f"  last turn: {rep['last_turn_tokens']['total_tokens']} tokens, "
                    f"~${rep['last_turn_cost_usd']:.4f}",
                    file=sys.stderr,
                )
            continue
        if user_input.startswith("/config") or user_input.startswith("/inspect"):
            print(inspector_report(agent), file=sys.stderr)
            continue
        if user_input.startswith("/export"):
            parts = user_input.split(None, 1)
            default_name = "pycode-session.html"
            target = parts[1].strip() if len(parts) > 1 and parts[1].strip() else default_name
            try:
                path = export_to_html(agent.messages, target, title="pycode session")
                print(f"Exported session to {path} ({len(agent.messages)} messages)", file=sys.stderr)
            except Exception as e:
                print(f"Error exporting session: {e}", file=sys.stderr)
            continue

        # Rewind commands
        if user_input.startswith("/rewind"):
            parts = user_input.split(None, 1)
            idx = parts[1].strip() if len(parts) > 1 else ""
            points = agent.rewind_points()
            if not points:
                print("No checkpoints recorded yet.", file=sys.stderr)
                continue
            if idx in ("", "list", "points"):
                for p in points:
                    print(f"  {p}", file=sys.stderr)
                continue
            try:
                target = int(idx)
                note = input("  rewind note (optional, Enter to skip): ").strip() or None
                count = agent.rewind(target, note=note)
                print(f"Rewound to checkpoint #{target} ({count} messages).", file=sys.stderr)
            except ValueError:
                print(f"invalid checkpoint index: {idx}", file=sys.stderr)
            except IndexError:
                print(f"checkpoint #{idx} out of range", file=sys.stderr)
            continue
        if user_input.startswith("/branch"):
            parts = user_input.split(None, 1)
            if len(parts) < 2:
                print("usage: /branch <checkpoint-index> <new instruction>", file=sys.stderr)
                continue
            try:
                target = int(parts[1].split()[0])
                new_input = " ".join(parts[1].split()[1:])
                count = agent.branch_from(target, new_input)
                print(f"Branch created from #{target} ({count} messages).", file=sys.stderr)
            except (ValueError, IndexError):
                print("invalid /branch syntax: /branch <index> <instruction>", file=sys.stderr)
            continue

        # Show the activity feed after each turn (TUI mode only)
        if args.use_tui and agent.feed.entries:
            print(dim(agent.feed.last_n(6)), file=sys.stderr)

        # Interactive keyboard diff review when in TUI + dry-run mode
        interactive_review = (
            args.dry_run and args.use_tui and sys.stdin.isatty() and not args.non_interactive
        )
        result = agent.run(
            user_input,
            interactive_review=interactive_review,
            use_tui=args.use_tui,
        )

        # Render the agent's final response: markdown-highlighted in TUI mode,
        # plain text otherwise.
        if args.use_tui:
            print_markdown(result, use_color=True)
        else:
            print(result, "\n")

        # Per-turn cost + live status bar when enabled
        if args.cost and agent.cost_tracker is not None:
            rep = agent.cost_report()
            print(dim(f"  [cost] last turn ~${rep['last_turn_cost_usd']:.4f} | "
                      f"session ~${rep['session_cost_usd']:.4f}"), file=sys.stderr)
        if args.use_tui and agent.cost_tracker is not None:
            usage = agent.context_usage()
            rep = agent.cost_report()
            update_status(
                iteration=1,
                max_iterations=agent.max_iterations,
                ctx_used=usage["used"],
                ctx_budget=usage["budget"],
                cost_session=rep.get("session_cost_usd", 0.0),
                model=rep.get("model", agent.provider.model),
                use_color=True,
            )


if __name__ == "__main__":
    main()
