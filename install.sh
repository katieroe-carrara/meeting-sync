#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="$HOME/.config/meeting-sync"
CONFIG_FILE="$CONFIG_DIR/config.json"
PLIST_NAME="com.meeting-sync.daily"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
LOG_DIR="$HOME/.config/meeting-sync/logs"

echo "=== Meeting Sync Installer ==="
echo

# 1. Config file
mkdir -p "$CONFIG_DIR"
if [ -f "$CONFIG_FILE" ]; then
    echo "Config already exists at $CONFIG_FILE"
else
    cp "$SCRIPT_DIR/config.example.json" "$CONFIG_FILE"
    chmod 600 "$CONFIG_FILE"
    echo "Created config at $CONFIG_FILE"
    echo
    echo "  >>> Edit this file with your settings before running! <<<"
    echo "  >>> At minimum, set your Slack bot_token and channel.  <<<"
    echo
    echo "Opening config in your default editor..."
    ${EDITOR:-open} "$CONFIG_FILE"
    read -p "Press Enter when you've finished editing the config..."
fi

# 2. Log directory
mkdir -p "$LOG_DIR"

# 3. Generate launchd plist
cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
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

# 4. Load the plist
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo
echo "Installed! The sync will run daily at 6:00 PM."
echo
echo "  Config:  $CONFIG_FILE"
echo "  Logs:    $LOG_DIR/meeting-sync.log"
echo "  Script:  $SCRIPT_DIR/meeting_sync.py"
echo
echo "To test now:  python3 $SCRIPT_DIR/meeting_sync.py"
echo "To uninstall: launchctl unload $PLIST_PATH && rm $PLIST_PATH"
