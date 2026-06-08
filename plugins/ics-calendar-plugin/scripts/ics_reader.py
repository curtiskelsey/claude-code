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
    from dateutil.rrule import rrulestr
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


def _event_fields(component):
    """Extract non-date metadata from a VEVENT component."""
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
    return summary, location, description, organizer, attendees


def _expand_component(component, skip_dates=None):
    """Return event dicts for a VEVENT, expanding RRULE into individual occurrences.

    skip_dates: set of date objects to omit (dates covered by RECURRENCE-ID overrides).
    """
    dtstart_prop = component.get("dtstart")
    if not dtstart_prop:
        return []

    start, is_all_day = normalize_dt(dtstart_prop)
    summary, location, description, organizer, attendees = _event_fields(component)

    dtend_prop = component.get("dtend")
    if dtend_prop:
        end_dt, _ = normalize_dt(dtend_prop)
        duration = end_dt - start
    else:
        dur_prop = component.get("duration")
        duration = dur_prop.dt if dur_prop and hasattr(dur_prop, "dt") else timedelta(0)
        end_dt = start + duration

    def make_event(occ_start, occ_end):
        return {
            "summary": summary,
            "start": occ_start,
            "end": occ_end,
            "all_day": is_all_day,
            "location": location,
            "description": description,
            "organizer": organizer,
            "attendees": attendees,
        }

    rrule_prop = component.get("rrule")
    if not rrule_prop:
        return [make_event(start, end_dt)]

    # Pass the already-parsed `start` as dtstart rather than reconstructing a
    # DTSTART line — icalendar already resolved Windows tz names (e.g. "Central
    # Standard Time") which rrulestr does not understand.
    rrule_ical = rrule_prop.to_ical().decode("utf-8")
    full_rule = f"RRULE:{rrule_ical}"

    # Collect EXDATE dates (cancelled occurrences)
    exdate_dates = set()
    exdate_raw = component.get("exdate")
    if exdate_raw:
        if not isinstance(exdate_raw, list):
            exdate_raw = [exdate_raw]
        for ex in exdate_raw:
            dts = ex.dts if hasattr(ex, "dts") else [ex]
            for dt_item in dts:
                exc_dt, _ = normalize_dt(dt_item)
                exdate_dates.add(exc_dt.date())

    now = datetime.now().astimezone()
    window_start = now - timedelta(days=365)
    window_end = now + timedelta(days=365)

    try:
        rule = rrulestr(full_rule, dtstart=start)
        occurrences = list(rule.between(window_start, window_end, inc=True))
    except Exception:
        return [make_event(start, end_dt)]

    result = []
    for occ in occurrences:
        if occ.tzinfo is None:
            occ = occ.astimezone()
        occ_date = occ.date()
        if occ_date in exdate_dates:
            continue
        if skip_dates and occ_date in skip_dates:
            continue
        result.append(make_event(occ, occ + duration))

    return result


def parse_all_events(ics_text: str) -> list:
    """Parse every VEVENT from the ICS text into a list of dicts, expanding recurrences."""
    cal = Calendar.from_ical(ics_text)
    components = [c for c in cal.walk() if c.name == "VEVENT"]

    # Collect dates covered by RECURRENCE-ID overrides per UID so the master
    # RRULE expansion doesn't double-emit those occurrences.
    uid_overrides: dict = {}
    for comp in components:
        if comp.get("recurrence-id"):
            uid = str(comp.get("uid", ""))
            rec_dt, _ = normalize_dt(comp.get("recurrence-id"))
            uid_overrides.setdefault(uid, set()).add(rec_dt.date())

    events = []
    for component in components:
        uid = str(component.get("uid", ""))
        # Override events are single instances; master events skip overridden dates
        skip = uid_overrides.get(uid) if not component.get("recurrence-id") else None
        events.extend(_expand_component(component, skip_dates=skip))

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
