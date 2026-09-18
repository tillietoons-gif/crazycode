"""Tools for the pycode agent: bash, read, write, edit, glob, grep, webfetch, todo.

Each tool exposes:
  - a callable that performs the work
  - a JSON-schema-style dict for LLM function-calling

The module also exposes TOOL_SCHEMAS (a list) and TOOLS (a name->callable map)
for the agent loop.
"""

from __future__ import annotations

import difflib
import glob as glob_mod
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MAX_READ_LINES = 200
_MAX_BASH_OUTPUT = 100_000


def _safe_read_file(path: str, offset: int = 1, limit: int = _MAX_READ_LINES) -> str:
    """Read up to `limit` lines starting from 1-indexed `offset`."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"No such file: {path}")
    with open(p, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    start = max(0, offset - 1)
    end = start + limit
    segment = lines[start:end]
    result = []
    for idx, raw in enumerate(segment, start=start + 1):
        text = raw.rstrip("\n")
        if len(text) > 2000:
            text = text[:2000] + " …[truncated]"
        result.append(f"{idx}: {text}")
    return "\n".join(result) if result else "(empty)"


def _run_cmd(
    cmd: str, workdir: Optional[str] = None, timeout: int = 120
) -> Dict[str, Any]:
    """Run a shell command and capture stdout/stderr/exit_code."""
    cwd = workdir or os.getcwd()
    if not os.path.isdir(cwd):
        return {"error": f"Working directory does not exist: {cwd}"}
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = proc.stdout
        stderr = proc.stderr
        if len(stdout) > _MAX_BASH_OUTPUT:
            stdout = stdout[:_MAX_BASH_OUTPUT] + "\n…[output truncated]"
        if len(stderr) > _MAX_BASH_OUTPUT:
            stderr = stderr[:_MAX_BASH_OUTPUT] + "\n…[output truncated]"
        return {
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout}s", "cmd": cmd}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "cmd": cmd}


def _fetch_url(url: str, timeout: int = 30) -> Dict[str, Any]:
    """Fetch a URL and return its text content."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pycode-agent/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {"url": url, "status": resp.status, "content": body}
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.reason}", "url": url}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "url": url}


# ---------------------------------------------------------------------------
# Individual tools
# ---------------------------------------------------------------------------


def tool_bash(
    command: str, workdir: Optional[str] = None, timeout: Optional[int] = None
) -> Dict[str, Any]:
    """Execute a shell command. Returns exit_code, stdout, stderr."""
    timeout = timeout or 120
    return _run_cmd(command, workdir=workdir, timeout=timeout)


# ---------------------------------------------------------------------------
# Background job tools
# ---------------------------------------------------------------------------

_JOB_MANAGER = None


def get_job_manager() -> Any:
    """Lazily-created shared JobManager for background shell jobs."""
    global _JOB_MANAGER
    if _JOB_MANAGER is None:
        from pycode.jobs import JobManager

        _JOB_MANAGER = JobManager()
    return _JOB_MANAGER


def tool_bash_background(command: str, workdir: Optional[str] = None) -> Dict[str, Any]:
    """Start a shell command in the background and return a job id."""
    return get_job_manager().start(command, workdir=workdir)


def tool_job_output(job_id: str, tail: int = 60) -> Dict[str, Any]:
    """Return a background job's status and last lines of output."""
    return get_job_manager().output(job_id, tail=tail)


def tool_job_list() -> Dict[str, Any]:
    """List background jobs started this session."""
    return {"jobs": get_job_manager().list()}


def tool_job_kill(job_id: str) -> Dict[str, Any]:
    """Kill a background job (and its child processes)."""
    return get_job_manager().kill(job_id)


def tool_read(
    path: str,
    offset: int = 1,
    limit: int = _MAX_READ_LINES,
    symbol: Optional[str] = None,
) -> str:
    """Read a file (max 200 lines per call, 1-indexed offset).

    If ``symbol`` is given, read around that definition instead: the window
    starts a few lines above the symbol's definition line.
    """
    if symbol:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            return f"Error: cannot read {path}"
        line = _find_symbol_line(text, symbol)
        if line is None:
            return f"Error: symbol {symbol!r} not found in {path}"
        offset = max(1, line - 5)
    try:
        return _safe_read_file(path, offset=offset, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return f"Error: {exc}"


_SYM_DEF_RE = re.compile(
    r"^\s*(?:async\s+)?(?:def|fn|function|func|class|struct|enum|trait|interface|type)\s+"
    r"([A-Za-z_]\w*)"
)


def _find_symbol_line(text: str, symbol: str) -> Optional[int]:
    """1-indexed line of ``symbol``'s definition in ``text`` (heuristic)."""
    for i, line in enumerate(text.splitlines(), 1):
        m = _SYM_DEF_RE.match(line)
        if m and m.group(1) == symbol:
            return i
    return None


# ---------------------------------------------------------------------------
# Symbol index tool
# ---------------------------------------------------------------------------


def tool_symbols(
    query: str = "",
    mode: str = "find",
    kind: Optional[str] = None,
    path: Optional[str] = None,
    limit: int = 25,
) -> Dict[str, Any]:
    """Query the project symbol index.

    Modes: find (definitions matching query), refs (references to query),
    map (whole-project summary). Builds/refreshes the index lazily.
    """
    from pycode.index import ProjectIndex

    idx = get_project_index()
    if not idx.files:
        idx.build()
    if mode == "map":
        return {"map": idx.summary(max_chars=4000), "stats": idx.stats()}
    if mode == "refs":
        if not query:
            return {"error": "refs mode requires a query"}
        return {"query": query, "references": idx.references(query, limit=limit)}
    # default: find definitions
    results = idx.find(query, kind=kind, limit=limit)
    if path:
        results = [r for r in results if path in r["path"]]
    return {"query": query, "mode": "find", "results": results}


_PROJECT_INDEX = None


def get_project_index(root: Optional[str] = None) -> Any:
    """Lazily-created shared ProjectIndex for the agent's working directory."""
    global _PROJECT_INDEX
    if _PROJECT_INDEX is None or root is not None:
        from pycode.index import ProjectIndex

        _PROJECT_INDEX = ProjectIndex(root or os.getcwd())
    return _PROJECT_INDEX


def tool_write(path: str, content: str) -> Dict[str, Any]:
    """Write (overwrite) a file. Parent dirs are created as needed."""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)
        return {"ok": True, "path": path, "bytes_written": len(content)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def tool_edit(
    path: str, old_string: str, new_string: str, replace_all: bool = False
) -> Dict[str, Any]:
    """Replace a substring in a file (single occurrence, or all)."""
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": f"File not found: {path}"}
    text = p.read_text(encoding="utf-8")
    count = text.count(old_string)
    if count == 0:
        return {"ok": False, "error": f"old_string not found in {path}"}
    if count > 1 and not replace_all:
        return {
            "ok": False,
            "error": f"old_string found {count} times; set replace_all=true or provide more context",
        }
    if replace_all:
        new_text = text.replace(old_string, new_string)
    else:
        new_text = text.replace(old_string, new_string, 1)
    p.write_text(new_text, encoding="utf-8")
    return {"ok": True, "replacements": count if replace_all else 1, "path": path}


def compute_diff(old_text: str, new_text: str, path: str = "") -> str:
    """Return a unified diff between two text strings.

    Used by the dry-run view to show what a write/edit would change
    without actually modifying the file.
    """
    old_lines = (old_text or "").splitlines(keepends=True)
    new_lines = (new_text or "").splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{path}" if path else "a/(existing)",
        tofile=f"b/{path}" if path else "b/(new)",
        n=2,
    )
    return "".join(diff) or "(no changes)"


def dry_run_diff(path: str, new_content: str) -> Dict[str, Any]:
    """Show what a write would change without applying it."""
    p = Path(path)
    if p.is_file():
        old = p.read_text(encoding="utf-8")
    else:
        old = ""
    diff = compute_diff(old, new_content, path)
    return {
        "dry_run": True,
        "path": path,
        "existed_before": p.is_file(),
        "diff": diff,
        "lines_old": len(old.splitlines()),
        "lines_new": len(new_content.splitlines()),
    }


def tool_glob(pattern: str, path: Optional[str] = None) -> List[str]:
    """Return file paths matching a glob pattern (e.g. **/*.py)."""
    base = Path(path) if path else Path(os.getcwd())
    if not base.exists():
        return []
    matches = glob_mod.glob(str(base / pattern), recursive=True)
    return sorted(matches)[:500]  # cap at 500 to avoid huge results


def tool_grep(
    pattern: str,
    path: Optional[str] = None,
    include: Optional[str] = None,
    max_results: int = 200,
) -> List[Dict[str, Any]]:
    """Search file contents with a regex pattern. Returns matching lines with file/line info."""
    search_dir = Path(path) if path else Path(os.getcwd())
    if not search_dir.exists():
        return []
    if include:
        # support patterns like *.py or *.{ts,tsx}
        # convert glob-style filter to a simple extension match
        exts = [ext.strip() for ext in re.findall(r"([^{}]*)", include) if ext.strip()]
        if not exts:
            exts = [include]
    else:
        exts = None

    rx = re.compile(pattern)
    results: List[Dict[str, Any]] = []

    for root, dirs, files in os.walk(search_dir):
        # skip hidden dirs
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            if exts:
                # check extension via endswith (handles *.py, *.tsx, etc.)
                if not any(
                    (
                        fname.endswith(ext.lstrip("*"))
                        if ext.startswith("*")
                        else fname == ext
                    )
                    for ext in exts
                ):
                    continue
            fpath = Path(root, fname)
            try:
                with open(fpath, encoding="utf-8", errors="replace") as fh:
                    for lineno, line in enumerate(fh, start=1):
                        if rx.search(line):
                            results.append(
                                {
                                    "file": str(fpath),
                                    "line": lineno,
                                    "text": line.rstrip(),
                                }
                            )
                            if len(results) >= max_results:
                                return results
            except Exception:  # noqa: BLE001
                continue
    return results


def tool_webfetch(url: str, timeout: int = 30) -> Dict[str, Any]:
    """Fetch a URL and return its content."""
    return _fetch_url(url, timeout=timeout)


def tool_web_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    """Search the web for a query and return top result titles/URLs/snippets.

    Uses DuckDuckGo's HTML endpoint (no API key required). Returns a list of
    {title, url}. Fails gracefully if the network is unavailable.
    """
    import base64
    import html as _html
    import urllib.parse
    import zlib

    try:
        url = "https://duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        req = urllib.request.Request(url, headers={"User-Agent": "pycode-agent/0.5"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        results: List[Dict[str, str]] = []
        for m in re.finditer(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.S
        ):
            href = m.group(1)
            title = _html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
            real_url = _decode_ddg_url(href) if "uddg=" in href else href
            results.append({"title": title, "url": real_url or href})
            if len(results) >= max_results:
                break
        return {"query": query, "results": results, "count": len(results)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"web search failed: {exc}", "query": query, "results": []}


def _decode_ddg_url(href: str) -> str:
    """Decode DuckDuckGo's uddg redirect param to the real URL."""
    import base64
    import urllib.parse
    import zlib

    try:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        b64 = params.get("uddg", [""])[0]
        if not b64:
            return ""
        padded = b64 + "=" * (-len(b64) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode())
        if decoded[:1] == b"\x9c":
            try:
                decoded = zlib.decompress(decoded, -15)
            except Exception:  # noqa: BLE001
                decoded = decoded[1:]
        return decoded.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def tool_view_image(path: str) -> Dict[str, Any]:
    """Read a local image file and return base64 + metadata for vision LLMs.

    Returns {path, mime, b64, size_bytes} so the agent can include it in a
    multimodal message. Errors if the file is missing or too large.
    """
    import base64
    import mimetypes

    MAX_BYTES = 10 * 1024 * 1024  # 10 MB cap
    p = Path(path)
    if not p.is_file():
        return {"error": f"Image not found: {path}"}
    data = p.read_bytes()
    if len(data) > MAX_BYTES:
        return {"error": f"Image too large ({len(data)} bytes; cap {MAX_BYTES})"}
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return {
        "path": path,
        "mime": mime,
        "b64": base64.b64encode(data).decode(),
        "size_bytes": len(data),
    }


def tool_todo(
    add: Optional[List[Dict[str, str]]] = None, clear: bool = False
) -> Dict[str, Any]:
    """Manage a simple in-memory todo list shared across the session."""
    # Stored on the agent instance via the module-level dict
    global _todo_items
    if clear:
        _todo_items = []
    if add:
        _todo_items.extend(add)
    return {"todos": _todo_items}


_todo_items: List[Dict[str, str]] = []


# ---------------------------------------------------------------------------
# JSON-schema tool definitions (for OpenAI function-calling)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command in the working directory. Use for git, npm, pip, tests, and general dev tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to run",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "Optional working directory",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 120)",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a file. Supports offset/limit for large files, or symbol=NAME to read around a definition.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative file path",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start from (1-indexed, default 1)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max lines to read (default 200)",
                    },
                    "symbol": {
                        "type": "string",
                        "description": "Read around this definition (function/class name) instead of using offset",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Write (create or overwrite) a file with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write"},
                    "content": {"type": "string", "description": "Full file content"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Replace a substring in a file. Use for surgical in-place edits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "old_string": {
                        "type": "string",
                        "description": "Exact text to find",
                    },
                    "new_string": {"type": "string", "description": "Replacement text"},
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace all occurrences (default false)",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files by glob pattern (e.g. **/*.py).",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern"},
                    "path": {
                        "type": "string",
                        "description": "Base directory to search from",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents with a regex pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "Base directory to search",
                    },
                    "include": {
                        "type": "string",
                        "description": "File filter (e.g. *.py or *.{ts,tsx})",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max matching lines to return",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "webfetch",
            "description": "Fetch a URL and return its content (useful for docs, APIs).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 30)",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo",
            "description": "Manage the session todo list. Pass add (list of {content, status, priority}) to add items, or clear=true to reset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "add": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": [
                                        "pending",
                                        "in_progress",
                                        "completed",
                                        "cancelled",
                                    ],
                                },
                                "priority": {
                                    "type": "string",
                                    "enum": ["high", "medium", "low"],
                                },
                            },
                        },
                        "description": "New todo items to add",
                    },
                    "clear": {
                        "type": "boolean",
                        "description": "Clear all existing todos",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for a query. Returns top result titles, URLs, and snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {
                        "type": "integer",
                        "description": "Max results (default 5)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_image",
            "description": "Read a local image file and return base64 + metadata for multimodal/vision LLMs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the image file"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash_background",
            "description": "Start a shell command in the background (dev servers, long builds, installs) "
            "and keep working. Returns a job_id to poll with job_output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to run in the background",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "Optional working directory",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "job_output",
            "description": "Check a background job: status plus its last output lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Job id returned by bash_background",
                    },
                    "tail": {
                        "type": "integer",
                        "description": "Last N lines to return (default 60)",
                    },
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "job_list",
            "description": "List all background jobs started this session with their status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "job_kill",
            "description": "Kill a running background job by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Job id returned by bash_background",
                    },
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "symbols",
            "description": "Query the project symbol index. Modes: find (default) lists definitions matching query; "
            "refs lists references to query; map returns a whole-project summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Symbol name (or substring) to look up",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["find", "refs", "map"],
                        "description": "find=definitions, refs=usages, map=project overview (default find)",
                    },
                    "kind": {
                        "type": "string",
                        "description": "Optional kind filter (def/class/fn/func/struct/trait/...)",
                    },
                    "path": {
                        "type": "string",
                        "description": "Restrict results to files under this path substring",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 25)",
                    },
                },
            },
        },
    },
]

# Name -> callable map
TOOLS: Dict[str, Any] = {
    "bash": tool_bash,
    "read": tool_read,
    "write": tool_write,
    "edit": tool_edit,
    "glob": tool_glob,
    "grep": tool_grep,
    "webfetch": tool_webfetch,
    "web_search": tool_web_search,
    "view_image": tool_view_image,
    "todo": tool_todo,
    "bash_background": tool_bash_background,
    "job_output": tool_job_output,
    "job_list": tool_job_list,
    "job_kill": tool_job_kill,
    "symbols": tool_symbols,
}


# ---------------------------------------------------------------------------
# Tool confirmation / sandbox
# ---------------------------------------------------------------------------

# Tools that mutate state and warrant a confirmation prompt
_DESTRUCTIVE_TOOLS = {"write", "edit"}
# Bash commands that are destructive even without confirmation
_DESTRUCTIVE_CMD_RE = re.compile(
    r"\b(rm\s|rm -|rm$|rm\b.*-f|drop\s+(table|database|schema)|"
    r"truncate|git\s+push.*--force|git\s+reset.*--hard|git\s+clean|"
    r"shutdown|reboot|mkfs|fdisk|dd\s+if=|chmod\s+777|userdel|shred)\b"
)


def is_destructive(name: str, args: Dict[str, Any]) -> bool:
    """Heuristic: does this tool call mutate state destructively?"""
    from pycode.plugins import USER_DESTRUCTIVE

    if name in USER_DESTRUCTIVE:
        return True
    if name in _DESTRUCTIVE_TOOLS:
        return True
    if name in ("bash", "bash_background"):
        cmd = args.get("command", "")
        if _DESTRUCTIVE_CMD_RE.search(cmd):
            return True
        return False
    return False


def dispatch_tool(
    name: str,
    args: Dict[str, Any],
    confirm: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
    auto_approve: bool = False,
    dry_run: bool = False,
    mcp_registry: Optional[Any] = None,
) -> str:
    """Call a tool by name, serialise the result to a JSON string.

    Args:
        name: tool name
        args: tool arguments
        confirm: optional callback (tool_name, args) -> bool; if it returns
                 False the tool is skipped with an error result.
        auto_approve: if True, skip all confirmations (sandbox mode).
        dry_run: if True, for write/edit show a diff instead of applying.
        mcp_registry: optional MCPRegistry; if present and the tool name
                      matches an MCP tool, it is routed there instead.
    """
    # MCP routing: external tool plugins
    if mcp_registry is not None:
        mcp_result = mcp_registry.dispatch(name, args)
        if mcp_result is not None:
            return mcp_result

    fn = TOOLS.get(name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool: {name}"})

    # Dry-run: preview the diff instead of mutating the file
    if dry_run and name in ("write", "edit"):
        if name == "write":
            preview = dry_run_diff(args.get("path", ""), args.get("content", ""))
            return json.dumps(preview, ensure_ascii=False, default=str)
        # edit: compute the would-be new content and diff it
        p = Path(args.get("path", ""))
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            old = text.count(args.get("old_string", ""))
            if old == 0:
                return json.dumps(
                    {"error": "old_string not found in file", "dry_run": True}
                )
            rep_all = args.get("replace_all", False)
            new_text = (
                text.replace(args.get("old_string", ""), args.get("new_string", ""))
                if rep_all
                else text.replace(
                    args.get("old_string", ""), args.get("new_string", ""), 1
                )
            )
            return json.dumps(
                {
                    "dry_run": True,
                    "path": str(p),
                    "diff": compute_diff(text, new_text, str(p)),
                },
                ensure_ascii=False,
                default=str,
            )
        return json.dumps(
            {"error": f"File not found: {args.get('path','?')}", "dry_run": True}
        )

    # Gate destructive tools behind confirmation
    if not auto_approve and confirm is not None and is_destructive(name, args):
        if not confirm(name, args):
            return json.dumps({"error": f"Tool {name} was declined by the user"})

    try:
        result = fn(**args)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"Tool {name} failed: {exc}"})
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)
