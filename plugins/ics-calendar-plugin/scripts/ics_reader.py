#!/usr/bin/env python3
"""Fetch and parse an ICS calendar feed, displaying upcoming meetings.

Usage:
    # Show next 7 days
    python3 ics_reader.py <url>

    # Show next 14 days including 3 past days
    python3 ics_reader.py <url> --days 14 --past 3

    # Only Zoom meetings
    python3 ics_reader.py <url> --zoom-only

    # Search for meetings by keyword (title match)
    python3 ics_reader.py <url> search "Ed"

    # Count 1:1 meetings with someone (future only by default)
    python3 ics_reader.py <url> ones "Ed"

    # Count 1:1s including past meetings
    python3 ics_reader.py <url> ones "Ed" --past 365
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

try:
    from icalendar import Calendar
except ImportError:
    print("Missing dependency. Install with: pip3 install icalendar")
    sys.exit(1)

# ── Config ───────────────────────────────────────────────────────────────────
CONFIG_DIR = Path.home() / ".config" / "calendar-tools"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def _save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")


def get_ics_url() -> str | None:
    """Return the saved ICS URL, or None if not configured."""
    return _load_config().get("ics_url")


def set_ics_url(url: str) -> None:
    """Save the ICS URL to config."""
    config = _load_config()
    config["ics_url"] = url
    _save_config(config)


# ── Cache ────────────────────────────────────────────────────────────────────
CACHE_DIR = Path(tempfile.gettempdir()) / "ics_reader_cache"
CACHE_MAX_AGE = 3600  # 1 hour in seconds


def _cache_path(url: str) -> Path:
    """Return a deterministic cache file path for the given URL."""
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{url_hash}.ics"


def _read_cache(url: str) -> str | None:
    """Return cached ICS text if it exists and is fresh, else None."""
    path = _cache_path(url)
    if not path.exists():
        return None
    age = datetime.now().timestamp() - path.stat().st_mtime
    if age > CACHE_MAX_AGE:
        return None
    return path.read_text(encoding="utf-8")


def _write_cache(url: str, text: str) -> None:
    """Write ICS text to the cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(url).write_text(text, encoding="utf-8")


# ── Helpers ──────────────────────────────────────────────────────────────────

def fetch_ics(url: str, refresh: bool = False) -> str:
    if not refresh:
        cached = _read_cache(url)
        if cached:
            print("  (using cached data — pass --refresh to force a fresh fetch)")
            return cached

    req = Request(url, headers={"User-Agent": "ICS-Reader/1.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except URLError as e:
        print(f"Error fetching calendar: {e}")
        sys.exit(1)

    _write_cache(url, text)
    return text


def normalize_dt(dt_prop):
    """Convert an icalendar date/datetime to a timezone-aware datetime."""
    if dt_prop is None:
        return None, False
    dt = dt_prop.dt if hasattr(dt_prop, "dt") else dt_prop
    if not hasattr(dt, "hour"):
        return datetime.combine(dt, datetime.min.time()).astimezone(), True
    if dt.tzinfo is None:
        return dt.astimezone(), False
    return dt, False


def parse_all_events(ics_text: str) -> list:
    """Parse every VEVENT from the ICS text into a list of dicts."""
    cal = Calendar.from_ical(ics_text)
    events = []

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        dtstart = component.get("dtstart")
        if not dtstart:
            continue

        start, is_all_day = normalize_dt(dtstart)
        end, _ = normalize_dt(component.get("dtend"))

        summary = str(component.get("summary", "No title"))
        location = str(component.get("location", "")) or None
        description = str(component.get("description", "")) or None

        organizer = component.get("organizer")
        if organizer:
            organizer = str(organizer).replace("mailto:", "")

        attendees_raw = component.get("attendee", [])
        if not isinstance(attendees_raw, list):
            attendees_raw = [attendees_raw]
        attendees = [str(a).replace("mailto:", "") for a in attendees_raw]

        events.append({
            "summary": summary,
            "start": start,
            "end": end,
            "all_day": is_all_day,
            "location": location,
            "description": description,
            "organizer": organizer,
            "attendees": attendees,
        })

    events.sort(key=lambda e: e["start"])
    return events


def filter_by_window(events, days_ahead=7, days_behind=0):
    now = datetime.now().astimezone()
    window_start = now - timedelta(days=days_behind)
    window_end = now + timedelta(days=days_ahead)
    return [e for e in events if window_start <= e["start"] <= window_end]


def filter_future(events):
    now = datetime.now().astimezone()
    return [e for e in events if e["start"] > now]


def format_event(event: dict) -> str:
    lines = []
    start = event["start"]

    if event["all_day"]:
        date_str = start.strftime("%a %b %d (all day)")
    else:
        date_str = start.strftime("%a %b %d  %I:%M %p")
        if event["end"]:
            date_str += f" - {event['end'].strftime('%I:%M %p')}"

    lines.append(f"  {date_str}")
    lines.append(f"  {event['summary']}")

    if event["location"]:
        loc = event["location"]
        if len(loc) > 80:
            loc = loc[:77] + "..."
        lines.append(f"  Location: {loc}")

    if event["organizer"]:
        lines.append(f"  Organizer: {event['organizer']}")

    return "\n".join(lines)


def print_events(events, title="Events"):
    if not events:
        print(f"No {title.lower()} found.")
        return

    print(f"\n{'=' * 50}")
    print(f" {title}  ({len(events)} total)")
    print(f"{'=' * 50}\n")

    current_date = None
    for event in events:
        event_date = event["start"].strftime("%Y-%m-%d")
        if event_date != current_date:
            current_date = event_date
            print(f"--- {event['start'].strftime('%A, %B %d')} ---")
        print(format_event(event))
        print()


# ── Queries ──────────────────────────────────────────────────────────────────

def is_one_on_one(summary: str, name: str) -> bool:
    """Check if a meeting title looks like a 1:1 with the given person.

    Matches patterns like:
        Name <> You, You <> Name, Name / You, Name 1:1, etc.
    """
    s = summary.lower()
    n = name.lower()
    # "Ed <> Curtis" or "Curtis <> Ed"
    if re.search(rf'\b{re.escape(n)}\b\s*<>', s):
        return True
    if re.search(rf'<>\s*\b{re.escape(n)}\b', s):
        return True
    # "Ed / Curtis" or "Curtis / Ed" (short titles only, to avoid false positives)
    if len(summary) < 40 and re.search(rf'\b{re.escape(n)}\b\s*/\s*\w+|\w+\s*/\s*\b{re.escape(n)}\b', s):
        return True
    # "Ed 1:1" or "1:1 Ed"
    if re.search(rf'\b{re.escape(n)}\b.*1[:\-]1|1[:\-]1.*\b{re.escape(n)}\b', s):
        return True
    return False


def cmd_show(events, args):
    """Default: show events in a time window."""
    filtered = filter_by_window(events, days_ahead=args.days, days_behind=args.past)
    if args.zoom_only:
        filtered = [
            e for e in filtered
            if any("zoom" in (val or "").lower() for val in [e["location"], e["description"]])
        ]
    print_events(filtered, f"Calendar — next {args.days} day(s)")


def cmd_search(events, args):
    """Search events by keyword in title."""
    keyword = args.keyword.lower()
    if args.past:
        pool = filter_by_window(events, days_ahead=args.days, days_behind=args.past)
    else:
        pool = filter_by_window(events, days_ahead=args.days, days_behind=0)

    matches = [e for e in pool if keyword in e["summary"].lower()]
    print_events(matches, f"Search: \"{args.keyword}\"")


def cmd_ones(events, args):
    """Count and list 1:1 meetings with a person."""
    name = args.name

    if args.past:
        pool = filter_by_window(events, days_ahead=args.days, days_behind=args.past)
    else:
        pool = filter_future(events)

    matches = [e for e in pool if is_one_on_one(e["summary"], name)]

    direction = "upcoming" if not args.past else "matching"
    print_events(matches, f"1:1s with {name} ({direction})")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    # Shared flags available to all commands
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--url", default=None, help="ICS feed URL (overrides saved config)")
    shared.add_argument("--days", type=int, default=7, help="Days ahead to show (default: 7)")
    shared.add_argument("--past", type=int, default=0, help="Past days to include (default: 0)")
    shared.add_argument("--zoom-only", action="store_true", help="Only show Zoom meetings")
    shared.add_argument("--refresh", action="store_true", help="Bypass cache and fetch fresh data")

    parser = argparse.ArgumentParser(
        description="Fetch and query an ICS calendar feed",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[shared],
    )

    subparsers = parser.add_subparsers(dest="command")

    # setup
    sp_setup = subparsers.add_parser("setup", help="Save your ICS feed URL")
    sp_setup.add_argument("url", help="The ICS feed URL to save")

    # search
    sp_search = subparsers.add_parser("search", parents=[shared], help="Search events by keyword in title")
    sp_search.add_argument("keyword", help="Keyword to search for")

    # ones (1:1s)
    sp_ones = subparsers.add_parser("ones", parents=[shared], help="Find 1:1 meetings with a person")
    sp_ones.add_argument("name", help="Person's name to search for")

    args = parser.parse_args()

    # Handle setup command
    if args.command == "setup":
        set_ics_url(args.url)
        print(f"ICS URL saved to {CONFIG_FILE}")
        return

    # Resolve URL: CLI flag > config file
    url = args.url or get_ics_url()
    if not url:
        print("ERROR: No ICS URL configured.")
        print("Run setup first:  python3 ics_reader.py setup <your-ics-url>")
        sys.exit(1)

    print(f"Fetching calendar...")
    ics_text = fetch_ics(url, refresh=args.refresh)
    events = parse_all_events(ics_text)
    print(f"Loaded {len(events)} events.")

    if args.command == "search":
        cmd_search(events, args)
    elif args.command == "ones":
        cmd_ones(events, args)
    else:
        cmd_show(events, args)


if __name__ == "__main__":
    main()
