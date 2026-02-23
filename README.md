# meeting-sync

Automatically sync [Granola](https://granola.ai) meetings to an [Obsidian](https://obsidian.md) vault and post action items to Slack — on a daily schedule and on-demand via DM.

## What it does

1. **Syncs meetings** — Reads your local Granola cache, classifies meetings into folders based on rules you define, and writes them as markdown files to your Obsidian vault (with frontmatter, AI summary, notes, and transcript).

2. **Extracts action items** — Parses today's meeting summaries for action-oriented sections (Next Steps, Action Items, Follow-ups, etc.).

3. **Posts to Slack** — Daily at 6pm, sends a formatted summary to a Slack channel.

4. **Interactive bot** — DM the bot or @mention it to get today's action items with checkboxes. Check items off as you complete them — the message updates in real time.

## Prerequisites

- **macOS** (uses launchd for scheduling)
- **Granola** desktop app (meetings are read from the local cache)
- **Obsidian** vault (or any folder where you want markdown files)
- **Slack app** with a bot token

## Quick start

```bash
git clone https://github.com/katieroe-carrara/meeting-sync.git
cd meeting-sync
./install.sh
```

The installer will:
1. Create a Python virtual environment and install dependencies
2. Copy `config.example.json` to `~/.config/meeting-sync/config.json`
3. Open it for editing
4. Set up launchd jobs (daily sync + interactive bot)

To run manually:
```bash
# Daily sync + Slack post
.venv/bin/python3 meeting_sync.py

# Interactive bot
.venv/bin/python3 bot.py
```

## Configuration

Edit `~/.config/meeting-sync/config.json`:

| Key | Description |
|-----|-------------|
| `obsidian_vault` | Path to your Obsidian vault (supports `~`) |
| `meetings_subfolder` | Subfolder for meeting files (default: `Meetings`) |
| `granola_cache` | Path to Granola's cache file (usually default is fine) |
| `slack.bot_token` | Slack bot token (`xoxb-...`) |
| `slack.app_token` | Slack app-level token (`xapp-...`) — needed for the interactive bot |
| `slack.channel` | Slack channel ID for the daily 6pm post |
| `organization_domain` | Your company's email domain (used for 1:1 detection) |
| `classification_rules` | Rules for sorting meetings into folders |
| `one_on_one_names` | Map of first names to 1:1 subfolder paths |
| `default_folder` | Fallback folder for unclassified meetings |

### Classification rules

Each rule has:
- `folder` — destination subfolder (e.g., `"Client A"` or `"Client A/Subteam"`)
- `domains` — email domains that trigger this rule
- `title_keywords` — words in the meeting title that trigger this rule
- `name_keywords` — participant names that trigger this rule

Rules are evaluated in order — first match wins.

## Setting up Slack

### Basic setup (daily posts only)

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and create a new app
2. Under **OAuth & Permissions**, add the `chat:write` bot scope
3. Install the app to your workspace and copy the **Bot User OAuth Token** (`xoxb-...`)
4. Invite the bot to your target channel
5. Get the channel ID (right-click channel > Copy link — ID is the last path segment)

### Socket Mode setup (interactive bot)

To also get the on-demand DM/mention feature:

1. Go to **Settings > Socket Mode** and toggle it **ON**
2. At **Settings > Basic Information > App-Level Tokens**, create a token with `connections:write` scope — copy the `xapp-...` token
3. At **OAuth & Permissions**, add these bot scopes:
   - `app_mentions:read`
   - `im:history`
4. At **Event Subscriptions**, toggle ON and subscribe to:
   - `app_mention`
   - `message.im`
5. At **Interactivity & Shortcuts**, toggle ON (no URL needed with Socket Mode)
6. **Reinstall the app** to your workspace to pick up the new scopes
7. Add the `app_token` to your config.json

## Usage

### Daily summary
Automatically posts at 6pm with all action items from the day's meetings.

### On-demand
DM the bot or @mention it in any channel. It responds with today's action items, each with a checkbox. Check items off as you complete them — the message updates in place. When all items are done, the message shows "All action items completed".

## Sync state

Stored at `<vault>/.meeting-sync/sync-state.json`. Delete it to force a full re-sync.

## Logs

- Daily sync: `~/.config/meeting-sync/logs/meeting-sync.log`
- Bot: `~/.config/meeting-sync/logs/bot.log`

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.meeting-sync.daily.plist
launchctl unload ~/Library/LaunchAgents/com.meeting-sync.bot.plist
rm ~/Library/LaunchAgents/com.meeting-sync.daily.plist
rm ~/Library/LaunchAgents/com.meeting-sync.bot.plist
rm -rf ~/.config/meeting-sync
```
