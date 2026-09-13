"""Provider presets: one-line setup for Ollama, OpenAI, Anthropic, Venice, OpenRouter.

Each preset returns a dict with {api_key, api_base, model} that LLMProvider accepts.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

# Preset names -> (default model, default base, env var for key)
PRESETS: Dict[str, Dict[str, str]] = {
    "openai": {
        "model": "gpt-4o",
        "api_base": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
    },
    "anthropic": {
        "model": "claude-sonnet-4-5",
        "api_base": "https://api.anthropic.com/v1",
        "env_key": "ANTHROPIC_API_KEY",
    },
    "ollama": {
        "model": "llama3.1:8b",
        "api_base": "http://localhost:11434/v1",
        "env_key": "OLLAMA_API_KEY",
    },
    "venice": {
        "model": "deepseek-v4-1-flash",
        "api_base": "https://api.venice.ai/api/v1",
        "env_key": "VENICE_API_KEY",
    },
    "openrouter": {
        "model": "anthropic/claude-3.5-sonnet",
        "api_base": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
    },
}


def get_preset(name: str, model: Optional[str] = None,
               api_key: Optional[str] = None, api_base: Optional[str] = None,
               key_env_var: Optional[str] = None) -> Dict[str, Any]:
    """Return provider config for a named preset.

    Args:
        name: preset key (openai, anthropic, ollama, venice, openrouter)
        model: override the default model
        api_key: explicit key (skips env lookup)
        api_base: override the base URL
        key_env_var: extra env var to check for the key (in addition to preset's)
    """
    preset = PRESETS.get(name.lower())
    if preset is None:
        raise ValueError(f"Unknown preset: {name!r}. Valid: {', '.join(PRESETS)}")

    key = api_key
    if not key:
        key = os.getenv(key_env_var, "") if key_env_var else ""
        if not key:
            key = os.getenv(preset["env_key"], "")

    return {
        "api_key": key,
        "api_base": api_base or preset["api_base"],
        "model": model or preset["model"],
    }


def detect_preset() -> Dict[str, Any]:
    """Auto-detect which provider is available based on env vars.

    Priority: PYCODE_API_BASE (explicit) > Ollama (local) > OpenAI >
    Anthropic > Venice > OpenRouter. Returns {} if nothing found.
    """
    # If the user set an explicit base URL, use it with whichever key is present
    explicit_base = os.getenv("PYCODE_API_BASE", "")
    explicit_key = os.getenv("PYCODE_API_KEY", "")
    explicit_model = os.getenv("PYCODE_MODEL", "")
    if explicit_base or explicit_key:
        cfg = {"api_key": explicit_key, "api_base": explicit_base, "model": explicit_model or "gpt-4o"}
        if cfg["api_base"]:
            cfg["api_base"] = cfg["api_base"].rstrip("/")
        return {k: v for k, v in cfg.items() if v}

    for name in ("ollama", "openai", "anthropic", "venice", "openrouter"):
        preset = PRESETS[name]
        key = os.getenv(preset["env_key"], "")
        if key:
            return get_preset(name, api_key=key)

    return {}
