#!/usr/bin/env python3
"""MCP server that exposes calendar queries via ICS feed."""

import hashlib
import json
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

from icalendar import Calendar
import recurring_ical_events
from mcp.server.fastmcp import FastMCP

# ── Config ───────────────────────────────────────────────────────────────────
CONFIG_DIR = Path.home() / ".config" / "calendar-tools"
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_DIR = Path(tempfile.gettempdir()) / "ics_reader_cache"
CACHE_MAX_AGE = 3600  # 1 hour


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def _save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")


def get_ics_url() -> str | None:
    return _load_config().get("ics_url")


# ── Cache ────────────────────────────────────────────────────────────────────

def _cache_path(url: str) -> Path:
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{url_hash}.ics"


def _read_cache(url: str) -> str | None:
    path = _cache_path(url)
    if not path.exists():
        return None
    age = datetime.now().timestamp() - path.stat().st_mtime
    if age > CACHE_MAX_AGE:
        return None
    return path.read_text(encoding="utf-8")


def _write_cache(url: str, text: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(url).write_text(text, encoding="utf-8")


# ── ICS Parsing ──────────────────────────────────────────────────────────────

def fetch_ics(url: str, refresh: bool = False) -> str:
    if not refresh:
        cached = _read_cache(url)
        if cached:
            return cached

    req = Request(url, headers={"User-Agent": "ICS-Reader/1.0"})
    with urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")

    _write_cache(url, text)
    return text


def normalize_dt(dt_prop):
    if dt_prop is None:
        return None, False
    dt = dt_prop.dt if hasattr(dt_prop, "dt") else dt_prop
    if not hasattr(dt, "hour"):
        return datetime.combine(dt, datetime.min.time()).astimezone(), True
    if dt.tzinfo is None:
        return dt.astimezone(), False
    return dt, False


def parse_events_in_range(ics_text: str, start_date: datetime, end_date: datetime) -> list:
    """Parse events from ICS text, expanding recurring events within the given range."""
    cal = Calendar.from_ical(ics_text)
    expanded = recurring_ical_events.of(cal).between(start_date, end_date)
    events = []

    for component in expanded:
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


# ── Filters ──────────────────────────────────────────────────────────────────

def filter_future(events):
    now = datetime.now().astimezone()
    return [e for e in events if e["start"] > now]


def is_one_on_one(summary: str, name: str) -> bool:
    s = summary.lower()
    n = name.lower()
    if re.search(rf'\b{re.escape(n)}\b\s*<>', s):
        return True
    if re.search(rf'<>\s*\b{re.escape(n)}\b', s):
        return True
    if len(summary) < 40 and re.search(
        rf'\b{re.escape(n)}\b\s*/\s*\w+|\w+\s*/\s*\b{re.escape(n)}\b', s
    ):
        return True
    if re.search(rf'\b{re.escape(n)}\b.*1[:\-]1|1[:\-]1.*\b{re.escape(n)}\b', s):
        return True
    return False


# ── Formatters ───────────────────────────────────────────────────────────────

def format_event(event: dict) -> str:
    start = event["start"]
    if event["all_day"]:
        date_str = start.strftime("%a %b %d (all day)")
    else:
        date_str = start.strftime("%a %b %d  %I:%M %p")
        if event["end"]:
            date_str += f" - {event['end'].strftime('%I:%M %p')}"

    lines = [f"  {date_str}", f"  {event['summary']}"]

    if event["location"]:
        loc = event["location"]
        if len(loc) > 80:
            loc = loc[:77] + "..."
        lines.append(f"  Location: {loc}")

    if event["organizer"]:
        lines.append(f"  Organizer: {event['organizer']}")

    return "\n".join(lines)


def format_event_list(events: list, title: str) -> str:
    if not events:
        return f"No {title.lower()} found."

    lines = [
        f"{'=' * 50}",
        f" {title}  ({len(events)} total)",
        f"{'=' * 50}",
        "",
    ]

    current_date = None
    for event in events:
        event_date = event["start"].strftime("%Y-%m-%d")
        if event_date != current_date:
            current_date = event_date
            lines.append(f"--- {event['start'].strftime('%A, %B %d')} ---")
        lines.append(format_event(event))
        lines.append("")

    return "\n".join(lines)


# ── Shared loader ────────────────────────────────────────────────────────────

def _load_events(days_ahead: int = 7, days_behind: int = 0, refresh: bool = False) -> tuple[list, str | None]:
    """Load events with recurrence expansion, returning (events, error_message)."""
    url = get_ics_url()
    if not url:
        return [], "No ICS URL configured. Please run the setup tool first with your Outlook ICS feed URL."
    ics_text = fetch_ics(url, refresh=refresh)
    now = datetime.now().astimezone()
    start_date = now - timedelta(days=days_behind)
    end_date = now + timedelta(days=days_ahead)
    return parse_events_in_range(ics_text, start_date, end_date), None


# ── MCP Server ───────────────────────────────────────────────────────────────

mcp = FastMCP("calendar")


@mcp.tool()
def setup_calendar(ics_url: str) -> str:
    """Save the user's ICS calendar feed URL. Must be called before any other calendar tool.

    Args:
        ics_url: The full ICS feed URL from Outlook (Settings > Calendar > Shared calendars > Publish a calendar)
    """
    config = _load_config()
    config["ics_url"] = ics_url
    _save_config(config)
    return f"ICS URL saved. Your calendar is now configured and ready to query."


@mcp.tool()
def get_schedule(days_ahead: int = 7, days_behind: int = 0, refresh: bool = False) -> str:
    """Show calendar events in a time window.

    Args:
        days_ahead: Number of days ahead to show (default 7)
        days_behind: Number of past days to include (default 0)
        refresh: Bypass the 1-hour cache and fetch fresh data
    """
    events, err = _load_events(days_ahead=days_ahead, days_behind=days_behind, refresh=refresh)
    if err:
        return err
    return format_event_list(events, f"Calendar — next {days_ahead} day(s)")


@mcp.tool()
def search_meetings(keyword: str, days_ahead: int = 30, days_behind: int = 0, refresh: bool = False) -> str:
    """Search for meetings by keyword in the title.

    Args:
        keyword: Text to search for in meeting titles (case-insensitive)
        days_ahead: Number of days ahead to search (default 30)
        days_behind: Number of past days to include (default 0)
        refresh: Bypass the 1-hour cache and fetch fresh data
    """
    events, err = _load_events(days_ahead=days_ahead, days_behind=days_behind, refresh=refresh)
    if err:
        return err
    matches = [e for e in events if keyword.lower() in e["summary"].lower()]
    return format_event_list(matches, f'Search: "{keyword}"')


@mcp.tool()
def find_one_on_ones(name: str, days_ahead: int = 90, days_behind: int = 0, refresh: bool = False) -> str:
    """Find 1:1 meetings with a specific person.

    Matches common 1:1 title patterns like "Name <> You", "You <> Name",
    "Name / You", "Name 1:1", etc.

    Args:
        name: Person's first name to search for
        days_ahead: Number of days ahead to search (default 90)
        days_behind: Number of past days to include (default 0, use 365 for past year)
        refresh: Bypass the 1-hour cache and fetch fresh data
    """
    events, err = _load_events(days_ahead=days_ahead, days_behind=days_behind, refresh=refresh)
    if err:
        return err
    if not days_behind:
        events = filter_future(events)
    matches = [e for e in events if is_one_on_one(e["summary"], name)]
    direction = "upcoming" if not days_behind else "matching"
    return format_event_list(matches, f"1:1s with {name} ({direction})")


@mcp.tool()
def get_zoom_meetings(days_ahead: int = 7, days_behind: int = 0, refresh: bool = False) -> str:
    """Show only meetings that have Zoom links.

    Args:
        days_ahead: Number of days ahead to show (default 7)
        days_behind: Number of past days to include (default 0)
        refresh: Bypass the 1-hour cache and fetch fresh data
    """
    events, err = _load_events(days_ahead=days_ahead, days_behind=days_behind, refresh=refresh)
    if err:
        return err
    zoom_events = [
        e for e in events
        if any("zoom" in (val or "").lower() for val in [e["location"], e["description"]])
    ]
    return format_event_list(zoom_events, "Zoom meetings")


@mcp.tool()
def get_todays_schedule(refresh: bool = False) -> str:
    """Show all meetings for today.

    Args:
        refresh: Bypass the 1-hour cache and fetch fresh data
    """
    url = get_ics_url()
    if not url:
        return "No ICS URL configured. Please run the setup tool first with your Outlook ICS feed URL."
    ics_text = fetch_ics(url, refresh=refresh)
    now = datetime.now().astimezone()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    todays = parse_events_in_range(ics_text, today_start, today_end)
    return format_event_list(todays, f"Today's schedule ({now.strftime('%A, %B %d')})")


@mcp.tool()
def get_free_time(days_ahead: int = 1, refresh: bool = False) -> str:
    """Show free time slots between meetings for the specified number of days.

    Args:
        days_ahead: Number of days to check (default 1, today only)
        refresh: Bypass the 1-hour cache and fetch fresh data
    """
    events, err = _load_events(days_ahead=days_ahead, refresh=refresh)
    if err:
        return err
    now = datetime.now().astimezone()
    work_start_hour = 8
    work_end_hour = 17

    lines = []

    for day_offset in range(days_ahead):
        current_day = (now + timedelta(days=day_offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_end = current_day + timedelta(days=1)

        day_events = [
            e for e in events
            if not e["all_day"] and current_day <= e["start"] < day_end
        ]
        day_events.sort(key=lambda e: e["start"])

        work_start = current_day.replace(hour=work_start_hour)
        work_end = current_day.replace(hour=work_end_hour)

        if day_offset == 0 and now > work_start:
            work_start = now

        lines.append(f"--- {current_day.strftime('%A, %B %d')} ---")

        if not day_events:
            lines.append(f"  Free all day ({work_start.strftime('%I:%M %p')} - {work_end.strftime('%I:%M %p')})")
            lines.append("")
            continue

        cursor = work_start
        free_slots = []

        for event in day_events:
            event_start = event["start"]
            event_end = event["end"] or (event_start + timedelta(hours=1))

            if event_start < cursor:
                cursor = max(cursor, event_end)
                continue

            if event_start > cursor and event_start <= work_end:
                gap = event_start - cursor
                if gap >= timedelta(minutes=15):
                    free_slots.append(
                        f"  {cursor.strftime('%I:%M %p')} - {event_start.strftime('%I:%M %p')}  "
                        f"({int(gap.total_seconds() // 60)} min free)"
                    )

            cursor = max(cursor, event_end)

        if cursor < work_end:
            gap = work_end - cursor
            if gap >= timedelta(minutes=15):
                free_slots.append(
                    f"  {cursor.strftime('%I:%M %p')} - {work_end.strftime('%I:%M %p')}  "
                    f"({int(gap.total_seconds() // 60)} min free)"
                )

        if free_slots:
            for slot in free_slots:
                lines.append(slot)
        else:
            lines.append("  No free time during work hours (8 AM - 5 PM)")

        lines.append("")

    header = f"Free time — next {days_ahead} day(s)"
    return f"{'=' * 50}\n {header}\n{'=' * 50}\n\n" + "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
