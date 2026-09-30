#!/bin/bash
# Installs claude-speaks: Claude Code reads its replies out loud in a cloned voice, locally.
#
#   ./install.sh               install (or update)
#   ./install.sh --uninstall   remove the hook, the commands and the program (keeps your voices)
#
# Optional variables:
#   CLAUDE_SPEAKS_HOME   install folder (default ~/.claude-speaks)
#   CLAUDE_CONFIG_DIR    Claude Code config folder (default ~/.claude)
#   BIN_DIR              where to put the claude-speaks command (default ~/.local/bin)
set -euo pipefail
umask 077

SRC="$(cd "$(dirname "$0")" && pwd)"
CS_HOME="${CLAUDE_SPEAKS_HOME:-$HOME/.claude-speaks}"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
MARKER="$CS_HOME/.installed-by-claude-speaks"
MLX_AUDIO_VERSION="0.5.7"          # tested version
MODEL="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16"
WRAPPER_TAG="# claude-speaks wrapper"
COMMAND_TAG='claude-speaks $ARGUMENTS'

step() { printf '\n== %s\n' "$1"; }
die()  { printf '\nERROR: %s\n' "$1" >&2; exit 1; }
ours_wrapper() { [ -f "$1" ] && grep -qF "$WRAPPER_TAG" "$1"; }
ours_command() { [ -f "$1" ] && grep -qF "$COMMAND_TAG" "$1"; }

if [ "${1:-}" = "--uninstall" ]; then
  if [ -f "$MARKER" ]; then
    # Use the folders chosen at install time, not the current environment.
    CLAUDE_DIR="$(sed -n 's/^CLAUDE_DIR=//p' "$MARKER")"
    BIN_DIR="$(sed -n 's/^BIN_DIR=//p' "$MARKER")"
  fi
  # The hook goes first. If it cannot be removed, nothing else is touched:
  # a hook pointing to a deleted program would fail on every Claude reply.
  removed=""
  for py in "$CS_HOME/venv/bin/python" python3; do
    if CLAUDE_CONFIG_DIR="$CLAUDE_DIR" CLAUDE_SPEAKS_HOME="$CS_HOME" \
         "$py" "$CS_HOME/claude_speaks.py" hook uninstall 2>/dev/null; then
      removed=1; break
    fi
  done
  [ -n "$removed" ] || [ ! -f "$CS_HOME/claude_speaks.py" ] \
    || die "could not remove the hook from $CLAUDE_DIR/settings.json. Remove the claude-speaks entry under hooks.Stop by hand, then run this again."
  pkill -f "$CS_HOME/claude_speaks.py serve" 2>/dev/null || true
  ours_wrapper "$BIN_DIR/claude-speaks" && rm -f "$BIN_DIR/claude-speaks"
  ours_command "$CLAUDE_DIR/commands/claude-speaks.md" && rm -f "$CLAUDE_DIR/commands/claude-speaks.md"
  if [ -f "$MARKER" ]; then
    rm -rf "$CS_HOME/venv"
    rm -f "$CS_HOME/claude_speaks.py" "$CS_HOME/speaks.sock" "$CS_HOME/server.lock" "$MARKER"
  fi
  echo "Uninstalled. Your voices, history and config stay in $CS_HOME (delete the folder if you don't want them)."
  exit 0
fi

step "Checking the machine"
[ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ] \
  || die "this needs an Apple Silicon Mac (M1 or newer). MLX does not run anywhere else."
command -v ffmpeg >/dev/null || die "ffmpeg is missing. Install it with: brew install ffmpeg"

PY=""
for cand in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$(command -v "$cand")"; break
  fi
done
[ -n "$PY" ] || die "Python 3.10 or newer is missing. Install it with: brew install python"
echo "Python: $PY ($("$PY" --version))"

# Never overwrite something that belongs to someone else.
[ -e "$BIN_DIR/claude-speaks" ] && ! ours_wrapper "$BIN_DIR/claude-speaks" \
  && die "$BIN_DIR/claude-speaks already exists and was not installed by claude-speaks. Move it away or set BIN_DIR."
[ -e "$CLAUDE_DIR/commands/claude-speaks.md" ] && ! ours_command "$CLAUDE_DIR/commands/claude-speaks.md" \
  && die "$CLAUDE_DIR/commands/claude-speaks.md already exists and is not ours. Move it away first."
[ -d "$CS_HOME/venv" ] && [ ! -f "$MARKER" ] \
  && die "$CS_HOME/venv already exists and was not created by claude-speaks. Set CLAUDE_SPEAKS_HOME to another folder."

step "Private Python environment in $CS_HOME/venv"
mkdir -p "$CS_HOME"
chmod 700 "$CS_HOME"
[ -x "$CS_HOME/venv/bin/python" ] || "$PY" -m venv "$CS_HOME/venv"
printf 'CLAUDE_DIR=%s\nBIN_DIR=%s\n' "$CLAUDE_DIR" "$BIN_DIR" > "$MARKER"
"$CS_HOME/venv/bin/pip" install --quiet --upgrade pip
"$CS_HOME/venv/bin/pip" install --quiet "mlx-audio==$MLX_AUDIO_VERSION"

step "Downloading the voice model (about 2.5 GB, only once)"
"$CS_HOME/venv/bin/python" -c "from huggingface_hub import snapshot_download; snapshot_download('$MODEL')"

step "Installing the claude-speaks command"
cp "$SRC/claude_speaks.py" "$CS_HOME/claude_speaks.py"
mkdir -p "$BIN_DIR"
{
  echo '#!/bin/bash'
  echo "$WRAPPER_TAG"
  printf 'export CLAUDE_SPEAKS_HOME="${CLAUDE_SPEAKS_HOME:-%q}"\n' "$CS_HOME"
  printf 'export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-%q}"\n' "$CLAUDE_DIR"
  printf 'export CLAUDE_SPEAKS_BIN=%q\n' "$BIN_DIR/claude-speaks"
  printf 'exec %q %q "$@"\n' "$CS_HOME/venv/bin/python" "$CS_HOME/claude_speaks.py"
} > "$BIN_DIR/claude-speaks"
chmod 755 "$BIN_DIR/claude-speaks"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "NOTE: $BIN_DIR is not in your PATH. Add this line to ~/.zshrc:"
     echo "  export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

step "Installing the /claude-speaks command in Claude Code"
mkdir -p "$CLAUDE_DIR/commands"
cp "$SRC/commands/claude-speaks.md" "$CLAUDE_DIR/commands/claude-speaks.md"
chmod 644 "$CLAUDE_DIR/commands/claude-speaks.md"

cat <<EOF

Installed. Two steps left:

  1. Add your voice (10 to 30 seconds, see the README):
       claude-speaks voice add me my-recording.m4a "The exact words you say in the recording."

  2. Plug in the hook so Claude speaks at the end of every reply:
       claude-speaks hook install

Quick test: claude-speaks say "Hi, this is Claude."
EOF
