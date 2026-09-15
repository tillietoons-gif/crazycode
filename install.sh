#!/bin/sh
# pycode installer for Linux and macOS.
#
#   sh install.sh
#
# What it does:
#   1. checks python3 (>= 3.10) and git
#   2. clones the repo and pip-installs it into a persistent venv
#   3. links the `pycode` command into ~/.local/bin (PATH hint at the end)
#
# Environment overrides:
#   PYCODE_REPO    git URL (default: the official repository)
#   PYCODE_HOME    install location (default: ~/.local/share/pycode)
#   PYCODE_BIN     bin dir for the symlink (default: ~/.local/bin)

set -eu

REPO="${PYCODE_REPO:-https://github.com/tillietoons-gif/crazycode}"
PYCODE_HOME="${PYCODE_HOME:-$HOME/.local/share/pycode}"
BIN_DIR="${PYCODE_BIN:-$HOME/.local/bin}"

fail() {
    echo "error: $1" >&2
    exit 1
}

echo "==> checking prerequisites"
command -v python3 >/dev/null 2>&1 || fail "python3 is required (3.10+)"
command -v git >/dev/null 2>&1 || fail "git is required"

PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
python3 - "$PYV" <<'EOF' || fail "python 3.10+ is required (found $1)"
import sys
sys.exit(0 if tuple(int(p) for p in sys.argv[1].split(".")) >= (3, 10) else 1)
EOF
echo "    python3 $PYV ok"

echo "==> fetching pycode"
SRC_DIR="$PYCODE_HOME/src"
mkdir -p "$PYCODE_HOME"
if [ -d "$SRC_DIR/.git" ]; then
    git -C "$SRC_DIR" pull --ff-only || echo "    (pull failed; keeping existing source)"
else
    git clone --depth 1 "$REPO" "$SRC_DIR"
fi

echo "==> installing into $PYCODE_HOME/venv"
python3 -m venv "$PYCODE_HOME/venv"
"$PYCODE_HOME/venv/bin/pip" install --quiet --upgrade pip
"$PYCODE_HOME/venv/bin/pip" install --quiet "$SRC_DIR"

echo "==> linking pycode into $BIN_DIR"
mkdir -p "$BIN_DIR"
ln -sf "$PYCODE_HOME/venv/bin/pycode" "$BIN_DIR/pycode"

echo ""
echo "pycode is installed."
echo ""
echo "Get started:"
echo "  pycode --wizard      first-run setup (provider + API key)"
echo "  pycode \"your task\"   run a task"
echo ""
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        echo "NOTE: $BIN_DIR is not on your PATH. Add this to ~/.bashrc or ~/.zshrc:"
        echo "  export PATH=\"$BIN_DIR:\$PATH\""
        ;;
esac
