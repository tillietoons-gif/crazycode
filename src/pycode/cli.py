"""CLI entry point for pycode - interactive REPL with readline support."""

from __future__ import annotations

import argparse
import os
import sys

from pycode.agent import Agent


def _print_banner() -> None:
    print("=" * 56)
    print("  pycode - Python AI Coding Agent (v0.1.0)")
    print("=" * 56)
    print("  Type a command. Use: quit/exit to stop, :clear to reset.")
    print("  Config: PYCODE_API_KEY, PYCODE_API_BASE, PYCODE_MODEL")
    print("=" * 56, "\n")


def _print_config(cfg: dict) -> None:
    model = cfg.get("model", "(default)")
    base = cfg.get("api_base", "(default)")
    key = cfg.get("api_key")
    key_status = "set" if key else "NOT SET"
    print(f"  Model:   {model}")
    print(f"  Base:    {base}")
    print(f"  API key: {key_status}")
    print()


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
        max_iterations=args.max_iterations,
        verbose=not args.quiet,
    )

    if not cfg.get("api_key"):
        print("Warning: No API key found.", file=sys.stderr)
        print("  Set PYCODE_API_KEY or --api-key, or set OPENAI_API_KEY.", file=sys.stderr)

    _print_banner()
    _print_config({k: v for k, v in cfg.items() if v})

    # Determine the prompt
    prompt_parts = " ".join(args.prompt).strip()

    if prompt_parts:
        # Single-shot mode: run the prompt then exit
        result = agent.run(prompt_parts)
        print(result)
        if args.non_interactive or not sys.stdin.isatty():
            return
        # Fall through to REPL after a successful single-shot

    # Interactive REPL
    try:
        import readline  # noqa: F401
    except ImportError:
        pass

    print("\nEntering interactive mode. Type 'quit' or 'exit' to stop.\n")

    while True:
        try:
            user_input = input(">>> ")
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Bye.")
            break
        if user_input.lower() == ":clear":
            agent.clear()
            print("Conversation cleared.")
            continue

        result = agent.run(user_input)
        print(result, "\n")


if __name__ == "__main__":
    main()
