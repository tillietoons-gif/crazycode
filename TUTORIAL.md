# pycode Tutorial

This guide walks through how to install, configure, run, and use pycode — a terminal-first AI coding agent built for local development workflows.

## 1. What pycode is

pycode is a Python-based coding agent that can:

- understand a task in plain English
- inspect files and project structure
- read and edit code
- run shell commands and tests
- keep session context across turns
- use tool-based planning and loops to complete multi-step work
- work with multiple LLM providers through an OpenAI-compatible API

It is designed to feel similar to a conversational coding assistant, but it runs in the terminal and is built around explicit tool use and project context.

---

## 2. What this project includes

The app is packaged as a Python project under `src/pycode` and exposes a CLI entry point:

```bash
pycode
```

Core pieces include:

- `src/pycode/cli.py` — command-line entry and interactive flow
- `src/pycode/agent.py` — core agent loop and orchestration
- `src/pycode/tools.py` — built-in tools such as bash, read, write, edit, grep, glob, and web access
- `src/pycode/providers.py` — provider presets and environment detection
- `src/pycode/config.py` — layered config handling
- `src/pycode/tui.py` — terminal dashboard and keyboard-driven UI
- `src/pycode/session.py` — saved session handling
- `src/pycode/hooks.py` and `src/pycode/permissions.py` — automation and safety rules

---

## 3. Installation

### Option A: local editable install

From the project root:

```bash
pip install -e .
```

This installs the package in editable mode so changes in the source tree are immediately reflected.

### Option B: from source with PYTHONPATH

If you want to run it directly without installation:

```bash
PYTHONPATH=src python -m pycode
```

### Option C: use the installer scripts

The project also includes:

- `install.sh` for Linux/macOS
- `install.ps1` for Windows

These scripts install pycode into a managed environment and place the command on PATH.

---

## 4. Prerequisites

pycode requires:

- Python 3.10+
- `requests`
- a working API key for an OpenAI-compatible provider, or a local provider such as Ollama
- git for repo-aware workflows

The project uses environment variables or config files for API access.

---

## 5. Configuration

### Environment variables

You can configure the app with environment variables such as:

```bash
export PYCODE_API_KEY="..."
export PYCODE_API_BASE="https://api.openai.com/v1"
export PYCODE_MODEL="gpt-4o"
```

The repository also includes `.env.example` with the standard variables.

### Config files

pycode reads config files in layers:

- project config: `.pycode/config.toml`
- user config: `~/.config/pycode/config.toml` or `~/.pycode/config.toml`
- CLI flags override those values

Example:

```toml
theme = "nord"
auto_approve = false
context_budget = 80000
max_iterations = 40

[provider]
model = "gpt-4o"
api_base = "https://api.openai.com/v1"
temperature = 0.2
max_tokens = 4096
```

### Provider presets

The project supports presets such as:

- `openai`
- `anthropic`
- `ollama`
- `venice`
- `openrouter`
- `auto`

Example:

```bash
pycode --preset ollama "explain this repo"
```

---

## 6. First run and setup wizard

On first launch without a valid key, pycode can trigger a setup wizard.

You can force it with:

```bash
pycode --wizard
```

Or disable auto-wizard behavior with:

```bash
pycode --no-wizard
```

The wizard helps you choose a provider, paste a key, and optionally override the model.

---

## 7. Running a prompt

### One-shot mode

```bash
pycode "create a hello-world Flask app and a test for it"
```

This runs the agent once and exits when the task is done.

### Interactive mode

```bash
pycode
```

This starts the REPL-style interactive session, where you can keep chatting with the agent and issue commands.

### Non-interactive mode

```bash
pycode --non-interactive "run the tests and fix any failure"
```

This avoids the REPL and exits after one turn or a controlled loop.

---

## 8. The built-in tool system

pycode is not just a chat model. It uses tools to act on your filesystem and project state.

Built-in tools include:

- `bash` — run shell commands
- `read` — read file contents
- `write` — create or overwrite a file
- `edit` — perform a targeted text replacement
- `glob` — find files by pattern
- `grep` — search text with regex
- `webfetch` — fetch URL contents
- `web_search` — search the web
- `todo` — manage session todos
- `view_image` — inspect local image files
- `bash_background` — start long-running jobs in the background
- `job_output` / `job_list` / `job_kill` — manage background jobs
- `symbols` — inspect project symbol definitions and references

These are exposed as JSON-schema tool definitions for an LLM function-calling loop.

### Safety and approval

Mutating tools such as `write` and `edit` are treated as potentially destructive and may trigger confirmation prompts depending on the app settings and permissions.

The project also includes a permissions system with a built-in blocklist for dangerous operations.

---

## 9. Safety and permissions

pycode supports policy-based control through `.pycode/permissions.toml`.

Example:

```toml
allow = ["read", "glob", "grep", "bash", "edit", "write"]
write_root = "src/"
```

Safety mechanics include:

- destructive action confirmation prompts
- blocklisted dangerous shell commands
- rule-based allowlists
- `--auto-approve` to skip confirmations for trusted runs
- `--yolo` for full bypass of confirmation prompts (dangerous)

Useful CLI flags:

```bash
pycode --dry-run "refactor the auth module"
pycode --auto-approve "run the test suite and fix failures"
pycode --always-yes "apply the requested edits without prompting"
pycode --yolo "upgrade dependencies"
```

You can also persist the behavior in your project config:

```toml
always_yes = true
```

This disables the interactive approval prompt for destructive actions during local development.

---

## 10. Context, memory, and sessions

pycode keeps a session and can resume prior work.

Common session features:

- save progress to JSONL files
- resume from a prior saved session
- export a session to HTML
- rewind or branch from checkpoints
- review past context and tool actions

Examples:

```bash
pycode --resume session.jsonl
pycode --export-html report.html
```

In the REPL, commands include:

- `:clear` / `:c` — reset the conversation
- `/save [file]` — save a session
- `/resume [file]` — restore a session
- `/sessions` — browse saved sessions
- `/rewind [n]` — roll back to a checkpoint
- `/branch n instr` — branch from a checkpoint

---

## 11. Project context and indexing

pycode can automatically load project guidance files such as:

- `CLAUDE.md`
- `.pycode.md`
- `AGENTS.md`

These are injected into the system prompt as project-specific instructions.

You can also generate a starter file with:

```bash
python -m pycode.scaffold
```

or:

```bash
python -m pycode.scaffold --name AGENTS.md --show
```

The project maintains a symbol index for quick lookup of definitions and references.

Useful commands:

- `/map` — show the project symbol map
- `/symbols <name>` — search the symbol index

---

## 12. Planning and auto-fix loops

pycode supports stronger autonomous workflows.

### Plan mode

```bash
pycode
# then in the REPL:
/plan "fix the failing CI checks"
```

This creates a numbered plan and executes the steps in order.

### Auto-fix loop

```bash
pycode --agent-loop "fix the failing tests"
```

or:

```bash
pycode
# then:
/loop "fix the failing tests"
```

This runs a loop of plan → do → test → fix until the goal is met or the loop cap is reached.

### Self-review mode

```bash
pycode --self-review "refactor the parser"
```

This adds a second-pass review after edits are applied, which can request one corrective revision.

---

## 13. Background jobs and long-running tasks

For long tasks like servers, builds, or installs, pycode supports the background job pattern.

Examples:

- `bash_background` starts a detached shell job
- `job_output` checks the output
- `job_list` shows active jobs
- `job_kill` terminates a running one

This lets the agent keep working while external processes continue in the background.

---

## 14. Terminal UI and dashboard

pycode includes a live terminal dashboard and keyboard-driven interface.

The TUI can show:

- project/session cards
- nav sections such as Inbox / Active / Review / Done
- selected item detail panels
- activity metadata
- project health signals
- session counts and review counts
- command palette with keyboard actions

The actual interactive dashboard is built in `src/pycode/tui.py`.

### Keyboard behaviors

Typical keys include:

- `↑` / `k` / `A` to move upward
- `↓` / `j` / `B` to move downward
- `Enter` or `o` to open/select
- `f` to filter
- `a` to clear filters
- `:` to open the command palette
- `q` or `Esc` to quit
- `/` to search

The TUI focuses on a Linear-style board with rich, terminal-friendly formatting.

---

## 15. Themes and output styling

The app supports several themes:

- `default`
- `mono`
- `dracula`
- `nord`
- `solarized`

Example:

```bash
pycode --theme nord
```

You can also set `theme` in a config file.

Color controls include:

```bash
pycode --color always
pycode --color never
pycode --color auto
```

---

## 16. Cost tracking and usage visibility

The app can display live token/cost summaries for the session.

Examples:

```bash
pycode --cost "build the CLI parser"
```

In the REPL:

```text
/cost
/context
```

This gives visibility into how much context and cost the current session has consumed.

---

## 17. MCP support

pycode can connect external MCP servers as additional tools.

Example config:

```json
{"name": "github", "command": ["npx", "-y", "@modelcontextprotocol/server-github"], "env": {"GITHUB_TOKEN": "ghp_..."}}
```

Then:

```bash
pycode --mcp mcp_servers.jsonl
```

These tools are exposed to the agent as additional callable tools.

---

## 18. Example workflows

### Workflow 1: fix a bug

```bash
pycode "find the bug in the parser and fix it with a focused test"
```

### Workflow 2: add a feature

```bash
pycode "add a new CLI option to export the session as markdown"
```

### Workflow 3: refactor safely

```bash
pycode --dry-run "refactor the config loader into smaller helper functions"
```

### Workflow 4: run long checks in the background

```bash
pycode "run the full test suite and fix any failures"
```

---

## 19. Useful commands cheat sheet

```bash
# install editable package
pip install -e .

# run the app
pycode

# run one prompt
pycode "write a hello world script"

# set a provider
export PYCODE_API_KEY="..."

# use a preset
pycode --preset ollama "explain this repo"

# dry run edits
pycode --dry-run "refactor the auth code"

# enable cost output
pycode --cost "run the tests"

# use a theme
pycode --theme nord

# export a session
pycode --export-html session_report.html

# resume a session
pycode --resume saved_session.jsonl
```

---

## 20. Best practices

- Keep project guidance in `CLAUDE.md` when you want agent-specific context.
- Use `--dry-run` before destructive or broad edits.
- Use sessions to save and resume long-running work.
- Prefer targeted reads and writes over broad file rewrites.
- Review the diff before approving changes.
- Use background jobs for build/test servers if you do not want to block the agent.

---

## 21. Troubleshooting

### "No API key found"

Set `PYCODE_API_KEY` or use a preset with the appropriate environment variable.

### "Module not found"

Run the app from the project root or install it in editable mode:

```bash
pip install -e .
```

### "Config file ignored"

Check your CLI flags and ensure you did not pass `--no-config`.

### "Background jobs not showing up"

Use the `job_list` tool or inspect the app session state.

---

## 22. Summary

pycode is a terminal-first AI coding assistant with a powerful loop, developer safety features, session memory, project indexing, and a keyboard-driven terminal dashboard. It is built to help you work on real codebases with a strong balance of autonomy, safety, and visibility.

The best way to start is simple:

```bash
pip install -e .
pycode --wizard
pycode
```

From there, you can begin with a focused task, let the agent inspect and edit the project, and review the work in a controlled, session-aware workflow.
