# pycode v1.0 Roadmap Design

Date: 2026-09-14
Status: Approved

## Decisions

- **Dependencies**: stdlib + `requests` only, strictly. SSE parsing, symbol
  indexing, and every other subsystem is hand-rolled.
- **Autonomy**: auto-fix / agent loops run fully unattended (may edit files)
  only when explicitly launched via `--agent-loop` or `/loop`. Normal turns
  keep existing confirmation behavior.
- **Platform**: Linux-first POSIX design; macOS mostly free; Windows gets
  best-effort plain mode (no cbreak/ESC, no color themes if not TTY).

## Milestone ladder

Each milestone ships as its own versioned commit with a green test suite.

### M1 - Agentic power (v0.10)

1. **Real SSE streaming** - `provider.chat_stream` uses
   `requests.post(stream=True)` with a hand-rolled `data:` line parser.
   Deltas print live while the turn runs. Any parse/HTTP error falls back to
   the existing single-POST path (keeps flaky providers working).
2. **Background tools** - `bash_background` starts a shell job (stdlib
   `subprocess.Popen`, output captured to a temp file); `job_output` /
   `job_list` / `job_kill` manage jobs. The LLM can run dev servers, long
   builds, or installs without blocking the loop.
3. **Hooks** - `hooks.py` event bus with `pre_tool`, `post_tool`, `on_turn`
   events. Shell-command hooks are declared in `[hooks]` inside config.toml
   with `{tool}`/`{path}` template expansion. Hook failures are logged, never
   fatal.
4. **Plan mode** - `/plan <goal>` (or `agent.run_plan(goal)`): one LLM call
   drafts a numbered plan, then each step runs as its own checkpointed turn.
   Steps are shown as they complete.

### M2 - Codebase intelligence (v0.11)

1. **Project map** - regex-based symbol index (`def`/`class`/`func`/`trait`
   etc. for ~10 languages) cached under `.pycode/index/`.
2. **Symbol tools** - `symbols` tool for go-to-def / find-references.
3. **Smart context** - `read` gains `around-symbol` mode; project map snippet
   injected into the system prompt.

### M3 - Extensibility (v0.11.5)

1. **Python tool SDK** - `.pycode/tools/*.py` exposing `SCHEMA` + `run(args)`
   are auto-registered; subject to the permissions system.
2. **Custom slash commands** - `.pycode/commands/*.md` prompt templates with
   `$ARGS` substitution become REPL commands.
3. **Tool-file hook registration**.

### M4 - Model quality (v0.12)

1. **Thinking models** - parse `reasoning_content` stream deltas; collapsible
   TUI display.
2. **Self-review pass** - after edits, a reviewer LLM call checks the diff and
   may request one revision.
3. **Auto-fix loop** - `/loop <goal>` / `--agent-loop "goal"`: plan -> do ->
   test -> fix, max N iterations; edits allowed because the user explicitly
   launched it.

### M5 - v1.0 polish

README overhaul, release checklist, version 1.0.0, tag.

## Error handling

Every subsystem degrades gracefully: SSE -> single POST, missing index -> no
symbol tool, no hooks -> no-op, non-TTY -> plain output.

## Testing

One `test_v1x.py` per milestone on top of the existing suite; full suite
green before every push.
