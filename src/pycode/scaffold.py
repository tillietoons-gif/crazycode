"""CLAUDE.md / AGENTS.md project-context template generator.

Generates a starter project instruction file that the agent auto-loads.
The template is filled from what it can detect about the project (language,
package manager, test runner, lint config) plus a few prompts the user can
fill in by hand.

Usage:
    python -m pycode.scaffold          # writes ./CLAUDE.md if missing
    python -m pycode.scaffold --name AGENTS.md
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

DEFAULT_TEMPLATE = """# Project Instructions for pycode

## Overview
{overview}

## Tech Stack
- Language: {language}
- Package manager: {package_manager}
- Test runner: {test_runner}
- Lint/format: {linter}

## Build & Run
```bash
{run_commands}
```

## Testing
```bash
{test_commands}
```

## Code Conventions
- {conventions}

## Important Notes
- {notes}
"""


def _detect_project(root: Path) -> dict:
    """Lightweight heuristics to fill the template with detected facts."""
    info = {
        "language": "unknown",
        "package_manager": "unknown",
        "test_runner": "unknown",
        "linter": "none detected",
        "run_commands": "(add your run commands here)",
        "test_commands": "(add your test commands here)",
        "conventions": "- (describe your conventions here)",
        "notes": "- (add anything the agent should always know)",
        "overview": "(one-line summary of what this project does)",
    }

    def has(fname: str) -> bool:
        return (root / fname).exists()

    if has("package.json"):
        info["language"] = "Node.js / TypeScript"
        info["package_manager"] = "npm (package.json present)"
        if has("pnpm-lock.yaml"):
            info["package_manager"] = "pnpm"
        elif has("yarn.lock"):
            info["package_manager"] = "yarn"
        if has("vitest.config.ts") or has("vitest.config.js"):
            info["test_runner"] = "vitest"
        elif has("jest.config.js"):
            info["test_runner"] = "jest"
        elif has("package.json"):
            info["test_runner"] = "jest/vitest (check package.json scripts)"
    elif has("pyproject.toml"):
        info["language"] = "Python"
        info["package_manager"] = "pip / setuptools (pyproject.toml)"
        if has("pytest.ini") or has("tox.ini"):
            info["test_runner"] = "pytest"
        else:
            info["test_runner"] = "pytest (likely)"
    elif has("go.mod"):
        info["language"] = "Go"
        info["package_manager"] = "go modules"
        info["test_runner"] = "go test"
    elif has("Cargo.toml"):
        info["language"] = "Rust"
        info["package_manager"] = "cargo"
        info["test_runner"] = "cargo test"
    elif has("pom.xml"):
        info["language"] = "Java"
        info["package_manager"] = "Maven"
    elif has("build.gradle"):
        info["language"] = "Java / Kotlin"
        info["package_manager"] = "Gradle"

    # Linter detection
    lints = []
    for f in ("ruff.toml", ".ruff.toml"):
        if has(f):
            lints.append("ruff")
    for f in (
        "eslint.config.js",
        "eslint.config.mjs",
        ".eslintrc.js",
        ".eslintrc.json",
    ):
        if has(f):
            lints.append("eslint")
    if has(".prettierrc") or has("prettier.config.js"):
        lints.append("prettier")
    if lints:
        info["linter"] = ", ".join(lints)

    return info


def render_template(info: Optional[dict] = None, root: Optional[Path] = None) -> str:
    """Return the filled-in template string."""
    if info is None:
        info = _detect_project(root or Path(os.getcwd()))
    return DEFAULT_TEMPLATE.format(**info)


def generate(
    name: str = "CLAUDE.md",
    root: Optional[str] = None,
    overwrite: bool = False,
) -> str:
    """Write the template to `name` under `root`. Returns the file path.

    Refuses to overwrite an existing file unless overwrite=True.
    """
    base = Path(root) if root else Path(os.getcwd())
    target = base / name
    if target.exists() and not overwrite:
        raise FileExistsError(
            f"{target} already exists (use overwrite=True to replace)"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_template(root=base), encoding="utf-8")
    return str(target)


def exists(root: Optional[str] = None, name: str = "CLAUDE.md") -> bool:
    base = Path(root) if root else Path(os.getcwd())
    return (base / name).exists()


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate a pycode project-context file"
    )
    parser.add_argument(
        "--name", default="CLAUDE.md", help="File name to create (default: CLAUDE.md)"
    )
    parser.add_argument("--root", default=".", help="Project root (default: cwd)")
    parser.add_argument(
        "--force", action="store_true", help="Overwrite if the file exists"
    )
    parser.add_argument(
        "--show", action="store_true", help="Print to stdout instead of writing"
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1

    if args.show:
        print(render_template(root=root))
        return 0

    try:
        path = generate(name=args.name, root=str(root), overwrite=args.force)
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"Wrote {path}")
    print("Edit it to add your project's specific conventions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
