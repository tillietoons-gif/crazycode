"""CLI entry point for pycode - interactive REPL with readline support."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys

from pycode.agent import Agent
from pycode.config import load_config, find_config_files, coalesce
from pycode import interrupts
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
from pycode.tui_theme import apply_theme, available_themes, current_theme
from pycode.tui_input import read_input
from pycode.tui_turn import print_summary
from pycode.onboarding import has_any_credential, onboarding_message, should_show_onboarding, mark_onboarded
from pycode.wizard import needs_wizard


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


def _run_with_abort(agent: Agent, use_tui: bool, **kwargs) -> str:
    """Run one agent turn, watching for Esc to abort on a TTY."""
    controller = interrupts.new_controller()
    try:
        if use_tui and interrupts.supports_esc():
            with interrupts.EscListener(controller):
                return agent.run(**kwargs)
        return agent.run(**kwargs)
    except interrupts.Aborted:
        return "[aborted by user]"
    finally:
        interrupts.clear_current()


def _run_loop_with_abort(agent: Agent, goal: str, loop_max: int,
                         use_tui: bool = False) -> dict:
    """Run the auto-fix loop with Esc-to-abort enabled."""
    controller = interrupts.new_controller()
    try:
        if use_tui and interrupts.supports_esc():
            with interrupts.EscListener(controller):
                return agent.run_loop(goal, max_iterations=loop_max)
        return agent.run_loop(goal, max_iterations=loop_max)
    except interrupts.Aborted:
        return {"goal": goal, "attempts": 0, "achieved": False,
                "results": [], "error": "aborted by user"}
    finally:
        interrupts.clear_current()


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
    parser.add_argument("--max-iterations", type=int, default=None,
                        help="Max tool-loop iterations per prompt (default: 30)")
    parser.add_argument("--quiet", action="store_true", help="Suppress stderr progress output")
    parser.add_argument("--non-interactive", action="store_true", help="Run prompt then exit (no REPL)")
    parser.add_argument("--auto-approve", action="store_true",
                        help="Auto-approve destructive tools (skip confirmations)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview write/edit changes as diffs instead of applying")
    parser.add_argument("--context-budget", type=int, default=None,
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
    parser.add_argument("--theme", choices=available_themes(), default=None,
                        help="TUI color theme (overrides config file; default: default)")
    parser.add_argument("--no-config", action="store_true",
                        help="Ignore .pycode/config.toml and user config files")
    parser.add_argument("--no-map", action="store_true",
                        help="Disable the project symbol map in the system prompt")
    parser.add_argument("--agent-loop", metavar="GOAL",
                        help="Run the auto-fix loop toward GOAL (plan/do/test/fix up to "
                             "--loop-max attempts), then exit")
    parser.add_argument("--loop-max", type=int, default=None,
                        help="Max auto-fix loop attempts (default: 5)")
    parser.add_argument("--self-review", action="store_true",
                        help="After edits are applied, a second reviewer call checks the "
                             "diff and may request one revision")
    parser.add_argument("--wizard", action="store_true",
                        help="Run the first-run setup wizard (auto-runs on first interactive "
                             "launch without a key)")
    parser.add_argument("--no-wizard", action="store_true",
                        help="Never auto-run the setup wizard")
    parser.add_argument("--no-tab-complete", action="store_true",
                        help="Disable slash-command tab completion")
    parser.add_argument("--export-html", metavar="FILE",
                        help="Export the session to a styled HTML file when done")
    parser.add_argument("--force-onboard", action="store_true",
                        help="Show the first-run onboarding even if a key is present")

    args = parser.parse_args()

    # Layered config: CLI > env > project config > user config > preset defaults
    file_cfg = {} if args.no_config else load_config(args.project_root)
    if file_cfg and not args.quiet:
        for path in find_config_files(args.project_root):
            print(dim(f"  config: {path}"), file=sys.stderr)

    # Fold config-file booleans/defaults into args (CLI flags always win)
    args.auto_approve = args.auto_approve or bool(file_cfg.get("auto_approve"))
    args.quiet = args.quiet or bool(file_cfg.get("quiet"))
    args.plain = args.plain or bool(file_cfg.get("plain"))
    args.cost = args.cost or bool(file_cfg.get("cost"))

    max_iterations = coalesce(args.max_iterations, file_cfg.get("max_iterations"), 30)
    context_budget = coalesce(args.context_budget, file_cfg.get("context_budget"), 60000)
    loop_max = coalesce(args.loop_max, file_cfg.get("loop_max"), 5)
    self_review = args.self_review or bool(file_cfg.get("self_review"))

    # Apply the theme (CLI --theme > config theme > default) before any output
    theme_name = coalesce(args.theme, file_cfg.get("theme"))
    if theme_name:
        try:
            apply_theme(theme_name)
        except ValueError as e:
            print(f"{c('yellow','warn:')} {e}", file=sys.stderr)

    # User plugins: python tools, custom slash commands, tool-file hooks
    from pycode import plugins
    plugin_summary = plugins.register_user_tools(args.project_root)
    if plugin_summary["loaded"] and not args.quiet:
        print(dim(f"  plugins: {len(plugin_summary['loaded'])} user tool(s): "
                  + ", ".join(plugin_summary["loaded"])), file=sys.stderr)
    for err in plugin_summary["errors"]:
        print(f"{c('yellow','warn:')} plugin {os.path.basename(err['path'])}: {err['error']}",
              file=sys.stderr)
    user_commands = plugins.load_user_commands(args.project_root)
    if user_commands and not args.quiet:
        print(dim("  commands: " + ", ".join("/" + n for n in sorted(user_commands))),
              file=sys.stderr)
    from pycode.tui_commands import USER_COMMANDS
    USER_COMMANDS.update("/" + name for name in user_commands)

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
        "api_key": coalesce(args.api_key, env_cfg.get("api_key"), file_cfg.get("api_key"), preset_cfg.get("api_key")),
        "api_base": coalesce(args.api_base,
                             os.getenv("PYCODE_API_BASE") or os.getenv("OPENAI_API_BASE"),
                             file_cfg.get("api_base"),
                             env_cfg.get("api_base"),
                             preset_cfg.get("api_base")),
        "model": coalesce(args.model,
                          os.getenv("PYCODE_MODEL") or os.getenv("OPENAI_MODEL"),
                          file_cfg.get("model"),
                          env_cfg.get("model"),
                          preset_cfg.get("model")),
        "temperature": coalesce(args.temperature, env_cfg.get("temperature"), file_cfg.get("temperature")),
        "max_tokens": coalesce(args.max_tokens, env_cfg.get("max_tokens"), file_cfg.get("max_tokens")),
        "system_prompt_extra": args.system_prompt_file and open(args.system_prompt_file).read()
                               or env_cfg.get("system_prompt_extra", ""),
    }

    # First-run setup wizard: explicit --wizard, or auto on an interactive
    # launch that has no provider key configured anywhere yet.
    if needs_wizard(
        has_api_key=bool(cfg.get("api_key")),
        interactive=sys.stdin.isatty() and not args.non_interactive,
        force=args.wizard,
        no_wizard=args.no_wizard,
    ):
        from pycode.wizard import run_wizard
        wiz = run_wizard(has_api_key=bool(cfg.get("api_key")), root=args.project_root)
        if wiz.get("api_key"):
            cfg["api_key"] = wiz["api_key"]
        if wiz.get("model"):
            cfg["model"] = wiz["model"]
        if wiz.get("config_path"):
            if not args.quiet:
                print(dim(f"  config: {wiz['config_path']}"), file=sys.stderr)

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
        max_iterations=max_iterations,
        max_context_tokens=context_budget,
        verbose=not args.quiet,
        use_project_map=not (args.no_map or bool(file_cfg.get("no_map"))),
        self_review=self_review,
    )

    # Wire up confirmation unless auto-approve / yolo is on
    if not args.auto_approve and not args.yolo and not args.non_interactive and sys.stdin.isatty():
        agent.set_confirm(_confirm_prompt)

    # Lifecycle hooks from the config file's [hooks] section
    from pycode.hooks import HookRunner
    hook_runner = HookRunner.from_config(file_cfg)
    # Tool-file HOOKS dicts merge into the same runner
    user_hook_cmds = plugins.collect_user_hooks(args.project_root)
    if user_hook_cmds:
        hook_runner.commands = plugins.merge_hooks(hook_runner.commands, user_hook_cmds)
    if hook_runner.enabled:
        agent.attach_hooks(hook_runner)
        if not args.quiet:
            n_hooks = sum(len(v) for v in hook_runner.commands.values())
            print(dim(f"  hooks: {n_hooks} registered"), file=sys.stderr)

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

    if args.agent_loop:
        summary = _run_loop_with_abort(agent, args.agent_loop, loop_max,
                                       use_tui=args.use_tui)
        print(bold(f"  auto-fix loop: {summary['attempts']} attempt(s), "
                   f"{'achieved' if summary.get('achieved') else 'not confirmed achieved'}"),
              file=sys.stderr)
        last = (summary.get("results") or [""])[-1]
        if args.use_tui:
            print_markdown(last, use_color=True)
        else:
            print(last, "\n")
        return

    if prompt_parts:
        interactive_review = args.dry_run and not args.non_interactive and sys.stdin.isatty()
        result = _run_with_abort(
            agent, args.use_tui,
            user_input=prompt_parts, interactive_review=interactive_review,
        )
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
            user_input = read_input(use_box=args.use_tui)
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
        if user_input.startswith("/loop"):
            parts = user_input.split(None, 1)
            goal = parts[1].strip() if len(parts) > 1 else ""
            if not goal:
                print(f"usage: /loop <goal>   (auto-fix cycle, up to {loop_max} attempts)", file=sys.stderr)
                continue
            print(dim(f"  auto-fix loop ({loop_max} max attempts): {goal}"), file=sys.stderr)
            summary = _run_loop_with_abort(agent, goal, loop_max, use_tui=args.use_tui)
            if summary.get("error"):
                print(f"  {c('red','error:')} {summary['error']}", file=sys.stderr)
            print(bold(f"  loop finished: {summary['attempts']} attempt(s), "
                       f"{'achieved' if summary.get('achieved') else 'not confirmed achieved'}"),
                  file=sys.stderr)
            last = (summary.get("results") or [""])[-1]
            if args.use_tui:
                print_markdown(last, use_color=True)
            else:
                print(last, "\n")
            continue
        if user_input.startswith("/plan"):
            parts = user_input.split(None, 1)
            goal = parts[1].strip() if len(parts) > 1 else ""
            if not goal:
                print("usage: /plan <goal>", file=sys.stderr)
                continue
            print(dim(f"  planning: {goal}"), file=sys.stderr)
            controller = interrupts.new_controller()
            try:
                if args.use_tui and interrupts.supports_esc():
                    with interrupts.EscListener(controller):
                        summary = agent.run_plan(goal)
                else:
                    summary = agent.run_plan(goal)
            except interrupts.Aborted:
                summary = {"goal": goal, "error": "aborted by user", "results": []}
            finally:
                interrupts.clear_current()
            if summary.get("error"):
                print(f"  {c('red','error:')} {summary['error']}", file=sys.stderr)
            total = len(summary.get("results", []))
            print(dim(f"  executed {total}/{len(summary.get('steps', []))} step(s)"), file=sys.stderr)
            for i, r in enumerate(summary.get("results", []), 1):
                print(bold(f"  step {i}: {r['step']}"), file=sys.stderr)
                if args.use_tui:
                    print_markdown(r["result"], use_color=True)
                else:
                    print(r["result"], "\n")
            continue
        if user_input.startswith("/preset"):
            print("  presets: " + ", ".join(PRESETS), file=sys.stderr)
            continue
        if user_input.startswith("/map"):
            from pycode.tools import get_project_index
            idx = get_project_index(args.project_root)
            if not idx.files:
                stats = idx.build()
                print(dim(f"  indexed {stats['scanned']} file(s)"), file=sys.stderr)
            print(idx.summary(max_chars=8000), file=sys.stderr)
            st = idx.stats()
            print(dim(f"  ({st['files']} files, {st['symbols']} symbols)"), file=sys.stderr)
            continue
        if user_input.startswith("/symbols"):
            parts = user_input.split(None, 1)
            query = parts[1].strip() if len(parts) > 1 else ""
            from pycode.tools import get_project_index
            idx = get_project_index(args.project_root)
            if not idx.files:
                idx.build()
            if not query:
                print("usage: /symbols <name-substring>  (also: /map)", file=sys.stderr)
                continue
            results = idx.find(query)
            if not results:
                print(f"  no symbols matching {query!r}", file=sys.stderr)
            for r in results[:25]:
                print(f"  {r['kind']:<8} {r['name']:<24} {os.path.relpath(r['path'], args.project_root)}:{r['line']}", file=sys.stderr)
            continue
        if user_input.startswith("/theme"):
            parts = user_input.split(None, 1)
            if len(parts) > 1 and parts[1].strip():
                try:
                    apply_theme(parts[1].strip(), force=True)
                    print(f"  theme: {current_theme()}", file=sys.stderr)
                except ValueError as e:
                    print(f"  {c('red','error:')} {e}", file=sys.stderr)
            else:
                print(f"  themes: {', '.join(available_themes())}", file=sys.stderr)
                print(f"  current: {current_theme()}", file=sys.stderr)
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

        # User-defined slash commands (from .pycode/commands/*.md): expand the
        # template and fall through to the normal turn handling
        words = user_input.split()
        cmd_name = words[0].lstrip("/") if words else ""
        if cmd_name in user_commands:
            args_str = user_input[len(words[0]):].strip()
            user_input = plugins.apply_command(user_commands[cmd_name]["template"], args_str)
            print(dim(f"  [{cmd_name}] prompt: {user_input[:120]}"), file=sys.stderr)

        # Show the activity feed after each turn (TUI mode only)
        if args.use_tui and agent.feed.entries:
            print(dim(agent.feed.last_n(6)), file=sys.stderr)

        # Interactive keyboard diff review when in TUI + dry-run mode
        interactive_review = (
            args.dry_run and args.use_tui and sys.stdin.isatty() and not args.non_interactive
        )
        result = _run_with_abort(
            agent,
            user_input=user_input,
            interactive_review=interactive_review,
            use_tui=args.use_tui,
        )

        # Render the agent's final response: markdown-highlighted in TUI mode,
        # plain text otherwise.
        if args.use_tui:
            print_markdown(result, use_color=True)
        else:
            print(result, "\n")

        # Turn summary panel: tool calls, duration, files changed
        if args.use_tui and agent.last_turn:
            print_summary(agent.last_turn)
            print(file=sys.stderr)

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
