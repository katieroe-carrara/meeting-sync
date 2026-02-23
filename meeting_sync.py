#!/usr/bin/env python3
"""Daily Meeting Sync + Action Items to Slack.

Syncs new Granola meetings to an Obsidian vault and posts a summary of
today's action items to a Slack channel.

Configuration is loaded from a JSON file (default: ~/.config/meeting-sync/config.json).
Override with --config <path>.
"""

import argparse
import json
import re
import sys
import urllib.request
import urllib.error
import html as html_mod
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Action-item extraction (not user-configurable) ────────────────────────

ACTION_SECTION_PATTERNS = [
    r"next\s*steps",
    r"action\s*items",
    r"follow[- ]?ups?",
    r"to[- ]?do",
    r"deliverables",
    r"decisions",
    r"key\s*decisions",
    r"takeaways",
    r"assignments",
]

# Patterns that signal real commitments/action items in transcript speech
# Intentionally specific to reduce noise from conversational filler
TRANSCRIPT_ACTION_PATTERNS = [
    # "I'll [action verb]" — specific commitments
    r"\bi'(?:ll|m going to|m gonna)\s+(?:send|email|share|follow up|reach out|schedule|set up|finalize|work on|handle|talk to|ping|create|draft|write|build|update|put together|coordinate|own that)",
    # "We need to" / "I need to" — obligations
    r"\bi need to\s+(?:send|email|share|follow up|reach out|schedule|set up|finalize|work on|handle|talk to|figure out|check|get|do|run|update)",
    r"\bwe need to\s+(?:send|email|share|follow up|reach out|schedule|set up|finalize|work on|handle|figure out|check|get|do|rebalance|reduce|pick|redesign|negotiate)",
    # Requests directed at someone
    r"\bcan you\s+(?:send|email|share|follow up|reach out|schedule|set up|get us|check|do|create|draft|handle|research|put)",
    # Deadlines
    r"\bby (?:end of (?:day|week)|monday|tuesday|wednesday|thursday|friday|next week|tomorrow|tonight|eod|eow)\b",
    # Explicit markers
    r"\baction item\b",
    r"\bnext step[s]?\s+(?:is|are|on|here|for)\b",
    r"\bfollow[- ]?up (?:with|on)\b",
]
TRANSCRIPT_ACTION_RE = re.compile("|".join(TRANSCRIPT_ACTION_PATTERNS), re.IGNORECASE)

# Short filler and non-actionable speech to ignore
FILLER_PATTERNS = re.compile(
    r"^(?:yeah|yep|okay|ok|cool|right|sure|mhm|uh huh|great|alright|thanks|bye|hello|hi|hey|hmm|huh|wow|oh)[.\s!?,]*$",
    re.IGNORECASE,
)
# Lines that match action patterns but aren't real action items
NOISE_PATTERNS = re.compile(
    r"\bi need to (?:run|go|hop|jump|leave|mute|drop)\b"
    r"|\blet me (?:see|show|think|check|look|pull up|share my screen)\b"
    r"|\bi'(?:ll|m going to|m gonna) (?:be |try |just )",
    re.IGNORECASE,
)


# ── Configuration ─────────────────────────────────────────────────────────

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "meeting-sync" / "config.json"


def load_config(config_path):
    """Load and validate user configuration."""
    path = Path(config_path).expanduser()
    if not path.exists():
        print(f"Config file not found: {path}", file=sys.stderr)
        print("Copy config.example.json to ~/.config/meeting-sync/config.json and fill it in.", file=sys.stderr)
        sys.exit(1)

    with open(path) as f:
        cfg = json.load(f)

    # Resolve paths with ~ expansion
    cfg["_vault"] = Path(cfg["obsidian_vault"]).expanduser()
    cfg["_meetings_dir"] = cfg["_vault"] / cfg.get("meetings_subfolder", "Meetings")
    cfg["_granola_cache"] = Path(cfg["granola_cache"]).expanduser()
    cfg["_sync_state"] = cfg["_vault"] / ".meeting-sync" / "sync-state.json"

    return cfg


# ── HTML / ProseMirror Conversion ─────────────────────────────────────────

def html_to_md(h):
    """Convert HTML to markdown."""
    if not h:
        return ""
    h = re.sub(r'<h([1-4])[^>]*>(.*?)</h\1>',
               lambda m: '#' * int(m.group(1)) + ' ' + m.group(2) + '\n',
               h, flags=re.DOTALL)
    h = re.sub(r'<strong>(.*?)</strong>', r'**\1**', h, flags=re.DOTALL)
    h = re.sub(r'<b>(.*?)</b>', r'**\1**', h, flags=re.DOTALL)
    h = re.sub(r'<em>(.*?)</em>', r'*\1*', h, flags=re.DOTALL)
    h = re.sub(r'<i>(.*?)</i>', r'*\1*', h, flags=re.DOTALL)
    h = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', h, flags=re.DOTALL)
    h = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n', h, flags=re.DOTALL)
    h = re.sub(r'</?[uo]l[^>]*>', '\n', h)
    h = re.sub(r'<br\s*/?>', '\n', h)
    h = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n\n', h, flags=re.DOTALL)
    h = re.sub(r'<[^>]+>', '', h)
    h = html_mod.unescape(h)
    h = re.sub(r'\n{3,}', '\n\n', h)
    return h.strip()


def prosemirror_to_md(node):
    """Convert ProseMirror JSON to markdown."""
    if not node:
        return ""
    if isinstance(node, str):
        return node
    ntype = node.get("type", "")
    content = node.get("content", [])
    text = node.get("text", "")
    marks = node.get("marks", [])
    if ntype == "text":
        t = text
        for mark in marks:
            mt = mark.get("type", "")
            if mt == "bold":
                t = f"**{t}**"
            elif mt == "italic":
                t = f"*{t}*"
            elif mt == "link":
                href = mark.get("attrs", {}).get("href", "")
                t = f"[{t}]({href})"
        return t
    parts = [prosemirror_to_md(c) for c in content]
    joined = "".join(parts)
    if ntype == "paragraph":
        return joined + "\n\n"
    elif ntype == "heading":
        level = node.get("attrs", {}).get("level", 1)
        return "#" * level + " " + joined + "\n\n"
    elif ntype in ("bulletList", "orderedList"):
        return joined
    elif ntype == "listItem":
        lines = joined.strip().split("\n")
        result = "- " + lines[0] + "\n"
        for line in lines[1:]:
            if line.strip():
                result += "  " + line + "\n"
        return result
    elif ntype == "doc":
        return joined
    elif ntype == "taskList":
        return joined
    elif ntype == "taskItem":
        checked = node.get("attrs", {}).get("checked", False)
        marker = "[x]" if checked else "[ ]"
        return f"- {marker} " + joined.strip() + "\n"
    elif ntype == "hardBreak":
        return "\n"
    else:
        return joined


# ── Granola Extraction Helpers ────────────────────────────────────────────

def get_summary(state, doc_id):
    """AI summaries live in state.documentPanels[doc_id] -> original_content (HTML)."""
    if doc_id not in state.get("documentPanels", {}):
        return ""
    panels = state["documentPanels"][doc_id]
    parts = []
    for pid, pdata in panels.items():
        oc = pdata.get("original_content", "")
        title = pdata.get("title", "")
        if oc and len(oc) > 10:
            md = html_to_md(oc)
            if title and title != "Summary":
                parts.append(f"### {title}\n\n{md}")
            else:
                parts.append(md)
    return "\n\n---\n\n".join(parts) if parts else ""


def get_notes(doc):
    """Human notes: try notes_markdown first, then ProseMirror, then plain."""
    md = doc.get("notes_markdown") or ""
    if md and len(md.strip()) > 5:
        return md.strip()
    notes = doc.get("notes")
    if isinstance(notes, dict):
        result = prosemirror_to_md(notes)
        if result and len(result.strip()) > 5:
            return result.strip()
    plain = doc.get("notes_plain") or ""
    if plain and len(plain.strip()) > 5:
        return plain.strip()
    return ""


def slugify(text, max_len=80):
    """Convert text to a filename-safe slug."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s]+', '-', text.strip())
    text = re.sub(r'-+', '-', text)
    return text[:max_len].rstrip('-')


# ── Meeting Classification ────────────────────────────────────────────────

def classify_meeting(title, participants, cfg):
    """Classify a meeting into a subfolder based on config rules."""
    title_lower = (title or "").lower()
    emails = [p.get("email", "").lower() for p in participants]
    names = [p.get("name", "").lower() for p in participants]
    domains = [e.split("@")[1] if "@" in e else "" for e in emails]

    org_domain = cfg.get("organization_domain", "")
    classification_rules = cfg.get("classification_rules", [])
    one_on_one_names = cfg.get("one_on_one_names", {})
    default_folder = cfg.get("default_folder", "General")

    # Check domain/keyword rules
    for rule in classification_rules:
        for domain in rule.get("domains", []):
            if domain in domains:
                return rule["folder"]
        for kw in rule.get("title_keywords", []):
            if kw in title_lower:
                return rule["folder"]
        for kw in rule.get("name_keywords", []):
            if any(kw in n for n in names):
                return rule["folder"]

    # Check 1:1 pattern: exactly 2 participants, both from org domain
    if org_domain:
        org_emails = [e for e in emails if e.endswith(f"@{org_domain}")]
        non_org = [e for e in emails if e and not e.endswith(f"@{org_domain}")]
        if len(non_org) == 0 and len(org_emails) == 2:
            for name_key, folder in one_on_one_names.items():
                if any(name_key in n for n in names):
                    return folder
            return "1:1"

    return default_folder


# ── Part A: Sync Meetings ─────────────────────────────────────────────────

def load_granola_state(cfg):
    """Load and parse the Granola cache."""
    with open(cfg["_granola_cache"], "r") as f:
        raw = json.load(f)
    data = json.loads(raw["cache"])
    return data["state"]


def load_sync_state(cfg):
    """Load the sync state, creating default if missing."""
    path = cfg["_sync_state"]
    if path.exists():
        with open(path) as f:
            state = json.load(f)
        if "synced_ids" in state and "meetings" not in state:
            state = {"last_sync": state.get("last_sync"), "meetings": {}}
            for sid in state.get("synced_ids", []):
                state["meetings"][sid] = {"skipped": True}
        return state
    return {"last_sync": None, "meetings": {}}


def save_sync_state(cfg, state):
    """Save sync state to disk."""
    path = cfg["_sync_state"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def extract_new_meetings(granola_state, sync_state):
    """Extract meetings from Granola cache that haven't been synced yet."""
    last_sync = sync_state.get("last_sync")
    known_ids = set(sync_state.get("meetings", {}).keys())

    if last_sync:
        cutoff = last_sync
    else:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    # Always sync anything from today, even if created_at < last_sync.
    # Granola's cache can lag behind, so meetings may appear after last_sync
    # has already moved past their created_at.
    today_prefix = datetime.now().strftime("%Y-%m-%d")

    # Build folder lookup
    folder_lookup = {}
    for list_id, list_data in granola_state.get("documentLists", {}).items():
        meta = granola_state.get("documentListsMetadata", {}).get(list_id, {})
        folder_name = meta.get("title", "Unknown")
        if isinstance(list_data, list):
            for item in list_data:
                doc_id = item.get("documentId") if isinstance(item, dict) else item
                if doc_id:
                    folder_lookup[doc_id] = folder_name

    new_meetings = []
    for doc_id, doc in granola_state.get("documents", {}).items():
        if doc.get("deleted_at") or doc.get("valid_meeting") is False:
            continue
        if doc_id in known_ids:
            continue
        created = doc.get("created_at", "")
        is_today = created.startswith(today_prefix)
        if not is_today and created < cutoff:
            continue

        # Build participants
        people = doc.get("people") or {}
        people_attendees = people.get("attendees", [])
        creator = people.get("creator", {})
        cal = doc.get("google_calendar_event") or {}
        cal_attendees = cal.get("attendees", [])

        participants = []
        seen_emails = set()

        if creator.get("email"):
            name = creator.get("name", "")
            details = creator.get("details", {})
            full_name = details.get("person", {}).get("name", {}).get("fullName", name)
            participants.append({"name": full_name, "email": creator["email"]})
            seen_emails.add(creator["email"].lower())

        for a in people_attendees:
            email = a.get("email", "")
            name = a.get("name", "")
            if not name:
                details = a.get("details", {})
                name = details.get("person", {}).get("name", {}).get("fullName", "")
            if email and email.lower() not in seen_emails:
                participants.append({"name": name, "email": email})
                seen_emails.add(email.lower())

        for a in cal_attendees:
            email = a.get("email", "")
            if email and email.lower() not in seen_emails:
                participants.append({"name": a.get("displayName", ""), "email": email})
                seen_emails.add(email.lower())

        # Transcript
        transcript_segments = granola_state.get("transcripts", {}).get(doc_id, [])
        transcript_lines = []
        if isinstance(transcript_segments, list) and transcript_segments:
            for seg in transcript_segments:
                text = seg.get("text", "").strip()
                if not text:
                    continue
                ts = seg.get("start_timestamp", "")[:19].replace("T", " ")
                source = seg.get("source", "unknown")
                label = "me" if source == "microphone" else "them" if source == "system" else source
                transcript_lines.append(f"[{ts}] ({label}) {text}")

        new_meetings.append({
            "id": doc_id,
            "title": doc.get("title", "Untitled"),
            "created_at": created,
            "granola_folder": folder_lookup.get(doc_id, ""),
            "participants": participants,
            "notes": get_notes(doc),
            "summary": get_summary(granola_state, doc_id),
            "transcript": "\n".join(transcript_lines),
            "has_transcript": len(transcript_lines) > 0,
        })

    return new_meetings


def write_meeting_file(meeting, cfg):
    """Write a meeting markdown file to the appropriate folder. Returns the relative path."""
    meetings_dir = cfg["_meetings_dir"]
    created_date = meeting["created_at"][:10]
    title_slug = slugify(meeting["title"])
    filename = f"{created_date}-{title_slug}.md"

    folder = classify_meeting(meeting["title"], meeting["participants"], cfg)
    dest_dir = meetings_dir / folder
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Build participants YAML
    participants_yaml = ""
    for p in meeting["participants"]:
        name = p.get("name", "").replace('"', '\\"')
        email = p.get("email", "")
        participants_yaml += f'  - name: "{name}"\n    email: {email}\n'

    summary = meeting["summary"] or "No summary available."
    notes = meeting["notes"] or "No notes."
    transcript = meeting["transcript"] or "Transcript not available \u2014 Granola only retains transcripts for recent meetings."

    content = f"""---
title: "{meeting['title']}"
date: {created_date}
granola_id: {meeting['id']}
granola_folder: "{meeting['granola_folder']}"
entity: "{folder.split('/')[0]}"
subproject: ""
has_transcript: {str(meeting['has_transcript']).lower()}
participants:
{participants_yaml}---

## Summary

{summary}

## Notes

{notes}

## Transcript

{transcript}
"""
    dest_path = dest_dir / filename
    dest_path.write_text(content)

    return f"{folder}/{filename}"


def sync_meetings(cfg):
    """Part A: Sync new Granola meetings to Obsidian. Returns list of today's synced files."""
    print(f"[{datetime.now().isoformat()}] Starting meeting sync...")

    granola_state = load_granola_state(cfg)
    sync_state = load_sync_state(cfg)
    new_meetings = extract_new_meetings(granola_state, sync_state)

    if not new_meetings:
        print("  No new meetings to sync.")
        save_sync_state(cfg, {
            "last_sync": datetime.now(timezone.utc).isoformat(),
            "meetings": sync_state.get("meetings", {}),
        })
        return []

    print(f"  Found {len(new_meetings)} new meeting(s).")

    today = datetime.now().strftime("%Y-%m-%d")
    today_files = []
    meetings_dict = sync_state.get("meetings", {})
    meetings_dir = cfg["_meetings_dir"]

    for meeting in new_meetings:
        rel_path = write_meeting_file(meeting, cfg)
        if meeting["created_at"][:10] == today:
            today_files.append(meetings_dir / rel_path)

        has_summary = bool(meeting["summary"]) and meeting["summary"] != "No summary available."
        has_notes = bool(meeting["notes"]) and meeting["notes"] != "No notes."

        meetings_dict[meeting["id"]] = {
            "file": rel_path,
            "has_summary": has_summary,
            "has_notes": has_notes,
            "has_transcript": meeting["has_transcript"],
            "entity": rel_path.split("/")[0],
            "skipped": False,
        }
        print(f"  Synced: {rel_path}")

    save_sync_state(cfg, {
        "last_sync": datetime.now(timezone.utc).isoformat(),
        "meetings": meetings_dict,
    })

    print(f"  Sync complete: {len(new_meetings)} meeting(s) written.")
    return today_files


# ── Part B: Extract Action Items ──────────────────────────────────────────

def extract_action_items_from_file(file_path):
    """Parse a meeting file and extract action items from the Transcript section."""
    content = file_path.read_text()

    title_match = re.search(r'^title:\s*"?([^"\n]+)"?', content, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else file_path.stem

    # Extract transcript section
    transcript_match = re.search(r'## Transcript\n+(.*?)(?=\n## |\Z)', content, re.DOTALL)
    if not transcript_match:
        return title, []

    transcript_text = transcript_match.group(1).strip()
    if transcript_text.startswith("Transcript not available"):
        return title, []

    # Parse transcript lines: [timestamp] (speaker) text
    lines = []
    for m in re.finditer(
        r'^\[([^\]]+)\]\s*\((\w+)\)\s*(.+)$', transcript_text, re.MULTILINE
    ):
        lines.append({"ts": m.group(1), "speaker": m.group(2), "text": m.group(3).strip()})

    if not lines:
        return title, []

    # Scan for action-item patterns, grabbing context around matches
    action_items = []
    seen = set()

    for idx, line in enumerate(lines):
        text = line["text"]

        # Skip filler
        if FILLER_PATTERNS.match(text):
            continue

        if TRANSCRIPT_ACTION_RE.search(text) and not NOISE_PATTERNS.search(text):
            # Grab this line + up to 2 following lines for context
            parts = [text]
            for j in range(1, 3):
                if idx + j < len(lines):
                    next_text = lines[idx + j]["text"]
                    if not FILLER_PATTERNS.match(next_text):
                        parts.append(next_text)
                    else:
                        break

            combined = " ".join(parts)
            # Clean up and truncate
            combined = re.sub(r'\s+', ' ', combined).strip()
            if len(combined) > 200:
                combined = combined[:200] + "..."

            # Deduplicate similar items
            dedup_key = combined[:60].lower()
            if dedup_key not in seen and len(combined) > 10:
                seen.add(dedup_key)
                # Tag with speaker
                speaker = line["speaker"]
                action_items.append(f"({speaker}) {combined}")

    return title, action_items


def get_todays_action_items(today_files):
    """Extract action items from all of today's meeting files."""
    results = []
    for file_path in today_files:
        if not file_path.exists():
            continue
        title, items = extract_action_items_from_file(file_path)
        if items:
            results.append({"title": title, "items": items})
    return results


def find_todays_meetings(cfg):
    """Find all meeting files created today (fallback if sync returns empty)."""
    today = datetime.now().strftime("%Y-%m-%d")
    return list(cfg["_meetings_dir"].rglob(f"{today}-*.md"))


# ── Part C: Post to Slack ─────────────────────────────────────────────────

def build_slack_blocks(action_items_by_meeting):
    """Build Slack Block Kit blocks for action items."""
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Meeting Action Items \u2014 {datetime.now().strftime('%B %d, %Y')}",
            },
        },
        {"type": "divider"},
    ]

    for meeting in action_items_by_meeting:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{meeting['title']}*"},
        })
        items_text = "\n".join(f"\u2022 {item}" for item in meeting["items"])
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": items_text},
        })
        blocks.append({"type": "divider"})

    return blocks


def post_to_slack(action_items_by_meeting, cfg):
    """Post action items to Slack."""
    slack = cfg.get("slack", {})
    token = slack.get("bot_token", "")
    channel = slack.get("channel", "")

    if not token or not channel:
        print("  Slack not configured (missing bot_token or channel in config). Skipping.", file=sys.stderr)
        return True  # not an error — just unconfigured

    if not action_items_by_meeting:
        payload = {
            "channel": channel,
            "text": f"No action items from meetings today ({datetime.now().strftime('%B %d, %Y')}).",
        }
    else:
        blocks = build_slack_blocks(action_items_by_meeting)
        total_items = sum(len(m["items"]) for m in action_items_by_meeting)
        fallback = f"{total_items} action item(s) from {len(action_items_by_meeting)} meeting(s) today"
        payload = {
            "channel": channel,
            "text": fallback,
            "blocks": blocks,
        }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
    )

    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("ok"):
                print(f"  Slack message posted to #{channel}")
            else:
                print(f"  Slack API error: {result.get('error', 'unknown')}", file=sys.stderr)
                return False
    except urllib.error.URLError as e:
        print(f"  Failed to post to Slack: {e}", file=sys.stderr)
        return False

    return True


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sync Granola meetings to Obsidian + post action items to Slack")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH),
                        help=f"Path to config.json (default: {DEFAULT_CONFIG_PATH})")
    args = parser.parse_args()

    cfg = load_config(args.config)

    print(f"=== Daily Meeting Sync \u2014 {datetime.now().isoformat()} ===\n")

    # Part A: Sync meetings
    today_files = sync_meetings(cfg)

    # Fallback: scan filesystem for today's meetings
    if not today_files:
        today_files = find_todays_meetings(cfg)

    print(f"\n  Today's meeting files: {len(today_files)}")

    # Part B: Extract action items
    action_items = get_todays_action_items(today_files)
    total_items = sum(len(m["items"]) for m in action_items)
    print(f"  Action items found: {total_items} across {len(action_items)} meeting(s)")

    # Part C: Post to Slack
    print("\n  Posting to Slack...")
    success = post_to_slack(action_items, cfg)

    if success:
        print("\nDone.")
    else:
        print("\nDone (with Slack errors).", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
