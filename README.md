# pycode

A Python-based AI coding agent — a Claude Code alternative that runs anywhere
Python 3.10+ does, with **zero heavyweight dependencies** (only `requests`).

pycode is an autonomous coding agent: you give it a natural-language task, and
it plans, reads/writes files, runs shell commands, searches code, and fetches
docs — using a tool-calling loop against any OpenAI-compatible LLM (OpenAI,
Anthropic, Ollama, Venice, OpenRouter, and more).

```
┌──────────────────────────────────────────────────────────────────────┐
│  pycode · v1.2.0                                                     │
│                                                                      │
│  15 tools · 5 provider presets · MCP plugins · subagents             │
│  project symbol map · background jobs · hooks · themes               │
│  SSE streaming · Esc-to-abort · self-review · auto-fix loop          │
│  framed input · thinking indicator · turn panels                     │
│  installers for Linux/macOS/Windows · setup wizard                   │
│  313 passing tests                                                   │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Features

### Core agent loop
- **Tool-calling loop** — the LLM plans a step, calls a tool, observes the
  result, and repeats until the task is done (up to a configurable iteration
  cap).
- **15 built-in tools** — `bash`, `read`, `write`, `edit`, `glob`, `grep`,
  `webfetch`, `web_search`, `view_image`, `todo`, plus background jobs
  (`bash_background`, `job_output`, `job_list`, `job_kill`) and `symbols`.
- **Real SSE token streaming** with live output; any streaming failure falls
  back to a single request automatically.
- **Plan mode** — `/plan <goal>` drafts a numbered plan, then executes each
  step as its own checkpointed turn.
- **Background jobs** — run dev servers, long builds, or installers without
  blocking the loop; the LLM polls output and can kill jobs.
- **Hooks** — shell commands on `pre_tool` / `post_tool` / `on_turn` events
  (e.g. run your formatter after every edit). Failures are never fatal.
- **Persistent conversation** with checkpointed history.

### Multi-provider
- **Provider presets** — one-line setup for OpenAI, Anthropic, Ollama,
  Venice, OpenRouter (plus auto-detection from env vars).
- **Provider failover** — a config-driven chain that tries each provider in
  order and sticks to the last one that succeeded.
- **Prompt caching** — a byte-stable system-prompt prefix so caching-enabled
  providers hit the cache.
- **Streaming + non-streaming fallback** — automatic.

### Safety & control
- **Permission model** — `.pycode/permissions.toml` policy: tool allowlists,
  a built-in catastrophic-bash blocklist, `write_root` sandbox, and a YOLO
  mode.
- **Dry-run diff review** — `write`/`edit` changes are staged and shown as
  diffs; a keyboard reviewer (`i/a/r/h/n/q/?`) gates application.
- **Destructive-action confirmations** — `rm -rf /`, `shutdown`,
  `git push --force`, `DROP DATABASE`, … are blocked by default.

### Recovery & introspection
- **Session save/resume** to JSONL, plus an arrow-key session picker.
- **Checkpoint rewind / branching** — roll back to any user turn and try a
  different path.
- **Subagents** — the `task` tool spawns a focused child agent that returns a
  summary, keeping the parent context clean.
- **Cost accounting** — per-turn and session token/cost estimates with a live
  status bar.
- **HTML session export** — a self-contained styled report of any session.

### Extensibility
- **MCP support** — connect external Model-Context-Protocol servers as
  additional tools.
- **Python tool SDK** — drop `SCHEMA` + `run(args)` modules into
  `.pycode/tools/`; they appear as regular tools (permission-guarded, and
  `DESTRUCTIVE = True` adds a confirmation prompt).
- **Custom slash commands** — `.pycode/commands/<name>.md` prompt templates
  become REPL commands; `$ARGS` is replaced with the typed arguments.
- **Tool-file hooks** — plugins may export a `HOOKS` dict that merges into
  the agent's hook runner.
- **Project context auto-load** — `CLAUDE.md`, `.pycode.md`, `AGENTS.md`, and
  a built-in template generator.

### Codebase intelligence
- **Project symbol map** — regex-based index of ~10 languages, cached under
  `.pycode/index/`, injected as a compact map into the system prompt.
- **`symbols` tool** — the LLM can find definitions (`find`), usages (`refs`),
  or ask for the whole-project map (`map`).
- **Smart `read`** — `symbol=NAME` reads around a definition instead of
  offset/limit line-hunting.

### Model quality
- **Thinking models** — `reasoning_content` deltas are parsed from the stream
  and summarized as a dim one-liner; the reasoning is returned in the result.
- **Self-review pass** (`--self-review`) — after edits apply, a reviewer call
  checks the git diff and may request exactly one corrective revision.
- **Auto-fix loop** (`--agent-loop "goal"` / `/loop goal`) — plan → do → test
  → fix cycle up to `--loop-max` attempts (default 5). Because you launched
  it explicitly, it may edit files unattended per your permissions policy.

### Terminal UX
- **Framed input box** — `╭─ you ─╮ / │ ❯ / ╰─╯` around every prompt
  (readline editing, history, and tab-completion all keep working).
- **Thinking indicator** — animated spinner with a live elapsed timer while
  the model responds (`✻ thinking · iter 1 · 2.4s`), plus a one-line
  reasoning summary for thinking models.
- **Live tool status** — `running bash · 1.1s` spinner per tool call
  (never during a confirmation prompt).
- **Turn summary panel** — after each turn: tool-call count, duration, and
  edited files with `+add/−del` counts from git.
- **ANSI markdown + code highlighter** (python/js/ts/go/rust/bash/json/sql).
- **Live status bar** for context, cost, and model.
- **Activity feed** with ✓/✗/→ glyphs and expandable detail.
- **Slash-command tab-completion**, aliases, and a `/help` listing.
- **Color themes** (`default`, `mono`, `dracula`, `nord`, `solarized`).

---

## Installation

### One-command installers (recommended)

Grab the latest release from the project's GitHub **Releases** page, then:

**Linux / macOS:**

```bash
sh install.sh
```

**Windows (PowerShell):**

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

Both scripts check prerequisites (Python 3.10+, git), install into a
persistent venv, and put `pycode` on your PATH. Locations are overridable
via `PYCODE_HOME` / `PYCODE_REPO` env vars.

### Prebuilt binaries

Each release also ships standalone binaries — no Python needed:

- `pycode-linux-x86_64`
- `pycode-macos-arm64` (Apple Silicon; Intel Macs can run it via Rosetta)
- `pycode-windows-x86_64.zip`

Download one, `chmod +x` it (unix), and run it like the `pycode` command.

### From source

```bash
pip install -e .
```

Requires Python ≥ 3.10. The only runtime dependency is `requests`.

---

## Configuration

pycode reads LLM credentials from environment variables (or CLI flags).
Copy the example and export the values you need:

```bash
cp .env.example .env
```

| Variable | Default | Purpose |
|---|---|---|
| `PYCODE_API_KEY` / `OPENAI_API_KEY` | – | LLM API key |
| `PYCODE_API_BASE` / `OPENAI_API_BASE` | Venice | Base URL for the OpenAI-compatible API |
| `PYCODE_MODEL` / `OPENAI_MODEL` | `deepseek-v4-1-flash` | Model name |
| `PYCODE_TEMPERATURE` | `0.3` | Sampling temperature |
| `PYCODE_MAX_TOKENS` | `8192` | Max response tokens |
| `PYCODE_SYSTEM_PROMPT_FILE` | – | Path to extra system instructions |

You can also use a named preset, which fills in the base URL + model + key env
var for you:

| Preset | Default model | Key env var |
|---|---|---|
| `openai` | `gpt-4o` | `OPENAI_API_KEY` |
| `anthropic` | `claude-sonnet-4-5` | `ANTHROPIC_API_KEY` |
| `ollama` | `llama3.1:8b` | (local, optional) |
| `venice` | `deepseek-v4-1-flash` | `VENICE_API_KEY` |
| `openrouter` | `anthropic/claude-3.5-sonnet` | `OPENROUTER_API_KEY` |

### First-run setup wizard

The first interactive launch without a configured key opens a short setup
wizard automatically:

1. pick a provider (openai / anthropic / ollama / venice / openrouter)
2. paste your API key (skipped for local Ollama)
3. optionally override the model

The result is written to `<project>/.pycode/config.toml` when the directory
is writable, else `~/.config/pycode/config.toml`, and the wizard marks
itself done so it never nags again.

Re-run it any time with:

```bash
pycode --wizard        # force the wizard
pycode --no-wizard     # never auto-run it
```

### Config files

For settings you want to keep, use a TOML config file instead of env vars.
Both a user-level and a project-level file are read and merged
(CLI flags > env vars > project config > user config > preset defaults):

- `~/.config/pycode/config.toml` (or `~/.pycode/config.toml`)
- `<project>/.pycode/config.toml`

Start from the example:

```bash
cp .pycode/config.toml.example .pycode/config.toml
```

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

Pass `--no-config` to ignore both files, or `--theme <name>` to override the
theme for a single run.

### Hooks

Declare shell commands in the `[hooks]` section; they fire on agent lifecycle
events with a 30s timeout. Failures are logged, never fatal. Placeholders:
`{tool}`, `{path}`, `{args_json}`, `{ok}`.

```toml
[hooks]
post_tool = "black --quiet {path}"   # format after every tool call
on_turn   = "echo turn done"
```

Events: `pre_tool`, `post_tool`, `tool_denied`, `on_turn`.

### Plugins

**Python tools** — create `.pycode/tools/wc_chars.py`:

```python
SCHEMA = {
    "type": "function",
    "function": {
        "name": "wc_chars",
        "description": "Count characters in a file",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}

def run(args):          # args is the arguments dict
    ...
```

It becomes a callable tool for the LLM on the next launch. A plugin whose
name collides with a built-in is skipped; a broken plugin is reported and
skipped, never fatal.

**Custom slash commands** — create `.pycode/commands/review.md`:

```markdown
Review the file $ARGS for style issues and report the top 3 findings.
```

Then `/review src/app.py` expands to the prompt with `$ARGS` replaced.

---

## Quick start

```bash
# 1. configure a provider (pick one)
export OPENAI_API_KEY="sk-..."            # or use --preset ollama for local

# 2. run a one-shot task
pycode "create a hello-world Flask app and a test for it"

# 3. or start an interactive session
pycode
```

Useful one-liners:

```bash
# local Ollama (free, no key)
pycode --preset ollama "explain this repo"

# preview changes before they apply, then approve each diff
pycode --dry-run "refactor the auth module"

# show live token/cost accounting
pycode --cost "build the CLI parser"

# run everything unattended
pycode --auto-approve "run the test suite and fix failures"
```

---

## CLI reference

```
pycode [PROMPT...] [OPTIONS]

PROMPT            Optional initial prompt (omit for interactive REPL)

Provider
  --api-key, --api-base, --model    Override credentials
  --preset {openai,anthropic,ollama,venice,openrouter,auto}
  --temperature, --max-tokens
  --system-prompt-file PATH

Safety / review
  --dry-run          Preview write/edit changes as diffs
  --auto-approve     Skip confirmations
  --yolo             Auto-approve + skip all confirmations (dangerous)
  --permissions PATH Load a permissions.toml policy

Context / limits
  --context-budget N  Context token budget (default 60000)
  --max-iterations N  Max tool-loop iterations (default 30)
  --project-root PATH
  --no-map            Disable the project symbol map

Autonomy
  --agent-loop GOAL   Auto-fix loop toward GOAL, then exit
  --loop-max N        Max loop attempts (default 5)
  --self-review       Reviewer call after edits; may request one revision

Extensibility
  --mcp FILE        MCP server config (repeatable)
  --failover FILE   Provider failover JSON
  --enable-subagents

Sessions
  --resume FILE     Resume from a saved JSONL session
  --export-html FILE   Export the session to styled HTML on exit

Cost / TUI
  --cost / --no-cost
  --plain           Plain text output (disable all TUI)
  --color MODE       ANSI colors: auto, always, or never
  --theme NAME      Color theme (default|mono|dracula|nord|solarized)
  --no-tab-complete
  --no-config       Ignore config.toml files

Output
  --quiet, --non-interactive
  --force-onboard     Show first-run onboarding even with a key present

Setup wizard
  --wizard            Run the setup wizard now
  --no-wizard         Never auto-run the wizard
```

### In-REPL commands

| Command | Description |
|---|---|
| `:clear` / `:c` | Reset the conversation |
| `/save [file]` | Save the session to JSONL |
| `/resume [file]` | Load a saved session (latest if omitted) |
| `/sessions` | Interactive picker of saved sessions |
| `/rewind [n]` | Roll back to checkpoint *n* (or list them) |
| `/branch n instr` | Branch from checkpoint *n* with a new instruction |
| `/context` | Context-window usage bar |
| `/cost` | Token/cost accounting for the session |
| `/config` | Inspect provider chain, permissions, and MCP servers |
| `/new-context` | Generate a `CLAUDE.md` project-instructions file |
| `/export [file]` | Export the session to styled HTML |
| `/preset` | List provider presets |
| `/theme [name]` | Show or switch the color theme |
| `/plan <goal>` | Draft a numbered plan, then execute each step |
| `/loop <goal>` | Auto-fix loop toward a goal (up to --loop-max attempts) |
| `/map` | Show the project symbol map |
| `/symbols <name>` | Look up symbols in the project index |
| `help` / `?` | Full command list + aliases |
| `quit` / `exit` / `:q` | Stop |

---

## Project layout

```
src/pycode/
  agent.py            core agent loop, tool dispatch, checkpoints, subagents
  cli.py              argparse CLI + interactive REPL
  provider.py         OpenAI-compatible client (stream + fallback)
  providers.py        provider presets & auto-detection
  failover.py         multi-provider failover chain
  config.py           layered TOML config (user + project)
  interrupts.py       Esc-to-abort controller + listener
  index.py            project symbol index (10+ languages, mtime cache)
  jobs.py             background job manager (bash_background / job_*)
  plugins.py          user tool SDK, custom slash commands, tool-file hooks
  hooks.py            pre_tool / post_tool / on_turn shell hooks
  tools.py            the 15 tools + dispatch + destructive detection
  permissions.py      .pycode/permissions.toml policy engine
  subagents.py        task-tool subagent dispatch
  rewind.py           checkpoint / branch manager
  session.py          JSONL session save/load
  session_export.py   styled HTML export
  context.py          CLAUDE.md / AGENTS.md auto-load
  context_manager.py  token estimation + auto-trim
  cost.py             token/cost accounting
  mcp.py              MCP stdio client
  scaffold.py         CLAUDE.md template generator
  onboarding.py       first-run setup guidance
  wizard.py           interactive first-run setup wizard
  tui*.py             terminal UI (markdown, status bar, feed, reviewer,
                       picker, context view, subagent trace, inspector, pager)
  tui_theme.py        named color themes
  tui_input.py        framed input box
  tui_turn.py         turn summary panel (tools, duration, files changed)
tests/                pytest suite (313 tests across multiple modules)
docs/                 roadmap spec + release checklist
.env.example          copy-pasteable configuration template
.pycode/              project-level config, policies & auto-saved sessions
```

---

## Project-specific rules (optional)

Drop a `CLAUDE.md` (or `.pycode.md`, `AGENTS.md`) in your project root and
pycode auto-loads it into the system prompt. Generate a starter file with:

```bash
python -m pycode.scaffold        # auto-detects the project and writes ./CLAUDE.md
python -m pycode.scaffold --name AGENTS.md --show   # preview to stdout
```

---

## Using MCP servers

Connect external tools via a JSON config:

```bash
pycode --mcp mcp_servers.jsonl
```

`mcp_servers.jsonl` (one object per line, or a JSON array):

```json
{"name": "github", "command": ["npx", "-y", "@modelcontextprotocol/server-github"], "env": {"GITHUB_TOKEN": "ghp_..."}}
```

MCP tools appear alongside the built-ins, exposed to the LLM with the name
`mcp_<server>_<tool>` (e.g. `mcp_github_search`), and the agent routes calls
back to the owning server.

---

## Provider failover

Configure a chain that tries providers in order:

```bash
pycode --failover providers.json "my task"
```

```json
{
  "default": 0,
  "providers": [
    {"name": "venice",     "api_key": "VENICE_ADMIN_KEY_...", "api_base": "https://api.venice.ai/api/v1", "model": "deepseek-v4-1-flash"},
    {"name": "openrouter", "api_key": "sk-or-...",            "api_base": "https://openrouter.ai/api/v1", "model": "anthropic/claude-3.5-sonnet"},
    {"name": "ollama",     "api_base": "http://localhost:11434/v1", "model": "llama3.1:8b"}
  ]
}
```

The first provider that answers is used; later calls stick to it.

---

## Security model

By default, pycode **denies** catastrophic bash commands and **confirms**
any state-mutating action. You can tighten this with a policy file:

```toml
# .pycode/permissions.toml
allow = ["read", "glob", "grep", "bash", "edit", "write"]
write_root = "src/"            # writes only under src/
# bash_blocklist = ["sudo"]    # extra denied substrings
# yolo = false                 # set true to skip confirmations
```

The built-in blocklist (always active) includes `rm -rf /`, `shutdown`,
`reboot`, `mkfs`, `dd if=/dev/zero`, the fork bomb, `git push --force`,
`DROP DATABASE`, and `TRUNCATE TABLE`.

For unattended runs, use `--auto-approve` (skips confirmations but keeps the
blocklist) or `--yolo` (skips everything except the hard-coded catastrophic
list).

---

## Development

```bash
# run the test suite
PYTHONPATH=src pytest -q                     # 313 passing tests

# run a single module's tests
PYTHONPATH=src pytest -q tests/test_v08.py

# watch live
python -m pytest -xvs 2>/dev/null || python -m unittest -v
```

All source modules are stdlib-only plus `requests`, so contributions stay easy
to run.

---

## License

This repository does not currently include a LICENSE file, so licensing terms are not yet declared in the project root.
