#!/usr/bin/env bash
# Install agent-coddies into Claude Code (macOS / Linux / Git Bash).
#
#   ./install.sh              install for the current user (~/.claude)
#   ./install.sh --project    install into ./.claude so the team gets it via git
#   ./install.sh --link       symlink the skill instead of copying it
#   ./install.sh --uninstall
set -euo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="agent-coddies"
SCOPE="user"
MODE="copy"
ACTION="install"
FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --project)   SCOPE="project" ;;
    --link)      MODE="link" ;;
    --uninstall) ACTION="uninstall" ;;
    --force)     FORCE=1 ;;
    -h|--help)   sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$SCOPE" = "project" ]; then ROOT="$PWD/.claude"; else ROOT="$HOME/.claude"; fi
SKILL_DIR="$ROOT/skills/$NAME"
AGENT_DIR="$ROOT/agents"
COMMAND_DIR="$ROOT/commands"

if [ "$ACTION" = "uninstall" ]; then
  echo "Removing $NAME from $ROOT"
  rm -rf "$SKILL_DIR"
  for a in coddie-dev coddie-qa coddie-ship; do rm -f "$AGENT_DIR/$a.md"; done
  rm -f "$COMMAND_DIR/coddies.md" "$COMMAND_DIR/coddies-status.md"
  echo "Done. $SOURCE/config/credentials.yaml was left untouched."
  exit 0
fi

echo "Installing $NAME"
echo "  source: $SOURCE"
echo "  target: $ROOT"

mkdir -p "$AGENT_DIR" "$COMMAND_DIR" "$(dirname "$SKILL_DIR")"

if [ -e "$SKILL_DIR" ] && [ "$FORCE" -eq 0 ]; then
  echo "  $SKILL_DIR already exists. Re-run with --force to replace it." >&2
  exit 1
fi
rm -rf "$SKILL_DIR"

if [ "$MODE" = "link" ]; then
  ln -s "$SOURCE" "$SKILL_DIR"
  echo "  linked  $SKILL_DIR -> $SOURCE"
else
  cp -R "$SOURCE" "$SKILL_DIR"
  # Never ship a filled-in credentials file or local run state into the install.
  rm -f "$SKILL_DIR/config/credentials.yaml"
  rm -rf "$SKILL_DIR/.coddies" "$SKILL_DIR/scripts/coddie/__pycache__"
  echo "  copied  $SKILL_DIR"
fi

for a in coddie-dev coddie-qa coddie-ship; do
  cp -f "$SOURCE/agents/$a.md" "$AGENT_DIR/$a.md"
  echo "  agent   $AGENT_DIR/$a.md"
done

if [ -d "$SOURCE/commands" ]; then
  for f in "$SOURCE"/commands/*.md; do
    [ -e "$f" ] || continue
    cp -f "$f" "$COMMAND_DIR/$(basename "$f")"
    echo "  command /$(basename "$f" .md)"
  done
fi

if [ ! -f "$SOURCE/config/credentials.yaml" ]; then
  cp "$SOURCE/config/credentials.example.yaml" "$SOURCE/config/credentials.yaml"
  chmod 600 "$SOURCE/config/credentials.yaml" 2>/dev/null || true
  echo
  echo "Created $SOURCE/config/credentials.yaml from the example."
  echo "Fill in jira.*, gitlab.* and (optionally) database.* before first use."
fi

echo
PYTHON="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON" ]; then
  echo "Python was not found on PATH. Install Python 3.9+ to use the toolkit."
else
  "$PYTHON" "$SOURCE/scripts/coddie_cli.py" config check --offline || true
fi

cat <<'MSG'

Installed. In Claude Code:
    /coddies HM-1234         run the pipeline on a ticket
    /coddies-status HM-1234  show the run ledger
Or just say: work on HM-1234
MSG
