#!/usr/bin/env python3
"""Interactive Slack bot for meeting action items.

Listens via Socket Mode for:
- DMs or @mentions → responds with today's action items as checkboxes
- Checkbox interactions → updates message to remove completed items

Requires slack-bolt: pip install slack-bolt
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Import action-item extraction from the sync script
sys.path.insert(0, str(Path(__file__).parent))
from meeting_sync import (
    load_config,
    sync_meetings,
    find_todays_meetings,
    extract_action_items_from_file,
    DEFAULT_CONFIG_PATH,
)

# ── Globals set at startup ────────────────────────────────────────────────

cfg = None
app = None


# ── Block Kit builders ────────────────────────────────────────────────────

def split_katie_items(action_items_by_meeting):
    """Separate Katie's action items from others across all meetings.

    Items starting with (me) are Katie's (from transcript speaker tagging).
    Items mentioning 'katie' by name are also hers.
    """
    katie_items = []
    other_meetings = []

    for meeting in action_items_by_meeting:
        katie = []
        other = []
        for item in meeting["items"]:
            if item.startswith("(me)") or re.search(r'\bkatie\b', item, re.IGNORECASE):
                katie.append(item)
            else:
                other.append(item)
        if katie:
            katie_items.append({"title": meeting["title"], "items": katie})
        if other:
            other_meetings.append({"title": meeting["title"], "items": other})

    return katie_items, other_meetings


def build_interactive_blocks(action_items_by_meeting):
    """Build Block Kit with checkboxes, Katie's items shown first."""
    katie_items, other_meetings = split_katie_items(action_items_by_meeting)

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Action Items \u2014 {datetime.now().strftime('%B %d, %Y')}",
            },
        },
        {"type": "divider"},
    ]

    # Combine: Katie's items first, then others
    all_meetings = []
    if katie_items:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Your action items:*"},
        })
        for m in katie_items:
            all_meetings.append(m)
    if other_meetings:
        if katie_items:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Other action items:*"},
            })
        for m in other_meetings:
            all_meetings.append(m)

    for m_idx, meeting in enumerate(all_meetings):
        # Meeting title
        blocks.append({
            "type": "section",
            "block_id": f"meeting_title_{m_idx}",
            "text": {"type": "mrkdwn", "text": f"*{meeting['title']}*"},
        })

        # Checkboxes for action items
        options = []
        for i_idx, item in enumerate(meeting["items"]):
            options.append({
                "text": {"type": "mrkdwn", "text": item},
                "value": json.dumps({"m": m_idx, "i": i_idx, "text": item}),
            })

        if options:
            blocks.append({
                "type": "actions",
                "block_id": f"meeting_actions_{m_idx}",
                "elements": [{
                    "type": "checkboxes",
                    "action_id": f"done_checkbox_{m_idx}",
                    "options": options,
                }],
            })

        blocks.append({"type": "divider"})

    return blocks


def build_all_done_blocks():
    """Blocks shown when every item is completed."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*All action items completed* \u2014 {datetime.now().strftime('%B %d, %Y')} :white_check_mark:",
            },
        },
    ]


def build_no_items_blocks():
    """Blocks shown when there are no action items today."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"No action items from meetings today ({datetime.now().strftime('%B %d, %Y')}).",
            },
        },
    ]


# ── Helpers ───────────────────────────────────────────────────────────────

def get_todays_items():
    """Sync new meetings from Granola, then extract today's action items."""
    # Run sync first to pull any new meetings from Granola cache
    synced_files = sync_meetings(cfg)

    # Then find ALL of today's files (synced now + previously synced)
    today_files = find_todays_meetings(cfg)
    results = []
    for file_path in today_files:
        if not file_path.exists():
            continue
        title, items = extract_action_items_from_file(file_path)
        if items:
            results.append({"title": title, "items": items})
    return results


def send_action_items(say):
    """Extract and send today's action items as an interactive message."""
    items = get_todays_items()
    if not items:
        say(blocks=build_no_items_blocks(), text="No action items today.")
        return

    total = sum(len(m["items"]) for m in items)
    blocks = build_interactive_blocks(items)
    say(blocks=blocks, text=f"{total} action item(s) from {len(items)} meeting(s)")


def rebuild_blocks_after_check(existing_blocks, checked_values):
    """Rebuild message blocks with checked items removed."""
    checked_texts = set()
    for val in checked_values:
        try:
            parsed = json.loads(val)
            checked_texts.add(parsed["text"])
        except (json.JSONDecodeError, KeyError):
            checked_texts.add(val)

    new_blocks = []
    any_items_remaining = False

    i = 0
    while i < len(existing_blocks):
        block = existing_blocks[i]

        # Keep header and top divider as-is
        if block.get("type") in ("header",):
            new_blocks.append(block)
            i += 1
            continue

        # Meeting title block
        if block.get("type") == "section" and (block.get("block_id") or "").startswith("meeting_title_"):
            title_block = block
            title_text = block["text"]["text"]

            # Look ahead for the actions block
            actions_block = None
            if i + 1 < len(existing_blocks) and existing_blocks[i + 1].get("type") == "actions":
                actions_block = existing_blocks[i + 1]

            if actions_block:
                # Filter out checked items
                element = actions_block["elements"][0]
                remaining_options = []
                for opt in element.get("options", []):
                    try:
                        parsed = json.loads(opt["value"])
                        if parsed["text"] not in checked_texts:
                            remaining_options.append(opt)
                    except (json.JSONDecodeError, KeyError):
                        if opt["value"] not in checked_texts:
                            remaining_options.append(opt)

                # Also remove any that were in initial_options (already checked)
                for opt in element.get("initial_options", []):
                    try:
                        parsed = json.loads(opt["value"])
                        checked_texts.add(parsed["text"])
                    except (json.JSONDecodeError, KeyError):
                        pass
                remaining_options = [
                    o for o in remaining_options
                    if json.loads(o["value"])["text"] not in checked_texts
                ]

                if remaining_options:
                    any_items_remaining = True
                    new_blocks.append({"type": "divider"})
                    new_blocks.append(title_block)
                    new_actions = dict(actions_block)
                    new_actions["elements"] = [{
                        "type": "checkboxes",
                        "action_id": element["action_id"],
                        "options": remaining_options,
                    }]
                    new_blocks.append(new_actions)
                else:
                    # All items done for this meeting
                    new_blocks.append({"type": "divider"})
                    clean_title = title_text.strip("*")
                    new_blocks.append({
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"~{clean_title}~ \u2014 all done :white_check_mark:"},
                    })

                # Skip past title + actions + divider
                i += 3 if (i + 2 < len(existing_blocks) and existing_blocks[i + 2].get("type") == "divider") else 2
                continue
            else:
                # Title with no actions block — keep as-is
                new_blocks.append({"type": "divider"})
                new_blocks.append(title_block)
                i += 1
                continue

        # Skip standalone dividers (we add our own)
        if block.get("type") == "divider":
            i += 1
            continue

        # Keep anything else
        new_blocks.append(block)
        i += 1

    if not any_items_remaining:
        return build_all_done_blocks()

    return new_blocks


# ── Event handlers ────────────────────────────────────────────────────────

def setup_handlers(app):
    """Register all Slack event handlers."""

    @app.event("app_mention")
    def handle_mention(body, say):
        """Respond to @mentions with today's action items."""
        send_action_items(say)

    @app.event("message")
    def handle_dm(body, say):
        """Respond to DMs with today's action items."""
        event = body.get("event", {})
        # Only respond to DMs (not channel messages, not bot's own messages)
        if event.get("channel_type") == "im" and not event.get("bot_id"):
            send_action_items(say)

    # Register a handler for each possible checkbox action_id pattern
    # We use a regex pattern to match done_checkbox_0, done_checkbox_1, etc.
    import re

    @app.action(re.compile(r"^done_checkbox_\d+$"))
    def handle_checkbox(ack, body, client):
        """Handle checkbox interactions — remove checked items from message."""
        ack()

        channel = body["channel"]["id"]
        message_ts = body["message"]["ts"]
        existing_blocks = body["message"].get("blocks", [])

        # Collect ALL checked values across all checkbox groups in this message
        checked_values = set()
        for action in body.get("actions", []):
            for opt in action.get("selected_options", []):
                checked_values.add(opt["value"])

        new_blocks = rebuild_blocks_after_check(existing_blocks, checked_values)

        total_remaining = 0
        for block in new_blocks:
            if block.get("type") == "actions":
                for el in block.get("elements", []):
                    total_remaining += len(el.get("options", []))

        client.chat_update(
            channel=channel,
            ts=message_ts,
            blocks=new_blocks,
            text=f"{total_remaining} action item(s) remaining" if total_remaining else "All done!",
        )


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    global cfg, app

    parser = argparse.ArgumentParser(description="Interactive Slack bot for meeting action items")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH),
                        help=f"Path to config.json (default: {DEFAULT_CONFIG_PATH})")
    args = parser.parse_args()

    cfg = load_config(args.config)

    slack_cfg = cfg.get("slack", {})
    bot_token = slack_cfg.get("bot_token", "")
    app_token = slack_cfg.get("app_token", "")

    if not bot_token or not app_token:
        print("Error: slack.bot_token and slack.app_token are both required in config.", file=sys.stderr)
        print("See README.md for Socket Mode setup instructions.", file=sys.stderr)
        sys.exit(1)

    app = App(token=bot_token)
    setup_handlers(app)

    print(f"Bot starting (Socket Mode)...")
    print(f"  Config: {args.config}")
    print(f"  Vault: {cfg['_vault']}")
    print(f"  Channel: {slack_cfg.get('channel', 'N/A')}")

    handler = SocketModeHandler(app, app_token)
    handler.start()


if __name__ == "__main__":
    main()
