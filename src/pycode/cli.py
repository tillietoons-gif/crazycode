"""CLI entry point for pycode - interactive REPL with readline support."""

from __future__ import annotations

import argparse
import os
import sys

from pycode.agent import Agent
from pycode.session import latest_session
from pycode.tui import print_banner, c, bold, dim, result_badge


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
    from pycode.tools import is_destructive
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


def main() -> None:
    parser = argparse.ArgumentParser(description="pycode - Python AI Coding Agent")
    parser.add_argument("prompt", nargs="*", help="Optional initial prompt (if omitted, interactive mode)")
    parser.add_argument("--api-key", help="LLM API key (or set PYCODE_API_KEY)")
    parser.add_argument("--api-base", help="LLM API base URL (or set PYCODE_API_BASE)")
    parser.add_argument("--model", help="Model name (or set PYCODE_MODEL)")
    parser.add_argument("--temperature", type=float, help="Sampling temperature")
    parser.add_argument("--max-tokens", type=int, help="Max response tokens")
    parser.add_argument("--system-prompt-file", help="Path to extra system instructions")
    parser.add_argument("--max-iterations", type=int, default=30, help="Max tool-loop iterations per prompt")
    parser.add_argument("--quiet", action="store_true", help="Suppress stderr progress output")
    parser.add_argument("--non-interactive", action="store_true", help="Run prompt then exit (no REPL)")
    parser.add_argument("--auto-approve", action="store_true",
                        help="Auto-approve destructive tools (skip confirmations)")
    parser.add_argument("--resume", metavar="FILE",
                        help="Resume from a saved JSONL session file")
    parser.add_argument("--project-root", default=os.getcwd(),
                        help="Project root for context file discovery (default: cwd)")

    args = parser.parse_args()

    # Load config from env, overlay with CLI flags
    env_cfg = Agent.load_env_config()
    cfg = {
        "api_key": args.api_key or env_cfg.get("api_key"),
        "api_base": args.api_base or env_cfg.get("api_base"),
        "model": args.model or env_cfg.get("model"),
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
        auto_approve=args.auto_approve,
        max_iterations=args.max_iterations,
        verbose=not args.quiet,
    )

    # Wire up confirmation unless auto-approve is on
    if not args.auto_approve and not args.non_interactive and sys.stdin.isatty():
        agent.set_confirm(_confirm_prompt)

    if not cfg.get("api_key"):
        print("Warning: No API key found.", file=sys.stderr)
        print("  Set PYCODE_API_KEY or --api-key, or set OPENAI_API_KEY.", file=sys.stderr)

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
        result = agent.run(prompt_parts)
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
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Bye.", file=sys.stderr)
            break

        # Built-in slash/colon commands
        if user_input == ":clear" or user_input.lower() == "clear":
            agent.clear()
            print("Conversation cleared.", file=sys.stderr)
            continue
        if user_input in (":help", "/help", "help", "?"):
            print(
                "  commands:\n"
                "    :clear          reset conversation\n"
                "    /save [file]    save session to JSONL (or auto-named)\n"
                "    /resume [file]  load a saved session (latest if omitted)\n"
                "    /sessions       list saved sessions\n"
                "    quit / exit    stop\n",
                file=sys.stderr,
            )
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
            from pycode.session import list_sessions
            sessions = list_sessions(args.project_root)
            if not sessions:
                print("No saved sessions in .pycode-sessions/.", file=sys.stderr)
            else:
                for s in sessions[:10]:
                    print(f"  {s}", file=sys.stderr)
            continue

        result = agent.run(user_input)
        print(result, "\n")


if __name__ == "__main__":
    main()
