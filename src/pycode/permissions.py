"""Permission model: gate tool calls by policy.

Policies are defined in `.pycode/permissions.toml` at the project root:

    # .pycode/permissions.toml
    [tools]
    allow = ["read", "glob", "grep", "bash", "edit", "write"]   # what the agent may call
    # bash_blocklist = ["rm -rf /", "shutdown"]                  # forbidden bash substrings
    # write_root = "src/"                                        # writes only under this dir
    # yolo = false                                               # skip all confirmations

The PermissionGuard checks a (tool_name, args) call against the policy and
returns an allow/deny decision. A built-in default bash blocklist protects
against catastrophic commands even when no policy file is present.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Catastrophic commands denied by default, even with no policy file.
_DEFAULT_BASH_BLOCKLIST: List[str] = [
    "rm -rf /",
    "rm -rf ~",
    "mkfs",
    "shutdown",
    "reboot",
    "dd if=/dev/zero",
    ":(){ :|:& };:",  # fork bomb
    "chmod -R 777 /",
    "git push --force origin main",
    "git push --force origin master",
    "DROP DATABASE",
    "TRUNCATE TABLE",
]

# A write_root policy restricts these mutating tools to a directory.
_FILE_MUTATION_TOOLS = {"write", "edit"}


@dataclass
class PermissionDecision:
    allowed: bool
    reason: str = ""
    requires_confirm: bool = False


@dataclass
class PermissionGuard:
    """Evaluates tool calls against a policy."""

    allow_tools: Optional[List[str]] = None  # None = all allowed
    bash_blocklist: List[str] = field(
        default_factory=lambda: list(_DEFAULT_BASH_BLOCKLIST)
    )
    write_root: Optional[str] = None
    yolo: bool = False
    source: str = "builtin"

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @classmethod
    def from_project(cls, root: Optional[str] = None) -> "PermissionGuard":
        """Load a policy from `.pycode/permissions.toml` if present."""
        base = Path(root) if root else Path(os.getcwd())
        policy_file = base / ".pycode" / "permissions.toml"
        if not policy_file.is_file():
            return cls()  # defaults
        return cls.from_file(str(policy_file))

    @classmethod
    def from_file(cls, path: str) -> "PermissionGuard":
        text = Path(path).read_text(encoding="utf-8")
        allow = _parse_toml_list(text, "allow")
        blocklist = _parse_toml_list(text, "bash_blocklist")
        write_root = _parse_toml_str(text, "write_root")
        yolo = _parse_toml_bool(text, "yolo")

        guard = cls(
            allow_tools=allow,
            write_root=write_root,
            yolo=yolo,
            source=path,
        )
        if blocklist is not None:
            guard.bash_blocklist = list(blocklist) + _DEFAULT_BASH_BLOCKLIST
        return guard

    # ------------------------------------------------------------------
    # Core check
    # ------------------------------------------------------------------

    def check(self, tool_name: str, args: Dict[str, Any]) -> PermissionDecision:
        # YOLO bypasses everything except hard-coded catastrophic bash
        if self.yolo:
            if tool_name == "bash" and self._bash_blocked(args.get("command", "")):
                return PermissionDecision(
                    False,
                    f"blocked by yolo-safety blocklist: {args.get('command','')}",
                    False,
                )
            return PermissionDecision(True, "yolo mode", False)

        # 1. Tool allowlist
        if self.allow_tools is not None and tool_name not in self.allow_tools:
            return PermissionDecision(
                False, f"tool '{tool_name}' not in allow list", False
            )

        # 2. Bash blocklist
        if tool_name == "bash":
            if self._bash_blocked(args.get("command", "")):
                return PermissionDecision(
                    False, f"bash command blocked: {args.get('command','')}", False
                )
            # bash that mutates state still requires confirmation
            if re.search(
                r"[|>&>]|(^|\s)(mv|cp|mkdir|touch|sudo|kill)\b", args.get("command", "")
            ):
                return PermissionDecision(True, "bash may mutate; confirm", True)
            return PermissionDecision(True, "", False)

        # 3. Write root restriction
        if tool_name in _FILE_MUTATION_TOOLS and self.write_root:
            target = args.get("path", "")
            if not self._within_root(target):
                return PermissionDecision(
                    False,
                    f"'{tool_name}' target '{target}' is outside write_root '{self.write_root}'",
                    False,
                )
            return PermissionDecision(True, "write allowed inside root; confirm", True)

        # 4. Plain file mutation without a root restriction -> confirm
        if tool_name in _FILE_MUTATION_TOOLS:
            return PermissionDecision(True, "file mutation; confirm", True)

        return PermissionDecision(True, "", False)

    def _bash_blocked(self, command: str) -> bool:
        cmd = (command or "").lower()
        for pattern in self.bash_blocklist:
            if pattern.lower() in cmd:
                return True
        return False

    def _within_root(self, target: str) -> bool:
        if not self.write_root:
            return True
        try:
            base = Path(os.getcwd())
            root = (base / self.write_root).resolve()
            t = (
                (base / target).resolve()
                if not os.path.isabs(target)
                else Path(target).resolve()
            )
            return t.is_relative_to(root)
        except (ValueError, OSError):
            return False


# ---------------------------------------------------------------------------
# Minimal TOML subset parser (lists, strings, bools) - avoids a tomllib dep
# on older Pythons while staying stdlib-only.
# ---------------------------------------------------------------------------


def _section_value(text: str, key: str) -> Optional[str]:
    """Find `key = <value>` anywhere in the text (simple line scan)."""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#") or not s:
            continue
        if s.startswith("[") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        if k.strip() == key:
            return v.strip()
    return None


def _parse_toml_list(text: str, key: str) -> Optional[List[str]]:
    raw = _section_value(text, key)
    if raw is None:
        return None
    if not (raw.startswith("[") and raw.endswith("]")):
        return None
    inner = raw[1:-1]
    items = re.findall(r"\"([^\"]*)\"|'([^']*)'", inner)
    return [a or b for a, b in items]


def _parse_toml_str(text: str, key: str) -> Optional[str]:
    raw = _section_value(text, key)
    if raw is None:
        return None
    m = re.match(r"^[\"'](.*)[\"']$", raw)
    return m.group(1) if m else raw


def _parse_toml_bool(text: str, key: str) -> bool:
    raw = _section_value(text, key)
    if raw is None:
        return False
    return raw.strip().lower() == "true"


# ---------------------------------------------------------------------------
# A confirmation callback that honors PermissionGuard decisions
# ---------------------------------------------------------------------------


def make_permission_confirm(
    guard: PermissionGuard,
    prompt: Optional[Any] = None,
) -> Any:
    """Build a confirm callback (tool_name, args) -> bool for dispatch_tool.

    - Hard-denied tools -> return False (no prompt).
    - Tools marked requires_confirm -> call `prompt` (or input()) to ask.
    - Everything else -> True.
    """

    def confirm(tool_name: str, args: Dict[str, Any]) -> bool:
        decision = guard.check(tool_name, args)
        if not decision.allowed:
            return False
        if not decision.requires_confirm:
            return True
        if guard.yolo:
            return True
        if prompt is not None:
            return bool(prompt(tool_name, args, decision.reason))
        try:
            ans = input(
                f"  approve {tool_name}: {args.get('command') or args.get('path') or decision.reason}? [y/N] "
            )
        except (EOFError, KeyboardInterrupt):
            return False
        return ans.strip().lower() in ("y", "yes")

    return confirm
