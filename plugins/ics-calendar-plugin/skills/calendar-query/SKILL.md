---
name: calendar-query
description: Use this skill when the user asks about their calendar, schedule, meetings, 1:1s, availability, free time, Zoom calls, or anything related to their work schedule. Trigger phrases include "what's on my calendar", "do I have any meetings", "when is my next", "how many meetings", "am I free", "what's my schedule", "1:1 with", "one on one with", "zoom meetings", "find meetings about", "PTO", "day off".
version: 1.0.0
---

# Calendar Query Skill

You have access to the user's calendar via an ICS feed reader script.

## Script Location

The script is at: `${CLAUDE_PLUGIN_ROOT}/scripts/ics_reader.py`

## First-Time Setup

Before running any calendar query, check if the ICS URL is configured:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ics_reader.py --days 1
```

If the output contains `ERROR: No ICS URL configured`, the user has not set up their calendar yet. Ask them:

> "I need your ICS calendar URL to access your schedule. You can get it from Outlook on the web: **Settings > Calendar > Shared calendars > Publish a calendar**, then copy the ICS link. Paste it here and I'll save it for you."

Once they provide the URL, save it:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ics_reader.py setup "<their-url>"
```

This only needs to happen once. The URL is saved to `~/.config/calendar-tools/config.json`.

## Available Commands

Run these via the Bash tool with `python3`:

### Show schedule for a time window
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ics_reader.py --days <N>
```
- `--days N` — days ahead (default: 7)
- `--past N` — include N past days
- `--zoom-only` — only Zoom meetings
- `--refresh` — bypass 1-hour cache, fetch fresh data

### Search meetings by keyword
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ics_reader.py search "<keyword>" --days <N>
```
Searches meeting titles (case-insensitive). Use `--past N` to include past days.

### Find 1:1 meetings with a person
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ics_reader.py ones "<name>" --days <N>
```
Matches patterns like "Name <> You", "You <> Name", "Name / You", "Name 1:1".
- Without `--past`: shows future 1:1s only
- With `--past 365`: includes past year

## Flags (work in any position, before or after subcommand)

| Flag | Description | Default |
|------|-------------|---------|
| `--days N` | Days ahead to search | 7 |
| `--past N` | Past days to include | 0 |
| `--zoom-only` | Only Zoom meetings | off |
| `--refresh` | Bypass 1-hour cache | off |

## Guidelines

- The script caches the ICS feed for 1 hour. Only use `--refresh` if the user says they just updated their calendar or explicitly asks for fresh data.
- For "today's schedule", use `--days 1`.
- For "this week", use `--days 7`.
- For "am I free" questions, run the schedule and look for gaps between meetings. Assume work hours are 8 AM - 5 PM Central.
- For counting meetings, run the appropriate query and count the results.
- When the user asks about a person, try `ones` first for 1:1s, and `search` for broader matches.
- Use `--past 365 --days 365` for broad historical + future searches.
- Always present results in a concise, readable format. Don't just dump the raw output — summarize when appropriate and answer the user's specific question.
