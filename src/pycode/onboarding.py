"""First-run onboarding: detect a missing API key and print setup guidance.

If the user has no LLM credentials at all, show a copy-pasteable snippet for
the most likely providers (Ollama local, OpenAI, Anthropic, Venice). This is
shown once, on the first interactive run when no key is detected.
"""

from __future__ import annotations

import os
from typing import Dict

from pycode.providers import PRESETS

# env var each preset expects
_PRESET_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "venice": "VENICE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "ollama": "OLLAMA_API_KEY",  # optional for local
}


def has_any_credential() -> bool:
    """True if any LLM env var the agent can use is set (non-empty)."""
    # the agent's primary var + every preset's key var
    for var in ["PYCODE_API_KEY", "OPENAI_API_KEY"] + list(_PRESET_ENV.values()):
        if os.getenv(var):
            return True
    return False


def _preset_snippet(name: str) -> str:
    p = PRESETS[name]
    key = _PRESET_ENV.get(name, "")
    lines = [f"  # {name}"]
    if key:
        lines.append(f"export {key}='sk-...your-key'")
    lines.append(f"python3 -m pycode --preset {name} 'your prompt here'")
    lines.append("")
    return "\n".join(lines)


def onboarding_message() -> str:
    """Return the first-run setup guidance text."""
    blocks = [_preset_snippet(n) for n in ("ollama", "openai", "anthropic", "venice")]
    return (
        "No LLM API key detected. To get started:\n"
        "\n"
        "  Option A - local Ollama (no key needed, free):\n"
        + _preset_snippet("ollama")
        + "\n"
        "  Option B - set a provider key and run with its preset:\n"
        + "\n".join(b for b in blocks[1:])
        + "\n"
        "  Or set the generic env vars:\n"
        "export PYCODE_API_KEY='your-key'\n"
        "export PYCODE_API_BASE='https://api.openai.com/v1'\n"
        "export PYCODE_MODEL='gpt-4o'\n"
    )


def should_show_onboarding(force: bool = False) -> bool:
    """True if onboarding should be shown this run."""
    if force:
        return True
    if has_any_credential():
        return False
    marker = os.path.join(os.path.expanduser("~"), ".pycode", ".onboarded")
    return not os.path.exists(marker)


def mark_onboarded() -> None:
    """Record that onboarding has been shown, so we don't nag again."""
    import os
    import pathlib

    marker = pathlib.Path(os.path.expanduser("~")) / ".pycode" / ".onboarded"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("1", encoding="utf-8")
