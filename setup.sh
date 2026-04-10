#!/usr/bin/env bash
set -euo pipefail

# claude-glean-tui setup
# Injects a statusline hook so Claude Code exports usage data for the TUI.
# Idempotent — safe to run multiple times.

MARKER="# claude-glean-tui: export usage data"
GLEAN_SNIPPET='# claude-glean-tui: export usage data
_glean_dir="${XDG_CONFIG_HOME:-$HOME/.config}/claude-glean-tui"
mkdir -p "$_glean_dir" 2>/dev/null
echo "$input" | jq -c '"'"'{
  rate_limits: .rate_limits,
  context_window: .context_window,
  cost: .cost,
  model: .model,
  timestamp: now
}'"'"' > "$_glean_dir/statusline.json" 2>/dev/null &'

CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"
SL_SCRIPT="$CLAUDE_DIR/statusline-command.sh"

echo "claude-glean-tui setup"
echo "======================"

# ── Step 1: Check jq dependency ──────────────────────────────────────────────
if ! command -v jq &>/dev/null; then
  echo "ERROR: jq is required. Install with: brew install jq (macOS) or apt install jq (Linux)"
  exit 1
fi

# ── Step 2: Check if statusLine is configured ────────────────────────────────
if [ ! -f "$SETTINGS" ]; then
  echo "No $SETTINGS found. Creating minimal settings..."
  mkdir -p "$CLAUDE_DIR"
  echo '{}' > "$SETTINGS"
fi

has_statusline=$(python3 -c "
import json
s = json.load(open('$SETTINGS'))
print('yes' if s.get('statusLine') else 'no')
" 2>/dev/null || echo "no")

if [ "$has_statusline" = "no" ]; then
  echo "No statusLine configured. Creating default script..."

  # Create a minimal statusline script
  cat > "$SL_SCRIPT" << 'SCRIPT'
#!/usr/bin/env bash
input=$(cat)

MARKER_PLACEHOLDER

model=$(echo "$input" | jq -r '.model.display_name // ""')
used=$(echo "$input" | jq -r '.context_window.used_percentage // empty')
five=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
week=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')

parts=""
[ -n "$model" ] && parts="$model"
[ -n "$used" ] && parts="$parts | ctx:$(printf '%.0f' "$used")%"
[ -n "$five" ] && parts="$parts | 5h:$(printf '%.0f' "$five")%"
[ -n "$week" ] && parts="$parts | 7d:$(printf '%.0f' "$week")%"
echo "$parts"
SCRIPT
  chmod +x "$SL_SCRIPT"

  # Register in settings.json
  python3 -c "
import json
s = json.load(open('$SETTINGS'))
s['statusLine'] = {'type': 'command', 'command': 'bash $SL_SCRIPT'}
json.dump(s, open('$SETTINGS', 'w'), indent=2, ensure_ascii=False)
print('  Registered statusLine in settings.json')
"
fi

# ── Step 3: Inject glean snippet into statusline script ──────────────────────

# Find the actual script path from settings
sl_cmd=$(python3 -c "
import json
s = json.load(open('$SETTINGS'))
sl = s.get('statusLine', {})
cmd = sl.get('command', '')
# Extract script path (last arg of 'bash /path/to/script.sh')
parts = cmd.split()
for p in reversed(parts):
    if p.endswith('.sh'):
        print(p.replace('~', '$HOME'))
        break
" 2>/dev/null)

# Expand ~ in path
sl_cmd=$(eval echo "$sl_cmd")

if [ -z "$sl_cmd" ] || [ ! -f "$sl_cmd" ]; then
  sl_cmd="$SL_SCRIPT"
fi

if grep -q "$MARKER" "$sl_cmd" 2>/dev/null; then
  echo "✓ Glean snippet already present in $sl_cmd"
else
  echo "  Injecting glean snippet into $sl_cmd..."

  # Insert after 'input=$(cat)' line
  if grep -q 'input=\$(cat)' "$sl_cmd"; then
    sed -i.bak "/input=\$(cat)/a\\
\\
$( echo "$GLEAN_SNIPPET" | sed 's/$/\\/' | sed '$ s/\\$//' )
" "$sl_cmd"
    rm -f "${sl_cmd}.bak"
    echo "✓ Injected after 'input=\$(cat)'"
  else
    # Fallback: prepend to file (after shebang)
    {
      head -1 "$sl_cmd"
      echo 'input=$(cat)'
      echo ""
      echo "$GLEAN_SNIPPET"
      echo ""
      tail -n +2 "$sl_cmd"
    } > "${sl_cmd}.tmp"
    mv "${sl_cmd}.tmp" "$sl_cmd"
    echo "✓ Injected at top of script"
  fi
fi

chmod +x "$sl_cmd"

# ── Step 4: Verify ───────────────────────────────────────────────────────────
echo ""
echo "Setup complete!"
echo "  statusLine script: $sl_cmd"
echo "  Usage data will be written to: \${XDG_CONFIG_HOME:-~/.config}/claude-glean-tui/statusline.json"
echo ""
echo "Restart Claude Code for changes to take effect."
echo "Then run: python3 $(dirname "$0")/tui.py"
