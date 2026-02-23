# meeting-sync

Automatically sync [Granola](https://granola.ai) meetings to an [Obsidian](https://obsidian.md) vault and post a daily summary of action items to Slack.

Runs daily at 6 PM via macOS launchd.

## What it does

1. **Syncs meetings** — Reads your local Granola cache, classifies meetings into folders based on rules you define, and writes them as markdown files to your Obsidian vault (with frontmatter, AI summary, notes, and transcript).

2. **Extracts action items** — Parses today's meeting summaries for action-oriented sections (Next Steps, Action Items, Follow-ups, etc.) and individual bullets with action language.

3. **Posts to Slack** — Sends a formatted summary of all action items grouped by meeting to a Slack channel using Block Kit.

## Prerequisites

- **macOS** (uses launchd for scheduling)
- **Granola** desktop app (meetings are read from the local cache)
- **Obsidian** vault (or any folder where you want markdown files)
- **Slack bot token** with `chat:write` scope

## Quick start

```bash
git clone https://github.com/katieroe-carrara/meeting-sync.git
cd meeting-sync
./install.sh
```

The installer will:
1. Copy `config.example.json` to `~/.config/meeting-sync/config.json`
2. Open it for editing
3. Set up a daily 6 PM launchd job

To run manually:
```bash
python3 meeting_sync.py
```

## Configuration

Edit `~/.config/meeting-sync/config.json`:

| Key | Description |
|-----|-------------|
| `obsidian_vault` | Path to your Obsidian vault (supports `~`) |
| `meetings_subfolder` | Subfolder within the vault for meeting files (default: `Meetings`) |
| `granola_cache` | Path to Granola's cache file (usually doesn't need changing) |
| `slack.bot_token` | Your Slack bot token (`xoxb-...`) |
| `slack.channel` | Slack channel ID to post to |
| `organization_domain` | Your company's email domain (used for 1:1 detection) |
| `classification_rules` | Array of rules for sorting meetings into folders |
| `one_on_one_names` | Map of first names to 1:1 subfolder paths |
| `default_folder` | Fallback folder for unclassified meetings |

### Classification rules

Each rule has:
- `folder` — destination subfolder (e.g., `"Client A"` or `"Client A/Subteam"`)
- `domains` — email domains that trigger this rule (e.g., `["clienta.com"]`)
- `title_keywords` — words in the meeting title that trigger this rule
- `name_keywords` — participant names that trigger this rule

Rules are evaluated in order — first match wins.

### Setting up Slack

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and create a new app
2. Under **OAuth & Permissions**, add the `chat:write` scope
3. Install the app to your workspace and copy the **Bot User OAuth Token** (`xoxb-...`)
4. Invite the bot to your target channel (channel settings > Integrations > Add apps)
5. Get the channel ID (right-click channel name > Copy link, the ID is the last segment)

## Sync state

Sync state is stored at `<vault>/.meeting-sync/sync-state.json`. It tracks which meetings have been synced so they aren't duplicated. Delete this file to force a full re-sync.

## Logs

Logs are written to `~/.config/meeting-sync/logs/meeting-sync.log`.

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.meeting-sync.daily.plist
rm ~/Library/LaunchAgents/com.meeting-sync.daily.plist
rm -rf ~/.config/meeting-sync
```
