---
name: tool-usage-review
description: Review, analyze, and summarize Claude Code tool and command usage from audit logs
---

You have access to tool audit logs stored as daily JSONL files at `~/.claude/tool-audit-YYYY-MM-DD.jsonl`.

## When to activate

Activate when the user asks about:
- What tools or commands were used (today, yesterday, this week, etc.)
- Tool usage frequency or patterns
- Which sessions used which tools
- Tool failures or errors
- How they've been using Claude Code
- Activity summaries or reports
- Optimizing permission rules or allow/deny lists
- Which tools keep getting prompted (not auto-approved)
- Stale, unused, or redundant permission rules
- Permission reconciliation or audit
- What should be in their allow list or deny list

## Audit log format

Each line in the JSONL files is a JSON object:

**Successful tool use:**
```json
{"timestamp": "2026-03-13T15:31:49Z", "tool": "Grep", "session": "uuid", "params": {"pattern": "...", ...}}
```

**Failed tool use:**
```json
{"timestamp": "2026-03-13T15:31:49Z", "tool": "Bash", "session": "uuid", "error": "...", "success": false}
```

## How to analyze

1. Use `ls ~/.claude/tool-audit-*.jsonl` to find available audit files
2. Read the relevant files based on the user's time range
3. Parse the JSONL data to count and categorize tool usage
4. For tool names, ignore arguments — just count by the tool name (Read, Bash, Grep, Edit, Glob, Write, etc.)
5. For Bash commands, you can extract the base command (first word of the command string) for more granular analysis
6. Present results in clean markdown tables sorted by frequency

## Permission reconciliation

When the user asks about optimizing permissions, reconciling rules, or understanding which tools are prompted vs auto-approved:

1. Run the reconciliation script:
   ```bash
   bash ${CLAUDE_PLUGIN_ROOT}/scripts/reconcile_permissions.sh [DAYS] [SETTINGS_PATH]
   ```
   - DAYS defaults to 7 (use 30 for a broader view)
   - SETTINGS_PATH defaults to `~/.claude/settings.json`

2. The script cross-references audit logs against settings.json permission rules:
   - **Covered**: Calls matching an allow rule (auto-approved)
   - **Prompted**: Calls matching no rule (user had to approve)
   - **Stale**: Allow rules with zero matches (consider removing)
   - **Denied**: Failed calls (note: lack params, so can't confirm deny-rule match)
   - **Suggested**: New rules for frequently-prompted commands (3+ occurrences)

3. After presenting the report, offer to help apply changes to settings.json.

## Response guidelines

- Default to today's usage if no time range is specified
- Always show total tool call count and unique session count
- Highlight any failures separately
- Keep summaries concise — expand detail only when asked
- For permission questions, default to 7 days of data for meaningful patterns
