"""Layered configuration from TOML files (user + project).

Precedence, highest first:

  1. CLI flags                 (resolved in cli.py)
  2. Environment variables     (``PYCODE_*``)
  3. Project config            ``<root>/.pycode/config.toml``
  4. User config               ``~/.config/pycode/config.toml`` then ``~/.pycode/config.toml``

Recognized top-level keys (``[provider]`` and ``[ui]`` sections are flattened
into the same namespace)::

    theme           = "nord"
    auto_approve    = false
    quiet           = false
    plain           = false
    cost            = false
    context_budget  = 80000
    max_iterations  = 40

    [provider]
    model       = "gpt-4o"
    api_base    = "https://api.openai.com/v1"
    temperature = 0.2
    max_tokens  = 4096

Only the small TOML subset above is needed, so ``tomllib`` (Python 3.11+) is
used when present and a minimal parser handles 3.10.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

try:  # Python 3.11+
    import tomllib as _tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10
    _tomllib = None


PROJECT_CONFIG_REL = os.path.join(".pycode", "config.toml")

_KNOWN_KEYS = {
    "theme", "auto_approve", "quiet", "plain", "cost", "no_map",
    "context_budget", "max_iterations",
    "model", "api_base", "temperature", "max_tokens",
    "hooks",
}

_SECTIONS = {"provider", "ui", "llm"}


def project_config_path(root: str) -> str:
    """Absolute path to the project-level config file."""
    return os.path.join(os.path.abspath(root), PROJECT_CONFIG_REL)


def user_config_paths(home: Optional[str] = None) -> List[str]:
    """Candidate user-level config paths, in precedence order."""
    home = home or os.path.expanduser("~")
    return [
        os.path.join(home, ".config", "pycode", "config.toml"),
        os.path.join(home, ".pycode", "config.toml"),
    ]


def find_config_files(root: str = ".", home: Optional[str] = None) -> List[str]:
    """Return existing config files, user first (project overrides later)."""
    paths = [p for p in user_config_paths(home) if os.path.isfile(p)]
    proj = project_config_path(root)
    if os.path.isfile(proj):
        paths.append(proj)
    return paths


# ---------------------------------------------------------------------------
# Minimal TOML subset parser (fallback for Python 3.10)
# ---------------------------------------------------------------------------

def _strip_comment(value: str) -> str:
    """Remove a trailing ``# comment`` that is not inside a quoted string."""
    quote = ""
    out = []
    for ch in value:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out).strip()


def _parse_value(value: str) -> Any:
    value = _strip_comment(value).strip()
    if not value:
        return ""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        if value[0] == '"':
            inner = inner.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
        return inner
    low = value.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def minimal_toml(text: str) -> Dict[str, Any]:
    """Parse the small TOML subset used by pycode config files."""
    result: Dict[str, Any] = {}
    section: Dict[str, Any] = result
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            section = result.setdefault(name, {})
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().strip('"').strip("'")
        if not key:
            continue
        section[key] = _parse_value(val)
    return result


def parse_toml(text: str) -> Dict[str, Any]:
    """Parse TOML text using tomllib when available, else the fallback."""
    if _tomllib is not None:
        try:
            return _tomllib.loads(text)
        except Exception:  # noqa: BLE001 - fall back to the lenient subset parser
            pass
    return minimal_toml(text)


# ---------------------------------------------------------------------------
# Loading + merging
# ---------------------------------------------------------------------------

def _load_file(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            return parse_toml(fh.read())
    except (OSError, ValueError):
        return {}


def _flatten(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten known sections and keep only recognized top-level keys.

    The ``hooks`` section is kept as a raw dict (handled by hooks.py).
    """
    flat: Dict[str, Any] = {}
    for key, value in raw.items():
        if key == "hooks" and isinstance(value, dict):
            flat[key] = value
        elif key in _SECTIONS and isinstance(value, dict):
            for sub_key, sub_val in value.items():
                if sub_key in _KNOWN_KEYS:
                    flat[sub_key] = sub_val
        elif key in _KNOWN_KEYS:
            flat[key] = value
    return flat


def merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Return a new dict with ``override`` taking precedence over ``base``."""
    out = dict(base)
    for key, value in override.items():
        if value is not None:
            out[key] = value
    return out


def load_config(root: str = ".", home: Optional[str] = None) -> Dict[str, Any]:
    """Load and merge user + project config files (project wins)."""
    merged: Dict[str, Any] = {}
    for path in find_config_files(root, home):
        merged = merge(merged, _flatten(_load_file(path)))
    return merged


def coalesce(*values: Any) -> Any:
    """First non-None value, or None if all are None."""
    for value in values:
        if value is not None:
            return value
    return None
