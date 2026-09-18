"""Project symbol index: a fast, regex-based codebase map.

Extracts definitions (functions, classes, types, ...) for ~10 languages with
plain regexes - no parser dependency - and caches them per-file (keyed by
mtime) under ``.pycode/index/`` so incremental refreshes are cheap.

Used by the ``symbols`` tool (find definitions / references / project map)
and to inject a compact map into the agent's system prompt.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SKIP_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    "target",
    "vendor",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".pycode",
    ".idea",
    ".vscode",
    "coverage",
}

MAX_FILES = 2000
MAX_FILE_BYTES = 512 * 1024

_CACHE_VERSION = 1

# extension -> [(kind, name-regex)] ; regex must have exactly one group (name)
SYMBOL_PATTERNS: Dict[str, List[tuple]] = {
    ".py": [
        ("class", re.compile(r"^\s*class\s+([A-Za-z_]\w*)")),
        ("def", re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)")),
    ],
    ".js": [
        (
            "class",
            re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)"),
        ),
        (
            "function",
            re.compile(
                r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"
            ),
        ),
        (
            "const",
            re.compile(
                r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\(|function)"
            ),
        ),
    ],
    ".mjs": [],  # falls back to .js patterns via _patterns_for
    ".jsx": [],
    ".ts": [
        (
            "class",
            re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"),
        ),
        ("interface", re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)")),
        ("type", re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*=")),
        (
            "function",
            re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s*([A-Za-z_$][\w$]*)"),
        ),
        (
            "const",
            re.compile(
                r"^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\(|function)"
            ),
        ),
    ],
    ".tsx": [],
    ".go": [
        ("func", re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)")),
        ("type", re.compile(r"^type\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b")),
    ],
    ".rs": [
        ("fn", re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)")),
        ("struct", re.compile(r"^\s*(?:pub\s+)?struct\s+([A-Za-z_]\w*)")),
        ("enum", re.compile(r"^\s*(?:pub\s+)?enum\s+([A-Za-z_]\w*)")),
        ("trait", re.compile(r"^\s*(?:pub\s+)?trait\s+([A-Za-z_]\w*)")),
    ],
    ".java": [
        (
            "class",
            re.compile(
                r"^\s*(?:public\s+|private\s+|protected\s+)?(?:final\s+|abstract\s+)*(?:class|interface|enum)\s+([A-Za-z_]\w*)"
            ),
        ),
    ],
    ".kt": [],
    ".rb": [
        ("class", re.compile(r"^\s*class\s+([A-Za-z_]\w*)")),
        ("module", re.compile(r"^\s*module\s+([A-Za-z_]\w*)")),
        ("def", re.compile(r"^\s*def\s+([A-Za-z_]\w*)")),
    ],
    ".c": [
        (
            "function",
            re.compile(r"^[A-Za-z_][\w\s\*]*?\s\*?([A-Za-z_]\w*)\s*\([^;]*\)\s*\{"),
        ),
        ("struct", re.compile(r"^\s*(?:typedef\s+)?struct\s+([A-Za-z_]\w*)")),
    ],
    ".h": [],
    ".cpp": [],
    ".hpp": [],
    ".sh": [
        ("function", re.compile(r"^\s*(?:function\s+)?([A-Za-z_]\w*)\s*\(\)\s*\{")),
    ],
    ".php": [
        ("class", re.compile(r"^\s*(?:abstract\s+|final\s+)?class\s+([A-Za-z_]\w*)")),
        (
            "function",
            re.compile(
                r"^\s*(?:public|private|protected)?\s*(?:static\s+)?function\s+([A-Za-z_]\w*)"
            ),
        ),
    ],
}

# aliases: extension -> patterns source
_ALIASES = {
    ".mjs": ".js",
    ".jsx": ".js",
    ".tsx": ".ts",
    ".kt": ".java",
    ".h": ".c",
    ".cpp": ".c",
    ".hpp": ".c",
}

_TEXT_EXTS = set(SYMBOL_PATTERNS) | {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".sql",
}


def patterns_for(ext: str) -> Optional[List[tuple]]:
    """Regex pattern table for a file extension (None = not indexed)."""
    ext = ext.lower()
    if ext in _ALIASES:
        return SYMBOL_PATTERNS[_ALIASES[ext]]
    return SYMBOL_PATTERNS.get(ext) or None


def detect_language(path: str) -> Optional[str]:
    """Language label for a path (extension-based), or None."""
    p = patterns_for(os.path.splitext(path)[1])
    return "generic" if p is not None else None


def extract_symbols(path: str, text: str) -> List[Dict[str, Any]]:
    """Extract symbol dicts {name, kind, path, line} from file text."""
    ext = os.path.splitext(path)[1].lower()
    pats = patterns_for(ext)
    if not pats:
        return []
    out: List[Dict[str, Any]] = []
    for i, line in enumerate(text.splitlines(), 1):
        for kind, rx in pats:
            m = rx.match(line)
            if m:
                out.append({"name": m.group(1), "kind": kind, "path": path, "line": i})
                break
    return out


def indexable(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return patterns_for(ext) is not None


class ProjectIndex:
    """Per-file symbol index with mtime cache."""

    def __init__(
        self, root: str, cache_dir: Optional[str] = None, max_files: int = MAX_FILES
    ):
        self.root = os.path.abspath(root)
        self.max_files = max_files
        self.cache_dir = Path(cache_dir or os.path.join(self.root, ".pycode", "index"))
        self.files: Dict[str, Dict[str, Any]] = {}  # path -> {mtime, symbols}

    # ------------------------------------------------------------------
    # Walking + building
    # ------------------------------------------------------------------

    def _walk(self) -> Iterable[str]:
        count = 0
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [
                d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
            ]
            for fn in filenames:
                if not indexable(fn):
                    continue
                path = os.path.join(dirpath, fn)
                if os.path.getsize(path) > MAX_FILE_BYTES:
                    continue
                yield path
                count += 1
                if count >= self.max_files:
                    return

    def build(self, save: bool = True) -> Dict[str, Any]:
        """(Re)index the project, reusing cached per-file results by mtime."""
        self.load()
        scanned = updated = 0
        for path in self._walk():
            scanned += 1
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            cached = self.files.get(path)
            if cached and cached.get("mtime") == mtime:
                continue
            try:
                text = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            self.files[path] = {"mtime": mtime, "symbols": extract_symbols(path, text)}
            updated += 1
        # drop entries for deleted files
        known = set(self.files)
        live = set()
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [
                d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
            ]
            for fn in filenames:
                live.add(os.path.join(dirpath, fn))
        for path in list(known - live):
            del self.files[path]
        if save:
            self.save()
        return {"scanned": scanned, "updated": updated, "files": len(self.files)}

    def refresh(self) -> Dict[str, Any]:
        """Public alias for build()."""
        return self.build()

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def save(self) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": _CACHE_VERSION,
                "root": self.root,
                "files": self.files,
            }
            (self.cache_dir / "symbols.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
        except OSError:
            pass

    def load(self) -> None:
        path = self.cache_dir / "symbols.json"
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if payload.get("version") != _CACHE_VERSION:
            return
        self.files = payload.get("files", {})

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def all_symbols(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for path in sorted(self.files):
            out.extend(self.files[path].get("symbols", []))
        return out

    def find(
        self, query: str, kind: Optional[str] = None, limit: int = 25
    ) -> List[Dict[str, Any]]:
        """Definitions whose name contains ``query`` (case-insensitive)."""
        q = query.lower()
        out = []
        for sym in self.all_symbols():
            if q and q not in sym["name"].lower():
                continue
            if kind and sym["kind"] != kind:
                continue
            out.append(sym)
            if len(out) >= limit:
                break
        return out

    def definition(self, name: str) -> Optional[Dict[str, Any]]:
        """Best definition for an exact symbol name (prefer shortest path)."""
        matches = [s for s in self.all_symbols() if s["name"] == name]
        if not matches:
            return None
        matches.sort(key=lambda s: (len(s["path"]), s["line"]))
        return matches[0]

    def references(self, name: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Word-boundary occurrences of ``name`` excluding its def line."""
        rx = re.compile(r"\b" + re.escape(name) + r"\b")
        defs = {(s["path"], s["line"]) for s in self.all_symbols() if s["name"] == name}
        out: List[Dict[str, Any]] = []
        for path in sorted(self.files):
            try:
                text = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if (path, i) in defs:
                    continue
                if rx.search(line):
                    out.append({"path": path, "line": i, "text": line.strip()[:120]})
                    if len(out) >= limit:
                        return out
        return out

    def summary(self, max_chars: int = 4000, per_file: int = 12) -> str:
        """Compact, human/LLM-friendly map grouped by file."""
        lines: List[str] = []
        used = 0
        for path in sorted(self.files):
            syms = self.files[path].get("symbols", [])
            if not syms:
                continue
            rel = os.path.relpath(path, self.root)
            names = ", ".join(f"{s['kind']} {s['name']}" for s in syms[:per_file])
            more = f" (+{len(syms) - per_file} more)" if len(syms) > per_file else ""
            entry = f"{rel}: {names}{more}"
            if used + len(entry) > max_chars:
                lines.append("... (map truncated)")
                break
            lines.append(entry)
            used += len(entry)
        return "\n".join(lines)

    def stats(self) -> Dict[str, Any]:
        total = sum(len(self.files[p].get("symbols", [])) for p in self.files)
        return {"files": len(self.files), "symbols": total, "root": self.root}
