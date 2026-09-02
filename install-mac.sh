#!/usr/bin/env bash
# install-mac.sh — set up FICO AI tooling on macOS
set -euo pipefail

FICO_DIR="$HOME/.fico"
LAUNCHAGENTS_DIR="$HOME/Library/LaunchAgents"
SCRIPTS_DIR="$(cd "$(dirname "$0")/fico-scripts" && pwd)"
PLISTS_DIR="$(cd "$(dirname "$0")/platform/mac/launchagents" && pwd)"
CLAUDE_DIR="$(cd "$(dirname "$0")/claude" && pwd)"
CLAUDE_DEST="$HOME/.claude"

echo "=== FICO AI Tooling — macOS Setup ==="
echo "FICO_DIR:     $FICO_DIR"
echo "Scripts from: $SCRIPTS_DIR"
echo ""

# ── 1. Detect Python 3 ─────────────────────────────────────────────────────
PYTHON_PATH=$(command -v python3 || command -v python || true)
if [[ -z "$PYTHON_PATH" ]]; then
  echo "ERROR: Python 3 not found. Install it via: brew install python"
  exit 1
fi
PY_VERSION=$("$PYTHON_PATH" --version 2>&1)
echo "Python: $PYTHON_PATH ($PY_VERSION)"

# ── 2. Create ~/.fico ─────────────────────────────────────────────────────
mkdir -p "$FICO_DIR"
echo "Created $FICO_DIR"

# ── 3. Copy scripts ───────────────────────────────────────────────────────
echo "Copying scripts to $FICO_DIR..."
for f in "$SCRIPTS_DIR"/*.py; do
  dest="$FICO_DIR/$(basename "$f")"
  if [[ -f "$dest" ]]; then
    echo "  [skip] $(basename "$f") — already exists (delete to overwrite)"
  else
    cp "$f" "$dest"
    chmod +x "$dest"
    echo "  [copy] $(basename "$f")"
  fi
done

# ── 4. Install pip dependencies ────────────────────────────────────────────
echo ""
echo "Installing Python dependencies..."
"$PYTHON_PATH" -m pip install --quiet --upgrade \
  requests \
  websocket-client \
  keyring \
  pyobjc-framework-Cocoa \
  rumps 2>/dev/null || true
echo "  Done."

# ── 5. Bootstrap ~/.teams_tokens.json ────────────────────────────────────
TOKENS_FILE="$HOME/.teams_tokens.json"
if [[ ! -f "$TOKENS_FILE" ]]; then
  cat > "$TOKENS_FILE" <<'EOF'
{
  "msalRefreshToken": "",
  "msalClientId": "",
  "graphToken": "",
  "mailGraphToken": "",
  "bearerToken": "",
  "skypeToken": ""
}
EOF
  echo "Created $TOKENS_FILE (fill in msalRefreshToken to enable fast-path refresh)"
else
  echo "$TOKENS_FILE already exists — skipping"
fi

# ── 6. Install LaunchAgents ────────────────────────────────────────────────
mkdir -p "$LAUNCHAGENTS_DIR"
echo ""
echo "Installing LaunchAgents..."

for plist_src in "$PLISTS_DIR"/*.plist; do
  label=$(basename "$plist_src" .plist)
  dest="$LAUNCHAGENTS_DIR/$label.plist"

  # Substitute PYTHON_PATH, FICO_DIR, and HOME_DIR placeholders
  sed \
    -e "s|PYTHON_PATH|$PYTHON_PATH|g" \
    -e "s|FICO_DIR|$FICO_DIR|g" \
    -e "s|HOME_DIR|$HOME|g" \
    "$plist_src" > "$dest"

  # Unload first (ignore errors if not loaded)
  launchctl unload "$dest" 2>/dev/null || true
  launchctl load "$dest"
  echo "  [load] $label"
done

# ── 7. Claude config ────────────────────────────────────────────────────────
echo ""
echo "Claude config..."
mkdir -p "$CLAUDE_DEST"
if [[ -f "$CLAUDE_DIR/settings.json" ]]; then
  if [[ -f "$CLAUDE_DEST/settings.json" ]]; then
    echo "  [skip] settings.json — already exists"
  else
    cp "$CLAUDE_DIR/settings.json" "$CLAUDE_DEST/settings.json"
    echo "  [copy] settings.json"
  fi
fi
if [[ -f "$CLAUDE_DIR/CLAUDE.md" ]]; then
  if [[ -f "$CLAUDE_DEST/CLAUDE.md" ]]; then
    echo "  [skip] CLAUDE.md — already exists"
  else
    cp "$CLAUDE_DIR/CLAUDE.md" "$CLAUDE_DEST/CLAUDE.md"
    echo "  [copy] CLAUDE.md"
  fi
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit $FICO_DIR/teams_channel_sound_watcher.py"
echo "     → Set YOUR_TEAM_ID and YOUR_CHANNEL_ID"
echo "  2. Populate $TOKENS_FILE with your msalRefreshToken"
echo "     (get it from Teams browser localStorage the first time)"
echo "  3. Run: python3 $FICO_DIR/refresh-tokens.py"
echo "  4. Check launchctl: launchctl list | grep com.ruben"
