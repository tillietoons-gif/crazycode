# pycode Release Checklist

Run through this list before tagging a release.

## 1. Version bump

- [ ] `__version__` updated in `src/pycode/__init__.py`
- [ ] `version` updated in `pyproject.toml`
- [ ] Banner string in `src/pycode/tui.py` (if it shows the version) matches
- [ ] README banner box shows the new version

## 2. Verification

- [ ] Full suite green: `python3 -m unittest discover -s tests`
- [ ] New milestones added a `tests/test_v1xx.py` and its tests pass
- [ ] `python3 -m pycode --help` runs and lists every documented flag
- [ ] README "CLI reference" matches `--help` exactly
- [ ] README tool list matches `TOOL_SCHEMAS` (count + names)
- [ ] README test count matches `unittest` output
- [ ] `python3 -c "import pycode; print(pycode.__version__)"` prints the version

## 3. Smoke tests (manual)

- [ ] `pycode --non-interactive "hi"` with a real key returns a response
- [ ] `pycode --dry-run "refactor something"` stages diffs and the keyboard
      reviewer works
- [ ] REPL: `/help`, `/map`, `/symbols <name>`, `/theme <name>`, `/plan`,
      `/loop`, `/save`, `/resume`, `/export` all respond
- [ ] Esc during a turn aborts cleanly (`[aborted by user]`)
- [ ] `--no-config` ignores config files; project config shows in startup log
- [ ] A `.pycode/tools/*.py` plugin loads; a broken plugin logs a warning
- [ ] A `.pycode/commands/*.md` command expands with `$ARGS`

## 4. Repository hygiene

- [ ] `git status` clean after commit (no stray runtime artifacts; note
      `.pycode/index/` and `.pycode-sessions/` are gitignored)
- [ ] CHANGELOG/commit history reflects the milestone
- [ ] All work pushed: `git push origin main`

## 5. Tagging

```bash
# tag the release (annotated, signed-off style message)
git tag -a vX.Y.Z -m "pycode vX.Y.Z"

# push the tag
git push origin vX.Y.Z
```

## 6. Post-release

- [ ] Update the roadmap spec (`docs/superpowers/specs/`) with shipped status
- [ ] Close milestone issues/notes
