"""ANSI markdown renderer + regex code highlighter (no external deps).

Renders a small markdown subset into ANSI-colored terminal text:
  - fenced code blocks (```lang ... ```) get a keyword/comment/string
    highlighter tuned per-language (python/js/ts/go/rust/bash/json/sql)
  - headers (##), bold (**x**), italic (*x*), inline code (`x`)
  - lists (- / * / 1.)

Everything degrades gracefully to plain text when stdout is not a TTY or
the caller passes use_color=False.
"""

from __future__ import annotations

import re
import sys
from typing import Dict, List, Optional


def _ansicolors(enabled: bool = True) -> Dict[str, str]:
    default = {
        "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
        "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
        "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m",
        "gray": "\033[90m",
    }
    if not enabled:
        return {k: "" for k in default}
    if not sys.stdout.isatty():
        return {k: "" for k in default}
    return default


# ---------------------------------------------------------------------------
# Code highlighting (regex tokenizer, per-language keyword sets)
# ---------------------------------------------------------------------------

_KEYWORDS: Dict[str, set] = {
    "python": set("""
        def return if elif else for while in not and or is None True False
        class import from as with try except finally raise lambda yield
        pass assert global nonlocal del async await print len range
        self super True False None
    """.split()),
    "js": set("""
        function const let var return if else for while new class import
        export from as async await try catch finally throw typeof instanceof
        null undefined true false this super yield of in
    """.split()),
    "ts": set("""
        function const let var return if else for while new class import
        export from as async await try catch finally throw typeof instanceof
        null undefined true false this super yield of in interface type enum
        string number boolean any void
    """.split()),
    "go": set("""
        func return if else for range := go defer struct interface map chan
        var const type package import nil true false cap len make new
    """.split()),
    "rust": set("""
        fn return if else while for loop match let mut const struct enum impl
        pub use mod crate self super as where async await move in true false
        ref dyn box
    """.split()),
    "bash": set("""
        if then else fi for do done case esac function local export source
        return exit shift read echo cat grep sed awk curl git python
    """.split()),
    "json": set(),
    "sql": set("""
        SELECT FROM WHERE INSERT INTO UPDATE DELETE CREATE TABLE ALTER DROP
        JOIN ON GROUP BY ORDER HAVING LIMIT OFFSET AS AND OR NOT NULL PRIMARY
        KEY INDEX CONSTRAINT FOREIGN VALUES DEFAULT
    """.split()),
}

# Fallback keyword set used when language is unknown
_DEFAULT_KEYWORDS = _KEYWORDS["python"]

# Language alias normalisation
_ALIASES = {
    "py": "python", "python3": "python", "python2": "python",
    "javascript": "js", "mjs": "js", "jsx": "js", "cjs": "js",
    "typescript": "ts", "tsx": "ts",
    "sh": "bash", "zsh": "bash", "shell": "bash",
    "rs": "rust", "golang": "go", "rb": "ruby", "rb": "python",
}


def _norm_lang(lang: str) -> Optional[str]:
    if not lang:
        return None
    l = lang.lower().strip()
    l = _ALIASES.get(l, l)
    return l if l in _KEYWORDS else None


def _highlight_line(
    line: str, lang: Optional[str], c: Dict[str, str]
) -> str:
    """Single-pass tokenizer that colors strings, comments, keywords, and
    function-call identifiers."""
    kws = _KEYWORDS.get(lang or "", _DEFAULT_KEYWORDS)
    out: List[str] = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        # string literals
        if ch in ("'", '"'):
            quote = ch
            j = i + 1
            while j < n:
                if line[j] == "\\":
                    j += 2
                    continue
                if line[j] == quote:
                    j += 1
                    break
                j += 1
            out.append(c["yellow"] + line[i:j] + c["reset"])
            i = j
            continue
        # line comment
        if ch == "#":
            out.append(c["gray"] + line[i:] + c["reset"])
            break
        if ch == "/" and i + 1 < n and line[i + 1] == "/":
            out.append(c["gray"] + line[i:] + c["reset"])
            break
        if ch == "/" and i + 1 < n and line[i + 1] == "*":
            j = i + 2
            while j + 1 < n and not (line[j] == "*" and line[j + 1] == "/"):
                j += 1
            j = min(n, j + 2)
            out.append(c["gray"] + line[i:j] + c["reset"])
            i = j
            continue
        # identifier / keyword
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (line[j].isalnum() or line[j] == "_"):
                j += 1
            word = line[i:j]
            rest = line[j:j + 1]
            if word in kws:
                out.append(c["magenta"] + c["bold"] + word + c["reset"])
            elif rest == "(":
                # function call
                out.append(c["cyan"] + word + c["reset"] + "(")
                i = j + 1
                continue
            else:
                out.append(word)
            i = j
            continue
        # number
        if ch.isdigit():
            j = i
            while j < n and (line[j].isalnum() or line[j] == "."):
                j += 1
            out.append(c["blue"] + line[i:j] + c["reset"])
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def highlight_code(code: str, lang: str = "", use_color: bool = True) -> str:
    """Highlight a code block. `lang` is a hint (python, js, go, ...)."""
    c = _ansicolors(use_color)
    key = _norm_lang(lang)
    lines = code.split("\n")
    return "\n".join(_highlight_line(ln, key, c) for ln in lines)


# ---------------------------------------------------------------------------
# Inline markdown (bold / italic / inline code)
# ---------------------------------------------------------------------------

_INLINE_CODE_RE = re.compile(r"(`([^`]+)`)")
_BOLD_RE = re.compile(r"(\*\*([^*]+)\*\*)")
_ITALIC_RE = re.compile(r"(\*([^*]+)\*)")
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")


def _inline(text: str, c: Dict[str, str]) -> str:
    # inline code first (so its content isn't further formatted)
    text = _INLINE_CODE_RE.sub(
        lambda m: c["yellow"] + m.group(1) + c["reset"], text
    )
    text = _BOLD_RE.sub(lambda m: c["bold"] + m.group(2) + c["reset"], text)
    text = _ITALIC_RE.sub(lambda m: c["dim"] + m.group(2) + c["reset"], text)
    return text


# ---------------------------------------------------------------------------
# Full document render
# ---------------------------------------------------------------------------

def render_markdown(text: str, use_color: bool = True, code_indent: str = "  ") -> str:
    """Render a markdown document to ANSI text.

    Fenced code blocks (```lang\n...\n```) get highlighted; everything else
    gets inline formatting. Headers become bold+colored; list items keep
    their markers.
    """
    c = _ansicolors(use_color)
    out: List[str] = []
    lines = text.split("\n")
    in_code = False
    code_lang = ""
    code_buf: List[str] = []

    def flush_code() -> None:
        if code_buf:
            block = "\n".join(code_buf)
            out.append(c["dim"] + "──" + c["reset"] + " " + highlight_code(block, code_lang, use_color))
            code_buf.clear()

    for line in lines:
        stripped = line.lstrip()
        if in_code:
            if stripped.startswith("```"):
                flush_code()
                in_code = False
            else:
                code_buf.append(line)
            continue

        if line.startswith("```"):
            in_code = True
            code_lang = stripped[3:].strip()
            code_buf = []
            continue

        m = _HEADER_RE.match(line)
        if m:
            level = len(m.group(1))
            head = _inline(m.group(2), c)
            color = c["cyan"] if level <= 2 else c["blue"]
            prefix = " " * (level - 1)
            out.append(prefix + c["bold"] + color + head + c["reset"])
            continue

        m = _LIST_RE.match(line)
        if m:
            indent, marker, content = m.groups()
            marker_style = c["cyan"] if marker in ("-", "*") else c["magenta"]
            out.append(
                indent + c["gray"] + marker + c["reset"] + " " + _inline(content, c)
            )
            continue

        out.append(_inline(line, c))

    if in_code and code_buf:
        flush_code()
    return "\n".join(out)


def print_markdown(text: str, use_color: bool = True) -> None:
    """Print a rendered markdown document to stdout."""
    print(render_markdown(text, use_color=use_color), flush=True)
