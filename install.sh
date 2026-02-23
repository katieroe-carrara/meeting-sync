#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="$HOME/.config/meeting-sync"
CONFIG_FILE="$CONFIG_DIR/config.json"
DAILY_PLIST_NAME="com.meeting-sync.daily"
DAILY_PLIST_PATH="$HOME/Library/LaunchAgents/$DAILY_PLIST_NAME.plist"
BOT_PLIST_NAME="com.meeting-sync.bot"
BOT_PLIST_PATH="$HOME/Library/LaunchAgents/$BOT_PLIST_NAME.plist"
LOG_DIR="$HOME/.config/meeting-sync/logs"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "=== Meeting Sync Installer ==="
echo

# 1. Python virtual environment + dependencies
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
fi
echo "Installing dependencies..."
"$VENV_DIR/bin/pip" install -q slack-bolt
PYTHON="$VENV_DIR/bin/python3"
echo "  Using: $PYTHON"

# 2. Config file
mkdir -p "$CONFIG_DIR"
if [ -f "$CONFIG_FILE" ]; then
    echo "Config already exists at $CONFIG_FILE"
else
    cp "$SCRIPT_DIR/config.example.json" "$CONFIG_FILE"
    chmod 600 "$CONFIG_FILE"
    echo "Created config at $CONFIG_FILE"
    echo
    echo "  >>> Edit this file with your settings before running! <<<"
    echo "  >>> Set bot_token, app_token, and channel at minimum.  <<<"
    echo
    echo "Opening config in your default editor..."
    ${EDITOR:-open} "$CONFIG_FILE"
    read -p "Press Enter when you've finished editing the config..."
fi

# 3. Log directory
mkdir -p "$LOG_DIR"

# 4. Check for app_token (needed for the bot)
if ! grep -q '"app_token"' "$CONFIG_FILE" || grep -q '"xapp-your-app-token-here"' "$CONFIG_FILE"; then
    echo
    echo "  WARNING: slack.app_token not configured."
    echo "  The interactive bot requires Socket Mode. See README.md for setup."
    echo "  The daily 6pm summary will still work without it."
    echo
    HAS_APP_TOKEN=false
else
    HAS_APP_TOKEN=true
fi

# 5. Generate daily sync plist (runs at 6pm)
cat > "$DAILY_PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$DAILY_PLIST_NAME</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SCRIPT_DIR/meeting_sync.py</string>
        <string>--config</string>
        <string>$CONFIG_FILE</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>18</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/meeting-sync.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/meeting-sync.log</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
EOF

launchctl unload "$DAILY_PLIST_PATH" 2>/dev/null || true
launchctl load "$DAILY_PLIST_PATH"
echo "Loaded daily sync (6pm): $DAILY_PLIST_NAME"

# 6. Generate bot plist (persistent daemon) — only if app_token is set
if [ "$HAS_APP_TOKEN" = true ]; then
    cat > "$BOT_PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$BOT_PLIST_NAME</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SCRIPT_DIR/bot.py</string>
        <string>--config</string>
        <string>$CONFIG_FILE</string>
    </array>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/bot.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/bot.log</string>
</dict>
</plist>
EOF

    launchctl unload "$BOT_PLIST_PATH" 2>/dev/null || true
    launchctl load "$BOT_PLIST_PATH"
    echo "Loaded interactive bot: $BOT_PLIST_NAME"
fi

echo
echo "Installed!"
echo
echo "  Config:     $CONFIG_FILE"
echo "  Logs:       $LOG_DIR/"
echo "  Daily sync: runs at 6:00 PM"
if [ "$HAS_APP_TOKEN" = true ]; then
    echo "  Bot:        running (DM or @mention to get action items)"
fi
echo
echo "To test:"
echo "  Daily sync: $PYTHON $SCRIPT_DIR/meeting_sync.py"
echo "  Bot:        $PYTHON $SCRIPT_DIR/bot.py"
echo
echo "To uninstall:"
echo "  launchctl unload $DAILY_PLIST_PATH && rm $DAILY_PLIST_PATH"
echo "  launchctl unload $BOT_PLIST_PATH && rm $BOT_PLIST_PATH"
