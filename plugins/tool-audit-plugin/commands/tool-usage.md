---
name: tool-usage
description: Summarize tool and command usage from audit logs
arguments:
  - name: days
    description: Number of days to look back (default 1)
    required: false
  - name: detail
    description: "Level of detail: summary, tools, or full (default summary)"
    required: false
---

Review the tool audit log files at `~/.claude/tool-audit-YYYY-MM-DD.jsonl` and provide a usage summary.

## Parameters
- **Days**: Look back {{ days | default: "1" }} day(s)
- **Detail level**: {{ detail | default: "summary" }}

## Instructions

1. Find all `tool-audit-*.jsonl` files in `~/.claude/` for the requested time range (last {{ days | default: "1" }} day(s)).

2. Parse each JSONL file. Each line is a JSON object with these fields:
   - `timestamp` - ISO 8601 timestamp
   - `tool` - The tool/command name (e.g. "Read", "Bash", "Grep", "Edit", "Glob")
   - `session` - Session ID
   - `params` - Tool parameters (for successful calls)
   - `error` - Error message (for failed calls)
   - `success` - `false` when the call failed

3. Based on detail level, provide:

   **summary** (default): A table showing each unique tool name and how many times it was used, sorted by frequency descending. Include total count across all tools. Show the number of unique sessions. Show the number of failures if any.

   **tools**: Same as summary, plus for each tool show a breakdown by the most common parameter patterns (e.g. for Bash show the top commands, for Read show the most-read files, for Grep show the most-searched patterns). Limit to top 5 per tool.

   **full**: Same as tools, plus break down usage by session ID, showing which tools each session used and when it was active.

4. Ignore the specific argument values when counting tools — just use the tool name (e.g. "Bash", "Read", "Grep"). For Bash commands, extract just the base command (first word) from the command string.

5. Present results in clean markdown tables.
